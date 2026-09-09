"""
Adversarial probe E: --require-all-live end to end, cache under stress,
and what an API failure looks like when a badge is standing in for it.
"""
import copy
import errno
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src.formatter import print_leg_results, print_live_leg_detail, print_trip_totals
from src.live_trip import (
    LiveOptions, annotate_live_verdicts, apply_live, provenance_counts,
)
from src.models import CashOption, DateRange, Leg, LiveQueryState, PointsCandidate
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)
UR = 160_000


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


def _mock_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _render(fn, *a, **kw):
    buf = StringIO()
    fn(*a, console=Console(file=buf, width=200, no_color=True), **kw)
    return buf.getvalue()


def _row(day):
    r = copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])
    r["Date"] = str(day)
    r["ParsedDate"] = f"{day}T00:00:00Z"
    return r


class _F:
    def __init__(self, legs):
        self.legs = legs


def _flight(lid, o, d, day, cash, cands=None):
    return Leg(id=lid, kind="flight", description=f"{o}->{d}", date=day,
               origin=o, destination=d,
               cash_options=[CashOption(label="c", amount=cash, currency="USD")],
               points_candidates=cands or [])


# =========================================================================
# E1. --require-all-live does not withhold a margin that contains a dead leg
# =========================================================================


@patch("src.seats_client.requests.get")
def test_E1_require_all_live_passes_a_run_with_a_dead_leg(mock_get, client, rm):
    legs = [
        _flight("L1", "SFO", "MAD", date(2027, 1, 15), 395.0),
        _flight("L2", "MAD", "AMS", date(2027, 1, 19), 44.0),
    ]

    def side_effect(url, **kw):
        if kw["params"]["origin_airport"] == "MAD":
            raise requests.exceptions.ConnectionError("connection reset by peer")
        return _mock_response({"data": [_row(date(2027, 1, 15))]})

    mock_get.side_effect = side_effect
    apply_live(_F(legs), client, LiveOptions(live=True))
    results = annotate_live_verdicts(
        evaluate_trip(legs, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                      transfer_date=TRANSFER_DATE, show_alternatives=False))
    totals = trip_totals(results)

    # main.py:435  withheld = require_all_live and margin_provenance != "live"
    withheld = True and totals.get("margin_provenance") != "live"
    print("margin_provenance:", totals["margin_provenance"])
    print("legs_api_error   :", totals["legs_api_error_ids"])
    print("would --require-all-live withhold?", withheld)
    out = _render(print_trip_totals, totals)
    print(out)
    assert totals["legs_api_error"] == 1
    assert withheld is False, "the margin is published despite a leg that never answered"
    assert "WITHHELD" not in out


@patch("src.seats_client.requests.get")
def test_E2_an_api_failure_hidden_behind_a_badge_is_never_stated_in_the_verdict(
    mock_get, client, rm
):
    """
    A leg that fails but HAS a badge keeps the badge, and annotate_live_verdicts
    returns early -- so the API failure never reaches the leg's verdict or its
    warnings. Only the provenance cell hints at it.
    """
    badge = PointsCandidate(label="badge 22.5k", program="British Airways Executive Club",
                            points=22500, source="google_badge_unverified",
                            operating_carrier="BA", carrier_source="assumed", cabin="Y")
    legs = [_flight("L1", "SFO", "MAD", date(2027, 1, 15), 395.0, [badge])]
    mock_get.side_effect = requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")
    apply_live(_F(legs), client, LiveOptions(live=True))
    results = annotate_live_verdicts(
        evaluate_trip(legs, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                      transfer_date=TRANSFER_DATE, show_alternatives=False))
    r = results[0]
    print("state    :", r.leg.live_outcome.state)
    print("provenance:", r.leg.points_provenance)
    print("verdict  :", r.verdict)
    print("reason   :", r.verdict_reason)
    print("warnings containing the error:",
          [w for w in r.warnings if "timeout" in w.lower() or "COULD NOT BE REACHED" in w])
    assert r.leg.live_outcome.state is LiveQueryState.API_ERROR
    assert not any("COULD NOT BE REACHED" in w for w in r.warnings)
    assert "timeout" not in r.verdict_reason.lower()
    detail = _render(print_live_leg_detail, results)
    print(detail)
    assert "COULD NOT BE REACHED" in detail, "at least the detail block says it"


# =========================================================================
# E3. Cache under stress
# =========================================================================


# E3/E3b removed: the sandbox runs as root, so chmod cannot simulate a
# write failure. The real disk-full case is proven in test_adv_f.py::test_F4.


def test_E4_two_concurrent_runs_write_the_same_snapshot_name(tmp_path):
    """
    Snapshot names are minute-resolution. Two DIFFERENT responses for the same
    leg inside one minute are disambiguated by content hash -- good. But the
    MANIFEST is appended with a plain open(..., 'a'), unlocked, so interleaved
    writers can corrupt a row.
    """
    import inspect
    from src import response_cache
    src = inspect.getsource(response_cache.ResponseCache._append_manifest)
    print(src)
    assert 'open(path, "a")' in src
    assert "lock" not in src.lower()
    assert "fcntl" not in src

    c = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    t = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    c.put("k1", {"origin_airport": "SFO", "destination_airport": "MAD",
                 "start_date": "2027-01-15"}, [{"data": [1]}],
          meta={"leg_id": "B1"}, now=t)
    c.put("k2", {"origin_airport": "SFO", "destination_airport": "MAD",
                 "start_date": "2027-01-15"}, [{"data": [2]}],
          meta={"leg_id": "B1"}, now=t)
    names = sorted(p.name for p in c.snapshots())
    print("snapshots:", names)
    assert len(names) == 2


def test_E5_annotate_manifest_can_stamp_the_WRONG_row(tmp_path):
    """
    annotate_manifest walks BACKWARDS for the last row naming the snapshot with
    an unfilled '| - | - |'. A duplicate (identical) re-fetch reuses the same
    snapshot name, so a later run's state can be written onto an earlier run's
    unfilled row.
    """
    c = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    req = {"origin_airport": "SFO", "destination_airport": "MAD",
           "start_date": "2027-01-15", "end_date": "2027-01-15"}
    t1 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 9, 18, 0, 0, tzinfo=timezone.utc)
    w1 = c.put("k", req, [{"data": []}], meta={"leg_id": "B1"}, now=t1)
    snap = w1.meta["snapshot"]
    # a byte-identical re-fetch six hours later: same snapshot file, new row
    c.put("k", req, [{"data": []}], meta={"leg_id": "B1"}, now=t2)
    print(c.manifest_path.read_text())
    ok = c.annotate_manifest(snap, "ok", 3)
    print("--- after annotating once ---")
    print(c.manifest_path.read_text())
    rows = [l for l in c.manifest_path.read_text().splitlines() if l.startswith("| 2026")]
    assert ok
    # The LAST row got the state; the FIRST run's row stays "- | -" forever.
    assert "| 3 | ok |" in rows[-1]
    assert "| - | - |" in rows[0]


# =========================================================================
# E6. A cache hit re-answers with a pagination note that is no longer true
# =========================================================================


@patch("src.seats_client.requests.get")
def test_E6_an_incomplete_response_is_cached_and_replayed(mock_get, client, tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    mock_get.return_value = _mock_response(
        {"data": [_row(date(2027, 1, 15))], "hasMore": True})   # more, no cursor
    dr = DateRange(date(2027, 1, 15), date(2027, 1, 15))
    client.search("SFO", "MAD", dr, cache=cache)
    print("note run 1:", client.last_pagination_note)
    c2 = SeatsClient(api_key="test_key")
    c2.clear_cache()
    c2.search("SFO", "MAD", dr, cache=cache)
    print("note run 2:", c2.last_pagination_note)
    assert "hasMore" in client.last_pagination_note
    assert "Original coverage note" in c2.last_pagination_note
