"""
G. The F5 fix under attack (a022cf4: `if (S.tripId === id && S.view === "trips")`
in `sendRun`).

One line decides whether a run that finishes somewhere else throws the user off
a half-typed new-trip form. Every arrangement I could build is here: the form
opened after the run started, the form reached through the Search tab, a full
leg typed before the run was even started, two runs in sequence, a run that is
REFUSED with a non-200 (409 BUSY, the engine's own run-slot refusal) and a run
that is REFUSED by the wallet (exit 2, a 200 the page must still not navigate
on). Each asserts the same four things:

  * the hash is still `#new-trip`;
  * the typed values are still in the inputs;
  * `S.nt` itself survived - checked by leaving the form by HASH (which keeps
    S.nt) and coming back, never by `+ New trip`, which starts a fresh state
    by design and would hide the defect;
  * the finished run is still reachable as a chip on its own trip.

And the normal case, unchanged: on the trips view showing that trip, a finished
run still navigates straight to the result.

The run is the `slow_run` scenario's four-second one, and the completion is
waited for on the response itself (`expect_response`), not on a sleep.
"""
import json

from conftest import (go_trip, nt_values, patched_page, q, run_search, st_server, text)  # noqa: F401

UI = "sfo-mad-2027-01-15"
RUN_URL = f"/api/trips/{UI}/run"


def _hash(pg):
    return pg.evaluate("() => location.hash")


def _focused(pg):
    return pg.evaluate("() => document.activeElement.getAttribute('data-testid')")


def _goto(pg, hash_):
    """Leave/enter a view the way the ADDRESS BAR does: S.nt is untouched.
    `+ New trip` is deliberately not used - it resets S.nt by design."""
    pg.evaluate("(h) => { location.hash = h; }", hash_)
    pg.wait_for_timeout(300)


def _start_run(pg, mode="offline"):
    """From the trips view showing UI: start the slow run."""
    pg.click(q("mode-" + mode))
    pg.wait_for_timeout(100)
    pg.click(q("run-go"))
    pg.wait_for_selector(q("run-busy"), timeout=10000)


def _type_partial(pg, name="typed-while-running"):
    pg.fill(q("nt-name"), name)
    pg.fill(q("nt-leg-1-origin"), "LH")
    pg.select_option(q("nt-cabin"), "J")
    pg.focus(q("nt-leg-1-destination"))
    pg.keyboard.type("SF")
    return {"name": name, "origin": "LH", "destination": "SF", "cabin": "J"}


def _type_full_leg(pg, name="full-leg-typed"):
    pg.fill(q("nt-name"), name)
    pg.select_option(q("nt-cabin"), "J")
    pg.fill(q("nt-leg-1-origin"), "SFO")
    pg.fill(q("nt-leg-1-destination"), "LHR")
    pg.fill(q("nt-leg-1-date"), "2027-04-02")
    pg.select_option(q("nt-leg-1-cabin"), "F")
    pg.fill(q("nt-leg-1-cash"), "3150")
    return {"name": name, "cabin": "J", "origin": "SFO", "destination": "LHR",
            "date": "2027-04-02", "legcabin": "F", "cash": "3150"}


def _assert_form(pg, want):
    v = nt_values(pg)
    for k, x in want.items():
        assert v[k] == x, (k, v)
    return v


def _run_finished(pg, timeout=30000):
    """A context manager that returns once the POST /run response is in."""
    return pg.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(RUN_URL), timeout=timeout)


def _chips(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('[data-testid^="run-chip-"]'))
        .map((b) => b.getAttribute('data-testid') + '=' + b.textContent)""")


def _assert_chip_on_ui(pg, n=1):
    """The finished run is still reachable on its own trip, and clicking the
    chip opens its result."""
    go_trip(pg, UI)
    chips = [c for c in _chips(pg) if not c.startswith("run-chip-fixture")]
    assert len(chips) == n, chips
    assert "exit" in chips[0], chips
    pg.click(q("run-chip-0"))
    pg.wait_for_selector(q("trip-result"), timeout=15000)
    assert _hash(pg).startswith("#trips/" + UI + "/run/"), _hash(pg)


# ------------------------------------------------------- 1. open the form after


def test_G1_run_on_x_then_open_new_trip_and_type(browser):
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
                pg.click(q("new-trip"))
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                want = _type_partial(pg)
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            assert _focused(pg) == "nt-leg-1-destination", _focused(pg)
            # S.nt itself: leave by hash and come back
            _goto(pg, "#trips/" + UI)
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            _assert_chip_on_ui(pg)
            assert pg.facts["errors"] == []


# --------------------------------------------- 2. via the Search tab in between


def test_G2_run_on_x_then_search_then_new_trip(browser):
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
                pg.click(q("tab-search"))
                pg.wait_for_timeout(200)
                assert _hash(pg) == "#search"
                _goto(pg, "#new-trip")
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                want = _type_partial(pg, name="search-then-form")
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            _goto(pg, "#search")
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            _assert_chip_on_ui(pg)
            assert pg.facts["errors"] == []


# ---------------------------------- 3. a FULL leg typed before the run is started


def test_G3_a_full_leg_typed_first_then_the_run_lands_on_the_form(browser):
    """The worst case for the user: a complete leg is already typed, the run
    was started from the trip page afterwards, and the form is re-entered by
    hash (S.nt intact) before the run returns."""
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.click(q("new-trip"))
            pg.wait_for_selector(q("nt-name"), timeout=10000)
            want = _type_full_leg(pg)
            _goto(pg, "#trips/" + UI)
            with _run_finished(pg):
                _start_run(pg)
                _goto(pg, "#new-trip")
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                _assert_form(pg, want)          # nothing lost on the way in
                pg.focus(q("nt-leg-1-cash"))
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            assert nt_values(pg)["legs"] == 1
            _assert_chip_on_ui(pg)
            # and the form still WORKS afterwards: Preview echoes the typed leg
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            assert pg.facts["errors"] == []


# ------------------------------------------------------ 4. two runs in sequence


def test_G4_two_runs_in_sequence_while_the_form_is_open(browser):
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
                pg.click(q("new-trip"))
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                want = _type_partial(pg, name="two-runs")
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            # second run, started from the trip page, form re-entered by hash
            _goto(pg, "#trips/" + UI)
            with _run_finished(pg):
                _start_run(pg)
                _goto(pg, "#new-trip")
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                pg.fill(q("nt-leg-1-date"), "2027-05-06")
                want["date"] = "2027-05-06"
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            _goto(pg, "#trips/" + UI)
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            _assert_chip_on_ui(pg, n=2)
            assert pg.facts["errors"] == []


# ------------------------------------- 5. a run that FAILS with a non-200 (409)


def test_G5_a_run_refused_with_a_non_200_while_the_form_is_open(browser):
    """The engine's own run-slot refusal: the slow run sleeps first and takes
    the lock after, so holding the lock from stdin turns THIS run into a real
    409 `busy` while the user is typing. The non-200 branch of `sendRun` calls
    `renderTrips()` unconditionally, which on the new-trip view rebuilds the
    form from S.nt - so the values must survive; where FOCUS goes is recorded
    below."""
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg) as ri:
                _start_run(pg)
                assert srv.cmd("lock")["locked"] is True
                pg.click(q("new-trip"))
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                want = _type_partial(pg, name="refused-non-200")
            res = ri.value
            assert res.status == 409, (res.status, res.text()[:200])
            assert json.loads(res.text())["error"] == "busy"
            srv.cmd("unlock")
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            where = pg.evaluate("() => document.activeElement.tagName + ':' + "
                                "document.activeElement.getAttribute('data-testid')")
            print("NON-200 ON THE FORM: banner", repr(text(pg, q("banner-error"))), "focus", where)
            # RECORDED, not a red: the values survive, the caret does not. The
            # non-200 branch re-renders unconditionally, so on the new-trip view
            # the form is rebuilt from S.nt and focus falls to <body>. The 200
            # branch does not re-render the form and keeps focus (G1, G6).
            assert where == "BODY:null", where
            _goto(pg, "#trips/" + UI)
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            # nothing was run, so there is no chip
            go_trip(pg, UI)
            assert [c for c in _chips(pg) if not c.startswith("run-chip-fixture")] == []
            assert pg.facts["errors"] == []


# ---------------------------------- 6. a run REFUSED by the wallet (exit 2, 200)


def test_G6_a_wallet_refused_run_exit_2_while_the_form_is_open(browser):
    """Exit 2 WALLET ERROR comes back as a 200 run payload, so it takes the
    SAME branch of `sendRun` as a good run: the guard, not the exit code, is
    what keeps the form. The wallet is the probe server's own tmp file."""
    with st_server("slow_run") as srv:
        wallet = srv.info["wallet"]
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg) as ri:
                _start_run(pg)
                with open(wallet, "w", encoding="utf-8") as fh:
                    fh.write("{ not json")
                pg.click(q("new-trip"))
                pg.wait_for_selector(q("nt-name"), timeout=10000)
                want = _type_partial(pg, name="wallet-refused")
            res = ri.value
            assert res.status == 200, res.status
            assert json.loads(res.text())["exit_code"] == 2, res.text()[:300]
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#new-trip", _hash(pg)
            _assert_form(pg, want)
            assert _focused(pg) == "nt-leg-1-destination", _focused(pg)
            _goto(pg, "#trips/" + UI)
            _goto(pg, "#new-trip")
            _assert_form(pg, want)
            go_trip(pg, UI)
            chips = [c for c in _chips(pg) if not c.startswith("run-chip-fixture")]
            assert len(chips) == 1 and "exit 2" in chips[0], chips
            assert pg.facts["errors"] == []


# ------------------------------------------- 7. the normal case is UNCHANGED


def test_G7_on_the_trips_view_a_finished_run_still_lands_on_its_result(browser):
    """The fix must not cost the ordinary path: the trips view showing THIS
    trip still goes straight to the run result - and another trip's page still
    only re-renders (F4), and the search view is still left alone."""
    with st_server("slow_run") as srv:
        srv.cmd("mk other-trip MAD:AMS:2027-01-19:120 J")
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
            pg.wait_for_selector(q("trip-result"), timeout=20000)
            h = _hash(pg)
            assert h.startswith("#trips/" + UI + "/run/"), h
            assert pg.evaluate("() => document.querySelector('[data-testid=run-chip-0]').getAttribute('aria-pressed')") == "true"
            # another trip's page: no navigation, buttons rebuilt (F4)
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
                pg.click(q("tab-trips"))
                pg.wait_for_timeout(100)
                pg.click(q("trip-row-other-trip"))
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#trips/other-trip", _hash(pg)
            assert pg.query_selector(q("run-busy")) is None
            assert pg.evaluate("""() => { const b = document.querySelector('[data-testid=trip-delete]');
                return b ? b.disabled : null; }""") is False
            # the search view keeps its own state (F4b's tail, re-asserted here)
            go_trip(pg, UI)
            run_search(pg)
            pg.locator("td.cab.pick").nth(0).click()
            pg.wait_for_timeout(250)
            pg.keyboard.press("Escape")
            go_trip(pg, UI)
            with _run_finished(pg):
                _start_run(pg)
                pg.click(q("tab-search"))
                pg.wait_for_timeout(200)
            pg.wait_for_timeout(700)
            assert _hash(pg) == "#search", _hash(pg)
            assert pg.evaluate("() => document.querySelectorAll('td.cab.sel').length") == 1
            assert pg.facts["errors"] == []
