"""
The UI's engine room: argv building, the run lock, confirm tokens, the per-run
reset, the transcript, the session wallet and the run store.

A UI RUN IS A CLI INVOCATION (plan decision D1). Every run builds a real
`python -m src.main` argument list, parses it with the CLI's own
`build_parser()`, and calls the CLI's own `dispatch()` with a recording console
and a sink. Nothing here decides a verdict, an exit code or a refusal; it only
decides WHICH invocation to make, and it shows that invocation to the user as
"Equivalent command".

A LONG-LIVED PROCESS IS NOT ONE CLI RUN, and three things are done about that:

* ONE RUN AT A TIME (D6). `SeatsClient`'s caches and call counter are class
  attributes and `config`'s FX table is module state; two runs interleaving in
  threads would share them. A second run gets HTTP 409.
* THE IN-PROCESS CACHE IS CLEARED BEFORE EVERY RUN (D7). It has no TTL, so in a
  server that stays up for days a morning search would answer an evening one.
  Each UI run then behaves exactly like a fresh CLI process; the 6-hour DISK
  cache still applies. The daily call counter is deliberately KEPT and shown as
  "since launch" - it resets itself at the date change, as the CLI's does.
* NOTHING THAT SPENDS CALLS RUNS WITHOUT A SERVER-SIDE CONFIRM (D5). A LIVE trip
  run and a search need a `confirm_id` from their preflight: single use, five
  minutes, bound to a hash of the exact request - fixture bytes included - so a
  forged, replayed or out-of-date confirm cannot spend.

The key never leaves this process: the transcript's key line is replaced, and
the server refuses to send any body that contains the key or its mask.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from src import config
from src import main as cli
from src.ui.server import ApiError

ROOT = Path(__file__).resolve().parent.parent.parent
PROG = [".venv/bin/python", "-m", "src.main"]
WIDTH = 190
CONFIRM_TTL_SECONDS = 5 * 60
RUNS_KEPT = 20
PAGES_PER_SEARCH = 25  # SeatsClient.MAX_PAGES: the real ceiling, not a guess
SEARCH_MAX_RESULTS = 200
FLEX_MAX = 7
KEY_LINE = re.compile(r"^(Seats\.aero key: )(.*?)(   \(source: .*\))$", re.MULTILINE)
REDACTED_KEY = "(masked key not sent to the browser)"

CONFIRM_REQUIRED = (
    "This run can spend Seats.aero calls, so it needs a confirmation from its "
    "preflight first. Nothing was sent."
)
CONFIRM_STALE = "The trip or options changed since you confirmed. Confirm again."
BUSY = "Another run is in progress. One run at a time: the call counter and caches are shared."


def _replay_manifest_candidates() -> List[Path]:
    """The REPLAY allowlist (D10): the configured snapshot directory's manifest,
    and each committed corpus directory's, one level deep. Never trips_endpoint/,
    whose manifest replay reads by itself. The browser sends an index into this
    list, never a path."""
    found: List[Path] = []
    first = Path(config.SNAPSHOT_DIR) / "MANIFEST.md"
    if first.is_file():
        found.append(first)
    corpus = ROOT / "tests" / "fixtures" / "seats_aero"
    if corpus.is_dir():
        for sub in sorted(corpus.iterdir()):
            if not sub.is_dir() or sub.name == "trips_endpoint":
                continue
            m = sub / "MANIFEST.md"
            if m.is_file():
                found.append(m)
    out, seen = [], set()
    for p in found:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def display_path(p: Path) -> str:
    """A path as the reader would type it from the repo root, when it is inside."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _loads_as_a_trip(path: Path) -> bool:
    from src.trip_loader import load_trip_fixture

    try:
        load_trip_fixture(path)
        return True
    except Exception:  # noqa: BLE001 - an unloadable file is listed, not hidden
        return False


def path_arg(p: Path) -> str:
    """A path for an argv: relative to the working directory when it is inside
    it (so "Equivalent command" reads the way it would be typed), else absolute."""
    try:
        return str(Path(p).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(Path(p).resolve())


class Engine:
    def __init__(
        self,
        trips_dir: Optional[Path] = None,
        wallet_path: Optional[Path] = None,
    ):
        self.trips_dir = Path(trips_dir or cli.FIXTURE_DIR).resolve()
        # As given at launch: the argv and the "Equivalent command" use it.
        self.wallet_path: Optional[str] = str(wallet_path) if wallet_path else None
        # Session edits (D8). None = the file is in use unchanged. Held in
        # memory only and NEVER written anywhere.
        self.session_wallet: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self._confirms: Dict[str, Tuple[str, float]] = {}
        self._confirms_lock = threading.Lock()
        self._runs: "OrderedDict[str, dict]" = OrderedDict()

    # ------------------------------------------------------------------ key

    def key_resolution(self):
        """(resolution or None, error text or None). Never raises."""
        try:
            return config.resolve_key(None), None
        except config.KeyResolutionError as e:
            return None, str(e)

    def key_needles(self) -> List[str]:
        """What the egress filter refuses to send: the key and its mask."""
        resolution, _ = self.key_resolution()
        if resolution is None:
            return []
        key = str(resolution.key or "")
        if len(key) < 8:
            return []
        return [key, config.mask_key(key)]

    def key_state(self) -> Dict[str, Any]:
        resolution, error = self.key_resolution()
        if resolution is None:
            return {"found": False, "source": None, "path": None, "error_text": error}
        return {
            "found": True,
            "source": resolution.source,
            "path": str(resolution.path) if resolution.path else None,
            "error_text": None,
        }

    # --------------------------------------------------------------- wallet

    def _file_wallet_dict(self) -> Optional[Dict[str, Any]]:
        if not self.wallet_path:
            return None
        from src.wallet import load_wallet

        w = load_wallet(Path(self.wallet_path))
        return {
            "balances": dict(w.balances),
            "cards": list(w.cards),
            # Cents per point, as --valuation takes it.
            "valuations": {c: v * 100 for c, v in w.valuation_cpp.items()},
        }

    def wallet_argv(self) -> List[str]:
        """The wallet as CLI flags: `--wallet PATH` while the file is unchanged,
        otherwise the session's balances/cards/valuations as flags. With no
        wallet at all: nothing - and the CLI's own exit-2 refusal follows."""
        if self.session_wallet is None:
            return ["--wallet", self.wallet_path] if self.wallet_path else []
        out: List[str] = []
        for cur, bal in self.session_wallet["balances"].items():
            out += ["--balance", f"{cur}=" if bal is None else f"{cur}={bal}"]
        for card in self.session_wallet["cards"]:
            out += ["--card", card]
        for cur, cents in self.session_wallet.get("valuations", {}).items():
            out += ["--valuation", f"{cur}={_fmt_cents(cents)}"]
        return out

    def wallet_source(self) -> Optional[str]:
        if self.session_wallet is not None:
            return "entered in this session (not saved)"
        if self.wallet_path:
            return self.wallet_path
        return None

    def wallet_state(self) -> Dict[str, Any]:
        """The wallet exactly as the CLI would build it from `wallet_argv()`."""
        from src.wallet import WalletError

        argv = self.wallet_argv()
        base = {
            "balances": {}, "cards": [], "source": self.wallet_source(),
            "describe_lines": [], "warnings": [], "error": None,
            "from_file": self.session_wallet is None and bool(self.wallet_path),
            "argv": argv,
        }
        if not argv:
            base["error"] = None
            base["missing"] = True
            return base
        base["missing"] = False
        try:
            args = cli.build_parser().parse_args(argv)
            wallet, warnings = cli.build_wallet(args, cli.load_ratio_manager())
        except WalletError as e:
            base["error"] = str(e)
            return base
        except SystemExit:
            base["error"] = "The wallet could not be turned into command-line flags."
            return base
        base["balances"] = dict(wallet.balances)
        base["cards"] = list(wallet.cards)
        base["valuations"] = {c: v * 100 for c, v in wallet.valuation_cpp.items()}
        base["describe_lines"] = wallet.describe()
        base["warnings"] = list(warnings)
        return base

    def set_wallet(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a session wallet (W panel). Validated by the CLI's own wallet
        code; refused with its message. Never written to disk."""
        from src.wallet import WalletError

        balances_in = body.get("balances")
        cards_in = body.get("cards", [])
        vals_in = body.get("valuations", {}) or {}
        if not isinstance(balances_in, dict) or not isinstance(cards_in, list) \
                or not isinstance(vals_in, dict):
            raise ApiError(400, "bad_request",
                           "balances must be an object, cards a list, valuations an object.")
        balances: Dict[str, Optional[int]] = {}
        flags: List[str] = []
        for cur, raw in balances_in.items():
            cur = str(cur).strip()
            text = "" if raw is None else str(raw).strip()
            flags.append(f"{cur}={text}")
        cards = [str(c).strip() for c in cards_in if str(c).strip()]
        valuations = {}
        val_flags = []
        for cur, cents in vals_in.items():
            val_flags.append(f"{str(cur).strip()}={str(cents).strip()}")
        try:
            from src.wallet import validate_wallet, wallet_from_flags

            wallet = wallet_from_flags(flags, cards, val_flags)
            ratios = cli.load_ratio_manager()
            validate_wallet(
                wallet,
                known_currencies=set(ratios.issuer_currencies()),
                known_cards=ratios.cards_affecting_ratios(),
            )
        except WalletError as e:
            return {"error": "wallet", "message": str(e)}
        balances = dict(wallet.balances)
        valuations = {c: v * 100 for c, v in wallet.valuation_cpp.items()}
        candidate = {"balances": balances, "cards": list(wallet.cards), "valuations": valuations}
        try:
            unchanged = candidate == self._file_wallet_dict()
        except WalletError:
            unchanged = False
        self.session_wallet = None if unchanged else candidate
        return self.wallet_state()

    # ---------------------------------------------------------------- state

    def replay_manifests(self) -> List[Dict[str, Any]]:
        from src import snapshot_replay

        out = []
        for i, path in enumerate(_replay_manifest_candidates()):
            try:
                rows = len(snapshot_replay.parse_manifest(path))
                problem = None
            except snapshot_replay.ManifestError as e:
                rows, problem = None, str(e)
            out.append({"id": i, "label": display_path(path), "rows": rows, "problem": problem})
        return out

    def calls_state(self) -> Dict[str, int]:
        from src.seats_client import SeatsClient

        remaining = SeatsClient._budget_remaining()
        return {
            "since_launch": SeatsClient.DAILY_CALL_CAP - remaining,
            "cap": SeatsClient.DAILY_CALL_CAP,
            "remaining": remaining,
        }

    def state(self) -> Dict[str, Any]:
        from src import seats_trips
        from src.seats_client import PARSER_VERSION

        key = self.key_state()
        manifests = self.replay_manifests()
        snapshot_manifest = Path(config.SNAPSHOT_DIR) / "MANIFEST.md"
        return {
            "today": str(date.today()),
            "wallet": self.wallet_state(),
            "key": key,
            "modes": {
                "live": key["found"],
                "live_reason": None if key["found"] else "LIVE needs a Seats.aero key.",
                "replay_manifests": manifests,
                "replay_reason": None if manifests else (
                    f"No snapshot manifest found at {display_path(snapshot_manifest)}. "
                    f"Run a trip LIVE once to write one."
                ),
            },
            "calls": self.calls_state(),
            "paths": {
                "snapshot_dir": display_path(Path(config.SNAPSHOT_DIR)),
                "cache_dir": display_path(Path(config.CACHE_DIR)),
                "trips_dir": display_path(self.trips_dir),
            },
            "relocation_lines": relocation_lines(),
            "parser": {
                "search": PARSER_VERSION,
                "trips": seats_trips.TRIPS_PARSER_VERSION,
                "trips_label": seats_trips.trips_parser_label(),
            },
            "running": self._lock.locked(),
        }

    # ---------------------------------------------------------------- trips

    def _trip_files(self) -> Dict[str, Path]:
        """id -> path for every fixture file in the trips directory. `*_answer.json`
        (acceptance ANSWER files) are excluded; nothing else is hidden."""
        from src.trip_builder import MAX_NAME_LENGTH, _SAFE_NAME

        out: Dict[str, Path] = {}
        if not self.trips_dir.is_dir():
            return out
        for p in sorted(self.trips_dir.glob("*.json")):
            if not p.is_file():
                continue
            if not _SAFE_NAME.match(p.stem) or ".." in p.stem:
                continue
            if len(p.stem) > MAX_NAME_LENGTH:
                # It could not be addressed in a URL; listing it would offer a
                # trip that 404s. Names this long can no longer be written.
                continue
            if p.name.endswith("_answer.json") and not _loads_as_a_trip(p):
                # M-3: the acceptance ANSWER files sit beside the fixtures and
                # are not trips. They are recognised by FAILING TO LOAD as one,
                # not by their name alone - a trip a user names "..._answer" is
                # a trip, and hiding it while reporting "Wrote ..." is the app
                # claiming something it then cannot show.
                continue
            out[p.stem] = p
        return out

    def trip_path(self, trip_id: str) -> Path:
        """Resolve a trip id ONLY against the current listing. Never joined."""
        files = self._trip_files()
        if trip_id not in files:
            raise ApiError(404, "not_found", f"No trip {trip_id!r} in {display_path(self.trips_dir)}.")
        return files[trip_id]

    def fixture_arg(self, path: Path) -> str:
        """What goes after --trip-fixture: the bare filename when the CLI would
        find this very file by it, the full path otherwise."""
        if (path.parent.resolve() == Path(cli.FIXTURE_DIR).resolve()
                and not Path(path.name).exists()):
            return path.name
        return str(path)

    def list_trips(self) -> List[Dict[str, Any]]:
        from src.trip_builder import LIVE_ONLY_FLAG
        from src.trip_loader import load_trip_fixture

        out = []
        for trip_id, path in self._trip_files().items():
            row: Dict[str, Any] = {"id": trip_id, "file": path.name, "load_error": None}
            try:
                fx = load_trip_fixture(path)
            except Exception as e:  # noqa: BLE001 - listed, never hidden
                row.update(name=trip_id, legs=None, flights=None, hotels=None,
                           max_flight_travellers=None, fixture_has_points_prices=None,
                           live_only=None, load_error=f"{type(e).__name__}: {e}")
                out.append(row)
                continue
            flights = [l for l in fx.legs if l.kind == "flight"]
            row.update(
                name=fx.name,
                legs=len(fx.legs),
                flights=len(flights),
                hotels=sum(1 for l in fx.legs if l.kind == "hotel"),
                max_flight_travellers=max((l.travelers for l in flights), default=0),
                fixture_has_points_prices=any(l.points_candidates for l in flights),
                live_only=LIVE_ONLY_FLAG in fx.trip_level_flags,
                flight_legs_queryable=sum(1 for l in flights if l.origin and l.destination),
            )
            out.append(row)
        return out

    def trip_detail(self, trip_id: str) -> Dict[str, Any]:
        from src.trip_loader import load_trip_fixture

        from src.ui import serialize

        path = self.trip_path(trip_id)
        try:
            fx = load_trip_fixture(path)
        except Exception as e:  # noqa: BLE001
            raise ApiError(422, "cannot_load", f"{type(e).__name__}: {e}")
        return serialize.fixture_detail(trip_id, path, fx)

    def _trip_options(self, trip_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Every UI field validated BEFORE an argv exists (argparse would exit)."""
        mode = body.get("mode")
        if mode not in ("live", "replay", "offline"):
            raise ApiError(400, "invalid_option", "mode must be live, replay or offline.")
        raw = body.get("options") or {}
        if not isinstance(raw, dict):
            raise ApiError(400, "invalid_option", "options must be an object.")
        opts: Dict[str, Any] = {"mode": mode}
        td = str(raw.get("transfer_date") or "").strip()
        if td:
            try:
                datetime.strptime(td, "%Y-%m-%d")
            except ValueError:
                raise ApiError(400, "invalid_option",
                               f"Transfer date {td!r} is not an ISO date (YYYY-MM-DD).",
                               field="transfer_date")
        opts["transfer_date"] = td or None
        if mode == "live":
            opts["flex_days"] = _int_in(raw.get("flex_days", 0), 0, FLEX_MAX, "flex_days",
                                        "Flex days")
            trips = raw.get("trips", "auto")
            if trips not in ("auto", "all", "off"):
                raise ApiError(400, "invalid_option",
                               "Operating-airline lookup must be auto, all or off.",
                               field="trips")
            opts["trips"] = trips
            opts["trips_cap"] = _int_in(raw.get("trips_cap", 10), 1, 50, "trips_cap",
                                        "Lookup cap")
            opts["refresh"] = raw.get("refresh", False) is True
        if mode == "replay":
            manifests = _replay_manifest_candidates()
            mid = raw.get("manifest_id", 0)
            if isinstance(mid, bool) or not isinstance(mid, int) or not 0 <= mid < len(manifests):
                raise ApiError(400, "invalid_option",
                               "That replay manifest is not in the list this app offers.",
                               field="manifest_id")
            opts["manifest"] = manifests[mid]
            opts["manifest_id"] = mid
        return opts

    def trip_argv(self, path: Path, opts: Dict[str, Any]) -> List[str]:
        argv = ["--trip-fixture", self.fixture_arg(path)]
        if opts["mode"] == "offline":
            argv.append("--offline")
        elif opts["mode"] == "replay":
            argv += ["--from-snapshot", path_arg(opts["manifest"])]
        else:
            argv += ["--live", "--trips", opts["trips"], "--trips-cap", str(opts["trips_cap"])]
            if opts["flex_days"]:
                argv += ["--flex-days", str(opts["flex_days"])]
            if opts["refresh"]:
                argv.append("--refresh")
        argv += self.wallet_argv()
        if opts.get("transfer_date"):
            argv += ["--transfer-date", opts["transfer_date"]]
        # Display only: every same-metal alternative the CLI would print is in
        # the drawer anyway, and the transcript then matches the CLI's.
        argv.append("--show-alternatives")
        return argv

    def _trip_digest(self, trip_id: str, path: Path, opts: Dict[str, Any]) -> str:
        """What a LIVE confirm is bound to: the trip, the fixture's BYTES, the
        mode and options, and the wallet argv. Any change -> confirm_stale."""
        return canonical_digest({
            "kind": "trip",
            "trip_id": trip_id,
            "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "options": {k: v for k, v in opts.items() if k != "manifest"},
            "wallet": self.wallet_argv(),
            "wallet_file_sha256": self._wallet_file_digest(),
        })

    def _wallet_file_digest(self) -> Optional[str]:
        if self.session_wallet is not None or not self.wallet_path:
            return None
        try:
            return hashlib.sha256(Path(self.wallet_path).read_bytes()).hexdigest()
        except OSError:
            return None

    def _cache_answerable(self, fx, opts: Dict[str, Any]) -> Optional[int]:
        """How many of this trip's searches the disk cache can answer right now,
        asked with the client's own key and the leg's own window. None when it
        cannot be said (then the confirm states the maximum only)."""
        from src.live_trip import leg_search_window
        from src.response_cache import ResponseCache
        from src.seats_client import search_request_key

        if opts.get("refresh"):
            return 0  # --refresh ignores the cache by definition
        try:
            probe = ResponseCache(cache_dir=Path(config.CACHE_DIR), snapshot_dir=None,
                                  ttl_seconds=config.CACHE_TTL_SECONDS)
            hits = 0
            for leg in fx.legs:
                if leg.kind != "flight" or not leg.origin or not leg.destination:
                    continue
                key = search_request_key(leg.origin, leg.destination,
                                         leg_search_window(leg, opts.get("flex_days", 0)))
                if probe.get(key) is not None:
                    hits += 1
            return hits
        except Exception:  # noqa: BLE001 - a prediction must never block a run
            return None

    def trip_preflight(self, trip_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        from src.trip_loader import load_trip_fixture

        path = self.trip_path(trip_id)
        opts = self._trip_options(trip_id, body or {})
        argv = self.trip_argv(path, opts)
        out: Dict[str, Any] = {
            "mode": opts["mode"],
            "argv_display": shlex.join(PROG + argv),
            "max_calls": 0,
            "breakdown": None,
            "cache_answerable": None,
            "calls": self.calls_state(),
            "archive_dir": None,
            "confirm_id": None,
            "blocked": self._blocked(opts["mode"]),
        }
        if opts["mode"] == "replay":
            out["manifest"] = display_path(opts["manifest"])
        if opts["mode"] != "live":
            return out
        try:
            fx = load_trip_fixture(path)
            legs = sum(1 for l in fx.legs if l.kind == "flight" and l.origin and l.destination)
        except Exception as e:  # noqa: BLE001 - the run itself will refuse it
            raise ApiError(422, "cannot_load", f"{type(e).__name__}: {e}")
        lookup_cap = 0 if opts["trips"] == "off" else opts["trips_cap"]
        # D12: the true ceiling - MAX_PAGES per search plus every lookup the cap
        # allows. An honest maximum beats a friendly guess.
        out["max_calls"] = legs * PAGES_PER_SEARCH + lookup_cap
        out["breakdown"] = {
            "flight_legs": legs,
            "pages_per_search": PAGES_PER_SEARCH,
            "lookup_cap": lookup_cap,
        }
        out["archive_dir"] = display_path(Path(config.SNAPSHOT_DIR))
        out["cache_answerable"] = self._cache_answerable(fx, opts)
        if out["blocked"] is None:
            out["confirm_id"] = self.issue_confirm(self._trip_digest(trip_id, path, opts))
        return out

    # -------------------------------------------------------------- new trip

    def _draft(self, body: Dict[str, Any]):
        """(fixture, errors) from the form, through the builder's OWN
        validators - the same ones --new-trip calls - one error per field."""
        from src import trip_builder as tb

        body = body or {}
        errors: List[Dict[str, Any]] = []

        def check(leg, field, fn, *a):
            try:
                return fn(*a)
            except tb.TripBuilderError as e:
                errors.append({"leg": leg, "field": field, "message": str(e)})
                return None

        name = check(None, "name", tb.validate_name, str(body.get("name") or ""))
        cabin = check(None, "cabin", tb.validate_cabin, str(body.get("cabin") or ""))
        legs_in = body.get("legs") or []
        if not isinstance(legs_in, list) or not legs_in:
            errors.append({"leg": None, "field": "legs",
                           "message": "A trip needs at least one flight leg."})
            legs_in = []
        if len(legs_in) > 20:
            errors.append({"leg": None, "field": "legs", "message": "At most 20 legs."})
            legs_in = []
        flights = []
        for i, raw in enumerate(legs_in, start=1):
            raw = raw if isinstance(raw, dict) else {}
            o = check(i, "origin", tb.validate_iata, str(raw.get("origin") or ""), f"Leg {i} from")
            d = check(i, "destination", tb.validate_iata, str(raw.get("destination") or ""),
                      f"Leg {i} to")
            when = check(i, "date", tb.validate_date, str(raw.get("date") or ""), f"Leg {i} date")
            cash = check(i, "cash", tb.validate_cash, str(raw.get("cash") or ""), f"Leg {i} cash")
            leg_cabin = check(i, "cabin", tb.validate_cabin, str(raw.get("cabin") or cabin or ""))
            if o and d and o == d:
                errors.append({"leg": i, "field": "destination",
                               "message": f"Leg {i}: origin and destination are both {o}. A "
                                          f"leg that goes nowhere has no fare and no award space."})
            if None not in (o, d, when, cash, leg_cabin) and o != d:
                flights.append(tb.FlightSpec(o, d, when, cash,
                                             cabin=None if leg_cabin == cabin else leg_cabin))
        if errors or name is None or cabin is None:
            return None, errors
        return tb.build_fixture(name, flights, [], 1, cabin), []

    def trip_draft(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from src import trip_builder as tb

        fixture, errors = self._draft(body)
        if errors:
            return {"ok": False, "errors": errors}
        return {
            "ok": True,
            "echo_lines": tb.echo_lines(fixture),
            "draft_hash": canonical_digest({"fixture": fixture, "dir": str(self.trips_dir)}),
        }

    def trip_create(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Write the previewed trip with the builder's own writer (D14). The
        writer refuses an existing name (no --force here) and proves the file
        loads before it reports success."""
        from src import trip_builder as tb

        fixture, errors = self._draft(body)
        if errors:
            raise ApiError(400, "invalid", "The trip was not written: fix the fields below.",
                           errors=errors)
        digest = canonical_digest({"fixture": fixture, "dir": str(self.trips_dir)})
        if (body or {}).get("draft_hash") != digest:
            raise ApiError(409, "draft_changed",
                           "The trip changed since the preview, or was never previewed. "
                           "Preview it again.")
        try:
            path = tb.write_fixture(fixture, directory=self.trips_dir, force=False)
        except tb.TripBuilderError as e:
            raise ApiError(409, "refused", str(e))
        return {
            "id": path.stem,
            "path": display_path(path),
            "lines": [f"Wrote {display_path(path)}",
                      "This fixture has NO points prices. Score it LIVE or REPLAY."],
        }

    # ---------------------------------------------------------------- search

    def _search_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """D17: codes and dates are checked with the trip builder's own
        validators BEFORE anything is spent - the CLI search would spend a call
        on a typo. Every refusal is the builder's wording."""
        from datetime import timedelta

        from src.trip_builder import TripBuilderError, validate_date, validate_iata

        body = body or {}
        errors: List[Dict[str, Any]] = []
        out: Dict[str, Any] = {}
        for field, label, key in (("origin", "From", "origin"),
                                  ("destination", "To", "destination")):
            try:
                out[key] = validate_iata(str(body.get(field) or ""), label)
            except TripBuilderError as e:
                errors.append({"leg": None, "field": field, "message": str(e)})
        d_from = d_to = None
        try:
            d_from = validate_date(str(body.get("date") or ""), "Date")
        except TripBuilderError as e:
            errors.append({"leg": None, "field": "date", "message": str(e)})
        raw_to = str(body.get("date_to") or "").strip()
        if raw_to:
            try:
                d_to = validate_date(raw_to, "To date")
            except TripBuilderError as e:
                errors.append({"leg": None, "field": "date_to", "message": str(e)})
        if d_from and d_to and d_to < d_from:
            errors.append({"leg": None, "field": "date_to",
                           "message": f"To date: {d_to} is before the date {d_from}. "
                                      f"Refused rather than swapped."})
        if out.get("origin") and out.get("origin") == out.get("destination"):
            errors.append({"leg": None, "field": "destination",
                           "message": f"To: origin and destination are both {out['origin']}."})
        if errors:
            raise ApiError(400, "invalid", "The search was not sent: fix the fields below.",
                           errors=errors)
        out["from"] = str(d_from)
        out["to"] = str(d_to) if d_to else str(d_from + timedelta(days=30))
        out["date_arg"] = f"{d_from}:{d_to}" if d_to else str(d_from)
        return out

    def search_argv(self, req: Dict[str, Any]) -> List[str]:
        return (["--origin", req["origin"], "--destination", req["destination"],
                 "--date", req["date_arg"], "--max-results", str(SEARCH_MAX_RESULTS)]
                + self.wallet_argv())

    def _search_digest(self, req: Dict[str, Any]) -> str:
        return canonical_digest({"kind": "search", "request": req,
                                 "wallet": self.wallet_argv(),
                                 "wallet_file_sha256": self._wallet_file_digest()})

    def search_preflight(self, body: Dict[str, Any]) -> Dict[str, Any]:
        req = self._search_request(body)
        blocked = self._blocked("search")
        return {
            "max_calls": PAGES_PER_SEARCH,
            "window": {"from": req["from"], "to": req["to"]},
            "argv_display": shlex.join(PROG + self.search_argv(req)),
            "calls": self.calls_state(),
            "blocked": blocked,
            "confirm_id": None if blocked else self.issue_confirm(self._search_digest(req)),
        }

    def search_run(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from src.ui import serialize

        req = self._search_request(body)
        self.redeem_confirm((body or {}).get("confirm_id"), self._search_digest(req))
        with self.run_slot():
            out = self.invoke(self.search_argv(req))
            payload = serialize.search_run(out, req, self.calls_state())
        return self.store(payload)

    def _blocked(self, mode: str) -> Optional[Dict[str, str]]:
        """Why this run would be refused before it starts, in the CLI's words.
        The run itself still goes through dispatch, which refuses it the same way."""
        w = self.wallet_state()
        if w.get("error"):
            return {"kind": "wallet", "message": f"Wallet error: {w['error']}"}
        if mode in ("live", "search"):
            key = self.key_state()
            if not key["found"]:
                return {"kind": "key", "message": key["error_text"]}
        return None

    def trip_run(self, trip_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        from src.ui import serialize

        path = self.trip_path(trip_id)
        opts = self._trip_options(trip_id, body or {})
        argv = self.trip_argv(path, opts)
        if opts["mode"] == "live":
            # Before ANY transport exists: no confirm, no call.
            self.redeem_confirm((body or {}).get("confirm_id"),
                                self._trip_digest(trip_id, path, opts))
        with self.run_slot():
            out = self.invoke(argv)
            payload = serialize.trip_run(out, opts["mode"], trip_id, self.calls_state())
        return self.store(payload)

    # ------------------------------------------------------------- running

    @contextmanager
    def run_slot(self):
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "busy", BUSY)
        try:
            yield
        finally:
            self._lock.release()

    def parse_argv(self, argv: List[str]):
        """The CLI's own parser. argparse exits on a bad value; that is a 400."""
        parser = cli.build_parser()
        err = io.StringIO()
        try:
            import contextlib

            with contextlib.redirect_stderr(err):
                return parser.parse_args(argv)
        except SystemExit:
            raise ApiError(400, "invalid_option",
                           "Invalid option: " + " ".join(err.getvalue().split())[-400:])

    def invoke(self, argv: List[str]) -> Dict[str, Any]:
        """One CLI invocation in this process: parse, reset, dispatch, record.
        The caller holds the run slot."""
        from src.seats_client import SeatsClient

        args = self.parse_argv(argv)
        buf = io.StringIO()
        console = Console(file=buf, width=WIDTH)
        # D7: exactly what a fresh `python -m src.main` process starts with,
        # except the daily call counter, which is Seats.aero's and is kept.
        SeatsClient.CACHE.clear()
        SeatsClient.CACHE_META.clear()
        budget_before = SeatsClient._budget_remaining()
        sink: list = []
        started = datetime.now().astimezone()
        t0 = time.monotonic()
        code = cli.dispatch(args, console, sink)
        duration = time.monotonic() - t0
        return {
            "args": args,
            "code": code,
            "run": sink[0] if sink else None,
            "transcript": redact_key_line(buf.getvalue()),
            "started_at": started.isoformat(timespec="seconds"),
            "duration_s": round(duration, 2),
            "calls_spent": max(budget_before - SeatsClient._budget_remaining(), 0),
            "argv_display": shlex.join(PROG + list(argv)),
        }

    def store(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        payload["run_id"] = run_id
        self._runs[run_id] = payload
        while len(self._runs) > RUNS_KEPT:
            self._runs.popitem(last=False)
        return payload

    def stored_run(self, run_id: str) -> Dict[str, Any]:
        if run_id not in self._runs:
            raise ApiError(404, "not_found",
                           "No such run. Results are kept in memory for the last "
                           f"{RUNS_KEPT} runs and are lost when the app restarts.")
        return self._runs[run_id]

    # -------------------------------------------------------------- confirms

    def issue_confirm(self, digest: str) -> str:
        cid = uuid.uuid4().hex
        with self._confirms_lock:
            now = time.monotonic()
            for k in [k for k, (_, exp) in self._confirms.items() if exp < now]:
                del self._confirms[k]
            self._confirms[cid] = (digest, now + CONFIRM_TTL_SECONDS)
        return cid

    def redeem_confirm(self, confirm_id, digest: str) -> None:
        """Single use: the id is gone after this call whatever the outcome."""
        if not isinstance(confirm_id, str) or not confirm_id:
            raise ApiError(409, "confirm_required", CONFIRM_REQUIRED)
        with self._confirms_lock:
            entry = self._confirms.pop(confirm_id, None)
        if entry is None:
            raise ApiError(409, "confirm_required", CONFIRM_REQUIRED)
        expected, expires = entry
        if time.monotonic() > expires or expected != digest:
            raise ApiError(409, "confirm_stale", CONFIRM_STALE)


def _int_in(value, lo: int, hi: int, field: str, label: str) -> int:
    if isinstance(value, bool):
        raise ApiError(400, "invalid_option", f"{label} must be a whole number from {lo} to {hi}.",
                       field=field)
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_option", f"{label} must be a whole number from {lo} to {hi}.",
                       field=field)
    if not lo <= n <= hi:
        raise ApiError(400, "invalid_option", f"{label} must be a whole number from {lo} to {hi}.",
                       field=field)
    return n


def _fmt_cents(cents: float) -> str:
    text = f"{float(cents):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def relocation_lines() -> List[str]:
    """What `print_relocation_banner` would print, as plain text."""
    return [
        f"  {var} is set: {os.environ[var]} (overrides the default location for this run)"
        for var in cli.RELOCATION_VARS
        if os.environ.get(var)
    ]


def redact_key_line(text: str) -> str:
    """The transcript's key line, with the masked key replaced. The source
    stays: which key was used is provenance; its characters are not needed."""
    return KEY_LINE.sub(lambda m: m.group(1) + REDACTED_KEY + m.group(3), text)


def canonical_digest(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
