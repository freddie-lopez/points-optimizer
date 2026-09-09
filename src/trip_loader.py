"""Load multi-leg trip fixtures from JSON."""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List

from src.models import CashOption, Leg, MandatoryFee, PointsCandidate, PointsProvenance


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


def load_trip_fixture(path: Path) -> TripFixture:
    """Load a multi-leg trip fixture from JSON."""
    with open(path, "r") as f:
        data = json.load(f)

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
                amount=float(c["amount"]),
                currency=c.get("currency", "USD"),
                notes=c.get("notes", ""),
                unavoidable_cash_note=c.get("unavoidable_cash_note", ""),
                source=c.get("source", raw.get("cash_source", "unknown")),
                captured_on=_maybe_date(c.get("captured_on") or raw.get("cash_captured_on")),
                # The travel date this price is FOR. Absent means the leg's own
                # date. A leg may carry several, which is the ONLY honest route
                # by which an off-date live award becomes scoreable.
                date=_maybe_date(c.get("date")),
            )
            for c in raw.get("cash_options", [])
        ]

        points_candidates = [
            PointsCandidate(
                label=p["label"],
                program=p["program"],
                points=int(p.get("points", 0) or 0),
                cash_surcharge=float(p.get("cash_surcharge", 0.0)),
                surcharge_currency=p.get("surcharge_currency", "USD"),
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
                    int(p["points_per_night"])
                    if p.get("points_per_night") is not None
                    else None
                ),
                nights=int(p.get("nights", 0) or 0),
            )
            for p in raw.get("points_candidates", [])
        ]

        mandatory_fees = [
            MandatoryFee(
                label=f["label"],
                amount=float(f["amount"]),
                currency=f.get("currency", "USD"),
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
                travelers=int(raw.get("travelers", 1)),
                notes=notes,
                data_flags=list(raw.get("data_flags", [])),
                unpriced_partner_programs=list(raw.get("unpriced_partner_programs", [])),
                origin=str(raw.get("origin", "") or "").upper(),
                destination=str(raw.get("destination", "") or "").upper(),
                # H-2: the builder has written this key since v5 Step 4 and
                # nothing read it. Absent stays absent - it is not "Y".
                cabin=str(raw.get("cabin", "") or "").strip().upper(),
                mandatory_fees=mandatory_fees,
                nights=int(raw.get("nights", 0) or 0),
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
