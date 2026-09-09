"""Acceptance tests: verify optimizer beats or ties human answers on synthetic trips."""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from src.models import Award, DateRange, Trip
from src.optimizer import optimize
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient


@pytest.fixture
def ratio_manager():
    """Create a RatioManager with test data."""
    ratios_path = Path(__file__).parent.parent / "data" / "ratios.csv"
    bonuses_path = Path(__file__).parent.parent / "data" / "bonuses.csv"
    return RatioManager(ratios_path, bonuses_path)


@pytest.fixture
def fixtures_dir():
    """Get the path to the fixtures directory."""
    return Path(__file__).parent / "fixtures" / "trips"


def load_trip(trip_file: Path) -> Trip:
    """Load a trip from a JSON fixture."""
    with open(trip_file, "r") as f:
        data = json.load(f)

    date_range = DateRange(
        from_date=date.fromisoformat(data["date_range"]["from"]),
        to_date=date.fromisoformat(data["date_range"]["to"]),
    )

    return Trip(
        origin=data["origin"],
        destination=data["destination"],
        date_range=date_range,
        balances=data["balances"],
        cards_held=data["cards_held"],
        num_passengers=data.get("num_passengers", 1),
        description=data.get("description", ""),
    )


def load_answer(answer_file: Path) -> Dict:
    """Load the expected answer from a JSON fixture."""
    with open(answer_file, "r") as f:
        return json.load(f)


def mock_awards_for_trip(trip_id: str) -> List[Award]:
    """Generate mock awards for each trip."""
    if trip_id == "trip_001":
        # Transatlantic to London: return various programs
        return [
            Award(
                date=date(2026, 5, 3),
                program="United",
                award_type="J",
                cost=120000,
                cash_component=200,
                airline="UA",
                route="SFO-LHR",
                seats_available=2,
            ),
            Award(
                date=date(2026, 5, 4),
                program="United",
                award_type="J",
                cost=120000,
                cash_component=200,
                airline="UA",
                route="SFO-LHR",
                seats_available=1,
            ),
            Award(
                date=date(2026, 5, 5),
                program="Flying Blue",
                award_type="J",
                cost=90000,
                cash_component=150,
                airline="AF",
                route="SFO-LHR",
                seats_available=2,
            ),
        ]
    elif trip_id == "trip_002":
        # Domestic first class: return United first
        return [
            Award(
                date=date(2026, 6, 10),
                program="United",
                award_type="F",
                cost=120000,
                cash_component=50,
                airline="UA",
                route="SFO-JFK",
                seats_available=1,
            ),
            Award(
                date=date(2026, 6, 12),
                program="MR",
                award_type="F",
                cost=140000,
                cash_component=75,
                airline="AA",
                route="SFO-JFK",
                seats_available=1,
            ),
        ]
    return []


@patch("src.optimizer.SeatsClient.search")
def test_trip_001_acceptance(mock_search, ratio_manager, fixtures_dir):
    """Acceptance test for trip 001."""
    trip_file = fixtures_dir / "trip_001.json"
    answer_file = fixtures_dir / "trip_001_answer.json"

    trip = load_trip(trip_file)
    expected = load_answer(answer_file)

    # Mock Seats.aero search
    mock_search.return_value = mock_awards_for_trip("trip_001")

    # Run optimizer
    seats_client = SeatsClient(api_key="test_key")
    results = optimize(trip, seats_client, ratio_manager, max_results=5)

    # Verify results
    assert len(results) > 0, "Optimizer returned no results"

    # Check that top result is valid
    top_result = results[0]
    assert top_result.award is not None
    assert top_result.points_cost > 0

    # Compare to human answer
    human_points_cost = expected["human_booking"]["points_cost"]
    optimizer_points_cost = top_result.points_cost

    # Margin: (optimizer - human) / human * 100
    margin_pct = (optimizer_points_cost - human_points_cost) / human_points_cost * 100

    # Log results
    print(f"\nTrip 001 Results:")
    print(f"  Human cost: {human_points_cost} points")
    print(f"  Optimizer cost: {optimizer_points_cost} points")
    print(f"  Margin: {margin_pct:.2f}%")

    # Acceptance criteria: margin should be < 10% (optimizer is close to human)
    assert margin_pct < 10, f"Optimizer margin too high: {margin_pct:.2f}%"


@patch("src.optimizer.SeatsClient.search")
def test_trip_002_acceptance(mock_search, ratio_manager, fixtures_dir):
    """Acceptance test for trip 002."""
    trip_file = fixtures_dir / "trip_002.json"
    answer_file = fixtures_dir / "trip_002_answer.json"

    trip = load_trip(trip_file)
    expected = load_answer(answer_file)

    # Mock Seats.aero search
    mock_search.return_value = mock_awards_for_trip("trip_002")

    # Run optimizer
    seats_client = SeatsClient(api_key="test_key")
    results = optimize(trip, seats_client, ratio_manager, max_results=5)

    # Verify results
    assert len(results) > 0, "Optimizer returned no results"

    # Check that top result is valid
    top_result = results[0]
    assert top_result.award is not None
    assert top_result.points_cost > 0

    # Compare to human answer
    human_points_cost = expected["human_booking"]["points_cost"]
    optimizer_points_cost = top_result.points_cost

    # Margin: (optimizer - human) / human * 100
    margin_pct = (optimizer_points_cost - human_points_cost) / human_points_cost * 100

    # Log results
    print(f"\nTrip 002 Results:")
    print(f"  Human cost: {human_points_cost} points")
    print(f"  Optimizer cost: {optimizer_points_cost} points")
    print(f"  Margin: {margin_pct:.2f}%")

    # Acceptance criteria: margin should be < 10%
    assert margin_pct < 10, f"Optimizer margin too high: {margin_pct:.2f}%"


def test_all_fixtures_exist(fixtures_dir):
    """Verify all fixture files exist."""
    required_files = [
        "trip_001.json",
        "trip_001_answer.json",
        "trip_002.json",
        "trip_002_answer.json",
    ]
    for filename in required_files:
        filepath = fixtures_dir / filename
        assert filepath.exists(), f"Missing fixture: {filepath}"


# ---------------------------------------------------------------------------
# Real-trip acceptance: the two January 2027 trips Tsuki captured.
#
# These use NO Seats.aero call. Every points number in them is a Google Flights
# badge, which is an unverified estimate, not confirmed award space.
# ---------------------------------------------------------------------------

from src.config import convert_to_usd  # noqa: E402
from src.optimizer import evaluate_trip, trip_totals  # noqa: E402
from src.trip_loader import load_trip_fixture  # noqa: E402

CSR = "Chase Sapphire Reserve"


TRANSFER_DATE = date(2026, 9, 15)


def _run(fixtures_dir, ratio_manager, filename, balance=None):
    fixture = load_trip_fixture(fixtures_dir / filename)
    results = evaluate_trip(
        legs=fixture.legs,
        user_balances={"UR": balance},
        user_cards=[CSR],
        ratios_manager=ratio_manager,
        transfer_date=TRANSFER_DATE,
        today=date(2026, 9, 8),
    )
    return fixture, results, trip_totals(results)


def test_trip_a_real(fixtures_dir, ratio_manager):
    """TRIP A: cash wins both legs. A 0% result is a real answer."""
    fixture, results, totals = _run(fixtures_dir, ratio_manager, "trip_a_mry_nyc.json")

    assert len(results) == 2
    assert all(r.verdict.startswith("cash") for r in results), (
        "every Trip A leg should come out as pay-cash on this data"
    )
    assert totals["beat_cash_pct"] == 0.0
    assert totals["all_cash_usd"] == pytest.approx(1200.26, abs=0.01)

    # A2 is Marriott: a UR partner, but no award price was captured.
    a2 = results[1]
    assert a2.verdict == "cash (points unpriced)"
    assert a2.break_even_points == 83326


def test_trip_b_real(fixtures_dir, ratio_manager):
    """
    TRIP B, RE-SCORED IN v1. CHANGED FROM v0 - this is the headline result.

    v0 asserted that points won B1, B3 and B4 and that the trip beat cash by 16%.
    Two of those three wins were bought with a British Airways surcharge silently
    modelled as $0, one of them departing Heathrow. v1 refuses to score an unknown
    surcharge, so:

      B1  the BA option is no longer scoreable. The best option that CAN be scored
          is Aeroplan at a confirmed $0 (50,000 points = $500), which loses to
          $395 cash. VERDICT FLIPS from POINTS to CASH.
      B3  KLM 4,000 points would beat $76 cash at a $0 surcharge, but the
          surcharge is unknown, so the verdict is withheld with a break-even.
          VERDICT FLIPS from POINTS to CASH (SURCHARGE UNKNOWN).
      B4  the BA option is unscoreable, but United's zero surcharge is a PROGRAM
          POLICY and resolves confirmed, so 27,600 United points = $276 beats
          $482 cash. STILL POINTS - but won by a different program, for a reason
          the tool can defend.
    """
    fixture, results, totals = _run(fixtures_dir, ratio_manager, "trip_b_europe.json")

    assert len(results) == 7
    by_id = {r.leg.id: r for r in results}

    # B1: BA's false $0 is gone. Aeroplan is scoreable and loses to cash.
    assert by_id["B1"].verdict == "cash"
    assert by_id["B1"].best_points.program == "Air Canada Aeroplan"
    assert by_id["B1"].surcharge.is_known and by_id["B1"].surcharge.amount_point == 0.0
    assert any("UNKNOWN (not $0)" in w for w in by_id["B1"].warnings), (
        "the unscoreable BA candidate must still be reported, not silently dropped"
    )

    # B2: cash wins whatever the surcharge turns out to be - a surcharge can only
    # ADD to the points side, and points already lose at $0.
    assert by_id["B2"].verdict == "cash"
    assert "can only ADD" in by_id["B2"].verdict_reason

    # B3: the unknown actually decides this one, so the verdict is withheld.
    assert by_id["B3"].verdict == "cash (surcharge unknown)"
    assert by_id["B3"].break_even_surcharge_usd == pytest.approx(36.0)

    # B4: won on United's program-policy zero, not on a BA guess.
    assert by_id["B4"].verdict == "points"
    assert by_id["B4"].best_points.program == "United MileagePlus"
    assert by_id["B4"].surcharge.amount_point == 0.0
    assert by_id["B4"].surcharge.is_known

    # Three hotels, none of them UR partners.
    for leg_id in ("B5", "B6", "B7"):
        assert by_id[leg_id].verdict == "cash (no points path)"
        assert not by_id[leg_id].has_points_path

    assert totals["legs_without_points_path"] == 3
    assert totals["legs_where_points_win"] == 1
    assert totals["legs_surcharge_unknown"] == 1


def test_trip_b_headline_is_a_range_not_the_bare_16_percent(fixtures_dir, ratio_manager):
    """
    v0's single number is replaced by an honest interval.

    The LOW end is what the tool can actually defend. The HIGH end is what you
    would get if every unknown BA surcharge turned out to be zero - which is
    precisely the assumption that produced v0's 16%, so the old headline sits at
    the top of the range and cannot be quoted as the answer.
    """
    _, results, totals = _run(fixtures_dir, ratio_manager, "trip_b_europe.json")

    assert totals["headline_is_a_range"] is True
    # CHANGED AT v3 STEP 0. NOT LISTED IN THE PLAN'S SECTION 8, but a pure FX
    # effect and not a live-data effect: v3 makes no live call in this test and
    # the badge inputs are byte-identical to v2's.
    #
    # The GBP correction (1.2700 -> 1.3540) adds $42.22 to the Hilton London leg,
    # which sits ONLY in the all-cash denominator - no points path exists for a
    # Hilton stay under a UR-only wallet, so the numerator (the dollars the
    # optimizer saves) is unchanged at $202 point / $483 low. A bigger
    # denominator over the same savings is a smaller percentage:
    #     6.55% -> 6.46%   and   15.66% -> 15.45%
    # The range did NOT narrow because live data confirmed anything. It shrank
    # because the trip was always $42 more expensive than the tool said.
    assert totals["all_cash_usd"] == pytest.approx(3126.11, abs=0.02)
    assert totals["beat_cash_pct_low"] == pytest.approx(6.46, abs=0.02)
    assert totals["beat_cash_pct_high"] == pytest.approx(15.45, abs=0.02)
    assert totals["beat_cash_pct_low"] < 16 < totals["beat_cash_pct_high"] + 1
    # The defensible figure is roughly the ~7% the architect predicted.
    assert 5.0 < totals["beat_cash_pct_low"] < 8.0
    # The dollar savings - the part that has nothing to do with FX - is intact.
    assert totals["all_cash_usd"] - totals["optimized_usd"] == pytest.approx(
        202.0, abs=0.02
    )


def test_trip_b_amsterdam_city_tax_now_enters_the_total(fixtures_dir, ratio_manager):
    """
    CHANGED FROM v0. The EUR56.47 city tax was free text on the cash option and
    entered no total at all. It is now a MandatoryFee on the leg.
    """
    _, results, _ = _run(fixtures_dir, ratio_manager, "trip_b_europe.json")
    b6 = next(r for r in results if r.leg.id == "B6")
    assert b6.mandatory_fees_usd == pytest.approx(65.61, abs=0.02)
    assert b6.cash_total_score_usd == pytest.approx(700.71, abs=0.02)
    assert b6.cash_total_score_usd > convert_to_usd(546.55, "EUR")
    assert any(r.code == "MANDATORY_FEE" for r in b6.reasons)


def test_trip_b_london_hotel_now_rests_on_a_sourced_rate_that_still_needs_confirming(
    fixtures_dir, ratio_manager
):
    """
    CHANGED AT v3 STEP 0 (was
    test_trip_b_london_hotel_is_marked_as_resting_on_an_unverified_rate).
    NOT LISTED IN THE PLAN'S SECTION 8; a mechanical consequence of GBP gaining
    a source, and `evaluate_leg` was not touched to produce it.

    The claim "this total rests on a rate with NO SOURCE" is simply no longer
    true of B7, so asserting it would be asserting a falsehood. What must remain
    true - and does - is that the GBP figure is never presented as settled.
    """
    from src import config

    _, results, totals = _run(fixtures_dir, ratio_manager, "trip_b_europe.json")
    b7 = next(r for r in results if r.leg.id == "B7")
    assert b7.rests_on_placeholder_fx is False
    assert totals["rests_on_placeholder_fx"] is False
    assert b7.cash_usd == pytest.approx(680.57, abs=0.01)

    # The disclosure moved to the FX banner and did not evaporate.
    assert config.needs_confirmation("GBP") is True
    banner = " ".join(config.fx_report_lines(today=date(2026, 9, 8)))
    assert "CONFIRM BEFORE TRUSTING" in banner
    assert "GBP" in banner
    # And the fixture still flags this leg as the least reliable number in the run.
    assert any("least reliable" in f for f in b7.leg.data_flags)


def test_trip_b_flags_the_impossible_hotel_dates(fixtures_dir):
    """The Madrid/Amsterdam date clash must be reported, not silently fixed."""
    fixture = load_trip_fixture(fixtures_dir / "trip_b_europe.json")
    joined = " ".join(fixture.trip_level_flags)
    assert "HOTEL DATES DO NOT LINE UP" in joined
    assert "Jan 15-19" in joined

    madrid = next(l for l in fixture.legs if l.id == "B5")
    amsterdam = next(l for l in fixture.legs if l.id == "B6")
    # Dates are transcribed as captured - both Jan 15 - NOT corrected.
    assert madrid.date == amsterdam.date == date(2027, 1, 15)


def test_every_points_number_is_labelled_unverified(fixtures_dir):
    """No Seats.aero data exists. Every points figure must say so."""
    for name in ("trip_a_mry_nyc.json", "trip_b_europe.json"):
        fixture = load_trip_fixture(fixtures_dir / name)
        for leg in fixture.legs:
            for cand in leg.points_candidates:
                assert cand.source == "google_badge_unverified", (
                    f"{name} {leg.id}: points source not marked unverified"
                )
                assert cand.program_attribution_assumed


def test_balance_constraint_binds_when_supplied(fixtures_dir, ratio_manager):
    """
    With a tiny balance, the expensive points paths drop out.

    This is the only way the feasibility ceiling can be exercised on real data:
    Tsuki has not supplied a balance, so the default run is unconstrained.
    """
    _, unconstrained, _ = _run(fixtures_dir, ratio_manager, "trip_b_europe.json")
    _, constrained, _ = _run(fixtures_dir, ratio_manager, "trip_b_europe.json", balance=5000)

    # CHANGED FROM v0: `has_points_path` now means "a points path that can be
    # SCORED". Only B1 (Aeroplan) and B4 (United) have a candidate whose
    # surcharge resolves; B2 and B3 are fundable but unscoreable, so they are no
    # longer counted here. `funding_plan` below is what still counts fundability.
    assert sum(1 for r in unconstrained if r.has_points_path) == 2
    # 5,000 UR covers only the 4,000-point AMS-LHR leg - and that leg's surcharge
    # is unknown, so nothing is scoreable at this balance.
    assert sum(1 for r in constrained if r.has_points_path) == 0
    # The constraint still BINDS, which is the thing being tested: with 5,000 UR
    # the expensive legs lose their funding plan entirely.
    assert sum(1 for r in constrained if r.funding_plan is not None) == 1
    assert sum(1 for r in unconstrained if r.funding_plan is not None) == 4
