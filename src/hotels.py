"""
Hotel award evaluation.

WHAT THIS MODULE DOES NOT DO: it does not find hotel awards. There is no public
award-rate API for Hyatt, Marriott, IHG or Wyndham, and Seats.aero is flights
only. v1 EVALUATES HOTEL AWARDS THE USER BRINGS: you read the points price off
the hotel's own site, put it in the fixture, and the tool scores the transfer and
the cash comparison. Every hotel row of the output says so.

The real bug this module fixes is MANDATORY FEES. A destination fee, a resort
fee, a city tax or mandatory parking is owed WHETHER OR NOT you pay with points.
v0 stored those as `CashOption.unavoidable_cash_note`, a free-text string on the
cash option - so they never entered any total at all, and on the points side they
were invisible. That is the same bug shape as the missing YQ: a real cash cost
that the model had no slot for.
"""
from typing import Dict, List, Optional, Tuple

from src.config import convert_to_usd
from src.models import Leg, MandatoryFee, PointsCandidate

MANUAL_CAPTURE = "manual_capture"

HOTEL_DISCLAIMER = (
    "v1 does not search hotel awards - this price was supplied manually."
)

# Award rules that may be declared per program in programs.yaml. All default to
# off. Enabling one without a source and a verified_on is a validation failure:
# a wrongly-enabled fifth_night_free silently makes every 5-night award stay 20%
# cheaper than it really is.
AWARD_RULE_NAMES = ("fifth_night_free", "peak_off_peak_calendar", "points_stay_tax_free")


class HotelDataError(ValueError):
    """Raised for structurally invalid hotel input."""


def nights_for(leg: Leg) -> int:
    """Nights in a stay. Zero for a flight leg or an unspecified stay."""
    return max(int(leg.nights or 0), 0)


def award_points_for(candidate: PointsCandidate, nights: int) -> Optional[int]:
    """
    Total award points for a stay.

    Either a captured total (`points`), or per-night x nights. A per-night price
    with zero nights is an input error, not a zero-cost stay.
    """
    if candidate.points_per_night is not None:
        if nights <= 0:
            raise HotelDataError(
                f"{candidate.label!r} is priced per night ({candidate.points_per_night:,}"
                f"/night) but the stay has no night count. Set `nights` on the leg."
            )
        return int(candidate.points_per_night) * nights
    if candidate.points and candidate.points > 0:
        return int(candidate.points)
    return None


def resolve_candidate_points(leg: Leg, candidate: PointsCandidate) -> Optional[int]:
    """Award points for a candidate on a leg, honouring the leg's night count."""
    nights = candidate.nights or nights_for(leg)
    return award_points_for(candidate, nights)


# ---------------------------------------------------------------------------
# Mandatory fees
# ---------------------------------------------------------------------------


def fees_usd(
    fees: List[MandatoryFee], nights: int, travelers: int = 1, points_side: bool = False
) -> float:
    """
    Total mandatory fees in USD.

    `points_side=True` counts only the fees marked `payable_on_points`. Note that
    most are: that is the entire point of the type. A fee you can pay with points
    is not a mandatory fee, it is part of the award price.
    """
    total = 0.0
    for fee in fees or []:
        if points_side and not fee.payable_on_points:
            continue
        total += convert_to_usd(fee.total_for(nights, travelers), fee.currency)
    return total


def fee_breakdown(
    fees: List[MandatoryFee], nights: int, travelers: int = 1
) -> List[Tuple[str, float, bool]]:
    """(label, usd amount, payable_on_points) per fee, for the output."""
    return [
        (
            fee.label,
            convert_to_usd(fee.total_for(nights, travelers), fee.currency),
            fee.payable_on_points,
        )
        for fee in fees or []
    ]


# ---------------------------------------------------------------------------
# Award-rule validation
# ---------------------------------------------------------------------------


def validate_award_rules(program: str, rules: Dict[str, object]) -> List[str]:
    """
    Award rules default to OFF and may only be enabled with provenance.

    Returns warnings; raises HotelDataError on an enabled rule with no source.
    """
    warnings: List[str] = []
    for name in AWARD_RULE_NAMES:
        value = rules.get(name, False)
        if value is False or value is None:
            continue
        if value is True:
            raise HotelDataError(
                f"{program}: award rule {name!r} is enabled as a bare `true` with no "
                f"provenance. Enabling it must look like:\n"
                f"    {name}:\n"
                f"      enabled: true\n"
                f"      source: <where this came from>\n"
                f"      verified_on: YYYY-MM-DD\n"
                f"An unsourced rule silently changes every award price in the program."
            )
        if isinstance(value, dict):
            if not value.get("enabled"):
                continue
            if not value.get("source") or not value.get("verified_on"):
                raise HotelDataError(
                    f"{program}: award rule {name!r} is enabled without a source "
                    f"and/or a verified_on date."
                )
            warnings.append(
                f"{program}: award rule {name} is ENABLED "
                f"(source: {value['source']}, verified {value['verified_on']})."
            )
        else:
            raise HotelDataError(
                f"{program}: award rule {name!r} has an unrecognised value {value!r}."
            )
    return warnings


def enabled_award_rules(rules: Dict[str, object]) -> List[str]:
    return [
        name
        for name in AWARD_RULE_NAMES
        if isinstance(rules.get(name), dict) and rules[name].get("enabled")
    ]
