"""Load multi-leg trip fixtures from JSON."""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List

from src.models import CashOption, Leg, MandatoryFee, PointsCandidate, PointsProvenance


def _finite_amount(value, what: str) -> float:
    """
    A money amount that is a real, finite, SCOREABLE number - or a
    TripFixtureError.

    M-1: a fixture written before this rule existed (or by hand) can carry an
    amount whose points-equivalent overflows. That used to reach the scorer and
    come back as an OverflowError traceback; it is now a load refusal, which
    every caller already renders as one clean line - "CANNOT LOAD" in the UI's
    trip list, `Error: ...` and exit 1 from the CLI.
    """
    from src import config

    try:
        amount = float(value)
    except (TypeError, ValueError) as e:
        raise TripFixtureError(f"{what} is {value!r}, which is not a number.") from e
    unscoreable = config.unscoreable_cash_reason(amount)
    if unscoreable:
        raise TripFixtureError(f"{what} is {value!r}: {unscoreable}")
    return amount


def _count(value, what: str, default: int = 0) -> int:
    """
    A whole-number field that reaches arithmetic - a points price, a night
    count, a points-per-night - or a TripFixtureError.

    R2-3: M-1 bounded money and nothing else, so `{"points": 10 ** 400}` loaded
    happily and then raised `OverflowError: int too large to convert to float`
    inside the scorer: a traceback from the CLI and a 500 from the UI, the exact
    class M-1 claimed to close. Same mechanical rule as the money one, and it is
    not a ceiling on award prices: it refuses only a figure no run could ever
    put on the scale beside a fare.
    """
    from src import config

    if value is None or value == "":
        value = default
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        raise TripFixtureError(f"{what} is {value!r}, which is not a whole number.")
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError) as e:
        raise TripFixtureError(
            f"{what} is {value!r}, which is not a whole number."
        ) from e
    unscoreable = config.unscoreable_count_reason(count)
    if unscoreable:
        raise TripFixtureError(f"{what} is {value!r}: {unscoreable}")
    return count


def _currency(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TripFixtureError(f"{what} is {value!r}, which is not a currency code.")
    return value.strip().upper()


def _strict_date(value, what: str) -> "date | None":
    """
    Absent -> None. Present but unreadable -> TripFixtureError.

    For a cash option's `date` - the travel date a price is FOR - None means
    "the leg's own date". A malformed value ("2027-02-30", "tomorrow") silently
    becoming None therefore became a CLAIM: a fare for some other day scored as
    the own-date fare. An unreadable date is refused, not reinterpreted.
    """
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as e:
        raise TripFixtureError(f"{what} is {value!r}, which is not an ISO date.") from e


def _travelers(value, leg_id) -> int:
    """A party size of at least one whole traveller - or a TripFixtureError."""
    from src import config

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TripFixtureError(f"leg {leg_id!r} travelers is {value!r}, not a count.")
    try:
        n = int(value)
    except ValueError as e:
        raise TripFixtureError(f"leg {leg_id!r} travelers is {value!r}, not a count.") from e
    if n < 1 or str(value).strip() != str(n):
        raise TripFixtureError(
            f"leg {leg_id!r} travelers is {value!r}; a leg is for at least 1 traveller."
        )
    # R2-3: the count multiplies every money figure on the leg (and APD is
    # charged per passenger), so it reaches arithmetic like the rest.
    unscoreable = config.unscoreable_count_reason(n)
    if unscoreable:
        raise TripFixtureError(f"leg {leg_id!r} travelers is {value!r}: {unscoreable}")
    return n


def _maybe_date(value) -> "date | None":
    """A date if the fixture supplied one, else None. Never a guessed date."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _leg_cash_provenance(cash_options: List[CashOption]) -> str:
    """
    One provenance for the leg's cash side, or "unknown" if they disagree.

    Deliberately pessimistic. If three options are screenshots and one is of
    unrecorded origin, the leg's cash side is not "captured_screenshot" - the
    cheapest option is the one that gets scored and it might be the unrecorded
    one.
    """
    sources = {c.source or "unknown" for c in cash_options}
    if len(sources) == 1:
        return sources.pop()
    return "unknown"


@dataclass
class TripFixture:
    """A multi-leg trip loaded from a JSON fixture."""

    id: str
    name: str
    description: str
    source: str
    legs: List[Leg] = field(default_factory=list)
    trip_level_flags: List[str] = field(default_factory=list)


class TripFixtureError(ValueError):
    """
    The file is not a loadable trip fixture. Names the file and what is wrong.

    A ValueError so `main` prints it as one red line and exits 1. Before this,
    pointing --trip-fixture at the wrong file (`trip_001_answer.json`, an
    acceptance ANSWER file that sits beside the fixtures) printed a raw Python
    traceback ending `KeyError: 'id'` - with the reader's own filesystem path
    in it, which is how a checkout folder named `...-v5` tripped the
    no-changelog scanner on Tsuki's Mac.
    """


# Only `id`. A v0 single-route fixture (trip_001.json, trip_002.json) carries no
# `legs` and has always loaded as a trip with none; that is a supported shape and
# tests render it. What it may not do is crash.
REQUIRED_TOP_LEVEL = ("id",)


def load_trip_fixture(path: Path) -> TripFixture:
    """Load a multi-leg trip fixture from JSON. Raises TripFixtureError, never KeyError."""
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise TripFixtureError(f"{path.name} is not valid JSON: {e}") from e
    except UnicodeDecodeError as e:
        raise TripFixtureError(f"{path.name} is not UTF-8 text: {e}") from e
    except IsADirectoryError as e:
        raise TripFixtureError(f"{path.name} is a directory, not a trip fixture file.") from e
    except PermissionError as e:
        raise TripFixtureError(f"{path.name} cannot be read: permission denied.") from e
    if not isinstance(data, dict):
        raise TripFixtureError(
            f"{path.name} is not a trip fixture: its top level is a "
            f"{type(data).__name__}, not a JSON object."
        )
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in data]
    if missing:
        hint = (
            " It looks like an acceptance-test ANSWER file (it carries "
            "'human_booking'), not a trip - pass the trip it answers instead."
            if "human_booking" in data or path.name.endswith("_answer.json")
            else ""
        )
        raise TripFixtureError(
            f"{path.name} is not a trip fixture: it has no "
            f"{' and no '.join(repr(k) for k in missing)}.{hint}"
        )
    if not isinstance(data.get("legs", []), list):
        raise TripFixtureError(
            f"{path.name}: 'legs' must be a list, not a {type(data['legs']).__name__}."
        )
    try:
        return _build_trip_fixture(data)
    except KeyError as e:
        raise TripFixtureError(
            f"{path.name}: a leg, cash option, points candidate or fee is missing "
            f"the required field {e.args[0]!r}."
        ) from e
    except (TypeError, ValueError, AttributeError) as e:
        raise TripFixtureError(f"{path.name}: {e}") from e


def _build_trip_fixture(data: dict) -> TripFixture:
    legs: List[Leg] = []
    for raw in data.get("legs", []):
        # v3: cash provenance is READ, never assumed. A cash option with no
        # `source` is "unknown" - NOT an assumed screenshot. The distinction is
        # the point: live mode replaces the POINTS side of a leg and the fixture
        # keeps owning the CASH side forever, so the reader has to be able to see
        # which of the two facts on a row came from where.
        cash_options = [
            CashOption(
                label=c["label"],
                amount=_finite_amount(c["amount"], f"leg {raw.get('id')!r} cash option amount"),
                currency=_currency(c.get("currency", "USD"), f"leg {raw.get('id')!r} cash option currency"),
                notes=c.get("notes", ""),
                unavoidable_cash_note=c.get("unavoidable_cash_note", ""),
                source=c.get("source", raw.get("cash_source", "unknown")),
                captured_on=_maybe_date(c.get("captured_on") or raw.get("cash_captured_on")),
                # The travel date this price is FOR. Absent means the leg's own
                # date. A leg may carry several, which is the ONLY honest route
                # by which an off-date live award becomes scoreable.
                date=_strict_date(c.get("date"), f"leg {raw.get('id')!r} cash option date"),
            )
            for c in raw.get("cash_options", [])
        ]

        points_candidates = [
            PointsCandidate(
                label=p["label"],
                program=p["program"],
                points=_count(p.get("points"), f"leg {raw.get('id')!r} points"),
                cash_surcharge=_finite_amount(
                    p.get("cash_surcharge", 0.0), f"leg {raw.get('id')!r} cash_surcharge"
                ),
                surcharge_currency=_currency(
                    p.get("surcharge_currency", "USD"),
                    f"leg {raw.get('id')!r} surcharge_currency",
                ),
                source=p.get("source", "google_badge_unverified"),
                source_note=p.get("source_note", ""),
                program_attribution_assumed=bool(
                    p.get("program_attribution_assumed", False)
                ),
                # --- v1 ---
                # `carrier_source` defaults to "unknown", NOT to "captured".
                # A fixture that names an operating carrier without saying where
                # that came from must not unlock the surcharge model.
                operating_carrier=str(p.get("operating_carrier", "") or "").upper(),
                marketing_carrier=str(p.get("marketing_carrier", "") or "").upper(),
                carrier_source=p.get("carrier_source", "unknown"),
                cabin=str(p.get("cabin", "Y") or "Y").upper(),
                is_round_trip=bool(p.get("is_round_trip", False)),
                # Likewise: a surcharge is only "captured" if the fixture says so.
                # `cash_surcharge: 0.0` with no flag is silence, not a real zero.
                surcharge_captured=bool(p.get("surcharge_captured", False)),
                points_per_night=(
                    _count(p["points_per_night"],
                           f"leg {raw.get('id')!r} points_per_night")
                    if p.get("points_per_night") is not None
                    else None
                ),
                nights=_count(p.get("nights"), f"leg {raw.get('id')!r} award nights"),
            )
            for p in raw.get("points_candidates", [])
        ]

        mandatory_fees = [
            MandatoryFee(
                label=f["label"],
                amount=_finite_amount(f["amount"], f"leg {raw.get('id')!r} fee amount"),
                currency=_currency(f.get("currency", "USD"), f"leg {raw.get('id')!r} fee currency"),
                per=f.get("per", "stay"),
                payable_on_points=bool(f.get("payable_on_points", True)),
                source=f.get("source", "captured"),
            )
            for f in raw.get("mandatory_fees", [])
        ]

        notes = list(raw.get("notes", []))
        notes.extend(raw.get("no_points_path_notes", []))

        legs.append(
            Leg(
                id=raw["id"],
                kind=raw["kind"],
                description=raw["description"],
                date=date.fromisoformat(raw["date"]),
                cash_options=cash_options,
                points_candidates=points_candidates,
                travelers=_travelers(raw.get("travelers", 1), raw.get("id")),
                notes=notes,
                data_flags=list(raw.get("data_flags", [])),
                unpriced_partner_programs=list(raw.get("unpriced_partner_programs", [])),
                origin=str(raw.get("origin", "") or "").upper(),
                destination=str(raw.get("destination", "") or "").upper(),
                # H-2: the builder has written this key since v5 Step 4 and
                # nothing read it. Absent stays absent - it is not "Y".
                cabin=str(raw.get("cabin", "") or "").strip().upper(),
                mandatory_fees=mandatory_fees,
                nights=_count(raw.get("nights"), f"leg {raw.get('id')!r} nights"),
                # Leg-level cash provenance: the most specific thing every cash
                # option on the leg agrees on, and "unknown" the moment they do
                # not. Silence is never promoted to a capture.
                cash_provenance=_leg_cash_provenance(cash_options),
                cash_captured_on=next(
                    (c.captured_on for c in cash_options if c.captured_on), None
                ),
                # Until apply_live says otherwise, a leg with badge candidates is
                # badge-derived and a leg with none has no points side at all.
                # There is no state meaning "nobody recorded where this came
                # from".
                points_provenance=(
                    PointsProvenance.BADGE_FALLBACK
                    if points_candidates
                    else PointsProvenance.UNAVAILABLE
                ),
            )
        )

    return TripFixture(
        id=data["id"],
        name=data.get("name", data["id"]),
        description=data.get("description", ""),
        source=data.get("source", ""),
        legs=legs,
        trip_level_flags=list(data.get("trip_level_flags", [])),
    )
