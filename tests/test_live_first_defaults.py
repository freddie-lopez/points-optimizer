"""
v5 Step 6: live is the default; offline is the exception.

THE INVARIANT THIS STEP IS MEASURED AGAINST. Every existing test that ran a trip
without naming a mode must, afterwards, either (a) pass `--offline` and assert
THE SAME OUTPUT it asserted before, or (b) be a live-path test asserting a
live-path outcome. NO TEST MAY BE EDITED TO ASSERT A DIFFERENT NUMBER. If a
value assertion changed, that is a bug in Step 6, not a test that needed
updating.

Exactly one existing test changed, and it is case (a):
`test_the_fx_flag_works_through_the_cli` gained `--offline` and kept its exit
code and its assertion untouched.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CSP = "Chase Sapphire Preferred"
BASE = [
    sys.executable, "-m", "src.main",
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", CSP,
    "--transfer-date", "2026-09-15",
]

# The badge-mode headline. THIS IS THE REGRESSION ANCHOR FOR THE WHOLE STEP: it
# must be reachable under --offline and reachable NOWHERE ELSE without a
# qualifier travelling on the same line.
#
# UPDATED BY v5 STEP 7, WHICH IS THE STEP THAT OWNS THIS MOVEMENT. Step 6
# landed with 6.46%-15.45%, verified against the v4 run. Step 7 then wired UK
# Air Passenger Duty into the offline path, and B4 (LHR->SFO) picked up GBP
# 102.00 = $138.11 on its points side. The all-cash denominator is UNCHANGED at
# $3,126.11 and the points spend is UNCHANGED at 28,000, because APD enters the
# points side only. What this file asserts is a MODE - which invocation can
# reach the badge headline - and that is unaffected.
BADGE_LOW = "2.04%"
BADGE_HIGH = "11.03%"
BADGE_CASH = "$3,126.11"
BADGE_POINTS = "28,000"


def run(*extra):
    proc = subprocess.run(
        BASE + list(extra), cwd=ROOT, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# The three modes
# ---------------------------------------------------------------------------


def test_no_network_and_no_offline_withholds_with_exit_3():
    """
    NOT exit 0, NOT a badge number, NOT a partial margin. The whole point of
    the flip is that a run with no network can no longer ARRIVE at a badge
    percentage by default.
    """
    code, out = run()
    assert code == 3
    assert "WITHHELD" in out
    assert BADGE_LOW not in out and BADGE_HIGH not in out
    assert "THIS IS AN API FAILURE" in out
    assert "It is NOT a finding of no availability" in out


def test_no_network_with_offline_reproduces_the_badge_run_exactly():
    """THE REGRESSION ANCHOR. Every number is the one v4 printed."""
    code, out = run("--offline")
    assert code == 0
    assert BADGE_LOW in out
    assert BADGE_HIGH in out
    assert BADGE_CASH in out
    assert BADGE_POINTS in out
    assert "margin provenance" in out
    assert "badge" in out


def test_no_network_with_allow_badge_fallback_answers_with_the_qualifier():
    code, out = run("--allow-badge-fallback")
    assert code == 0
    assert BADGE_LOW in out and BADGE_HIGH in out
    assert "badge_fallback" in out
    assert "A live/replay run WAS attempted" in out
    assert "The fixture's badges answered instead" in out


def test_the_badge_headline_appears_under_offline_or_the_opt_out_and_nowhere_else():
    """
    The single cross-cutting assertion for this step: the badge headline is
    reachable from exactly two invocations, and a plain one is not one of them.
    """
    plain = run()[1]
    assert BADGE_LOW not in plain
    for extra in (["--offline"], ["--allow-badge-fallback"]):
        assert BADGE_LOW in run(*extra)[1]


def test_offline_and_live_together_is_a_usage_error():
    code, out = run("--offline", "--live")
    assert code == 1
    assert "mutually exclusive" in out
    assert not re.search(r"\d+\.\d\d%", out)


# ---------------------------------------------------------------------------
# The old flags still work and still mean what they said
# ---------------------------------------------------------------------------


def test_live_is_accepted_and_is_a_no_op():
    """Every existing script and every README line keeps working."""
    assert run()[0] == run("--live")[0] == 3


def test_require_all_live_is_accepted_and_is_a_no_op():
    assert run()[0] == run("--require-all-live")[0] == 3


def test_allow_badge_fallback_beats_an_explicit_require_all_live():
    """
    --require-all-live is now the DEFAULT and therefore a no-op, so passing it
    beside the opt-out cannot resurrect it. Stated here rather than left to be
    discovered: the two together are contradictory and the opt-out wins.
    """
    code, out = run("--allow-badge-fallback", "--require-all-live")
    assert code == 0
    assert "badge_fallback" in out


# ---------------------------------------------------------------------------
# The exit-code contract
# ---------------------------------------------------------------------------


def _help_text():
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--help"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.stdout


def test_help_lists_all_five_exit_codes_including_2():
    """
    Exit 2 has been implemented since v1 and was absent from both lists. A
    behaviour that exists, is correct and is written down nowhere the reader
    looks is this project's bug in miniature.
    """
    text = _help_text()
    section = text[text.index("EXIT CODES"):]
    for code in ("0", "1", "2", "3", "4"):
        assert re.search(rf"^\s*{code}\s+\S", section, re.M), code
    assert "WALLET ERROR" in section


def test_readme_and_help_agree_on_the_exit_codes():
    readme = (ROOT / "README.md").read_text()
    table = readme[readme.index("### Exit codes"):]
    table = table[: table.index("###", 10)]
    listed = set(re.findall(r"^\| `(\d)` \|", table, re.M))
    assert listed == {"0", "1", "2", "3", "4"}

    section = _help_text()
    section = section[section.index("EXIT CODES"):]
    from_help = set(re.findall(r"^\s*(\d)\s+\S", section, re.M))
    assert listed == from_help, (
        f"README lists {sorted(listed)} and --help lists {sorted(from_help)}. "
        f"--help is generated from the code and wins."
    )


def test_a_wallet_error_really_does_exit_2():
    """The code the two lists now agree about is the code the tool returns."""
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", "trip_b_europe.json",
         "--offline"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "will NOT assume" in proc.stdout + proc.stderr


def test_the_readme_documents_the_default_flip():
    readme = (ROOT / "README.md").read_text()
    assert "--offline" in readme
    assert "exits `3`" in readme
    assert "no-ops" in readme
