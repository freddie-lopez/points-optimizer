"""
C-2: the qualifier travels with the number, for EVERY provenance.

A replay in which not one snapshot contributed a point printed the offline
Google-badge margin - byte-identical to `--offline` - with `mh_...` glued to the
same line, while the qualifier that corrects it (`badge_fallback`) sat two rows
below. The only token on the number was the false one.

The rule these tests hold: a percentage is never printed without a token on the
SAME LINE saying what produced it, and `mh_` is on a number only when the hashed
bytes produced that number.
"""
import re

import pytest
from rich.console import Console

from src.formatter import print_trip_totals


def _render(totals, width=200):
    console = Console(width=width, record=True, force_terminal=False)
    print_trip_totals(totals, label="T", console=console)
    return console.export_text()


def _base(**over):
    totals = {
        "all_cash_usd": 100.0,
        "optimized_usd": 90.0,
        "savings_usd": 10.0,
        "beat_cash_pct": 10.0,
        "points_spent": 0,
        "cash_still_owed_usd": 0.0,
        "legs_where_points_win": 0,
        # MR5-3: the "NO UR path at all" row is driven by `legs_no_partner`,
        # one predicate shared with the footer, and no longer by subtracting
        # `legs_never_priced` from `legs_without_points_path` - a subtraction
        # of two differently-derived counts that reached the screen as -1.
        "legs_no_partner": 0,
        "trip_funding_executable": True,
        "margin_provenance": "badge",
        "margin_provenance_note": "note",
    }
    totals.update(over)
    return totals


def _percentage_lines(text):
    return [ln for ln in text.splitlines() if re.search(r"\d+\.\d\d%", ln)]


@pytest.mark.parametrize(
    "provenance", ["live", "snapshot", "mixed", "badge", "badge_fallback"]
)
def test_every_percentage_line_carries_its_provenance(provenance):
    text = _render(_base(margin_provenance=provenance))
    lines = _percentage_lines(text)
    assert lines
    for line in lines:
        assert provenance in line, f"a percentage was printed bare: {line!r}"


def test_a_snapshot_margin_carries_the_hash_on_the_number():
    text = _render(
        _base(margin_provenance="snapshot", manifest_hash="mh_" + "a" * 16)
    )
    for line in _percentage_lines(text):
        assert "mh_" + "a" * 16 in line
        assert "snapshot" in line


def test_a_badge_fallback_margin_never_carries_an_unqualified_hash():
    """The false certificate. The hash may appear only next to its correction."""
    text = _render(
        _base(margin_provenance="badge_fallback", manifest_hash="mh_" + "b" * 16)
    )
    lines = _percentage_lines(text)
    assert lines
    for line in lines:
        assert "badge_fallback" in line
        if "mh_" in line:
            assert "NOT from" in line, f"unqualified hash on a number: {line!r}"


def test_the_manifest_row_says_when_the_bytes_produced_no_part_of_the_margin():
    text = _render(
        _base(margin_provenance="badge_fallback", manifest_hash="mh_" + "c" * 16)
    )
    assert "NO part of the margin above" in text
    clean = _render(
        _base(margin_provenance="snapshot", manifest_hash="mh_" + "c" * 16)
    )
    assert "NO part of the margin above" not in clean


def test_a_withheld_margin_prints_no_percentage_and_therefore_no_hash():
    text = _render(
        _base(
            margin_provenance="badge_fallback",
            manifest_hash="mh_" + "d" * 16,
            margin_withheld=True,
        )
    )
    assert _percentage_lines(text) == []
    assert "WITHHELD" in text
