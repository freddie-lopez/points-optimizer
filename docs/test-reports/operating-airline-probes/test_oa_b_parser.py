"""
B. The parser and matcher, attacked directly: KNOWN built on partial data, the
availability-id validator, pagination signals, flight numbers and cabins.
"""
import pytest

from conftest import B4_VS, vs_itinerary  # noqa: F401
from src import seats_trips
from src.models import MetalStatus
from tests import _trips_payloads as tp

FACTS = seats_trips.AwardFacts(
    availability_id=B4_VS, source_code="virginatlantic", cabin="J", cost=60000,
    row_carriers=("VS", "DL"), origin="LHR", destination="SFO",
)


def match(payload, facts=FACTS):
    parsed = seats_trips.parse_trips_payload(payload, facts.availability_id, (facts.origin, facts.destination))
    return seats_trips.match_award(parsed, facts)


def seg(flight, o, d, order, **kw):
    return tp.segment(flight, o, d, order, AvailabilityID=B4_VS, **kw)


# ---------------------------------------------------------------------------
# The availability-id gate: "checked BEFORE any URL or filename is built from it"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "../x", "a/b", "?x=1", "A" * 200, "", "abc", "ABCDEFGHIJ K", "ABCDEFGHIJ%2F",
])
def test_obviously_bad_ids_are_refused(bad):
    assert not seats_trips.valid_availability_id(bad)


@pytest.mark.parametrize("bad", ["ABCDEFGHIJKLMNOP\n", "B4virxxxxxxxxxxxxxxxxxxxxxx\n"])
def test_an_id_with_a_trailing_newline_is_refused(bad):
    """
    FINDING. AVAILABILITY_ID_RE is `^[A-Za-z0-9]{10,64}$` used with re.match, and
    Python's `$` also matches BEFORE a trailing newline. So "<id>\\n" passes the
    gate that is supposed to run before any URL, filename or manifest cell is
    built from the id.
    """
    assert not seats_trips.valid_availability_id(bad)


# ---------------------------------------------------------------------------
# KNOWN on partial data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{"hasMore": 1}, {"hasMore": "1"}, {"has_more": "yes"}, {"skip": "100"}],
                         ids=["hasMore-1", "hasMore-str1", "has_more-yes", "skip-str"])
def test_pagination_signals_the_search_transport_honours_are_honoured_here(extra):
    """
    The search transport reads hasMore through `_as_bool` (True for 1, "1",
    "yes") and skip through `_as_int` (a digit string counts). `trips_coverage`
    accepts only `True`/"true" and an int skip, so the same signal that marks a
    search INCOMPLETE lets a trips list read as whole - and KNOWN.
    """
    lookup = match(tp.payload([vs_itinerary(B4_VS)], **extra))
    assert lookup.status is not MetalStatus.KNOWN, lookup.render()


def test_a_count_larger_than_the_list_is_incomplete():
    """
    `count` is in PAGINATION_KEYS but trips_coverage never reads it. A response
    that says it has 3 itineraries and carries 1 is a partial list.
    """
    lookup = match(tp.payload([vs_itinerary(B4_VS)], count=3))
    assert lookup.status is not MetalStatus.KNOWN, lookup.render()


@pytest.mark.parametrize("extra", [{"hasMore": True}, {"hasMore": "true"}, {"cursor": "abc"},
                                   {"next_cursor": 17}, {"skip": 100}])
def test_documented_pagination_signals_block_known(extra):
    assert match(tp.payload([vs_itinerary(B4_VS)], **extra)).reason_code == "INCOMPLETE"


def test_one_unreadable_could_match_itinerary_blocks_known():
    bad = vs_itinerary(B4_VS, trip_id="t2", Cabin=None)
    lookup = match(tp.payload([vs_itinerary(B4_VS), bad]))
    assert lookup.status is MetalStatus.UNKNOWN


def test_an_unreadable_itinerary_of_another_program_cabin_and_price_does_not_block():
    bad = vs_itinerary(B4_VS, trip_id="t2", source="flyingblue", cost=90000, cabin="economy",
                       AvailabilitySegments=[])
    assert match(tp.payload([vs_itinerary(B4_VS), bad])).status is MetalStatus.KNOWN


@pytest.mark.parametrize("mutate", [
    lambda t: t.update(Carriers="DL"),
    lambda t: t.update(FlightNumbers="DL41"),
    lambda t: t.update(AvailabilityID="Zz" + B4_VS[2:]),
    lambda t: t["AvailabilitySegments"].append(seg("VS20", "SFO", "LAX", 1)),
    lambda t: t.update(MixedCabinPct=101),
    lambda t: t.update(MixedCabinPct="10"),
    lambda t: t.update(MileageCost=60000.0),
    lambda t: t.update(MileageCost=True),
    lambda t: t.update(Source=""),
])
def test_self_contradicting_itinerary_never_gives_known(mutate):
    t = vs_itinerary(B4_VS)
    mutate(t)
    lookup = match(tp.payload([t]))
    assert lookup.status is MetalStatus.UNKNOWN, lookup.render()


def test_broken_segment_chain_never_known():
    t = tp.trip([seg("VS19", "LHR", "JFK", 1), seg("DL41", "EWR", "SFO", 2)],
                availability_id=B4_VS, cost=60000)
    assert match(tp.payload([t])).reason_code == "TRIP_INCONSISTENT"


def test_segments_out_of_order_in_the_list_are_sorted_by_order():
    t = tp.trip([seg("DL41", "JFK", "SFO", 2), seg("VS19", "LHR", "JFK", 1)],
                availability_id=B4_VS, cost=60000, flight_numbers="VS19, DL41")
    lookup = match(tp.payload([t]))
    assert lookup.status is MetalStatus.KNOWN and lookup.carriers == ("VS", "DL")


def test_mixed_cabin_zero_is_not_excluded_and_nonzero_is():
    lookup = match(tp.payload([vs_itinerary(B4_VS, mixed=0), vs_itinerary(B4_VS, "DL41", trip_id="t2", mixed=30)]))
    assert lookup.status is MetalStatus.KNOWN and lookup.carriers == ("VS",)
    assert lookup.excluded_mixed == 1


def test_parser_never_raises_on_hostile_input():
    for payload in [None, 0, "x", [], {"data": None}, {"data": [None, 1, "x", []]},
                    {"data": [{"AvailabilitySegments": [None]}]},
                    {"data": [{"AvailabilitySegments": [{"Order": [1]}]}]},
                    {"data": [{"Source": {"a": 1}, "Cabin": ["business"], "MileageCost": {"x": 1}}]},
                    {"data": [vs_itinerary(B4_VS, Carriers=["VS"])]},
                    {"data": [vs_itinerary(B4_VS, FlightNumbers=123)]}]:
        lookup = match(payload)
        assert lookup.status is MetalStatus.UNKNOWN


# ---------------------------------------------------------------------------
# D19 flight numbers, D15 cabins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,carrier", [
    ("9W123", "9W"), ("B6 1", "B6"), ("ua194", "UA"), ("U21234", "U2"), ("A3601", "A3"),
    ("BA0117", "BA"), (" vs 19 ", "VS"), ("UA1234A", "UA"),
])
def test_iata_flight_numbers_parse(raw, carrier):
    assert seats_trips.parse_flight_number(raw)[0] == carrier


@pytest.mark.parametrize("raw", ["BAW123", "12345", "", None, "123", 123, "VS", "V", "VS12345", "99123"])
def test_non_iata_flight_numbers_are_unparseable(raw):
    c, _, why = seats_trips.parse_flight_number(raw)
    assert c == "" and why


@pytest.mark.parametrize("cabin,letter", [("Business", "J"), (" business ", "J"), ("PREMIUM", "W")])
def test_cabin_words_map_exactly(cabin, letter):
    t = vs_itinerary(B4_VS, cabin=cabin)
    parsed = seats_trips.parse_trips_payload(tp.payload([t]), B4_VS, ("LHR", "SFO"))
    assert parsed.trips[0].cabin == letter


@pytest.mark.parametrize("cabin", ["premium economy", "premium_economy", "Upper Class", "J", "first class"])
def test_other_cabin_words_are_unmapped_never_guessed(cabin):
    lookup = match(tp.payload([vs_itinerary(B4_VS, cabin=cabin)]))
    assert lookup.reason_code == "CABIN_UNMAPPED"


def test_per_trip_taxes_are_display_only_while_the_unit_is_unverified():
    assert seats_trips.TRIPS_TOTALTAXES_UNIT == "unverified"
    for raw in (0, -1, 10 ** 9, None, "abc", 2 ** 63):
        lookup = match(tp.payload([vs_itinerary(B4_VS, taxes=raw)]))
        assert seats_trips.trip_taxes_view(lookup, None, 609.30) == (False, "")
