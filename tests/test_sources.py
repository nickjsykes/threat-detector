"""Parsers run against synthetic council output shaped like the real thing."""
from datetime import datetime

from threatdetector.sources import idox, wandf

COVER = "Weekly list of planning applications determined between: 05/01/2026 and 11/01/2026"

PAGE = """Application Number Location Proposal
2026/9001/FPA
1 Example Lane
Testville
ZZ1 1ZZ
Erection of single storey rear
extension to replace approval 2020/0001/FPA
Easting Northing 350210 490100
Click to View Application Details for 2026/9001/FPA
2026/9002/TPO
Sample House
Sample Road ZZ1 2ZZ
Fell one sycamore
Easting Northing 350300 490300
Click to View Application Details for 2026/9002/TPO
Determined Applications
2026/8000/FPA
Somewhere ZZ1 3ZZ
Already decided
Easting Northing 350000 490000
"""


def test_wandf_date_range_from_cover():
    assert wandf.read_date_range(COVER) == ("2026-01-05", "2026-01-11")
    assert wandf.read_date_range("no dates here") == (None, None)


def test_wandf_parse_skips_quoted_refs_and_decided():
    apps = wandf.parse_weekly_list([COVER, PAGE, "2026/7000/FPA\nnever read\nEasting Northing 1 2"])
    assert [a["app_no"] for a in apps] == ["2026/9001/FPA", "2026/9002/TPO"]
    first = apps[0]
    assert first["address"] == "1 Example Lane, Testville, ZZ1 1ZZ"
    assert first["proposal"].startswith("Erection of single storey rear extension")
    assert (first["easting"], first["northing"]) == (350210, 490100)
    assert first["url"].endswith("/2026/9001/FPA")


def test_wandf_find_links():
    html = ('<a href="/media/abc/weekly-list-wandf-0501-11012026.pdf">a</a>'
            '<a href="/media/abc/weekly-list-wandf-0501-11012026.pdf">dup</a>'
            '<a href="/media/def/weekly-list-wandf.pdf">b</a>')
    links = wandf.find_list_links(html)
    assert len(links) == 2 and links[0].startswith("https://")


BASE = "https://planning.example.gov.uk/online-applications"
RESULTS = """
<ul>
<li class="searchresult">
  <a href="/online-applications/applicationDetails.do?activeTab=summary&amp;keyVal=ABC123">Erection of garden room</a>
  <p class="address">1 Example Lane, Testville, ZZ1 1ZZ</p>
  <p class="metaInfo">Ref. No: 26/90001 <span>|</span> Status: <span class="value">Awaiting decision</span></p>
  <div class="summaryLinkTextClamp">Erection of   garden room &amp; shed</div>
</li>
<li class="searchresult">
  <a href="/online-applications/applicationDetails.do?keyVal=DEF456&amp;activeTab=summary">x</a>
  <p class="address">Field Barn, Nowhere Lane</p>
  <p class="metaInfo">Ref. No: SDNP/26/90002/FUL Status: <span class="value">Decided</span></p>
  <p class="description">Conversion of barn</p>
</li>
</ul>
<a class="next" href="/online-applications/pagedSearchResults.do?action=page&amp;searchCriteria.page=2">Next</a>
"""


def test_idox_parse_results():
    apps = idox.parse_results_page(RESULTS, BASE)
    assert len(apps) == 2
    a, b = apps
    assert a["app_no"] == "26/90001" and a["key_val"] == "ABC123"
    assert a["postcode"] == "ZZ1 1ZZ"
    assert a["proposal"] == "Erection of garden room & shed"
    assert a["url"].startswith("https://planning.example.gov.uk/online-applications/applicationDetails.do")
    assert a["easting"] is None
    assert b["app_no"] == "SDNP/26/90002/FUL" and b["status"] == "Decided" and "postcode" not in b


def test_idox_next_page():
    url = idox.next_page_url(RESULTS, BASE)
    assert url == "https://planning.example.gov.uk/online-applications/pagedSearchResults.do?action=page&searchCriteria.page=2"
    assert idox.next_page_url("<p>no more</p>", BASE) is None


def test_idox_weeks():
    form = '<option value="12 Jan 2026">x</option><option value="05 Jan 2026">y</option>'
    opts = idox.week_options(form)
    assert opts == ["12 Jan 2026", "05 Jan 2026"]
    assert idox.completed_weeks(opts, today=datetime(2026, 1, 14)) == ["05 Jan 2026"]
    assert idox.week_range("05 Jan 2026") == ("2026-01-05", "2026-01-11")


def test_idox_red_line_gml():
    gml = b"""<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">
      <wfs:member><gml:Polygon><gml:exterior><gml:LinearRing>
        <gml:posList>350210 490050 350310 490050 350310 490150 350210 490150 350210 490050</gml:posList>
      </gml:LinearRing></gml:exterior>
      <gml:interior><gml:LinearRing><gml:posList>1 1 2 2 3 3 1 1</gml:posList></gml:LinearRing></gml:interior>
      </gml:Polygon></wfs:member></wfs:FeatureCollection>"""
    rings = idox.parse_red_line_gml(gml)
    assert len(rings) == 1 and rings[0][0] == (350210.0, 490050.0)


def test_portal_root():
    assert idox.portal_root(BASE) == "https://planning.example.gov.uk"
