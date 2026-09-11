"""
Fix round 1, findings 8, 9 and 15.

- A request that fails outside the named errors still counts against the cap.
- An HTTP 429 on a SEARCH stops trips requests, like one on a lookup.
- After the cap is spent or a 429 is seen, an answer already in the disk cache
  is still read: nothing more is SENT, and a cache hit is free.
"""
from unittest.mock import MagicMock, patch

import requests

from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from tests.test_trips_flags import IDS, TRIP_B, Stub


def live(stub, cache=None, **kw):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode="auto", **kw)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    return {l.id: l.points_candidates[0].metal for l in fixture.legs if l.points_candidates}, opts


class Weird(Stub):
    """Every trips response blows up in .json() with a TypeError."""

    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        if "/trips/" in url:
            r.json.side_effect = TypeError("decoder exploded")
        return r


def test_an_unexpected_failure_after_sending_still_counts_against_the_cap():
    stub = Weird()
    metal, opts = live(stub, trips_cap=1)
    assert len(stub.trips_calls) == 1
    assert metal["B2"].reason_code == "UNEXPECTED_ERROR"
    assert metal["B3"].reason_code == metal["B4"].reason_code == "CAP_REACHED"


class Search429(Stub):
    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            self.calls.append(url)
            r = MagicMock()
            r.status_code = 429
            r.raise_for_status.side_effect = requests.HTTPError("429 Client Error")
            return r
        return super().__call__(url, **kwargs)


def test_a_429_on_a_search_stops_every_trips_request():
    stub = Search429()
    metal, opts = live(stub)
    assert stub.trips_calls == []
    for leg in ("B2", "B3"):
        assert metal[leg].reason_code == "RATE_LIMITED_EARLIER"
        assert "HTTP 429 on a search earlier in this run" in metal[leg].render()
        assert "rate-limited an earlier request in this run" in metal[leg].render()
    assert opts.metal_report.rate_limited


def _warm(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    live(Stub(), cache=cache)
    for p in (tmp_path / "c").glob("*.json"):  # forget the searches, keep trips/
        p.unlink()
    # forget B2's and B3's lookups too, so only B4's is on disk
    from src import response_cache, seats_trips

    for leg in ("B2", "B3"):
        key = response_cache.request_key(
            "trips", {"availability_id": IDS[leg], **seats_trips.TRIPS_REQUEST_PARAMS}
        )
        (tmp_path / "c" / "trips" / f"{key}.json").unlink()
    return cache


def test_a_cache_hit_after_the_cap_is_spent_is_still_read(tmp_path):
    cache = _warm(tmp_path)
    stub = Stub()
    metal, opts = live(stub, cache=cache, trips_cap=1)
    assert len(stub.trips_calls) == 1
    assert metal["B4"].status is MetalStatus.KNOWN
    assert metal["B4"].served_from_cache
    assert metal["B3"].reason_code == "CAP_REACHED"


def test_a_cache_hit_after_a_429_is_still_read_and_nothing_more_is_sent(tmp_path):
    cache = _warm(tmp_path)
    stub = Stub(trips_status={"B2": 429})
    metal, opts = live(stub, cache=cache)
    assert len(stub.trips_calls) == 1
    assert metal["B2"].reason_code == "HTTP_429"
    assert metal["B3"].reason_code == "RATE_LIMITED_EARLIER"
    assert metal["B4"].status is MetalStatus.KNOWN and metal["B4"].served_from_cache


def test_refresh_never_reads_the_cache_even_after_the_cap(tmp_path):
    cache = _warm(tmp_path)
    metal, _ = live(Stub(), cache=cache, trips_cap=1, refresh=True)
    assert metal["B4"].reason_code == "CAP_REACHED"
