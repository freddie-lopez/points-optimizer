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

v5 STEP 7 WIRED IT IN, ASYMMETRICALLY, AND THE ASYMMETRY IS THE DESIGN.

  * On an OFFLINE or BADGE leg the tool owns the whole cash figure and knows
    APD is missing from it, so APD is ADDED and disclosed as its own line.
  * On a LIVE or REPLAYED leg the cash figure came from Seats.aero's
    TotalTaxes and NOBODY HAS CHECKED whether that field already contains APD.
    The amount is STATED and NOT ADDED. Adding it risks double-charging;
    omitting it silently risks understating; stating it without applying it is
    the only honest third option, and it turns the unknown into a one-row
    experiment somebody can run against a booking page.

The cost of that asymmetry is real and is written down in the plan's risk 9.4:
the same trip scores differently in two modes ON PURPOSE, and a reader who
compares them without reading the flag sees an inconsistency. It is unavoidable
until one live LHR-departure row settles it.

APD IS ADDED TO THE POINTS SIDE ONLY. A captured cash fare is a published fare
and already contains APD; adding it to both sides would double-count on the
cash side and cancel out of the margin, which would hide the whole effect.

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
from typing import Dict, List, Optional

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


# ===========================================================================
# v5 STEP 7: bands, and the per-leg charge
# ===========================================================================

DEFAULT_APD_BANDS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "apd_bands.csv"
)

# A band value of this in the CSV means "the row exists, and it deliberately
# does NOT state a band". It is NOT a band, it is a written-down unknown, and it
# resolves to None exactly the way a missing row does - but with a reason
# attached, which a missing row cannot carry.
BAND_UNKNOWN = "UNKNOWN"

# HMRC's own two rate names, and this project's reading of which cabin is which.
# The statute's test is SEAT PITCH (lowest class of travel, under 40 inches),
# not a fare bucket, so W is the row to revisit first: a premium-economy seat
# under 40in pitch is reduced-rate under the law and standard-rate here.
CABIN_TO_CLASS = {"Y": "reduced", "W": "standard", "J": "standard", "F": "standard"}

PITCH_CAVEAT = (
    "CABIN MAPPING CAVEAT: HMRC sets the reduced rate by SEAT PITCH (the lowest "
    "class of travel, pitch under 40 inches), not by cabin name. This leg is a "
    "W (premium economy) cabin and has been mapped to the STANDARD rate. A "
    "premium-economy seat with under 40in pitch is REDUCED-rate under the "
    "statute and would owe less than shown. W is the first row to revisit."
)


@dataclass(frozen=True)
class APDBand:
    """One destination country's band, with the fact that justified it."""

    destination_country: str
    band: Optional[str]  # None when the row says UNKNOWN
    capital: str
    distance_miles: Optional[int]
    source: str
    verified_on: Optional[date]
    notes: str = ""

    @property
    def is_known(self) -> bool:
        return self.band is not None


@dataclass
class APDBandTable:
    """destination country -> band. A country not here resolves to None."""

    bands: Dict[str, APDBand] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.bands)

    def lookup(self, country: str) -> Optional[APDBand]:
        return self.bands.get((country or "").strip().upper())

    def band_for_destination(self, country: str) -> Optional[str]:
        """
        The band, or None. NEVER a default and NEVER band A.

        An unlisted country is UNKNOWN, and a country listed as UNKNOWN is
        UNKNOWN with a reason. Defaulting to band A would make every
        unrecognised destination the cheapest possible tax, which is the
        absence-as-zero bug with a band name on it.
        """
        row = self.lookup(country)
        return row.band if row is not None else None

    def unknown_reason(self, country: str) -> str:
        """Why this country has no band, in words, for the rendered line."""
        code = (country or "").strip().upper() or "(no country)"
        row = self.lookup(code)
        if row is None:
            return (
                f"data/apd_bands.csv does not cover {code}. The band is UNKNOWN, "
                f"which is NOT band A and NOT zero. Add a row with the capital "
                f"and the London distance that justifies it."
            )
        return f"data/apd_bands.csv lists {code} as UNKNOWN. {row.notes}"


def load_apd_bands(path: Optional[Path] = None) -> APDBandTable:
    """Load and validate `data/apd_bands.csv`."""
    csv_path = Path(path) if path is not None else DEFAULT_APD_BANDS_PATH
    if not csv_path.exists():
        raise APDTableError(f"APD band table not found at {csv_path}.")

    bands: Dict[str, APDBand] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            country = (row.get("destination_country") or "").strip().upper()
            if not country:
                continue
            if country in bands:
                raise APDTableError(
                    f"line {lineno}: {country} appears twice. Two bands for one "
                    f"country would make the answer depend on row order."
                )
            raw_band = (row.get("band") or "").strip().upper()
            if not raw_band:
                raise APDTableError(
                    f"line {lineno}: {country} has an EMPTY band. Write "
                    f"{BAND_UNKNOWN} and say why in `notes` - a blank cell is "
                    f"indistinguishable from a typo, and an unknown tax must "
                    f"state that it is unknown."
                )
            if not (row.get("source") or "").strip():
                raise APDTableError(f"line {lineno}: {country} has no source.")
            band = None if raw_band == BAND_UNKNOWN else raw_band
            bands[country] = APDBand(
                destination_country=country,
                band=band,
                capital=(row.get("capital") or "").strip(),
                distance_miles=_parse_optional_int(row.get("distance_miles", "")),
                source=(row.get("source") or "").strip(),
                verified_on=(
                    _parse_date(row.get("verified_on", ""), "verified_on", lineno)
                    if (row.get("verified_on") or "").strip()
                    else None
                ),
                notes=(row.get("notes") or "").strip(),
            )
    if not bands:
        raise APDTableError("the APD band table is empty.")
    return APDBandTable(bands=bands)


@dataclass(frozen=True)
class APDCharge:
    """
    What one leg owes in APD, per passenger and in total, or why it is UNKNOWN.

    `is_known` False means UNKNOWN. It NEVER means zero, and `render()` never
    prints a dollar figure for an unknown charge - a test asserts the string
    "$0.00" does not appear on an unknown APD line.
    """

    leg_id: str
    departure_country: str
    destination_country: str
    cabin: str
    travelers: int
    band: Optional[str] = None
    rate: Optional[APDRate] = None
    band_row: Optional[APDBand] = None
    rate_gbp: Optional[float] = None
    total_gbp: Optional[float] = None
    total_usd: Optional[float] = None
    fx_marked: str = ""
    unknown_reason: str = ""
    # True on a LIVE or REPLAYED leg: the amount is STATED and NOT ADDED,
    # because nobody has checked whether Seats.aero's TotalTaxes contains it.
    inclusion_unverified: bool = False

    @property
    def is_known(self) -> bool:
        return self.total_usd is not None

    @property
    def applied_usd(self) -> float:
        """What actually entered the score. Zero when unknown OR when flagged."""
        if not self.is_known or self.inclusion_unverified:
            return 0.0
        return float(self.total_usd)

    @property
    def cabin_class(self) -> str:
        return CABIN_TO_CLASS.get(self.cabin.upper(), "standard")

    @property
    def shows_pitch_caveat(self) -> bool:
        return self.cabin.upper() == "W"

    def render(self) -> str:
        head = (
            f"UK AIR PASSENGER DUTY on {self.leg_id}: this leg DEPARTS "
            f"{self.departure_country} for {self.destination_country or '(unknown)'}."
        )
        if not self.is_known:
            return (
                f"{head} The amount is UNKNOWN - this table does not cover it. "
                f"{self.unknown_reason} UNKNOWN IS NOT ZERO: real cash is owed "
                f"and its size is not known here, so the points-side total below "
                f"is a LOWER BOUND."
            )
        # The GBP figure is printed beside the USD one so the conversion is
        # checkable, and the multiplication is shown rather than folded in
        # because APD is PER PASSENGER and Trip B's traveller counts are still
        # incoherent (plan section 8.1).
        arithmetic = (
            f"GBP {self.rate_gbp:,.2f} x {self.travelers} = "
            f"GBP {self.total_gbp:,.2f} = ${self.total_usd:,.2f}"
        )
        band_note = ""
        if self.band_row is not None:
            band_note = (
                f" Band {self.band} chosen because HMRC bands by distance from "
                f"London to the DESTINATION COUNTRY'S CAPITAL: "
                f"{self.band_row.capital}, {self.band_row.distance_miles} miles."
            )
        source = f" Source: {self.rate.source}." if self.rate is not None else ""
        caveat = f" {PITCH_CAVEAT}" if self.shows_pitch_caveat else ""
        if self.inclusion_unverified:
            return (
                f"{head} It owes {arithmetic} per the {self.cabin_class} rate."
                f"{band_note} IT IS NOT ADDED HERE: this leg's cash figure came "
                f"from Seats.aero's TotalTaxes and NOBODY HAS CHECKED whether "
                f"that field already includes APD. Adding it would risk "
                f"double-charging; omitting it risks understating. The figure is "
                f"stated so you can check it against a booking page - open the "
                f"same route, date and cabin on a real award booking page and "
                f"compare its taxes breakdown against the TotalTaxes above."
                f"{source}{caveat}{self.fx_marked}"
            )
        return (
            f"{head} ADDED to the points-side cash total: {arithmetic} per the "
            f"{self.cabin_class} rate.{band_note} APD is owed on award tickets "
            f"too and does not care which program or which airline; the captured "
            f"CASH fare already contains it, which is why it is added to the "
            f"points side only.{source}{caveat}{self.fx_marked}"
        )


def _fx_marker(currency: str, on: Optional[date] = None) -> str:
    """Propagate FX staleness onto the APD line rather than hiding it."""
    from src import config

    marks = []
    if config.is_placeholder_rate(currency):
        marks.append("[UNVERIFIED RATE]")
    if config.is_stale_rate(currency, on):
        marks.append("[STALE RATE]")
    if config.needs_confirmation(currency):
        marks.append("[CONFIRM BEFORE TRUSTING]")
    if not marks:
        return ""
    return (
        f" The GBP->USD conversion above is marked {' '.join(marks)}; this APD "
        f"figure inherits that uncertainty."
    )


def apd_for_leg(
    leg,
    cabin: str,
    travelers: int,
    on: Optional[date] = None,
    *,
    rates: Optional[APDTable] = None,
    bands: Optional[APDBandTable] = None,
    inclusion_unverified: bool = False,
    today: Optional[date] = None,
) -> Optional[APDCharge]:
    """
    What this leg owes in APD, or None if APD does not apply to it at all.

    RETURNS None FOR ANY NON-GB DEPARTURE, and None means NOTHING - not zero,
    and no field is set on the leg. A leg that does not depart the UK does not
    owe UK departure tax and must not carry an APD line saying "$0.00", which
    would be a claim rather than an absence.

    Returns an APDCharge with `is_known` False when the departure IS from the
    UK and either lookup fails. That is a written-down UNKNOWN and it renders
    as one.
    """
    from src import config, regions

    if leg is None or getattr(leg, "kind", "") != "flight":
        return None
    origin = (getattr(leg, "origin", "") or "").strip().upper()
    destination = (getattr(leg, "destination", "") or "").strip().upper()
    if not origin or not destination:
        return None
    try:
        departure_country = regions.departure_country(origin)
    except regions.UnknownAirportError:
        return None
    if departure_country != "GB":
        return None

    rates = rates if rates is not None else load_apd_table()
    bands = bands if bands is not None else load_apd_bands()
    when = on or getattr(leg, "date", None) or date.today()
    cabin = (cabin or "Y").strip().upper()
    travelers = max(int(travelers or 1), 1)

    try:
        destination_country = regions.country_of(destination)
    except regions.UnknownAirportError:
        destination_country = ""

    base = dict(
        leg_id=getattr(leg, "id", ""),
        departure_country=departure_country,
        destination_country=destination_country,
        cabin=cabin,
        travelers=travelers,
        inclusion_unverified=inclusion_unverified,
    )

    band = bands.band_for_destination(destination_country)
    if band is None:
        return APDCharge(
            **base, unknown_reason=bands.unknown_reason(destination_country)
        )

    cabin_class = CABIN_TO_CLASS.get(cabin, "standard")
    rate = rates.lookup(departure_country, band, cabin_class, when)
    if rate is None:
        return APDCharge(
            **base,
            band=band,
            band_row=bands.lookup(destination_country),
            unknown_reason=(
                f"data/apd.csv has no {departure_country} band {band} "
                f"{cabin_class} rate active on {when}. The published rate "
                f"periods do not cover that date, so the amount is UNKNOWN - "
                f"not the nearest period's rate, and not zero."
            ),
        )

    total_gbp = rate.rate * travelers
    return APDCharge(
        **base,
        band=band,
        band_row=bands.lookup(destination_country),
        rate=rate,
        rate_gbp=rate.rate,
        total_gbp=total_gbp,
        total_usd=config.convert_to_usd(total_gbp, rate.currency),
        fx_marked=_fx_marker(rate.currency, today),
    )
