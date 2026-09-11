"""
Builders for Seats.aero trips responses shaped like the PUBLISHED schema.

Nothing here is a real response - no real trips response has ever been
captured. The field names and types come from the OpenAPI document
(developers.seats.aero/reference/get-trips.md, read 2026-09-11); the values are
whatever a test needs. A test that wants drift passes it explicitly.
"""
import copy
from typing import Any, Dict, List, Optional

# An availability id in the shape the search endpoint uses (27 base58-ish chars).
AVAIL_ID = "2mB3kQx9LpTz7VwYc4Hn8RsDfGj"


def segment(
    flight: str,
    origin: str,
    destination: str,
    order: int,
    *,
    departs: str = "2027-01-27T11:00:00Z",
    arrives: str = "2027-01-27T14:00:00Z",
    aircraft: str = "787-9",
    **extra: Any,
) -> Dict[str, Any]:
    seg = {
        "ID": f"seg{order}{flight}",
        "RouteID": "route1",
        "AvailabilityID": AVAIL_ID,
        "AvailabilityTripID": "trip1",
        "FlightNumber": flight,
        "Distance": 5000,
        "FareClass": "I",
        "AircraftName": aircraft,
        "AircraftCode": "789",
        "OriginAirport": origin,
        "DestinationAirport": destination,
        "DepartsAt": departs,
        "ArrivesAt": arrives,
        "CreatedAt": "2026-09-11T00:00:00Z",
        "UpdatedAt": "2026-09-11T00:00:00Z",
        "Source": "virginatlantic",
        "Order": order,
    }
    seg.update(extra)
    return seg


def trip(
    segments: List[Dict[str, Any]],
    *,
    trip_id: str = "trip1",
    source: str = "virginatlantic",
    cabin: str = "business",
    cost: Any = 60000,
    taxes: Any = 45000,
    currency: str = "GBP",
    availability_id: str = AVAIL_ID,
    mixed: Optional[int] = None,
    carriers: Optional[str] = "auto",
    flight_numbers: Optional[str] = "auto",
    **extra: Any,
) -> Dict[str, Any]:
    """One itinerary. `carriers`/`flight_numbers` "auto" derive from the segments; None omits them."""
    dicts = [s for s in segments if isinstance(s, dict)]
    numbers = [s["FlightNumber"] for s in sorted(dicts, key=lambda s: s.get("Order", 0))]
    prefixes = []
    for n in numbers:
        p = str(n).strip().upper()[:2]
        if p not in prefixes:
            prefixes.append(p)
    t = {
        "ID": trip_id,
        "RouteID": "route1",
        "AvailabilityID": availability_id,
        "AvailabilitySegments": segments,
        "TotalDuration": 600,
        "Stops": len(segments) - 1,
        "RemainingSeats": 2,
        "MileageCost": cost,
        "TotalTaxes": taxes,
        "TaxesCurrency": currency,
        "TaxesCurrencySymbol": "£",
        "AllianceCost": 0,
        "DepartsAt": dicts[0].get("DepartsAt") if dicts else None,
        "ArrivesAt": dicts[-1].get("ArrivesAt") if dicts else None,
        "Cabin": cabin,
        "CreatedAt": "2026-09-11T00:00:00Z",
        "UpdatedAt": "2026-09-11T00:00:00Z",
        "Source": source,
    }
    if carriers == "auto":
        t["Carriers"] = ", ".join(prefixes)
    elif carriers is not None:
        t["Carriers"] = carriers
    if flight_numbers == "auto":
        t["FlightNumbers"] = ", ".join(numbers)
    elif flight_numbers is not None:
        t["FlightNumbers"] = flight_numbers
    if mixed is not None:
        t["MixedCabinPct"] = mixed
    t.update(extra)
    return t


def payload(trips: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    body = {
        "data": trips,
        "origin_coordinates": {"Lat": 51.47, "Lon": -0.45},
        "destination_coordinates": {"Lat": 37.62, "Lon": -122.38},
        "booking_links": [{"label": "Book", "link": "https://example.invalid", "primary": True}],
    }
    body.update(extra)
    return body


def vs_direct(flight: str = "VS19", **kw: Any) -> Dict[str, Any]:
    """A one-segment LHR->SFO itinerary. Keyword args go to `trip()`."""
    return trip([segment(flight, "LHR", "SFO", 1)], **kw)


def deep(value: Any) -> Any:
    return copy.deepcopy(value)
