"""Which applications matter, and how close is too close.

Each proposal is read for what kind of development it is. That sets the buffer:
how near to ancient woodland it can be before someone should look at it.
Some application types are ignored outright (listed building consent, most
discharges of conditions, tree works for safety or conservation).

The buffer bands below are the defaults used for ancient woodland screening.
Change them in BUFFERS if your organisation uses different ones.
"""
from __future__ import annotations

import re

BUFFERS = {
    "intensive_livestock": 5000.0,   # ammonia reaches a long way
    "minerals": 100.0,
    "slurry": 50.0,
    "utilities": 50.0,
    "leisure": 50.0,
    "housing_large": 50.0,           # 100+ units
    "housing_medium": 30.0,          # 20-99 units
    "small": 15.0,                   # extensions, single houses, <20 units
    "tree_works": 15.0,
}

# Statuses that mean the decision is made and there is nothing left to comment on.
DECIDED_STATUS_WORDS = ("decided", "determined", "withdrawn", "refused", "granted", "approved")

RESIDENTIAL_CARAVAN_WORDS = ("traveller", "gypsy", "residential caravan", "residential static",
                             "residential mobile", "mobile home", "to residential")
HOLIDAY_WORDS = ("holiday", "caravan park", "campsite", "camping", "glamping", "touris")

HS_CONSERVATION_PURPOSE = (
    "deadwood", "fell dead", "ash dieback", "dieback", "diseased", "disease ",
    "for safety", "safety reasons", "safety hazard", "hazard tree", "dangerous tree",
    "dying tree", "structurally unsafe", "conservation purposes", "for conservation",
    "conservation reasons", "conservation management", "conservation grounds",
    "removal of non-native", "haloing",
)
AMENITY_PURPOSE = (
    "garden", " view", "light", "shading", "shade", "leaf fall", "tv signal", "amenity",
    "damage to", "obstruct", "blocking", "overhang",
)

_REFERENCE_MARKERS = (
    "planning permission", "permission ref", "approved under", "granted under",
    "pursuant to", "approval ref", "approval no", "consent ref", "application ref",
    "material start", "lawful development certificate",
)


def is_decided(status: str | None) -> bool:
    status = (status or "").lower()
    return any(w in status for w in DECIDED_STATUS_WORDS)


def strip_referenced_permissions(text: str) -> str:
    """Cut a proposal at the first mention of an earlier permission.

    "Certificate to confirm a material start on permission X (erection of a slurry
    store)" is not itself a slurry store. Only the heavy categories use this,
    because they are the ones a quoted scheme would wrongly inflate.
    """
    low = text.lower()
    cut = len(text)
    for marker in _REFERENCE_MARKERS:
        i = low.find(marker)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


def residential_caravan_buffer(prop_lower: str):
    """Caravans people live in are housing, sized by pitch count, not leisure."""
    if not any(k in prop_lower for k in RESIDENTIAL_CARAVAN_WORDS):
        return None
    if any(k in prop_lower for k in HOLIDAY_WORDS):
        return None
    m = re.search(r"(\d+)\s*(?:no\.?\s*)?(?:x\s*)?(?:[a-z'&]+\s+){0,4}?"
                  r"(?:pitch(?:es)?|caravans?|mobile homes?|plots?)\b", prop_lower)
    pitches = int(m.group(1)) if m else 1
    if pitches >= 100:
        return BUFFERS["housing_large"], f"Residential caravans / traveller pitches ({pitches}) / Large"
    if pitches >= 20:
        return BUFFERS["housing_medium"], f"Residential caravans / traveller pitches ({pitches}) / Medium"
    return BUFFERS["small"], f"Residential caravans / traveller pitches ({pitches}) / Small"


def _has(text, words):
    return any(w in text for w in words)


def classify_proposal(proposal: str, app_no: str = ""):
    """Return (buffer_m, classification), or (None, reason) if the application is ignored."""
    prop_lower = (proposal or "").lower()
    app_lower = (app_no or "").lower()
    core_lower = strip_referenced_permissions(proposal or "").lower()

    layout_words = ("layout", "position", "move", "tree", "fell")
    arb_words = ("arboricultural", "tree", "landscape", "woodland")

    # 1. Application-type suffixes
    if _has(app_lower, ("/lbc", "/doc", "/cond", "/var", "/voc", "/vcn")):
        if _has(app_lower, ("/var", "/voc", "/vcn")):
            if not _has(prop_lower, layout_words):
                return None, "Ignored - Variation of Conditions (no layout or felling change)"
        elif _has(app_lower, ("/doc", "/cond")):
            if not _has(prop_lower, arb_words):
                return None, "Ignored - Discharge of Conditions (non-arboricultural)"
        else:
            return None, f"Ignored - Application type ({app_no.split('/')[-1]})"

    # 2. Same tests on the proposal wording
    if _has(prop_lower, ("discharge of condition", "discharge of details")) and not _has(prop_lower, arb_words):
        return None, "Ignored - Discharge of Conditions (non-arboricultural)"
    if _has(prop_lower, ("variation of condition", "vary condition")) and not _has(prop_lower, layout_words):
        return None, "Ignored - Variation of Conditions (non-layout)"

    # 3. Tree works (TPO consent, conservation-area notice). Ignored only when the
    # stated purpose is safety or conservation and no amenity reason is given.
    is_tree_works = (
        _has(app_lower, ("/tpo", "/tca", "/tpa"))
        or re.search(r"\btpo\b", prop_lower) is not None
        or _has(prop_lower, ("tree preservation order", "tree works", "works to tree"))
    )
    if is_tree_works:
        if _has(prop_lower, HS_CONSERVATION_PURPOSE) and not _has(prop_lower, AMENITY_PURPOSE):
            return None, "Ignored - Tree works for health & safety / conservation"
        return BUFFERS["tree_works"], "Tree works (TPO/TCA) - manual arboricultural review"

    if _has(prop_lower, ("internal alteration", "internal changes", "loft conversion", "roof extension",
                         "replacement window", "advertisement", "signage")):
        if not _has(prop_lower, ("ground floor", "rear extension", "side extension", "footprint")):
            return None, "Ignored - Internal / vertical / advertisement changes"

    # 4. Heavy categories read the core proposal, not quoted earlier schemes
    if _has(core_lower, ("poultry", "intensive farming", "broiler", "chicken farm", "livestock building", "pig farm")):
        return BUFFERS["intensive_livestock"], "Intensive poultry/farming units"
    if _has(core_lower, ("slurry pit", "slurry lagoon", "slurry store", "earthworks")):
        return BUFFERS["slurry"], "Farm or other slurry pits (with earthworks)"
    if _has(core_lower, ("mineral", "quarry", "extraction", "opencast", "mining")):
        return BUFFERS["minerals"], "Mineral extraction"

    if _has(prop_lower, ("utility", "utilities", "substation", "wind turbine", "solar farm",
                         "waste water", "sewage", "telecommunications", "mast")):
        return BUFFERS["utilities"], "Utilities"

    residential = residential_caravan_buffer(prop_lower)
    if residential:
        return residential
    if _has(prop_lower, ("leisure", "caravan", "camping", "glamping", "sports", "golf", "recreation", "play area")):
        return BUFFERS["leisure"], "Leisure"

    # 5. Housing and holiday units, sized by count
    m = re.search(r"(\d+)\s*(?:dwellings|houses|residential units|plots|holiday|lodges|pods|cottages)", prop_lower)
    if m:
        n = int(m.group(1))
        if n >= 100:
            return BUFFERS["housing_large"], f"Housing development ({n} units) / Large"
        if n >= 20:
            return BUFFERS["housing_medium"], f"Housing development ({n} units) / Medium"
        return BUFFERS["small"], f"Housing/Holiday development ({n} units) / Small"
    if _has(prop_lower, ("major development", "major residential", "outline planning for residential")):
        return BUFFERS["housing_large"], "Housing development (Major/Large)"

    if _has(prop_lower, ("extension", "garage", "shed", "dwelling", "house", "annex", "barn conversion",
                         "bungalow", "porch", "carport", "patio", "decking")):
        return BUFFERS["small"], "Extensions / Individual house / Small development"

    return BUFFERS["small"], "Other development (Default)"
