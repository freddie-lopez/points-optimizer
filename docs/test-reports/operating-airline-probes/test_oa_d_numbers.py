"""
D. Numbers that must not move, and numbers that must move exactly once.

With the COMMITTED header-only data/yq_inclusion.csv, no lookup outcome may move
a verdict, score, floor, break-even, trip range, margin, withholding or exit
code. Under a TEMP YQ table: includes_yq never adds a band, excludes_yq adds
exactly one, and untrusted taxes win over both.
"""
import json
from datetime import date
from io import StringIO

import pytest
from rich.console import Console

from conftest import (  # noqa: F401
    BASE, B4_VS, CSP, TRIP_B, Stub, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src import yq_inclusion
from src.models import MetalStatus
from src.yq_inclusion import YqInclusionError, YqVerdict
from tests import _trips_payloads as tp

FIELDS = (
    "cash_usd", "points_required", "points_surcharge_usd", "points_total_score_usd",
    "cash_total_score_usd", "verdict", "points_absence", "margin_usd", "margin_pct",
    "has_points_path", "break_even_points", "points_score_low_usd", "points_score_high_usd",
    "verdict_sensitive", "break_even_surcharge_usd", "points_floor_usd",
    "surcharge_cannot_change_verdict", "apd_added_usd", "apd_unknown_withheld",
    "observed_taxes_usd", "mandatory_fees_usd",
)


def snapshot(results, totals):
    legs = {}
    for leg, r in results.items():
        legs[leg] = tuple(getattr(r, f) for f in FIELDS) + (
            r.best_points.label if r.best_points else None,
            (r.surcharge.is_known, r.surcharge.amount_low, r.surcharge.amount_high) if r.surcharge else None,
        )
    tot = {k: v for k, v in totals.items() if not k.startswith("legs_metal")}
    return legs, json.dumps(tot, sort_keys=True, default=str)


def seg(flight, o="LHR", d="SFO", order=1):
    return tp.segment(flight, o, d, order, AvailabilityID=B4_VS)


OUTCOMES = {
    "known_vs": tp.payload([vs_itinerary(B4_VS)]),
    "known_dl": tp.payload([vs_itinerary(B4_VS, "DL41")]),
    "known_vs_dl": tp.payload([tp.trip([seg("VS19", "LHR", "JFK", 1), seg("DL41", "JFK", "SFO", 2)],
                                       availability_id=B4_VS, cost=60000)]),
    "ambiguous": tp.payload([vs_itinerary(B4_VS), vs_itinerary(B4_VS, "DL41", trip_id="t2")]),
    "404": (404, None),
    "empty": tp.payload([]),
    "nomatch": tp.payload([vs_itinerary(B4_VS, cost=1)]),
    "trip_taxes_huge": tp.payload([vs_itinerary(B4_VS, taxes=10 ** 9)]),
    "trip_taxes_zero": tp.payload([vs_itinerary(B4_VS, taxes=0)]),
}


@pytest.mark.parametrize("airlines", ["VS, DL", "VS"])
@pytest.mark.parametrize("name", sorted(OUTCOMES))
@pytest.mark.parametrize("mode", ["auto", "all"])
def test_with_the_committed_table_no_lookup_outcome_moves_a_number(name, mode, airlines):
    base = snapshot(*evaluate(Stub(rows_for=vs_b4_rows(airlines=airlines)), trips_mode=None)[:2])
    got = snapshot(*evaluate(Stub(rows_for=vs_b4_rows(airlines=airlines), trips={B4_VS: OUTCOMES[name]}),
                             trips_mode=mode)[:2])
    assert got[0] == base[0]
    assert got[1] == base[1]


@pytest.mark.parametrize("name", ["known_vs", "404", "ambiguous"])
def test_the_exit_code_and_headline_do_not_move(name, capsys, monkeypatch):
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: OUTCOMES[name]})
    code_on, out_on, fl_on, _ = run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch, stub=stub)
    code_off, out_off, fl_off, _ = run_cli(BASE + ["--allow-badge-fallback", "--trips", "off"], capsys,
                                           monkeypatch, stub=Stub(rows_for=vs_b4_rows()))
    assert code_on == code_off
    pick = lambda fl: [l for l in fl.split("│") if "Saving" in l or "beats paying cash" in l]  # noqa: E731
    assert pick(fl_on) == pick(fl_off)


def _party_file(tmp_path):
    data = json.loads(TRIP_B.read_text())
    for leg in data["legs"]:
        if leg["id"] == "B4":
            leg["travelers"] = 2
    p = tmp_path / "trip_b_europe.json"
    p.write_text(json.dumps(data))
    return p


def test_a_couple_trip_is_still_withheld_with_trips_on(tmp_path, capsys, monkeypatch):
    p = _party_file(tmp_path)
    argv = ["--trip-fixture", str(p), "--balance", "UR=160000", "--card", CSP,
            "--transfer-date", "2026-09-15", "--allow-badge-fallback", "--trips", "all"]
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: OUTCOMES["known_vs"]})
    code, out, fl, stub = run_cli(argv, capsys, monkeypatch, stub=stub)
    assert code == 3, code
    assert "WITHHELD" in fl
    assert "cash (multi-traveller points not priced)" in fl.lower()


def test_a_couple_leg_that_can_never_be_scored_spends_no_trips_call(tmp_path, capsys, monkeypatch):
    """
    B4 with 2 travellers is refused scoring ("multi-traveller points not
    priced") whatever its metal. Looking up its operating airline in auto
    mode spends a Seats.aero call on an answer that cannot change anything.
    """
    p = _party_file(tmp_path)
    argv = ["--trip-fixture", str(p), "--balance", "UR=160000", "--card", CSP,
            "--transfer-date", "2026-09-15", "--allow-badge-fallback"]
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: OUTCOMES["known_vs"]})
    code, out, fl, stub = run_cli(argv, capsys, monkeypatch, stub=stub)
    assert stub.trips_calls == [], stub.trips_calls


# ---------------------------------------------------------------------------
# Temp YQ tables
# ---------------------------------------------------------------------------

EVID = "docs/yq-checks/2026-09-10-virginatlantic.md"


def table(which, source="virginatlantic"):
    return {source: YqVerdict(source, which, date(2026, 9, 10), EVID)}


def run_yq(which, trips, airlines="VS, DL", **row_kw):
    stub = Stub(rows_for=vs_b4_rows(airlines=airlines, **row_kw), trips={B4_VS: trips})
    results, totals, text, opts, fx = evaluate(stub, yq_table=table(which))
    return results["B4"], totals, text


@pytest.mark.parametrize("airlines", ["VS, DL", "VS"])
@pytest.mark.parametrize("name", ["known_vs", "404", "ambiguous", "known_dl"])
def test_includes_yq_never_adds_a_band(name, airlines):
    b4, totals, text = run_yq("includes_yq", OUTCOMES[name], airlines=airlines)
    cand = b4.best_points
    assert b4.points_total_score_usd == pytest.approx(b4.funding_plan.score_usd + cand.observed_taxes_usd)
    assert b4.points_score_low_usd == pytest.approx(b4.points_score_high_usd)
    assert b4.apd_added_usd == 0.0


def test_excludes_yq_adds_exactly_one_band_on_known_single_metal():
    b4, totals, text = run_yq("excludes_yq", OUTCOMES["known_vs"])
    base = b4.funding_plan.score_usd + b4.best_points.observed_taxes_usd
    assert (b4.points_score_low_usd - base, b4.points_score_high_usd - base) == pytest.approx((200.0, 350.0))


@pytest.mark.parametrize("name", ["ambiguous", "known_vs_dl"])
def test_excludes_yq_on_carriers_with_different_bands_stays_unknown(name):
    b4, totals, text = run_yq("excludes_yq", OUTCOMES[name])
    assert b4.points_total_score_usd == float("inf")
    assert "SURCHARGE_UNKNOWN" in {r.code for r in b4.reasons}


@pytest.mark.parametrize("which", ["includes_yq", "excludes_yq"])
@pytest.mark.parametrize("taxes", [0, 1, -5, 500])
def test_untrusted_taxes_win_over_every_verdict(which, taxes):
    b4, totals, text = run_yq(which, OUTCOMES["known_vs"], taxes=taxes)
    assert b4.points_total_score_usd == float("inf")
    assert "B4" in totals["legs_taxes_unknown_ids"]


@pytest.mark.parametrize("which", ["includes_yq", "excludes_yq"])
def test_unconvertible_currency_wins_over_every_verdict(which):
    b4, totals, text = run_yq(which, OUTCOMES["known_vs"], currency="XAF")
    assert b4.points_total_score_usd == float("inf")


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------


def _record(root, name, source):
    d = root / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"# yq-check record: {source}, 2026-09-10\n\n## Seats.aero\n- program: x (source {source})\n"
        f"\n## site\n- verdict (includes_yq / excludes_yq / inconclusive): includes_yq\n"
    )
    return f"docs/yq-checks/{name}"


def test_a_record_for_one_source_cannot_back_a_verdict_for_another(tmp_path):
    """
    The plan's undercount guard: no source leaves case E "without a row backed
    by evidence". The loader checks that the evidence file exists, is inside
    docs/yq-checks/, has the marker and no blanks - but not that it is a record
    FOR THIS SOURCE. Copying the Virgin row to add Flying Blue (the plan's
    rejected option (b), "generalise one check to every source") loads cleanly
    and moves Flying Blue scores.
    """
    ev = _record(tmp_path, "2026-09-10-virginatlantic.md", "virginatlantic")
    csv = tmp_path / "yq.csv"
    csv.write_text(
        "source,verdict,verified_on,evidence,notes\n"
        f"virginatlantic,includes_yq,2026-09-10,{ev},JFK-LHR J\n"
        f"flyingblue,includes_yq,2026-09-10,{ev},copied\n"
    )
    with pytest.raises(YqInclusionError):
        yq_inclusion.load(csv, today=date(2026, 9, 11), root=tmp_path)


def test_the_committed_table_is_still_header_only():
    assert yq_inclusion.load() == {}
    assert (yq_inclusion.YQ_TABLE_PATH.read_text().strip()
            == "source,verdict,verified_on,evidence,notes")


@pytest.mark.parametrize("line", [
    "qatar,includes_yq,2026-09-10,{ev},x",
    "virginatlantic,unverified,2026-09-10,{ev},x",
    "virginatlantic,includes_yq,2099-01-01,{ev},x",
    "notasource,includes_yq,2026-09-10,{ev},x",
    "virginatlantic,includes_yq,2026-09-10,docs/yq-checks/../../README.md,x",
    "virginatlantic,includes_yq,2026-09-10,README.md,x",
    "virginatlantic,includes_yq,2026-09-10,/etc/passwd,x",
    "virginatlantic,includes_yq,2026-09-10,docs/yq-checks/README.md,x",
])
def test_the_validator_refuses_bad_rows(tmp_path, line):
    ev = _record(tmp_path, "2026-09-10-virginatlantic.md", "virginatlantic")
    (tmp_path / "docs" / "yq-checks" / "README.md").write_text("yq-check records ____\n")
    (tmp_path / "README.md").write_text("yq-check record\n")
    csv = tmp_path / "yq.csv"
    csv.write_text("source,verdict,verified_on,evidence,notes\n" + line.format(ev=ev) + "\n")
    with pytest.raises(YqInclusionError):
        yq_inclusion.load(csv, today=date(2026, 9, 11), root=tmp_path)


# ---------------------------------------------------------------------------
# Alternatives derived from an unverified parse
# ---------------------------------------------------------------------------


def test_an_alternative_built_from_trips_metal_carries_the_unverified_caveats():
    """
    `find_same_metal_alternatives` now runs on a KNOWN single-carrier lookup
    (Step 11). Its line - "<program> can ticket AF metal and carries $X" - is a
    claim derived from the trips parse, printed in the alternatives section,
    with neither the UNVERIFIED parser label nor "by flight number / marketing
    carrier". Every other parse-derived line carries both.
    """
    from src.formatter import print_alternatives

    stub = Stub(rows_for=vs_b4_rows(airlines="VS, AF, DL"),
                trips={B4_VS: tp.payload([vs_itinerary(B4_VS, "AF83")])})
    results, totals, text, opts, fx = evaluate(stub)
    b4 = results["B4"]
    assert b4.best_points.metal.status is MetalStatus.KNOWN
    assert b4.alternatives, "precondition: the KNOWN AF metal produced an alternative"
    buf = StringIO()
    print_alternatives(list(results.values()), console=Console(file=buf, width=250))
    out = flat(buf.getvalue())
    assert "UNVERIFIED" in out and "flight number" in out, out
