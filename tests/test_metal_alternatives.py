"""
Same-metal alternatives from the itinerary lookup's metal.

A Virgin Atlantic award whose flight numbers name AF points at Flying Blue, which
can ticket AF metal: UNPRICED, marked as an assumed partnership, and never in a
total. AMBIGUOUS, UNKNOWN and not-looked-up metal point at nothing.
"""
from io import StringIO

import pytest
from rich.console import Console

from src.formatter import print_alternatives
from src.models import MetalLookup, MetalStatus, PointsCandidate
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, Stub, _numbers, vs_row
from tests.test_yq_inclusion import rm, scored  # noqa: F401 - fixture reuse


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


class AFStub(Stub):
    """B4's Virgin Atlantic row lists VS and AF; its itineraries are as given."""

    def __init__(self, trips):
        super().__init__(trips_payloads={B4_ID: tp.payload(trips)})

    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            row = vs_row()
            row["JAirlines"] = "VS, AF"
            r.json.return_value = {"data": [row]}
        return r


def _af():
    return tp.trip([tp.segment("AF1181", "LHR", "SFO", 1)])


def _vs(trip_id="t2"):
    return tp.trip([tp.segment("VS19", "LHR", "SFO", 1)], trip_id=trip_id)


def nums(legs, totals):
    return _numbers(list(legs.values()), totals)


def test_known_af_metal_on_a_vs_award_gives_an_unpriced_flying_blue_alternative(rm):
    legs, _ = scored(rm, None, stub=AFStub([_af()]))
    b4 = legs["B4"]
    assert b4.best_points.metal.status is MetalStatus.KNOWN
    assert b4.best_points.metal.carriers == ("AF",)
    alts = {a.program: a for a in b4.alternatives}
    assert "Air France-KLM Flying Blue" in alts
    fb = alts["Air France-KLM Flying Blue"]
    assert fb.operating_carrier == "AF"
    assert fb.points_price is None and not fb.is_priced
    assert fb.partnership_assumed is True
    assert (fb.surcharge.amount_low, fb.surcharge.amount_high) == (75.0, 125.0)
    assert "Award price NOT known" in fb.note
    reasons = [x for x in b4.reasons if x.code == "ALTERNATIVE_UNPRICED"]
    assert reasons and reasons[0].data["carrier"] == "AF"
    buf = StringIO()
    print_alternatives(list(legs.values()), Console(file=buf, width=190))
    assert "Air France-KLM Flying Blue" in buf.getvalue()


@pytest.mark.parametrize(
    "trips,status",
    [
        ([_af(), _vs()], MetalStatus.AMBIGUOUS),
        ([], MetalStatus.UNKNOWN),
    ],
    ids=["ambiguous", "unknown"],
)
def test_no_alternative_from_metal_that_is_not_known(rm, trips, status):
    legs, _ = scored(rm, None, stub=AFStub(trips))
    b4 = legs["B4"]
    assert b4.best_points.metal.status is status
    assert b4.alternatives == []


def test_no_alternative_when_not_looked_up(rm):
    legs, _ = scored(rm, None, trips_mode="off", stub=AFStub([_af()]))
    b4 = legs["B4"]
    assert b4.best_points.metal.status is MetalStatus.NOT_LOOKED_UP
    assert b4.alternatives == []


def test_the_totals_are_unchanged_by_an_alternative(rm):
    with_alt = nums(*scored(rm, None, stub=AFStub([_af()])))
    without = nums(*scored(rm, None, trips_mode="off", stub=AFStub([_af()])))
    assert with_alt == without


def test_metal_for_alternatives_is_one_carrier_or_nothing():
    base = dict(label="x", program="p", points=1)
    known = MetalLookup(
        status=MetalStatus.KNOWN, carriers=("AF",), matched_trips=1, provenance="seats_aero_trips"
    )
    two = MetalLookup(
        status=MetalStatus.KNOWN, carriers=("AF", "KL"), matched_trips=1,
        provenance="seats_aero_trips",
    )
    assert PointsCandidate(**base, metal=known).metal_for_alternatives == "AF"
    assert PointsCandidate(**base, metal=two).metal_for_alternatives == ""
    assert PointsCandidate(**base).metal_for_alternatives == ""
    assert PointsCandidate(
        **base, operating_carrier="ba", carrier_source="captured"
    ).metal_for_alternatives == "BA"
    assert PointsCandidate(
        **base, operating_carrier="BA", carrier_source="assumed"
    ).metal_for_alternatives == ""
