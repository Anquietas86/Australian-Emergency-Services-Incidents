"""Shared utility functions for the aus_emergency integration."""

from __future__ import annotations

from math import radians, degrees, sin, cos, sqrt, atan2


def haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    radius: float = 6371000.0,
) -> float:
    """Calculate the great-circle distance between two points using Haversine.

    Args:
        lat1, lon1: First point coordinates in decimal degrees.
        lat2, lon2: Second point coordinates in decimal degrees.
        radius: Earth radius in metres (default 6371000).
            Use 6371000 for metres, 6371 for kilometres.

    Returns:
        Distance in the same unit as radius.
    """
    lat1_r, lon1_r = radians(lat1), radians(lon1)
    lat2_r, lon2_r = radians(lat2), radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius * c


COMPASS_POINTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial bearing in degrees (0-360) from point 1 to point 2."""
    lat1_r, lat2_r = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(lat2_r)
    y = cos(lat1_r) * sin(lat2_r) - sin(lat1_r) * cos(lat2_r) * cos(dlon)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def compass_direction(bearing: float) -> str:
    """Return an 8-point compass direction for a bearing in degrees."""
    return COMPASS_POINTS[int((bearing % 360.0) / 45.0 + 0.5) % 8]


def point_in_polygon(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting test for a point inside a (lat, lon) ring.

    Treats coordinates as planar, which is accurate enough at warning-area scale.
    """
    inside = False
    count = len(ring)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if (lat_i > lat) != (lat_j > lat):
            cross_lon = lon_i + (lat - lat_i) * (lon_j - lon_i) / (lat_j - lat_i)
            if lon < cross_lon:
                inside = not inside
        j = i
    return inside


def distance_to_incident(
    home_lat: float,
    home_lon: float,
    lat: float | None,
    lon: float | None,
    polygons: list[list[tuple[float, float]]] | None = None,
) -> tuple[float | None, bool]:
    """Return (distance in km, home inside a warning area) for an incident.

    Inside any polygon counts as 0 km. Otherwise the distance is to the
    incident point or the nearest polygon vertex, whichever is closer.
    """
    candidates: list[float] = []
    for ring in polygons or []:
        if point_in_polygon(home_lat, home_lon, ring):
            return 0.0, True
        candidates.extend(
            haversine_distance(home_lat, home_lon, p_lat, p_lon, radius=6371.0)
            for p_lat, p_lon in ring
        )
    if lat is not None and lon is not None:
        candidates.append(haversine_distance(home_lat, home_lon, lat, lon, radius=6371.0))
    if not candidates:
        return None, False
    return round(min(candidates), 1), False


def public_incident(item: dict) -> dict:
    """Drop internal keys (like warning polygons) before exposing an incident."""
    return {k: v for k, v in item.items() if not k.startswith("_")}
