"""
The CLI golden scenarios, and the one narrow normalizer they are compared under.

WHY THIS EXISTS. The local UI (docs/plans/ui.md) splits `src/main.py` and
`src/formatter.py` into "build the structure, then print it" so the UI can read
the same objects the terminal prints from. That refactor must not move a single
byte of CLI output, and the pre-refactor code does not exist after it. So the
output of thirteen real invocations was recorded BEFORE any `src/` change, into
`tests/fixtures/cli_golden/`, and `tests/test_cli_output_unchanged.py` compares
every later commit against them.

Shared by the test and by the writer (`PO_WRITE_CLI_GOLDENS=1 pytest
tests/test_cli_output_unchanged.py`), so the scenario that produced a golden and
the scenario that is compared with it cannot drift.

WHAT IS HELD STILL, AND HOW
===========================
* THE HARNESS IS CONFTEST'S. No key, a temporary HOME, the network refused.
  Scenarios that need a transport patch `requests.get` with a stub routed by URL
  (the pattern of tests/test_metal_end_to_end.py) or with a refusal.
* THE DATE AND THE CLOCK ARE PINNED, NOT MASKED. The FX banner prints each rate's age in days
  and, past 30 days, a STALE RATE marker - lines that would change every day and
  appear from 2026-10-09 on. `config.date` is replaced by a date whose today()
  is `PINNED_TODAY`, so those lines are identical on any day. The clock the
  cache and the client stamp fetches with (`response_cache._utcnow`,
  `seats_client.datetime.now`) is pinned to `PINNED_NOW` for the same reason.
  Pinning makes the run deterministic; masking would only hide the difference.
* PATHS ARE RELATIVE. Each scenario runs in its own empty working directory with
  `config.CACHE_DIR` and `config.SNAPSHOT_DIR` set to relative paths inside it,
  so no machine-specific temporary path reaches the output (a path of a
  different length would also change where rich wraps a line). The
  POINTS_OPTIMIZER_* relocation variables are unset for the in-process run: they
  exist for CHILD processes, and conftest has already relocated this process.
* THE TRIPS PARSER LABEL is pinned to its unverified state, as the suite's own
  label tests do, so flipping the two constants does not turn this red.

THE NORMALIZER is a narrow safety net over what the pins above already hold
still: an instant directly after "fetched"/"captured", "N minutes ago", and the
stamp inside a snapshot filename. It never masks a date, an amount, a
percentage or a stub's own flight times.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import requests
from rich.console import Console

from tests import _trips_payloads as tp

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "cli_golden"
REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)
CSP = "Chase Sapphire Preferred"
FAKE_KEY = "golden_fake_key_0123456789"
PINNED_TODAY = date(2026, 9, 11)
PINNED_NOW = datetime(2026, 9, 11, 9, 30, 0, tzinfo=timezone.utc)
WIDTH = 190

WALLET = ["--balance", "UR=160000", "--card", CSP, "--transfer-date", "2026-09-15"]

# ---------------------------------------------------------------------------
# Transport: requests.get, routed by URL (tests/test_metal_end_to_end.py's Stub)
# ---------------------------------------------------------------------------

ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}
B4_ID = tp.AVAIL_ID


def vs_row() -> Dict:
    """B4 LHR->SFO: a Virgin Atlantic J award whose row lists VS, DL. SYNTHETIC."""
    row = copy.deepcopy(REAL["data"][0])
    row["ID"] = B4_ID
    row["Route"].update(
        OriginAirport="LHR", DestinationAirport="SFO", OriginRegion="Europe",
        DestinationRegion="North America", Source="virginatlantic",
    )
    row["Date"] = "2027-01-27"
    row["ParsedDate"] = "2027-01-27T00:00:00Z"
    row.update(
        YAvailable=False, JAvailable=True, JMileageCost="60000", JTotalTaxes=45000,
        TaxesCurrency="GBP", JAirlines="VS, DL", JRemainingSeats=2,
    )
    return row


class Stub:
    """requests.get, routed by URL. Records every call in order. SYNTHETIC data:
    the committed SFO-MAD Aeroplan row, re-dated and re-routed, plus B4's VS row
    and one VS19 itinerary for it."""

    def __init__(self, trips_payloads=None, search_override: Optional[Callable] = None):
        self.calls: List[str] = []
        self.trips_payloads = trips_payloads if trips_payloads is not None else {
            B4_ID: tp.payload([tp.vs_direct("VS19", departs="2027-01-27T11:00:00Z")])
        }
        self.search_override = search_override

    @property
    def search_calls(self) -> int:
        return sum(1 for c in self.calls if c.endswith("/search"))

    @property
    def trips_calls(self) -> int:
        return sum(1 for c in self.calls if "/trips/" in c)

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        r = MagicMock()
        r.raise_for_status.return_value = None
        if url.endswith("/search"):
            params = kwargs.get("params") or {}
            route = (params.get("origin_airport"), params.get("destination_airport"))
            if self.search_override is not None:
                payload = self.search_override(route, params)
            elif route == ("LHR", "SFO"):
                payload = {"data": [vs_row()]}
            else:
                payload = {"data": copy.deepcopy(REAL["data"])}
                row = payload["data"][0]
                iso = ROUTES.get(route, params.get("start_date"))
                row["Date"] = iso
                row["ParsedDate"] = f"{iso}T00:00:00Z"
                row["Route"]["OriginAirport"], row["Route"]["DestinationAirport"] = route
                row["ID"] = f"row{route[0]}{route[1]}0000000000000"
            r.status_code = 200
            r.json.return_value = payload
            r.text = json.dumps(payload)
            return r
        aid = url.rsplit("/", 1)[-1]
        r.status_code = 200
        body = self.trips_payloads.get(aid, tp.payload([]))
        r.json.return_value = body
        r.text = json.dumps(body)
        return r


class Refused:
    """requests.get with the network down."""

    def __init__(self):
        self.calls: List[str] = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        raise requests.ConnectionError(
            "Connection refused (golden scenario: the network is down)"
        )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    argv: List[str]
    transport: Optional[str] = None  # None | "stub" | "refused"
    needs_key: bool = False
    # Runs in the same working directory BEFORE the scenario (its output is
    # discarded). G6/G13 replay what G4 archived.
    before: Optional[str] = None
    prepare: Optional[Callable[[Path], None]] = None
    patches: List[Tuple[str, object]] = field(default_factory=list)
    note: str = ""


G7_FIXTURE = GOLDEN_DIR / "inputs" / "g7_never_priced_couple.json"


def _drop_one_snapshot(workdir: Path) -> None:
    """G13: delete B2's snapshot so the manifest no longer matches the files."""
    snaps = sorted((workdir / "snapshots").glob("B2_*.json"))
    if not snaps:
        raise AssertionError("G13 setup: G4 archived no B2 snapshot to delete")
    for p in snaps:
        p.unlink()


def _overdrawn_funding_report(results, wallet):
    """
    G8 ONLY. Exit 4 is not reachable from real CLI input: the trip-level
    balance ceiling demotes a leg the balance cannot fund before the funding
    report is written, so no committed fixture and wallet overdraw. The CLI's
    exit-4 RENDERING still exists and must be pinned, so this scenario forces
    the funding report into its overdrawn state. It is labelled synthetic.
    """
    report = _REAL_FUNDING_REPORT(results, wallet)
    report["trip_overdrawn_currencies"] = {"UR": {"needed": 200000, "balance": 160000}}
    report["trip_funding_executable"] = False
    report["trip_funding_note"] = (
        "THIS PLAN CANNOT BE EXECUTED: it spends 200,000 UR against a balance of "
        "160,000. No margin is quoted for a plan the balance cannot fund."
    )
    return report


_REAL_FUNDING_REPORT = None


SCENARIOS: Dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario("G1", ["--trip-fixture", "trip_b_europe.json", "--offline", *WALLET,
                        "--show-alternatives"], note="Trip B offline"),
        Scenario("G2", ["--trip-fixture", "trip_a_mry_nyc.json", "--offline", *WALLET],
                 note="Trip A offline"),
        Scenario("G3", ["--trip-fixture", "trip_c_lon_mry_surcharge.json", "--offline",
                        *WALLET, "--show-alternatives"], note="Trip C offline"),
        Scenario("G4", ["--trip-fixture", "trip_b_europe.json", *WALLET],
                 transport="stub", needs_key=True,
                 note="Trip B LIVE against the SYNTHETIC stub (VS19 on B4)"),
        Scenario("G5", ["--trip-fixture", "trip_b_europe.json", *WALLET],
                 transport="refused", needs_key=True,
                 note="Trip B LIVE with the network refused (exit 3)"),
        Scenario("G6", ["--trip-fixture", "trip_b_europe.json", *WALLET,
                        "--from-snapshot", "snapshots/MANIFEST.md"],
                 before="G4", note="replay of G4's snapshots"),
        Scenario("G7", ["--trip-fixture", str(G7_FIXTURE), "--offline", *WALLET],
                 note="never-priced legs + a 2-traveller flight leg, offline"),
        Scenario("G8", ["--trip-fixture", "trip_b_europe.json", "--offline", *WALLET],
                 patches=[("src.optimizer.trip_funding_report", "overdrawn")],
                 note="exit 4, funding report forced overdrawn (SYNTHETIC)"),
        Scenario("G9", ["--origin", "SFO", "--destination", "MAD", "--date",
                        "2027-01-15", "--balance", "UR=160000", "--card", CSP],
                 transport="stub", needs_key=True, note="search, stubbed SFO-MAD"),
        Scenario("G10", ["--origin", "SFO", "--destination", "MAD", "--date",
                         "2027-01-15", "--balance", "UR=160000", "--card", CSP],
                 transport="refused", needs_key=True, note="search, API error"),
        Scenario("G11", ["--origin", "SFO", "--destination", "MAD", "--date",
                         "2027-01-15", "--balance", "MR=100000"],
                 transport="stub", needs_key=True, note="search, none fundable"),
        Scenario("G12", ["--trip-fixture", "trip_b_europe.json", "--offline"],
                 note="no wallet (exit 2)"),
        Scenario("G13", ["--trip-fixture", "trip_b_europe.json", *WALLET,
                         "--from-snapshot", "snapshots/MANIFEST.md"],
                 before="G4", prepare=_drop_one_snapshot,
                 note="replay refused: a snapshot is missing (exit 1)"),
    ]
}


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------


class _PinnedDate(date):
    @classmethod
    def today(cls):  # noqa: D401 - the whole point
        return PINNED_TODAY


class _PinnedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return PINNED_NOW if tz is not None else PINNED_NOW.replace(tzinfo=None)


@dataclass
class Result:
    code: int
    text: str
    sink: list
    transport: object = None


def _reset_process_state() -> None:
    """What a fresh `python -m src.main` process would start with."""
    from src.seats_client import SeatsClient

    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _invoke(argv: List[str]) -> Tuple[int, str, list]:
    """Through `dispatch` when it exists; through `main()` and stdout before."""
    from src import main as cli

    sink: list = []
    if hasattr(cli, "dispatch"):
        buf = io.StringIO()
        console = Console(file=buf, width=WIDTH)
        args = cli.build_parser().parse_args(argv)
        code = cli.dispatch(args, console, sink)
        return code, buf.getvalue(), sink
    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["python -m src.main"] + list(argv)
    try:
        with contextlib.redirect_stdout(buf):
            code = cli.main()
    finally:
        sys.argv = old
    return code, buf.getvalue(), sink


def run_scenario(name: str, workdir: Path, monkeypatch) -> Result:
    """Run scenario `name` in `workdir` (empty, owned by the caller)."""
    global _REAL_FUNDING_REPORT
    from src import config, seats_trips

    scen = SCENARIOS[name]
    workdir.mkdir(parents=True, exist_ok=True)
    if scen.before:
        run_scenario(scen.before, workdir, monkeypatch)
    if scen.prepare:
        scen.prepare(workdir)

    monkeypatch.chdir(workdir)
    monkeypatch.setattr(config, "CACHE_DIR", Path("cache"))
    monkeypatch.setattr(config, "SNAPSHOT_DIR", Path("snapshots"))
    monkeypatch.setattr(config, "date", _PinnedDate)
    from src import response_cache, seats_client

    monkeypatch.setattr(response_cache, "_utcnow", lambda: PINNED_NOW)
    monkeypatch.setattr(seats_client, "datetime", _PinnedDatetime)
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "")
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "unverified")
    for var in ("POINTS_OPTIMIZER_ENV_FILE", "POINTS_OPTIMIZER_CACHE_DIR",
                "POINTS_OPTIMIZER_SNAPSHOT_DIR", "FORCE_COLOR", "NO_COLOR",
                "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        monkeypatch.delenv(var, raising=False)
    if scen.needs_key:
        monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    else:
        monkeypatch.delenv(config.KEY_ENV_VAR, raising=False)

    transport = None
    stack = contextlib.ExitStack()
    with stack:
        if scen.transport == "stub":
            transport = Stub()
        elif scen.transport == "refused":
            transport = Refused()
        if transport is not None:
            stack.enter_context(patch("src.seats_client.requests.get", side_effect=transport))
        for target, what in scen.patches:
            if what == "overdrawn":
                from src import optimizer

                _REAL_FUNDING_REPORT = optimizer.trip_funding_report
                stack.enter_context(patch(target, side_effect=_overdrawn_funding_report))
        _reset_process_state()
        code, text, sink = _invoke(scen.argv)
    return Result(code=code, text=text, sink=sink, transport=transport)


# ---------------------------------------------------------------------------
# The normalizer: narrow on purpose
# ---------------------------------------------------------------------------

MASKS = [
    # An instant straight after "fetched"/"captured": the fetch clock. The
    # date/time separator may be a line break, because rich wraps at spaces.
    (re.compile(
        r"(?<=[Ff]etched |aptured )\d{4}-\d{2}-\d{2}(?:T|\s+)\d{2}:\d{2}"
        r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})"
    ), "<TIMESTAMP>"),
    # The stamp inside an archived snapshot's filename: _20260911T1234Z.json
    (re.compile(r"_\d{8}T\d{4}Z(?=\.json)"), "_<STAMP>"),
    (re.compile(r"\b\d+ minutes ago\b"), "<N> minutes ago"),
]


def normalize(text: str) -> Tuple[str, int]:
    """(masked text, number of tokens masked)."""
    total = 0
    for pattern, repl in MASKS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.txt"


def display_argv(argv: List[str]) -> str:
    """The argv as a reader would type it from the repo root."""
    prefix = str(ROOT) + os.sep
    return " ".join(a[len(prefix):] if a.startswith(prefix) else a for a in argv)


def header(scen: Scenario, code: int) -> str:
    return (
        f"# scenario {scen.name}: {scen.note}\n"
        f"# argv: {display_argv(scen.argv)}\n"
        f"# exit: {code}\n"
    )
