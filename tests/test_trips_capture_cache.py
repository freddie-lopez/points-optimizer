"""
Re-test 2, R2-3: a refused capture leaves no truncated search in the shared
runtime cache.

`capture` caps its search at one page (fix 9). A first page that says there is
more is refused - and the one-page entry that search just wrote is removed, so
the next trip run for that route and day fetches (and follows the pages)
instead of being served page one. A complete capture search is still cached.
"""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src import config
from src.models import DateRange
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from tests.test_metal_end_to_end import vs_row
from tests.test_trips_capture_guards import PagedStub, run
from tests.test_trips_tools import capture_args

DAY = date(2027, 1, 27)


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _entries():
    return sorted(Path(config.CACHE_DIR).glob("*.json"))


def _trip_search(stub):
    """What a trip leg's search for LHR-SFO on that day sees next."""
    cache = ResponseCache(cache_dir=config.CACHE_DIR, snapshot_dir=None)
    with patch("src.seats_client.requests.get", side_effect=stub):
        return SeatsClient(api_key="test_key_capture_cache").search_raw(
            "LHR", "SFO", DateRange(DAY, DAY), cache=cache
        )


def test_a_refused_capture_leaves_no_search_in_the_cache(tmp_path):
    first = {"data": [vs_row()], "hasMore": True, "cursor": "p2"}
    code, out = run(capture_args(tmp_path), PagedStub(pages=[first]))
    assert code == 1
    assert "the search is INCOMPLETE" in out
    assert "not kept in the cache" in out
    assert _entries() == []


def test_the_next_trip_search_fetches_and_follows_the_pages(tmp_path):
    first = {"data": [vs_row()], "hasMore": True, "cursor": "p2"}
    run(capture_args(tmp_path), PagedStub(pages=[first]))
    stub = PagedStub(pages=[first, {"data": [], "hasMore": False}])
    raw = _trip_search(stub)
    assert not raw.served_from_cache
    assert len(stub.calls) == 2
    assert not raw.incomplete


def test_a_complete_capture_search_is_still_cached(tmp_path):
    code, _ = run(capture_args(tmp_path, "--yes"), PagedStub())
    assert code == 0
    assert len(_entries()) == 1
    stub = PagedStub()
    raw = _trip_search(stub)
    assert raw.served_from_cache
    assert stub.calls == []


def test_an_incomplete_entry_the_capture_did_not_write_is_left_alone(tmp_path):
    first = {"data": [vs_row()], "hasMore": True, "cursor": "p2"}
    # A trip run's own truncated answer, already cached (MAX_PAGES 1 stands in
    # for the 25-page safety cap).
    cache = ResponseCache(cache_dir=config.CACHE_DIR, snapshot_dir=None)
    client = SeatsClient(api_key="test_key_capture_cache")
    client.MAX_PAGES = 1
    with patch("src.seats_client.requests.get", side_effect=PagedStub(pages=[first])):
        client.search_raw("LHR", "SFO", DateRange(DAY, DAY), cache=cache)
    before = _entries()
    assert len(before) == 1
    code, out = run(capture_args(tmp_path, "--yes"), PagedStub(pages=[first]))
    assert code == 1
    assert "served from the disk cache, where an earlier run left it" in out
    assert _entries() == before
