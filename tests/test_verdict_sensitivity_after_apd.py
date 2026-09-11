"""
FINDING H-1: VERDICT SENSITIVE is decided on the score that was actually
verdicted on, UK Air Passenger Duty included.

`evaluate_leg` used to decide it once, before `apply_apd` moved BOTH ends of
the surcharge band by the duty. A UK departure whose points side wins at the
band's low end and loses at its high end ONCE THE DUTY IS IN was therefore
reported as a settled POINTS verdict - the F-3 shape one field along: a flag
decided on figures that are not the ones scored, and this round paints a green
chip on it.

The leg below is SYNTHETIC (a VS-metal LHR departure in business, whose
surcharge the committed table models as a band) and is built here rather than
committed, so no fixture on disk claims these numbers.
"""
from datetime import date
from pathlib import Path

import pytest

from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_trip, set_verdict_sensitivity, trip_totals
from src.ratio_manager import RatioManager
from src.surcharge import default_table
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
CSP = "Chase Sapphire Preferred"
TRANSFER = date(2026, 9, 15)


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


def leg(origin, destination, cash_usd, points=50000):
    return Leg(
        id="P1",
        kind="flight",
        description=f"SYNTHETIC {origin}->{destination} business on VS metal",
        date=date(2027, 1, 27),
        origin=origin,
        destination=destination,
        cabin="J",
        cash_options=[CashOption(label=f"SYNTHETIC ${cash_usd} fare", amount=float(cash_usd))],
        points_candidates=[PointsCandidate(
            label="SYNTHETIC VS award", program="Virgin Atlantic Flying Club",
            points=points, source="manual_capture", operating_carrier="VS",
            marketing_carrier="VS", carrier_source="captured", cabin="J",
        )],
    )


def score(rm, one_leg):
    results = evaluate_trip([one_leg], ratios_manager=rm,
                            wallet=Wallet(balances={"UR": 160000}, cards=[CSP]),
                            transfer_date=TRANSFER, surcharges=default_table(),
                            show_alternatives=False)
    return results[0], trip_totals(results)


def test_a_band_that_straddles_the_fare_only_after_apd_is_sensitive(rm):
    r, totals = score(rm, leg("LHR", "JFK", 1150))
    assert r.apd_added_usd > 0, "the probe needs a UK departure that pays the duty"
    assert r.points_score_low_usd < r.cash_total_score_usd < r.points_score_high_usd
    assert r.verdict_sensitive is True
    codes = [x.code for x in r.reasons if x.code == "VERDICT_SENSITIVE"]
    assert codes == ["VERDICT_SENSITIVE"]
    detail = next(x.detail for x in r.reasons if x.code == "VERDICT_SENSITIVE")
    # The figures it quotes are the ones scored, and the duty in them is named.
    assert f"${r.points_score_low_usd:,.2f}" in detail
    assert f"${r.points_score_high_usd:,.2f}" in detail
    assert f"UK Air Passenger Duty of ${r.apd_added_usd:,.2f}" in detail
    assert any(w.startswith("VERDICT SENSITIVE:") for w in r.warnings)
    assert totals["legs_verdict_sensitive_ids"] == ["P1"]


def test_the_same_band_without_a_uk_departure_is_decided_exactly_as_before(rm):
    r, _ = score(rm, leg("JFK", "LHR", 780))
    assert r.apd_added_usd == 0.0
    assert r.points_score_low_usd < r.cash_total_score_usd < r.points_score_high_usd
    assert r.verdict_sensitive is True
    detail = next(x.detail for x in r.reasons if x.code == "VERDICT_SENSITIVE")
    assert "UK Air Passenger Duty" not in detail


def test_a_flip_the_duty_removes_is_not_left_claiming_one(rm):
    """Both ends lose once the duty is in: settled, and the stale marker goes."""
    r, totals = score(rm, leg("LHR", "JFK", 790))
    assert r.apd_added_usd > 0
    assert r.cash_total_score_usd < r.points_score_low_usd
    assert r.verdict_sensitive is False
    assert not [x for x in r.reasons if x.code == "VERDICT_SENSITIVE"]
    assert not [w for w in r.warnings if w.startswith("VERDICT SENSITIVE:")]
    assert totals["legs_verdict_sensitive_ids"] == []


def test_the_marker_is_idempotent_and_keeps_its_place_when_nothing_changed(rm):
    r, _ = score(rm, leg("LHR", "JFK", 1150))
    before = (list(r.warnings), [x.detail for x in r.reasons], r.verdict_sensitive)
    set_verdict_sensitivity(r, apd_usd=r.apd_added_usd)
    set_verdict_sensitivity(r)
    assert (list(r.warnings), [x.detail for x in r.reasons], r.verdict_sensitive) == before


def test_the_ui_paints_the_sensitive_tag_on_that_leg(rm):
    from src.formatter import leg_table_cells, verdict_kind
    from src.ui.serialize import leg_json

    r, _ = score(rm, leg("LHR", "JFK", 1150))
    cells = leg_table_cells(r)
    assert cells.verdict.text.endswith(" !SENSITIVE")
    assert cells.score_points.kind == "range"
    payload = leg_json(r)
    assert payload["verdict"]["sensitive"] is True
    assert payload["numbers"]["score_points"] is None
    assert payload["numbers"]["score_low"] == pytest.approx(r.points_score_low_usd)
    assert payload["numbers"]["score_high"] == pytest.approx(r.points_score_high_usd)
    assert verdict_kind(r) in ("points", "cash", "cash_qualified")
