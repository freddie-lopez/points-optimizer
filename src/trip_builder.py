"""
Building a trip fixture from the command line, and the three things it refuses.

WHY A BUILDER EXISTS
====================
Until v5 a new trip meant hand-writing JSON, and hand-written JSON is how Trip
B's B1 came to say "MRY->MAD" in its description while carrying `"origin":
"SFO"` in its code. That drift survived four versions because the description
was written by a human and the airport code by another human on a different day,
and nothing ever compared them.

THE THREE REFUSALS, EACH WITH A CAUTIONARY TALE BEHIND IT
=========================================================

1. NO `points_candidates` KEY IS WRITTEN AT ALL. Not an empty list with a
   comment - the key is absent. A fixture built here is therefore scoreable ONLY
   against live award space or a replayed snapshot, which is the whole point: a
   hand-written badge number is exactly the thing v3 existed to stop quoting. An
   empty list is a thing a future editor fills in; a missing key plus a
   `trip_level_flags` sentence is a decision, and the sentence survives being
   read by a human six months later.

2. THE DESCRIPTION IS GENERATED FROM THE CODES. `origin` and `destination` come
   from the flag, and the description is derived from them, so the two cannot
   disagree - only one of them is input. This is B1's bug made unexpressible.

3. AN UNKNOWN IATA CODE IS REFUSED, NOT PASSED THROUGH. Validated against
   `data/airports.csv` via `regions.airport()`, which already raises with a
   message about wrong regions selecting wrong surcharge bands. NO "DID YOU
   MEAN" IS OFFERED. A near-miss suggestion on an airport code is precisely how
   MRY becomes SFO.

EVERY VALIDATION BELOW IS A REFUSAL, NEVER A REPAIR. The one number this module
writes is the cash amount the user typed. Its whole exposure to this project's
failure-as-finding class is that a malformed input could be silently COERCED
into a plausible fixture - a date that parses to the wrong day, a cash amount
that rounds, a two-letter code that resolves to something. So nothing here
guesses, and nothing here corrects except case.

It imports `regions` and writes JSON. It does not import `optimizer`, the
parser, or anything in the surcharge path.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src import regions

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "trips"

CABINS = {
    "Y": "economy",
    "W": "premium economy",
    "J": "business",
    "F": "first",
}

# The sentence that travels INSIDE the fixture, so the refusal is legible to a
# human reading the file with no access to this module.
LIVE_ONLY_FLAG = (
    "THIS FIXTURE CARRIES NO POINTS PRICES AT ALL. It was built by --new-trip, "
    "which refuses to write a points_candidates key - not an empty one, none. "
    "The only way any leg here can be scored on points is --live (real "
    "Seats.aero award space) or --from-snapshot (a committed replay of one). A "
    "hand-written points price is a badge number wearing a capture's clothes, "
    "and quoting those is the bug this whole project exists to stop. If you add "
    "a points_candidates key by hand, this sentence is no longer true of this "
    "file and should come out with it."
)

# A fixture NAME becomes a filename. It must not be able to escape the fixtures
# directory: `--new-trip ../../../etc/whatever` is not a trip name.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# FINDING M-3. A NAME THE APP CANNOT ADDRESS IS NOT A USABLE NAME. There was no
# length limit at all: a 200-character name was written and then 404ed, because
# the local UI addresses a trip through `/api/trips/<id>`, whose route allows
# 121 characters; a ~300-character one raised ENAMETOOLONG from the filesystem
# and surfaced as an unexpected error rather than a refusal. The limit is the
# smallest thing in that chain, stated, and checked before anything is written.
MAX_NAME_LENGTH = 120


class TripBuilderError(ValueError):
    """A refusal. NO FILE IS EVER WRITTEN when this is raised."""


# ---------------------------------------------------------------------------
# Validators. ONE COPY OF EACH.
# ---------------------------------------------------------------------------
#
# The flags path and the interactive path both call these functions. A refusal
# that existed on one and not the other would mean the tool's honesty depended
# on which way you invoked it.


def validate_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise TripBuilderError("--new-trip needs a NAME.")
    if len(text) > MAX_NAME_LENGTH:
        raise TripBuilderError(
            f"--new-trip: that name is {len(text)} characters. The longest name "
            f"this tool can address is {MAX_NAME_LENGTH}: a longer one is written "
            f"and then cannot be opened or run, which is worse than refusing it. "
            f"No file has been written."
        )
    if not _SAFE_NAME.match(text) or ".." in text:
        raise TripBuilderError(
            f"--new-trip {name!r} is not a usable fixture name. Use letters, "
            f"digits, '_', '-' and '.', starting with a letter or digit. A name "
            f"containing a path separator or '..' would write outside "
            f"{FIXTURE_DIR}, which a trip name has no business doing."
        )
    return text


def validate_iata(code: str, flag: str) -> str:
    """
    An airport code that `data/airports.csv` knows. Case is corrected; nothing
    else is. NO SUGGESTION IS OFFERED - see the module docstring.
    """
    text = (code or "").strip().upper()
    if len(text) != 3:
        raise TripBuilderError(
            f"{flag}: {code!r} is not a 3-letter IATA airport code (it is "
            f"{len(text)} characters). Refused rather than padded or truncated."
        )
    try:
        regions.airport(text)
    except regions.UnknownAirportError as e:
        raise TripBuilderError(
            f"{flag}: {text!r} is not an airport this tool knows. {e} No "
            f"correction is being suggested: a near-miss suggestion on an "
            f"airport code is how MRY becomes SFO. No file has been written."
        ) from None
    return text


def validate_date(value: str, flag: str, today: Optional[date] = None) -> date:
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise TripBuilderError(
            f"{flag}: {value!r} is not an ISO date (YYYY-MM-DD), or names a day "
            f"that does not exist. Refused rather than nudged to a nearby date."
        ) from None
    if parsed < (today or date.today()):
        raise TripBuilderError(
            f"{flag}: {parsed} is in the past. A trip you cannot take is not a "
            f"trip this tool can price, and Seats.aero will not have award space "
            f"for it."
        )
    return parsed


def validate_cash(value: str, flag: str) -> float:
    text = str(value or "").strip()
    try:
        amount = float(text)
    except ValueError:
        raise TripBuilderError(
            f"{flag}: {value!r} is not a number. A cash price is the one figure "
            f"this builder writes and it is not being guessed."
        ) from None
    # M-1: refused HERE, before anything is written, and by the same rule the
    # loader applies - so a fixture this builder writes can always be scored.
    from src import config

    unscoreable = config.unscoreable_cash_reason(amount)
    if unscoreable:
        raise TripBuilderError(
            f"{flag}: {unscoreable} Refused rather than written into a fixture "
            f"that every later run would crash on."
        )
    if amount <= 0:
        raise TripBuilderError(
            f"{flag}: a cash price of {amount} is refused. Zero is not a price - "
            f"it is silence, and this project has confused the two before. Omit "
            f"the leg, or capture the real fare."
        )
    return amount


def validate_travelers(value) -> int:
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripBuilderError(f"--travelers: {value!r} is not a whole number.") from None
    if count < 1:
        raise TripBuilderError(
            f"--travelers {count} is refused. A trip with no travellers has no "
            f"cost, and APD is charged PER PASSENGER, so the count is load-bearing."
        )
    # R2-3: the count multiplies every money figure on the leg, so it has to
    # survive the same round trip the money does - checked here, before
    # anything is written, and again at load.
    from src import config

    unscoreable = config.unscoreable_count_reason(count)
    if unscoreable:
        raise TripBuilderError(f"--travelers: {unscoreable}")
    return count


def validate_cabin(value: str) -> str:
    text = (value or "").strip().upper()
    if text not in CABINS:
        raise TripBuilderError(
            f"--cabin {value!r} is not one of {'/'.join(CABINS)}. These are the "
            f"four cabins the surcharge and APD tables are keyed on."
        )
    return text


def validate_nights(value, flag: str) -> int:
    try:
        nights = int(str(value).strip())
    except (TypeError, ValueError):
        raise TripBuilderError(f"{flag}: {value!r} is not a whole number of nights.") from None
    if nights < 1:
        raise TripBuilderError(
            f"{flag}: {nights} nights is refused. A zero-night stay is not a "
            f"hotel leg."
        )
    from src import config

    unscoreable = config.unscoreable_count_reason(nights)
    if unscoreable:
        raise TripBuilderError(f"{flag}: {unscoreable}")
    return nights


# ---------------------------------------------------------------------------
# Parsed inputs
# ---------------------------------------------------------------------------


@dataclass
class FlightSpec:
    origin: str
    destination: str
    date: date
    cash_usd: float
    # This leg's cabin, when it differs from the trip's. None = the trip's
    # cabin. No CLI path sets it (--new-trip takes one --cabin for every leg),
    # so every CLI-built fixture is unchanged; the local UI's form sets it.
    cabin: Optional[str] = None


@dataclass
class HotelSpec:
    name: str
    date: date
    nights: int
    cash_usd: float


def parse_leg_flag(raw: str, today: Optional[date] = None) -> FlightSpec:
    """`ORIGIN:DEST:YYYY-MM-DD:CASH_USD`. Exactly four fields."""
    parts = str(raw or "").split(":")
    if len(parts) != 4:
        raise TripBuilderError(
            f"--leg {raw!r} has {len(parts)} colon-separated fields; it needs "
            f"exactly 4: ORIGIN:DEST:YYYY-MM-DD:CASH_USD."
        )
    origin, destination, when, cash = parts
    origin = validate_iata(origin, "--leg ORIGIN")
    destination = validate_iata(destination, "--leg DEST")
    if origin == destination:
        raise TripBuilderError(
            f"--leg {raw!r}: origin and destination are both {origin}. A leg that "
            f"goes nowhere has no fare and no award space."
        )
    return FlightSpec(
        origin=origin,
        destination=destination,
        date=validate_date(when, "--leg DATE", today),
        cash_usd=validate_cash(cash, "--leg CASH_USD"),
    )


def parse_hotel_flag(raw: str, today: Optional[date] = None) -> HotelSpec:
    """
    `NAME:CHECKIN:NIGHTS:CASH_USD`. Split from the RIGHT, so a hotel called
    "Hotel Kyoto: Gion" keeps its colon instead of becoming a field-count error.
    """
    parts = str(raw or "").rsplit(":", 3)
    if len(parts) != 4:
        raise TripBuilderError(
            f"--hotel {raw!r} has {len(parts)} colon-separated fields; it needs "
            f"at least 4: NAME:CHECKIN:NIGHTS:CASH_USD."
        )
    name, when, nights, cash = parts
    if not name.strip():
        raise TripBuilderError(f"--hotel {raw!r}: the hotel has no name.")
    return HotelSpec(
        name=name.strip(),
        date=validate_date(when, "--hotel CHECKIN", today),
        nights=validate_nights(nights, "--hotel NIGHTS"),
        cash_usd=validate_cash(cash, "--hotel CASH_USD"),
    )


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


def _pretty_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day} {d.year}"


def describe_flight(spec: FlightSpec, travelers: int, cabin: str) -> str:
    """
    THE DESCRIPTION IS DERIVED, NEVER TYPED.

    Trip B's B1 says "MRY->MAD" and is coded SFO->MAD. Here the codes are the
    only input and this string is a function of them, so the two cannot drift.
    """
    who = "1 adult" if travelers == 1 else f"{travelers} adults"
    return (
        f"{spec.origin}->{spec.destination}, {_pretty_date(spec.date)}, "
        f"one-way, {CABINS[cabin]}, {who}"
    )


def build_fixture(
    name: str,
    flights: List[FlightSpec],
    hotels: List[HotelSpec],
    travelers: int,
    cabin: str,
    today: Optional[date] = None,
) -> Dict:
    """The fixture dict. NOTE WHAT IS ABSENT: any `points_candidates` key."""
    if not flights and not hotels:
        raise TripBuilderError(
            "--new-trip needs at least one --leg or --hotel. An empty trip has "
            "nothing to score and writing the file would suggest otherwise."
        )
    captured = str(today or date.today())
    legs: List[Dict] = []

    for i, spec in enumerate(flights, start=1):
        leg_cabin = spec.cabin or cabin
        legs.append(
            {
                "id": f"L{i}",
                "kind": "flight",
                "description": describe_flight(spec, travelers, leg_cabin),
                "date": str(spec.date),
                "origin": spec.origin,
                "destination": spec.destination,
                "travelers": travelers,
                "cabin": leg_cabin,
                "cash_options": [
                    {
                        "label": (
                            f"{spec.origin}->{spec.destination} "
                            f"{_pretty_date(spec.date)}, entered by the user"
                        ),
                        "amount": spec.cash_usd,
                        "currency": "USD",
                        "source": "user_entered_via_new_trip",
                        "captured_on": captured,
                        "date": str(spec.date),
                    }
                ],
                # NO points_candidates KEY. See the module docstring.
            }
        )

    for i, spec in enumerate(hotels, start=1):
        legs.append(
            {
                "id": f"H{i}",
                "kind": "hotel",
                # Hotels get NO origin/destination. Seats.aero is flights-only,
                # and a hotel leg carrying airport codes would be QUERIED.
                "description": (
                    f"{spec.name}, {_pretty_date(spec.date)}, {spec.nights} "
                    f"night(s), {travelers} traveller(s)"
                ),
                "date": str(spec.date),
                "nights": spec.nights,
                "travelers": travelers,
                "cash_options": [
                    {
                        "label": f"{spec.name}, entered by the user",
                        "amount": spec.cash_usd,
                        "currency": "USD",
                        "source": "user_entered_via_new_trip",
                        "captured_on": captured,
                        "date": str(spec.date),
                    }
                ],
            }
        )

    leg_cabins = [spec.cabin or cabin for spec in flights]
    if len(set(leg_cabins)) <= 1:
        shown = leg_cabins[0] if leg_cabins else cabin
        cabin_text = f"cabin {shown} ({CABINS[shown]})"
    else:
        cabin_text = "cabins by leg: " + ", ".join(
            f"L{i} {c}" for i, c in enumerate(leg_cabins, start=1)
        )
    return {
        "id": name,
        "name": name,
        "description": (
            f"Built by --new-trip on {captured}: {len(flights)} flight leg(s), "
            f"{len(hotels)} hotel leg(s), {travelers} traveller(s), {cabin_text}."
        ),
        "source": (
            "user_entered_via_new_trip. Cash prices are what the user typed; "
            "nothing here was captured from a booking page by this tool."
        ),
        "trip_level_flags": [LIVE_ONLY_FLAG],
        "legs": legs,
    }


def write_fixture(
    fixture: Dict,
    directory: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Write the fixture and PROVE IT LOADS before reporting success.

    Risk 9.6: `tests/fixtures/trips/` is a test directory that is now also a
    user-data directory, so a fixture that fails to load breaks the suite for
    reasons unrelated to the code. The builder loads its own output back and
    deletes the file if it cannot, so a broken fixture is never left behind.
    """
    from src.trip_loader import load_trip_fixture

    directory = Path(directory or FIXTURE_DIR)
    path = directory / f"{fixture['id']}.json"
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(fixture, indent=2) + "\n"
    # FINDING M-2. EXCLUSIVE CREATE, not "check then write". A one-shot CLI
    # cannot race itself; the local UI is a threaded server, and two creates of
    # one name both passed the exists() check and both reported "Wrote ...",
    # with one trip silently overwriting the other. The kernel decides who wins.
    if force:
        path.write_text(body)
    else:
        try:
            with open(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644),
                      "w", encoding="utf-8") as f:
                f.write(body)
        except FileExistsError:
            raise TripBuilderError(
                f"{path} already exists and --force was not given. Refusing to "
                f"overwrite: the file may hold captures nobody can reproduce."
            ) from None
        except OSError as e:
            raise TripBuilderError(
                f"{path} could not be written ({e.strerror}). Nothing was written."
            ) from None

    try:
        load_trip_fixture(path)
    except Exception as e:  # noqa: BLE001 - never leave an unloadable fixture
        path.unlink(missing_ok=True)
        raise TripBuilderError(
            f"the fixture this builder wrote could not be loaded back "
            f"({type(e).__name__}: {e}), so it has been DELETED rather than left "
            f"in a directory the test suite reads. This is a bug in the builder."
        ) from None
    return path


def new_trip_from_flags(
    name: str,
    leg_flags: List[str],
    hotel_flags: List[str],
    travelers: int = 1,
    cabin: str = "Y",
    directory: Optional[Path] = None,
    force: bool = False,
    today: Optional[date] = None,
) -> Path:
    """
    The whole flags path. Every input is validated BEFORE anything is written.

    Ordering matters: a run that wrote three good legs and then refused the
    fourth would leave a partial fixture on disk under a name the user believes
    describes their trip.
    """
    name = validate_name(name)
    travelers = validate_travelers(travelers)
    cabin = validate_cabin(cabin)
    flights = [parse_leg_flag(raw, today) for raw in (leg_flags or [])]
    hotels = [parse_hotel_flag(raw, today) for raw in (hotel_flags or [])]
    fixture = build_fixture(name, flights, hotels, travelers, cabin, today)
    return write_fixture(fixture, directory, force)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------
#
# A THIN SEAM ON PURPOSE. `_prompt` takes the reader function as an argument so
# tests drive a whole session from a scripted list, which is the only way an
# interactive path gets tested at all. The alternative was a dependency
# (prompt_toolkit, questionary) for a prompt loop; requirements.txt stays at
# three lines.
#
# THE VALIDATORS ARE THE SAME FUNCTIONS THE FLAGS PATH CALLS. Not equivalent
# ones - the same ones. A refusal that existed on one path and not the other
# would make the tool's honesty depend on how you invoked it, and a test asserts
# the two paths produce byte-identical files.

MAX_PROMPT_ATTEMPTS = 3


def _prompt(
    question: str,
    validate: Callable[[str], object],
    read: Callable[[str], str],
    write: Callable[[str], None] = print,
    attempts: int = MAX_PROMPT_ATTEMPTS,
):
    """
    Ask until the answer validates, then give up. NEVER loops forever.

    A bad answer RE-PROMPTS rather than aborting - a typo in leg four should
    not throw away legs one to three. Three bad answers in a row abort, because
    a prompt that cannot be satisfied and will not stop is worse than one that
    quits and says why.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        answer = read(f"{question}\n> ")
        try:
            return validate(answer)
        except TripBuilderError as e:
            last = str(e)
            write(f"  refused: {last}")
            if attempt < attempts:
                write(f"  {attempts - attempt} attempt(s) left.")
    raise TripBuilderError(
        f"{attempts} invalid answers in a row for {question!r}. Giving up rather "
        f"than looping. Nothing has been written. Last refusal: {last}"
    )


def interactive_session(
    read: Callable[[str], str],
    write: Callable[[str], None] = print,
    today: Optional[date] = None,
):
    """
    Walk a user through a trip. Returns the same tuple the flags path builds.

    Blank ends each repeating section. Blank is NOT a validated value - it is
    an end-of-list marker checked before any validator runs, so "" can never be
    coerced into a leg.
    """
    name = _prompt("Trip name (letters, digits, _ - .)", validate_name, read, write)
    travelers = _prompt("Travellers on every leg", validate_travelers, read, write)
    cabin = _prompt(
        f"Cabin for every flight leg ({'/'.join(CABINS)})", validate_cabin, read, write
    )

    flights: List[FlightSpec] = []
    while True:
        answer = read(
            f"Flight leg {len(flights) + 1} as ORIGIN:DEST:YYYY-MM-DD:CASH_USD "
            f"(blank to finish)\n> "
        )
        if not answer.strip():
            break
        try:
            flights.append(parse_leg_flag(answer, today))
        except TripBuilderError as e:
            write(f"  refused: {e}")
            flights.append(
                _prompt(
                    f"Flight leg {len(flights) + 1}, again",
                    lambda a: parse_leg_flag(a, today),
                    read,
                    write,
                    attempts=MAX_PROMPT_ATTEMPTS - 1,
                )
            )

    hotels: List[HotelSpec] = []
    while True:
        answer = read(
            f"Hotel leg {len(hotels) + 1} as NAME:CHECKIN:NIGHTS:CASH_USD "
            f"(blank to finish)\n> "
        )
        if not answer.strip():
            break
        try:
            hotels.append(parse_hotel_flag(answer, today))
        except TripBuilderError as e:
            write(f"  refused: {e}")
            hotels.append(
                _prompt(
                    f"Hotel leg {len(hotels) + 1}, again",
                    lambda a: parse_hotel_flag(a, today),
                    read,
                    write,
                    attempts=MAX_PROMPT_ATTEMPTS - 1,
                )
            )

    return name, flights, hotels, travelers, cabin


def echo_lines(fixture: Dict) -> List[str]:
    """
    What is about to be written, read back to the user before the write.

    The cash amounts are the only numbers here and they came from a keyboard.
    Echoing them is the cheapest possible guard against a typo becoming a
    fixture somebody scores a trip against six months later.
    """
    lines = [f"About to write {fixture['id']}.json:"]
    for leg in fixture["legs"]:
        cash = leg["cash_options"][0]
        lines.append(
            f"  {leg['id']}  {leg['description']}  ->  "
            f"${cash['amount']:,.2f} {cash['currency']}"
        )
    lines.append(
        "  points_candidates: NONE ON ANY LEG. This trip is scoreable only with "
        "--live or --from-snapshot."
    )
    return lines
