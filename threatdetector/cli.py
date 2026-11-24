"""Command line.

    python -m threatdetector run                     every council in councils.json
    python -m threatdetector run --council wandf     one council
    python -m threatdetector screen apps.csv --woodland aw.geojson   offline, from a CSV

Exit codes: 0 = screened something, 2 = nothing new since last run, 1 = a council failed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import requests
import urllib3

from .gis import LiveGIS, LocalGIS
from .report import State, write_week
from .screen import ScreeningFailed, screen_applications
from .sources import idox, wandf

DEFAULT_CONFIG = Path(__file__).with_name("councils.json")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def load_councils(path, only=None):
    councils = json.loads(Path(path).read_text(encoding="utf-8"))
    if only:
        councils = [c for c in councils if c["id"] in only]
        missing = set(only) - {c["id"] for c in councils}
        if missing:
            raise SystemExit(f"Unknown council id(s): {', '.join(sorted(missing))}")
    return councils


def make_gis(args):
    if args.woodland:
        return LocalGIS(args.woodland, args.trees)
    return LiveGIS()


def run_council(council, args, gis, state):
    session = requests.Session()
    session.headers["User-Agent"] = BROWSER_UA
    if council.get("verify_tls", True) is False:
        session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    print(f"\n== {council['name']} ==")
    source_url = None
    if council["type"] == "wandf_pdf":
        week_id, week_label, apps, source_url = wandf.fetch_week(session, url=args.url)
    elif council["type"] == "idox":
        week_id, week_label, apps = idox.fetch_week(session, council["base_url"], week=args.week)
        source_url = council["base_url"]
    else:
        raise ValueError(f"Unknown council type: {council['type']}")
    print(f"  Week {week_label}: {len(apps)} applications.")

    explicit = args.url or args.week
    if not (args.force or explicit) and state.done(council["id"], week_id):
        print("  Already screened. Nothing new.")
        return "duplicate"

    if council["type"] == "wandf_pdf":
        if council.get("red_lines"):
            wandf.place_sites(session, apps)
    else:
        lookup = idox.UprnLookup(args.uprn_db) if args.uprn_db else None
        idox.place_sites(session, council["base_url"], apps,
                         red_lines=council.get("red_lines", False), uprn_lookup=lookup)

    result = screen_applications(apps, gis, workers=args.workers)
    folder = write_week(args.out, council, week_id, week_label, result, source_url)
    state.mark(council["id"], week_id)
    print(f"  {len(result['flagged'])} flagged, {len(result['near_miss'])} near misses. Report: {folder / 'report.md'}")
    return "screened"


def cmd_run(args):
    councils = load_councils(args.config, args.council)
    gis = make_gis(args)
    state = State(Path(args.out) / "state.json")
    outcomes = {}
    for council in councils:
        try:
            outcomes[council["id"]] = run_council(council, args, gis, state)
        except ScreeningFailed as e:
            print(f"  FAILED (nothing saved, will retry next run): {e}")
            outcomes[council["id"]] = "failed"
        except Exception as e:  # noqa: BLE001 - one council failing must not stop the others
            print(f"  FAILED: {type(e).__name__}: {e}")
            outcomes[council["id"]] = "failed"
    print("\nSummary: " + ", ".join(f"{k} {v}" for k, v in outcomes.items()))
    # An unscreened council is a failure, not a footnote.
    if "failed" in outcomes.values():
        return 1
    return 0 if "screened" in outcomes.values() else 2


def read_csv(path):
    """CSV with columns app_no, address, proposal, easting, northing (status, url optional)."""
    apps = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            e, n = (row.get("easting") or "").strip(), (row.get("northing") or "").strip()
            apps.append({
                "app_no": row["app_no"].strip(),
                "address": row.get("address", "").strip(),
                "proposal": row.get("proposal", "").strip(),
                "status": row.get("status", "").strip(),
                "url": row.get("url", "").strip(),
                "easting": float(e) if e else None,
                "northing": float(n) if n else None,
                "location_source": row.get("location_source", "").strip() or "council grid point",
            })
    return apps


def cmd_screen(args):
    apps = read_csv(args.csv)
    result = screen_applications(apps, make_gis(args), workers=args.workers)
    council = {"id": args.name, "name": args.name}
    folder = write_week(args.out, council, args.label, args.label, result, str(args.csv))
    print(f"{len(apps)} applications: {len(result['flagged'])} flagged, "
          f"{len(result['near_miss'])} near misses. Report: {folder / 'report.md'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="threatdetector",
                                description="Screen planning applications against ancient woodland and ancient trees.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--out", default="output", help="folder for reports and state (default: ./output)")
        sp.add_argument("--woodland", help="local ancient woodland GeoJSON (EPSG:27700) instead of the live service")
        sp.add_argument("--trees", help="local ancient tree GeoJSON points (EPSG:27700); used with --woodland")
        sp.add_argument("--workers", type=int, default=4, help="parallel GIS lookups (default 4; the woodland service times out under heavier load)")

    r = sub.add_parser("run", help="fetch this week's lists from the councils and screen them")
    common(r)
    r.add_argument("--config", default=str(DEFAULT_CONFIG), help="councils JSON file")
    r.add_argument("--council", action="append", help="council id; repeat for several (default: all)")
    r.add_argument("--week", help="Idox only: a past week, as the portal labels it, e.g. '13 Jul 2026'")
    r.add_argument("--url", help="W&F only: screen this weekly list PDF instead of the newest")
    r.add_argument("--force", action="store_true", help="screen even if this week was done before")
    r.add_argument("--uprn-db", help="OS Open UPRN SQLite (tools/build_uprn_db.py) for better Idox placement")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("screen", help="screen applications from a CSV file")
    common(s)
    s.add_argument("csv", help="CSV: app_no,address,proposal,easting,northing[,status,url]")
    s.add_argument("--name", default="csv", help="name for the report (default: csv)")
    s.add_argument("--label", default="manual", help="week label for the report folder (default: manual)")
    s.set_defaults(func=cmd_screen)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
