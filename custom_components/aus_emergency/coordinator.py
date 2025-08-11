
from __future__ import annotations

import logging
from datetime import timedelta
import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# SA CFS Endpoints
CFS_INCIDENTS_JSON = "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json"

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
        try:
            async with self._session.get(CFS_INCIDENTS_JSON, timeout=30) as resp:
                if resp.status == 200:
                    incidents = await resp.json()
                else:
                    _LOGGER.warning("CFS incidents returned HTTP %s", resp.status)
        except Exception as exc:
            _LOGGER.error("Error fetching CFS incidents: %s", exc)

        return {"incidents": incidents}

    async def async_close(self):
        if self._session and not self._session.closed:
            await self._session.close()
