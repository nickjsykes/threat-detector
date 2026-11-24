"""The screening itself: classify, measure, flag.

Input is a list of applications as dicts. Sources fill these keys:

    app_no, address, proposal          from the council's list
    easting, northing                  BNG metres (None if the site couldn't be placed)
    site_rings                         optional red-line boundary [[(e, n), ...], ...]
    location_source                    "red line", "UPRN address point", "council grid point", "postcode"
    status, url                        optional

The result says, for every application, whether it was ignored (and why),
could not be placed, was screened clean, or is flagged for a human to check.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import geometry
from .classify import classify_proposal, is_decided
from .gis import GISLookupError, SEARCH_SWEEP_M, avt_buffer_for, nearest_avt, nearest_wood, remeasure_trees

# How far outside the buffer still earns a second look, by how well the site
# was placed. A rural postcode centre can sit 300 m from the actual plot.
NEAR_MISS_MARGIN_M = {
    "red line": 50.0,
    "UPRN address point": 100.0,
    "council grid point": 50.0,
}
NEAR_MISS_MARGIN_DEFAULT_M = 250.0  # postcode centre, or anything unknown


class ScreeningFailed(Exception):
    """Some GIS lookups failed. Nothing is saved, so the next run tries the week again."""


def describe_threat(app, wood, avt, wood_hit, tree_hit):
    """One plain paragraph saying what is near and why it matters."""
    tree_works = "tree works" in (app.get("classification") or "").lower()
    buffer_m = app["required_buffer"]
    parts = []
    if wood_hit and wood:
        name, d = wood["name"], wood["exact_distance_m"]
        if tree_works:
            parts.append(f"Proposed tree works within {d}m of {name} ancient woodland. Check whether the "
                         "trees are within, or part of, the ancient woodland.")
        elif buffer_m >= 500:
            parts.append(f"Within {d}m of {name} ancient woodland ({buffer_m:g}m check for intensive "
                         "livestock). Risk of ammonia, nitrogen deposition and dust reaching the wood.")
        else:
            parts.append(f"{d}m from {name} ancient woodland, inside the {buffer_m:g}m buffer. Risk of root "
                         "damage, soil compaction, canopy conflict and edge effects during construction.")
    if tree_hit and avt:
        d, status, species = avt["exact_distance_m"], (avt.get("status") or "veteran tree").lower(), avt.get("species")
        if tree_works:
            parts.append(f"Works are {d}m from a recorded {status} ({species}) on the Ancient Tree Inventory. "
                         "Risk of direct harm to the tree.")
        else:
            parts.append(f"A recorded {status} ({species}) is {d}m from the site (Ancient Tree Inventory). "
                         "Risk to its roots, soil and canopy during construction.")
    if tree_works:
        parts.append("Tree works: only worth a comment if the works are not for safety or conservation. "
                     "Confirm the stated reason first.")
    return " ".join(parts)


def near_miss_note(app):
    """A note if an unflagged site is close enough outside its buffer to deserve a look."""
    buffer_m = app.get("required_buffer") or 0
    source = app.get("location_source") or "postcode"
    margin = NEAR_MISS_MARGIN_M.get(source, NEAR_MISS_MARGIN_DEFAULT_M)
    facts = []
    wood = nearest_wood(app.get("matched_woodlands"))
    if wood and wood["exact_distance_m"] <= buffer_m + margin:
        facts.append(f"Ancient woodland {wood['exact_distance_m']}m ({wood['name']}), buffer {buffer_m:g}m")
    avt = nearest_avt(app.get("matched_trees"))
    avt_buf = avt_buffer_for(buffer_m)
    if avt and avt["exact_distance_m"] <= avt_buf + margin:
        facts.append(f"{avt['status']} ({avt['species']}) {avt['exact_distance_m']}m, buffer {avt_buf:g}m")
    if not facts:
        return None
    return "; ".join(facts) + f". Measured from the {source}."


def screen_applications(apps, gis, workers=8):
    """Screen a week's applications. Returns a dict of lists; mutates the app dicts."""
    ignored, unplaced, active = [], [], []
    for app in apps:
        if is_decided(app.get("status")):
            app.update(classification=f"Ignored - already decided ({app.get('status')})",
                       required_buffer=0.0, outcome="ignored")
            ignored.append(app)
            continue
        buffer_m, classification = classify_proposal(app.get("proposal", ""), app.get("app_no", ""))
        app["classification"] = classification
        if buffer_m is None:
            app.update(required_buffer=0.0, outcome="ignored")
            ignored.append(app)
            continue
        app["required_buffer"] = buffer_m
        if app.get("site_rings"):
            # A red line places the site even when there's no postcode or grid point.
            cx, cy, app["site_radius"] = geometry.centre_and_radius(app["site_rings"])
            app["easting"], app["northing"] = round(cx), round(cy)
        if app.get("easting") is None or app.get("northing") is None:
            app["outcome"] = "unplaced"
            unplaced.append(app)
            continue
        active.append(app)

    def lookup(app):
        rings = app.get("site_rings")
        radius = app.get("site_radius", 0.0)
        try:
            woods = gis.woodland_near(app["easting"], app["northing"],
                                      max(SEARCH_SWEEP_M, app["required_buffer"]),
                                      site_rings=rings, site_radius=radius)
            trees = gis.trees_near(app["easting"], app["northing"],
                                   max(SEARCH_SWEEP_M, avt_buffer_for(app["required_buffer"])) + radius)
            if rings:
                remeasure_trees(rings, trees)
            return app, woods, trees, None
        except GISLookupError as e:
            return app, None, None, str(e)

    failures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lookup, active))
    for app, woods, trees, err in results:
        if err:
            failures.append(f"{app['app_no']}: {err}")
            continue
        app["matched_woodlands"], app["matched_trees"] = woods, trees
        wood, avt = nearest_wood(woods), nearest_avt(trees)
        wood_hit = bool(wood) and wood["exact_distance_m"] <= app["required_buffer"]
        tree_hit = bool(avt) and avt["exact_distance_m"] <= avt_buffer_for(app["required_buffer"])
        if wood_hit or tree_hit:
            app["outcome"] = "flagged"
            app["threat_description"] = describe_threat(app, wood, avt, wood_hit, tree_hit)
        else:
            app["outcome"] = "clear"
            note = near_miss_note(app)
            if note:
                app["outcome"] = "near_miss"
                app["near_miss_note"] = note
    if failures:
        raise ScreeningFailed(f"GIS lookup failed for {len(failures)} of {len(active)} applications: "
                              + "; ".join(failures[:5]))

    return {
        "flagged": [a for a in active if a["outcome"] == "flagged"],
        "near_miss": [a for a in active if a["outcome"] == "near_miss"],
        "clear": [a for a in active if a["outcome"] == "clear"],
        "unplaced": unplaced,
        "ignored": ignored,
    }
