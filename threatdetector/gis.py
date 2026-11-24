"""Where the ancient woodland and the ancient trees are.

Two backends with the same two methods:

* LiveGIS  - Natural England's Ancient Woodland Inventory (public WFS) and the
             Woodland Trust Ancient Tree Inventory (the public ATI map's API).
* LocalGIS - GeoJSON files on disk, in EPSG:27700. For offline runs, other
             datasets, and the tests.

Both return plain dicts, nearest first, each with exact_distance_m.
A failed lookup raises GISLookupError. It never comes back as "nothing nearby",
because a silent empty answer reads exactly like a clean week.
"""
from __future__ import annotations

import json
import math
import time

import requests

from . import geometry

AW_WFS_URL = "https://environment.data.gov.uk/spatialdata/ancient-woodland-england/wfs"
AW_WFS_LAYER = "dataset-f425f1e1-fc18-4b5a-88d8-76934125627c:Ancient_Woodland_England"
ATI_API_URL = "https://ati.woodlandtrust.org.uk/umbraco/api/trees/list"
ATI_TREE_URL = "https://ati.woodlandtrust.org.uk/tree-search/tree?treeid="
USER_AGENT = "ThreatDetector/1.0 (ancient woodland planning screening)"

# Individual ancient/veteran trees are harmed by physical closeness (roots, soil,
# canopy), not by the long-range ammonia buffers that apply to whole woods.
AVT_BUFFER_CAP_M = 50.0
# Always look at least this far, so the report can say how near the nearest one is.
SEARCH_SWEEP_M = 500.0


class GISLookupError(Exception):
    """A GIS service could not be queried. Never treat this as 'nothing nearby'."""


def avt_buffer_for(required_buffer: float) -> float:
    return min(float(required_buffer), AVT_BUFFER_CAP_M)


def nearest_avt(trees):
    """Nearest ancient or veteran tree (notable trees never flag), or None."""
    avts = [t for t in trees or [] if t.get("is_avt")]
    return min(avts, key=lambda t: t["exact_distance_m"]) if avts else None


def nearest_wood(woods):
    return min(woods, key=lambda w: w["exact_distance_m"]) if woods else None


def _is_avt(status: str) -> bool:
    s = (status or "").lower()
    return "ancient" in s or "veteran" in s


def remeasure_trees(site_rings, trees):
    """Re-measure trees from the red line rather than its centre, nearest first."""
    for t in trees:
        en = (t.get("easting"), t.get("northing"))
        if en[0] is None:
            en = geometry.gridref_to_en(t.get("grid_reference"))
        if en and en[0] is not None:
            t["exact_distance_m"] = round(geometry.distance_point_to_site(site_rings, *en), 1)
    trees.sort(key=lambda t: t["exact_distance_m"])
    return trees


def _distance(easting, northing, geom, site_rings):
    if site_rings:
        return geometry.distance_site_to_geometry(site_rings, geom)
    return geometry.distance_point_to_geometry(easting, northing, geom)


def _wood_record(props, dist):
    return {
        "name": (props.get("name") or "").strip() or "Unnamed wood",
        "status": props.get("status", "Unknown"),
        "classification": props.get("themname") or props.get("classification") or "Unknown",
        "area_ha": props.get("area") or props.get("area_ha") or 0.0,
        "exact_distance_m": round(dist, 1),
    }


# --------------------------------------------------------------------------
# Live services
# --------------------------------------------------------------------------
class LiveGIS:
    def __init__(self, session: requests.Session | None = None, attempts: int = 3, timeout: int = 45):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.attempts = attempts
        self.timeout = timeout

    def _get(self, url, params, headers=None):
        last = None
        for attempt in range(self.attempts):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 200:
                    return r
                last = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last = str(e)
            if attempt < self.attempts - 1:
                time.sleep(2 * (attempt + 1))
        raise GISLookupError(f"{url}: {last}")

    def woodland_near(self, easting, northing, search_m, site_rings=None, site_radius=0.0):
        reach = search_m + site_radius
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": AW_WFS_LAYER, "outputFormat": "application/json",
            "bbox": f"{easting - reach},{northing - reach},{easting + reach},{northing + reach},"
                    "urn:ogc:def:crs:EPSG::27700",
        }
        try:
            features = self._get(AW_WFS_URL, params).json().get("features", [])
        except GISLookupError:
            raise
        except Exception as e:  # bad JSON etc.
            raise GISLookupError(f"ancient woodland WFS ({easting},{northing}): {e}") from e
        out = []
        for f in features:
            dist = _distance(easting, northing, f.get("geometry") or {}, site_rings)
            if dist <= search_m:
                out.append(_wood_record(f.get("properties") or {}, dist))
        return sorted(out, key=lambda w: w["exact_distance_m"])

    def trees_near(self, easting, northing, search_m):
        lat, lng = geometry.osgb36_to_wgs84(easting, northing)
        reach = max(float(search_m), 15.0) + 25.0
        dlat = reach / 111320.0
        dlng = reach / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
        box = {"nwLat": lat + dlat, "nwLng": lng - dlng, "seLat": lat - dlat, "seLng": lng + dlng}
        out, page = [], 1
        try:
            while page <= 25:
                data = self._get(ATI_API_URL, dict(box, pn=page),
                                 headers={"Accept": "application/json"}).json()
                for it in data.get("items") or []:
                    if it.get("latitude") is None or it.get("longitude") is None:
                        continue
                    dist = geometry.latlng_distance_m(lat, lng, it["latitude"], it["longitude"])
                    if dist > float(search_m):
                        continue
                    species = it.get("species")
                    species = species.get("name") if isinstance(species, dict) else species
                    status = it.get("veteranStatus")
                    status = (status.get("name") if isinstance(status, dict) else status) or "Notable tree"
                    tree_id = it.get("id")
                    out.append({
                        "species": species or "Unknown species",
                        "status": status,
                        "is_avt": _is_avt(status),
                        "exact_distance_m": round(dist, 1),
                        "grid_reference": it.get("gridReference", ""),
                        "url": f"{ATI_TREE_URL}{tree_id}" if tree_id else "",
                    })
                if page >= (data.get("totalPages") or 1):
                    break
                page += 1
        except GISLookupError:
            raise
        except Exception as e:
            raise GISLookupError(f"Ancient Tree Inventory ({easting},{northing}): {e}") from e
        return sorted(out, key=lambda t: t["exact_distance_m"])


# --------------------------------------------------------------------------
# Local GeoJSON
# --------------------------------------------------------------------------
class LocalGIS:
    """Woodland polygons and tree points from GeoJSON files in EPSG:27700.

    Tree properties used: status (e.g. "Veteran", "Ancient", "Notable"), species.
    """

    def __init__(self, woodland_path=None, trees_path=None):
        self.woods = self._load(woodland_path)
        self.trees = self._load(trees_path)

    @staticmethod
    def _load(path):
        if not path:
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("type") != "FeatureCollection":
            raise ValueError(f"{path}: expected a GeoJSON FeatureCollection")
        return data.get("features") or []

    def woodland_near(self, easting, northing, search_m, site_rings=None, site_radius=0.0):
        out = []
        for f in self.woods:
            dist = _distance(easting, northing, f.get("geometry") or {}, site_rings)
            if dist <= search_m:
                out.append(_wood_record(f.get("properties") or {}, dist))
        return sorted(out, key=lambda w: w["exact_distance_m"])

    def trees_near(self, easting, northing, search_m):
        out = []
        for f in self.trees:
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            x, y = geom["coordinates"][:2]
            dist = math.hypot(x - easting, y - northing)
            if dist > search_m:
                continue
            props = f.get("properties") or {}
            status = props.get("status") or "Notable tree"
            out.append({
                "species": props.get("species") or "Unknown species",
                "status": status,
                "is_avt": _is_avt(status),
                "exact_distance_m": round(dist, 1),
                "easting": x,
                "northing": y,
                "grid_reference": props.get("grid_reference", ""),
                "url": props.get("url", ""),
            })
        return sorted(out, key=lambda t: t["exact_distance_m"])
