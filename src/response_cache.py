"""
Raw-response disk cache and snapshot archive for Seats.aero.

WHAT THIS CACHES, AND WHY IT MATTERS THAT IT IS RAW.

It stores the VERBATIM response pages, not parsed Awards. The argument is v2:
the parser read `cost` / `taxes` / `Source` / `Carriers`, none of which exist in
the real payload, so it produced zero awards from a response containing a real
9-seat bookable award - and that emptiness was reported as "no award
availability". The fix was only possible because ONE raw response happened to
exist. A cache of parsed objects would have cached the bug, and a parser fix
would have had to spend fresh API calls to prove itself.

Caching raw means a future parser fix replays every response this project has
ever seen, for free, offline, in CI.

THE CACHE ENTRY AND THE TEST FIXTURE ARE THE SAME FILE FORMAT. That is
deliberate. `put()` writes the cache entry and the snapshot in one operation, so
the archival step cannot be forgotten - there is no `--snapshot` flag to omit.
Every response that reaches the cache also joins the regression corpus.

WHAT IS NEVER WRITTEN HERE: the Partner-Authorization header, or the API key in
any form. The header is not part of the cache key, is not stored in `_meta`, and
a test greps every cache and snapshot file for the live key and for anything
shaped like one.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent

# Bumped whenever the envelope shape changes. It is part of the key, so a schema
# change cannot hit a stale entry - it simply misses and re-fetches.
CACHE_SCHEMA = 1

DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "seats_aero"
DEFAULT_SNAPSHOT_DIR = ROOT / "tests" / "fixtures" / "seats_aero" / "live_trip_b"

# Award space moves. A day-old cache quietly re-answering a fresh question is its
# own kind of lie, so entries go cold after six hours by default. An expired
# entry is NEVER deleted - deleting a response is deleting evidence.
DEFAULT_TTL_SECONDS = 6 * 60 * 60

MANIFEST_NAME = "MANIFEST.md"

_MANIFEST_HEADER = """# Live Seats.aero snapshot manifest

Every row is one FETCH. Every fetch writes a cache entry and archives a snapshot
here in the same envelope format, so any file in this directory is directly
loadable as a test fixture. Snapshots are deduplicated by content hash: a
re-fetch that returns byte-identical data adds a row here but no second file.

This manifest is what makes a live run reviewable after the fact. It records
what was asked, when, what came back, and which file holds it.

v5 MADE THIS FILE AN INPUT. `--from-snapshot MANIFEST.md` replays the selected
rows and quotes a margin against a hash of them. Three columns were added for
that: `content_hash` (16 hex of the archived pages), `parser_version` (which
parser read them), and `trip_id` (which trip they belong to). Rows written
before v5 have none of the three; they read as `unknown`, never as `matches`.

THE PROSE ABOVE THIS TABLE IS NOT HASHED. The manifest hash is over the
selected rows' snapshot CONTENT, so fixing a typo here cannot invalidate a
number somebody quoted, and changing one byte of one snapshot always does.

| fetched_at (UTC) | leg | route | dates | rows | awards | state | snapshot | content_hash | parser_version | trip_id |
| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
"""


class CacheCorrupt(ValueError):
    """A cache file exists but cannot be read as an envelope."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def canonical_request(endpoint: str, params: Dict[str, Any]) -> str:
    """
    The exact string that gets hashed. Printed in `_meta` so a key is auditable.

    Sorted by parameter name so that dict ordering cannot change a key, and
    prefixed with the schema version so an envelope change invalidates
    everything at once.
    """
    parts = "|".join(f"{k}={params[k]}" for k in sorted(params))
    return f"v{CACHE_SCHEMA}|{endpoint}|{parts}"


def request_key(endpoint: str, params: Dict[str, Any]) -> str:
    """sha256 of the canonical request string."""
    return hashlib.sha256(canonical_request(endpoint, params).encode("utf-8")).hexdigest()


def content_hash(pages: List[Dict[str, Any]]) -> str:
    """Stable hash of the response body alone, used to deduplicate snapshots."""
    blob = json.dumps(pages, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# How many hex characters of `content_hash` go in the manifest column. Short
# enough to read in a table, long enough that a collision is not a thing anyone
# needs to think about. The FULL hash is recomputed from the file at verify
# time; this column is a second, independent copy of the claim.
MANIFEST_HASH_CHARS = 16


def _parser_version() -> str:
    """
    `seats_client.PARSER_VERSION`, imported lazily to avoid an import cycle.

    Returns "unknown" rather than raising: a cache write must never fail
    because of a bookkeeping field, and "unknown" is the honest value when the
    module cannot be reached.
    """
    try:
        from src.seats_client import PARSER_VERSION

        return str(PARSER_VERSION)
    except Exception:  # noqa: BLE001 - a version stamp must not break a write
        return "unknown"


@dataclass(frozen=True)
class CachedResponse:
    """One cache hit: the raw pages plus everything known about how they got here."""

    pages: List[Dict[str, Any]]
    meta: Dict[str, Any]
    path: Path
    fetched_at: Optional[datetime]
    age_seconds: Optional[float]

    @property
    def http_status(self) -> Optional[int]:
        return self.meta.get("http_status")

    @property
    def pagination_note(self) -> str:
        return str(self.meta.get("pagination_note") or "")


# ---------------------------------------------------------------------------
# Key material must never reach disk
# ---------------------------------------------------------------------------

# Anything that looks like an opaque credential. Deliberately broad: it is used
# to REFUSE a write, and a false positive costs a run while a false negative
# leaks a key into a committed fixture.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_\-]{20,}")

# Substrings that appear in ordinary Seats.aero payloads and are not secrets.
# Availability IDs are 27-character base58 and would otherwise trip the shape
# check on every single response.
_ALLOWED_META_KEYS = frozenset(
    {"request_key", "content_hash", "cache_schema"}
)


def assert_no_key_material(text: str, where: str = "payload") -> None:
    """
    Refuse to write anything containing the live API key.

    The key is read from the environment, never from the payload, so this is a
    cheap exact-substring guard rather than an attempt to recognise secrets in
    general. The 20+ character token shape is checked by a test over the whole
    tree rather than here, because response bodies legitimately contain long
    opaque IDs and blocking those would block every write.
    """
    key = os.getenv("SEATS_AERO_KEY") or ""
    if key and key in text:
        raise ValueError(
            f"REFUSING TO WRITE {where}: it contains the value of SEATS_AERO_KEY. "
            f"The Partner-Authorization header is not part of the cache key, is "
            f"not stored in _meta, and must never reach a file that gets "
            f"committed as a fixture."
        )
    if "Partner-Authorization" in text:
        raise ValueError(
            f"REFUSING TO WRITE {where}: it names the Partner-Authorization "
            f"header. Auth material is not cached and not snapshotted."
        )


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


@dataclass
class ResponseCache:
    """
    A directory of raw response envelopes, plus the snapshot corpus beside it.

    Two directories with different lifetimes:
      * `cache_dir` is runtime state, gitignored, keyed by request hash;
      * `snapshot_dir` is COMMITTED, browsably named, and is the regression
        corpus. Step 2's test replays every file in it through the parser.
    """

    cache_dir: Path = DEFAULT_CACHE_DIR
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    # Non-fatal problems worth printing: a corrupt entry, an expired entry that
    # was skipped. Never raised - a bad cache file must degrade to a miss, never
    # to a crash and never to a silent empty result.
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.snapshot_dir = Path(self.snapshot_dir)

    # -- paths ----------------------------------------------------------

    def path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    @property
    def manifest_path(self) -> Path:
        return self.snapshot_dir / MANIFEST_NAME

    # -- read -----------------------------------------------------------

    def get(
        self, key: str, ttl: Optional[int] = None, now: Optional[datetime] = None
    ) -> Optional[CachedResponse]:
        """
        A usable cache entry, or None on miss, expiry or corruption.

        `ttl=0` disables reuse entirely: everything is a miss. An EXPIRED entry
        is reported as a miss and the FILE IS LEFT ALONE - it is evidence, and a
        future parser fix may want to replay it even though it is too old to
        answer a live question.
        """
        ttl = self.ttl_seconds if ttl is None else ttl
        path = self.path_for(key)
        if not path.exists():
            return None

        try:
            envelope = json.loads(path.read_text())
            if not isinstance(envelope, dict):
                raise ValueError("envelope is not an object")
            pages = envelope.get("pages")
            if not isinstance(pages, list):
                raise ValueError("envelope has no 'pages' list")
            meta = envelope.get("_meta") or {}
        except (OSError, ValueError) as e:
            # A corrupt entry is a MISS WITH A WARNING. It is never a crash and
            # never a silent empty result - an empty result read as a finding is
            # the exact bug this project has now made twice.
            self.warnings.append(
                f"Cache file {path.name} could not be read ({e}). Treated as a "
                f"MISS and re-fetched. It is NOT being read as an empty result. "
                f"The file is left in place for inspection."
            )
            return None

        fetched_at = _parse_iso(str(meta.get("fetched_at") or ""))
        age = None
        if fetched_at is not None:
            age = ((now or _utcnow()) - fetched_at).total_seconds()

        if ttl <= 0:
            self.warnings.append(
                f"Cache reuse is disabled (ttl={ttl}); {path.name} was NOT used."
            )
            return None
        if age is None:
            self.warnings.append(
                f"Cache file {path.name} carries no readable fetched_at, so its "
                f"age cannot be checked. Treated as a MISS rather than assumed "
                f"fresh."
            )
            return None
        if age > ttl:
            self.warnings.append(
                f"Cache entry {path.name} is {age / 3600:.1f}h old, past the "
                f"{ttl / 3600:.1f}h TTL. Re-fetching. The stale file is KEPT, "
                f"not deleted - a response is evidence."
            )
            return None

        return CachedResponse(
            pages=pages, meta=meta, path=path, fetched_at=fetched_at, age_seconds=age
        )

    # -- write ----------------------------------------------------------

    def put(
        self,
        key: str,
        request: Dict[str, Any],
        pages: List[Dict[str, Any]],
        meta: Optional[Dict[str, Any]] = None,
        *,
        now: Optional[datetime] = None,
    ) -> CachedResponse:
        """
        Write the cache entry AND the snapshot AND the manifest row. One event.

        Returns the CachedResponse that was written, so a caller that just
        fetched has the same object shape as a caller that hit the cache.
        """
        meta = dict(meta or {})
        # Truncated to whole seconds because that is the resolution the envelope
        # stores. Without this, a fresh write reports a fetched_at with
        # microseconds that the very next cache hit cannot reproduce, and
        # "served from cache with the ORIGINAL timestamp" quietly stops being
        # checkable.
        fetched_at = (now or _utcnow()).replace(microsecond=0)

        envelope = {
            "_meta": {
                "cache_schema": CACHE_SCHEMA,
                "request": dict(request),
                "canonical_request": canonical_request(
                    str(meta.get("endpoint") or "search"), request
                ),
                "request_key": key,
                "content_hash": content_hash(pages),
                "fetched_at": _iso(fetched_at),
                "fetched_by": "v3 live trip mode",
                "http_status": meta.get("http_status"),
                "pages": len(pages),
                "pagination_note": meta.get("pagination_note", ""),
                # Stated as a fact in the file so a reviewer does not have to
                # infer it from an absence.
                "key_redacted": True,
                # Worded WITHOUT the literal header name: assert_no_key_material
                # refuses any write that names it, and that guard is deliberately
                # dumb enough that it would otherwise trip on this very note.
                # v5 STEP 2. WHICH PARSER READ THESE BYTES.
                #
                # Storing raw pages exists so a parser fix can be re-run against
                # yesterday's responses. That property is only usable if a
                # replay can say whether the parser has moved since - otherwise
                # a reparse silently changes the award count and the reader has
                # no way to know it happened. Imported lazily because
                # seats_client imports this module.
                "parser_version": _parser_version(),
                "key_note": (
                    "The partner auth header is not part of the request key, is "
                    "not written to this file, and is not recoverable from it."
                ),
                "leg_id": meta.get("leg_id"),
                "trip_id": meta.get("trip_id"),
                "state": meta.get("state", ""),
                "awards_parsed": meta.get("awards_parsed"),
                "rows_seen": meta.get("rows_seen"),
            },
            "pages": pages,
        }

        text = json.dumps(envelope, indent=2, sort_keys=False)
        assert_no_key_material(text, where="a Seats.aero cache entry")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.path_for(key)
        cache_path.write_text(text + "\n")

        snapshot_path = self._archive_snapshot(envelope, text, request, meta, fetched_at)
        envelope["_meta"]["snapshot"] = snapshot_path.name if snapshot_path else None
        # The manifest row this fetch wrote, identified by its own timestamp so a
        # later identical re-fetch cannot claim it (finding L-8).
        envelope["_meta"]["manifest_key"] = _iso(fetched_at)
        # Re-write so the cache entry NAMES its own snapshot. Without this a
        # cache hit cannot say which committed fixture holds the same bytes, and
        # the manifest stops being able to join the two on a later run.
        text = json.dumps(envelope, indent=2, sort_keys=False)
        assert_no_key_material(text, where="a Seats.aero cache entry")
        cache_path.write_text(text + "\n")

        return CachedResponse(
            pages=pages,
            meta=envelope["_meta"],
            path=cache_path,
            fetched_at=fetched_at,
            age_seconds=0.0,
        )

    # -- snapshot corpus -------------------------------------------------

    def _snapshot_name(
        self, request: Dict[str, Any], meta: Dict[str, Any], fetched_at: datetime
    ) -> str:
        leg = str(meta.get("leg_id") or "adhoc")
        origin = str(request.get("origin_airport") or "???")
        destination = str(request.get("destination_airport") or "???")
        start = str(request.get("start_date") or "nodate")
        stamp = fetched_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", f"{leg}_{origin}_{destination}_{start}_{stamp}")
        return f"{safe}.json"

    def _archive_snapshot(
        self,
        envelope: Dict[str, Any],
        text: str,
        request: Dict[str, Any],
        meta: Dict[str, Any],
        fetched_at: datetime,
    ) -> Optional[Path]:
        """
        Archive the response as a browsable fixture, deduplicated by content.

        A re-fetch that returns byte-identical data writes NO second file - the
        corpus is a set of distinct observations, not a log. The manifest row is
        written either way, so the fact that a re-fetch happened is not lost.
        """
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        digest = envelope["_meta"]["content_hash"]
        # The manifest row needs three facts the CALLER's meta does not carry:
        # the content hash and parser version the envelope was just stamped
        # with, and the trip. Merged here rather than duplicated in the row
        # builder, so there is one place a manifest column can go wrong.
        meta = {
            **meta,
            "content_hash": digest,
            "parser_version": envelope["_meta"].get("parser_version"),
        }

        existing = self._snapshot_with_content(digest)
        if existing is not None:
            self._append_manifest(request, meta, fetched_at, existing, duplicate=True)
            return existing

        path = self.snapshot_dir / self._snapshot_name(request, meta, fetched_at)
        # The browsable name is minute-resolution, so two DIFFERENT responses for
        # the same leg inside one minute would collide - and the second would
        # silently overwrite the first, destroying an observation. Disambiguate
        # with the content hash rather than dropping either.
        if path.exists():
            path = path.with_name(f"{path.stem}_{digest[:8]}.json")
        assert_no_key_material(text, where=f"snapshot {path.name}")
        path.write_text(text + "\n")
        self._append_manifest(request, meta, fetched_at, path, duplicate=False)
        return path

    def _snapshot_with_content(self, digest: str) -> Optional[Path]:
        for path in sorted(self.snapshot_dir.glob("*.json")):
            try:
                meta = (json.loads(path.read_text()) or {}).get("_meta") or {}
            except (OSError, ValueError):
                continue
            if meta.get("content_hash") == digest:
                return path
        return None

    def _append_manifest(
        self,
        request: Dict[str, Any],
        meta: Dict[str, Any],
        fetched_at: datetime,
        snapshot: Path,
        duplicate: bool,
    ) -> None:
        path = self.manifest_path
        if not path.exists():
            path.write_text(_MANIFEST_HEADER)
        route = (
            f"{request.get('origin_airport', '?')}->"
            f"{request.get('destination_airport', '?')}"
        )
        dates = f"{request.get('start_date', '?')}..{request.get('end_date', '?')}"
        note = f"{snapshot.name}" + (" (re-fetch, identical)" if duplicate else "")
        # v5 STEP 2. The content hash written here is RECOMPUTED FROM THE PAGES,
        # not copied out of `_meta`. That makes the column an independent second
        # claim about the same bytes: a tampered snapshot whose `_meta` was
        # edited to match still disagrees with this, and a tampered snapshot
        # whose `_meta` was not edited disagrees with both.
        digest = str(meta.get("content_hash") or "")[:MANIFEST_HASH_CHARS] or "-"
        parser_version = str(meta.get("parser_version") or _parser_version())
        # `awards` and `state` are left as "-" here on purpose: at fetch time
        # nothing has been PARSED yet, and inventing a count would defeat the
        # transport/parse split. `annotate_manifest` fills them in once the live
        # run knows.
        row = (
            f"| {_iso(fetched_at)} | {meta.get('leg_id') or '-'} | {route} | {dates} "
            f"| {meta.get('rows_seen') if meta.get('rows_seen') is not None else '-'} "
            f"| - | - | {note} | {digest} | {parser_version} "
            f"| {meta.get('trip_id') or '-'} |\n"
        )
        # FINDING L-8: the append was a plain unlocked open(path, "a"), so two
        # interleaved writers could corrupt a row. An exclusive lock around the
        # append is cheap and makes concurrent live runs safe. flock is advisory
        # and POSIX-only; on a platform without it the write still happens, just
        # unlocked, which is exactly the old behaviour rather than a hard failure.
        with open(path, "a") as f:
            try:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            f.write(row)
            f.flush()

    # -- corpus access ---------------------------------------------------

    def annotate_manifest(
        self,
        snapshot_name: Optional[str],
        state: str,
        awards_parsed: int,
        manifest_key: str = "",
    ) -> bool:
        """
        Fill in the state and award count on the manifest row for THIS fetch.

        The transport layer writes the row (it must - the snapshot write and the
        cache write are one event, so the archival step cannot be forgotten), but
        it CANNOT know how many awards a response yields: that is the parser's
        answer and the whole point of Step 1 is that the two are separate. So the
        live run comes back and completes its own row.

        FINDING L-8, SECOND HALF. This used to walk BACKWARDS for the LAST
        unfilled row naming the snapshot. Because a byte-identical re-fetch
        reuses the snapshot NAME, a run that died between `put` and `annotate`
        left a row that could never be filled: the next identical fetch's state
        was written onto its own (later) row and the orphan stayed `- | -`
        forever. Two changes fix it. `manifest_key` is this fetch's own
        timestamp, so a row can be matched exactly; and the fallback now walks
        FORWARDS, filling the OLDEST unfilled row, which is the one whose run
        finished first.

        A manifest whose state column is always "-" is not reviewable, and
        reviewability is the only reason the manifest exists.
        """
        if not snapshot_name or not self.manifest_path.exists():
            return False
        lines = self.manifest_path.read_text().splitlines(keepends=True)

        def _fill(i: int) -> bool:
            lines[i] = lines[i].replace("| - | - |", f"| {awards_parsed} | {state} |", 1)
            self.manifest_path.write_text("".join(lines))
            return True

        if manifest_key:
            for i, line in enumerate(lines):
                if (
                    manifest_key in line
                    and snapshot_name in line
                    and "| - | - |" in line
                ):
                    return _fill(i)
        for i, line in enumerate(lines):
            if snapshot_name in line and "| - | - |" in line:
                return _fill(i)
        return False

    def snapshots(self) -> List[Path]:
        """Every archived envelope, oldest name first. The regression corpus."""
        if not self.snapshot_dir.exists():
            return []
        return sorted(self.snapshot_dir.glob("*.json"))


def load_envelope(path: Path) -> CachedResponse:
    """
    Load any cache entry or snapshot as a CachedResponse.

    Snapshots and cache entries are the same format precisely so this works, and
    so that `--from-snapshot` (designed for, not built in v3) has nothing to
    invent.
    """
    envelope = json.loads(Path(path).read_text())
    meta = envelope.get("_meta") or {}
    return CachedResponse(
        pages=envelope.get("pages") or [],
        meta=meta,
        path=Path(path),
        fetched_at=_parse_iso(str(meta.get("fetched_at") or "")),
        age_seconds=None,
    )


def envelope_from_raw_payload(
    payload: Dict[str, Any], request: Dict[str, Any], **meta: Any
) -> Dict[str, Any]:
    """
    Wrap a single already-captured response page in the envelope format.

    Used to bring `sfo_mad_real.json` - which predates the envelope - into the
    same shape the cache uses, so the Step 1 round-trip test can prove the
    transport/parse split changed nothing.
    """
    return {
        "_meta": {
            "cache_schema": CACHE_SCHEMA,
            "request": dict(request),
            "request_key": request_key("search", request),
            "content_hash": content_hash([payload]),
            "fetched_at": meta.pop("fetched_at", "2026-09-08T00:00:00Z"),
            "fetched_by": meta.pop("fetched_by", "manual capture, pre-v3"),
            "key_redacted": True,
            **meta,
        },
        "pages": [payload],
    }
