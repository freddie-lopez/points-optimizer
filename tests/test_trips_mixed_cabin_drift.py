"""
Fix round 1, Tester finding 21: a mixed-cabin itinerary under min_cabin_pct=100
is drift (D16), and a capture holding one cannot flip the label.

The live matcher is unchanged: it still excludes the mixed itinerary from the
carrier answer.
"""
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.models import MetalStatus
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID
from tests.test_trips_tools import Stub, capture_args

NEEDLE = "did not honour min_cabin_pct=100"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _parse(trips):
    return seats_trips.parse_trips_payload(tp.payload(trips), B4_ID, ("LHR", "SFO"))


@pytest.mark.parametrize("pct", [1, 30, 100])
def test_a_nonzero_mixed_cabin_pct_is_required_drift(pct):
    parsed = _parse([tp.vs_direct(mixed=pct)])
    assert any(NEEDLE in d and f"MixedCabinPct is {pct}" in d for d in parsed.drift)
    assert any(NEEDLE in d for d in parsed.required_drift)


def test_zero_and_absent_are_not_required_drift():
    assert not any(NEEDLE in d for d in _parse([tp.vs_direct(mixed=0)]).drift)
    assert _parse([tp.vs_direct(mixed=0)]).required_drift == []
    assert _parse([tp.vs_direct()]).required_drift == []


def test_a_capture_with_a_mixed_itinerary_exits_5_and_cannot_flip(tmp_path):
    buf = StringIO()
    stub = Stub(trips=tp.payload([tp.vs_direct(), tp.vs_direct("VS21", trip_id="t2", mixed=30)]))
    with patch("src.seats_client.requests.get", side_effect=stub):
        code = trips_tools.main(
            capture_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=190)
        )
    out = " ".join(buf.getvalue().split())
    assert code == trips_tools.EXIT_DRIFT
    assert NEEDLE in out
    path = next((tmp_path / "real").glob("*.json"))
    assert any(NEEDLE in p for p in seats_trips.schema_verification_problems(path.name, tmp_path / "real"))


def test_the_matcher_still_excludes_the_mixed_itinerary():
    parsed = _parse([tp.vs_direct(), tp.vs_direct("DL41", trip_id="t2", mixed=30)])
    facts = seats_trips.AwardFacts(
        availability_id=B4_ID, source_code="virginatlantic", cabin="J", cost=60000,
        row_carriers=("VS", "DL"), origin="LHR", destination="SFO",
    )
    lookup = seats_trips.match_award(parsed, facts)
    assert lookup.status is MetalStatus.KNOWN
    assert lookup.carriers == ("VS",)
    assert lookup.excluded_mixed == 1
