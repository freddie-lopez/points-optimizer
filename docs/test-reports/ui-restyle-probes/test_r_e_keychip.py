"""
E. THE KEY CHIP (decision D8). Found: the element exists with its testid, is
hidden, and /api/state is unchanged. Not found: `key: not found` is visible in
the warn form, the mode pill says LIVE UNAVAILABLE on Search, mode-live is
disabled with the key-error fold. Then the key goes away and comes back in one
session: does the page follow?
"""
import json

import pytest

from conftest import ROOT, box, open_trip, probe_server, q, styles, text

WARN = "rgb(240, 184, 90)"


def api_state(srv):
    # http.client, not urllib: conftest points the proxy variables at a dead
    # proxy which urllib would honour. Loopback only; the canary allows the
    # port this test's own server listens on.
    import http.client
    from ui_probes_conftest import ALLOWED_PORTS
    ALLOWED_PORTS.add(srv.port)
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.request("GET", "/api/state", headers={"X-PO-Token": srv.info["token"],
                                               "Host": f"127.0.0.1:{srv.port}"})
    r = conn.getresponse()
    body = json.loads(r.read().decode())
    conn.close()
    return body


def chip(pg):
    s = styles(pg, q("key-source"), ["display", "color", "border-top-style", "border-top-color",
                                     "background-color", "border-radius"])
    assert len(s) == 1, s
    return s[0]


def test_E1_key_found_the_chip_exists_is_hidden_and_the_state_is_unchanged(browser):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port) as pg:
            c = chip(pg)
            st = api_state(srv)
            open_trip(pg, "trip_b_europe", "offline")
            c2 = chip(pg)
            pg.click(q("run-details") + " summary")
            pg.wait_for_timeout(150)
            details_offline = text(pg, q("run-details")) or ""
            page_offline = text(pg, "body") or ""
            open_trip(pg, "trip_b_europe", "live")
            pg.click(q("run-details") + " summary")
            pg.wait_for_timeout(150)
            details_live = text(pg, q("run-details")) or ""
    assert c["hidden"] is True and c["display"] == "none" and c["text"] == "" and c["cls"] == "keysrc", c
    assert c2["hidden"] is True and c2["text"] == "", c2
    assert st["key"] == {"found": True, "source": "environment", "path": None, "error_text": None}, st["key"]
    # D8's fallback: "the run-details box names the source"
    assert "(source: environment)" in details_live, details_live[:400]
    assert "environment" in page_offline, (
        "with a key found and an OFFLINE run (the default), nowhere on the page says a key was "
        "found or where it came from - the chip is hidden and run-details says 'not used'",
        details_offline[:200])


def test_E2_key_not_found_the_chip_is_visible_in_the_warn_form(browser):
    with probe_server("no_key") as srv:
        with browser.page(srv.port) as pg:
            c = chip(pg)
            b = box(pg, q("key-source"))
            pill_trips = text(pg, q("mode-pill"))
            pg.click(q("tab-search"))
            pg.wait_for_timeout(200)
            pill_search = text(pg, q("mode-pill"))
            pill_style = styles(pg, q("mode-pill"), ["color", "border-top-color", "background-color"])[0]
            search_run_disabled = pg.evaluate("() => document.querySelector('[data-testid=\"search-run\"]').disabled")
            search_state = text(pg, q("search-state"))
            open_trip(pg, "trip_b_europe", "offline", run=False)
            live_disabled = pg.evaluate("() => document.querySelector('[data-testid=\"mode-live\"]').disabled")
            fold = pg.query_selector(q("key-error")) is not None
            st = api_state(srv)
    assert c["hidden"] is False and c["text"] == "key: not found" and c["cls"] == "keysrc warn", c
    assert c["display"] != "none" and b["w"] > 0 and b["h"] > 0
    assert c["color"] == WARN and c["border-top-style"] == "solid" and c["border-top-color"] != c["background-color"], c
    assert c["background-color"] not in ("rgba(0, 0, 0, 0)", "transparent"), c
    assert pill_trips == "OFFLINE" and pill_search == "LIVE UNAVAILABLE", (pill_trips, pill_search)
    assert pill_style["color"] == WARN, pill_style
    assert search_run_disabled is True and search_state and "LIVE UNAVAILABLE" in search_state.upper()
    assert live_disabled is True and fold is True
    assert st["key"]["found"] is False and st["key"]["error_text"]


def test_E3_the_chip_follows_the_key_when_it_goes_away_and_comes_back(browser):
    """The key is resolved per request. Remove it mid-session, run OFFLINE (which
    refreshes state), and the chip must appear; restore it, run again, and it
    must go. The LIVE card must follow too."""
    with probe_server("no_key_toggle") as srv:
        with browser.page(srv.port) as pg:
            # no_key_toggle starts WITHOUT a key
            assert chip(pg)["hidden"] is False
            srv.cmd("key on")
            open_trip(pg, "trip_b_europe", "offline")
            pg.wait_for_timeout(400)
            after_on = chip(pg)
            live_after_on = pg.evaluate("() => document.querySelector('[data-testid=\"mode-live\"]').disabled")
            fold_after_on = pg.query_selector(q("key-error")) is not None
            # does a re-render (any navigation) catch the strip up with the chip?
            pg.click(q("tab-search")); pg.wait_for_timeout(150)
            pg.click(q("tab-trips")); pg.wait_for_timeout(150)
            pg.click(q("trip-row-trip_b_europe")); pg.wait_for_selector(q("trip-detail"))
            live_after_rerender = pg.evaluate("() => document.querySelector('[data-testid=\"mode-live\"]').disabled")
            srv.cmd("key off")
            pg.click(q("mode-offline"))
            pg.click(q("run-go"))
            pg.wait_for_function("() => document.querySelectorAll('[data-testid^=\"run-chip-\"]').length >= 3",
                                 timeout=30000)
            pg.wait_for_timeout(400)
            after_off = chip(pg)
            live_after_off = pg.evaluate("() => document.querySelector('[data-testid=\"mode-live\"]').disabled")
            fold_after_off = pg.query_selector(q("key-error")) is not None
            pill_after_off = text(pg, q("mode-pill"))
    assert after_on["hidden"] is True and after_on["text"] == "", after_on
    assert after_off["hidden"] is False and after_off["text"] == "key: not found", after_off
    # the run strip is rendered from the same state; does it agree with the chip?
    assert live_after_rerender is False, "even a re-render leaves LIVE disabled with a key present"
    assert live_after_on is False and fold_after_on is False, (
        "the chip (hidden = key found) and the LIVE card (disabled = no key) disagree on the same "
        "screen until the next render", live_after_on, fold_after_on)
    assert live_after_off is True and fold_after_off is True, (live_after_off, fold_after_off)


def test_E4_the_chip_is_hidden_before_state_arrives_and_never_says_key_ellipsis():
    html = (ROOT / "src" / "ui" / "static" / "index.html").read_text()
    assert 'id="key-source" data-testid="key-source" hidden' in html
    assert "key: …" not in html and "key: ..." not in html


def test_E5_api_state_key_shape_is_byte_identical_to_the_base_commit():
    import subprocess
    d = subprocess.run(["git", "diff", "386b2fc..HEAD", "--", "src/ui/engine.py", "src/ui/api.py",
                        "src/ui/serialize.py", "src/ui/server.py"], cwd=str(ROOT),
                       capture_output=True, text=True)
    assert d.stdout == "", d.stdout[:500]
