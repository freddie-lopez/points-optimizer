"""
Step 10: hotels.

Two things are being tested and they are different:

  * ARITHMETIC - per-night x nights, and the transfer maths on top of it.
  * MANDATORY FEES - the actual bug. A destination fee or a city tax is owed on a
    POINTS stay too. v0 stored those as free text on the cash option, so they
    entered no total at all and were invisible on the points side.

And one thing is being tested by its ABSENCE: v1 does not find hotel awards.
"""
from datetime import date
from pathlib import Path

import pytest

from src import hotels
from src.hotels import HotelDataError
from src.models import CashOption, Leg, MandatoryFee, PointsCandidate
from src.optimizer import evaluate_leg
from src.ratio_manager import RatioManager
from src.wallet import Wallet

DATA = Path(__file__).parent.parent / "data"
CSP = "Chase Sapphire Preferred"
CSR = "Chase Sapphire Reserve"


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def stay(nights=3, per_night=15000, cash=900.0, fees=(), program="World of Hyatt",
         points_total=None, travelers=1):
    return Leg(
        id="H1",
        kind="hotel",
        description="a hotel stay",
        date=date(2027, 1, 15),
        nights=nights,
        travelers=travelers,
        cash_options=[CashOption(label="cash", amount=cash)],
        points_candidates=(
            [
                PointsCandidate(
                    label="award",
                    program=program,
                    points=points_total or 0,
                    points_per_night=per_night,
                    source=hotels.MANUAL_CAPTURE,
                    # NOT surcharge_captured: a hotel leg has no operating
                    # carrier, so the hotel branch of resolve_leg_surcharge
                    # should be what answers here.
                )
            ]
            if (per_night or points_total)
            else []
        ),
        mandatory_fees=list(fees),
    )


def wallet(balance=None, cards=(CSP,)):
    return Wallet(balances={"UR": balance}, cards=list(cards))


# ---------------------------------------------------------------------------
# Per-night arithmetic
# ---------------------------------------------------------------------------


def test_three_nights_at_fifteen_thousand_costs_forty_five_thousand(rm):
    r = evaluate_leg(
        stay(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.points_path.transfers[0].points_delivered == 45000
    assert r.points_required == 45000


def test_the_same_stay_costs_sixty_thousand_ur_after_the_hyatt_cliff(rm):
    """Step 10's acceptance number: 45,000 Hyatt at 4:3 needs 60,000 UR."""
    r = evaluate_leg(
        stay(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 10, 2), today=date(2026, 10, 2),
    )
    assert r.points_required == 60000
    assert r.points_path.transfers[0].ratio == "4:3"


def test_a_captured_total_is_used_as_is(rm):
    leg = stay(per_night=None, points_total=40000)
    r = evaluate_leg(
        leg, ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.points_required == 40000


def test_per_night_price_with_zero_nights_is_an_error_not_a_free_stay():
    cand = PointsCandidate(label="x", program="World of Hyatt", points=0,
                           points_per_night=15000)
    with pytest.raises(HotelDataError, match="no night count"):
        hotels.award_points_for(cand, 0)


def test_no_award_price_reports_break_even_never_a_guess(rm):
    leg = stay(per_night=None, points_total=None)
    leg.unpriced_partner_programs = ["World of Hyatt"]
    r = evaluate_leg(
        leg, ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.verdict == "cash (points unpriced)"
    assert r.break_even_points == 90000
    assert not r.has_points_path


# ---------------------------------------------------------------------------
# MANDATORY FEES - the actual bug
# ---------------------------------------------------------------------------


DEST_FEE = MandatoryFee(
    label="destination fee", amount=30.0, currency="USD", per="night",
    payable_on_points=True,
)


def test_a_nightly_fee_multiplies_by_nights():
    assert DEST_FEE.total_for(3) == 90.0
    assert hotels.fees_usd([DEST_FEE], 3) == 90.0


def test_a_per_stay_fee_does_not_multiply():
    fee = MandatoryFee(label="city tax", amount=56.47, currency="EUR", per="stay")
    assert fee.total_for(4) == 56.47


def test_a_person_night_fee_multiplies_by_both():
    fee = MandatoryFee(label="tourist tax", amount=4.0, per="person_night")
    assert fee.total_for(4, travelers=2) == 32.0


def test_the_fee_hits_the_cash_total_and_the_points_total(rm):
    """
    THE BUG FIX. The fee is on the LEG, so it lands on BOTH sides.

    v0 put it on CashOption as a string and it entered neither.
    """
    leg = stay(fees=[DEST_FEE], cash=900.0)
    r = evaluate_leg(
        leg, ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.mandatory_fees_usd == 90.0
    assert r.cash_total_score_usd == pytest.approx(990.0)
    # 45,000 points at 1cpp = $450, plus the same $90 that is owed either way.
    assert r.points_total_score_usd == pytest.approx(540.0)


def test_a_fee_that_is_decisive_actually_flips_the_verdict(rm):
    """
    Pinned because it is the whole point of MODELLING fees rather than noting them.

    $440 cash vs 45,000 points ($450 at 1cpp): cash wins by $10. Add a $200 fee
    that is waived on award stays and the verdict reverses. v0 could not express
    this at all - the fee was a string on the cash option and entered no total.
    """
    waived_on_awards = MandatoryFee(
        label="resort fee, waived on award stays", amount=200.0, per="stay",
        payable_on_points=False,
    )
    base = evaluate_leg(
        stay(cash=440.0), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert base.verdict == "cash"

    flipped = evaluate_leg(
        stay(cash=440.0, fees=[waived_on_awards]), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert flipped.verdict == "points", "the fee is decisive and must flip the verdict"
    assert flipped.cash_total_score_usd == pytest.approx(640.0)
    assert flipped.points_total_score_usd == pytest.approx(450.0)


def test_a_fee_owed_on_both_sides_shifts_both_totals_and_does_not_flip(rm):
    """The complement: a fee you owe either way is not a reason to switch."""
    both_sides = MandatoryFee(
        label="destination fee", amount=200.0, per="stay", payable_on_points=True,
    )
    base = evaluate_leg(
        stay(cash=460.0), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    with_fee = evaluate_leg(
        stay(cash=460.0, fees=[both_sides]), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert base.verdict == with_fee.verdict == "points"
    assert with_fee.cash_total_score_usd == pytest.approx(660.0)
    assert with_fee.points_total_score_usd == pytest.approx(650.0)
    assert with_fee.margin_usd == pytest.approx(base.margin_usd)


def test_a_points_side_fee_is_excluded_when_not_payable_on_points():
    fees = [
        MandatoryFee(label="a", amount=100.0, payable_on_points=True),
        MandatoryFee(label="b", amount=100.0, payable_on_points=False),
    ]
    assert hotels.fees_usd(fees, 1, points_side=False) == 200.0
    assert hotels.fees_usd(fees, 1, points_side=True) == 100.0


def test_a_foreign_currency_fee_is_converted(rm):
    fee = MandatoryFee(label="city tax", amount=56.47, currency="EUR", per="stay")
    assert hotels.fees_usd([fee], 4) == pytest.approx(65.62, abs=0.02)


def test_zero_night_stay_with_a_nightly_fee_costs_nothing():
    assert hotels.fees_usd([DEST_FEE], 0) == 0.0


# ---------------------------------------------------------------------------
# v1 does not FIND hotel awards
# ---------------------------------------------------------------------------


def test_manually_captured_hotel_price_carries_the_disclaimer(rm):
    r = evaluate_leg(
        stay(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    manual = [x for x in r.reasons if x.code == "MANUAL_CAPTURE"]
    assert manual, "every manually-supplied hotel price must be labelled as such"
    assert "does not search hotel awards" in manual[0].detail


def test_the_disclaimer_text_is_the_one_the_plan_specifies():
    assert hotels.HOTEL_DISCLAIMER == (
        "v1 does not search hotel awards - this price was supplied manually."
    )


def test_a_hotel_leg_never_receives_a_carrier_surcharge(rm):
    r = evaluate_leg(
        stay(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.surcharge.is_known
    assert r.surcharge.amount_point == 0.0
    assert "no carrier surcharge exists" in r.surcharge.matched_rule


# ---------------------------------------------------------------------------
# Award rules default OFF and need provenance to turn on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "program", ["World of Hyatt", "Marriott Bonvoy", "IHG One Rewards", "Wyndham Rewards"]
)
def test_every_award_rule_ships_off(rm, program):
    rules = rm.award_rules(program)
    assert rules, f"{program} has no award_rules block"
    for name in hotels.AWARD_RULE_NAMES:
        assert rules.get(name) is False, f"{program}.{name} is not off"
    assert hotels.enabled_award_rules(rules) == []


def test_production_award_rules_validate(rm):
    for program in ("World of Hyatt", "Marriott Bonvoy", "IHG One Rewards", "Wyndham Rewards"):
        assert hotels.validate_award_rules(program, rm.award_rules(program)) == []


def test_enabling_a_rule_without_a_source_fails_validation():
    with pytest.raises(HotelDataError, match="no provenance"):
        hotels.validate_award_rules("World of Hyatt", {"fifth_night_free": True})


def test_enabling_a_rule_without_a_verified_on_fails_validation():
    with pytest.raises(HotelDataError, match="without a source"):
        hotels.validate_award_rules(
            "World of Hyatt",
            {"fifth_night_free": {"enabled": True, "source": "somewhere"}},
        )


def test_a_properly_sourced_rule_validates_and_is_reported():
    warnings = hotels.validate_award_rules(
        "World of Hyatt",
        {"fifth_night_free": {
            "enabled": True, "source": "hyatt.com T&Cs", "verified_on": "2026-09-01",
        }},
    )
    assert len(warnings) == 1 and "ENABLED" in warnings[0]
