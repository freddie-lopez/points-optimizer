"""
The operating-airline lookup, end to end through `apply_live`, on Trip B.

Search and trips are both stubbed at `requests.get`, routed by URL, so the real
`SeatsClient` runs its real transport, the real parser, the real metal pass and
the real formatter. No network: tests/conftest.py refuses any unpatched call.

B4 (LHR->SFO) is given a Virgin Atlantic J award whose row lists "VS, DL", and
the trips stub answers its availability id with one VS itinerary. Every other
leg gets the committed SFO-MAD Aeroplan row, re-dated and re-routed.
"""
import copy
import json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src.formatter import print_leg_detail, print_live_leg_detail
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet
from tests import _trips_payloads as tp

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"
REAL = json.loads((ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text())
CSP = "Chase Sapphire Preferred"

ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}
B4_ID = tp.AVAIL_ID


def vs_row():
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
    """requests.get, routed by URL. Records every call in order."""

    def __init__(self, trips_payloads=None, trips_status=None):
        self.calls = []
        self.trips_payloads = trips_payloads if trips_payloads is not None else {
            B4_ID: tp.payload([tp.vs_direct("VS19", departs="2027-01-27T11:00:00Z")])
        }
        self.trips_status = trips_status or {}

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        r = MagicMock()
        r.raise_for_status.return_value = None
        if url.endswith("/search"):
            params = kwargs.get("params") or {}
            route = (params.get("origin_airport"), params.get("destination_airport"))
            if route == ("LHR", "SFO"):
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
        r.status_code = self.trips_status.get(aid, 200)
        body = self.trips_payloads.get(aid, tp.payload([]))
        r.json.return_value = body
        r.text = json.dumps(body)
        return r


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv", ROOT / "data" / "programs.yaml"
    )


@pytest.fixture(autouse=True)
def fresh_client_state():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def run(rm, trips_mode="auto", stub=None, **opt_kw):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    stub = stub or Stub()
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, trips_mode=trips_mode, **opt_kw)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, outcomes = apply_live(fixture, SeatsClient(api_key="test_key_e2e"), opts)
    results = evaluate_trip(
        fixture.legs,
        ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=[CSP]),
        transfer_date=date(2026, 9, 15),
        today=date(2026, 9, 8),
    )
    totals = trip_totals(results)
    return fixture, outcomes, results, totals, stub, opts


def render(results):
    buf = StringIO()
    console = Console(file=buf, width=190)
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    # Rich wraps at the console width; the assertions are about words, not
    # about where a line happens to break.
    return " ".join(buf.getvalue().split())


def _b4(results):
    return next(r for r in results if r.leg.id == "B4")


def test_b4_prints_vs_by_flight_number_with_every_qualifier(rm):
    _, _, results, _, stub, _ = run(rm)
    b4 = _b4(results)
    assert b4.best_points.program == "Virgin Atlantic Flying Club"
    assert b4.best_points.metal.status is MetalStatus.KNOWN
    out = render(results)
    b4_block = out[out.index("B4 -"):]
    assert "operating airline: VS by flight number (VS19)" in b4_block
    assert "flights: VS19 LHR 2027-01-27T11:00:00Z -> SFO" in b4_block
    assert (
        "A codeshare operated by another airline in this award's list (VS, DL) "
        "cannot be detected" in b4_block
    )
    assert "[trips parser UNVERIFIED against a real Seats.aero response" in b4_block
    assert "Seats.aero reports the MARKETING carrier" in b4_block


def test_every_search_call_comes_before_any_trips_call(rm):
    _, _, _, _, stub, _ = run(rm)
    kinds = ["trips" if "/trips/" in c else "search" for c in stub.calls]
    assert kinds.count("trips") == 1
    assert kinds.index("trips") > max(i for i, k in enumerate(kinds) if k == "search")


def test_the_aeroplan_legs_are_not_looked_up_by_policy(rm):
    _, _, results, _, stub, opts = run(rm)
    b1 = next(r for r in results if r.leg.id == "B1")
    assert b1.best_points.metal.status is MetalStatus.NOT_LOOKED_UP
    assert b1.best_points.metal.reason_code == "NOT_NEEDED_POLICY"
    assert "Air Canada Aeroplan levies no carrier surcharge whatever the metal" in b1.best_points.metal.render()
    assert opts.metal_report.requests_sent == 1
    assert opts.metal_report.search_calls == 4
    assert opts.metal_report.trips_calls == 1


FIELDS = (
    "verdict", "points_total_score_usd", "points_score_low_usd", "points_score_high_usd",
    "points_floor_usd", "break_even_surcharge_usd", "margin_usd", "margin_pct",
    "cash_total_score_usd", "verdict_sensitive", "surcharge_cannot_change_verdict",
    "points_required", "has_points_path", "apd_added_usd",
)


def _numbers(results, totals):
    per_leg = {r.leg.id: tuple(getattr(r, f) for f in FIELDS) for r in results}
    reasons = {
        r.leg.id: sorted(
            x.code for x in r.reasons
            if x.code not in ("ALTERNATIVE_UNPRICED", "METAL_LOOKUP_MISSING", "METAL_UNKNOWN")
        )
        for r in results
    }
    kept = {k: v for k, v in totals.items() if "metal" not in k}
    return per_leg, reasons, kept


def test_trips_auto_off_and_not_engaged_score_identically(rm):
    """THE EQUIVALENCE TEST. A lookup cannot move a verdict, score or total."""
    auto = _numbers(*run(rm, "auto")[2:4])
    off = _numbers(*run(rm, "off")[2:4])
    none = _numbers(*run(rm, None)[2:4])
    assert auto == off == none


def test_trips_off_makes_no_trips_call_and_says_so(rm):
    _, _, results, _, stub, _ = run(rm, "off")
    assert not any("/trips/" in c for c in stub.calls)
    metal = _b4(results).best_points.metal
    assert metal.reason_code == "TRIPS_OFF"
    assert "NOT LOOKED UP (--trips off)" in metal.render()
    assert "possible carriers are VS, DL" in metal.render()


def test_not_engaged_means_no_metal_at_all_and_the_legacy_line(rm):
    _, _, results, _, stub, _ = run(rm, None)
    assert all(
        getattr(c, "metal", None) is None for r in results for c in r.leg.points_candidates
    )
    out = render(results)
    assert "operating airline" not in out


def test_an_empty_trips_answer_is_unknown_not_no_trips(rm):
    stub = Stub(trips_payloads={B4_ID: tp.payload([])})
    _, _, results, _, _, _ = run(rm, stub=stub)
    metal = _b4(results).best_points.metal
    assert metal.reason_code == "EMPTY_DATA"
    out = render(results)
    b4_block = out[out.index("B4 -"):]
    assert "operating airline: NOT KNOWN" in b4_block
    assert "no trips" not in b4_block.lower()


def test_a_404_is_unknown_and_says_it_is_not_about_flights(rm):
    stub = Stub(trips_status={B4_ID: 404})
    _, _, results, _, _, _ = run(rm, stub=stub)
    metal = _b4(results).best_points.metal
    assert metal.status is MetalStatus.UNKNOWN and metal.reason_code == "HTTP_404"
    assert "This is NOT a finding about whether the award has flights" in metal.render()
