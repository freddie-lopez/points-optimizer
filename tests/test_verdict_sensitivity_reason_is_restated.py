"""
FINDING R2-2: the VERDICT SENSITIVE sentence quotes figures, so it is rebuilt
when the FIGURES move - not only when the flag does.

H-1's fix re-decided the flag after `apply_apd` moved both ends of the band,
but returned early when the flag came out the same. A band WIDER than the duty
straddles the fare before and after it, so the flag does not change - and the
sentence was left quoting the pre-APD band, both ends low by the duty, with the
duty not named. The "defensible low end" then reads better than the one
actually scored: the F-3 shape one branch along.

The leg below is SYNTHETIC and built here rather than committed: BA Executive
Club on IB metal out of LHR in business, whose surcharge the committed table
models as a band wider than the UK duty.
"""
from datetime import date
from pathlib import Path

import pytest

from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_trip, set_verdict_sensitivity
from src.ratio_manager import RatioManager
from src.surcharge import default_table
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
CSP = "Chase Sapphire Preferred"


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


def leg(cash_usd, program="British Airways Executive Club", metal="IB", points=50000):
    return Leg(
        id="P1",
        kind="flight",
        description=f"SYNTHETIC LHR->JFK business on {metal} metal",
        date=date(2027, 1, 27),
        origin="LHR",
        destination="JFK",
        cabin="J",
        cash_options=[CashOption(label=f"SYNTHETIC ${cash_usd} fare", amount=float(cash_usd))],
        points_candidates=[PointsCandidate(
            label=f"SYNTHETIC {program} award", program=program, points=points,
            source="manual_capture", operating_carrier=metal, marketing_carrier=metal,
            carrier_source="captured", cabin="J")],
    )


def score(rm, one_leg):
    return evaluate_trip([one_leg], ratios_manager=rm,
                         wallet=Wallet(balances={"UR": 160000}, cards=[CSP]),
                         transfer_date=date(2026, 9, 15), surcharges=default_table(),
                         show_alternatives=False)[0]


def reason(r):
    return next((x.detail for x in r.reasons if x.code == "VERDICT_SENSITIVE"), "")


def test_a_band_that_straddles_before_and_after_the_duty_quotes_the_scored_band(rm):
    r = score(rm, leg(1450))
    assert r.apd_added_usd > 0, "the probe needs a UK departure that pays the duty"
    assert r.points_score_low_usd < r.cash_total_score_usd < r.points_score_high_usd, \
        "the band no longer straddles the fare at both ends; rebuild the fixture"
    assert r.verdict_sensitive is True
    detail = reason(r)
    assert f"${r.points_score_low_usd:,.2f}" in detail
    assert f"${r.points_score_high_usd:,.2f}" in detail
    assert f"UK Air Passenger Duty of ${r.apd_added_usd:,.2f}" in detail


def test_the_sentence_never_quotes_the_band_as_it_stood_before_the_duty(rm):
    r = score(rm, leg(1450))
    apd = r.apd_added_usd
    detail = reason(r)
    for pre in (r.points_score_low_usd - apd, r.points_score_high_usd - apd):
        assert f"${pre:,.2f}" not in detail, f"{pre:,.2f} is a pre-APD figure: {detail}"


def test_exactly_one_marker_survives_any_number_of_re_decisions(rm):
    r = score(rm, leg(1450))
    for _ in range(5):
        set_verdict_sensitivity(r, apd_usd=r.apd_added_usd)
    assert len([x for x in r.reasons if x.code == "VERDICT_SENSITIVE"]) == 1
    assert len([w for w in r.warnings if w.startswith("VERDICT SENSITIVE:")]) == 1
    assert f"${r.points_score_low_usd:,.2f}" in reason(r)


def test_the_cli_and_the_ui_show_the_same_restated_figures(rm):
    from src.formatter import leg_table_cells
    from src.ui.serialize import leg_json

    r = score(rm, leg(1450))
    cells = leg_table_cells(r)
    assert cells.verdict.text.endswith(" !SENSITIVE")
    payload = leg_json(r)
    assert payload["numbers"]["score_points"] is None
    assert payload["numbers"]["score_low"] == pytest.approx(r.points_score_low_usd)
    assert payload["numbers"]["score_high"] == pytest.approx(r.points_score_high_usd)
    # The drawer renders reason codes verbatim, so the restated sentence is what
    # reaches the page.
    sent = next(x["detail"] for x in payload["reasons"] if x["code"] == "VERDICT_SENSITIVE")
    assert sent == reason(r)
    assert f"${r.points_score_low_usd:,.2f}" in sent
    assert f"UK Air Passenger Duty of ${r.apd_added_usd:,.2f}" in sent


def test_a_leg_that_never_flips_carries_no_marker_at_all(rm):
    r = score(rm, leg(800))
    assert r.verdict_sensitive is False
    assert reason(r) == ""
    assert [w for w in r.warnings if w.startswith("VERDICT SENSITIVE:")] == []
