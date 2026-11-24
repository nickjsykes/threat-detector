"""Councils on the Idox Public Access portal (South Downs NPA, New Forest DC, and many others).

The weekly list gives addresses, not coordinates. Each site is placed as well as
the council allows, best first:

  1. red line   - the portal's own map WFS, where the council runs it
  2. UPRN       - the building's address point, from a local OS Open UPRN copy (optional)
  3. postcode   - postcode centre from postcodes.io (free, no key)

The near-miss net widens to match: a rural postcode centre can be 300 m off.
"""
from __future__ import annotations

import html as htmllib
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

POSTCODES_API_URL = "https://api.postcodes.io/postcodes"
GML = "{http://www.opengis.net/gml/3.2}"


def portal_root(base_url):
    """https://host/online-applications -> https://host"""
    return re.sub(r"/online-applications/?$", "", base_url.rstrip("/"))


# --------------------------------------------------------------------------
# Parsing (pure functions, tested offline)
# --------------------------------------------------------------------------
def week_options(form_html: str):
    """The 'week beginning' labels in the weekly list form, newest first."""
    return re.findall(r'<option value="(\d{2} \w+ \d{4})">', form_html)


def completed_weeks(options, today=None):
    """Only weeks that have finished. A mid-week run must not lock in half a list."""
    today = today or datetime.now()
    done = []
    for week in options:
        try:
            if today >= datetime.strptime(week, "%d %b %Y") + timedelta(days=7):
                done.append(week)
        except ValueError:
            done.append(week)
    return done


def _text(fragment: str) -> str:
    """HTML fragment -> plain text: tags gone, entities decoded, spaces collapsed."""
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def parse_results_page(html: str, base_url: str):
    """Applications from one page of Idox weekly list results."""
    apps = []
    root = portal_root(base_url)
    for block in re.findall(r'<li class="searchresult">(.*?)</li>', html, re.DOTALL):
        app = {}
        link = re.search(r'href="([^"]*keyVal=([^"&]+)[^"]*)"', block)
        if link:
            path = link.group(1).replace("&amp;", "&")
            app["key_val"] = link.group(2)
            app["url"] = root + path if path.startswith("/") else path
        addr = re.search(r'class="address"[^>]*>(.*?)</p>', block, re.DOTALL)
        if addr:
            address = _text(addr.group(1))
            app["address"] = address
            pc = re.search(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b", address, re.IGNORECASE)
            if pc:
                app["postcode"] = pc.group(1).strip().upper()
        ref = re.search(r"Ref\.?\s*No:\s*([A-Z0-9][A-Z0-9/\-\.]*)", block, re.IGNORECASE)
        if ref:
            app["app_no"] = ref.group(1).strip()
        desc = re.search(r'class="(?:description|summaryLinkTextClamp)"[^>]*>(.*?)</(?:p|div)>', block, re.DOTALL)
        if desc:
            app["proposal"] = _text(desc.group(1))
        status = re.search(r'class="value"[^>]*>([^<]+)', block)
        if status:
            app["status"] = _text(status.group(1))
        if app.get("address") and (app.get("app_no") or app.get("key_val")):
            app.setdefault("app_no", app.get("key_val"))
            app.setdefault("proposal", "")
            app.update(easting=None, northing=None, location_source="unplaced")
            apps.append(app)
    return apps


def next_page_url(html: str, base_url: str):
    tag = re.search(r'<a\b[^>]*class="next"[^>]*>', html)
    href = re.search(r'href\s*=\s*"([^"]+)"', tag.group(0)) if tag else None
    if not href:
        return None
    url = href.group(1).replace("&amp;", "&")
    return portal_root(base_url) + url if url.startswith("/") else url


def parse_red_line_gml(gml_bytes: bytes):
    """Exterior rings from the PAM map WFS (GML 3.2, EPSG:27700)."""
    rings = []
    for ext in ET.fromstring(gml_bytes).iter(f"{GML}exterior"):
        for pos_list in ext.iter(f"{GML}posList"):
            vals = [float(v) for v in (pos_list.text or "").split()]
            pts = list(zip(vals[0::2], vals[1::2]))
            if len(pts) >= 3:
                rings.append(pts)
    return rings


def week_range(week_label: str):
    """'13 Jul 2026' -> ('2026-07-13', '2026-07-19')."""
    try:
        start = datetime.strptime(week_label, "%d %b %Y")
    except ValueError:
        return None, None
    return start.strftime("%Y-%m-%d"), (start + timedelta(days=6)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
def fetch_week(session, base_url, week=None, log=print):
    """Scrape one week's list. Returns (week_id, week_label, apps).

    Without `week`, takes the latest completed week, stepping back up to two
    weeks if a portal hasn't filled the newest one yet.
    """
    r = session.get(f"{base_url}/search.do?action=weeklyList&searchType=Application", timeout=20)
    r.raise_for_status()
    csrf = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text)
    csrf = csrf.group(1) if csrf else ""
    options = week_options(r.text)
    if not options:
        raise ValueError("No weeks offered in the portal's weekly list form.")
    if week:
        if week not in options:
            raise ValueError(f"Week '{week}' not offered. Available: {', '.join(options[:8])}")
        tries = [week]
    else:
        tries = completed_weeks(options)[:3]
        if not tries:
            raise ValueError("No completed weeks offered yet.")
    for label in tries:
        apps = _fetch_list(session, base_url, csrf, label, log)
        if apps or week:
            start, end = week_range(label)
            return start or label, f"{start} to {end}" if start else label, apps
        log(f"  Week '{label}' is empty so far, trying the week before.")
    start, end = week_range(tries[0])
    return start or tries[0], f"{start} to {end}" if start else tries[0], []


def _fetch_list(session, base_url, csrf, week, log):
    r = session.post(f"{base_url}/weeklyListResults.do?action=firstPage", data={
        "_csrf": csrf, "searchCriteria.parish": "", "searchCriteria.ward": "",
        "week": week, "dateType": "DC_Validated", "searchType": "Application",
    }, timeout=20)
    r.raise_for_status()
    if "Too many results" in r.text:
        raise ValueError("Portal refused the weekly list: too many results.")
    apps = parse_results_page(r.text, base_url)
    html, page = r.text, 1
    while page < 50:
        url = next_page_url(html, base_url)
        if not url:
            break
        page += 1
        log(f"  Fetching page {page}...")
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            break
        html = r.text
        apps.extend(parse_results_page(html, base_url))
    return apps


def geocode_postcodes(apps, session=None, log=print):
    """Place apps at their postcode centre (postcodes.io bulk lookup)."""
    http = session or requests
    wanted = sorted({a["postcode"] for a in apps if a.get("postcode")})
    found = {}
    for i in range(0, len(wanted), 100):
        try:
            r = http.post(POSTCODES_API_URL, json={"postcodes": wanted[i:i + 100]}, timeout=15)
            r.raise_for_status()
            for item in r.json().get("result", []):
                res = item.get("result")
                if res and res.get("eastings") and res.get("northings"):
                    key = item["query"].replace(" ", "").upper()
                    found[key] = (int(res["eastings"]), int(res["northings"]))
        except Exception as e:  # noqa: BLE001
            log(f"  postcodes.io batch failed: {e}")
    placed = 0
    for app in apps:
        en = found.get((app.get("postcode") or "").replace(" ", "").upper())
        if en:
            app["easting"], app["northing"] = en
            app["location_source"] = "postcode"
            placed += 1
    log(f"  Placed {placed} of {len(apps)} by postcode.")


def fetch_red_line(session, base_url, key_val, timeout=30):
    """Red-line rings from the council's PAM map WFS, [] if none drawn."""
    root = portal_root(base_url)
    flt = ('<Filter xmlns="http://www.opengis.net/fes/2.0"><PropertyIsEqualTo>'
           '<ValueReference>KEYVAL</ValueReference><Literal>%s</Literal>'
           '</PropertyIsEqualTo></Filter>' % key_val)
    r = session.get(f"{root}/PAM/live/MapServer", params={
        "map": "pa", "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "Planning_Application_Polygons", "FILTER": flt,
    }, headers={"Referer": f"{root}/PAM/live/pamap/"}, timeout=timeout)
    if r.status_code != 200 or "FeatureCollection" not in r.text:
        raise ValueError(f"site boundary WFS HTTP {r.status_code}")
    return parse_red_line_gml(r.content)


def _polite_get(session, url, params, timeout, attempts=4):
    """Some portals answer bursts with HTTP 429 or a dropped connection. Go slowly."""
    for i in range(attempts):
        time.sleep(1.5)
        try:
            r = session.get(url, params=params, timeout=timeout)
        except requests.ConnectionError:
            time.sleep(10 * (i + 1))
            continue
        if r.status_code != 429:
            r.raise_for_status()
            return r
        time.sleep(int(r.headers.get("Retry-After") or 0) or 10 * (i + 1))
    raise ValueError(f"refused after {attempts} tries: {url}")


def fetch_uprn(session, base_url, key_val, timeout=30):
    """UPRN of the property linked to an application, or None."""
    r = _polite_get(session, f"{base_url}/applicationDetails.do",
                    {"activeTab": "relatedCases", "keyVal": key_val}, timeout)
    m = re.search(r'propertyDetails\.do\?[^"\']*?keyVal=([^&"\']+)', r.text)
    if not m:
        return None
    r = _polite_get(session, f"{base_url}/propertyDetails.do",
                    {"keyVal": m.group(1), "activeTab": "summary"}, timeout)
    m = (re.search(r"UPRN:?\s*</th>\s*<td>\s*(\d{6,12})", r.text)
         or re.search(r"UPRN:[^0-9]{0,200}?(\d{6,12})", r.text, re.S))
    return m.group(1) if m else None


class UprnLookup:
    """OS Open UPRN in a local SQLite file: table uprn(uprn INTEGER PRIMARY KEY, x REAL, y REAL).

    Build it with tools/build_uprn_db.py from the free OS Open UPRN download.
    """

    def __init__(self, path):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)

    def __call__(self, uprn):
        row = self.db.execute("SELECT x, y FROM uprn WHERE uprn = ?", (int(uprn),)).fetchone()
        return (row[0], row[1]) if row else None


def place_sites(session, base_url, apps, red_lines=False, uprn_lookup=None, workers=8, log=print):
    """Postcode first, then upgrade to red line or UPRN where possible."""
    geocode_postcodes(apps, log=log)
    if red_lines:
        def get(app):
            try:
                return app, fetch_red_line(session, base_url, app["key_val"]), None
            except Exception as e:  # noqa: BLE001
                return app, None, str(e)
        found = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for app, rings, _err in pool.map(get, [a for a in apps if a.get("key_val")]):
                if rings:
                    app["site_rings"], app["location_source"] = rings, "red line"
                    found += 1
        log(f"  Red line found for {found} of {len(apps)}.")
    if uprn_lookup:
        placed = 0
        # Sequential on purpose: portals rate-limit parallel requests.
        for app in [a for a in apps if a.get("key_val") and not a.get("site_rings")]:
            try:
                uprn = fetch_uprn(session, base_url, app["key_val"])
            except Exception:  # noqa: BLE001
                continue
            en = uprn_lookup(uprn) if uprn else None
            if en:
                app["easting"], app["northing"] = round(en[0]), round(en[1])
                app["uprn"], app["location_source"] = uprn, "UPRN address point"
                placed += 1
        log(f"  Placed {placed} by UPRN address point.")
