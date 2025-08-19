
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
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL,
    SOURCE_SA_CFS,
    ATTR_INCIDENT_NO, ATTR_TYPE, ATTR_STATUS, ATTR_LEVEL, ATTR_REGION,
    ATTR_LOCATION_NAME, ATTR_MESSAGE_LINK, ATTR_DATE, ATTR_TIME, ATTR_AGENCY
)
from .coordinator import CFSDataCoordinator

_LOGGER = logging.getLogger(__name__)

def _norm(level: str | None, status: str | None) -> str:
    t = (str(level) or str(status) or "").lower()
    if "emergency" in t:
        return "emergency_warning"
    if "watch" in t:
        return "watch_and_act"
    if "advice" in t:
        return "advice"
    if "safe" in t or "all clear" in t:
        return "all_clear"
    return "info"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    update_seconds = entry.options.get(CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    coordinator = CFSDataCoordinator(hass, update_seconds)
    await coordinator.async_config_entry_first_refresh()

    sensor = ActiveIncidentsSensor(coordinator, entry)
    async_add_entities([sensor], update_before_add=True)

    async def _periodic_update(now):
        await coordinator.async_request_refresh()
        sensor.async_write_ha_state()

    async_track_time_interval(hass, _periodic_update, timedelta(seconds=update_seconds))

    async def _refresh_cb():
        await coordinator.async_request_refresh()
        sensor.async_write_ha_state()

    hass.data.setdefault(DOMAIN, {}).setdefault("refresh_cbs", []).append(_refresh_cb)


class ActiveIncidentsSensor(CoordinatorEntity[CFSDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Active incidents"
    _attr_icon = "mdi:alert"
    _attr_entity_category = "diagnostic"

    def __init__(self, coordinator: CFSDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_active_incidents"
        self._source = SOURCE_SA_CFS
        self._last_summary_ts = None

    @property
    def native_value(self) -> int:
        return len(self._current_incidents())

    def _current_incidents(self) -> List[Dict[str, Any]]:
        data = self.coordinator.data or {}
        incidents = data.get("incidents", []) or []
        parsed: List[Dict[str, Any]] = []
        for item in incidents:
            inc_no = (item.get("IncidentNo") or "").strip() or None
            lat = lon = None
            loc = item.get("Location")
            if isinstance(loc, str) and "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                if len(parts) == 2:
                    try:
                        lat = float(parts[0]); lon = float(parts[1])
                    except Exception:
                        pass
            sev = _norm(item.get("Level"), item.get("Status"))
            parsed.append({
                ATTR_INCIDENT_NO: inc_no,
                ATTR_TYPE: item.get("Type"),
                ATTR_STATUS: item.get("Status"),
                ATTR_LEVEL: item.get("Level"),
                "severity": sev,
                ATTR_LOCATION_NAME: item.get("Location_name"),
                ATTR_REGION: item.get("Region"),
                ATTR_DATE: item.get("Date"),
                ATTR_TIME: item.get("Time"),
                ATTR_MESSAGE_LINK: item.get("Message_link"),
                ATTR_AGENCY: item.get("Service") or item.get("Agency"),
                "latitude": lat,
                "longitude": lon,
            })
        return parsed

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        incidents = self._current_incidents()
        counts = {"total": len(incidents), "info":0,"advice":0,"watch_and_act":0,"emergency_warning":0,"all_clear":0}
        for p in incidents:
            counts[p.get("severity","info")] = counts.get(p.get("severity","info"),0) + 1
        self._last_summary_ts = dt_now().isoformat()
        return {
            "source": self._source,
            "summary_generated": self._last_summary_ts,
            "counts": counts,
            "incidents": incidents,
        }

    @property
    def device_info(self):
        return {
            "identifiers": {("aus_emergency", "sa_cfs")},
            "name": "Australian Emergency (SA)",
            "manufacturer": "SA CFS / SES",
            "model": "CRIIMSON Feed",
        }
