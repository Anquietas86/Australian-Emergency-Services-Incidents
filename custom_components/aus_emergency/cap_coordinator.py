from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
import aiohttp
from defusedxml import ElementTree as ET

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .coordinator import is_cap_warning
from .const import (
    ATTR_SEVERITY,
    FEED_URLS,
    DEFAULT_RETRY_DELAY,
    MAX_RETRY_DELAY,
    BACKOFF_MULTIPLIER,
)

_LOGGER = logging.getLogger(__name__)

CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
ATOM_NS = "{http://www.w3.org/2005/Atom}"
CAP_ALERT_TAG = "{urn:oasis:names:tc:emergency:cap:1.2}alert"


def cap_parameters(info: Any) -> dict[str, str]:
    """Return a CAP <info> block's <parameter> name/value pairs."""
    params: dict[str, str] = {}
    for param in info.findall("cap:parameter", CAP_NS):
        name = param.findtext("cap:valueName", "", CAP_NS)
        if name:
            params[name] = param.findtext("cap:value", "", CAP_NS) or ""
    return params


def _alert_elements(root: Any) -> list[Any]:
    """Return cap:alert elements from a bare alert, a list of alerts, or an Atom feed.

    AlertSA publishes CAP wrapped in Atom (feed/entry/content/alert). A document
    that is none of these is a feed change, not a quiet day, so it raises.
    """
    if root.tag == CAP_ALERT_TAG:
        return [root]
    if root.tag == f"{ATOM_NS}feed":
        alerts = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            content = entry.find(f"{ATOM_NS}content")
            alert = content.find("cap:alert", CAP_NS) if content is not None else None
            if alert is not None:
                alerts.append(alert)
        return alerts
    alerts = root.findall("cap:alert", CAP_NS)
    if not alerts and len(root):
        raise UpdateFailed(f"CAP feed has an unrecognised layout (root <{root.tag}>)")
    return alerts


def parse_cap_alerts(xml_string: str) -> list[dict[str, Any]]:
    """Parse CAP public warnings; routine incident notices are skipped."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as exc:
        raise UpdateFailed(f"CAP feed returned invalid XML: {exc}") from exc

    alerts = []
    for alert in _alert_elements(root):
        alert_id = alert.findtext("cap:identifier", None, CAP_NS)
        if not alert_id:
            continue

        # Process the first info block we find
        info = alert.find("cap:info", CAP_NS)
        if info is None:
            continue

        params = cap_parameters(info)
        # Feeds without a WarningLevel parameter only carry warnings.
        if "WarningLevel" in params and not is_cap_warning(params):
            continue

        # An alert can have multiple areas
        areas = []
        for area in info.findall("cap:area", CAP_NS):
            area_data = {"areaDesc": area.findtext("cap:areaDesc", None, CAP_NS)}
            polygons = [p.text.strip() for p in area.findall("cap:polygon", CAP_NS) if p.text]
            if polygons:
                area_data["polygon"] = polygons
            circles = [c.text.strip() for c in area.findall("cap:circle", CAP_NS) if c.text]
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
            "warning_level": params.get("WarningLevel"),
        })
    return alerts


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
    def consecutive_failures(self) -> int:
        """Return the number of consecutive failed updates."""
        return self._consecutive_failures

    @property
    def cap_url(self) -> str | None:
        """Return the CAP feed URL for this state."""
        return self._feed_config.get("cap")

    async def _async_update_data(self) -> dict[str, Any]:
        # Skip if no CAP feed for this state
        if not self.cap_url:
            return {"alerts": []}

        if self._session is None:
            self._session = async_get_clientsession(self.hass)

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
        async with self._session.get(self.cap_url, timeout=30) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"{self._state} CAP feed returned HTTP {resp.status}")
            if "xml" not in resp.content_type.lower():
                raise UpdateFailed(
                    f"{self._state} CAP feed returned {resp.content_type}, expected XML"
                )
            xml_string = await resp.text()

        return {"alerts": parse_cap_alerts(xml_string)}

    async def async_close(self) -> None:
        """Release resources; the shared HA client session is never closed here."""
        self._session = None
