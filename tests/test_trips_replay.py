"""
Replaying recorded itinerary lookups with `--from-snapshot`.

The corpus is WRITTEN BY THE TOOL: a stubbed live run through `apply_live` with a
tmp `ResponseCache` archives the search snapshots, the trips snapshots and both
manifests. The replay then reads them with the network guard active.
"""
import json
import re
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from src import config, snapshot_replay
from src.live_trip import LiveOptions, apply_live
from src.main import build_parser, build_replay
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from tests.test_from_snapshot import REPLAY_BASE, run_cli
from tests.test_metal_end_to_end import B4_ID, Stub

ROOT = Path(__file__).parent.parent
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
HASH = re.compile(r"mh_[0-9a-f]{16}")


@pytest.fixture(autouse=True)
def fresh_client_state():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def live_corpus(tmp_path):
    """Run Trip B live against the stub; return (snapshot dir, B4's live MetalLookup)."""
    snap = tmp_path / "corpus"
    cache = ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=snap, ttl_seconds=0)
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(
        live=True, cache=cache, trip_id=fixture.id, trips_mode="auto",
        allow_badge_fallback=False,
    )
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="test_key_replay"), opts)
    b4 = next(l for l in fixture.legs if l.id == "B4")
    return snap, b4.points_candidates[0].metal


def replay_b4(snap):
    """Replay through the real build_replay; return B4's MetalLookup and the call count."""
    args = build_parser().parse_args(
        REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")]
    )
    fixture = load_trip_fixture(TRIP_B)
    transport, opts, _, mh = build_replay(args, None, fixture)
    with patch("src.seats_client.requests.get") as get:
        fixture, _ = apply_live(fixture, transport, opts)
    b4 = next(l for l in fixture.legs if l.id == "B4")
    return b4.points_candidates[0].metal, get.call_count, mh


def test_the_snapshot_transport_overrides_the_http_path():
    assert snapshot_replay.SnapshotTransport.trips_raw is not SeatsClient.trips_raw


def test_a_replay_reproduces_the_live_metal_line_plus_the_replay_clause(tmp_path):
    snap, live = live_corpus(tmp_path)
    assert live.status is MetalStatus.KNOWN
    replayed, calls, _ = replay_b4(snap)
    assert calls == 0, "a replay never touches requests.get"
    assert replayed.status is MetalStatus.KNOWN
    assert replayed.carriers == live.carriers
    assert replayed.render().startswith(live.render())
    assert "REPLAYED FROM A COMMITTED TRIPS SNAPSHOT" in replayed.render()
    assert "REPARSED" not in replayed.render()


def test_the_cli_replay_prints_the_same_line_and_writes_nothing(tmp_path, capsys, monkeypatch):
    snap, _ = live_corpus(tmp_path)
    before = sorted(str(p) for p in snap.rglob("*"))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "replay-cache")
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    flat = " ".join(out.split())
    assert "operating airline: VS by flight number (VS19)" in flat
    assert "REPLAYED FROM A COMMITTED TRIPS SNAPSHOT" in flat
    assert "itinerary lookups: 1 recorded row(s)" in flat
    assert sorted(str(p) for p in snap.rglob("*")) == before
    assert not (tmp_path / "replay-cache").exists()
    assert code in (0, 3, 4)


def _cli_hash(snap, capsys):
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    return code, out, HASH.search(out).group(0)


def test_no_trips_directory_reads_not_recorded_with_the_same_exit_and_the_old_hash(
    tmp_path, capsys
):
    snap, _ = live_corpus(tmp_path)
    code_with, out_with, hash_with = _cli_hash(snap, capsys)
    shutil.rmtree(snap / "trips_endpoint")
    code_without, out_without, hash_without = _cli_hash(snap, capsys)
    flat = " ".join(out_without.split())
    assert "operating airline: NOT RECORDED" in flat
    assert "none recorded (no trips_endpoint/MANIFEST.md)" in flat
    assert code_without == code_with
    # The trips-less hash is the one the pre-trips code computes: no trips lines.
    fixture = load_trip_fixture(TRIP_B)
    rows = snapshot_replay.parse_manifest(snap / "MANIFEST.md")
    selection = snapshot_replay.select_replay_set(rows, fixture.id)
    queryable = [
        snapshot_replay.LegQuery(l.id, l.origin, l.destination, l.date)
        for l in fixture.legs if l.kind == "flight"
    ]
    legacy = snapshot_replay.manifest_hash(
        selection.selected, snap, itinerary=queryable, trip_id=fixture.id
    )
    assert hash_without == legacy
    assert hash_with != hash_without, "adding trips rows changes the hash"


def test_not_recorded_names_the_domain_and_never_a_carrier(tmp_path):
    snap, _ = live_corpus(tmp_path)
    shutil.rmtree(snap / "trips_endpoint")
    replayed, calls, _ = replay_b4(snap)
    assert calls == 0
    assert replayed.status is MetalStatus.NOT_RECORDED
    text = replayed.render()
    assert "nothing was asked of Seats.aero on this run" in text
    assert "possible carriers are VS, DL" in text


def _trips_snapshot(snap):
    return next((snap / "trips_endpoint").glob("*.json"))


def test_a_missing_trips_snapshot_refuses_the_whole_replay(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    _trips_snapshot(snap).unlink()
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    assert code == 1
    flat = " ".join(out.split())
    assert "[snapshot_missing]" in flat
    assert "trips_endpoint/MANIFEST.md" in flat
    assert "replays as NOT RECORDED" in flat
    assert not re.search(r"\d+\.\d\d%", out)


def test_a_tampered_trips_snapshot_refuses_the_whole_replay(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    path = _trips_snapshot(snap)
    envelope = json.loads(path.read_text())
    envelope["pages"][0]["data"][0]["MileageCost"] = 1
    path.write_text(json.dumps(envelope))
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    assert code == 1
    assert "hash_mismatch" in out
    assert not re.search(r"\d+\.\d\d%", out)


def test_an_empty_trips_snapshot_refuses_the_whole_replay(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    path = _trips_snapshot(snap)
    envelope = json.loads(path.read_text())
    envelope["pages"] = []
    path.write_text(json.dumps(envelope))
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    assert code == 1
    assert "snapshot_empty" in out


def test_deleting_the_row_turns_the_refusal_into_not_recorded(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    _trips_snapshot(snap).unlink()
    manifest = snap / "trips_endpoint" / "MANIFEST.md"
    manifest.write_text(
        "\n".join(l for l in manifest.read_text().splitlines() if B4_ID not in l) + "\n"
    )
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    assert code != 1
    flat = " ".join(out.split())
    assert "operating airline: NOT RECORDED" in flat
    assert "itinerary lookups: 0 recorded row(s)" in flat


def test_an_old_trips_parser_version_prints_reparsed(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    path = _trips_snapshot(snap)
    envelope = json.loads(path.read_text())
    envelope["_meta"]["parser_version"] = "2026-01-01.old-trips"
    path.write_text(json.dumps(envelope))
    manifest = snap / "trips_endpoint" / "MANIFEST.md"
    from src import seats_trips

    manifest.write_text(
        manifest.read_text().replace(seats_trips.TRIPS_PARSER_VERSION, "2026-01-01.old-trips")
    )
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    flat = " ".join(out.split())
    assert "TRIPS LOOKUPS REPARSED" in flat
    assert "REPARSED: these bytes were captured under 2026-01-01.old-trips" in flat
    assert code != 1
