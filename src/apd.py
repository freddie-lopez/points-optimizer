"""
UK Air Passenger Duty: a GOVERNMENT TAX, deliberately kept out of surcharges.csv.

WHY THIS IS A SEPARATE TABLE AND A SEPARATE MODULE
==================================================
APD and a carrier-imposed surcharge are different quantities with different
payers, different rules and different reasons to be unknown:

  * A carrier surcharge (YQ/YR) is a PROGRAM x OPERATING METAL property. United
    charges none; BA charges hundreds on the same metal Iberia Plus prices at a
    fraction. It varies by who you booked through.
  * APD is a ROUTE-DEPARTURE property. It is charged on every passenger
    DEPARTING a UK airport, on award tickets too, and it does NOT care which
    program or which airline. United's zero-YQ policy does not exempt you from
    it.

Conflating them is how "United charges no surcharge" would silently become
"this leg costs nothing in cash", which is the same shape as v0's phantom $0.
So APD lives in `data/apd.csv` with its own keys and its own loader, and
`SurchargeTable` never sees it.

NOT WIRED INTO SCORING. This module loads and validates the data and nothing
else. Applying APD changes every UK-departure verdict in the tool - by roughly
$138 on Trip B's LHR->SFO leg alone, which would cut that leg's reported $202
saving to about $64 - and a change that size needs a plan, not a drive-by. That
is v5's job. Landing the DATA now, separately and honestly, is what lets v5
start from a sourced table instead of from research notes.

TWO THINGS THAT ARE NOT YET KNOWN, and must not be assumed by whoever wires it:

  1. WHETHER LIVE `TotalTaxes` ALREADY INCLUDES APD. Seats.aero's tax figure
     probably does. If it does and this table is added on top, every UK
     departure is double-counted. NOBODY HAS CHECKED - there has never been a
     live LHR-departure row. Until one arrives and someone compares it against a
     real booking page, a UK-departure live row must be flagged "APD inclusion
     unverified" rather than adjusted in either direction.
  2. THE CABIN MAPPING IS AN INTERPRETATION, NOT THE STATUTE. HMRC defines the
     reduced rate by SEAT PITCH (lowest class of travel, pitch under 40 inches),
     not by a fare bucket. `cabins` in the CSV records this project's reading -
     reduced = Y, standard = W/J/F - as data, so it can be corrected in the CSV
     rather than in code. A premium-economy seat with under 40in pitch would be
     reduced-rate under the statute and standard-rate under our mapping.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

DEFAULT_APD_PATH = Path(__file__).resolve().parent.parent / "data" / "apd.csv"

# The government's own two rate names. A row that is neither is a row this
# loader does not understand, and it says so rather than defaulting.
CABIN_CLASSES = ("reduced", "standard")


class APDTableError(Exception):
    """The APD table is malformed. Never swallowed: a wrong tax is a wrong total."""


@dataclass(frozen=True)
class APDRate:
    """One published rate, for one band and cabin class, over one date window."""

    departure_country: str
    band: str
    cabin_class: str
    rate: float
    currency: str
    effective_from: date
    effective_to: date
    source: str
    verified_on: date
    distance_min_miles: Optional[int] = None
    distance_max_miles: Optional[int] = None
    cabins: List[str] = field(default_factory=list)
    notes: str = ""

    def is_active_on(self, check_date: date) -> bool:
        return self.effective_from <= check_date <= self.effective_to

    def describe(self) -> str:
        return (
            f"APD {self.departure_country} band {self.band} "
            f"{self.cabin_class} ({self.effective_from}..{self.effective_to})"
        )


@dataclass
class APDTable:
    """Every published APD rate. A lookup returns None rather than 0.0."""

    rates: List[APDRate] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rates)

    def lookup(
        self, departure_country: str, band: str, cabin_class: str, on: date
    ) -> Optional[APDRate]:
        """
        The rate for one (country, band, cabin class) on one date, or None.

        RETURNS None, NEVER 0.0. A missing rate means this table does not know
        what is owed - not that nothing is owed. £0 and "unknown" are the exact
        pair this project has confused before, and a tax is the worst place to
        repeat it.
        """
        country = (departure_country or "").strip().upper()
        want_band = (band or "").strip().upper()
        want_cabin = (cabin_class or "").strip().lower()
        for r in self.rates:
            if (
                r.departure_country == country
                and r.band == want_band
                and r.cabin_class == want_cabin
                and r.is_active_on(on)
            ):
                return r
        return None

    def validate(self) -> None:
        """
        Reject a table that cannot be trusted. Called by the loader.

        Every check here exists because the corresponding mistake would produce a
        confident wrong number rather than a visible failure.
        """
        if not self.rates:
            raise APDTableError("the APD table is empty.")
        for r in self.rates:
            if r.rate < 0:
                raise APDTableError(f"{r.describe()}: negative rate {r.rate}.")
            if not r.source:
                raise APDTableError(f"{r.describe()}: no source.")
            if r.cabin_class not in CABIN_CLASSES:
                raise APDTableError(
                    f"{r.describe()}: cabin_class must be one of "
                    f"{CABIN_CLASSES}, got {r.cabin_class!r}. These are HMRC's own "
                    f"two rate names; a third value is a row this loader cannot "
                    f"read and must not silently ignore."
                )
            if r.effective_from > r.effective_to:
                raise APDTableError(
                    f"{r.describe()}: effective_from is after effective_to, so "
                    f"the row is active on no date at all."
                )
        # An overlap would make the answer depend on CSV row order, which is not
        # a property anybody should have to know about.
        seen = {}
        for r in self.rates:
            key = (r.departure_country, r.band, r.cabin_class)
            for other in seen.get(key, []):
                if (
                    r.effective_from <= other.effective_to
                    and other.effective_from <= r.effective_to
                ):
                    raise APDTableError(
                        f"{r.describe()} overlaps {other.describe()}. Two rates "
                        f"active on the same date for the same band and cabin "
                        f"would make the answer depend on row order."
                    )
            seen.setdefault(key, []).append(r)


def _parse_date(value: str, what: str, lineno: int) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError) as e:
        raise APDTableError(f"line {lineno}: bad {what} {value!r}: {e}") from e


def _parse_optional_int(value: str) -> Optional[int]:
    """Blank means NO BOUND, which is why it is None and not 0."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as e:
        raise APDTableError(f"bad distance {value!r}: {e}") from e


def load_apd_table(path: Optional[Path] = None) -> APDTable:
    """
    Load and validate `data/apd.csv`.

    Raises APDTableError on a malformed table rather than returning a partial
    one. A tax table that quietly drops the rows it could not read is worse than
    no table: the total still comes out, and it comes out too low.
    """
    csv_path = Path(path) if path is not None else DEFAULT_APD_PATH
    if not csv_path.exists():
        raise APDTableError(f"APD table not found at {csv_path}.")

    rates: List[APDRate] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("band") or "").strip():
                continue
            try:
                rate = float((row.get("rate") or "").strip())
            except ValueError as e:
                raise APDTableError(
                    f"line {lineno}: bad rate {row.get('rate')!r}: {e}"
                ) from e
            rates.append(
                APDRate(
                    departure_country=(row.get("departure_country") or "").strip().upper(),
                    band=(row.get("band") or "").strip().upper(),
                    cabin_class=(row.get("cabin_class") or "").strip().lower(),
                    rate=rate,
                    currency=(row.get("currency") or "GBP").strip().upper(),
                    effective_from=_parse_date(
                        row.get("effective_from", ""), "effective_from", lineno
                    ),
                    effective_to=_parse_date(
                        row.get("effective_to", ""), "effective_to", lineno
                    ),
                    source=(row.get("source") or "").strip(),
                    verified_on=_parse_date(
                        row.get("verified_on", ""), "verified_on", lineno
                    ),
                    distance_min_miles=_parse_optional_int(row.get("distance_min_miles", "")),
                    distance_max_miles=_parse_optional_int(row.get("distance_max_miles", "")),
                    cabins=[
                        c.strip().upper()
                        for c in (row.get("cabins") or "").split(",")
                        if c.strip()
                    ],
                    notes=(row.get("notes") or "").strip(),
                )
            )

    table = APDTable(rates=rates)
    table.validate()
    return table
