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
