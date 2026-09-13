"""
A. BRIEF §2 AFTER THE RESTYLE: every marker the CLI prints is in the rendered
DOM (not merely in the transcript fold), and every marker keeps its FORM as
well as its colour - dashed border, hatch, thick left edge, small caps, fill.
Computed styles, not stylesheet text.

Scenarios: Trip B offline, Trip C offline, a LIVE run against the stub, a
REPLAY, the API down, the never-priced couple trip, the overdrawn wallet
(exit 4), no wallet, a wallet file that does not parse, no key, the Search
view (ok / API error / no awards).
"""
import re

import pytest

from conftest import drive

TRIP_SCENARIOS = ["offline_b", "offline_c", "live_b", "replay_b", "down_b", "g7_offline",
                  "exit4", "no_wallet", "wallet_error", "no_key"]
SEARCH = ["search_ok", "search_api_error", "search_no_awards"]
ALL = TRIP_SCENARIOS + SEARCH

# The brief's markers. `badge` / `snapshot` / `live` are qualifiers and are
# checked with their number (A3); `exit N` is checked by the chip (A4).
MARKERS = ["UNKNOWN", "NOT $0", "WITHHELD", "UNVERIFIED", "NOT LOOKED UP", "NOT RECORDED",
           "NEVER PRICED", "API FAILED", "UNREADABLE", "CONFIRM BEFORE TRUSTING", "REPARSED",
           "SURCHARGE unknown", "TAXES unknown", "APD unknown", "WALLET ERROR", "NOTHING SCORED",
           "NOT EXECUTABLE", "NOT FUNDABLE", "NOT A PARTNER", "NOT ADDED"]


def flat(s):
    return re.sub(r"\s+", " ", s or "")


def ci(s):
    return flat(s).upper()


@pytest.fixture(scope="module", params=ALL)
def scen(request, browser):
    return request.param, drive(browser, request.param)


# ------------------------------------------------------------- A1 markers


def test_A1_every_marker_the_transcript_carries_is_in_the_rendered_dom(scen):
    name, d = scen
    tr = d["transcript"]
    if tr is None:
        pytest.skip(f"{name}: no transcript on this page (refusal/banner scenario)")
    # Case-sensitive: the markers are the CLI's capitals ("UNKNOWN"), not the
    # word "unknown" inside a sentence.
    page = flat(d["text"])
    # A coverage count of zero ("NOT RECORDED 0", "UNREADABLE: 0") is a tally,
    # not a marker on a leg; the page carries tallies its own way (pre-restyle).
    carried = re.sub(r"(NOT LOOKED UP|NOT RECORDED|UNREADABLE|NOT KNOWN):? 0\b", "", flat(tr))
    missing = [m for m in MARKERS if m in carried and m not in page]
    assert missing == [], f"{name}: in the transcript but not in the page: {missing}\n{page[:3000]}"


def test_A1b_no_page_error_and_nothing_left_the_machine_but_the_font_link(scen):
    name, d = scen
    assert d["facts"]["errors"] == [], (name, d["facts"]["errors"])
    off = [u for u in d["facts"]["offsite"] if not u.startswith("https://fonts.googleapis.com/")]
    assert off == [], (name, off)
    assert d["pwned"] is False


# ------------------------------------------------------- A2 chip FORMS


def chips_of(d):
    out = list(d["chips"])
    dc = d.get("drawer_chips") or {}
    if isinstance(dc, dict):
        for v in dc.values():
            out.extend(v)
    else:
        out.extend(dc)
    return out


def test_A2a_every_UNKNOWN_chip_has_a_dashed_border_and_the_literal_word(scen):
    name, d = scen
    bad = []
    for c in chips_of(d):
        if "chip-unknown" not in c["cls"].split():
            continue
        if c["border-top-style"] != "dashed" or c["border-left-style"] != "dashed":
            bad.append(("border", c["cls"], c["border-top-style"], c["border-left-style"]))
        if flat(c["text"]).strip() != "UNKNOWN":
            bad.append(("text", c["cls"], c["text"]))
        if c["display"] == "none":
            bad.append(("hidden", c["cls"]))
    assert bad == [], (name, bad)


def test_A2b_every_WITHHELD_form_is_hatched_with_a_repeating_gradient(scen):
    name, d = scen
    bad = []
    for c in chips_of(d):
        if "chip-withheld" not in c["cls"].split():
            continue
        if "repeating-linear-gradient" not in c["background-image"]:
            bad.append((c["cls"], c["text"], c["background-image"]))
        if c["border-top-style"] != "solid":
            bad.append(("border", c["cls"], c["border-top-style"]))
    assert bad == [], (name, bad)


def test_A2c_every_qualified_CASH_chip_has_a_3px_left_edge(scen):
    name, d = scen
    bad = []
    for c in chips_of(d):
        if "chip-cashq" not in c["cls"].split():
            continue
        if c["border-left-width"] != "3px":
            bad.append((c["cls"], c["text"], c["border-left-width"]))
        # ...and the edge is a different colour from the other edges (a form, not a wider border)
        if c["border-left-color"] == c["border-top-color"]:
            bad.append(("edge colour = border colour", c["cls"], c["text"]))
    assert bad == [], (name, bad)


def test_A2d_every_UNVERIFIED_tag_is_small_caps(scen):
    name, d = scen
    bad = []
    seen = 0
    for c in chips_of(d):
        if "tag-unv" not in c["cls"].split():
            continue
        seen += 1
        if c["font-variant-caps"] != "all-small-caps":
            bad.append((c["cls"], c["text"], c["font-variant-caps"]))
    assert bad == [], (name, bad)
    if "UNVERIFIED" in flat(d["text"]):
        # the page prints the word somewhere; is it ever the small-caps form?
        # (informational - the tag is drawn for fixture points prices, the word
        # for parser notes; both are the marker)
        pass


def test_A2e_every_POINTS_chip_is_filled(scen):
    name, d = scen
    bad = []
    for c in chips_of(d):
        if "chip-points" not in c["cls"].split():
            continue
        if c["background-color"] in ("rgba(0, 0, 0, 0)", "transparent", ""):
            bad.append((c["cls"], c["text"], c["background-color"]))
    assert bad == [], (name, bad)


def test_A2f_no_chip_is_uppercase_only_by_css_when_its_word_is_the_marker(scen):
    """UNKNOWN must be the literal word in the DOM, not 'unknown' uppercased by
    text-transform: a copy/paste or a screen reader gets the DOM text."""
    name, d = scen
    bad = [c for c in chips_of(d) if "chip-unknown" in c["cls"].split() and c["text"] != "UNKNOWN"]
    assert bad == [], (name, bad)


# ------------------------------------------------- A3 headline qualifier


def test_A3_the_headline_percentage_never_travels_without_its_qualifier(scen):
    name, d = scen
    v = d["headlineValue"]
    if v is None:
        pytest.skip(f"{name}: no headline on this page")
    if "%" in v:
        assert re.search(r"\((live|badge|snapshot|none|badge_fallback)", v), (name, v)
    else:
        assert "WITHHELD" in v or "UNKNOWN" in v, (name, v)


def test_A3b_the_headline_value_and_its_qualifier_are_one_element(scen):
    name, d = scen
    hv = [c for c in d["chips"] if "hl-value" in c["cls"].split()]
    if not hv:
        pytest.skip(f"{name}: no headline")
    t = flat(hv[0]["text"])
    if "%" in t:
        assert "(" in t and ")" in t, (name, t)


# ---------------------------------------------- A4 cells of an unknown kind


def test_A4_no_unknown_cell_shows_a_number_a_blank_a_dash_or_a_zero(scen):
    name, d = scen
    bad = []
    for c in d["cells"]:
        if c["kind"] != "unknown":
            continue
        t = flat(c["text"]).strip()
        tid = c["testid"] or ""
        if tid.endswith("-surch_source"):
            continue  # the CLI's own word for the SOURCE of a surcharge (round-1 decision)
        if t in ("", "-", "—", "–", "$0", "$0.00", "0") or re.fullmatch(r"[\d,.$%\s]+", t):
            bad.append((tid, t))
        if not c["hasUnknownChip"]:
            bad.append(("no dashed chip", tid, t))
    assert bad == [], (name, bad)


def test_A5_the_exit_chip_is_in_the_dom_with_its_label(scen):
    name, d = scen
    if d["transcript"] is None:
        pytest.skip(f"{name}: no run")
    assert d["exitChip"] and re.match(r"exit \d · ", d["exitChip"], re.I), (name, d["exitChip"])


def test_A6_the_exit_chip_for_a_withheld_or_refused_run_keeps_its_form(scen):
    name, d = scen
    ec = [c for c in d["chips"] if c["testid"] == "exit-chip"]
    if not ec:
        pytest.skip(f"{name}: no exit chip")
    c = ec[0]
    m = re.match(r"exit (\d)", c["text"])
    code = int(m.group(1))
    if code in (3, 4):
        assert "repeating-linear-gradient" in c["background-image"], (name, c)
    if code in (1, 2):
        assert c["border-top-color"] != c["color"], (name, c)
        assert "chip-exitwarn" in c["cls"], (name, c)
