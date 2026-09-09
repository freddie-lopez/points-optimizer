"""
v5 Step 2: the manifest as an INPUT, and what a quoted margin is hashed against.

Nothing here scores anything. These tests are about one question: can a number
somebody quoted last month be re-derived today from committed bytes, and can
anybody tamper with those bytes without the tool noticing.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import response_cache, snapshot_replay
from src.response_cache import ResponseCache, content_hash
from src.seats_client import PARSER_VERSION

REAL_SNAPSHOT = (
    Path(__file__).parent / "fixtures" / "seats_aero" / "sfo_mad_real.json"
)


@pytest.fixture(autouse=True)
def _no_key_in_env(monkeypatch):
    """`assert_no_key_material` reads the environment; keep it deterministic."""
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(
        cache_dir=tmp_path / "cache",
        snapshot_dir=tmp_path / "snap",
        ttl_seconds=3600,
    )


def _pages(marker: str = "a"):
    return [{"data": [{"ID": marker, "Date": "2027-01-15"}], "hasMore": False}]


def _put(cache, leg_id, origin, destination, pages, *, trip_id="trip_b", when=None):
    request = {
        "origin_airport": origin,
        "destination_airport": destination,
        "start_date": "2027-01-15",
        "end_date": "2027-01-15",
    }
    key = response_cache.request_key("search", request)
    return cache.put(
        key,
        request,
        pages,
        meta={"leg_id": leg_id, "trip_id": trip_id, "rows_seen": 1},
        now=when or datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_put_writes_a_row_whose_content_hash_matches_the_archived_file(cache):
    pages = _pages()
    _put(cache, "B1", "SFO", "MAD", pages)

    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    assert len(rows) == 1
    row = rows[0]

    archived = cache.snapshot_dir / row.snapshot_name
    recomputed = snapshot_replay.recompute_content_hash(archived)
    assert recomputed == content_hash(pages)
    assert recomputed.startswith(row.content_hash)
    assert row.parser_version == PARSER_VERSION
    assert row.trip_id == "trip_b"
    assert row.leg_id == "B1"


def test_parser_version_reaches_the_envelope_too(cache):
    _put(cache, "B1", "SFO", "MAD", _pages())
    envelope = json.loads(next(cache.snapshot_dir.glob("*.json")).read_text())
    assert envelope["_meta"]["parser_version"] == PARSER_VERSION


def test_annotate_manifest_still_fills_state_and_awards_with_the_new_columns(cache):
    _put(cache, "B1", "SFO", "MAD", _pages())
    name = snapshot_replay.parse_manifest(cache.manifest_path)[0].snapshot_name
    assert cache.annotate_manifest(name, "ok", 3) is True

    row = snapshot_replay.parse_manifest(cache.manifest_path)[0]
    assert row.state == "ok"
    assert row.awards == "3"
    # The three new columns survived the in-place edit.
    assert row.parser_version == PARSER_VERSION
    assert row.trip_id == "trip_b"
    assert row.content_hash


# ---------------------------------------------------------------------------
# Pre-v5 rows
# ---------------------------------------------------------------------------


PRE_V5_MANIFEST = """# Live Seats.aero snapshot manifest

| fetched_at (UTC) | leg | route | dates | rows | awards | state | snapshot |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| 2026-09-08T10:00:00Z | B1 | SFO->MAD | 2027-01-15..2027-01-15 | 9 | 2 | ok | old.json |
"""


def test_pre_v5_rows_parse_with_none_and_read_as_unknown(tmp_path):
    path = tmp_path / "MANIFEST.md"
    path.write_text(PRE_V5_MANIFEST)

    row = snapshot_replay.parse_manifest(path)[0]
    assert row.content_hash is None
    assert row.parser_version is None
    assert row.trip_id is None
    assert row.content_hash_display == "unknown"
    assert row.parser_version_display == "unknown"


def test_a_pre_v5_row_is_not_replayable(tmp_path):
    """
    UNKNOWN is not "matches". A hash quoted over a row nobody can check against
    the file proves nothing, and it would be printed as if it did.
    """
    path = tmp_path / "MANIFEST.md"
    path.write_text(PRE_V5_MANIFEST)
    (tmp_path / "old.json").write_text(
        json.dumps({"_meta": {}, "pages": _pages()})
    )

    rows = snapshot_replay.parse_manifest(path)
    problems = snapshot_replay.verify(rows, tmp_path)
    assert [p.kind for p in problems] == ["content_hash_unknown"]


def test_an_empty_manifest_is_refused_not_treated_as_an_empty_replay_set(tmp_path):
    path = tmp_path / "MANIFEST.md"
    path.write_text("# not a manifest\n\njust some prose.\n")
    with pytest.raises(snapshot_replay.ManifestError):
        snapshot_replay.parse_manifest(path)


def test_a_missing_manifest_is_refused(tmp_path):
    with pytest.raises(snapshot_replay.ManifestError):
        snapshot_replay.parse_manifest(tmp_path / "nope.md")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_the_later_fetch_is_selected_and_the_earlier_reported_as_superseded(cache):
    t0 = datetime(2027, 1, 1, 10, 0, tzinfo=timezone.utc)
    _put(cache, "B1", "SFO", "MAD", _pages("first"), when=t0)
    _put(cache, "B1", "SFO", "MAD", _pages("second"), when=t0 + timedelta(hours=6))

    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    selection = snapshot_replay.select_replay_set(rows, "trip_b")

    assert len(selection.selected) == 1
    assert len(selection.superseded) == 1
    assert selection.selected[0].fetched_at > selection.superseded[0].fetched_at
    assert selection.describe() == "2 rows considered, 1 groups, 1 selected, 1 superseded"


def test_an_identical_refetch_does_not_double_count(cache):
    """
    A byte-identical re-fetch writes a second ROW pointing at the SAME file.
    Both rows are one question asked twice, so the replay set has one entry.
    """
    t0 = datetime(2027, 1, 1, 10, 0, tzinfo=timezone.utc)
    _put(cache, "B1", "SFO", "MAD", _pages("same"), when=t0)
    _put(cache, "B1", "SFO", "MAD", _pages("same"), when=t0 + timedelta(hours=1))

    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    assert rows[1].is_refetch is True
    assert rows[0].snapshot_name == rows[1].snapshot_name
    assert len(list(cache.snapshot_dir.glob("*.json"))) == 1

    selection = snapshot_replay.select_replay_set(rows, "trip_b")
    assert len(selection.selected) == 1
    assert not selection.problems


def test_different_legs_are_different_groups(cache):
    _put(cache, "B1", "SFO", "MAD", _pages("one"))
    _put(cache, "B4", "LHR", "SFO", _pages("two"))

    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    selection = snapshot_replay.select_replay_set(rows, "trip_b")
    assert len(selection.selected) == 2
    assert selection.groups == 2


def test_two_rows_with_the_same_fetched_at_and_different_snapshots_is_a_problem(
    tmp_path,
):
    """Picking either would make a quoted number depend on file order."""
    manifest = tmp_path / "MANIFEST.md"
    manifest.write_text(
        "| fetched_at (UTC) | leg | route | dates | rows | awards | state | snapshot | content_hash | parser_version | trip_id |\n"
        "| 2027-01-01T10:00:00Z | B1 | SFO->MAD | d | 1 | 1 | ok | a.json | aaaa | v1 | t |\n"
        "| 2027-01-01T10:00:00Z | B1 | SFO->MAD | d | 1 | 1 | ok | b.json | bbbb | v1 | t |\n"
    )
    rows = snapshot_replay.parse_manifest(manifest)
    selection = snapshot_replay.select_replay_set(rows, "t")
    assert [p.kind for p in selection.problems] == ["ambiguous_latest_row"]


def test_selection_filters_by_trip_id(cache):
    _put(cache, "B1", "SFO", "MAD", _pages("b"), trip_id="trip_b")
    _put(cache, "A1", "MRY", "JFK", _pages("a"), trip_id="trip_a")

    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    selection = snapshot_replay.select_replay_set(rows, "trip_b")
    assert [r.leg_id for r in selection.selected] == ["B1"]


# ---------------------------------------------------------------------------
# The hash
# ---------------------------------------------------------------------------


def _three_row_manifest(tmp_path):
    """The one real snapshot plus two synthesised envelopes."""
    snap = tmp_path / "snap"
    snap.mkdir()
    real_pages = [json.loads(REAL_SNAPSHOT.read_text())]
    envelopes = {
        "b1_real.json": real_pages,
        "b2_synth.json": _pages("synthetic-two"),
        "b3_synth.json": _pages("synthetic-three"),
    }
    lines = [
        "# Live Seats.aero snapshot manifest",
        "",
        "some prose that is deliberately not hashed",
        "",
        "| fetched_at (UTC) | leg | route | dates | rows | awards | state | snapshot | content_hash | parser_version | trip_id |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for i, (name, pages) in enumerate(envelopes.items(), start=1):
        digest = content_hash(pages)
        (snap / name).write_text(
            json.dumps({"_meta": {"content_hash": digest,
                                  "parser_version": PARSER_VERSION,
                                  "fetched_at": "2027-01-01T10:00:00Z"},
                        "pages": pages})
        )
        lines.append(
            f"| 2027-01-0{i}T10:00:00Z | B{i} | R{i} | D{i} | 1 | 1 | ok | {name} "
            f"| {digest[:16]} | {PARSER_VERSION} | trip_b |"
        )
    manifest = tmp_path / "MANIFEST.md"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest, snap


def test_three_row_manifest_hashes_deterministically(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    rows = snapshot_replay.parse_manifest(manifest)
    assert snapshot_replay.verify(rows, snap) == []

    h1 = snapshot_replay.manifest_hash(rows, snap)
    h2 = snapshot_replay.manifest_hash(rows, snap)
    assert h1 == h2
    assert h1.startswith("mh_") and len(h1) == 19


def test_the_hash_is_invariant_under_row_reordering(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    rows = snapshot_replay.parse_manifest(manifest)
    assert snapshot_replay.manifest_hash(rows, snap) == snapshot_replay.manifest_hash(
        list(reversed(rows)), snap
    )


def test_the_hash_is_invariant_under_edits_to_the_manifest_prose(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    before = snapshot_replay.manifest_hash(
        snapshot_replay.parse_manifest(manifest), snap
    )

    text = manifest.read_text().replace(
        "some prose that is deliberately not hashed",
        "a typo in the prose, corrected months after a number was quoted",
    )
    manifest.write_text(text)

    after = snapshot_replay.manifest_hash(
        snapshot_replay.parse_manifest(manifest), snap
    )
    assert before == after, "fixing a typo must not invalidate a quoted number"


def test_the_hash_changes_when_one_byte_of_one_snapshot_changes(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    before = snapshot_replay.manifest_hash(
        snapshot_replay.parse_manifest(manifest), snap
    )

    target = snap / "b2_synth.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["ID"] = "synthetic-twoX"
    target.write_text(json.dumps(envelope))

    after = snapshot_replay.manifest_hash(
        snapshot_replay.parse_manifest(manifest), snap
    )
    assert before != after


# ---------------------------------------------------------------------------
# verify()'s named problems
# ---------------------------------------------------------------------------


def test_verify_names_a_missing_file(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    (snap / "b2_synth.json").unlink()

    problems = snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    assert [p.kind for p in problems] == ["snapshot_missing"]
    assert "b2_synth.json" in problems[0].detail


def test_verify_catches_tampered_bytes_via_the_meta_hash(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    target = snap / "b2_synth.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["ID"] = "tampered"
    target.write_text(json.dumps(envelope))

    kinds = [
        p.kind for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    ]
    assert "meta_hash_mismatch" in kinds
    assert "manifest_hash_mismatch" in kinds


def test_fixing_the_meta_hash_does_not_hide_the_tamper(tmp_path):
    """The manifest column is a second, independent copy of the claim."""
    manifest, snap = _three_row_manifest(tmp_path)
    target = snap / "b2_synth.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["ID"] = "tampered"
    envelope["_meta"]["content_hash"] = content_hash(envelope["pages"])
    target.write_text(json.dumps(envelope))

    kinds = [
        p.kind for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    ]
    assert kinds == ["manifest_hash_mismatch"]


def test_fixing_both_still_changes_the_manifest_hash(tmp_path):
    """
    THE LAST DEFENCE. With `_meta` and the manifest column both edited to agree
    with the tampered bytes, verify() passes - and the manifest hash, which is
    a function of the bytes, is a different number. There is no way to alter a
    snapshot and keep the quoted hash.
    """
    manifest, snap = _three_row_manifest(tmp_path)
    before = snapshot_replay.manifest_hash(
        snapshot_replay.parse_manifest(manifest), snap
    )

    target = snap / "b2_synth.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["ID"] = "tampered"
    new_digest = content_hash(envelope["pages"])
    envelope["_meta"]["content_hash"] = new_digest
    target.write_text(json.dumps(envelope))
    old_row_hash = json.loads((snap / "b1_real.json").read_text())  # untouched
    text = manifest.read_text()
    # Rewrite the manifest column too.
    old_col = [
        r.content_hash
        for r in snapshot_replay.parse_manifest(manifest)
        if r.snapshot_name == "b2_synth.json"
    ][0]
    manifest.write_text(text.replace(old_col, new_digest[:16]))

    rows = snapshot_replay.parse_manifest(manifest)
    assert snapshot_replay.verify(rows, snap) == []
    assert snapshot_replay.manifest_hash(rows, snap) != before
    assert old_row_hash  # the real snapshot was not touched


def test_verify_catches_a_snapshot_that_is_not_an_envelope(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    (snap / "b2_synth.json").write_text("")

    kinds = [
        p.kind for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    ]
    assert kinds == ["snapshot_unloadable"]


def test_verify_refuses_a_row_pointing_outside_the_snapshot_directory(tmp_path):
    manifest, snap = _three_row_manifest(tmp_path)
    manifest.write_text(manifest.read_text().replace("b2_synth.json", "../secrets.json"))

    kinds = [
        p.kind for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    ]
    assert kinds == ["snapshot_outside_corpus"]


def test_verify_refuses_an_archived_budget_failure(tmp_path):
    """
    A budget failure is never cached and never archived (finding H-1), so a row
    claiming one describes a response that cannot exist.
    """
    manifest, snap = _three_row_manifest(tmp_path)
    manifest.write_text(
        manifest.read_text().replace("| ok | b2_synth.json", "| budget_exhausted | b2_synth.json")
    )
    kinds = [
        p.kind for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
    ]
    assert kinds == ["impossible_state_archived"]


def test_manifest_hash_over_zero_rows_is_refused(tmp_path):
    with pytest.raises(snapshot_replay.ManifestError):
        snapshot_replay.manifest_hash([], tmp_path)


def test_parser_versions_reports_unknown_for_pre_v5_rows(tmp_path):
    path = tmp_path / "MANIFEST.md"
    path.write_text(PRE_V5_MANIFEST)
    rows = snapshot_replay.parse_manifest(path)
    assert snapshot_replay.parser_versions(rows) == ["unknown"]
