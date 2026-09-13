"""
The only intended CLI output changes of the UI round: F-1 and F-3.

F-1. The per-leg table's "Best points path" cell printed `none - not a partner`
     - a claim about partnerships - on a leg whose live search FAILED and on a
     leg that was NEVER PRICED. Those cells now read `no live data` and
     `never priced`, and a never-priced leg's verdict cell reads
     `PAY CASH (never priced)` (its verdict CODE is unchanged, so the README
     verdict table and the exit codes are unchanged).
F-3. B4's verdict sentence quoted the points score BEFORE UK APD ($280.00)
     while the table, margin and totals use the score with it ($418.11). The
     sentence now quotes the scored figure and names the duty in it.

The goldens as they were before this change are kept in
tests/fixtures/cli_golden/pre_f1_f3/. This test prints the diff against them and
asserts that EVERY changed line is one of those cells or that sentence; a table
border or header line may differ only in how wide rich drew a column.
"""
import difflib
import re

import pytest

from tests import _cli_golden as g

CHANGED = ["G1", "G3", "G5", "G7", "G8"]
PRE = g.GOLDEN_DIR / "pre_f1_f3"
BOX = "┏┃┡│└┗┳╇┴┻━─┓┩┘┛"
F1_CELLS = {
    ("none - not a partner", "no live data"),
    ("none - not a partner", "never priced"),
    ("PAY CASH (no path)", "PAY CASH (never priced)"),
}
F3 = re.compile(
    r"Points path scores \$(?P<after>[\d,]+\.\d\d) vs \$(?P<cash>[\d,]+\.\d\d) cash "
    r"\(points \$(?P<before>[\d,]+\.\d\d) \+ UK APD \$(?P<apd>[\d,]+\.\d\d)\)\."
)


def _money(text):
    return float(text.replace(",", ""))


def _is_table_line(line):
    s = line.strip()
    return bool(s) and (s[0] in BOX or s == "Per-leg: cash vs points")


def _cells(line):
    if set(line.strip()) <= set(BOX + " "):
        return ["<border>"]
    return [c.strip() for c in re.split(r"[│┃]", line)]


def _allowed(old, new):
    if _is_table_line(old) and _is_table_line(new):
        a, b = _cells(old), _cells(new)
        if len(a) != len(b):
            return False
        return all(x == y or (x, y) in F1_CELLS for x, y in zip(a, b))
    m = F3.search(new)
    if m:
        before = m.group("before")
        restored = new.replace(m.group(0), f"Points path scores ${before} vs ${m.group('cash')} cash.")
        total_ok = abs(_money(m.group("after")) - _money(before) - _money(m.group("apd"))) < 0.006
        return restored == old and total_ok
    return False


@pytest.mark.parametrize("name", CHANGED)
def test_every_changed_line_is_an_F1_cell_or_the_F3_sentence(name):
    old = (PRE / f"{name}.txt").read_text().splitlines()
    new = g.golden_path(name).read_text().splitlines()
    diff = "\n".join(difflib.unified_diff(old, new, "pre_f1_f3", "now", lineterm=""))
    print(diff)
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        assert tag == "replace" and (i2 - i1) == (j2 - j1), diff
        for o, n in zip(old[i1:i2], new[j1:j2]):
            assert _allowed(o, n), f"unexpected change in {name}:\n- {o}\n+ {n}"


def test_the_goldens_not_listed_did_not_change_at_all():
    for name in g.SCENARIOS:
        if name in CHANGED:
            continue
        assert not (PRE / f"{name}.txt").exists()


def test_f1_an_api_failed_leg_and_a_never_priced_leg_claim_nothing(tmp_path, monkeypatch):
    from src.formatter import leg_table_cells, verdict_kind

    down = g.run_scenario("G5", tmp_path / "g5", monkeypatch).sink[0]
    for r in down.results:
        if r.leg.kind != "flight":
            continue
        cells = leg_table_cells(r)
        assert cells.path.text == "no live data" and cells.path.kind == "no_live_data"
        assert "not a partner" not in cells.path.text

    never = g.run_scenario("G7", tmp_path / "g7", monkeypatch).sink[0]
    for r in never.results:
        cells = leg_table_cells(r)
        assert r.verdict == "cash (no points path)"          # the CODE is unchanged
        assert cells.path.text == "never priced"
        assert cells.verdict.text == "PAY CASH (never priced)"
        assert verdict_kind(r) == "cash_qualified"


def test_f1_a_real_no_partner_leg_still_says_so(tmp_path, monkeypatch):
    from src.formatter import leg_table_cells

    run = g.run_scenario("G1", tmp_path / "g1", monkeypatch).sink[0]
    b5 = next(r for r in run.results if r.leg.id == "B5")
    assert leg_table_cells(b5).path.text == "none - not a partner"
    assert leg_table_cells(b5).verdict.text == "PAY CASH (no path)"


def test_f3_the_verdict_sentence_quotes_the_scored_figure(tmp_path, monkeypatch):
    run = g.run_scenario("G1", tmp_path / "g1", monkeypatch).sink[0]
    b4 = next(r for r in run.results if r.leg.id == "B4")
    assert b4.apd_added_usd == pytest.approx(138.11, abs=0.01)
    assert b4.verdict_reason == (
        f"Points path scores ${b4.points_total_score_usd:,.2f} vs "
        f"${b4.cash_total_score_usd:,.2f} cash (points "
        f"${b4.points_total_score_usd - b4.apd_added_usd:,.2f} + UK APD "
        f"${b4.apd_added_usd:,.2f})."
    )
    assert "$418.11 vs $482.00" in b4.verdict_reason


def test_f3_a_cash_verdict_after_apd_also_quotes_the_scored_figure():
    """The same stale figure on the other branch: 'Cash is cheaper: $C vs $P'."""
    from src.models import Leg, LegResult
    from src.optimizer import _restate_verdict_after_apd

    r = LegResult(leg=Leg(id="X", kind="flight", description="x", date=None))
    r.cash_total_score_usd = 300.0
    r.points_total_score_usd = 438.11
    r.verdict_reason = "Cash is cheaper: $300.00 vs $300.00 on points at 1.0cpp. Do NOT burn points here."
    _restate_verdict_after_apd(r, 300.0, 138.11, 0.01)
    assert r.verdict_reason == (
        "Cash is cheaper: $300.00 vs $438.11 on points at 1.0cpp "
        "(points $300.00 + UK APD $138.11). Do NOT burn points here."
    )
