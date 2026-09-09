"""
Regression net for the Manager's five must-fixes (manager-review-v1-v4.md).

WHY THIS FILE EXISTS. The Manager's single sharpest criticism of the v4 round
was that a fix is not finished when its probe flips - it is finished when the
CLASS is closed and something in the MAINTAINED suite would notice it reopening.
The v4 Coder learned that lesson and built `test_v1_v3_adversarial_fixes.py`
unasked; this is the same net for MR-1 through MR-5.

Every test here fails if the corresponding fix is reverted.
"""
from datetime import date, datetime, timezone

import pytest

from src.models import (
    Award,
    LiveLegOutcome,
    LiveQuerySpec,
    LiveQueryState,
    PointsProvenance,
    Strategy,
)
from src.seats_client import SeatsClient, envelope_shape_error


SPEC = LiveQuerySpec(
    leg_id="B1",
    origin="SFO",
    destination="MAD",
    leg_date=date(2027, 1, 15),
    start_date=date(2027, 1, 15),
    end_date=date(2027, 1, 15),
)


def _outcome(**kw):
    base = dict(
        leg_id="B1",
        state=LiveQueryState.NO_AWARD_SPACE,
        provenance=PointsProvenance.UNAVAILABLE,
        queried=SPEC,
    )
    base.update(kw)
    return LiveLegOutcome(**base)


# ===========================================================================
# MR-1. Truncation is not a finding. THE FOURTH INSTANCE OF FAILURE-AS-FINDING.
# ===========================================================================


def test_MR1_no_award_space_over_a_truncated_result_set_is_unbuildable():
    """The invariant, not the branch. A `raise`, so `python -O` cannot remove it."""
    with pytest.raises(ValueError, match="INCOMPLETE result set"):
        _outcome(
            result_incomplete=True,
            incomplete_reason="hasMore with no cursor.",
        )


def test_MR1_the_truncation_flag_must_be_explicable():
    with pytest.raises(ValueError, match="no incomplete_reason"):
        _outcome(state=LiveQueryState.ANSWERED_INCOMPLETE, result_incomplete=True)


def test_MR1_answered_incomplete_requires_the_flag():
    with pytest.raises(ValueError, match="without result_incomplete"):
        _outcome(state=LiveQueryState.ANSWERED_INCOMPLETE)


def test_MR1_answered_incomplete_may_not_carry_an_api_error():
    """An API failure must not wear a coverage failure's clothes."""
    with pytest.raises(ValueError, match="ANSWERED_INCOMPLETE means the API ANSWERED"):
        _outcome(
            state=LiveQueryState.ANSWERED_INCOMPLETE,
            result_incomplete=True,
            incomplete_reason="page cap reached.",
            error="ConnectionError: boom",
        )


def test_MR1_the_two_empty_answers_no_longer_render_alike():
    """
    THE BUG THE MANAGER FOUND, PINNED.

    `{"data": [], "hasMore": true}` and `{"data": []}` used to render
    BYTE-IDENTICAL text. If that ever becomes true again, this fails.
    """
    complete = _outcome(rows_seen=0)
    truncated = _outcome(
        state=LiveQueryState.ANSWERED_INCOMPLETE,
        rows_seen=0,
        result_incomplete=True,
        incomplete_reason=(
            "pagination stopped after page 1 because the response says there are "
            "more results (hasMore) but carries no cursor and no usable offset."
        ),
    )
    assert complete.render() != truncated.render()
    assert "THIS IS A FINDING" in complete.render()
    assert "THIS IS NOT A FINDING" in truncated.render()
    assert "no award to buy on this date" in complete.render()
    assert "no award to buy on this date" not in truncated.render()


def test_MR1_the_truncation_clause_is_unconditional_on_every_render():
    """
    The previous fix left the marker on `pagination_note`, which
    `NO_AWARD_SPACE.render()` never read. Writing the truth somewhere nothing
    prints is not fixing it, so the clause is appended by `render()` itself.
    """
    for state, extra in [
        (LiveQueryState.OK, dict(awards_parsed=3)),
        (LiveQueryState.ANSWERED_INCOMPLETE, {}),
        (LiveQueryState.API_ERROR, dict(error="ConnectionError: boom")),
        (LiveQueryState.BUDGET_EXHAUSTED, dict(error="cap spent.")),
        (LiveQueryState.NOT_QUERIED, dict(note="hotel leg", queried=None)),
    ]:
        out = _outcome(
            state=state,
            result_incomplete=True,
            incomplete_reason="the 25-page safety cap was reached.",
            **extra,
        )
        assert "COVERAGE IS INCOMPLETE" in out.render(), state


def test_MR1_a_truncated_but_non_empty_result_stays_OK_and_still_discloses():
    """Awards we found are found; coverage is a FIELD, not a state."""
    out = _outcome(
        state=LiveQueryState.OK,
        awards_parsed=2,
        awards_on_leg_date=2,
        result_incomplete=True,
        incomplete_reason="the 25-page safety cap was reached.",
    )
    assert out.state is LiveQueryState.OK
    assert "COVERAGE IS INCOMPLETE" in out.render()


def test_MR1_truncation_is_not_a_finding_about_award_space():
    assert not LiveQueryState.ANSWERED_INCOMPLETE.is_a_finding_about_award_space
    assert _outcome(
        state=LiveQueryState.ANSWERED_INCOMPLETE,
        result_incomplete=True,
        incomplete_reason="x.",
    ).tells_us_nothing_about_award_space


def test_MR1_the_client_exposes_the_flag_that_previously_reached_nothing():
    """`RawSearchResult.incomplete` was set and read by nobody."""
    c = SeatsClient(api_key="x")
    assert hasattr(c, "last_incomplete")
    assert hasattr(c, "last_incomplete_reason")


# ===========================================================================
# MR-1's FIFTH WAY. Two documented row containers that disagree.
# ===========================================================================


def test_MR1_fifth_way_two_disagreeing_row_containers_is_unreadable_not_empty():
    """
    `{"data": [], "results": [...20 rows...]}` read `data`, saw nothing, and
    announced a finding over twenty discarded bookable rows. Choosing one of two
    containers is a guess, and a guess is not knowledge.
    """
    row = {
        "Route": {"OriginAirport": "SFO", "DestinationAirport": "MAD", "Source": "aeroplan"},
        "Date": "2027-01-15",
        "YAvailable": True,
        "YMileageCost": "30000",
        "TotalTaxes": 5000,
        "TaxesCurrency": "USD",
    }
    payload = {"data": [], "results": [row] * 20}

    why = envelope_shape_error(payload)
    assert why, "a disagreement between the two containers must be reported"
    assert "not the same rows" in why

    parsed = SeatsClient.parse_pages_detail([payload])
    assert parsed.rows_unreadable == 1
    assert parsed.awards == []
    # And the invariant then forbids calling that an absence of award space.
    with pytest.raises(ValueError, match="UNREADABLE"):
        _outcome(rows_seen=0, rows_skipped=1, rows_unreadable=1)


def test_MR1_identical_duplicate_containers_are_still_readable():
    """Agreement is not a disagreement. Only a conflict is unreadable."""
    assert envelope_shape_error({"data": [], "results": []}) == ""


# ===========================================================================
# MR-1's remaining axes: asked-at-all, and freshness.
# ===========================================================================


def test_MR1_not_queried_must_say_why():
    with pytest.raises(ValueError, match="NOT_QUERIED with no note"):
        _outcome(state=LiveQueryState.NOT_QUERIED, note="", queried=None)


def test_MR1_a_replayed_answer_must_know_its_own_age():
    with pytest.raises(ValueError, match="no cache_fetched_at"):
        _outcome(served_from_cache=True)
    ok = _outcome(
        served_from_cache=True,
        cache_fetched_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    assert "fetched None" not in ok.render()


def test_MR1_the_exhaustive_list_is_written_down():
    """
    The Manager asked for the list, not another instance fix. If someone deletes
    it, the next round starts from instances again.
    """
    import sys

    if sys.flags.optimize >= 2:
        # -OO strips docstrings; @dataclass then synthesises a signature string
        # in their place, so there is nothing to assert against.
        pytest.skip("docstrings stripped under -OO")
    doc = LiveLegOutcome.__doc__
    assert "EXHAUSTIVE LIST OF WAYS THIS TOOL CAN FAIL TO KNOW" in doc
    for axis in ("AXIS 1", "AXIS 2", "AXIS 3", "AXIS 4", "AXIS 5"):
        assert axis in doc, axis
    assert "HOW TO EXTEND THIS" in doc


# ===========================================================================
# MR-2. L-1's third call site: a captured surcharge in an unpriceable currency.
# ===========================================================================


def test_MR2_an_unpriceable_captured_surcharge_is_unknown_not_a_dead_run():
    from src.models import PointsCandidate
    from src.optimizer import _captured_surcharge

    cand = PointsCandidate(
        label="BA SFO-MAD",
        program="British Airways Executive Club",
        points=22500,
        cash_surcharge=15000.0,
        surcharge_captured=True,
        surcharge_currency="JPY",
    )
    est = _captured_surcharge(cand)
    assert est is not None
    assert not est.is_known, "an unconvertible captured figure is UNKNOWN, not $0"
    assert est.amount_point == 0.0, "the placeholder is guarded by is_known"
    assert "CANNOT BE PRICED" in est.notes
    assert "JPY" in est.notes
    assert "--fx JPY" in est.notes, "the message must name the way out"
    assert "BA SFO-MAD" in est.notes, "and it must name the leg's candidate"


def test_MR2_a_priceable_captured_surcharge_still_works():
    from src.models import PointsCandidate
    from src.optimizer import _captured_surcharge

    cand = PointsCandidate(
        label="BA SFO-MAD",
        program="British Airways Executive Club",
        points=22500,
        cash_surcharge=100.0,
        surcharge_captured=True,
        surcharge_currency="USD",
    )
    est = _captured_surcharge(cand)
    assert est.is_known
    assert est.amount_point == 100.0
    assert est.confidence == "captured"


# ===========================================================================
# MR-5. An honesty flag must not default to the unsafe value.
# ===========================================================================


def test_MR5_an_award_told_nothing_about_taxes_does_not_claim_to_know():
    """
    v0's bug on the single-route path the adversarial round never attacked: a
    badge award with cash_component=0.0 and no tax data was a KNOWN zero and
    rendered $0.00 in the HTML export with no marker.
    """
    a = Award(
        date=date(2027, 1, 15),
        program="British Airways Executive Club",
        award_type="Y",
        cost=22500,
        cash_component=0.0,
        airline="BA",
        route="SFO-LHR",
        source="google_badge_unverified",
    )
    assert a.cash_component_known is False


def test_MR5_an_award_that_does_know_still_says_so():
    a = Award(
        date=date(2027, 1, 15),
        program="Air Canada Aeroplan",
        award_type="Y",
        cost=50000,
        cash_component=32.15,
        airline="AC",
        route="SFO-MAD",
        cash_component_known=True,
    )
    assert a.cash_component_known is True


def test_MR5_a_strategy_built_without_stating_its_answer_is_a_lower_bound():
    from src.models import TransferPath

    s = Strategy(
        award=Award(
            date=date(2027, 1, 15),
            program="X",
            award_type="Y",
            cost=1,
            cash_component=0.0,
            airline="BA",
            route="A-B",
        ),
        transfer_path=TransferPath(total_transferred=1, target_points_received=1),
        points_cost=1,
        cash_cost=0.0,
        total_value=0.01,
    )
    assert s.cash_cost_known is False
    assert s.is_lower_bound


def test_MR5_the_html_export_marks_an_unknown_cash_component():
    """End to end: the default must reach the artefact that gets forwarded."""
    from src.formatter import _html_cash_cell
    from src.models import TransferPath

    s = Strategy(
        award=Award(
            date=date(2027, 1, 15),
            program="British Airways Executive Club",
            award_type="Y",
            cost=22500,
            cash_component=0.0,
            airline="BA",
            route="SFO-LHR",
            source="google_badge_unverified",
        ),
        transfer_path=TransferPath(
            total_transferred=23000, target_points_received=23000
        ),
        points_cost=23000,
        cash_cost=0.0,
        total_value=230.0,
    )
    cell = _html_cash_cell(s)
    assert "$0.00</td>" not in cell
    assert "UNKNOWN" in cell
