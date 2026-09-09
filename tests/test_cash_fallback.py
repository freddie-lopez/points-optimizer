"""Tests for the cash fallback option scored at 1 cent per point (Task 3)."""
from datetime import date
from pathlib import Path

import pytest

from src.config import CASH_VALUATION_CPP, cash_to_points_equivalent, convert_to_usd
from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_leg, evaluate_trip, trip_totals
from src.ratio_manager import RatioManager

DATA = Path(__file__).parent.parent / "data"
CSR = "Chase Sapphire Reserve"


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def make_leg(cash=367.0, currency="USD", points=None, program="JetBlue TrueBlue", **kw):
    """
    A leg with a KNOWN, CAPTURED $0 surcharge.

    CHANGED IN v1. These tests are about the 1cpp yardstick and the cash-vs-points
    verdict logic; the surcharge is not their subject. In v0 a candidate with no
    surcharge information was silently scored at $0, so the helper did not need to
    say anything. In v1 silence means UNKNOWN and an unknown surcharge makes the
    points side unscoreable - so the helper now DECLARES the zero it always
    assumed. `surcharge_captured=True` is exactly the "genuinely surcharge-free
    fare, read off a real page" case, which is a real known zero and is
    deliberately distinguishable from an unknown.

    The unknown-surcharge behaviour has its own tests below.
    """
    return Leg(
        id=kw.get("id", "L1"),
        kind=kw.get("kind", "flight"),
        description=kw.get("description", "test leg"),
        date=kw.get("leg_date", date(2027, 1, 15)),
        cash_options=[CashOption(label="cash", amount=cash, currency=currency)],
        points_candidates=(
            [
                PointsCandidate(
                    label="pts",
                    program=program,
                    points=points,
                    cash_surcharge=kw.get("cash_surcharge", 0.0),
                    surcharge_captured=kw.get("surcharge_captured", True),
                    operating_carrier=kw.get("operating_carrier", ""),
                    carrier_source=kw.get("carrier_source", "unknown"),
                    cabin=kw.get("cabin", "Y"),
                )
            ]
            if points is not None
            else []
        ),
        unpriced_partner_programs=kw.get("unpriced_partner_programs", []),
        origin=kw.get("origin", ""),
        destination=kw.get("destination", ""),
        mandatory_fees=kw.get("mandatory_fees", []),
        nights=kw.get("nights", 0),
    )


# ---------------------------------------------------------------------------
# The 1cpp yardstick
# ---------------------------------------------------------------------------


def test_one_cent_per_point_conversion():
    """The brief's worked example: $367 cash = 36,700 points-equivalent."""
    assert CASH_VALUATION_CPP == 0.01
    assert cash_to_points_equivalent(367.00) == 36700


def test_every_leg_gets_a_cash_option(rm):
    """Even a leg with a great points path still reports its cash number."""
    leg = make_leg(cash=482.0, points=20000, program="British Airways Executive Club")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert r.best_cash is not None
    assert r.cash_usd == 482.0
    assert r.cash_as_points_equivalent == 48200


def test_points_and_cash_reported_separately_and_combined(rm):
    leg = make_leg(cash=482.0, points=20000, program="British Airways Executive Club")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    # separately
    assert r.points_required == 20000
    assert r.cash_usd == 482.0
    # combined score, both sides, same yardstick
    assert r.points_total_score_usd == pytest.approx(200.00)
    assert r.cash_total_score_usd == pytest.approx(482.00)


# ---------------------------------------------------------------------------
# "Pay cash" is a correct answer, not a failure
# ---------------------------------------------------------------------------


def test_cash_wins_is_reported_as_pay_cash(rm):
    """MAD-AMS: $44 cash beats 7,500 Iberia points (= $75 at 1cpp)."""
    leg = make_leg(cash=44.0, points=7500, program="Club Iberia Plus")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert r.verdict == "cash"
    assert r.has_points_path, "a points path exists - cash simply wins"
    assert "Do NOT burn points here" in r.verdict_reason


def test_points_win_is_reported_as_points(rm):
    leg = make_leg(cash=482.0, points=20000, program="British Airways Executive Club")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert r.verdict == "points"
    assert r.margin_usd == pytest.approx(282.00)
    assert r.margin_pct == pytest.approx(58.51, abs=0.01)


def test_non_partner_leg_has_no_points_path(rm):
    """easyJet / Accor / NH / Hilton: cash only, by construction."""
    leg = make_leg(cash=76.0, points=None)
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert not r.has_points_path
    assert r.verdict == "cash (no points path)"


def test_hilton_is_not_a_ur_partner(rm):
    leg = make_leg(cash=638.35, points=60000, program="Hilton Honors")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert not r.has_points_path
    assert any("not a UR transfer partner" in w or "not a" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# Missing award price -> break-even, never an invented number
# ---------------------------------------------------------------------------


def test_partner_without_award_price_reports_break_even(rm):
    """Marriott IS a partner but no award price was captured."""
    leg = make_leg(cash=833.26, points=None, unpriced_partner_programs=["Marriott Bonvoy"])
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm)
    assert r.verdict == "cash (points unpriced)"
    assert r.break_even_points == 83326
    assert r.break_even_programs == ["Marriott Bonvoy"]
    assert not r.has_points_path


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------


def test_foreign_currency_converted_via_config():
    """
    CHANGED AT v3 STEP 0 (plan section 8, item 2). The GBP rate went from an
    unsourced 1.2700 to a sourced 1.3540, so the Hilton London leg moves from
    $638.35 to $680.57 - a correction of $42.22 upward on the all-cash side of
    Trip B. The EUR assertion is untouched, which is the point: only the rate
    that gained a source moved.
    """
    assert convert_to_usd(643.57, "EUR") == pytest.approx(747.83, abs=0.01)
    assert convert_to_usd(502.64, "GBP") == pytest.approx(680.57, abs=0.01)
    # NEW at v3: CAD exists at all, so the one real tax figure the project owns
    # (CAD 44.60 on the captured SFO-MAD Aeroplan award) is finally scoreable.
    assert convert_to_usd(44.60, "CAD") == pytest.approx(32.36, abs=0.01)


def test_unknown_currency_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="No FX rate configured"):
        convert_to_usd(100.0, "JPY")


# ---------------------------------------------------------------------------
# Trip totals
# ---------------------------------------------------------------------------


def test_trip_totals_beat_cash_percentage(rm):
    legs = [
        make_leg(id="X1", cash=482.0, points=20000, program="British Airways Executive Club"),
        make_leg(id="X2", cash=44.0, points=7500, program="Club Iberia Plus"),
    ]
    results = evaluate_trip(legs, {"UR": None}, [CSR], rm)
    totals = trip_totals(results)

    assert totals["all_cash_usd"] == pytest.approx(526.00)
    # Points win leg 1 ($200), cash wins leg 2 ($44).
    assert totals["optimized_usd"] == pytest.approx(244.00)
    assert totals["savings_usd"] == pytest.approx(282.00)
    assert totals["beat_cash_pct"] == pytest.approx(53.61, abs=0.01)
    assert totals["legs_where_points_win"] == 1


def test_valuation_cpp_changes_the_verdict(rm):
    """
    The verdict is a function of the yardstick, and the yardstick is a choice.

    At 1cpp the 7,500-point Iberia option loses to $44 cash. At 0.5cpp it wins.
    """
    leg = make_leg(cash=44.0, points=7500, program="Club Iberia Plus")
    assert evaluate_leg(leg, {"UR": None}, [CSR], rm, valuation_cpp=0.01).verdict == "cash"
    assert evaluate_leg(leg, {"UR": None}, [CSR], rm, valuation_cpp=0.005).verdict == "points"


def test_blocked_path_is_not_reported_as_missing_partner(rm):
    """
    A constraint rejection must not be reported as "not a UR partner".

    Iberia IS a partner; under a strict no-stranding policy the 7,500-point path
    is rejected because 8,000 must be transferred. Reporting that as a missing
    partner would blame the data for a policy decision.
    """
    leg = make_leg(cash=44.0, points=7500, program="Club Iberia Plus")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm, max_stranded_points=0)
    assert not r.has_points_path
    assert r.verdict == "cash (points blocked)"
    assert "not a missing partner" in r.verdict_reason
    assert r.verdict != "cash (no points path)"


def test_genuinely_absent_partner_still_reports_no_path(rm):
    leg = make_leg(cash=638.35, points=60000, program="Hilton Honors")
    r = evaluate_leg(leg, {"UR": None}, [CSR], rm, max_stranded_points=0)
    assert r.verdict == "cash (no points path)"


# ---------------------------------------------------------------------------
# H-4: an absent capture is not a claim about partnerships
# ---------------------------------------------------------------------------


def test_a_leg_with_no_captured_award_price_makes_no_claim_about_partners(tmp_path, rm):
    """
    A --new-trip fixture carries NO points_candidates by design and --offline is
    a documented mode for it. It used to report "No UR transfer partner covers
    this leg" on SFO->MAD - the exact route Trip B's own B1 scores an Aeroplan
    path on, in the same binary.
    """
    from src import trip_builder
    from src.surcharge import default_table
    from src.trip_loader import load_trip_fixture
    from src.wallet import Wallet

    path = trip_builder.new_trip_from_flags(
        "h4_unit", ["SFO:MAD:2027-01-15:395"], [], directory=tmp_path
    )
    fixture = load_trip_fixture(path)
    results = evaluate_trip(
        legs=fixture.legs,
        ratios_manager=rm,
        wallet=Wallet({"UR": 160000}),
        transfer_date=date(2026, 9, 15),
        surcharges=default_table(),
    )
    leg = results[0]
    assert leg.verdict == "cash (no points path)"
    assert leg.points_absence == "never_priced"
    assert "No UR transfer partner covers this leg" not in leg.verdict_reason
    assert "NO AWARD PRICE WAS CAPTURED" in leg.verdict_reason
    assert "never reached" in leg.verdict_reason


def test_a_leg_whose_recorded_programs_reach_nothing_still_says_so(rm):
    """The real finding keeps its words: candidates existed and none was usable."""
    from src.surcharge import default_table
    from src.wallet import Wallet

    leg = Leg(
        id="L1", kind="flight", description="", date=date(2027, 1, 15),
        origin="SFO", destination="MAD", travelers=1,
        points_candidates=[
            PointsCandidate(label="x", program="Not A Real Program", points=1000)
        ],
    )
    results = evaluate_trip(
        legs=[leg],
        ratios_manager=rm,
        wallet=Wallet({"UR": 160000}),
        transfer_date=date(2026, 9, 15),
        surcharges=default_table(),
    )
    assert results[0].points_absence == "no_partner"
    assert "No UR transfer partner covers this leg" in results[0].verdict_reason
