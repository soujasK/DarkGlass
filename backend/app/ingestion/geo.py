"""Small spherical-geometry helpers used by topology ingestion."""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lon, lat) points in kilometres."""
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _wrap(dlon: float) -> float:
    return (dlon + 180.0) % 360.0 - 180.0


def project_on_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, tuple[float, float], float]:
    """Closest point on segment a-b to p using a local equirectangular frame centred on p.

    Returns (t, point, distance_km) with t in [0, 1]. Accurate to well under 1%
    for the short offsets that matter when snapping a landing station to a route.
    """
    kx = 111.320 * math.cos(math.radians(p[1]))
    ky = 110.574
    ax, ay = _wrap(a[0] - p[0]) * kx, (a[1] - p[1]) * ky
    bx, by = _wrap(b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
    dlon = _wrap(b[0] - a[0])
    q = (a[0] + dlon * t, a[1] + (b[1] - a[1]) * t)
    if q[0] > 180:
        q = (q[0] - 360, q[1])
    elif q[0] < -180:
        q = (q[0] + 360, q[1])
    return t, q, haversine_km(p, q)


def great_circle_points(a: tuple[float, float], b: tuple[float, float], step_km: float = 150.0):
    """Interpolated (lon, lat) points along the great circle from a to b, inclusive."""
    d = haversine_km(a, b)
    n = max(1, int(math.ceil(d / step_km)))
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    delta = d / EARTH_RADIUS_KM
    if delta == 0:
        return [a, b]
    out = []
    for i in range(n + 1):
        f = i / n
        s1 = math.sin((1 - f) * delta) / math.sin(delta)
        s2 = math.sin(f * delta) / math.sin(delta)
        x = s1 * math.cos(lat1) * math.cos(lon1) + s2 * math.cos(lat2) * math.cos(lon2)
        y = s1 * math.cos(lat1) * math.sin(lon1) + s2 * math.cos(lat2) * math.sin(lon2)
        z = s1 * math.sin(lat1) + s2 * math.sin(lat2)
        out.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return out
