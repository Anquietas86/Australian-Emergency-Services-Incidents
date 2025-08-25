from __future__ import annotations

import logging, hashlib
from typing import Any, Dict
from datetime import timedelta

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import now as dt_now

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL,
    CONF_REMOVE_STALE, DEFAULT_REMOVE_STALE,
    SOURCE_SA_CFS,
    ATTR_INCIDENT_NO, ATTR_TYPE, ATTR_STATUS, ATTR_LEVEL, ATTR_REGION,
    ATTR_LOCATION_NAME, ATTR_MESSAGE, ATTR_MESSAGE_LINK, ATTR_RESOURCES,
    ATTR_AIRCRAFT, ATTR_DATE, ATTR_TIME, ATTR_AGENCY,
    EVENT_CREATED, EVENT_UPDATED, EVENT_REMOVED,
)

from .coordinator import CFSDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_SOURCE = SOURCE_SA_CFS

def _normalize_severity(level: str | None, status: str | None) -> str:
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

def _build_title(attrs: dict) -> str:
    typ = attrs.get("Type") or "Incident"
    loc = attrs.get("Location_name") or "Unknown location"
    sev = _normalize_severity(attrs.get("Level"), attrs.get("Status"))
    sev_disp = {
        "emergency_warning": "Emergency",
        "watch_and_act": "Watch and Act",
        "advice": "Advice",
        "all_clear": "All clear",
        "info": "Info",
    }.get(sev, "Info")
    return f"{typ} – {loc} ({sev_disp})"

def _build_summary(attrs: dict) -> str:
    st = attrs.get("Status") or attrs.get("Level") or "Unknown status"
    reg = attrs.get("Region") or ""
    when = (attrs.get("Date") or "") + (" " + (attrs.get("Time") or "") if attrs.get("Time") else "")
    return " · ".join([s for s in [st, reg, when] if s])

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
                ent = CFSIncidentEntity(hass, item, unique_id=inc_no)
                entities[inc_no] = ent
                async_add_entities([ent], update_before_add=True)
                ent.fire_change_event(EVENT_CREATED)
            else:
                if ent.update_from_item(item):
                    ent.fire_change_event(EVENT_UPDATED)

        stale_ids = [eid for eid in list(entities.keys()) if eid not in seen_ids]
        if stale_ids:
            if remove_stale:
                registry = er.async_get(hass)
                for sid in stale_ids:
                    ent = entities.pop(sid, None)
                    if ent:
                        ent.fire_change_event(EVENT_REMOVED)
                        if ent.entity_id:
                            entry_reg = registry.async_get(ent.entity_id)
                            if entry_reg:
                                registry.async_remove(ent.entity_id)
                                _LOGGER.debug("Removed stale geo entity %s", ent.entity_id)
            else:
                for sid in stale_ids:
                    ent = entities.pop(sid, None)
                    if ent:
                        ent.mark_stale()
                        ent.fire_change_event(EVENT_REMOVED)

    await _sync_entities()

    async def _periodic_update(now):
        await coordinator.async_request_refresh()
        await _sync_entities()

    async_track_time_interval(hass, _periodic_update, timedelta(seconds=update_seconds))

    async def _refresh_cb():
        await coordinator.async_request_refresh()
        await _sync_entities()

    hass.data.setdefault(DOMAIN, {}).setdefault("refresh_cbs", []).append(_refresh_cb)


class CFSIncidentEntity(GeolocationEvent):
    def __init__(self, hass: HomeAssistant, item: Dict[str, Any], unique_id: str | None = None) -> None:
        self.hass = hass
        self._source = PLATFORM_SOURCE
        self._available = True
        self._attrs: Dict[str, Any] = {}
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._name: str = "SA CFS Incident"
        self._first_seen = dt_now().isoformat()
        self._last_seen = self._first_seen
        self._last_changed = self._first_seen

        inc_no = (item.get("IncidentNo") or "").strip()
        self._incident_no = inc_no or (unique_id or "unknown")
        self._unique_id = f"{self._source}_{self._incident_no}".lower()

        self._last_hash: str | None = None
        self.update_from_item(item, first=True)

    def _calc_hash(self) -> str:
        parts = [
            str(self._attrs.get("Status") or ""),
            str(self._attrs.get("Level") or ""),
            str(self._attrs.get("Type") or ""),
            str(self._attrs.get("Message_link") or ""),
            str(self._latitude or ""),
            str(self._longitude or ""),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def update_from_item(self, item: Dict[str, Any], first: bool=False) -> bool:
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

        if self._latitude is not None and self._longitude is not None:
            self._attrs["map_url"] = f"/map?z=14&lat={self._latitude}&lng={self._longitude}"
            self._attrs["google_maps_url"] = f"https://maps.google.com/?q={self._latitude},{self._longitude}"
        else:
            self._attrs["map_url"] = None
            self._attrs["google_maps_url"] = None

        sev = _normalize_severity(self._attrs.get("Level"), self._attrs.get("Status"))
        self._attrs["severity"] = sev
        self._attrs["title"] = _build_title(self._attrs)
        self._attrs["summary"] = _build_summary(self._attrs)

        name_parts = []
        if item.get("Type"):
            name_parts.append(item["Type"])
        if item.get("Location_name"):
            name_parts.append(item["Location_name"])
        if item.get("Service") or item.get("Agency"):
            name_parts.append(item.get("Service") or item.get("Agency"))
        self._name = " - ".join([str(p) for p in name_parts if p]) or "SA CFS Incident"

        self._state = item.get("Status") or item.get("Level")

        self._last_seen = dt_now().isoformat()
        new_hash = self._calc_hash()
        changed = (self._last_hash is not None and new_hash != self._last_hash)
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
            "incident_no": self._attrs.get("IncidentNo"),
            "status": self._attrs.get("Status"),
            "level": self._attrs.get("Level"),
            "severity": self._attrs.get("severity"),
            "type": self._attrs.get("Type"),
            "region": self._attrs.get("Region"),
            "location_name": self._attrs.get("Location_name"),
            "latitude": self._latitude,
            "longitude": self._longitude,
            "message_link": self._attrs.get("Message_link"),
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
