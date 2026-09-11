"""
Tester probes: an Avios-family award (qatar, finnair) is 'indirect, not scored'
and is NEVER reported as 'not a partner / no points path'.
"""
import re

import pytest

from conftest import BASE_ARGV, UNSET, evaluate, flat, only, row, run_cli

B4 = ("LHR", "SFO")
B1 = ("SFO", "MAD")
NO_PATH_CLAIMS = re.compile(
    r"not a partner|no points path|NO UR PATH|NO CHASE UR PATH|PAY CASH \(no path\)"
    r"|not a transfer partner|not a Chase UR",
    re.IGNORECASE,
)


def _lines_about(text, program):
    return [line for line in text.splitlines() if program in line]


def _claims_about(raw, program):
    """Every table cell / sentence (from the RAW output) that names `program`."""
    out = []
    for line in raw.splitlines():
        for cell in line.split("│"):
            for sentence in re.split(r"(?<=[.:])\s", cell):
                if program in sentence:
                    out.append(sentence)
    return out


def _mixed_b4(o, d, i):
    return [
        row(source="qatar", cost="33000", taxes=0, currency="USD",
            airlines="BA, QR", origin=o, dest=d, iso=i, rid="qr"),
        row(source="american", cost="30000", taxes=5600, currency="USD",
            airlines="AA", origin=o, dest=d, iso=i, rid="aa"),
        row(source="", cost="25000", taxes=5600, currency="USD",
            airlines="UA", origin=o, dest=d, iso=i, rid="unnamed"),
    ]


# ===========================================================================
# GREEN - attacks that did not break it
# ===========================================================================


def test_qatar_mixed_with_a_non_partner_and_an_unattributed_award(tmp_path):
    res, totals, out = evaluate(only(B4, _mixed_b4), tmp_path)
    b4 = res["B4"]
    assert b4.verdict == "cash (indirect path not scored)"
    assert "B4" not in totals["legs_no_partner_ids"]
    assert totals["legs_indirect_path_unverified_ids"] == ["B4"]
    for line in _lines_about(out, "Qatar"):
        for sentence in re.split(r"(?<=\.)\s", line):
            if "Qatar" in sentence:
                assert not NO_PATH_CLAIMS.search(sentence), sentence
    b4_row = next(line for line in out.splitlines() if "│ B4" in line)
    assert "Qatar Privilege Club (indirect, not scored)" in b4_row
    assert "not a partner" not in b4_row


@pytest.mark.parametrize("source,program", [("qatar", "Qatar Privilege Club"),
                                            ("finnair", "Finnair Plus")])
def test_the_whole_cli_never_says_no_path_about_an_avios_family_program(
    source, program, capsys, monkeypatch
):
    code, raw, text = run_cli(
        BASE_ARGV,
        only(B1, lambda o, d, i: [row(source=source, cost="30000", taxes=5600,
                                      currency="USD", airlines="AY", origin=o,
                                      dest=d, iso=i)]),
        capsys, monkeypatch,
    )
    assert code == 0
    claims = _claims_about(raw, program)
    assert claims, "precondition: the program is named in the output"
    for sentence in claims:
        assert not NO_PATH_CLAIMS.search(sentence), sentence
    assert "Legs with NO points path at all (no partner exists): B1" not in text
    assert "Legs with an award reachable only INDIRECTLY" in text


def test_an_indirect_award_never_enters_the_score_even_when_it_is_cheapest(tmp_path):
    res, totals, _ = evaluate(
        only(B4, lambda o, d, i: [
            row(source="qatar", cost="5000", taxes=0, currency="USD",
                airlines="QR", origin=o, dest=d, iso=i, rid="qr"),
            row(source="united", cost="27600", taxes=22463, currency="USD",
                airlines="UA", origin=o, dest=d, iso=i, rid="ua"),
        ]),
        tmp_path,
    )
    b4 = res["B4"]
    assert b4.best_points.program == "United MileagePlus"
    assert b4.points_floor_usd is None or b4.points_floor_usd > 100
    assert b4.winner_cost_low_usd == b4.cash_total_score_usd  # 504.63 loses to 482
    # RE-TEST (278332e): this assertion used to demand verdict-based counting
    # (== 0). Finding 10 argued the opposite - count by reason code, because a
    # leg can carry an indirect award whatever its verdict - and the fix did
    # exactly that. B4 DOES carry an award reachable only indirectly, so the
    # trip row "Legs with an award reachable only INDIRECTLY - NOT scored: 1
    # (B4)" is true. What this probe is about is that the award is never SCORED.
    assert totals["legs_indirect_path_unverified_ids"] == ["B4"]


def test_an_off_date_qatar_award_is_a_finding_not_a_no_path_claim(tmp_path):
    res, totals, out = evaluate(
        only(B4, lambda o, d, i: [row(source="qatar", cost="33000", taxes=0,
                                      currency="USD", airlines="QR", origin=o,
                                      dest=d, iso="2027-01-25")]),
        tmp_path, flex_days=2,
    )
    for sentence in re.split(r"(?<=\.)\s", flat(out)):
        if "Qatar" in sentence:
            assert not NO_PATH_CLAIMS.search(sentence), sentence
    assert "B4" not in totals["legs_no_partner_ids"]


# ===========================================================================
# RED - defects still present
# ===========================================================================


def test_an_unattributed_award_beside_a_qatar_award_is_still_counted_at_trip_level(tmp_path):
    """
    `elif indirect_candidates:` is checked before `elif unattributed_candidates:`
    and `legs_award_unattributed` counts VERDICTS, so a leg carrying a
    PROGRAM_UNATTRIBUTED reason (a 25,000-point award the response did not
    name - which could be a direct UR partner, cheaper than the Qatar one)
    reaches the trip block only as 'indirect'. The unattributed award is in
    the leg's warnings and nowhere at trip level.
    """
    res, totals, _ = evaluate(only(B4, _mixed_b4), tmp_path)
    assert any(r.code == "PROGRAM_UNATTRIBUTED" for r in res["B4"].reasons)
    assert "B4" in totals["legs_award_unattributed_ids"]


def test_search_mode_does_not_call_a_qatar_award_no_award_availability(capsys, monkeypatch):
    """
    PRE-EXISTING (master prints the same), on the surface this branch did not
    touch. `--origin/--destination/--date` with Seats.aero returning a Qatar
    award (indirect UR path) and an American award (no UR path): `optimize()`
    finds no fundable path, returns [], and `run_search` prints 'Seats.aero
    returned no award availability for this route and date range.' Seats.aero
    returned TWO awards. The indirect path is not mentioned anywhere.
    """
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"]
    rows = [row(source="qatar", cost="30000", taxes=0, currency="USD",
                airlines="QR", rid="qr"),
            row(source="american", cost="25000", taxes=5600, currency="USD",
                airlines="AA", rid="aa")]
    code, _, text = run_cli(argv, lambda o, d, i: rows, capsys, monkeypatch)
    assert code == 0
    assert "returned no award availability" not in text
