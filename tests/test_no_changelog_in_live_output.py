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


# ---------------------------------------------------------------------------
# Re-test 4 observations: --help and two replay refusals cited releases too
# ---------------------------------------------------------------------------


def _help(module):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    r = subprocess.run([sys.executable, "-m", module, "--help"], cwd=root,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout


@pytest.mark.parametrize("module", ["src.main", "src.trips_tools"])
def test_no_release_reference_in_help(module):
    text = _help(module)
    assert "EXIT CODES" in text or "exit" in text.lower()
    assert _scan(text) == [], _scan(text)


def test_no_release_reference_in_the_old_row_and_budget_refusals(tmp_path):
    from src import snapshot_replay
    from tests.test_snapshot_manifest import PRE_V5_MANIFEST, _three_row_manifest

    old = tmp_path / "old"
    old.mkdir()
    (old / "MANIFEST.md").write_text(PRE_V5_MANIFEST)
    (old / "old.json").write_text('{"_meta": {}, "pages": [{"data": []}]}')
    problems = snapshot_replay.verify(snapshot_replay.parse_manifest(old / "MANIFEST.md"), old)
    unknown = [p for p in problems if p.kind == "content_hash_unknown"]
    assert unknown, [p.kind for p in problems]

    manifest, snap = _three_row_manifest(tmp_path)
    manifest.write_text(
        manifest.read_text().replace("| ok | b2_synth.json", "| budget_exhausted | b2_synth.json")
    )
    budget = [
        p for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
        if p.kind == "impossible_state_archived"
    ]
    assert budget
    text = "\n".join(p.render() for p in unknown + budget)
    assert "written by an older version of the tool" in text
    assert _scan(text) == [], _scan(text)
