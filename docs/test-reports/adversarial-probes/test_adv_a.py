"""
Adversarial probe A: parser/live-mode boundary + surcharge model.

Every test here is written to FAIL if the code is honest. A pass means the
attack landed.
"""
import copy
import json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src import config
from src.formatter import print_leg_results, print_live_leg_detail, print_trip_totals
from src.live_trip import (
    LiveOptions,
    apply_live,
    annotate_live_verdicts,
    award_to_candidate,
    provenance_counts,
    query_leg,
)
from src.models import (
    CashOption,
    Leg,
    LiveLegOutcome,
    LiveQueryState,
    PointsProvenance,
)
from src.optimizer import evaluate_trip, trip_totals, evaluate_leg
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient, parse_availability_row
from src.surcharge import SurchargeTable
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
TRIPS = ROOT / "tests" / "fixtures" / "trips"
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)

# TSUKI'S REAL UR BALANCE.
UR = 160_000


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


def _mock_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _render(fn, *a, **kw):
    buf = StringIO()
    fn(*a, console=Console(file=buf, width=200, no_color=True), **kw)
    return buf.getvalue()


def _leg(**kw):
    base = dict(
        id="B1",
        kind="flight",
        description="SFO->MAD",
        date=date(2027, 1, 15),
        origin="SFO",
        destination="MAD",
        cash_options=[CashOption(label="cash", amount=395.0, currency="USD")],
        points_candidates=[],
    )
    base.update(kw)
    return Leg(**base)


# =========================================================================
# A1. Rows the parser cannot read become "NO award space -- THIS IS A FINDING"
# =========================================================================


@patch("src.seats_client.requests.get")
def test_A1_unparseable_rows_are_reported_as_a_finding_of_no_award_space(
    mock_get, client
):
    """
    v2's bug, exactly: rows arrive, the parser produces nothing from them, and
    the emptiness is announced as a fact about the world.

    9 rows in, 9 rows skipped, 0 awards -> NO_AWARD_SPACE.
    """
    real = json.loads(REAL_FIXTURE.read_text())["data"][0]
    # A shape the parser cannot read: Date replaced with something unparseable.
    # (Any future schema drift in the date field produces exactly this.)
    broken = copy.deepcopy(real)
    broken["Date"] = "15/01/2027"          # a real date, a format the parser rejects
    broken["ParsedDate"] = "15/01/2027"
    mock_get.return_value = _mock_response({"data": [broken] * 9})

    outcome, awards = query_leg(_leg(), client, LiveOptions(live=True))

    print("state          :", outcome.state)
    print("rows_seen      :", outcome.rows_seen)
    print("rows_skipped   :", outcome.rows_skipped)
    print("awards         :", len(awards))
    print("error          :", repr(outcome.error))
    print("RENDER         :", outcome.render())

    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert outcome.rows_seen == 9 and outcome.rows_skipped == 9
    assert outcome.error == ""
    assert "THIS IS A FINDING" in outcome.render()
    assert "no award to buy on this date" in outcome.render()
    # And the render never mentions that 9 of 9 rows were unreadable:
    assert "skipped" not in outcome.render().lower()
    assert "unreadable" not in outcome.render().lower()
    assert "could not be parsed" not in outcome.render().lower()


@patch("src.seats_client.requests.get")
def test_A2_a_json_body_of_the_wrong_shape_is_reported_as_a_finding(mock_get, client):
    """A `data` that is a dict, not a list. No exception, no error, a 'finding'."""
    mock_get.return_value = _mock_response({"data": {"unexpected": "envelope"}})
    outcome, awards = query_leg(_leg(), client, LiveOptions(live=True))
    print("state:", outcome.state, "rows_seen:", outcome.rows_seen)
    print("RENDER:", outcome.render())
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in outcome.render()


@patch("src.seats_client.requests.get")
def test_A3_a_string_data_field_counts_characters_as_rows(mock_get, client):
    """`data` is a string -> rows_seen is its character count."""
    mock_get.return_value = _mock_response({"data": "no results"})
    outcome, _ = query_leg(_leg(), client, LiveOptions(live=True))
    print("rows_seen:", outcome.rows_seen, "state:", outcome.state)
    assert outcome.rows_seen == len("no results")
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE


# =========================================================================
# A4. An unconvertible tax currency collapses to $0 on a program-policy leg
# =========================================================================


@patch("src.seats_client.requests.get")
def test_A4_unconvertible_taxes_vanish_on_a_program_policy_zero_leg(mock_get, client, rm):
    """
    The v0 bug in a new hat: an Aeroplan award whose TaxesCurrency has no FX
    rate is SCORED, with the API's own tax figure silently dropped to nothing.
    """
    real = json.loads(REAL_FIXTURE.read_text())["data"][0]
    row = copy.deepcopy(real)
    row["TaxesCurrency"] = "MXN"      # no FX rate configured
    row["YTotalTaxes"] = 900000       # MXN 9,000.00  ~ $450 of real cash
    mock_get.return_value = _mock_response({"data": [row]})

    leg = _leg()
    class _F:  # a minimal fixture object
        legs = [leg]
    fixture, outcomes = apply_live(_F(), client, LiveOptions(live=True))

    cand = leg.points_candidates[0]
    print("program        :", cand.program)
    print("cash_surcharge :", cand.cash_surcharge)
    print("captured?      :", cand.surcharge_captured)
    print("carrier_source :", cand.carrier_source)

    res = evaluate_leg(
        leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
        transfer_date=TRANSFER_DATE, show_alternatives=False,
    )
    print("surcharge      :", res.surcharge.render(), res.surcharge.confidence)
    print("points score   :", res.points_total_score_usd)
    print("cash score     :", res.cash_total_score_usd)
    print("verdict        :", res.verdict)

    # The award's real cash component (MXN 9,000) is nowhere:
    assert res.surcharge.is_known
    assert res.surcharge.amount_point == 0.0
    assert res.points_total_score_usd == 500.0   # 50,000 pts @1cpp + $0
    # and the leg renders a confident, fully-scored number
    assert res.verdict == "cash"
    summary_only = _render(print_leg_results, [res])
    print("---- summary table ----")
    print(summary_only)
    # The per-leg summary table shows a clean, confident $0.00 modeled surcharge
    # and a total of $500.00 that is short by the whole MXN 9,000.
    assert "$0.00" in summary_only and "modeled" in summary_only
    assert "MXN" not in summary_only
    assert "$500.00" in summary_only


@patch("src.seats_client.requests.get")
def test_A4b_unconvertible_taxes_flip_a_verdict_the_wrong_way(mock_get, client, rm):
    """The same hole, on a leg where it decides the answer."""
    real = json.loads(REAL_FIXTURE.read_text())["data"][0]
    row = copy.deepcopy(real)
    row["TaxesCurrency"] = "MXN"
    row["YTotalTaxes"] = 900000        # MXN 9,000.00
    mock_get.return_value = _mock_response({"data": [row]})

    leg = _leg(cash_options=[CashOption(label="cash", amount=520.0, currency="USD")])
    class _F:
        legs = [leg]
    apply_live(_F(), client, LiveOptions(live=True))
    res = evaluate_leg(
        leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
        transfer_date=TRANSFER_DATE, show_alternatives=False,
    )
    print("verdict :", res.verdict)
    print("reason  :", res.verdict_reason)
    print("points  :", res.points_total_score_usd, "cash:", res.cash_total_score_usd)
    assert res.verdict == "points"
    assert res.margin_usd == 20.0
    assert res.verdict_sensitive is False
    assert res.points_floor_usd is None


# =========================================================================
# A5. Surcharge table: a MORE specific row loses to a LESS specific one
# =========================================================================


def test_A5_a_more_specific_row_is_unreachable_and_a_less_specific_row_wins(tmp_path):
    """
    _tier_keys() enumerates only 5 of the 16 possible wildcard patterns. A row
    with cabin=* but a specific departure_country is never consulted; the
    generic (program, carrier, *, *, *) row answers instead.
    """
    csv = tmp_path / "s.csv"
    csv.write_text(
        "program,operating_carrier,route_region,cabin,departure_country,amount_low,"
        "amount_point,amount_high,currency,basis,confidence,source,verified_on,notes\n"
        # more specific: BA metal, NA-EU, ANY cabin, departing GB -> $900
        "BAX,BA,NA-EU,*,GB,750,900,1100,USD,round_trip,modeled,src,2026-09-08,ex-UK APD\n"
        # less specific: BA metal, anything at all -> $50
        "BAX,BA,*,*,*,40,50,60,USD,round_trip,modeled,src,2026-09-08,generic\n"
    )
    t = SurchargeTable(csv)
    got = t.resolve("BAX", "BA", "NA-EU", "Y", "GB", is_round_trip=True)
    print("matched_rule:", got.matched_rule)
    print("amount      :", got.render())
    print("specificity of the ex-GB row:", t.rules[0].specificity)
    print("specificity of the generic  :", t.rules[1].specificity)
    # The ex-GB row is tier 3 by the table's own reckoning, the generic tier 4 -
    # yet the generic wins.
    assert t.rules[0].specificity < t.rules[1].specificity
    assert got.amount_point == 50.0, "the LESS specific row answered"
    assert "dep=*" in got.matched_rule


def test_A6_duplicate_rows_at_one_tier_resolve_by_file_order_not_by_error(tmp_path):
    """
    validate() raises on duplicates -- but only if somebody calls it. resolve()
    itself silently takes whichever row is LAST in the file.
    """
    csv = tmp_path / "s.csv"
    csv.write_text(
        "program,operating_carrier,route_region,cabin,departure_country,amount_low,"
        "amount_point,amount_high,currency,basis,confidence,source,verified_on,notes\n"
        "P,BA,NA-EU,J,GB,100,100,100,USD,round_trip,modeled,src,2026-09-08,first\n"
        "P,BA,NA-EU,J,GB,900,900,900,USD,round_trip,modeled,src,2026-09-08,second\n"
    )
    t = SurchargeTable(csv)
    got = t.resolve("P", "BA", "NA-EU", "J", "GB", is_round_trip=True)
    print("resolved to:", got.amount_point, "notes:", got.notes)
    assert got.amount_point == 900.0, "last row in the file silently wins"
    # reversing the file reverses the answer
    csv.write_text(
        "program,operating_carrier,route_region,cabin,departure_country,amount_low,"
        "amount_point,amount_high,currency,basis,confidence,source,verified_on,notes\n"
        "P,BA,NA-EU,J,GB,900,900,900,USD,round_trip,modeled,src,2026-09-08,second\n"
        "P,BA,NA-EU,J,GB,100,100,100,USD,round_trip,modeled,src,2026-09-08,first\n"
    )
    assert SurchargeTable(csv).resolve(
        "P", "BA", "NA-EU", "J", "GB", is_round_trip=True
    ).amount_point == 100.0


def test_A7_resolve_ambiguous_metal_edge_cases(tmp_path):
    t = SurchargeTable(ROOT / "data" / "surcharges.csv")
    kw = dict(region="NA-EU", cabin="J", departure_country="GB", is_round_trip=True)

    # empty list -> falls through to the carrier-unknown path
    e = t.resolve_ambiguous_metal("British Airways Executive Club", [], **kw)
    print("empty      :", e.render(), "|", e.confidence)

    # duplicates of one carrier: not deduplicated, so it takes the AMBIGUOUS path
    d = t.resolve_ambiguous_metal("British Airways Executive Club", ["BA", "BA"], **kw)
    print("dupes      :", d.render(), "| notes:", d.notes[:90])

    # whitespace + mixed case of ONE carrier -> same
    w = t.resolve_ambiguous_metal("British Airways Executive Club", [" ba ", "BA"], **kw)
    print("ws+case    :", w.render(), "| notes:", w.notes[:90])

    single = t.resolve_ambiguous_metal("British Airways Executive Club", ["BA"], **kw)
    print("single     :", single.render(), "| notes:", single.notes[:60])

    assert d.is_known and d.amount_point == 900.0
    # THE BUG: a single carrier written twice is announced as ambiguous metal.
    assert "OPERATING METAL AMBIGUOUS" in d.notes
    assert "BA, BA" in d.notes
    assert "OPERATING METAL AMBIGUOUS" not in single.notes
    assert "BA, BA" in w.notes


def test_A8_unknown_carrier_codes_and_program_policy(tmp_path):
    t = SurchargeTable(ROOT / "data" / "surcharges.csv")
    kw = dict(region="NA-EU", cabin="Y", departure_country="GB", is_round_trip=False)
    # Nonsense codes against a blanket-zero program still resolve to a confident $0
    z = t.resolve_ambiguous_metal("Air Canada Aeroplan", ["ZZ", "QQ", "!!"], **kw)
    print("aeroplan/garbage:", z.render(), z.confidence, "|", z.notes[:120])
    assert z.is_known and z.amount_point == 0.0
