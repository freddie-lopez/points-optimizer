"""
Regression tests for the Tester's findings on `fix/known-failures`
(docs/test-reports/known-failures.md). One block per finding; each would fail on
the build the Tester attacked.
"""
import copy
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src.seats_client import PARSER_VERSION, SeatsClient
from tests.test_untrusted_taxes import (
    CSP,
    REAL,
    ROUTES,
    UNSET,
    _row,
    _run_trip_b,
)

ROOT = Path(__file__).parent.parent
APD_Y_USD = 138.108  # GBP 102 at the configured GBP rate, as the leg reports it


def _cli(argv, capsys, side_effect):
    from src.main import main

    SeatsClient.reset_call_budget()
    SeatsClient(api_key="k").clear_cache()
    old = sys.argv
    sys.argv = ["prog"] + argv
    try:
        with patch("src.seats_client.requests.get", side_effect=side_effect):
            code = main()
    finally:
        sys.argv = old
    return code, " ".join(capsys.readouterr().out.split())


def _resp(rows):
    r = MagicMock()
    r.json.return_value = {"data": rows}
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


# -- Finding 1 (High): search mode ranked an unknown cash cost as $0 -----------


def test_search_mode_never_ranks_or_prints_unknown_taxes_as_zero(capsys, monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")
    rows = [
        _row(source="singapore", cost="52000", taxes=15000, currency="USD", airlines="SQ"),
        _row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA"),
    ]
    rows[1]["ID"] = "ua"
    code, out = _cli(
        ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
         "--balance", "UR=160000", "--card", CSP],
        capsys, lambda *a, **k: _resp(rows),
    )
    assert "Top strategy cash cost: $56.00" in out, "the known-cash strategy ranks first"
    assert "Top strategy cash cost: $0.00" not in out
    assert "UNKNOWN" in out and "cannot be ranked on it" in out


# -- Finding 2 (High): a rejected unknown-tax alternative set the high end -------


def test_a_rejected_unknown_tax_alternative_floor_carries_apd_and_says_taxes(tmp_path):
    b4, totals, out = _run_trip_b(
        [
            dict(source="united", cost="27600", taxes=22463, currency="USD", airlines="UA"),
            dict(source="united", cost="20000", taxes=UNSET, currency="USD", airlines="UA"),
        ],
        tmp_path,
    )
    assert b4.best_points.observed_taxes_known is True, "the $224.63 award is chosen"
    assert b4.points_floor_usd == pytest.approx(200.0 + APD_Y_USD, abs=0.01)
    assert totals["legs_taxes_unknown_ids"] == ["B4"]
    assert any("20,000 pts" in w and "TAXES are UNKNOWN" in w for w in b4.warnings)
    assert "unknown award taxes add nothing beyond any UK APD shown" in out


def test_a_scored_win_beside_an_unknown_tax_alternative_is_bounded_by_the_duty(tmp_path):
    b4, _, _ = _run_trip_b(
        [
            dict(source="united", cost="20000", taxes=15000, currency="USD", airlines="UA"),
            dict(source="singapore", cost="10000", taxes=0, currency="USD", airlines="SQ"),
        ],
        tmp_path,
    )
    assert b4.verdict == "points"
    assert b4.winner_cost_low_usd >= 100.0 + APD_Y_USD - 0.01


# -- Finding 3 (High): a UK tax figure below the duty it must contain ---------


@pytest.mark.parametrize("cents", [1, 500, 13000])
def test_a_uk_tax_figure_below_the_owed_apd_is_not_believed(cents, tmp_path):
    b4, totals, _ = _run_trip_b(
        dict(source="united", cost="35000", taxes=cents, currency="USD", airlines="UA"),
        tmp_path,
    )
    assert b4.verdict != "points"
    assert b4.best_points.taxes_unknown is True
    assert "INCOMPLETE" in b4.best_points.observed_taxes_note
    assert b4.points_floor_usd == pytest.approx(350.0 + APD_Y_USD, abs=0.01)
    assert not totals["headline_is_a_range"] or totals["beat_cash_pct_low"] == 0.0


def test_a_uk_tax_figure_above_the_duty_is_still_believed(tmp_path):
    b4, _, _ = _run_trip_b(
        dict(source="united", cost="27600", taxes=22463, currency="USD", airlines="UA"),
        tmp_path,
    )
    assert b4.best_points.taxes_unknown is False


# -- Finding 4 (Medium): the parse changed, so the parser version must --------


def test_the_parser_version_moved_off_the_one_the_macs_corpus_carries():
    assert PARSER_VERSION != "2026-09-09.v5"


# -- Finding 5 (Medium): no stale pre-APD break-even anywhere -----------------


def test_no_pre_apd_break_even_survives_in_any_warning(tmp_path):
    b4, _, _ = _run_trip_b(
        dict(source="united", cost="30000", taxes=UNSET, currency="USD", airlines="UA"),
        tmp_path,
    )
    text = " ".join([b4.verdict_reason] + b4.warnings)
    assert "$182.00" not in text
    assert "$43.89" in text


# -- Finding 6 (Medium): negative taxes are corrupt, not unconvertible --------


def test_negative_taxes_put_the_duty_in_the_floor_and_blame_no_fx_rate(tmp_path):
    b4, _, out = _run_trip_b(
        dict(source="united", cost="30000", taxes=-500, currency="USD", airlines="UA"),
        tmp_path,
    )
    assert b4.best_points.taxes_unconvertible is False
    assert b4.points_floor_usd == pytest.approx(300.0 + APD_Y_USD, abs=0.01)
    assert "no FX rate" not in " ".join([b4.verdict_reason] + b4.warnings)


# -- Finding 7 (Medium): "no availability" when awards WERE returned ----------


def test_search_mode_says_none_fundable_not_no_availability(capsys, monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")
    rows = [
        _row(source="qatar", cost="33000", taxes=0, currency="USD", airlines="QR"),
        _row(source="american", cost="30000", taxes=5600, currency="USD", airlines="AA"),
    ]
    rows[1]["ID"] = "aa"
    _, out = _cli(
        ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
         "--balance", "UR=160000", "--card", CSP],
        capsys, lambda *a, **k: _resp(rows),
    )
    assert "returned no award availability" not in out
    assert "NONE of them can be funded" in out
    assert "reachable only INDIRECTLY" in out


# -- Finding 8 (Low): a taxes-only unknown is not called a surcharge ----------


def test_a_taxes_only_leg_is_labelled_taxes_and_counted_once(tmp_path):
    b4, totals, out = _run_trip_b(
        dict(source="united", cost="30000", taxes=UNSET, currency="USD", airlines="UA"),
        tmp_path,
    )
    assert "WITHHELD (taxes unknown)" in out
    assert "B4" not in totals["legs_surcharge_unknown_ids"]
    assert totals["legs_taxes_unknown_ids"] == ["B4"]
    assert "carrier-imposed surcharges that are not known" not in out.split(
        "The headline above is a RANGE"
    )[-1][:400]


# -- Finding 10 (Low): an indirect verdict must not hide an unattributed award


def test_indirect_and_unattributed_on_one_leg_are_both_counted(tmp_path):
    _, totals, out = _run_trip_b(
        [
            dict(source="qatar", cost="33000", taxes=0, currency="USD", airlines="QR"),
            dict(source="hawaiianairlines", cost="25000", taxes=4460),
        ],
        tmp_path,
    )
    assert totals["legs_indirect_path_unverified_ids"] == ["B4"]
    assert totals["legs_award_unattributed_ids"] == ["B4"]
    assert "Legs carrying an award the response did NOT" in out


# -- Finding 12 (Low): loader leftovers ---------------------------------------


def _load(tmp_path, content: bytes, name="trip.json"):
    from src.trip_loader import TripFixtureError, load_trip_fixture

    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(p)
    return str(e.value)


def _leg(**cash):
    opt = {"label": "fare", "amount": 100, **cash}
    return json.dumps({
        "id": "t", "legs": [{"id": "L1", "kind": "flight", "description": "x",
                             "date": "2027-01-15", "cash_options": [opt]}],
    }).encode()


def test_a_directory_is_one_clean_error(tmp_path):
    from src.trip_loader import TripFixtureError, load_trip_fixture

    d = tmp_path / "trip.json"
    d.mkdir()
    with pytest.raises(TripFixtureError, match="is a directory"):
        load_trip_fixture(d)


def test_non_utf8_names_the_file(tmp_path):
    assert "trip.json" in _load(tmp_path, b"\xff\xfe{}")


@pytest.mark.parametrize("cash, needle", [
    ({"currency": 5}, "not a currency code"),
    ({"amount": "1e400"}, "not a finite amount"),
    ({"amount": "NaN"}, "not a finite amount"),
    ({"date": "2027-02-30"}, "not an ISO date"),
    ({"date": "tomorrow"}, "not an ISO date"),
])
def test_bad_values_are_refused_at_load_not_at_scoring(tmp_path, cash, needle):
    msg = _load(tmp_path, _leg(**cash))
    assert needle in msg and "trip.json" in msg


# ===========================================================================
# Re-test round (docs/test-reports/known-failures.md, "Re-test")
# ===========================================================================


def _search(capsys, monkeypatch, rows, origin="SFO", dest="MAD", iso="2027-01-15",
            balance="UR=160000"):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")
    for i, r in enumerate(rows):
        r["ID"] = f"r{i}"
        r["Route"]["OriginAirport"], r["Route"]["DestinationAirport"] = origin, dest
        r["Date"], r["ParsedDate"] = iso, f"{iso}T00:00:00Z"
    return _cli(
        ["--origin", origin, "--destination", dest, "--date", iso,
         "--balance", balance, "--card", CSP],
        capsys, lambda *a, **k: _resp(rows),
    )


def test_R1_dedup_does_not_let_an_unknown_evict_a_known_award(capsys, monkeypatch):
    _, out = _search(capsys, monkeypatch, [
        _row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA"),
        _row(source="united", cost="52000", taxes=UNSET, currency="USD", airlines="UA"),
    ])
    assert "Top strategy cash cost: $56.00" in out
    assert "no strategy on this search has a known cash cost" not in out


def test_R2_search_mode_applies_the_below_duty_rule(capsys, monkeypatch):
    _, out = _search(capsys, monkeypatch, [
        _row(source="united", cost="35000", taxes=500, currency="USD", airlines="UA"),
    ], origin="LHR", dest="SFO", iso="2027-01-27")
    assert "Top strategy cash cost: $5.00" not in out
    assert "INCOMPLETE" in out


def test_R3_a_balance_shortfall_is_not_called_a_partner_finding(capsys, monkeypatch):
    _, out = _search(capsys, monkeypatch, [
        _row(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA"),
    ], balance="UR=1000")
    assert "a transfer partner, but no fundable path" in out
    assert "finding about your transfer partners" not in out


def test_R4_the_unfundable_listing_does_not_search_twice(capsys, monkeypatch):
    calls = []

    def side(*a, **k):
        calls.append(1)
        return _resp([_row(source="american", cost="30000", taxes=5600,
                           currency="USD", airlines="AA")])

    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")
    _, out = _cli(
        ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
         "--balance", "UR=160000", "--card", CSP],
        capsys, side,
    )
    assert "NONE of them can be funded" in out
    assert len(calls) == 1
    assert "no API call made" not in out


def test_R7_a_manifest_row_that_disagrees_with_its_snapshot_is_called_out(tmp_path, capsys):
    from tests.test_from_snapshot import REPLAY_BASE, build_corpus, run_cli

    manifest, snap = build_corpus(tmp_path, parser_version="2026-09-09.v5")
    # Rewrite the manifest rows to CLAIM the current parser; the snapshots still
    # say they were captured under the old one.
    manifest.write_text(manifest.read_text().replace("2026-09-09.v5", PARSER_VERSION))
    _, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert "PARSER VERSION DISAGREEMENT" in " ".join(out.split())
