"""
C. SPENDING. Nothing may reach Seats.aero without a fresh, matching, unused
server-side confirm; the maximum stated before a run must be a maximum; the
calls counters must equal what the transport actually saw.

RED = a defect that exists. GREEN = held up.
"""
import copy
import threading
import time

import pytest

from conftest import (LIVE, OFFLINE, TRANSFER, aeroplan_row, g, response, server, tp,
                      write_wallet, copy_trips)

SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15", "date_to": ""}


def _paging_impl(pages=3, rows_per_page=1, trips_payload=None):
    """Every search answers `pages` pages (hasMore + cursor), each carrying real
    rows re-routed to the request. Every trips lookup answers one itinerary."""
    stub = g.Stub()

    def impl(url, **kw):
        params = kw.get("params") or {}
        if url.endswith("/search"):
            cur = params.get("cursor")
            n = 1 if cur is None else int(cur)
            o, d = params["origin_airport"], params["destination_airport"]
            iso = g.ROUTES.get((o, d), params.get("start_date"))
            rows = []
            for i in range(rows_per_page):
                r = aeroplan_row(o, d, iso, rid=(f"p{n}r{i}{o}{d}" + "x" * 27)[:27])
                rows.append(r)
            if (o, d) == ("LHR", "SFO") and n == 1:
                rows = [g.vs_row()]
            body = {"data": rows, "hasMore": n < pages}
            if n < pages:
                body["cursor"] = str(n + 1)
            return response(body)
        aid = url.rsplit("/", 1)[-1]
        payload = trips_payload(aid) if trips_payload else tp.payload(
            [tp.trip([tp.segment("AC837", "SFO", "MAD", 1, AvailabilityID=aid)],
                     availability_id=aid, source="aeroplan", cabin="economy", cost=50000)])
        return response(payload)

    return impl


# ---------------------------------------------------------------- confirms


def test_C1_live_trip_without_confirm_forged_or_reused_spends_nothing(ui):
    r = ui.post("/api/trips/trip_b_europe/run", LIVE)
    assert r.status == 409 and r.json()["error"] == "confirm_required"
    for forged in ("0" * 32, "../../x", {"a": 1}, ["x"], 12345, True):
        r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=forged))
        assert r.status in (409, 400), (forged, r.status)
    assert ui.net.calls == []


def test_C2_search_without_confirm_or_with_a_trip_confirm_spends_nothing(ui):
    r = ui.post("/api/search/run", SEARCH)
    assert r.status == 409 and r.json()["error"] == "confirm_required"
    # A confirm issued for a LIVE trip is not a confirm for a search.
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    r = ui.post("/api/search/run", dict(SEARCH, confirm_id=pf["confirm_id"]))
    assert r.status == 409
    # ... and a search confirm is not a confirm for a trip.
    spf = ui.post("/api/search/preflight", SEARCH).json()
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=spf["confirm_id"]))
    assert r.status == 409
    assert ui.net.calls == []


def test_C3_a_confirm_is_single_use_even_when_raced(ui):
    """Two runs fired at the same instant with ONE confirm: exactly one spends."""
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    body = dict(LIVE, confirm_id=pf["confirm_id"])
    out = []
    barrier = threading.Barrier(4)

    def fire():
        barrier.wait()
        out.append(ui.post("/api/trips/trip_b_europe/run", body).status)

    ts = [threading.Thread(target=fire) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out).count(200) == 1, out
    assert ui.net.search_calls == 4


def test_C4_a_confirm_expires_after_five_minutes(ui, monkeypatch):
    from src.ui import engine as eng

    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    real = time.monotonic
    monkeypatch.setattr(eng.time, "monotonic", lambda: real() + 301)
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409 and r.json()["error"] == "confirm_stale"
    assert ui.net.calls == []


def test_C5_a_wallet_change_after_confirming_makes_the_confirm_stale(ui):
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    w = ui.post("/api/wallet", {"balances": {"UR": "100000"}, "cards": ["Chase Sapphire Preferred"]})
    assert w.status == 200 and not w.json().get("error"), w.text
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409 and r.json()["error"] == "confirm_stale"
    # the wallet FILE edited under a confirm too
    pf = ui.post("/api/search/preflight", SEARCH).json()
    ui.post("/api/wallet", {"balances": {"UR": "160000"}, "cards": ["Chase Sapphire Preferred"]})
    r = ui.post("/api/search/run", dict(SEARCH, confirm_id=pf["confirm_id"]))
    assert r.status == 409
    assert ui.net.calls == []


def test_C6_a_wallet_file_edit_after_confirming_makes_the_confirm_stale(ui):
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    write_wallet(ui.wallet_file, balances={"UR": 5000})
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409 and r.json()["error"] == "confirm_stale"
    assert ui.net.calls == []


def test_C7_offline_and_replay_never_touch_the_transport(ui):
    pf, r = ui.run_trip("trip_b_europe", OFFLINE)
    assert r.status == 200 and r.json()["exit_code"] == 0
    assert ui.net.calls == []
    # one LIVE run to write a manifest, then replay it with the network DOWN
    pf, r = ui.run_trip("trip_b_europe", LIVE)
    assert r.status == 200
    n = len(ui.net.calls)
    ui.net.impl = g.Refused()
    body = {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}}
    pf, r = ui.run_trip("trip_b_europe", body)
    assert r.status == 200, r.text
    assert r.json()["headline"]["qualifier"].startswith("(snapshot mh_")
    assert len(ui.net.calls) == n


# ---------------------------------------------------------- the max-calls rule


def test_C8_the_stated_maximum_holds_with_paging_and_every_lookup(ui):
    """3 pages per search, rows on every page, --trips all at cap 10."""
    ui.net.impl = _paging_impl(pages=3)
    body = {"mode": "live", "options": {**TRANSFER, "trips": "all", "trips_cap": 10}}
    pf, r = ui.run_trip("trip_b_europe", body)
    assert r.status == 200, r.text
    run = r.json()
    total = len(ui.net.calls)
    assert total <= pf["max_calls"], (total, pf["max_calls"])
    assert ui.net.search_calls == 12
    assert ui.net.trips_calls <= 10


def test_C9_calls_this_run_equals_what_the_transport_saw_with_paging(ui):
    ui.net.impl = _paging_impl(pages=3)
    body = {"mode": "live", "options": {**TRANSFER, "trips": "all", "trips_cap": 10}}
    pf, r = ui.run_trip("trip_b_europe", body)
    run = r.json()
    assert run["calls"]["this_run"] == {"search": ui.net.search_calls, "trips": ui.net.trips_calls}
    assert run["calls"]["since_launch"] == len(ui.net.calls)


def test_C10_search_stated_maximum_and_counter_with_paging(ui):
    ui.net.impl = _paging_impl(pages=3)
    pf, r = ui.search(SEARCH)
    assert r.status == 200, r.text
    run = r.json()
    assert ui.net.search_calls == 3 <= pf["max_calls"] == 25
    assert run["calls"]["this_run"]["search"] == 3
    assert run["calls"]["since_launch"] == 3
    assert "followed pagination to the end: 3 pages fetched" in (run["coverage_note"] or "")


def test_C11_a_search_that_never_stops_paging_spends_at_most_the_stated_25(ui):
    def endless(url, **kw):
        n = int((kw.get("params") or {}).get("cursor") or 1)
        return response({"data": [], "hasMore": True, "cursor": str(n + 1)})

    ui.net.impl = endless
    pf, r = ui.search(SEARCH)
    run = r.json()
    assert ui.net.search_calls == 25 == pf["max_calls"]
    assert run["coverage_incomplete"] is True
    # an INCOMPLETE empty answer must not read as a clean "no awards" finding
    assert run["state"] != "no_awards" or run["coverage_incomplete"]


def test_C12_trips_cap_one_sends_at_most_one_lookup(ui):
    ui.net.impl = _paging_impl(pages=1)
    body = {"mode": "live", "options": {**TRANSFER, "trips": "all", "trips_cap": 1}}
    pf, r = ui.run_trip("trip_b_europe", body)
    assert r.status == 200
    assert ui.net.trips_calls <= 1
    assert pf["max_calls"] == 4 * 25 + 1


def test_C13_trips_off_sends_no_lookup_and_says_so_in_the_max(ui):
    body = {"mode": "live", "options": {**TRANSFER, "trips": "off", "trips_cap": 10}}
    pf, r = ui.run_trip("trip_b_europe", body)
    assert pf["max_calls"] == 100 and pf["breakdown"]["lookup_cap"] == 0
    assert ui.net.trips_calls == 0


# ------------------------------------------------------------- concurrency


def test_C14_two_live_runs_at_once_one_409_and_only_one_spends(ui):
    gate = threading.Event()
    stub = g.Stub()

    def slow(url, **kw):
        gate.wait(10)
        return stub(url, **kw)

    ui.net.impl = slow
    pf1 = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    pf2 = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    res = {}

    def fire(k, pf):
        res[k] = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))

    t1 = threading.Thread(target=fire, args=("a", pf1))
    t1.start()
    time.sleep(0.5)
    t2 = threading.Thread(target=fire, args=("b", pf2))
    t2.start()
    t2.join(5)
    gate.set()
    t1.join(30)
    assert res["b"].status == 409 and res["b"].json()["error"] == "busy"
    assert res["a"].status == 200
    assert ui.net.search_calls == 4


def test_C15_a_run_refused_as_busy_spends_nothing_and_its_confirm_is_gone(ui):
    """By design (engine.redeem_confirm): single use WHATEVER the outcome. The
    page re-preflights on every Run press, so this costs a second dialog, not a
    call. Asserted as designed."""
    engine = ui.engine
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    engine._lock.acquire()
    try:
        r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    finally:
        engine._lock.release()
    assert r.status == 409 and r.json()["error"] == "busy"
    r2 = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r2.status == 409 and r2.json()["error"] == "confirm_required"
    assert ui.net.calls == []


def test_C16_offline_run_while_a_live_run_is_in_flight_is_refused_not_interleaved(ui):
    gate = threading.Event()
    stub = g.Stub()

    def slow(url, **kw):
        gate.wait(10)
        return stub(url, **kw)

    ui.net.impl = slow
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    res = {}
    t1 = threading.Thread(target=lambda: res.__setitem__(
        "a", ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))))
    t1.start()
    time.sleep(0.5)
    res["b"] = ui.post("/api/trips/trip_b_europe/run", OFFLINE)
    res["c"] = ui.post("/api/wallet", {"balances": {"UR": "1"}, "cards": []})
    gate.set()
    t1.join(30)
    assert res["b"].status == 409
    # a wallet edit in the middle of a LIVE run must not change that run
    run = res["a"].json()
    assert "UR: 160,000" in " ".join(run["context"]["wallet_lines"]), run["context"]["wallet_lines"]


# ------------------------------------------------------------- the counter


def test_C17_since_launch_survives_many_runs_and_matches_the_transport(ui):
    for _ in range(3):
        ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))
        ui.search(SEARCH)
    st = ui.get("/api/state").json()
    assert st["calls"]["since_launch"] == len(ui.net.calls)


def test_C18_the_counter_after_midnight_is_not_labelled_since_launch(ui, monkeypatch):
    """The class counter resets at the date change. After midnight the UI's
    'N since launch' is really 'since midnight' - a smaller number than spent."""
    from datetime import date as real_date

    from src import seats_client

    ui.run_trip("trip_b_europe", LIVE)
    before = ui.get("/api/state").json()["calls"]["since_launch"]
    assert before == len(ui.net.calls) > 0

    class Tomorrow(real_date):
        @classmethod
        def today(cls):
            return real_date.today().fromordinal(real_date.today().toordinal() + 1)

    monkeypatch.setattr(seats_client, "date", Tomorrow)
    after = ui.get("/api/state").json()["calls"]["since_launch"]
    assert after >= before, (
        f"'since launch' fell from {before} to {after} at midnight with no restart")


def test_C19_editing_the_fixture_after_confirming_makes_the_confirm_stale(ui):
    """The confirm is bound to the fixture's BYTES: if the trip changed, the
    maximum he agreed to no longer describes the run."""
    path = ui.trips_dir / "trip_b_europe.json"
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    text = path.read_text()
    path.write_text(text.replace("Europe multi-city", "Europe multi-city "))
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409 and r.json()["error"] == "confirm_stale", r.text[:200]
    assert ui.net.calls == []


def test_C20_a_confirm_for_one_trip_is_not_a_confirm_for_another(ui):
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    r = ui.post("/api/trips/trip_a_mry_nyc/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409, r.status
    assert ui.net.calls == []


def test_C21_changing_the_options_after_confirming_makes_the_confirm_stale(ui):
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    other = {"mode": "live", "options": {**LIVE["options"], "trips_cap": 50},
             "confirm_id": pf["confirm_id"]}
    r = ui.post("/api/trips/trip_b_europe/run", other)
    assert r.status == 409 and r.json()["error"] == "confirm_stale"
    assert ui.net.calls == []


def test_C22_flex_days_and_a_paging_stub_still_fit_inside_the_stated_maximum(ui):
    """--flex-days widens each leg's window. If it also multiplied the number of
    SEARCHES, the stated maximum would be an understatement."""
    ui.net.impl = _paging_impl(pages=3)
    body = {"mode": "live", "options": {**TRANSFER, "trips": "all", "trips_cap": 50,
                                        "flex_days": 7}}
    pf, r = ui.run_trip("trip_b_europe", body)
    assert r.status == 200, r.text[:300]
    assert len(ui.net.calls) <= pf["max_calls"], (len(ui.net.calls), pf["max_calls"])
    run = r.json()
    assert run["calls"]["this_run"]["search"] == ui.net.search_calls
