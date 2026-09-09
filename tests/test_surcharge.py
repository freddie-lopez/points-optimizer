"""
Step 7: the surcharge model.

The highest-value test in this file - and arguably in the suite - is
`test_absence_is_unknown_never_zero`. v0's single worst bug was an absent
surcharge silently becoming $0.
"""
import csv
from datetime import date
from pathlib import Path

import pytest

from src.models import SurchargeEstimate
from src.ratio_manager import RatioManager
from src.surcharge import SurchargeTable, SurchargeTableError

DATA = Path(__file__).parent.parent / "data"

BA = "British Airways Executive Club"
IB = "Club Iberia Plus"
EI = "Aer Lingus AerClub"
UA = "United MileagePlus"
AC = "Air Canada Aeroplan"
FB = "Air France-KLM Flying Blue"
SQ = "Singapore Airlines KrisFlyer"


@pytest.fixture
def table():
    return SurchargeTable(DATA / "surcharges.csv")


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


# ---------------------------------------------------------------------------
# THE RULE: absence is unknown, never zero
# ---------------------------------------------------------------------------


def test_absence_is_unknown_never_zero(table):
    """
    An unmatched (program, carrier) pair must NOT yield 0.0.

    This is the direct inverse of the v0 bug and the single most important
    assertion in the project.
    """
    est = table.resolve(BA, "BA", "NA-EU", "Y", "GB", is_round_trip=False)
    assert est.confidence == "unknown"
    assert not est.is_known
    assert est.render() == "UNKNOWN"


def test_economy_ba_ex_lhr_is_unknown_not_the_business_figure(table):
    """
    Tsuki's real B4 leg. The research is business-cabin; economy is deliberately
    absent. Resolving to the $900 business figure would be inventing a number;
    resolving to $0 is the v0 bug. Unknown is the only honest answer.
    """
    est = table.resolve(BA, "BA", "NA-EU", "Y", "GB")
    assert not est.is_known
    assert est.amount_point != 900.0


def test_no_code_path_substitutes_a_default(table):
    """Sweep every program in the table in an unmodelled cabin."""
    for program in {r.program for r in table.rules}:
        est = table.resolve(program, "XX", "NA-EU", "W", "US")
        if est.is_known:
            # Only a blanket-policy program may answer for unmodelled metal.
            assert est.amount_point == 0.0
            assert "POLICY" in est.notes.upper()
        else:
            assert est.confidence == "unknown"


def test_unknown_amounts_are_guarded_by_is_known():
    est = SurchargeEstimate.unknown("no rule")
    assert not est.is_known
    # The zeros exist only as placeholders; is_known is the gate.
    assert est.amount_low == est.amount_point == est.amount_high == 0.0
    assert est.render() == "UNKNOWN"


# ---------------------------------------------------------------------------
# Five-tier specificity
# ---------------------------------------------------------------------------


def test_ex_gb_row_beats_the_generic_ba_row(table):
    ex_uk = table.resolve(BA, "BA", "NA-EU", "J", "GB")
    generic = table.resolve(BA, "BA", "NA-EU", "J", "US")
    assert ex_uk.amount_point == 900.0
    assert generic.amount_point == 800.0
    assert ex_uk.amount_point > generic.amount_point
    assert "dep=GB" in ex_uk.matched_rule


def test_no_blending_between_tiers(table):
    """The winner is one row's numbers verbatim, never an average of two."""
    ex_uk = table.resolve(BA, "BA", "NA-EU", "J", "GB")
    assert (ex_uk.amount_low, ex_uk.amount_point, ex_uk.amount_high) == (750.0, 900.0, 1100.0)


def test_iberia_is_a_much_lower_band_on_the_same_avios(table):
    """
    Same balance, same route region, wildly different cash. The whole feature.
    """
    ba = table.resolve(BA, "BA", "NA-EU", "J", "US")
    ib = table.resolve(IB, "IB", "NA-EU", "J", "US")
    ei = table.resolve(EI, "EI", "NA-EU", "J", "US")
    assert ib.amount_point == 175.0
    assert ei.amount_point == 150.0
    assert ba.amount_point - ib.amount_point >= 600


def test_united_is_a_confirmed_zero_at_every_specificity(table):
    for carrier, region, cabin, country in [
        ("UA", "NA-EU", "J", "GB"),
        ("LH", "NA-NA", "Y", "US"),
        ("AC", "AS-EU", "F", "SG"),
    ]:
        est = table.resolve(UA, carrier, region, cabin, country)
        assert est.is_known, "United's zero is a program policy, not an absence"
        assert est.amount_point == 0.0
        assert est.confidence == "modeled"


def test_aeroplan_is_a_confirmed_zero(table):
    est = table.resolve(AC, "LH", "NA-EU", "J", "GB")
    assert est.is_known
    assert est.amount_point == 0.0


def test_krisflyer_zero_applies_to_sq_metal_only(table):
    own = table.resolve(SQ, "SQ", "AS-NA", "J", "SG")
    assert own.is_known and own.amount_point == 0.0
    partner = table.resolve(SQ, "LH", "AS-EU", "J", "DE")
    assert not partner.is_known, "KrisFlyer on partner metal is not covered"


def test_flying_blue_covers_own_metal_only(table):
    af = table.resolve(FB, "AF", "NA-EU", "J", "US")
    assert af.is_known and af.amount_point == 200.0
    partner = table.resolve(FB, "DL", "NA-EU", "J", "US")
    assert not partner.is_known, (
        "Flying Blue on a partner's metal is not covered by the AF/KL rows"
    )


# ---------------------------------------------------------------------------
# Metal is never guessed
# ---------------------------------------------------------------------------


def test_missing_carrier_degrades_to_unknown(table):
    est = table.resolve(BA, "", "NA-EU", "J", "GB")
    assert not est.is_known
    assert "carrier" in est.notes.lower()


def test_assumed_carrier_degrades_to_unknown(table):
    """
    carrier_is_known=False short-circuits BEFORE the lookup.

    A guessed metal produces a confident wrong surcharge, which is worse than an
    honest unknown.
    """
    est = table.resolve(BA, "BA", "NA-EU", "J", "GB", carrier_is_known=False)
    assert not est.is_known
    assert est.amount_point == 0.0 and not est.is_known


# ---------------------------------------------------------------------------
# Captured beats modeled beats unknown
# ---------------------------------------------------------------------------


def test_captured_overrides_the_modeled_table(table):
    captured = SurchargeEstimate(
        amount_low=412.0, amount_point=412.0, amount_high=412.0,
        confidence="captured", source="ba.com booking page", basis="one_way",
    )
    est = table.resolve(BA, "BA", "NA-EU", "J", "GB", captured=captured)
    assert est.confidence == "captured"
    assert est.amount_point == 412.0


def test_a_captured_zero_is_a_real_zero_not_an_unknown(table):
    """A genuinely surcharge-free fare, read off a real page, is KNOWN to be $0."""
    captured = SurchargeEstimate(
        amount_low=0.0, amount_point=0.0, amount_high=0.0,
        confidence="captured", source="aa.com booking page",
    )
    est = table.resolve(BA, "BA", "NA-EU", "Y", "GB", captured=captured)
    assert est.is_known
    assert est.amount_point == 0.0
    assert est.render() != "UNKNOWN"


def test_captured_wins_even_when_carrier_is_unknown(table):
    captured = SurchargeEstimate(
        amount_low=99.0, amount_point=99.0, amount_high=99.0, confidence="captured",
    )
    est = table.resolve(BA, "", "NA-EU", "J", "GB", carrier_is_known=False, captured=captured)
    assert est.confidence == "captured" and est.amount_point == 99.0


# ---------------------------------------------------------------------------
# Ranges and basis conversion
# ---------------------------------------------------------------------------


def test_ranges_are_preserved_not_collapsed_to_a_midpoint(table):
    est = table.resolve(BA, "BA", "NA-EU", "J", "US")
    assert (est.amount_low, est.amount_point, est.amount_high) == (600.0, 800.0, 1000.0)
    assert est.is_range


def test_one_way_halves_a_round_trip_row_and_says_so(table):
    rt = table.resolve(BA, "BA", "NA-EU", "J", "GB", is_round_trip=True)
    ow = table.resolve(BA, "BA", "NA-EU", "J", "GB", is_round_trip=False)
    assert ow.amount_point == rt.amount_point / 2
    assert ow.basis == "one_way"
    assert "BASIS CONVERTED" in ow.notes


def test_basis_conversion_is_disclosed_as_an_approximation(table):
    ow = table.resolve(BA, "BA", "NA-EU", "J", "GB", is_round_trip=False)
    assert "directional" in ow.notes.lower()


# ---------------------------------------------------------------------------
# Table validator
# ---------------------------------------------------------------------------


def _blanket_map(rm):
    return {p: rm.blanket_no_yq(p) for p in rm.programs}


def test_production_table_validates(table, rm):
    warnings = table.validate(_blanket_map(rm), today=date(2026, 9, 8))
    assert warnings == []


def test_every_row_has_a_source_and_verified_on(table):
    for rule in table.rules:
        assert rule.source, rule.describe()
        assert rule.verified_on is not None, rule.describe()


def test_wildcard_rows_only_for_blanket_no_yq_programs(table, rm):
    for rule in table.rules:
        if rule.operating_carrier == "*":
            assert rm.blanket_no_yq(rule.program), (
                f"{rule.program} has a carrier=* row but is not blanket_no_yq"
            )


def _write(tmp_path, rows):
    header = (
        "program,operating_carrier,route_region,cabin,departure_country,"
        "amount_low,amount_point,amount_high,currency,basis,confidence,"
        "source,verified_on,notes\n"
    )
    p = tmp_path / "surcharges.csv"
    p.write_text(header + "".join(rows))
    return p


def test_validator_rejects_a_wildcard_row_for_a_yq_levying_program(tmp_path, rm):
    p = _write(tmp_path, [
        "British Airways Executive Club,*,*,*,*,0,0,0,USD,round_trip,modeled,s,2026-09-08,n\n"
    ])
    with pytest.raises(SurchargeTableError, match="blanket_no_yq"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_duplicate_keys_at_the_same_tier(tmp_path, rm):
    row = "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,modeled,s,2026-09-08,n\n"
    p = _write(tmp_path, [row, row])
    with pytest.raises(SurchargeTableError, match="Duplicate"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_low_above_high(tmp_path, rm):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,900,175,200,USD,round_trip,modeled,s,2026-09-08,n\n"
    ])
    with pytest.raises(SurchargeTableError, match="low <= point <= high"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_a_negative_surcharge(tmp_path, rm):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,-50,175,200,USD,round_trip,modeled,s,2026-09-08,n\n"
    ])
    with pytest.raises(SurchargeTableError):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_a_missing_source(tmp_path, rm):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,modeled,,2026-09-08,n\n"
    ])
    with pytest.raises(SurchargeTableError, match="no source"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_a_verified_on_in_the_future(tmp_path, rm):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,modeled,s,2099-01-01,n\n"
    ])
    with pytest.raises(SurchargeTableError, match="future"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_rejects_confidence_unknown_in_the_table(tmp_path, rm):
    """A row can never be 'unknown'. Unknown is the ABSENCE of a row."""
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,unknown,s,2026-09-08,n\n"
    ])
    with pytest.raises(SurchargeTableError, match="unknown is the ABSENCE"):
        SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))


def test_validator_warns_but_does_not_fail_on_a_stale_row(tmp_path, rm):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,modeled,s,2026-01-01,n\n"
    ])
    warnings = SurchargeTable(p).validate(_blanket_map(rm), today=date(2026, 9, 8))
    assert len(warnings) == 1 and "more than 180 days" in warnings[0]


def test_stale_rows_are_flagged_in_the_resolved_estimate(tmp_path):
    p = _write(tmp_path, [
        "Club Iberia Plus,IB,NA-EU,J,*,150,175,200,USD,round_trip,modeled,s,2026-01-01,n\n"
    ])
    est = SurchargeTable(p).resolve(IB, "IB", "NA-EU", "J", "US", today=date(2026, 9, 8))
    assert "STALE" in est.notes


def test_csv_header_is_the_documented_schema():
    with open(DATA / "surcharges.csv") as f:
        header = next(csv.reader(f))
    assert header == [
        "program", "operating_carrier", "route_region", "cabin",
        "departure_country", "amount_low", "amount_point", "amount_high",
        "currency", "basis", "confidence", "source", "verified_on", "notes",
    ]
