"""
Re-test 2, R2-1: a record `yq-check` wrote, filled in exactly as it asks, loads.

The blank marker appears only on the five field lines of the program-site half;
the intro prose no longer quotes it, so filling those five fields (and nothing
else) plus the printed row with <VERDICT> replaced is enough.
"""
from src import trips_tools, yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import BLANK
from tests.test_trips_tools import TODAY, run, yq_args

FIELDS = {
    "- date checked: ____": "- date checked: 2026-09-12",
    "- flight(s) shown: ____": "- flight(s) shown: VS19 LHR-SFO 11:00",
    "- the site shows this flight operated by VS itself, not a codeshare partner (yes / no): ____": "- the site shows this flight operated by VS itself, not a codeshare partner (yes / no): yes",
    "- total taxes, fees and carrier-imposed charges for ONE adult, as the site shows it (one combined figure, or its lines added up): ____": "- total taxes, fees and carrier-imposed charges for ONE adult, as the site shows it (one combined figure, or its lines added up): GBP 450.00",
    "- verdict (includes_yq / excludes_yq / inconclusive): ____":
        "- verdict (includes_yq / excludes_yq / inconclusive): {verdict}",
}


def setup_function():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _check(tmp_path, monkeypatch):
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    rec_dir = tmp_path / "docs" / "yq-checks"
    argv = yq_args(tmp_path)
    argv[argv.index("--record-dir") + 1] = str(rec_dir)
    code, out, _ = run(argv)
    assert code == 0, out
    template = (
        out.split("<VERDICT> replaced by that same word:", 1)[1]
        .split(" An inconclusive check", 1)[0].strip()
    )
    assert template.startswith("virginatlantic,VS,<VERDICT>,2026-09-11,docs/yq-checks/"), template
    return next(rec_dir.glob("*.md")), template


def test_the_marker_appears_only_on_the_five_field_lines(tmp_path, monkeypatch):
    rec, _ = _check(tmp_path, monkeypatch)
    lines = [l for l in rec.read_text().splitlines() if BLANK in l]
    assert lines == list(FIELDS), lines


def _fill(rec, verdict):
    text = rec.read_text()
    for blank, filled in FIELDS.items():
        text = text.replace(blank, filled.format(verdict=verdict))
    rec.write_text(text)


def test_a_record_filled_as_instructed_loads(tmp_path, monkeypatch):
    rec, template = _check(tmp_path, monkeypatch)
    _fill(rec, "includes_yq")
    csv = tmp_path / "yq.csv"
    csv.write_text(
        "source,airline,verdict,verified_on,evidence,notes\n"
        + template.replace("<VERDICT>", "includes_yq") + "\n"
    )
    assert yq_inclusion.load(csv, today=TODAY, root=tmp_path)[("virginatlantic", "VS")].includes


def test_one_field_left_blank_is_still_refused(tmp_path, monkeypatch):
    import pytest

    rec, template = _check(tmp_path, monkeypatch)
    _fill(rec, "includes_yq")
    rec.write_text(rec.read_text().replace("- date checked: 2026-09-12", "- date checked: ____"))
    csv = tmp_path / "yq.csv"
    csv.write_text(
        "source,airline,verdict,verified_on,evidence,notes\n"
        + template.replace("<VERDICT>", "includes_yq") + "\n"
    )
    with pytest.raises(yq_inclusion.YqInclusionError, match="blanks"):
        yq_inclusion.load(csv, today=TODAY, root=tmp_path)
