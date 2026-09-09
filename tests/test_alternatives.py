"""
Step 9: surcharge-avoidance alternatives.

Also documents a CORRECTION TO THE PLAN. Steps 9 and 13 of docs/plans/v1.md ask
for "Aeroplan and United alternatives" on a BA-metal candidate. That contradicts
the plan's own section 4.7, which defines an alternative as another program that
can ticket THE SAME METAL: Aeroplan and United are Star Alliance and British
Airways is oneworld, so neither can issue an award on a BA aeroplane. See
src/alternatives.py for the two clearly separated things that were built instead.
"""
from datetime import date
from pathlib import Path

import pytest

from src.alternatives import (
    cross_metal_note,
    find_same_metal_alternatives,
)
from src.models import PointsCandidate, SurchargeEstimate
from src.optimizer import evaluate_leg
from src.ratio_manager import RatioManager
from src.surcharge import SurchargeTable
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

DATA = Path(__file__).parent.parent / "data"
TRIPS = Path(__file__).parent / "fixtures" / "trips"
CSP = "Chase Sapphire Preferred"
D = date(2026, 9, 15)

BA = "British Airways Executive Club"
IB = "Club Iberia Plus"
EI = "Aer Lingus AerClub"


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def table():
    return SurchargeTable(DATA / "surcharges.csv")


def cand(program=BA, carrier="EI", cabin="J", points=50000, source="captured"):
    return PointsCandidate(
        label="award", program=program, points=points,
        operating_carrier=carrier, carrier_source=source, cabin=cabin,
        is_round_trip=False,
    )


# ---------------------------------------------------------------------------
# The case the plan specifies and the data supports
# ---------------------------------------------------------------------------


def test_ba_award_on_aer_lingus_metal_emits_an_aerclub_alternative(rm, table):
    """
    Step 9's first acceptance case. BA Executive Club's surcharge on EI metal is
    not modelled, but AerClub's surcharge on its OWN metal is - so the same
    aeroplane, ticketed in AerClub, has a known and much lower band.
    """
    alts = find_same_metal_alternatives(
        winning_candidate=cand(program=BA, carrier="EI", cabin="J"),
        winning_surcharge=SurchargeEstimate.unknown("no rule"),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    programs = [a.program for a in alts]
    assert EI in programs
    aerclub = next(a for a in alts if a.program == EI)
    assert aerclub.surcharge.is_known
    assert aerclub.surcharge.amount_point == 75.0  # 150 round-trip, halved


def test_no_alternative_is_ever_emitted_with_an_invented_points_price(rm, table):
    alts = find_same_metal_alternatives(
        winning_candidate=cand(),
        winning_surcharge=SurchargeEstimate.unknown(),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    assert alts, "expected at least one alternative for this case"
    for alt in alts:
        assert alt.points_price is None
        assert not alt.is_priced
        assert alt.break_even_points is not None
        assert "look it up" in alt.note.lower()


def test_avios_family_alternatives_never_reuse_the_ba_award_price(rm, table):
    alts = find_same_metal_alternatives(
        winning_candidate=cand(program=BA, carrier="EI", points=50000),
        winning_surcharge=SurchargeEstimate.unknown(),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    aerclub = next(a for a in alts if a.program == EI)
    assert aerclub.points_price != 50000
    assert aerclub.points_price is None
    assert "DIFFERENT charts" in aerclub.note
    assert "SAME Avios" in aerclub.note


def test_break_even_accounts_for_the_alternatives_own_surcharge(rm, table):
    """$1,800 cash minus AerClub's $75 surcharge = 172,500 points at 1cpp."""
    alts = find_same_metal_alternatives(
        winning_candidate=cand(),
        winning_surcharge=SurchargeEstimate.unknown(),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    aerclub = next(a for a in alts if a.program == EI)
    assert aerclub.break_even_points == 172500


def test_alternatives_are_flagged_as_assumed_partnerships(rm, table):
    """bookable_carriers is alliance-derived, not a verified matrix."""
    alts = find_same_metal_alternatives(
        winning_candidate=cand(),
        winning_surcharge=SurchargeEstimate.unknown(),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    assert all(a.partnership_assumed for a in alts)


# ---------------------------------------------------------------------------
# The tool never guesses metal, so it never guesses an alternative either
# ---------------------------------------------------------------------------


def test_no_alternatives_without_a_confirmed_operating_carrier(rm, table):
    for carrier, source in [("", "unknown"), ("EI", "assumed"), ("", "assumed")]:
        alts = find_same_metal_alternatives(
            winning_candidate=cand(carrier=carrier, source=source),
            winning_surcharge=SurchargeEstimate.unknown(),
            region="NA-EU", departure_country="US",
            ratios_manager=rm, surcharges=table,
            wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
        )
        assert alts == [], f"carrier={carrier!r} source={source} produced alternatives"


def test_only_programs_reachable_from_a_held_currency_are_offered(rm, table):
    """An alternative you cannot fund is not an alternative."""
    alts = find_same_metal_alternatives(
        winning_candidate=cand(),
        winning_surcharge=SurchargeEstimate.unknown(),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["MR"],  # no verified MR partner rows exist
        transfer_date=D, cash_usd=1800.0,
    )
    assert alts == []


def test_the_winning_program_is_never_its_own_alternative(rm, table):
    alts = find_same_metal_alternatives(
        winning_candidate=cand(program=EI, carrier="EI"),
        winning_surcharge=SurchargeEstimate(
            amount_low=60, amount_point=75, amount_high=100, confidence="modeled",
        ),
        region="NA-EU", departure_country="US",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=1800.0,
    )
    assert EI not in [a.program for a in alts]


def test_an_alternative_with_no_surcharge_row_is_not_offered(rm, table):
    """
    Only a KNOWN band is worth switching for. This is why the plan's headline
    Iberia-instead-of-BA sentence cannot be produced from the shipped table: the
    seed data covers each program on its OWN metal, so there is no row for
    "Club Iberia Plus on BA metal".
    """
    alts = find_same_metal_alternatives(
        winning_candidate=cand(program=BA, carrier="BA", cabin="J"),
        winning_surcharge=SurchargeEstimate(
            amount_low=750, amount_point=900, amount_high=1100, confidence="modeled",
        ),
        region="NA-EU", departure_country="GB",
        ratios_manager=rm, surcharges=table,
        wallet_currencies=["UR"], transfer_date=D, cash_usd=2000.0,
    )
    assert [a.program for a in alts] == [], (
        "no program has a modelled surcharge for BA metal, so no alternative can "
        "be offered without inventing one"
    )


# ---------------------------------------------------------------------------
# Cross-metal note: the honest version of "book it on United instead"
# ---------------------------------------------------------------------------


def test_cross_metal_note_names_a_zero_surcharge_option_already_on_the_leg(table):
    ba = cand(program=BA, carrier="BA", cabin="Y", points=20000)
    ba.label = "BA LHR-SFO"
    ua = PointsCandidate(
        label="United LHR-SFO", program="United MileagePlus", points=27600,
        operating_carrier="UA", carrier_source="captured", cabin="Y",
    )
    note = cross_metal_note([ba, ua], ba, "NA-EU", "GB", table)
    assert note is not None
    assert "United MileagePlus" in note
    assert "NO carrier-imposed surcharge" in note
    assert "Government taxes" in note, "the UK APD caveat must ride along"


def test_cross_metal_note_is_absent_when_there_is_no_zero_option(table):
    ba = cand(program=BA, carrier="BA", cabin="J")
    assert cross_metal_note([ba], ba, "NA-EU", "GB", table) is None


# ---------------------------------------------------------------------------
# End to end, through the flagship fixture
# ---------------------------------------------------------------------------


def test_flagship_fixture_emits_the_aerclub_alternative(rm):
    fixture = load_trip_fixture(TRIPS / "trip_c_lon_mry_surcharge.json")
    results = [
        evaluate_leg(leg, ratios_manager=rm,
                     wallet=Wallet(balances={"UR": None}, cards=[CSP]),
                     transfer_date=D, today=date(2026, 9, 8))
        for leg in fixture.legs
    ]
    c2 = next(r for r in results if r.leg.id == "C2")
    assert [a.program for a in c2.alternatives] == [EI]
    assert any(x.code == "ALTERNATIVE_UNPRICED" for x in c2.reasons)
