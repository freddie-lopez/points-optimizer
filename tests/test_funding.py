"""
Steps 4 and 5: per-currency valuation, funding plans, and the multi-currency
stranding rule.

The two tests that matter most here:

  test_single_currency_matches_v0_exactly   - the golden-output regression guard.
  test_two_currency_split_strands_one_block_not_two - the R2 trap. A naive
      implementation that rounds each currency up independently passes the whole
      v0 suite and fails only this.

All multi-currency ratio data is SYNTHETIC (tests/fixtures/ratios_multicurrency_test.csv).
data/ratios.csv still holds no MR/TY/C1/Bilt rows - see test_ratio_table.py.
"""
from datetime import date
from pathlib import Path

import pytest

from src import config, funding
from src.funding import all_plans, best_plan, residue_report, size_down, size_up
from src.optimizer import find_transfer_paths
from src.ratio_manager import RatioManager
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
FIXTURES = Path(__file__).parent / "fixtures"

CSR = "Chase Sapphire Reserve"
CSP = "Chase Sapphire Preferred"
BA = "British Airways Executive Club"
D = date(2026, 9, 15)


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def mc():
    """SYNTHETIC multi-currency ratio table."""
    return RatioManager(
        FIXTURES / "ratios_multicurrency_test.csv",
        DATA / "bonuses.csv",
        DATA / "programs.yaml",
    )


def w(balances, cards=(CSR,), valuation=None):
    return Wallet(
        balances=dict(balances),
        cards=list(cards),
        valuation_cpp=dict(valuation or {}),
    )


# ---------------------------------------------------------------------------
# Sizing primitives
# ---------------------------------------------------------------------------


def test_size_up_covers_and_size_down_underfills(mc):
    ratio = mc.query_ratio("UR", BA, D, CSR)
    src_up, del_up = size_up(22500, ratio, 1000)
    assert (src_up, del_up) == (23000, 23000)
    src_dn, del_dn = size_down(22500, ratio, 1000)
    assert (src_dn, del_dn) == (22000, 22000)
    assert del_dn <= 22500 <= del_up


def test_size_down_respects_a_balance_ceiling(mc):
    ratio = mc.query_ratio("UR", BA, D, CSR)
    src, delivered = size_down(50000, ratio, 1000, max_source=12400)
    assert src == 12000, "must round DOWN to a whole increment inside the balance"
    assert delivered == 12000


# ---------------------------------------------------------------------------
# Step 4: per-currency valuation, no cross-currency points sum
# ---------------------------------------------------------------------------


def test_single_currency_matches_v0_exactly(rm):
    """
    GOLDEN OUTPUT. funding.best_plan must reproduce v0's find_transfer_paths
    for every single-currency case, or the extraction regressed something.
    """
    cases = [
        (22500, BA, 180000, None),
        (20000, BA, 180000, None),
        (7500, "Club Iberia Plus", None, None),
        (90000, "World of Hyatt", 200000, None),
        (4000, "Air France-KLM Flying Blue", 5000, None),
        (22500, "IHG One Rewards", 100000, 0),
    ]
    for needed, program, balance, ceiling in cases:
        v0 = find_transfer_paths(
            user_balances={"UR": balance},
            user_cards=[CSR],
            target_program=program,
            check_date=D,
            ratios_manager=rm,
            points_needed=needed,
            max_stranded_points=ceiling,
        )
        v1 = all_plans(
            wallet=w({"UR": balance}),
            target_program=program,
            points_needed=needed,
            transfer_date=D,
            ratios_manager=rm,
            max_stranded_points=ceiling,
        )
        label = f"{needed} {program} bal={balance} ceiling={ceiling}"
        assert len(v0) == len(v1), label
        if not v0:
            continue
        assert v0[0].total_transferred == v1[0].per_currency_spend.get("UR", 0), label
        assert v0[0].stranded_points == v1[0].stranded_points, label
        assert v0[0].target_points_received == v1[0].delivered, label
        # v0's score was total_points_cost * 1cpp. Same number, honestly derived.
        assert v1[0].score_usd == pytest.approx(v0[0].total_points_cost * 0.01), label


def test_score_is_usd_and_points_are_reported_per_currency(mc):
    plan = best_plan(
        wallet=w({"UR": 20000, "MR": 20000}),
        target_program=BA,
        points_needed=30000,
        transfer_date=D,
        ratios_manager=mc,
    )
    assert plan is not None
    assert set(plan.per_currency_spend) == {"UR", "MR"}
    # Two separate figures, never one summed integer.
    assert plan.per_currency_spend["UR"] + plan.per_currency_spend["MR"] >= 30000
    assert "UR" in plan.spend_summary() and "MR" in plan.spend_summary()


def test_per_currency_valuation_changes_the_ranking(mc):
    """
    Identical points cost, different valuation -> the cheaper currency wins.

    UR at 1.0cpp and MR at 1.4cpp: for the same 20,000 points, UR is the cheaper
    plan in dollars. v0 could not express this at all - it summed raw points.
    """
    wallet = w({"UR": 50000, "MR": 50000}, valuation={"UR": 0.01, "MR": 0.014})
    plans = all_plans(
        wallet=wallet,
        target_program=BA,
        points_needed=20000,
        transfer_date=D,
        ratios_manager=mc,
    )
    singles = [p for p in plans if p.currencies_used == 1]
    assert singles[0].per_currency_spend.get("UR") == 20000
    ur = next(p for p in singles if "UR" in p.per_currency_spend)
    assert ur.score_usd == pytest.approx(200.0)
    mr_plan = funding._build_plan(
        legs=[("MR", mc.query_ratio("MR", BA, D, CSR), None, 50000, 1000, 0.0)],
        target_program=BA, points_needed=20000, existing_used=0, shortfall=20000,
        wallet=wallet, default_valuation_cpp=None, max_stranded_points=None,
    )
    assert mr_plan.score_usd == pytest.approx(280.0)
    assert ur.score_usd < mr_plan.score_usd


def test_default_valuation_is_unchanged_at_one_cent(mc):
    plan = best_plan(
        wallet=w({"UR": 50000}), target_program=BA, points_needed=20000,
        transfer_date=D, ratios_manager=mc,
    )
    assert plan.score_usd == pytest.approx(200.0)


def test_a_bad_ratio_currency_loses_on_usd_not_on_raw_points(mc):
    """
    C1 transfers at a synthetic 2:1, so 20,000 BA costs 40,000 C1.

    v0's total_points_cost would compare 20,000 UR against 40,000 C1 as if they
    were the same unit. They are not; the USD score is the comparison that means
    something.
    """
    wallet = w({"UR": 60000, "C1": 60000})
    plans = all_plans(wallet=wallet, target_program=BA, points_needed=20000,
                      transfer_date=D, ratios_manager=mc)
    best = plans[0]
    assert "UR" in best.per_currency_spend
    assert best.score_usd == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Step 5 / R2: THE STRANDING TRAP
# ---------------------------------------------------------------------------


def test_two_currency_split_strands_one_block_not_two(mc):
    """
    R2. Neither currency alone can fund 30,000 BA; together they must.

    The trap: rounding BOTH currencies up independently gives
    17,000 + 14,000 = 31,000 delivered and strands 1,000 - two blocks' worth of
    slack in a plan that only ever needed one. Correct behaviour under-fills from
    the first currency and rounds up only on the last.
    """
    wallet = w({"UR": 17000, "MR": 20000})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=30000,
                     transfer_date=D, ratios_manager=mc)
    assert plan is not None, "a split should be able to fund this"
    assert plan.currencies_used == 2
    assert plan.delivered >= 30000
    assert plan.stranded_points <= 999, (
        f"stranded {plan.stranded_points} - R2 violated, this is the "
        f"multi-block-stranding regression"
    )
    assert plan.stranded_points == 0


def test_split_underfills_from_every_currency_but_the_last(mc):
    """The mechanism behind R2, asserted directly."""
    wallet = w({"UR": 12000, "MR": 20000})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=22500,
                     transfer_date=D, ratios_manager=mc)
    assert plan.currencies_used == 2
    first, last = plan.transfers[0], plan.transfers[-1]
    assert first.points_delivered < 22500, "the first transfer must UNDER-fill"
    assert plan.delivered >= 22500
    assert plan.stranded_points <= 999


def test_stranding_ceiling_is_one_block_not_the_sum_of_blocks(mc):
    ratio = mc.query_ratio("UR", BA, D, CSR)
    assert funding.plan_stranding_ceiling([(ratio, 1000)]) == 999
    # Two transfers do NOT double the ceiling.
    assert funding.plan_stranding_ceiling([(ratio, 1000), (ratio, 1000)]) == 999


def test_no_split_when_one_currency_suffices(mc):
    """R3. A split that merely ties must not be proposed."""
    wallet = w({"UR": 50000, "MR": 50000})
    plans = all_plans(wallet=wallet, target_program=BA, points_needed=20000,
                      transfer_date=D, ratios_manager=mc)
    assert plans, "expected at least one plan"
    assert all(p.currencies_used <= 1 for p in plans), (
        "a split was proposed even though a single currency funds this identically"
    )


def test_rejected_split_records_a_reason(mc):
    wallet = w({"UR": 50000, "MR": 50000})
    plans = all_plans(wallet=wallet, target_program=BA, points_needed=20000,
                      transfer_date=D, ratios_manager=mc, include_infeasible=True)
    codes = {r.code for p in plans for r in p.reasons}
    assert "SPLIT_REJECTED" in codes


def test_split_is_used_when_no_single_currency_can_fund_it(mc):
    wallet = w({"UR": 11000, "MR": 11000})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=20000,
                     transfer_date=D, ratios_manager=mc)
    assert plan is not None and plan.currencies_used == 2
    codes = {r.code for r in plan.reasons}
    assert "SPLIT_REQUIRED" in codes


def test_three_way_splits_are_never_proposed_at_the_default(mc):
    """R4. MAX_SPLIT_CURRENCIES = 2."""
    assert config.MAX_SPLIT_CURRENCIES == 2
    wallet = w({"UR": 8000, "MR": 8000, "TY": 8000, "Bilt": 8000})
    plans = all_plans(wallet=wallet, target_program=BA, points_needed=24000,
                      transfer_date=D, ratios_manager=mc, include_infeasible=True)
    assert all(p.currencies_used <= 2 for p in plans)
    # And with only 8,000 in each, a 24,000 award simply cannot be funded.
    assert best_plan(wallet=wallet, target_program=BA, points_needed=24000,
                     transfer_date=D, ratios_manager=mc) is None


def test_max_stranded_zero_still_rejects_the_500_point_overshoot(rm):
    """(d) The v0 B1 case, unchanged."""
    plan = best_plan(
        wallet=w({"UR": 100000}), target_program="IHG One Rewards",
        points_needed=22500, transfer_date=D, ratios_manager=rm,
        max_stranded_points=0,
    )
    assert plan is None


def test_max_stranded_zero_reason_is_stranding_not_balance(rm):
    plans = all_plans(
        wallet=w({"UR": 100000}), target_program="IHG One Rewards",
        points_needed=22500, transfer_date=D, ratios_manager=rm,
        max_stranded_points=0, include_infeasible=True,
    )
    codes = {r.code for p in plans for r in p.reasons}
    assert "STRANDING_REJECTED" in codes
    assert "BALANCE_REJECTED" not in codes


# ---------------------------------------------------------------------------
# Balance states: absent / None / 0 are three different things
# ---------------------------------------------------------------------------


def test_absent_currency_funds_nothing(mc):
    """Absence means NOT HELD."""
    plan = best_plan(wallet=w({"MR": 50000}), target_program="World of Hyatt",
                     points_needed=20000, transfer_date=D, ratios_manager=mc)
    assert plan is None, "MR has no Hyatt row; UR is absent so it cannot be used"


def test_zero_balance_contributes_nothing_but_is_still_held(mc):
    wallet = w({"UR": 0, "MR": 30000})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=20000,
                     transfer_date=D, ratios_manager=mc)
    assert plan is not None
    assert "UR" not in plan.per_currency_spend
    assert wallet.holds("UR") and wallet.balance_of("UR") == 0


def test_unconstrained_balance_applies_no_ceiling(mc):
    plan = best_plan(wallet=w({"UR": None}), target_program=BA,
                     points_needed=500000, transfer_date=D, ratios_manager=mc)
    assert plan is not None
    assert plan.per_currency_spend["UR"] == 500000


# ---------------------------------------------------------------------------
# Residue report
# ---------------------------------------------------------------------------


def test_residue_reconciles_exactly(mc):
    wallet = w({"UR": 50000, "MR": 50000})
    p1 = best_plan(wallet=wallet, target_program=BA, points_needed=20000,
                   transfer_date=D, ratios_manager=mc)
    report = residue_report(wallet, [p1])
    for currency, row in report.items():
        assert row["remaining"] == row["starting"] - row["spent"], currency
    assert report["UR"]["spent"] == 20000
    assert report["UR"]["remaining"] == 30000
    assert report["MR"]["spent"] == 0


def test_residue_flags_a_balance_too_small_to_be_useful(mc):
    wallet = w({"UR": 21000, "MR": 50000})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=20000,
                     transfer_date=D, ratios_manager=mc)
    report = residue_report(wallet, [plan])
    assert report["UR"]["remaining"] == 1000
    assert report["UR"]["too_small_to_use"] is True
    assert report["MR"]["too_small_to_use"] is False


def test_residue_handles_unconstrained_balances(mc):
    wallet = w({"UR": None})
    plan = best_plan(wallet=wallet, target_program=BA, points_needed=20000,
                     transfer_date=D, ratios_manager=mc)
    report = residue_report(wallet, [plan])
    assert report["UR"]["unconstrained"] is True
    assert report["UR"]["remaining"] is None
    assert report["UR"]["spent"] == 20000
