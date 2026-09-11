"""
Manager review, decision D1 option (b): a YQ verdict covers ONE airline.

data/yq_inclusion.csv has an `airline` column. The loader backs a row only with
a record whose itinerary lookup was KNOWN and names that airline; yq-check
writes both into the record and the airline into the row template, and writes
no template at all when the lookup did not name one KNOWN airline. Scoring
(case B and case C) applies a verdict only to an award whose own lookup is KNOWN
on that airline - see test_yq_inclusion.py::test_a_verdict_covers_only_known_metal_on_its_airline.
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import trips_tools, yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from tests.test_trips_tools import Stub, yq_args

TODAY = date(2026, 9, 11)
HEADER = "source,airline,verdict,verified_on,evidence,notes"


def body(status="KNOWN", airline="VS", verdict="includes_yq", extra=""):
    return (
        "# yq-check record: virginatlantic, 2026-09-10\n\n## Seats.aero\n\n"
        "- program: Virgin Atlantic Flying Club (source virginatlantic)\n"
        f"- itinerary lookup status: {status}\n"
        f"- checked airline (the award's KNOWN flight-number carrier): {airline}\n{extra}\n"
        "## virginatlantic.com\n\n"
        "- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00\n"
        f"- verdict (includes_yq / excludes_yq / inconclusive): {verdict}\n"
    )


def load(tmp_path, records, *rows):
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in records.items():
        (d / name).write_text(text)
    csv = tmp_path / "yq.csv"
    csv.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    return yq_inclusion.load(csv, today=TODAY, root=tmp_path)


def row(airline="VS", name="a.md", verdict="includes_yq"):
    return f"virginatlantic,{airline},{verdict},2026-09-10,docs/yq-checks/{name},x"


def test_the_committed_table_has_the_airline_column():
    assert yq_inclusion.COLUMNS[1] == "airline"
    assert yq_inclusion.YQ_TABLE_PATH.read_text().strip() == HEADER


def test_a_record_on_vs_backs_a_vs_row(tmp_path):
    table = load(tmp_path, {"a.md": body()}, row())
    assert list(table) == [("virginatlantic", "VS")]


def test_two_airlines_of_one_source_are_two_rows(tmp_path):
    table = load(
        tmp_path, {"a.md": body(), "b.md": body(airline="AF", verdict="excludes_yq")},
        row(), row("AF", "b.md", "excludes_yq"),
    )
    assert table[("virginatlantic", "VS")].includes
    assert table[("virginatlantic", "AF")].excludes


@pytest.mark.parametrize(
    "record,row_,needle",
    [
        (body(airline="VS"), row("DL"), "was run on VS metal and the row says DL"),
        (body(status="AMBIGUOUS", airline="NONE - x"), row(), "not KNOWN"),
        (body(status="UNKNOWN"), row(), "not KNOWN"),
        (body(airline="NONE - the lookup did not name one KNOWN airline"), row(),
         "was run on NONE"),
        (body().replace("- itinerary lookup status: KNOWN\n", ""), row(),
         "0 'itinerary lookup status' lines"),
        (body().replace("- checked airline (the award's KNOWN flight-number carrier): VS\n", ""),
         row(), "0 'checked airline' lines"),
        (body(extra="- checked airline (the award's KNOWN flight-number carrier): VS\n"), row(),
         "2 'checked airline' lines"),
    ],
    ids=["other-airline", "ambiguous", "unknown", "none", "no-status", "no-airline", "two-airlines"],
)
def test_a_record_that_does_not_name_the_rows_known_airline_is_refused(tmp_path, record, row_, needle):
    with pytest.raises(YqInclusionError, match=needle):
        load(tmp_path, {"a.md": record}, row_)


# -- yq-check writes the airline --------------------------------------------------


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def run_yq(tmp_path, trips=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub(trips=trips)):
        code = trips_tools.main(
            yq_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=400), today=TODAY
        )
    out = " ".join(buf.getvalue().split())
    return code, out, next((tmp_path / "records").glob("*.md")).read_text()


@pytest.mark.parametrize("flight,airline", [("VS19", "VS"), ("DL41", "DL")])
def test_yq_check_writes_the_known_airline_into_the_record_and_the_row(tmp_path, flight, airline):
    code, out, record = run_yq(tmp_path, trips=tp.payload([tp.vs_direct(flight)]))
    assert "- itinerary lookup status: KNOWN" in record
    assert f"- checked airline (the award's KNOWN flight-number carrier): {airline}" in record
    assert f"virginatlantic,{airline},<VERDICT>,2026-09-11," in out


def test_yq_check_with_no_single_known_airline_prints_no_row(tmp_path):
    trips = tp.payload([tp.vs_direct("VS19", trip_id="t1"), tp.vs_direct("DL41", trip_id="t2")])
    code, out, record = run_yq(tmp_path, trips=trips)
    assert "- itinerary lookup status: AMBIGUOUS" in record
    assert "checked airline (the award's KNOWN flight-number carrier): NONE" in record
    assert "<VERDICT>" not in out
    assert "This check cannot back a verdict" in out
