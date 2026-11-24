import math

import pytest

from threatdetector import geometry as g

SQUARE = [[(0, 0), (100, 0), (100, 100), (0, 100)]]
WITH_HOLE = [[(0, 0), (100, 0), (100, 100), (0, 100)], [(40, 40), (60, 40), (60, 60), (40, 60)]]


def test_point_in_polygon_and_hole():
    assert g.point_in_polygon(50, 10, SQUARE)
    assert not g.point_in_polygon(150, 10, SQUARE)
    assert not g.point_in_polygon(50, 50, WITH_HOLE)   # in the hole
    assert g.point_in_polygon(20, 20, WITH_HOLE)


def test_distance_point_to_polygon():
    poly = {"type": "Polygon", "coordinates": SQUARE}
    assert g.distance_point_to_geometry(50, 50, poly) == 0.0
    assert g.distance_point_to_geometry(110, 50, poly) == pytest.approx(10.0)
    assert g.distance_point_to_geometry(103, 104, poly) == pytest.approx(5.0)
    # Inside the hole, the nearest edge is the hole's
    holed = {"type": "Polygon", "coordinates": WITH_HOLE}
    assert g.distance_point_to_geometry(50, 50, holed) == pytest.approx(10.0)


def test_distance_other_geometries():
    assert g.distance_point_to_geometry(3, 4, {"type": "Point", "coordinates": [0, 0]}) == pytest.approx(5.0)
    line = {"type": "LineString", "coordinates": [[0, 0], [100, 0]]}
    assert g.distance_point_to_geometry(50, 7, line) == pytest.approx(7.0)
    # An open line is not closed back to its start
    bent = {"type": "LineString", "coordinates": [[0, 0], [100, 0], [100, 100]]}
    assert g.distance_point_to_geometry(10, 60, bent) == pytest.approx(60.0)
    assert g.distance_point_to_geometry(0, 0, {}) == math.inf


def test_site_to_geometry():
    wood = {"type": "Polygon", "coordinates": SQUARE}
    near = [[(120, 0), (140, 0), (140, 20), (120, 20)]]
    overlap = [[(90, 90), (150, 90), (150, 150), (90, 150)]]
    crossing = [[(50, -10), (60, -10), (60, 200), (50, 200)]]   # no vertex inside either way
    assert g.distance_site_to_geometry(near, wood) == pytest.approx(20.0)
    assert g.distance_site_to_geometry(overlap, wood) == 0.0
    assert g.distance_site_to_geometry(crossing, wood) == 0.0


def test_centre_and_radius():
    cx, cy, r = g.centre_and_radius([[(0, 0), (100, 0), (100, 100), (0, 100)]])
    assert (cx, cy) == (50, 50)
    assert r == pytest.approx(math.hypot(50, 50))


def test_gridref():
    assert g.gridref_to_en("TQ 30 80") == (530000, 180000)
    assert g.gridref_to_en("SD1234567890") == (312345, 467890)
    assert g.gridref_to_en("nonsense") is None


def test_osgb_to_wgs84():
    # Ordnance Survey's worked example point. Expected values from pyproj (EPSG:27700 -> 4326).
    lat, lng = g.osgb36_to_wgs84(651409.903, 313177.270)
    assert lat == pytest.approx(52.657979, abs=0.00005)
    assert lng == pytest.approx(1.716052, abs=0.00005)
