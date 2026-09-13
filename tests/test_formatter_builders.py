"""
UI plan step 5: the formatter's builders, `search_award_cash`, and
`MetalLookup.parser_label_text`.

The goldens (tests/test_cli_output_unchanged.py) prove the terminal bytes did
not move. These pin that the builders ARE what the terminal prints - so the
UI, which renders the builders, shows the terminal's own words - and that the
extracted search-tax rule is the one `optimize()` applies.
"""
import pytest

from src.formatter import (
    leg_table_cells,
    trip_headline,
    trip_notes,
    trip_totals_rows,
    verdict_kind,
)
from src.models import MetalLookup, MetalStatus
from src.optimizer import search_award_cash
from tests import _cli_golden as g


def _flat(text):
    return " ".join(text.split())


@pytest.mark.parametrize("name", ["G1", "G4", "G5", "G7"])
def test_every_row_the_table_prints_is_a_builder_row(name, tmp_path, monkeypatch):
    result = g.run_scenario(name, tmp_path / "w", monkeypatch)
    run = result.sink[0]
    printed = _flat(result.text)
    for r in run.results:
        cells = leg_table_cells(r)
        # The leg id and the verdict label as the builder has them appear on
        # the printed row (rich may truncate a long cell with an ellipsis).
        assert r.leg.id in printed
        label = cells.verdict.segments[0].text
        assert label[:12] in printed
    for row in trip_totals_rows(run.totals):
        first_line = "".join(s.text for s in row.label).split("\n")[0].strip()
        assert first_line[:30] in printed
    for note in trip_notes(run.totals):
        assert _flat(note.text)[:60] in printed


def test_verdict_kind_is_from_the_code_not_the_words(tmp_path, monkeypatch):
    run = g.run_scenario("G1", tmp_path / "w", monkeypatch).sink[0]
    kinds = {r.leg.id: verdict_kind(r) for r in run.results}
    assert kinds["B3"] == "withheld"
    assert kinds["B4"] == "points"
    assert kinds["B1"] == "cash"


def test_the_headline_builder_never_carries_a_single_number_for_a_range(tmp_path, monkeypatch):
    run = g.run_scenario("G1", tmp_path / "w", monkeypatch).sink[0]
    h = trip_headline(run.totals)
    assert h.state == "range"
    assert h.to_json()["pct"] is None
    assert h.qualifier == "(badge)"


def test_search_award_cash_is_what_optimize_used(tmp_path, monkeypatch):
    result = g.run_scenario("G9", tmp_path / "w", monkeypatch)
    run = result.sink[0]
    for s in run.strategies:
        known, cash, note, floor = search_award_cash(run.trip, s.award)
        assert known == s.cash_cost_known
        assert cash == s.cash_cost
        assert note == s.cash_cost_note
        assert s.total_value == pytest.approx(s.points_cost * 0.01 + cash + floor)


def test_search_award_cash_never_reports_an_unknown_as_a_number(tmp_path, monkeypatch):
    result = g.run_scenario("G11", tmp_path / "w", monkeypatch)
    run = result.sink[0]
    assert run.awards
    for award in run.awards:
        known, cash, note, _ = search_award_cash(run.trip, award)
        if not known:
            assert cash == 0.0 and note


def test_parser_label_text_is_exactly_the_clause_render_appends():
    from src import seats_trips

    known = MetalLookup(status=MetalStatus.KNOWN, availability_id="x", carriers=("VS",),
                        flight_numbers=("VS19",), matched_trips=1, row_carriers=("VS", "DL"),
                        provenance="seats_aero_trips")
    assert known.parser_label_text == seats_trips.trips_parser_label()
    assert known.render().endswith(known.parser_label_text)
    not_looked = MetalLookup(status=MetalStatus.NOT_LOOKED_UP, availability_id="x",
                             reason_code="NOT_NEEDED_POLICY", possible_carriers=("AC",),
                             detail="Air Canada Aeroplan levies no carrier surcharge")
    assert not_looked.parser_label_text == ""
