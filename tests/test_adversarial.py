"""
Adversarial inputs, plus the Step 0 gate status.

Nothing here should raise an unhandled exception, and nothing should quietly
substitute a plausible value for a missing one.
"""
from datetime import date
from pathlib import Path

import pytest

from src import regions
from src.models import CashOption, Leg, MandatoryFee, PointsCandidate
from src.optimizer import evaluate_leg
from src.ratio_manager import RatioManager
from src.regions import UnknownAirportError
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
CSP = "Chase Sapphire Preferred"
D = date(2026, 9, 15)


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


def leg(**kw):
    return Leg(
        id=kw.get("id", "X"),
        kind=kw.get("kind", "flight"),
        description="adversarial",
        date=date(2027, 1, 15),
        origin=kw.get("origin", ""),
        destination=kw.get("destination", ""),
        nights=kw.get("nights", 0),
        travelers=kw.get("travelers", 1),
        cash_options=kw.get("cash_options", [CashOption(label="c", amount=500.0)]),
        points_candidates=kw.get("points_candidates", []),
        mandatory_fees=kw.get("mandatory_fees", []),
    )


def w(balances=None, cards=(CSP,)):
    return Wallet(balances=dict(balances or {"UR": None}), cards=list(cards))


# ---------------------------------------------------------------------------
# Unknown / missing route data
# ---------------------------------------------------------------------------


def test_an_unknown_iata_code_degrades_the_surcharge_to_unknown_not_to_a_region(rm):
    """
    The leg still evaluates - it does not crash - but the surcharge cannot be
    modelled and says so. A defaulted region would pick a wrong band.
    """
    l = leg(
        origin="ZZZ", destination="MAD",
        points_candidates=[
            PointsCandidate(label="p", program="British Airways Executive Club",
                            points=20000, operating_carrier="BA",
                            carrier_source="captured", cabin="J")
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert not r.surcharge.is_known
    assert "ZZZ" in r.surcharge.notes


def test_a_leg_with_no_airports_still_evaluates(rm):
    l = leg(
        points_candidates=[
            PointsCandidate(label="p", program="British Airways Executive Club",
                            points=20000, operating_carrier="BA",
                            carrier_source="captured", cabin="J")
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert not r.surcharge.is_known
    assert "route region cannot be classified" in r.surcharge.notes


def test_a_program_wide_zero_still_resolves_without_airports(rm):
    """United's zero is a policy and needs neither region nor metal."""
    l = leg(
        points_candidates=[
            PointsCandidate(label="p", program="United MileagePlus", points=20000)
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert r.surcharge.is_known and r.surcharge.amount_point == 0.0


def test_unknown_carrier_code_does_not_crash_the_region_module():
    assert regions.alliance_of("ZZ") is None
    assert regions.carrier_name("ZZ") == "ZZ"
    with pytest.raises(UnknownAirportError):
        regions.classify("SFO", "")


# ---------------------------------------------------------------------------
# Degenerate legs
# ---------------------------------------------------------------------------


def test_a_hotel_leg_with_zero_nights_and_a_nightly_fee_costs_nothing_extra(rm):
    l = leg(
        kind="hotel", nights=0,
        mandatory_fees=[MandatoryFee(label="f", amount=30.0, per="night")],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert r.mandatory_fees_usd == 0.0


def test_a_leg_with_no_cash_option_does_not_produce_a_bogus_margin(rm):
    l = leg(
        cash_options=[],
        points_candidates=[
            PointsCandidate(label="p", program="United MileagePlus", points=20000)
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert r.cash_total_score_usd == float("inf")
    assert any("No cash option" in x for x in r.warnings)
    assert r.break_even_surcharge_usd is None


def test_a_leg_with_no_candidates_at_all(rm):
    r = evaluate_leg(leg(), ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert r.verdict == "cash (no points path)"


def test_a_zero_point_award_price_is_not_treated_as_free(rm):
    l = leg(
        points_candidates=[
            PointsCandidate(label="p", program="United MileagePlus", points=0)
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w(), transfer_date=D,
                     today=date(2026, 9, 8))
    assert r.verdict.startswith("cash")
    assert any("no award price supplied" in x for x in r.warnings)


def test_an_empty_wallet_finds_no_partner_rather_than_crashing(rm):
    l = leg(
        points_candidates=[
            PointsCandidate(label="p", program="United MileagePlus", points=20000)
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=Wallet(balances={}, cards=[]),
                     transfer_date=D, today=date(2026, 9, 8))
    assert r.verdict == "cash (no points path)"
    assert any("no currencies supplied" in x.lower() for x in r.warnings)


def test_a_currency_with_no_verified_partner_rows_funds_nothing(rm):
    """MR is a declared currency with no ratio rows. It must fund nothing."""
    l = leg(
        points_candidates=[
            PointsCandidate(label="p", program="United MileagePlus", points=20000)
        ],
    )
    r = evaluate_leg(l, ratios_manager=rm, wallet=w({"MR": 500000}),
                     transfer_date=D, today=date(2026, 9, 8))
    assert r.verdict == "cash (no points path)"


# ---------------------------------------------------------------------------
# Step 0 gate status
# ---------------------------------------------------------------------------


def test_a_real_seats_aero_response_is_recorded():
    """
    Step 0's capture half is DONE. Tsuki authenticated from his own Mac on
    2026-09-08 and recorded the first real response the project has ever seen.
    Every parser test runs against it; deleting it would put the parser back to
    being tested against an invented schema, which is how the wrong parser
    passed its tests for two versions.
    """
    recorded = list((ROOT / "tests" / "fixtures" / "seats_aero").glob("*.json"))
    assert recorded, "the recorded Seats.aero response is the parser's only ground truth"


def test_the_recorded_response_carries_no_api_key():
    """
    v3 widened this from iterdir() to rglob(): the fixture directory gained the
    `live_trip_b/` snapshot corpus underneath it, and a check that only looked at
    the top level would have stopped covering exactly the files that will hold
    real live responses.
    """
    checked = 0
    for path in (ROOT / "tests" / "fixtures" / "seats_aero").rglob("*"):
        if not path.is_file():
            continue
        checked += 1
        text = path.read_text()
        assert "Partner-Authorization: " not in text.replace(
            "Partner-Authorization: <key redacted>", ""
        ), f"{path} names the auth header with a value after it"
    assert checked, "nothing was checked - the fixture tree has moved"


def test_the_seats_aero_client_does_not_overclaim_verification():
    """
    The schema is verified against ONE captured response. Nothing has ever been
    executed against the live service from a build environment, and pagination
    was never observed because the capture was truncated. The banner must keep
    saying all three things.
    """
    text = (ROOT / "src" / "seats_client.py").read_text()
    assert "LIVE BEHAVIOUR FROM THIS MACHINE: STILL UNVERIFIED" in text
    assert "no network egress" in text
    assert "WAS NOT OBSERVED" in text


def test_the_seats_aero_directory_records_what_the_capture_did_and_did_not_settle():
    readme = ROOT / "tests" / "fixtures" / "seats_aero" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "STILL UNKNOWN" in text
    assert "pagination" in text.lower()
