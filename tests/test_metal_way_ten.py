"""
Way (10) for the operating-airline lookup: METAL_LOOKUP_MISSING and METAL_UNKNOWN.

Both are leg-level unknowns discovered from source, declared COUNTED_AT_TRIP_LEVEL,
and carried in `trip_totals` on every run - zero when the lookup was never
engaged. The trip block names the legs.
"""
import subprocess
import sys
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import models
from src.formatter import print_trip_totals
from src.live_trip import LiveOptions, apply_live
from src.models import (
    COUNTED_AT_TRIP_LEVEL,
    TRIP_LEVEL_ANSWERS,
    MetalLookup,
    MetalStatus,
    check_trip_level_answers,
    leg_level_unknowns,
)
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet
from tests.test_metal_end_to_end import Stub

ROOT = Path(__file__).parent.parent
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
CODES = ("METAL_LOOKUP_MISSING", "METAL_UNKNOWN")


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv", ROOT / "data" / "programs.yaml"
    )


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def test_the_models_and_optimizer_import_under_python_O():
    proc = subprocess.run(
        [sys.executable, "-O", "-c", "import src.models, src.optimizer, src.live_trip"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_both_codes_are_discovered_and_counted():
    found = leg_level_unknowns()
    for code in CODES:
        assert code in found
        assert code in models.REASON_CODES
        assert TRIP_LEVEL_ANSWERS[code].treatment == COUNTED_AT_TRIP_LEVEL
    assert TRIP_LEVEL_ANSWERS["METAL_LOOKUP_MISSING"].totals_key == "legs_metal_lookup_missing"
    assert TRIP_LEVEL_ANSWERS["METAL_UNKNOWN"].totals_key == "legs_metal_unknown"


def _scored(rm, trips_mode="auto", b4_metal=None, stub=None):
    """Trip B through apply_live, optionally with B4's lookup replaced, then scored."""
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, trips_mode=trips_mode)
    with patch("src.seats_client.requests.get", side_effect=stub or Stub()):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    if b4_metal is not None:
        b4 = next(l for l in fixture.legs if l.id == "B4")
        for cand in b4.points_candidates:
            cand.metal = b4_metal
    results = evaluate_trip(
        fixture.legs,
        ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"]),
        transfer_date=date(2026, 9, 15),
        today=date(2026, 9, 8),
    )
    totals = trip_totals(results)
    check_trip_level_answers(results, totals)
    return results, totals


def _metal(status, **kw):
    base = dict(availability_id="abcDEF1234567890", row_carriers=("VS", "DL"))
    if status is MetalStatus.KNOWN:
        base.update(carriers=("VS",), matched_trips=1, provenance="seats_aero_trips")
    elif status is MetalStatus.AMBIGUOUS:
        base.update(
            carrier_sets=(("VS",), ("DL",)), possible_carriers=("VS", "DL"),
            matched_trips=2, provenance="seats_aero_trips",
        )
    else:
        base.update(possible_carriers=("VS", "DL"), detail="d")
    base.update(kw)
    return MetalLookup(status=status, **base)


SCENARIOS = {
    "known": (_metal(MetalStatus.KNOWN), 0, 0),
    "ambiguous": (_metal(MetalStatus.AMBIGUOUS), 0, 1),
    "unknown": (_metal(MetalStatus.UNKNOWN, reason_code="NO_MATCH"), 0, 1),
    "cap": (_metal(MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED"), 1, 0),
    "budget": (_metal(MetalStatus.NOT_LOOKED_UP, reason_code="BUDGET_EXHAUSTED"), 1, 0),
    "off": (_metal(MetalStatus.NOT_LOOKED_UP, reason_code="TRIPS_OFF"), 1, 0),
    "not_recorded": (
        _metal(MetalStatus.NOT_RECORDED, reason_code="NO_TRIPS_SNAPSHOT", on_replay=True), 1, 0
    ),
    "not_needed": (_metal(MetalStatus.NOT_LOOKED_UP, reason_code="NOT_NEEDED_POLICY"), 0, 0),
    "not_partner": (_metal(MetalStatus.NOT_LOOKED_UP, reason_code="NOT_DIRECT_PARTNER"), 0, 0),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_counts_across_every_status(rm, name):
    metal, missing, unknown = SCENARIOS[name]
    _, totals = _scored(rm, b4_metal=metal)
    assert totals["legs_metal_lookup_missing"] == missing
    assert totals["legs_metal_unknown"] == unknown
    assert totals["legs_metal_lookup_missing_ids"] == (["B4"] if missing else [])
    assert totals["legs_metal_unknown_ids"] == (["B4"] if unknown else [])


def test_the_keys_are_present_when_the_lookup_was_never_engaged(rm):
    _, totals = _scored(rm, trips_mode=None)
    for key in ("legs_metal_lookup_missing", "legs_metal_unknown"):
        assert totals[key] == 0
        assert totals[f"{key}_ids"] == []


def test_carrier_unknown_is_absent_when_the_metal_is_known(rm):
    results, _ = _scored(rm)
    b4 = next(r for r in results if r.leg.id == "B4")
    assert b4.best_points.metal.status is MetalStatus.KNOWN
    assert "CARRIER_UNKNOWN" not in [x.code for x in b4.reasons]


def test_carrier_unknown_stays_when_the_metal_is_not_known(rm):
    for metal in (
        _metal(MetalStatus.UNKNOWN, reason_code="NO_MATCH"),
        _metal(MetalStatus.AMBIGUOUS),
        _metal(MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED"),
    ):
        results, _ = _scored(rm, b4_metal=metal)
        b4 = next(r for r in results if r.leg.id == "B4")
        assert "CARRIER_UNKNOWN" in [x.code for x in b4.reasons], metal.status


def test_the_answers_are_honoured_on_the_flag_scenarios(rm):
    """check_trip_level_answers runs inside trip_totals; each scenario must pass it."""
    from tests.test_trips_flags import Stub as FlagStub

    for stub, kw in (
        (FlagStub(), {}),
        (FlagStub(trips_status={"B2": 429}), {}),
        (FlagStub(), {"trips_mode": "off"}),
        (FlagStub(), {"trips_mode": "all"}),
    ):
        fixture = load_trip_fixture(TRIP_B)
        opts = LiveOptions(
            live=True, cache=None, trip_id=fixture.id,
            trips_mode=kw.get("trips_mode", "auto"), trips_cap=1,
        )
        with patch("src.seats_client.requests.get", side_effect=stub):
            fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
        results = evaluate_trip(
            fixture.legs, ratios_manager=rm,
            wallet=Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"]),
            transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
        )
        totals = trip_totals(results)
        check_trip_level_answers(results, totals)
        SeatsClient.CACHE.clear()
        SeatsClient.CACHE_META.clear()
        SeatsClient.reset_call_budget()


def test_the_trip_block_names_the_legs(rm):
    from tests.test_trips_flags import Stub as FlagStub

    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, trips_mode="auto", trips_cap=1)
    with patch("src.seats_client.requests.get", side_effect=FlagStub()):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"]),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    totals = trip_totals(results)
    buf = StringIO()
    print_trip_totals(totals, label="Trip B", console=Console(file=buf, width=190))
    out = " ".join(buf.getvalue().split())
    assert totals["legs_metal_lookup_missing_ids"] == ["B3", "B4"]
    assert "operating airline NOT LOOKED UP on B3, B4" in out
    assert "Legs whose operating airline was NOT LOOKED UP" in out


def test_the_trip_block_names_not_recorded_and_not_known_separately(rm):
    _, totals = _scored(
        rm,
        b4_metal=_metal(MetalStatus.NOT_RECORDED, reason_code="NO_TRIPS_SNAPSHOT", on_replay=True),
    )
    buf = StringIO()
    print_trip_totals(totals, label="Trip B", console=Console(file=buf, width=190))
    out = " ".join(buf.getvalue().split())
    assert "operating airline NOT RECORDED on B4" in out
    assert "operating airline NOT LOOKED UP on" not in out
    _, totals = _scored(rm, b4_metal=_metal(MetalStatus.UNKNOWN, reason_code="NO_MATCH"))
    buf = StringIO()
    print_trip_totals(totals, label="Trip B", console=Console(file=buf, width=190))
    assert "operating airline NOT KNOWN on B4" in " ".join(buf.getvalue().split())


def test_nothing_is_printed_when_the_lookup_was_never_engaged(rm):
    _, totals = _scored(rm, trips_mode=None)
    buf = StringIO()
    print_trip_totals(totals, label="Trip B", console=Console(file=buf, width=190))
    assert "operating airline" not in buf.getvalue()
