"""
E. The two-click state machine, out of order; the preflight/confirm flow
byte-identical whether typed or clicked; the pane toggle; the drawer.
"""
import sys

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))


def active(pg):
    return pg.evaluate("() => document.activeElement && (document.activeElement.getAttribute('data-testid') || document.activeElement.tagName)")


def picked(pg):
    return sorted(m["id"] for m in C.markers(pg) if m["picked"])


def test_E1_click_a_a_b_c_and_the_focus_rule(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-hub-SYD")
            assert C.values(pg) == {"from": "SYD", "to": ""} and active(pg) == "search-to"
            assert picked(pg) == ["map-hub-SYD"] and C.route_d(pg) is None
            C.click_marker(pg, "map-hub-SYD")                  # A again: no-op
            assert C.values(pg) == {"from": "SYD", "to": ""}
            C.click_marker(pg, "map-hub-MAD")                  # B
            assert C.values(pg) == {"from": "SYD", "to": "MAD"} and active(pg) == "search-run"
            assert picked(pg) == ["map-hub-MAD", "map-hub-SYD"] and C.route_d(pg)
            C.click_marker(pg, "map-cluster-SFO")              # C via the list: replaces To
            pg.click(C.q("map-pick-SFO"))
            pg.wait_for_timeout(200)
            assert C.values(pg) == {"from": "SYD", "to": "SFO"}
            assert picked(pg) == ["map-hub-SFO", "map-hub-SYD"]
            C.click_marker(pg, "map-hub-SYD")                  # From's airport with To filled: replaces To
            assert C.values(pg) == {"from": "SYD", "to": "SYD"}
            assert C.route_d(pg) is None                       # no line from A to A
            assert pg.facts["errors"] == []


def test_E2_edits_recompute_the_picks_from_the_inputs(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-hub-SYD")
            C.click_marker(pg, "map-hub-MAD")
            pg.fill(C.q("search-to"), "")
            pg.wait_for_timeout(150)
            assert picked(pg) == ["map-hub-SYD"] and C.route_d(pg) is None
            assert C.status(pg) == "Click an airport to set To."
            pg.fill(C.q("search-from"), "")
            pg.wait_for_timeout(150)
            assert picked(pg) == [] and C.status(pg) == "Click an airport to set From."
            C.click_marker(pg, "map-hub-MAD")                  # next click fills From again
            assert C.values(pg) == {"from": "MAD", "to": ""}
            pg.fill(C.q("search-from"), "sfo")                 # lower-case typed = a pick, value stays as typed
            pg.wait_for_timeout(150)
            assert picked(pg) == ["map-hub-SFO"] and C.values(pg)["from"] == "sfo"
            pg.fill(C.q("search-from"), "SOF")
            pg.wait_for_timeout(150)
            assert picked(pg) == []
            assert C.status(pg).endswith(" · SOF is not on this map. It can still be searched.")
            pg.fill(C.q("search-from"), "SF")
            pg.wait_for_timeout(150)
            assert "not on this map" not in C.status(pg)


def test_E3_picks_survive_a_tab_round_trip_and_a_pane_round_trip(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-hub-SYD")
            C.click_marker(pg, "map-hub-MAD")
            d = C.route_d(pg)
            pg.click(C.q("tab-trips"))
            pg.wait_for_timeout(300)
            pg.click(C.q("tab-search"))
            pg.wait_for_timeout(400)
            assert C.values(pg) == {"from": "SYD", "to": "MAD"}
            assert picked(pg) == ["map-hub-MAD", "map-hub-SYD"] and C.route_d(pg) == d
            pg.fill(C.q("search-date"), "2027-01-15")
            pg.click(C.q("search-run"))
            pg.wait_for_selector(C.q("search-confirm-go"))
            C.finish_search(pg)
            assert C.text(pg, C.q("search-pane-toggle")) == "Show map"
            assert pg.evaluate("() => document.getElementById('map-pane').hidden")
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            assert C.text(pg, C.q("search-pane-toggle")) == "Show results"
            assert picked(pg) == ["map-hub-MAD", "map-hub-SYD"] and C.route_d(pg) == d
            assert C.text(pg, C.q("search-table")) is None
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            assert C.text(pg, C.q("search-table")) is not None
            assert pg.evaluate("() => document.getElementById('map-pane').hidden")


def test_E4_a_map_search_is_byte_identical_to_a_typed_search(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.run_search_typed(pg, "SFO", "MAD")
            typed = C.confirm_text(pg)
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
            pg.fill(C.q("search-from"), "")
            pg.fill(C.q("search-to"), "")
            pg.wait_for_timeout(150)
            C.click_marker(pg, "map-cluster-SFO")
            pg.click(C.q("map-pick-SFO"))
            pg.wait_for_timeout(150)
            C.click_marker(pg, "map-hub-MAD")
            pg.click(C.q("search-run"))
            pg.wait_for_selector(C.q("search-confirm-go"))
            clicked = C.confirm_text(pg)
            assert clicked == typed
            assert "--origin SFO --destination MAD --date 2027-01-15" in clicked
            C.finish_search(pg)
            t_map = C.transcript(pg)
            assert t_map and "SFO" in t_map
        with browser.page(srv.port) as pg2:
            C.open_search(pg2)
            C.run_search_typed(pg2, "SFO", "MAD")
            C.finish_search(pg2)
            t_typed = C.transcript(pg2)
            # the transcript carries a call counter and a run duration; compare
            # everything but the numbers that count runs on this server
            import re
            norm = lambda s: re.sub(r"\d+ since launch|\d+ of 1,000|\d+\.\d+ s", "N", s)  # noqa: E731
            assert norm(t_typed) == norm(t_map)


def test_E5_show_map_is_not_offered_while_a_run_is_in_flight(browser):
    with hub_server("slow_search") as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            pg.click(C.q("search-confirm-go"))
            pg.wait_for_timeout(500)
            assert C.text(pg, C.q("search-pane-toggle")) is None
            assert pg.evaluate("() => document.getElementById('map-pane').hidden")
            assert C.text(pg, C.q("search-state")).startswith("Asking Seats.aero")
            pg.wait_for_function("() => !document.querySelector('[data-testid=search-run]').disabled", timeout=30000)
            pg.wait_for_timeout(300)
            assert C.text(pg, C.q("search-pane-toggle")) == "Show map"


def test_E6_after_no_awards_and_api_error_runs_the_map_is_unchanged(browser):
    for sc in ("search_api_error", "search_no_awards"):
        with hub_server(sc) as srv:
            with browser.page(srv.port) as pg:
                C.open_search(pg)
                C.click_marker(pg, "map-cluster-SFO")
                pg.click(C.q("map-pick-SFO"))
                pg.wait_for_timeout(150)
                C.click_marker(pg, "map-hub-MAD")
                pg.fill(C.q("search-date"), "2027-01-15")
                before = [(m["id"], m["picked"], round(m["x"]), round(m["y"])) for m in C.markers(pg)]
                d, st = C.route_d(pg), C.status(pg)
                pg.click(C.q("search-run"))
                pg.wait_for_selector(C.q("search-confirm-go"))
                C.finish_search(pg)
                pg.click(C.q("search-pane-toggle"))
                pg.wait_for_timeout(300)
                after = [(m["id"], m["picked"], round(m["x"]), round(m["y"])) for m in C.markers(pg)]
                assert after == before and C.route_d(pg) == d and C.status(pg) == st
                pane = pg.evaluate("() => document.getElementById('map-pane').innerText")
                assert pane.count("availability") == 1


def test_E7_a_drawer_open_from_a_result_never_sits_beside_the_map(browser):
    """Plan §7: 'at 1180+: drawer as a third column with results, never
    beside the map'. Open a cell's drawer, press Show map."""
    with hub_server() as srv:
        with browser.page(srv.port, width=1440) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            C.finish_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            assert not pg.evaluate("() => document.getElementById('drawer-search').hidden")
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            map_shown = not pg.evaluate("() => document.getElementById('map-pane').hidden")
            drawer_shown = not pg.evaluate("() => document.getElementById('drawer-search').hidden")
            pg.screenshot(path=str(C.SHOTS / "E7-map-beside-drawer.png"))
            assert not (map_shown and drawer_shown), "the map and the drawer are on screen together"


def test_E8_escape_closes_popover_then_suggestions_then_drawer(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            C.finish_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            pg.fill(C.q("search-to"), "s")
            pg.wait_for_timeout(120)
            assert C.text(pg, C.q("search-suggest-to"))
            # open the popover by keyboard so the input's list is not blurred away
            pg.evaluate("() => document.querySelector('[data-testid=map-cluster-JFK]').focus()")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(150)
            state = lambda: (bool(C.text(pg, C.q("map-cluster-list"))), bool(C.text(pg, C.q("search-suggest-to"))),  # noqa: E731
                             not pg.evaluate("() => document.getElementById('drawer-search').hidden"))
            s0 = state()
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(120)
            s1 = state()
            assert s0[0] and not s1[0], (s0, s1)          # popover first
            assert s1[2], "the drawer closed before the popover"
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(120)
            s2 = state()
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(120)
            s3 = state()
            assert not s3[2], "the drawer never closed"


def test_E9_a_click_on_the_ocean_does_not_close_the_popover(browser):
    """Plan §4.6: 'Any pan/zoom closes it'. A plain click elsewhere leaves it
    open - recorded; Esc and the × work."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-LHR")
            b = C.box(pg, C.q("map-svg"))
            pg.mouse.click(b["x"] + 40, b["y"] + b["h"] - 80)
            pg.wait_for_timeout(150)
            assert C.text(pg, C.q("map-cluster-list")) is None, "a click on the ocean leaves the list open"


def test_E10_a_preflight_refusal_of_a_drifted_searchable_flag_is_visible(browser):
    """A hub flagged searchable:true that airports.csv does not know: the pick
    lands in the input and preflight refuses it in the builder's words -
    loudly, in the column."""
    from src import map_tools
    doc = C.synthetic()
    row = map_tools.load_coordinates()["AAA"]
    doc["hubs"].append({"iata": "AAA", "name": row["name"], "city": row["city"], "country": row["country"],
                        "lat": float(row["lat"]), "lon": float(row["lon"]), "routes": 700,
                        "sources": ["a", "b", "c"], "regions": [], "searchable": True})
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-hub-AAA")
            C.click_marker(pg, "map-hub-SYD")
            pg.fill(C.q("search-date"), "2027-01-15")
            pg.click(C.q("search-run"))
            pg.wait_for_timeout(600)
            errs = pg.evaluate("() => Array.from(document.querySelectorAll('.field-error')).map(e => e.textContent)")
            assert errs and "'AAA' is not an airport this tool knows" in errs[0]
            assert C.confirm_text(pg) is None
            assert C.values(pg) == {"from": "AAA", "to": "SYD"}
