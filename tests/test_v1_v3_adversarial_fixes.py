"""
Regression tests for the 25 findings in docs/test-reports/v1-v3-adversarial.md.

WHY THIS FILE EXISTS SEPARATELY FROM THE PROBES.

The Tester's 78 probe scripts live at `docs/test-reports/adversarial-probes/`,
OUTSIDE `pytest.ini`'s testpaths, and each one asserts that a defect IS PRESENT.
They were the acceptance criteria for the fix - a finding is fixed when its probe
flips from pass to fail - and by construction they are not maintained as the code
moves on. That makes them a one-shot instrument, not a regression net.

So each finding is pinned HERE, asserting the CORRECT behaviour, inside the suite
that actually runs. Without this file, every fix below could silently regress and
the 470-test suite would stay green.

The findings collapse into three root causes and the file is organised that way:

  ROOT CAUSE 1 - "we did not get usable data, so we said there is nothing there"
                 (C-1, H-1, H-2, M-5, M-8, M-9, L-3, L-4)
  ROOT CAUSE 2 - "the API's tax figure and the modeled surcharge share one float"
                 (C-2, M-3, M-4)
  ROOT CAUSE 3 - a missing constraint: the balance ceiling does not exist at trip
                 level (C-3)
  plus the independent findings (H-3, H-4, H-5, M-1, M-2, M-6, M-7, L-1, L-2,
  L-5, L-6, L-7, L-8).
"""
import copy
import errno
import json
import tempfile
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src.formatter import (
    export_html,
    print_leg_results,
    print_trip_totals,
)
from src.live_trip import (
    LiveOptions,
    annotate_live_verdicts,
    apply_live,
    provenance_counts,
    query_leg,
)
from src.models import (
    Award,
    CashOption,
    DateRange,
    Leg,
    LiveLegOutcome,
    LiveQueryState,
    PointsCandidate,
    PointsProvenance,
    Strategy,
    TransferPath,
)
from src.optimizer import (
    VERDICT_AWARD_UNATTRIBUTED,
    evaluate_leg,
    evaluate_trip,
    trip_residue,
    trip_totals,
)
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import (
    MAX_PLAUSIBLE_MILEAGE,
    SeatsClient,
    convert_taxes,
    parse_availability_row,
    resolve_source,
)
from src.surcharge import SurchargeTable, SurchargeTableError
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)

# TSUKI'S REAL UR BALANCE. Every feasibility number below is against this figure.
UR = 160_000

SURCHARGE_HEADER = (
    "program,operating_carrier,route_region,cabin,departure_country,amount_low,"
    "amount_point,amount_high,currency,basis,confidence,source,verified_on,notes\n"
)


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


def _real_row(day=date(2027, 1, 15), **over):
    row = copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])
    row["Date"] = str(day)
    row["ParsedDate"] = f"{day}T00:00:00Z"
    row.update(over)
    return row


def _flight(lid="L1", origin="SFO", destination="MAD", day=date(2027, 1, 15),
            cash=395.0, cands=None, cash_opts=None):
    return Leg(
        id=lid, kind="flight", description=f"{origin}->{destination}", date=day,
        origin=origin, destination=destination,
        cash_options=(
            cash_opts
            if cash_opts is not None
            else [CashOption(label="c", amount=cash, currency="USD")]
        ),
        points_candidates=cands or [],
    )


class _F:
    def __init__(self, legs):
        self.legs = legs
        self.id = "regression"
        self.name = "regression"


# =========================================================================
# ROOT CAUSE 1: a failure presented as a finding
# =========================================================================


@patch("src.seats_client.requests.get")
def test_C1_unparseable_rows_are_NOT_a_finding_of_no_award_space(mock_get, client):
    """
    THE SIGNATURE FAILURE MODE, FOR THE THIRD TIME, AND ITS FIX.

    Nine rows whose Date the parser rejects produced zero awards, and `query_leg`
    reported that as NO_AWARD_SPACE - rendered as "THIS IS A FINDING: the API
    answered, and the answer is that there is no award to buy on this date". It
    is not a finding. It is a parser failure.
    """
    broken = _real_row()
    broken["Date"] = "15/01/2027"       # a real date in a format we reject
    broken["ParsedDate"] = "15/01/2027"
    mock_get.return_value = _mock_response({"data": [broken] * 9})

    outcome, awards = query_leg(_flight(), client, LiveOptions(live=True))

    assert outcome.state is LiveQueryState.ANSWERED_UNREADABLE
    assert outcome.rows_seen == 9
    assert outcome.rows_unreadable == 9
    assert outcome.rows_without_availability == 0
    assert awards == []
    # The state says nothing about award space, and the render says so in words.
    assert outcome.tells_us_nothing_about_award_space is True
    assert outcome.state.is_a_finding_about_award_space is False
    text = outcome.render()
    assert "THIS IS NOT A FINDING" in text
    assert "COULD NOT BE PARSED" in text
    assert "no award to buy" not in text
    # rows_skipped is SURFACED, not stored and forgotten.
    assert "9 of 9 rows" in text


def test_C1_the_invariant_refuses_to_build_the_broken_combination():
    """The type system, not a convention, is what stops this coming back."""
    with pytest.raises(ValueError) as e:
        LiveLegOutcome(
            leg_id="X",
            state=LiveQueryState.NO_AWARD_SPACE,
            provenance=PointsProvenance.UNAVAILABLE,
            rows_seen=9,
            rows_skipped=9,
            rows_unreadable=9,
        )
    assert "UNREADABLE" in str(e.value)
    assert "ANSWERED_UNREADABLE" in str(e.value)
    # An error state must still say what went wrong.
    with pytest.raises(ValueError):
        LiveLegOutcome(
            leg_id="X",
            state=LiveQueryState.ANSWERED_UNREADABLE,
            provenance=PointsProvenance.UNAVAILABLE,
            rows_unreadable=1,
        )


def test_C1_the_invariant_survives_python_O():
    """As with the original five states: a ValueError, never an assert."""
    import subprocess

    code = (
        "from src.models import LiveLegOutcome, LiveQueryState, PointsProvenance\n"
        "try:\n"
        "    LiveLegOutcome(leg_id='X', state=LiveQueryState.NO_AWARD_SPACE,\n"
        "                   provenance=PointsProvenance.UNAVAILABLE,\n"
        "                   rows_seen=9, rows_skipped=9, rows_unreadable=9)\n"
        "    print('NO RAISE')\n"
        "except ValueError:\n"
        "    print('RAISED')\n"
    )
    for flags in ([], ["-O"], ["-OO"]):
        out = subprocess.run(
            ["python", *flags, "-c", code], cwd=str(ROOT), capture_output=True,
            text=True,
        )
        assert out.stdout.strip() == "RAISED", flags


@patch("src.seats_client.requests.get")
def test_C1_a_genuinely_empty_response_is_still_a_finding(mock_get, client):
    """
    THE OTHER HALF, AND IT MATTERS AS MUCH. The fix must not make every empty
    answer 'unreadable' - a response we READ that carries no bookable cabin is
    real evidence about award space and stays a finding.
    """
    mock_get.return_value = _mock_response({"data": []})
    outcome, awards = query_leg(_flight(), client, LiveOptions(live=True))
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in outcome.render()

    # And a row that reads cleanly with nothing available is also a finding.
    client.clear_cache()
    nothing = _real_row(YAvailable=False)
    mock_get.return_value = _mock_response({"data": [nothing]})
    outcome, _ = query_leg(_flight(), client, LiveOptions(live=True))
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert outcome.rows_unreadable == 0
    assert outcome.rows_without_availability == 1
    assert "THIS IS A FINDING" in outcome.render()
    assert "carried no bookable cabin" in outcome.render()


@pytest.mark.parametrize(
    "payload,label",
    [
        ({"data": {"unexpected": "envelope"}}, "data is a dict"),
        ({"data": "no results"}, "data is a string"),
        ({"unexpected": "shape"}, "neither data nor results"),
    ],
)
@patch("src.seats_client.requests.get")
def test_C1_a_response_body_of_the_wrong_shape_is_unreadable(
    mock_get, client, payload, label
):
    """
    The two cheap variants. `{"data": {...}}` iterated a dict and yielded key
    strings; `{"data": "no results"}` made `rows_seen` the CHARACTER COUNT of
    the string, so a leg reported a finding over ten imaginary rows.
    """
    mock_get.return_value = _mock_response(payload)
    outcome, _ = query_leg(_flight(), client, LiveOptions(live=True))
    assert outcome.state is LiveQueryState.ANSWERED_UNREADABLE, label
    assert outcome.rows_seen == 0, "a string's length is not a row count"
    assert "THIS IS NOT A FINDING" in outcome.render()


@patch("src.seats_client.requests.get")
def test_H1_a_budget_failure_is_never_cached_or_snapshotted(mock_get, client):
    """
    A zero-page envelope written by an exhausted budget was cached AND committed
    to the regression corpus, then re-served for six hours as a confident
    finding of no award space, with zero API calls to contradict it.
    """
    tmp = Path(tempfile.mkdtemp())
    cache = ResponseCache(cache_dir=tmp / "c", snapshot_dir=tmp / "s")
    SeatsClient.reset_call_budget()
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP

    outcome, awards = query_leg(
        _flight(), client, LiveOptions(live=True, cache=cache)
    )

    assert mock_get.call_count == 0
    # NOT a finding: a request that was never sent says nothing about the world.
    assert outcome.state is LiveQueryState.BUDGET_EXHAUSTED
    assert "THIS IS A BUDGET FAILURE, not a finding" in outcome.render()
    assert list(cache.cache_dir.glob("*.json")) == []
    assert list(cache.snapshot_dir.glob("*.json")) == []
    assert any("NOTHING was written to the cache" in w for w in cache.warnings)
    SeatsClient.reset_call_budget()


@patch("src.seats_client.requests.get")
def test_H2_hasMore_with_no_cursor_is_reported_as_INCOMPLETE(mock_get, client):
    """
    The note said the response "carried hasMore and none of them indicated a
    further page" - asserting the opposite of what the payload said - and
    carried no INCOMPLETE marker, which is the only token main.py reddens on.
    """
    mock_get.return_value = _mock_response({"data": [_real_row()], "hasMore": True})
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    note = client.last_pagination_note
    assert mock_get.call_count == 1, "still must not invent an offset scheme"
    assert "INCOMPLETE" in note
    assert "none of them indicated a further page" not in note


@patch("src.seats_client.requests.get")
def test_M8_a_skip_that_does_not_advance_stops_after_one_call(mock_get, client):
    """
    `skip: 0` was echoed back unchanged, re-requesting page one 25 times: 2.5%
    of the daily budget for one page of data, duplicated 25 times into `pages`.
    """
    mock_get.return_value = _mock_response(
        {"data": [_real_row()], "hasMore": True, "skip": 0}
    )
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    assert mock_get.call_count == 1
    assert "INCOMPLETE" in client.last_pagination_note
    assert "does not advance" in client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_M8_a_skip_that_DOES_advance_is_still_followed(mock_get, client):
    """The fence must not break real pagination."""
    pages = [
        {"data": [_real_row()], "hasMore": True, "skip": 25},
        {"data": [_real_row()], "hasMore": False},
    ]
    mock_get.side_effect = [_mock_response(p) for p in pages]
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    assert mock_get.call_count == 2
    assert "INCOMPLETE" not in client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_M9_a_disk_write_failure_does_not_lose_a_good_response(mock_get, client):
    """
    `cache.put()` ran outside any try, so ENOSPC after a successful parse
    discarded the awards, spent a call, and reported that the API "COULD NOT BE
    REACHED" - a false message with the evidence thrown away behind it.
    """
    tmp = Path(tempfile.mkdtemp())
    cache = ResponseCache(cache_dir=tmp / "c", snapshot_dir=tmp / "s")
    mock_get.return_value = _mock_response({"data": [_real_row()]})
    real_write = Path.write_text

    def _enospc(self, *a, **kw):
        if self.suffix == ".json":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(self, *a, **kw)

    with patch.object(Path, "write_text", _enospc):
        outcome, awards = query_leg(
            _flight(), client, LiveOptions(live=True, cache=cache)
        )

    assert outcome.state is LiveQueryState.OK
    assert len(awards) == 1
    assert "COULD NOT BE REACHED" not in outcome.render()
    assert any("COULD NOT ARCHIVE" in w for w in cache.warnings)


@patch("src.seats_client.requests.get")
def test_M5_an_award_with_no_program_is_not_a_claim_about_partnerships(
    mock_get, client, rm
):
    """
    `resolve_source(None)` produced an empty program name, which reached
    `is_partner("UR", "")` -> False -> "No UR transfer partner covers this leg.
    Cash is the only option." A definitive statement about Chase's partner list,
    generated from a MISSING FIELD.
    """
    row = _real_row()
    row.pop("Route")
    mock_get.return_value = _mock_response({"data": [row]})
    leg = _flight()
    apply_live(_F([leg]), client, LiveOptions(live=True))

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.verdict == VERDICT_AWARD_UNATTRIBUTED
    assert "NAMED NO PROGRAM" in res.verdict_reason
    assert "No UR transfer partner covers this leg" not in res.verdict_reason
    assert not any("is not a transfer partner" in w for w in res.warnings)
    assert any("DID NOT NAME A PROGRAM" in w for w in res.warnings)
    assert any(r.code == "PROGRAM_UNATTRIBUTED" for r in res.reasons)
    # The award is NOT dropped - it is real availability.
    assert leg.points_candidates and leg.points_candidates[0].points == 50000


def test_L3_an_implausible_mileage_price_is_refused():
    """`YMileageCost: "99999999999999"` was accepted verbatim into an Award."""
    row = _real_row(YMileageCost="99999999999999")
    assert parse_availability_row(row) == []
    # And the fence has an inside as well as an outside.
    ok = _real_row(YMileageCost=str(MAX_PLAUSIBLE_MILEAGE))
    assert parse_availability_row(ok)[0].cost == MAX_PLAUSIBLE_MILEAGE


def test_L3_an_implausible_price_is_UNREADABLE_not_no_availability():
    """It is a row we could not read, not a row with nothing in it."""
    detail = SeatsClient.parse_pages_detail(
        [{"data": [_real_row(YMileageCost="99999999999999")]}]
    )
    assert detail.rows_unreadable == 1
    assert detail.rows_without_availability == 0
    assert any("plausibility ceiling" in r for r in detail.unreadable_reasons)


def test_L4_an_unmapped_source_code_is_not_used_as_a_program_name():
    """
    Source "hawaiianairlines" became `Award.program == "hawaiianairlines"`,
    which the formatter printed in a Program column as if it were one.
    """
    program, ur, note = resolve_source("hawaiianairlines")
    assert program == ""
    assert ur is None, "unknown is not the same answer as 'not a partner'"
    assert "hawaiianairlines" in note
    award = parse_availability_row(
        _real_row(Route={**_real_row()["Route"], "Source": "hawaiianairlines"})
    )[0]
    assert award.program == ""
    # The code itself is preserved, so a mapping gap is diagnosable.
    assert award.program_source_code == "hawaiianairlines"
    assert award.cost == 50000, "the award is still reported"


# =========================================================================
# ROOT CAUSE 2: taxes and surcharge sharing one float field
# =========================================================================


@patch("src.seats_client.requests.get")
def test_C2_an_unconvertible_tax_currency_cannot_score_a_leg(mock_get, client, rm):
    """
    THE VERDICT-FLIPPING ONE. An Aeroplan award with TaxesCurrency "MXN" and MXN
    9,000 of taxes had the amount dropped to 0.0 (because it could not be
    converted), then collected the tier-5 program-policy $0 from the MODELED
    table - a figure for a DIFFERENT QUANTITY - and scored $500 against $520
    cash: POINTS, margin $20.00, verdict_sensitive False, no floor shown. The
    true cost is about $950.
    """
    row = _real_row(TaxesCurrency="MXN", YTotalTaxes=900000)   # MXN 9,000.00
    mock_get.return_value = _mock_response({"data": [row]})
    leg = _flight(cash=520.0)
    apply_live(_F([leg]), client, LiveOptions(live=True))

    cand = leg.points_candidates[0]
    # The two quantities are now separate fields and neither is faked.
    assert cand.taxes_unconvertible is True
    assert cand.surcharge_captured is False
    assert cand.observed_taxes_known is False
    assert cand.observed_taxes_amount == 9000.0
    assert cand.observed_taxes_currency == "MXN"

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.verdict != "points"
    assert res.surcharge is not None and not res.surcharge.is_known
    assert res.points_total_score_usd == float("inf")
    assert res.points_floor_usd == pytest.approx(500.0)
    assert res.break_even_surcharge_usd == pytest.approx(20.0)
    # The per-leg table must not show a confident $0.00 / modeled.
    out = _render(print_leg_results, [res])
    assert "UNKNOWN" in out
    for line in out.splitlines():
        if "UNKNOWN" in line:
            assert "$0.00" not in line


@patch("src.seats_client.requests.get")
def test_C2_a_convertible_tax_still_scores_exactly_as_before(mock_get, client, rm):
    """The fix must not disarm the program-policy case it was built for."""
    mock_get.return_value = _mock_response({"data": [_real_row()]})   # CAD 44.60
    leg = _flight(cash=900.0)
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    assert cand.surcharge_captured is True
    assert cand.observed_taxes_are_the_surcharge is True
    # The taxes ride as the captured surcharge and are NOT added a second time.
    assert cand.extra_observed_taxes_usd == 0.0
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.verdict == "points"
    assert res.points_total_score_usd == pytest.approx(532.36176)


@patch("src.seats_client.requests.get")
def test_M3_a_negative_tax_is_never_a_cash_credit(mock_get, client, rm):
    """
    `YTotalTaxes: -50000` became CAD -500.00, converted to -$362.80, and rode
    into the score as a CAPTURED surcharge - the strongest confidence tier -
    making a $500 award cost $137.20 and win its leg.
    """
    known, amount, cur, usd, note = convert_taxes(-50000, "CAD")
    assert known is False and usd == 0.0
    assert "NEGATIVE" in note

    mock_get.return_value = _mock_response({"data": [_real_row(YTotalTaxes=-50000)]})
    leg = _flight(cash=395.0)
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    assert cand.cash_surcharge >= 0.0
    assert cand.observed_taxes_usd >= 0.0

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.points_total_score_usd >= 500.0 or res.points_total_score_usd == float(
        "inf"
    ), "a negative tax must never make points cheaper"
    assert res.verdict != "points"
    assert "$-" not in _render(print_leg_results, [res])


@patch("src.seats_client.requests.get")
def test_M4_the_floor_contains_the_APIs_own_known_taxes(mock_get, client, rm):
    """
    On the unscoreable path the candidate's cash figure was forced to 0.0, so
    the API's KNOWN CAD 44.60 never reached the floor - while the formatter went
    on labelling that number "points at the run's valuation + the API's taxes...
    the least this can possibly cost". The label named a component the figure did
    not contain.
    """
    row = _real_row()
    row["Route"]["Source"] = "flyingblue"    # no tier-5 zero for this program
    row["YAirlines"] = "AF"
    mock_get.return_value = _mock_response({"data": [row]})
    leg = _flight(cash=900.0)
    apply_live(_F([leg]), client, LiveOptions(live=True))

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.surcharge is not None and not res.surcharge.is_known
    # 50,000 pts @1cpp = $500.00, + a known CAD 44.60 = $32.36.
    assert res.points_floor_usd == pytest.approx(532.36176)
    assert res.observed_taxes_usd == pytest.approx(32.36176)
    assert res.break_even_surcharge_usd == pytest.approx(900.0 - 532.36176)


# =========================================================================
# ROOT CAUSE 3: no balance ceiling across legs
# =========================================================================


def _united_leg(lid, cash, points=70000):
    return _flight(
        lid=lid, cash=cash,
        cands=[PointsCandidate(
            label=f"{lid} United", program="United MileagePlus", points=points,
            source="seats_aero", operating_carrier="UA",
            carrier_source="seats_aero", cabin="Y",
        )],
    )


def test_C3_the_trip_never_recommends_spending_more_points_than_exist(rm):
    """
    THE ONE THAT WOULD HAVE COST REAL POINTS. Three legs at 70,000 United points
    each were funded independently from the FULL balance, spending 210,000 UR out
    of 160,000, and the headline printed a clean 22.22%. "OVERDRAWN" appeared
    only in the residue table, which main.py prints AFTER the headline.
    """
    legs = [_united_leg("L1", 900.0), _united_leg("L2", 900.0),
            _united_leg("L3", 900.0)]
    wallet = Wallet({"UR": UR}, [CSP])
    results = evaluate_trip(legs, ratios_manager=rm, wallet=wallet,
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    totals = trip_totals(results, wallet=wallet)
    residue = trip_residue(results, wallet)

    assert totals["points_spent"] <= UR
    assert totals["points_spent"] == 140_000
    assert residue["UR"]["remaining"] == 20_000
    assert residue["UR"]["overdrawn"] is False
    assert totals["trip_funding_executable"] is True
    # The leg that lost out says WHY, and says it is the trip that ran out.
    demoted = totals["trip_legs_demoted_for_balance"]
    assert len(demoted) == 1
    loser = next(r for r in results if r.leg.id in demoted)
    assert loser.verdict != "points"
    assert loser.demoted_for_trip_balance is True
    assert "THE TRIP RAN OUT OF POINTS" in loser.verdict_reason
    assert any(r.code == "TRIP_BALANCE_EXHAUSTED" for r in loser.reasons)


def test_C3_the_constraint_is_stated_BEFORE_the_headline(rm):
    """A recommendation you cannot execute is not a recommendation."""
    legs = [_united_leg("L1", 900.0), _united_leg("L2", 900.0),
            _united_leg("L3", 900.0)]
    wallet = Wallet({"UR": UR}, [CSP])
    results = evaluate_trip(legs, ratios_manager=rm, wallet=wallet,
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    out = _render(print_trip_totals, trip_totals(results, wallet=wallet))
    assert "BINDING CONSTRAINT" in out
    headline_at = out.index("beats paying cash by")
    assert out.index("BINDING CONSTRAINT") < headline_at, (
        "the balance constraint must be printed before the headline number"
    )


def test_C3_a_trip_that_fits_the_balance_is_completely_unchanged(rm):
    """The ceiling must be invisible when it does not bind."""
    legs = [_united_leg("L1", 900.0, points=50000),
            _united_leg("L2", 900.0, points=50000)]
    wallet = Wallet({"UR": UR}, [CSP])
    results = evaluate_trip(legs, ratios_manager=rm, wallet=wallet,
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    totals = trip_totals(results, wallet=wallet)
    assert [r.verdict for r in results] == ["points", "points"]
    assert totals["points_spent"] == 100_000
    assert totals["trip_legs_demoted_for_balance"] == []
    assert totals["trip_balance_bound"] is False


def test_C3_an_unconstrained_balance_still_imposes_no_ceiling(rm):
    """A balance of None means UNCONSTRAINED and must not be treated as zero."""
    legs = [_united_leg("L1", 900.0), _united_leg("L2", 900.0),
            _united_leg("L3", 900.0)]
    wallet = Wallet({"UR": None}, [CSP])
    results = evaluate_trip(legs, ratios_manager=rm, wallet=wallet,
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert [r.verdict for r in results] == ["points", "points", "points"]


def test_C3_totals_without_a_wallet_do_not_claim_the_check_passed(rm):
    """Silence about a check that did not run is how C-3 hid for three versions."""
    legs = [_united_leg("L1", 900.0)]
    results = evaluate_trip(legs, ratios_manager=rm,
                            wallet=Wallet({"UR": UR}, [CSP]),
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    totals = trip_totals(results)          # no wallet
    assert totals["trip_balance_checked"] is False
    assert "NOT" in totals["trip_funding_note"]


# =========================================================================
# The independent findings
# =========================================================================


def test_H3_the_html_export_carries_every_marker_the_cli_carries(tmp_path):
    """
    `export_html` read none of `cash_cost_known` / `cash_cost_note` /
    `is_lower_bound`, so an unconvertible tax exported as a clean `$0.00` with a
    clean total. v1's non-negotiable is "unknown never renders as $0 - output,
    totals, HTML export"; the export path was exempt.
    """
    award = Award(
        date=date(2027, 1, 15), program="Air Canada Aeroplan", award_type="Y",
        cost=50000, cash_component=0.0, airline="AC", route="SFO-MAD",
        cash_component_known=False, cash_component_source_amount=9000.0,
        cash_component_currency="MXN",
        cash_component_note="No FX rate for MXN is configured.",
    )
    unknown = Strategy(
        award=award, transfer_path=TransferPath(), points_cost=50000,
        cash_cost=0.0, total_value=500.0, cash_cost_known=False,
        cash_cost_note="The cash component of this award is unknown.",
    )
    badge = Award(
        date=date(2027, 1, 15), program="British Airways Executive Club",
        award_type="Y", cost=22500, cash_component=0.0, airline="BA",
        route="SFO-MAD", source="google_badge_unverified",
        source_note="Google Flights badge. UNVERIFIED.",
    )
    unverified = Strategy(
        award=badge, transfer_path=TransferPath(), points_cost=22500,
        cash_cost=0.0, total_value=225.0,
    )
    out = tmp_path / "r.html"
    export_html([unknown, unverified], str(out))
    html = out.read_text()

    assert "UNKNOWN" in html
    assert "MXN" in html and "9,000" in html
    assert "LOWER BOUND" in html
    assert "UNVERIFIED" in html
    assert "google_badge_unverified" in html
    # The unknown row does not carry a dollar figure for the unknown component.
    # (The literal "NOT $0.00" in the cell is the disclosure, not a value.)
    unknown_row = html.split("<tr>")[2]
    assert "<td>$0.00</td>" not in unknown_row


@patch("src.seats_client.requests.get")
def test_H4_require_all_live_withholds_a_run_with_a_dead_leg(mock_get, client, rm):
    """
    `margin_provenance` was computed only from legs that HAVE candidates, so a
    leg whose API call failed and which carries no badge was invisible to the
    test and could not make the run "mixed". `--require-all-live` exists for "the
    moment someone wants to quote a number publicly"; it did not fire.
    """
    legs = [_flight("L1", "SFO", "MAD", date(2027, 1, 15), 395.0),
            _flight("L2", "MAD", "AMS", date(2027, 1, 19), 44.0)]

    def side_effect(url, **kw):
        if kw["params"]["origin_airport"] == "MAD":
            raise requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")
        return _mock_response({"data": [_real_row()]})

    mock_get.side_effect = side_effect
    apply_live(_F(legs), client, LiveOptions(live=True))
    results = annotate_live_verdicts(
        evaluate_trip(legs, ratios_manager=rm, wallet=Wallet({"UR": UR}, [CSP]),
                      transfer_date=TRANSFER_DATE, show_alternatives=False)
    )
    totals = trip_totals(results, wallet=Wallet({"UR": UR}, [CSP]))

    assert totals["legs_api_error"] == 1
    assert totals["margin_provenance"] != "live"
    # main.py: withheld = require_all_live and margin_provenance != "live"
    assert (True and totals["margin_provenance"] != "live") is True
    assert "NEVER PRODUCED A USABLE ANSWER" in totals["margin_provenance_note"]


@patch("src.seats_client.requests.get")
def test_H4_an_unreadable_leg_also_breaks_live_provenance(mock_get, client, rm):
    """The sixth state must count as 'did not answer' for the gate too."""
    broken = _real_row()
    broken["Date"] = "15/01/2027"
    mock_get.return_value = _mock_response({"data": [broken]})
    legs = [_flight("L1")]
    apply_live(_F(legs), client, LiveOptions(live=True))
    results = evaluate_trip(legs, ratios_manager=rm,
                            wallet=Wallet({"UR": UR}, [CSP]),
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    counts = provenance_counts(results)
    assert counts["margin_provenance"] != "live"
    assert counts["legs_unreadable"] == 1
    assert counts["legs_unreadable_ids"] == ["L1"]
    assert counts["legs_answer_unusable_ids"] == ["L1"]


@patch("src.seats_client.requests.get")
def test_H5_a_promoted_off_date_award_is_scored_against_its_own_dates_cash(
    mock_get, client, rm
):
    """
    v3 section 4.5 refuses to score off-date awards specifically to avoid a bias
    toward points, and offers ONE escape hatch: capture the cash fare for that
    date. The hatch did not work - `evaluate_leg` took min() over every captured
    price regardless of date, so a promoted Jan-17 award was scored against the
    Jan-15 fare.
    """
    leg = _flight(cash_opts=[
        CashOption(label="Jan 15 fare", amount=395.0, currency="USD",
                   date=date(2027, 1, 15), source="captured_screenshot"),
        CashOption(label="Jan 17 fare", amount=900.0, currency="USD",
                   date=date(2027, 1, 17), source="captured_screenshot"),
    ])
    mock_get.return_value = _mock_response({"data": [_real_row(date(2027, 1, 17))]})
    apply_live(_F([leg]), client, LiveOptions(live=True, flex_days=3))
    assert leg.date_shifted is True and leg.date_shifted_to == date(2027, 1, 17)

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    # The head-to-head is now same-date: Jan-17 award vs the Jan-17 fare.
    assert res.best_cash.date == date(2027, 1, 17)
    assert res.cash_total_score_usd == 900.0
    assert res.scored_off_date is True and res.scoring_date == date(2027, 1, 17)
    # ...and the leg's OWN date is kept as the baseline, so a date-shifted "win"
    # that loses to simply flying as planned is not recommended.
    assert res.cash_baseline_usd == 395.0
    assert res.verdict == "cash"
    assert "fly on the leg's own date" in res.verdict_reason


@patch("src.seats_client.requests.get")
def test_H5_an_on_date_award_is_not_scored_against_another_dates_fare(
    mock_get, client, rm
):
    """
    The reverse case, which disclosed NOTHING: a Jan-15 award was scored against
    a Jan-17 fare it cannot be booked with, and `date_shifted` stayed False.
    """
    leg = _flight(cash_opts=[
        CashOption(label="Jan 15 fare", amount=900.0, currency="USD",
                   date=date(2027, 1, 15), source="captured_screenshot"),
        CashOption(label="Jan 17 fare", amount=400.0, currency="USD",
                   date=date(2027, 1, 17), source="captured_screenshot"),
    ])
    mock_get.return_value = _mock_response({"data": [_real_row(date(2027, 1, 15))]})
    apply_live(_F([leg]), client, LiveOptions(live=True, flex_days=3))
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    assert res.best_cash.date == date(2027, 1, 15)
    assert res.cash_total_score_usd == 900.0
    assert leg.date_shifted is False
    assert res.scored_off_date is False


def test_M1_an_unreachable_surcharge_row_is_refused_at_load(tmp_path):
    """
    `_tier_keys` enumerates 5 of the 16 wildcard patterns, so a row like
    `(BAX, BA, NA-EU, *, GB)` - the natural shape for "UK APD applies in any
    cabin" - could never be matched, and the LESS specific generic answered
    instead with $50 instead of $900. Silent, and in the dangerous direction.
    """
    csv = tmp_path / "s.csv"
    csv.write_text(
        SURCHARGE_HEADER
        + "BAX,BA,NA-EU,*,GB,750,900,1100,USD,round_trip,modeled,src,2026-09-08,ex-UK\n"
        + "BAX,BA,*,*,*,40,50,60,USD,round_trip,modeled,src,2026-09-08,generic\n"
    )
    with pytest.raises(SurchargeTableError) as e:
        SurchargeTable(csv)
    assert "can NEVER be matched" in str(e.value)


def test_M2_a_duplicate_row_is_refused_at_load(tmp_path):
    """
    `match()` indexed rows by key, so the LAST duplicate silently won and
    reversing two CSV lines reversed the answer. `validate()` always said the
    right thing about this; it was never called outside the test suite.
    """
    csv = tmp_path / "s.csv"
    csv.write_text(
        SURCHARGE_HEADER
        + "P,BA,NA-EU,J,GB,100,100,100,USD,round_trip,modeled,src,2026-09-08,first\n"
        + "P,BA,NA-EU,J,GB,900,900,900,USD,round_trip,modeled,src,2026-09-08,second\n"
    )
    with pytest.raises(SurchargeTableError) as e:
        SurchargeTable(csv)
    assert "Duplicate surcharge rule" in str(e.value)


def test_M2_the_production_table_is_validated_by_default_table():
    """The check that never ran in production now runs on the production path."""
    from src.surcharge import blanket_no_yq_map, default_table

    table = default_table()
    assert table.rules, "the production table loaded"
    assert blanket_no_yq_map().get("Air Canada Aeroplan") is True
    # Idempotent, and still clean. Frozen date moved to 2026-09-09 with the
    # sourced surcharge rows; see test_production_table_validates.
    table.validate(blanket_no_yq_map(), today=date(2026, 9, 9))


@patch("src.seats_client.requests.get")
def test_M6_an_api_failure_behind_a_badge_still_reaches_the_verdict(
    mock_get, client, rm
):
    """
    `annotate_live_verdicts` returned early when a leg had candidates, so when a
    fixture badge stood in for a dead API call the failure never reached the
    verdict or the warnings. With `allow_badge_fallback` defaulting to True that
    is the COMMON case for a failing leg on Trip B.
    """
    badge = PointsCandidate(
        label="badge 22.5k", program="British Airways Executive Club",
        points=22500, source="google_badge_unverified", operating_carrier="BA",
        carrier_source="assumed", cabin="Y",
    )
    legs = [_flight(cands=[badge])]
    mock_get.side_effect = requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")
    apply_live(_F(legs), client, LiveOptions(live=True))
    results = annotate_live_verdicts(
        evaluate_trip(legs, ratios_manager=rm, wallet=Wallet({"UR": UR}, [CSP]),
                      transfer_date=TRANSFER_DATE, show_alternatives=False)
    )
    r = results[0]
    assert r.leg.live_outcome.state is LiveQueryState.API_ERROR
    assert any("COULD NOT BE REACHED" in w for w in r.warnings)
    assert "LIVE DATA WAS NOT AVAILABLE" in r.verdict_reason


@patch("src.seats_client.requests.get")
def test_M7_refresh_reaches_the_network_and_the_cache_keeps_its_timestamp(
    mock_get, client, tmp_path
):
    """
    The in-memory class cache short-circuited `search()` BEFORE `search_raw`,
    where `refresh` is handled, so within one process `--refresh` was a no-op
    returning the stale price. The hit also erased `last_fetched_at`, so
    `award_to_candidate` stamped "Fetched during this run" onto older bytes.
    """
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    dr = DateRange(date(2027, 1, 15), date(2027, 1, 15))
    mock_get.return_value = _mock_response({"data": [_real_row()]})
    client.search("SFO", "MAD", dr, cache=cache)
    first_stamp = client.last_fetched_at
    assert first_stamp is not None

    # A repeat call inside one process: served from memory, but HONESTLY.
    client.search("SFO", "MAD", dr, cache=cache)
    assert client.last_fetched_at == first_stamp
    assert client.last_served_from_cache is True
    assert "IN-PROCESS cache" in client.last_pagination_note

    # --refresh now actually re-fetches and sees the new price.
    calls_before = mock_get.call_count
    mock_get.return_value = _mock_response(
        {"data": [_real_row(YMileageCost="25000")]}
    )
    awards = client.search("SFO", "MAD", dr, cache=cache, refresh=True)
    assert mock_get.call_count > calls_before
    assert awards[0].cost == 25000


def test_L1_one_legs_unconfigured_currency_does_not_kill_the_trip(rm):
    """
    Exit code 1 for the whole run, with a message that named no leg: one leg's
    currency killed six other legs' results.
    """
    bad = Leg(id="X1", kind="flight", description="NRT->SFO",
              date=date(2027, 1, 15), origin="NRT", destination="SFO",
              cash_options=[CashOption(label="JAL", amount=90000.0, currency="JPY")])
    good = _united_leg("X2", 900.0, points=50000)
    results = evaluate_trip([bad, good], ratios_manager=rm,
                            wallet=Wallet({"UR": UR}, [CSP]),
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    x1, x2 = results
    assert any("JPY" in w and "X1" in w for w in x1.warnings)
    assert x2.verdict == "points", "the other legs still score"
    # And the unpriceable leg is EXCLUDED from the totals, never counted as $0.
    totals = trip_totals(results, wallet=Wallet({"UR": UR}, [CSP]))
    assert totals["legs_unpriceable_ids"] == ["X1"]
    assert totals["beat_cash_pct"] == pytest.approx(
        (900.0 - 500.0) / 900.0 * 100
    )
    out = _render(print_leg_results, results)
    assert "UNKNOWN" in out
    assert "$0.00" not in out.split("X1")[1].split("X2")[0]


def test_L2_the_provenance_note_reads_the_candidates_actual_sources(rm):
    """
    A non-live run always printed "Every points price here is a Google Flights
    badge" - even for a candidate whose own `source` is manual_capture.
    """
    leg = _flight(cands=[PointsCandidate(
        label="captured off the Aeroplan booking page",
        program="Air Canada Aeroplan", points=50000, source="manual_capture",
        operating_carrier="AC", carrier_source="captured", cabin="Y")])
    results = evaluate_trip([leg], ratios_manager=rm,
                            wallet=Wallet({"UR": UR}, [CSP]),
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    note = provenance_counts(results)["margin_provenance_note"]
    assert "Google Flights badge" not in note
    assert "manual_capture" in note


def test_L5_a_carrier_written_twice_is_not_ambiguous_metal():
    """
    `["BA", "BA"]` and `[" ba ", "BA"]` both took the multi-carrier branch and
    announced "OPERATING METAL AMBIGUOUS: Seats.aero listed BA, BA...". The
    figure was right; the disclosure was false.
    """
    t = SurchargeTable(ROOT / "data" / "surcharges.csv")
    kw = dict(region="NA-EU", cabin="J", departure_country="GB", is_round_trip=True)
    program = "British Airways Executive Club"

    single = t.resolve_ambiguous_metal(program, ["BA"], **kw)
    for carriers in (["BA", "BA"], [" ba ", "BA"], ["BA", " BA "]):
        got = t.resolve_ambiguous_metal(program, carriers, **kw)
        assert got.amount_point == single.amount_point
        assert "OPERATING METAL AMBIGUOUS" not in got.notes, carriers
        assert "BA, BA" not in got.notes, carriers

    # A None entry is an absence, not a carrier code called "NONE".
    none_entry = t.resolve_ambiguous_metal(program, [None], **kw)
    assert "NONE" not in none_entry.notes

    # A GENUINELY ambiguous list still discloses.
    real = t.resolve_ambiguous_metal(program, ["BA", "IB"], **kw)
    assert not real.is_known or "AMBIGUOUS" in real.notes


def test_L6_max_split_currencies_is_honoured(rm):
    """
    `all_plans` built pairs regardless of `max_split`, so raising
    MAX_SPLIT_CURRENCIES changed nothing and no warning was emitted.
    """
    from src.funding import all_plans, best_plan

    rm_multi = RatioManager(
        ROOT / "tests" / "fixtures" / "ratios_multicurrency_test.csv",
        ROOT / "tests" / "fixtures" / "bonuses_test.csv",
        ROOT / "data" / "programs.yaml",
    )
    w = Wallet({"UR": UR, "MR": 50_000, "TY": 50_000}, [CSP])
    target = "British Airways Executive Club"

    # At the default cap of two, 250,000 is unfundable - and the reason names
    # the SPLIT CAP rather than blaming the balance.
    assert best_plan(w, target, 250_000, TRANSFER_DATE, rm_multi) is None
    infeasible = all_plans(w, target, 250_000, TRANSFER_DATE, rm_multi,
                           include_infeasible=True)
    assert any("SPLIT CAP" in p.infeasible_reason for p in infeasible)

    # Raise the cap and the three-way split is actually built.
    plan = best_plan(w, target, 250_000, TRANSFER_DATE, rm_multi,
                     max_split_currencies=3)
    assert plan is not None
    assert plan.currencies_used == 3
    assert plan.delivered + plan.existing_target_points_used >= 250_000

    # R3 still holds: no split when one currency suffices.
    plans = all_plans(w, target, 50_000, TRANSFER_DATE, rm_multi,
                      max_split_currencies=3)
    assert all(p.currencies_used <= 1 for p in plans)


def test_L7_the_trip_b_fixture_no_longer_calls_GBP_a_placeholder():
    """
    v3 Step 0 made GBP `sourced`, so `is_placeholder_rate("GBP")` is False - and
    the fixture's free text went on calling the rate "an unverified placeholder",
    contradicting the structured marker, with the free text the wrong one.
    """
    from src import config
    from src.trip_loader import load_trip_fixture

    assert config.is_placeholder_rate("GBP") is False
    assert config.needs_confirmation("GBP") is True
    fixture = load_trip_fixture(ROOT / "tests/fixtures/trips/trip_b_europe.json")
    b7 = next(leg for leg in fixture.legs if leg.id == "B7")
    flags = " ".join(b7.data_flags)
    assert "unverified placeholder" not in flags
    assert "sourced" in flags
    assert "CONFIRM BEFORE TRUSTING" in flags


def test_L8_an_interrupted_runs_manifest_row_is_the_one_that_gets_filled(tmp_path):
    """
    `annotate_manifest` walked BACKWARDS for the last unfilled row naming the
    snapshot. Because a byte-identical re-fetch reuses the snapshot NAME, a run
    that died between `put` and `annotate` left a permanently `- | -` row while
    the next fetch's state landed on its own row.
    """
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    req = {"origin_airport": "SFO", "destination_airport": "MAD",
           "start_date": "2027-01-15", "end_date": "2027-01-15"}
    first = cache.put("k", req, [{"data": []}], meta={"leg_id": "B1"},
                      now=datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc))
    cache.put("k", req, [{"data": []}], meta={"leg_id": "B1"},
              now=datetime(2026, 9, 9, 18, 0, 0, tzinfo=timezone.utc))

    assert cache.annotate_manifest(
        first.meta["snapshot"], "ok", 3, manifest_key=first.meta["manifest_key"]
    )
    rows = [l for l in cache.manifest_path.read_text().splitlines()
            if l.startswith("| 2026")]
    assert "| 3 | ok |" in rows[0], "the row for THIS fetch is the one filled"
    assert "12:00:00Z" in rows[0]


def test_L8_the_manifest_append_is_locked():
    """A plain unlocked append lets interleaved writers corrupt a row."""
    import inspect

    from src import response_cache

    src = inspect.getsource(response_cache.ResponseCache._append_manifest)
    assert "flock" in src
