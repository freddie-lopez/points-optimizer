"""
C. The data states the pane can be in - empty (shipped), 404, malformed,
wrong types, broken land - each degrades loudly, never plots garbage, never
breaks the typed search; and every plan §4.9 string is in the DOM byte for
byte.
"""
import json
import sys

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))

NO_DATA = ('NO AIRPORT DATA - run "python -m src.map_tools capture-hubs" on your Mac. '
           'The map plots nothing until then.')
UNREADABLE = "AIRPORT DATA UNREADABLE - /static/hubs.json could not be read ({reason}). The map plots nothing."


def test_C1_the_shipped_empty_file_renders_the_sentence_visibly_and_the_form_still_searches(browser):
    doc = json.loads((ROOT / "data" / "hubs.json").read_text(encoding="utf-8"))
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.status(pg) == NO_DATA
            b = C.box(pg, C.q("map-status"))
            assert b and b["w"] > 200 and 0 <= b["y"] < 1000, b
            assert C.markers(pg) == []
            assert pg.evaluate("() => document.querySelector('[data-testid=map-provenance]').hidden")
            pg.fill(C.q("search-from"), "san")
            pg.wait_for_timeout(200)
            assert C.text(pg, C.q("search-suggest-from")) is None
            C.run_search_typed(pg)
            assert "--origin SFO --destination MAD" in C.confirm_text(pg)
            pg.screenshot(path=str(C.SHOTS / "C1-empty.png"))
            assert pg.facts["errors"] == []


def test_C2_a_missing_hub_file_is_unreadable_with_http_404_and_the_form_still_searches(browser):
    with hub_server(missing=True) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.status(pg) == UNREADABLE.format(reason="HTTP 404")
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(150)
            assert C.status(pg) == UNREADABLE.format(reason="HTTP 404")
            C.run_search_typed(pg)
            assert "--origin SFO --destination MAD" in C.confirm_text(pg)
            assert pg.facts["errors"] == []


@pytest.mark.parametrize("label,text,reason", [
    ("truncated", '{"_meta": {}, "hubs": [{"iata": "SFO"', "SyntaxError"),
    ("empty", "", "SyntaxError"),
    ("hubs-object", '{"_meta": {}, "hubs": {"a": 1}}', "hubs is not a list"),
    ("top-array", "[]", "hubs is not a list"),
    ("string", '"x"', "hubs is not a list"),
    ("null", "null", "hubs is not a list"),
    ("nan", '{"_meta": {}, "hubs": [{"iata": "SFO", "lat": NaN}]}', "SyntaxError"),
])
def test_C3_a_broken_hub_file_is_named_never_an_empty_ocean(browser, label, text, reason):
    with hub_server(hubs_text=text) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.status(pg) == UNREADABLE.format(reason=reason)
            assert C.markers(pg) == []
            assert pg.facts["errors"] == []


def test_C4_a_hub_file_with_meta_but_bad_hub_types_counts_each_row_and_plots_none_of_them(browser):
    doc = C.synthetic(codes=["SFO", "MAD"])
    doc["hubs"] += [
        {"iata": "BAD", "name": "x", "city": "y", "country": "z", "lat": "37", "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "BAE", "name": "x", "city": "y", "country": "z", "lat": 95, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "BAF", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 0, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "BAG", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": [], "regions": [], "searchable": True},
        {"iata": "BAH", "name": "x", "city": "y", "country": "z", "lat": None, "lon": None, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "BAI", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": False},
        {"iata": "MAD", "name": "dup", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "SFOX", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "sfo", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        {"iata": "<b>", "name": "x", "city": "y", "country": "z", "lat": 1, "lon": 1, "routes": 5, "sources": ["a"], "regions": [], "searchable": True},
        "string hub", None, [1], 42,
    ]
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            ids = sorted(m["id"] for m in C.markers(pg))
            assert ids == ["map-hub-MAD", "map-hub-SFO"], ids
            prov = C.provenance(pg)
            assert prov.startswith("16 airports Seats.aero tracked on 2026-09-20 · 14 not plotted ("), prov
            # THE ARITHMETIC MUST ADD UP: the parenthesis names J and M only;
            # the 9 rows dropped for a bad code/routes/sources/duplicate are
            # inside K but named nowhere.
            import re
            m = re.search(r"(\d+) not plotted \((\d+) not in data/airports.csv, (\d+) without coordinates\)", prov)
            k, j, mm = int(m.group(1)), int(m.group(2)), int(m.group(3))
            assert k == j + mm, f"{k} not plotted but only {j} + {mm} are accounted for: {prov}"


def test_C5_a_land_file_that_parses_but_is_not_a_land_file_still_says_something(browser):
    with hub_server(land_text='{"w": 1}') as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg, wait_map=False)
            pg.wait_for_timeout(800)
            txt = pg.evaluate("() => document.getElementById('map-pane').innerText")
            assert txt.strip() != "", "a blank pane: no sentence at all (unhandled rejection)"
            assert "land.json: not a land file" not in pg.facts["errors"], pg.facts["errors"]


@pytest.mark.parametrize("land_text,reason", [('{"w":', "SyntaxError"), (None, "HTTP 404")])
def test_C6_a_land_failure_names_land_json_not_hubs_json(browser, land_text, reason):
    import os
    with hub_server(land_text=land_text if land_text is not None else "") as srv:
        if land_text is None:
            os.remove(str(srv.info["hubs_file"]).replace("hubs.json", "land.json"))
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            st = C.status(pg)
            assert reason in st
            # the sentence must not claim that hubs.json could not be read
            assert "/static/hubs.json could not be read" not in st, st


def test_C7_every_plan_string_is_in_the_dom_byte_for_byte(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pane = pg.evaluate("() => document.getElementById('map-pane').innerText")
            col = pg.evaluate("() => document.getElementById('search-main').innerText")
            assert C.status(pg) == "Click an airport to set From."
            for s in ("airport Seats.aero tracks", "several airports - click to list",
                      "Drag to pan · scroll to zoom"):
                assert s in pane, s
            assert "Where are you flying?" in col
            assert "Type an airport or city, or pick it on the map." in col
            assert C.provenance(pg) == ("12 airports Seats.aero tracked on 2026-09-20 · 2 not plotted "
                                        "(2 not in data/airports.csv, 0 without coordinates)")
            labels = pg.evaluate("""() => ({
                zi: document.querySelector('[data-testid=map-zoom-in]').getAttribute('aria-label'),
                zo: document.querySelector('[data-testid=map-zoom-out]').getAttribute('aria-label'),
                rs: document.querySelector('[data-testid=map-reset]').getAttribute('aria-label'),
                svg: document.querySelector('[data-testid=map-svg]').getAttribute('aria-label') })""")
            assert labels == {"zi": "Zoom in", "zo": "Zoom out", "rs": "Reset view",
                              "svg": "World map of airports Seats.aero tracks"}
            C.click_marker(pg, "map-hub-SYD")
            assert C.status(pg) == "Click an airport to set To."
            C.click_marker(pg, "map-hub-MAD")
            assert C.status(pg) == ("The line is your route, not availability. Award space appears "
                                    "only after the search runs.")
            pg.fill(C.q("search-to"), "SOF")
            pg.wait_for_timeout(150)
            assert C.status(pg) == "Click an airport to set To. · SOF is not on this map. It can still be searched."
            C.click_marker(pg, "map-cluster-JFK")
            pop = C.text(pg, C.q("map-cluster-list"))
            assert pop.startswith("3 airports near New York\n3 airports Seats.aero tracks · pick one"), pop
            assert pg.evaluate("() => document.querySelector('.map-pop .x').getAttribute('aria-label')") == "Close list"
            pg.keyboard.press("Escape")
            C.click_marker(pg, "map-cluster-LHR")
            pop = C.text(pg, C.q("map-cluster-list"))
            assert pop.startswith("London\n2 airports Seats.aero tracks · pick one"), pop
            pg.keyboard.press("Escape")
            pg.fill(C.q("search-from"), "s")
            pg.wait_for_timeout(150)
            assert pg.evaluate("() => document.querySelector('[data-testid=search-suggest-from]').getAttribute('aria-label')") == "Suggestions"


def test_C8_the_status_sentence_is_wrong_when_to_is_picked_over_a_non_hub_from(browser):
    """From holds 's' (a partial), To was just set by a click: the footer
    says 'Click an airport to set To.' although To is the field just set,
    and the next click replaces To again."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "s")
            pg.wait_for_timeout(120)
            C.click_marker(pg, "map-hub-SYD")
            assert C.values(pg) == {"from": "s", "to": "SYD"}
            assert C.status(pg) != "Click an airport to set To.", C.status(pg)


def test_C9_the_pane_after_a_run_does_not_say_award_space_appears_only_after_the_search_runs(browser):
    """After the search ran, 'Award space appears only after the search runs'
    is no longer true of the page; the plan pins the sentence - recorded, not
    a red on its own."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            C.finish_search(pg)
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            assert "Award space appears only after the search runs." in C.status(pg)


# ---------------------------------------------------------- fix round 4 (c68e3eb)


def test_C10_a_rejected_fetch_is_the_sentence_never_a_blank_pane(browser):
    """fetch() itself rejecting (network layer, not an HTTP status): for the
    hub file - string #2 with `fetch failed`; for the land file - #16."""
    with hub_server() as srv:
        for which, expect in (("hubs", UNREADABLE.format(reason="fetch failed")),
                              ("land", "AIRPORT DATA UNREADABLE - /static/land.json could not be read (fetch failed). The map plots nothing.")):
            with browser.page(srv.port) as pg:
                pg.route(f"**/static/{which}.json", lambda r: r.abort())
                C.open_search(pg)
                assert C.status(pg) == expect, (which, C.status(pg))
                assert C.markers(pg) == [] and pg.facts["errors"] == []
                C.run_search_typed(pg)
                assert "--origin SFO --destination MAD" in C.confirm_text(pg)


def test_C11_a_mount_that_throws_is_caught_loading_is_cleared_and_the_sentence_shows(browser):
    """ensureMap's .catch (c5c52fb): POMap.mount is made to throw before
    app.js runs (an init script: no inline script in the page). The pane must
    carry #2 with the error's message, S.map.loading must be cleared - proved
    by a second mount attempt on the next render (tab round trip) - and the
    typed form must still search."""
    with hub_server() as srv:
        ctx = browser.browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
            ctx.add_init_script("""(() => {
              let real = null; window.__mounts = 0;
              Object.defineProperty(window, 'POMap', { configurable: true,
                get() { return real; },
                set(v) { real = Object.assign({}, v, { mount() { window.__mounts += 1; throw new Error('boom from mount'); } }); } });
            })();""")
            errors = []
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{srv.port}/", wait_until="domcontentloaded")
            pg.wait_for_selector('[data-testid="wordmark"]', timeout=20000)
            pg.click(C.q("tab-search"))
            pg.wait_for_selector(C.q("map-status"), timeout=15000)
            pg.wait_for_timeout(300)
            assert C.status(pg) == UNREADABLE.format(reason="boom from mount")
            assert pg.evaluate("() => window.__mounts") == 1
            assert errors == [], errors
            pg.click(C.q("tab-trips"))
            pg.wait_for_timeout(300)
            pg.click(C.q("tab-search"))
            pg.wait_for_timeout(500)
            assert pg.evaluate("() => window.__mounts") == 2, "loading was not cleared: no second mount attempt"
            assert C.status(pg) == UNREADABLE.format(reason="boom from mount")
            assert pg.evaluate("() => document.querySelectorAll('[data-testid=map-status]').length") == 1
            pg.fill(C.q("search-from"), "SFO")
            pg.fill(C.q("search-to"), "MAD")
            pg.fill(C.q("search-date"), "2027-01-15")
            pg.click(C.q("search-run"))
            pg.wait_for_selector(C.q("search-confirm-go"), timeout=15000)
            assert "--origin SFO --destination MAD" in C.confirm_text(pg)
        finally:
            ctx.close()
