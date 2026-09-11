"""
H. THE RENDERED PAGE. Everything above this file asks the server what it would
say. This file asks CHROMIUM what the user actually sees: cell text, chips, the
headline, the drawer, the keyboard, the layout at 400px and at 1440px.

Each test starts a real server (stubbed transport, pinned clock, fake key) and
drives the real page. It is slow (a browser per scenario, ~30-60s each) and
lives outside `testpaths` for that reason.

NO BYTE LEAVES THIS MACHINE: the server process refuses every outbound
connect(), and Chromium is launched with no proxy, with every name but
127.0.0.1 unresolvable, and with a route interceptor that aborts and RECORDS
anything that is not the probe server (the page's Google Fonts link is the only
entry, and it never loads).

RED = a defect that exists. GREEN = the attack held up.
"""
import json
import re
import subprocess

import pytest

from browser import HERE, Server, drive, ui_browser

TRIP_B = {"kind": "trip", "trip": "trip_b_europe", "openAllLegs": True}
MARKERS = ["UNKNOWN", "NOT $0", "WITHHELD", "UNVERIFIED", "NOT LOOKED UP", "NOT RECORDED",
           "API FAILED", "NEVER PRICED", "NOT ADDED", "CONFIRM BEFORE TRUSTING"]


@pytest.fixture(scope="module")
def offline_dom():
    dom, _ = ui_browser("offline_b", name="h_offline", mode="offline", **TRIP_B)
    return dom


@pytest.fixture(scope="module")
def down_dom():
    dom, _ = ui_browser("down_b", name="h_down", mode="live", **TRIP_B)
    return dom


def flat(text):
    return re.sub(r"\s+", " ", text or "")


# ------------------------------------------------------- the recurring failure


def test_H1_no_cell_of_an_unknown_kind_shows_a_number_a_blank_or_a_dash(offline_dom, down_dom):
    bad = []
    for dom in (offline_dom, down_dom):
        for row in dom["rows"]:
            for name, cell in row["cells"].items():
                # `surch_source` carries the CLI's own word "unknown" for the
                # SOURCE of a surcharge, not a money figure (coder deviation 8).
                if cell["kind"] != "unknown" or name == "surch_source":
                    continue
                text = (cell["text"] or "").strip()
                if text != "UNKNOWN":
                    bad.append((dom["scenario"], row["id"], name, text))
    assert bad == [], bad


def test_H2_no_dollar_zero_appears_where_the_cli_has_an_unknown(offline_dom, down_dom):
    bad = []
    for dom in (offline_dom, down_dom):
        for row in dom["rows"]:
            for name, cell in row["cells"].items():
                if cell["kind"] in ("unknown", "none") and "$0.00" in (cell["text"] or ""):
                    bad.append((dom["scenario"], row["id"], name, cell["text"]))
    assert bad == [], bad


def test_H3_the_headline_value_never_appears_without_its_qualifier(offline_dom, down_dom):
    for dom in (offline_dom, down_dom):
        value = dom["headlineValue"]
        assert value, dom["scenario"]
        if "%" in value:
            assert re.search(r"\((live|badge|snapshot|none|badge_fallback[^)]*)", value), value


def test_H4_an_api_failed_leg_never_claims_anything_about_partners(down_dom):
    """F-1. The network was down: no leg may say 'not a partner', and every
    failed leg must carry API FAILED where the user can see it."""
    for row in down_dom["rows"]:
        prov = row["cells"]["provenance"]["text"]
        if "API FAILED" not in prov:
            continue
        assert "not a partner" not in row["cells"]["path"]["text"].lower(), row
        assert "NO LIVE" in row["cells"]["verdict"]["text"].upper(), row


def test_H5_every_marker_in_a_legs_cli_lines_also_reaches_its_drawer(offline_dom, down_dom):
    """The drawer is where the user reads a leg. A marker that is only in the
    collapsed transcript is a marker he will not see."""
    bad = []
    for dom in (offline_dom, down_dom):
        for leg_id, text in dom["drawers"].items():
            up = (text or "").upper()
            row = [r for r in dom["rows"] if r["id"] == leg_id][0]
            joined = " ".join(c["text"] for c in row["cells"].values()).upper()
            for marker in MARKERS:
                if marker in joined and marker not in up:
                    bad.append((dom["scenario"], leg_id, marker))
    assert bad == [], bad


def test_H6_a_never_priced_leg_says_so_in_the_table_and_the_drawer(  ):
    dom, _ = ui_browser("g7_offline", name="h_g7", kind="trip",
                        trip="g7_never_priced_couple", mode="offline", openAllLegs=True)
    for row in dom["rows"]:
        assert "never priced" in row["cells"]["path"]["text"].lower(), row
        assert "NEVER PRICED" in row["cells"]["verdict"]["text"].upper(), row
        assert "not a partner" not in json.dumps(row).lower()
    assert "0.00%" in dom["headlineValue"] and "(none)" in dom["headlineValue"]
    assert "No points price of any provenance entered this margin." in dom["headline"]


def test_H7_a_two_traveller_flight_leg_is_withheld_on_the_page(  ):
    """The couple rule, in the browser: exit 3, WITHHELD headline, and the leg
    itself says the party was not priced."""
    dom, _ = ui_browser("g7_live_ondate", name="h_couple", kind="trip",
                        trip="g7_never_priced_couple", mode="live", openAllLegs=True)
    assert "exit 3" in (dom["exitChip"] or "").lower(), dom["exitChip"]
    assert "WITHHELD" in (dom["headlineValue"] or "")
    assert "2+ travellers" in flat(dom["headline"]), flat(dom["headline"])[:400]
    l2 = [r for r in dom["rows"] if r["id"] == "L2"][0]
    assert "NOT PRICED" in l2["cells"]["verdict"]["text"].upper(), l2["cells"]["verdict"]


# ------------------------------------------------------------- hostile strings


def test_H8_seats_aero_strings_are_text_and_nothing_else(  ):
    """`<img onerror>`, `</pre><script>`, U+202E and a 5,000-character carrier
    name, through a LIVE trip run and a search."""
    reached = []
    for kind, name in (("trip", "h_hostile_trip"), ("search", "h_hostile_search")):
        spec = dict(TRIP_B, mode="live", name=name) if kind == "trip" else {"kind": "search",
                                                                            "name": name}
        dom, _ = ui_browser("hostile", **spec)
        assert dom["pwned"] is False, kind
        assert dom["errors"] == [], (kind, dom["errors"])
        # the page must not have grown a horizontal scrollbar from a long string
        assert dom["doc"]["sw"] <= dom["doc"]["cw"], (kind, dom["doc"])
        if "__pwned" in json.dumps(dom):
            reached.append(kind)
    assert reached, "the hostile string never reached the page in either view"


# ------------------------------------------------------------------- keyboard


def test_H9_tab_to_a_leg_row_and_enter_opens_its_drawer(  ):
    """Plan 4.7: rows are focusable, Enter opens, Esc closes."""
    srv = Server("offline_b")
    try:
        out = json.loads(subprocess.run(
            ["node", str(HERE / "keyboard.js"),
             json.dumps({"port": srv.port, "trip": "trip_b_europe", "mode": "offline"})],
            capture_output=True, text=True, timeout=300).stdout)
    finally:
        srv.close()
    assert out.get("focused", {}).get("testid", "").startswith("leg-row-"), out
    assert out["ring"]["outlineWidth"] == "2px", out["ring"]
    assert out["afterEnter"]["drawerHidden"] is False, (
        "Enter on a focused leg row does not open the drawer: it opens and is "
        "closed again by the same keypress, because focus is moved onto the "
        "drawer's close button inside the keydown handler")


# --------------------------------------------------------------------- layout


def test_H10_the_verdict_column_is_visible_without_scrolling(offline_dom):
    """The verdict is the answer. With the drawer docked (>= 1180px) the legs
    table is 1178px wide and needs ~1620px, so the Verdict column is off-screen
    until the user scrolls the table sideways."""
    box = offline_dom["legsTableScroll"]
    assert box["verdict"]["visible"], box


def test_H11_at_400px_the_page_itself_never_scrolls_sideways(  ):
    for scenario, spec in (("offline_b", dict(TRIP_B, mode="offline", name="h_phone_trip")),
                           ("search_ok", {"kind": "search", "name": "h_phone_search"})):
        dom, _ = ui_browser(scenario, width=400, height=860, **spec)
        assert dom["doc"]["sw"] <= dom["doc"]["cw"], (scenario, dom["doc"])
        assert dom["doc"]["bodySW"] <= 400, (scenario, dom["doc"])


def test_H12_the_drawer_is_docked_above_1180_and_a_sheet_below(  ):
    wide, _ = ui_browser("offline_b", width=1440, name="h_wide", **dict(TRIP_B, mode="offline"))
    narrow, _ = ui_browser("offline_b", width=1100, name="h_narrow",
                           **dict(TRIP_B, mode="offline"))
    assert wide["drawerBox"]["position"] == "sticky", wide["drawerBox"]
    assert narrow["drawerBox"]["position"] == "fixed", narrow["drawerBox"]


# ------------------------------------------------------------------- traffic


def test_H13_the_page_asks_for_nothing_but_this_server_and_google_fonts(offline_dom):
    off = [u for u in offline_dom["offsite"] if "fonts.googleapis.com" not in u]
    assert off == [], off


# --------------------------------------------------------------- another site


def test_H14_a_hostile_page_on_another_origin_can_neither_read_nor_spend():
    """The stated threat model: another website open in the same browser. It
    gets a real page on another loopback port and tries fetch, a no-preflight
    POST, a form POST, an <img> and a <script> against the app."""
    from browser import evil_site

    srv = Server("offline_b")
    token = srv.info["token"]
    try:
        with evil_site() as evil_port:
            out = json.loads(subprocess.run(
                ["node", str(HERE / "csrf.js"),
                 json.dumps({"port": srv.port, "evil": evil_port})],
                capture_output=True, text=True, timeout=300).stdout)
        facts = srv.attempts()
    finally:
        srv.close()
    assert not out.get("fatal"), out
    for label, verdict, detail in out["attempts"]:
        if label.startswith("read the app page"):
            assert verdict == "blocked", (label, detail)
        if label.startswith("GET /api/state"):
            # It may see a response object, but never a 200 and never data.
            assert verdict == "blocked" or detail.startswith("403"), (label, detail)
        if label.startswith("img"):
            assert verdict == "blocked", (label, detail)
        if label.startswith("script"):
            # app.js is not a secret and carries no token, so a cross-origin
            # <script> include of it gains the attacker nothing - as long as it
            # really carries nothing. Checked below against the live token.
            pass
    # Nothing it did may have reached Seats.aero.
    assert facts["calls"] == [] or facts["calls"] == 0, facts
    assert facts["attempts"] == []
    # The app's own page still holds the token; the attacker never read it.
    assert out["tokenInPage"], out
    assert "po-token" not in (out.get("afterFormPost") or "")
    assert token not in (out.get("evilPageText") or ""), "the token reached the hostile page"
    assert token not in (out.get("afterFormPost") or "")
