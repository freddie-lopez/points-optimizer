"""
Manager review, must-fix 1: flipping the label is a state the suite supports.

A genuine capture is written to tmp by the real (stubbed) capture tool. Both
constants are then patched to what a flip would set -
`TRIPS_SCHEMA_VERIFIED_BY` to that file, `TRIPS_TOTALTAXES_UNIT` to "cents" -
and every module that reads them is run: the flip checks themselves, the
matcher's render, the banner, a full live Trip B run with its printed output,
the per-itinerary tax rule, and the yq-check block.

The committed tree is never touched: the capture lives in tmp, and the
constants are monkeypatched. (The whole suite was also run with the constants
edited in source and a stubbed capture in real/; see the fix report.)
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.formatter import (
    print_alternatives,
    print_leg_detail,
    print_live_banner,
    print_live_leg_detail,
)
from src.live_trip import LiveOptions, MetalPassReport
from src.models import MetalStatus
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, Stub as TripStub
from tests.test_trips_tools import Stub, capture_args, yq_args
from tests.test_yq_inclusion import rm, scored, verdict  # noqa: F401 - fixture reuse

LABEL_WORDS = "trips parser UNVERIFIED"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


@pytest.fixture
def flipped(tmp_path, monkeypatch):
    """A genuine tmp capture, and both constants set the way a flip sets them."""
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code = trips_tools.main(
            capture_args(tmp_path, "--yes"), read=lambda _: "y",
            console=Console(file=buf, width=300),
        )
    out = " ".join(buf.getvalue().split())
    assert code == 0, out
    real = tmp_path / "real"
    name = next(real.glob("*.json")).name
    assert f"set TRIPS_SCHEMA_VERIFIED_BY = {name!r}" in out
    assert seats_trips.schema_verification_problems(name, real) == []
    assert seats_trips.totaltaxes_unit_problems("cents", real) == []
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", name)
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "cents")
    SeatsClient.reset_call_budget()
    return real / name


def _printed(legs):
    buf = StringIO()
    console = Console(file=buf, width=5000)
    results = list(legs.values())
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    print_alternatives(results, console)
    return buf.getvalue()


def test_the_flip_drops_the_label_from_the_matcher_and_the_banner(flipped):
    assert seats_trips.parser_is_verified()
    assert seats_trips.trips_parser_label() == ""
    parsed, envelope = seats_trips.capture_parse(flipped)
    facts = seats_trips.AwardFacts(
        availability_id=envelope["_meta"]["availability_id"], source_code="virginatlantic",
        cabin="J", cost=60000, row_carriers=("VS", "DL"), origin="LHR", destination="SFO",
    )
    lookup = seats_trips.match_award(parsed, facts)
    assert lookup.status is MetalStatus.KNOWN
    assert LABEL_WORDS not in lookup.render()
    buf = StringIO()
    opts = LiveOptions(live=True, trips_mode="auto")
    opts.metal_report = MetalPassReport(mode="auto", cap=10)
    print_live_banner([], opts, None, Console(file=buf, width=300))
    assert LABEL_WORDS not in buf.getvalue()


def test_a_live_run_prints_no_label_and_still_moves_no_number_without_a_verdict(rm, flipped):
    legs, _ = scored(rm, None)
    b4 = legs["B4"]
    assert b4.best_points.metal.status is MetalStatus.KNOWN
    assert b4.best_points.metal.parser_verified
    text = _printed(legs)
    assert "operating airline: VS by flight number" in text
    assert LABEL_WORDS not in text
    # No verdict: the band is still NOT ADDED and B4 is still unscoreable.
    assert "NOT ADDED" in b4.best_points.metal_surcharge_note
    assert b4.points_total_score_usd == float("inf")


def test_cents_lets_a_higher_itinerary_figure_make_the_taxes_unknown(rm, flipped):
    stub = TripStub(trips_payloads={B4_ID: tp.payload([
        tp.vs_direct("VS19", trip_id="t0", taxes=45000),
        tp.vs_direct("VS21", trip_id="t1", taxes=60000),
    ])})
    legs, totals = scored(rm, verdict(which="includes_yq"), stub=stub)
    b4 = legs["B4"]
    assert "TAXES_UNKNOWN" in [x.code for x in b4.reasons]
    assert "B4" in totals["legs_taxes_unknown_ids"]
    assert "unit NOT VERIFIED" not in b4.best_points.observed_taxes_note


def test_the_yq_check_block_carries_no_label_once_flipped(tmp_path, flipped):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code = trips_tools.main(
            yq_args(tmp_path / "second"), read=lambda _: "y",
            console=Console(file=buf, width=300), today=date(2026, 9, 11),
        )
    out = " ".join(buf.getvalue().split())
    assert code == 0, out
    block = out.split("YQ CHECK - compare against the program's own site", 1)[1]
    assert "flight-number carrier: VS" in block
    assert LABEL_WORDS not in block
