from __future__ import annotations

import logging
from typing import Any, Dict, List
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    SOURCE_SA_CFS,
    DEVICE_INFO_SA_CFS,
    ATTR_SEVERITY,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_LOCATION_NAME,
    ATTR_INCIDENT_NO,
)
from .coordinator import CFSDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    update_seconds = entry.options.get(
        CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )
    coordinator = CFSDataCoordinator(hass, update_seconds)
    await coordinator.async_config_entry_first_refresh()

    sensor = ActiveIncidentsSensor(coordinator, entry)
    summary_sensor = IncidentSummarySensor(coordinator, entry)
    async_add_entities([sensor, summary_sensor], update_before_add=True)

    # Allow aus_emergency.refresh to trigger an immediate update
    async def _refresh_cb():
        await coordinator.async_request_refresh()

    hass.data.setdefault(DOMAIN, {}).setdefault("refresh_cbs", []).append(_refresh_cb)


class ActiveIncidentsSensor(CoordinatorEntity[CFSDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Active incidents"
    _attr_icon = "mdi:alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_info = DEVICE_INFO_SA_CFS

    def __init__(self, coordinator: CFSDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_active_incidents"
        self._source = SOURCE_SA_CFS

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

        return {
            "source": self._source,
            "summary_generated": dt_now().isoformat(),
            "counts": counts,
            "incidents": incidents,
        }


class IncidentSummarySensor(CoordinatorEntity[CFSDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Incident summary"
    _attr_icon = "mdi:alert-decagram"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_info = DEVICE_INFO_SA_CFS

    def __init__(self, coordinator: CFSDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_incident_summary"
        self._source = SOURCE_SA_CFS

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
