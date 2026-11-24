"""Plain-Python geometry in British National Grid metres (EPSG:27700).

No GIS libraries needed. Distances are planar, which is exact enough at the
scale we screen over (a few hundred metres to 5 km).

Geometries are GeoJSON-style dicts: {"type": "Polygon", "coordinates": [...]}.
A "site" is a list of exterior rings [(e, n), ...] - the red-line boundary.
"""
from __future__ import annotations

import math
import re


# --------------------------------------------------------------------------
# Points, segments, rings
# --------------------------------------------------------------------------
def dist_to_segment(x, y, ax, ay, bx, by):
    """Distance from (x, y) to the segment (ax, ay)-(bx, by)."""
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    if length2 == 0:
        return math.hypot(x - ax, y - ay)
    t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / length2))
    return math.hypot(x - (ax + t * vx), y - (ay + t * vy))


def point_in_ring(x, y, ring):
    """Ray-casting test. Works whether or not the ring repeats its first point."""
    inside = False
    n = len(ring)
    for i in range(n):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def point_in_polygon(x, y, polygon):
    """polygon = [outer_ring, hole, hole, ...]. Inside the outer, outside every hole."""
    if not polygon or not polygon[0]:
        return False
    if not point_in_ring(x, y, polygon[0]):
        return False
    return not any(point_in_ring(x, y, hole) for hole in polygon[1:] if hole)


def _segments(ring):
    n = len(ring)
    return [(ring[i], ring[(i + 1) % n]) for i in range(n)]


def _cross(p, q, r, s):
    """True if segment p-q properly crosses segment r-s."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return ((orient(p, q, r) > 0) != (orient(p, q, s) > 0)
            and (orient(r, s, p) > 0) != (orient(r, s, q) > 0))


# --------------------------------------------------------------------------
# Point to GeoJSON geometry
# --------------------------------------------------------------------------
def _polygons(geom):
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return [c]
    if t == "MultiPolygon":
        return c
    return []


def _lines(geom):
    """Every ring or line of a geometry as a list of points."""
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return c
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    if t == "LineString":
        return [c]
    if t == "MultiLineString":
        return c
    if t == "Point":
        return [[c]]
    if t == "MultiPoint":
        return [[p] for p in c]
    return []


def distance_point_to_geometry(x, y, geom):
    """Shortest distance (m) from a point to a geometry; 0 if inside a polygon."""
    if not geom:
        return float("inf")
    if any(point_in_polygon(x, y, poly) for poly in _polygons(geom)):
        return 0.0
    closed = geom.get("type") in ("Polygon", "MultiPolygon")
    best = float("inf")
    for line in _lines(geom):
        if len(line) == 1:
            best = min(best, math.hypot(x - line[0][0], y - line[0][1]))
            continue
        pairs = _segments(line) if closed else list(zip(line, line[1:]))
        for a, b in pairs:
            best = min(best, dist_to_segment(x, y, a[0], a[1], b[0], b[1]))
    return best


# --------------------------------------------------------------------------
# Site (red line) to geometry
# --------------------------------------------------------------------------
def _geom_contains(geom, x, y):
    return any(point_in_polygon(x, y, poly) for poly in _polygons(geom))


def distance_site_to_geometry(site_rings, geom):
    """Shortest distance (m) between a red line and a geometry; 0 if they touch or overlap."""
    other = _lines(geom)
    if not other or not site_rings:
        return float("inf")
    if any(_geom_contains(geom, x, y) for ring in site_rings for x, y in ring):
        return 0.0
    if any(point_in_ring(x, y, s) for s in site_rings for ring in other for x, y in ring):
        return 0.0
    best = float("inf")
    for s in site_rings:
        for a, b in _segments(s):
            for ring in other:
                o_segs = _segments(ring) if len(ring) > 1 else [(ring[0], ring[0])]
                for c, d in o_segs:
                    if len(ring) > 1 and _cross(a, b, c, d):
                        return 0.0
                    best = min(best,
                               dist_to_segment(a[0], a[1], c[0], c[1], d[0], d[1]),
                               dist_to_segment(b[0], b[1], c[0], c[1], d[0], d[1]),
                               dist_to_segment(c[0], c[1], a[0], a[1], b[0], b[1]),
                               dist_to_segment(d[0], d[1], a[0], a[1], b[0], b[1]))
    return best


def distance_point_to_site(site_rings, x, y):
    """Distance (m) from a point (e.g. a tree) to the red line; 0 if inside."""
    if any(point_in_ring(x, y, s) for s in site_rings):
        return 0.0
    return min(dist_to_segment(x, y, a[0], a[1], b[0], b[1])
               for s in site_rings for a, b in _segments(s))


def centre_and_radius(site_rings):
    """Bounding-box centre of the red line and the distance to its furthest vertex,
    so point-based searches can be widened to cover the whole site."""
    pts = [p for s in site_rings for p in s]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    return cx, cy, max(math.hypot(x - cx, y - cy) for x, y in pts)


# --------------------------------------------------------------------------
# Grid references and coordinate conversion
# --------------------------------------------------------------------------
_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def gridref_to_en(gr):
    """OS grid reference ('SD 12345 67890') -> (easting, northing), or None."""
    m = re.fullmatch(r"([A-HJ-Z]{2})(\d+)", (gr or "").replace(" ", "").upper())
    if not m or len(m.group(2)) % 2:
        return None
    l1, l2 = _LETTERS.index(m.group(1)[0]), _LETTERS.index(m.group(1)[1])
    e100 = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100 = (19 - (l1 // 5) * 5) - (l2 // 5)
    digits = m.group(2)
    half = len(digits) // 2
    scale = 10 ** (5 - half)
    return e100 * 100000 + int(digits[:half]) * scale, n100 * 100000 + int(digits[half:]) * scale


def osgb36_to_wgs84(easting, northing):
    """British National Grid (easting, northing) -> WGS84 (lat, lng).

    Inverse transverse Mercator on Airy 1830, then a 7-parameter Helmert shift.
    Good to a few metres, which is what the tree lookup needs.
    """
    a, b = 6377563.396, 6356256.909
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2)
    N0, E0 = -100000, 400000
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    n2, n3 = n * n, n * n * n

    lat, M = lat0, 0
    for _ in range(20):
        lat = (northing - N0 - M) / (a * F0) + lat
        Ma = (1 + n + 1.25 * n2 + 1.25 * n3) * (lat - lat0)
        Mb = (3 * n + 3 * n2 + 2.625 * n3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (1.875 * n2 + 1.875 * n3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = (35 / 24) * n3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
        if abs(northing - N0 - M) < 0.00001:
            break

    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    nu = a * F0 / math.sqrt(1 - e2 * sin_lat ** 2)
    rho = a * F0 * (1 - e2) / ((1 - e2 * sin_lat ** 2) ** 1.5)
    eta2 = nu / rho - 1
    tan_lat = math.tan(lat)
    tan2, tan4, tan6 = tan_lat ** 2, tan_lat ** 4, tan_lat ** 6
    sec_lat = 1 / cos_lat
    nu3, nu5, nu7 = nu ** 3, nu ** 5, nu ** 7

    VII = tan_lat / (2 * rho * nu)
    VIII = tan_lat / (24 * rho * nu3) * (5 + 3 * tan2 + eta2 - 9 * tan2 * eta2)
    IX = tan_lat / (720 * rho * nu5) * (61 + 90 * tan2 + 45 * tan4)
    X = sec_lat / nu
    XI = sec_lat / (6 * nu3) * (nu / rho + 2 * tan2)
    XII = sec_lat / (120 * nu5) * (5 + 28 * tan2 + 24 * tan4)
    XIIA = sec_lat / (5040 * nu7) * (61 + 662 * tan2 + 1320 * tan4 + 720 * tan6)

    dE = easting - E0
    lat_rad = lat - VII * dE ** 2 + VIII * dE ** 4 - IX * dE ** 6
    lon_rad = lon0 + X * dE - XI * dE ** 3 + XII * dE ** 5 - XIIA * dE ** 7

    # Helmert OSGB36 -> WGS84
    e2o = 1 - (b / a) ** 2
    sL, cL = math.sin(lat_rad), math.cos(lat_rad)
    nuH = a / math.sqrt(1 - e2o * sL ** 2)
    x1 = nuH * cL * math.cos(lon_rad)
    y1 = nuH * cL * math.sin(lon_rad)
    z1 = nuH * (1 - e2o) * sL

    tx, ty, tz = 446.448, -125.157, 542.060
    s = -20.4894e-6
    rx = math.radians(0.1502 / 3600)
    ry = math.radians(0.2470 / 3600)
    rz = math.radians(0.8421 / 3600)
    x2 = tx + (1 + s) * x1 + (-rz) * y1 + ry * z1
    y2 = ty + rz * x1 + (1 + s) * y1 + (-rx) * z1
    z2 = tz + (-ry) * x1 + rx * y1 + (1 + s) * z1

    a_w, b_w = 6378137.0, 6356752.3142
    e2w = 1 - (b_w / a_w) ** 2
    p = math.sqrt(x2 ** 2 + y2 ** 2)
    lat_w = math.atan2(z2, p * (1 - e2w))
    for _ in range(10):
        nu_w = a_w / math.sqrt(1 - e2w * math.sin(lat_w) ** 2)
        lat_w = math.atan2(z2 + e2w * nu_w * math.sin(lat_w), p)
    lon_w = math.atan2(y2, x2)
    return round(math.degrees(lat_w), 7), round(math.degrees(lon_w), 7)


def latlng_distance_m(lat1, lng1, lat2, lng2):
    """Approximate ground distance (m) between two nearby WGS84 points."""
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    x = math.radians(lng2 - lng1) * math.cos(mean_lat)
    y = math.radians(lat2 - lat1)
    return math.hypot(x, y) * 6371000.0
