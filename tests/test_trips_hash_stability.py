"""
The manifest hash of a manifest with NO trips rows is pinned, byte for byte.

Adding the operating-airline lookup appends `trips|...` lines to the hash, but
only when a replay actually has trips rows. A quoted `mh_...` over a search-only
manifest must keep meaning the same bytes after this feature lands, so the
literal below was computed on the code BEFORE any trips change and is asserted
unchanged afterwards.

The manifest is built by the real writer (`ResponseCache.put`) with a fixed
clock, so the snapshot names and the manifest rows are the same on every run.
"""
from datetime import datetime, timezone

from src import snapshot_replay
from src.response_cache import ResponseCache

FIXED_NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

# Two legs, two tiny synthetic pages. The content is irrelevant; the point is
# that it is fixed.
PAGES = {
    "B1": [{"data": [{"ID": "synthAAAAAAAAAAAAAAAAAAAAAA1", "Date": "2027-01-15"}]}],
    "B2": [{"data": []}],
}
REQUESTS = {
    "B1": ("SFO", "MAD", "2027-01-15"),
    "B2": ("MAD", "AMS", "2027-01-19"),
}

# Computed on master code (a17497d + the plan commit) before any trips change.
PINNED_BARE = "mh_775a13936a4e27e0"
PINNED_WITH_ITINERARY = "mh_bf36633363c6772b"


def _build(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=tmp_path / "snaps")
    for leg, (origin, destination, day) in REQUESTS.items():
        request = {
            "origin_airport": origin,
            "destination_airport": destination,
            "start_date": day,
            "end_date": day,
        }
        cache.put(
            f"key-{leg}",
            request,
            PAGES[leg],
            meta={
                "endpoint": "search",
                "http_status": 200,
                "leg_id": leg,
                "trip_id": "trip_b",
                "rows_seen": len(PAGES[leg][0]["data"]),
                "incomplete": False,
                "incomplete_reason": "",
            },
            now=FIXED_NOW,
        )
    return cache


def test_a_search_only_manifest_hashes_to_the_pinned_value(tmp_path):
    cache = _build(tmp_path)
    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    selection = snapshot_replay.select_replay_set(rows, "trip_b")
    assert not selection.problems
    assert not snapshot_replay.verify(selection.selected, cache.snapshot_dir)
    assert (
        snapshot_replay.manifest_hash(selection.selected, cache.snapshot_dir)
        == PINNED_BARE
    )


def test_the_itinerary_bound_hash_is_pinned_too(tmp_path):
    cache = _build(tmp_path)
    rows = snapshot_replay.parse_manifest(cache.manifest_path)
    selection = snapshot_replay.select_replay_set(rows, "trip_b")
    itinerary = [
        snapshot_replay.LegQuery(leg, o, d, day) for leg, (o, d, day) in REQUESTS.items()
    ]
    assert (
        snapshot_replay.manifest_hash(
            selection.selected, cache.snapshot_dir, itinerary=itinerary, trip_id="trip_b"
        )
        == PINNED_WITH_ITINERARY
    )


def test_no_trips_directory_is_written_by_a_search_only_run(tmp_path):
    cache = _build(tmp_path)
    assert not (cache.snapshot_dir / "trips_endpoint").exists()
    assert not (cache.cache_dir / "trips").exists()
