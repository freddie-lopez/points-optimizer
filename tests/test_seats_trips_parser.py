"""
The trips parser and matcher (`src/seats_trips.py`), against payloads shaped
like the published schema. No real response exists yet; see
tests/fixtures/seats_aero/trips_endpoint/README.md.

What these tests guard is the house failure: an itinerary list the tool could
not read, or could not match, must come out as a NAMED unknown - never as a
carrier, and never as "no trips".
"""
import json
from pathlib import Path

import pytest

from src import seats_trips
from src.models import MetalStatus
from src.seats_trips import AwardFacts, match_award, parse_flight_number, parse_trips_payload
from tests import _trips_payloads as tp

ROOT = Path(__file__).parent.parent
OPENAPI = ROOT / "tests" / "fixtures" / "seats_aero" / "trips_endpoint" / "synthetic" / "openapi_example.json"

VS_FACTS = AwardFacts(
    availability_id=tp.AVAIL_ID,
    source_code="virginatlantic",
    cabin="J",
    cost=60000,
    row_carriers=("VS", "DL"),
    origin="LHR",
    destination="SFO",
)


def _lookup(payload, facts=VS_FACTS):
    parsed = parse_trips_payload(payload, facts.availability_id, (facts.origin, facts.destination))
    return parsed, match_award(parsed, facts)


def _code(payload, facts=VS_FACTS):
    return _lookup(payload, facts)[1].reason_code


# ---------------------------------------------------------------------------
# The published example
# ---------------------------------------------------------------------------


def test_the_openapi_example_is_known_cm_tk():
    envelope = json.loads(OPENAPI.read_text())
    assert envelope["_meta"]["synthetic"] is True
    payload = envelope["pages"][0]
    facts = AwardFacts(
        availability_id=envelope["_meta"]["availability_id"],
        source_code="lifemiles",
        cabin="J",
        cost=63000,
        row_carriers=("CM", "TK"),
        origin="PTY",
        destination="IST",
    )
    parsed, lookup = _lookup(payload, facts)
    assert parsed.required_drift == []
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.carriers == ("CM", "TK")
    assert lookup.flight_numbers == ("CM326", "TK800")
    assert lookup.matched_trips == 1
    text = lookup.render()
    assert text.startswith("operating airline: CM, TK by flight number (CM326, TK800)")
    assert "UNVERIFIED" in text
    assert any("CM326 PTY" in f for f in lookup.flights)


def test_a_single_vs_itinerary_is_known_vs_with_the_codeshare_clause():
    _, lookup = _lookup(tp.payload([tp.vs_direct()]))
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.carriers == ("VS",)
    text = lookup.render()
    assert "operating airline: VS by flight number (VS19)" in text
    assert "A codeshare operated by another airline in this award's list (VS, DL) cannot be detected" in text
    assert lookup.provenance == "seats_aero_trips"


# ---------------------------------------------------------------------------
# D13: every parse-derived row of the table produces its code
# ---------------------------------------------------------------------------


def _one(**kw):
    return tp.payload([tp.vs_direct(**kw)])


def _seg_payload(**seg_kw):
    return tp.payload([tp.trip([tp.segment("VS19", "LHR", "SFO", 1, **seg_kw)])])


D13 = {
    "SHAPE_ERROR: a list": ([], "SHAPE_ERROR"),
    "SHAPE_ERROR: a string": ("no results", "SHAPE_ERROR"),
    "SHAPE_ERROR: null": (None, "SHAPE_ERROR"),
    "SHAPE_ERROR: no data key": ({"results": []}, "SHAPE_ERROR"),
    "SHAPE_ERROR: data is an object": ({"data": {"a": 1}}, "SHAPE_ERROR"),
    "SHAPE_ERROR: data is null": ({"data": None}, "SHAPE_ERROR"),
    "EMPTY_DATA": (tp.payload([]), "EMPTY_DATA"),
    "INCOMPLETE: hasMore": (tp.payload([tp.vs_direct()], hasMore=True), "INCOMPLETE"),
    "INCOMPLETE: hasMore on an empty list": (tp.payload([], hasMore=True), "INCOMPLETE"),
    "INCOMPLETE: cursor": (tp.payload([tp.vs_direct()], cursor="abc"), "INCOMPLETE"),
    "INCOMPLETE: skip": (tp.payload([tp.vs_direct()], skip=25), "INCOMPLETE"),
    "TRIP_UNREADABLE: not an object": (tp.payload(["x"]), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: no ID": (_one(ID=None), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: no AvailabilityID": (_one(availability_id=""), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: no Source": (_one(source=""), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: no Cabin": (_one(Cabin=None), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MileageCost bool": (_one(cost=True), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MileageCost float": (_one(cost=60000.0), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MileageCost None": (_one(cost=None), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MileageCost 0": (_one(cost=0), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MileageCost words": (_one(cost="sixty"), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: empty segments": (
        tp.payload([tp.trip([], carriers=None, flight_numbers=None)]), "TRIP_UNREADABLE"
    ),
    "TRIP_UNREADABLE: segments missing": (_one(AvailabilitySegments=None), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: segment not an object": (
        tp.payload([tp.trip(["seg"], carriers=None, flight_numbers=None)]), "TRIP_UNREADABLE"
    ),
    "TRIP_UNREADABLE: Order not an int": (_seg_payload(Order="1"), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: no OriginAirport": (_seg_payload(OriginAirport=""), "TRIP_UNREADABLE"),
    "TRIP_UNREADABLE: MixedCabinPct 150": (_one(mixed=150), "TRIP_UNREADABLE"),
    "CABIN_UNMAPPED": (_one(cabin="premium_economy"), "CABIN_UNMAPPED"),
    "FLIGHT_NUMBER_UNPARSEABLE: ICAO": (_one(flight="BAW123"), "FLIGHT_NUMBER_UNPARSEABLE"),
    "FLIGHT_NUMBER_UNPARSEABLE: digits": (_one(flight="12345"), "FLIGHT_NUMBER_UNPARSEABLE"),
    "TRIP_INCONSISTENT: FlightNumbers disagree": (
        _one(flight_numbers="VS21"), "TRIP_INCONSISTENT"
    ),
    "TRIP_INCONSISTENT: Carriers disagree": (_one(carriers="DL"), "TRIP_INCONSISTENT"),
    "TRIP_INCONSISTENT: duplicate Order": (
        tp.payload([tp.trip([
            tp.segment("VS19", "LHR", "JFK", 1), tp.segment("VS21", "JFK", "SFO", 1)
        ])]),
        "TRIP_INCONSISTENT",
    ),
    "TRIP_INCONSISTENT: chain does not connect": (
        tp.payload([tp.trip([
            tp.segment("VS19", "LHR", "JFK", 1), tp.segment("VS21", "BOS", "SFO", 2)
        ])]),
        "TRIP_INCONSISTENT",
    ),
    "TRIP_INCONSISTENT: does not reach the destination": (
        tp.payload([tp.trip([tp.segment("VS19", "LHR", "LAX", 1)])]), "TRIP_INCONSISTENT"
    ),
    "AVAILABILITY_ID_MISMATCH": (
        _one(availability_id="SomeOtherId000000000000001"), "AVAILABILITY_ID_MISMATCH"
    ),
    "NO_MATCH: only another price": (_one(cost=70000), "NO_MATCH"),
    "NO_MATCH: only another cabin": (_one(cabin="economy"), "NO_MATCH"),
    "NO_MATCH: only another program": (_one(source="delta"), "NO_MATCH"),
    "MIXED_CABIN_ONLY": (_one(mixed=20), "MIXED_CABIN_ONLY"),
    "CARRIER_NOT_IN_ROW_LIST": (_one(flight="AF83"), "CARRIER_NOT_IN_ROW_LIST"),
}


@pytest.mark.parametrize("name", sorted(D13))
def test_every_row_of_the_table_produces_its_code(name):
    payload, code = D13[name]
    _, lookup = _lookup(payload)
    assert lookup.status is MetalStatus.UNKNOWN, (name, lookup)
    assert lookup.reason_code == code, (name, lookup.reason_code, lookup.detail)
    text = lookup.render().lower()
    for word in ("no trips", "no flights", "operated by", "not available"):
        assert word not in text, text
    assert "possible carriers are vs, dl" in text


def test_row_carriers_absent_is_unknown_with_an_unbounded_domain():
    facts = AwardFacts(**{**VS_FACTS.__dict__, "row_carriers": ()})
    _, lookup = _lookup(tp.payload([tp.vs_direct()]), facts)
    assert lookup.reason_code == "ROW_CARRIERS_ABSENT"
    assert lookup.domain_unbounded
    assert "not bounded" in lookup.render()


def test_an_error_inside_the_matcher_is_unexpected_error_not_a_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(seats_trips, "_could_be_the_award", boom)
    parsed = parse_trips_payload(tp.payload([tp.vs_direct(cost=None)]), tp.AVAIL_ID, ("LHR", "SFO"))
    lookup = match_award(parsed, VS_FACTS)
    assert lookup.reason_code == "UNEXPECTED_ERROR"
    assert "RuntimeError" in lookup.detail


@pytest.mark.parametrize(
    "garbage",
    [0, 1.5, True, "x", [1, 2], {"data": [None, 3, "x", []]}, {"data": [{"AvailabilitySegments": [None]}]},
     {"data": [{"ID": 5, "Cabin": {}, "MileageCost": [], "AvailabilitySegments": [{"Order": None}]}]}],
)
def test_the_parser_never_raises(garbage):
    parsed, lookup = _lookup(garbage)
    assert lookup.status is MetalStatus.UNKNOWN


# ---------------------------------------------------------------------------
# D19: flight numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,carrier,number",
    [
        ("9W123", "9W", "9W123"),
        ("B6 1", "B6", "B61"),
        ("ua194", "UA", "UA194"),
        ("U21234", "U2", "U21234"),
        ("A3601", "A3", "A3601"),
        ("BA0117", "BA", "BA0117"),
        (" VS19 ", "VS", "VS19"),
        ("VS19A", "VS", "VS19A"),
    ],
)
def test_iata_flight_numbers_parse(raw, carrier, number):
    assert parse_flight_number(raw) == (carrier, number, "")


@pytest.mark.parametrize("raw", ["BAW123", "12345", "123", "", "   ", None, 123, ["VS19"], "VS12345", "V"])
def test_everything_else_is_unparseable(raw):
    carrier, _, why = parse_flight_number(raw)
    assert carrier == "" and why


def test_an_icao_designator_says_why_it_is_not_guessed():
    assert "no ICAO->IATA table is configured, not guessed" in parse_flight_number("BAW123")[2]


# ---------------------------------------------------------------------------
# D15: cabins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word,letter",
    [("economy", "Y"), ("premium", "W"), ("business", "J"), ("first", "F"),
     ("Business", "J"), (" PREMIUM ", "W"), ("FIRST", "F")],
)
def test_the_four_cabin_words_map(word, letter):
    facts = AwardFacts(**{**VS_FACTS.__dict__, "cabin": letter})
    _, lookup = _lookup(_one(cabin=word), facts)
    assert lookup.status is MetalStatus.KNOWN, lookup.render()


@pytest.mark.parametrize("word", ["premium_economy", "premium economy", "Upper Class", "J", "biz", 3])
def test_any_other_cabin_value_is_unmapped(word):
    parsed, lookup = _lookup(_one(cabin=word))
    assert lookup.reason_code == "CABIN_UNMAPPED"
    assert (word if isinstance(word, str) else repr(word)) in parsed.cabins_seen


# ---------------------------------------------------------------------------
# D16: matching
# ---------------------------------------------------------------------------


def test_itineraries_at_another_price_are_disclosed_and_do_not_match():
    payload = tp.payload([
        tp.vs_direct(),
        tp.vs_direct("VS41", trip_id="t2", cost=70000),
        tp.vs_direct("VS43", trip_id="t3", cost=70000),
    ])
    _, lookup = _lookup(payload)
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.matched_trips == 1
    assert lookup.other_price_note == "other itineraries in J at 70,000 (2) - not this award's price"


def test_two_carrier_sets_at_the_award_price_are_ambiguous():
    facts = AwardFacts(**{**VS_FACTS.__dict__, "row_carriers": ("VS", "DL")})
    payload = tp.payload([
        tp.vs_direct(),
        tp.trip([tp.segment("DL41", "LHR", "SFO", 1)], trip_id="t2"),
    ])
    _, lookup = _lookup(payload, facts)
    assert lookup.status is MetalStatus.AMBIGUOUS
    assert lookup.carrier_sets == (("VS",), ("DL",))
    assert lookup.possible_carriers == ("VS", "DL")
    text = lookup.render()
    assert "NOT KNOWN" in text and "VS | DL" in text
    assert "operated by" not in text


def test_two_itineraries_on_the_same_carrier_are_still_known():
    payload = tp.payload([tp.vs_direct(), tp.vs_direct("VS41", trip_id="t2")])
    _, lookup = _lookup(payload)
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.matched_trips == 2
    assert "2 itineraries at this price" in lookup.render()


def test_a_mixed_cabin_itinerary_is_excluded_and_disclosed():
    payload = tp.payload([tp.vs_direct(), tp.vs_direct("DL41", trip_id="t2", mixed=30)])
    _, lookup = _lookup(payload)
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.carriers == ("VS",)
    assert lookup.excluded_mixed == 1


def test_source_matching_is_case_insensitive():
    _, lookup = _lookup(_one(source="VirginAtlantic"))
    assert lookup.status is MetalStatus.KNOWN


def test_one_unreadable_itinerary_that_could_match_blocks_known():
    payload = tp.payload([tp.vs_direct(), tp.vs_direct("VS41", trip_id="t2", Cabin=None)])
    _, lookup = _lookup(payload)
    assert lookup.status is MetalStatus.UNKNOWN
    assert lookup.reason_code == "TRIP_UNREADABLE"


def test_an_unreadable_itinerary_whose_key_rules_it_out_does_not_block():
    """Its source, cabin and cost are all readable, and it is another price."""
    payload = tp.payload([
        tp.vs_direct(),
        tp.vs_direct("VS41", trip_id="t2", cost=70000, flight_numbers="VS99"),
    ])
    parsed, lookup = _lookup(payload)
    assert len(parsed.unreadable) == 1
    assert lookup.status is MetalStatus.KNOWN


def test_extra_keys_are_tolerated_and_listed_as_drift():
    t = tp.vs_direct(NewTripKey=1)
    t["AvailabilitySegments"][0]["NewSegmentKey"] = "x"
    parsed, lookup = _lookup(tp.payload([t], newTopKey=True))
    assert lookup.status is MetalStatus.KNOWN
    assert "undocumented top-level key 'newTopKey'" in parsed.drift
    assert "undocumented itinerary key 'NewTripKey'" in parsed.drift
    assert "undocumented segment key 'NewSegmentKey'" in parsed.drift
    assert parsed.required_drift == []


def test_a_digit_string_mileage_cost_is_read_and_recorded_as_required_drift():
    parsed, lookup = _lookup(_one(cost="60000"))
    assert lookup.status is MetalStatus.KNOWN
    assert any("MileageCost is the string" in d for d in parsed.required_drift)


def test_coverage_is_a_function_of_the_bytes():
    assert seats_trips.trips_coverage({"data": [], "hasMore": False}) == (False, "")
    assert seats_trips.trips_coverage({"data": [], "hasMore": "true"})[0]
    assert seats_trips.trips_coverage({"data": [], "skip": 0}) == (False, "")
    assert seats_trips.trips_coverage([]) == (False, "")


def test_per_trip_taxes_are_shown_raw_with_both_readings_and_used_nowhere():
    _, lookup = _lookup(_one(taxes=4460, currency="CAD"))
    assert lookup.trip_taxes_note == (
        "raw 4460 CAD (unit NOT VERIFIED: CAD 44.60 if cents, CAD 4,460 if whole "
        "units); not used in any figure"
    )


def test_manifest_state_names_every_shape():
    def state(p):
        return parse_trips_payload(p, tp.AVAIL_ID, ("LHR", "SFO")).manifest_state

    assert state(tp.payload([tp.vs_direct()])) == "trips_readable"
    assert state(tp.payload([tp.vs_direct(cost=None)])) == "trips_unreadable"
    assert state(tp.payload([])) == "trips_empty"
    assert state(tp.payload([], hasMore=True)) == "trips_incomplete"
    assert state([]) == "trips_shape_error"


def test_availability_ids_are_validated_before_anything_is_built_from_them():
    assert seats_trips.valid_availability_id(tp.AVAIL_ID)
    for bad in ("../x", "a/b", "?x=1", "x" * 200, "short", "", None, 12345678901, "abc def ghij"):
        assert not seats_trips.valid_availability_id(bad), bad
