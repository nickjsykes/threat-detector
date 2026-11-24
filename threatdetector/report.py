"""Write the week's results: a JSON file for machines and a Markdown report for people."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .gis import avt_buffer_for, nearest_avt, nearest_wood

MAGIC_URL = ("https://magic.defra.gov.uk/MagicMap.html?xygridref={e},{n}"
             "&startScale=10000&chosenLayers=ancwoodIndex")


def safe_name(text: str) -> str:
    return re.sub(r'[\\/*?:"<>|\s]+', "_", str(text)).strip("_")


def _public(app):
    """The app dict without bulky geometry."""
    return {k: v for k, v in app.items() if k not in ("site_rings",)}


def write_week(out_dir, council, week_id, week_label, result, source_url=None):
    """Write screening.json and report.md under out_dir/<council>/<week>/. Returns the folder."""
    folder = Path(out_dir) / safe_name(council["id"]) / safe_name(week_id)
    folder.mkdir(parents=True, exist_ok=True)
    counts = {k: len(v) for k, v in result.items()}
    counts["total"] = sum(counts.values())
    payload = {
        "council_id": council["id"],
        "council_name": council["name"],
        "week": week_label,
        "source": source_url,
        "counts": counts,
        **{k: [_public(a) for a in v] for k, v in result.items()},
    }
    (folder / "screening.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (folder / "report.md").write_text(render_markdown(council, week_label, result, counts), encoding="utf-8")
    return folder


def _app_header(app):
    link = f"[{app['app_no']}]({app['url']})" if app.get("url") else app["app_no"]
    return f"### {link}\n\n{app.get('address', '')}\n\n> {app.get('proposal', '')}\n"


def _facts(app):
    lines = [f"- **Type:** {app['classification']} (buffer {app['required_buffer']:g} m)",
             f"- **Placed by:** {app.get('location_source', 'unknown')}"]
    wood = nearest_wood(app.get("matched_woodlands"))
    if wood:
        lines.append(f"- **Nearest ancient woodland:** {wood['name']}, {wood['exact_distance_m']} m "
                     f"({wood['classification']})")
    avt = nearest_avt(app.get("matched_trees"))
    if avt:
        tree = f"{avt['status']} {avt['species']}, {avt['exact_distance_m']} m"
        if avt.get("url"):
            tree = f"[{tree}]({avt['url']})"
        lines.append(f"- **Nearest ancient/veteran tree:** {tree} "
                     f"(tree buffer {avt_buffer_for(app['required_buffer']):g} m)")
    lines.append(f"- [Map on MAGIC]({MAGIC_URL.format(e=app['easting'], n=app['northing'])})")
    return "\n".join(lines)


def render_markdown(council, week_label, result, counts):
    out = [f"# {council['name']}: week {week_label}", ""]
    out.append(f"{counts['total']} applications. "
               f"**{counts['flagged']} flagged**, {counts['near_miss']} near misses, "
               f"{counts['clear']} clear, {counts['unplaced']} could not be placed, "
               f"{counts['ignored']} ignored by type.")
    out.append("")
    out.append("## Flagged")
    out.append("")
    if not result["flagged"]:
        out.append("None this week.")
    for app in result["flagged"]:
        out += [_app_header(app), app.get("threat_description", ""), "", _facts(app), ""]
    if result["near_miss"]:
        out += ["## Near misses", "",
                "Outside the buffer, but close enough that the site plan is worth a look.", ""]
        for app in result["near_miss"]:
            out += [_app_header(app), app["near_miss_note"], "", _facts(app), ""]
    if result["unplaced"]:
        out += ["## Could not be placed", "",
                "These are the kind of application that matters, but no location was found. Check by hand.", ""]
        for app in result["unplaced"]:
            link = f"[{app['app_no']}]({app['url']})" if app.get("url") else app["app_no"]
            out.append(f"- {link}: {app.get('address', '')} ({app['classification']})")
        out.append("")
    if result["ignored"]:
        out += ["## Ignored by type", ""]
        for app in result["ignored"]:
            out.append(f"- {app['app_no']}: {app['classification']}")
        out.append("")
    return "\n".join(out)


class State:
    """Remembers the last week screened per council, so re-runs don't repeat work."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def done(self, council_id, week_id):
        return self.data.get(council_id) == week_id

    def mark(self, council_id, week_id):
        self.data[council_id] = week_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
