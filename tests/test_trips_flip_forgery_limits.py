"""
Re-test 2, R2-5: the cheap closures on the label flip, and nothing more.

`schema_verification_problems` now also refuses:
  * the committed synthetic page under any wrapper, any itinerary copied from
    it, and its placeholder availability id;
  * a raw body that differs from the page in VALUE TYPES (60000.0, true) - it is
    compared as canonical JSON, so whitespace, CRLF and key order still pass;
  * a capture missing the _meta the tool always writes (request path and params,
    http_status 200, trips_parser_version, a past captured_at);
  * a capture whose recorded row has no award the response matches.

A deliberately hand-built file can still pass: nothing is signed. The README and
the fixtures READMEs say so.
"""
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.response_cache import content_hash
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_trips_tools import Stub, capture_args

ROOT = Path(__file__).parent.parent
SYNTHETIC = ROOT / "tests/fixtures/seats_aero/trips_endpoint/synthetic/openapi_example.json"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _capture(tmp_path, stub=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=stub or Stub()):
        code = trips_tools.main(
            capture_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=190)
        )
    real = tmp_path / "real"
    return code, next(real.glob("*.json")), real


def _problems(path, real):
    return " | ".join(seats_trips.schema_verification_problems(path.name, real))


def _edit(path, fn):
    envelope = json.loads(path.read_text())
    fn(envelope)
    envelope["_meta"]["content_hash"] = content_hash(envelope["pages"])
    path.write_text(json.dumps(envelope, indent=2))
    path.with_suffix(".raw.txt").write_text(json.dumps(envelope["pages"][0]))


def test_a_genuine_capture_still_flips(tmp_path):
    code, path, real = _capture(tmp_path)
    assert code == 0
    assert _problems(path, real) == ""


# -- the synthetic page --------------------------------------------------------


def _forge(tmp_path, page):
    aid = page["data"][0]["AvailabilityID"]
    segs = page["data"][0]["AvailabilitySegments"]
    o, d = segs[0]["OriginAirport"], segs[-1]["DestinationAirport"]
    real = tmp_path / "real"
    real.mkdir(exist_ok=True)
    env = {
        "_meta": {
            "captured_by": seats_trips.CAPTURED_BY, "synthetic": False, "key_redacted": True,
            "captured_at": "2026-09-10T10:00:00Z", "http_status": 200,
            "trips_parser_version": seats_trips.TRIPS_PARSER_VERSION,
            "request": {"path": f"/partnerapi/trips/{aid}",
                        "params": dict(seats_trips.TRIPS_REQUEST_PARAMS)},
            "availability_id": aid, "content_hash": content_hash([page]),
            "route": f"{o}->{d}", "route_inferred_from_itineraries": False,
            "availability_row": {"ID": aid, "Route": {"OriginAirport": o, "DestinationAirport": d,
                                                      "Source": page["data"][0]["Source"]}},
        },
        "pages": [page],
    }
    (real / "forged.json").write_text(json.dumps(env))
    (real / "forged.raw.txt").write_text(json.dumps(page))
    return real / "forged.json", real


def test_the_synthetic_page_under_the_full_wrapper_is_refused(tmp_path):
    page = json.loads(SYNTHETIC.read_text())["pages"][0]
    path, real = _forge(tmp_path, page)
    problems = _problems(path, real)
    assert "IS a committed synthetic/ page" in problems


def test_a_synthetic_itinerary_and_id_inside_another_page_are_refused(tmp_path):
    page = json.loads(SYNTHETIC.read_text())["pages"][0]
    page = dict(page, data=page["data"] + [tp.vs_direct()])
    path, real = _forge(tmp_path, page)
    problems = _problems(path, real)
    assert "copied from a committed synthetic/ page" in problems
    assert "synthetic/ placeholder id" in problems


# -- the raw body, compared with types -----------------------------------------


@pytest.mark.parametrize("value", [60000.0, True, "60000"])
def test_a_raw_body_whose_types_differ_from_the_page_is_refused(tmp_path, value):
    code, path, real = _capture(tmp_path)
    raw = json.loads(path.with_suffix(".raw.txt").read_text())
    raw["data"][0]["MileageCost"] = value
    path.with_suffix(".raw.txt").write_text(json.dumps(raw))
    assert "a different body" in _problems(path, real)


@pytest.mark.parametrize("style", ["compact", "sorted", "crlf"])
def test_harmless_formatting_of_the_raw_body_still_flips(tmp_path, style):
    code, path, real = _capture(tmp_path)
    page = json.loads(path.with_suffix(".raw.txt").read_text())
    text = {
        "compact": json.dumps(page, separators=(",", ":")),
        "sorted": json.dumps(page, sort_keys=True),
        "crlf": json.dumps(page, indent=2).replace("\n", "\r\n"),
    }[style]
    path.with_suffix(".raw.txt").write_bytes(text.encode())
    assert _problems(path, real) == ""


# -- the capture tool's own _meta ----------------------------------------------


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda m: m.pop("request"), "request is None"),
        (lambda m: m["request"].update(path="/partnerapi/trips/other"), "not the capture tool's"),
        (lambda m: m["request"]["params"].update(min_cabin_pct="0"), "not the capture tool's"),
        (lambda m: m.update(http_status=203), "http_status is 203"),
        (lambda m: m.update(http_status=True), "http_status is True"),
        (lambda m: m.pop("trips_parser_version"), "no trips_parser_version"),
        (lambda m: m.pop("captured_at"), "captured_at is ''"),
        (lambda m: m.update(captured_at="2999-01-01T00:00:00Z"), "is in the future"),
    ],
    ids=["no-request", "other-path", "other-params", "203", "bool-status",
         "no-parser-version", "no-captured-at", "future"],
)
def test_a_capture_missing_what_the_tool_writes_is_refused(tmp_path, mutate, needle):
    code, path, real = _capture(tmp_path)
    _edit(path, lambda env: mutate(env["_meta"]))
    assert needle in _problems(path, real)


# -- the row the capture was fetched for must be matched -----------------------


def test_a_capture_whose_row_no_itinerary_matches_is_refused(tmp_path):
    code, path, real = _capture(tmp_path, Stub(trips=tp.payload([tp.vs_direct(cost=70000)])))
    assert code == trips_tools.EXIT_DRIFT
    assert "no award in the recorded availability row is matched" in _problems(path, real)
