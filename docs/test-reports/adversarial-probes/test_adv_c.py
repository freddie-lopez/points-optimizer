"""
Adversarial probe C: HTML export, malformed-fixture matrix, Raw fields,
FX gaps, the budget->cache->"finding" chain, and VERDICT_SENSITIVE.
"""
import copy
import json
import re
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src import config
from src.formatter import export_html, print_leg_results, print_trip_totals
from src.live_trip import LiveOptions, apply_live, query_leg
from src.models import (
    Award,
    CashOption,
    DateRange,
    Leg,
    LiveQueryState,
    PointsCandidate,
    Strategy,
    TransferPath,
)
from src.optimizer import evaluate_leg, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient, parse_availability_row
from src.surcharge import SurchargeTable
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)
UR = 160_000


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


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


def _real_row():
    return copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])


# =========================================================================
# C1. The HTML export renders an UNKNOWN cash component as $0.00
# =========================================================================


def test_C1_html_export_prints_an_unknown_cash_component_as_zero_dollars(tmp_path):
    """
    Strategy carries cash_cost_known / cash_cost_note / is_lower_bound EXACTLY so
    an unconvertible tax cannot be totalled as zero. export_html reads none of
    them.
    """
    award = Award(
        date=date(2027, 1, 15), program="Air Canada Aeroplan", award_type="Y",
        cost=50000, cash_component=0.0, airline="AC", route="SFO-MAD",
        cash_component_known=False,
        cash_component_source_amount=9000.0, cash_component_currency="MXN",
        cash_component_note="No FX rate for MXN is configured.",
    )
    s = Strategy(award=award, transfer_path=TransferPath(), points_cost=50000,
                 cash_cost=0.0, total_value=500.0, cash_cost_known=False,
                 cash_cost_note="The cash component of this award is unknown.")
    assert s.is_lower_bound is True

    out = tmp_path / "r.html"
    export_html([s], str(out))
    html = out.read_text()
    print(html[html.index("<table>"):])
    assert "<td>$0.00</td>" in html, "UNKNOWN rendered as $0.00 in the HTML export"
    assert "unknown" not in html.lower().replace("points optimizer", "")
    assert "MXN" not in html
    assert "lower bound" not in html.lower()
    assert "9,000" not in html


def test_C1b_html_export_also_drops_the_unverified_badge_warning(tmp_path):
    award = Award(
        date=date(2027, 1, 15), program="British Airways Executive Club",
        award_type="Y", cost=22500, cash_component=0.0, airline="BA",
        route="SFO-MAD", source="google_badge_unverified",
        source_note="Google Flights badge. UNVERIFIED.",
    )
    s = Strategy(award=award, transfer_path=TransferPath(), points_cost=22500,
                 cash_cost=0.0, total_value=225.0)
    out = tmp_path / "r2.html"
    export_html([s], str(out))
    html = out.read_text()
    assert "google_badge_unverified" not in html
    assert "UNVERIFIED" not in html
    print("HTML mentions provenance:", "badge" in html.lower())


# =========================================================================
# C2. Malformed-fixture matrix on the real capture
# =========================================================================


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda r: r.pop("Route"), "missing Route"),
        (lambda r: r["Route"].pop("Source"), "missing Route.Source"),
        (lambda r: r["Route"].update(Source="hawaiianairlines"), "unknown source code"),
        (lambda r: r.update(TaxesCurrency=None), "null TaxesCurrency"),
        (lambda r: r.update(TaxesCurrency="ZWL"), "TaxesCurrency with no FX rate"),
        (lambda r: r.update(YMileageCost="-50000"), "negative mileage"),
        (lambda r: r.update(YMileageCost="99999999999999"), "absurd mileage"),
        (lambda r: r.update(YMileageCost="fifty thousand"), "string mileage"),
        (lambda r: r.update(YMileageCost=None), "null mileage"),
        (lambda r: r.update(YTotalTaxes="not a number"), "string taxes"),
        (lambda r: r.update(YTotalTaxes=-4460), "negative taxes"),
        (lambda r: r.update(YRemainingSeats="lots"), "unparseable seats"),
        (lambda r: r.update(YAirlines=" , , "), "whitespace-only carriers"),
        (lambda r: r.update(Date=None), "null Date"),
    ],
)
def test_C2_malformed_rows_never_raise_and_never_price_from_nothing(mutate, label):
    row = _real_row()
    mutate(row)
    awards = parse_availability_row(row)
    print(f"{label:<34} -> {len(awards)} award(s)", end="")
    for a in awards:
        print(f"  program={a.program!r} cost={a.cost} taxes_known={a.cash_component_known}"
              f" cash={a.cash_component} cur={a.cash_component_currency!r}"
              f" carriers={a.candidate_carriers}", end="")
    print()
    for a in awards:
        assert a.cost > 0, f"{label}: a non-positive price reached an Award"
        if not a.cash_component_known:
            assert a.cash_component == 0.0  # documented, but see C3


def test_C3_an_absurd_mileage_price_is_accepted_verbatim():
    row = _real_row()
    row["YMileageCost"] = "99999999999999"
    a = parse_availability_row(row)[0]
    print("cost:", a.cost)
    assert a.cost == 99999999999999
    # nothing anywhere flags it as implausible
    assert "implausible" not in a.source_note.lower()


def test_C4_missing_route_yields_an_award_with_an_empty_program_name():
    row = _real_row()
    row.pop("Route")
    a = parse_availability_row(row)[0]
    print("program:", repr(a.program), "route:", repr(a.route),
          "region:", repr(a.route_region), "ur:", a.ur_transferable)
    assert a.program == "" and a.cost == 50000
    assert a.ur_transferable is None


def test_C5_an_unknown_source_code_becomes_the_program_name(rm):
    row = _real_row()
    row["Route"]["Source"] = "hawaiianairlines"
    a = parse_availability_row(row)[0]
    print("program:", repr(a.program), "| normalized:",
          repr(rm.normalize_program(a.program)))
    assert a.program == "hawaiianairlines"


def test_C6_raw_fields_never_reach_a_scored_award():
    """The fixture's own trap: JMileageCostRaw 470500 against JAvailable false."""
    awards = parse_availability_row(_real_row())
    print("awards:", [(a.award_type, a.cost) for a in awards])
    assert [a.cost for a in awards] == [50000]
    blob = json.dumps([a.__dict__ for a in awards], default=str)
    # PROVEN: 470,500 cannot surface as a price. It is not even retained --
    # raw_diagnostics is built per EMITTED cabin, and J is never emitted, so
    # the parser-fix report's claim that Raw values are "retained in
    # Award.raw_diagnostics" is false for a skipped cabin.
    assert "470500" not in blob
    assert "5590" not in blob
    print("raw_diagnostics:", awards[0].raw_diagnostics)


def test_C6b_raw_would_be_scored_if_the_clean_field_were_ever_populated():
    """The fence is `cost <= 0`, not `never read Raw`. Confirm the fence holds."""
    row = _real_row()
    row["JAvailable"] = True          # clean availability now true
    row["JMileageCost"] = "0"         # clean price still 0
    awards = parse_availability_row(row)
    print("cabins:", [(a.award_type, a.cost) for a in awards])
    assert [a.award_type for a in awards] == ["Y"], "J correctly skipped"


# =========================================================================
# C7. FX: a cash price in a currency with no rate crashes the run
# =========================================================================


def test_C7_a_cash_option_in_an_unconfigured_currency_raises_out_of_evaluate_leg(rm):
    leg = Leg(id="L1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD",
              cash_options=[CashOption(label="c", amount=50000.0, currency="JPY")])
    with pytest.raises(ValueError) as e:
        evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                     transfer_date=TRANSFER_DATE, show_alternatives=False)
    print("raised:", e.value)
    assert "No FX rate configured" in str(e.value)


def test_C7b_a_mandatory_fee_in_an_unconfigured_currency_crashes_too(rm):
    from src.models import MandatoryFee
    leg = Leg(id="H1", kind="hotel", description="x", date=date(2027, 1, 15),
              nights=3,
              cash_options=[CashOption(label="c", amount=500.0, currency="USD")],
              mandatory_fees=[MandatoryFee(label="city tax", amount=10.0,
                                           currency="JPY", per="night")])
    with pytest.raises(ValueError):
        evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                     transfer_date=TRANSFER_DATE, show_alternatives=False)


# =========================================================================
# C8. A budget failure is CACHED as an empty response and re-served as a
#     "finding of no award space" for the whole TTL.
# =========================================================================


@patch("src.seats_client.requests.get")
def test_C8_budget_exhaustion_is_written_to_the_cache_as_an_empty_response(
    mock_get, client, tmp_path
):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP     # budget gone
    dr = DateRange(date(2027, 1, 15), date(2027, 1, 15))
    awards = client.search("SFO", "MAD", dr, cache=cache)
    print("http calls:", mock_get.call_count, "awards:", len(awards))
    print("note      :", client.last_pagination_note)

    entries = list(cache.cache_dir.glob("*.json"))
    snaps = [p for p in cache.snapshot_dir.glob("*.json")]
    print("cache files:", [p.name for p in entries])
    print("snapshots  :", [p.name for p in snaps])
    assert mock_get.call_count == 0
    assert entries, "a zero-page envelope was written to the cache"
    env = json.loads(entries[0].read_text())
    assert env["pages"] == []
    # ...and it is committed to the regression corpus as a snapshot too
    assert snaps

    # Now a fresh client with a full budget gets the cached emptiness back.
    SeatsClient.reset_call_budget()
    c2 = SeatsClient(api_key="test_key")
    c2.clear_cache()
    leg = Leg(id="L1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD",
              cash_options=[CashOption(label="c", amount=395.0)])
    outcome, _ = query_leg(leg, c2, LiveOptions(live=True, cache=cache))
    print("second run state :", outcome.state)
    print("second run render:", outcome.render())
    assert mock_get.call_count == 0, "no API call: served from the cache"
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in outcome.render()
    SeatsClient.reset_call_budget()


# =========================================================================
# C9. VERDICT_SENSITIVE
# =========================================================================


def _band_leg(cash, program, carrier, points, cabin="J"):
    return Leg(
        id="V1", kind="flight", description="LHR->SFO", date=date(2027, 1, 15),
        origin="LHR", destination="SFO",
        cash_options=[CashOption(label="c", amount=cash, currency="USD")],
        points_candidates=[PointsCandidate(
            label="cand", program=program, points=points, source="seats_aero",
            operating_carrier=carrier, carrier_source="seats_aero", cabin=cabin,
            is_round_trip=True)],
    )


@pytest.mark.parametrize("cash,expect_sensitive", [
    # points side: low $980, point $1,130, high $1,330 (23,000 UR + BA ex-GB band)
    (1000.0, True),    # flip strictly inside the band
    (980.0, False),    # cash == points_low exactly: points never strictly win
    (1330.0, True),    # cash == points_high exactly: flips
    (1331.0, False),   # points win at both ends
    (979.0, False),    # points lose at both ends
])
def test_C9_verdict_sensitive_fires_on_boundaries(rm, cash, expect_sensitive):
    """BA J ex-GB row: 750/900/1100 round trip, plus 22,500 pts = $225."""
    leg = _band_leg(cash, "British Airways Executive Club", "BA", 22500)
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    print(f"cash={cash:>8}  low={res.points_score_low_usd}  "
          f"pt={res.points_total_score_usd}  high={res.points_score_high_usd}  "
          f"verdict={res.verdict}  sensitive={res.verdict_sensitive}")
    assert res.verdict_sensitive is expect_sensitive


def test_C10_an_unknown_surcharge_never_renders_as_zero_in_the_leg_table(rm):
    leg = _band_leg(1000.0, "British Airways Executive Club", "BA", 22500, cabin="Y")
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    out = _render(print_leg_results, [res])
    print(out)
    assert res.surcharge is not None and not res.surcharge.is_known
    assert "UNKNOWN" in out
    assert "$0.00" not in out
