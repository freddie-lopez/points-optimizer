"""
Fix round 1, Tester findings 12, 13 and 14: a trips replay reads only what its
files say.

  * 12: a trips MANIFEST.md with no manifest table (zero bytes, a merge
    conflict, any other text) is refused, never read as "no lookups recorded".
    A manifest whose table header is intact but whose rows were deleted still
    replays as NOT RECORDED (deviation 3, unchanged).
  * 13: a row whose snapshot is a lookup of ANOTHER availability id is refused
    before scoring, and the replay transport itself refuses to answer for an
    id its file is not about.
  * 14: the same id recorded twice with different bytes, and a row for a leg
    the trip does not have, are refused instead of resolved silently.

Every corpus is written by the tool (a stubbed live run into tmp_path).
"""
import dataclasses
import json
import re
import shutil

import pytest

from src import response_cache, snapshot_replay
from src.models import MetalStatus
from src.seats_client import SeatsClient, TripsLookupError
from tests.test_from_snapshot import REPLAY_BASE, run_cli
from tests.test_metal_end_to_end import B4_ID
from tests.test_trips_replay import live_corpus, replay_b4

OTHER_ID = "9zZ9yY8xX7wW6vV5uU4tT3sS2rR"
PCT = re.compile(r"\d+\.\d\d%")


@pytest.fixture(autouse=True)
def fresh_client_state():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _manifest(snap):
    return snap / "trips_endpoint" / "MANIFEST.md"


def _snapshot(snap):
    return next((snap / "trips_endpoint").glob("*.json"))


def _replay(snap, capsys):
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys)
    return code, out, " ".join(out.split())


def _b4_row(snap):
    return next(l for l in _manifest(snap).read_text().splitlines() if B4_ID in l and "|" in l)


def _write_recording(snap, name, availability_id, data):
    """A second, self-consistent recording: pages, _meta hash and ids all agree."""
    envelope = json.loads(_snapshot(snap).read_text())
    envelope["pages"][0]["data"] = data
    envelope["_meta"]["availability_id"] = availability_id
    envelope["_meta"]["request"]["availability_id"] = availability_id
    full = response_cache.content_hash(envelope["pages"])
    envelope["_meta"]["content_hash"] = full
    (snap / "trips_endpoint" / name).write_text(json.dumps(envelope))
    return full[:16]


# --------------------------------------------------------------------------
# 12. A manifest with no table is not "no lookups recorded"
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "<<<<<<< HEAD\n=======\n>>>>>>> other\n", "not a manifest\n"],
    ids=["zero-bytes", "merge-conflict", "other-text"],
)
def test_a_trips_manifest_without_its_table_is_refused(tmp_path, capsys, text):
    snap, _ = live_corpus(tmp_path)
    _manifest(snap).write_text(text)
    code, out, flat = _replay(snap, capsys)
    assert code == 1
    assert "trips_manifest_unreadable" in flat
    assert "NOT being read as 'no lookups recorded'" in flat
    assert not PCT.search(out)


def test_a_manifest_whose_rows_were_deleted_still_replays_as_not_recorded(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    manifest = _manifest(snap)
    kept = [l for l in manifest.read_text().splitlines() if B4_ID not in l]
    manifest.write_text("\n".join(kept) + "\n")
    code, out, flat = _replay(snap, capsys)
    assert code != 1
    assert "operating airline: NOT RECORDED" in flat
    assert "itinerary lookups: 0 recorded row(s)" in flat


# --------------------------------------------------------------------------
# 13. A recording of another id never answers for this one
# --------------------------------------------------------------------------


def test_a_row_pointing_at_another_ids_recording_is_refused(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    name = f"B4_trips_{OTHER_ID}_20260101T0000Z.json"
    short = _write_recording(snap, name, OTHER_ID, [])
    row = _b4_row(snap)
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    cells[7], cells[8] = name, short
    manifest = _manifest(snap)
    manifest.write_text(manifest.read_text().replace(row, "| " + " | ".join(cells) + " |"))
    code, out, flat = _replay(snap, capsys)
    assert code == 1
    assert "trips_snapshot_is_another_lookup" in flat
    assert f"a lookup of availability {OTHER_ID}, not of {B4_ID}" in flat
    assert "EMPTY itinerary list" not in flat
    assert not PCT.search(out)


def test_a_recording_that_names_no_id_is_refused(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    path = _snapshot(snap)
    envelope = json.loads(path.read_text())
    del envelope["_meta"]["availability_id"]
    del envelope["_meta"]["request"]["availability_id"]
    path.write_text(json.dumps(envelope))
    code, _, flat = _replay(snap, capsys)
    assert code == 1
    assert "trips_snapshot_is_another_lookup" in flat
    assert "records exactly one" in flat


def test_the_replay_transport_refuses_a_file_about_another_id(tmp_path):
    snap, _ = live_corpus(tmp_path)
    name = f"B4_trips_{OTHER_ID}_20260101T0000Z.json"
    _write_recording(snap, name, OTHER_ID, [])
    rows = snapshot_replay.parse_manifest(_manifest(snap))
    rows = [dataclasses.replace(rows[0], snapshot_name=name)]
    transport = snapshot_replay.SnapshotTransport(
        [], snap, trips_rows=rows, trips_dir=snap / "trips_endpoint"
    )
    with pytest.raises(TripsLookupError) as caught:
        transport.trips_raw(B4_ID)
    assert caught.value.code == "AVAILABILITY_ID_MISMATCH"
    assert OTHER_ID in str(caught.value)


def test_an_untouched_corpus_still_replays_known(tmp_path):
    snap, live = live_corpus(tmp_path)
    replayed, calls, _ = replay_b4(snap)
    assert calls == 0
    assert replayed.status is MetalStatus.KNOWN
    assert replayed.carriers == live.carriers


# --------------------------------------------------------------------------
# 14. Two answers for one id, and a row for a leg the trip does not have
# --------------------------------------------------------------------------


def test_one_id_recorded_twice_with_different_bytes_is_refused(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    envelope = json.loads(_snapshot(snap).read_text())
    changed = json.loads(json.dumps(envelope["pages"][0]["data"]))
    changed[0]["MileageCost"] = 1
    name = f"B3_trips_{B4_ID}_20270101T0000Z.json"
    short = _write_recording(snap, name, B4_ID, changed)
    row = _b4_row(snap)
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    cells[0] = "2027-01-01T00:00:00Z"
    cells[1] = "B3"
    cells[7], cells[8] = name, short
    manifest = _manifest(snap)
    manifest.write_text(manifest.read_text().rstrip("\n") + "\n| " + " | ".join(cells) + " |\n")
    code, out, flat = _replay(snap, capsys)
    assert code == 1
    assert "duplicate_trips_lookup" in flat
    assert not PCT.search(out)


def test_one_id_recorded_twice_with_the_same_bytes_is_not_a_conflict(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    row = _b4_row(snap)
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    cells[0] = "2027-01-01T00:00:00Z"
    cells[1] = "B3"
    manifest = _manifest(snap)
    manifest.write_text(manifest.read_text().rstrip("\n") + "\n| " + " | ".join(cells) + " |\n")
    code, _, flat = _replay(snap, capsys)
    assert "duplicate_trips_lookup" not in flat
    assert code != 1


@pytest.mark.parametrize("leg", ["B9", "B5"], ids=["no-such-leg", "hotel-leg"])
def test_a_row_for_a_leg_the_trip_does_not_fly_is_refused(tmp_path, capsys, leg):
    snap, _ = live_corpus(tmp_path)
    row = _b4_row(snap)
    manifest = _manifest(snap)
    manifest.write_text(manifest.read_text().replace(row, row.replace("| B4 |", f"| {leg} |", 1)))
    code, out, flat = _replay(snap, capsys)
    assert code == 1
    assert "trips_row_for_unknown_leg" in flat
    assert not PCT.search(out)


def test_no_trips_directory_is_still_not_recorded(tmp_path, capsys):
    snap, _ = live_corpus(tmp_path)
    shutil.rmtree(snap / "trips_endpoint")
    code, _, flat = _replay(snap, capsys)
    assert code != 1
    assert "operating airline: NOT RECORDED" in flat
