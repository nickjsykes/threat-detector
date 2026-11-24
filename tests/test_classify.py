import pytest

from threatdetector.classify import BUFFERS, classify_proposal, is_decided


@pytest.mark.parametrize("proposal, app_no, buffer_m, contains", [
    ("Erection of single storey rear extension", "2026/0001/FPA", 15.0, "Extensions"),
    ("Erection of 25 dwellings with access", "2026/0002/FPA", 30.0, "Medium"),
    ("Outline application for 150 dwellings", "2026/0003/OUT", 50.0, "Large"),
    ("Erection of broiler unit", "2026/0004/FPA", 5000.0, "poultry"),
    ("Extension to existing quarry", "2026/0005/FPA", 100.0, "Mineral"),
    ("Installation of telecommunications mast", "2026/0006/FPA", 50.0, "Utilities"),
    ("Change of use of land for touring caravans", "2026/0007/FPA", 50.0, "Leisure"),
    ("Use of land for 2 gypsy and traveller pitches", "2026/0008/FPA", 15.0, "traveller pitches (2)"),
    ("Erection of 6 holiday lodges", "2026/0009/FPA", 15.0, "Small"),
    ("Fell two oaks to improve light to garden", "2026/0010/TPO", 15.0, "Tree works"),
])
def test_screened_types(proposal, app_no, buffer_m, contains):
    got_buffer, classification = classify_proposal(proposal, app_no)
    assert got_buffer == buffer_m
    assert contains.lower() in classification.lower()


@pytest.mark.parametrize("proposal, app_no", [
    ("Replacement windows", "2026/0100/LBC"),
    ("Discharge of condition 3 (materials)", "2026/0101/DOC"),
    ("Variation of condition 2 (opening hours)", "2026/0102/VAR"),
    ("Fell ash with ash dieback for safety reasons", "2026/0103/TPO"),
    ("Internal alterations and loft conversion", "2026/0104/FPA"),
])
def test_ignored_types(proposal, app_no):
    buffer_m, reason = classify_proposal(proposal, app_no)
    assert buffer_m is None
    assert reason.startswith("Ignored")


def test_discharge_kept_when_arboricultural():
    buffer_m, _ = classify_proposal("Discharge of condition 5 (arboricultural method statement)", "2026/0200/DOC")
    assert buffer_m == BUFFERS["small"]


def test_quoted_permission_does_not_inflate_buffer():
    proposal = ("Lawful development certificate to confirm a material start on planning permission "
                "2020/0001/FPA (erection of slurry store)")
    buffer_m, classification = classify_proposal(proposal, "2026/0300/CLE")
    assert buffer_m == BUFFERS["small"]
    assert "slurry" not in classification.lower()


def test_tpo_word_not_matched_inside_other_words():
    # "outpost" contains "tpo" but is not tree works
    _, classification = classify_proposal("Erection of a garden outpost shed", "2026/0400/FPA")
    assert "tree works" not in classification.lower()


def test_decided():
    assert is_decided("Decided")
    assert is_decided("Application Withdrawn")
    assert not is_decided("Awaiting decision")
    assert not is_decided(None)
