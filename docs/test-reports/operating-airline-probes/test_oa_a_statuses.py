"""
A. Every way the lookup can end, pushed through the REAL pipeline (SeatsClient ->
metal pass -> award_to_candidate -> evaluate_trip -> formatter), and read off
the rendered text: is there a line, does it say NOT KNOWN / NOT LOOKED UP / NOT
RECORDED, does it name the domain, is it counted in the trip block, and does it
avoid every phrase that would turn "could not find out" into a finding?
"""
import re

import pytest
import requests
from unittest.mock import MagicMock

from conftest import (  # noqa: F401
    B4_VS, Stub, aid_for, evaluate, flat, row, vs_b4_rows, vs_itinerary,
)
from src.models import MetalStatus
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp

FORBIDDEN = ("no trips", "no flights", "not available", "no itineraries")
# "operated by <carrier>" as a claim. The plan's own codeshare clause ("A codeshare
# operated by another airline ... cannot be detected") is not a claim.
OPERATED_BY_CLAIM = re.compile(r"operated by [A-Z0-9]{2}\b")
LABEL = "[trips parser UNVERIFIED against a real Seats.aero response - built from the published schema only]"


def _raise(exc):
    def f(url, kw):
        raise exc
    return f


def _bad_json(url, kw):
    r = MagicMock()
    r.status_code = 200
    r.json.side_effect = ValueError("Expecting value: line 1 column 1")
    r.text = "<html>oops</html>"
    return r


def seg(flight, o="LHR", d="SFO", order=1, **kw):
    return tp.segment(flight, o, d, order, AvailabilityID=B4_VS, **kw)


def one(trip_or_list):
    trips = trip_or_list if isinstance(trip_or_list, list) else [trip_or_list]
    return tp.payload(trips)


# (name, rows_for kwargs, trips spec for B4, expected status, expected reason)
CASES = [
    ("known", {}, one(vs_itinerary(B4_VS)), MetalStatus.KNOWN, ""),
    ("ambiguous", {}, one([vs_itinerary(B4_VS, "VS19"), vs_itinerary(B4_VS, "DL41", trip_id="t2")]),
     MetalStatus.AMBIGUOUS, ""),
    ("404", {}, (404, None), MetalStatus.UNKNOWN, "HTTP_404"),
    ("429", {}, (429, None), MetalStatus.UNKNOWN, "HTTP_429"),
    ("500", {}, (500, None), MetalStatus.UNKNOWN, "HTTP_ERROR"),
    ("timeout", {}, _raise(requests.Timeout("read timed out")), MetalStatus.UNKNOWN, "TIMEOUT"),
    ("conn", {}, _raise(requests.ConnectionError("reset")), MetalStatus.UNKNOWN, "TRANSPORT_ERROR"),
    ("json", {}, _bad_json, MetalStatus.UNKNOWN, "JSON_ERROR"),
    ("shape-list", {}, [], MetalStatus.UNKNOWN, "SHAPE_ERROR"),
    ("shape-nodata", {}, {"booking_links": []}, MetalStatus.UNKNOWN, "SHAPE_ERROR"),
    ("empty", {}, tp.payload([]), MetalStatus.UNKNOWN, "EMPTY_DATA"),
    ("incomplete", {}, tp.payload([vs_itinerary(B4_VS)], hasMore=True), MetalStatus.UNKNOWN, "INCOMPLETE"),
    ("unreadable", {}, one(vs_itinerary(B4_VS, ID=None)), MetalStatus.UNKNOWN, "TRIP_UNREADABLE"),
    ("cabin", {}, one(vs_itinerary(B4_VS, cabin="Upper Class")), MetalStatus.UNKNOWN, "CABIN_UNMAPPED"),
    ("icao", {}, one(tp.trip([seg("VIR19")], availability_id=B4_VS, carriers=None, flight_numbers=None,
                             cost=60000)), MetalStatus.UNKNOWN, "FLIGHT_NUMBER_UNPARSEABLE"),
    ("chain", {}, one(tp.trip([seg("VS19", "LHR", "JFK")], availability_id=B4_VS, cost=60000)),
     MetalStatus.UNKNOWN, "TRIP_INCONSISTENT"),
    ("idmismatch", {}, one(vs_itinerary(B4_VS, availability_id="SomeOtherId0000000000000000")),
     MetalStatus.UNKNOWN, "AVAILABILITY_ID_MISMATCH"),
    ("nomatch", {}, one(vs_itinerary(B4_VS, cost=70000)), MetalStatus.UNKNOWN, "NO_MATCH"),
    ("mixed", {}, one(vs_itinerary(B4_VS, mixed=20)), MetalStatus.UNKNOWN, "MIXED_CABIN_ONLY"),
    ("outside", {"airlines": "VS"}, one(vs_itinerary(B4_VS, "DL41")), MetalStatus.UNKNOWN,
     "CARRIER_NOT_IN_ROW_LIST"),
    ("rowempty", {"airlines": ""}, one(vs_itinerary(B4_VS)), MetalStatus.UNKNOWN, "ROW_CARRIERS_ABSENT"),
    ("noid", {"rid": ""}, one(vs_itinerary(B4_VS)), MetalStatus.NOT_LOOKED_UP, "NO_AVAILABILITY_ID"),
    ("badid", {"rid": "../../etc/passwd"}, one(vs_itinerary(B4_VS)), MetalStatus.NOT_LOOKED_UP,
     "AVAILABILITY_ID_INVALID"),
]


def _run(case_kw, spec, trips_mode="auto", **kw):
    rid = case_kw.get("rid", B4_VS)
    rows_kw = {k: v for k, v in case_kw.items() if k != "rid"}
    stub = Stub(rows_for=vs_b4_rows(rid=rid, **rows_kw), trips={B4_VS: spec})
    results, totals, text, opts, fixture = evaluate(stub, trips_mode=trips_mode, **kw)
    return results["B4"], totals, text, stub, opts


def _b4_lines(text):
    return [l for l in flat(text).split("operating airline:")[1:]]


@pytest.mark.parametrize("name,rows_kw,spec,status,reason", CASES, ids=[c[0] for c in CASES])
def test_every_ending_is_a_named_status_on_the_chosen_award(name, rows_kw, spec, status, reason):
    b4, totals, text, stub, opts = _run(rows_kw, spec)
    metal = b4.best_points.metal
    assert metal is not None, "the chosen award's lookup disappeared"
    assert metal.status is status, (metal.status, metal.reason_code, metal.detail)
    assert metal.reason_code == reason
    line = metal.render()
    assert line in flat(text) or " ".join(line.split()) in flat(text), "the status line is not printed"
    low = line.lower()
    for phrase in FORBIDDEN:
        assert phrase not in low, (phrase, line)
    assert not OPERATED_BY_CLAIM.search(line), line
    if status is not MetalStatus.KNOWN:
        assert re.match(r"operating airline: NOT (KNOWN|LOOKED UP|RECORDED)", line), line
        assert "possible carriers are" in line or "not bounded" in line, line


@pytest.mark.parametrize("name,rows_kw,spec,status,reason", CASES, ids=[c[0] for c in CASES])
def test_every_non_known_chosen_award_is_counted_in_the_trip_block(name, rows_kw, spec, status, reason):
    b4, totals, text, stub, opts = _run(rows_kw, spec)
    if status is MetalStatus.KNOWN:
        assert "B4" not in totals["legs_metal_unknown_ids"]
        assert "B4" not in totals["legs_metal_lookup_missing_ids"]
        return
    counted = "B4" in totals["legs_metal_unknown_ids"] or "B4" in totals["legs_metal_lookup_missing_ids"]
    assert counted, (status, reason, totals["legs_metal_unknown_ids"], totals["legs_metal_lookup_missing_ids"])
    assert "operating airline NOT" in flat(text)


PARSE_DERIVED = {"SHAPE_ERROR", "EMPTY_DATA", "INCOMPLETE", "TRIP_UNREADABLE", "CABIN_UNMAPPED",
                 "FLIGHT_NUMBER_UNPARSEABLE", "TRIP_INCONSISTENT", "AVAILABILITY_ID_MISMATCH",
                 "NO_MATCH", "MIXED_CABIN_ONLY", "CARRIER_NOT_IN_ROW_LIST", "ROW_CARRIERS_ABSENT"}


@pytest.mark.parametrize("name,rows_kw,spec,status,reason", CASES, ids=[c[0] for c in CASES])
def test_every_parse_derived_line_carries_the_unverified_label(name, rows_kw, spec, status, reason):
    b4, totals, text, stub, opts = _run(rows_kw, spec)
    line = b4.best_points.metal.render()
    if status in (MetalStatus.KNOWN, MetalStatus.AMBIGUOUS) or reason in PARSE_DERIVED:
        assert LABEL in line
    else:
        assert LABEL not in line, "the parser label rides on a line no parse produced"


def test_a_shape_error_line_says_what_shape_it_saw():
    b4, *_ = _run({}, [])
    assert "list" in b4.best_points.metal.detail


def test_the_banner_counts_add_up_and_the_label_is_printed():
    from io import StringIO
    from rich.console import Console
    from src.formatter import print_live_banner

    b4, totals, text, stub, opts = _run({}, (404, None))
    buf = StringIO()
    print_live_banner([], opts, None, Console(file=buf, width=250))
    out = flat(buf.getvalue())
    r = opts.metal_report
    assert sum(r.by_status.values()) == r.candidates
    assert LABEL in out
    assert "1 request(s) sent" in out


def test_transport_without_trips_is_named_not_silent():
    client = SeatsClient(api_key="test_key_probe_nt")
    client.trips_raw = None  # a stub transport that has no trips lookup
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, totals, text, opts, fixture = evaluate(stub, client=client)
    metal = results["B4"].best_points.metal
    assert metal.status is MetalStatus.NOT_LOOKED_UP and metal.reason_code == "TRANSPORT_HAS_NO_TRIPS"
    assert "B4" in totals["legs_metal_lookup_missing_ids"]
    assert stub.trips_calls == []
