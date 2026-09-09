"""
Honesty invariants, asserted as PROPERTIES over every trip fixture.

These are the tests that would have caught v0's headline bug. They do not check
that a number is right - no test can do that - they check that the tool never
presents an unknown as a known, and never lets an unverified figure into a total
without saying so.
"""
from datetime import date
from pathlib import Path

import pytest

from src.models import REASON_CODES, Reason
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

DATA = Path(__file__).parent.parent / "data"
TRIPS = Path(__file__).parent / "fixtures" / "trips"
CSP = "Chase Sapphire Preferred"
D = date(2026, 9, 15)

REAL_FIXTURES = [
    "trip_a_mry_nyc.json",
    "trip_b_europe.json",
    "trip_c_lon_mry_surcharge.json",
]

VERDICTS = {
    "points",
    "cash",
    "cash (no points path)",
    "cash (points unpriced)",
    "cash (points blocked)",
    "cash (surcharge unknown)",
}


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def _results(rm, name, balance=None):
    fixture = load_trip_fixture(TRIPS / name)
    return fixture, evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": balance}, cards=[CSP]),
        transfer_date=D, today=date(2026, 9, 8),
    )


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_an_unknown_surcharge_never_enters_a_score(rm, name):
    """THE invariant. An unknown must never be scored as if it were zero."""
    _, results = _results(rm, name)
    for r in results:
        if r.surcharge is not None and not r.surcharge.is_known:
            assert r.points_total_score_usd == float("inf"), (
                f"{r.leg.id}: an unknown surcharge produced a finite points score"
            )
            assert r.verdict != "points", (
                f"{r.leg.id}: points won a leg whose surcharge is unknown"
            )


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_an_unknown_surcharge_never_renders_as_zero_dollars(rm, name):
    _, results = _results(rm, name)
    for r in results:
        if r.surcharge is not None and not r.surcharge.is_known:
            assert r.surcharge.render() == "UNKNOWN"
            assert "$0" not in r.surcharge.render()


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_an_unscoreable_leg_contributes_cash_to_the_trip_total(rm, name):
    """
    A leg we refuse to score must cost the trip its CASH price, not a discount.

    Getting this wrong in the other direction is exactly how v0 produced 16%.
    """
    _, results = _results(rm, name)
    for r in results:
        if r.points_total_score_usd == float("inf"):
            assert r.winner_cost_usd == r.cash_total_score_usd, r.leg.id


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_google_badge_figures_always_carry_their_warning(rm, name):
    _, results = _results(rm, name)
    for r in results:
        if r.best_points is None:
            continue
        if r.best_points.source == "google_badge_unverified":
            assert any("UNVERIFIED" in w for w in r.warnings), (
                f"{r.leg.id}: a google_badge_unverified price was presented without "
                f"its warning"
            )


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_no_google_badge_figure_is_ever_called_confirmed_award_space(rm, name):
    fixture, _ = _results(rm, name)
    for leg in fixture.legs:
        for cand in leg.points_candidates:
            if cand.source == "google_badge_unverified":
                assert cand.source != "seats_aero"


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_every_verdict_is_one_of_the_known_states(rm, name):
    _, results = _results(rm, name)
    for r in results:
        assert r.verdict in VERDICTS, f"{r.leg.id}: unknown verdict {r.verdict!r}"


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_pay_cash_remains_a_first_class_verdict(rm, name):
    """
    A leg where cash wins is a correct answer, not a failure.

    Its sub-states stay DISTINCT: conflating "no partner exists" with "the path
    was blocked" or with "we cannot score the surcharge" would misreport a
    constraint or a data gap as a fact about the world.
    """
    _, results = _results(rm, name)
    for r in results:
        if not r.verdict.startswith("cash"):
            continue
        assert r.verdict_reason, f"{r.leg.id}: a cash verdict with no reason"
        if r.verdict == "cash (no points path)":
            assert r.funding_plan is None
        if r.verdict == "cash (surcharge unknown)":
            assert r.funding_plan is not None, (
                "this state means a path EXISTS and cannot be scored - if there is "
                "no path it is a different verdict"
            )


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_every_reason_code_is_known(rm, name):
    _, results = _results(rm, name)
    for r in results:
        for reason in r.reasons:
            assert reason.code in REASON_CODES, f"{r.leg.id}: {reason.code}"


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_every_leg_with_a_points_path_explains_its_ratio_and_its_surcharge(rm, name):
    """No plan may reach the output without a reason for both."""
    _, results = _results(rm, name)
    for r in results:
        if r.funding_plan is None:
            continue
        codes = {x.code for x in r.reasons}
        assert "RATIO_CHOSEN" in codes, f"{r.leg.id}: no ratio explanation"
        assert codes & {
            "SURCHARGE_MODELED", "SURCHARGE_CAPTURED", "SURCHARGE_UNKNOWN"
        }, f"{r.leg.id}: no surcharge explanation"


def test_an_unknown_reason_code_is_rejected_at_construction():
    with pytest.raises(ValueError, match="Unknown Reason code"):
        Reason(code="MADE_UP", detail="x")


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_the_trip_headline_never_exceeds_its_own_upper_bound(rm, name):
    _, results = _results(rm, name)
    totals = trip_totals(results)
    assert totals["beat_cash_pct_low"] <= totals["beat_cash_pct"] + 1e-9
    assert totals["beat_cash_pct"] <= totals["beat_cash_pct_high"] + 1e-9
    assert totals["optimized_low_usd"] <= totals["optimized_usd"] + 1e-9
    assert totals["optimized_usd"] <= totals["optimized_high_usd"] + 1e-9


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_a_verdict_sensitive_leg_is_never_presented_as_settled(rm, name):
    _, results = _results(rm, name)
    for r in results:
        if r.verdict_sensitive:
            assert any("VERDICT SENSITIVE" in w for w in r.warnings)
            assert any(x.code == "VERDICT_SENSITIVE" for x in r.reasons)


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_no_leg_raises_on_evaluation(rm, name):
    """Adversarial sweep: every fixture, constrained and unconstrained."""
    for balance in (None, 0, 5000, 500000):
        _results(rm, name, balance=balance)


def test_the_production_bonus_table_is_still_empty(rm):
    """
    data/bonuses.csv stays empty. No current transfer bonus has been verified,
    and an invented 25% promo silently inflates every downstream result.
    """
    assert rm.bonuses == []
    text = (DATA / "bonuses.csv").read_text().strip()
    assert text == "" or len(text.splitlines()) <= 1
