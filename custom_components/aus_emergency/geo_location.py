from __future__ import annotations

import hashlib
import logging
import statistics
from datetime import datetime
from typing import Any, Dict

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import now as dt_now
from homeassistant.util import slugify
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .utils import compass_direction, distance_to_incident, initial_bearing, public_incident
from .const import (
    DOMAIN,
    CONF_REMOVE_STALE,
    CONF_EXPOSE_TO_ASSISTANTS,
    CONF_ZONES,
    CONF_ZONE_BUFFER,
    CONF_RADIUS,
    CONF_STATE,
    CONF_STATES,
    DEFAULT_REMOVE_STALE,
    DEFAULT_EXPOSE_TO_ASSISTANTS,
    DEFAULT_ZONE_BUFFER,
    DEFAULT_RADIUS,
    DEFAULT_STATE,
    DEFAULT_STATES,
    ATTR_INCIDENT_NO,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_REGION,
    ATTR_LOCATION_NAME,
    ATTR_MESSAGE_LINK,
    ATTR_DATE,
    ATTR_TIME,
    ATTR_SEVERITY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_DURATION_MINUTES,
    ATTR_IN_ZONE,
    ATTR_DISTANCE_KM,
    ATTR_BEARING,
    ATTR_DIRECTION,
    ATTR_HOME_IN_AREA,
    ATTR_POLYGONS,
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_REMOVED,
    EVENT_CAP_CREATED,
    EVENT_CAP_UPDATED,
    EVENT_CAP_REMOVED,
    STATE_DEVICE_INFO,
    DEVICE_INFO_SA_CFS,
)

from .coordinator import IncidentDataCoordinator
from .cap_coordinator import CFSCAPDataCoordinator

_LOGGER = logging.getLogger(__name__)


def _build_title(attrs: dict) -> str:
    typ = attrs.get(ATTR_TYPE) or "Incident"
    loc = attrs.get(ATTR_LOCATION_NAME) or "Unknown location"
    sev = attrs.get(ATTR_SEVERITY, "info")
    sev_disp = {
        "emergency_warning": "Emergency",
        "watch_and_act": "Watch and Act",
        "advice": "Advice",
        "all_clear": "All clear",
        "info": "Info",
    }.get(sev, "Info")
    return f"{typ} – {loc} ({sev_disp})"


def _build_summary(attrs: dict) -> str:
    st = attrs.get(ATTR_STATUS) or attrs.get(ATTR_LEVEL) or "Unknown status"
    reg = attrs.get(ATTR_REGION) or ""
    when = (
        (attrs.get(ATTR_DATE) or "")
        + (" " + (attrs.get(ATTR_TIME) or "") if attrs.get(ATTR_TIME) else "")
    )
    return " · ".join([s for s in [st, reg, when] if s])


VOICE_ASSISTANTS = ("conversation", "cloud.google_assistant")

# Volatile or bulky attributes kept out of the recorder database
UNRECORDED_GEO_ATTRIBUTES = frozenset({
    ATTR_DURATION_MINUTES,
    "last_seen",
    "description",
    "instruction",
    "areas",
})


def _expose_entity_to_voice_assistants(hass: HomeAssistant, entity_id: str) -> None:
    """Expose a registered entity to voice assistants."""
    # Imported here so the platform loads even where the component is unavailable.
    from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
        async_expose_entity,
    )

    for assistant in VOICE_ASSISTANTS:
        try:
            async_expose_entity(hass, assistant, entity_id, True)
        except Exception as err:  # noqa: BLE001 - exposure is best effort
            _LOGGER.debug("Could not expose %s to %s: %s", entity_id, assistant, err)


def _is_registered(hass: HomeAssistant, unique_id: str) -> bool:
    """Return True if this platform registered the unique_id on an earlier run."""
    return er.async_get(hass).async_get_entity_id("geo_location", DOMAIN, unique_id) is not None


def _point_in_zone(
    hass: HomeAssistant,
    lat: float | None,
    lon: float | None,
    zone_entity_id: str,
    buffer_km: float = 0,
    polygons: list | None = None,
) -> bool:
    """Check if an incident is within a zone's radius plus a buffer.

    With warning-area polygons, a zone whose centre is inside the area matches.
    """
    zone_state = hass.states.get(zone_entity_id)
    if not zone_state:
        return False

    try:
        zone_lat = float(zone_state.attributes.get("latitude", 0))
        zone_lon = float(zone_state.attributes.get("longitude", 0))
        zone_radius_km = float(zone_state.attributes.get("radius", 0)) / 1000  # metres
        buffer_km = float(buffer_km or 0)
    except (ValueError, TypeError):
        return False

    reach_km = zone_radius_km + max(buffer_km, 0)
    if reach_km <= 0:
        return False

    distance, inside = distance_to_incident(zone_lat, zone_lon, lat, lon, polygons)
    return inside or (distance is not None and distance <= reach_km)


def _get_zones_for_point(
    hass: HomeAssistant,
    lat: float | None,
    lon: float | None,
    zone_ids: list[str],
    buffer_km: float = 0,
    polygons: list | None = None,
) -> list[str]:
    """Get list of zone names that contain the given incident."""
    if not zone_ids or ((lat is None or lon is None) and not polygons):
        return []

    matching_zones = []
    for zone_id in zone_ids:
        if _point_in_zone(hass, lat, lon, zone_id, buffer_km, polygons):
            zone_state = hass.states.get(zone_id)
            if zone_state:
                matching_zones.append(zone_state.attributes.get("friendly_name", zone_id))

    return matching_zones


def _within_radius(distance_km: float | None, radius_km: float) -> bool:
    """Return True if an incident passes the map-entity radius filter."""
    if not radius_km:
        return True
    # Without a location there is nothing to put on the map, and no way to
    # tell whether it is nearby; it still counts in the state sensors.
    return distance_km is not None and distance_km <= radius_km


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    remove_stale = entry.options.get(
        CONF_REMOVE_STALE, entry.data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE)
    )
    expose_to_assistants = entry.options.get(
        CONF_EXPOSE_TO_ASSISTANTS, entry.data.get(CONF_EXPOSE_TO_ASSISTANTS, DEFAULT_EXPOSE_TO_ASSISTANTS)
    )
    monitored_zones = entry.options.get(
        CONF_ZONES, entry.data.get(CONF_ZONES, [])
    )
    zone_buffer = entry.options.get(
        CONF_ZONE_BUFFER, entry.data.get(CONF_ZONE_BUFFER, DEFAULT_ZONE_BUFFER)
    ) or 0
    radius = entry.options.get(
        CONF_RADIUS, entry.data.get(CONF_RADIUS, DEFAULT_RADIUS)
    ) or 0

    entry_data = hass.data[DOMAIN][entry.entry_id]
    incident_coordinators = entry_data.get("incident_coordinators", {})
    cap_coordinators = entry_data.get("cap_coordinators", {})

    # Get configured states (support both new multi-state and legacy single-state)
    states = entry_data.get("states", [])
    if not states:
        old_state = entry.options.get(CONF_STATE) or entry.data.get(CONF_STATE, DEFAULT_STATE)
        states = [old_state] if old_state else DEFAULT_STATES

    # Set up incident and CAP entities for each state
    for state in states:
        incident_coordinator = incident_coordinators.get(state)
        cap_coordinator = cap_coordinators.get(state)
        device_info = STATE_DEVICE_INFO.get(state, DEVICE_INFO_SA_CFS)

        if incident_coordinator:
            _setup_incident_entities(
                hass, entry, async_add_entities,
                incident_coordinator, device_info, monitored_zones, remove_stale, expose_to_assistants, state,
                zone_buffer=zone_buffer, radius=radius,
            )

        if cap_coordinator:
            _setup_cap_entities(
                hass, entry, async_add_entities,
                cap_coordinator, device_info, monitored_zones, expose_to_assistants, state,
                zone_buffer=zone_buffer, radius=radius,
            )


def _prune_orphaned_incident_entries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state: str,
    incident_entities: dict[str, IncidentEntity],
    *,
    cap: bool = False,
) -> None:
    """Remove old incident (or CAP alert) registrations no longer tracked in memory.

    Only reconcile after a successful fetch: an offline provider must never
    make an old but potentially active incident appear resolved.
    """
    registry = er.async_get(hass)
    prefix = f"aus_emergency_{state}_".lower()
    cap_prefix = f"{prefix}cap_"
    active_ids = {entity._attr_unique_id for entity in incident_entities.values()}
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registered.unique_id.lower()
        if (
            registered.platform == DOMAIN
            and registered.entity_id.startswith("geo_location.")
            and unique_id.startswith(prefix)
            and unique_id.startswith(cap_prefix) == cap
            and registered.unique_id not in active_ids
        ):
            _LOGGER.info("Removing orphaned %s registration %s",
                         "CAP alert" if cap else "incident", registered.entity_id)
            registry.async_remove(registered.entity_id)


def _setup_incident_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    incident_coordinator: IncidentDataCoordinator,
    device_info: dict,
    monitored_zones: list[str],
    remove_stale: bool,
    expose_to_assistants: bool,
    state: str,
    *,
    zone_buffer: float = 0,
    radius: float = 0,
):
    """Set up incident geo_location entities for a single state."""
    # Use state prefix in entity tracking to avoid collisions
    incident_entities: dict[str, IncidentEntity] = {}

    def _sync_incident_entities():
        if not incident_coordinator.last_update_success:
            for entity in incident_entities.values():
                entity.mark_stale()
                entity.async_write_ha_state()
            return

        data = incident_coordinator.data or {}
        # Incidents outside the radius are handled exactly like ended ones.
        incidents = [
            item for item in data.get("incidents", [])
            if _within_radius(item.get(ATTR_DISTANCE_KM), radius)
        ]
        seen_ids: set[str] = set()

        for item in incidents:
            inc_no = item.get(ATTR_INCIDENT_NO)
            if not inc_no:
                inc_no = hashlib.sha1(
                    (
                        f"{item.get(ATTR_LOCATION_NAME,'unknown')}-"
                        f"{item.get(ATTR_DATE,'')}-{item.get(ATTR_TIME,'')}"
                    ).encode("utf-8")
                ).hexdigest()

            # Prefix with state to ensure uniqueness across states
            full_id = f"{state}_{inc_no}"
            seen_ids.add(full_id)

            ent = incident_entities.get(full_id)
            if ent is None:
                ent = IncidentEntity(
                    hass, item, unique_id=full_id,
                    source=incident_coordinator.source,
                    device_info=device_info,
                    monitored_zones=monitored_zones,
                    state_code=state,
                    zone_buffer=zone_buffer,
                )
                # An incident registered on an earlier run is not new: a restart
                # or reload must not re-announce every active incident.
                is_new = not _is_registered(hass, ent._attr_unique_id)
                ent.expose_on_add = expose_to_assistants and is_new
                incident_entities[full_id] = ent
                async_add_entities([ent])
                if is_new:
                    ent.fire_change_event(EVENT_CREATED)
            else:
                was_ended = ent.ended
                changed = ent.update_from_item(item, monitored_zones)
                ent.async_write_ha_state()
                if was_ended:
                    ent.fire_change_event(EVENT_CREATED)
                elif changed:
                    ent.fire_change_event(EVENT_UPDATED)

        stale_ids = [eid for eid in incident_entities if eid not in seen_ids]
        if stale_ids:
            registry = er.async_get(hass)
            for sid in stale_ids:
                if remove_stale:
                    ent = incident_entities.pop(sid)
                    ent.fire_change_event(EVENT_REMOVED)
                    if ent.entity_id and registry.async_get(ent.entity_id):
                        registry.async_remove(ent.entity_id)
                        _LOGGER.debug("Removed stale geo entity %s", ent.entity_id)
                else:
                    # Keep tracking it so it can come back under the same unique_id.
                    ent = incident_entities[sid]
                    if not ent.ended:
                        ent.mark_stale(ended=True)
                        ent.async_write_ha_state()
                        ent.fire_change_event(EVENT_REMOVED)

        if remove_stale:
            _prune_orphaned_incident_entries(hass, entry, state, incident_entities)

    entry.async_on_unload(incident_coordinator.async_add_listener(_sync_incident_entities))
    _sync_incident_entities()


def _setup_cap_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    cap_coordinator: CFSCAPDataCoordinator,
    device_info: dict,
    monitored_zones: list[str],
    expose_to_assistants: bool,
    state: str,
    *,
    zone_buffer: float = 0,
    radius: float = 0,
):
    """Set up CAP alert geo_location entities for a single state."""
    cap_entities: dict[str, CAPAlertGeolocation] = {}
    cap_hashes: dict[str, str] = {}

    def _sync_cap_entities():
        # Keep existing alerts during a CAP outage; never treat it as all clear.
        if not cap_coordinator.last_update_success:
            return

        data = cap_coordinator.data or {}
        alerts = data.get("alerts", [])
        seen_ids: set[str] = set()

        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id:
                continue
            if radius and not _within_radius(_cap_alert_distance(hass, alert), radius):
                continue

            # Prefix with state to ensure uniqueness
            full_id = f"{state}_{alert_id}"
            seen_ids.add(full_id)

            alert_hash = hashlib.sha1(
                str(alert).encode("utf-8")
            ).hexdigest()

            ent = cap_entities.get(full_id)
            if ent is None:
                ent = CAPAlertGeolocation(
                    hass, cap_coordinator, entry, alert_id,
                    device_info=device_info,
                    monitored_zones=monitored_zones,
                    state_code=state,
                    zone_buffer=zone_buffer,
                )
                is_new = not _is_registered(hass, ent._attr_unique_id)
                ent.expose_on_add = expose_to_assistants and is_new
                cap_entities[full_id] = ent
                cap_hashes[full_id] = alert_hash
                async_add_entities([ent])
                if is_new:
                    ent.fire_change_event(EVENT_CAP_CREATED)
            else:
                old_hash = cap_hashes.get(full_id)
                if old_hash != alert_hash:
                    cap_hashes[full_id] = alert_hash
                    ent.async_write_ha_state()
                    ent.fire_change_event(EVENT_CAP_UPDATED)

        stale_ids = [eid for eid in cap_entities if eid not in seen_ids]
        if stale_ids:
            registry = er.async_get(hass)
            for sid in stale_ids:
                ent = cap_entities.pop(sid)
                cap_hashes.pop(sid, None)
                ent.fire_change_event(EVENT_CAP_REMOVED)
                if ent.entity_id and registry.async_get(ent.entity_id):
                    registry.async_remove(ent.entity_id)
                    _LOGGER.debug("Removed stale CAP geo entity %s", ent.entity_id)

        # Alerts that expired while HA was down are only in the registry.
        _prune_orphaned_incident_entries(hass, entry, state, cap_entities, cap=True)

    entry.async_on_unload(cap_coordinator.async_add_listener(_sync_cap_entities))
    _sync_cap_entities()


def _parse_cap_points(text: str) -> list[tuple[float, float]]:
    """Parse CAP "lat,lon lat,lon ..." text, skipping malformed pairs."""
    points = []
    for pair in text.split():
        parts = pair.split(",")
        if len(parts) != 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points


def _cap_alert_polygons(alert: dict) -> list[list[tuple[float, float]]]:
    """Return the alert's area polygons as (lat, lon) rings."""
    rings = []
    for area in alert.get("areas") or []:
        for poly_str in area.get("polygon", []):
            ring = _parse_cap_points(poly_str)
            if len(ring) >= 3:
                rings.append(ring)
    return rings


def _cap_alert_centroid(alert: dict | None) -> tuple[float, float] | None:
    """Calculate the centroid of a CAP alert's area."""
    if not alert:
        return None

    all_lats, all_lons = [], []

    for area in alert.get("areas") or []:
        # Handle polygons
        for poly_str in area.get("polygon", []):
            for lat, lon in _parse_cap_points(poly_str):
                all_lats.append(lat)
                all_lons.append(lon)

        # Handle circles ("lat,lon radius"; use the center point)
        for circle_str in area.get("circle", []):
            center = _parse_cap_points(circle_str.split()[0]) if circle_str.split() else []
            for lat, lon in center:
                all_lats.append(lat)
                all_lons.append(lon)

    if all_lats and all_lons:
        return statistics.mean(all_lats), statistics.mean(all_lons)

    return None


def _home_distance(
    hass: HomeAssistant,
    lat: float | None,
    lon: float | None,
    polygons: list | None = None,
) -> tuple[float | None, bool]:
    """Return (km from home, home inside the area) for a location."""
    config = getattr(hass, "config", None)
    home_lat = getattr(config, "latitude", None)
    home_lon = getattr(config, "longitude", None)
    if home_lat is None or home_lon is None:
        return None, False
    return distance_to_incident(home_lat, home_lon, lat, lon, polygons)


def _cap_alert_distance(hass: HomeAssistant, alert: dict) -> float | None:
    centroid = _cap_alert_centroid(alert)
    lat, lon = centroid if centroid else (None, None)
    return _home_distance(hass, lat, lon, _cap_alert_polygons(alert))[0]


def _direction_attrs(hass: HomeAssistant, lat: float | None, lon: float | None, in_area: bool) -> dict:
    """Bearing and compass direction from home, or None when not meaningful."""
    config = getattr(hass, "config", None)
    home_lat = getattr(config, "latitude", None)
    home_lon = getattr(config, "longitude", None)
    if in_area or None in (lat, lon, home_lat, home_lon):
        return {ATTR_BEARING: None, ATTR_DIRECTION: None}
    bearing = round(initial_bearing(home_lat, home_lon, lat, lon))
    return {ATTR_BEARING: bearing, ATTR_DIRECTION: compass_direction(bearing)}


class CAPAlertGeolocation(CoordinatorEntity[CFSCAPDataCoordinator], GeolocationEvent):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert"
    _unrecorded_attributes = UNRECORDED_GEO_ATTRIBUTES
    expose_on_add = False

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CFSCAPDataCoordinator,
        entry: ConfigEntry,
        alert_id: str,
        device_info: dict | None = None,
        monitored_zones: list[str] | None = None,
        state_code: str = "",
        zone_buffer: float = 0,
    ) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._alert_id = alert_id
        self._state_code = state_code
        self._monitored_zones = monitored_zones or []
        self._zone_buffer = zone_buffer
        self._first_seen = dt_now()
        # Use a truncated hash for the unique ID to keep it manageable
        self._alert_hash = hashlib.sha1(f"{state_code}_{alert_id}".encode("utf-8")).hexdigest()[:12]
        # Entity ID format: geo_location.aus_emergency_{state}_cap_{hash}
        self._attr_object_id = f"aus_emergency_{state_code}_cap_{self._alert_hash}".lower()
        self._attr_unique_id = self._attr_object_id
        self.entity_id = f"geo_location.{self._attr_object_id}"
        self._attr_has_entity_name = False
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.expose_on_add:
            _expose_entity_to_voice_assistants(self.hass, self.entity_id)

    def fire_change_event(self, event_type: str) -> None:
        """Fire a CAP alert change event."""
        alert = self._alert_data
        payload = {
            "source": "cap",
            "alert_id": self._alert_id,
            "headline": alert.get("headline") if alert else None,
            "event": alert.get("event") if alert else None,
            "severity": alert.get("severity") if alert else None,
            "urgency": alert.get("urgency") if alert else None,
            "latitude": self.latitude,
            "longitude": self.longitude,
            **self._location_attrs(),
            "first_seen": self._first_seen.isoformat(),
            "changed_at": dt_now().isoformat(),
        }
        self.hass.bus.async_fire(event_type, payload)

    @property
    def _alert_data(self) -> Dict[str, Any] | None:
        if self.coordinator.data:
            for alert in self.coordinator.data.get("alerts", []):
                if alert.get("id") == self._alert_id:
                    return alert
        return None

    @property
    def _centroid(self) -> tuple[float, float] | None:
        """Calculate the centroid of the alert area."""
        return _cap_alert_centroid(self._alert_data)

    def _home_distance(self) -> tuple[float | None, bool]:
        alert = self._alert_data
        polygons = _cap_alert_polygons(alert) if alert else []
        return _home_distance(self.hass, self.latitude, self.longitude, polygons)

    @property
    def name(self) -> str:
        if alert := self._alert_data:
            event = alert.get("event", "Alert")
            areas = alert.get("areas") or [{}]
            area = areas[0].get("areaDesc") or "Unknown Area"
            return f"{event} for {area}"
        return "CAP Alert"

    @property
    def source(self) -> str:
        return "cap"

    @property
    def latitude(self) -> float | None:
        if centroid := self._centroid:
            return centroid[0]
        return None

    @property
    def longitude(self) -> float | None:
        if centroid := self._centroid:
            return centroid[1]
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any] | None:
        attrs = dict(self._alert_data) if self._alert_data else {}

        # Add duration tracking
        duration = (dt_now() - self._first_seen).total_seconds() / 60
        attrs[ATTR_DURATION_MINUTES] = round(duration, 1)
        attrs["first_seen"] = self._first_seen.isoformat()

        attrs.update(self._location_attrs())

        # Add zone membership
        if self._monitored_zones:
            alert = self._alert_data
            matching = _get_zones_for_point(
                self.hass, self.latitude, self.longitude, self._monitored_zones,
                self._zone_buffer, _cap_alert_polygons(alert) if alert else None,
            )
            attrs[ATTR_IN_ZONE] = matching

        return attrs

    def _location_attrs(self) -> dict:
        distance, in_area = self._home_distance()
        return {
            ATTR_DISTANCE_KM: distance,
            ATTR_HOME_IN_AREA: in_area,
            **_direction_attrs(self.hass, self.latitude, self.longitude, in_area),
        }

    @property
    def available(self) -> bool:
        return super().available and self._alert_data is not None

    @property
    def distance(self) -> float | None:
        """Return distance from home to this alert in km (0 inside its area)."""
        return self._home_distance()[0]


class IncidentEntity(GeolocationEvent):
    # Pushed by the coordinator listener; nothing to poll.
    _attr_should_poll = False
    _unrecorded_attributes = UNRECORDED_GEO_ATTRIBUTES
    expose_on_add = False

    def __init__(
        self,
        hass: HomeAssistant,
        item: Dict[str, Any],
        unique_id: str,
        source: str = "unknown",
        device_info: dict | None = None,
        monitored_zones: list[str] | None = None,
        state_code: str = "",
        zone_buffer: float = 0,
    ) -> None:
        self.hass = hass
        self._source = source
        self._zone_buffer = zone_buffer
        self._polygons: list = []
        self._available = True
        self.ended = False
        self._attrs: Dict[str, Any] = {}
        self._latitude: float | None = item.get(ATTR_LATITUDE)
        self._longitude: float | None = item.get(ATTR_LONGITUDE)
        self._name: str = "Emergency Incident"
        self._first_seen = dt_now()
        self._last_seen = self._first_seen
        self._last_changed = self._first_seen
        self._monitored_zones = monitored_zones or []
        self._device_info = device_info
        self._state_code = state_code

        self._incident_no = unique_id
        # Get the raw incident number (without state prefix)
        raw_incident_no = item.get(ATTR_INCIDENT_NO) or unique_id.split("_", 1)[-1]
        # Entity ID format: geo_location.aus_emergency_{state}_{incident_no}
        self._attr_object_id = f"aus_emergency_{state_code}_{raw_incident_no}".lower()
        self._attr_unique_id = self._attr_object_id
        # Feed ids can be URLs (NSW guid), so the entity_id needs slugifying.
        self.entity_id = f"geo_location.{slugify(self._attr_object_id)}"
        self._attr_has_entity_name = False

        self._state: str | None = None
        self._last_hash: str | None = None
        self.update_from_item(item, monitored_zones, first=True)

    def _calc_hash(self) -> str:
        parts = [
            str(self._attrs.get(ATTR_STATUS) or ""),
            str(self._attrs.get(ATTR_LEVEL) or ""),
            str(self._attrs.get(ATTR_TYPE) or ""),
            str(self._attrs.get(ATTR_MESSAGE_LINK) or ""),
            str(self._latitude or ""),
            str(self._longitude or ""),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def update_from_item(
        self,
        item: Dict[str, Any],
        monitored_zones: list[str] | None = None,
        first: bool = False
    ) -> bool:
        self._available = True
        self.ended = False
        self._latitude = item.get(ATTR_LATITUDE)
        self._longitude = item.get(ATTR_LONGITUDE)

        self._polygons = item.get(ATTR_POLYGONS) or []
        self._attrs = public_incident(item)

        if self._latitude is not None and self._longitude is not None:
            self._attrs[
                "map_url"
            ] = f"/map?z=14&lat={self._latitude}&lng={self._longitude}"
            self._attrs[
                "google_maps_url"
            ] = f"https://maps.google.com/?q={self._latitude},{self._longitude}"
        else:
            self._attrs["map_url"] = None
            self._attrs["google_maps_url"] = None

        self._attrs["title"] = _build_title(self._attrs)
        self._attrs["summary"] = _build_summary(self._attrs)

        name_parts = []
        if item.get(ATTR_TYPE):
            name_parts.append(item[ATTR_TYPE])
        if item.get(ATTR_LOCATION_NAME):
            name_parts.append(item[ATTR_LOCATION_NAME])
        self._name = " at ".join([str(p) for p in name_parts if p]) or "Emergency Incident"

        self._state = item.get(ATTR_STATUS) or item.get(ATTR_LEVEL)

        now = dt_now()
        self._last_seen = now
        new_hash = self._calc_hash()
        changed = self._last_hash is not None and new_hash != self._last_hash
        if first or changed:
            self._last_changed = self._last_seen
        self._last_hash = new_hash

        # Calculate duration in minutes
        duration = (now - self._first_seen).total_seconds() / 60
        self._attrs[ATTR_DURATION_MINUTES] = round(duration, 1)

        self._attrs["first_seen"] = self._first_seen.isoformat()
        self._attrs["last_seen"] = self._last_seen.isoformat()
        self._attrs["last_changed"] = self._last_changed.isoformat()

        # Check zone membership
        zones = monitored_zones or self._monitored_zones
        if zones:
            matching = _get_zones_for_point(
                self.hass, self._latitude, self._longitude, zones, self._zone_buffer, self._polygons
            )
            self._attrs[ATTR_IN_ZONE] = matching

        return changed

    def fire_change_event(self, event_type: str) -> None:
        payload = {
            "source": self._source,
            "incident_no": self._attrs.get(ATTR_INCIDENT_NO),
            "status": self._attrs.get(ATTR_STATUS),
            "level": self._attrs.get(ATTR_LEVEL),
            "severity": self._attrs.get(ATTR_SEVERITY),
            "type": self._attrs.get(ATTR_TYPE),
            "region": self._attrs.get(ATTR_REGION),
            "location_name": self._attrs.get(ATTR_LOCATION_NAME),
            "latitude": self._latitude,
            "longitude": self._longitude,
            "message_link": self._attrs.get(ATTR_MESSAGE_LINK),
            "changed_at": self._last_seen.isoformat(),
            "hash": self._last_hash,
            "title": self._attrs.get("title"),
            "summary": self._attrs.get("summary"),
            "first_seen": self._first_seen.isoformat(),
            "last_seen": self._last_seen.isoformat(),
            "last_changed": self._last_changed.isoformat(),
            ATTR_DURATION_MINUTES: self._attrs.get(ATTR_DURATION_MINUTES),
            ATTR_IN_ZONE: self._attrs.get(ATTR_IN_ZONE, []),
            ATTR_DISTANCE_KM: self._attrs.get(ATTR_DISTANCE_KM),
            ATTR_BEARING: self._attrs.get(ATTR_BEARING),
            ATTR_DIRECTION: self._attrs.get(ATTR_DIRECTION),
            ATTR_HOME_IN_AREA: self._attrs.get(ATTR_HOME_IN_AREA, False),
        }
        self.hass.bus.async_fire(event_type, payload)

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> str:
        return self._source

    @property
    def latitude(self) -> float | None:
        return self._latitude

    @property
    def longitude(self) -> float | None:
        return self._longitude

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    @property
    def available(self) -> bool:
        return self._available

    @property
    def distance(self) -> float | None:
        """Return distance from home to this incident in km (0 inside its warning area)."""
        if ATTR_DISTANCE_KM in self._attrs:
            return self._attrs[ATTR_DISTANCE_KM]
        return _home_distance(self.hass, self._latitude, self._longitude, self._polygons)[0]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.expose_on_add:
            _expose_entity_to_voice_assistants(self.hass, self.entity_id)

    def mark_stale(self, *, ended: bool = False) -> None:
        """Mark unavailable; ended means the incident left the feed (not a feed outage)."""
        self._available = False
        if ended:
            self.ended = True

    @property
    def object_id(self) -> str | None:
        return self._attr_object_id

    @property
    def has_entity_name(self) -> bool:
        return False

    @property
    def device_info(self):
        return self._device_info
