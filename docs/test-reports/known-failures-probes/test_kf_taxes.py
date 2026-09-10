"""
Tester probes: UNKNOWN taxes must never reach a score, a verdict, a single
number, or the optimistic end of the range as $0 without the label saying so.

A RED test here is a defect that is still present on `fix/known-failures`.
Every probe drives the real parser -> apply_live -> evaluate_trip -> formatter
(or the whole CLI in-process) with `requests.get` stubbed. No network.
"""
import re
import sys
from datetime import date

import pytest

from conftest import (
    BASE_ARGV,
    ROOT,
    UNSET,
    evaluate,
    flat,
    only,
    row,
    run_cli,
)

B4 = ("LHR", "SFO")
APD_Y_USD = 138.108  # GBP 102 x 1.3540, reduced rate, band B


def _rows(*specs):
    return lambda o, d, i: [row(origin=o, dest=d, iso=i, **s) for s in specs]


UA_KNOWN = dict(source="united", cost="27600", taxes=22463, currency="USD", airlines="UA")
UA_MISSING_20K = dict(source="united", cost="20000", taxes=UNSET, currency="USD", airlines="UA")
UA_MISSING_30K = dict(source="united", cost="30000", taxes=UNSET, currency="USD", airlines="UA")


# ===========================================================================
# RED - defects still present
# ===========================================================================


def test_gap1_a_rejected_unknown_tax_alternative_is_named_as_TAXES_at_trip_level(tmp_path):
    """
    Known gap 1, sized. The CHOSEN award has known taxes; a rejected alternative
    has none. Its floor ($200 = 20,000 pts, taxes $0, APD $0) becomes the leg's
    optimistic end and the trip's high end (0.00% - 9.02%), and the label says
    only "every unknown surcharge is $0". `legs_taxes_unknown` is keyed on the
    CHOSEN candidate, so B4 is not in it and nothing says TAXES.
    """
    res, totals, out = evaluate(only(B4, _rows(UA_KNOWN, UA_MISSING_20K)), tmp_path)
    b4 = res["B4"]
    assert b4.winner_cost_low_usd < b4.cash_total_score_usd, "precondition: B4 widens the range"
    text = flat(out)
    assert "B4" in totals["legs_taxes_unknown_ids"] or "award TAXES on B4" in text, (
        "the trip's high end rests on an award whose TAXES are unknown, and the "
        "label names only surcharges"
    )


def test_gap1_a_rejected_unknown_tax_alternative_floor_carries_the_owed_uk_apd(tmp_path):
    """
    The alternative has NO TotalTaxes for APD to hide in, so the duty is owed on
    top of it exactly as on a chosen unknown-tax award. Its floor is at least
    $200 + $138.11, not $200.
    """
    res, _, _ = evaluate(only(B4, _rows(UA_KNOWN, UA_MISSING_20K)), tmp_path)
    b4 = res["B4"]
    assert b4.points_floor_usd >= 200.0 + APD_Y_USD - 0.01, (
        f"floor {b4.points_floor_usd} omits UK APD that is certainly owed"
    )


def test_gap1_the_high_end_overstates_a_points_win_by_the_owed_apd(tmp_path):
    """
    A scored United win ($250) plus a cheaper KrisFlyer alternative with
    unreported taxes (10,000 pts). The headline's high end is built on $100 for
    B4 - no taxes, no APD - and printed as 12.22%. With the duty that is owed on
    that ticket regardless, the best B4 can be is $238.11.
    """
    res, totals, _ = evaluate(
        only(B4, _rows(
            dict(source="united", cost="20000", taxes=5000, currency="USD", airlines="UA"),
            dict(source="singapore", cost="10000", taxes=0, currency="USD", airlines="SQ"),
        )),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.verdict == "points"
    assert b4.winner_cost_low_usd >= 100.0 + APD_Y_USD - 0.01, (
        f"B4 contributes {b4.winner_cost_low_usd:.2f} to the optimistic end"
    )


def test_gap1_the_rejected_alternative_warning_names_taxes_not_only_a_surcharge(tmp_path):
    res, _, _ = evaluate(only(B4, _rows(UA_KNOWN, UA_MISSING_20K)), tmp_path)
    line = next(
        w for w in res["B4"].warnings if "20,000 pts" in w and "NOT SCORED" in w
    )
    assert "TAXES" in line.upper().replace("CARRIER-IMPOSED SURCHARGE", ""), line


def test_negative_taxes_floor_carries_the_owed_uk_apd(tmp_path):
    """
    -5.00 USD is corrupt, not a figure 'in a currency we cannot price'. It is
    bucketed with unconvertible taxes, so the live rule ('TotalTaxes may already
    contain APD') is applied to a TotalTaxes that cannot contain GBP 102, and
    the floor stays at $300.
    """
    res, _, _ = evaluate(
        only(B4, _rows(dict(source="united", cost="30000", taxes=-500,
                            currency="USD", airlines="UA"))),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.verdict != "points"
    assert b4.points_floor_usd >= 300.0 + APD_Y_USD - 0.01, b4.points_floor_usd


def test_negative_usd_taxes_are_not_described_as_a_missing_fx_rate(tmp_path):
    _, _, out = evaluate(
        only(B4, _rows(dict(source="united", cost="30000", taxes=-500,
                            currency="USD", airlines="UA"))),
        tmp_path,
    )
    text = flat(out)
    assert "USD -5.00 and no FX rate for that currency is configured" not in text
    assert "USD -5.00 - CANNOT be converted to USD" not in text


def test_a_leg_whose_only_unknown_is_TAXES_is_not_reported_as_an_unknown_SURCHARGE(
    tmp_path, capsys, monkeypatch
):
    """
    United levies no carrier surcharge (program policy, known $0). The ONLY
    unknown on B4 is the missing tax figure, yet the CLI prints it three more
    times as a surcharge question: the verdict cell, the totals row that counts
    it TWICE (surcharge row AND taxes row), and the footer.
    """
    code, raw, text = run_cli(BASE_ARGV, only(B4, _rows(UA_MISSING_30K)), capsys, monkeypatch)
    assert code == 0
    assert (
        "Legs where a points path exists but its carrier-imposed surcharge is "
        "UNKNOWN (NOT $0): B4" not in text
    )
    assert "The spread is carrier-imposed surcharges that are not known" not in text


def test_no_stale_pre_apd_break_even_is_printed(tmp_path, capsys, monkeypatch):
    """
    The break-even moved from $182.00 to $43.89 when APD was added. The leg's
    warning line was written by evaluate_leg BEFORE apply_apd ran and still says
    'Points beat cash only if the surcharge is below $182.00' - a figure that
    ignores GBP 102 owed on the ticket, printed a few lines from the $43.89.
    The Coder's own CLI test asserts on '$43.89' strings only, so it misses it.
    """
    _, _, text = run_cli(BASE_ARGV, only(B4, _rows(UA_MISSING_30K)), capsys, monkeypatch)
    assert "$43.89" in text, "precondition: the moved break-even is printed"
    assert "below $182.00" not in text


def test_the_parser_version_was_bumped_when_the_parse_changed():
    """
    seats_client.PARSER_VERSION: 'BUMP THIS whenever parse_pages_detail or
    anything it calls changes what a given page yields.' This branch changes
    what a page yields (tax 0 / qatar / turkish / singapore -> UNKNOWN; six new
    source names; qatar and finnair carry an indirect path) and leaves the
    version at master's value. A replay of a pre-branch snapshot prints
    'parser 2026-09-09.v5 at capture / 2026-09-09.v5 now' - no REPARSED
    warning - over a different answer (see the replay probe below).
    """
    from src.seats_client import PARSER_VERSION

    assert PARSER_VERSION != "2026-09-09.v5"


def test_a_replay_whose_answer_changed_under_the_parser_says_it_was_reparsed(
    tmp_path, capsys, monkeypatch
):
    """
    Same bytes, same manifest hash: master prints 0.00% - 5.82% for this corpus,
    this branch prints 0.00% - 1.40%. The only thing that makes a changed answer
    over unchanged bytes legible is the REPARSED banner, which keys on the
    parser version.
    """
    from docs_v5 import build_corpus, one_award_page  # type: ignore

    def pages_for(leg_id, o, d, iso):
        p = one_award_page(o, d, iso)
        if leg_id == "B4":
            r = p["data"][0]
            r["Route"]["Source"] = "singapore"
            r["YMileageCost"] = "30000"
            r["YTotalTaxes"] = 0
            r["TaxesCurrency"] = "USD"
            r["YAirlines"] = "SQ"
            r["Route"]["OriginRegion"] = "Europe"
            r["Route"]["DestinationRegion"] = "North America"
        return [p]

    manifest, _ = build_corpus(
        tmp_path, pages_for=pages_for,
        meta_extra=lambda leg: {"parser_version": "2026-09-09.v5"},
    )
    code, _, text = run_cli(
        BASE_ARGV + ["--from-snapshot", str(manifest)], lambda *a: None,
        capsys, monkeypatch, key=None,
    )
    assert code == 0
    assert "REPARSED UNDER A DIFFERENT PARSER VERSION" in text


@pytest.mark.parametrize("cents", [1, 500])
def test_a_uk_departure_tax_figure_below_the_apd_owed_is_not_believed(cents, tmp_path):
    """
    PRE-EXISTING on master, and the same defect class this branch closes for
    exactly 0. B4 LHR-SFO, United 35,000 points with TotalTaxes of $0.01 / $5.00.
    GBP 102 of UK APD ($138.11) is owed on this ticket, so a figure below that
    CANNOT contain it and is provably incomplete. It is believed: B4 scores
    POINTS at $350.01 / $355.00 and the trip headline is a SINGLE number
    (4.22% / 4.06%, not a range). With the duty that is certainly owed, the
    points side is at least $488.11 against $482.00 cash - the verdict is CASH.
    The branch's own premise ('no award ticket carries zero government
    charges') is enforced at 0 and nowhere else; one cent walks past it.
    """
    res, totals, _ = evaluate(
        only(B4, _rows(dict(source="united", cost="35000", taxes=cents,
                            currency="USD", airlines="UA"))),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.verdict != "points", (
        f"B4 scored POINTS at ${b4.points_total_score_usd:.2f} on a tax figure of "
        f"${cents / 100:.2f} that cannot contain the $138.11 APD owed"
    )


# --- the single-route search path (--origin/--destination/--date) ---------


def _search(rows, capsys, monkeypatch):
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"]
    return run_cli(argv, lambda o, d, i: rows, capsys, monkeypatch)


def test_search_mode_never_prints_unknown_taxes_as_zero_dollars(capsys, monkeypatch):
    """
    REGRESSION on this branch. A KrisFlyer row carrying $150.00 of taxes was
    totalled at $670 on master. The branch now (rightly) disbelieves the figure,
    and `optimize()` - never touched by the branch - turns the unknown into
    `cash_cost = 0.0`: the table prints 'Cash Cost $0.00', and 'Top strategy
    cash cost: $0.00'. Same for a 0 from any source and a missing figure.
    """
    code, raw, text = _search(
        [row(source="singapore", cost="52000", taxes=15000, currency="USD",
             airlines="SQ", rid="sq"),
         row(source="united", cost="50000", taxes=5600, currency="USD",
             airlines="UA", rid="ua")],
        capsys, monkeypatch,
    )
    assert code == 0
    sq_line = next(l for l in raw.splitlines() if "KrisFlyer" in l)
    assert "$0.00" not in sq_line, sq_line
    assert "Top strategy cash cost: $0.00" not in text


def test_search_mode_does_not_rank_an_unknown_tax_award_first_on_a_zero(capsys, monkeypatch):
    code, raw, _ = _search(
        [row(source="singapore", cost="52000", taxes=15000, currency="USD",
             airlines="SQ", rid="sq"),
         row(source="united", cost="50000", taxes=5600, currency="USD",
             airlines="UA", rid="ua")],
        capsys, monkeypatch,
    )
    first = next(l for l in raw.splitlines() if re.match(r"^[│|]\s*1\s", l))
    assert "KrisFlyer" not in first, (
        "an award with UNKNOWN taxes is ranked #1 because its unknown was "
        "totalled as $0: " + first
    )


# ===========================================================================
# GREEN - attacks that did not break it
# ===========================================================================


def test_two_unknown_tax_candidates_the_floor_carries_apd_and_nothing_is_scored(tmp_path):
    res, totals, _ = evaluate(
        only(B4, _rows(
            dict(source="united", cost="27600", taxes=UNSET, currency="USD", airlines="UA"),
            dict(source="singapore", cost="20000", taxes=15000, currency="USD", airlines="SQ"),
        )),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.verdict != "points"
    assert b4.points_total_score_usd == float("inf")
    assert b4.points_floor_usd == pytest.approx(200.0 + APD_Y_USD, abs=0.01)
    assert totals["legs_taxes_unknown_ids"] == ["B4"]


def test_an_off_date_promoted_unknown_tax_award_is_not_scored_and_uses_its_own_dates_cash(tmp_path):
    from src.models import CashOption

    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                leg.cash_options.append(CashOption(
                    label="$470 UA Jan 26", amount=470.0, currency="USD",
                    date=date(2027, 1, 26)))

    res, totals, _ = evaluate(
        only(B4, lambda o, d, i: [row(origin=o, dest=d, iso="2027-01-26", **UA_MISSING_20K)]),
        tmp_path, flex_days=2, fixture_mutator=mut,
    )
    b4 = res["B4"]
    assert b4.verdict == "cash (surcharge unknown)"
    assert b4.cash_total_score_usd == 470.0
    assert b4.points_floor_usd == pytest.approx(200.0 + APD_Y_USD, abs=0.01)
    assert totals["legs_taxes_unknown_ids"] == ["B4"]


def test_an_off_date_finding_says_taxes_unknown(tmp_path):
    _, _, out = evaluate(
        only(B4, lambda o, d, i: [row(origin=o, dest=d, iso="2027-01-25",
                                      source="singapore", cost="10000", taxes=0,
                                      currency="USD", airlines="SQ")]),
        tmp_path, flex_days=2,
    )
    assert "Singapore Airlines KrisFlyer Y 10,000 + taxes UNKNOWN" in flat(out)


@pytest.mark.parametrize("cabin,gbp", [("J", 244.0), ("F", 244.0), ("W", 244.0)])
def test_a_premium_cabin_charges_the_standard_rate_and_the_duty_alone_settles_it(
    cabin, gbp, tmp_path
):
    res, _, _ = evaluate(
        only(B4, _rows(dict(UA_MISSING_30K, cabin=cabin))), tmp_path
    )
    b4 = res["B4"]
    assert b4.apd.total_gbp == gbp
    assert b4.verdict == "cash" and b4.surcharge_cannot_change_verdict
    assert b4.points_floor_usd == pytest.approx(300.0 + gbp * 1.354, abs=0.01)


@pytest.mark.parametrize("origin", ["MAN", "EDI", "LGW"])
def test_apd_is_added_to_the_floor_from_other_uk_airports(origin, tmp_path):
    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                leg.origin = origin

    res, _, _ = evaluate(
        lambda o, d, i: [row(origin=o, dest=d, iso=i, **UA_MISSING_30K)]
        if (o, d) == (origin, "SFO") else None,
        tmp_path, fixture_mutator=mut,
    )
    b4 = res["B4"]
    assert b4.apd_added_usd == pytest.approx(APD_Y_USD, abs=0.01)
    assert b4.points_floor_usd == pytest.approx(300.0 + APD_Y_USD, abs=0.01)


def test_unknown_apd_band_and_unknown_taxes_both_reach_the_label(tmp_path):
    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                leg.destination = "MEX"

    res, totals, out = evaluate(
        lambda o, d, i: [row(origin=o, dest=d, iso=i, **UA_MISSING_30K)] if o == "LHR" else None,
        tmp_path, fixture_mutator=mut,
    )
    text = flat(out)
    assert res["B4"].verdict != "points"
    assert "AND the departure tax is $0" in text
    assert "award TAXES on B4" in text


def test_snapshot_replay_of_zero_and_missing_tax_rows_is_unscored_with_apd(
    tmp_path, capsys, monkeypatch
):
    from docs_v5 import build_corpus, one_award_page  # type: ignore

    def pages_for(leg_id, o, d, iso):
        p = one_award_page(o, d, iso)
        if leg_id == "B4":
            r = p["data"][0]
            r["Route"]["Source"] = "united"
            r["YMileageCost"] = "30000"
            r.pop("YTotalTaxes")
            r["TaxesCurrency"] = "USD"
            r["YAirlines"] = "UA"
            q = dict(r)
            q["ID"] = "sq0"
            q["Route"] = dict(r["Route"], Source="singapore")
            q["YTotalTaxes"] = 0
            q["YMileageCost"] = "35000"
            q["YAirlines"] = "SQ"
            p["data"].append(q)
            r["Route"]["OriginRegion"] = "Europe"
            r["Route"]["DestinationRegion"] = "North America"
        return [p]

    manifest, _ = build_corpus(tmp_path, pages_for=pages_for)
    code, _, text = run_cli(
        BASE_ARGV + ["--from-snapshot", str(manifest)], lambda *a: None,
        capsys, monkeypatch, key=None,
    )
    assert code == 0
    assert "below $43.89 - after UK Air Passenger Duty of $138.11" in text
    assert re.search(r"TAXES are UNKNOWN │ 1 \(B4\)", text)
    assert re.search(r"Legs where points win │ 0 ", text)


def _replay_missing_tax_b4(tmp_path, capsys, monkeypatch):
    from docs_v5 import build_corpus, one_award_page  # type: ignore

    def pages_for(leg_id, o, d, iso):
        p = one_award_page(o, d, iso)
        if leg_id == "B4":
            r = p["data"][0]
            r["Route"]["Source"] = "united"
            r["YMileageCost"] = "30000"
            r.pop("YTotalTaxes")
            r["TaxesCurrency"] = "USD"
            r["YAirlines"] = "UA"
            r["Route"]["OriginRegion"] = "Europe"
            r["Route"]["DestinationRegion"] = "North America"
        return [p]

    manifest, _ = build_corpus(tmp_path, pages_for=pages_for)
    return run_cli(
        BASE_ARGV + ["--from-snapshot", str(manifest)], lambda *a: None,
        capsys, monkeypatch, key=None,
    )


def test_a_replayed_leg_with_unknown_taxes_prints_the_loud_not_zero_line(
    tmp_path, capsys, monkeypatch
):
    """
    PRE-EXISTING (master behaves the same), but it is the replay half of this
    branch's promise. `_print_live_scoring_block` returns early unless the leg is
    LIVE, so on --from-snapshot the line 'taxes from the API: NONE USABLE ... IT
    IS NOT $0' and the floor clause naming the added APD are never printed, and
    the per-leg provenance cell for a replayed leg with a points side reads
    'none'.
    """
    _, raw, text = _replay_missing_tax_b4(tmp_path, capsys, monkeypatch)
    assert "taxes from the API: NONE USABLE" in text
    b4 = next(l for l in raw.splitlines() if "│ B4" in l)
    assert "none |" not in b4, b4


def test_offline_badge_path_does_not_carry_the_taxes_unknown_flag(tmp_path):
    """The offline path is untouched: no fixture candidate is taxes_unknown."""
    from src.trip_loader import load_trip_fixture

    for name in ("trip_a_mry_nyc.json", "trip_b_europe.json",
                 "trip_c_lon_mry_surcharge.json"):
        fx = load_trip_fixture(ROOT / "tests" / "fixtures" / "trips" / name)
        for leg in fx.legs:
            for c in leg.points_candidates:
                assert c.taxes_unknown is False
