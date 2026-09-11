"""
Trips responses in the disk cache, the snapshot corpus and a manifest of their own.

Everything lives under tmp. The layout is the one-way door the plan names:
`cache/trips/<key>.json`, `<snapshots>/trips_endpoint/<leg>_trips_<id>_<stamp>.json`
and `<snapshots>/trips_endpoint/MANIFEST.md`, with the search manifest's columns.
"""
import dataclasses
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import seats_trips
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient, TripsLookupError
from src.snapshot_replay import parse_manifest, verify
from src.trip_loader import load_trip_fixture
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, Stub

ROOT = Path(__file__).parent.parent
FLAG_KEY = "flag_supplied_key_0123456789abcdef"


@pytest.fixture(autouse=True)
def fresh_client_state():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=tmp_path / "snaps")


def _live(cache, stub=None, **kw):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    stub = stub or Stub()
    fixture = load_trip_fixture(ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json")
    opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode="auto", **kw)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key=FLAG_KEY), opts)
    b4 = next(l for l in fixture.legs if l.id == "B4")
    return b4.points_candidates[0].metal, stub, opts


def _trips_calls(stub):
    return [c for c in stub.calls if "/trips/" in c]


def _response(payload=None, status=200, exc=None):
    r = MagicMock()
    r.status_code = status
    if exc is not None:
        r.json.side_effect = exc
    else:
        r.json.return_value = payload
    r.text = json.dumps(payload) if exc is None else "<html>"
    return r


def test_the_first_lookup_writes_cache_snapshot_and_an_annotated_manifest_row(cache):
    metal, stub, _ = _live(cache)
    assert metal.status is MetalStatus.KNOWN
    trips_cache = list((cache.cache_dir / "trips").glob("*.json"))
    assert len(trips_cache) == 1
    snaps = list((cache.snapshot_dir / "trips_endpoint").glob("*.json"))
    assert len(snaps) == 1
    assert snaps[0].name.startswith(f"B4_trips_{B4_ID}_")
    manifest = cache.snapshot_dir / "trips_endpoint" / "MANIFEST.md"
    rows = parse_manifest(manifest)
    assert len(rows) == 1
    row = rows[0]
    assert row.leg_id == "B4"
    assert row.route == f"trips:{B4_ID}"
    assert row.dates == "2027-01-27"
    assert row.rows_seen == "1"
    assert row.awards == "1"
    assert row.state == "trips_readable"
    assert row.parser_version == seats_trips.TRIPS_PARSER_VERSION
    assert row.trip_id == "trip_b_europe"
    assert verify(rows, manifest.parent) == []


def test_a_second_run_inside_the_ttl_makes_no_trips_call_and_answers_the_same(cache):
    first, stub1, _ = _live(cache)
    second, stub2, opts = _live(cache)
    assert len(_trips_calls(stub1)) == 1
    assert _trips_calls(stub2) == []
    assert second.served_from_cache is True
    assert second.fetched_at is not None
    strip = dict(served_from_cache=False, fetched_at=None)
    assert dataclasses.replace(first, **strip) == dataclasses.replace(second, **strip)
    assert "Served from the disk cache" in second.render()
    assert opts.metal_report.served_from_cache == 1
    assert opts.metal_report.requests_sent == 0


def test_the_trips_row_is_annotated_once_and_a_cache_hit_adds_no_row(cache):
    _live(cache)
    _live(cache)
    rows = parse_manifest(cache.snapshot_dir / "trips_endpoint" / "MANIFEST.md")
    assert len(rows) == 1


@pytest.mark.parametrize(
    "response",
    [
        _response(status=404),
        _response(status=429),
        _response(status=500),
        _response(exc=ValueError("not json")),
    ],
)
def test_an_http_or_json_failure_writes_nothing(cache, response):
    client = SeatsClient(api_key=FLAG_KEY)
    with patch("src.seats_client.requests.get", return_value=response):
        with pytest.raises(TripsLookupError):
            client.trips_raw(B4_ID, cache=cache, leg_id="B4", award_date="2027-01-27")
    assert not (cache.cache_dir / "trips").exists() or not list((cache.cache_dir / "trips").iterdir())
    assert not (cache.snapshot_dir / "trips_endpoint").exists()


@pytest.mark.parametrize("exc", [requests.Timeout("t"), requests.ConnectionError("c")])
def test_a_transport_failure_writes_nothing(cache, exc):
    client = SeatsClient(api_key=FLAG_KEY)
    with patch("src.seats_client.requests.get", side_effect=exc):
        with pytest.raises(TripsLookupError):
            client.trips_raw(B4_ID, cache=cache)
    assert not (cache.cache_dir / "trips").exists()
    assert not (cache.snapshot_dir / "trips_endpoint").exists()


def test_a_spent_budget_writes_nothing_and_sends_nothing(cache):
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP
    client = SeatsClient(api_key=FLAG_KEY)
    with patch("src.seats_client.requests.get") as get:
        with pytest.raises(TripsLookupError) as e:
            client.trips_raw(B4_ID, cache=cache)
    assert e.value.code == "BUDGET_EXHAUSTED"
    assert get.call_count == 0
    assert not (cache.cache_dir / "trips").exists()


def test_the_envelope_is_the_documented_one_and_holds_no_key(cache, tmp_path):
    _live(cache)
    snap = next((cache.snapshot_dir / "trips_endpoint").glob("*.json"))
    envelope = json.loads(snap.read_text())
    meta = envelope["_meta"]
    assert meta["endpoint"] == "trips"
    assert meta["parser_version"] == seats_trips.TRIPS_PARSER_VERSION
    assert meta["key_redacted"] is True
    assert meta["availability_id"] == B4_ID
    assert meta["award_date"] == "2027-01-27"
    assert meta["route"] == "LHR->SFO"
    assert meta["leg_id"] == "B4"
    assert meta["trip_id"] == "trip_b_europe"
    for k in ("incomplete", "incomplete_reason", "fetched_at"):
        assert k in meta
    assert meta["request"] == {
        "availability_id": B4_ID, "include_filtered": "false", "min_cabin_pct": "100"
    }
    assert meta["canonical_request"].startswith("v1|trips|")
    assert len(envelope["pages"]) == 1
    # The key was supplied by FLAG, so it is not in the environment: grep for it.
    for path in tmp_path.rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert FLAG_KEY not in text, path
            assert "Partner-Authorization" not in text, path


def test_a_payload_carrying_the_flag_key_is_not_archived(cache):
    client = SeatsClient(api_key=FLAG_KEY)
    body = tp.payload([tp.vs_direct()], note=f"echo {FLAG_KEY}")
    with patch("src.seats_client.requests.get", return_value=_response(body)):
        raw = client.trips_raw(B4_ID, cache=cache)
    assert raw.payload == body, "the response is still used"
    assert any("COULD NOT ARCHIVE the trips response" in w for w in cache.warnings)
    assert not list((cache.cache_dir / "trips").glob("*.json")) or all(
        FLAG_KEY not in p.read_text() for p in (cache.cache_dir / "trips").glob("*.json")
    )


def test_trips_files_are_invisible_to_the_search_corpus_globs(cache):
    _live(cache)
    assert all("trips" not in p.name for p in cache.snapshots())
    assert all("trips" not in p.name for p in cache.cache_dir.glob("*.json"))
    search_rows = parse_manifest(cache.snapshot_dir / "MANIFEST.md")
    assert all(not r.route.startswith("trips:") for r in search_rows)


def test_a_cached_wrong_shape_answer_reads_as_the_same_unknown_twice(cache):
    stub = Stub(trips_payloads={B4_ID: {"data": "no itineraries"}})
    first, _, _ = _live(cache, stub=stub)
    second, stub2, _ = _live(cache, stub=Stub(trips_payloads={B4_ID: {"data": "no itineraries"}}))
    assert _trips_calls(stub2) == []
    assert first.reason_code == second.reason_code == "SHAPE_ERROR"
    assert second.served_from_cache
    rows = parse_manifest(cache.snapshot_dir / "trips_endpoint" / "MANIFEST.md")
    assert rows[0].state == "trips_shape_error"
    assert rows[0].rows_seen == "n/a"


def test_a_truncated_answer_is_still_incomplete_on_the_second_read(cache):
    body = tp.payload([tp.vs_direct()], hasMore=True)
    first, _, _ = _live(cache, stub=Stub(trips_payloads={B4_ID: body}))
    second, _, _ = _live(cache, stub=Stub(trips_payloads={B4_ID: body}))
    assert first.reason_code == second.reason_code == "INCOMPLETE"
    assert second.served_from_cache


def test_a_cache_without_a_snapshot_dir_caches_but_archives_nothing(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "cache")
    metal, _, _ = _live(cache)
    assert metal.status is MetalStatus.KNOWN
    assert list((tmp_path / "cache" / "trips").glob("*.json"))
    assert not (tmp_path / "trips_endpoint").exists()


def test_for_trips_keeps_ttl_and_shares_warnings(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s", ttl_seconds=60)
    trips = cache.for_trips()
    assert trips.cache_dir == tmp_path / "c" / "trips"
    assert trips.snapshot_dir == tmp_path / "s" / "trips_endpoint"
    assert trips.ttl_seconds == 60
    assert trips.warnings is cache.warnings
    assert trips.for_trips() is trips


def test_search_envelopes_are_unchanged_by_the_endpoint_fields(cache):
    written = cache.put(
        "k", {"origin_airport": "SFO", "destination_airport": "MAD", "start_date": "2027-01-15",
              "end_date": "2027-01-15"},
        [{"data": []}], meta={"endpoint": "search", "incomplete": False, "incomplete_reason": ""},
    )
    for key in ("endpoint", "availability_id", "award_date", "route"):
        assert key not in written.meta
