"""
Per-itinerary TotalTaxes from the trips endpoint.

While `TRIPS_TOTALTAXES_UNIT` is "unverified" (the committed value), a trip's own
figure is DISPLAY ONLY: nothing it says can move a number. The flip to "cents" is
monkeypatched here to pin what happens after a real capture justifies it.
"""
import pytest

from src import seats_trips
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, Stub, _numbers, render
from tests.test_yq_inclusion import rm, scored, verdict  # noqa: F401 - fixture reuse

ROW_USD = 609.30  # the B4 row: GBP 450.00 at the configured GBP rate


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def nums(legs, totals):
    return _numbers(list(legs.values()), totals)


def _stub(*taxes, currency="GBP"):
    trips = [
        tp.vs_direct(f"VS{19 + 2 * i}", trip_id=f"t{i}", taxes=t, currency=currency)
        for i, t in enumerate(taxes)
    ]
    return Stub(trips_payloads={B4_ID: tp.payload(trips)})


def test_the_committed_unit_is_unverified():
    assert seats_trips.TRIPS_TOTALTAXES_UNIT == "unverified"


def test_unverified_taxes_are_shown_raw_with_both_readings(rm):
    legs, _ = scored(rm, verdict(which="includes_yq"), stub=_stub(4460, currency="CAD"))
    out = render(list(legs.values()))
    assert (
        "per-itinerary taxes: raw 4460 CAD (unit NOT VERIFIED: CAD 44.60 if cents, "
        "CAD 4,460 if whole units); not used in any figure" in out
    )


@pytest.mark.parametrize("taxes", [0, -1, 10 ** 9, None, "abc"])
@pytest.mark.parametrize("which", [None, "includes_yq", "excludes_yq"])
def test_while_unverified_no_trip_figure_moves_any_number(rm, taxes, which):
    table = verdict(which=which) if which else None
    base = nums(*scored(rm, table, stub=_stub(45000)))
    moved = nums(*scored(rm, table, stub=_stub(taxes)))
    assert base == moved


# ---------------------------------------------------------------------------
# After the flip to cents
# ---------------------------------------------------------------------------


@pytest.fixture
def cents(monkeypatch):
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "cents")


def _b4(legs):
    return legs["B4"]


def test_a_matching_figure_changes_nothing(rm, cents):
    base = nums(*scored(rm, verdict(which="includes_yq"), stub=_stub(45000)))
    within = nums(*scored(rm, verdict(which="includes_yq"), stub=_stub(45000, 45040)))
    assert base == within
    legs, _ = scored(rm, verdict(which="includes_yq"), stub=_stub(45000))
    assert _b4(legs).points_total_score_usd != float("inf")


def test_a_higher_figure_makes_the_award_taxes_unknown_and_widens(rm, cents):
    legs, totals = scored(rm, verdict(which="includes_yq"), stub=_stub(45000, 60000))
    b4 = _b4(legs)
    assert b4.points_total_score_usd == float("inf")
    assert "TAXES_UNKNOWN" in [x.code for x in b4.reasons]
    assert "B4" in totals["legs_taxes_unknown_ids"]
    note = b4.best_points.observed_taxes_note
    assert "which itinerary you book decides the taxes" in note
    assert f"between ${ROW_USD:,.2f} and $812.40" in note
    assert b4.points_floor_usd is not None
    assert b4.winner_cost_low_usd <= b4.winner_cost_high_usd


@pytest.mark.parametrize(
    "taxes,currency,why",
    [
        (0, "GBP", "not reported"),
        (-1, "GBP", "negative"),
        (45000, "XXX", "No FX rate"),
        (100, "GBP", "below the UK duty"),
        (None, "GBP", "not an integer"),
    ],
)
def test_the_trust_rules_apply_to_each_itinerary(rm, cents, taxes, currency, why):
    stub = Stub(trips_payloads={B4_ID: tp.payload([
        tp.vs_direct("VS19", trip_id="t0", taxes=45000),
        tp.vs_direct("VS21", trip_id="t1", taxes=taxes, currency=currency),
    ])})
    legs, _ = scored(rm, verdict(which="includes_yq"), stub=stub)
    b4 = _b4(legs)
    assert b4.points_total_score_usd == float("inf")
    assert "TAXES_UNKNOWN" in [x.code for x in b4.reasons]
    assert why in b4.best_points.observed_taxes_note


def test_a_lower_figure_is_disclosed_only(rm, cents):
    base = nums(*scored(rm, verdict(which="includes_yq"), stub=_stub(45000)))
    legs, totals = scored(rm, verdict(which="includes_yq"), stub=_stub(45000, 30000))
    assert nums(legs, totals) == base
    note = _b4(legs).best_points.metal.trip_taxes_note
    assert "shows lower taxes ($406.20)" in note
    assert "the row figure is used" in note


def test_untrusted_row_taxes_are_not_rescued_by_a_trip_figure(rm, cents):
    """The row's own 0 is already unknown; a trip figure never makes it known."""
    from tests.test_yq_inclusion import TaxStub

    stub = TaxStub(0)
    stub.trips_payloads[B4_ID] = tp.payload([tp.vs_direct(taxes=45000)])
    legs, _ = scored(rm, verdict(which="includes_yq"), stub=stub)
    assert _b4(legs).points_total_score_usd == float("inf")
