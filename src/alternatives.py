"""
"Same metal, different program, less cash."

This is v1's headline capability and the one sentence v0 structurally could not
say:

    Same flight, same 20,000 Avios - book it through Iberia instead of British
    Airways and pay $400 less cash.

British Airways Executive Club, Club Iberia Plus and Aer Lingus AerClub all spend
Avios out of the same balance and all three can ticket each other's metal, but
each levies a completely different carrier-imposed surcharge. Nothing about the
aeroplane changes; only the cash does.

TWO HONESTY CONSTRAINTS, both load-bearing:

1. An alternative is almost always UNPRICED. We know Iberia's surcharge band is
   far below BA's; we do NOT know Iberia's award price for the specific flight,
   because the Avios family members have three different award charts. So the
   alternative carries a BREAK-EVEN POINTS figure - "if Iberia prices this below
   N points, it beats the BA option; go look it up" - and is never scored,
   totalled, or presented as a price. This reuses v0's break-even machinery
   rather than inventing a second honesty mechanism.

2. `bookable_carriers` in programs.yaml is ALLIANCE-DERIVED, not a verified
   partnership matrix (see the caveat block in that file). Every alternative
   built from it is flagged `partnership_assumed` and told to verify.


A CORRECTION TO THE PLAN, found while implementing it
-----------------------------------------------------
Steps 9 and 13 of docs/plans/v1.md ask for "Aeroplan and United alternatives" on
a BA-METAL candidate. That is not possible and the plan contradicts its own
section 4.7 here: 4.7 defines an alternative as another program that can ticket
THE SAME METAL, and Aeroplan and United are Star Alliance while British Airways
is oneworld. Neither can issue an award on a BA aeroplane.

What is really going on in the real trip's B4 leg is a DIFFERENT comparison: LHR
-> SFO has a BA-metal option and a separate United-metal option, two different
aeroplanes on the same route. That comparison is already made by the leg
evaluator, because both are candidates on the leg.

So this module implements two clearly separated things:

  find_same_metal_alternatives()  - section 4.7 as written. The Iberia-vs-BA case.
  cross_metal_note()              - names the zero-surcharge option that already
                                    exists on the leg, on different metal. Never
                                    claims one program can ticket another's plane.
"""
from datetime import date
from typing import List, Optional

from src.config import cash_to_points_equivalent
from src.models import Alternative, PointsCandidate, SurchargeEstimate
from src.ratio_manager import RatioManager
from src.surcharge import SurchargeTable

# An alternative is only worth showing if it saves real money.
MATERIAL_SAVING_USD = 50.0


def find_same_metal_alternatives(
    winning_candidate: PointsCandidate,
    winning_surcharge: SurchargeEstimate,
    region: str,
    departure_country: str,
    ratios_manager: RatioManager,
    surcharges: SurchargeTable,
    wallet_currencies: List[str],
    transfer_date: date,
    cash_usd: float,
    valuation_cpp: float = 0.01,
) -> List[Alternative]:
    """
    Every other program that can ticket the winning candidate's METAL, whose
    surcharge band is materially lower.

    Pure function of already-fetched data plus the local tables. It triggers NO
    additional Seats.aero calls - the 1,000/day rate limit must not be spent on
    counterfactuals.
    """
    # Known metal, or a KNOWN single-carrier itinerary lookup (by flight number).
    metal = winning_candidate.metal_for_alternatives
    if not metal:
        # No metal, no alternatives. Guessing which aeroplane it is in order to
        # suggest a cheaper program would be the same error the surcharge model
        # refuses to make.
        return []

    winning_program = ratios_manager.normalize_program(winning_candidate.program)
    out: List[Alternative] = []

    for program in ratios_manager.programs_that_can_ticket(metal):
        if program == winning_program:
            continue
        # Only programs the user can actually reach from a currency they hold.
        if not any(
            ratios_manager.is_partner(currency, program, transfer_date)
            for currency in wallet_currencies
        ):
            continue

        est = surcharges.resolve(
            program,
            metal,
            region,
            winning_candidate.cabin,
            departure_country,
            is_round_trip=winning_candidate.is_round_trip,
            carrier_is_known=True,
        )
        if not est.is_known:
            continue

        if winning_surcharge.is_known:
            saving = winning_surcharge.amount_point - est.amount_point
            if saving < MATERIAL_SAVING_USD:
                continue
        else:
            # The winner's surcharge is unknown, so we cannot quantify a saving.
            # A KNOWN low band is still worth surfacing - that is the whole
            # point when the incumbent is an unknown-and-probably-large figure.
            saving = 0.0

        # We do NOT know this program's award price. The Avios family share a
        # currency and NOT an award chart, so reusing the winner's points figure
        # would be inventing the number that matters most.
        break_even = None
        if cash_usd not in (0.0, float("inf")):
            budget = cash_usd - est.amount_point
            break_even = max(cash_to_points_equivalent(budget, valuation_cpp), 0)

        note = (
            f"{program} can ticket {metal} metal and carries "
            f"{est.render()} in carrier-imposed surcharge"
        )
        if winning_surcharge.is_known:
            note += f", against {winning_surcharge.render()} on {winning_program}"
        else:
            note += f", where {winning_program}'s surcharge on this metal is UNKNOWN"
        note += ". Award price NOT known - look it up."

        if ratios_manager.is_avios_family(program) and ratios_manager.is_avios_family(
            winning_program
        ):
            note += (
                f" NOTE: {program} and {winning_program} spend the SAME Avios but "
                f"price awards from DIFFERENT charts. Do not assume the "
                f"{winning_program} price applies."
            )

        out.append(
            Alternative(
                program=program,
                operating_carrier=metal,
                surcharge=est,
                cash_saved_vs_best_usd=saving,
                points_price=None,
                break_even_points=break_even,
                note=note,
                partnership_assumed=True,
            )
        )

    out.sort(key=lambda a: (-a.cash_saved_vs_best_usd, a.surcharge.amount_point))
    return out


def cross_metal_note(
    leg_candidates: List[PointsCandidate],
    winning_candidate: Optional[PointsCandidate],
    region: str,
    departure_country: str,
    surcharges: SurchargeTable,
) -> Optional[str]:
    """
    A one-line note when the SAME ROUTE has another candidate, on DIFFERENT metal,
    whose program carries a confirmed zero surcharge.

    This is the honest version of the plan's "book it on Aeroplan instead"
    line. It never claims a program can ticket another airline's aeroplane: it
    points at an option that is already on the leg.
    """
    if winning_candidate is None:
        return None

    win_metal = (winning_candidate.operating_carrier or "").upper()
    for cand in leg_candidates:
        metal = (cand.operating_carrier or "").upper()
        if cand is winning_candidate or not metal or metal == win_metal:
            continue
        if not cand.has_known_metal:
            continue
        est = surcharges.resolve(
            cand.program, metal, region, cand.cabin, departure_country,
            is_round_trip=cand.is_round_trip, carrier_is_known=True,
        )
        if est.is_known and est.amount_point == 0.0:
            return (
                f"Same route on different metal: {cand.label} ({cand.program} on "
                f"{metal}) carries NO carrier-imposed surcharge - that is a program "
                f"policy, not a route observation. Government taxes and airport "
                f"charges are still owed and are not modelled."
            )
    return None
