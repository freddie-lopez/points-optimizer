"""
Does Seats.aero's `TotalTaxes` already include carrier-imposed surcharges (YQ)?

One answer PER SOURCE, in data/yq_inclusion.csv, and only Tsuki can write one:
each row must point at a filled-in yq-check record under docs/yq-checks/ (see
`python -m src.trips_tools yq-check`). An ABSENT row means UNVERIFIED, and a row
can never say "unverified" - absence is the only way to say it, so a half-done
edit cannot look like an answer.

What a verdict changes, in `live_trip.award_to_candidate`:
  * includes_yq - the API's taxes are the whole carrier-side cash figure. The
    award scores at those taxes and NO modelled band is added on top.
  * excludes_yq - the taxes are government taxes only. The modelled band for
    the operating airline is added, which needs the metal - so only an award
    whose itinerary lookup is KNOWN or AMBIGUOUS (and resolves) can score.
  * no row - today's rule: scoreable only under a program-wide $0 surcharge.

The table is validated at load and RAISES on anything malformed. A wrong row
here moves scores, so a mistake must stop the run rather than score quietly.
"""
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).parent.parent
YQ_TABLE_PATH = ROOT / "data" / "yq_inclusion.csv"
EVIDENCE_DIR = "docs/yq-checks"
RECORD_MARKER = "yq-check record"
BLANK = "____"
COLUMNS = ("source", "verdict", "verified_on", "evidence", "notes")
VERDICTS = ("includes_yq", "excludes_yq")


class YqInclusionError(ValueError):
    """data/yq_inclusion.csv is malformed. Never swallowed."""


@dataclass(frozen=True)
class YqVerdict:
    source: str
    verdict: str
    verified_on: date
    evidence: str
    notes: str = ""

    @property
    def includes(self) -> bool:
        return self.verdict == "includes_yq"

    @property
    def excludes(self) -> bool:
        return self.verdict == "excludes_yq"


def _check_evidence(evidence: str, root: Path, line: int) -> None:
    text = (evidence or "").strip()
    if not text:
        raise YqInclusionError(f"yq_inclusion.csv line {line}: no evidence path.")
    rel = Path(text)
    if rel.is_absolute() or ".." in rel.parts:
        raise YqInclusionError(
            f"yq_inclusion.csv line {line}: evidence {text!r} must be a path "
            f"inside {EVIDENCE_DIR}/, relative to the repository."
        )
    if rel.parts[:2] != tuple(EVIDENCE_DIR.split("/")) or len(rel.parts) < 3:
        raise YqInclusionError(
            f"yq_inclusion.csv line {line}: evidence {text!r} is outside "
            f"{EVIDENCE_DIR}/. Only a yq-check record can back a verdict."
        )
    path = root / rel
    if not path.is_file():
        raise YqInclusionError(
            f"yq_inclusion.csv line {line}: evidence {text!r} does not exist."
        )
    body = path.read_text()
    if RECORD_MARKER not in body:
        raise YqInclusionError(
            f"yq_inclusion.csv line {line}: {text!r} is not a yq-check record (it "
            f"lacks the {RECORD_MARKER!r} marker)."
        )
    if BLANK in body:
        raise YqInclusionError(
            f"yq_inclusion.csv line {line}: {text!r} still has {BLANK} blanks. "
            f"Fill in the site half of the record before recording a verdict."
        )


def load(
    path: Optional[Path] = None, *, today: Optional[date] = None, root: Path = ROOT
) -> Dict[str, YqVerdict]:
    """
    Source code -> verdict, for every row. Raises YqInclusionError on a bad row.

    A missing FILE raises too: the committed table exists (header only), and a
    deleted one is a mistake to be told about, not silence to read as "none".
    """
    from src.seats_client import SEATS_AERO_SOURCES, TAXES_UNREPORTED_SOURCES

    path = Path(path) if path is not None else YQ_TABLE_PATH
    today = today or date.today()
    if not path.is_file():
        raise YqInclusionError(f"No YQ inclusion table at {path}.")
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise YqInclusionError(
                f"{path.name} has columns {reader.fieldnames}; expected "
                f"{', '.join(COLUMNS)}."
            )
        out: Dict[str, YqVerdict] = {}
        for line, row in enumerate(reader, start=2):
            source = (row.get("source") or "").strip().lower()
            verdict = (row.get("verdict") or "").strip()
            if source not in SEATS_AERO_SOURCES:
                raise YqInclusionError(
                    f"{path.name} line {line}: {source!r} is not a Seats.aero source "
                    f"this tool maps."
                )
            if source in TAXES_UNREPORTED_SOURCES:
                raise YqInclusionError(
                    f"{path.name} line {line}: Seats.aero reports no taxes for "
                    f"{source!r}, so there is no figure for a verdict to be about."
                )
            if verdict not in VERDICTS:
                raise YqInclusionError(
                    f"{path.name} line {line}: verdict {verdict!r} is not one of "
                    f"{', '.join(VERDICTS)}. 'Unverified' is said by having NO row."
                )
            try:
                verified_on = date.fromisoformat((row.get("verified_on") or "").strip())
            except ValueError:
                raise YqInclusionError(
                    f"{path.name} line {line}: verified_on "
                    f"{row.get('verified_on')!r} is not a YYYY-MM-DD date."
                ) from None
            if verified_on > today:
                raise YqInclusionError(
                    f"{path.name} line {line}: verified_on {verified_on} is in the "
                    f"future."
                )
            if source in out:
                raise YqInclusionError(
                    f"{path.name} line {line}: {source!r} appears twice. One source, "
                    f"one verdict."
                )
            evidence = (row.get("evidence") or "").strip()
            _check_evidence(evidence, root, line)
            out[source] = YqVerdict(
                source=source,
                verdict=verdict,
                verified_on=verified_on,
                evidence=evidence,
                notes=(row.get("notes") or "").strip(),
            )
    return out


def verdict_for(source_code: str, table: Dict[str, YqVerdict]) -> Optional[YqVerdict]:
    return table.get(str(source_code or "").strip().lower())
