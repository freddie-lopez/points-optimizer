"""
F. TRIP-LIST KINDS (to-do #4). A per-leg trip, a single-route search request
and a fixture that cannot load, side by side in a TMP fixtures dir: three
forms, captions byte-identical to the base commit, forms not colour-only,
aria-current still drives selection, hostile names still render as text and
the new row layout holds at 400.
"""
import io
import re

import pytest
from PIL import Image

from conftest import SHOTS, box, doc_widths, open_trip, probe_server, q, styles, text

ROW_PROPS = ["background-color", "border-left-width", "border-left-color", "border-top-width",
             "border-top-color", "border-top-style", "border-left-style", "border-radius", "color",
             "font-weight"]

# The captions as the base commit renders them (app.js at 386b2fc, verified by
# reading renderTripList there).
CAPTIONS = {"CANNOT LOAD", "NOT A PER-LEG TRIP", "NO POINTS PRICES — LIVE OR REPLAY ONLY",
            "2+ TRAVELLERS ON A FLIGHT — NOT SCORED ON POINTS"}


def rows(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('.trow')).map((b) => {
        const cs = getComputedStyle(b); const n = b.querySelector('.n'); const ncs = getComputedStyle(n);
        return { testid: b.getAttribute('data-testid'), cls: b.className,
          current: b.getAttribute('aria-current'),
          name: n.textContent, flags: Array.from(b.querySelectorAll('.flag')).map((f) => f.textContent),
          meta: Array.from(b.querySelectorAll('.m')).map((f) => f.textContent),
          bg: cs.backgroundColor, bl: cs.borderLeftWidth, blc: cs.borderLeftColor, bt: cs.borderTopWidth,
          btc: cs.borderTopColor, bls: cs.borderLeftStyle, nColor: ncs.color, nWeight: ncs.fontWeight,
          h: b.getBoundingClientRect().height, w: b.getBoundingClientRect().width }; })""")


@pytest.fixture(scope="module")
def listing(browser):
    with probe_server("broken_fixture") as srv:
        with browser.page(srv.port, width=1440) as pg:
            pg.click(q("tab-trips"))
            pg.wait_for_timeout(200)
            before = rows(pg)
            shots = {}
            for r in before:
                h = pg.query_selector(q(r["testid"]))
                shots[r["testid"]] = Image.open(io.BytesIO(h.screenshot(type="png"))).convert("L")
            # select each kind in turn
            selected = {}
            for tid in ("trip-row-broken_file", "trip-row-trip_001", "trip-row-trip_b_europe"):
                pg.click(q(tid))
                pg.wait_for_timeout(300)
                selected[tid] = [r for r in rows(pg) if r["testid"] == tid][0]
                selected[tid]["main"] = text(pg, "main")[:600]
            pg.screenshot(path=str(SHOTS / "f-list-1440.png"))
            yield {"rows": before, "shots": shots, "selected": selected}


def by_kind(listing):
    k = {}
    for r in listing["rows"]:
        for c in r["cls"].split():
            if c.startswith("trow-"):
                k.setdefault(c, []).append(r)
    return k


def test_F1_the_three_kinds_are_present_and_classed(listing):
    k = by_kind(listing)
    assert set(k) == {"trow-trip", "trow-search", "trow-broken"}, set(k)
    assert [r["testid"] for r in k["trow-broken"]] == ["trip-row-broken_file"]
    assert {r["testid"] for r in k["trow-search"]} == {"trip-row-trip_001", "trip-row-trip_002"}
    # every row has exactly one kind class
    for r in listing["rows"]:
        assert sum(1 for c in r["cls"].split() if c.startswith("trow-")) == 1, r["cls"]


def test_F2_captions_are_unchanged(listing):
    k = by_kind(listing)
    assert k["trow-broken"][0]["flags"] == ["CANNOT LOAD"]
    assert k["trow-broken"][0]["meta"] and "Expecting" in k["trow-broken"][0]["meta"][0] or \
        k["trow-broken"][0]["meta"], k["trow-broken"][0]
    for r in k["trow-search"]:
        assert r["flags"] == ["NOT A PER-LEG TRIP"], r
        assert r["meta"] == ["a single-route search request for " + ("SFO->LHR" if "001" in r["testid"] else "SFO->JFK")], r
    for r in k["trow-trip"]:
        assert r["flags"] == [] or set(r["flags"]) <= CAPTIONS, r
        assert re.fullmatch(r"\d+ legs · \d+ flights( · \d+ hotels?)?", r["meta"][0]), r


def test_F3_the_forms_differ_by_shape_not_only_colour(listing):
    k = by_kind(listing)
    t, s, b = k["trow-trip"][0], k["trow-search"][0], k["trow-broken"][0]
    # search: a 3px left rail, plain: 1px transparent, broken: 1px visible border + fill
    assert s["bl"] == "3px" and t["bl"] == "1px" and b["bl"] == "1px", (s["bl"], t["bl"], b["bl"])
    assert s["bls"] == "solid" and b["bls"] == "solid" and "dashed" not in (s["bls"], b["bls"], t["bls"])
    assert t["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), t["bg"]
    assert b["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), b["bg"]
    assert t["btc"] == "rgba(0, 0, 0, 0)" and b["btc"] != "rgba(0, 0, 0, 0)", (t["btc"], b["btc"])
    # search request: quiet name
    assert s["nWeight"] == "400" and t["nWeight"] == "600", (s["nWeight"], t["nWeight"])


def test_F4_grayscale_still_tells_the_three_apart(listing):
    def col(im, x):
        return sum(im.getpixel((x, y)) for y in range(im.height)) / im.height

    def fill(im):
        return im.getpixel((im.width // 2, im.height // 2 - 8))

    k = by_kind(listing)
    t = listing["shots"][k["trow-trip"][0]["testid"]]
    s = listing["shots"][k["trow-search"][0]["testid"]]
    b = listing["shots"][k["trow-broken"][0]["testid"]]
    # the search rail: the first columns are brighter than the plain row's
    assert col(s, 1) - col(t, 1) > 6, (col(s, 1), col(t, 1))
    # the broken fill: interior brighter than the plain row's interior
    assert fill(b) - fill(t) > 4, (fill(b), fill(t))
    # and the broken row differs from the search row other than by the rail
    assert abs(fill(b) - fill(s)) > 4


def test_F5_aria_current_drives_selection_for_every_kind(listing):
    sel = listing["selected"]
    for tid, r in sel.items():
        assert r["current"] == "true", (tid, r["current"])
        assert r["nColor"] == "rgb(125, 169, 255)", (tid, r["nColor"])
    assert sel["trip-row-broken_file"]["blc"] == "rgb(240, 184, 90)", sel["trip-row-broken_file"]
    assert sel["trip-row-trip_001"]["blc"] == "rgb(125, 169, 255)" and sel["trip-row-trip_001"]["bl"] == "3px"
    assert "CANNOT LOAD" in sel["trip-row-broken_file"]["main"]
    assert "NOT A PER-LEG TRIP" in sel["trip-row-trip_001"]["main"]


def test_F6_no_row_is_dashed(listing):
    for r in listing["rows"]:
        assert r["bls"] != "dashed" and r["btc"] != "dashed", r


@pytest.mark.parametrize("width", [1440, 400])
def test_F7_a_hostile_trip_name_renders_as_text_and_the_row_holds(browser, width):
    with probe_server("hostile_list") as srv:
        with browser.page(srv.port, width=width, height=800) as pg:
            pg.click(q("tab-trips"))
            pg.wait_for_timeout(300)
            rs = rows(pg)
            host = [r for r in rs if r["testid"] == "trip-row-hostile_name"]
            pwned = pg.evaluate("() => !!window.__pwned")
            imgs = pg.evaluate("() => document.querySelectorAll('[data-testid=\"trip-list\"] img, [data-testid=\"trip-list\"] script').length")
            d = doc_widths(pg)
            lst = box(pg, q("trip-list"))
            pg.click(q("trip-row-hostile_name"))
            pg.wait_for_selector(q("trip-detail"))
            d2 = doc_widths(pg)
            pg.screenshot(path=str(SHOTS / f"f7-hostile-{width}.png"), full_page=False)
    assert host, [r["testid"] for r in rs]
    assert pwned is False and imgs == 0
    assert host[0]["name"].startswith('<img src=x onerror=')
    assert "trow-trip" in host[0]["cls"]
    assert d["sw"] <= width and d2["sw"] <= width, (d, d2)
    # the 5,000-char name wraps inside its column rather than widening it
    assert host[0]["w"] <= (lst["w"] + 1), (host[0]["w"], lst["w"])
