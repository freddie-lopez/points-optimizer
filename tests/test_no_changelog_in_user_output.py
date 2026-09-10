"""
User-facing output describes the TRIP, never this project's own history.

Carried forward from two Manager reviews, untouched both times: "Tsuki is
reading a booking recommendation, not a commit log." The instances found were a
headline caveat ending "the assumption that made v0's number wrong", six
`data/surcharges.csv` rows whose `source` column was the repo path
`docs/plans/v1.md section 7 seed table`, and two Trip C fixture strings
describing which release the fixture was built for.

FIXED AS A CLASS, NOT AS INSTANCES, which is the standard this project is held
to: the check below renders every committed trip fixture through the real CLI
and greps the whole output. A new version reference anywhere a reader can see
it fails here, whether it comes from a format string, a CSV data row or a
fixture note - the three different places the four instances actually came from.

WHAT IS BEING REMOVED IS THE CITATION, NOT THE WARNING. Every one of these
sentences was telling the reader something true and useful about what the tool
does not know. The rewrite keeps the substance and drops the release number:
"the assumption that turns an unknown into a saving that is not there" says the
same thing to somebody who has never heard of v0, which is everybody reading
the output.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"

# A release reference ("v0", "v3's"), an internal finding id ("H-4",
# "finding C-2"), a plan-document path, or a build-step number. All four are
# things a reader of a booking recommendation has no way to interpret.
CHANGELOG = re.compile(
    r"""(
        \bv[0-9]+(?:'s)?\b            # v0, v5's
      | \bfinding\s+[A-Z]-[0-9]+      # finding C-2
      | \b[HCML]-[0-9]\b              # H-4, M-1
      | docs/(?:plans|test-reports)/  # an internal document path
      | \bStep\s+[0-9]+\b             # v5 Step 7
    )""",
    re.VERBOSE,
)

# `v` followed by a digit also spells real things in the world. Award fare
# buckets and aircraft types are the reader's own vocabulary, not ours.
ALLOWED = re.compile(r"\b(?:A3[0-9]{2}|B7[0-9]{2}|CRJ[0-9]+)\b")


def _run(fixture: str):
    proc = subprocess.run(
        [
            sys.executable, "-m", "src.main",
            "--trip-fixture", fixture,
            "--offline",
            "--allow-badge-fallback",
            "--balance", "UR=180000",
            "--card", "Chase Sapphire Preferred",
            "--transfer-date", "2026-09-15",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "fixture", sorted(p.name for p in TRIPS.glob("*.json"))
)
def test_no_release_or_finding_reference_reaches_the_reader(fixture):
    output = _run(fixture)
    assert output.strip(), f"{fixture} produced no output to check"
    offenders = []
    for line in output.splitlines():
        clean = ALLOWED.sub("", line)
        for match in CHANGELOG.finditer(clean):
            offenders.append(f"{match.group(0)!r} in: {line.strip()[:160]}")
    assert not offenders, (
        f"{fixture}: this project's own history reached the reader:\n  "
        + "\n  ".join(offenders)
    )


def test_the_surcharge_table_cites_sources_a_reader_can_evaluate():
    """
    A `source` column is provenance the reader is meant to WEIGH, so it has to
    name something outside this repository - a published page, or an honest
    statement that the figure is the project's own uncorroborated research.
    A path to a planning document is neither.
    """
    import csv

    rows = list(csv.DictReader((ROOT / "data" / "surcharges.csv").open()))
    assert rows
    for row in rows:
        source = row["source"]
        assert not source.startswith("docs/"), (
            f"{row['program']}: source is the repo path {source!r}. Cite a "
            f"published source, or say plainly that the figure is this "
            f"project's own uncorroborated research."
        )
        assert source.startswith("http") or "NOT corroborated" in source, (
            f"{row['program']}: source {source!r} neither links to a published "
            f"page nor admits it is uncorroborated."
        )
