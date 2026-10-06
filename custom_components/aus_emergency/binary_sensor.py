"""Binary sensor that turns on when a serious incident is near home."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_ALERT_RADIUS,
    CONF_ALERT_MIN_SEVERITY,
    DEFAULT_ALERT_RADIUS,
    DEFAULT_ALERT_MIN_SEVERITY,
    SEVERITY_ORDER,
    UNAVAILABLE_STATES,
    ATTR_DISTANCE_KM,
    ATTR_HOME_IN_AREA,
    ATTR_SEVERITY,
    ATTR_TYPE,
    ATTR_LOCATION_NAME,
    ATTR_DIRECTION,
)
from .utils import public_incident

# Incidents listed in attributes; the nearest come first
MAX_NEARBY_IN_ATTRIBUTES = 10


def _title(incident: dict[str, Any]) -> str:
    parts = [incident.get(ATTR_TYPE), incident.get(ATTR_LOCATION_NAME)]
    return " at ".join(str(p) for p in parts if p) or "Emergency incident"


def matching_incidents(
    incidents_by_state: dict[str, list[dict[str, Any]]],
    radius_km: float,
    min_severity: str,
) -> list[dict[str, Any]]:
    """Return incidents at or above min_severity that are within radius_km of home.

    Home inside an incident's warning area always matches, whatever the radius.
    """
    threshold = SEVERITY_ORDER.index(min_severity) if min_severity in SEVERITY_ORDER else 0
    matches = []
    for state, incidents in incidents_by_state.items():
        for incident in incidents:
            severity = incident.get(ATTR_SEVERITY)
            if severity not in SEVERITY_ORDER or SEVERITY_ORDER.index(severity) < threshold:
                continue
            distance = incident.get(ATTR_DISTANCE_KM)
            if incident.get(ATTR_HOME_IN_AREA) or (distance is not None and distance <= radius_km):
                matches.append({**incident, "state": state})
    matches.sort(key=lambda i: (
        not i.get(ATTR_HOME_IN_AREA),
        -SEVERITY_ORDER.index(i[ATTR_SEVERITY]),
        i.get(ATTR_DISTANCE_KM) if i.get(ATTR_DISTANCE_KM) is not None else float("inf"),
    ))
    return matches


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the nearby emergency binary sensor."""
    coordinators = hass.data[DOMAIN][entry.entry_id].get("incident_coordinators", {})
    if coordinators:
        async_add_entities([NearbyEmergencyBinarySensor(entry, coordinators)])


class NearbyEmergencyBinarySensor(BinarySensorEntity):
    """On when a qualifying incident is within the alert radius of home.

    "On" only needs one feed to confirm an incident. "Off" is a claim that
    nothing qualifies, so it needs every feed: if any feed is down and
    nothing matched, the sensor is unavailable rather than a false all clear.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "emergency_nearby"
    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_should_poll = False
    # The incident list is bulky and changes every poll.
    _unrecorded_attributes = frozenset({"incidents"})

    def __init__(self, entry: ConfigEntry, coordinators: dict) -> None:
        self._coordinators = coordinators
        options = {**entry.data, **(entry.options or {})}
        self._radius = float(options.get(CONF_ALERT_RADIUS, DEFAULT_ALERT_RADIUS) or 0)
        self._min_severity = options.get(CONF_ALERT_MIN_SEVERITY, DEFAULT_ALERT_MIN_SEVERITY)
        self._attr_unique_id = f"{entry.entry_id}_emergency_nearby"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for coordinator in self._coordinators.values():
            self.async_on_remove(coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    def _healthy_incidents(self) -> dict[str, list[dict[str, Any]]]:
        return {
            state: (coordinator.data or {}).get("incidents", []) or []
            for state, coordinator in self._coordinators.items()
            if coordinator.last_update_success and coordinator.data is not None
        }

    def _failed_states(self) -> list[str]:
        # States without any feed are always down; they must not hide the others.
        return sorted(
            state for state, coordinator in self._coordinators.items()
            if state not in UNAVAILABLE_STATES
            and not (coordinator.last_update_success and coordinator.data is not None)
        )

    @property
    def _matches(self) -> list[dict[str, Any]]:
        return matching_incidents(self._healthy_incidents(), self._radius, self._min_severity)

    @property
    def available(self) -> bool:
        return bool(self._matches) or not self._failed_states()

    @property
    def is_on(self) -> bool:
        return bool(self._matches)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        matches = self._matches
        nearest = matches[0] if matches else None
        return {
            "count": len(matches),
            "alert_radius_km": self._radius,
            "min_severity": self._min_severity,
            "nearest_title": _title(nearest) if nearest else None,
            "nearest_severity": nearest.get(ATTR_SEVERITY) if nearest else None,
            "nearest_distance_km": nearest.get(ATTR_DISTANCE_KM) if nearest else None,
            "nearest_direction": nearest.get(ATTR_DIRECTION) if nearest else None,
            "home_in_warning_area": any(m.get(ATTR_HOME_IN_AREA) for m in matches),
            "unavailable_states": self._failed_states(),
            "incidents": [public_incident(m) for m in matches[:MAX_NEARBY_IN_ATTRIBUTES]],
        }
