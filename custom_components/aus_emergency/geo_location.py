from __future__ import annotations

import logging, hashlib
from typing import Any, Dict
from datetime import timedelta

import statistics
from typing import Any, Dict
import hashlib

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import now as dt_now
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_REMOVE_STALE,
    DEFAULT_REMOVE_STALE,
    SOURCE_SA_CFS,
    ATTR_INCIDENT_NO,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_REGION,
    ATTR_LOCATION_NAME,
    ATTR_MESSAGE,
    ATTR_MESSAGE_LINK,
    ATTR_RESOURCES,
    ATTR_AIRCRAFT,
    ATTR_DATE,
    ATTR_TIME,
    ATTR_AGENCY,
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_REMOVED,
    DEVICE_INFO_SA_CFS,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_SEVERITY,
)

from .coordinator import CFSDataCoordinator
from .cap_coordinator import CFSCAPDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_SOURCE = SOURCE_SA_CFS


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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    remove_stale = entry.options.get(
        CONF_REMOVE_STALE, entry.data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE)
    )

    coordinators = hass.data[DOMAIN][entry.entry_id]
    cfs_coordinator: CFSDataCoordinator = coordinators["cfs_coordinator"]
    cap_coordinator: CFSCAPDataCoordinator = coordinators["cap_coordinator"]

    # Incident entities
    incident_entities: dict[str, CFSIncidentEntity] = {}

    def _sync_incident_entities():
        data = cfs_coordinator.data or {}
        incidents = data.get("incidents", [])
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

            seen_ids.add(inc_no)

            ent = incident_entities.get(inc_no)
            if ent is None:
                ent = CFSIncidentEntity(hass, item, unique_id=inc_no)
                incident_entities[inc_no] = ent
                async_add_entities([ent], update_before_add=True)
                ent.fire_change_event(EVENT_CREATED)
            else:
                if ent.update_from_item(item):
                    ent.fire_change_event(EVENT_UPDATED)

        stale_ids = [eid for eid in list(incident_entities.keys()) if eid not in seen_ids]
        if stale_ids:
            if remove_stale:
                registry = er.async_get(hass)
                for sid in stale_ids:
                    ent = incident_entities.pop(sid, None)
                    if ent:
                        ent.fire_change_event(EVENT_REMOVED)
                        if ent.entity_id:
                            entry_reg = registry.async_get(ent.entity_id)
                            if entry_reg:
                                registry.async_remove(ent.entity_id)
                                _LOGGER.debug("Removed stale geo entity %s", ent.entity_id)
            else:
                for sid in stale_ids:
                    ent = incident_entities.pop(sid, None)
                    if ent:
                        ent.mark_stale()
                        ent.fire_change_event(EVENT_REMOVED)

    cfs_coordinator.async_add_listener(_sync_incident_entities)
    _sync_incident_entities()

    # CAP alert entities
    cap_entities: dict[str, CAPAlertGeolocation] = {}

    def _sync_cap_entities():
        data = cap_coordinator.data or {}
        alerts = data.get("alerts", [])
        seen_ids: set[str] = set()

        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id:
                continue
            
            seen_ids.add(alert_id)

            ent = cap_entities.get(alert_id)
            if ent is None:
                ent = CAPAlertGeolocation(cap_coordinator, entry, alert_id)
                cap_entities[alert_id] = ent
                async_add_entities([ent], update_before_add=True)
            else:
                ent.async_write_ha_state() # Trigger update

        stale_ids = [eid for eid in list(cap_entities.keys()) if eid not in seen_ids]
        if stale_ids:
            registry = er.async_get(hass)
            for sid in stale_ids:
                ent = cap_entities.pop(sid, None)
                if ent and ent.entity_id:
                    registry.async_remove(ent.entity_id)
                    _LOGGER.debug("Removed stale CAP geo entity %s", ent.entity_id)

    cap_coordinator.async_add_listener(_sync_cap_entities)
    _sync_cap_entities()


class CAPAlertGeolocation(CoordinatorEntity[CFSCAPDataCoordinator], GeolocationEvent):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert"
    _attr_device_info = DEVICE_INFO_SA_CFS

    def __init__(
        self, coordinator: CFSCAPDataCoordinator, entry: ConfigEntry, alert_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._alert_id = alert_id
        # Use a truncated hash for the unique ID to keep it manageable
        self._alert_hash = hashlib.sha1(alert_id.encode("utf-8")).hexdigest()[:12]
        self._attr_unique_id = f"aus_emergency_cap_{self._alert_hash}"
        self.entity_id = f"geo_location.aus_emergency_cap_{self._alert_hash}"
        self._attr_object_id = f"aus_emergency_cap_{self._alert_hash}"
        self._attr_has_entity_name = False

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
        alert = self._alert_data
        if not alert:
            return None

        all_lats, all_lons = [], []

        for area in alert.get("areas", []):
            # Handle polygons
            for poly_str in area.get("polygon", []):
                points = [p.strip().split(',') for p in poly_str.split(' ')]
                for lat_str, lon_str in points:
                    try:
                        all_lats.append(float(lat_str))
                        all_lons.append(float(lon_str))
                    except (ValueError, TypeError):
                        pass
            
            # Handle circles (use center point)
            for circle_str in area.get("circle", []):
                parts = circle_str.replace(',', ' ').split()
                if len(parts) >= 2:
                    try:
                        lat, lon = float(parts[0]), float(parts[1])
                        all_lats.append(lat)
                        all_lons.append(lon)
                    except (ValueError, TypeError):
                        pass

        if all_lats and all_lons:
            return statistics.mean(all_lats), statistics.mean(all_lons)
        
        return None

    @property
    def name(self) -> str:
        if (alert := self._alert_data) and (headline := alert.get("headline")):
            return f"CAP Alert: {headline}"
        return "CAP Alert"

    @property
    def source(self) -> str:
        return "sa_cfs_cap"

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
        return self._alert_data

    @property
    def available(self) -> bool:
        return super().available and self._alert_data is not None
class CFSIncidentEntity(GeolocationEvent):
    def __init__(
        self, hass: HomeAssistant, item: Dict[str, Any], unique_id: str
    ) -> None:
        self.hass = hass
        self._source = PLATFORM_SOURCE
        self._available = True
        self._attrs: Dict[str, Any] = {}
        self._latitude: float | None = item.get(ATTR_LATITUDE)
        self._longitude: float | None = item.get(ATTR_LONGITUDE)
        self._name: str = "SA CFS Incident"
        self._first_seen = dt_now().isoformat()
        self._last_seen = self._first_seen
        self._last_changed = self._first_seen

        self._incident_no = unique_id
        self._attr_unique_id = f"aus_emergency_{self._incident_no}".lower()
        self._attr_object_id = f"aus_emergency_{self._incident_no}".lower()
        self._attr_has_entity_name = False

        self._last_hash: str | None = None
        self.update_from_item(item, first=True)

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

    def update_from_item(self, item: Dict[str, Any], first: bool = False) -> bool:
        self._latitude = item.get(ATTR_LATITUDE)
        self._longitude = item.get(ATTR_LONGITUDE)

        self._attrs = item.copy()

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
        if item.get(ATTR_AGENCY):
            name_parts.append(item[ATTR_AGENCY])
        self._name = " - ".join([str(p) for p in name_parts if p]) or "SA CFS Incident"

        self._state = item.get(ATTR_STATUS) or item.get(ATTR_LEVEL)

        self._last_seen = dt_now().isoformat()
        new_hash = self._calc_hash()
        changed = self._last_hash is not None and new_hash != self._last_hash
        if first or changed:
            self._last_changed = self._last_seen
        self._last_hash = new_hash

        self._attrs["first_seen"] = self._first_seen
        self._attrs["last_seen"] = self._last_seen
        self._attrs["last_changed"] = self._last_changed

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
            "changed_at": self._last_seen,
            "hash": self._last_hash,
            "title": self._attrs.get("title"),
            "summary": self._attrs.get("summary"),
            "first_seen": self._first_seen,
            "last_seen": self._last_seen,
            "last_changed": self._last_changed,
        }
        self.hass.bus.async_fire(event_type, payload)

    @property
    def unique_id(self) -> str:
        return self._unique_id

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
    def state(self) -> str | None:
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    @property
    def available(self) -> bool:
        return self._available

    def mark_stale(self) -> None:
        self._available = False

    @property
    def object_id(self) -> str | None:
        return self._attr_object_id

    @property
    def has_entity_name(self) -> bool:
        return False

    @property
    def device_info(self):
        return DEVICE_INFO_SA_CFS
