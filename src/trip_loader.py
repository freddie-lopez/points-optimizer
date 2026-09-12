"""Load multi-leg trip fixtures from JSON."""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List

from src import config
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

    R4-1: this used to do its own `float(value)` one line ABOVE the guard, in a
    `try` that caught TypeError and ValueError - and `float(10 ** 400)` raises
    OverflowError, so a huge int in a money field never reached the guard at
    all. There is no conversion here now: `config.scoreable_amount` is the one
    place a number from outside becomes a number this tool will score.
    """
    from src import config

    try:
        return config.scoreable_amount(value, what)
    except config.UnscoreableNumber as e:
        raise TripFixtureError(str(e)) from e


def _price(value, what: str) -> float:
    """A money amount that is being read AS A PRICE - R5-1. Same boundary, same
    words as the builder: zero is silence, a negative fare is a broken figure,
    and a boolean is not a dollar."""
    from src import config

    try:
        return config.scoreable_price(value, what)
    except config.UnscoreableNumber as e:
        raise TripFixtureError(str(e)) from e


def _points(value, what: str) -> int:
    """An award price in points - R5-2. Refused here rather than dropped
    silently, because a dropped one comes out as "none - not a partner"."""
    from src import config

    try:
        return config.scoreable_points(value, what)
    except config.UnscoreableNumber as e:
        raise TripFixtureError(str(e)) from e


def _count(value, what: str, default: int = 0) -> int:
    """
    A whole-number field that reaches arithmetic - a points price, a night
    count, a points-per-night - or a TripFixtureError.

    R2-3: M-1 bounded money and nothing else, so `{"points": 10 ** 400}` loaded
    happily and then raised `OverflowError: int too large to convert to float`
    inside the scorer: a traceback from the CLI and a 500 from the UI, the exact
    class M-1 claimed to close. Same mechanical rule as the money one, and it is
    not a ceiling: it refuses only a figure no run could ever put on the scale
    beside a fare.

    R3-3: the reason already names the value, shortened; wrapping it in
    `{value!r}` here is what put 401 digits back in front of the reader.
    """
    from src import config

    if value is None or value == "":
        value = default
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        raise TripFixtureError(
            f"{what} is {config.short_number(value)}, which is not a whole number."
        )
    try:
        return config.scoreable_count(value, what)
    except config.UnscoreableNumber as e:
        raise TripFixtureError(str(e)) from e


def _check_products(leg) -> None:
    """
    Every figure the fixture's own numbers MULTIPLY INTO, bounded the way the
    fields are.

    R3-1. Bounding each field on its own is not enough: `nights = 10 ** 200` and
    `points_per_night = 10 ** 200` each pass (each times the valuation is
    finite), and `hotels.award_points_for` multiplies them into `10 ** 400`,
    which `funding._score` then tries to convert to a float - OverflowError, a
    traceback from the CLI and a 500 from the UI. The guard was one
    multiplication short of the arithmetic.

    So the products the FILE determines are computed here, where a bad one is
    still a load refusal, and checked by the same mechanical rule. The other
    three shapes (a party's cash, a party's fees, a party's points) are clean
    today and are checked anyway - "clean today" is what the last two rounds
    were each told about the shape before this one.
    """
    from src import config

    where = f"leg {leg.id!r}"
    # Both already came through the boundary above, as ints.
    nights = max(leg.nights or 0, 0)
    party = max(leg.travelers or 1, 1)

    def refuse(reason, what):
        # A product that has already overflowed to `inf` reports itself as "inf
        # is not a finite amount", which points at the arithmetic rather than at
        # the file. The sentence names what multiplied, so the reader can find
        # the two numbers that did it.
        if reason:
            raise TripFixtureError(
                f"{where}: the {what} is too large to score - this fixture's own "
                f"numbers multiply past what a run can hold ({reason})"
            )

    def refuse_count(value, what):
        refuse(config.unscoreable_count_reason(value), what)

    def refuse_cash(value, what):
        refuse(config.unscoreable_cash_reason(value), what)

    for c in leg.points_candidates:
        total = c.points
        if c.points_per_night is not None and nights > 0:
            # hotels.award_points_for: per-night x nights. Zero nights with a
            # per-night price is a DATA error raised later, not an overflow.
            total = c.points_per_night * nights
            refuse_count(total, "award points (points_per_night x nights)")
        refuse_count(total * party, "award points for the party (points x travellers)")

    for c in leg.cash_options:
        refuse_cash(c.amount * party, "cash for the party (amount x travellers)")

    for f in leg.mandatory_fees:
        # MandatoryFee.total_for: amount x nights [x travellers].
        refuse_cash(f.total_for(nights, party),
                    f"fee {f.label!r} for the stay (amount x nights x travellers)")


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
        raise TripFixtureError(
            f"leg {leg_id!r} travelers is {config.short_number(value)}, not a count."
        )
    # R2-3: the count multiplies every money figure on the leg (and APD is
    # charged per passenger), so it comes in through the same boundary as the
    # rest - no conversion of its own.
    try:
        n = config.scoreable_count(value, f"leg {leg_id!r} travelers")
    except config.UnscoreableNumber as e:
        raise TripFixtureError(str(e)) from e
    if n < 1 or str(value).strip() != str(n):
        raise TripFixtureError(
            f"leg {leg_id!r} travelers is {config.short_number(value)}; a leg is "
            f"for at least 1 traveller."
        )
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


# ---------------------------------------------------------------------------
# What a trip fixture may be SHAPED like - MAC-2
# ---------------------------------------------------------------------------
#
# R5-4 caught a 400-level-deep JSON document by catching the RecursionError
# `json.loads` raises walking it. That is not a rule, it is whatever the
# interpreter happens to do with its stack: the same 400-deep file is REFUSED
# here (exit 1, CANNOT LOAD) and LOADED on Tsuki's macOS Python, where the C
# scanner gets further before the limit bites. The suite's own
# test_a_malformed_file_is_one_line_not_a_traceback[deeply_nested] therefore
# passed on one machine and failed on the other - the refusal boundary deciding
# by platform, which is the exact class of problem the boundary was built to
# end.
#
# So the depth is a RULE, checked before the file is parsed, by SCANNING the
# text rather than by walking a parsed tree - the scan cannot itself run out of
# stack, so it gives the same answer on every interpreter on every platform.
# The scan and the two limits are `config`'s, next to the boundary every NUMBER
# from outside already comes through, because the wallet file is read by a
# second loader with the same problem. What is decided HERE is what those limits
# mean for a trip fixture:
#
# DEPTH. The deepest shape the fixture schema has is five levels: the document,
# `legs`, a leg, `cash_options`/`points_candidates`/`mandatory_fees`, and one of
# those objects. Every committed fixture measures 2 to 5. 32 is six times the
# deepest shape that exists, so a nesting level nobody has designed yet still
# loads, and nothing a person writes by hand comes close.
#
# SIZE. The largest committed fixture is 17 KB (trip_b_europe.json, seven legs),
# and both the scan and `json.loads` hold the whole file in memory. 4 MB is
# ~240x the largest real one - room for a trip with hundreds of legs, each with
# every option filled in - and it is a bound rather than a ceiling somebody will
# meet. It matters because the file is chosen by whoever points --trip-fixture
# at it, and because the UI lists and loads every file in its trips directory
# on every page load.
#
# NUMBER OF LEGS, NUMBER OF KEYS: deliberately NOT limited. Both are bounded
# already - a file under MAX_FILE_BYTES cannot hold more than a few tens of
# thousands of either - and neither reaches recursion or any other stack: legs
# are a flat loop and keys are dict lookups, so a big-but-legal trip is slow at
# worst, never a crash. A leg cap would be a number invented out of nothing that
# could one day refuse a real round-the-world itinerary, which is the thing this
# comment block is here to avoid.
MAX_NESTING_DEPTH = config.MAX_JSON_NESTING_DEPTH
MAX_FILE_BYTES = config.MAX_INPUT_FILE_BYTES


def _too_deep(name: str, depth: int) -> "TripFixtureError":
    """The ONE sentence a too-deeply-nested fixture gets, on every platform,
    whether the rule caught it or the RecursionError backstop did."""
    return TripFixtureError(
        config.too_deeply_nested_reason(name, depth, MAX_NESTING_DEPTH)
        + " It is not a trip fixture."
    )


def load_trip_fixture(path: Path) -> TripFixture:
    """Load a multi-leg trip fixture from JSON. Raises TripFixtureError, never KeyError."""
    path = Path(path)
    # MAC-2: the size is checked before the bytes are read, so a file too large
    # to hold in memory is refused without being held in memory.
    try:
        size = path.stat().st_size
    except IsADirectoryError as e:
        raise TripFixtureError(f"{path.name} is a directory, not a trip fixture file.") from e
    except PermissionError as e:
        raise TripFixtureError(f"{path.name} cannot be read: permission denied.") from e
    except OSError:
        # Anything else about the file itself (it is gone, it is a broken
        # symlink) is reported by the read below, which already has a clause
        # for it - FileNotFoundError stays FileNotFoundError, as `main` and the
        # UI's trip list both expect.
        size = 0
    if size > MAX_FILE_BYTES:
        raise TripFixtureError(
            config.too_large_reason(path.name, size, MAX_FILE_BYTES)
            + " It is not a trip fixture."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise TripFixtureError(f"{path.name} is not UTF-8 text: {e}") from e
    except IsADirectoryError as e:
        raise TripFixtureError(f"{path.name} is a directory, not a trip fixture file.") from e
    except PermissionError as e:
        raise TripFixtureError(f"{path.name} cannot be read: permission denied.") from e
    # MAC-2: the RULE, before the parser gets a chance to decide by stack depth.
    depth = config.json_nesting_depth(text)
    if depth > MAX_NESTING_DEPTH:
        raise _too_deep(path.name, depth)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TripFixtureError(f"{path.name} is not valid JSON: {e}") from e
    except RecursionError as e:
        # R5-4, kept as a BACKSTOP. `json.loads` recurses per level of nesting,
        # so on an interpreter with a small stack (or one already deep in a call
        # chain) it can give up on a document the rule above accepted. Same
        # sentence either way: the reader is told the file's depth and the
        # limit, never which interpreter ran out first.
        raise _too_deep(path.name, depth) from e
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
    except RecursionError as e:
        # The same backstop, for the build walk rather than the parse.
        raise _too_deep(path.name, depth) from e


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
                amount=_price(c["amount"], f"leg {raw.get('id')!r} cash option amount"),
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
                points=(
                    _points(p["points"], f"leg {raw.get('id')!r} points")
                    if p.get("points") is not None
                    else 0
                ),
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
                    _points(p["points_per_night"],
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
        _check_products(legs[-1])

    return TripFixture(
        id=data["id"],
        name=data.get("name", data["id"]),
        description=data.get("description", ""),
        source=data.get("source", ""),
        legs=legs,
        trip_level_flags=list(data.get("trip_level_flags", [])),
    )
