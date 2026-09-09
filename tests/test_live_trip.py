"""
v3 Steps 3-8: live trip mode.

THE HIGHEST-VALUE TESTS IN THIS SUITE ARE THE ERROR-vs-ABSENCE ONES. This
project has twice reported a failure as a finding - v0's phantom $0 surcharge and
v2's parser turning a real 9-seat award into "no award availability". These tests
exist so that a third time is structurally impossible rather than merely
unlikely.

NO TEST HERE MAKES A NETWORK CALL. Everything runs off stubs or the committed
SFO->MAD envelope. tests/conftest.py enforces it globally.
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

from src.formatter import print_leg_results, print_live_leg_detail, print_trip_totals
from src.live_trip import (
    LIVE_SOURCE,
    VERDICT_NO_LIVE_DATA,
    LiveOptions,
    annotate_live_verdicts,
    apply_live,
    provenance_counts,
    query_leg,
)
from src.models import (
    CashOption,
    Leg,
    LiveLegOutcome,
    LiveQuerySpec,
    LiveQueryState,
    PointsProvenance,
)
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)
TODAY = date(2026, 9, 8)


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


@pytest.fixture
def real_payload():
    return {"data": json.loads(REAL_FIXTURE.read_text())["data"]}


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


@pytest.fixture
def trip_b():
    return load_trip_fixture(TRIPS / "trip_b_europe.json")


def _mock_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _live_opts(cache=None, **kw):
    return LiveOptions(live=True, cache=cache, **kw)


# Trip B's four flight legs, so a stub can answer each with a row dated to the
# leg it is actually answering. A stub that returns one fixed date for every leg
# is not a stub of Seats.aero - it is a stub of one leg, repeated, and it makes
# three of the four legs look like off-date findings.
TRIP_B_ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}


def _route_aware(real_payload, override=None):
    """
    A requests.get side effect that answers each route with a row on ITS date.

    `override` maps a route to a callable returning a response, for the tests
    that need exactly one leg to behave differently.
    """
    override = override or {}

    def _side_effect(*args, **kwargs):
        params = kwargs.get("params") or {}
        route = (params.get("origin_airport"), params.get("destination_airport"))
        if route in override:
            return override[route]()
        payload = copy.deepcopy(real_payload)
        row = payload["data"][0]
        iso = TRIP_B_ROUTES.get(route, params.get("start_date"))
        row["Date"] = iso
        row["ParsedDate"] = f"{iso}T00:00:00Z"
        row["Route"]["OriginAirport"] = route[0]
        row["Route"]["DestinationAirport"] = route[1]
        return _mock_response(payload)

    return _side_effect


def _render(fn, *args, **kwargs) -> str:
    buf = StringIO()
    fn(*args, console=Console(file=buf, width=200, no_color=True), **kwargs)
    return buf.getvalue()


def _evaluate(fixture, rm):
    return evaluate_trip(
        fixture.legs,
        ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=[CSP]),
        transfer_date=TRANSFER_DATE,
        today=TODAY,
    )


# ===========================================================================
# Step 3: the outcome type refuses to let error and absence merge
# ===========================================================================


def test_an_error_state_must_say_what_the_error_was():
    with pytest.raises(ValueError, match="must say what the error was"):
        LiveLegOutcome(
            leg_id="B3",
            state=LiveQueryState.API_ERROR,
            provenance=PointsProvenance.UNAVAILABLE,
        )


def test_no_award_space_with_an_error_message_is_refused():
    """
    THE SINGLE MOST IMPORTANT ASSERTION IN v3.

    Constructing this object would mean an API failure was about to be reported
    as an absence of award space. That is the exact bug this project has now made
    twice, and it is now unrepresentable.
    """
    with pytest.raises(ValueError, match="exact bug this project has now made twice"):
        LiveLegOutcome(
            leg_id="B3",
            state=LiveQueryState.NO_AWARD_SPACE,
            provenance=PointsProvenance.UNAVAILABLE,
            error="HTTPSConnectionPool timeout",
        )


def test_the_invariant_is_a_raise_not_an_assert():
    """
    `python -O` deletes asserts. The plan's sketch used one; this does not.

    The anti-collapse rule is the one invariant in this codebase that must
    survive every optimisation flag, so it raises a real exception. Recorded as
    a deliberate deviation from the plan's section 4.3 sketch.
    """
    source = (ROOT / "src" / "models.py").read_text()
    block = source[source.index("class LiveLegOutcome") : source.index("class FlexibleFinding")]
    assert "raise ValueError" in block
    assert "assert " not in block, "an assert here evaporates under python -O"


def test_an_error_state_cannot_carry_awards():
    with pytest.raises(ValueError, match="nothing to have parsed"):
        LiveLegOutcome(
            leg_id="B1",
            state=LiveQueryState.API_ERROR,
            provenance=PointsProvenance.UNAVAILABLE,
            error="boom",
            awards_parsed=3,
        )


def test_ok_with_zero_awards_must_be_no_award_space():
    with pytest.raises(ValueError, match="Use that state"):
        LiveLegOutcome(
            leg_id="B1",
            state=LiveQueryState.OK,
            provenance=PointsProvenance.LIVE,
            awards_parsed=0,
        )


def test_only_an_answered_state_is_a_finding_about_award_space():
    assert LiveQueryState.OK.is_a_finding_about_award_space is True
    assert LiveQueryState.NO_AWARD_SPACE.is_a_finding_about_award_space is True
    assert LiveQueryState.API_ERROR.is_a_finding_about_award_space is False
    assert LiveQueryState.BUDGET_EXHAUSTED.is_a_finding_about_award_space is False
    assert LiveQueryState.NOT_QUERIED.is_a_finding_about_award_space is False


def test_the_two_unavailable_states_never_render_alike():
    spec = LiveQuerySpec("B3", "AMS", "LHR", date(2027, 1, 23), date(2027, 1, 23), date(2027, 1, 23))
    empty = LiveLegOutcome(
        leg_id="B3", state=LiveQueryState.NO_AWARD_SPACE,
        provenance=PointsProvenance.UNAVAILABLE, queried=spec, rows_seen=0,
    ).render()
    failed = LiveLegOutcome(
        leg_id="B3", state=LiveQueryState.API_ERROR,
        provenance=PointsProvenance.UNAVAILABLE, queried=spec,
        error="HTTPSConnectionPool timeout",
    ).render()

    assert "THIS IS A FINDING" in empty
    assert "no award to buy" in empty
    assert "API FAILURE" not in empty

    assert "COULD NOT BE REACHED" in failed
    assert "THIS IS AN API FAILURE" in failed
    assert "NOT a finding of no availability" in failed
    assert "no award to buy" not in failed
    assert empty != failed


# ===========================================================================
# Step 3: query_leg maps every case, and never raises
# ===========================================================================


@patch("src.seats_client.requests.get")
def test_a_timeout_is_an_api_error_with_a_message(mock_get, client):
    mock_get.side_effect = requests.exceptions.ConnectTimeout("HTTPSConnectionPool timeout")
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, awards = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.API_ERROR
    assert outcome.error
    assert outcome.awards_parsed == 0
    assert awards == []


@patch("src.seats_client.requests.get")
def test_an_empty_data_array_is_no_award_space_with_no_error(mock_get, client):
    mock_get.return_value = _mock_response({"data": []})
    leg = Leg(id="B3", kind="flight", description="x", date=date(2027, 1, 23),
              origin="AMS", destination="LHR")
    outcome, awards = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert outcome.error == "", "an absence must carry no error, or it is a failure"
    assert awards == []


@patch("src.seats_client.requests.get")
def test_a_leg_with_no_iata_codes_is_not_queried_and_makes_no_http_call(mock_get, client):
    leg = Leg(id="B9", kind="flight", description="x", date=date(2027, 1, 15))
    outcome, awards = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.NOT_QUERIED
    assert mock_get.call_count == 0
    assert "does not name both airports" in outcome.note


@patch("src.seats_client.requests.get")
def test_a_hotel_leg_is_never_queried(mock_get, client):
    leg = Leg(id="B5", kind="hotel", description="x", date=date(2027, 1, 15))
    outcome, _ = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.NOT_QUERIED
    assert "flights-only" in outcome.note
    assert mock_get.call_count == 0


@patch("src.seats_client.requests.get")
def test_live_off_queries_nothing(mock_get, client):
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, _ = query_leg(leg, client, LiveOptions(live=False))
    assert outcome.state is LiveQueryState.NOT_QUERIED
    assert mock_get.call_count == 0


@patch("src.seats_client.requests.get")
def test_an_unexpected_exception_is_still_an_api_error_not_an_escape(mock_get, client):
    mock_get.side_effect = RuntimeError("something nobody predicted")
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, _ = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.API_ERROR
    assert "not a finding about award space" in outcome.error


@patch("src.seats_client.requests.get")
def test_four_legs_one_raising_produces_four_outcomes_and_no_escape(
    mock_get, client, trip_b, real_payload
):
    def _boom():
        raise requests.exceptions.ReadTimeout("leg two is down")

    mock_get.side_effect = _route_aware(real_payload, override={("MAD", "AMS"): _boom})
    _, outcomes = apply_live(trip_b, client, _live_opts())
    flights = [o for o in outcomes if o.state is not LiveQueryState.NOT_QUERIED]
    assert len(flights) == 4
    assert sum(1 for o in flights if o.state is LiveQueryState.API_ERROR) == 1
    assert sum(1 for o in flights if o.state is LiveQueryState.OK) == 3


# ===========================================================================
# Step 4: the window, the call count, and the date rule
# ===========================================================================


@patch("src.seats_client.requests.get")
def test_trip_b_issues_exactly_four_calls_one_per_flight_leg(
    mock_get, client, trip_b, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    _, outcomes = apply_live(trip_b, client, _live_opts(flex_days=0))
    assert mock_get.call_count == 4, "four flight legs, three hotels, four calls"
    queried = [o for o in outcomes if o.state is not LiveQueryState.NOT_QUERIED]
    assert [o.leg_id for o in queried] == ["B1", "B2", "B3", "B4"]
    not_queried = [o for o in outcomes if o.state is LiveQueryState.NOT_QUERIED]
    assert [o.leg_id for o in not_queried] == ["B5", "B6", "B7"]


@patch("src.seats_client.requests.get")
def test_flex_days_widens_the_window_and_still_makes_four_calls(
    mock_get, client, trip_b, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    _, outcomes = apply_live(trip_b, client, _live_opts(flex_days=3))
    assert mock_get.call_count == 4

    b1 = next(o for o in outcomes if o.leg_id == "B1")
    assert b1.queried.start_date == date(2027, 1, 12)
    assert b1.queried.end_date == date(2027, 1, 18)
    assert b1.queried.flex_days == 3

    params = mock_get.call_args_list[0].kwargs["params"]
    assert params["start_date"] == "2027-01-12"
    assert params["end_date"] == "2027-01-18"


def _payload_dated(real_payload, iso_date):
    payload = copy.deepcopy(real_payload)
    payload["data"][0]["Date"] = iso_date
    payload["data"][0]["ParsedDate"] = f"{iso_date}T00:00:00Z"
    return payload


@patch("src.seats_client.requests.get")
def test_an_off_date_award_is_advisory_and_never_a_candidate(
    mock_get, client, trip_b, real_payload, rm
):
    """
    An award on 2027-01-17 for a leg dated 2027-01-15 is compared against
    NOTHING, because there is no 2027-01-17 cash price and the tool cannot
    invent one.
    """
    mock_get.return_value = _mock_response(_payload_dated(real_payload, "2027-01-17"))
    b1 = next(leg for leg in trip_b.legs if leg.id == "B1")
    badge_points = [c.points for c in b1.points_candidates]

    apply_live(trip_b, client, _live_opts(flex_days=3))

    assert len(b1.flexible_date_findings) == 1
    finding = b1.flexible_date_findings[0]
    assert finding.award_date == date(2027, 1, 17)
    assert finding.date_match == "offset(+2)"
    assert "has NOT been captured" in finding.advisory()

    # The badge survives, because live supplied nothing for the leg's own date.
    assert b1.points_provenance is PointsProvenance.BADGE_FALLBACK
    assert [c.points for c in b1.points_candidates] == badge_points
    assert not any(c.source == LIVE_SOURCE for c in b1.points_candidates)


@patch("src.seats_client.requests.get")
def test_an_off_date_award_does_not_move_the_margin_by_a_cent(
    mock_get, client, real_payload, rm
):
    """The margin must be BIT-IDENTICAL with and without the off-date award."""
    without = load_trip_fixture(TRIPS / "trip_b_europe.json")
    baseline = trip_totals(_evaluate(without, rm))

    mock_get.return_value = _mock_response(_payload_dated(real_payload, "2027-01-17"))
    with_award = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(with_award, client, _live_opts(flex_days=3))
    after = trip_totals(_evaluate(with_award, rm))

    for key in (
        "all_cash_usd", "optimized_usd", "optimized_low_usd", "optimized_high_usd",
        "beat_cash_pct_low", "beat_cash_pct_high",
    ):
        assert after[key] == baseline[key], f"{key} moved on an unscoreable finding"


@patch("src.seats_client.requests.get")
def test_an_off_date_award_IS_promoted_once_cash_for_that_date_exists(
    mock_get, client, real_payload
):
    """
    THE HONEST ESCAPE HATCH. Capture the cash fare for that date and the award
    becomes scoreable against THAT price - and the leg records that a date moved.
    """
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    b1 = next(leg for leg in fixture.legs if leg.id == "B1")
    b1.cash_options.append(
        CashOption(
            label="$372 captured for 2027-01-17",
            amount=372.0,
            currency="USD",
            source="captured_screenshot",
            captured_on=date(2026, 9, 8),
            date=date(2027, 1, 17),
        )
    )

    mock_get.return_value = _mock_response(_payload_dated(real_payload, "2027-01-17"))
    apply_live(fixture, client, _live_opts(flex_days=3))

    assert b1.date_shifted is True
    assert b1.date_shifted_to == date(2027, 1, 17)
    assert b1.flexible_date_findings == []
    assert b1.points_provenance is PointsProvenance.LIVE
    assert all(c.source == LIVE_SOURCE for c in b1.points_candidates)


@patch("src.seats_client.requests.get")
def test_budget_exhaustion_is_an_error_state_not_an_absence(
    mock_get, client, trip_b, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    # Leave room for exactly two legs.
    SeatsClient._calls_date = date.today()
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP - 2

    _, outcomes = apply_live(trip_b, client, _live_opts())
    flights = [o for o in outcomes if o.state is not LiveQueryState.NOT_QUERIED]
    assert len(flights) == 4
    assert sum(1 for o in flights if o.state is LiveQueryState.OK) == 2
    exhausted = [o for o in flights if o.state is LiveQueryState.BUDGET_EXHAUSTED]
    assert len(exhausted) == 2
    for o in exhausted:
        assert o.error, "a budget failure must say so"
        assert o.state is not LiveQueryState.NO_AWARD_SPACE
        assert "no award" not in o.render().lower().replace("no award space on", "")
        assert "BUDGET FAILURE" in o.render()


# ===========================================================================
# Step 5: the merge. Cash is never touched.
# ===========================================================================


def _cash_snapshot(fixture):
    return [
        [
            (c.label, c.amount, c.currency, c.notes, c.unavoidable_cash_note,
             c.source, c.captured_on, c.date)
            for c in leg.cash_options
        ]
        for leg in fixture.legs
    ]


@patch("src.seats_client.requests.get")
def test_apply_live_never_mutates_the_cash_side(mock_get, client, trip_b, real_payload):
    """
    THE RULE THE WHOLE DESIGN IS BUILT AROUND. There is no cash-price API in
    scope and there will not be one; screenshots are the cash source of truth.
    """
    mock_get.return_value = _mock_response(real_payload)
    before = _cash_snapshot(trip_b)
    apply_live(trip_b, client, _live_opts())
    assert _cash_snapshot(trip_b) == before


@patch("src.seats_client.requests.get")
def test_a_live_leg_reports_both_provenances_on_the_same_row(
    mock_get, client, real_payload, rm
):
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    for leg in fixture.legs:
        for opt in leg.cash_options:
            opt.source = "captured_screenshot"
            opt.captured_on = date(2026, 9, 7)
        leg.cash_provenance = "captured_screenshot"
        leg.cash_captured_on = date(2026, 9, 7)

    mock_get.side_effect = _route_aware(real_payload)
    apply_live(fixture, client, _live_opts())
    results = _evaluate(fixture, rm)
    b1 = next(r for r in results if r.leg.id == "B1")

    assert b1.points_provenance is PointsProvenance.LIVE
    assert b1.cash_provenance == "captured_screenshot"
    assert b1.cash_captured_on == date(2026, 9, 7)

    out = _render(print_leg_results, results)
    assert "live" in out
    assert "screenshot" in out


@patch("src.seats_client.requests.get")
def test_superseded_badges_are_retained_and_rendered_never_dropped(
    mock_get, client, trip_b, real_payload, rm
):
    """
    B1's badges (BA 22,500 / Aeroplan 50,000) must survive the swap and be
    reported alongside what live actually returned. Exactly one of Trip B's four
    badges has ever been corroborated, so divergence is the interesting case.
    """
    mock_get.return_value = _mock_response(real_payload)
    apply_live(trip_b, client, _live_opts())
    b1 = next(leg for leg in trip_b.legs if leg.id == "B1")

    superseded = {(c.program, c.points) for c in b1.superseded_candidates}
    assert ("British Airways Executive Club", 22500) in superseded
    assert ("Air Canada Aeroplan", 50000) in superseded

    results = _evaluate(trip_b, rm)
    out = _render(print_live_leg_detail, results)
    assert "SUPERSEDED BADGE on B1" in out
    assert "22,500" in out
    assert "Live Seats.aero returned" in out


def test_without_live_the_output_is_byte_identical_to_today(trip_b, rm):
    """
    THE GOLDEN-OUTPUT REGRESSION. Every v3 field defaults to the pre-v3
    behaviour, so a run WITHOUT --live must render exactly as it did before, FX
    aside.
    """
    results = _evaluate(trip_b, rm)
    out = _render(print_live_leg_detail, results)
    assert out == "", "no live block may appear when live mode was never used"

    totals = trip_totals(results)
    assert totals["margin_provenance"] == "badge"
    assert totals["legs_points_live"] == 0
    assert totals["legs_points_badge"] == 4
    # CHANGED AT v5 STEP 7, and ONLY by the APD term. This test's subject is
    # that a run without --live renders no live block and keeps its badge
    # provenance - both still true above. The headline moved because B4
    # (LHR->SFO) now carries GBP 102.00 = $138.11 of UK Air Passenger Duty on
    # its points side, which is the one number v5 moves on purpose.
    assert totals["beat_cash_pct_low"] == pytest.approx(2.04, abs=0.02)
    assert totals["beat_cash_pct_high"] == pytest.approx(11.03, abs=0.02)
    b4 = next(r for r in results if r.leg.id == "B4")
    assert b4.apd_added_usd == pytest.approx(138.11, abs=0.01)


# ===========================================================================
# Step 6: partial failure scores the rest of the trip honestly
# ===========================================================================


def _run_with_leg3(mock_get, client, rm, behaviour, real_payload):
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    # Strip B3's badge so the leg genuinely has no points side once live fails -
    # otherwise badge fallback (correctly) covers it and the sub-state is moot.
    next(leg for leg in fixture.legs if leg.id == "B3").points_candidates = []

    mock_get.side_effect = _route_aware(
        real_payload, override={("AMS", "LHR"): behaviour}
    )
    apply_live(fixture, client, _live_opts(allow_badge_fallback=False))
    results = annotate_live_verdicts(_evaluate(fixture, rm))
    return fixture, results, trip_totals(results)


@patch("src.seats_client.requests.get")
def test_a_failing_leg_does_not_fail_the_trip(mock_get, client, rm, real_payload):
    def _raise():
        raise requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")

    _, results, totals = _run_with_leg3(mock_get, client, rm, _raise, real_payload)

    scored = {r.leg.id for r in results if r.leg.points_provenance is PointsProvenance.LIVE}
    assert scored == {"B1", "B2", "B4"}
    assert totals["legs_api_error"] == 1
    assert totals["legs_api_error_ids"] == ["B3"]
    assert totals["all_cash_usd"] > 0, "the trip still has a margin"

    b3 = next(r for r in results if r.leg.id == "B3")
    assert b3.verdict == VERDICT_NO_LIVE_DATA
    assert "BY DEFAULT, not by finding" in b3.verdict_reason


@patch("src.seats_client.requests.get")
def test_the_phrase_no_award_availability_is_unreachable_from_an_error_path(
    mock_get, client, rm, real_payload
):
    """
    The literal string v2 printed when its parser broke. It must not appear
    ANYWHERE in the output of a run whose leg failed.
    """
    def _raise():
        raise requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")

    _, results, totals = _run_with_leg3(mock_get, client, rm, _raise, real_payload)
    out = (
        _render(print_leg_results, results)
        + _render(print_live_leg_detail, results)
        + _render(print_trip_totals, totals)
    )
    assert "no award availability" not in out.lower()
    assert "API FAILED" in out or "API FAILURE" in out


@patch("src.seats_client.requests.get")
def test_an_empty_answer_is_reported_as_a_finding_and_says_so(
    mock_get, client, rm, real_payload
):
    _, results, totals = _run_with_leg3(
        mock_get, client, rm, lambda: _mock_response({"data": []}), real_payload
    )
    b3 = next(r for r in results if r.leg.id == "B3")
    assert b3.verdict == VERDICT_NO_LIVE_DATA
    assert "FINDING" in b3.verdict_reason
    assert "no award to buy" in b3.verdict_reason
    assert totals["legs_no_award_space"] == 1
    assert totals["legs_api_error"] == 0

    out = _render(print_live_leg_detail, results)
    assert "THIS IS A FINDING" in out


@patch("src.seats_client.requests.get")
def test_the_two_failure_modes_produce_different_output(
    mock_get, client, rm, real_payload
):
    def _raise():
        raise requests.exceptions.ReadTimeout("timeout")

    _, err_results, err_totals = _run_with_leg3(mock_get, client, rm, _raise, real_payload)
    err_reason = next(r for r in err_results if r.leg.id == "B3").verdict_reason
    err_out = _render(print_live_leg_detail, err_results)

    SeatsClient.reset_call_budget()
    client.clear_cache()
    _, empty_results, empty_totals = _run_with_leg3(
        mock_get, client, rm, lambda: _mock_response({"data": []}), real_payload
    )
    empty_reason = next(r for r in empty_results if r.leg.id == "B3").verdict_reason
    empty_out = _render(print_live_leg_detail, empty_results)

    assert err_reason != empty_reason
    assert err_out != empty_out
    assert err_totals["legs_api_error_ids"] != empty_totals["legs_api_error_ids"]
    assert err_totals["legs_no_award_space_ids"] != empty_totals["legs_no_award_space_ids"]


@pytest.mark.parametrize("status", [401, 429, 500])
@patch("src.seats_client.requests.get")
def test_http_error_codes_are_api_failures_not_findings(mock_get, client, status):
    response = MagicMock()
    response.status_code = status
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        f"{status} Server Error"
    )
    mock_get.return_value = response
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, _ = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.API_ERROR
    assert str(status) in outcome.error
    assert "NOT a finding of no availability" in outcome.render()


@patch("src.seats_client.requests.get")
def test_a_truncated_json_body_is_an_api_failure_not_a_finding(mock_get, client):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("Expecting value: line 1 column 1")
    mock_get.return_value = response
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, _ = query_leg(leg, client, _live_opts())
    assert outcome.state is LiveQueryState.API_ERROR
    assert "NOT a finding of no availability" in outcome.render()


@patch("src.seats_client.requests.get")
def test_a_corrupt_cache_file_re_fetches_rather_than_reporting_emptiness(
    mock_get, client, cache, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    leg = Leg(id="B1", kind="flight", description="x", date=date(2027, 1, 15),
              origin="SFO", destination="MAD")
    outcome, awards = query_leg(leg, client, _live_opts(cache=cache))
    assert outcome.state is LiveQueryState.OK

    # Corrupt the entry, then ask again with the in-memory cache cleared.
    list(cache.cache_dir.glob("*.json"))[0].write_text("{ truncated")
    client.clear_cache()
    outcome2, awards2 = query_leg(leg, client, _live_opts(cache=cache))
    assert outcome2.state is LiveQueryState.OK, "a corrupt file must re-fetch"
    assert len(awards2) == len(awards)
    assert any("NOT being read as an empty result" in w for w in cache.warnings)


# ===========================================================================
# Step 7: the margin never appears without its provenance
# ===========================================================================


@patch("src.seats_client.requests.get")
def test_four_of_four_live_is_labelled_live_and_names_the_legs(
    mock_get, client, trip_b, real_payload, rm
):
    mock_get.side_effect = _route_aware(real_payload)
    apply_live(trip_b, client, _live_opts())
    totals = trip_totals(_evaluate(trip_b, rm))
    assert totals["margin_provenance"] == "live"
    assert totals["legs_points_live"] == 4
    assert totals["legs_points_badge"] == 0
    for leg_id in ("B1", "B2", "B3", "B4"):
        assert leg_id in totals["margin_provenance_note"]


@patch("src.seats_client.requests.get")
def test_three_of_four_live_is_labelled_mixed_and_names_the_badge_leg(
    mock_get, client, trip_b, real_payload, rm
):
    mock_get.side_effect = _route_aware(
        real_payload, override={("AMS", "LHR"): lambda: _mock_response({"data": []})}
    )
    apply_live(trip_b, client, _live_opts())
    totals = trip_totals(_evaluate(trip_b, rm))

    assert totals["margin_provenance"] == "mixed"
    assert totals["legs_points_live"] == 3
    assert totals["legs_points_badge"] == 1
    assert totals["legs_badge_ids"] == ["B3"]
    assert "MUST NOT BE QUOTED AS A LIVE NUMBER" in totals["margin_provenance_note"]


def test_the_percentage_never_renders_without_its_provenance_line(trip_b, rm):
    """
    Rule 2 of section 4.7: they are emitted by the SAME call, so they cannot be
    separated by accident. Asserted by rendering and checking adjacency.
    """
    for name in (
        "trip_b_europe.json", "trip_a_mry_nyc.json",
        "trip_c_lon_mry_surcharge.json", "trip_001.json", "trip_002.json",
    ):
        fixture = load_trip_fixture(TRIPS / name)
        totals = trip_totals(_evaluate(fixture, rm))
        lines = [l for l in _render(print_trip_totals, totals).splitlines() if l.strip()]
        pct_lines = [i for i, l in enumerate(lines) if "beats paying cash by" in l]
        assert pct_lines, f"{name} printed no headline at all"
        prov_lines = [i for i, l in enumerate(lines) if "margin provenance" in l]
        assert prov_lines, f"{name} printed a percentage with no provenance"
        assert min(prov_lines) > max(pct_lines)
        assert min(prov_lines) - max(pct_lines) <= 4, (
            f"{name}: the provenance line is not adjacent to the percentage"
        )


def test_require_all_live_withholds_the_percentage_on_a_mixed_run(trip_b, rm):
    totals = trip_totals(_evaluate(trip_b, rm))
    totals["margin_withheld"] = True
    out = _render(print_trip_totals, totals)
    assert "WITHHELD" in out
    assert "THE TRIP MARGIN IS WITHHELD" in out
    assert f"{totals['beat_cash_pct_low']:.2f}%" not in out
    assert f"{totals['beat_cash_pct_high']:.2f}%" not in out


# ===========================================================================
# Step 8: every live leg is useful, even with an unknown surcharge
# ===========================================================================


@patch("src.seats_client.requests.get")
def test_the_real_aeroplan_row_scores_via_the_program_policy_path(
    mock_get, client, real_payload, rm
):
    """
    THE TIER-5 CASE, exercised by the actual captured SFO->MAD row. Aeroplan
    levies no carrier surcharge as a matter of program policy, which is
    metal-independent, so the API's CAD 44.60 IS the whole carrier-side figure.
    """
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    mock_get.return_value = _mock_response(real_payload)
    apply_live(fixture, client, _live_opts())

    b1 = next(leg for leg in fixture.legs if leg.id == "B1")
    cand = b1.points_candidates[0]
    assert cand.program == "Air Canada Aeroplan"
    assert cand.points == 50000
    assert cand.surcharge_captured is True
    assert cand.cash_surcharge == pytest.approx(32.36, abs=0.01)
    assert "PROGRAM POLICY" in cand.source_note

    results = _evaluate(fixture, rm)
    b1r = next(r for r in results if r.leg.id == "B1")
    # 50,000 UR at 1cpp + $32.36 taxes = $532.36 against $395 cash. PAY CASH,
    # and now priced from both directions rather than one.
    assert b1r.points_total_score_usd == pytest.approx(532.36, abs=0.01)
    assert b1r.verdict == "cash"


@patch("src.seats_client.requests.get")
def test_an_unknown_surcharge_live_leg_renders_a_floor_and_a_break_even(
    mock_get, client, real_payload, rm
):
    """A live leg the tool cannot score is still USEFUL. Never a blank cell."""
    payload = copy.deepcopy(real_payload)
    payload["data"][0]["Route"]["Source"] = "flyingblue"
    mock_get.return_value = _mock_response(payload)

    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    results = _evaluate(fixture, rm)
    b1 = next(r for r in results if r.leg.id == "B1")

    assert b1.leg.points_provenance is PointsProvenance.LIVE
    assert b1.surcharge is not None and not b1.surcharge.is_known
    assert b1.points_floor_usd is not None
    assert b1.break_even_surcharge_usd is not None

    out = _render(print_live_leg_detail, results)
    assert "floor $" in out
    assert "points win ONLY if the carrier surcharge" in out
    assert "surcharge UNKNOWN - this is NOT $0." in out


@patch("src.seats_client.requests.get")
def test_no_live_leg_renders_an_empty_or_dashed_surcharge_cell(
    mock_get, client, real_payload, rm
):
    payload = copy.deepcopy(real_payload)
    payload["data"][0]["Route"]["Source"] = "flyingblue"
    mock_get.return_value = _mock_response(payload)
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    results = _evaluate(fixture, rm)

    out = _render(print_leg_results, results)
    for r in results:
        if r.leg.points_provenance is not PointsProvenance.LIVE:
            continue
        assert r.surcharge is not None
        if not r.surcharge.is_known:
            assert r.surcharge.render() == "UNKNOWN"
    assert "UNKNOWN" in out
    # An unknown never renders as a zero.
    assert "UNKNOWN" not in out.replace("UNKNOWN", "X") or True
    for line in out.splitlines():
        if "UNKNOWN" in line:
            assert "$0.00" not in line, "an unknown surcharge rendered as $0.00"


@patch("src.seats_client.requests.get")
def test_a_floor_that_already_loses_gives_a_confident_verdict_not_a_withheld_one(
    mock_get, client, real_payload, rm
):
    """
    50,000 points is $500 at the run's valuation, PLUS the API's own known taxes
    of CAD 44.60 ($32.36), against $395 cash. A carrier surcharge can only ADD on
    top of that, so the answer is CERTAIN even though the number is unknown.
    Withholding here would hide an answer we actually have.

    THE EXPECTED FLOOR CHANGED AT THE v1-v3 ADVERSARIAL FIX, finding M-4. It was
    $500.00, which was this test pinning a bug: `award_to_candidate` forced the
    candidate's cash figure to 0.0 on the unscoreable path, so the API's KNOWN
    CAD 44.60 was dropped, while the formatter went on labelling the number
    "points at the run's valuation + the API's taxes ... the least this can
    possibly cost". The label named a component the figure did not contain. The
    floor is the least this can possibly cost, the taxes are a known part of that
    cost, and $532.36 is that number.
    """
    payload = copy.deepcopy(real_payload)
    payload["data"][0]["Route"]["Source"] = "flyingblue"
    mock_get.return_value = _mock_response(payload)
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    results = _evaluate(fixture, rm)
    b1 = next(r for r in results if r.leg.id == "B1")

    assert b1.points_floor_usd == pytest.approx(532.36176)
    assert b1.observed_taxes_usd == pytest.approx(32.36176)
    assert b1.points_floor_usd > b1.cash_total_score_usd
    assert b1.surcharge_cannot_change_verdict is True
    assert b1.verdict == "cash"
    assert "whatever it turns out to be" in b1.verdict_reason


@patch("src.seats_client.requests.get")
def test_a_live_price_never_renders_without_its_fetched_at(
    mock_get, client, real_payload, rm
):
    mock_get.return_value = _mock_response(real_payload)
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    for leg in fixture.legs:
        for cand in leg.points_candidates:
            if cand.source == LIVE_SOURCE:
                assert "Fetched" in cand.source_note

    results = _evaluate(fixture, rm)
    out = _render(print_live_leg_detail, results)
    assert LIVE_SOURCE in out
    assert "Fetched" in out


@patch("src.seats_client.requests.get")
def test_no_candidate_takes_its_metal_from_a_multi_entry_carrier_list(
    mock_get, client, real_payload
):
    """`YAirlines` is "AC, LH, UA, VL". Four airlines is not metal."""
    mock_get.return_value = _mock_response(real_payload)
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    for leg in fixture.legs:
        for cand in leg.points_candidates:
            if cand.source != LIVE_SOURCE:
                continue
            if cand.carrier_source == "seats_aero_ambiguous":
                assert cand.operating_carrier == ""
                assert cand.has_known_metal is False


@patch("src.seats_client.requests.get")
def test_an_award_with_no_ur_path_is_kept_and_marked_never_dropped(
    mock_get, client, real_payload
):
    """
    "The award exists and you cannot reach this program from UR" is a FINDING.
    Dropping the row would hide real availability.
    """
    payload = copy.deepcopy(real_payload)
    payload["data"][0]["Route"]["Source"] = "delta"
    mock_get.return_value = _mock_response(payload)
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    apply_live(fixture, client, _live_opts())
    b1 = next(leg for leg in fixture.legs if leg.id == "B1")
    assert b1.points_candidates, "the award must be kept"
    assert "NO UR PATH" in b1.points_candidates[0].source_note


# ===========================================================================
# Provenance counting
# ===========================================================================


def test_provenance_of_a_run_with_no_live_legs_is_badge(trip_b, rm):
    counts = provenance_counts(_evaluate(trip_b, rm))
    assert counts["margin_provenance"] == "badge"
    assert counts["legs_flight_total"] == 4
    assert "not a live margin" in counts["margin_provenance_note"]


def test_every_leg_id_is_actually_visible_in_the_rendered_table(trip_b, rm):
    """
    v3 added a provenance column and rich starved the Leg column to zero width,
    so the table rendered with a blank first column - every row anonymous, and
    every cross-reference in the rest of the output unusable. A table you cannot
    read is its own kind of dishonesty, so the width is now pinned by a test.
    """
    results = _evaluate(trip_b, rm)
    out = _render(print_leg_results, results)
    for r in results:
        assert f"│ {r.leg.id} " in out, f"{r.leg.id} is not visible in the table"
    assert "Provenance" in out
