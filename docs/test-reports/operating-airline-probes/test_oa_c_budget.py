"""
C. Budget, cap, 429 and ordering: trips never before a search, never over the
cap, never sent at a counter of 0, never cached on failure - and the coder's two
admitted gaps (cap-before-cache, 429-skips-cached) measured.
"""
from unittest.mock import MagicMock

import pytest
import requests

from conftest import (  # noqa: F401
    BASE, B4_VS, Stub, aid_for, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp

B2_FB = aid_for("B2", "flb")
B3_FB = aid_for("B3", "flb")


def fb_row(o, d, iso, rid):
    return row(source="flyingblue", cost="30000", taxes=9000, currency="EUR", airlines="AF, KL",
               origin=o, dest=d, iso=iso, rid=rid)


def three_rows(o, d, iso):
    if (o, d) == ("MAD", "AMS"):
        return [fb_row(o, d, iso, B2_FB)]
    if (o, d) == ("AMS", "LHR"):
        return [fb_row(o, d, iso, B3_FB)]
    if (o, d) == ("LHR", "SFO"):
        return [row(rid=B4_VS, iso=iso)]
    return None


def fb_itin(aid, o, d):
    return tp.payload([tp.trip([tp.segment("KL1704", o, d, 1, AvailabilityID=aid)],
                               availability_id=aid, source="flyingblue", cost=30000)])


TRIPS3 = {
    B2_FB: fb_itin(B2_FB, "MAD", "AMS"),
    B3_FB: fb_itin(B3_FB, "AMS", "LHR"),
    B4_VS: tp.payload([vs_itinerary(B4_VS)]),
}


def statuses(results):
    return {leg: (r.best_points.metal.status, r.best_points.metal.reason_code)
            for leg, r in results.items() if r.best_points is not None and r.best_points.metal is not None}


def test_every_search_is_sent_before_any_trips_request(capsys, monkeypatch):
    code, out, fl, stub = run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch,
                                  stub=Stub(rows_for=three_rows, trips=TRIPS3))
    kinds = ["t" if "/trips/" in u else "s" for u in stub.urls]
    assert "t" in kinds
    assert kinds.index("t") > max(i for i, k in enumerate(kinds) if k == "s")


def test_the_cap_bounds_requests_sent(capsys, monkeypatch):
    code, out, fl, stub = run_cli(BASE + ["--allow-badge-fallback", "--trips-cap", "1"], capsys, monkeypatch,
                                  stub=Stub(rows_for=three_rows, trips=TRIPS3))
    assert len(stub.trips_calls) == 1
    assert fl.count("per-run cap of 1 lookups was reached") >= 2


def test_nothing_is_sent_when_the_counter_is_at_zero_after_the_searches():
    stub = Stub(rows_for=three_rows, trips=TRIPS3)

    def spend_all(url, **kw):
        r = stub(url, **kw)
        if url.endswith("/search") and kw.get("params", {}).get("origin_airport") == "LHR":
            SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP
        return r

    results, totals, text, opts, fx = evaluate(spend_all)
    assert stub.trips_calls == []
    st = statuses(results)
    assert st["B4"] == (MetalStatus.NOT_LOOKED_UP, "BUDGET_EXHAUSTED")
    assert "B4" in totals["legs_metal_lookup_missing_ids"]


def test_one_call_left_sends_exactly_one_trips_request():
    stub = Stub(rows_for=three_rows, trips=TRIPS3)

    def leave_one(url, **kw):
        r = stub(url, **kw)
        if url.endswith("/search") and kw.get("params", {}).get("origin_airport") == "LHR":
            SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP - 1
        return r

    results, totals, text, opts, fx = evaluate(leave_one)
    assert len(stub.trips_calls) == 1
    st = statuses(results)
    assert st["B3"] == (MetalStatus.NOT_LOOKED_UP, "BUDGET_EXHAUSTED")
    assert st["B4"] == (MetalStatus.NOT_LOOKED_UP, "BUDGET_EXHAUSTED")


def test_a_429_stops_every_later_lookup_after_one_request():
    stub = Stub(rows_for=three_rows, trips={**TRIPS3, B2_FB: (429, None)})
    results, totals, text, opts, fx = evaluate(stub)
    assert len(stub.trips_calls) == 1
    st = statuses(results)
    assert st["B2"] == (MetalStatus.UNKNOWN, "HTTP_429")
    assert st["B3"][1] == st["B4"][1] == "RATE_LIMITED_EARLIER"
    assert "rate-limited an itinerary lookup" in flat(text) or opts.metal_report.rate_limited


@pytest.mark.parametrize("spec", [(404, None), (429, None), (500, None),
                                  lambda u, k: (_ for _ in ()).throw(requests.Timeout("t"))])
def test_a_failure_is_never_cached_or_archived(tmp_path, spec):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: spec})
    evaluate(stub, cache=cache)
    assert not list((tmp_path / "c" / "trips").glob("*.json")) if (tmp_path / "c" / "trips").exists() else True
    tdir = tmp_path / "s" / "trips_endpoint"
    assert not tdir.exists() or not list(tdir.glob("*.json"))


def test_two_cabins_of_one_row_cost_one_request():
    def rows_for(o, d, iso):
        if (o, d) == ("LHR", "SFO"):
            return [row(rid=B4_VS, iso=iso, extra_cabins={"W": {"cost": "40000", "taxes": 30000}})]
        return None

    both = tp.payload([vs_itinerary(B4_VS), vs_itinerary(B4_VS, trip_id="t2", cabin="premium", cost=40000)])
    stub = Stub(rows_for=rows_for, trips={B4_VS: both})
    results, totals, text, opts, fx = evaluate(stub)
    assert len(stub.trips_calls) == 1
    b4 = next(l for l in fx.legs if l.id == "B4")
    got = {c.cabin: c.metal.status for c in b4.points_candidates}
    assert got == {"J": MetalStatus.KNOWN, "W": MetalStatus.KNOWN}


def test_off_date_findings_are_never_looked_up():
    def rows_for(o, d, iso):
        if (o, d) == ("LHR", "SFO"):
            return [row(rid=B4_VS, iso="2027-01-29")]
        return None

    stub = Stub(rows_for=rows_for, trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    evaluate(stub, flex_days=3)
    assert stub.trips_calls == []


def test_all_mode_looks_up_a_program_policy_award_and_auto_does_not():
    stub = Stub(rows_for=lambda o, d, iso: None)
    evaluate(stub, trips_mode="auto")
    assert stub.trips_calls == []
    stub2 = Stub(rows_for=lambda o, d, iso: None)
    evaluate(stub2, trips_mode="all")
    assert len(stub2.trips_calls) == 4


# ---------------------------------------------------------------------------
# The admitted gaps, sized
# ---------------------------------------------------------------------------


def test_admitted_gap_a_free_cache_hit_after_the_cap_reads_not_looked_up(tmp_path):
    """
    ADMITTED (coder report, Known gaps): the cap is checked before the cache.
    B4's lookup is on disk from a first run; on the second run B2 and B3 are
    new, the cap of 1 is spent on B2, and B4 - which would cost nothing - reads
    CAP_REACHED. The run had the answer and printed NOT LOOKED UP.
    """
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    evaluate(Stub(rows_for=vs_b4_rows(), trips=TRIPS3), cache=cache)  # warms B4's lookup
    for p in (tmp_path / "c").glob("*.json"):  # forget the SEARCH responses, keep trips/
        p.unlink()
    stub = Stub(rows_for=three_rows, trips=TRIPS3)
    results, totals, text, opts, fx = evaluate(stub, cache=cache, trips_cap=1)
    assert len(stub.trips_calls) == 1
    assert statuses(results)["B4"][0] is MetalStatus.KNOWN, statuses(results)["B4"]


def test_admitted_gap_a_429_also_skips_lookups_the_cache_could_answer(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    evaluate(Stub(rows_for=vs_b4_rows(), trips=TRIPS3), cache=cache)
    for p in (tmp_path / "c").glob("*.json"):
        p.unlink()
    stub = Stub(rows_for=three_rows, trips={**TRIPS3, B2_FB: (429, None)})
    results, *_ = evaluate(stub, cache=cache)
    assert len(stub.trips_calls) == 1
    assert statuses(results)["B4"][0] is MetalStatus.KNOWN, statuses(results)["B4"]


def test_a_request_that_fails_outside_the_named_errors_still_counts_against_the_cap():
    """
    `state["sent"]` is only incremented for a RawTripsResult or a
    TripsLookupError. If anything else escapes `trips_raw` AFTER the request left
    (here: `.json()` raising TypeError), the budget counter moved but the cap
    counter did not, so the cap stops bounding what is sent.
    """
    def weird(url, kw):
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = TypeError("decoder exploded")
        return r

    stub = Stub(rows_for=three_rows, trips={B2_FB: weird, B3_FB: weird, B4_VS: weird})
    results, totals, text, opts, fx = evaluate(stub, trips_cap=1)
    assert len(stub.trips_calls) == 1, stub.trips_calls


def test_a_search_429_stops_trips_requests_in_the_same_run():
    """
    Seats.aero's limit shows up as HTTP 429 (plan finding 3). When a SEARCH in
    phase 1 was rate-limited, phase 2 still sends trips requests - the metal
    pass only watches its own responses for 429.
    """
    def rows_for(o, d, iso):
        return three_rows(o, d, iso)

    base = Stub(rows_for=rows_for, trips=TRIPS3)

    def side(url, **kw):
        if url.endswith("/search") and kw.get("params", {}).get("origin_airport") == "LHR":
            base.calls.append((url, {}, {}))
            r = MagicMock()
            r.status_code = 429
            r.raise_for_status.side_effect = requests.HTTPError("429 Client Error: Too Many Requests")
            return r
        return base(url, **kw)

    evaluate(side)
    assert base.trips_calls == [], base.trips_calls
