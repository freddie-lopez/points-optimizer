"""
FINDING M-6: F-1's longer verdict label must not re-flow a money figure away.

"PAY CASH (never priced)" is five characters longer than the "PAY CASH
(no path)" it replaced. Rich fits an over-wide table by shaving EVERY column,
so those five characters came out of the cash column and the LIVE run of the
never-priced fixture printed the fare as "$2,400.…" - a figure the reader
cannot check and cannot tell from $2,400.99. The fare, the path cell and the
provenance date now survive; scenario G14 pins the whole transcript.

The Score columns are NOT covered here: they were already shaved by the
pre-UI CLI on this and other trips, and this round's contract is that nothing
but F-1 and F-3 moves. That is filed, not fixed.
"""
import io
import re

import pytest
from rich.console import Console

from src.formatter import print_leg_results
from tests import _cli_golden as g

MONEY = re.compile(r"\$[\d,]+\.?\d*…")


def _table(text):
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if "Per-leg: cash vs points" in l)
    end = next(i for i, l in enumerate(lines[start:], start) if l.startswith("└"))
    rows = [l for l in lines[start:end] if l.startswith("│ L")]
    assert rows, text
    return [[c.strip() for c in re.split(r"[│┃]", r)] for r in rows]


@pytest.fixture
def g14(tmp_path, monkeypatch):
    return g.run_scenario("G14", tmp_path / "work", monkeypatch).text


def test_the_live_never_priced_run_prints_the_fare_in_full(g14):
    cash = [r[3] for r in _table(g14)]
    assert cash == ["$2,400.00", "$1,850.00"]
    assert not any(MONEY.match(c) for c in cash), "a fare was truncated into an ellipsis"


def test_the_long_verdict_is_still_the_one_that_says_never_priced(g14):
    rows = _table(g14)
    assert rows[1][5] == "never priced"
    assert rows[1][-1].startswith("PAY CASH (never pric")


def test_the_path_and_the_provenance_date_are_not_shaved_either(g14):
    rows = _table(g14)
    assert rows[0][5] == "Air Canada Aeroplan @ 1:1"
    assert rows[0][11] == "live | user_entered_via_new_trip 2026-09-11"
    assert rows[1][11] == "none | user_entered_via_new_trip 2026-09-11"


def test_the_floors_engage_only_where_that_label_is(tmp_path, monkeypatch):
    """A trip with no never-priced leg is laid out exactly as before: the What
    column still asks for its 12 characters. (G6 is the LIVE trip_b run.)"""
    text = g.run_scenario("G6", tmp_path / "work", monkeypatch).text
    what = [len(re.split(r"[│┃]", r)[2]) for r in text.splitlines() if r.startswith("│ B")]
    assert what and min(what) >= 14, "the What column lost its floor on a trip without F-1"


def test_an_empty_leg_table_still_prints(tmp_path):
    """The floors are computed from the cells; no legs must not raise."""
    buf = io.StringIO()
    print_leg_results([], Console(file=buf, width=190))
    assert "Per-leg: cash vs points" in buf.getvalue()
