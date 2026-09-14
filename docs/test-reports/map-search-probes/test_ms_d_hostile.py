"""
D. Hostile strings in the hub file: names and cities that are HTML, close
the SVG, flip the reading direction, are 5,000 characters long or emoji,
plus a `_meta.captured_at` that is a script tag. Everything must land as
text, the layout must hold, the popover must scroll, the page must not run
it.
"""
import sys

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))

XSS = '<img src=x onerror="window.__pwned=1"></svg><script>window.__pwned=1</script>'
RTL = "‮RIGHT-TO-LEFT‬"
LONG = "N" * 5000


def hostile_doc():
    doc = C.synthetic()
    by = {h["iata"]: h for h in doc["hubs"]}
    by["SFO"]["name"] = XSS + RTL
    by["SFO"]["city"] = LONG
    by["SJC"]["city"] = None
    by["SJC"]["name"] = 42
    by["SAN"]["name"] = "🛫 emoji ✈️ 名前"
    by["SAN"]["country"] = "<b>x</b>"
    by["LHR"]["sources"] = ["<b>x</b>"]
    by["JFK"]["city"] = "\"'&"
    doc["_meta"]["captured_at"] = "<script>alert(1)</script>"
    return doc


def test_D1_nothing_runs_and_no_element_is_created(browser):
    with hub_server(doc=hostile_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert pg.facts["errors"] == []
            assert not pg.evaluate("() => !!window.__pwned")
            assert pg.evaluate("() => document.querySelectorAll('#map-pane img, #map-pane script, #map-pane b, #view-search img, #view-search b').length") == 0
            assert C.provenance(pg).startswith("12 airports Seats.aero tracked on <script>al")
            C.click_marker(pg, "map-cluster-SFO")
            pop = C.text(pg, C.q("map-cluster-list"))
            assert XSS in pop and RTL in pop
            assert pg.evaluate("() => document.querySelectorAll('#map-pane img, #map-pane script').length") == 0
            assert not pg.evaluate("() => !!window.__pwned")
            pg.keyboard.press("Escape")
            pg.fill(C.q("search-from"), "N")
            pg.wait_for_timeout(200)
            rows = pg.evaluate("() => Array.from(document.querySelectorAll('.srow .sm')).map(e => e.textContent)")
            assert any(XSS in r for r in rows)
            assert not pg.evaluate("() => !!window.__pwned")
            pg.keyboard.press("Escape")
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(200)
            aria = pg.get_attribute(C.q("map-hub-SFO"), "aria-label")
            assert aria.startswith("SFO " + XSS)
            assert pg.evaluate("() => document.querySelectorAll('img').length") == 0
            assert pg.facts["errors"] == []


def test_D2_a_5000_char_city_is_clipped_by_css_and_the_popover_scrolls(browser):
    with hub_server(doc=hostile_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-SFO")
            pop = C.box(pg, C.q("map-cluster-list"))
            pane = C.box(pg, C.q("map-pane"))
            assert C._r.inside(pop, pane, tol=1.0), (pop, pane)
            sc = pg.evaluate("""() => { const p = document.querySelector('[data-testid=map-cluster-list]');
                return {sh: p.scrollHeight, ch: p.clientHeight, ov: getComputedStyle(p).overflowY}; }""")
            assert sc["ov"] == "auto" and sc["sh"] >= sc["ch"]
            # the full text is kept (textContent) - CSS clips, JS never truncates
            assert pg.evaluate("() => document.querySelector('.map-pop .pt').textContent.length") > 5000
            assert pg.evaluate("() => document.querySelector('[data-testid=map-pick-SFO] .dim').textContent.length") > 5000
            assert C.doc_widths(pg)["bodySW"] <= 1440
            pg.screenshot(path=str(C.SHOTS / "D2-hostile-popover.png"))
            pg.keyboard.press("Escape")
            pg.fill(C.q("search-from"), "N")
            pg.wait_for_timeout(200)
            sug = C.box(pg, C.q("search-suggest-from"))
            col = C.box(pg, "#search-main")
            assert sug["right"] <= col["right"] + 1 and sug["x"] >= col["x"] - 1, (sug, col)
            assert C.doc_widths(pg)["bodySW"] <= 1440
            pg.screenshot(path=str(C.SHOTS / "D2-hostile-suggest.png"))


def test_D3_odd_types_become_text_and_the_title_rule_survives_a_null_city(browser):
    with hub_server(doc=hostile_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-SFO")
            rows = pg.evaluate("() => Array.from(document.querySelectorAll('.prow')).map(b => [b.getAttribute('data-testid'), b.querySelector('.sm, .pm').textContent, b.querySelector('.dim').textContent])")
            by = {r[0]: r for r in rows}
            assert by["map-pick-SJC"][1] == "SJC 42"
            assert by["map-pick-SJC"][2] == "US"
            assert by["map-pick-SAN"][1] == "SAN 🛫 emoji ✈️ 名前"
            assert by["map-pick-SAN"][2] == "San Diego, <b>x</b>"
            pg.keyboard.press("Escape")
            C.click_marker(pg, "map-cluster-JFK")
            assert C.text(pg, C.q("map-cluster-list")).startswith("3 airports near \"'&")
