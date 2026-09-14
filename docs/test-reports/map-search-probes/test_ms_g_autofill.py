"""
G. Autofill: the ranking rule, 1-char and unicode input, 500 characters,
keyboard driving, that a choice fills exactly the IATA code, that Enter with
no active row is the old preflight, and that a list never widens the page.
"""
import sys

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from src import map_tools, regions  # noqa: E402

COORDS = map_tools.load_coordinates()
KNOWN = [c for c in regions.known_airports() if c in COORDS]


def rows(pg, key="from"):
    return pg.evaluate("(k) => Array.from(document.querySelectorAll('[data-testid=search-suggest-'+k+'] .srow')).map(b => b.getAttribute('data-testid').slice(8) + (b.getAttribute('aria-selected')==='true' ? '*' : ''))", key)


def rank(q, hubs):
    """The plan's rule, independently: exact code, code prefix, city prefix,
    name/city substring; ties by routes desc then iata."""
    q = q.strip().lower()
    out = []
    for h in hubs:
        code, city, name = h["iata"].lower(), h["city"].lower(), h["name"].lower()
        if code == q:
            r = 0
        elif code.startswith(q):
            r = 1
        elif city.startswith(q):
            r = 2
        elif q in name or q in city:
            r = 3
        else:
            continue
        out.append((r, -h["routes"], h["iata"]))
    return [o[2] for o in sorted(out)][:8]


def test_G1_the_ranking_is_the_plans_rule_for_every_query(browser):
    doc = C.synthetic(codes=KNOWN, routes={c: 1000 - i for i, c in enumerate(sorted(KNOWN))})
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            for q in ("s", "san", "sf", "lon", "new york", "é", "SFO", "sfo ", "Kingsford", "a", "l", "j", "new", "paris", "tokyo"):
                pg.fill(C.q("search-from"), q)
                pg.wait_for_timeout(120)
                assert rows(pg) == rank(q, doc["hubs"]), q
            for q in ("ü", "🛫", "x" * 500, " ", ""):
                pg.fill(C.q("search-from"), q)
                pg.wait_for_timeout(120)
                assert rows(pg) == [] and C.text(pg, C.q("search-suggest-from")) is None, q
                assert pg.get_attribute(C.q("search-from"), "aria-expanded") == "false"
            assert pg.facts["errors"] == []


def test_G2_keyboard_driving_and_the_pick_writes_exactly_the_code(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "san")
            pg.wait_for_timeout(120)
            assert rows(pg) == ["SAN", "SFO", "SJC"]
            pg.keyboard.press("ArrowDown")
            pg.keyboard.press("ArrowDown")
            assert rows(pg) == ["SAN", "SFO*", "SJC"]
            assert pg.get_attribute(C.q("search-from"), "aria-activedescendant") == "suggest-from-SFO"
            pg.keyboard.press("ArrowDown")
            pg.keyboard.press("ArrowDown")
            assert rows(pg) == ["SAN", "SFO", "SJC*"]
            pg.keyboard.press("ArrowUp")
            pg.keyboard.press("ArrowUp")
            pg.keyboard.press("ArrowUp")
            assert rows(pg) == ["SAN", "SFO", "SJC"]
            pg.keyboard.press("ArrowDown")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(150)
            assert C.values(pg)["from"] == "SAN" and rows(pg) == []
            assert C.confirm_text(pg) is None
            assert sorted(m["id"] for m in C.markers(pg) if m["picked"]) == ["map-hub-SAN"]
            assert C.status(pg) == "Click an airport to set To."
            assert pg.evaluate("() => document.activeElement.getAttribute('data-testid')") == "search-from"
            pg.fill(C.q("search-to"), "mad")
            pg.wait_for_timeout(120)
            assert rows(pg, "to") == ["MAD"]
            pg.keyboard.press("Escape")
            assert rows(pg, "to") == [] and C.confirm_text(pg) is None
            pg.fill(C.q("search-date"), "2027-01-15")
            pg.focus(C.q("search-to"))
            pg.keyboard.press("Enter")
            pg.wait_for_selector(C.q("search-confirm-go"), timeout=10000)
            assert "--origin SAN --destination MAD --date 2027-01-15" in C.confirm_text(pg)


def test_G3_mouse_pick_and_one_list_at_a_time(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "lon")
            pg.wait_for_timeout(120)
            pg.click(C.q("suggest-LGW"))
            pg.wait_for_timeout(150)
            assert C.values(pg)["from"] == "LGW" and rows(pg) == []
            pg.fill(C.q("search-from"), "s")
            pg.wait_for_timeout(120)
            pg.fill(C.q("search-to"), "s")
            pg.wait_for_timeout(250)
            assert rows(pg) == [] and rows(pg, "to") != []
            # blur (click elsewhere) closes it
            pg.click(".search-head h1")
            pg.wait_for_timeout(300)
            assert rows(pg, "to") == []


def test_G4_an_open_list_and_a_map_click_leave_the_inputs_consistent(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "s")
            pg.wait_for_timeout(120)
            C.click_marker(pg, "map-hub-SYD")
            v = C.values(pg)
            assert rows(pg) == [] and rows(pg, "to") == []
            # the partial 's' is not a From; the click fills To - the state the
            # footer then describes is checked in C8
            assert v == {"from": "s", "to": "SYD"}
            assert sorted(m["id"] for m in C.markers(pg) if m["picked"]) == ["map-hub-SYD"]


def test_G5_the_list_never_widens_the_page_at_any_width(browser):
    with hub_server() as srv:
        for w in (400, 899, 900, 1440):
            with browser.page(srv.port, width=w) as pg:
                C.open_search(pg, wait_map=w >= 900)
                pg.fill(C.q("search-from"), "s")
                pg.wait_for_timeout(150)
                assert rows(pg) != []
                assert C.doc_widths(pg)["bodySW"] <= w
                sb = C.box(pg, C.q("search-suggest-from"))
                assert sb["right"] <= w and sb["x"] >= 0
                pg.fill(C.q("search-to"), "s")
                pg.wait_for_timeout(150)
                assert C.doc_widths(pg)["bodySW"] <= w
                sb = C.box(pg, C.q("search-suggest-to"))
                assert sb["right"] <= w and sb["x"] >= 0
