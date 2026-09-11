"""
Fix round 1, findings 1 (High) and 2, and deviation 5.

A row in data/yq_inclusion.csv moves scores, so it is backed only by a record
that is FOR ITS SOURCE and whose ONE verdict line SAYS ITS VERDICT. And yq-check
no longer prints a row with a verdict already in it for someone to copy.
"""
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import trips_tools, yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import YqInclusionError
from tests.test_trips_tools import FLAG_KEY, Stub, yq_args

TODAY = date(2026, 9, 11)
VERDICT = "- verdict (includes_yq / excludes_yq / inconclusive): {v}"


def record(source="virginatlantic", verdict_line=VERDICT.format(v="includes_yq"),
           program_source=None):
    program_source = program_source or source
    return (
        f"# yq-check record: {source}, 2026-09-10\n\n## Seats.aero\n\n"
        f"- program: Some Program (source {program_source})\n"
        f"- itinerary lookup status: KNOWN\n"
        f"- checked airline (the award's KNOWN flight-number carrier): VS\n\n## site\n\n"
        f"- the site shows this flight operated by VS itself, not a codeshare partner (yes / no): yes\n"
        f"- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00\n"
        f"{verdict_line}\n"
    )


def load_with(tmp_path, body, *rows):
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rec.md").write_text(body)
    csv = tmp_path / "yq.csv"
    csv.write_text("source,airline,verdict,verified_on,evidence,notes\n" + "\n".join(rows) + "\n")
    return yq_inclusion.load(csv, today=TODAY, root=tmp_path)


ROW = "virginatlantic,VS,{v},2026-09-10,docs/yq-checks/rec.md,x"


@pytest.mark.parametrize("says", ["excludes_yq", "inconclusive", "INCONCLUSIVE", "", "maybe"])
def test_a_record_that_does_not_say_the_rows_verdict_is_refused(tmp_path, says):
    with pytest.raises(YqInclusionError):
        load_with(tmp_path, record(verdict_line=VERDICT.format(v=says)), ROW.format(v="includes_yq"))


def test_an_includes_record_cannot_back_an_excludes_row(tmp_path):
    with pytest.raises(YqInclusionError, match="says includes_yq and the row says excludes_yq"):
        load_with(tmp_path, record(), ROW.format(v="excludes_yq"))


def test_a_record_with_no_verdict_line_or_two_is_refused(tmp_path):
    with pytest.raises(YqInclusionError, match="0 verdict lines"):
        load_with(tmp_path, record(verdict_line=""), ROW.format(v="includes_yq"))
    two = record() + VERDICT.format(v="includes_yq") + "\n"
    with pytest.raises(YqInclusionError, match="2 verdict lines"):
        load_with(tmp_path, two, ROW.format(v="includes_yq"))


def test_a_record_for_one_source_cannot_back_another(tmp_path):
    with pytest.raises(YqInclusionError, match="not for 'flyingblue'"):
        load_with(
            tmp_path, record(),
            ROW.format(v="includes_yq"),
            "flyingblue,VS,includes_yq,2026-09-10,docs/yq-checks/rec.md,copied",
        )


def test_a_record_whose_program_line_names_another_source_is_refused(tmp_path):
    with pytest.raises(YqInclusionError):
        load_with(tmp_path, record(program_source="flyingblue"), ROW.format(v="includes_yq"))


def test_a_record_with_no_source_title_is_refused(tmp_path):
    body = record().replace("# yq-check record: virginatlantic, 2026-09-10", "# yq-check record")
    with pytest.raises(YqInclusionError, match="title"):
        load_with(tmp_path, body, ROW.format(v="includes_yq"))


@pytest.mark.parametrize("v", ["includes_yq", "excludes_yq"])
def test_a_matching_record_loads(tmp_path, v):
    table = load_with(tmp_path, record(verdict_line=VERDICT.format(v=v)), ROW.format(v=v))
    assert table[("virginatlantic", "VS")].verdict == v


# ---------------------------------------------------------------------------
# Deviation 5: no verdict-bearing row is printed
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def run_yq(tmp_path):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code = trips_tools.main(
            yq_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=250), today=TODAY
        )
    return code, " ".join(buf.getvalue().split())


def test_yq_check_prints_no_row_with_a_verdict_in_it(tmp_path):
    code, out = run_yq(tmp_path)
    assert code == 0
    assert "includes_yq,2026" not in out and "excludes_yq,2026" not in out
    assert "virginatlantic,VS,<VERDICT>,2026-09-11," in out


def test_the_printed_template_pasted_unchanged_is_refused(tmp_path):
    run_yq(tmp_path)
    rec = next((tmp_path / "records").glob("*.md"))
    text = rec.read_text().replace("inconclusive): ____", "inconclusive): excludes_yq")
    text = text.replace("(yes / no): ____", "(yes / no): yes")
    text = text.replace("____", "filled")
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True)
    (d / rec.name).write_text(text)
    ev = f"docs/yq-checks/{rec.name}"
    csv = tmp_path / "yq.csv"
    for verdict, ok in (("<VERDICT>", False), ("includes_yq", False), ("excludes_yq", True)):
        csv.write_text(
            "source,airline,verdict,verified_on,evidence,notes\n"
            f"virginatlantic,VS,{verdict},2026-09-11,{ev},x\n"
        )
        if ok:
            table = yq_inclusion.load(csv, today=TODAY, root=tmp_path)
            assert table[("virginatlantic", "VS")].excludes
        else:
            with pytest.raises(YqInclusionError):
                yq_inclusion.load(csv, today=TODAY, root=tmp_path)
