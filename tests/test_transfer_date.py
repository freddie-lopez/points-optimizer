"""
Step 1: the transfer date is not the travel date.

v0 looked every ratio up on `leg.date`. That is wrong. You do not transfer points
in January because you fly in January - you transfer them when you choose to, and
the ratio that applies is the one in force ON THE TRANSFER DATE.

The live consequence: the World of Hyatt 4:3 change lands 2026-10-01 for
non-Reserve cards. Tsuki's real trips travel January 2027. v0 would have quoted
4:3 for a transfer he could still make at 1:1 today.
"""
from datetime import date
from pathlib import Path

import pytest

from src import config
from src.funding import best_plan
from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_leg
from src.ratio_manager import RatioManager
from src.wallet import Wallet

DATA = Path(__file__).parent.parent / "data"
CSP = "Chase Sapphire Preferred"
CSR = "Chase Sapphire Reserve"
CLIFF = date(2026, 10, 1)
TRAVEL = date(2027, 1, 15)


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def hyatt_leg(points=45000, cash=900.0):
    """A January 2027 Hyatt stay - the case v0 gets wrong."""
    return Leg(
        id="H1",
        kind="hotel",
        description="Hyatt, Jan 2027, 3 nights",
        date=TRAVEL,
        nights=3,
        cash_options=[CashOption(label="cash", amount=cash)],
        points_candidates=[
            PointsCandidate(
                label="Hyatt award",
                program="World of Hyatt",
                points=points,
                source="manual_capture",
                surcharge_captured=True,
            )
        ],
    )


def wallet(balance=None, cards=(CSP,)):
    return Wallet(balances={"UR": balance}, cards=list(cards))


# ---------------------------------------------------------------------------
# The cliff, from both sides
# ---------------------------------------------------------------------------


def test_transfer_before_the_cliff_quotes_one_to_one(rm):
    """
    Travelling January 2027, transferring 2026-09-15: the rate is 1:1.

    v0 would have used the TRAVEL date and quoted 4:3 - costing 60,000 UR for a
    45,000-point stay instead of 45,000.
    """
    r = evaluate_leg(
        hyatt_leg(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert r.points_path.transfers[0].ratio == "1:1"
    assert r.points_required == 45000


def test_transfer_after_the_cliff_quotes_four_to_three(rm):
    r = evaluate_leg(
        hyatt_leg(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 10, 2), today=date(2026, 10, 2),
    )
    assert r.points_path.transfers[0].ratio == "4:3"
    assert r.points_required == 60000


def test_the_travel_date_does_not_move_the_ratio(rm):
    """Same transfer date, two different travel dates -> identical ratio."""
    a = hyatt_leg()
    b = hyatt_leg()
    b.date = date(2026, 9, 20)
    for leg in (a, b):
        r = evaluate_leg(
            leg, ratios_manager=rm, wallet=wallet(),
            transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
        )
        assert r.points_required == 45000


def test_the_reserve_card_is_unaffected_by_the_cliff(rm):
    for transfer_date in (date(2026, 9, 15), date(2026, 10, 2)):
        r = evaluate_leg(
            hyatt_leg(), ratios_manager=rm, wallet=wallet(cards=(CSR,)),
            transfer_date=transfer_date, today=transfer_date,
        )
        assert r.points_required == 45000, f"failed at {transfer_date}"


# ---------------------------------------------------------------------------
# TRANSFER_DATE_WINDOW
# ---------------------------------------------------------------------------


def test_transfer_date_window_names_the_cliff(rm):
    r = evaluate_leg(
        hyatt_leg(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    windows = [x for x in r.reasons if x.code == "TRANSFER_DATE_WINDOW"]
    assert windows, "no TRANSFER_DATE_WINDOW reason emitted before the cliff"
    assert windows[0].data["cliff"] == "2026-10-01"
    assert windows[0].data["ratio_now"] == "1:1"
    assert windows[0].data["ratio_then"] == "4:3"
    assert "WHEN YOU TRANSFER" in windows[0].detail


def test_no_window_reason_for_a_ratio_that_never_changes(rm):
    """Airlines are 1:1 across the whole horizon - nothing to warn about."""
    leg = Leg(
        id="F1", kind="flight", description="flight", date=TRAVEL,
        origin="SFO", destination="JFK",
        cash_options=[CashOption(label="cash", amount=400.0)],
        points_candidates=[
            PointsCandidate(label="p", program="JetBlue TrueBlue", points=20000,
                            surcharge_captured=True)
        ],
    )
    r = evaluate_leg(
        leg, ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    assert not [x for x in r.reasons if x.code == "TRANSFER_DATE_WINDOW"]


def test_no_window_reason_once_the_cliff_has_passed(rm):
    r = evaluate_leg(
        hyatt_leg(), ratios_manager=rm, wallet=wallet(),
        transfer_date=date(2026, 10, 2), today=date(2026, 10, 2),
    )
    assert not [x for x in r.reasons if x.code == "TRANSFER_DATE_WINDOW"]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_transfer_date_defaults_to_today(rm):
    assert config.default_transfer_date() == date.today()
    r = evaluate_leg(hyatt_leg(), ratios_manager=rm, wallet=wallet())
    assert r.points_path is not None


def test_funding_uses_the_transfer_date_directly(rm):
    before = best_plan(wallet(), "World of Hyatt", 45000, date(2026, 9, 15), rm)
    after = best_plan(wallet(), "World of Hyatt", 45000, date(2026, 10, 2), rm)
    assert before.per_currency_spend["UR"] == 45000
    assert after.per_currency_spend["UR"] == 60000
