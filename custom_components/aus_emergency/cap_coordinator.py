from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
import aiohttp
from defusedxml import ElementTree as ET

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_SEVERITY,
    FEED_URLS,
    DEFAULT_RETRY_DELAY,
    MAX_RETRY_DELAY,
    BACKOFF_MULTIPLIER,
)

_LOGGER = logging.getLogger(__name__)

CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


class CFSCAPDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch CAP alerts (XML) with retry/backoff support."""

    def __init__(self, hass: HomeAssistant, state: str, update_seconds: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{state} CAP Data",
            update_interval=timedelta(seconds=update_seconds),
        )
        self._state = state
        self._session: aiohttp.ClientSession | None = None
        self._consecutive_failures = 0
        self._base_update_seconds = update_seconds
        self._feed_config = FEED_URLS.get(state, FEED_URLS["SA"])

    @property
    def cap_url(self) -> str | None:
        """Return the CAP feed URL for this state."""
        return self._feed_config.get("cap")

    async def _async_update_data(self) -> dict[str, Any]:
        # Skip if no CAP feed for this state
        if not self.cap_url:
            return {"alerts": []}

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        try:
            result = await self._fetch_data()
            # Reset backoff on success
            if self._consecutive_failures > 0:
                self._consecutive_failures = 0
                self.update_interval = timedelta(seconds=self._base_update_seconds)
                _LOGGER.info("CAP feed recovered, reset update interval to %s seconds", self._base_update_seconds)
            return result
        except (aiohttp.ClientError, UpdateFailed) as exc:
            self._consecutive_failures += 1
            self._apply_backoff()
            raise UpdateFailed(f"Error fetching {self._state} CAP feed: {exc}") from exc
        except ET.ParseError as exc:
            self._consecutive_failures += 1
            self._apply_backoff()
            raise UpdateFailed(f"Error parsing CAP XML: {exc}") from exc
        except Exception as exc:
            self._consecutive_failures += 1
            self._apply_backoff()
            _LOGGER.error("Unexpected error fetching %s CAP feed: %s", self._state, exc)
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

    def _apply_backoff(self) -> None:
        """Apply exponential backoff after failures."""
        delay = min(
            DEFAULT_RETRY_DELAY * (BACKOFF_MULTIPLIER ** (self._consecutive_failures - 1)),
            MAX_RETRY_DELAY
        )
        self.update_interval = timedelta(seconds=delay)
        _LOGGER.warning(
            "CAP feed failure #%d for %s, backing off to %d seconds",
            self._consecutive_failures, self._state, delay
        )

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch and parse CAP XML data."""
        alerts = []

        async with self._session.get(self.cap_url, timeout=30) as resp:
            if resp.status != 200:
                _LOGGER.warning("%s CAP feed returned HTTP %s", self._state, resp.status)
                return {"alerts": []}

            xml_string = await resp.text()
            root = ET.fromstring(xml_string)

            for alert in root.findall("cap:alert", CAP_NS):
                alert_id = alert.findtext("cap:identifier", None, CAP_NS)
                if not alert_id:
                    continue

                # Process the first info block we find
                info = alert.find("cap:info", CAP_NS)
                if info is None:
                    continue

                # An alert can have multiple areas
                areas = []
                for area in info.findall("cap:area", CAP_NS):
                    area_data = {"areaDesc": area.findtext("cap:areaDesc", None, CAP_NS)}

                    # Parse polygon
                    polygons_text = area.findall("cap:polygon", CAP_NS)
                    polygons = []
                    for poly_text_elem in polygons_text:
                        if poly_text_elem.text:
                            polygons.append(poly_text_elem.text.strip())
                    if polygons:
                        area_data["polygon"] = polygons

                    # Parse circle
                    circles_text = area.findall("cap:circle", CAP_NS)
                    circles = []
                    for circle_text_elem in circles_text:
                        if circle_text_elem.text:
                            circles.append(circle_text_elem.text.strip())
                    if circles:
                        area_data["circle"] = circles

                    areas.append(area_data)

                alerts.append({
                    "id": alert_id,
                    "areas": areas,
                    "headline": info.findtext("cap:headline", None, CAP_NS),
                    "description": info.findtext("cap:description", None, CAP_NS),
                    "instruction": info.findtext("cap:instruction", None, CAP_NS),
                    ATTR_SEVERITY: info.findtext("cap:severity", "Unknown", CAP_NS),
                    "urgency": info.findtext("cap:urgency", "Unknown", CAP_NS),
                    "certainty": info.findtext("cap:certainty", "Unknown", CAP_NS),
                    "event": info.findtext("cap:event", "Unknown", CAP_NS),
                    "effective": info.findtext("cap:effective", None, CAP_NS),
                    "expires": info.findtext("cap:expires", None, CAP_NS),
                })

        return {"alerts": alerts}

    async def async_close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
