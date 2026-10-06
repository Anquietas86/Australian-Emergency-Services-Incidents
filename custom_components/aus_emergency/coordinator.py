from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
import aiohttp
from defusedxml import ElementTree as ET

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import async_get_time_zone, parse_datetime, as_local

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
    ATTR_DISTANCE_KM,
    ATTR_BEARING,
    ATTR_DIRECTION,
    ATTR_HOME_IN_AREA,
    ATTR_POLYGONS,
    DOMAIN,
    FEED_ISSUE_FAILURE_THRESHOLD,
    FEED_URLS,
    UNAVAILABLE_STATES,
    SA_MAP_INCIDENTS_URL,
    SA_MAP_MAX_RECORD_AGE_DAYS,
    SOURCE_SA_CFS_GIS,
    STATE_TIME_ZONES,
    DEFAULT_RETRY_DELAY,
    MAX_RETRY_DELAY,
    BACKOFF_MULTIPLIER,
)
from .utils import compass_direction, distance_to_incident, initial_bearing

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


def _parse_incident_datetime(
    date_str: str | None,
    time_str: str | None,
    tz: tzinfo | None = None,
) -> datetime | None:
    """Parse provider date/time strings into an aware local datetime.

    Naive values are interpreted in ``tz`` (the provider's own timezone).
    Without ``tz`` they are returned naive.
    """
    if not date_str:
        return None

    datetime_str = str(date_str).strip()
    if time_str:
        datetime_str = f"{datetime_str} {time_str}"

    # Common formats used by emergency services
    formats = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%d %b %Y %H:%M",
        "%d %b %Y %H:%M:%S",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]

    parsed: datetime | None = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(datetime_str, fmt)
        except (ValueError, TypeError):
            continue
        if fmt.endswith("Z"):
            parsed = parsed.replace(tzinfo=timezone.utc)
        break

    if parsed is None:
        # ISO 8601 with fractional seconds or offsets
        try:
            parsed = parse_datetime(datetime_str)
        except (ValueError, TypeError):
            parsed = None
        if parsed is None:
            return None

    if parsed.tzinfo is None:
        if tz is None:
            return parsed
        parsed = parsed.replace(tzinfo=tz)
    return as_local(parsed)


def _to_float(value: Any) -> float | None:
    """Coerce a feed coordinate to a finite float."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# Bound the work done per incident; warning areas are rarely this detailed.
MAX_POLYGON_RINGS = 20
MAX_RING_POINTS = 2000


def _ring_to_lat_lon(ring: Any) -> list[tuple[float, float]]:
    """Convert a GeoJSON [lon, lat(, z)] ring to (lat, lon) tuples."""
    points: list[tuple[float, float]] = []
    if not isinstance(ring, list):
        return points
    step = max(1, len(ring) // MAX_RING_POINTS)
    for point in ring[::step]:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        lon, lat = _to_float(point[0]), _to_float(point[1])
        if lat is not None and lon is not None:
            points.append((lat, lon))
    return points


def _extract_polygons(geometry: Any) -> list[list[tuple[float, float]]]:
    """Return outer rings of every (Multi)Polygon in a GeoJSON geometry."""
    if not isinstance(geometry, dict):
        return []
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    rings: list[Any] = []
    if geom_type == "Polygon" and isinstance(coords, list) and coords:
        rings = [coords[0]]
    elif geom_type == "MultiPolygon" and isinstance(coords, list):
        rings = [poly[0] for poly in coords if isinstance(poly, list) and poly]
    elif geom_type == "GeometryCollection":
        polygons: list[list[tuple[float, float]]] = []
        for member in geometry.get("geometries") or []:
            polygons.extend(_extract_polygons(member))
        return polygons[:MAX_POLYGON_RINGS]
    polygons = [r for r in (_ring_to_lat_lon(ring) for ring in rings) if len(r) >= 3]
    return polygons[:MAX_POLYGON_RINGS]


def _first_point(geometry: Any) -> tuple[Any, Any]:
    """Return (lon, lat) of the first Point in a GeoJSON geometry."""
    if not isinstance(geometry, dict):
        return None, None
    if geometry.get("type") == "Point":
        return _point_coords(geometry.get("coordinates"))
    if geometry.get("type") == "GeometryCollection":
        for member in geometry.get("geometries") or []:
            lon, lat = _first_point(member)
            if lat is not None:
                return lon, lat
    return None, None


def _ring_centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    """Vertex average of a (lat, lon) ring; good enough to place a map pin."""
    return (
        sum(p[0] for p in ring) / len(ring),
        sum(p[1] for p in ring) / len(ring),
    )


def _geo_source_polygons(item: dict) -> list[list[tuple[float, float]]]:
    """Collect warning-area polygons from an EmergencyWA geo-source collection."""
    geo_source = item.get("geo-source")
    if not isinstance(geo_source, dict):
        return []
    polygons: list[list[tuple[float, float]]] = []
    for feature in geo_source.get("features") or []:
        if isinstance(feature, dict):
            polygons.extend(_extract_polygons(feature.get("geometry")))
    return polygons[:MAX_POLYGON_RINGS]


def _point_coords(coords: Any) -> tuple[Any, Any]:
    """Return (lon, lat) from a GeoJSON point coordinate list."""
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        return coords[0], coords[1]
    return None, None


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
        self._tz: tzinfo | None = None

    @property
    def consecutive_failures(self) -> int:
        """Return the number of consecutive failed updates."""
        return self._consecutive_failures

    @property
    def source(self) -> str:
        """Return the actual source of the most recent successful update."""
        return (self.data or {}).get("fallback_source") or self._feed_config.get("source", "unknown")

    def _parse_dt(self, date_str: str | None, time_str: str | None = None) -> datetime | None:
        """Parse a provider timestamp, treating naive values as the state's local time."""
        return _parse_incident_datetime(date_str, time_str, self._tz)

    async def _async_update_data(self) -> dict[str, Any]:
        if self._session is None:
            self._session = async_get_clientsession(self.hass)
        if self._tz is None and (tz_name := STATE_TIME_ZONES.get(self._state)):
            # Loading zoneinfo touches the filesystem, so HA does it off the loop.
            self._tz = await async_get_time_zone(tz_name)

        try:
            result = await self._fetch_data()
        except (aiohttp.ClientError, UpdateFailed) as exc:
            self._record_failure(exc)
            raise UpdateFailed(f"Error fetching {self._state} incidents: {exc}") from exc
        except Exception as exc:
            self._record_failure(exc)
            _LOGGER.error("Unexpected error fetching %s incidents: %s", self._state, exc)
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

        # Reset backoff on success
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0
            self.update_interval = timedelta(seconds=self._base_update_seconds)
            _LOGGER.info("Feed recovered, reset update interval to %s seconds", self._base_update_seconds)
        ir.async_delete_issue(self.hass, DOMAIN, self.feed_issue_id)
        if result.get("fallback_source"):
            ir.async_create_issue(
                self.hass, DOMAIN, self.fallback_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="sa_map_fallback",
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.fallback_issue_id)
        return result

    @property
    def feed_issue_id(self) -> str:
        return f"feed_unavailable_{self._state.lower()}"

    @property
    def fallback_issue_id(self) -> str:
        return f"map_fallback_{self._state.lower()}"

    def _record_failure(self, exc: Exception) -> None:
        """Back off, and raise a Repairs issue once a feed has stayed down."""
        self._consecutive_failures += 1
        self._apply_backoff()
        # States with no feed at all get their own issue at setup instead.
        if (
            self._consecutive_failures >= FEED_ISSUE_FAILURE_THRESHOLD
            and self._state not in UNAVAILABLE_STATES
        ):
            ir.async_create_issue(
                self.hass, DOMAIN, self.feed_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="feed_unavailable",
                translation_placeholders={
                    "state": self._state,
                    "failures": str(self._consecutive_failures),
                    "error": str(exc)[:200],
                },
            )

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
        """Fetch a state feed and normalise coordinates to floats."""
        result = await self._fetch_feed_data()
        for incident in result.get("incidents", []):
            incident[ATTR_LATITUDE] = _to_float(incident.get(ATTR_LATITUDE))
            incident[ATTR_LONGITUDE] = _to_float(incident.get(ATTR_LONGITUDE))
        self._add_distances(result.get("incidents", []))
        return result

    def _home(self) -> tuple[float, float] | None:
        """Return HA's home location, if configured."""
        config = getattr(self.hass, "config", None)
        lat = _to_float(getattr(config, "latitude", None))
        lon = _to_float(getattr(config, "longitude", None))
        if lat is None or lon is None:
            return None
        return lat, lon

    def _add_distances(self, incidents: list[dict[str, Any]]) -> None:
        """Add distance/direction from home and sort nearest first.

        Sorting means the sensors' truncated incident lists keep the closest ones.
        """
        home = self._home()
        for incident in incidents:
            lat, lon = incident.get(ATTR_LATITUDE), incident.get(ATTR_LONGITUDE)
            distance = bearing = None
            in_area = False
            if home is not None:
                distance, in_area = distance_to_incident(
                    home[0], home[1], lat, lon, incident.get(ATTR_POLYGONS)
                )
                if lat is not None and lon is not None and not in_area and distance:
                    bearing = round(initial_bearing(home[0], home[1], lat, lon))
            incident[ATTR_DISTANCE_KM] = distance
            incident[ATTR_BEARING] = bearing
            incident[ATTR_DIRECTION] = compass_direction(bearing) if bearing is not None else None
            incident[ATTR_HOME_IN_AREA] = in_area
        if home is not None:
            incidents.sort(
                key=lambda i: i[ATTR_DISTANCE_KM] if i[ATTR_DISTANCE_KM] is not None else float("inf")
            )

    async def _fetch_feed_data(self) -> dict[str, Any]:
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
        if self._state == "ACT":
            return await self._fetch_act_georss()

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
                result = await self._parse_vic_data(resp)
            elif self._state == "QLD":
                return await self._parse_qld_data(resp)
            elif self._state == "WA":
                return await self._parse_wa_data(resp)
            else:
                raise UpdateFailed(f"No parser configured for {self._state}")

        # VIC: warnings (the highest-severity items) come from a second feed.
        # Like WA, a failed warnings fetch fails the update rather than
        # presenting incidents alone as a trustworthy picture.
        warnings_url = self._feed_config.get("warnings")
        if warnings_url:
            async with self._session.get(warnings_url, timeout=30) as warn_resp:
                if warn_resp.status != 200:
                    raise UpdateFailed(f"VIC warnings returned HTTP {warn_resp.status}")
                if "json" not in warn_resp.content_type.lower():
                    raise UpdateFailed(
                        f"VIC warnings returned {warn_resp.content_type}, expected JSON"
                    )
                warn_data = await warn_resp.json(content_type=None)
            result["incidents"].extend(self._parse_vic_warnings(warn_data))
        return result

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
            incident_dt = self._parse_dt(date_str, time_str)

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
                lon, lat = _point_coords(coords)
            elif geom.get("type") == "GeometryCollection":
                # Try to get first point from geometry collection
                for g in geom.get("geometries", []):
                    if g.get("type") == "Point" and g.get("coordinates"):
                        lon, lat = _point_coords(g["coordinates"])
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

            # pubDate is UTC (e.g. "5/10/2026 4:15:00 AM" for 15:15 AEDT).
            pub_date = props.get("pubDate")
            incident_dt = _parse_incident_datetime(pub_date, None, timezone.utc)

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
                ATTR_POLYGONS: _extract_polygons(geom),
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
            incident_dt = self._parse_dt(updated)

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

    def _parse_vic_warnings(self, data: Any) -> list[dict[str, Any]]:
        """Parse VicEmergency public warnings (osom GeoJSON) into incidents.

        The same feed also carries incidents, burn areas and earthquakes; only
        warnings are taken, since incidents already come from the main feed.
        """
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise UpdateFailed("VIC warnings response is not a GeoJSON feature collection")

        warnings = []
        for feature in data["features"]:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            if props.get("feedType") != "warning":
                continue
            geom = feature.get("geometry") or {}
            polygons = _extract_polygons(geom)
            lon, lat = _first_point(geom)
            if lat is None and polygons:
                lat, lon = _ring_centroid(polygons[0])

            cap = props.get("cap") if isinstance(props.get("cap"), dict) else {}
            level = props.get("category1") or props.get("name") or props.get("sourceTitle")
            identifier = props.get("id") or props.get("sourceId")
            updated = props.get("updated") or props.get("created")
            incident_dt = self._parse_dt(updated)

            warnings.append({
                # Prefixed so a warning id can never collide with an incident number.
                ATTR_INCIDENT_NO: f"warning-{identifier}" if identifier is not None else None,
                ATTR_TYPE: cap.get("event") or props.get("category2") or "Warning",
                ATTR_STATUS: props.get("action") or props.get("status"),
                ATTR_LEVEL: level,
                ATTR_SEVERITY: _norm_severity(level, None),
                ATTR_LOCATION_NAME: props.get("location"),
                ATTR_REGION: None,
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE: props.get("webHeadline"),
                ATTR_MESSAGE_LINK: props.get("url"),
                ATTR_AGENCY: cap.get("senderName") or props.get("sourceOrg") or "VIC EMV",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
                ATTR_POLYGONS: polygons,
            })
        return warnings

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
            polygons = _extract_polygons(geom)
            if coords:
                if geom.get("type") == "Point":
                    lon, lat = _point_coords(coords)
                elif polygons:
                    # Pin the map marker at the warning area's centroid
                    lat, lon = _ring_centroid(polygons[0])
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
            incident_dt = self._parse_dt(updated)

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
                ATTR_POLYGONS: polygons,
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
            incident_dt = self._parse_dt(updated)

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
                ATTR_POLYGONS: _geo_source_polygons(item),
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
            incident_dt = self._parse_dt(updated)

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
                ATTR_POLYGONS: _geo_source_polygons(item),
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
            incident_dt = self._parse_dt(pub_date)

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

    async def _fetch_act_georss(self) -> dict[str, Any]:
        """Fetch the ACT ESA current incidents GeoRSS feed."""
        url = self._feed_config.get("georss")
        async with self._session.get(url, timeout=30) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"ACT incidents returned HTTP {resp.status}")
            xml_string = await resp.text()
        return self._parse_act_georss(xml_string)

    def _parse_act_georss(self, xml_string: str) -> dict[str, Any]:
        """Parse the ACT ESA current incidents feed (RSS 2.0 + georss:point).

        Items carry type, agency, CAD id and statuses as their own elements,
        and the update time inside the description text. The feed has no
        warning levels, so its incidents are all severity "info".
        """
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            # A broken document is an outage, not a quiet day with no incidents.
            raise UpdateFailed(f"ACT incidents returned invalid XML: {exc}") from exc
        if root.tag != "rss" or root.find("channel") is None:
            raise UpdateFailed("ACT incidents response is not an RSS feed")

        namespaces = {"georss": "http://www.georss.org/georss"}
        incidents = []
        for item in root.find("channel").findall("item"):
            def text(tag: str) -> str:
                return (item.findtext(tag) or "").strip()

            description = text("description")

            def description_field(label: str) -> str | None:
                match = re.search(
                    rf"{label}:\s*(.*?)\s*(?=(?:Incident|Location|Status|Suburb|Type|Agency|"
                    rf"Incident Number|Updated|Time of Call):|$)",
                    description,
                )
                return match.group(1).strip() or None if match else None

            lat = lon = None
            point = item.find("georss:point", namespaces)
            if point is not None and point.text:
                parts = point.text.split()
                if len(parts) >= 2:
                    lat, lon = _to_float(parts[0]), _to_float(parts[1])

            title = text("title")
            inc_type = text("type") or description_field("Type")
            location = description_field("Location") or description_field("Suburb")
            if not location and " - " in title:
                location = title.split(" - ", 1)[1].strip()
            status = text("resourceStatus") or description_field("Status")

            # "06 Oct 2026 17:13:47.65": drop the fractional seconds
            updated = description_field("Updated")
            if updated:
                updated = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", updated)
            incident_dt = self._parse_dt(updated)

            incidents.append({
                ATTR_INCIDENT_NO: text("guid") or text("cadid") or description_field("Incident Number") or title,
                ATTR_TYPE: inc_type.title() if inc_type else None,
                ATTR_STATUS: status,
                ATTR_LEVEL: None,
                ATTR_SEVERITY: _norm_severity(None, status),
                ATTR_LOCATION_NAME: location.title() if location else title,
                ATTR_REGION: description_field("Suburb"),
                ATTR_DATE: updated,
                ATTR_TIME: None,
                ATTR_INCIDENT_DATETIME: incident_dt.isoformat() if incident_dt else None,
                ATTR_MESSAGE_LINK: "https://esa.act.gov.au/",
                ATTR_AGENCY: f"ACT {text('agency')}".strip() if text("agency") else "ACT ESA",
                ATTR_LATITUDE: lat,
                ATTR_LONGITUDE: lon,
            })

        return {"incidents": incidents}

    async def async_close(self) -> None:
        """Release resources; the shared HA client session is never closed here."""
        self._session = None


# Backwards compatibility alias
CFSDataCoordinator = IncidentDataCoordinator
