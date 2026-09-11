"""
Manager review, should-fix 2: `capture` and `yq-check` take --refresh.

A search row cached for up to 6h can carry a stale availability id (a 404 after
the trips call is spent) or a changed price (exit 5). --refresh sends the
search even when the disk cache holds an answer, says so in the call-count line,
and still writes the fresh answer to the cache. With --availability-id there is
no search to refresh, so the pair is a usage error.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from src import config
from src.seats_client import SeatsClient
from tests.test_metal_end_to_end import B4_ID
from tests.test_trips_tools import FLAG_KEY, Stub, capture_args, run, yq_args


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _searches(stub):
    return [c for c in stub.calls if c.endswith("/search")]


def _warm(tmp_path):
    code, _, stub = run(capture_args(tmp_path / "warm", "--yes"))
    assert code == 0 and len(_searches(stub)) == 1
    assert list(Path(config.CACHE_DIR).glob("*.json"))


@pytest.mark.parametrize("argv", [capture_args, yq_args], ids=["capture", "yq-check"])
def test_without_refresh_the_cached_search_is_used(tmp_path, argv):
    _warm(tmp_path)
    SeatsClient.reset_call_budget()
    code, out, stub = run(argv(tmp_path, "--yes"))
    assert _searches(stub) == []
    assert "search: served from the disk cache" in out


@pytest.mark.parametrize("argv", [capture_args, yq_args], ids=["capture", "yq-check"])
def test_refresh_sends_the_search_even_when_the_cache_holds_it(tmp_path, argv):
    _warm(tmp_path)
    SeatsClient.reset_call_budget()
    code, out, stub = run(argv(tmp_path, "--yes", "--refresh"))
    assert code == 0, out
    assert len(_searches(stub)) == 1
    assert "search: fetched" in out
    assert "1 search (--refresh: never served from the disk cache) + 1 trips" in out


def test_refresh_with_an_availability_id_is_a_usage_error(tmp_path):
    argv = ["capture", "--availability-id", B4_ID, "--refresh", "--out-dir",
            str(tmp_path / "real"), "--api-key", FLAG_KEY]
    code, out, stub = run(argv)
    assert code == 1
    assert "--refresh bypasses the SEARCH cache" in out
    assert stub.calls == []
