"""
Manager review, should-fix 6: every refusal whose remedy is deleting a trips
row (or the trips manifest) says that doing so changes the replay's manifest
hash, so a number quoted against the old hash will not reproduce.
"""
import json
import re

import pytest

from src.seats_client import SeatsClient
from tests.test_from_snapshot import REPLAY_BASE, run_cli
from tests.test_trips_replay import live_corpus

NOTE = "Deleting a trips row CHANGES THE REPLAY'S MANIFEST HASH"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _replay(snap, capsys):
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    return code, " ".join(out.split())


def test_a_missing_trips_snapshot_refusal_says_deleting_the_row_changes_the_hash(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    next((snap / "trips_endpoint").glob("*.json")).unlink()
    code, flat = _replay(snap, capsys)
    assert code == 1
    assert "delete its row from trips_endpoint/MANIFEST.md" in flat
    assert NOTE in flat
    assert "will not reproduce against the edited manifest" in flat


def test_an_unreadable_trips_manifest_refusal_says_so_too(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    (snap / "trips_endpoint" / "MANIFEST.md").write_text("")
    code, flat = _replay(snap, capsys)
    assert code == 1
    assert "delete the file to replay with no lookups" in flat
    assert NOTE in flat


def test_a_duplicate_lookup_refusal_says_so_too(tmp_path, capsys):
    from src import response_cache

    snap, _ = live_corpus(tmp_path)
    tdir = snap / "trips_endpoint"
    path = next(tdir.glob("*.json"))
    env = json.loads(path.read_text())
    env["pages"][0]["data"][0]["MileageCost"] = 1
    env["_meta"]["content_hash"] = response_cache.content_hash(env["pages"])
    (tdir / "dup.json").write_text(json.dumps(env))
    manifest = tdir / "MANIFEST.md"
    row = next(l for l in manifest.read_text().splitlines() if "| trips:" in l)
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    cells[0], cells[1] = "2027-01-01T00:00:00Z", "B3"
    cells[7], cells[8] = "dup.json", env["_meta"]["content_hash"][:16]
    manifest.write_text(manifest.read_text() + "| " + " | ".join(cells) + " |\n")
    code, flat = _replay(snap, capsys)
    assert code == 1
    assert "duplicate_trips_lookup" in flat
    assert NOTE in flat
    assert not re.search(r"\d+\.\d\d%", flat)
