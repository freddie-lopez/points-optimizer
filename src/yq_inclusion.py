"""
Does Seats.aero's `TotalTaxes` already include carrier-imposed surcharges (YQ)?

One answer PER SOURCE AND AIRLINE, in data/yq_inclusion.csv, and only a filled-in
yq-check record under docs/yq-checks/ can back one (see `python -m
src.trips_tools yq-check`). An ABSENT row means UNVERIFIED, and a row can never
say "unverified" - absence is the only way to say it, so a half-done edit cannot
look like an answer.

DECISION D1 (Manager review), option (b), the default: a row applies ONLY to an
award whose itinerary lookup is KNOWN and names exactly the airline the check
was run on. A Virgin Atlantic check on a VS flight says nothing about how
Seats.aero builds the taxes figure for a Virgin Atlantic award on an Air France
flight, so that award stays unscoreable until a check on AF metal. Option (a),
one verdict per source whatever the metal, is a decision Tsuki can reverse to;
see the README.

What a verdict changes, in `live_trip.award_to_candidate`:
  * includes_yq - the API's taxes are the whole carrier-side cash figure. The
    award scores at those taxes and NO modelled band is added on top.
  * excludes_yq - the taxes are government taxes only. The modelled band for
    that airline is added on top.
  * no row for (source, the award's KNOWN airline) - today's rule: scoreable
    only under a program-wide $0 surcharge.

The table is validated at load and RAISES on anything malformed. A wrong row
here moves scores, so a mistake must stop the run rather than score quietly.
"""
import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent
YQ_TABLE_PATH = ROOT / "data" / "yq_inclusion.csv"
EVIDENCE_DIR = "docs/yq-checks"
RECORD_MARKER = "yq-check record"
BLANK = "____"
COLUMNS = ("source", "airline", "verdict", "verified_on", "evidence", "notes")
AIRLINE_RE = re.compile(r"[A-Z0-9]{2}")
VERDICTS = ("includes_yq", "excludes_yq")

# The record's own statements, as `yq-check` writes them. A row is backed only
# by a record that names THE SAME SOURCE in its title (and on its program line,
# if it has one) and whose ONE verdict line says THE SAME VERDICT. Two
# independent statements that must agree: a row copied from another source's
# record, or a verdict typed differently from the one written after reading the
# airline's site, is refused rather than scored.
RECORD_TITLE_RE = re.compile(r"^#\s*yq-check record:\s*([A-Za-z0-9_]+)\s*,", re.M)
RECORD_SOURCE_RE = re.compile(r"\(source\s+([A-Za-z0-9_]+)\)")
RECORD_VERDICT_RE = re.compile(
    r"^\s*-\s*verdict\s*\(includes_yq / excludes_yq / inconclusive\)\s*:[ \t]*(.*?)[ \t]*$",
    re.M,
)
# D1. The airline the check was run on, and the lookup status it came from, as
# yq-check writes them into the Seats.aero half. Only a KNOWN single-carrier
# lookup gives an airline; anything else is written as NONE and backs nothing.
RECORD_AIRLINE_RE = re.compile(
    r"^\s*-\s*checked airline \(the award's KNOWN flight-number carrier\)\s*:[ \t]*(.*?)[ \t]*$",
    re.M,
)
RECORD_LOOKUP_RE = re.compile(
    r"^\s*-\s*itinerary lookup status\s*:[ \t]*(.*?)[ \t]*$", re.M
)


class YqInclusionError(ValueError):
    """data/yq_inclusion.csv is malformed. Never swallowed."""


@dataclass(frozen=True)
class YqVerdict:
    source: str
    airline: str
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


def _check_evidence(
    evidence: str, root: Path, line: int, source: str = "", verdict: str = "",
    airline: str = "",
) -> None:
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
    _check_record_statements(body, text, line, source, verdict, airline)


def _check_record_statements(
    body: str, text: str, line: int, source: str, verdict: str, airline: str = ""
) -> None:
    """The record must be FOR this source and airline and must SAY this verdict."""
    where = f"yq_inclusion.csv line {line}: {text!r}"
    titles = RECORD_TITLE_RE.findall(body)
    if len(titles) != 1:
        raise YqInclusionError(
            f"{where} does not name exactly one source in a '# yq-check record: "
            f"<source>, <date>' title, so it cannot be told which source it is "
            f"evidence for."
        )
    named = {titles[0].lower()} | {s.lower() for s in RECORD_SOURCE_RE.findall(body)}
    if named != {source}:
        raise YqInclusionError(
            f"{where} is a record for {', '.join(sorted(named))}, not for "
            f"{source!r}. One check settles one source; a verdict is not copied "
            f"to another program."
        )
    said = RECORD_VERDICT_RE.findall(body)
    if len(said) != 1:
        raise YqInclusionError(
            f"{where} has {len(said)} verdict lines; it needs exactly one "
            f"'- verdict (includes_yq / excludes_yq / inconclusive): <verdict>'."
        )
    value = said[0].strip().lower()
    if value == "inconclusive":
        raise YqInclusionError(
            f"{where} says the check was INCONCLUSIVE. An inconclusive check "
            f"backs no verdict: record nothing for {source!r}."
        )
    if value not in VERDICTS:
        raise YqInclusionError(
            f"{where} has verdict line {said[0]!r}, which is not one of "
            f"{', '.join(VERDICTS)}."
        )
    if value != verdict:
        raise YqInclusionError(
            f"{where} says {value} and the row says {verdict}. The row must "
            f"repeat the verdict written in its record; neither is picked."
        )
    _check_record_airline(body, where, airline)


def _check_record_airline(body: str, where: str, airline: str) -> None:
    """D1: the record's lookup was KNOWN and names the row's airline."""
    statuses = RECORD_LOOKUP_RE.findall(body)
    if len(statuses) != 1:
        raise YqInclusionError(
            f"{where} has {len(statuses)} 'itinerary lookup status' lines; it "
            f"needs exactly one, as yq-check writes it."
        )
    if statuses[0].strip().upper() != "KNOWN":
        raise YqInclusionError(
            f"{where} records an itinerary lookup that was "
            f"{statuses[0].strip() or '(blank)'}, not KNOWN. A verdict applies only "
            f"to the airline the check was run on, so a check whose airline is not "
            f"KNOWN backs nothing."
        )
    named = RECORD_AIRLINE_RE.findall(body)
    if len(named) != 1:
        raise YqInclusionError(
            f"{where} has {len(named)} 'checked airline' lines; it needs exactly "
            f"one, as yq-check writes it."
        )
    if named[0].strip().upper() != airline:
        raise YqInclusionError(
            f"{where} was run on {named[0].strip() or '(blank)'} metal and the row "
            f"says {airline}. A verdict covers only the airline it was checked on."
        )


YqTable = Dict[Tuple[str, str], YqVerdict]


def load(
    path: Optional[Path] = None, *, today: Optional[date] = None, root: Path = ROOT
) -> YqTable:
    """
    (source code, airline) -> verdict, for every row. Raises YqInclusionError on a bad row.

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
        out: YqTable = {}
        for line, row in enumerate(reader, start=2):
            source = (row.get("source") or "").strip().lower()
            airline = (row.get("airline") or "").strip().upper()
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
            if not AIRLINE_RE.fullmatch(airline):
                raise YqInclusionError(
                    f"{path.name} line {line}: airline {row.get('airline')!r} is not a "
                    f"two-character airline code. A verdict covers the one airline "
                    f"its check was run on (D1)."
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
            if (source, airline) in out:
                raise YqInclusionError(
                    f"{path.name} line {line}: {source!r} on {airline} appears twice. "
                    f"One source and airline, one verdict."
                )
            evidence = (row.get("evidence") or "").strip()
            _check_evidence(
                evidence, root, line, source=source, verdict=verdict, airline=airline
            )
            out[(source, airline)] = YqVerdict(
                source=source,
                airline=airline,
                verdict=verdict,
                verified_on=verified_on,
                evidence=evidence,
                notes=(row.get("notes") or "").strip(),
            )
    return out


def verdicts_for_source(source_code: str, table: Optional[YqTable]) -> List[YqVerdict]:
    """Every verdict recorded for this source, whatever airline it was run on."""
    source = str(source_code or "").strip().lower()
    return [v for (s, _), v in sorted((table or {}).items()) if s == source]


def verdict_for(
    source_code: str, airline: Optional[str], table: Optional[YqTable]
) -> Optional[YqVerdict]:
    """The verdict for this source on this airline, or None. No airline, no verdict."""
    if not airline:
        return None
    key = (str(source_code or "").strip().lower(), str(airline).strip().upper())
    return (table or {}).get(key)
