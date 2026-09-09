"""
Funding plans: how to get N points into one destination program, from a wallet.

This module replaces the ranking half of v0's `find_transfer_paths`. The v0
function is still there and still correct for what it does - it is the reference
implementation the single-currency golden test in tests/test_funding.py compares
against - but it ranked candidate paths on `TransferPath.total_points_cost`,
which ADDS SOURCE POINTS ACROSS PROGRAMS. Its own docstring conceded that this is
only faithful because every Chase UR ratio in scope happens to be 1:1. With five
issuer currencies at differing ratios it is a category error: 10,000 UR and
10,000 MR are not 20,000 of anything.

v1 ranks on `score_usd` - each currency's points valued at that currency's own
cents-per-point and summed in dollars - and reports the raw per-currency point
counts separately, never summed into one integer.

Because every currency defaults to the same neutral 1cpp yardstick v0 used,
default behaviour is numerically unchanged.

THE STRANDING RULE, restated for multi-currency (plan section 4.6):

  R1  Stranding is a property of the PLAN, not of a transfer:
      stranded = total_delivered - points_needed, in the destination currency.
  R2  Only ONE transfer in a plan may carry rounding slack. Every currency but
      the last is sized to an exact increment multiple that UNDER-fills; the last
      covers the remainder, rounded up. A naive implementation that rounds each
      currency up independently would strand up to N blocks instead of one - and
      would pass every v0 test, because v0 never built a two-currency plan.
  R3  Do not split when a single currency will do.
  R4  Never split across more than MAX_SPLIT_CURRENCIES currencies.
"""
from datetime import date
from itertools import permutations
from typing import Dict, List, Optional, Tuple

from src import config
from src.models import FundingPlan, Ratio, Transfer
from src.ratio_manager import RatioManager
from src.wallet import Wallet


# ---------------------------------------------------------------------------
# Transfer sizing primitives
# ---------------------------------------------------------------------------


def _deliver(src: int, ratio: Ratio, bonus_pct: float = 0.0) -> int:
    """Destination points delivered by moving `src` source points."""
    out = ratio.apply(src)
    if bonus_pct:
        out = int(out * (1 + bonus_pct / 100))
    return out


def _round_up_to_increment(amount: int, increment: int) -> int:
    if increment <= 1:
        return amount
    return -(-amount // increment) * increment


def _round_down_to_increment(amount: int, increment: int) -> int:
    if increment <= 1:
        return max(amount, 0)
    return max((amount // increment) * increment, 0)


def size_up(
    points_needed: int, ratio: Ratio, increment: int, bonus_pct: float = 0.0
) -> Tuple[int, int]:
    """
    Smallest transfer that COVERS `points_needed`. Returns (source, delivered).

    This is the "last currency" sizing: it is the only transfer in a plan allowed
    to overshoot, and its overshoot is the plan's entire stranding.
    """
    if points_needed <= 0:
        return 0, 0
    source = _round_up_to_increment(ratio.source_needed_for(points_needed), increment)
    guard = 0
    while _deliver(source, ratio, bonus_pct) < points_needed and guard < 10000:
        source += max(increment, 1)
        guard += 1
    return source, _deliver(source, ratio, bonus_pct)


def size_down(
    points_target: int,
    ratio: Ratio,
    increment: int,
    bonus_pct: float = 0.0,
    max_source: Optional[int] = None,
) -> Tuple[int, int]:
    """
    Largest transfer that does NOT exceed `points_target`. Returns (source, delivered).

    R2's under-fill. Every currency but the last is sized with this, so no
    currency but the last can contribute stranding.
    """
    if points_target <= 0:
        return 0, 0
    source = _round_down_to_increment(ratio.source_needed_for(points_target), increment)
    if max_source is not None:
        source = min(source, _round_down_to_increment(max_source, increment))
    guard = 0
    while source > 0 and _deliver(source, ratio, bonus_pct) > points_target and guard < 10000:
        source -= max(increment, 1)
        guard += 1
    source = max(source, 0)
    return source, _deliver(source, ratio, bonus_pct)


def plan_stranding_ceiling(
    ratios: List[Tuple[Ratio, int]], override: Optional[int] = None
) -> int:
    """
    R2's ceiling: ONE indivisible block, not the sum of blocks.

    `ratios` is [(ratio, increment)] for every transfer in the plan. Because only
    the last transfer may overshoot, the plan's stranding is bounded by the
    destination value of a single source increment, minus one point. Taking the
    max across the plan's transfers is the safe form of that bound.
    """
    if override is not None:
        return override
    if not ratios:
        return 0
    return max(max(r.apply(inc) - 1, 0) for r, inc in ratios)


# ---------------------------------------------------------------------------
# Building plans
# ---------------------------------------------------------------------------


def _valuation(wallet: Wallet, currency: str, default_cpp: Optional[float]) -> float:
    """Per-currency cpp: wallet override -> config table -> the caller's default."""
    if currency in wallet.valuation_cpp:
        return wallet.valuation_cpp[currency]
    if currency in config.PER_CURRENCY_VALUATION_CPP:
        return config.PER_CURRENCY_VALUATION_CPP[currency]
    return default_cpp if default_cpp is not None else config.DEFAULT_CURRENCY_VALUATION_CPP


def _score(
    per_currency_spend: Dict[str, int],
    existing_used: int,
    target_program: str,
    wallet: Wallet,
    default_cpp: Optional[float],
) -> float:
    """
    Plan cost in USD. THE cross-currency comparison, and the only legitimate one.

    Points in different currencies are converted to dollars at each currency's
    own valuation and summed. They are never added as raw integers.
    """
    total = 0.0
    for currency, points in per_currency_spend.items():
        total += points * _valuation(wallet, currency, default_cpp)
    if existing_used:
        total += existing_used * _valuation(wallet, target_program, default_cpp)
    return total


def _usable_sources(
    wallet: Wallet,
    target_program: str,
    transfer_date: date,
    ratios_manager: RatioManager,
) -> List[Tuple[str, Ratio, Optional[str], Optional[int], int, float]]:
    """
    Every (currency, best ratio, card, balance, increment, bonus) that can fund
    the target on the transfer date.

    Candidate-driven, not enumeration-driven: we start from a program we already
    have a priced award for and ask only "which of the held currencies reach it?"
    - typically one to three of five, never a cross product.
    """
    out = []
    for currency in wallet.currencies:
        canonical = ratios_manager.normalize_program(currency)
        if canonical == target_program:
            continue
        balance = wallet.balance_of(currency)
        if balance is not None and balance == 0:
            continue
        pairs = ratios_manager.best_ratio_across_cards(
            canonical, target_program, transfer_date, wallet.cards
        )
        if not pairs:
            continue
        card, ratio = pairs[0]
        bonus = ratios_manager.query_bonus(canonical, target_program, transfer_date)
        out.append(
            (
                currency,
                ratio,
                card,
                balance,
                config.transfer_increment_for(currency),
                bonus.bonus_percent if bonus else 0.0,
            )
        )
    # Best ratio first, so the greedy fill starts from the most efficient source.
    out.sort(key=lambda t: t[1].ratio_denominator / t[1].ratio_numerator, reverse=True)
    return out


def _existing_in_target(
    wallet: Wallet, target_program: str, ratios_manager: RatioManager
) -> Tuple[int, bool]:
    """(usable existing balance, whether it is unconstrained)."""
    for currency in wallet.currencies:
        if ratios_manager.normalize_program(currency) == target_program:
            balance = wallet.balance_of(currency)
            return (0, True) if balance is None else (balance, False)
    return 0, False


def all_plans(
    wallet: Wallet,
    target_program: str,
    points_needed: int,
    transfer_date: date,
    ratios_manager: RatioManager,
    max_stranded_points: Optional[int] = None,
    default_valuation_cpp: Optional[float] = None,
    max_split_currencies: Optional[int] = None,
    include_infeasible: bool = False,
) -> List[FundingPlan]:
    """Every feasible funding plan, cheapest (USD) first."""
    target_program = ratios_manager.normalize_program(target_program)
    max_split = (
        max_split_currencies
        if max_split_currencies is not None
        else config.MAX_SPLIT_CURRENCIES
    )
    plans: List[FundingPlan] = []

    existing, existing_unconstrained = _existing_in_target(
        wallet, target_program, ratios_manager
    )
    if existing_unconstrained:
        existing = points_needed

    # ---- Option 0: already holding enough in the destination ----------------
    if points_needed > 0 and existing >= points_needed:
        plan = FundingPlan(
            target_program=target_program,
            existing_target_points_used=points_needed,
            points_needed=points_needed,
            delivered=0,
            stranded_points=0,
        )
        plan.score_usd = _score({}, points_needed, target_program, wallet, default_valuation_cpp)
        plan.add_reason(
            "RATIO_CHOSEN",
            f"No transfer needed: {points_needed:,} {target_program} already held.",
            program=target_program,
        )
        plans.append(plan)

    existing_used = min(existing, points_needed)
    shortfall = points_needed - existing_used

    sources = _usable_sources(wallet, target_program, transfer_date, ratios_manager)

    # ---- Single-currency plans ---------------------------------------------
    single_feasible = False
    for currency, ratio, card, balance, increment, bonus_pct in sources:
        if shortfall <= 0:
            break
        plan = _build_plan(
            legs=[(currency, ratio, card, balance, increment, bonus_pct)],
            target_program=target_program,
            points_needed=points_needed,
            existing_used=existing_used,
            shortfall=shortfall,
            wallet=wallet,
            default_valuation_cpp=default_valuation_cpp,
            max_stranded_points=max_stranded_points,
        )
        if plan.feasible:
            single_feasible = True
        if plan.feasible or include_infeasible:
            plans.append(plan)

    # ---- Split plans (R3: only when a split is actually needed or better) ----
    #
    # FINDING L-6. This enumerated pairs only - a hardcoded width of two -
    # regardless of `max_split`, so `config.MAX_SPLIT_CURRENCIES` was documented
    # as overridable and was silently ignored: raising it to 3 or 5 produced
    # EXACTLY the same plans as 2, with no warning. `_build_plan` has always
    # handled an arbitrary number of legs correctly - R2's under-fill applies to
    # every currency but the last regardless of how many there are - so honouring
    # the knob is a one-line change to the enumeration. The default is still 2,
    # so default behaviour is unchanged; what changes is that the setting now
    # means what it says.
    split_collapsed = False
    max_width = min(max_split, len(sources))
    if shortfall > 0 and len(sources) >= 2 and max_split >= 2:
        for width in range(2, max_width + 1):
            for combo in permutations(sources, width):
                plan = _build_plan(
                    legs=list(combo),
                    target_program=target_program,
                    points_needed=points_needed,
                    existing_used=existing_used,
                    shortfall=shortfall,
                    wallet=wallet,
                    default_valuation_cpp=default_valuation_cpp,
                    max_stranded_points=max_stranded_points,
                )
                if not plan.feasible and not include_infeasible:
                    continue
                if plan.currencies_used < 2:
                    # The greedy fill was satisfied by the first currency alone,
                    # so this "split" is really the single-currency plan already
                    # built.
                    split_collapsed = True
                    continue
                if plan.currencies_used < width:
                    # A narrower split already covers this combination and is
                    # built by an earlier `width`. Keeping it would duplicate.
                    continue
                plan.add_reason(
                    "SPLIT_REQUIRED",
                    f"Funded from {plan.currencies_used} currencies. Only the last "
                    f"transfer carries rounding slack, so stranding stays within "
                    f"one block (R2).",
                    currencies=sorted(plan.per_currency_spend),
                )
                plans.append(plan)

    # FINDING L-6, SECOND HALF: AN EXPLANATION BUG HIDING A BEHAVIOUR BUG.
    #
    # With UR 160,000 + MR 50,000 + TY 50,000 (260,000 available) and a 250,000
    # award, every plan came back infeasible with `infeasible_reason = "Needs
    # 250,000 UR but balance is 160,000"` - a claim about the BALANCE for what is
    # actually an R4 POLICY REFUSAL: the wallet holds enough, the split cap says
    # no more than `max_split` currencies. v1 section 5 warns about exactly this
    # pattern. The refusal is legitimate and stays; the reason now names it.
    if shortfall > 0 and max_split < len(sources) and not any(
        p.feasible for p in plans
    ):
        held = sum(
            b for _, _, _, b, _, _ in sources if b is not None
        )
        if held >= shortfall:
            for plan in plans:
                plan.infeasible_reason = (
                    f"{plan.infeasible_reason} THE BALANCE IS NOT THE REAL "
                    f"CONSTRAINT: you hold {held:,} points across "
                    f"{len(sources)} currencies that reach {target_program}, "
                    f"which is enough for the {shortfall:,} needed. What blocks "
                    f"this is the SPLIT CAP - config.MAX_SPLIT_CURRENCIES is "
                    f"{max_split}, and funding it would take more than that. "
                    f"Raise it if you are willing to hold residue in that many "
                    f"accounts."
                ).strip()
                plan.add_reason(
                    "SPLIT_REJECTED",
                    plan.infeasible_reason,
                    max_split=max_split,
                    currencies_available=len(sources),
                    points_held=held,
                )

    if not include_infeasible:
        plans = [p for p in plans if p.feasible]

    # R3: a split is permitted only if no single currency suffices, or it is
    # STRICTLY cheaper. Ties break to fewer currencies.
    if single_feasible:
        cheapest_single = min(
            (p.score_usd for p in plans if p.currencies_used <= 1), default=None
        )
        if cheapest_single is not None:
            kept = []
            for p in plans:
                if p.currencies_used >= 2 and p.score_usd >= cheapest_single:
                    p.add_reason(
                        "SPLIT_REJECTED",
                        f"A single currency funds this at the same or lower cost "
                        f"(${cheapest_single:,.2f}). Splitting creates unusable "
                        f"residue in two accounts and doubles the operational risk "
                        f"of a one-way transfer.",
                        split_score_usd=round(p.score_usd, 2),
                        single_score_usd=round(cheapest_single, 2),
                    )
                    if include_infeasible:
                        kept.append(p)
                    continue
                kept.append(p)
            plans = kept

            if split_collapsed:
                # A split WAS considered and was not needed. Record that on the
                # surviving plan so "why didn't it use my MR too?" is answerable.
                for p in kept:
                    if p.currencies_used <= 1 and p.score_usd == cheapest_single:
                        p.add_reason(
                            "SPLIT_REJECTED",
                            "A two-currency split was evaluated and rejected: one "
                            "currency funds this award on its own (R3). Splitting "
                            "would leave residue in a second account for no gain.",
                            single_score_usd=round(cheapest_single, 2),
                        )
                        break

    plans.sort(key=lambda p: (p.score_usd, p.currencies_used, p.stranded_points))
    return plans


def _build_plan(
    legs,
    target_program: str,
    points_needed: int,
    existing_used: int,
    shortfall: int,
    wallet: Wallet,
    default_valuation_cpp: Optional[float],
    max_stranded_points: Optional[int],
) -> FundingPlan:
    """
    Build one plan from an ordered list of source currencies.

    R2 IS IMPLEMENTED HERE and this is the single most regression-prone function
    in v1: every currency but the LAST is sized DOWN to an increment multiple
    that under-fills, and only the last is rounded up. Round each one up
    independently and the plan strands one block per currency instead of one
    block total - a bug that would pass the entire v0 suite silently.
    """
    plan = FundingPlan(
        target_program=target_program,
        existing_target_points_used=existing_used,
        points_needed=points_needed,
    )

    remaining = shortfall
    used_ratios: List[Tuple[Ratio, int]] = []
    last_index = len(legs) - 1

    for i, (currency, ratio, card, balance, increment, bonus_pct) in enumerate(legs):
        if remaining <= 0:
            break

        if i < last_index:
            # UNDER-fill. Never overshoot from a non-final currency.
            source, delivered = size_down(
                remaining, ratio, increment, bonus_pct, max_source=balance
            )
        else:
            source, delivered = size_up(remaining, ratio, increment, bonus_pct)

        if source <= 0:
            continue

        if balance is not None and source > balance:
            if i < last_index:
                continue  # this currency simply contributes nothing
            plan.feasible = False
            plan.infeasible_reason = (
                f"Needs {source:,} {currency} but balance is {balance:,}"
            )
            plan.add_reason(
                "BALANCE_REJECTED",
                plan.infeasible_reason,
                currency=currency,
                needed=source,
                balance=balance,
            )

        plan.transfers.append(
            Transfer(
                from_program=currency,
                to_program=target_program,
                amount=source,
                ratio=ratio.as_string,
                ratio_numerator=ratio.ratio_numerator,
                ratio_denominator=ratio.ratio_denominator,
                card_used=card,
                points_delivered=delivered,
            )
        )
        plan.per_currency_spend[currency] = plan.per_currency_spend.get(currency, 0) + source
        plan.ratio_remainder_points[currency] = (
            plan.ratio_remainder_points.get(currency, 0)
            + (source - ratio.source_needed_for(ratio.apply(source)))
        )
        plan.delivered += delivered
        remaining -= delivered
        used_ratios.append((ratio, increment))

        plan.add_reason(
            "RATIO_CHOSEN",
            f"{currency} -> {target_program} at {ratio.as_string}"
            + (f" on {card}" if card else ""),
            currency=currency,
            ratio=ratio.as_string,
        )
        if card:
            plan.add_reason(
                "CARD_CHOSEN",
                f"{card} gives the best {currency} -> {target_program} ratio "
                f"({ratio.as_string}) on the transfer date.",
                card=card,
            )

    if remaining > 0 and plan.feasible:
        plan.feasible = False
        plan.infeasible_reason = (
            f"Still {remaining:,} {target_program} points short after every "
            f"available currency was used."
        )
        plan.add_reason("BALANCE_REJECTED", plan.infeasible_reason, short_by=remaining)

    # R1: stranding is a property of the PLAN.
    plan.stranded_points = max(plan.delivered + existing_used - points_needed, 0)
    plan.score_usd = _score(
        plan.per_currency_spend,
        existing_used,
        target_program,
        wallet,
        default_valuation_cpp,
    )

    ceiling = plan_stranding_ceiling(used_ratios, max_stranded_points)
    if plan.feasible and plan.stranded_points > ceiling:
        plan.feasible = False
        plan.infeasible_reason = (
            f"Would strand {plan.stranded_points:,} {target_program} points "
            f"(ceiling {ceiling:,}). Transfers are one-way; the excess cannot be "
            f"moved back."
        )
        plan.add_reason(
            "STRANDING_REJECTED",
            plan.infeasible_reason,
            stranded=plan.stranded_points,
            ceiling=ceiling,
        )

    return plan


def best_plan(
    wallet: Wallet,
    target_program: str,
    points_needed: int,
    transfer_date: date,
    ratios_manager: RatioManager,
    max_stranded_points: Optional[int] = None,
    default_valuation_cpp: Optional[float] = None,
    max_split_currencies: Optional[int] = None,
) -> Optional[FundingPlan]:
    """Cheapest feasible funding plan in USD, or None if there is none."""
    plans = all_plans(
        wallet=wallet,
        target_program=target_program,
        points_needed=points_needed,
        transfer_date=transfer_date,
        ratios_manager=ratios_manager,
        max_stranded_points=max_stranded_points,
        default_valuation_cpp=default_valuation_cpp,
        max_split_currencies=max_split_currencies,
    )
    return plans[0] if plans else None


# ---------------------------------------------------------------------------
# Residue report
# ---------------------------------------------------------------------------

# A balance below this is very unlikely to fund anything on its own and is worth
# flagging as effectively stranded. Not a claim about value - just a threshold
# for "you should know this is sitting there".
RESIDUE_WARN_BELOW = 5000


def residue_report(
    wallet: Wallet, plans: List[FundingPlan]
) -> Dict[str, Dict[str, object]]:
    """
    Leftover balance per currency after a whole trip is planned.

    "This plan leaves 3,000 MR and 12,000 TY stranded" is a real cost that the v0
    output never showed. Reconciles exactly: remaining == starting - spent.
    """
    spent: Dict[str, int] = {}
    for plan in plans:
        for currency, points in plan.per_currency_spend.items():
            spent[currency] = spent.get(currency, 0) + points
        if plan.existing_target_points_used and plan.target_program in wallet.balances:
            spent[plan.target_program] = (
                spent.get(plan.target_program, 0) + plan.existing_target_points_used
            )

    report: Dict[str, Dict[str, object]] = {}
    for currency in wallet.currencies:
        starting = wallet.balance_of(currency)
        used = spent.get(currency, 0)
        if starting is None:
            report[currency] = {
                "starting": None,
                "spent": used,
                "remaining": None,
                "unconstrained": True,
                "too_small_to_use": False,
                "overdrawn": False,
            }
            continue
        remaining = starting - used
        report[currency] = {
            "starting": starting,
            "spent": used,
            "remaining": remaining,
            "unconstrained": False,
            "too_small_to_use": 0 < remaining < RESIDUE_WARN_BELOW,
            "overdrawn": remaining < 0,
        }
    return report
