"""
Fix round 1, findings 10 and 11.

- A 2+ traveller leg is never scored on points, so `auto` spends no trips call
  on it (NOT_NEEDED_PARTY); `all` still looks it up.
- A lookup made for an award that is not the chosen one is printed under its leg.
- A single-carrier award the scorer treats as known metal keeps its `metal:`
  line, and its lookup line does not say "Nothing is known".
"""
import copy
from datetime import date
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from src.formatter import print_leg_detail, print_live_leg_detail
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.optimizer import evaluate_trip
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet
from tests.test_metal_end_to_end import B4_ID, REAL, TRIPS, Stub, vs_row
from tests.test_yq_inclusion import rm  # noqa: F401 - fixture reuse


class RowsStub(Stub):
    """B4 (LHR->SFO) answers with the given rows."""

    def __init__(self, b4_rows, **kw):
        super().__init__(**kw)
        self.b4_rows = b4_rows

    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            r.json.return_value = {"data": copy.deepcopy(self.b4_rows)}
        return r


def run(rm, stub, trips_mode="auto", travelers=1):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    for leg in fixture.legs:
        if leg.id == "B4":
            leg.travelers = travelers
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, trips_mode=trips_mode)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"]),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    buf = StringIO()
    console = Console(file=buf, width=250)
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    b4 = next(l for l in fixture.legs if l.id == "B4")
    return b4, results, " ".join(buf.getvalue().split())


def test_auto_spends_no_trips_call_on_a_leg_for_two_travellers(rm):
    stub = Stub()
    b4, _, _ = run(rm, stub, travelers=2)
    assert not [c for c in stub.calls if "/trips/" in c]
    metal = b4.points_candidates[0].metal
    assert metal.reason_code == "NOT_NEEDED_PARTY"
    assert "is for 2 travellers" in metal.render()
    assert not metal.is_missing_lookup, "a lookup that cannot matter is not a gap"


def test_trips_all_still_looks_a_party_leg_up(rm):
    stub = Stub()
    b4, _, _ = run(rm, stub, trips_mode="all", travelers=2)
    assert [c for c in stub.calls if "/trips/" in c]


def _aeroplan_row():
    row = copy.deepcopy(REAL["data"][0])
    row["Route"].update(OriginAirport="LHR", DestinationAirport="SFO")
    row["Date"], row["ParsedDate"] = "2027-01-27", "2027-01-27T00:00:00Z"
    row["ID"] = "aeroplanB4AAAAAAAAAAAAAAAAA"
    return row


def test_a_paid_lookup_for_a_non_chosen_award_is_printed(rm):
    stub = RowsStub([_aeroplan_row(), vs_row()], trips_status={B4_ID: 404})
    b4, results, out = run(rm, stub)
    r4 = next(r for r in results if r.leg.id == "B4")
    assert r4.best_points.program == "Air Canada Aeroplan"
    assert [c for c in stub.calls if "/trips/" in c]
    assert "other live award Virgin Atlantic Flying Club J 60,000 points (not the chosen option)" in out
    assert "HTTP 404" in out


def test_a_single_carrier_known_metal_award_keeps_its_metal_line(rm):
    row = vs_row()
    row["Route"]["Source"] = "united"
    row["JAirlines"] = "UA"
    b4, results, out = run(rm, RowsStub([row]))
    cand = next(r for r in results if r.leg.id == "B4").best_points
    assert cand.has_known_metal and cand.operating_carrier == "UA"
    assert cand.metal.reason_code == "NOT_NEEDED_POLICY"
    assert "metal: UA (source: seats_aero), cabin J" in out
    assert "Nothing is known about which airline flies it; the possible carriers are UA" not in out
    assert "the award's own carrier list names one carrier, so the possible carriers are UA" in out
