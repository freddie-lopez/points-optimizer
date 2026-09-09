"""
Step 12 (FX honesty) and Step 13 (the flagship acceptance case).

Step 13's whole purpose is one sentence: v0 scored the LON->MRY British Airways
leg at a $0 surcharge and called it a $282 saving. v1 must not.
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from src import config
from src.optimizer import evaluate_leg, evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
TRIPS = Path(__file__).parent / "fixtures" / "trips"
CSP = "Chase Sapphire Preferred"
D = date(2026, 9, 15)


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def restore_fx():
    """--fx mutates process-wide config; put it back."""
    rates = dict(config.FX_RATES_TO_USD)
    tiers = dict(config.FX_PROVENANCE_TIER)
    prov = dict(config.FX_RATES_PROVENANCE)
    # v3: set_fx_rate drops the source date, so it must be restored too or a
    # --fx test silently un-dates a rate for every test that runs after it.
    sourced = dict(config.FX_SOURCED_ON)
    yield
    config.FX_RATES_TO_USD.clear(); config.FX_RATES_TO_USD.update(rates)
    config.FX_PROVENANCE_TIER.clear(); config.FX_PROVENANCE_TIER.update(tiers)
    config.FX_RATES_PROVENANCE.clear(); config.FX_RATES_PROVENANCE.update(prov)
    config.FX_SOURCED_ON.clear(); config.FX_SOURCED_ON.update(sourced)


# ---------------------------------------------------------------------------
# Step 12: FX honesty
# ---------------------------------------------------------------------------


def test_the_provenance_tiers_are_distinguishable():
    """
    CHANGED AT v3 STEP 0 (was test_gbp_is_a_placeholder_and_usd_and_eur_are_not).

    GBP is no longer a placeholder: 1.2700-with-no-source became 1.3540 sourced
    on 2026-09-08. The test's real subject - that provenance tiers are
    machine-distinguishable and that the tool never flattens them into
    "verified"/"not verified" - is preserved and extended. GBP is now in the
    middle tier: not a placeholder, still needing confirmation.
    """
    assert config.is_placeholder_rate("GBP") is False
    assert config.FX_PROVENANCE_TIER["GBP"] == "sourced"
    assert config.FX_SOURCED_ON["GBP"] == date(2026, 9, 8)
    assert config.needs_confirmation("GBP") is True

    assert config.is_placeholder_rate("CAD") is False
    assert config.FX_PROVENANCE_TIER["CAD"] == "sourced"
    assert config.needs_confirmation("CAD") is True

    # EUR keeps its own tier. It is back-derived from one booking page, which is
    # not the same thing as a looked-up rate, and the string must keep saying so.
    assert config.is_placeholder_rate("EUR") is False
    assert config.FX_PROVENANCE_TIER["EUR"] == "booking_site_implied"
    assert config.needs_confirmation("EUR") is True
    assert "BOOKING-SITE IMPLIED" in config.FX_RATES_PROVENANCE["EUR"]
    assert "NOT a market rate" in config.FX_RATES_PROVENANCE["EUR"]

    # The base currency is the only thing nobody has to confirm.
    assert config.is_placeholder_rate("USD") is False
    assert config.needs_confirmation("USD") is False


def test_an_unconfigured_currency_is_treated_as_a_placeholder():
    assert config.is_placeholder_rate("JPY") is True


def test_the_sourced_rates_are_the_values_step_0_specified():
    """The two corrections, as numbers, so a silent edit to either is caught."""
    assert config.convert_to_usd(44.60, "CAD") == pytest.approx(32.36, abs=0.01)
    assert config.convert_to_usd(502.64, "GBP") == pytest.approx(680.57, abs=0.01)
    # The size of the GBP correction, stated once, in a test.
    assert config.convert_to_usd(502.64, "GBP") - 502.64 * 1.2700 == pytest.approx(
        42.22, abs=0.01
    )


def test_the_marker_appears_in_the_fx_banner():
    """
    CHANGED AT v3 STEP 0 (plan section 8, item 3 - the --fx example string).

    There is no placeholder rate left in the default table, so UNVERIFIED RATE
    no longer appears. What must survive is the thing that test was protecting:
    a foreign rate is never printed as a clean number. CONFIRM BEFORE TRUSTING
    replaces it, and the source date and age are now printed too.
    """
    lines = " ".join(config.fx_report_lines(today=date(2026, 9, 8)))
    assert "CONFIRM BEFORE TRUSTING" in lines
    assert "--fx GBP=1.36" in lines
    assert "sourced 2026-09-08" in lines
    assert "0 days old" in lines
    assert "UNVERIFIED RATE" not in lines, "nothing in the default table is unsourced"


def test_a_sourced_rate_goes_stale_and_says_so(rm):
    """
    A rate that was looked up on a date is not a placeholder, but it does rot.
    Forty days on, GBP is stale and every total that used it is marked.
    """
    forty_days_on = date(2026, 10, 18)
    assert config.fx_rate_age_days("GBP", forty_days_on) == 40
    assert config.is_stale_rate("GBP", forty_days_on) is True
    assert config.is_stale_rate("GBP", date(2026, 9, 8)) is False

    lines = " ".join(config.fx_report_lines(today=forty_days_on))
    assert "STALE RATE" in lines
    assert "GBP" in lines

    # ...and on the total that depends on it. B7 is the GBP leg.
    from io import StringIO

    from rich.console import Console as RichConsole

    from src.formatter import print_trip_totals

    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=[CSP]),
        transfer_date=D, today=date(2026, 9, 8),
    )
    buf = StringIO()
    print_trip_totals(
        trip_totals(results), console=RichConsole(file=buf, width=170),
        today=forty_days_on,
    )
    out = buf.getvalue()
    assert "STALE RATE" in out
    assert "GBP" in out


def test_the_gbp_leg_no_longer_rests_on_a_placeholder_but_still_needs_confirming(rm):
    """
    CHANGED AT v3 STEP 0 (was
    test_a_gbp_leg_is_marked_as_resting_on_an_unverified_rate).

    NOT LISTED IN THE PLAN'S SECTION 8. It is nevertheless a direct, mechanical
    consequence of the GBP tier change and not a v3 bug: `rests_on_placeholder_fx`
    is set by `evaluate_leg` from `is_placeholder_rate`, which is now False for
    GBP because the rate HAS a source. `evaluate_leg` was not touched.

    The honesty that test protected does not disappear - it moves to the banner,
    which still prints CONFIRM BEFORE TRUSTING with the source date and age, and
    to the STALE RATE path above. What is no longer true is the specific claim
    "this total rests on a rate with no source". It has one.
    """
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=[CSP]),
        transfer_date=D, today=date(2026, 9, 8),
    )
    b7 = next(r for r in results if r.leg.id == "B7")
    assert b7.rests_on_placeholder_fx is False
    assert not any(x.code == "FX_PLACEHOLDER" for x in b7.reasons)
    # The number moved by the full correction, and the leg is still flagged as
    # the least reliable in the run by its own data_flags.
    assert b7.cash_usd == pytest.approx(680.57, abs=0.01)
    assert config.needs_confirmation("GBP") is True


def test_supplying_a_rate_clears_the_marker_and_records_provenance(restore_fx, rm):
    config.set_fx_rate("GBP", 1.29)
    assert config.is_placeholder_rate("GBP") is False
    assert config.FX_PROVENANCE_TIER["GBP"] == "user_supplied"
    assert "USER SUPPLIED" in config.FX_RATES_PROVENANCE["GBP"]
    assert "UNVERIFIED RATE" not in " ".join(
        l for l in config.fx_report_lines() if "GBP" in l
    )

    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=[CSP]),
        transfer_date=D, today=date(2026, 9, 8),
    )
    b7 = next(r for r in results if r.leg.id == "B7")
    assert b7.rests_on_placeholder_fx is False
    assert b7.cash_usd == pytest.approx(502.64 * 1.29, abs=0.01)


def test_a_non_positive_fx_rate_is_rejected(restore_fx):
    with pytest.raises(ValueError, match="must be positive"):
        config.set_fx_rate("GBP", 0)
    with pytest.raises(ValueError, match="must be positive"):
        config.set_fx_rate("GBP", -1.3)


def test_the_fx_flag_works_through_the_cli():
    """
    v5 STEP 6, CASE (a). `--offline` was added and NOTHING ELSE CHANGED: the
    same exit code and the same assertion. This test is about --fx, not about
    which transport answers, and after the live-first flip a plain run in a
    sandbox with no network correctly exits 3 instead of scoring badges. The
    mode it always meant is now the mode it names.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", "trip_b_europe.json",
         "--offline",
         "--balance", "UR=180000", "--card", CSP, "--fx", "GBP=1.29",
         "--transfer-date", "2026-09-15"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "user_supplied" in proc.stdout


# ---------------------------------------------------------------------------
# Step 13: the flagship case
# ---------------------------------------------------------------------------


def _flagship(rm):
    fixture = load_trip_fixture(TRIPS / "trip_c_lon_mry_surcharge.json")
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=[CSP]),
        transfer_date=D, today=date(2026, 9, 8),
    )
    return {r.leg.id: r for r in results}, trip_totals(results)


def test_the_ba_option_is_not_scored_at_zero_and_does_not_claim_282_dollars(rm):
    """
    THE POINT OF THE WHOLE VERSION.

    v0: 20,000 BA Avios = $200, surcharge $0, against $482 cash -> "$282 saving".
    v1: that surcharge is UNKNOWN, so the BA option is not scored at all and the
    $282 figure cannot be produced.
    """
    legs, _ = _flagship(rm)
    c1 = legs["C1"]

    assert c1.best_points.program != "British Airways Executive Club", (
        "the BA option must not be the scored winner - its surcharge is unknown"
    )
    assert c1.margin_usd != pytest.approx(282.0), "v0's phantom saving is back"
    assert any(
        "UNKNOWN (not $0)" in w and "British Airways" in w for w in c1.warnings
    ), "the unscoreable BA option must be reported, not silently dropped"


def test_the_ba_surcharge_is_reported_as_unknown_with_a_floor(rm):
    legs, _ = _flagship(rm)
    c1 = legs["C1"]
    # 20,000 UR at 1cpp = $200 before any surcharge.
    #
    # CHANGED AT v5 STEP 7. C1 is LHR->SFO - the SAME UK departure as Trip B's
    # B4 - so the floor now also carries UK Air Passenger Duty: band B, reduced
    # rate, GBP 102.00 x 1 = $138.11 at GBP 1.354. The floor is "the cheapest
    # the points side could conceivably be", and it could not conceivably be
    # cheaper than the government tax that is owed regardless of surcharge. The
    # PROPERTY this test defends - a floor exists and it is not the phantom
    # $282 saving - is unchanged; only the tax it now includes is new.
    assert c1.apd_added_usd == pytest.approx(138.11, abs=0.01)
    assert c1.points_floor_usd == pytest.approx(200.0 + 138.108)
    assert any(
        "surcharge is below $80.00" in x.detail
        for x in c1.reasons
        if x.code == "SURCHARGE_UNKNOWN"
    ), "the break-even against the winning option must be stated"


def test_united_resolves_to_a_confirmed_zero_and_wins(rm):
    legs, _ = _flagship(rm)
    c1 = legs["C1"]
    assert c1.verdict == "points"
    assert c1.best_points.program == "United MileagePlus"
    assert c1.surcharge.is_known and c1.surcharge.amount_point == 0.0
    assert c1.surcharge.confidence == "modeled"


def test_the_zero_is_stated_as_a_program_policy_with_the_tax_caveat(rm):
    """Step 13's one-line statement, plus the caveat that must ride with it."""
    legs, _ = _flagship(rm)
    c1 = legs["C1"]
    line = next(
        (w for w in c1.warnings if "SAME ROUTE, DIFFERENT PROGRAM" in w), None
    )
    assert line is not None
    assert "United MileagePlus" in line
    assert "program policy" in line
    assert "Government taxes" in line, (
        "a $0 CARRIER surcharge is not a $0 ticket - UK APD is still owed and is "
        "not modelled"
    )


def test_the_flagship_headline_is_a_range(rm):
    _, totals = _flagship(rm)
    assert totals["headline_is_a_range"] is True
    assert totals["legs_surcharge_unknown"] == 1


def test_economy_ba_ex_lhr_stays_unknown_end_to_end(rm):
    """
    The research is business-cabin. Extrapolating it to economy would be
    inventing a number, so this leg resolves to unknown - which is correct
    behaviour even though it is not a satisfying answer.
    """
    legs, _ = _flagship(rm)
    c1 = legs["C1"]
    ba = next(
        c for c in c1.leg.points_candidates
        if c.program == "British Airways Executive Club"
    )
    assert ba.cabin == "Y"
    assert ba.operating_carrier == "BA"
    assert ba.has_known_metal, "metal IS known here - the gap is the cabin, not the metal"
