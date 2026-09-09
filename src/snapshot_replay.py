"""
Reading a snapshot manifest as an INPUT, and quoting a number against it.

WHY THIS MODULE EXISTS
======================
v3 built a manifest and treated it as documentation. A live margin was therefore
re-derivable by exactly one person: whoever ran it, on the day they ran it,
before award space moved. `--from-snapshot` makes the manifest an input, so a
percentage can be quoted with a hash beside it and anyone with this repo gets
the same number.

WHAT THE HASH IS OVER, AND WHY THAT SHAPE
=========================================
    manifest_hash = sha256("\\n".join(sorted(
        f"{leg_id}|{route}|{dates}|{snapshot_name}|{recomputed_content_hash}"
        for each SELECTED row
    )))

Three deliberate properties:

  * It is over the SELECTED ROWS' CONTENT, not over the markdown bytes. Fixing a
    typo in the manifest's prose does not invalidate a number somebody quoted;
    changing one byte of one snapshot always does.
  * `recomputed_content_hash` is `content_hash(pages)` RECOMPUTED FROM THE FILE
    ON DISK, never read out of `_meta`. Trusting a file's own claim about itself
    makes tampering a two-line edit. A snapshot whose `_meta` was edited to
    match its tampered bytes still disagrees with the manifest column, and a
    snapshot whose manifest column was ALSO edited still disagrees with the
    recomputation, which is the one number nobody can write down in advance.
  * Sorted, so row order in an append-only log cannot change a quoted number.

WHAT THIS MODULE REFUSES
========================
Everything it cannot fully account for. A missing snapshot, a hash that does not
match, a manifest that does not cover every queryable leg: each is a hard
refusal BEFORE anything is scored. There is no "replay what we have" mode.
A partially-replayed margin is an irreproducible number printed next to a hash,
which is worse than no hash - it looks stable.

That refusal is the decision in this feature most likely to be wrong (plan
§10.1). The common failure in six months is nine good rows and one snapshot
somebody deleted while tidying, and the tool will produce nothing at all.
Recorded here rather than hedged in code.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import response_cache

# The prefix that marks a manifest hash in output. Short enough to read aloud,
# distinctive enough to grep for.
HASH_PREFIX = "mh_"
HASH_CHARS = 16

# What a missing v5 column reads as. NOT "matches", NOT an empty string that a
# comparison would treat as equal to something.
UNKNOWN = "unknown"


class ManifestError(ValueError):
    """The manifest cannot be read, or cannot be replayed. Never swallowed."""


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRow:
    """One fetch, as the manifest recorded it. Nothing here is interpreted."""

    fetched_at: Optional[datetime]
    fetched_at_text: str
    leg_id: str
    route: str
    dates: str
    rows_seen: str
    awards: str
    state: str
    snapshot_cell: str
    snapshot_name: str
    is_refetch: bool
    # None means the column was absent (a pre-v5 row), which reads as UNKNOWN.
    content_hash: Optional[str]
    parser_version: Optional[str]
    trip_id: Optional[str]
    line_no: int

    @property
    def group_key(self) -> Tuple[str, str, str]:
        """What makes two rows the same question asked twice."""
        return (self.leg_id, self.route, self.dates)

    @property
    def content_hash_display(self) -> str:
        return self.content_hash or UNKNOWN

    @property
    def parser_version_display(self) -> str:
        return self.parser_version or UNKNOWN

    def describe(self) -> str:
        return (
            f"row {self.line_no}: {self.leg_id} {self.route} {self.dates} "
            f"-> {self.snapshot_name or '(no snapshot named)'}"
        )


@dataclass(frozen=True)
class Problem:
    """One reason a manifest cannot be replayed. Named, not numbered."""

    kind: str
    row: Optional[ManifestRow]
    detail: str

    def render(self) -> str:
        where = self.row.describe() if self.row is not None else "manifest"
        return f"[{self.kind}] {where}: {self.detail}"


@dataclass
class ReplaySelection:
    """The rows that will be replayed, and the ones they superseded."""

    considered: List[ManifestRow] = field(default_factory=list)
    selected: List[ManifestRow] = field(default_factory=list)
    superseded: List[ManifestRow] = field(default_factory=list)
    problems: List[Problem] = field(default_factory=list)

    @property
    def groups(self) -> int:
        return len({r.group_key for r in self.considered})

    def describe(self) -> str:
        """
        The selection line in the replay banner.

        The manifest is append-only and unbounded (risk 9.3): after a hundred
        live runs the selection rule does real work, and a wrong selection
        silently changes a hash. This line is the mitigation. It is a REPORT,
        not a guard.
        """
        return (
            f"{len(self.considered)} rows considered, {self.groups} groups, "
            f"{len(self.selected)} selected, {len(self.superseded)} superseded"
        )


def _parse_dt(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _cell(cells: List[str], index: int) -> Optional[str]:
    """
    A cell's value, or None when the column does not exist on this row.

    None is load-bearing. A pre-v5 row has no `content_hash` column at all, and
    the difference between "absent" and "empty" is the difference between
    UNKNOWN and a value that a comparison might treat as equal to something.
    """
    if index >= len(cells):
        return None
    value = cells[index].strip()
    if not value or value == "-":
        return None
    return value


def parse_manifest(path: Path) -> List[ManifestRow]:
    """
    Every data row of a MANIFEST.md, in file order. Prose and headers ignored.

    Raises ManifestError if the file does not exist or contains no data rows.
    A manifest with zero rows is not an empty replay set - it is a file that is
    not a manifest, or one whose rows were deleted, and either way nothing may
    be scored from it.
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"No manifest at {p}. Nothing can be replayed.")
    try:
        text = p.read_text()
    except OSError as e:
        raise ManifestError(f"Manifest at {p} could not be read: {e}") from e

    rows: List[ManifestRow] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 8:
            continue
        # The header row and its separator both start with "|" and neither is a
        # fetch. Identified by content, not by line number, because the prose
        # above the table is edited by hand and moves.
        if cells[0].lower().startswith("fetched_at") or set(cells[0]) <= {"-", ":"}:
            continue

        snapshot_cell = cells[7]
        is_refetch = "(re-fetch" in snapshot_cell
        snapshot_name = snapshot_cell.split("(")[0].strip()

        rows.append(
            ManifestRow(
                fetched_at=_parse_dt(cells[0]),
                fetched_at_text=cells[0],
                leg_id=cells[1],
                route=cells[2],
                dates=cells[3],
                rows_seen=cells[4],
                awards=cells[5],
                state=cells[6],
                snapshot_cell=snapshot_cell,
                snapshot_name=snapshot_name,
                is_refetch=is_refetch,
                content_hash=_cell(cells, 8),
                parser_version=_cell(cells, 9),
                trip_id=_cell(cells, 10),
                line_no=lineno,
            )
        )

    if not rows:
        raise ManifestError(
            f"{p} contains no manifest rows. An empty manifest is not an empty "
            f"replay set - it is a file that is not a manifest, or one whose "
            f"rows were removed. Nothing is scored from it."
        )
    return rows


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_replay_set(
    rows: List[ManifestRow], trip_id: Optional[str] = None
) -> ReplaySelection:
    """
    The rows to replay: latest `fetched_at` per (leg, route, dates).

    THE LOG HAS DUPLICATES BY DESIGN. Every fetch appends a row, and a re-fetch
    that returned byte-identical data appends a row pointing at the SAME file.
    So a replay set is a selection, and the rule lives here, in one place, and
    is printed in the banner.

    `trip_id=None` selects across every row, which is what a manifest written
    entirely by one trip needs (and what pre-v5 rows, which carry no trip_id,
    require - filtering on a column they do not have would select nothing and
    report it as an empty manifest).
    """
    considered = [
        r
        for r in rows
        if trip_id is None or r.trip_id is None or r.trip_id == trip_id
    ]
    selection = ReplaySelection(considered=considered)

    groups: Dict[Tuple[str, str, str], List[ManifestRow]] = {}
    for row in considered:
        groups.setdefault(row.group_key, []).append(row)

    for key in sorted(groups):
        members = groups[key]
        undated = [r for r in members if r.fetched_at is None]
        if undated and len(members) > 1:
            selection.problems.append(
                Problem(
                    kind="unorderable_rows",
                    row=undated[0],
                    detail=(
                        "this row has no readable fetched_at and shares its "
                        "(leg, route, dates) with another row, so 'the latest "
                        "one' has no answer. Which snapshot the margin was "
                        "quoted from would depend on file order."
                    ),
                )
            )
        newest = max(
            members,
            key=lambda r: (r.fetched_at is not None, r.fetched_at or datetime.min.replace(tzinfo=timezone.utc)),
        )
        ties = [
            r
            for r in members
            if r.fetched_at is not None
            and newest.fetched_at is not None
            and r.fetched_at == newest.fetched_at
            and r.snapshot_name != newest.snapshot_name
        ]
        if ties:
            selection.problems.append(
                Problem(
                    kind="ambiguous_latest_row",
                    row=newest,
                    detail=(
                        f"two rows share the latest fetched_at "
                        f"{newest.fetched_at_text} for this (leg, route, dates) "
                        f"and name DIFFERENT snapshots "
                        f"({newest.snapshot_name}, {ties[0].snapshot_name}). "
                        f"There is no rule that picks one, and picking either "
                        f"would make a quoted number depend on row order."
                    ),
                )
            )
        selection.selected.append(newest)
        selection.superseded.extend(r for r in members if r is not newest)

    return selection


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def recompute_content_hash(path: Path) -> str:
    """
    `content_hash(pages)` computed FROM THE FILE, not read from `_meta`.

    This is the one number in the whole scheme that nobody can write into a
    file in advance: it is a function of the bytes. Everything else - the
    `_meta` claim, the manifest column - is a copy that can be edited, and both
    are compared against this.
    """
    envelope = json.loads(Path(path).read_text())
    if not isinstance(envelope, dict):
        raise ValueError("envelope is not an object")
    pages = envelope.get("pages")
    if not isinstance(pages, list):
        raise ValueError("envelope has no 'pages' list")
    return response_cache.content_hash(pages)


def verify(rows: List[ManifestRow], snapshot_dir: Path) -> List[Problem]:
    """
    Every reason these rows cannot be replayed. An empty list means replayable.

    Returns problems rather than raising, so a caller can print ALL of them.
    Reporting the first missing snapshot and stopping means fixing it reveals
    the second, which is how a five-minute failure becomes a five-round one.
    """
    problems: List[Problem] = []
    directory = Path(snapshot_dir)

    for row in rows:
        if not row.snapshot_name:
            problems.append(
                Problem("no_snapshot_named", row, "the row names no snapshot file.")
            )
            continue

        # A manifest may not point outside its own snapshot directory. A row
        # reading `../../../etc/passwd` is not a fetch record.
        if "/" in row.snapshot_name or "\\" in row.snapshot_name or ".." in row.snapshot_name:
            problems.append(
                Problem(
                    "snapshot_outside_corpus",
                    row,
                    f"{row.snapshot_name!r} is not a plain filename inside the "
                    f"snapshot directory. A manifest may only name files beside "
                    f"itself.",
                )
            )
            continue

        path = directory / row.snapshot_name
        if not path.exists():
            problems.append(
                Problem(
                    "snapshot_missing",
                    row,
                    f"{path} does not exist. The manifest is no longer "
                    f"replayable: the bytes a number would be quoted from are "
                    f"gone, and there is no partial-replay mode.",
                )
            )
            continue

        try:
            recomputed = recompute_content_hash(path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            problems.append(
                Problem(
                    "snapshot_unloadable",
                    row,
                    f"{path.name} could not be loaded as a response envelope "
                    f"({e}). It is NOT being read as an empty result.",
                )
            )
            continue

        try:
            claimed = (json.loads(path.read_text()).get("_meta") or {}).get(
                "content_hash"
            )
        except (OSError, ValueError):
            claimed = None
        if claimed and claimed != recomputed:
            problems.append(
                Problem(
                    "meta_hash_mismatch",
                    row,
                    f"{path.name}'s own _meta.content_hash "
                    f"({str(claimed)[:HASH_CHARS]}) does not match the hash "
                    f"recomputed from its pages ({recomputed[:HASH_CHARS]}). "
                    f"The bytes changed after the file was written.",
                )
            )

        if row.content_hash is None:
            # A pre-v5 row. Reported as unknown, and unknown is NOT replayable:
            # a hash quoted over a row nobody can check is a hash that proves
            # nothing.
            problems.append(
                Problem(
                    "content_hash_unknown",
                    row,
                    "this row predates v5 and carries no content_hash column, "
                    "so there is nothing to check the file against. It reads as "
                    "UNKNOWN, never as 'matches'. Re-fetch the leg to write a "
                    "v5 row, or replay a manifest that has one.",
                )
            )
        elif not recomputed.startswith(row.content_hash):
            problems.append(
                Problem(
                    "manifest_hash_mismatch",
                    row,
                    f"the manifest column says {row.content_hash} and the file's "
                    f"pages hash to {recomputed[:HASH_CHARS]}. This is the check "
                    f"that cannot be defeated by editing the file: the "
                    f"recomputation is a function of the bytes.",
                )
            )

        if (row.state or "").strip() == "budget_exhausted":
            problems.append(
                Problem(
                    "impossible_state_archived",
                    row,
                    "the row records BUDGET_EXHAUSTED against an archived "
                    "snapshot. A budget failure is never cached and never "
                    "archived (finding H-1), so a row claiming one describes a "
                    "response that cannot exist. The manifest is corrupt.",
                )
            )

    return problems


# ---------------------------------------------------------------------------
# The hash
# ---------------------------------------------------------------------------


def manifest_hash(rows: List[ManifestRow], snapshot_dir: Path) -> str:
    """
    `mh_` + 16 hex over the selected rows' CONTENT. What a margin is quoted at.

    Raises if any row's snapshot cannot be hashed. There is deliberately no
    "hash what we could read" path: a hash over an unknown subset is a hash
    that means nothing and looks like it means something.
    """
    if not rows:
        raise ManifestError(
            "no rows selected, so there is nothing to hash. A hash over an "
            "empty set would be a constant printed beside a percentage."
        )
    directory = Path(snapshot_dir)
    lines = []
    for row in rows:
        recomputed = recompute_content_hash(directory / row.snapshot_name)
        lines.append(
            f"{row.leg_id}|{row.route}|{row.dates}|{row.snapshot_name}|{recomputed}"
        )
    blob = "\n".join(sorted(lines))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest[:HASH_CHARS]}"


def parser_versions(rows: List[ManifestRow]) -> List[str]:
    """The distinct parser versions the selected rows were captured under."""
    return sorted({r.parser_version_display for r in rows})
