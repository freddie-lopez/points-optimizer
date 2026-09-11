"""
F. Replay: missing rows, tampered files, duplicate ids, rows for legs not in
the trip, trips-less hash stability against MASTER's own code, and the network
never touched.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (  # noqa: F401
    BASE, B4_VS, ROOT, Stub, aid_for, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src.response_cache import ResponseCache
from tests import _trips_payloads as tp

HASH = re.compile(r"mh_[0-9a-f]{16}")
LABEL = "[trips parser UNVERIFIED against a real Seats.aero response - built from the published schema only]"


def corpus(tmp_path, trips=None, rows_for=None):
    snap = tmp_path / "corpus"
    cache = ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=snap, ttl_seconds=0)
    stub = Stub(rows_for=rows_for or vs_b4_rows(),
                trips=trips if trips is not None else {B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, *_ = evaluate(stub, cache=cache)
    return snap, results


class Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError("a replay touched requests.get")


def replay(snap, capsys, monkeypatch):
    counting = Counting()
    code, out, fl, _ = run_cli(BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys, monkeypatch,
                               stub=counting, key=None)
    assert counting.calls == 0
    return code, out, fl


def test_a_replay_touches_no_network_needs_no_key_and_keeps_the_label(tmp_path, capsys, monkeypatch):
    snap, live = corpus(tmp_path)
    code, out, fl = replay(snap, capsys, monkeypatch)
    assert "operating airline: VS by flight number (VS19)" in fl
    assert "REPLAYED FROM A COMMITTED TRIPS SNAPSHOT" in fl
    assert LABEL in fl


def _master_manifest_hash(where):
    """snapshot_replay.manifest_hash exactly as it is on master a17497d."""
    src = subprocess.run(["git", "show", "a17497d:src/snapshot_replay.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    mod_path = where / "master_snapshot_replay_a17497d.py"
    mod_path.write_text(src)
    spec = importlib.util.spec_from_file_location("master_snapshot_replay_a17497d", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_an_old_corpus_with_no_trips_folder_prints_masters_hash(tmp_path, capsys, monkeypatch):
    """Tsuki's live_trip_b corpus has no trips_endpoint/. Its hash must be master's."""
    from src.trip_loader import load_trip_fixture
    from conftest import TRIP_B

    snap, _ = corpus(tmp_path)
    shutil.rmtree(snap / "trips_endpoint")
    code, out, fl = replay(snap, capsys, monkeypatch)
    printed = HASH.search(out).group(0)
    master = _master_manifest_hash(tmp_path)
    fixture = load_trip_fixture(TRIP_B)
    rows = master.parse_manifest(snap / "MANIFEST.md")
    selection = master.select_replay_set(rows, fixture.id)
    q = [master.LegQuery(l.id, l.origin, l.destination, l.date) for l in fixture.legs if l.kind == "flight"]
    assert printed == master.manifest_hash(selection.selected, snap, itinerary=q, trip_id=fixture.id)
    assert "operating airline: NOT RECORDED" in fl
    assert "B4" in fl.split("operating airline NOT RECORDED on")[1][:40]


def test_a_truncated_trips_manifest_is_refused_not_read_as_no_lookups(tmp_path, capsys, monkeypatch):
    """
    Deviation 3 lets a trips manifest with its rows deleted replay as NOT
    RECORDED. The code implements it as "parse_manifest raised, the file is
    readable -> rows = []", which also swallows a manifest truncated to zero
    bytes (or any file that is not a manifest) while its trips snapshots sit
    beside it. The recorded lookups silently become NOT RECORDED and the hash
    silently drops their lines.
    """
    snap, _ = corpus(tmp_path)
    code_ok, out_ok, _ = replay(snap, capsys, monkeypatch)
    (snap / "trips_endpoint" / "MANIFEST.md").write_text("")
    assert list((snap / "trips_endpoint").glob("*.json")), "the recorded snapshot is still there"
    code, out, fl = replay(snap, capsys, monkeypatch)
    assert code == 1, "a zero-byte trips manifest was replayed as 'no lookups recorded'"


def test_two_rows_for_one_availability_id_are_not_resolved_silently(tmp_path, capsys, monkeypatch):
    """
    ADMITTED gap, sized: two trips rows carrying the same availability id (here
    recorded under two different legs) - the later one wins and nothing says a
    second, different recording was set aside.
    """
    snap, _ = corpus(tmp_path)
    tdir = snap / "trips_endpoint"
    manifest = tdir / "MANIFEST.md"
    text = manifest.read_text()
    line = [l for l in text.splitlines() if l.startswith("|") and "| trips:" in l][0]
    # a second recording of the SAME id, later, under B3, whose bytes say DL
    other = json.loads(next(tdir.glob("*.json")).read_text())
    other["pages"] = [tp.payload([vs_itinerary(B4_VS, "DL41")])]
    from src.response_cache import content_hash
    h = content_hash(other["pages"])
    other["_meta"]["content_hash"] = h
    (tdir / "B3_trips_second.json").write_text(json.dumps(other))
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells[0] = "2026-12-31T23:59:59Z"
    cells[1] = "B3"
    cells[7] = "B3_trips_second.json"
    cells[8] = h[:16] if len(cells[8]) == 16 else h
    manifest.write_text(text + "| " + " | ".join(cells) + " |\n")
    code, out, fl = replay(snap, capsys, monkeypatch)
    assert code == 1 or "two recordings" in fl.lower() or "duplicate" in fl.lower(), fl[-600:]


def test_a_trips_row_for_a_leg_not_in_the_trip_is_not_used_silently(tmp_path, capsys, monkeypatch):
    """ADMITTED gap, sized: the leg column of a trips row is never read."""
    snap, _ = corpus(tmp_path)
    manifest = snap / "trips_endpoint" / "MANIFEST.md"
    manifest.write_text(manifest.read_text().replace("| B4 | trips:", "| B9 | trips:"))
    code, out, fl = replay(snap, capsys, monkeypatch)
    assert "operating airline: VS by flight number" not in fl or code == 1


def test_a_row_pointing_at_another_ids_recording_is_not_attributed_to_this_id(tmp_path, capsys, monkeypatch):
    """
    The replay transport reads the file a row names and never checks that the
    envelope it opens is a lookup OF THIS ID (`_meta.availability_id`). A row
    re-pointed at another lookup's file whose list is empty replays as
    "Seats.aero returned an EMPTY itinerary list for availability <this id>" -
    a statement about a request that was never made for this id.
    """
    snap, _ = corpus(tmp_path)
    tdir = snap / "trips_endpoint"
    env = json.loads(next(tdir.glob("*.json")).read_text())
    other_id = aid_for("B9", "zzz")
    env["_meta"]["availability_id"] = other_id
    env["_meta"]["request"]["availability_id"] = other_id
    env["pages"] = [tp.payload([])]
    from src.response_cache import content_hash
    h = content_hash(env["pages"])
    env["_meta"]["content_hash"] = h
    (tdir / "other.json").write_text(json.dumps(env))
    manifest = tdir / "MANIFEST.md"
    lines = manifest.read_text().splitlines()
    out_lines = []
    for l in lines:
        if l.startswith("|") and "| trips:" in l:
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            cells[7] = "other.json"
            cells[8] = h[: len(cells[8])]
            l = "| " + " | ".join(cells) + " |"
        out_lines.append(l)
    manifest.write_text("\n".join(out_lines) + "\n")
    code, out, fl = replay(snap, capsys, monkeypatch)
    assert f"EMPTY itinerary list for availability {B4_VS}" not in fl
