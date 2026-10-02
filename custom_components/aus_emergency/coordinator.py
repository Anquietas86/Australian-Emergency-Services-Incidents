from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
import aiohttp
from defusedxml import ElementTree as ET

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import parse_datetime, as_local

from .const import (
    ATTR_INCIDENT_NO,
    ATTR_TYPE,
    ATTR_STATUS,
    ATTR_LEVEL,
    ATTR_LOCATION_NAME,
    ATTR_REGION,
    ATTR_DATE,
    ATTR_TIME,
    ATTR_MESSAGE,
    ATTR_MESSAGE_LINK,
    ATTR_RESOURCES,
    ATTR_AIRCRAFT,
    ATTR_AGENCY,
    ATTR_SEVERITY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_INCIDENT_DATETIME,
    FEED_URLS,
    SA_MAP_INCIDENTS_URL,
    SA_MAP_MAX_RECORD_AGE_DAYS,
    SOURCE_SA_CFS_GIS,
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
        """Return the actual source of the most recent successful update."""
        return (self.data or {}).get("fallback_source") or self._feed_config.get("source", "unknown")

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
        """Fetch a state feed; use the official CFS map when SA CRIIMSON fails."""
        try:
            return await self._fetch_primary_data()
        except (UpdateFailed, aiohttp.ClientError, ValueError) as primary_error:
            if self._state != "SA":
                raise
            _LOGGER.warning("SA CRIIMSON unavailable (%s); trying official CFS map", primary_error)
            try:
                return await self._fetch_sa_map_data()
            except (UpdateFailed, aiohttp.ClientError, ValueError) as map_error:
                raise UpdateFailed(
                    f"SA primary feed failed ({primary_error}); map fallback failed ({map_error})"
                ) from map_error

    async def _fetch_primary_data(self) -> dict[str, Any]:
        """Fetch and parse the configured state feed."""
        if self._state == "TAS":
            return await self._fetch_tas_georss()

        url = self._feed_config.get("json")
        if not url:
            raise UpdateFailed(f"No incident feed configured for {self._state}")

        async with self._session.get(url, timeout=30) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"{self._state} incidents returned HTTP {resp.status}")
            # Queensland publishes JSON with an octet-stream content type.
            content_type = resp.content_type.lower()
            if "json" not in content_type and not (
                self._state == "QLD" and content_type in ("binary/octet-stream", "application/octet-stream")
            ):
                raise UpdateFailed(
                    f"{self._state} incidents returned {resp.content_type}, expected JSON"
                )

            if self._state == "SA":
                return await self._parse_sa_data(resp)
            elif self._state == "NSW":
                return await self._parse_nsw_data(resp)
            elif self._state == "VIC":
                return await self._parse_vic_data(resp)
            elif self._state == "QLD":
                return await self._parse_qld_data(resp)
            elif self._state == "WA":
                return await self._parse_wa_data(resp)
            else:
                raise UpdateFailed(f"No parser configured for {self._state}")

    async def _fetch_sa_map_data(self) -> dict[str, Any]:
        """Read the public incident layer used by the official CFS map.

        This layer covers CFS and MFS incidents, but is not equivalent to the
        CRIIMSON/CAP feeds. Its source is labelled separately for consumers.
        """
        async with self._session.get(SA_MAP_INCIDENTS_URL, timeout=30) as resp:
            if resp.status != 200 or "json" not in resp.content_type.lower():
                raise UpdateFailed(
                    f"SA map returned HTTP {resp.status}, {resp.content_type}"
                )
            data = await resp.json(content_type=None)

        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("features"), list):
            raise UpdateFailed("SA map response is not an incident feature collection")
        if data.get("exceededTransferLimit"):
            raise UpdateFailed("SA map incident result was truncated")

        incidents = []
        excluded_stale_count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=SA_MAP_MAX_RECORD_AGE_DAYS)
        for feature in data["features"]:
            attrs = feature.get("attributes") or {}
            geometry = feature.get("geometry") or {}
            identifier = attrs.get("ident") or attrs.get("atom_id")
            if not identifier:
                raise UpdateFailed("SA map incident has no stable identifier")
            updated = attrs.get("updated")
            if not isinstance(updated, (int, float)):
                excluded_stale_count += 1
                continue
            updated_at = datetime.fromtimestamp(updated / 1000, tz=timezone.utc)
            if updated_at < cutoff:
                excluded_stale_count += 1
                continue
            updated_iso = updated_at.isoformat()
            incidents.append({
                ATTR_INCIDENT_NO: identifier,
                ATTR_TYPE: attrs.get("event") or attrs.get("sub_cat"),
                ATTR_STATUS: attrs.get("inc_status"),
                ATTR_LEVEL: attrs.get("warn_level"),
                ATTR_SEVERITY: _norm_severity(attrs.get("warn_level"), attrs.get("inc_status")),
                ATTR_LOCATION_NAME: attrs.get("location") or attrs.get("inc_name"),
                ATTR_REGION: attrs.get("fbd"),
                ATTR_DATE: updated_iso,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: updated_iso,
                ATTR_MESSAGE: attrs.get("headline"),
                ATTR_MESSAGE_LINK: attrs.get("web"),
                ATTR_AGENCY: attrs.get("authority"),
                ATTR_LATITUDE: attrs.get("lat") if attrs.get("lat") is not None else geometry.get("y"),
                ATTR_LONGITUDE: attrs.get("long") if attrs.get("long") is not None else geometry.get("x"),
            })
        if excluded_stale_count:
            _LOGGER.warning("SA map omitted %d stale or undated incident(s)", excluded_stale_count)
        if data["features"] and not incidents:
            raise UpdateFailed("SA map contains only stale or undated incidents; current count is unknown")
        return {
            "incidents": incidents,
            "fallback_source": SOURCE_SA_CFS_GIS,
            "excluded_stale_count": excluded_stale_count,
        }

    async def _parse_sa_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse SA CFS JSON format."""
        data = await resp.json(content_type=None)
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
                ATTR_MESSAGE: item.get("Message"),
                ATTR_MESSAGE_LINK: item.get("Message_link"),
                ATTR_RESOURCES: item.get("Resources"),
                ATTR_AIRCRAFT: item.get("Aircraft"),
                ATTR_AGENCY: item.get("Service") or item.get("Agency"),
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_nsw_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse NSW RFS GeoJSON format."""
        data = await resp.json(content_type=None)
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

            # The current RFS feed puts the alert level in category and
            # embeds incident type and status in its HTML description.
            description = props.get("description") or ""
            def description_field(field: str, text: str = description) -> str | None:
                match = re.search(rf"(?:^|<br\s*/?>)\s*{field}:\s*([^<]+)", text, re.I)
                return match.group(1).strip() if match else None

            alert_level = props.get("alertLevel") or props.get("category") or description_field("ALERT LEVEL")
            sev = _norm_severity(alert_level, None)
            status = props.get("status") or description_field("STATUS")

            pub_date = props.get("pubDate")
            incident_dt = _parse_incident_datetime(pub_date, None)

            incidents.append({
                ATTR_INCIDENT_NO: props.get("guid"),
                ATTR_TYPE: description_field("TYPE") or props.get("category"),
                ATTR_STATUS: status,
                ATTR_LEVEL: alert_level,
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
        data = await resp.json(content_type=None)
        incidents = []

        # VIC format has results array
        results = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(results, list):
            results = []

        for item in results:
            lat = item.get("latitude", item.get("lat"))
            lon = item.get("longitude", item.get("lon"))

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

            status = item.get("incidentStatus") or item.get("status")
            level = item.get("category2") or item.get("feedType")
            sev = _norm_severity(level, status)

            updated = item.get("lastUpdateDateTime") or item.get("created") or item.get("updated")
            incident_dt = _parse_incident_datetime(updated, None)

            incidents.append({
                ATTR_INCIDENT_NO: item.get("incidentNo") or item.get("id") or item.get("sourceId"),
                ATTR_TYPE: item.get("incidentType") or item.get("category1") or item.get("feedType"),
                ATTR_STATUS: status,
                ATTR_LEVEL: level,
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: item.get("incidentLocation") or item.get("location") or item.get("name"),
                ATTR_REGION: item.get("municipality") or item.get("fireDistrict") or item.get("lga"),
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: item.get("url"),
                ATTR_AGENCY: item.get("agency") or item.get("sourceOrg") or "VIC EMV",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_qld_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse QLD QFES JSON format (QFDWarnings GeoJSON)."""
        # content_type=None skips validation - QLD S3 returns binary/octet-stream
        data = await resp.json(content_type=None)
        incidents = []

        # QLD format is GeoJSON FeatureCollection
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
                elif geom.get("type") == "Polygon" and coords:
                    # For polygons, try to get centroid from first ring
                    try:
                        ring = coords[0]
                        if ring:
                            lon = sum(p[0] for p in ring) / len(ring)
                            lat = sum(p[1] for p in ring) / len(ring)
                    except (ValueError, TypeError, IndexError):
                        pass
                elif isinstance(coords, list) and len(coords) >= 2:
                    # Might be raw coordinates
                    try:
                        lon, lat = float(coords[0]), float(coords[1])
                    except (ValueError, TypeError, IndexError):
                        pass

            # QLD feed has Latitude/Longitude directly in properties
            if lat is None:
                lat = props.get("Latitude") or props.get("latitude") or props.get("lat")
            if lon is None:
                lon = props.get("Longitude") or props.get("longitude") or props.get("lon")

            # QLD uses WarningLevel and CurrentStatus
            warning_level = props.get("WarningLevel") or props.get("level")
            current_status = props.get("CurrentStatus") or props.get("status")
            sev = _norm_severity(warning_level, current_status)

            # QLD uses ISO datetime fields
            updated = (
                props.get("ItemDateTimeLocal_ISO")
                or props.get("PublishDateLocal_ISO")
                or props.get("updated")
                or props.get("created")
                or props.get("date")
            )
            incident_dt = _parse_incident_datetime(updated, None)

            # Build location name from available fields
            location_name = (
                props.get("WarningTitle")
                or props.get("WarningArea")
                or props.get("Location")
                or props.get("location")
                or props.get("name")
            )

            incidents.append({
                ATTR_INCIDENT_NO: props.get("UniqueID") or props.get("OBJECTID") or props.get("id") or props.get("event_id"),
                ATTR_TYPE: props.get("EventType") or props.get("GroupedType") or props.get("type") or props.get("event_type"),
                ATTR_STATUS: current_status,
                ATTR_LEVEL: warning_level,
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: location_name,
                ATTR_REGION: props.get("Jurisdiction") or props.get("Locality") or props.get("lga") or props.get("region"),
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: props.get("url") or props.get("link"),
                ATTR_AGENCY: "QLD QFD",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def _parse_wa_data(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse WA DFES EmergencyWA API format."""
        data = await resp.json(content_type=None)
        incidents = []

        # WA API returns {"incidents": [...]}
        raw_incidents = data.get("incidents", [])

        for item in raw_incidents:
            # Extract coordinates from location object
            location = item.get("location", {})
            lat = location.get("latitude")
            lon = location.get("longitude")

            # Fallback to geo-source if location missing
            if lat is None or lon is None:
                geo_source = item.get("geo-source", {})
                features = geo_source.get("features", [])
                if features:
                    geom = features[0].get("geometry", {})
                    if geom.get("type") == "Point":
                        coords = geom.get("coordinates", [])
                        if len(coords) >= 2:
                            lon, lat = coords[0], coords[1]

            # Determine severity from incident status
            status = item.get("incident-status", "")
            inc_type = item.get("incident-type", "")
            sev = _norm_severity(inc_type, status)

            # Parse datetime
            updated = item.get("updated-date-time") or item.get("start-date-time")
            incident_dt = _parse_incident_datetime(updated, None)

            # Build location name from address and suburbs
            location_name = location.get("value", "")
            suburbs = item.get("suburbs", [])
            if suburbs and location_name:
                location_name = f"{location_name}, {suburbs[0]}"
            elif suburbs:
                location_name = suburbs[0]

            # Get region from LGA or DFES regions
            lga = item.get("lga", [])
            dfes_regions = item.get("dfes-regions", [])
            region = lga[0] if lga else (dfes_regions[0] if dfes_regions else None)

            incidents.append({
                ATTR_INCIDENT_NO: item.get("id") or item.get("cad-id"),
                ATTR_TYPE: inc_type or item.get("name"),
                ATTR_STATUS: status,
                ATTR_LEVEL: status,
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: location_name,
                ATTR_REGION: region,
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: f"https://emergency.wa.gov.au/incidents/{item.get('id')}" if item.get("id") else None,
                ATTR_AGENCY: "WA DFES",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        # Warnings include the highest-severity alerts; a partial response must
        # not present their absence as a trustworthy zero count.
        warnings_url = self._feed_config.get("warnings")
        if warnings_url:
            async with self._session.get(warnings_url, timeout=30) as warn_resp:
                if warn_resp.status != 200:
                    raise UpdateFailed(f"WA warnings returned HTTP {warn_resp.status}")
                if "json" not in warn_resp.content_type.lower():
                    raise UpdateFailed(
                        f"WA warnings returned {warn_resp.content_type}, expected JSON"
                    )
                warn_data = await warn_resp.json(content_type=None)
                incidents.extend(self._parse_wa_warnings(warn_data))

        return {"incidents": incidents}

    def _parse_wa_warnings(self, data: dict) -> list[dict]:
        """Parse WA DFES warnings into incident format."""
        incidents = []

        for item in data.get("warnings", []):
            # Extract coordinates from location object
            location = item.get("location", {})
            lat = location.get("latitude")
            lon = location.get("longitude")

            # Fallback to geo-source centroid
            if lat is None or lon is None:
                geo_source = item.get("geo-source", {})
                features = geo_source.get("features", [])
                for feat in features:
                    geom = feat.get("geometry", {})
                    if geom.get("type") == "Point":
                        coords = geom.get("coordinates", [])
                        if len(coords) >= 2:
                            lon, lat = coords[0], coords[1]
                            break

            # Determine severity from CAP severity or entity subtype
            cap_severity = item.get("cap-severity", "")
            entity_subtype = item.get("entitySubType", "")

            if "emergency" in entity_subtype.lower() or "extreme" in cap_severity.lower():
                sev = "emergency_warning"
            elif "watch" in entity_subtype.lower() or "severe" in cap_severity.lower():
                sev = "watch_and_act"
            elif "advice" in entity_subtype.lower() or "moderate" in cap_severity.lower():
                sev = "advice"
            else:
                sev = "info"

            # Parse datetime
            updated = item.get("published-date-time")
            incident_dt = _parse_incident_datetime(updated, None)

            # Build location name
            location_name = location.get("value", "")
            suburbs = item.get("suburbs", [])
            if suburbs and not location_name:
                location_name = ", ".join(suburbs[:3])
                if len(suburbs) > 3:
                    location_name += f" (+{len(suburbs) - 3} more)"

            # Get region
            lga = item.get("lga", [])
            region = lga[0] if lga else None

            # Extract warning type from name or entity subtype
            warning_name = item.get("name", "Warning")

            incidents.append({
                ATTR_INCIDENT_NO: item.get("id"),
                ATTR_TYPE: warning_name,
                ATTR_STATUS: cap_severity,
                ATTR_LEVEL: cap_severity,
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: location_name,
                ATTR_REGION: region,
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: f"https://emergency.wa.gov.au/warnings/{item.get('id')}" if item.get("id") else None,
                ATTR_AGENCY: "WA DFES",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return incidents

    async def _fetch_tas_georss(self) -> dict[str, Any]:
        """Fetch and parse TAS TFS GeoRSS feed."""
        url = self._feed_config.get("georss")
        if not url:
            raise UpdateFailed("TAS incident feed retired; no current source is configured")

        async with self._session.get(url, timeout=30) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"TAS incidents returned HTTP {resp.status}")
            if "xml" not in resp.content_type.lower():
                raise UpdateFailed(f"TAS incidents returned {resp.content_type}, expected XML")

            xml_string = await resp.text()
            return self._parse_tas_georss(xml_string)

    def _parse_tas_georss(self, xml_string: str) -> dict[str, Any]:
        """Parse TAS TFS GeoRSS XML format."""
        incidents = []

        # GeoRSS namespace
        namespaces = {
            "georss": "http://www.georss.org/georss",
        }

        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            _LOGGER.error("Error parsing TAS GeoRSS XML: %s", exc)
            return {"incidents": []}

        # Find channel/item elements (RSS 2.0 format)
        channel = root.find("channel")
        if channel is None:
            # Try finding items directly
            items = root.findall(".//item")
        else:
            items = channel.findall("item")

        for item in items:
            title = item.findtext("title", "").strip()
            description = item.findtext("description", "").strip()
            link = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            guid = item.findtext("guid", "").strip()

            # Extract coordinates from georss:point (format: "lat lon")
            lat = lon = None
            point = item.find("georss:point", namespaces)
            if point is not None and point.text:
                coords = point.text.strip().split()
                if len(coords) >= 2:
                    try:
                        lat = float(coords[0])
                        lon = float(coords[1])
                    except (ValueError, TypeError):
                        pass

            # Parse incident details from title/description
            # TAS titles often follow format: "Type - Location (Status)"
            inc_type = None
            location_name = title
            status = None

            # Try to extract type and status from title
            if " - " in title:
                parts = title.split(" - ", 1)
                inc_type = parts[0].strip()
                location_name = parts[1].strip() if len(parts) > 1 else title

            # Extract status from parentheses at end
            status_match = re.search(r'\(([^)]+)\)\s*$', location_name)
            if status_match:
                status = status_match.group(1)
                location_name = location_name[:status_match.start()].strip()

            # Determine severity from status/type
            sev = _norm_severity(inc_type, status)

            # Parse pubDate
            incident_dt = _parse_incident_datetime(pub_date, None)

            # Use guid or generate from title
            incident_no = guid or title

            incidents.append({
                ATTR_INCIDENT_NO: incident_no,
                ATTR_TYPE: inc_type or "Incident",
                ATTR_STATUS: status,
                ATTR_LEVEL: status,
                ATTR_SEVERITY: sev,
                ATTR_LOCATION_NAME: location_name,
                ATTR_REGION: None,  # Not provided in GeoRSS
                ATTR_DATE: pub_date,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: link,
                ATTR_AGENCY: "TAS TFS",
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
