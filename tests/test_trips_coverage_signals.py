"""
Fix round 1, findings 5, 7 and 6: a trips list is incomplete on every "there
is more" signal the search transport honours (read by the SAME function), on a
`count` larger than the list, and whenever the stored record says so.
"""
import json
from unittest.mock import patch

import pytest

from src import seats_client, seats_trips
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.seats_trips import AwardFacts, match_award, parse_trips_payload
from src.trip_loader import load_trip_fixture
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import TRIPS, Stub

FACTS = AwardFacts(tp.AVAIL_ID, "virginatlantic", "J", 60000, ("VS", "DL"), "LHR", "SFO")


def lookup(**extra):
    parsed = parse_trips_payload(tp.payload([tp.vs_direct()], **extra), tp.AVAIL_ID, ("LHR", "SFO"))
    return match_award(parsed, FACTS)


@pytest.mark.parametrize(
    "extra",
    [{"hasMore": 1}, {"hasMore": "1"}, {"has_more": "yes"}, {"hasMore": "TRUE"},
     {"skip": "100"}, {"skip": 25}, {"next_cursor": "x"}],
)
def test_every_signal_the_search_transport_honours_blocks_known(extra):
    assert lookup(**extra).reason_code == "INCOMPLETE"


@pytest.mark.parametrize("extra", [{"hasMore": 0}, {"hasMore": "no"}, {"skip": "0"}, {"count": 1}])
def test_signals_that_say_nothing_more_leave_it_whole(extra):
    assert lookup(**extra).status is MetalStatus.KNOWN


def test_a_count_larger_than_the_list_is_incomplete():
    got = lookup(count=3)
    assert got.reason_code == "INCOMPLETE"
    assert "count=3" in got.detail


def test_search_and_trips_read_the_signals_through_one_function(monkeypatch):
    calls = []
    real = seats_client.pagination_signals

    def spy(payload):
        calls.append(payload)
        return real(payload)

    monkeypatch.setattr(seats_client, "pagination_signals", spy)
    seats_trips.trips_coverage({"data": [], "hasMore": "yes"})
    SeatsClient._next_page_params({"data": [], "hasMore": "yes"}, None)
    assert len(calls) == 2


def test_a_stored_incomplete_flag_is_honoured_on_a_cache_hit(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)

    def once():
        SeatsClient.CACHE.clear()
        SeatsClient.CACHE_META.clear()
        SeatsClient.reset_call_budget()
        fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
        stub = Stub()
        opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode="auto")
        with patch("src.seats_client.requests.get", side_effect=stub):
            fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
        b4 = next(l for l in fixture.legs if l.id == "B4")
        return b4.points_candidates[0].metal, stub

    first, _ = once()
    assert first.status is MetalStatus.KNOWN
    [path] = list((tmp_path / "c" / "trips").glob("*.json"))
    env = json.loads(path.read_text())
    env["_meta"]["incomplete"] = True
    env["_meta"]["incomplete_reason"] = "the stored record says the list was cut short"
    path.write_text(json.dumps(env))
    second, stub = once()
    assert not [c for c in stub.calls if "/trips/" in c]
    assert second.served_from_cache
    assert second.reason_code == "INCOMPLETE"
    assert "cut short" in second.detail
