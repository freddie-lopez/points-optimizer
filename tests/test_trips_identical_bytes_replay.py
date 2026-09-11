"""
Re-test 2, R2-2: two lookups whose responses are byte-identical still replay.

The trips archive deduplicates a snapshot only against earlier snapshots of the
SAME availability id. Two ids that both got an empty itinerary list each get a
file of their own, so every trips file names exactly the lookup it records -
and the replay keeps refusing a row whose file is about another id (L13), with
no exception for "it happens to hold the same bytes".
"""
import json
import re

import pytest
from unittest.mock import patch

from src.live_trip import LiveOptions, apply_live
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from tests import _trips_payloads as tp
from tests.test_from_snapshot import REPLAY_BASE, run_cli
from tests.test_trips_flags import IDS, TRIP_B, Stub


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


class EmptyTrips(Stub):
    """Every itinerary lookup answers with the same empty list."""

    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        if "/trips/" in url:
            body = tp.payload([])
            r.json.return_value = body
            r.text = json.dumps(body)
        return r


def _live(snap, cache_dir):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    cache = ResponseCache(cache_dir=cache_dir, snapshot_dir=snap, ttl_seconds=0)
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode="auto",
                       allow_badge_fallback=False)
    stub = EmptyTrips()
    with patch("src.seats_client.requests.get", side_effect=stub):
        apply_live(fixture, SeatsClient(api_key="test_key_identical_bytes"), opts)
    return stub


def _rows(snap):
    text = (snap / "trips_endpoint" / "MANIFEST.md").read_text()
    return [l for l in text.splitlines() if "| trips:" in l]


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_each_id_gets_its_own_file_even_when_the_bytes_are_identical(tmp_path):
    snap = tmp_path / "corpus"
    stub = _live(snap, tmp_path / "cache")
    assert len(stub.trips_calls) >= 2, "precondition: two or more lookups"
    rows = _rows(snap)
    assert len(rows) == len(stub.trips_calls)
    hashes = {_cells(r)[8] for r in rows}
    assert len(hashes) == 1, "precondition: every response is byte-identical"
    files = [_cells(r)[7] for r in rows]
    assert len(set(files)) == len(rows)
    assert not any("(re-fetch, identical)" in r for r in rows)
    for r in rows:
        cells = _cells(r)
        meta = json.loads((snap / "trips_endpoint" / cells[7]).read_text())["_meta"]
        assert "trips:" + meta["availability_id"] == cells[2]


def test_the_corpus_replays_and_says_empty_for_each_id(tmp_path, capsys):
    snap = tmp_path / "corpus"
    _live(snap, tmp_path / "cache")
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    flat = " ".join(out.split())
    assert code != 1, flat[:900]
    assert "trips_snapshot_is_another_lookup" not in flat
    assert f"EMPTY itinerary list for availability {IDS['B4']}" in flat


def test_a_row_re_pointed_at_another_ids_identical_file_is_still_refused(tmp_path, capsys):
    snap = tmp_path / "corpus"
    _live(snap, tmp_path / "cache")
    rows = _rows(snap)
    b3 = next(r for r in rows if IDS["B3"] in r)
    b4 = next(r for r in rows if IDS["B4"] in r)
    cells = _cells(b4)
    cells[7] = _cells(b3)[7]
    manifest = snap / "trips_endpoint" / "MANIFEST.md"
    manifest.write_text(manifest.read_text().replace(b4, "| " + " | ".join(cells) + " |"))
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    flat = " ".join(out.split())
    assert code == 1
    assert "trips_snapshot_is_another_lookup" in flat
    assert not re.search(r"\d+\.\d\d%", out)


def test_a_re_fetch_of_the_same_id_with_identical_bytes_still_shares_its_file(tmp_path):
    snap = tmp_path / "corpus"
    _live(snap, tmp_path / "cache")
    first = {_cells(r)[2]: _cells(r)[7] for r in _rows(snap)}
    _live(snap, tmp_path / "cache")
    rows = _rows(snap)
    assert len(rows) == 2 * len(first)
    again = [r for r in rows if "(re-fetch, identical)" in r]
    assert len(again) == len(first)
    for r in again:
        cells = _cells(r)
        assert cells[7].startswith(first[cells[2]])
    assert len(list((snap / "trips_endpoint").glob("*.json"))) == len(first)
