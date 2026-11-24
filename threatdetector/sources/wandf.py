"""Westmorland & Furness (South Lakeland area): the weekly list PDF.

The council publishes one PDF a week. Each application carries an easting and
northing, so every site can be placed. Where the council's iShare map holds the
red line, distances are measured from that instead of the single grid point.
"""
from __future__ import annotations

import io
import json
import re
from concurrent.futures import ThreadPoolExecutor

from pypdf import PdfReader

COUNCIL_BASE_URL = "https://www.southlakeland.gov.uk"
WEEKLY_LIST_INDEX_URL = (COUNCIL_BASE_URL + "/planning-and-building/planning/planning-applications/"
                         "weekly-list-of-planning-applications/")
PORTAL_URL = "https://planningregister.westmorlandandfurness.gov.uk/Planning/Display/{app_no}"
ISHARE = "https://my.southlakeland.gov.uk"
ISHARE_MAPSOURCE = "SouthLakes/MyMapsSouthLakes"

APP_NO = re.compile(r"(\d{4}/\d{4}/[A-Z]{3,4})")
CLICK_TO_VIEW = re.compile(r"Click to View Application Details for (\d{4}/\d{4}/[A-Z]{3,4})")
POSTCODE = re.compile(r"\b([A-Z]{1,2})\s*\n?\s*(\d{1,2})\s+(\d)\s*\n?\s*([A-Z]{2})\b")
DATE_RANGE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+and\s+(\d{2})/(\d{2})/(\d{4})")
COORDS = re.compile(r"Easting\s+Northing\s+(\d{6})\s+(\d{6})", re.IGNORECASE)


# --------------------------------------------------------------------------
# Parsing (pure functions, tested offline)
# --------------------------------------------------------------------------
def read_date_range(first_page_text: str):
    """'... between: 13/07/2026 and 19/07/2026' -> ('2026-07-13', '2026-07-19'), or (None, None).

    The printed range is the truth. Filenames have carried the wrong year before.
    """
    m = DATE_RANGE.search(first_page_text or "")
    if not m:
        return None, None
    d1, m1, y1, d2, m2, y2 = m.groups()
    return f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"


def find_list_links(index_html: str):
    """Every weekly-list PDF link on the index page, absolute, in page order, deduplicated."""
    hrefs = re.findall(r'href="(/media/[^"]+/weekly-list-wandf[^"]*\.pdf)"', index_html, re.IGNORECASE)
    urls = []
    for href in hrefs:
        url = COUNCIL_BASE_URL + href
        if url not in urls:
            urls.append(url)
    return urls


def parse_weekly_list(pages: list[str]):
    """Applications from the text of each PDF page (page 1 is the cover and is skipped).

    Decided applications at the end of the list are dropped: nothing left to comment on.
    """
    apps = []
    for page_no, text in enumerate(pages[1:], start=2):
        text = text or ""
        cut = text.find("Determined Applications")
        if cut != -1:
            text = text[:cut]
        # Real applications appear in a "Click to View" line. Any other reference
        # on the page is an earlier permission quoted inside a proposal.
        # Blocks run from one real application to the next, so a quoted reference
        # can't cut an application off before its coordinates.
        authoritative = set(CLICK_TO_VIEW.findall(text))
        matches = [m for m in APP_NO.finditer(text)
                   if not authoritative or m.group(1) in authoritative]
        seen = set()
        for i, match in enumerate(matches):
            app_no = match.group(1)
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[match.start():end]
            coords = COORDS.search(block)
            # The "Click to View" line repeats the number; only the block with coordinates counts.
            if not coords or app_no in seen:
                continue
            seen.add(app_no)
            head = block[:coords.start()]
            pc = POSTCODE.search(head)
            if pc:
                postcode = f"{pc.group(1)}{pc.group(2)} {pc.group(3)}{pc.group(4)}"
                address = head[len(app_no):pc.start()].strip().replace("\n", ", ") + ", " + postcode
                proposal = head[pc.end():].strip().replace("\n", " ")
            else:
                lines = [ln.strip() for ln in head[len(app_no):].split("\n") if ln.strip()]
                address = lines[0] if len(lines) >= 2 else "Unknown address"
                proposal = " ".join(lines[1:] if len(lines) >= 2 else lines)
            apps.append({
                "app_no": app_no,
                "address": re.sub(r"\s+", " ", address).strip(),
                "proposal": re.sub(r"\s+", " ", proposal).strip(),
                "easting": int(coords.group(1)),
                "northing": int(coords.group(2)),
                "location_source": "council grid point",
                "url": PORTAL_URL.format(app_no=app_no),
                "page": page_no,
            })
        if cut != -1:
            break
    return apps


def pdf_pages(pdf_bytes: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages]


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
def fetch_week(session, url=None, log=print):
    """Download the newest weekly list (or the one at url).

    Returns (week_id, week_label, apps, source_url). week_id is the list's end
    date, used to tell whether this list has been screened before.
    """
    if url:
        candidates = [url]
    else:
        r = session.get(WEEKLY_LIST_INDEX_URL, timeout=30)
        r.raise_for_status()
        candidates = find_list_links(r.text)
        if not candidates:
            raise ValueError("No weekly list PDF links found on the council's index page.")
    best = None
    for candidate in candidates:
        r = session.get(candidate, timeout=60)
        r.raise_for_status()
        pages = pdf_pages(r.content)
        start, end = read_date_range(pages[0] if pages else "")
        log(f"  {candidate.rsplit('/', 1)[-1]}: {start} to {end}")
        if best is None or (end or "") > (best[1] or ""):
            best = (start, end, pages, candidate)
    assert best is not None
    start, end, pages, source = best
    if not end:
        raise ValueError(f"Could not read the date range printed in {source}")
    return end, f"{start} to {end}", parse_weekly_list(pages), source


def fetch_red_line(session, app_no, timeout=30):
    """Red-line exterior rings from the council iShare map, or [] if none is mapped.

    The search gives only the bounding box; the WFS has the true polygons but no
    attributes. So pull polygons in the box and keep the one whose bounds match.
    """
    r = session.get(f"{ISHARE}/MapGetImage.aspx", params={
        "Type": "json", "MapSource": ISHARE_MAPSOURCE, "RequestType": "GeoJSON",
        "ServiceAction": "SearchLayer", "ActiveTool": "MultiInfo", "ActiveLayer": "allPlanning",
        "mapid": "-1", "SearchLayer": "allPlanning", "SearchField": "reference", "SearchValue": app_no,
    }, timeout=timeout)
    if r.status_code != 200:
        raise ValueError(f"iShare search HTTP {r.status_code}")
    t = r.text
    feats = json.loads(t[t.find("["):t.rfind("]") + 1])[0].get("features") or []
    if not feats:
        return []
    c = feats[0]["geometry"]["coordinates"]
    pts = c[0] if c and isinstance(c[0][0], list) else c
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    r = session.get(f"{ISHARE}/GetOWS.ashx", params={
        "MAPSOURCE": ISHARE_MAPSOURCE, "SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
        "TYPENAME": "allPlanning", "BBOX": f"{x0},{y0},{x1},{y1},EPSG:27700",
    }, timeout=timeout)
    if r.status_code != 200:
        raise ValueError(f"iShare WFS HTTP {r.status_code}")
    for m in re.finditer(r"<gml:posList[^>]*>([^<]+)</gml:posList>", r.text):
        v = [float(a) for a in m.group(1).split()]
        ring = list(zip(v[0::2], v[1::2]))
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        if (abs(min(xs) - x0) < 0.05 and abs(max(xs) - x1) < 0.05
                and abs(min(ys) - y0) < 0.05 and abs(max(ys) - y1) < 0.05):
            return [ring]
    # Multi-part site: the box still contains the whole red line, so it can only err towards flagging.
    return [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]


def place_sites(session, apps, workers=8, log=print):
    """Swap the grid point for the red line wherever the council has mapped one."""
    def get(app):
        try:
            return app, fetch_red_line(session, app["app_no"]), None
        except Exception as e:  # noqa: BLE001 - any failure just means "use the grid point"
            return app, None, str(e)

    found = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for app, rings, err in pool.map(get, apps):
            if rings:
                app["site_rings"] = rings
                app["location_source"] = "red line"
                found += 1
            elif err:
                failed += 1
    log(f"  Red line found for {found} of {len(apps)}; lookup failed for {failed} (those use the grid point).")
