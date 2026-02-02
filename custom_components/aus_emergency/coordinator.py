from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import now as dt_now, parse_datetime, as_local

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
    ATTR_INCIDENT_DATETIME,
    FEED_URLS,
    DEFAULT_RETRY_DELAY,
    MAX_RETRY_DELAY,
    BACKOFF_MULTIPLIER,
)

_LOGGER = logging.getLogger(__name__)


def _norm_severity(level: str | None, status: str | None) -> str:
    """Normalize severity from level/status text."""
    t = (str(level or "") + " " + str(status or "")).lower()
    if "emergency" in t:
        return "emergency_warning"
    if "watch" in t:
        return "watch_and_act"
    if "advice" in t:
        return "advice"
    if "safe" in t or "all clear" in t:
        return "all_clear"
    return "info"


def _parse_incident_datetime(date_str: str | None, time_str: str | None) -> datetime | None:
    """Parse date and time strings into a datetime object."""
    if not date_str:
        return None

    # Try various date/time formats
    datetime_str = date_str
    if time_str:
        datetime_str = f"{date_str} {time_str}"

    # Common formats used by emergency services
    formats = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%d %b %Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except (ValueError, TypeError):
            continue

    # Try ISO format parsing
    try:
        parsed = parse_datetime(datetime_str)
        if parsed:
            return as_local(parsed)
    except (ValueError, TypeError):
        pass

    return None


class IncidentDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch emergency incidents with retry/backoff support."""

    def __init__(self, hass: HomeAssistant, state: str, update_seconds: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{state} Emergency Data",
            update_interval=timedelta(seconds=update_seconds),
        )
        self._state = state
        self._session: aiohttp.ClientSession | None = None
        self._consecutive_failures = 0
        self._base_update_seconds = update_seconds
        self._feed_config = FEED_URLS.get(state, FEED_URLS["SA"])

    @property
    def source(self) -> str:
        """Return the data source identifier."""
        return self._feed_config.get("source", "unknown")

    async def _async_update_data(self) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        try:
            result = await self._fetch_data()
            # Reset backoff on success
            if self._consecutive_failures > 0:
                self._consecutive_failures = 0
                self.update_interval = timedelta(seconds=self._base_update_seconds)
                _LOGGER.info("Feed recovered, reset update interval to %s seconds", self._base_update_seconds)
            return result
        except (aiohttp.ClientError, UpdateFailed) as exc:
            self._consecutive_failures += 1
            self._apply_backoff()
            raise UpdateFailed(f"Error fetching {self._state} incidents: {exc}") from exc
        except Exception as exc:
            self._consecutive_failures += 1
            self._apply_backoff()
            _LOGGER.error("Unexpected error fetching %s incidents: %s", self._state, exc)
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

    def _apply_backoff(self) -> None:
        """Apply exponential backoff after failures."""
        delay = min(
            DEFAULT_RETRY_DELAY * (BACKOFF_MULTIPLIER ** (self._consecutive_failures - 1)),
            MAX_RETRY_DELAY
        )
        self.update_interval = timedelta(seconds=delay)
        _LOGGER.warning(
            "Feed failure #%d for %s, backing off to %d seconds",
            self._consecutive_failures, self._state, delay
        )

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch and parse incident data based on state."""
        url = self._feed_config.get("json")
        if not url:
            return {"incidents": []}

        async with self._session.get(url, timeout=30) as resp:
            if resp.status != 200:
                _LOGGER.warning("%s incidents returned HTTP %s", self._state, resp.status)
                return {"incidents": []}

            if self._state == "SA":
                return await self._parse_sa_data(resp)
            elif self._state == "NSW":
                return await self._parse_nsw_data(resp)
            elif self._state == "VIC":
                return await self._parse_vic_data(resp)
            elif self._state == "QLD":
                return await self._parse_qld_data(resp)
            else:
                return {"incidents": []}

    async def _parse_sa_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse SA CFS JSON format."""
        data = await resp.json()
        incidents = []

        if isinstance(data, dict) and "results" in data:
            raw_incidents = data["results"]
        elif isinstance(data, list):
            raw_incidents = data
        else:
            _LOGGER.warning("SA incidents JSON is in an unexpected format")
            return {"incidents": []}

        for item in raw_incidents:
            inc_no = (item.get("IncidentNo") or "").strip() or None
            lat = lon = None
            loc = item.get("Location")
            if isinstance(loc, str) and "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                if len(parts) == 2:
                    try:
                        lat = float(parts[0])
                        lon = float(parts[1])
                    except (ValueError, TypeError):
                        pass

            sev = _norm_severity(item.get("Level"), item.get("Status"))
            date_str = item.get("Date")
            time_str = item.get("Time")
            incident_dt = _parse_incident_datetime(date_str, time_str)

            incidents.append({
                ATTR_INCIDENT_NO: inc_no,
                ATTR_TYPE: item.get("Type"),
                ATTR_STATUS: item.get("Status"),
                ATTR_LEVEL: item.get("Level"),
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: item.get("Location_name"),
                ATTR_REGION: item.get("Region"),
                ATTR_DATE: date_str,
                ATTR_TIME: time_str,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: item.get("Message_link"),
                ATTR_AGENCY: item.get("Service") or item.get("Agency"),
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_nsw_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse NSW RFS GeoJSON format."""
        data = await resp.json()
        incidents = []

        features = data.get("features", [])
        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})

            # Extract coordinates (GeoJSON is [lon, lat])
            lat = lon = None
            coords = geom.get("coordinates")
            if coords and geom.get("type") == "Point":
                lon, lat = coords[0], coords[1] if len(coords) >= 2 else (None, None)
            elif coords and geom.get("type") == "GeometryCollection":
                # Try to get first point from geometry collection
                for g in geom.get("geometries", []):
                    if g.get("type") == "Point" and g.get("coordinates"):
                        lon, lat = g["coordinates"][0], g["coordinates"][1]
                        break

            # NSW uses "alertLevel" for severity
            alert_level = props.get("alertLevel", "").lower()
            if "emergency" in alert_level:
                sev = "emergency_warning"
            elif "watch" in alert_level:
                sev = "watch_and_act"
            elif "advice" in alert_level:
                sev = "advice"
            else:
                sev = "info"

            # Parse pubDate
            pub_date = props.get("pubDate")
            incident_dt = _parse_incident_datetime(pub_date, None)

            incidents.append({
                ATTR_INCIDENT_NO: props.get("guid"),
                ATTR_TYPE: props.get("category"),
                ATTR_STATUS: props.get("status"),
                ATTR_LEVEL: props.get("alertLevel"),
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: props.get("location") or props.get("title"),
                ATTR_REGION: props.get("council") or props.get("councilArea"),
                ATTR_DATE: pub_date,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: props.get("link"),
                ATTR_AGENCY: "NSW RFS",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_vic_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse VIC EMV JSON format."""
        data = await resp.json()
        incidents = []

        # VIC format has results array
        results = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(results, list):
            results = []

        for item in results:
            lat = item.get("lat")
            lon = item.get("lon")

            # Try to parse coordinates if they're strings
            if isinstance(lat, str):
                try:
                    lat = float(lat)
                except (ValueError, TypeError):
                    lat = None
            if isinstance(lon, str):
                try:
                    lon = float(lon)
                except (ValueError, TypeError):
                    lon = None

            sev = _norm_severity(item.get("feedType"), item.get("status"))

            # Parse created/updated time
            created = item.get("created") or item.get("updated")
            incident_dt = _parse_incident_datetime(created, None)

            incidents.append({
                ATTR_INCIDENT_NO: item.get("id") or item.get("sourceId"),
                ATTR_TYPE: item.get("feedType") or item.get("category1"),
                ATTR_STATUS: item.get("status"),
                ATTR_LEVEL: item.get("feedType"),
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: item.get("location") or item.get("name"),
                ATTR_REGION: item.get("lga") or item.get("originId"),
                ATTR_DATE: created,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: item.get("url"),
                ATTR_AGENCY: item.get("sourceOrg") or "VIC EMV",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_qld_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse QLD QFES JSON format."""
        data = await resp.json()
        incidents = []

        # QLD format varies - handle both array and object with features
        if isinstance(data, dict):
            features = data.get("features", data.get("alerts", []))
        elif isinstance(data, list):
            features = data
        else:
            features = []

        for feature in features:
            props = feature.get("properties", feature)
            geom = feature.get("geometry", {})

            lat = lon = None
            coords = geom.get("coordinates")
            if coords:
                if geom.get("type") == "Point":
                    lon, lat = coords[0], coords[1] if len(coords) >= 2 else (None, None)
                elif isinstance(coords, list) and len(coords) >= 2:
                    # Might be raw coordinates
                    try:
                        lon, lat = float(coords[0]), float(coords[1])
                    except (ValueError, TypeError, IndexError):
                        pass

            # Also check for lat/lon directly in props
            if lat is None:
                lat = props.get("latitude") or props.get("lat")
            if lon is None:
                lon = props.get("longitude") or props.get("lon")

            sev = _norm_severity(props.get("level"), props.get("status"))

            updated = props.get("updated") or props.get("created") or props.get("date")
            incident_dt = _parse_incident_datetime(updated, None)

            incidents.append({
                ATTR_INCIDENT_NO: props.get("id") or props.get("event_id"),
                ATTR_TYPE: props.get("type") or props.get("event_type"),
                ATTR_STATUS: props.get("status"),
                ATTR_LEVEL: props.get("level"),
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: props.get("location") or props.get("name"),
                ATTR_REGION: props.get("lga") or props.get("region"),
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: props.get("url") or props.get("link"),
                ATTR_AGENCY: "QLD QFES",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def async_close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


# Backwards compatibility alias
CFSDataCoordinator = IncidentDataCoordinator
