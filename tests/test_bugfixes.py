"""
Regression tests for the four bugs in docs/test-reports/points-optimizer.md.

These are the tests that actually exercise the stranded-points constraint. The
real trip data cannot exercise it, because Tsuki has not supplied a UR balance
and the optimizer therefore runs unconstrained.
"""
from datetime import date
from pathlib import Path

import pytest

from src.models import Ratio
from src.optimizer import (
    find_transfer_paths,
    max_unavoidable_stranding,
    size_transfer,
    validate_balances,
)
from src.ratio_manager import RatioManager

DATA = Path(__file__).parent.parent / "data"
CSR = "Chase Sapphire Reserve"
CSP = "Chase Sapphire Preferred"


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def ratio(num, den):
    return Ratio("UR", "X", "all", num, den, date(2000, 1, 1), date(2099, 12, 31))


# ---------------------------------------------------------------------------
# BUG 3 (CRITICAL): stranded points are calculated and the constraint fires
# ---------------------------------------------------------------------------


def test_stranded_points_are_actually_calculated():
    """A 1:1 award of 22,500 needs a 23,000 transfer, stranding 500."""
    source, delivered, stranded, remainder = size_transfer(22500, ratio(1, 1), 1000)
    assert source == 23000
    assert delivered == 23000
    assert stranded == 500, "stranded_points must be computed, not hardcoded to 0"


def test_stranded_points_zero_when_award_lands_on_increment():
    source, delivered, stranded, _ = size_transfer(20000, ratio(1, 1), 1000)
    assert (source, delivered, stranded) == (20000, 20000, 0)


def test_stranding_at_four_to_three_ratio():
    """90,000 Hyatt at 4:3 needs exactly 120,000 UR - no stranding."""
    source, delivered, stranded, _ = size_transfer(90000, ratio(4, 3), 1000)
    assert source == 120000
    assert delivered == 90000
    assert stranded == 0


def test_stranding_constraint_rejects_over_transfer(rm):
    """
    The tester's core case: do not dump a whole balance into a program for a
    small award. With a strict ceiling of 0, an award that cannot be funded
    exactly must be rejected rather than silently accepted.
    """
    paths = find_transfer_paths(
        user_balances={"UR": 100000},
        user_cards=[CSR],
        target_program="IHG One Rewards",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=22500,
        transfer_increment=1000,
        max_stranded_points=0,
    )
    assert paths == [], "strict no-stranding must reject a 500-point overshoot"


def test_stranding_constraint_allows_unavoidable_rounding(rm):
    """The same case passes under the default ceiling, which is the increment."""
    paths = find_transfer_paths(
        user_balances={"UR": 100000},
        user_cards=[CSR],
        target_program="IHG One Rewards",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=22500,
        transfer_increment=1000,
    )
    assert len(paths) == 1
    assert paths[0].stranded_points == 500
    assert paths[0].feasible


def test_transfers_are_sized_to_demand_not_to_balance(rm):
    """
    The root cause of the old bug: the optimizer moved the ENTIRE balance.
    250,000 UR for a 22,500-point award must transfer 23,000, not 250,000.
    """
    paths = find_transfer_paths(
        user_balances={"UR": 250000},
        user_cards=[CSR],
        target_program="British Airways Executive Club",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=22500,
    )
    assert paths[0].total_transferred == 23000
    assert paths[0].total_transferred < 250000


def test_infeasible_when_balance_too_small(rm):
    paths = find_transfer_paths(
        user_balances={"UR": 5000},
        user_cards=[CSR],
        target_program="British Airways Executive Club",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=22500,
    )
    assert paths == []


def test_infeasible_reason_is_reported(rm):
    paths = find_transfer_paths(
        user_balances={"UR": 5000},
        user_cards=[CSR],
        target_program="British Airways Executive Club",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=22500,
        include_infeasible=True,
    )
    assert len(paths) == 1
    assert not paths[0].feasible
    assert "balance is 5,000" in paths[0].infeasible_reason


def test_unconstrained_balance_has_no_ceiling(rm):
    """A balance of None means unconstrained - the default when Tsuki gives none."""
    paths = find_transfer_paths(
        user_balances={"UR": None},
        user_cards=[CSR],
        target_program="British Airways Executive Club",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=500000,
    )
    assert len(paths) == 1
    assert paths[0].feasible
    assert paths[0].total_transferred == 500000


def test_max_unavoidable_stranding_tracks_the_ratio():
    assert max_unavoidable_stranding(ratio(1, 1), 1000) == 999
    assert max_unavoidable_stranding(ratio(4, 3), 1000) == 749


# ---------------------------------------------------------------------------
# BUG 2: negative balances are rejected with a clear error
# ---------------------------------------------------------------------------


def test_negative_balance_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        validate_balances({"UR": -100000})


def test_negative_balance_rejected_in_find_transfer_paths(rm):
    with pytest.raises(ValueError, match="cannot be negative"):
        find_transfer_paths(
            user_balances={"UR": -100000},
            user_cards=[CSR],
            target_program="United MileagePlus",
            check_date=date(2027, 1, 15),
            ratios_manager=rm,
            points_needed=20000,
        )


def test_zero_balance_is_allowed_and_contributes_nothing(rm):
    validate_balances({"UR": 0})
    paths = find_transfer_paths(
        user_balances={"UR": 0},
        user_cards=[CSR],
        target_program="United MileagePlus",
        check_date=date(2027, 1, 15),
        ratios_manager=rm,
        points_needed=20000,
    )
    assert paths == []


def test_non_numeric_balance_rejected():
    with pytest.raises(ValueError, match="not a number"):
        validate_balances({"UR": "lots"})


# ---------------------------------------------------------------------------
# BUG 1: all cards are evaluated and the BEST ratio wins, order-independently
# ---------------------------------------------------------------------------


def test_multiple_cards_picks_best_ratio_regardless_of_order(rm):
    """
    After 2026-10-01, Sapphire Preferred is 4:3 to Hyatt but Sapphire Reserve
    stays 1:1. Holding both must give the 1:1 result either way round.
    """
    after = date(2026, 10, 1)
    forward = find_transfer_paths(
        user_balances={"UR": 200000},
        user_cards=[CSP, CSR],
        target_program="World of Hyatt",
        check_date=after,
        ratios_manager=rm,
        points_needed=90000,
    )
    reverse = find_transfer_paths(
        user_balances={"UR": 200000},
        user_cards=[CSR, CSP],
        target_program="World of Hyatt",
        check_date=after,
        ratios_manager=rm,
        points_needed=90000,
    )
    assert forward[0].total_transferred == 90000, "should use the 1:1 Reserve ratio"
    assert forward[0].total_transferred == reverse[0].total_transferred
    assert forward[0].transfers[0].card_used == CSR
    assert reverse[0].transfers[0].card_used == CSR


def test_all_cards_are_evaluated_not_just_the_first(rm):
    """Both cards must produce a path, so the optimizer can choose."""
    pairs = rm.best_ratio_across_cards(
        "UR", "World of Hyatt", date(2026, 10, 1), [CSP, CSR]
    )
    ratios_found = {(c, r.as_string) for c, r in pairs}
    assert (CSP, "4:3") in ratios_found
    assert (CSR, "1:1") in ratios_found


def test_best_ratio_is_first(rm):
    pairs = rm.best_ratio_across_cards(
        "UR", "World of Hyatt", date(2026, 10, 1), [CSP, CSR]
    )
    assert pairs[0][0] == CSR


# ---------------------------------------------------------------------------
# BUG 4: card matching is exact, not substring
# ---------------------------------------------------------------------------


def test_card_matching_is_exact_not_substring():
    r = Ratio(
        "UR", "World of Hyatt", "Sapphire", 1, 1, date(2000, 1, 1), date(2099, 12, 31)
    )
    # Under the old `in` logic this was True for both cards.
    assert not r.is_valid_on(date(2027, 1, 1), "Chase Sapphire Reserve")
    assert not r.is_valid_on(date(2027, 1, 1), "Chase Sapphire Preferred")
    assert r.is_valid_on(date(2027, 1, 1), "Sapphire")


def test_partial_card_name_does_not_match(rm):
    """'Sapphire Reserve' is no longer enough - the full card name is required."""
    assert rm.query_ratio("UR", "World of Hyatt", date(2027, 1, 1), "Sapphire Reserve") is None
    assert rm.query_ratio("UR", "World of Hyatt", date(2027, 1, 1), CSR) is not None


def test_other_dependency_is_not_hardcoded_to_one_card():
    """'other' now means 'any card not explicitly named for this pair'."""
    r = Ratio("UR", "X", "other", 4, 3, date(2000, 1, 1), date(2099, 12, 31))
    named = frozenset({CSR, CSP})
    assert not r.is_valid_on(date(2027, 1, 1), CSR, named)
    assert not r.is_valid_on(date(2027, 1, 1), CSP, named)
    assert r.is_valid_on(date(2027, 1, 1), "Chase Freedom Flex", named)
