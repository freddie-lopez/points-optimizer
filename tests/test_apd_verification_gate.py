"""
v5 Step 8: the APD verification run. A GATE that runs on Tsuki's Mac.

THE QUESTION THIS FILE EXISTS TO CLOSE, AND WHY IT IS STILL OPEN.

Step 7 wired UK Air Passenger Duty into the OFFLINE path and only FLAGGED it on
live and replayed legs. The asymmetry is deliberate and it rests on one
unanswered empirical question:

    Does Seats.aero's `{X}TotalTaxes` already include UK APD?

If it does and v5 had added APD on top, every UK departure would be
double-counted. If it does not and v5 had said nothing, every live UK departure
understates by GBP 102 or more. Nobody has checked, because there has never been
a live LHR-departure row in this project's corpus. Until one arrives, the tool
states the amount without applying it, which is the only answer that is not a
guess in one direction or the other.

THE RUN, exactly as Tsuki should execute it:

    python -m src.main --new-trip apd_probe \\
        --leg LHR:SFO:<a date within 330 days>:500 \\
        --live --balance UR=160000 --card "Chase Sapphire Preferred"

Then, on the resulting live row for a Y cabin:

  1. Read the reported `TotalTaxes` and its currency off the per-leg detail.
  2. Open the SAME route, date and cabin on a real award booking page - BA.com
     award search, or AA/Iberia for the same metal - and read the
     taxes-and-carrier-charges breakdown.
  3. Record which of these is true, in
     docs/research/surcharge-and-apd-data.md, with the date and the screenshot:

       (a) TotalTaxes >= GBP 102 AND the booking page attributes ~GBP 102 to APD
           -> Seats.aero INCLUDES it -> v6 removes the flag and adds nothing.
       (b) TotalTaxes is materially below GBP 102
           -> it does NOT include it -> v6 adds APD to live legs too and the
              flag becomes a real charge.
       (c) inconclusive -> the flag STAYS and says so.

THERE IS NO FOURTH OUTCOME IN WHICH THE FLAG IS QUIETLY DROPPED. The gate below
is what enforces that: it skips until the research document records an answer,
and it asserts the answer is one of the three and carries its evidence.

This is the same shape as the v3 Step 10 gate in tests/test_live_trip_b.py, and
it is a GATE rather than a failure for the same reason: the build sandbox has no
network egress, and a test that can only pass with a network is not testing what
it claims to test.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
RESEARCH = ROOT / "docs" / "research" / "surcharge-and-apd-data.md"

# The marker the research document must carry once the run has happened. It is
# a literal string rather than a heading so that reformatting the document
# cannot accidentally satisfy the gate.
ANSWER_MARKER = "APD-IN-TOTALTAXES ANSWER:"
VALID_ANSWERS = ("INCLUDED", "NOT_INCLUDED", "INCONCLUSIVE")


def _answer_line():
    """
    The one line that records the answer, or None while the run has not happened.

    Deliberately picky. The document TALKS ABOUT the marker in its instructions
    and carries a commented-out example of it, and neither of those is an
    answer. A gate that opened on a sentence describing how to open it would be
    the same shape as every other bug this project has fixed: a placeholder
    read as a finding.
    """
    if not RESEARCH.exists():
        return None
    for line in RESEARCH.read_text().splitlines():
        stripped = line.strip()
        if ANSWER_MARKER not in stripped:
            continue
        if stripped.startswith("<!--") or stripped.startswith("|"):
            continue
        if "`" in stripped:  # a mention in prose, quoted as code
            continue
        if not any(a in stripped for a in VALID_ANSWERS):
            continue
        return stripped
    return None


ANSWER = _answer_line()

pytestmark = pytest.mark.skipif(
    ANSWER is None,
    reason=(
        "v5 Step 8 has not been run. Nobody has yet compared a live "
        "LHR-departure row's TotalTaxes against a real booking page, so it is "
        "still unknown whether Seats.aero's tax figure already contains UK APD. "
        "This is a GATE, not a failure: run the --new-trip apd_probe command in "
        "this module's docstring on a machine with egress, write the answer into "
        f"docs/research/surcharge-and-apd-data.md on a line containing "
        f"'{ANSWER_MARKER}', and this module starts asserting. Until then the "
        "live/replayed path STATES the APD amount and does not apply it."
    ),
)


def test_the_answer_is_one_of_the_three_and_names_its_evidence():
    assert any(a in ANSWER for a in VALID_ANSWERS), (
        f"{ANSWER_MARKER} must be followed by one of {VALID_ANSWERS}. "
        f"Got: {ANSWER!r}"
    )
    assert re.search(r"\d{4}-\d{2}-\d{2}", ANSWER), (
        "the answer must carry the date it was observed - an undated "
        "observation cannot go stale, which is a claim about it that is never "
        "true"
    )


def test_an_answer_of_not_included_means_the_flag_must_become_a_charge():
    """
    v6's instruction, written down at the moment the answer lands rather than
    left to be remembered.
    """
    if "NOT_INCLUDED" not in ANSWER:
        pytest.skip("the answer is not NOT_INCLUDED")
    from src.apd import apd_for_leg

    assert apd_for_leg is not None
    pytest.fail(
        "Step 8 recorded NOT_INCLUDED: Seats.aero's TotalTaxes does NOT contain "
        "UK APD. Every live and replayed UK-departure leg is therefore "
        "understating by the APD amount, and v6 must ADD it on those paths "
        "rather than flagging it. Delete this test in that commit. This is a "
        "deliberate failing test: the tool is currently wrong in a known "
        "direction and the suite says so out loud."
    )


def test_an_answer_of_included_means_the_flag_must_come_off():
    if "INCLUDED" not in ANSWER or "NOT_INCLUDED" in ANSWER:
        pytest.skip("the answer is not INCLUDED")
    pytest.fail(
        "Step 8 recorded INCLUDED: Seats.aero's TotalTaxes already contains UK "
        "APD. The live/replayed flag is now noise on every UK departure and v6 "
        "must remove it, adding nothing. Delete this test in that commit."
    )


def test_an_inconclusive_answer_keeps_the_flag_and_says_so():
    if "INCONCLUSIVE" not in ANSWER:
        pytest.skip("the answer is not INCONCLUSIVE")
    from src.apd import apd_for_leg
    from src.models import Leg
    from datetime import date

    charge = apd_for_leg(
        Leg(
            id="probe",
            kind="flight",
            description="LHR->SFO",
            date=date(2027, 1, 27),
            origin="LHR",
            destination="SFO",
        ),
        "Y",
        1,
        inclusion_unverified=True,
    )
    assert charge is not None and charge.inclusion_unverified
    assert "NOBODY HAS CHECKED" in charge.render(), (
        "an inconclusive run leaves the flag in place AND leaves it saying that "
        "the question is open"
    )
