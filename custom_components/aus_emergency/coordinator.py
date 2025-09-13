from __future__ import annotations

import logging
from datetime import timedelta
import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ATTR_INCIDENT_NO,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_LOCATION_NAME,
    ATTR_REGION,
    ATTR_DATE,
    ATTR_TIME,
    ATTR_MESSAGE_LINK,
    ATTR_AGENCY,
    ATTR_SEVERITY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
)

_LOGGER = logging.getLogger(__name__)

CFS_INCIDENTS_JSON = "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json"

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

class CFSDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch SA CFS incidents (JSON)."""

    def __init__(self, hass: HomeAssistant, update_seconds: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="SA CFS Data",
            update_interval=timedelta(seconds=update_seconds),
        )
        self._session: aiohttp.ClientSession | None = None

    async def _async_update_data(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        incidents = []
        parsed = []
        try:
            async with self._session.get(CFS_INCIDENTS_JSON, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and "results" in data:
                        incidents = data["results"]
                    elif isinstance(data, list):
                        incidents = data
                    else:
                        _LOGGER.warning("CFS incidents JSON is in an unexpected format or empty")
                        incidents = []
                else:
                    _LOGGER.warning("CFS incidents returned HTTP %s", resp.status)
        except Exception as exc:
            _LOGGER.error("Error fetching CFS incidents: %s", exc)

        for item in incidents:
            inc_no = (item.get("IncidentNo") or "").strip() or None
            lat = lon = None
            loc = item.get("Location")
            if isinstance(loc, str) and "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                if len(parts) == 2:
                    try:
                        lat = float(parts[0])
                        lon = float(parts[1])
                    except Exception:
                        pass
            sev = _norm(item.get("Level"), item.get("Status"))
            parsed.append(
                {
                    ATTR_INCIDENT_NO: inc_no,
                    ATTR_TYPE: item.get("Type"),
                    ATTR_STATUS: item.get("Status"),
                    ATTR_LEVEL: item.get("Level"),
                    ATTR_SEVERITY: sev,
                    ATTR_LOCATION_NAME: item.get("Location_name"),
                    ATTR_REGION: item.get("Region"),
                    ATTR_DATE: item.get("Date"),
                    ATTR_TIME: item.get("Time"),
                    ATTR_MESSAGE_LINK: item.get("Message_link"),
                    ATTR_AGENCY: item.get("Service") or item.get("Agency"),
                    ATTR_LATITUDE: lat,
                    ATTR_LONGITUDE: lon,
                }
            )

        return {"incidents": parsed}

    async def async_close(self):
        if self._session and not self._session.closed:
            await self._session.close()
