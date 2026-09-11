"""
--trips, --trips-cap, the shared budget, HTTP 429, the conflicts and the banner.

CLI runs are in-process through `main()` with `requests.get` patched by a
URL-routing stub, a fake key, and the harness's tmp cache and snapshot dirs.
"""
import copy
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from tests import _trips_payloads as tp
from tests.test_from_snapshot import REPLAY_BASE, build_corpus, run_cli

ROOT = Path(__file__).parent.parent
REAL = json.loads((ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text())
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
BASE = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", "Chase Sapphire Preferred",
    "--transfer-date", "2026-09-15",
    "--allow-badge-fallback",
]

IDS = {
    "B2": "B2flyingblueAAAAAAAAAAAAAA2",
    "B3": "B3flyingblueAAAAAAAAAAAAAA3",
    "B4": "B4virginatlanticAAAAAAAAAA4",
}
ROUTES = {
    ("SFO", "MAD"): ("B1", "2027-01-15"),
    ("MAD", "AMS"): ("B2", "2027-01-19"),
    ("AMS", "LHR"): ("B3", "2027-01-23"),
    ("LHR", "SFO"): ("B4", "2027-01-27"),
}


def _row(leg, origin, destination, day):
    row = copy.deepcopy(REAL["data"][0])
    row["Route"].update(OriginAirport=origin, DestinationAirport=destination)
    row["Date"], row["ParsedDate"] = day, f"{day}T00:00:00Z"
    row["ID"] = IDS.get(leg, f"aeroplan{leg}AAAAAAAAAAAAAAAAA")
    if leg in ("B2", "B3"):
        row["Route"].update(Source="flyingblue", OriginRegion="Europe", DestinationRegion="Europe")
        row.update(YAvailable=False, JAvailable=True, JMileageCost="30000",
                   JTotalTaxes=9000, TaxesCurrency="EUR", JAirlines="AF, KL",
                   JRemainingSeats=2)
    if leg == "B4":
        row["Route"].update(Source="virginatlantic", OriginRegion="Europe",
                            DestinationRegion="North America")
        row.update(YAvailable=False, JAvailable=True, JMileageCost="60000",
                   JTotalTaxes=45000, TaxesCurrency="GBP", JAirlines="VS, DL",
                   JRemainingSeats=2)
    return row


def _itinerary(leg):
    origin, destination = next(k for k, v in ROUTES.items() if v[0] == leg)
    if leg == "B4":
        return tp.payload([tp.trip(
            [tp.segment("VS19", origin, destination, 1)],
            availability_id=IDS[leg], cost=60000,
        )])
    return tp.payload([tp.trip(
        [tp.segment("KL1704", origin, destination, 1)],
        availability_id=IDS[leg], source="flyingblue", cost=30000,
    )])


class Stub:
    def __init__(self, trips_status=None, on_last_search=None):
        self.calls = []
        self.trips_status = trips_status or {}
        self.on_last_search = on_last_search

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.status_code = 200
        if url.endswith("/search"):
            params = kwargs.get("params") or {}
            key = (params.get("origin_airport"), params.get("destination_airport"))
            leg, day = ROUTES[key]
            body = {"data": [_row(leg, key[0], key[1], day)]}
            if leg == "B4" and self.on_last_search:
                self.on_last_search()
        else:
            aid = url.rsplit("/", 1)[-1]
            leg = next(k for k, v in IDS.items() if v == aid)
            r.status_code = self.trips_status.get(leg, 200)
            body = _itinerary(leg)
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    @property
    def trips_calls(self):
        return [c for c in self.calls if "/trips/" in c]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_trips_flags_0001")
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def cli(capsys, *extra, stub=None):
    stub = stub or Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        code, out = run_cli(BASE + list(extra), capsys)
    return code, " ".join(out.split()), stub


def live(stub, **kw):
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, **kw)
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    return {l.id: l.points_candidates[0].metal for l in fixture.legs if l.points_candidates}, opts


# ---------------------------------------------------------------------------
# Modes and the cap
# ---------------------------------------------------------------------------


def test_the_default_is_auto_and_looks_up_the_three_qualifying_awards(capsys):
    code, out, stub = cli(capsys)
    assert len(stub.trips_calls) == 3
    assert "itinerary lookups (--trips auto, cap 10)" in out
    assert "3 request(s) sent" in out


def test_trips_off_makes_no_trips_call_and_says_not_looked_up(capsys):
    code, out, stub = cli(capsys, "--trips", "off")
    assert stub.trips_calls == []
    assert "NOT LOOKED UP (--trips off)" in out


def test_a_cap_of_one_sends_exactly_one_and_names_the_cap_on_the_rest(capsys):
    code, out, stub = cli(capsys, "--trips-cap", "1")
    assert len(stub.trips_calls) == 1
    assert out.count("NOT LOOKED UP - the per-run cap of 1 lookups was reached") >= 2


def test_the_cap_counts_requests_not_lookups():
    stub = Stub()
    metal, opts = live(stub, trips_mode="auto", trips_cap=2)
    assert len(stub.trips_calls) == 2
    assert metal["B4"].reason_code == "CAP_REACHED"
    assert opts.metal_report.requests_sent == 2


def test_trips_all_looks_up_aeroplan_too():
    stub = Stub()
    metal, _ = live(stub, trips_mode="all")
    assert metal["B1"].status is not MetalStatus.NOT_LOOKED_UP
    assert len(stub.trips_calls) == 4


def test_auto_leaves_aeroplan_as_not_needed():
    metal, _ = live(Stub(), trips_mode="auto")
    assert metal["B1"].reason_code == "NOT_NEEDED_POLICY"
    assert metal["B4"].status is MetalStatus.KNOWN
    assert metal["B2"].status is MetalStatus.KNOWN


# ---------------------------------------------------------------------------
# The budget and 429
# ---------------------------------------------------------------------------


def test_a_budget_spent_after_the_searches_sends_nothing_and_says_so():
    def spend():
        # B4 is the last search, and it has already been counted: 0 left after it.
        SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP

    stub = Stub(on_last_search=spend)
    metal, _ = live(stub, trips_mode="auto")
    assert stub.trips_calls == []
    for leg in ("B2", "B3", "B4"):
        assert metal[leg].status is MetalStatus.NOT_LOOKED_UP
        assert metal[leg].reason_code == "BUDGET_EXHAUSTED"
        assert "no request was made" in metal[leg].render()


def test_a_429_on_the_first_lookup_stops_every_later_one():
    stub = Stub(trips_status={"B2": 429})
    metal, opts = live(stub, trips_mode="auto")
    assert len(stub.trips_calls) == 1
    assert metal["B2"].status is MetalStatus.UNKNOWN
    assert metal["B2"].reason_code == "HTTP_429"
    for leg in ("B3", "B4"):
        assert metal[leg].reason_code == "RATE_LIMITED_EARLIER"
        assert "NOT LOOKED UP (Seats.aero rate-limited an earlier request in this run" in metal[leg].render()
    assert opts.metal_report.rate_limited


def test_the_banner_names_the_429(capsys):
    _, out, _ = cli(capsys, stub=Stub(trips_status={"B2": 429}))
    assert "Seats.aero rate-limited a request in this run (HTTP 429)" in out


# ---------------------------------------------------------------------------
# Conflicts: exit 1 with a named reason, and nothing scored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra,needle",
    [
        (["--offline", "--trips", "auto"], "cannot be combined with --offline"),
        (["--offline", "--trips-cap", "5"], "cannot be combined with --offline"),
        (["--trips-cap", "0"], "not a whole number from 1 to 50"),
        (["--trips-cap", "51"], "not a whole number from 1 to 50"),
        (["--trips-cap", "ten"], "not a whole number from 1 to 50"),
        (["--trips-cap", "-3"], "not a whole number from 1 to 50"),
        (["--trips", "some"], "is not one of auto, all, off"),
    ],
)
def test_a_flag_that_cannot_be_honoured_exits_1_and_names_why(capsys, extra, needle):
    code, out, stub = cli(capsys, *extra)
    assert code == 1
    assert needle in out
    assert stub.calls == []
    assert not re.search(r"\d+\.\d\d%", out)


@pytest.mark.parametrize("extra", [["--trips", "auto"], ["--trips-cap", "3"]])
def test_from_snapshot_refuses_the_trips_flags(tmp_path, capsys, extra):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)] + extra, capsys)
    assert code == 1
    assert "cannot be combined with --from-snapshot" in " ".join(out.split())


def test_a_single_route_search_refuses_trips(capsys):
    code, out = run_cli(
        ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
         "--balance", "UR=160000", "--trips", "auto"],
        capsys,
    )
    assert code == 1
    assert "cannot be combined with a single-route search" in " ".join(out.split())


def test_a_single_route_search_prints_the_not_looked_up_footer(capsys):
    stub = Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        code, out = run_cli(
            ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15:2027-01-15",
             "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"],
            capsys,
        )
    flat = " ".join(out.split())
    assert (
        "operating airline: NOT LOOKED UP - single-route search does not call the "
        "trips endpoint; use --trip-fixture or `python -m src.trips_tools capture`"
    ) in flat
    assert stub.trips_calls == []


def test_the_default_trips_is_resolved_later_not_by_argparse():
    from src.main import build_parser

    args = build_parser().parse_args(BASE)
    assert args.trips is None and args.trips_cap is None


# ---------------------------------------------------------------------------
# The banner
# ---------------------------------------------------------------------------


def test_the_banner_splits_the_calls_and_carries_the_parser_label(capsys):
    _, out, _ = cli(capsys)
    assert "Seats.aero calls spent this run: 4 search + 3 trips" in out
    assert "[trips parser UNVERIFIED against a real Seats.aero response" in out
    assert "operating airline known 3" in out
    assert "NOT LOOKED UP 1" in out


def test_a_second_run_is_served_from_the_disk_cache(capsys):
    cli(capsys)
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    _, out, stub = cli(capsys)
    assert stub.trips_calls == []
    assert "0 request(s) sent, 3 served from the disk cache" in out


def test_the_exit_code_is_the_same_with_the_lookup_on_or_off(capsys):
    on, _, _ = cli(capsys)
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    off, _, _ = cli(capsys, "--trips", "off", "--refresh")
    assert on == off
