"""
v5 Step 7: what UK Air Passenger Duty does to a score, and what it refuses to do.

APD emits a dollar figure, so every failure mode that produces a confident wrong
number has a test here:

  absence-as-zero  an uncovered country is UNKNOWN, never $0.00, never band A
  unit error       GBP is converted and the GBP figure is printed beside the $
  double charge    NOT added on live or replayed legs. Flag only.
  per-passenger    multiplied by travelers, and the multiplication is SHOWN
  cabin            W maps to standard AND prints the pitch caveat
"""
from datetime import date
from pathlib import Path

import pytest

from src import apd as apd_module
from src.apd import APDTableError, apd_for_leg, load_apd_bands, load_apd_table
from src.models import Leg, PointsProvenance
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.surcharge import default_table
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"
CSP = "Chase Sapphire Preferred"
D = date(2026, 9, 15)


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


@pytest.fixture
def rates():
    return load_apd_table()


@pytest.fixture
def bands():
    return load_apd_bands()


def flight(origin, destination, when=date(2027, 1, 27), travelers=1, leg_id="X1"):
    return Leg(
        id=leg_id,
        kind="flight",
        description=f"{origin}->{destination}",
        date=when,
        origin=origin,
        destination=destination,
        travelers=travelers,
    )


def score_trip_b(rm, provenance=None):
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    if provenance is not None:
        for leg in fixture.legs:
            leg.points_provenance = provenance
    results = evaluate_trip(
        fixture.legs,
        ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=[CSP]),
        transfer_date=D,
        surcharges=default_table(),
    )
    return {r.leg.id: r for r in results}, trip_totals(results)


# ---------------------------------------------------------------------------
# The band table
# ---------------------------------------------------------------------------


def test_the_band_table_loads_and_every_row_carries_its_justification(bands):
    assert len(bands) > 50
    for code, row in bands.bands.items():
        assert row.source, code
        assert row.verified_on is not None, code
        if row.is_known:
            assert row.capital, code
            assert row.distance_miles is not None, code


def test_the_us_is_band_b_because_of_washington_not_because_of_sfo(bands):
    """
    HMRC bands by distance from London to the DESTINATION COUNTRY'S CAPITAL.
    Nothing in this repo knows a capital or a distance, so the band is DATA and
    the capital that justified it is written down beside it.
    """
    row = bands.lookup("US")
    assert row.band == "B"
    assert row.capital == "Washington DC"
    assert 3000 < row.distance_miles < 4500, (
        "London-SFO is about 5350 miles and would be a different answer; the "
        "band is set by the capital"
    )


def test_a_country_not_in_the_table_has_no_band_and_is_not_band_a(bands):
    assert bands.band_for_destination("ZZ") is None
    assert "UNKNOWN" in bands.unknown_reason("ZZ")


def test_a_country_near_a_band_boundary_is_written_down_as_unknown(bands):
    """
    The distances in this table are COMPUTED from capital coordinates, not read
    off HMRC's published country list. Within 300 miles of the 2000- or
    5500-mile boundary that is not good enough to assert a band, so those rows
    say UNKNOWN and say why. UNKNOWN is not the nearer band and it is not zero.
    """
    row = bands.lookup("MX")
    assert row is not None, "the row EXISTS - the unknown is written down"
    assert row.band is None
    assert "BOUNDARY" in row.notes.upper()
    assert bands.band_for_destination("MX") is None


def test_a_duplicate_country_is_rejected(tmp_path):
    path = tmp_path / "bands.csv"
    path.write_text(
        "destination_country,band,capital,distance_miles,source,verified_on,notes\n"
        "US,B,Washington,3665,gov.uk,2026-09-09,x\n"
        "US,C,Washington,3665,gov.uk,2026-09-09,x\n"
    )
    with pytest.raises(APDTableError, match="twice"):
        load_apd_bands(path)


def test_an_empty_band_cell_is_rejected_rather_than_read_as_unknown(tmp_path):
    """A blank cell is indistinguishable from a typo. UNKNOWN must be typed."""
    path = tmp_path / "bands.csv"
    path.write_text(
        "destination_country,band,capital,distance_miles,source,verified_on,notes\n"
        "US,,Washington,3665,gov.uk,2026-09-09,x\n"
    )
    with pytest.raises(APDTableError, match="EMPTY band"):
        load_apd_bands(path)


# ---------------------------------------------------------------------------
# apd_for_leg: applies only to UK departures
# ---------------------------------------------------------------------------


def test_a_non_uk_departure_gets_nothing_at_all_not_a_zero(rates, bands):
    for origin, destination in (("SFO", "MAD"), ("MAD", "AMS"), ("AMS", "LHR")):
        charge = apd_for_leg(
            flight(origin, destination), "Y", 1, rates=rates, bands=bands
        )
        assert charge is None, f"{origin}->{destination} must carry NO APD field"


def test_a_uk_departure_to_the_us_owes_band_b_reduced(rates, bands):
    charge = apd_for_leg(
        flight("LHR", "SFO", date(2027, 1, 27)), "Y", 1, rates=rates, bands=bands
    )
    assert charge.is_known
    assert charge.band == "B"
    assert charge.cabin_class == "reduced"
    assert charge.rate_gbp == 102.00
    assert charge.total_gbp == 102.00
    assert charge.total_usd == pytest.approx(138.108)


def test_the_gbp_figure_is_printed_beside_the_dollar_one(rates, bands):
    """A conversion nobody can check is a conversion nobody should trust."""
    line = apd_for_leg(
        flight("LHR", "SFO"), "Y", 1, rates=rates, bands=bands
    ).render()
    assert "GBP 102.00" in line
    assert "$138.11" in line


def test_the_per_passenger_multiplication_is_shown_not_folded_in(rates, bands):
    """
    APD is per passenger, and Trip B's traveller counts are still incoherent
    (plan section 8.1), which is exactly why the arithmetic is printed.
    """
    charge = apd_for_leg(
        flight("LHR", "SFO", travelers=2), "Y", 2, rates=rates, bands=bands
    )
    assert charge.total_gbp == 204.00
    assert "GBP 102.00 x 2 = GBP 204.00" in charge.render()


def test_the_2027_april_boundary_is_exercised_on_a_uk_departure(rates, bands):
    before = apd_for_leg(
        flight("LHR", "SFO", date(2027, 3, 31)), "Y", 1, rates=rates, bands=bands
    )
    after = apd_for_leg(
        flight("LHR", "SFO", date(2027, 4, 1)), "Y", 1, rates=rates, bands=bands
    )
    assert before.rate_gbp == 102.00
    assert after.rate_gbp == 105.33


def test_a_j_cabin_uk_departure_to_the_us_picks_standard(rates, bands):
    charge = apd_for_leg(flight("LHR", "SFO"), "J", 1, rates=rates, bands=bands)
    assert charge.cabin_class == "standard"
    assert charge.rate_gbp == 244.00
    assert "CABIN MAPPING CAVEAT" not in charge.render()


def test_a_w_cabin_picks_standard_and_prints_the_pitch_caveat(rates, bands):
    """
    HMRC's test is SEAT PITCH >= 40in, not cabin name. A premium-economy seat
    below that pitch is legally reduced-rate and standard-rate under our
    mapping, so W is the row to revisit first and the line says so.
    """
    charge = apd_for_leg(flight("LHR", "SFO"), "W", 1, rates=rates, bands=bands)
    assert charge.cabin_class == "standard"
    assert charge.rate_gbp == 244.00
    assert "CABIN MAPPING CAVEAT" in charge.render()
    assert "40 inches" in charge.render()


def test_a_uk_departure_to_an_uncovered_country_is_unknown_and_never_zero(
    rates, bands
):
    charge = apd_for_leg(flight("LHR", "MEX"), "Y", 1, rates=rates, bands=bands)
    assert charge is not None, "the leg DOES depart the UK, so APD applies"
    assert charge.is_known is False
    assert charge.applied_usd == 0.0
    line = charge.render()
    assert "UNKNOWN" in line
    assert "$0.00" not in line, "an unknown tax must never render as a zero"
    assert "LOWER BOUND" in line


def test_a_date_outside_both_rate_periods_is_unknown_not_the_nearest_period(
    rates, bands
):
    charge = apd_for_leg(
        flight("LHR", "SFO", date(2026, 1, 1)), "Y", 1, rates=rates, bands=bands
    )
    assert charge.is_known is False
    assert "$0.00" not in charge.render()
    assert "not the nearest period" in charge.render()


def test_a_hotel_leg_never_owes_apd(rates, bands):
    hotel = Leg(id="H1", kind="hotel", description="x", date=date(2027, 1, 27))
    assert apd_for_leg(hotel, "Y", 1, rates=rates, bands=bands) is None


def test_fx_staleness_propagates_onto_the_apd_line(rates, bands, monkeypatch):
    from src import config

    monkeypatch.setitem(config.FX_PROVENANCE_TIER, "GBP", "placeholder")
    line = apd_for_leg(
        flight("LHR", "SFO"), "Y", 1, rates=rates, bands=bands
    ).render()
    assert "UNVERIFIED RATE" in line
    assert "inherits that uncertainty" in line


# ---------------------------------------------------------------------------
# Trip B, offline: the one number v5 moves on purpose
# ---------------------------------------------------------------------------


def test_trip_b_offline_b4_picks_up_apd_and_its_saving_drops(rm):
    legs, totals = score_trip_b(rm)
    b4 = legs["B4"]

    assert b4.apd is not None and b4.apd.is_known
    assert b4.apd.band == "B"
    assert b4.apd.cabin_class == "reduced"
    assert b4.apd.rate_gbp == 102.00
    assert b4.apd_added_usd == pytest.approx(138.108)

    # THE INTENDED MOVEMENT, stated as both numbers.
    assert b4.margin_usd == pytest.approx(63.892, abs=0.01)
    assert b4.margin_usd + b4.apd_added_usd == pytest.approx(202.0, abs=0.01)
    assert b4.verdict == "points", "it still wins, by $64 rather than by $202"

    # And the trip headline follows from it, with the DENOMINATOR untouched.
    assert totals["all_cash_usd"] == pytest.approx(3126.11, abs=0.02)
    assert totals["savings_usd"] == pytest.approx(63.89, abs=0.02)
    assert totals["beat_cash_pct_low"] == pytest.approx(2.04, abs=0.02)
    assert totals["beat_cash_pct_high"] == pytest.approx(11.03, abs=0.02)


def test_apd_enters_the_points_side_only(rm):
    """
    A captured cash fare is a PUBLISHED fare and already contains APD. Adding
    it to both sides would cancel out of the margin and hide the whole effect.
    """
    legs, totals = score_trip_b(rm)
    b4 = legs["B4"]
    assert b4.cash_total_score_usd == pytest.approx(482.0)
    assert b4.cash_baseline_usd == pytest.approx(482.0)
    assert totals["all_cash_usd"] == pytest.approx(3126.11, abs=0.02)


def test_b1_b2_b3_are_unaffected_and_print_no_zero(rm):
    legs, _ = score_trip_b(rm)
    for leg_id in ("B1", "B2", "B3"):
        result = legs[leg_id]
        assert result.apd is None, f"{leg_id} has no UK departure"
        assert result.apd_added_usd == 0.0
        assert not any(
            w.startswith("UK AIR PASSENGER DUTY on ") for w in result.warnings
        )


def test_the_apd_line_says_what_it_did_and_why(rm):
    legs, _ = score_trip_b(rm)
    line = next(w for w in legs["B4"].warnings if w.startswith("UK AIR PASSENGER DUTY on "))
    assert "ADDED to the points-side cash total" in line
    assert "Band B chosen because" in line
    assert "Washington DC" in line and "3665 miles" in line
    assert "gov.uk" in line


def test_a_reason_code_records_the_addition(rm):
    legs, _ = score_trip_b(rm)
    codes = [r.code for r in legs["B4"].reasons]
    assert "APD_ADDED" in codes


# ---------------------------------------------------------------------------
# Trip B, live or replayed: FLAGGED, NOT ADDED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance", [PointsProvenance.LIVE, PointsProvenance.SNAPSHOT]
)
def test_a_live_or_replayed_b4_is_bit_identical_with_and_without_the_table(
    rm, provenance, monkeypatch
):
    """
    THE DOUBLE-CHARGE FENCE. On a live or replayed leg the cash figure came
    from Seats.aero's TotalTaxes and nobody has checked whether that already
    includes APD, so the amount is stated and NOT added. Proved by scoring the
    same trip with the APD table LOADABLE and with it made to fail to load: the
    totals must be identical.
    """
    _, with_table = score_trip_b(rm, provenance)

    def _boom(*_a, **_kw):
        raise APDTableError("table deliberately unavailable for this test")

    monkeypatch.setattr(apd_module, "load_apd_table", _boom)
    _, without_table = score_trip_b(rm, provenance)

    for key in (
        "all_cash_usd",
        "optimized_usd",
        "savings_usd",
        "beat_cash_pct_low",
        "beat_cash_pct_high",
    ):
        assert with_table[key] == without_table[key], key


@pytest.mark.parametrize(
    "provenance", [PointsProvenance.LIVE, PointsProvenance.SNAPSHOT]
)
def test_a_live_or_replayed_b4_states_the_amount_and_names_what_settles_it(
    rm, provenance
):
    legs, _ = score_trip_b(rm, provenance)
    b4 = legs["B4"]

    assert b4.apd is not None and b4.apd.is_known
    assert b4.apd.inclusion_unverified is True
    assert b4.apd_added_usd == 0.0, "flagged, not charged"
    assert b4.apd.applied_usd == 0.0

    line = next(w for w in b4.warnings if w.startswith("UK AIR PASSENGER DUTY on "))
    assert "GBP 102.00" in line and "$138.11" in line
    assert "IT IS NOT ADDED HERE" in line
    assert "NOBODY HAS CHECKED" in line
    assert "TotalTaxes" in line
    assert "double-charging" in line
    assert "booking page" in line, "the line must name what would settle it"

    assert "APD_INCLUSION_UNVERIFIED" in [r.code for r in b4.reasons]


def test_the_two_modes_disagree_on_purpose_and_both_say_so(rm):
    """
    RISK 9.4, MADE VISIBLE. The same leg scores differently offline and live,
    deliberately, and a reader who compares them without reading the flag sees
    an inconsistency. Pinned here so nobody "fixes" it by making one side
    guess.
    """
    offline, _ = score_trip_b(rm)
    live, _ = score_trip_b(rm, PointsProvenance.LIVE)
    difference = (
        live["B4"].points_total_score_usd - offline["B4"].points_total_score_usd
    )
    assert difference == pytest.approx(-138.108), (
        "the two modes differ by EXACTLY the APD, and by nothing else"
    )
    assert "ADDED" in next(
        w for w in offline["B4"].warnings if w.startswith("UK AIR PASSENGER DUTY on ")
    )
    assert "NOT ADDED" in next(
        w for w in live["B4"].warnings if w.startswith("UK AIR PASSENGER DUTY on ")
    )


# ---------------------------------------------------------------------------
# Off-limits compliance
# ---------------------------------------------------------------------------


def test_apd_never_enters_the_surcharge_table(rm):
    """
    A government departure tax and a carrier YQ are different quantities. APD
    never becomes a surcharge row and never participates in the
    captured-beats-table precedence.
    """
    legs, _ = score_trip_b(rm)
    b4 = legs["B4"]
    assert b4.surcharge is not None
    assert b4.surcharge.amount_point != pytest.approx(138.108)
    assert "APD" not in (b4.surcharge.matched_rule or "").upper()
    assert "APD" not in (b4.surcharge.source or "").upper()


def test_optimizer_touches_apd_in_exactly_one_function():
    """
    One import, one function, and that function CALLS nothing behind the fence.
    Checked on calls rather than on mentions, so the docstring may name what it
    is complying with.
    """
    src = (ROOT / "src" / "optimizer.py").read_text()
    assert src.count("from src import apd as apd_module") == 1
    body = src[src.index("def apply_apd("):]
    for fenced in (
        "SurchargeTable(",
        ".resolve(",
        ".resolve_ambiguous_metal(",
        "parse_pages",
        "evaluate_leg(",
    ):
        assert fenced not in body, fenced


def test_a_missing_apd_table_leaves_every_score_untouched(rm, monkeypatch):
    """
    Loud elsewhere, silent here. A malformed table must not make every UK
    departure cost zero APD while the output claims APD was considered.
    """
    def _boom(*_a, **_kw):
        raise APDTableError("nope")

    monkeypatch.setattr(apd_module, "load_apd_bands", _boom)
    legs, _ = score_trip_b(rm)
    assert legs["B4"].apd is None
    assert legs["B4"].apd_added_usd == 0.0


# ---------------------------------------------------------------------------
# H-2: the cabin comes from the LEG, not only from a points candidate
# ---------------------------------------------------------------------------


def test_apd_cabin_prefers_the_leg_over_a_candidate_and_reports_a_conflict():
    from src.models import Leg as _Leg, LegResult as _LegResult, PointsCandidate as _PC
    from src.optimizer import _apd_cabin

    leg = _Leg(id="L1", kind="flight", description="", date=date(2027, 1, 27),
               origin="LHR", destination="SFO", travelers=1, cabin="J")
    result = _LegResult(leg=leg)
    assert _apd_cabin(result) == ("J", "")

    candidate = _PC(label="award", program="p", points=1, cabin="Y")
    leg.points_candidates = [candidate]
    result.best_points = candidate
    cabin, note = _apd_cabin(result)
    assert cabin == "J"
    assert "CABIN DISAGREEMENT" in note

    # No leg cabin: the scored award's cabin answers.
    leg.cabin = ""
    assert _apd_cabin(result) == ("Y", "")

    # Nothing records a cabin at all: UNKNOWN, not "Y".
    leg.points_candidates = []
    result.best_points = None
    assert _apd_cabin(result) == ("", "")


def test_a_new_trip_shaped_leg_is_taxed_on_its_own_cabin(rates, bands):
    """
    The shape v5's own builder produces: a leg with a cabin and NO candidates.
    It used to fall through to the literal "Y" and be charged GBP 102.
    """
    from src.models import Leg as _Leg, LegResult as _LegResult
    from src.optimizer import _apd_cabin

    for cabin, gbp, words in (("J", 244.0, "standard"), ("Y", 102.0, "reduced")):
        leg = _Leg(id="L1", kind="flight", description="", date=date(2027, 1, 27),
                   origin="LHR", destination="SFO", travelers=1, cabin=cabin)
        charge = apd_for_leg(
            leg, _apd_cabin(_LegResult(leg=leg))[0], 1, rates=rates, bands=bands
        )
        assert charge.total_gbp == gbp
        assert f"per the {words} rate" in charge.render()


def test_a_leg_with_no_cabin_anywhere_is_unknown_not_reduced(rates, bands):
    from src.models import Leg as _Leg, LegResult as _LegResult
    from src.optimizer import _apd_cabin

    leg = _Leg(id="L1", kind="flight", description="", date=date(2027, 1, 27),
               origin="LHR", destination="SFO", travelers=1)
    charge = apd_for_leg(
        leg, _apd_cabin(_LegResult(leg=leg))[0], 1, rates=rates, bands=bands
    )
    assert charge.is_known is False
    assert "not one of" in charge.render()


def test_the_loader_reads_the_leg_level_cabin_the_builder_writes(tmp_path):
    from src import trip_builder
    from src.trip_loader import load_trip_fixture

    path = trip_builder.new_trip_from_flags(
        "cabin_probe", ["LHR:SFO:2027-01-27:500"], [], cabin="J", directory=tmp_path
    )
    fixture = load_trip_fixture(path)
    assert fixture.legs[0].cabin == "J"
