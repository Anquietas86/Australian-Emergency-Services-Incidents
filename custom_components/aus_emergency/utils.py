"""Shared utility functions for the aus_emergency integration."""

from math import radians, sin, cos, sqrt, atan2


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
