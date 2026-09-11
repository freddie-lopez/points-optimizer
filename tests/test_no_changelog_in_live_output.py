"""
Manager review, should-fix 5a: live output describes the trip, never this
project's history ("the v0 bug").

tests/test_no_changelog_in_user_output.py scans every committed trip fixture's
OFFLINE output. It predates the live path and is left untouched; this file runs
the same scanner over LIVE output, with the transport stubbed: Trip B in the
default mode, with `--trips all`, with an includes_yq / excludes_yq verdict
table in force, and a single-route search. Nothing is sent anywhere.
"""
from datetime import date
from unittest.mock import patch

import pytest

from src import yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import YqVerdict
from tests.test_from_snapshot import run_cli
from tests.test_metal_end_to_end import Stub
from tests.test_no_changelog_in_user_output import _scan

BASE = [
    "--trip-fixture", "trip_b_europe.json", "--balance", "UR=160000",
    "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15",
    "--allow-badge-fallback",
]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_no_changelog_live")
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _live(capsys, *extra):
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code, out = run_cli(BASE + list(extra), capsys)
    assert "Traceback (most recent call last)" not in out
    return code, out


@pytest.mark.parametrize("extra", [[], ["--trips", "all"], ["--trips", "off"]],
                         ids=["auto", "all", "off"])
def test_no_release_reference_in_live_trip_output(capsys, extra):
    code, out = _live(capsys, *extra)
    assert out.strip()
    assert _scan(out) == [], _scan(out)


@pytest.mark.parametrize("which", ["includes_yq", "excludes_yq"])
def test_no_release_reference_with_a_verdict_in_force(capsys, monkeypatch, which):
    table = {("virginatlantic", "VS"): YqVerdict(
        "virginatlantic", "VS", which, date(2026, 9, 10),
        "docs/yq-checks/2026-09-10-virginatlantic.md",
    )}
    monkeypatch.setattr(yq_inclusion, "load", lambda *a, **k: table)
    code, out = _live(capsys)
    assert _scan(out) == [], _scan(out)
