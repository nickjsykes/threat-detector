from pathlib import Path

import pytest

from threatdetector.cli import main, read_csv
from threatdetector.gis import GISLookupError, LocalGIS
from threatdetector.screen import ScreeningFailed, screen_applications

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def gis():
    return LocalGIS(FIX / "woodland.geojson", FIX / "trees.geojson")


@pytest.fixture
def result(gis):
    return screen_applications(read_csv(FIX / "applications.csv"), gis, workers=2)


def outcome(result, app_no):
    for key, apps in result.items():
        for app in apps:
            if app["app_no"] == app_no:
                return key, app
    raise KeyError(app_no)


def test_extension_next_to_wood_is_flagged(result):
    key, app = outcome(result, "2026/9001/FPA")
    assert key == "flagged"
    assert app["matched_woodlands"][0]["name"] == "Example Wood"
    assert app["matched_woodlands"][0]["exact_distance_m"] == 10.0
    assert "Example Wood" in app["threat_description"]


def test_just_outside_buffer_is_a_near_miss(result):
    key, app = outcome(result, "2026/9002/FPA")   # 25 dwellings, 30 m buffer, 35 m away
    assert key == "near_miss"
    assert "35.0m" in app["near_miss_note"]


def test_well_outside_is_clear(result):
    assert outcome(result, "2026/9003/FPA")[0] == "clear"   # garage, 100 m away
    assert outcome(result, "2026/9009/FPA")[0] == "clear"   # nothing within 500 m


def test_poultry_uses_long_buffer(result):
    key, app = outcome(result, "2026/9005/FPA")   # 2.8 km from Example Wood
    assert key == "flagged"
    assert app["required_buffer"] == 5000.0
    assert "ammonia" in app["threat_description"]


def test_notable_tree_alone_never_flags_but_veteran_does(result):
    key, app = outcome(result, "2026/9006/FPA")   # 10 m from a veteran oak
    assert key == "flagged"
    assert "veteran" in app["threat_description"].lower()


def test_ignored_and_unplaced(result):
    assert outcome(result, "2026/9004/LBC")[0] == "ignored"
    key, app = outcome(result, "2026/9008/FPA")
    assert key == "ignored" and "already decided" in app["classification"]
    assert outcome(result, "2026/9007/FPA")[0] == "unplaced"


def test_red_line_measured_from_boundary(gis):
    # Centre is 60 m from the wood but the red line reaches to 10 m.
    # No postcode or grid point at all: the red line alone places it.
    app = {"app_no": "2026/9100/FPA", "proposal": "Erection of a dwelling", "easting": None, "northing": None,
           "site_rings": [[(350210, 490050), (350310, 490050), (350310, 490150), (350210, 490150)]],
           "location_source": "red line"}
    result = screen_applications([app], gis)
    assert result["flagged"] and result["flagged"][0]["matched_woodlands"][0]["exact_distance_m"] == 10.0
    assert (app["easting"], app["northing"]) == (350260, 490100)


def test_gis_failure_is_never_a_clean_week():
    class Broken:
        def woodland_near(self, *a, **k):
            raise GISLookupError("HTTP 403")

        def trees_near(self, *a, **k):
            return []

    app = {"app_no": "X", "proposal": "Erection of a dwelling", "easting": 1, "northing": 1}
    with pytest.raises(ScreeningFailed):
        screen_applications([app], Broken())


def test_cli_screen_writes_report(tmp_path):
    code = main(["screen", str(FIX / "applications.csv"),
                 "--woodland", str(FIX / "woodland.geojson"), "--trees", str(FIX / "trees.geojson"),
                 "--out", str(tmp_path), "--name", "sample", "--label", "2026-01-05"])
    assert code == 0
    report = (tmp_path / "sample" / "2026-01-05" / "report.md").read_text(encoding="utf-8")
    assert "**3 flagged**" in report
    assert "2026/9001/FPA" in report
    assert (tmp_path / "sample" / "2026-01-05" / "screening.json").exists()


def test_cli_run_skips_a_week_already_done(tmp_path, monkeypatch):
    from threatdetector.sources import wandf

    apps = read_csv(FIX / "applications.csv")
    monkeypatch.setattr(wandf, "fetch_week", lambda session, url=None: ("2026-01-11", "2026-01-05 to 2026-01-11",
                                                                      [dict(a) for a in apps], "synthetic"))
    monkeypatch.setattr(wandf, "place_sites", lambda *a, **k: None)
    args = ["run", "--council", "wandf", "--out", str(tmp_path),
            "--woodland", str(FIX / "woodland.geojson"), "--trees", str(FIX / "trees.geojson")]
    assert main(args) == 0
    assert (tmp_path / "wandf" / "2026-01-11" / "report.md").exists()
    assert main(args) == 2            # same week again: nothing new
    assert main(args + ["--force"]) == 0
