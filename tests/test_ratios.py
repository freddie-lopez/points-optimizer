"""Test ratio versioning and card dependencies."""
from datetime import date
from pathlib import Path

import pytest

from src.ratio_manager import RatioManager


DATA = Path(__file__).parent.parent / "data"
CSR = "Chase Sapphire Reserve"
CSP = "Chase Sapphire Preferred"


@pytest.fixture
def ratio_manager():
    """RatioManager over the real production ratio table."""
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def ratio_manager_with_bonus():
    """
    RatioManager with a SYNTHETIC bonus table.

    The production bonuses.csv is deliberately empty: no current Chase UR
    transfer bonus has been verified, and inventing one would corrupt every
    result. Bonus mechanics are tested against a clearly-labelled fixture.
    """
    return RatioManager(
        DATA / "ratios.csv",
        Path(__file__).parent / "fixtures" / "bonuses_test.csv",
        DATA / "programs.yaml",
    )


def test_chase_ur_hyatt_sapphire_before_oct1(ratio_manager):
    """UR → Hyatt on Sapphire Reserve should be 1:1 before Oct 1."""
    ratio = ratio_manager.query_ratio(
        "UR", "World of Hyatt", date(2026, 9, 30), card=CSR
    )
    assert ratio is not None
    assert ratio.ratio_numerator == 1
    assert ratio.ratio_denominator == 1


def test_chase_ur_hyatt_other_before_oct1(ratio_manager):
    """
    UR -> Hyatt on Sapphire Preferred IS 1:1 before Oct 1 2026.

    This test previously asserted None. That was not correct behaviour, it was
    a hole in the data: ratios.csv had no pre-2026-10-01 row for a non-Reserve
    card, so the lookup found nothing. The row has been added, and the 4:3
    change is now correctly scoped to Oct 1 2026 onwards.
    """
    ratio = ratio_manager.query_ratio(
        "UR", "World of Hyatt", date(2026, 9, 30), card=CSP
    )
    assert ratio is not None, "pre-change lookup for a non-Reserve card must resolve"
    assert (ratio.ratio_numerator, ratio.ratio_denominator) == (1, 1)


def test_chase_ur_hyatt_other_after_oct1(ratio_manager):
    """UR → Hyatt on other cards should be 4:3 after Oct 1."""
    ratio = ratio_manager.query_ratio(
        "UR", "World of Hyatt", date(2026, 10, 1), card=CSP
    )
    assert ratio is not None
    assert ratio.ratio_numerator == 4
    assert ratio.ratio_denominator == 3


def test_chase_ur_hyatt_sapphire_after_oct1(ratio_manager):
    """UR → Hyatt on Sapphire Reserve should still be 1:1 after Oct 1."""
    ratio = ratio_manager.query_ratio(
        "UR", "World of Hyatt", date(2026, 10, 1), card=CSR
    )
    assert ratio is not None
    assert ratio.ratio_numerator == 1
    assert ratio.ratio_denominator == 1


def test_chase_ur_united_constant(ratio_manager):
    """UR → United should be 1:1 regardless of card or date."""
    ratio_may = ratio_manager.query_ratio("UR", "United MileagePlus", date(2026, 5, 1), card="Any")
    ratio_oct = ratio_manager.query_ratio(
        "UR", "United MileagePlus", date(2026, 10, 1), card="Any"
    )
    assert ratio_may is not None and ratio_may.ratio_numerator == 1
    assert ratio_oct is not None and ratio_oct.ratio_numerator == 1


def test_apply_ratio(ratio_manager):
    """Apply a ratio to a points amount."""
    ratio = ratio_manager.query_ratio(
        "UR", "World of Hyatt", date(2026, 10, 1), card=CSP
    )
    assert ratio is not None
    # 4:3 ratio: 120 points → 90 points
    result = ratio.apply(120)
    assert result == 90


def test_apply_ratio_with_bonus(ratio_manager_with_bonus):
    """Apply ratio and bonus together (synthetic bonus fixture)."""
    points, ratio_str = ratio_manager_with_bonus.apply_ratio_and_bonus(
        100000, "UR", "Air France-KLM Flying Blue", date(2026, 9, 20)
    )
    # 1:1 ratio, then 25% bonus (Q4 promo active Sep 15 - Oct 31)
    # 100000 * 1 * 1.25 = 125000
    assert points == 125000
    assert ratio_str == "1:1"


def test_apply_ratio_no_bonus(ratio_manager):
    """Apply ratio without bonus."""
    points, ratio_str = ratio_manager.apply_ratio_and_bonus(
        100000, "UR", "United MileagePlus", date(2026, 5, 1)
    )
    # 1:1 ratio, no bonus active
    assert points == 100000
    assert ratio_str == "1:1"


def test_ratio_not_found(ratio_manager):
    """Raise error if ratio doesn't exist."""
    with pytest.raises(ValueError):
        ratio_manager.apply_ratio_and_bonus(
            100000, "Unknown", "Also Unknown", date(2026, 5, 1)
        )
