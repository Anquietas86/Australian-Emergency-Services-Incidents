
from __future__ import annotations

import logging
from typing import Any, Dict
from datetime import timedelta

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL,
    CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE,
    SOURCE_SA_CFS,
    ATTR_INCIDENT_NO, ATTR_TYPE, ATTR_STATUS, ATTR_LEVEL, ATTR_REGION,
    ATTR_LOCATION_NAME, ATTR_MESSAGE, ATTR_MESSAGE_LINK, ATTR_RESOURCES,
    ATTR_AIRCRAFT, ATTR_DATE, ATTR_TIME, ATTR_AGENCY,
)
from .coordinator import CFSDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_SOURCE = SOURCE_SA_CFS

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    update_seconds = entry.options.get(CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    remove_stale = entry.options.get(CONF_REMOVE_STALE, entry.data.get(CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE))

    coordinator = CFSDataCoordinator(hass, update_seconds)
    await coordinator.async_config_entry_first_refresh()

    entities: dict[str, CFSIncidentEntity] = {}

    async def _sync_entities(now=None):
        data = coordinator.data or {}
        incidents = data.get("incidents", [])
        seen_ids: set[str] = set()

        for item in incidents:
            inc_no = (item.get("IncidentNo") or "").strip()
            if not inc_no:
                inc_no = f"{item.get('Location_name','unknown')}-{item.get('Date','')}-{item.get('Time','')}"

            seen_ids.add(inc_no)

            ent = entities.get(inc_no)
            if ent is None:
                ent = CFSIncidentEntity(item, unique_id=inc_no)
                entities[inc_no] = ent
                async_add_entities([ent], update_before_add=True)
            else:
                ent.update_from_item(item)

        stale_ids = [eid for eid in list(entities.keys()) if eid not in seen_ids]
        if stale_ids:
            if remove_stale:
                registry = er.async_get(hass)
                for sid in stale_ids:
                    ent = entities.pop(sid, None)
                    if ent and ent.entity_id:
                        entry_reg = registry.async_get(ent.entity_id)
                        if entry_reg:
                            registry.async_remove(ent.entity_id)
                            _LOGGER.debug("Removed stale geo entity %s", ent.entity_id)
            else:
                for sid in stale_ids:
                    ent = entities.pop(sid, None)
                    if ent:
                        ent.mark_stale()

    await _sync_entities()

    async def _periodic_update(now):
        await coordinator.async_request_refresh()
        await _sync_entities()

    async_track_time_interval(hass, _periodic_update, timedelta(seconds=update_seconds))

class CFSIncidentEntity(GeolocationEvent):
    """A geolocation entity for a single CFS incident."""

    def __init__(self, item: Dict[str, Any], unique_id: str | None = None) -> None:
        self._source = PLATFORM_SOURCE
        self._available = True
        self._attrs: Dict[str, Any] = {}
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._name: str = "SA CFS Incident"

        inc_no = (item.get("IncidentNo") or "").strip()
        self._incident_no = inc_no or (unique_id or "unknown")
        self._unique_id = f"{self._source}_{self._incident_no}".lower()

        self.update_from_item(item)

    def update_from_item(self, item: Dict[str, Any]) -> None:
        loc = item.get("Location")
        lat = lon = None
        if isinstance(loc, str) and "," in loc:
            parts = [p.strip() for p in loc.split(",")]
            if len(parts) == 2:
                try:
                    lat = float(parts[0]); lon = float(parts[1])
                except Exception:
                    pass
        self._latitude = lat
        self._longitude = lon

        self._attrs[ATTR_INCIDENT_NO] = item.get("IncidentNo")
        self._attrs[ATTR_DATE] = item.get("Date")
        self._attrs[ATTR_TIME] = item.get("Time")
        self._attrs[ATTR_MESSAGE] = item.get("Message")
        self._attrs[ATTR_MESSAGE_LINK] = item.get("Message_link")
        self._attrs[ATTR_LOCATION_NAME] = item.get("Location_name")
        self._attrs[ATTR_REGION] = item.get("Region")
        self._attrs[ATTR_TYPE] = item.get("Type")
        self._attrs[ATTR_STATUS] = item.get("Status")
        self._attrs[ATTR_LEVEL] = item.get("Level")
        self._attrs[ATTR_RESOURCES] = item.get("Resources")
        self._attrs[ATTR_AIRCRAFT] = item.get("Aircraft")
        self._attrs[ATTR_AGENCY] = item.get("Service") or item.get("Agency")

        name_parts = []
        if item.get("Type"):
            name_parts.append(item["Type"])
        if item.get("Location_name"):
            name_parts.append(item["Location_name"])
        if item.get("Service") or item.get("Agency"):
            name_parts.append(item.get("Service") or item.get("Agency"))
        self._name = " - ".join([str(p) for p in name_parts if p]) or "SA CFS Incident"

        self._state = item.get("Status") or item.get("Level")

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
    def suggested_object_id(self) -> str | None:
        return f"{self._source}_{self._incident_no}".lower() if self._incident_no else None

    @property
    def has_entity_name(self) -> bool:
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {("aus_emergency", "sa_cfs")},
            "name": "Australian Emergency (SA)",
            "manufacturer": "SA CFS / SES",
            "model": "CRIIMSON Feed",
        }
