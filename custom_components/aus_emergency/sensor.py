from __future__ import annotations

import logging
from typing import Any, Dict, List

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_STATE,
    CONF_STATES,
    DEFAULT_STATE,
    DEFAULT_STATES,
    STATE_DEVICE_INFO,
    DEVICE_INFO_SA_CFS,
    ATTR_SEVERITY,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_LOCATION_NAME,
    ATTR_INCIDENT_NO,
    HIGH_SEVERITY_LEVELS,
    MAX_INCIDENTS_IN_ATTRIBUTES,
)
from .coordinator import IncidentDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the sensor platform."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    incident_coordinators = entry_data.get("incident_coordinators", {})

    # Get configured states (support both new multi-state and legacy single-state)
    states = entry_data.get("states", [])
    if not states:
        old_state = entry.options.get(CONF_STATE) or entry.data.get(CONF_STATE, DEFAULT_STATE)
        states = [old_state] if old_state else DEFAULT_STATES

    sensors = []
    for state in states:
        coordinator = incident_coordinators.get(state)
        if not coordinator:
            continue

        device_info = STATE_DEVICE_INFO.get(state, DEVICE_INFO_SA_CFS)
        sensors.extend([
            ActiveIncidentsSensor(coordinator, entry, device_info, state),
            IncidentSummarySensor(coordinator, entry, device_info, state),
            HighSeverityIncidentsSensor(coordinator, entry, device_info, state),
        ])

    async_add_entities(sensors, update_before_add=True)


class ActiveIncidentsSensor(CoordinatorEntity[IncidentDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IncidentDataCoordinator,
        entry: ConfigEntry,
        device_info: dict,
        state: str = "",
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._state_code = state
        self._attr_name = f"{state} Active incidents" if state else "Active incidents"
        self._attr_unique_id = f"{entry.entry_id}_{state}_active_incidents"
        self._attr_device_info = device_info
        # Use standardized entity_id pattern
        if state:
            self.entity_id = f"sensor.{state.lower()}_active_incidents"

    @property
    def native_value(self) -> int:
        return len(self.incidents)

    @property
    def incidents(self) -> List[Dict[str, Any]]:
        data = self.coordinator.data or {}
        return data.get("incidents", []) or []

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        incidents = self.incidents
        counts = {
            "total": len(incidents),
            "info": 0,
            "advice": 0,
            "watch_and_act": 0,
            "emergency_warning": 0,
            "all_clear": 0,
        }
        for p in incidents:
            sev = p.get(ATTR_SEVERITY, "info")
            counts[sev] = counts.get(sev, 0) + 1

        # Truncate incidents to avoid exceeding 16KB attribute limit
        truncated = len(incidents) > MAX_INCIDENTS_IN_ATTRIBUTES
        incidents_to_store = incidents[:MAX_INCIDENTS_IN_ATTRIBUTES] if truncated else incidents

        return {
            "source": self.coordinator.source,
            "summary_generated": dt_now().isoformat(),
            "counts": counts,
            "incidents": incidents_to_store,
            "incidents_truncated": truncated,
            "incidents_omitted": max(0, len(incidents) - MAX_INCIDENTS_IN_ATTRIBUTES),
        }


class HighSeverityIncidentsSensor(CoordinatorEntity[IncidentDataCoordinator], SensorEntity):
    """Sensor that tracks only high-severity incidents (emergency_warning, watch_and_act)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-octagon"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IncidentDataCoordinator,
        entry: ConfigEntry,
        device_info: dict,
        state: str = "",
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._state_code = state
        self._attr_name = f"{state} High severity incidents" if state else "High severity incidents"
        self._attr_unique_id = f"{entry.entry_id}_{state}_high_severity_incidents"
        self._attr_device_info = device_info
        # Use standardized entity_id pattern
        if state:
            self.entity_id = f"sensor.{state.lower()}_high_severity_incidents"

    @property
    def incidents(self) -> List[Dict[str, Any]]:
        data = self.coordinator.data or {}
        all_incidents = data.get("incidents", []) or []
        return [
            inc for inc in all_incidents
            if inc.get(ATTR_SEVERITY) in HIGH_SEVERITY_LEVELS
        ]

    @property
    def native_value(self) -> int:
        return len(self.incidents)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        incidents = self.incidents
        counts = {
            "emergency_warning": 0,
            "watch_and_act": 0,
        }
        for p in incidents:
            sev = p.get(ATTR_SEVERITY)
            if sev in counts:
                counts[sev] += 1

        # Truncate incidents to avoid exceeding 16KB attribute limit
        truncated = len(incidents) > MAX_INCIDENTS_IN_ATTRIBUTES
        incidents_to_store = incidents[:MAX_INCIDENTS_IN_ATTRIBUTES] if truncated else incidents

        return {
            "source": self.coordinator.source,
            "summary_generated": dt_now().isoformat(),
            "counts": counts,
            "incidents": incidents_to_store,
            "incidents_truncated": truncated,
            "incidents_omitted": max(0, len(incidents) - MAX_INCIDENTS_IN_ATTRIBUTES),
            "severity_levels": HIGH_SEVERITY_LEVELS,
        }


class IncidentSummarySensor(CoordinatorEntity[IncidentDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-decagram"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: IncidentDataCoordinator,
        entry: ConfigEntry,
        device_info: dict,
        state: str = "",
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._state_code = state
        self._attr_name = f"{state} Incident summary" if state else "Incident summary"
        self._attr_unique_id = f"{entry.entry_id}_{state}_incident_summary"
        self._attr_device_info = device_info
        # Use standardized entity_id pattern
        if state:
            self.entity_id = f"sensor.{state.lower()}_incident_summary"

    @property
    def incidents(self) -> List[Dict[str, Any]]:
        data = self.coordinator.data or {}
        return data.get("incidents", []) or []

    @property
    def native_value(self) -> str:
        incidents = self.incidents
        if not incidents:
            return "No active incidents."

        spoken = []
        # Limit to what will fit in 255 chars
        for item in incidents:
            itype = item.get(ATTR_TYPE) or ""
            title = (
                item.get(ATTR_LOCATION_NAME)
                or item.get(ATTR_INCIDENT_NO)
                or "Incident"
            )
            status = item.get(ATTR_STATUS) or item.get(ATTR_LEVEL) or "Unknown"
            prefix = f"{itype} " if itype else ""
            next_incident = f"{prefix}{title} is {status}."

            # Check if adding the next incident exceeds the limit
            if (
                len(f"Incidents: {len(incidents)}. " + " ".join(spoken) + next_incident)
                > 250
            ):
                break
            spoken.append(next_incident)

        more = len(incidents) - len(spoken)
        tail = f" Plus {more} more." if more > 0 else ""
        summary = f"Incidents: {len(incidents)}. " + " ".join(spoken) + tail

        # Final check to prevent error
        if len(summary) > 255:
            summary = summary[:252] + "..."

        return summary
