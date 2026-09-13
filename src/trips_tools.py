"""
Two tools for the operating-airline lookup, run on a machine that can reach
seats.aero. A separate module so `python -m src.main` keeps its 0-4 exit codes.

    python -m src.trips_tools capture (--availability-id ID
        | --origin O --destination D --date YYYY-MM-DD --source CODE)
        [--cabin J] [--yes] [--refresh] [--out-dir DIR] [--api-key KEY]

    python -m src.trips_tools yq-check --origin O --destination D
        --date YYYY-MM-DD --source CODE --cabin X
        [--yes] [--refresh] [--record-dir DIR] [--out-dir DIR] [--api-key KEY]

`capture` writes ONE real trips response (key redacted, with its verbatim body
beside it) and prints a drift report against the published schema. A clean
capture is what can flip the trips parser's UNVERIFIED label.

`yq-check` is a capture plus a block to compare against the program's own
booking site, and a record with the site half left blank. It settles, for ONE
source, whether Seats.aero's TotalTaxes already includes carrier surcharges.

Exit codes: 0 captured and clean (the label check accepts the file); 1 nothing
captured (usage, no key, declined, HTTP or network error, key material detected,
refused input); 5 captured with drift, or captured but refused by the label
check (the file IS written; do not flip the label).
"""
import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console
from rich.markup import escape

from src import config, response_cache, seats_trips
from src.formatter import print_copyable
from src.models import DateRange, MetalStatus

ROOT = Path(__file__).parent.parent
DEFAULT_REAL_DIR = ROOT / "tests" / "fixtures" / "seats_aero" / "trips_endpoint" / "real"
DEFAULT_RECORD_DIR = ROOT / "docs" / "yq-checks"
CAPTURED_BY = "src.trips_tools capture"

EXIT_OK = 0
EXIT_NOTHING_CAPTURED = 1
EXIT_DRIFT = 5

CABIN_LETTERS = ("Y", "W", "J", "F")

# The one test that reads every committed capture's required fields: the only
# test a capture that drifted leaves red until the parser is fixed.
DRIFT_TEST = (
    "tests/test_trips_verification_label.py::"
    "test_every_committed_real_capture_parses_without_required_field_drift"
)

# The airline each direct-partner source flies itself, for the INCONCLUSIVE
# warning, and the site a reader compares against.
OWN_METAL = {
    "virginatlantic": "VS",
    "flyingblue": "AF or KL",
    "jetblue": "B6",
    "singapore": "SQ",
    "united": "UA",
    "aeroplan": "AC",
}
PROGRAM_SITE = {
    "virginatlantic": "virginatlantic.com",
    "flyingblue": "flyingblue.com",
    "jetblue": "jetblue.com",
    "singapore": "singaporeair.com",
    "united": "united.com",
    "aeroplan": "aircanada.com",
}


class ToolRefusal(Exception):
    """Nothing is captured. Printed and exit 1."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.trips_tools",
        description=(
            "Capture a real Seats.aero trips response, or run a YQ check. Both "
            "spend Seats.aero calls and ask before they do."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "EXIT CODES:\n"
            "  0  captured and clean: the label check accepts the file\n"
            "  1  nothing captured (usage, no key, declined, HTTP or network error,\n"
            "     key material detected, refused input)\n"
            "  5  captured WITH drift, or refused by the label check - the file is\n"
            "     written; do not flip the label\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    def common(p, route_required: bool):
        p.add_argument("--origin", required=route_required)
        p.add_argument("--destination", required=route_required)
        p.add_argument("--date", required=route_required, metavar="YYYY-MM-DD")
        p.add_argument("--source", required=route_required, metavar="CODE")
        p.add_argument("--yes", action="store_true", help="Do not ask before spending calls.")
        p.add_argument(
            "--out-dir", default=None, metavar="DIR",
            help=f"Where the capture is written (default {DEFAULT_REAL_DIR}).",
        )
        p.add_argument("--api-key", default=None, metavar="KEY")
        p.add_argument(
            "--refresh", action="store_true",
            help=(
                "Fetch the search even if the disk cache holds an answer for it (a "
                "cached row can carry a stale availability id or price). The search "
                "call is then always spent."
            ),
        )

    cap = sub.add_parser("capture", help="Capture one real trips response.")
    cap.add_argument("--availability-id", default=None, metavar="ID")
    cap.add_argument("--cabin", default=None, metavar="Y|W|J|F")
    common(cap, route_required=False)

    yq = sub.add_parser("yq-check", help="Capture plus a YQ comparison record.")
    yq.add_argument("--cabin", required=True, metavar="Y|W|J|F")
    yq.add_argument(
        "--record-dir", default=None, metavar="DIR",
        help=f"Where the yq-check record is written (default {DEFAULT_RECORD_DIR}).",
    )
    common(yq, route_required=True)
    return parser


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _rows_of(pages) -> List[Dict[str, Any]]:
    out = []
    for page in pages or []:
        data = page.get("data") if isinstance(page, dict) else None
        if isinstance(data, list):
            out.extend(r for r in data if isinstance(r, dict))
    return out


def _row_date(row: Dict[str, Any]) -> str:
    return str(row.get("Date") or (row.get("ParsedDate") or "")[:10])


def _row_source(row: Dict[str, Any]) -> str:
    return str((row.get("Route") or {}).get("Source") or "").strip().lower()


def _row_route(row: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    route = (row or {}).get("Route") or {}
    return (
        str(route.get("OriginAirport") or "").upper(),
        str(route.get("DestinationAirport") or "").upper(),
    )


def _find_row_locally(availability_id: str) -> Optional[Dict[str, Any]]:
    """The availability row with this ID in the local cache or corpus. 0 calls."""
    for directory in (config.CACHE_DIR, config.SNAPSHOT_DIR):
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                envelope = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            for row in _rows_of(envelope.get("pages")):
                if row.get("ID") == availability_id:
                    return row
    return None


def _inferred_route(payload: Any) -> Tuple[str, str]:
    """The (origin, destination) most itineraries run, for a capture with no row."""
    counts: Dict[Tuple[str, str], int] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    for trip in data if isinstance(data, list) else []:
        segs = trip.get("AvailabilitySegments") if isinstance(trip, dict) else None
        if not isinstance(segs, list) or not segs:
            continue
        ordered = [s for s in segs if isinstance(s, dict)]
        try:
            ordered.sort(key=lambda s: s.get("Order"))
        except TypeError:
            pass
        if not ordered:
            continue
        key = (
            str(ordered[0].get("OriginAirport") or "").upper(),
            str(ordered[-1].get("DestinationAirport") or "").upper(),
        )
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "", ""
    return max(counts, key=counts.get)


def _unique_path(path: Path) -> Path:
    """Never overwrite a capture: a second one the same day gets a time suffix."""
    if not path.exists():
        return path
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def _awards_of(row: Optional[Dict[str, Any]], cabin: Optional[str]):
    from src.seats_client import parse_availability_row

    if not row:
        return []
    awards = parse_availability_row(row)
    if cabin:
        awards = [a for a in awards if a.award_type == cabin]
    return awards


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


class Capture:
    """Everything a capture produced, for the report and for yq-check."""

    def __init__(self):
        self.row: Optional[Dict[str, Any]] = None
        self.flip_problems: List[str] = []
        self.search_request: Optional[Dict[str, str]] = None
        self.availability_id = ""
        self.payload: Any = None
        self.raw = None
        self.parsed = None
        self.route: Tuple[str, str] = ("", "")
        self.route_inferred = False
        self.path: Optional[Path] = None
        self.raw_path: Optional[Path] = None
        self.lookups: Dict[str, Any] = {}
        self.blocking_drift: List[str] = []
        # yq-check only: why this check can back NO verdict ("" when it can).
        self.no_verdict_reason = ""
        self.banded_options: List[str] = []


def _validate(args, command: str) -> Optional[str]:
    """(error) for arguments that cannot be run, or None."""
    route_flags = [args.origin, args.destination, args.date, args.source]
    aid = getattr(args, "availability_id", None)
    if command == "capture":
        if aid and any(route_flags):
            return (
                "give EITHER --availability-id OR --origin/--destination/--date/"
                "--source, not both"
            )
        if not aid and not all(route_flags):
            return (
                "give --availability-id ID, or all four of --origin, --destination, "
                "--date and --source"
            )
        if aid and not seats_trips.valid_availability_id(aid):
            return f"--availability-id {aid!r} is not a plain 10-64 character id"
        if aid and getattr(args, "refresh", False):
            return (
                "--refresh bypasses the SEARCH cache, and --availability-id makes no "
                "search: drop one of them"
            )
    if all(route_flags) or command == "yq-check":
        try:
            date.fromisoformat(str(args.date))
        except ValueError:
            return f"--date {args.date!r} is not YYYY-MM-DD"
    cabin = getattr(args, "cabin", None)
    if cabin and str(cabin).upper() not in CABIN_LETTERS:
        return f"--cabin {cabin!r} is not one of {', '.join(CABIN_LETTERS)}"
    return None


def _call_count_line(id_mode: bool, refresh: bool = False) -> str:
    from src.seats_client import SeatsClient

    spent = SeatsClient.DAILY_CALL_CAP - SeatsClient._budget_remaining()
    if id_mode:
        calls = "1 trips"
    elif refresh:
        calls = "1 search (--refresh: never served from the disk cache) + 1 trips"
    else:
        calls = "1 search (0 if served from the disk cache) + 1 trips"
    return (
        f"This will make at most {1 if id_mode else 2} Seats.aero API call(s): "
        f"{calls}. This process has spent {spent} of "
        f"{SeatsClient.DAILY_CALL_CAP:,}; Seats.aero also counts your other runs "
        f"today, which this tool cannot see."
    )


def _resolve_row_by_search(client, args, console) -> Tuple[Dict[str, Any], Dict[str, str]]:
    from src.response_cache import ResponseCache
    from src.seats_client import SeatsAeroError

    day = date.fromisoformat(args.date)
    # The runtime cache only: a capture tool never writes the committed corpus.
    cache = ResponseCache(cache_dir=config.CACHE_DIR, snapshot_dir=None)
    # The prompt promised ONE search call. Pagination is capped at one page on
    # THIS client, and a search that says there is more is refused below
    # rather than followed: the row picked from page one might not be the only
    # match.
    client.MAX_PAGES = 1
    try:
        raw = client.search_raw(
            args.origin.upper(), args.destination.upper(), DateRange(day, day), cache=cache,
            refresh=bool(getattr(args, "refresh", False)),
        )
    except SeatsAeroError as e:
        raise ToolRefusal(f"the search failed ({e}). Nothing was captured.") from None
    if raw.budget_exhausted:
        raise ToolRefusal("the call budget ran out before the search. Nothing was captured.")
    if raw.incomplete:
        # Re-test 2, R2-3. This one-page answer was cut short by the capture's
        # OWN page cap, under the same key a trip leg uses for this route and
        # day. Left in the shared runtime cache, the next trip run would be
        # served page one and never follow the pages it would have fetched.
        # So the entry this search just wrote is removed. (An incomplete answer
        # that was already in the cache was not written by this tool: left as
        # it is.)
        if raw.served_from_cache:
            leftover = (
                " The incomplete search was served from the disk cache, where an "
                "earlier run left it; it is left as it is."
            )
        else:
            leftover = " The one-page search was not kept in the cache."
            if raw.cache_path is not None:
                try:
                    Path(raw.cache_path).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    leftover = (
                        f" The one-page search could NOT be removed from the cache "
                        f"({raw.cache_path}: {e}); run the trip with --refresh."
                    )
        raise ToolRefusal(
            f"the search is INCOMPLETE ({raw.incomplete_reason or 'Seats.aero says there is more'}). "
            f"Following it would spend more calls than the one search promised, and "
            f"page one alone cannot show the matching row is the only one. No trips "
            f"call was made and nothing was captured.{leftover} Narrow the search, or use "
            f"--availability-id with the row's ID."
        )
    console.print(
        f"search: {'served from the disk cache' if raw.served_from_cache else 'fetched'} "
        f"({len(_rows_of(raw.pages))} availability row(s))"
    )
    code = args.source.strip().lower()
    rows = _rows_of(raw.pages)
    picked = [r for r in rows if _row_source(r) == code and _row_date(r) == args.date]
    if len(picked) != 1:
        listing = "; ".join(
            f"{r.get('ID')} {_row_source(r)} {_row_date(r)}" for r in rows[:20]
        ) or "(none)"
        raise ToolRefusal(
            f"{len(picked)} availability rows match source {code!r} on {args.date}; "
            f"exactly one is needed. Rows seen: {listing}. Nothing was captured."
        )
    return picked[0], dict(raw.request)


def run_capture(
    args, console: Console, read: Callable[[str], str], *, command: str = "capture",
    before_trips: Optional[Callable[["Capture"], None]] = None,
) -> Tuple[int, "Capture"]:
    from src.seats_client import SeatsClient, TripsLookupError

    cap = Capture()
    error = _validate(args, command)
    if error:
        raise ToolRefusal(error)
    try:
        resolution = config.resolve_key(args.api_key)
    except config.KeyResolutionError as e:
        raise ToolRefusal(str(e)) from None
    console.print(f"[dim]{escape(resolution.describe())}[/dim]")

    id_mode = bool(getattr(args, "availability_id", None))
    console.print(_call_count_line(id_mode, bool(getattr(args, "refresh", False))))
    if not args.yes:
        try:
            answer = read("Continue? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            # Piped or closed stdin, or Ctrl-C at the question: no answer is
            # not a yes.
            raise ToolRefusal(
                "no answer at the prompt (stdin closed or interrupted). No call was "
                "made and nothing was captured. Pass --yes to run without the question."
            ) from None
        if str(answer or "").strip().lower() not in ("y", "yes"):
            raise ToolRefusal("declined. No call was made and nothing was captured.")

    client = SeatsClient(resolution.key)
    if id_mode:
        cap.availability_id = args.availability_id
        cap.row = _find_row_locally(cap.availability_id)
        if cap.row is None:
            console.print(
                "[yellow]no availability row found locally; unit and cross-check "
                "evidence NOT available from this capture[/yellow]"
            )
    else:
        cap.row, cap.search_request = _resolve_row_by_search(client, args, console)
        cap.availability_id = str(cap.row.get("ID") or "")
        if not seats_trips.valid_availability_id(cap.availability_id):
            raise ToolRefusal(
                f"the matching row's ID {cap.availability_id!r} is not a usable "
                f"availability id. Nothing was captured."
            )
    if before_trips is not None:
        before_trips(cap)

    try:
        cap.raw = client.trips_raw(cap.availability_id)
    except TripsLookupError as e:
        raise ToolRefusal(
            f"the trips request failed: {e.code} ({e}). Nothing was captured."
        ) from None
    cap.payload = cap.raw.payload

    cap.route = _row_route(cap.row)
    if not all(cap.route):
        cap.route = _inferred_route(cap.payload)
        cap.route_inferred = True

    # -- write the capture ---------------------------------------------------
    day = _row_date(cap.row) if cap.row else (args.date or "unknown-date")
    source = _row_source(cap.row) if cap.row else (args.source or "unknown-source")
    o, d = cap.route
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_REAL_DIR
    # Every part but the (already validated) id comes from an API response or
    # a local row: sanitized exactly as trips snapshot names are, so no string
    # Seats.aero sends can add a directory or break the write.
    safe = lambda part: re.sub(r"[^A-Za-z0-9_.-]", "-", str(part))  # noqa: E731
    name = (
        f"{safe(day)}_{safe(source)}_{safe(o or 'XXX')}{safe(d or 'XXX')}_"
        f"{cap.availability_id}.json"
    )
    envelope = {
        "_meta": {
            "captured_by": CAPTURED_BY,
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endpoint": "trips",
            "request": {
                "path": f"/partnerapi/trips/{cap.availability_id}",
                "params": dict(seats_trips.TRIPS_REQUEST_PARAMS),
            },
            "http_status": cap.raw.http_status,
            "key_redacted": True,
            "synthetic": False,
            "trips_parser_version": seats_trips.TRIPS_PARSER_VERSION,
            "availability_id": cap.availability_id,
            "route": f"{o}->{d}" if o and d else None,
            "route_inferred_from_itineraries": cap.route_inferred,
            "availability_row": cap.row,
            "search_request": cap.search_request,
            "content_hash": response_cache.content_hash([cap.payload]),
        },
        "pages": [cap.payload],
    }
    text = json.dumps(envelope, indent=2)
    raw_text = cap.raw.raw_text or ""
    try:
        response_cache.assert_no_key_material(text, where=name, key=resolution.key)
        response_cache.assert_no_key_material(raw_text, where=f"{name} raw body", key=resolution.key)
    except ValueError as e:
        raise ToolRefusal(f"{e} Nothing was written.") from None
    out_dir.mkdir(parents=True, exist_ok=True)
    cap.path = _unique_path(out_dir / name)
    cap.raw_path = cap.path.with_suffix(".raw.txt")
    cap.path.write_text(text + "\n")
    cap.raw_path.write_text(raw_text)
    print_copyable(console, f"wrote {cap.path}", "green")
    print_copyable(console, f"wrote {cap.raw_path}", "green")

    # -- the drift report ------------------------------------------------------
    cap.parsed = seats_trips.parse_trips_payload(cap.payload, cap.availability_id, cap.route)
    cabin = (getattr(args, "cabin", None) or "").upper() or None
    for award in _awards_of(cap.row, cabin):
        facts = seats_trips.AwardFacts.from_award(award)
        cap.lookups[award.award_type] = seats_trips.match_award(cap.parsed, facts)
    _print_drift_report(cap, console, cabin)

    parsed = cap.parsed
    if parsed.envelope_error:
        cap.blocking_drift.append(parsed.envelope_error[1])
    cap.blocking_drift.extend(parsed.required_drift)
    if not parsed.trips and not parsed.envelope_error:
        cap.blocking_drift.append("the response carries no itinerary to verify against")
    if parsed.incomplete:
        cap.blocking_drift.append(parsed.incomplete_reason)
    if cap.blocking_drift:
        # MUST-FIX 3. No new capture is needed: the parser is fixed against THIS
        # file, and once it reads it cleanly the same file passes the label
        # check with no further call. And exactly one test reads every
        # committed capture's required fields, so that is the one red test a
        # drifting capture leaves - expected, not a regression.
        console.print(
            f"[bold red]CAPTURED WITH DRIFT ({len(cap.blocking_drift)} blocking item(s)). "
            f"The file is written and is exactly what is needed: send both files "
            f"back. Do NOT set TRIPS_SCHEMA_VERIFIED_BY to it, and do NOT capture "
            f"again - the parser is fixed against this file, and the same file then "
            f"verifies with no new call.[/bold red]"
        )
        for item in cap.blocking_drift:
            # MAC-1: a drift item can name the capture's absolute path.
            print_copyable(console, f"  - {item}", "red")
        if parsed.required_drift or parsed.envelope_error:
            console.print(
                f"[yellow]Expected: once these files are committed, exactly one test "
                f"is red until the parser is fixed - {escape(DRIFT_TEST)}. Every "
                f"other test stays green.[/yellow]"
            )
        else:
            console.print(
                "[yellow]Committing these files turns no test red; the label simply "
                "stays unverified.[/yellow]"
            )
        return EXIT_DRIFT, cap
    # Re-test 2, R2-4. "Clean" is the LABEL CHECK's verdict, not this tool's
    # own: the advice below is only printed for a file the check accepts, so
    # following it can never turn the label test red.
    cap.flip_problems = seats_trips.schema_verification_problems(cap.path.name, cap.path.parent)
    if cap.flip_problems:
        console.print(
            f"[bold red]CAPTURED, BUT THIS FILE CANNOT FLIP THE UNVERIFIED LABEL "
            f"({len(cap.flip_problems)} reason(s) from the label check). The file is "
            f"written and is still useful as a drift record: send both files back. "
            f"Do NOT set TRIPS_SCHEMA_VERIFIED_BY to it.[/bold red]"
        )
        for item in cap.flip_problems:
            # MAC-1: the label check names the directory and the file it read.
            print_copyable(console, f"  - {item}", "red")
        console.print(
            "[yellow]Committing these files turns no test red; the label simply "
            "stays unverified.[/yellow]"
        )
        return EXIT_DRIFT, cap
    console.print(
        f"[green]CAPTURED CLEAN. Send both files back; do not edit "
        f"src/seats_trips.py yourself. The Coder commits them and will set "
        f"TRIPS_SCHEMA_VERIFIED_BY = {cap.path.name!r}, which drops the UNVERIFIED "
        f"label.[/green]"
    )
    return EXIT_OK, cap


def _print_drift_report(cap: "Capture", console: Console, cabin: Optional[str]) -> None:
    parsed = cap.parsed
    p = lambda text="": console.print(escape(text))  # noqa: E731 - a local printer
    p("")
    p(f"DRIFT REPORT - trips parser {seats_trips.TRIPS_PARSER_VERSION}, against the published schema")
    if cap.route_inferred:
        p(
            f"  route {cap.route[0] or '?'}->{cap.route[1] or '?'} was INFERRED from the "
            f"itineraries (no availability row), so the route check below is not evidence"
        )
    if parsed.envelope_error:
        p(f"  ENVELOPE: {parsed.envelope_error[0]} - {parsed.envelope_error[1]}")
    p(f"  itineraries in data: {parsed.data_len if parsed.data_len is not None else 'n/a'}; "
      f"readable {len(parsed.readable)}, unreadable {len(parsed.unreadable)}")
    p("  required-field failures (per itinerary and field):")
    for line in parsed.required_drift or ["none"]:
        p(f"    - {line}")
    others = [d for d in parsed.drift if d not in parsed.required_drift]
    type_dev = [d for d in others if "undocumented" not in d]
    unknown_keys = [d for d in others if "undocumented" in d]
    p("  type deviations on optional fields:")
    for line in type_dev or ["none"]:
        p(f"    - {line}")
    p("  unknown keys (listed, never an error):")
    for line in unknown_keys or ["none"]:
        p(f"    - {line}")
    p(f"  cabin values seen: {', '.join(map(str, parsed.cabins_seen)) or 'none'}")
    p(f"  flight-number shapes seen: {', '.join(parsed.flight_number_shapes) or 'none'}")
    p(f"  MixedCabinPct present on {parsed.mixed_cabin_present} itinerary(ies)")
    p(f"  pagination keys: {', '.join(parsed.pagination_keys) or 'none'}")
    if parsed.incomplete:
        p(f"  INCOMPLETE: {parsed.incomplete_reason}")
    p("  TotalTaxes unit evidence (itineraries at the row's source, cabin and price):")
    awards = _awards_of(cap.row, cabin)
    if not cap.row:
        p("    NOT AVAILABLE - no availability row to compare against")
    for award in awards:
        letter = award.award_type
        row_taxes = (cap.row or {}).get(f"{letter}TotalTaxes")
        facts = seats_trips.AwardFacts.from_award(award)
        matched = [
            t for t in parsed.readable
            if t.source == facts.source_code and t.cabin == letter
            and t.mileage_cost == facts.cost
        ]
        equal = sum(1 for t in matched if t.total_taxes_raw == row_taxes)
        p(
            f"    {letter}: {equal} of {len(matched)} matched itinerary(ies) carry "
            f"TotalTaxes == the row's {letter}TotalTaxes ({row_taxes!r})"
        )
    p("  operating-airline lookup per available cabin:")
    if not cap.lookups:
        p("    none - no available cabin in the row (or no row)")
    for letter, lookup in cap.lookups.items():
        p(f"    {letter}: {lookup.render()}")


# ---------------------------------------------------------------------------
# yq-check
# ---------------------------------------------------------------------------


def _yq_block(cap: "Capture", args, console: Console) -> Dict[str, str]:
    """Print the comparison block and return its fields for the record."""
    from src.seats_client import SEATS_AERO_SOURCES, convert_taxes

    letter = args.cabin.upper()
    row = cap.row or {}
    source = args.source.strip().lower()
    program = SEATS_AERO_SOURCES[source].program if source in SEATS_AERO_SOURCES else source
    cents = row.get(f"{letter}TotalTaxes")
    currency = str(row.get("TaxesCurrency") or "")
    known, amount, cur, usd, note = convert_taxes(
        cents if isinstance(cents, int) and not isinstance(cents, bool) else None, currency
    )
    lookup = cap.lookups.get(letter)
    flights = "; ".join(lookup.flights) if lookup is not None and lookup.flights else "none matched"
    carrier = (
        ", ".join(lookup.carriers)
        if lookup is not None and lookup.status is MetalStatus.KNOWN
        else "NOT KNOWN"
    )
    per_trip = lookup.trip_taxes_note if lookup is not None and lookup.trip_taxes_note else "none"
    # Both lines come from the trips parse: they carry what every other line
    # derived from it carries - what a flight number is, and the parser label.
    label = seats_trips.trips_parser_label()
    tail = f" {label}" if label else ""
    if lookup is not None and lookup.flights:
        flights += tail
    carrier += (
        " (the MARKETING carrier by flight number; Seats.aero does not report who "
        "operates the flight)" if carrier != "NOT KNOWN" else ""
    ) + tail
    fields = {
        "program": f"{program} (source {source})",
        "route": f"{args.origin.upper()}->{args.destination.upper()} on {args.date}, cabin {letter}",
        "miles": f"{row.get(f'{letter}MileageCost')!r} ({letter}MileageCost)",
        "row taxes": (
            f"raw {cents!r} {currency} = {currency} {cents / 100:,.2f} read as cents"
            + (f" = ${usd:,.2f}. {note}" if known else f". NOT CONVERTIBLE: {note}")
            if isinstance(cents, int) and not isinstance(cents, bool)
            else f"raw {cents!r} - not a figure"
        ),
        "flights": flights,
        "flight-number carrier": carrier,
        "seats": f"{row.get(f'{letter}RemainingSeats')!r}",
        "per-itinerary TotalTaxes": per_trip,
    }
    # MUST-FIX 2 (Manager review). The decision is made by the site's TOTAL, so
    # the block prints both totals it can be compared with: the row figure, and
    # the row figure plus the modelled band for the airline the lookup names.
    status, airline = _checked_airline(cap, args)
    band_text, with_band = "none - the lookup did not name one KNOWN airline", ""
    if not airline:
        cap.no_verdict_reason = (
            f"the itinerary lookup for cabin {letter} is {status}, not one KNOWN "
            f"airline, and a verdict covers only the airline it was checked on"
        )
    else:
        _, est = _modelled_band(args, [airline])
        if est.is_known and est.amount_high > 0:
            band_text = f"{est.render()} one way ({airline} metal, cabin {letter})"
            if known:
                with_band = (
                    f"${usd + est.amount_low:,.2f}-${usd + est.amount_high:,.2f} "
                    f"(row ${usd:,.2f} + band)"
                )
        else:
            band_text = (
                f"NONE MODELLED for {airline} metal, so the site total cannot tell "
                f"includes from excludes"
            )
        if not with_band:
            # R4-1. With no nonzero band, a site total equal to the row figure is
            # ALSO what a fare with no carrier surcharge shows: the comparison
            # cannot tell includes_yq from excludes_yq, so it backs nothing.
            cap.no_verdict_reason = (
                f"no nonzero carrier surcharge band is modelled for {airline} metal "
                f"under {program}, cabin {letter}, on this route, so a site total "
                f"equal to the row figure is also what a fare with no carrier "
                f"surcharge would show: the check cannot tell includes_yq from "
                f"excludes_yq"
            )
            cap.banded_options = _banded_options(program, airline)
    fields["modelled carrier surcharge band"] = band_text
    fields["row figure + band"] = with_band or "n/a"
    console.print("")
    console.print("YQ CHECK - compare against the program's own site")
    for key, value in fields.items():
        console.print(f"  {key}: {escape(str(value))}")
    for line in _decision_rule(source, airline, known and usd, with_band, cap.no_verdict_reason):
        console.print(f"  {escape(line)}")
    return fields


OWN_AIRLINE_NAME = {
    "virginatlantic": "Virgin Atlantic",
    "flyingblue": "Air France or KLM",
    "jetblue": "JetBlue",
    "singapore": "Singapore Airlines",
    "united": "United",
    "aeroplan": "Air Canada",
}


def _banded_options(program: str, airline: str) -> List[str]:
    """Where the surcharge table DOES model a nonzero band for this program on this airline."""
    from src.surcharge import default_table

    seen = []
    for rule in default_table().rules:
        if rule.program != program or rule.operating_carrier != airline:
            continue
        if not rule.amount_high or rule.amount_high <= 0:
            continue
        cabin = "any cabin" if rule.cabin == "*" else f"cabin {rule.cabin}"
        region = "any route" if rule.route_region == "*" else f"{rule.route_region} routes"
        text = f"{cabin} on {region}"
        if text not in seen:
            seen.append(text)
    return seen


def _decision_rule(
    source: str, airline: str, row_usd, with_band: str, no_verdict_reason: str = ""
) -> List[str]:
    """The rule, stated by the TOTAL the site shows for ONE adult on the same flight."""
    site = PROGRAM_SITE.get(source, "the program's own site")
    own = OWN_AIRLINE_NAME.get(source, "the program's own airline")
    row = f"${row_usd:,.2f}" if row_usd else "the row figure"
    if no_verdict_reason:
        return [
            f"THIS CHECK CANNOT BACK A VERDICT: {no_verdict_reason}. Whatever {site} "
            f"shows, record nothing from it.",
        ]
    return [
        f"On {site}, find the SAME flight for ONE adult.",
        f"1. Confirm the site shows it OPERATED BY {airline or 'the airline above'} "
        f"itself ({own} for this program), not by a partner such as Delta. If it is "
        f"operated by anyone else, the check is inconclusive: record nothing.",
        "2. Read the site's TOTAL of taxes, fees and carrier-imposed charges - one "
        "combined figure, or its lines added up - converted to US dollars if needed.",
        f"3. Site total about equal to the row figure ({row}): includes_yq.",
        f"4. Site total about equal to the row figure plus the band "
        f"({with_band or 'no band: this check cannot show excludes_yq'}): excludes_yq.",
        "5. Anything else is inconclusive: record nothing.",
    ]


def _modelled_band(args, carriers):
    """The one-way modelled surcharge band for these carriers under the source's program."""
    from src import regions
    from src.seats_client import SEATS_AERO_SOURCES
    from src.surcharge import default_table

    source = args.source.strip().lower()
    program = SEATS_AERO_SOURCES[source].program if source in SEATS_AERO_SOURCES else source
    try:
        region = regions.classify(args.origin.upper(), args.destination.upper())
        country = regions.departure_country(args.origin.upper())
    except Exception:  # noqa: BLE001 - an unknown airport just means no band
        region, country = "", ""
    return program, default_table().resolve_ambiguous_metal(
        program, list(carriers), region, args.cabin.upper(), country, is_round_trip=False,
    )


def _inconclusive_warning(cap: "Capture", args) -> str:
    source = args.source.strip().lower()
    own = OWN_METAL.get(source, "the program's own airline")
    lookup = cap.lookups.get(args.cabin.upper())
    if lookup is None or lookup.status is not MetalStatus.KNOWN:
        return f"likely INCONCLUSIVE - pick a flight on {own} metal (the flight-number carrier is not known)"
    program, est = _modelled_band(args, lookup.carriers)
    if not est.is_known or est.amount_high <= 0:
        label = seats_trips.trips_parser_label()
        return (
            f"likely INCONCLUSIVE - pick a flight on {own} metal ({program} on "
            f"{', '.join(lookup.carriers)} metal has no nonzero surcharge row, so "
            f"including and excluding it look the same)"
            + (f" {label}" if label else "")
        )
    return ""


def _checked_airline(cap: "Capture", args) -> Tuple[str, str]:
    """(itinerary lookup status, the one KNOWN airline or "") for the checked cabin (D1)."""
    lookup = cap.lookups.get(args.cabin.upper())
    if lookup is None:
        return "NONE", ""
    status = lookup.status.value.replace("_", " ").upper()
    if lookup.status is MetalStatus.KNOWN and len(lookup.carriers) == 1:
        return status, lookup.carriers[0]
    return status, ""


def _write_record(cap: "Capture", args, fields: Dict[str, str], today: date, warning: str) -> Path:
    source = args.source.strip().lower()
    record_dir = Path(args.record_dir) if args.record_dir else DEFAULT_RECORD_DIR
    record_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(record_dir / f"{today.isoformat()}-{source}.md")
    lines = [
        f"# yq-check record: {source}, {today.isoformat()}",
        "",
        # The blank marker appears ONLY on the field lines below: the loader
        # refuses any record that still contains it, so prose that quoted it
        # would make every filled record unloadable.
        "Written by `python -m src.trips_tools yq-check`. The Seats.aero half is "
        "filled in; fill every blank field under the program-site heading from the "
        "program's own site before adding a row to data/yq_inclusion.csv. A record "
        "with an unfilled field is refused by the loader.",
        "",
        "## Seats.aero",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in fields.items()]
    # D1: the verdict this record backs covers ONE airline, the one the lookup
    # KNOWS. The loader reads both lines; a record without one KNOWN airline
    # backs nothing.
    status, airline = _checked_airline(cap, args)
    if cap.no_verdict_reason:
        # R4-1: machine-readable. The loader refuses any record carrying it.
        from src.yq_inclusion import NO_VERDICT_MARKER

        lines += [f"- {NO_VERDICT_MARKER} - {cap.no_verdict_reason}"]
    lines += [
        f"- itinerary lookup status: {status}",
        "- checked airline (the award's KNOWN flight-number carrier): "
        + (airline or "NONE - the lookup did not name one KNOWN airline, so this "
           "record cannot back a verdict"),
        f"- capture: {cap.path.name if cap.path else '(none)'}",
    ]
    if warning:
        lines += [f"- WARNING: {warning}"]
    lines += [
        "",
        f"## {PROGRAM_SITE.get(source, 'The program site')}",
        "",
        "The rule, by the site's TOTAL for ONE adult on the same flight: about the "
        "row figure is includes_yq; about the row figure plus the modelled band "
        "(both above) is excludes_yq; anything else, or a flight operated by another "
        "airline, is inconclusive and records nothing.",
        "",
        "- date checked: ____",
        "- flight(s) shown: ____",
        f"- the site shows this flight operated by {airline or 'NONE'} itself, not a "
        f"codeshare partner (yes / no): ____",
        "- total taxes, fees and carrier-imposed charges for ONE adult, as the site "
        "shows it (one combined figure, or its lines added up): ____",
        "- verdict (includes_yq / excludes_yq / inconclusive): ____",
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def run_yq_check(args, console: Console, read, today: date) -> int:
    from src.seats_client import TAXES_UNREPORTED_SOURCES

    source = (args.source or "").strip().lower()
    if source in TAXES_UNREPORTED_SOURCES:
        raise ToolRefusal(
            f"Seats.aero reports no taxes for {source!r}, so there is no figure to "
            f"compare. No call was made."
        )

    def check_row(cap: "Capture") -> None:
        letter = args.cabin.upper()
        row = cap.row or {}
        if row.get(f"{letter}Available") is not True:
            raise ToolRefusal(
                f"cabin {letter} is not available on that row. The search call was "
                f"spent; no trips call was made."
            )
        if row.get(f"{letter}TotalTaxes") in (0, None, "0"):
            raise ToolRefusal(
                f"the row's {letter}TotalTaxes is {row.get(f'{letter}TotalTaxes')!r}: a "
                f"0 means not reported and cannot settle anything. No trips call was made."
            )
        # The check settles what the figure CONTAINS, so it compares only a
        # figure scoring would use. The same two tests scoring applies: the
        # parser's (negative, unconvertible, unreported) and the UK duty floor.
        awards = _awards_of(row, letter)
        award = awards[0] if awards else None
        if award is None or not award.cash_component_known:
            raise ToolRefusal(
                f"the row's {letter}TotalTaxes is {row.get(f'{letter}TotalTaxes')!r}, "
                f"which scoring does not believe as a tax figure (negative, "
                f"unconvertible or unreported). A figure the tool does not trust "
                f"cannot settle what it contains. No trips call was made."
            )
        from src.live_trip import taxes_below_owed_uk_duty
        from src.models import Leg

        origin, destination = _row_route(row)
        leg = Leg(
            id="yq-check", kind="flight", description="yq-check",
            date=award.date or date.fromisoformat(args.date),
            origin=origin, destination=destination, cabin=letter,
        )
        below = taxes_below_owed_uk_duty(leg, award)
        if below:
            raise ToolRefusal(
                f"{below} A figure the tool does not trust cannot settle what it "
                f"contains. No trips call was made."
            )

    code, cap = run_capture(args, console, read, command="yq-check", before_trips=check_row)
    fields = _yq_block(cap, args, console)
    warning = _inconclusive_warning(cap, args)
    if warning:
        console.print(f"[bold yellow]{escape(warning)}[/bold yellow]")
    record = _write_record(cap, args, fields, today, warning)
    print_copyable(
        console, f"wrote {record} - fill the ____ blanks from the site.", "green"
    )
    try:
        evidence = record.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        evidence = str(record)
    # NO ROW WITH A VERDICT IN IT IS PRINTED. The verdict is decided on the
    # airline's site, after this tool has exited, and is written on the
    # record's verdict line. The row below leaves it for the reader to copy
    # from there, and the loader refuses a row whose verdict differs from its
    # record's (or a record that says inconclusive) - so a pre-filled row that
    # is one line off can never be pasted in and scored.
    status, airline = _checked_airline(cap, args)
    if cap.no_verdict_reason:
        where = (
            f" For {airline} metal the surcharge table models a band for: "
            f"{'; '.join(cap.banded_options)} - run the check there."
            if cap.banded_options
            else (
                " The surcharge table models no band for this program on this "
                "airline at all, so no yq-check on it can settle the question."
                if airline
                else " Pick a flight the program's own airline operates."
            )
        )
        console.print(
            f"[bold yellow]This check cannot back a verdict: "
            f"{escape(cap.no_verdict_reason)}. Record nothing from it; no row is "
            f"printed.{escape(where)}[/bold yellow]"
        )
        return code
    console.print(
        "Fill every ____ in the record from the site, then send back the record and "
        "the two capture files. Do not edit data/yq_inclusion.csv yourself. ONLY if "
        "the record's verdict line says includes_yq or excludes_yq, the Coder adds "
        "this row, with <VERDICT> replaced by that same word:"
    )
    # MAC-1. THIS is the line the reader pastes into data/yq_inclusion.csv, and
    # `evidence` is an absolute path whenever --record-dir is outside the tree:
    # printed the ordinary way it folded mid-path on a long checkout, and a row
    # pasted out of two lines is a different row.
    print_copyable(
        console,
        f"  {source},{airline},<VERDICT>,{today.isoformat()},{evidence},"
        f"{args.origin.upper()}-{args.destination.upper()} {args.cabin.upper()} "
        f"{args.date}",
    )
    console.print(
        "An inconclusive check records nothing. The loader refuses a row whose "
        "verdict is not the one written in its record."
    )
    return code


def main(
    argv: Optional[List[str]] = None,
    *,
    read: Callable[[str], str] = input,
    console: Optional[Console] = None,
    today: Optional[date] = None,
) -> int:
    console = console or Console(width=190)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse's own usage errors exit 2, which this tool's contract does
        # not have. A usage error captured nothing: 1.
        return EXIT_NOTHING_CAPTURED if e.code not in (0, None) else EXIT_OK
    if not args.command:
        parser.print_help()
        return EXIT_NOTHING_CAPTURED
    try:
        if args.command == "capture":
            code, _ = run_capture(args, console, read)
            return code
        return run_yq_check(args, console, read, today or date.today())
    except ToolRefusal as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        return EXIT_NOTHING_CAPTURED


if __name__ == "__main__":
    import sys

    # MAC-A: the yq-check row is copied out of this process's stdout, so it has
    # to reach the terminal at all. UTF-8 wherever it runs.
    config.use_utf8_output()
    sys.exit(main())
