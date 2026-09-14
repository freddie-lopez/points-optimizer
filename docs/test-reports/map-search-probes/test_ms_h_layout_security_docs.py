"""
H. 400/899/900/1179/1180/1440 non-regression, the results pane with a
drawer, CSP and static serving, docs against the real CLI and DOM.
"""
import http.client
import json
import re
import subprocess
import sys

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from tests import _cli_golden as g  # noqa: E402

BASE = "56742af"


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


@pytest.mark.parametrize("w", [400, 720, 899])
def test_H1_below_900_the_map_is_absent_and_the_form_is_the_old_strip(browser, w):
    with hub_server() as srv:
        with browser.page(srv.port, width=w) as pg:
            C.open_search(pg, wait_map=False)
            pg.wait_for_timeout(300)
            mp = C.box(pg, C.q("map-pane"))
            assert mp["display"] == "none"
            assert C.doc_widths(pg)["bodySW"] <= w
            grid = pg.evaluate("() => getComputedStyle(document.querySelector('.search-grid')).gridTemplateColumns")
            assert len(grid.split()) == 1, grid
            assert C.text(pg, C.q("search-state")).startswith("Search a route.")
            C.run_search_typed(pg)
            C.finish_search(pg)
            assert C.text(pg, C.q("search-table")) is not None
            assert C.doc_widths(pg)["bodySW"] <= w
            assert pg.evaluate("() => document.getElementById('map-pane').hidden")
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            d = C.box(pg, C.q("drawer-search"))
            assert d["position"] == "fixed" and d["w"] <= w
            assert C.doc_widths(pg)["bodySW"] <= w
            pg.screenshot(path=str(C.SHOTS / f"H1-{w}.png"))
            assert pg.facts["errors"] == []


@pytest.mark.parametrize("w", [900, 1179])
def test_H2_between_900_and_1179_two_columns_and_a_fixed_drawer(browser, w):
    with hub_server() as srv:
        with browser.page(srv.port, width=w) as pg:
            C.open_search(pg)
            grid = pg.evaluate("() => getComputedStyle(document.querySelector('.search-grid')).gridTemplateColumns")
            assert grid.startswith("360px ") and len(grid.split()) == 2, grid
            assert C.box(pg, C.q("map-pane"))["display"] != "none"
            C.run_search_typed(pg)
            C.finish_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            assert C.box(pg, C.q("drawer-search"))["position"] == "fixed"
            assert C.doc_widths(pg)["bodySW"] <= w


@pytest.mark.parametrize("w", [1180, 1300, 1440])
def test_H3_the_results_pane_with_a_docked_drawer_is_not_narrower_than_the_base(browser, w):
    """At 56742af the results had (w - 32 - 20 - 440)px beside the drawer;
    now a 360px column is taken off that. At 1180 the table gets 308px."""
    with hub_server() as srv:
        with browser.page(srv.port, width=w) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            C.finish_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            assert C.box(pg, C.q("drawer-search"))["position"] == "sticky"
            res = C.box(pg, C.q("search-result"))
            pg.screenshot(path=str(C.SHOTS / f"H3-{w}-results-drawer.png"))
            table = C.box(pg, C.q("search-table"))
            assert res["w"] >= 440, f"the results pane is {res['w']}px beside a 440px drawer at {w}px"
            assert table["right"] <= res["right"] + 1 or pg.evaluate(
                "() => getComputedStyle(document.querySelector('[data-testid=search-table]').closest('.tablewrap, .result, div')).overflowX") in ("auto", "scroll")


def test_H4_the_map_is_one_call_the_same_headers_and_no_traversal(browser):
    with hub_server() as srv:
        for p, ctype in (("/static/hubs.json", "application/json"), ("/static/land.json", "application/json"),
                         ("/static/map.js", "text/javascript")):
            s, h, b = get(srv.port, p)
            assert s == 200 and h["Content-Type"].startswith(ctype)
            assert "default-src 'none'" in h["Content-Security-Policy"]
            assert h["Cross-Origin-Resource-Policy"] == "same-origin"
            assert h["X-Content-Type-Options"] == "nosniff"
            assert h["Cache-Control"] == "no-store"
            assert srv.info["token"].encode() not in b
        for p in ("/static/../data/hubs.json", "/static/hubs.json/", "/static/HUBS.JSON", "/data/hubs.json",
                  "/static/land.json/../app.js", "/static/./hubs.json", "/static/hubs.json%00",
                  "/static/airportsdata_iata.csv", "/data/airportsdata_iata.csv", "/static/map.js.map"):
            s, h, b = get(srv.port, p)
            assert s == 404, p
        s, h, b = get(srv.port, "/")
        csp = h["Content-Security-Policy"]
        assert csp == ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                       "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; "
                       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        assert re.findall(r"<script[^>]*>", b.decode()) == ['<script src="/static/map.js" defer>',
                                                             '<script src="/static/app.js" defer>']
        assert b" style=" not in b


def test_H5_a_key_planted_in_the_hub_file_is_refused_by_egress_and_logged(browser):
    doc = C.synthetic()
    doc["_meta"]["x"] = g.FAKE_KEY
    with hub_server(doc=doc) as srv:
        s, h, b = get(srv.port, "/static/hubs.json")
        assert s == 500 and g.FAKE_KEY.encode() not in b
        assert b"key material" in b
        logs = srv.cmd("attempts")["logs"]
        assert any("REFUSED" in l for l in logs)
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.status(pg) == ("AIRPORT DATA UNREADABLE - /static/hubs.json could not be read (HTTP 500). "
                                    "The map plots nothing.")
            assert g.FAKE_KEY not in pg.evaluate("() => document.documentElement.outerHTML")


def test_H6_map_js_has_no_html_string_api_and_no_inline_style_from_the_mockup():
    js = (ROOT / "src" / "ui" / "static" / "map.js").read_text(encoding="utf-8")
    for needle in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "javascript:",
                   "new Function", "setTimeout(\"", "setTimeout('"):
        assert needle not in js, needle
    assert not re.search(r"\|\|\s*0(?![.\d])|\?\?\s*0(?![.\d])", js)
    # the popover's position is the only style written, and it is a number + px
    styles = re.findall(r"\.style\.(\w+)\s*=\s*([^;]+);", js)
    assert {s[0] for s in styles} <= {"left", "top"}, styles
    assert "cssText" not in js and "setAttribute(\"style\"" not in js
    # one global
    assert js.count("window.POMap =") == 1
    assert not re.search(r"window\.(?!POMap|addEventListener|requestAnimationFrame|setTimeout)\w+\s*=", js)


def test_H7_the_restyle_tokens_are_unchanged_outside_the_map():
    out = subprocess.run(["git", "diff", f"{BASE}..HEAD", "--", "src/ui/static/app.css"],
                         cwd=str(ROOT), capture_output=True, text=True).stdout
    removed = [l for l in out.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert all(".search-grid" in l or "search-grid" in l for l in removed), removed
    assert not re.search(r"^\+\s*:root", out, re.M)
    assert not re.search(r"^\+.*--accent2\s*:", out, re.M)


def test_H8_the_readme_and_ui_md_match_the_real_cli_and_the_dom(browser):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ui = (ROOT / "docs" / "plans" / "ui.md").read_text(encoding="utf-8")
    from src import map_tools
    help_text = map_tools.build_parser().format_help()
    for cmd in ("capture-hubs", "mark-searchable"):
        assert cmd in help_text and cmd in readme
    assert "capture-hubs" in ui
    assert ("This will make at most N Seats.aero API call(s): one GET /partnerapi/routes per source "
            "(N sources). This process has spent S of 1,000; Seats.aero also counts your other runs today, "
            "which this tool cannot see.") in readme.replace("\n", " ")
    assert "28" in readme.split("### map_tools")[1][:400]
    from src.seats_client import SEATS_AERO_SOURCES
    assert len(SEATS_AERO_SOURCES) == 28
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pane = pg.evaluate("() => document.getElementById('map-pane').innerText")
            for s in ("Drag to pan · scroll to zoom", "several airports - click to list", "airport Seats.aero tracks"):
                assert s in ui and s in pane
            assert "Where are you flying?" in ui and "Type an airport or city, or pick it on the map." in ui
            for tid in ("map-pane", "map-svg", "map-land", "map-status", "map-provenance", "map-zoom-in",
                        "map-zoom-out", "map-reset", "map-cluster-list", "search-pane-toggle", "search-result",
                        "search-suggest-from", "search-suggest-to", "map-route", "map-hub-", "map-cluster-",
                        "map-pick-", "suggest-"):
                assert f"`{tid}" in ui or f"`-to`" in ui, tid


def test_H9_role_img_hides_the_marker_buttons_from_assistive_tech(browser):
    """Plan §2.18 asks for role=img on the SVG AND real buttons inside it;
    an img role makes its subtree presentational. Chromium's accessibility
    tree is the judge."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            snap = pg.accessibility.snapshot(root=pg.query_selector(C.q("map-svg")))
            found = json.dumps(snap or {})
            assert "SYD" in found, "the SYD marker button is not in the accessibility tree under role=img"
