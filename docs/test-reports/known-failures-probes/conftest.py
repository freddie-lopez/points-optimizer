"""Shared helpers for the known-failures Tester probes.

These live OUTSIDE `testpaths` on purpose: each test asserts the CORRECT
behaviour, so a RED test here is a defect that is still present. Run with:

    .venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/known-failures-probes

No test here makes a network call: `requests.get` is patched with a stub that
serves synthetic Seats.aero rows built from the committed SFO-MAD capture.
"""
import copy
import json
import re
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


def _load_v5_probe_helpers():
    """The v5 probes' corpus builder, importable as `docs_v5` (its file is also
    named conftest.py, so it cannot be imported by name from here)."""
    import importlib.util

    if "docs_v5" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "docs_v5", ROOT / "docs" / "test-reports" / "v5-probes" / "conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["docs_v5"] = mod
    spec.loader.exec_module(mod)


_load_v5_probe_helpers()

REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
CSP = "Chase Sapphire Preferred"
ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}
UNSET = object()
BASE_ARGV = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", CSP,
    "--transfer-date", "2026-09-15",
]


def row(source="aeroplan", cost="50000", taxes=4460, currency="CAD",
        airlines="AC, LH, UA, VL", origin="SFO", dest="MAD", iso="2027-01-15",
        cabin="Y", rid=None, regions=None):
    """One Seats.aero availability row with a single available cabin."""
    r = copy.deepcopy(REAL["data"][0])
    r["Route"]["Source"] = source
    r["Route"]["OriginAirport"] = origin
    r["Route"]["DestinationAirport"] = dest
    if regions:
        r["Route"]["OriginRegion"], r["Route"]["DestinationRegion"] = regions
    elif origin in ("LHR", "LGW", "MAN", "EDI", "LCY", "STN", "BHX", "GLA"):
        r["Route"]["OriginRegion"] = "Europe"
        r["Route"]["DestinationRegion"] = "North America"
    r["Date"] = iso
    r["ParsedDate"] = f"{iso}T00:00:00Z"
    r["ID"] = rid or f"{source}-{origin}{dest}{iso}-{cabin}-{cost}"
    for c in "YWJF":
        r[f"{c}Available"] = False
        r[f"{c}MileageCost"] = "0"
        r[f"{c}TotalTaxes"] = 0
        r[f"{c}Airlines"] = ""
        r[f"{c}RemainingSeats"] = 0
    r[f"{cabin}Available"] = True
    r[f"{cabin}MileageCost"] = cost
    r[f"{cabin}RemainingSeats"] = 4
    if taxes is UNSET:
        r.pop(f"{cabin}TotalTaxes", None)
    else:
        r[f"{cabin}TotalTaxes"] = taxes
    r["TaxesCurrency"] = currency
    r[f"{cabin}Airlines"] = airlines
    return r


def default_rows(origin, dest, iso):
    """The real Aeroplan capture, moved to this route. Known CAD taxes."""
    return [row(origin=origin, dest=dest, iso=iso)]


def make_side(rows_for):
    """`rows_for(origin, dest, iso)` -> list of rows, or None for the default."""

    def side(*args, **kwargs):
        params = kwargs.get("params") or {}
        o, d = params.get("origin_airport"), params.get("destination_airport")
        iso = ROUTES.get((o, d), params.get("start_date"))
        rows = rows_for(o, d, iso)
        if rows is None:
            rows = default_rows(o, d, iso)
        r = MagicMock()
        r.json.return_value = {"data": rows, "count": len(rows), "hasMore": False}
        r.status_code = 200
        r.raise_for_status.return_value = None
        return r

    return side


def reset_client():
    from src.seats_client import SeatsClient

    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient._CALLS = {}


def run_cli(argv, rows_for, capsys, monkeypatch, key="test_key_not_a_real_one"):
    """The whole CLI in-process with a stubbed transport. (exit, raw, flat)."""
    from src.main import main

    if key:
        monkeypatch.setenv("SEATS_AERO_KEY", key)
    reset_client()
    old = sys.argv
    sys.argv = ["prog"] + list(argv)
    try:
        with patch("src.seats_client.requests.get", side_effect=make_side(rows_for)):
            code = main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    return code, out, flat(out)


def evaluate(rows_for, tmp_path, fixture_path=TRIP_B, flex_days=0,
             balances=None, fixture_mutator=None):
    """apply_live + evaluate_trip + totals + rendered text, without main()."""
    from rich.console import Console

    from src.formatter import (
        print_leg_detail,
        print_leg_results,
        print_live_leg_detail,
        print_trip_totals,
    )
    from src.live_trip import LiveOptions, annotate_live_verdicts, apply_live
    from src.optimizer import evaluate_trip, trip_totals
    from src.ratio_manager import RatioManager
    from src.response_cache import ResponseCache
    from src.seats_client import SeatsClient
    from src.trip_loader import load_trip_fixture
    from src.wallet import Wallet

    fixture = load_trip_fixture(fixture_path)
    if fixture_mutator:
        fixture_mutator(fixture)
    reset_client()
    client = SeatsClient(api_key="test_key")
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    with patch("src.seats_client.requests.get", side_effect=make_side(rows_for)):
        fixture, _ = apply_live(
            fixture, client, LiveOptions(live=True, flex_days=flex_days, cache=cache)
        )
    rm = RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )
    wallet = Wallet(balances=balances or {"UR": 160000}, cards=[CSP])
    results = annotate_live_verdicts(
        evaluate_trip(
            fixture.legs,
            ratios_manager=rm,
            wallet=wallet,
            transfer_date=date(2026, 9, 15),
            today=date(2026, 9, 10),
        )
    )
    totals = trip_totals(results, wallet)
    buf = StringIO()
    con = Console(file=buf, width=250, no_color=True)
    print_leg_results(results, console=con)
    print_leg_detail(results, console=con)
    print_live_leg_detail(results, console=con)
    print_trip_totals(totals, console=con)
    return {r.leg.id: r for r in results}, totals, buf.getvalue()


def flat(text):
    return re.sub(r"\s+", " ", text)


def only(route, rows):
    """rows_for that answers `route` with `rows` and every other leg by default."""

    def rows_for(o, d, iso):
        if (o, d) == route:
            return rows(o, d, iso) if callable(rows) else rows
        return None

    return rows_for


@pytest.fixture(autouse=True)
def _fresh_client_state():
    reset_client()
    yield
    reset_client()
