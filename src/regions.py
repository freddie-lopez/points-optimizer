"""
IATA -> country -> region classification, and carrier metadata.

Two jobs, both of which exist only to feed the surcharge model:

  classify(origin, destination) -> a normalized UNORDERED region pair, "NA-EU".
      Unordered because a surcharge band is a property of the market, not of the
      direction of travel.

  departure_country(origin) -> ISO-2 country of the first departure, "GB".
      Ordered and directional, because UK Air Passenger Duty is levied on
      DEPARTURE from the UK and on nothing else. This is the hook that makes
      ex-LHR the worst case in the whole model.

An unknown IATA code RAISES. It does not default to a region. A wrong region
silently selects a wrong surcharge band, which is exactly the class of confident
error v1 exists to eliminate.
"""
import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).parent.parent / "data"

# Region codes used throughout, in the FIXED PRECEDENCE ORDER that normalizes a
# region pair. The order is not alphabetical: it is the order the plan's own
# examples use ("NA-EU", "NA-AS", "EU-EU"), and it is what makes the pair a
# stable key into data/surcharges.csv. Changing this tuple silently re-keys every
# row in that file.
#
# Deliberately coarse: a surcharge band is a market generalisation, and finer
# regions would imply a precision the underlying data does not have.
REGIONS = ("NA", "SA", "EU", "AF", "ME", "AS", "OC")

_REGION_ORDER = {r: i for i, r in enumerate(REGIONS)}


class UnknownAirportError(ValueError):
    """Raised for an IATA code that is not in data/airports.csv."""


class UnknownCarrierError(ValueError):
    """Raised for an IATA carrier code that is not in data/carriers.csv."""


@dataclass(frozen=True)
class Airport:
    iata: str
    name: str
    country: str
    region: str


@dataclass(frozen=True)
class Carrier:
    iata: str
    name: str
    alliance: str
    country: str


@lru_cache(maxsize=1)
def _airports(path: str = "") -> Dict[str, Airport]:
    p = Path(path) if path else DATA_DIR / "airports.csv"
    out: Dict[str, Airport] = {}
    with open(p, "r") as f:
        for row in csv.DictReader(f):
            code = (row.get("iata") or "").strip().upper()
            if not code:
                continue
            region = (row["region"] or "").strip().upper()
            if region not in REGIONS:
                raise ValueError(
                    f"airports.csv: {code} has region {region!r}, which is not one "
                    f"of {REGIONS}."
                )
            out[code] = Airport(
                iata=code,
                name=(row.get("name") or "").strip(),
                country=(row["country"] or "").strip().upper(),
                region=region,
            )
    return out


@lru_cache(maxsize=1)
def _carriers(path: str = "") -> Dict[str, Carrier]:
    p = Path(path) if path else DATA_DIR / "carriers.csv"
    out: Dict[str, Carrier] = {}
    with open(p, "r") as f:
        for row in csv.DictReader(f):
            code = (row.get("iata") or "").strip().upper()
            if not code:
                continue
            out[code] = Carrier(
                iata=code,
                name=(row.get("name") or "").strip(),
                alliance=(row.get("alliance") or "none").strip(),
                country=(row.get("country") or "").strip().upper(),
            )
    return out


def airport(code: str) -> Airport:
    """Look up an airport. Raises UnknownAirportError naming the code."""
    if not code or not str(code).strip():
        raise UnknownAirportError(
            "No IATA code supplied. A leg with no origin/destination cannot be "
            "region-classified and its surcharge must resolve to unknown."
        )
    key = str(code).strip().upper()
    airports = _airports()
    if key not in airports:
        raise UnknownAirportError(
            f"Unknown IATA airport code {key!r}. Add it to data/airports.csv with "
            f"its country and region. It is NOT being defaulted to a region - a "
            f"wrong region selects a wrong surcharge band."
        )
    return airports[key]


def carrier(code: str) -> Carrier:
    """Look up a carrier. Raises UnknownCarrierError naming the code."""
    key = str(code or "").strip().upper()
    carriers = _carriers()
    if key not in carriers:
        raise UnknownCarrierError(
            f"Unknown IATA carrier code {key!r}. Add it to data/carriers.csv."
        )
    return carriers[key]


def carrier_name(code: str) -> str:
    """Carrier name, or the bare code if we do not know it. Never raises."""
    try:
        return carrier(code).name
    except UnknownCarrierError:
        return str(code or "").strip().upper() or "(unknown carrier)"


def region_of(code: str) -> str:
    return airport(code).region


def country_of(code: str) -> str:
    return airport(code).country


# Seats.aero states a region on every Route ("North America", "Europe"). Using
# the API's own classification beats re-deriving one from the IATA code: it is
# what the award was actually filed under. Anything not in this map resolves to
# None and the caller falls back to airports.csv - it is NEVER defaulted, because
# a wrong region silently picks a wrong surcharge band.
SEATS_AERO_REGIONS: Dict[str, str] = {
    "north america": "NA",
    "south america": "SA",
    "central america": "NA",
    "europe": "EU",
    "africa": "AF",
    "middle east": "ME",
    "asia": "AS",
    "oceania": "OC",
    "australia": "OC",
    "australia/new zealand": "OC",
}


def region_from_seats_aero(name: Optional[str]) -> Optional[str]:
    """Map a Seats.aero region label to a REGIONS code, or None if unrecognised."""
    if not name:
        return None
    return SEATS_AERO_REGIONS.get(str(name).strip().lower())


def normalize_pair(a: str, b: str) -> str:
    """Normalize two REGIONS codes into the unordered pair key, e.g. 'NA-EU'."""
    for r in (a, b):
        if r not in _REGION_ORDER:
            raise ValueError(f"{r!r} is not one of {REGIONS}.")
    return "-".join(sorted((a, b), key=lambda r: _REGION_ORDER[r]))


def classify(origin: str, destination: str) -> str:
    """
    Normalized UNORDERED region pair: classify("SFO","MAD") == classify("MAD","SFO").

    Same-region routes collapse to a doubled code: "NA-NA", "EU-EU".
    """
    a, b = region_of(origin), region_of(destination)
    return "-".join(sorted((a, b), key=lambda r: _REGION_ORDER[r]))


def departure_country(origin: str) -> str:
    """
    ISO-2 country of the departure point. DIRECTIONAL, unlike classify().

    UK APD applies to departures from the UK and is what makes ex-LHR the worst
    case in the surcharge model.
    """
    return country_of(origin)


def known_airports() -> List[str]:
    return sorted(_airports())


def known_carriers() -> List[str]:
    return sorted(_carriers())


def alliance_of(code: str) -> Optional[str]:
    try:
        alliance = carrier(code).alliance
    except UnknownCarrierError:
        return None
    return None if alliance in ("", "none") else alliance
