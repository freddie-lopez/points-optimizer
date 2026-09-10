"""
A tax figure the tool may not believe is UNKNOWN, and UNKNOWN taxes are never scored.

Three ways a live award's taxes were being turned into a confident $0:

  * Seats.aero documents that it does not report taxes for `qatar`, `turkish`
    and `singapore` ("Taxes and surcharges are not available for this mileage
    program" - developers.seats.aero/reference/concepts-copy). Those rows carry
    0, and a KrisFlyer row is a DIRECT Chase UR partner.
  * A 0 on an available cabin from any source. The same payload writes 0 into
    every cabin it has no data for.
  * NO tax figure at all. `PointsCandidate` used to say, in a comment, that only
    an unconvertible figure made a leg unscoreable - so a United award with no
    tax figure on Trip B's B4 scored POINTS, 5.82%, as a single number.

And the UK half: when the chosen award's taxes are unknown there is no TotalTaxes
for UK Air Passenger Duty to be hiding in, so the duty is owed on top and belongs
in the floor. Otherwise "points win if surcharge < $182" is printed on a leg where
GBP 102 of that is owed before any surcharge at all.

NO TEST HERE MAKES A NETWORK CALL.
"""
import copy
import json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src.formatter import print_leg_results, print_trip_totals
from src.live_trip import LiveOptions, annotate_live_verdicts, apply_live
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import (
    TAXES_UNREPORTED_SOURCES,
    SeatsClient,
    parse_availability_row,
    untrusted_tax_reason,
)
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
CSP = "Chase Sapphire Preferred"
ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}
UNSET = object()


def _row(source="aeroplan", cost="50000", taxes=4460, currency="CAD",
         airlines="AC, LH, UA, VL", origin="SFO", dest="MAD", iso="2027-01-15"):
    row = copy.deepcopy(REAL["data"][0])
    row["Route"]["Source"] = source
    row["Route"]["OriginAirport"] = origin
    row["Route"]["DestinationAirport"] = dest
    row["Date"] = iso
    row["ParsedDate"] = f"{iso}T00:00:00Z"
    row["YMileageCost"] = cost
    if taxes is UNSET:
        row.pop("YTotalTaxes", None)
    else:
        row["YTotalTaxes"] = taxes
    row["TaxesCurrency"] = currency
    row["YAirlines"] = airlines
    return row


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_the_three_documented_sources_are_the_ones_named():
    assert TAXES_UNREPORTED_SOURCES == {"qatar", "turkish", "singapore"}


@pytest.mark.parametrize("source", ["qatar", "turkish", "singapore", "SINGAPORE "])
@pytest.mark.parametrize("cents", [0, 22463, None])
def test_any_figure_from_an_unreported_source_is_not_believed(source, cents):
    why = untrusted_tax_reason(source, cents)
    assert "does not report taxes" in why
    assert "UNKNOWN - not $0" in why


@pytest.mark.parametrize("source", ["aeroplan", "united", "flyingblue", "", None])
def test_zero_is_not_believed_from_any_source(source):
    assert "NOT REPORTED" in untrusted_tax_reason(source, 0)


@pytest.mark.parametrize("source", ["aeroplan", "united"])
def test_a_real_figure_from_a_reporting_source_is_believed(source):
    assert untrusted_tax_reason(source, 4460) == ""
    # None is convert_taxes's business (already unknown there), not this rule's.
    assert untrusted_tax_reason(source, None) == ""


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def test_a_krisflyer_row_with_zero_taxes_parses_as_unknown_taxes_not_zero():
    (award,) = parse_availability_row(_row(source="singapore", taxes=0, currency="USD"))
    assert award.program == "Singapore Airlines KrisFlyer"
    assert award.ur_transferable is True, "the dangerous case is a DIRECT UR partner"
    assert award.cash_component_known is False
    assert award.cash_component_source_amount is None, (
        "nothing USABLE was reported; an amount here would route the award down "
        "the 'figure that merely needs converting' path"
    )
    assert "does not report taxes" in award.cash_component_note
    # The number the API sent is not thrown away - it is labelled.
    assert award.raw_diagnostics["YTotalTaxes (NOT BELIEVED)"] == 0


def test_a_qatar_row_with_a_nonzero_figure_is_still_unknown():
    (award,) = parse_availability_row(_row(source="qatar", taxes=22463, currency="USD"))
    assert award.cash_component_known is False
    assert award.raw_diagnostics["YTotalTaxes (NOT BELIEVED)"] == 22463


def test_zero_on_an_available_aeroplan_cabin_is_unknown():
    (award,) = parse_availability_row(_row(source="aeroplan", taxes=0))
    assert award.cash_component_known is False
    assert "NOT REPORTED" in award.cash_component_note


def test_the_real_captured_row_is_untouched():
    """CAD 44.60 of real taxes on an Aeroplan row is still a KNOWN figure."""
    (award,) = parse_availability_row(copy.deepcopy(REAL["data"][0]))
    assert award.cash_component_known is True
    assert award.cash_component_source_amount == pytest.approx(44.60)
    assert "YTotalTaxes" in award.raw_diagnostics


def test_zeros_in_unavailable_cabins_still_produce_no_award():
    """The W/J/F zeros in the real row are cabins with no award, not free awards."""
    awards = parse_availability_row(copy.deepcopy(REAL["data"][0]))
    assert [a.award_type for a in awards] == ["Y"]


# ---------------------------------------------------------------------------
# End to end: Trip B, with B4 (LHR->SFO) answered by a row whose taxes are
# not usable. None of these may produce a points verdict or a single number.
# ---------------------------------------------------------------------------


def _run_trip_b(b4_row_kwargs, tmp_path):
    def side(*args, **kwargs):
        params = kwargs.get("params") or {}
        route = (params.get("origin_airport"), params.get("destination_airport"))
        iso = ROUTES[route]
        if route == ("LHR", "SFO"):
            row = _row(origin="LHR", dest="SFO", iso=iso, **b4_row_kwargs)
            row["Route"]["OriginRegion"] = "Europe"
            row["Route"]["DestinationRegion"] = "North America"
        else:
            row = _row(origin=route[0], dest=route[1], iso=iso)
        r = MagicMock()
        r.json.return_value = {"data": [row]}
        r.status_code = 200
        r.raise_for_status.return_value = None
        return r

    fixture = load_trip_fixture(TRIP_B)
    client = SeatsClient(api_key="test_key")
    client.clear_cache()
    SeatsClient.reset_call_budget()
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    with patch("src.seats_client.requests.get", side_effect=side):
        fixture, _ = apply_live(
            fixture, client, LiveOptions(live=True, flex_days=0, cache=cache)
        )
    rm = RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )
    wallet = Wallet(balances={"UR": 160000}, cards=[CSP])
    results = annotate_live_verdicts(
        evaluate_trip(
            fixture.legs,
            ratios_manager=rm,
            wallet=wallet,
            transfer_date=date(2026, 9, 15),
            today=date(2026, 9, 10),
        )
    )
    totals = trip_totals(results, wallet)
    buf = StringIO()
    con = Console(file=buf, width=240, no_color=True)
    print_leg_results(results, console=con)
    print_trip_totals(totals, console=con)
    b4 = next(r for r in results if r.leg.id == "B4")
    return b4, totals, buf.getvalue()


UNUSABLE_B4 = {
    "krisflyer_zero": dict(source="singapore", cost="30000", taxes=0,
                           currency="USD", airlines="SQ"),
    "united_missing": dict(source="united", cost="30000", taxes=UNSET,
                           currency="USD", airlines="UA"),
    "united_zero": dict(source="united", cost="30000", taxes=0,
                        currency="USD", airlines="UA"),
}


@pytest.mark.parametrize("case", sorted(UNUSABLE_B4))
def test_unusable_taxes_never_produce_a_points_verdict(case, tmp_path):
    b4, totals, out = _run_trip_b(UNUSABLE_B4[case], tmp_path)
    assert b4.verdict != "points"
    assert b4.points_total_score_usd == float("inf")
    assert b4.best_points.taxes_unknown is True
    assert totals["legs_where_points_win"] == 0
    assert totals["legs_taxes_unknown_ids"] == ["B4"]
    assert "taxes+surch" in out, "the break-even must not read as a known $0 tax"
    assert "unknown award taxes add nothing beyond any UK APD shown" in out
    assert "award TAXES on B4" in out


@pytest.mark.parametrize("case", sorted(UNUSABLE_B4))
def test_the_floor_carries_uk_apd_when_the_live_taxes_are_unusable(case, tmp_path):
    """30,000 points at 1cpp = $300, plus GBP 102 of APD - never $300 alone."""
    b4, totals, _ = _run_trip_b(UNUSABLE_B4[case], tmp_path)
    assert b4.apd is not None and b4.apd.is_known
    assert b4.apd_added_usd > 100
    assert b4.points_floor_usd == pytest.approx(300.0 + b4.apd_added_usd)
    assert b4.break_even_surcharge_usd == pytest.approx(
        b4.cash_total_score_usd - b4.points_floor_usd
    )
    assert "B4" not in (totals["legs_apd_unverified_ids"] or []), (
        "'unverified whether the fare includes it' is false when there is no "
        "fare figure for it to be in"
    )


def test_the_duty_alone_can_settle_the_verdict(tmp_path):
    """
    40,000 points ($400) + $138 of APD > $482 cash. The unknown remainder can
    only add, so this is a certain cash verdict, not a withheld one.
    """
    b4, _, _ = _run_trip_b(
        dict(source="united", cost="40000", taxes=UNSET, currency="USD",
             airlines="UA"),
        tmp_path,
    )
    assert b4.verdict == "cash"
    assert b4.surcharge_cannot_change_verdict is True
    assert "UK Air Passenger Duty" in b4.verdict_reason
    assert "Do NOT burn points here" in b4.verdict_reason


def test_the_reason_text_quotes_the_moved_break_even(tmp_path):
    b4, _, _ = _run_trip_b(UNUSABLE_B4["united_missing"], tmp_path)
    assert f"${b4.break_even_surcharge_usd:,.2f}" in b4.verdict_reason
    assert "$182.00" not in b4.verdict_reason, "the pre-duty figure is stale"


def test_an_unconvertible_figure_still_does_not_add_apd(tmp_path):
    """
    MXN has no configured rate. The figure EXISTS and may well contain the duty,
    so the live rule (state it, do not add it) still holds.
    """
    b4, _, _ = _run_trip_b(
        dict(source="united", cost="30000", taxes=900000, currency="MXN",
             airlines="UA"),
        tmp_path,
    )
    assert b4.best_points.taxes_unconvertible is True
    assert b4.apd_added_usd == 0.0
    assert b4.apd.inclusion_unverified is True


def test_a_real_tax_figure_on_b4_is_scored_exactly_as_before(tmp_path):
    """The United capture: 27,600 miles + $224.63. Known, scoreable, APD not added."""
    b4, _, _ = _run_trip_b(
        dict(source="united", cost="27600", taxes=22463, currency="USD",
             airlines="UA"),
        tmp_path,
    )
    assert b4.best_points.taxes_unknown is False
    assert b4.verdict == "cash"
    assert b4.apd_added_usd == 0.0


# ---------------------------------------------------------------------------
# The per-leg table's path cell must not contradict the verdict
# ---------------------------------------------------------------------------


def test_a_qatar_row_on_b4_is_named_indirect_in_the_table(tmp_path):
    b4, totals, out = _run_trip_b(
        dict(source="qatar", cost="33000", taxes=0, currency="USD",
             airlines="BA, QR"),
        tmp_path,
    )
    assert b4.verdict == "cash (indirect path not scored)"
    assert "Qatar Privilege Club (indirect, not scored)" in out
    assert "B4" not in totals["legs_no_partner_ids"]


def test_an_unattributed_row_on_b4_does_not_say_not_a_partner(tmp_path):
    """
    The cell used to read "none - not a partner" beside a verdict saying the
    program was never named - the M-5 claim, one column over.
    """
    _, _, out = _run_trip_b(
        dict(source="hawaiianairlines", cost="33000", taxes=4460),
        tmp_path,
    )
    b4_line = next(line for line in out.splitlines() if "│ B4" in line)
    assert "program NOT NAMED - no claim" in b4_line
    assert "not a partner" not in b4_line
