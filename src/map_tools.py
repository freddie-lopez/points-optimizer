"""
The map's airport set, captured from Seats.aero on a machine that can reach it.
A separate module, like `src.trips_tools`, so `python -m src.main` keeps its
0-4 exit codes.

    python -m src.map_tools capture-hubs [--sources aeroplan,united,...]
        [--min-sources 3] [--min-routes 20] [--yes] [--out data/hubs.json]
        [--raw-dir DIR] [--api-key KEY]

    python -m src.map_tools mark-searchable [--file data/hubs.json]

`capture-hubs` asks `GET /partnerapi/routes?source=<code>` once per source (at
most one call per source; a cursor is recorded, never followed), derives the
airports that appear in the routes of at least --min-sources sources AND at
least --min-routes routes, joins coordinates from data/airportsdata_iata.csv,
marks each `searchable` (= in data/airports.csv, the set the engine accepts)
and writes data/hubs.json with full provenance in `_meta`. It prints the call
count and asks before spending; it prints a histogram of what other thresholds
would give before writing; it refuses, with a named reason and nothing on disk,
on every failure.

`mark-searchable` recomputes the `searchable` flag against the current
data/airports.csv without a call.

Exit codes: 0 written and every source answered; 1 nothing written (usage, no
key, declined, stdin closed, every source failed, 0 hubs met the thresholds,
key material in the output, or an existing capture that covers more sources
than this gapless run - NOT OVERWRITTEN without --force); 5 the run had gaps
(at least one source failed, was unreadable or answered with a cursor):
written with them recorded in `_meta`, or - when the existing file covers more
sources and --force was not passed - the existing file kept and nothing
written (the console says which).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.markup import escape

from src import config, regions, response_cache
from src.formatter import print_copyable

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_OUT = DATA_DIR / "hubs.json"
IATA_CSV = DATA_DIR / "airportsdata_iata.csv"
AIRPORTS_CSV = DATA_DIR / "airports.csv"
CAPTURED_BY = "src.map_tools capture-hubs"
ENDPOINT = "/partnerapi/routes"
AIRPORT_TABLE = "airportsdata 20260905 (MIT) via data/airportsdata_iata.csv"
EMPTY_NOTE = (
    "No capture has run; the map plots nothing until python -m src.map_tools "
    "capture-hubs is run on a machine with a Seats.aero key."
)

EXIT_OK = 0
EXIT_NOTHING_WRITTEN = 1
EXIT_GAPS = 5

DEFAULT_MIN_SOURCES = 3
DEFAULT_MIN_ROUTES = 20
HUB_WARN_COUNT = 400
HISTOGRAM_SOURCES = (1, 2, 3, 4, 5)
HISTOGRAM_ROUTES = (5, 10, 20, 50)
IATA_RE = re.compile(r"^[A-Z0-9]{3}$")

META_KEYS = (
    "captured_by", "captured_at", "endpoint", "sources_asked", "sources_ok",
    "sources_failed", "sources_incomplete", "routes_seen", "thresholds",
    "airport_table", "engine_airports_sha256", "searchable_marked_at",
    "key_redacted", "synthetic",
)
HUB_KEYS = ("iata", "name", "city", "country", "lat", "lon", "routes", "sources",
            "regions", "searchable")


class ToolRefusal(Exception):
    """Nothing is written. Printed and exit 1."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.map_tools",
        description=(
            "Capture the airports Seats.aero tracks, for the map on the Search "
            "tab. capture-hubs spends Seats.aero calls and asks before it does."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "EXIT CODES:\n"
            "  0  written and every source answered\n"
            "  1  nothing written (usage, no key, declined, stdin closed, every\n"
            "     source failed, 0 hubs met the thresholds, key material in the output)\n"
            "  5  the run had gaps (a source failed, was unreadable or answered with\n"
            "     a cursor): written with them in _meta, or the existing more complete\n"
            "     file kept (without --force)\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")
    cap = sub.add_parser("capture-hubs", help="One GET /partnerapi/routes per source.")
    cap.add_argument("--sources", default=None, metavar="a,b,c",
                     help="Source codes to ask (default: every code in SEATS_AERO_SOURCES).")
    cap.add_argument("--min-sources", type=int, default=None, metavar="N",
                     help=f"default {DEFAULT_MIN_SOURCES}, or the number of sources asked when fewer")
    cap.add_argument("--min-routes", type=int, default=DEFAULT_MIN_ROUTES, metavar="N")
    cap.add_argument("--yes", action="store_true", help="Do not ask before spending calls.")
    cap.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    cap.add_argument("--raw-dir", default=None, metavar="DIR",
                     help="Also write each verbatim response body as routes_<source>.json.")
    cap.add_argument("--api-key", default=None, metavar="KEY")
    cap.add_argument("--force", action="store_true",
                     help="Replace an existing capture even when it is more complete than this run.")
    mark = sub.add_parser("mark-searchable",
                          help="Recompute `searchable` against data/airports.csv. 0 calls.")
    mark.add_argument("--file", default=str(DEFAULT_OUT), metavar="PATH")
    return parser


# ---------------------------------------------------------------------------
# The file's shape
# ---------------------------------------------------------------------------


def airports_csv_sha256(path: Optional[Path] = None) -> str:
    return hashlib.sha256(Path(path or AIRPORTS_CSV).read_bytes()).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_document() -> Dict[str, Any]:
    """The committed state before any capture: `hubs: []` and a note saying so."""
    return {
        "_meta": {
            "captured_by": None,
            "captured_at": None,
            "endpoint": None,
            "sources_asked": [],
            "sources_ok": [],
            "sources_failed": {},
            "sources_incomplete": [],
            "routes_seen": 0,
            "thresholds": {"min_sources": DEFAULT_MIN_SOURCES, "min_routes": DEFAULT_MIN_ROUTES},
            "airport_table": AIRPORT_TABLE,
            "engine_airports_sha256": None,
            "searchable_marked_at": None,
            "key_redacted": True,
            "synthetic": False,
            "note": EMPTY_NOTE,
        },
        "hubs": [],
    }


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num_or_none(x: Any) -> bool:
    return x is None or (isinstance(x, (int, float)) and not isinstance(x, bool))


def hubs_file_problems(doc: Any) -> List[str]:
    """Every way a hubs.json can fail the shape src.map_tools writes (empty list = fine)."""
    out: List[str] = []
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    meta, hubs = doc.get("_meta"), doc.get("hubs")
    if not isinstance(meta, dict):
        return ["_meta is not an object"]
    if not isinstance(hubs, list):
        return ["hubs is not a list"]
    for key in META_KEYS:
        if key not in meta:
            out.append(f"_meta.{key} is missing")
    if out:
        return out
    if not isinstance(meta["sources_asked"], list) or not isinstance(meta["sources_ok"], list):
        out.append("_meta.sources_asked / sources_ok must be lists")
    if not isinstance(meta["sources_failed"], dict):
        out.append("_meta.sources_failed must be an object")
    if not isinstance(meta["sources_incomplete"], list):
        out.append("_meta.sources_incomplete must be a list")
    if not _is_int(meta["routes_seen"]) or meta["routes_seen"] < 0:
        out.append("_meta.routes_seen must be a non-negative integer")
    th = meta["thresholds"]
    if not (isinstance(th, dict) and _is_int(th.get("min_sources")) and _is_int(th.get("min_routes"))):
        out.append("_meta.thresholds must carry integer min_sources and min_routes")
    if meta["key_redacted"] is not True:
        out.append("_meta.key_redacted must be true")
    if not isinstance(meta["synthetic"], bool):
        out.append("_meta.synthetic must be a boolean")
    empty = not hubs and meta["captured_at"] is None
    if empty and meta.get("note") != EMPTY_NOTE:
        out.append("an empty, uncaptured file must carry the empty-state note")
    if not empty and "note" in meta:
        out.append("_meta.note belongs only to the empty, uncaptured file")
    if hubs and meta["captured_at"] is None:
        out.append("a file with hubs must record captured_at")
    codes: List[str] = []
    for i, hub in enumerate(hubs):
        where = f"hubs[{i}]"
        if not isinstance(hub, dict):
            out.append(f"{where} is not an object")
            continue
        missing = [k for k in HUB_KEYS if k not in hub]
        if missing:
            out.append(f"{where} lacks {missing}")
            continue
        code = hub["iata"]
        if not isinstance(code, str) or not IATA_RE.match(code):
            out.append(f"{where}.iata {code!r} is not three upper-case letters or digits")
        else:
            codes.append(code)
        if not _is_int(hub["routes"]) or hub["routes"] < 1:
            out.append(f"{where}.routes must be an integer >= 1")
        if not isinstance(hub["sources"], list) or not hub["sources"] \
                or not all(isinstance(s, str) for s in hub["sources"]):
            out.append(f"{where}.sources must be a non-empty list of strings")
        if not isinstance(hub["regions"], list):
            out.append(f"{where}.regions must be a list")
        if not (_is_num_or_none(hub["lat"]) and _is_num_or_none(hub["lon"])):
            out.append(f"{where}.lat/lon must be numbers or null")
        if (hub["lat"] is None) != (hub["lon"] is None):
            out.append(f"{where}.lat and lon must be both present or both null")
        if not isinstance(hub["searchable"], bool):
            out.append(f"{where}.searchable must be a boolean")
        for k in ("name", "city", "country"):
            if not isinstance(hub[k], str):
                out.append(f"{where}.{k} must be a string")
    if codes != sorted(codes):
        out.append("hubs must be sorted by iata")
    if len(set(codes)) != len(codes):
        out.append("an iata code appears twice")
    return out


def render_document(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_atomically(path: Path, text: str) -> None:
    """tmp + rename. Any disk error is a ToolRefusal and the tmp is removed:
    the target is never partial and nothing is left beside it."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ToolRefusal(
            f"{path} could not be written ({type(e).__name__}: {e}). Nothing was written."
        ) from None


# ---------------------------------------------------------------------------
# Deriving hubs from routes
# ---------------------------------------------------------------------------


class Tally:
    __slots__ = ("routes", "sources", "regions")

    def __init__(self):
        self.routes = 0
        self.sources: Set[str] = set()
        self.regions: Set[str] = set()


class TallyResult:
    """What tally_routes counted: the per-airport tally and every drop, named."""

    __slots__ = ("tally", "seen", "not_objects", "bad_codes", "unmapped_regions")

    def __init__(self):
        self.tally: Dict[str, Tally] = {}
        self.seen = 0            # route rows that were objects
        self.not_objects = 0     # rows that were not objects (no code to read)
        self.bad_codes = 0       # route sides whose code is not ^[A-Z0-9]{3}$
        self.unmapped_regions = 0  # region labels not in regions.SEATS_AERO_REGIONS


def tally_routes(routes_by_source: Dict[str, List[Any]]) -> TallyResult:
    """An airport is counted once per route SIDE it appears on; a side whose
    code is not three upper-case letters or digits is dropped and counted,
    never normalised into a hub. A region label is stored only as the REGIONS
    code `regions.SEATS_AERO_REGIONS` maps it to (free text from the wire is
    counted, never stored)."""
    r = TallyResult()
    for source, rows in routes_by_source.items():
        for row in rows:
            if not isinstance(row, dict):
                r.not_objects += 1
                continue
            r.seen += 1
            for code_key, region_key in (("OriginAirport", "OriginRegion"),
                                         ("DestinationAirport", "DestinationRegion")):
                code = row.get(code_key)
                if not isinstance(code, str) or not IATA_RE.match(code):
                    r.bad_codes += 1
                    continue
                t = r.tally.setdefault(code, Tally())
                t.routes += 1
                t.sources.add(source)
                label = row.get(region_key)
                if isinstance(label, str) and label.strip():
                    mapped = regions.region_from_seats_aero(label)
                    if mapped:
                        t.regions.add(mapped)
                    else:
                        r.unmapped_regions += 1
    return r


def count_at(tally: Dict[str, Tally], min_sources: int, min_routes: int) -> int:
    return sum(1 for t in tally.values()
               if len(t.sources) >= min_sources and t.routes >= min_routes)


def histogram_lines(tally: Dict[str, Tally]) -> List[str]:
    head = "  min-sources \\ min-routes" + "".join(f"{r:>8}" for r in HISTOGRAM_ROUTES)
    lines = ["hubs at each threshold pair (airports meeting BOTH):", head]
    for s in HISTOGRAM_SOURCES:
        lines.append(f"  {s:>25}" + "".join(f"{count_at(tally, s, r):>8}" for r in HISTOGRAM_ROUTES))
    return lines


def load_coordinates(path: Path = IATA_CSV) -> Dict[str, Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return {row["iata"]: row for row in csv.DictReader(f)}


def derive_hubs(tally: Dict[str, Tally], min_sources: int, min_routes: int,
                coords: Dict[str, Dict[str, str]], known: Set[str]) -> List[Dict[str, Any]]:
    hubs = []
    for code in sorted(tally):
        t = tally[code]
        if len(t.sources) < min_sources or t.routes < min_routes:
            continue
        row = coords.get(code)
        hubs.append({
            "iata": code,
            "name": (row or {}).get("name", ""),
            "city": (row or {}).get("city", ""),
            "country": (row or {}).get("country", ""),
            "lat": float(row["lat"]) if row else None,
            "lon": float(row["lon"]) if row else None,
            "routes": t.routes,
            "sources": sorted(t.sources),
            "regions": sorted(t.regions),
            "searchable": code in known,
        })
    return hubs


# ---------------------------------------------------------------------------
# capture-hubs
# ---------------------------------------------------------------------------


def _call_count_line(n: int) -> str:
    from src.seats_client import SeatsClient

    spent = SeatsClient.DAILY_CALL_CAP - SeatsClient._budget_remaining()
    return (
        f"This will make at most {n} Seats.aero API call(s): one GET {ENDPOINT} per "
        f"source ({n} sources). This process has spent {spent} of "
        f"{SeatsClient.DAILY_CALL_CAP:,}; Seats.aero also counts your other runs "
        f"today, which this tool cannot see."
    )


class SourceAnswer:
    __slots__ = ("status", "reason", "routes", "body")

    def __init__(self, status: str, reason: str = "", routes=None, body: str = ""):
        self.status = status      # ok | failed | unreadable | incomplete
        self.reason = reason
        self.routes = routes if routes is not None else []
        self.body = body


def _read_routes(payload: Any) -> Tuple[str, str, List[Any]]:
    """(status, reason, routes) for a decoded body: a list, or {"data": [...]}."""
    if isinstance(payload, list):
        return "ok", "", payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            if payload.get("hasMore") or payload.get("cursor"):
                return "incomplete", "answered with hasMore/cursor; not followed", data
            return "ok", "", data
        return "unreadable", "top level is an object without a data list", []
    return "unreadable", f"top level is a {type(payload).__name__}", []


def _ask_source(get: Callable, code: str, key: str) -> SourceAnswer:
    import requests

    from src.seats_client import SeatsClient

    try:
        response = get(
            f"{SeatsClient.BASE_URL}/routes",
            params={"source": code},
            headers={"Partner-Authorization": key, "Accept": "application/json"},
            timeout=30,
        )
    except requests.Timeout:
        return SourceAnswer("failed", "Timeout")
    except requests.ConnectionError:
        return SourceAnswer("failed", "ConnectionError")
    except Exception as e:  # noqa: BLE001 - a failed source is recorded, never a crash
        return SourceAnswer("failed", type(e).__name__)
    status = getattr(response, "status_code", None)
    body = getattr(response, "text", "")
    body = body if isinstance(body, str) else ""
    if status != 200:
        return SourceAnswer("failed", f"HTTP {status}", body=body)
    try:
        payload = response.json()
    except ValueError as e:
        return SourceAnswer("unreadable", f"not JSON ({type(e).__name__})", body=body)
    kind, reason, routes = _read_routes(payload)
    return SourceAnswer(kind, reason, routes, body)


def _parse_sources(flag: Optional[str]) -> List[str]:
    from src.seats_client import SEATS_AERO_SOURCES

    if not flag:
        return list(SEATS_AERO_SOURCES)
    wanted = []
    for s in (x.strip().lower() for x in flag.split(",")):
        if s and s not in wanted:
            wanted.append(s)
    bogus = [s for s in wanted if s not in SEATS_AERO_SOURCES]
    if bogus:
        raise ToolRefusal(
            f"--sources names {', '.join(repr(b) for b in bogus)}, which is not a "
            f"Seats.aero source code this tool knows ({', '.join(SEATS_AERO_SOURCES)}). "
            f"No call was made and nothing was written."
        )
    if not wanted:
        raise ToolRefusal("--sources is empty. No call was made and nothing was written.")
    return wanted


def run_capture(args, console: Console, read: Callable[[str], str],
                get: Optional[Callable] = None) -> int:
    from src.seats_client import SeatsClient

    if get is None:
        import requests

        get = requests.get
    explicit = args.min_sources is not None
    if (explicit and args.min_sources < 1) or args.min_routes < 1:
        raise ToolRefusal("--min-sources and --min-routes must be at least 1. No call was "
                          "made and nothing was written.")
    sources = _parse_sources(args.sources)
    if explicit and args.min_sources > len(sources):
        # Decidable before the prompt: refuse rather than spend the calls.
        raise ToolRefusal(
            f"--min-sources {args.min_sources} can never be met by the {len(sources)} "
            f"source(s) asked; no airport could be a hub. No call was made and nothing "
            f"was written."
        )
    if not explicit:
        args.min_sources = min(DEFAULT_MIN_SOURCES, len(sources))
        if args.min_sources < DEFAULT_MIN_SOURCES:
            console.print(
                f"--min-sources not given: using {args.min_sources}, the number of "
                f"sources asked (the default {DEFAULT_MIN_SOURCES} could never be met). "
                f"Recorded in _meta.thresholds."
            )
    out_path = Path(args.out)
    raw_dir = Path(args.raw_dir) if args.raw_dir else None
    try:
        resolution = config.resolve_key(args.api_key)
    except config.KeyResolutionError as e:
        raise ToolRefusal(str(e)) from None
    console.print(f"[dim]{escape(resolution.describe())}[/dim]")
    console.print(_call_count_line(len(sources)))
    if not args.yes:
        try:
            answer = read("Continue? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            raise ToolRefusal(
                "no answer at the prompt (stdin closed or interrupted). No call was made "
                "and nothing was written. Pass --yes to run without the question."
            ) from None
        if str(answer or "").strip().lower() not in ("y", "yes"):
            raise ToolRefusal("declined. No call was made and nothing was written.")

    key = resolution.key
    answers: Dict[str, SourceAnswer] = {}
    rate_limited = False
    for code in sources:
        if rate_limited:
            answers[code] = SourceAnswer("failed", "not asked: rate limited")
            continue
        if SeatsClient._budget_remaining() <= 0:
            answers[code] = SourceAnswer("failed", "not asked: daily call budget exhausted")
            continue
        SeatsClient._count_call()
        ans = _ask_source(get, code, key)
        if ans.body and key in ans.body:
            raise ToolRefusal(
                f"the response for source {code!r} contains the Seats.aero key this run "
                f"resolved. Key material must never reach a file that gets committed. "
                f"Nothing was written."
            )
        if ans.status == "failed" and ans.reason == "HTTP 429":
            rate_limited = True
        answers[code] = ans
        if ans.status == "ok":
            console.print(f"  {code}: ok ({len(ans.routes)} routes)")
        elif ans.status == "incomplete":
            console.print(f"  [yellow]{code}: incomplete ({ans.reason}; "
                          f"{len(ans.routes)} routes on this page)[/yellow]")
        else:
            console.print(f"  [yellow]{code}: {ans.status}({escape(ans.reason)})[/yellow]")
        if raw_dir is not None and ans.body:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"routes_{code}.json").write_text(ans.body, encoding="utf-8")
    spent = SeatsClient.DAILY_CALL_CAP - SeatsClient._budget_remaining()
    console.print(f"This process has spent {spent} of {SeatsClient.DAILY_CALL_CAP:,} today.")

    ok = [c for c, a in answers.items() if a.status == "ok"]
    incomplete = [c for c, a in answers.items() if a.status == "incomplete"]
    failed = {c: (a.reason if a.status == "failed" else f"unreadable: {a.reason}")
              for c, a in answers.items() if a.status in ("failed", "unreadable")}
    if not ok and not incomplete:
        raise ToolRefusal(f"every source failed ({len(sources)} of {len(sources)}); "
                          f"nothing was written.")

    routes_by_source = {c: a.routes for c, a in answers.items() if a.status in ("ok", "incomplete")}
    tr = tally_routes(routes_by_source)
    tally, seen = tr.tally, tr.seen
    console.print(f"{seen} routes seen across {len(routes_by_source)} source(s); "
                  f"{tr.bad_codes} route side(s) dropped for a code that is not three "
                  f"upper-case letters or digits; {tr.not_objects} row(s) that were not "
                  f"objects ignored; {tr.unmapped_regions} region label(s) not in the "
                  f"known Seats.aero regions counted, not stored.")
    for line in histogram_lines(tally):
        console.print(escape(line))
    coords = load_coordinates()
    known = set(regions.known_airports())
    hubs = derive_hubs(tally, args.min_sources, args.min_routes, coords, known)
    if not hubs:
        raise ToolRefusal(
            f"0 airports met the thresholds (--min-sources {args.min_sources}, "
            f"--min-routes {args.min_routes}); nothing was written. The histogram above "
            f"shows what lower thresholds would give."
        )
    searchable = [h for h in hubs if h["searchable"]]
    no_coords = [h for h in hubs if h["lat"] is None]
    unsearchable = [h for h in hubs if not h["searchable"]]
    console.print(
        f"{len(hubs)} hubs ({len(searchable)} searchable, {len(no_coords)} without "
        f"coordinates, {len(unsearchable)} not in data/airports.csv)"
    )
    if len(hubs) > HUB_WARN_COUNT:
        console.print(
            f"[yellow]{len(hubs)} hubs is above the {HUB_WARN_COUNT} the map was sized "
            f"for; consider higher thresholds.[/yellow]"
        )
    if unsearchable:
        console.print(
            "Not in data/airports.csv (the map does not plot these; adding one is a "
            "surcharge-region decision, not made here) - iata,name,country:"
        )
        for h in unsearchable:
            console.print("  " + escape(f"{h['iata']},{h['name']},{h['country']}"))

    now = _utcnow()
    doc = {
        "_meta": {
            "captured_by": CAPTURED_BY,
            "captured_at": now,
            "endpoint": ENDPOINT,
            "sources_asked": list(sources),
            "sources_ok": ok,
            "sources_failed": failed,
            "sources_incomplete": incomplete,
            "routes_seen": seen,
            "thresholds": {"min_sources": args.min_sources, "min_routes": args.min_routes},
            "airport_table": AIRPORT_TABLE,
            "engine_airports_sha256": airports_csv_sha256(),
            "searchable_marked_at": now,
            "key_redacted": True,
            "synthetic": False,
        },
        "hubs": hubs,
    }
    problems = hubs_file_problems(doc)
    if problems:  # pragma: no cover - the writer and the checker agree by test
        raise ToolRefusal(f"the derived file fails its own shape check ({problems[0]}); "
                          f"nothing was written.")
    text = render_document(doc)
    try:
        response_cache.assert_no_key_material(text, where=str(out_path), key=key)
    except ValueError as e:
        raise ToolRefusal(f"{e} Nothing was written.") from None
    gaps = len(failed) + len(incomplete)
    kept = None if args.force else _more_complete_existing(out_path, ok)
    if kept is not None:
        # Whatever this run's gaps: a file that covers more sources (or a
        # superset of these) is the more complete capture and stays.
        console.print(
            f"[bold yellow]NOT OVERWRITTEN: {out_path} holds a capture with {kept} source(s) "
            f"ok, this run has {len(ok)}. The existing file is kept and nothing was written. "
            f"Pass --force to replace it with this run.[/bold yellow]"
        )
        return EXIT_GAPS if gaps else EXIT_NOTHING_WRITTEN
    _write_atomically(out_path, text)
    print_copyable(console, f"wrote {out_path}", "green")
    if gaps:
        console.print(
            f"[bold yellow]WRITTEN WITH GAPS: {len(failed)} source(s) failed or were "
            f"unreadable, {len(incomplete)} answered with a cursor that was not "
            f"followed. _meta in the file lists them; the map's footer will not.[/bold yellow]"
        )
        return EXIT_GAPS
    console.print("[green]every source answered.[/green]")
    return EXIT_OK


def _more_complete_existing(path: Path, ok_now: List[str]) -> Optional[int]:
    """The existing file's sources_ok count when that capture is more complete
    than this run: more sources answered, or a strict superset of the ones that
    answered now. Else None (an equal or narrower file, or no readable file)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if hubs_file_problems(doc) or not doc["hubs"]:
        return None
    before = {s for s in doc["_meta"]["sources_ok"] if isinstance(s, str)}
    now = set(ok_now)
    if len(before) > len(now) or (before > now):
        return len(before)
    return None


# ---------------------------------------------------------------------------
# mark-searchable
# ---------------------------------------------------------------------------


def run_mark_searchable(args, console: Console) -> int:
    path = Path(args.file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolRefusal(f"{path} could not be read ({type(e).__name__}: {e}). "
                          f"Nothing was written.") from None
    problems = hubs_file_problems(doc)
    if problems:
        raise ToolRefusal(f"{path} is not a hubs file this tool wrote ({problems[0]}). "
                          f"Nothing was written.")
    known = set(regions.known_airports())
    changed = 0
    for hub in doc["hubs"]:
        flag = hub["iata"] in known
        if hub["searchable"] != flag:
            hub["searchable"] = flag
            changed += 1
    doc["_meta"]["engine_airports_sha256"] = airports_csv_sha256()
    doc["_meta"]["searchable_marked_at"] = _utcnow()
    if not doc["hubs"]:
        # The empty file's provenance stays "no capture has run".
        doc["_meta"]["engine_airports_sha256"] = None
        doc["_meta"]["searchable_marked_at"] = None
    _write_atomically(path, render_document(doc))
    console.print(f"{changed} hub(s) changed searchable; 0 calls made.")
    print_copyable(console, f"wrote {path}", "green")
    return EXIT_OK


def main(
    argv: Optional[List[str]] = None,
    *,
    read: Callable[[str], str] = input,
    console: Optional[Console] = None,
    get: Optional[Callable] = None,
) -> int:
    console = console or Console(width=190)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return EXIT_NOTHING_WRITTEN if e.code not in (0, None) else EXIT_OK
    if not args.command:
        console.print(escape(parser.format_help()))
        return EXIT_NOTHING_WRITTEN
    try:
        if args.command == "capture-hubs":
            return run_capture(args, console, read, get)
        return run_mark_searchable(args, console)
    except ToolRefusal as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        return EXIT_NOTHING_WRITTEN


if __name__ == "__main__":
    import sys

    config.use_utf8_output()
    sys.exit(main())
