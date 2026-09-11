"""
CLI output is byte-identical to the goldens recorded before the UI refactor.

Every scenario in tests/_cli_golden.py is run in-process under conftest's
harness and compared, after the narrow normalizer, with
tests/fixtures/cli_golden/<name>.txt. A difference prints a unified diff.

To (re)write the goldens - only ever deliberately, and the diff is reviewed:

    PO_WRITE_CLI_GOLDENS=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
        tests/test_cli_output_unchanged.py
"""
import difflib
import os

import pytest

from tests import _cli_golden as g

WRITE = os.environ.get("PO_WRITE_CLI_GOLDENS") == "1"

# Scenarios with nothing volatile in them at all. The normalizer must mask
# NOTHING here: a mask that fires on these is a mask wide enough to hide a real
# difference.
NO_VOLATILE_TOKENS = ("G1", "G2", "G3", "G7", "G8", "G12")


def _expected_exit(name):
    return {
        "G5": 3, "G8": 4, "G12": 2, "G13": 1,
    }.get(name, 0)


@pytest.mark.parametrize("name", sorted(g.SCENARIOS, key=lambda n: int(n[1:])))
def test_cli_output_matches_the_golden(name, tmp_path, monkeypatch):
    result = g.run_scenario(name, tmp_path / "work", monkeypatch)
    masked, _ = g.normalize(result.text)
    actual = g.header(g.SCENARIOS[name], result.code) + masked
    path = g.golden_path(name)
    if WRITE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
        return
    assert path.exists(), f"no golden for {name}; see this module's docstring"
    expected = path.read_text()
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(True), actual.splitlines(True),
                fromfile=f"golden/{name}.txt", tofile=f"now/{name}.txt",
            )
        )
        pytest.fail(f"CLI output for {name} changed:\n{diff}")


@pytest.mark.parametrize("name", sorted(g.SCENARIOS, key=lambda n: int(n[1:])))
def test_each_scenario_exits_as_designed(name, tmp_path, monkeypatch):
    result = g.run_scenario(name, tmp_path / "work", monkeypatch)
    assert result.code == _expected_exit(name), result.text[-2000:]


@pytest.mark.parametrize("name", NO_VOLATILE_TOKENS)
def test_the_normalizer_masks_nothing_where_nothing_is_volatile(name, tmp_path, monkeypatch):
    result = g.run_scenario(name, tmp_path / "work", monkeypatch)
    _, count = g.normalize(result.text)
    assert count == 0, f"the normalizer masked {count} token(s) in {name}"


@pytest.mark.parametrize("name", ["G4", "G6", "G13"])
def test_the_normalizer_masks_the_same_count_in_two_runs(name, tmp_path, monkeypatch):
    first = g.run_scenario(name, tmp_path / "one", monkeypatch)
    second = g.run_scenario(name, tmp_path / "two", monkeypatch)
    a, na = g.normalize(first.text)
    b, nb = g.normalize(second.text)
    assert na == nb
    assert a == b


def test_the_normalizer_is_narrow():
    """It masks instants, filename stamps and minute ages - and not a date,
    not a percentage, not a dollar figure, not a day count."""
    text = (
        "fetched 2026-09-11T10:20:30Z and Fetched 2026-09-11\n10:20:30.123456+00:00; "
        "B1_SFO_MAD_2027-01-15_20260911T1020Z.json; 12 minutes ago; "
        "2027-01-15 13.30% $1,209.30 (3 days old); VS19 LHR 2027-01-27T11:00:00Z"
    )
    masked, n = g.normalize(text)
    assert n == 4
    assert "2027-01-15 13.30% $1,209.30 (3 days old); VS19 LHR 2027-01-27T11:00:00Z" in masked
    assert "B1_SFO_MAD_2027-01-15_<STAMP>.json" in masked
