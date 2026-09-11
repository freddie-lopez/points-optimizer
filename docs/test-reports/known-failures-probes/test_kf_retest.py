"""
Re-test of 278332e ("Tester round: unknown taxes never ranked, never floor-less,
never hidden behind a surcharge label"). Attacks on what the fixes might BREAK,
plus the places the same defects survive on surfaces the fixes did not reach.

RED = defect present. Every probe is offline (stubbed `requests.get`).
"""
import re
from datetime import date

import pytest

from conftest import BASE_ARGV, ROOT, UNSET, evaluate, flat, only, row, run_cli

B4 = ("LHR", "SFO")
APD_Y = 138.108   # GBP 102 reduced rate x 1.354
APD_J = 330.376   # GBP 244 standard rate x 1.354
SEARCH = ["--date", "2027-01-15", "--balance", "UR=160000",
          "--card", "Chase Sapphire Preferred"]


def _rows(*specs):
    return lambda o, d, i: [row(origin=o, dest=d, iso=i, **s) for s in specs]


def _search(o, d, rows, capsys, monkeypatch, balance=None):
    argv = ["--origin", o, "--destination", d] + SEARCH
    if balance:
        argv[argv.index("UR=160000")] = balance
    return run_cli(argv, lambda *_: rows, capsys, monkeypatch)


def _mut(**leg_over):
    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                for k, v in leg_over.items():
                    setattr(leg, k, v)
    return mut


# ===========================================================================
# RED - defects present on 278332e
# ===========================================================================


def test_R1_search_dedup_does_not_drop_a_known_cash_award_for_an_unknown_one(capsys, monkeypatch):
    """
    `optimize()` keeps ONE strategy per program/date/cabin, the one with the lower
    `total_value` - and an unknown cash cost still counts as $0 there. United
    50,000 + $56.00 (known, $556) loses to United 52,000 with NO tax figure
    ($520 on a $0). The known award disappears; the summary then says 'no
    strategy on this search has a known cash cost' - false: one did, and it
    needed 2,000 FEWER points. The sort was fixed; the dedup above it was not.
    """
    code, raw, text = _search("SFO", "MAD", [
        row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA", rid="a"),
        row(source="united", cost="52000", taxes=UNSET, currency="USD", airlines="UA", rid="b"),
    ], capsys, monkeypatch)
    assert code == 0
    assert "50,000" in raw, "the known-cash 50,000-point United award was dropped"
    assert "no strategy on this search has a known cash cost" not in text


def test_R2_search_mode_applies_the_below_uk_duty_rule(capsys, monkeypatch):
    """
    Finding 3 was fixed in live_trip.award_to_candidate - the TRIP path. Search
    mode builds strategies straight from Awards, so LHR-SFO United 35,000 with
    $5.00 of 'taxes' is ranked #1 as a KNOWN $5.00 / $355.00 and the summary says
    'Top strategy cash cost: $5.00', while GBP 102 of APD is owed on it.
    """
    code, raw, text = _search("LHR", "SFO", [
        row(source="united", cost="35000", taxes=500, currency="USD", airlines="UA",
            origin="LHR", dest="SFO"),
    ], capsys, monkeypatch)
    assert code == 0
    assert "Top strategy cash cost: $5.00" not in text


def test_R3_search_no_fundable_award_does_not_blame_partners_for_a_balance(capsys, monkeypatch):
    """
    United IS a UR partner; the wallet holds 1,000 UR against 50,000 needed. The
    header says 'That is a finding about your transfer partners' - a claim about
    partnerships built from a balance shortfall (the M-5 shape).
    """
    code, _, text = _search("SFO", "MAD", [
        row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA"),
    ], capsys, monkeypatch, balance="UR=1000")
    assert code == 0
    assert "NONE of them can be funded" in text, "precondition"
    assert "finding about your transfer partners" not in text


def test_R4_search_coverage_line_does_not_say_no_api_call_on_a_run_that_made_one(capsys, monkeypatch):
    """
    To list the unfundable awards, `run_search` calls `seats_client.search()` a
    SECOND time; the in-process cache answers it (good - one HTTP call), but the
    call resets `last_pagination_note`, so the run's coverage line becomes
    'served from the IN-PROCESS cache; no API call made' on a run that made one.
    """
    code, _, text = _search("SFO", "MAD", [
        row(source="qatar", cost="30000", taxes=0, currency="USD", airlines="QR", rid="q"),
        row(source="american", cost="25000", taxes=5600, currency="USD", airlines="AA", rid="a"),
    ], capsys, monkeypatch)
    assert code == 0
    assert "no API call made" not in text


def test_R5_the_v5_probe_conftest_scrubs_the_key_files_before_importing_src():
    """
    Fix 11 moves the key files away when tests/conftest.py is IMPORTED. The v5
    probe conftest imports `src.response_cache` and `src.seats_client` (which
    import `src.config`, which runs `load_env()` on the real repo .env and the
    real ~/.config/.env) BEFORE it imports tests.conftest. Verified on the
    simulated Mac: at collection of docs/test-reports/v5-probes,
    `config._ENV_INJECTED` holds the repo .env key; under tests/ and
    adversarial-probes it is empty.
    """
    text = (ROOT / "docs" / "test-reports" / "v5-probes" / "conftest.py").read_text()
    first_src = min(m.start() for m in re.finditer(r"^from src\b|^import src\b", text, re.M))
    harness = text.find("from tests.conftest import")
    assert harness != -1 and harness < first_src


def test_R6_an_off_date_finding_does_not_print_a_below_duty_figure_as_a_clean_price(tmp_path):
    """
    The promoted award on Jan 26 with $5.00 is (correctly) UNKNOWN. An award on
    Jan 25 from the same response, same leg, same $5.00, is an advisory finding
    and renders '30,000 + USD 5.00' - `_finding` reads the parser's view, not
    the below-duty rule.
    """
    from src.models import CashOption

    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                leg.cash_options.append(CashOption(
                    label="$470 UA Jan 26", amount=470.0, currency="USD",
                    date=date(2027, 1, 26)))

    res, _, out = evaluate(
        only(B4, lambda o, d, i: [
            row(source="united", cost="35000", taxes=500, currency="USD",
                airlines="UA", origin=o, dest=d, iso="2027-01-26"),
            row(source="united", cost="30000", taxes=500, currency="USD",
                airlines="UA", origin=o, dest=d, iso="2027-01-25", rid="off"),
        ]),
        tmp_path, flex_days=2, fixture_mutator=mut,
    )
    assert res["B4"].best_points.taxes_unknown, "precondition: the promoted one is unknown"
    assert "United MileagePlus Y 30,000 + USD 5.00" not in flat(out)


def test_R7_a_manifest_row_and_its_snapshot_disagreeing_on_the_parser_is_flagged(
    tmp_path, capsys, monkeypatch
):
    """
    PRE-EXISTING (v5 design). The banner reads the parser version from the
    MANIFEST ROW; the per-leg replay clause reads it from the SNAPSHOT's _meta.
    A row saying the current parser over a snapshot saying 2026-09-09.v5
    replays with no REPARSED banner while the same output says 'parsed at
    capture by 2026-09-09.v5'. `verify()` does not compare the two.
    """
    from docs_v5 import build_corpus  # type: ignore

    manifest, _ = build_corpus(
        tmp_path, meta_extra=lambda leg: {"parser_version": "2026-09-09.v5"}
    )
    code, _, text = run_cli(
        BASE_ARGV + ["--from-snapshot", str(manifest)], lambda *a: None,
        capsys, monkeypatch, key=None,
    )
    assert "parsed at capture by 2026-09-09.v5" in text, "precondition"
    assert code == 1 or "REPARSED" in text


# ===========================================================================
# GREEN - what the fixes did not break
# ===========================================================================


@pytest.mark.parametrize("per_pax_cents,unknown", [(15000, False), (10000, True)])
def test_the_below_duty_rule_is_per_passenger_on_a_two_traveller_leg(per_pax_cents, unknown, tmp_path):
    res, _, _ = evaluate(
        only(B4, _rows(dict(source="united", cost="30000", taxes=per_pax_cents,
                            currency="USD", airlines="UA"))),
        tmp_path, fixture_mutator=_mut(travelers=2),
    )
    b4 = res["B4"]
    assert b4.best_points.taxes_unknown is unknown
    if unknown:
        assert b4.apd_added_usd == pytest.approx(2 * APD_Y, abs=0.01)


@pytest.mark.parametrize("cents,unknown", [(22463, True), (40000, False)])
def test_the_below_duty_rule_uses_the_j_cabin_standard_rate(cents, unknown, tmp_path):
    res, _, _ = evaluate(
        only(B4, _rows(dict(source="united", cost="60000", taxes=cents,
                            currency="USD", airlines="UA", cabin="J"))),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.best_points.taxes_unknown is unknown
    assert b4.verdict != "points"
    if unknown:
        assert b4.points_floor_usd == pytest.approx(600.0 + APD_J, abs=0.01)


@pytest.mark.parametrize("cents,unknown", [(10200, False), (10199, True)])
def test_the_below_duty_boundary_is_exact_in_gbp(cents, unknown, tmp_path):
    res, _, _ = evaluate(
        only(B4, _rows(dict(source="united", cost="30000", taxes=cents,
                            currency="GBP", airlines="UA"))),
        tmp_path,
    )
    assert res["B4"].best_points.taxes_unknown is unknown


@pytest.mark.parametrize("origin", ["MAN", "EDI", "LGW"])
def test_the_below_duty_rule_fires_from_other_uk_airports(origin, tmp_path):
    res, _, _ = evaluate(
        lambda o, d, i: [row(origin=o, dest=d, iso=i, source="united", cost="35000",
                             taxes=500, currency="USD", airlines="UA")]
        if (o, d) == (origin, "SFO") else None,
        tmp_path, fixture_mutator=_mut(origin=origin),
    )
    b4 = res["B4"]
    assert b4.best_points.taxes_unknown
    assert b4.verdict == "cash" and b4.points_floor_usd == pytest.approx(350 + APD_Y, abs=0.01)


def test_a_promoted_off_date_below_duty_award_is_unknown_with_apd_in_the_floor(tmp_path):
    from src.models import CashOption

    def mut(fx):
        for leg in fx.legs:
            if leg.id == "B4":
                leg.cash_options.append(CashOption(
                    label="$470", amount=470.0, currency="USD", date=date(2027, 1, 26)))

    res, _, _ = evaluate(
        only(B4, lambda o, d, i: [row(source="united", cost="35000", taxes=500,
                                      currency="USD", airlines="UA", origin=o,
                                      dest=d, iso="2027-01-26")]),
        tmp_path, flex_days=2, fixture_mutator=mut,
    )
    b4 = res["B4"]
    assert b4.cash_total_score_usd == 470.0
    assert b4.points_floor_usd == pytest.approx(350 + APD_Y, abs=0.01)
    assert b4.verdict == "cash"


def test_a_realistic_live_tax_figure_still_states_apd_without_adding_it(tmp_path, capsys, monkeypatch):
    """
    The v5 probe `test_a_live_leg_states_apd_without_adding_it` went red on
    278332e because its corpus puts Aeroplan's CAD 44.60 ($32.36) on the LHR
    departure - below the duty, so now (by design) UNKNOWN with APD added. The
    property it guards still holds for a figure that can contain the duty.
    """
    from docs_v5 import build_corpus, one_award_page  # type: ignore

    def pages_for(leg_id, o, d, iso):
        p = one_award_page(o, d, iso)
        if leg_id == "B4":
            r = p["data"][0]
            r["Route"]["Source"] = "united"
            r["YMileageCost"] = "27600"
            r["YTotalTaxes"] = 22463
            r["TaxesCurrency"] = "USD"
            r["YAirlines"] = "UA"
        return [p]

    manifest, _ = build_corpus(tmp_path, pages_for=pages_for)
    code, _, text = run_cli(BASE_ARGV + ["--from-snapshot", str(manifest)],
                            lambda *a: None, capsys, monkeypatch, key=None)
    assert code == 0
    assert "IT IS NOT ADDED HERE" in text and "NOBODY HAS CHECKED" in text


def test_a_rejected_unknown_tax_alternative_high_end_carries_apd_and_names_taxes(tmp_path):
    res, totals, out = evaluate(
        only(B4, _rows(
            dict(source="united", cost="20000", taxes=15000, currency="USD", airlines="UA"),
            dict(source="singapore", cost="10000", taxes=0, currency="USD", airlines="SQ"),
            dict(source="united", cost="12000", taxes=900000, currency="MXN",
                 airlines="UA", rid="mx"),
        )),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.verdict == "points"
    assert b4.winner_cost_low_usd == pytest.approx(100 + APD_Y, abs=0.01)
    assert totals["legs_taxes_unknown_ids"] == ["B4"]
    assert "high end = only if unknown award taxes add nothing beyond any UK APD shown" in flat(out)


def test_search_mixed_known_and_unknown_known_first_unknown_never_zero(capsys, monkeypatch):
    code, raw, text = _search("SFO", "MAD", [
        row(source="singapore", cost="52000", taxes=15000, currency="USD", airlines="SQ", rid="sq"),
        row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA", rid="ua"),
        row(source="aeroplan", cost="30000", taxes=UNSET, currency="USD", airlines="AC", rid="ac"),
    ], capsys, monkeypatch)
    ranks = [l for l in raw.splitlines() if re.match(r"^│\s*\d\s", l)]
    assert "United" in ranks[0]
    assert all("$0.00" not in l for l in ranks)
    assert "Top strategy cash cost: $56.00" in text
