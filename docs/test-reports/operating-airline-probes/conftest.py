"""Shared harness for the operating-airline Tester probes (round 1).

These live OUTSIDE `testpaths` on purpose: each test asserts the CORRECT
behaviour, so a RED test here is a defect that is still present and a GREEN
test is an attack that held up. Run with:

    .venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/operating-airline-probes

NO TEST HERE MAKES A NETWORK CALL. `requests.get` is replaced by a URL-routing
stub for every run, and a socket CANARY (`net_canary`) is armed in every test:
any attempt to open a real connection is recorded and fails the test, so a
green "no network" claim here is proved, not assumed.

Uses only pytest features present in 7.4.0 (fixtures, monkeypatch, tmp_path,
capsys, parametrize).
"""
import copy
import json
import re
import socket
import sys
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.conftest import isolated_environment, no_network_egress  # noqa: E402,F401
from tests import _trips_payloads as tp  # noqa: E402

REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
CSP = "Chase Sapphire Preferred"
ROUTES = {
    ("SFO", "MAD"): ("B1", "2027-01-15"),
    ("MAD", "AMS"): ("B2", "2027-01-19"),
    ("AMS", "LHR"): ("B3", "2027-01-23"),
    ("LHR", "SFO"): ("B4", "2027-01-27"),
}
LEG_ROUTE = {v[0]: k for k, v in ROUTES.items()}
BASE = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", CSP,
    "--transfer-date", "2026-09-15",
]
UNSET = object()


def flat(text):
    return re.sub(r"\s+", " ", text)


def aid_for(leg, tag="A"):
    """A valid 27-character availability id for a leg."""
    base = f"{leg}{tag}"
    return (base + "x" * 27)[:27]


def row(source="virginatlantic", cost="60000", taxes=45000, currency="GBP",
        airlines="VS, DL", origin="LHR", dest="SFO", iso="2027-01-27",
        cabin="J", rid=None, extra_cabins=None):
    """One Seats.aero availability row. One available cabin unless extra_cabins."""
    r = copy.deepcopy(REAL["data"][0])
    r["Route"]["Source"] = source
    r["Route"]["OriginAirport"] = origin
    r["Route"]["DestinationAirport"] = dest
    eu = {"LHR", "MAD", "AMS", "CDG"}
    r["Route"]["OriginRegion"] = "Europe" if origin in eu else "North America"
    r["Route"]["DestinationRegion"] = "Europe" if dest in eu else "North America"
    r["Date"] = iso
    r["ParsedDate"] = f"{iso}T00:00:00Z"
    r["ID"] = rid if rid is not None else aid_for(ROUTES.get((origin, dest), ("X",))[0], source[:3])
    for c in "YWJF":
        r[f"{c}Available"] = False
        r[f"{c}MileageCost"] = "0"
        r[f"{c}TotalTaxes"] = 0
        r[f"{c}Airlines"] = ""
        r[f"{c}RemainingSeats"] = 0
    r[f"{cabin}Available"] = True
    r[f"{cabin}MileageCost"] = cost
    r[f"{cabin}RemainingSeats"] = 2
    if taxes is UNSET:
        r.pop(f"{cabin}TotalTaxes", None)
    else:
        r[f"{cabin}TotalTaxes"] = taxes
    r["TaxesCurrency"] = currency
    r[f"{cabin}Airlines"] = airlines
    for c, spec in (extra_cabins or {}).items():
        r[f"{c}Available"] = True
        r[f"{c}MileageCost"] = spec.get("cost", "30000")
        r[f"{c}TotalTaxes"] = spec.get("taxes", 30000)
        r[f"{c}Airlines"] = spec.get("airlines", airlines)
        r[f"{c}RemainingSeats"] = 2
    return r


def aeroplan_row(origin, dest, iso):
    """The committed real Aeroplan capture, moved to this route (known CAD taxes)."""
    r = copy.deepcopy(REAL["data"][0])
    r["Route"]["OriginAirport"], r["Route"]["DestinationAirport"] = origin, dest
    r["Date"], r["ParsedDate"] = iso, f"{iso}T00:00:00Z"
    leg = ROUTES.get((origin, dest), ("X",))[0]
    r["ID"] = aid_for(leg, "aer")
    return r


class Stub:
    """
    requests.get, routed by URL, recording every call IN ORDER.

    rows_for(origin, dest, iso) -> list of rows, or None for the Aeroplan default.
    trips: {availability id: payload | (status, payload) | callable(url, kw) -> response}
    """

    def __init__(self, rows_for=None, trips=None, default_trips=None):
        self.rows_for = rows_for or (lambda o, d, iso: None)
        self.trips = trips or {}
        self.default_trips = default_trips
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, dict(kwargs.get("params") or {}), dict(kwargs.get("headers") or {})))
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.status_code = 200
        if url.endswith("/search"):
            params = kwargs.get("params") or {}
            o, d = params.get("origin_airport"), params.get("destination_airport")
            iso = ROUTES.get((o, d), (None, params.get("start_date")))[1]
            rows = self.rows_for(o, d, iso)
            if rows is None:
                rows = [aeroplan_row(o, d, iso)]
            body = {"data": rows, "count": len(rows), "hasMore": False}
        else:
            aid = url.rsplit("/", 1)[-1]
            spec = self.trips.get(aid, self.default_trips)
            if callable(spec):
                return spec(url, kwargs)
            if isinstance(spec, tuple):
                r.status_code, body = spec
            else:
                body = spec if spec is not None else tp.payload([])
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    @property
    def urls(self):
        return [c[0] for c in self.calls]

    @property
    def trips_calls(self):
        return [u for u in self.urls if "/trips/" in u]

    @property
    def search_calls(self):
        return [u for u in self.urls if u.endswith("/search")]


def vs_itinerary(aid, flight="VS19", origin="LHR", dest="SFO", cost=60000, **kw):
    kw.setdefault("availability_id", aid)
    return tp.trip([tp.segment(flight, origin, dest, 1, AvailabilityID=aid)], cost=cost, **kw)


def reset_client():
    from src.seats_client import SeatsClient

    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


@pytest.fixture(autouse=True)
def _fresh_client_state():
    reset_client()
    yield
    reset_client()


class Canary:
    def __init__(self):
        self.attempts = []


@pytest.fixture(autouse=True)
def net_canary(monkeypatch):
    """Record (and refuse) ANY real socket connection. Asserted clean after the test."""
    canary = Canary()

    def refuse(self, address, *a, **k):
        canary.attempts.append(address)
        raise OSError(f"PROBE CANARY: a real connection to {address!r} was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    orig_cc = socket.create_connection

    def refuse_cc(address, *a, **k):
        canary.attempts.append(address)
        raise OSError(f"PROBE CANARY: create_connection({address!r})")

    monkeypatch.setattr(socket, "create_connection", refuse_cc)
    yield canary
    assert canary.attempts == [], f"a real network connection was attempted: {canary.attempts}"


def run_cli(argv, capsys, monkeypatch, stub=None, key="test_key_probe_0001", env_key=True):
    """The whole CLI in-process with a stubbed transport. (exit, raw, flat, stub)."""
    from src.main import main

    if key and env_key:
        monkeypatch.setenv("SEATS_AERO_KEY", key)
    reset_client()
    stub = stub or Stub()
    old = sys.argv
    sys.argv = ["prog"] + list(argv)
    try:
        with patch("src.seats_client.requests.get", side_effect=stub):
            code = main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    return code, out, flat(out), stub


def rm():
    from src.ratio_manager import RatioManager

    return RatioManager(
        ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


def evaluate(stub, tmp_path=None, trips_mode="auto", cache=None, travelers=None,
             fixture_mutator=None, client=None, **opt_kw):
    """apply_live + evaluate_trip + totals + rendered text, without main()."""
    from rich.console import Console

    from src.formatter import (
        print_leg_detail,
        print_leg_results,
        print_live_banner,
        print_live_leg_detail,
        print_trip_totals,
    )
    from src.live_trip import LiveOptions, annotate_live_verdicts, apply_live
    from src.optimizer import evaluate_trip, trip_totals
    from src.seats_client import SeatsClient
    from src.trip_loader import load_trip_fixture
    from src.wallet import Wallet

    fixture = load_trip_fixture(TRIP_B)
    if fixture_mutator:
        fixture_mutator(fixture)
    reset_client()
    client = client or SeatsClient(api_key="test_key_probe_eval")
    opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode=trips_mode, **opt_kw)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, outcomes = apply_live(fixture, client, opts)
    wallet = Wallet(balances={"UR": 160000}, cards=[CSP])
    results = annotate_live_verdicts(
        evaluate_trip(
            fixture.legs, ratios_manager=rm(), wallet=wallet,
            transfer_date=date(2026, 9, 15), today=date(2026, 9, 10),
        )
    )
    totals = trip_totals(results, wallet)
    buf = StringIO()
    con = Console(file=buf, width=250, no_color=True)
    print_leg_results(results, console=con)
    print_leg_detail(results, console=con)
    print_live_leg_detail(results, console=con)
    print_trip_totals(totals, console=con)
    return {r.leg.id: r for r in results}, totals, buf.getvalue(), opts, fixture


def vs_b4_rows(airlines="VS, DL", taxes=45000, cost="60000", rid=None, **kw):
    """rows_for giving B4 (LHR->SFO) one Virgin Atlantic J row; other legs Aeroplan."""
    rid = aid_for("B4", "vir") if rid is None else rid

    def rows_for(o, d, iso):
        if (o, d) == ("LHR", "SFO"):
            return [row(airlines=airlines, taxes=taxes, cost=cost, rid=rid, iso=iso, **kw)]
        return None

    return rows_for


B4_VS = aid_for("B4", "vir")


# ---------------------------------------------------------------------------
# Re-test 4: the D1(b) / must-fix-2 YQ contract, in one place.
#   data/yq_inclusion.csv is source,airline,verdict,verified_on,evidence,notes;
#   a record carries "itinerary lookup status", "checked airline" and the
#   "operated by <airline> itself (yes / no)" line.
# ---------------------------------------------------------------------------

YQ_HEADER = "source,airline,verdict,verified_on,evidence,notes"


def yq_record_body(source="virginatlantic", verdict="includes_yq", airline="VS", *, title_source=None,
                   status="KNOWN", checked=None, operated="yes", operated_code=None, extra=""):
    checked = airline if checked is None else checked
    operated_code = airline if operated_code is None else operated_code
    return (
        f"# yq-check record: {title_source or source}, 2026-09-10\n\n## Seats.aero\n\n"
        f"- program: Virgin Atlantic Flying Club (source {source})\n{extra}"
        f"- itinerary lookup status: {status}\n"
        f"- checked airline (the award's KNOWN flight-number carrier): {checked}\n\n"
        f"## site\n\n"
        f"- the site shows this flight operated by {operated_code} itself, not a codeshare partner "
        f"(yes / no): {operated}\n"
        f"- total taxes, fees and carrier-imposed charges for ONE adult, as the site shows it "
        f"(one combined figure, or its lines added up): GBP 450.00\n"
        f"- verdict (includes_yq / excludes_yq / inconclusive): {verdict}\n"
    )


def yq_write_record(root, body, name="2026-09-10-virginatlantic.md"):
    d = root / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return f"docs/yq-checks/{name}"


def yq_load(root, *rows):
    from src import yq_inclusion

    csv = root / "yq.csv"
    csv.write_text(YQ_HEADER + "\n" + "\n".join(rows) + "\n")
    return yq_inclusion.load(csv, today=date(2026, 9, 11), root=root)


def yq_table(which, source="virginatlantic", airline="VS", evidence="docs/yq-checks/2026-09-10-virginatlantic.md"):
    from src.yq_inclusion import YqVerdict

    return {(source, airline): YqVerdict(source=source, airline=airline, verdict=which,
                                         verified_on=date(2026, 9, 10), evidence=evidence)}


# RE-TEST 4 (the Manager's must-fix 1, applied to these probes too). Every probe
# here describes the UNVERIFIED state, so the two label constants are pinned for
# every in-process probe: flipping them in source must not turn this suite red.
# Probes that run a CHILD process on a tree (the master / round-2 byte
# comparisons) read the source constants and need the committed, unflipped tree
# and a git checkout; they say so in their docstrings.
@pytest.fixture(autouse=True)
def _pin_unverified_label_constants(monkeypatch):
    from src import seats_trips

    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "")
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "unverified")
