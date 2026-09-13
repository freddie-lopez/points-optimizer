"""
Fix round 1, Tester findings 17, 18 and 19: `capture` keeps its promise.

  * 17: "at most 2 calls" holds when the search paginates. The search is capped
    at one page on the tool's own client, and a search that says there is more
    is refused before the trips call.
  * 18: the capture's filename is sanitized like trips snapshot names, so an
    airport code with "/" in it can neither add a directory nor crash the write.
  * 19: a closed stdin (EOFError) or Ctrl-C at the prompt is a clean refusal:
    exit 1, no call.

Transport stubbed; every file goes to tmp_path.
"""
import copy
import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src import trips_tools
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row
from tests.test_trips_tools import FLAG_KEY, capture_args


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


class PagedStub:
    """Search answers page by page from `pages`; trips with `trips`."""

    def __init__(self, pages=None, trips=None):
        self.pages = pages if pages is not None else [{"data": [vs_row()]}]
        self.trips = trips if trips is not None else tp.payload([tp.vs_direct()])
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/search"):
            n = sum(1 for c in self.calls if c.endswith("/search"))
            body = copy.deepcopy(self.pages[min(n, len(self.pages)) - 1])
        else:
            body = self.trips
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = body
        r.text = json.dumps(body)
        return r


def run(argv, stub, read=lambda _: "y"):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=stub):
        code = trips_tools.main(argv, read=read, console=Console(file=buf, width=190))
    return code, " ".join(buf.getvalue().split())


# -- 17 -----------------------------------------------------------------------


def test_a_paginating_search_is_refused_within_the_promised_calls(tmp_path):
    pages = [{"data": [], "hasMore": True, "cursor": f"c{i}"} for i in range(4)] + [
        {"data": [vs_row()]}
    ]
    stub = PagedStub(pages=pages)
    code, out = run(capture_args(tmp_path), stub)
    assert "at most 2 Seats.aero API call(s)" in out
    assert len(stub.calls) <= 2
    assert [c for c in stub.calls if "/trips/" in c] == []
    assert code == 1
    assert "the search is INCOMPLETE" in out
    assert not (tmp_path / "real").exists()


def test_a_first_page_that_holds_the_row_but_says_there_is_more_is_refused(tmp_path):
    stub = PagedStub(pages=[{"data": [vs_row()], "hasMore": True, "cursor": "c1"}])
    code, out = run(capture_args(tmp_path), stub)
    assert code == 1
    assert len(stub.calls) == 1
    assert "the search is INCOMPLETE" in out


def test_the_page_cap_is_the_tools_own_and_not_the_class_default(tmp_path):
    before = SeatsClient.MAX_PAGES
    stub = PagedStub()
    code, _ = run(capture_args(tmp_path, "--yes"), stub)
    assert code == 0
    assert len(stub.calls) == 2
    assert SeatsClient.MAX_PAGES == before


# -- 18 -----------------------------------------------------------------------


def _only_in(real):
    files = list(real.rglob("*"))
    assert files
    for p in files:
        assert p.parent == real, p
    return [p.name for p in files]


def test_an_inferred_route_with_a_slash_is_sanitized_not_a_crash(tmp_path):
    trip = tp.trip(
        [tp.segment("VS19", "LH/R", "SFO", 1, AvailabilityID=B4_ID)], availability_id=B4_ID
    )
    argv = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", FLAG_KEY, "--yes"]
    code, out = run(argv, PagedStub(trips=tp.payload([trip])))
    assert code in (0, 5), out
    names = _only_in(tmp_path / "real")
    assert any("LH-RSFO" in n for n in names), names


def test_a_row_route_with_path_characters_is_sanitized(tmp_path):
    row = vs_row()
    row["Route"] = dict(row["Route"], OriginAirport="../LHR")
    code, out = run(capture_args(tmp_path, "--yes"), PagedStub(pages=[{"data": [row]}]))
    assert code in (0, 5), out
    names = _only_in(tmp_path / "real")
    assert any("..-LHRSFO" in n for n in names), names


# -- 19 -----------------------------------------------------------------------


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_no_answer_at_the_prompt_is_a_clean_refusal(tmp_path, exc):
    def closed(prompt):
        raise exc

    stub = PagedStub()
    code, out = run(capture_args(tmp_path), stub, read=closed)
    assert code == 1
    assert stub.calls == []
    assert "no answer at the prompt" in out
    assert not (tmp_path / "real").exists()
