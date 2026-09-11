"""
Seats.aero trips endpoint: which flights an award is, and the carrier they name.

    GET https://seats.aero/partnerapi/trips/{availability_id}
        ?include_filtered=false&min_cabin_pct=100

THIS PARSER HAS NEVER SEEN A REAL RESPONSE. It is built from Seats.aero's
published OpenAPI document (developers.seats.aero/reference/get-trips.md,
re-fetched 2026-09-11) and nothing else. The search parser was built the same
way once, read four keys that do not exist, and turned a real 9-seat award into
"no award availability". So every line derived from this module carries a
label saying the parser is UNVERIFIED until a real capture, written by
`python -m src.trips_tools capture`, is committed under
tests/fixtures/seats_aero/trips_endpoint/real/ and named below.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.models import (
    METAL_PROVENANCE_TRIPS,
    MetalLookup,
    MetalStatus,
)


# Which parser read a trips response. Stamped into every trips snapshot and its
# manifest row, separately from the search `PARSER_VERSION`, so a change to this
# parser says REPARSED for trips lookups only and never for search snapshots
# whose parse did not change.
TRIPS_PARSER_VERSION = "2026-09-11.trips-unverified"

# The filename of the committed REAL capture this parser has been verified
# against, under tests/fixtures/seats_aero/trips_endpoint/real/. Empty means
# nobody has checked it against a real response, and every line derived from a
# parse says so. Only a real capture can fill it in; the label test refuses a
# file under synthetic/.
TRIPS_SCHEMA_VERIFIED_BY: str = ""

# The unit of a trip's own `TotalTaxes`. The search endpoint's `{X}TotalTaxes` is
# integer cents; the trips document only says "integer". "unverified" means the
# per-trip figure is DISPLAY ONLY. The allowed flipped values are "cents" and
# "units", and only a real capture whose availability row shows the same figure
# can justify either.
TRIPS_TOTALTAXES_UNIT = "unverified"
TRIPS_TOTALTAXES_UNITS = ("unverified", "cents", "units")

PARSER_UNVERIFIED_LABEL = (
    "[trips parser UNVERIFIED against a real Seats.aero response - built from "
    "the published schema only]"
)


def parser_is_verified() -> bool:
    """True only once a real capture has been named. Read at call time."""
    return bool(TRIPS_SCHEMA_VERIFIED_BY)


def trips_parser_label() -> str:
    """The label every parse-derived line carries, or "" once verified."""
    return "" if parser_is_verified() else PARSER_UNVERIFIED_LABEL


# The request, sent explicitly even though these are the documented defaults,
# so the cache key and the snapshot record exactly what was asked.
#   include_filtered=false: the award being matched is a non-Raw price, and
#     dynamically priced itineraries are a different product.
#   min_cabin_pct=100: a mixed-cabin itinerary is a different product from a
#     {cabin} award.
TRIPS_REQUEST_PARAMS: Dict[str, str] = {
    "include_filtered": "false",
    "min_cabin_pct": "100",
}

# Seats.aero's cabin WORDS to this codebase's cabin LETTERS. Exact, on a
# lowercased stripped value. "business" is the published example; the other
# three are the vocabulary of Seats.aero's own cabin parameter and are ASSUMED.
# Anything else is CABIN_UNMAPPED - an unknown, never a near miss.
CABIN_FROM_TRIPS: Dict[str, str] = {
    "economy": "Y",
    "premium": "W",
    "business": "J",
    "first": "F",
}

# An availability id is checked BEFORE any URL or filename is built from it.
AVAILABILITY_ID_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")

# The keys the published schema documents. Anything else is DRIFT: listed,
# never an error.
DOCUMENTED_TOP_KEYS = frozenset(
    {"data", "origin_coordinates", "destination_coordinates", "booking_links"}
)
DOCUMENTED_TRIP_KEYS = frozenset(
    {
        "ID", "RouteID", "AvailabilityID", "AvailabilitySegments", "TotalDuration",
        "Stops", "Carriers", "RemainingSeats", "MileageCost", "TotalTaxes",
        "TaxesCurrency", "TaxesCurrencySymbol", "AllianceCost", "FlightNumbers",
        "DepartsAt", "ArrivesAt", "Cabin", "CreatedAt", "UpdatedAt", "Source",
        "MixedCabinPct",
    }
)
DOCUMENTED_SEGMENT_KEYS = frozenset(
    {
        "ID", "RouteID", "AvailabilityID", "AvailabilityTripID", "FlightNumber",
        "Distance", "FareClass", "AircraftName", "AircraftCode", "OriginAirport",
        "DestinationAirport", "DepartsAt", "ArrivesAt", "CreatedAt", "UpdatedAt",
        "Source", "Order",
    }
)
# Keys that would mean the itinerary list is paginated. None is documented.
PAGINATION_KEYS = ("hasMore", "has_more", "cursor", "next_cursor", "skip", "count")

# The same plausibility ceiling the search parser uses for a mileage price.
MAX_PLAUSIBLE_MILEAGE = 5_000_000

# The manifest `state` column for a trips row.
STATE_READABLE = "trips_readable"
STATE_UNREADABLE = "trips_unreadable"
STATE_EMPTY = "trips_empty"
STATE_INCOMPLETE = "trips_incomplete"
STATE_SHAPE_ERROR = "trips_shape_error"


def valid_availability_id(value: Any) -> bool:
    # fullmatch, not match: `$` also matches before a trailing newline, and an
    # id carrying one would reach a URL, a filename and a manifest cell.
    return isinstance(value, str) and bool(AVAILABILITY_ID_RE.fullmatch(value))


# ---------------------------------------------------------------------------
# Flight numbers
# ---------------------------------------------------------------------------

_IATA_FLIGHT = re.compile(r"^([A-Z][A-Z0-9]|[0-9][A-Z])([0-9]{1,4})([A-Z]?)$")
_ICAO_FLIGHT = re.compile(r"^[A-Z]{3}[0-9]{1,4}[A-Z]?$")


def normalize_flight_number(value: Any) -> str:
    """Upper-case, strip, and drop whitespace between the prefix and the digits."""
    if not isinstance(value, str):
        return ""
    text = value.strip().upper()
    return re.sub(r"^([A-Z0-9]{2,3})\s+(?=[0-9])", r"\1", text)


def parse_flight_number(value: Any) -> Tuple[str, str, str]:
    """
    (carrier, normalized flight number, why-unparseable). A carrier is returned
    only for an IATA two-character designator with at least one letter.

    `carriers.csv` membership is NOT required: the row's own carrier list is
    the authority, and the cross-check against it happens in `match_award`.
    """
    if not isinstance(value, str):
        return "", "", (
            f"FlightNumber is a {type(value).__name__}, not a string"
        )
    text = normalize_flight_number(value)
    if not text:
        return "", "", "FlightNumber is empty"
    m = _IATA_FLIGHT.match(text)
    if m:
        return m.group(1), text, ""
    if _ICAO_FLIGHT.match(text):
        return "", text, (
            f"FlightNumber {value!r} looks like an ICAO designator; no "
            f"ICAO->IATA table is configured, not guessed"
        )
    return "", text, f"FlightNumber {value!r} is not an airline designator plus a number"


def _split_list(value: str) -> List[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TripSegment:
    order: int
    flight_number: str
    carrier: str
    origin: str
    destination: str
    departs_at: str = ""
    arrives_at: str = ""
    aircraft: str = ""

    def describe(self) -> str:
        times = ""
        if self.departs_at or self.arrives_at:
            times = f" {self.departs_at or '?'}"
        aircraft = f" ({self.aircraft})" if self.aircraft else ""
        arrive = f" {self.arrives_at}" if self.arrives_at else ""
        return (
            f"{self.flight_number} {self.origin}{times} -> {self.destination}"
            f"{arrive}{aircraft}"
        )


@dataclass
class TripItinerary:
    """One itinerary from the response. `unreadable_code` set means unreadable."""

    id: str = ""
    source: Optional[str] = None  # lowercased, None if unreadable
    cabin: Optional[str] = None  # a letter, None if unreadable or unmapped
    cabin_raw: Any = None
    mileage_cost: Optional[int] = None
    total_taxes_raw: Any = None
    taxes_currency: str = ""
    mixed_cabin_pct: int = 0
    segments: Tuple[TripSegment, ...] = ()
    carriers: Tuple[str, ...] = ()
    unreadable_code: str = ""
    unreadable_reason: str = ""

    @property
    def readable(self) -> bool:
        return not self.unreadable_code

    @property
    def flight_numbers(self) -> Tuple[str, ...]:
        return tuple(s.flight_number for s in self.segments)

    @property
    def key_fully_readable(self) -> bool:
        return (
            self.source is not None
            and self.cabin is not None
            and self.mileage_cost is not None
        )


@dataclass
class ParsedTrips:
    """What the parser made of one trips response. Failures kept apart."""

    trips: List[TripItinerary] = field(default_factory=list)
    # (reason code, sentence) when the envelope itself is not readable.
    envelope_error: Optional[Tuple[str, str]] = None
    incomplete: bool = False
    incomplete_reason: str = ""
    # Every deviation from the published schema, as a sentence. Never an error.
    drift: List[str] = field(default_factory=list)
    # The subset that blocks flipping the UNVERIFIED label: a required field
    # missing, of the wrong type, or failing a documented check.
    required_drift: List[str] = field(default_factory=list)
    cabins_seen: List[str] = field(default_factory=list)
    flight_number_shapes: List[str] = field(default_factory=list)
    pagination_keys: List[str] = field(default_factory=list)
    mixed_cabin_present: int = 0
    data_len: Optional[int] = None

    @property
    def readable(self) -> List[TripItinerary]:
        return [t for t in self.trips if t.readable]

    @property
    def unreadable(self) -> List[TripItinerary]:
        return [t for t in self.trips if not t.readable]

    @property
    def manifest_state(self) -> str:
        if self.envelope_error:
            return STATE_SHAPE_ERROR
        if self.incomplete:
            return STATE_INCOMPLETE
        if not self.trips:
            return STATE_EMPTY
        if self.unreadable:
            return STATE_UNREADABLE
        return STATE_READABLE

    def note(self, text: str, required: bool = False) -> None:
        if text not in self.drift:
            self.drift.append(text)
        if required and text not in self.required_drift:
            self.required_drift.append(text)


def _flight_shape(text: str) -> str:
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Z]", "A", text))


def trips_coverage(payload: Any) -> Tuple[bool, str]:
    """
    (incomplete, why) as a FUNCTION OF THE BYTES, so a cached or replayed
    response answers the same way as the fetch that wrote it.
    """
    from src.seats_client import _as_int, pagination_signals

    if not isinstance(payload, dict):
        return False, ""
    reasons = []
    # THE SAME READERS THE SEARCH TRANSPORT USES, shared rather than copied.
    has_more, cursor, skip = pagination_signals(payload)
    if has_more:
        reasons.append("the response says hasMore")
    if cursor:
        reasons.append("the response carries a cursor to a further page")
    # This request sent no offset, so any positive one advances past it - the
    # rule the search transport applies to an offset.
    if skip is not None and skip > 0:
        reasons.append(f"the response carries skip={skip}")
    count = _as_int(payload.get("count"))
    data = payload.get("data")
    if count is not None and isinstance(data, list) and count > len(data):
        reasons.append(
            f"the response says count={count} and carries {len(data)} itinerary(ies)"
        )
    if reasons:
        return True, (
            "; ".join(reasons)
            + ", and this tool does not page the itinerary list, so it saw only "
            "part of it"
        )
    return False, ""


def _str_field(obj: Dict[str, Any], key: str) -> Optional[str]:
    value = obj.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_trip(
    raw: Any, index: int, requested_id: str, route: Tuple[str, str], out: ParsedTrips
) -> TripItinerary:
    trip = TripItinerary()
    label = f"itinerary {index + 1}"
    if not isinstance(raw, dict):
        trip.unreadable_code = "TRIP_UNREADABLE"
        trip.unreadable_reason = f"{label} is a {type(raw).__name__}, not an object"
        out.note(trip.unreadable_reason, required=True)
        return trip

    for key in sorted(set(raw) - DOCUMENTED_TRIP_KEYS):
        out.note(f"undocumented itinerary key {key!r}")

    def fail(code: str, reason: str) -> TripItinerary:
        if not trip.unreadable_code:
            trip.unreadable_code = code
            trip.unreadable_reason = f"{label}: {reason}"
        out.note(f"{label}: {reason}", required=True)
        return trip

    # -- the three key fields first, so "could this be the award?" can be
    #    answered even about an itinerary that fails a later check --------
    source = _str_field(raw, "Source")
    trip.source = source.lower() if source else None
    trip.cabin_raw = raw.get("Cabin")
    if isinstance(trip.cabin_raw, str):
        if trip.cabin_raw not in out.cabins_seen:
            out.cabins_seen.append(trip.cabin_raw)
        trip.cabin = CABIN_FROM_TRIPS.get(trip.cabin_raw.strip().lower())
    elif trip.cabin_raw is not None:
        shown = repr(trip.cabin_raw)
        if shown not in out.cabins_seen:
            out.cabins_seen.append(shown)
    cost_raw = raw.get("MileageCost")
    cost: Optional[int] = None
    cost_problem = ""
    if isinstance(cost_raw, bool) or cost_raw is None or isinstance(cost_raw, float):
        cost_problem = f"MileageCost is {cost_raw!r}, not an integer"
    elif isinstance(cost_raw, int):
        cost = cost_raw
    elif isinstance(cost_raw, str) and cost_raw.strip().isdigit():
        cost = int(cost_raw.strip())
        out.note(
            f"{label}: MileageCost is the string {cost_raw!r}, not an integer "
            f"(read as {cost:,})",
            required=True,
        )
    else:
        cost_problem = f"MileageCost is {cost_raw!r}, not an integer"
    if cost is not None and not (0 < cost <= MAX_PLAUSIBLE_MILEAGE):
        cost_problem = f"MileageCost {cost:,} is not a bookable price"
        cost = None
    trip.mileage_cost = cost

    trip.total_taxes_raw = raw.get("TotalTaxes")
    if trip.total_taxes_raw is not None and (
        isinstance(trip.total_taxes_raw, bool) or not isinstance(trip.total_taxes_raw, int)
    ):
        out.note(f"{label}: TotalTaxes is {trip.total_taxes_raw!r}, not an integer")
    currency = raw.get("TaxesCurrency")
    if currency is not None and not isinstance(currency, str):
        out.note(f"{label}: TaxesCurrency is {currency!r}, not a string")
    trip.taxes_currency = currency.strip().upper() if isinstance(currency, str) else ""
    for key in ("DepartsAt", "ArrivesAt"):
        if key in raw and raw[key] is not None and not isinstance(raw[key], str):
            out.note(f"{label}: {key} is {raw[key]!r}, not a string")

    # -- required fields, in a fixed order; the first failure names it --------
    trip_id = raw.get("ID")
    if not isinstance(trip_id, str) or not trip_id.strip():
        fail("TRIP_UNREADABLE", f"ID is {trip_id!r}, not a non-empty string")
    else:
        trip.id = trip_id.strip()
    avail = raw.get("AvailabilityID")
    if not isinstance(avail, str) or not avail.strip():
        fail("TRIP_UNREADABLE", f"AvailabilityID is {avail!r}, not a non-empty string")
    elif avail.strip() != requested_id:
        fail(
            "AVAILABILITY_ID_MISMATCH",
            f"AvailabilityID is {avail.strip()!r} and the lookup asked about "
            f"{requested_id!r}",
        )
    if trip.source is None:
        fail("TRIP_UNREADABLE", f"Source is {raw.get('Source')!r}, not a non-empty string")
    if trip.cabin_raw is None:
        fail("TRIP_UNREADABLE", "Cabin is missing")
    elif trip.cabin is None:
        fail(
            "CABIN_UNMAPPED",
            f"Cabin is {trip.cabin_raw!r}, which is not one of "
            f"{sorted(CABIN_FROM_TRIPS)}",
        )
    if cost_problem:
        fail("TRIP_UNREADABLE", cost_problem)

    raw_segments = raw.get("AvailabilitySegments")
    if not isinstance(raw_segments, list) or not raw_segments:
        return fail(
            "TRIP_UNREADABLE",
            f"AvailabilitySegments is {type(raw_segments).__name__ if raw_segments is not None else 'missing'}"
            f"{' and empty' if raw_segments == [] else ''}, not a non-empty list",
        )

    segments: List[TripSegment] = []
    orders = []
    for s_index, seg in enumerate(raw_segments):
        s_label = f"segment {s_index + 1}"
        if not isinstance(seg, dict):
            return fail("TRIP_UNREADABLE", f"{s_label} is a {type(seg).__name__}, not an object")
        for key in sorted(set(seg) - DOCUMENTED_SEGMENT_KEYS):
            out.note(f"undocumented segment key {key!r}")
        order = seg.get("Order")
        if isinstance(order, bool) or not isinstance(order, int):
            return fail("TRIP_UNREADABLE", f"{s_label} Order is {order!r}, not an integer")
        orders.append(order)
        origin = _str_field(seg, "OriginAirport")
        destination = _str_field(seg, "DestinationAirport")
        if origin is None or destination is None:
            return fail(
                "TRIP_UNREADABLE",
                f"{s_label} OriginAirport/DestinationAirport is "
                f"{seg.get('OriginAirport')!r}/{seg.get('DestinationAirport')!r}",
            )
        if "FlightNumber" not in seg:
            return fail("TRIP_UNREADABLE", f"{s_label} FlightNumber is missing")
        carrier, number, why = parse_flight_number(seg.get("FlightNumber"))
        if number:
            shape = _flight_shape(number)
            if shape not in out.flight_number_shapes:
                out.flight_number_shapes.append(shape)
        if why:
            return fail("FLIGHT_NUMBER_UNPARSEABLE", f"{s_label}: {why}")
        aircraft = seg.get("AircraftName") or seg.get("AircraftCode") or ""
        if not isinstance(aircraft, str):
            out.note(f"{label} {s_label}: aircraft is {aircraft!r}, not a string")
            aircraft = ""
        segments.append(
            TripSegment(
                order=order,
                flight_number=number,
                carrier=carrier,
                origin=origin.upper(),
                destination=destination.upper(),
                departs_at=seg.get("DepartsAt") if isinstance(seg.get("DepartsAt"), str) else "",
                arrives_at=seg.get("ArrivesAt") if isinstance(seg.get("ArrivesAt"), str) else "",
                aircraft=aircraft.strip(),
            )
        )
    if len(set(orders)) != len(orders):
        return fail("TRIP_INCONSISTENT", f"segment Order values repeat ({orders})")
    segments.sort(key=lambda s: s.order)
    trip.segments = tuple(segments)
    carriers: List[str] = []
    for s in segments:
        if s.carrier not in carriers:
            carriers.append(s.carrier)
    trip.carriers = tuple(carriers)

    # -- documented cross-checks, used only if present -------------------------
    if "FlightNumbers" in raw:
        listed = raw.get("FlightNumbers")
        if not isinstance(listed, str):
            return fail("TRIP_UNREADABLE", f"FlightNumbers is {listed!r}, not a string")
        mine = [normalize_flight_number(x) for x in _split_list(listed)]
        if mine != list(trip.flight_numbers):
            return fail(
                "TRIP_INCONSISTENT",
                f"FlightNumbers says {listed!r} and the segments say "
                f"{', '.join(trip.flight_numbers)}",
            )
    if "Carriers" in raw:
        listed = raw.get("Carriers")
        if not isinstance(listed, str):
            return fail("TRIP_UNREADABLE", f"Carriers is {listed!r}, not a string")
        named = {c.upper() for c in _split_list(listed)}
        if named != set(trip.carriers):
            # One of the two may be the OPERATING carrier and the other the
            # marketing one. Which is which is not documented, so neither is
            # believed.
            return fail(
                "TRIP_INCONSISTENT",
                f"Carriers says {listed!r} and the flight numbers name "
                f"{', '.join(trip.carriers)}; one may be the operating carrier, "
                f"and which one is not documented",
            )
    if "MixedCabinPct" in raw:
        out.mixed_cabin_present += 1
        pct = raw.get("MixedCabinPct")
        if isinstance(pct, bool) or not isinstance(pct, int) or not (0 <= pct <= 100):
            return fail("TRIP_UNREADABLE", f"MixedCabinPct is {pct!r}, not an integer 1-100")
        if pct == 0:
            out.note(f"{label}: MixedCabinPct is 0 (documented as omitted when 0)")
        trip.mixed_cabin_pct = pct

    # -- the route chain -----------------------------------------------------
    origin, destination = route
    if not origin or not destination:
        return fail(
            "TRIP_INCONSISTENT",
            "the award's own route is not known, so the itinerary cannot be "
            "checked against it",
        )
    chain_ok = segments[0].origin == origin and segments[-1].destination == destination
    for before, after in zip(segments, segments[1:]):
        if after.origin != before.destination:
            chain_ok = False
    if not chain_ok:
        path = " ".join(f"{s.origin}-{s.destination}" for s in segments)
        return fail(
            "TRIP_INCONSISTENT",
            f"the segments ({path}) do not run {origin}->{destination} in one chain",
        )
    return trip


def parse_trips_payload(
    payload: Any, requested_id: str, route: Tuple[str, str]
) -> ParsedTrips:
    """
    Strict on required fields, tolerant of extras. NEVER RAISES.

    An empty `data` list parses to zero trips and `data_len == 0`; what that
    MEANS is `match_award`'s business, and the answer there is EMPTY_DATA - an
    unknown, never "no trips".
    """
    out = ParsedTrips()
    try:
        if not isinstance(payload, dict):
            out.envelope_error = (
                "SHAPE_ERROR",
                f"the response is a {type(payload).__name__}, not a JSON object",
            )
            out.note(out.envelope_error[1], required=True)
            return out
        for key in sorted(set(payload) - DOCUMENTED_TOP_KEYS):
            out.note(f"undocumented top-level key {key!r}")
        out.pagination_keys = [k for k in PAGINATION_KEYS if k in payload]
        out.incomplete, out.incomplete_reason = trips_coverage(payload)
        if "data" not in payload:
            out.envelope_error = ("SHAPE_ERROR", "the response has no 'data' key")
            out.note(out.envelope_error[1], required=True)
            return out
        data = payload.get("data")
        if not isinstance(data, list):
            out.envelope_error = (
                "SHAPE_ERROR",
                f"'data' is a {type(data).__name__}, not a list of itineraries",
            )
            out.note(out.envelope_error[1], required=True)
            return out
        out.data_len = len(data)
        route_key = (str(route[0] or "").upper(), str(route[1] or "").upper())
        for index, raw in enumerate(data):
            out.trips.append(_parse_trip(raw, index, requested_id, route_key, out))
    except Exception as e:  # noqa: BLE001 - a parse must never raise into the run
        out.envelope_error = (
            "SHAPE_ERROR",
            f"the parser failed unexpectedly ({type(e).__name__}: {e})",
        )
        out.note(out.envelope_error[1], required=True)
    return out


# ---------------------------------------------------------------------------
# Matching one award
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AwardFacts:
    """The facts about one award that a trips response is matched against."""

    availability_id: str
    source_code: str
    cabin: str
    cost: int
    row_carriers: Tuple[str, ...]
    origin: str
    destination: str

    @classmethod
    def from_award(cls, award) -> "AwardFacts":
        route = str(getattr(award, "route", "") or "")
        origin, _, destination = route.partition("-")
        diag = getattr(award, "raw_diagnostics", None) or {}
        return cls(
            availability_id=str(diag.get("availability_id") or ""),
            source_code=str(getattr(award, "program_source_code", "") or "").lower(),
            cabin=str(getattr(award, "award_type", "") or "").upper(),
            cost=int(getattr(award, "cost", 0) or 0),
            row_carriers=tuple(getattr(award, "candidate_carriers", None) or ()),
            origin=origin.strip().upper(),
            destination=destination.strip().upper(),
        )


def _could_be_the_award(trip: TripItinerary, facts: AwardFacts) -> bool:
    """
    An unreadable itinerary is ruled out ONLY when its source, cabin and cost
    are all readable and they are not this award's. Anything less and it might
    be the award, so it blocks KNOWN.
    """
    if not trip.key_fully_readable:
        return True
    return (
        trip.source == facts.source_code
        and trip.cabin == facts.cabin
        and trip.mileage_cost == facts.cost
    )


def _money_raw(amount: int, currency: str) -> str:
    return f"{currency or '(no currency)'} {amount:,}"


def trip_taxes_display(trip: TripItinerary) -> str:
    """
    A trip's own TotalTaxes as text. While the unit is unverified it is shown raw
    with BOTH readings and is never a figure.
    """
    raw = trip.total_taxes_raw
    cur = trip.taxes_currency or "(no currency)"
    if raw is None:
        return "no TotalTaxes on this itinerary"
    if isinstance(raw, bool) or not isinstance(raw, int):
        return f"TotalTaxes {raw!r} is not an integer"
    if TRIPS_TOTALTAXES_UNIT == "cents":
        return f"raw {raw} {cur} = {cur} {raw / 100:,.2f} (unit verified as cents)"
    if TRIPS_TOTALTAXES_UNIT == "units":
        return f"raw {raw} {cur} = {cur} {raw:,} (unit verified as whole units)"
    return (
        f"raw {raw} {cur} (unit NOT VERIFIED: {cur} {raw / 100:,.2f} if cents, "
        f"{cur} {raw:,} if whole units)"
    )


def _one_trip_usd(raw: Any, currency: str, source_code: str, award, below_duty):
    """(usd or None, why-unknown) for ONE matched itinerary's TotalTaxes."""
    from src import seats_client

    if source_code in seats_client.TAXES_UNREPORTED_SOURCES:
        return None, f"Seats.aero reports no taxes for {source_code!r}"
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return None, f"TotalTaxes {raw!r} is not an integer"
    if raw == 0:
        return None, "TotalTaxes 0, which means not reported"
    if raw < 0:
        return None, f"TotalTaxes {raw} is negative, which is corrupt"
    cents = raw if TRIPS_TOTALTAXES_UNIT == "cents" else raw * 100
    known, _, _, usd, note = seats_client.convert_taxes(cents, currency)
    if not known:
        return None, note
    if below_duty is not None:
        import dataclasses as _dc

        why = below_duty(
            _dc.replace(award, cash_component=usd, cash_component_known=True)
        )
        if why:
            return None, f"${usd:,.2f} is below the UK duty owed on it"
    return usd, ""


def trip_taxes_view(metal: MetalLookup, award, row_usd: float, below_duty=None):
    """
    What the matched itineraries' own TotalTaxes do to the award's taxes.

    Returns (taxes_unknown_now, note). While `TRIPS_TOTALTAXES_UNIT` is
    "unverified" this ALWAYS returns (False, ""): per-trip taxes are display
    only and cannot move any number, whatever they say.

    Once the unit is verified, each matched itinerary's figure goes through the
    same trust rules as the row's. If any is UNKNOWN, or above the row's figure
    by more than max($1, 1%), which itinerary you book decides the cash - so the
    award's taxes become UNKNOWN. A LOWER figure is disclosed and never used.
    """
    if TRIPS_TOTALTAXES_UNIT not in ("cents", "units"):
        return False, ""
    if metal is None or metal.status not in (MetalStatus.KNOWN, MetalStatus.AMBIGUOUS):
        return False, ""
    if not metal.matched_trip_taxes:
        return False, ""
    source = str(getattr(award, "program_source_code", "") or "").lower()
    unknown: List[str] = []
    higher: List[float] = []
    lower: List[float] = []
    tolerance = max(1.0, 0.01 * row_usd)
    for i, (raw, currency) in enumerate(metal.matched_trip_taxes, start=1):
        usd, why = _one_trip_usd(raw, currency, source, award, below_duty)
        if usd is None:
            unknown.append(f"itinerary {i}: {why}")
        elif usd > row_usd + tolerance:
            higher.append(usd)
        elif usd < row_usd - tolerance:
            lower.append(usd)
    if unknown:
        return True, (
            f"which itinerary you book decides the taxes, and one itinerary's own "
            f"figure is UNKNOWN ({'; '.join(unknown)}). The row says ${row_usd:,.2f}; "
            f"the taxes on this award are UNKNOWN - not ${row_usd:,.2f}."
        )
    if higher:
        return True, (
            f"which itinerary you book decides the taxes; between ${row_usd:,.2f} "
            f"and ${max(higher):,.2f} across the {metal.matched_trips} matched "
            f"itineraries. The taxes on this award are UNKNOWN - not ${row_usd:,.2f}."
        )
    if lower:
        return False, (
            f"an itinerary at this price shows lower taxes (${min(lower):,.2f}) than "
            f"the row's ${row_usd:,.2f}; the row figure is used and the lower one "
            f"is disclosed only"
        )
    return False, ""


def _describe_counts(costs: List[int]) -> str:
    counted: Dict[int, int] = {}
    for c in costs:
        counted[c] = counted.get(c, 0) + 1
    return ", ".join(f"{c:,} ({n})" for c, n in sorted(counted.items()))


def match_award(parsed: ParsedTrips, facts: AwardFacts) -> MetalLookup:
    """
    The MetalLookup for ONE award from one parsed response. NEVER RAISES.

    Only a set of READABLE itineraries that match the award's program, cabin
    and price, that all name ONE carrier set by flight number, and whose every
    carrier is in the row's own list, gives KNOWN. Everything else is a named
    UNKNOWN (or AMBIGUOUS) with the award's possible carriers attached.
    """
    domain: Dict[str, Any] = (
        {"possible_carriers": tuple(facts.row_carriers)}
        if facts.row_carriers
        else {"domain_unbounded": True}
    )
    common = dict(
        availability_id=facts.availability_id,
        row_carriers=tuple(facts.row_carriers),
        parser_verified=parser_is_verified(),
    )

    def unknown(code: str, detail: str, **extra) -> MetalLookup:
        return MetalLookup(
            status=MetalStatus.UNKNOWN, reason_code=code, detail=detail,
            **common, **domain, **extra,
        )

    try:
        if parsed.envelope_error:
            return unknown(parsed.envelope_error[0], parsed.envelope_error[1])
        if parsed.incomplete:
            return unknown("INCOMPLETE", parsed.incomplete_reason)
        if not parsed.trips:
            return unknown("EMPTY_DATA", "data: []")

        blocking = [t for t in parsed.unreadable if _could_be_the_award(t, facts)]
        if blocking:
            first = blocking[0]
            more = (
                f"; {len(blocking)} itineraries like it"
                if len(blocking) > 1
                else ""
            )
            return unknown(first.unreadable_code, f"{first.unreadable_reason}{more}")

        same = [
            t for t in parsed.readable
            if t.source == facts.source_code and t.cabin == facts.cabin
        ]
        matched = [t for t in same if t.mileage_cost == facts.cost]
        clean = [t for t in matched if t.mixed_cabin_pct == 0]
        mixed = [t for t in matched if t.mixed_cabin_pct > 0]
        others = [t.mileage_cost for t in same if t.mileage_cost != facts.cost]
        other_note = (
            f"other itineraries in {facts.cabin} at {_describe_counts(others)} - not "
            f"this award's price"
            if others
            else ""
        )
        extra = dict(other_price_note=other_note, excluded_mixed=len(mixed))

        if not clean:
            if mixed:
                return unknown(
                    "MIXED_CABIN_ONLY",
                    f"{len(mixed)} at {facts.cost:,} with MixedCabinPct "
                    f"{', '.join(str(t.mixed_cabin_pct) for t in mixed)}",
                    **extra,
                )
            seen = (
                f"costs seen in {facts.cabin} for {facts.source_code}: "
                f"{_describe_counts(others)}"
                if others
                else f"no readable itinerary in {facts.cabin} for "
                f"{facts.source_code or '(no source)'} among {len(parsed.readable)} "
                f"read"
            )
            return unknown(
                "NO_MATCH", f"{seen}; this award is {facts.cost:,}", **extra
            )

        named = []
        for t in clean:
            for c in t.carriers:
                if c not in named:
                    named.append(c)
        if not facts.row_carriers:
            return unknown(
                "ROW_CARRIERS_ABSENT",
                f"the flight numbers name {', '.join(named)}",
                **extra,
            )
        outside = [c for c in named if c not in facts.row_carriers]
        if outside:
            flights = sorted(
                {s.flight_number for t in clean for s in t.segments if s.carrier in outside}
            )
            return unknown(
                "CARRIER_NOT_IN_ROW_LIST",
                f"{', '.join(outside)} (flight {', '.join(flights)}; the row lists "
                f"{', '.join(facts.row_carriers)})",
                **extra,
            )

        sets: List[Tuple[str, ...]] = []
        for t in clean:
            if frozenset(t.carriers) not in {frozenset(s) for s in sets}:
                sets.append(t.carriers)
        flights: List[str] = []
        numbers: List[str] = []
        for i, t in enumerate(clean, start=1):
            prefix = f"[{i}] " if len(clean) > 1 else ""
            for s in t.segments:
                flights.append(prefix + s.describe())
                if s.flight_number not in numbers:
                    numbers.append(s.flight_number)
        taxes = "; ".join(
            (f"[{i}] " if len(clean) > 1 else "") + trip_taxes_display(t)
            for i, t in enumerate(clean, start=1)
        )
        if not taxes:
            taxes_note = ""
        elif TRIPS_TOTALTAXES_UNIT in ("cents", "units"):
            taxes_note = f"{taxes}; checked against the row's figure"
        else:
            taxes_note = f"{taxes}; not used in any figure"
        found = dict(
            flights=tuple(flights),
            flight_numbers=tuple(numbers),
            matched_trips=len(clean),
            trip_taxes_note=taxes_note,
            matched_trip_taxes=tuple((t.total_taxes_raw, t.taxes_currency) for t in clean),
            provenance=METAL_PROVENANCE_TRIPS,
            **extra,
        )
        if len(sets) == 1:
            return MetalLookup(
                status=MetalStatus.KNOWN, carriers=tuple(clean[0].carriers),
                **common, **found,
            )
        union = []
        for s in sets:
            for c in s:
                if c not in union:
                    union.append(c)
        return MetalLookup(
            status=MetalStatus.AMBIGUOUS,
            carrier_sets=tuple(tuple(s) for s in sets),
            possible_carriers=tuple(union),
            **common,
            **found,
        )
    except Exception as e:  # noqa: BLE001 - the metal pass never raises
        return unknown("UNEXPECTED_ERROR", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# What may flip the UNVERIFIED labels
# ---------------------------------------------------------------------------

REAL_CAPTURE_DIR_PARTS = ("tests", "fixtures", "seats_aero", "trips_endpoint", "real")
CAPTURED_BY = "src.trips_tools capture"


def _load_capture(path) -> Tuple[Optional[Dict[str, Any]], str]:
    import json

    try:
        envelope = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return None, f"{path.name} cannot be read as JSON ({e})"
    if not isinstance(envelope, dict) or not isinstance(envelope.get("_meta"), dict):
        return None, f"{path.name} is not a capture envelope"
    pages = envelope.get("pages")
    if not isinstance(pages, list) or len(pages) != 1:
        return None, f"{path.name} does not hold exactly one response page"
    return envelope, ""


def capture_parse(path):
    """(ParsedTrips, envelope) for a capture file, or (None, problem)."""
    envelope, problem = _load_capture(path)
    if envelope is None:
        return None, problem
    meta = envelope["_meta"]
    row = meta.get("availability_row") or {}
    route = row.get("Route") or {}
    origin = str(route.get("OriginAirport") or "").upper()
    destination = str(route.get("DestinationAirport") or "").upper()
    if not (origin and destination) and isinstance(meta.get("route"), str) and "->" in meta["route"]:
        origin, _, destination = meta["route"].partition("->")
    parsed = parse_trips_payload(
        envelope["pages"][0], str(meta.get("availability_id") or ""), (origin, destination)
    )
    return parsed, envelope


def schema_verification_problems(name: str, real_dir) -> List[str]:
    """
    Every reason `name` cannot be what TRIPS_SCHEMA_VERIFIED_BY names. [] means it can.

    It must be a plain filename in real/ (never synthetic/), written by the
    capture tool, not synthetic, key-redacted, with a matching content hash and a
    .raw.txt sibling, parsing to at least one itinerary with none unreadable and
    no required-field drift.
    """
    from pathlib import Path

    from src.response_cache import content_hash

    problems: List[str] = []
    if not name or "/" in name or "\\" in name or ".." in name:
        return [f"{name!r} is not a plain filename in real/"]
    path = Path(real_dir) / name
    if Path(real_dir).name != "real":
        problems.append(f"{real_dir} is not the real/ capture directory")
    if not path.is_file():
        return problems + [f"{path} does not exist"]
    envelope, problem = _load_capture(path)
    if envelope is None:
        return problems + [problem]
    meta = envelope["_meta"]
    if meta.get("synthetic") is not False:
        problems.append(f"{name}: _meta.synthetic is {meta.get('synthetic')!r}, not False")
    if meta.get("captured_by") != CAPTURED_BY:
        problems.append(f"{name}: captured_by is {meta.get('captured_by')!r}")
    if meta.get("key_redacted") is not True:
        problems.append(f"{name}: key_redacted is not True")
    if meta.get("content_hash") != content_hash(envelope["pages"]):
        problems.append(f"{name}: content_hash does not match the page it holds")
    if not path.with_suffix(".raw.txt").is_file():
        problems.append(f"{name}: no .raw.txt sibling with the verbatim body")
    parsed, _ = capture_parse(path)
    if parsed is None:
        return problems + [f"{name}: cannot be parsed"]
    if parsed.envelope_error:
        problems.append(f"{name}: {parsed.envelope_error[1]}")
    if not parsed.trips:
        problems.append(f"{name}: no itinerary to verify against")
    if parsed.unreadable:
        problems.append(f"{name}: {len(parsed.unreadable)} itinerary(ies) unreadable")
    if parsed.required_drift:
        problems.append(f"{name}: required-field drift: {parsed.required_drift}")
    return problems


def totaltaxes_unit_problems(unit: str, real_dir) -> List[str]:
    """
    Why `unit` may not be TRIPS_TOTALTAXES_UNIT. [] means it may.

    "unverified" always may. "cents" needs a real capture whose recorded
    availability row shows an itinerary at the row's source, cabin and price
    with TotalTaxes EQUAL to the row's {X}TotalTaxes (which is cents). "units"
    has no evidence path this tool can check, so it is refused.
    """
    from pathlib import Path

    if unit == "unverified":
        return []
    if unit not in TRIPS_TOTALTAXES_UNITS:
        return [f"{unit!r} is not one of {TRIPS_TOTALTAXES_UNITS}"]
    if unit == "units":
        return ["no capture can show whole units against a cents row figure; refused"]
    for path in sorted(Path(real_dir).glob("*.json")):
        parsed, envelope = capture_parse(path)
        if parsed is None:
            continue
        row = envelope["_meta"].get("availability_row") or {}
        source = str((row.get("Route") or {}).get("Source") or "").lower()
        for letter in ("Y", "W", "J", "F"):
            if row.get(f"{letter}Available") is not True:
                continue
            try:
                cost = int(str(row.get(f"{letter}MileageCost")).strip())
            except ValueError:
                continue
            row_taxes = row.get(f"{letter}TotalTaxes")
            for t in parsed.readable:
                if (
                    t.source == source and t.cabin == letter and t.mileage_cost == cost
                    and isinstance(row_taxes, int) and row_taxes > 0
                    and t.total_taxes_raw == row_taxes
                ):
                    return []
    return [
        "no real capture shows a matched itinerary whose TotalTaxes equals its "
        "row's {X}TotalTaxes"
    ]
