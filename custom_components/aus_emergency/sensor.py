from __future__ import annotations

import logging
from typing import Any, Dict, List
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    DEVICE_INFO_SA_CFS,
    ATTR_SEVERITY,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_LOCATION_NAME,
    ATTR_INCIDENT_NO,
    SOURCE_SA_CFS,
)
from .coordinator import CFSDataCoordinator
from .cap_coordinator import CFSCAPDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the sensor platform."""
    coordinators = hass.data[DOMAIN][entry.entry_id]
    cfs_coordinator = coordinators["cfs_coordinator"]
    cap_coordinator = coordinators["cap_coordinator"]

    # Existing JSON-based sensors
    sensor = ActiveIncidentsSensor(cfs_coordinator, entry)
    summary_sensor = IncidentSummarySensor(cfs_coordinator, entry)
    async_add_entities([sensor, summary_sensor], update_before_add=True)

    # Manage dynamic CAP alert sensors
    managed_sensors: Dict[str, CAPAlertSensor] = {}

    def _update_cap_sensors():
        """Add/remove CAP alert sensors."""
        new_alerts = cap_coordinator.data.get("alerts", []) if cap_coordinator.data else []
        new_alert_ids = {alert["id"] for alert in new_alerts}

        # Remove old sensors
        for alert_id in list(managed_sensors.keys()):
            if alert_id not in new_alert_ids:
                hass.async_create_task(managed_sensors[alert_id].async_remove())
                del managed_sensors[alert_id]

        # Add new sensors
        new_entities = []
        for alert in new_alerts:
            if alert["id"] not in managed_sensors:
                sensor = CAPAlertSensor(cap_coordinator, entry, alert["id"])
                managed_sensors[alert["id"]] = sensor
                new_entities.append(sensor)
        
        if new_entities:
            async_add_entities(new_entities)

    cap_coordinator.async_add_listener(_update_cap_sensors)
    _update_cap_sensors()


class CAPAlertSensor(CoordinatorEntity[CFSCAPDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_info = DEVICE_INFO_SA_CFS

    def __init__(
        self, coordinator: CFSCAPDataCoordinator, entry: ConfigEntry, alert_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._alert_id = alert_id
        self._attr_unique_id = f"{entry.entry_id}_cap_{alert_id}"

    @property
    def _alert_data(self) -> Dict[str, Any] | None:
        """Return the specific alert data for this entity."""
        if self.coordinator.data:
            for alert in self.coordinator.data.get("alerts", []):
                if alert.get("id") == self._alert_id:
                    return alert
        return None

    @property
    def name(self) -> str:
        if (alert := self._alert_data) and (headline := alert.get("headline")):
            return f"CAP Alert: {headline}"
        return "CAP Alert"

    @property
    def native_value(self) -> str | None:
        if alert := self._alert_data:
            return alert.get("event")
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any] | None:
        return self._alert_data

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._alert_data is not None


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
