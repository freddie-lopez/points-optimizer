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


def _scrub_repo_root(text: str, root: Path = ROOT) -> str:
    """
    Remove EXACTLY this checkout's own path, and nothing else.

    On Tsuki's Mac the checkout lives in `.../points-optimizer-v5/`, so any line
    naming a file under it contains `v5` - a fact about where he unzipped the
    project, not this project's history reaching him. Excluding every path-shaped
    string would be a hole (a real "v5" leak inside a path-like note would pass);
    removing the one known root is not.
    """
    return text.replace(str(root), "<repo>")


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
        # MAC-A: the child writes UTF-8 by rule (config.use_utf8_output), so
        # this reads UTF-8 by rule. `text=True` alone decodes with the LOCALE's
        # encoding, which under LANG=C is ASCII - and then the test, not the
        # tool, is what fails on a C-locale machine.
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    return proc.returncode, proc.stdout + proc.stderr


def _scan(output: str):
    offenders = []
    for line in _scrub_repo_root(output).splitlines():
        clean = ALLOWED.sub("", line)
        for match in CHANGELOG.finditer(clean):
            offenders.append(f"{match.group(0)!r} in: {line.strip()[:160]}")
    return offenders


# TRIP fixtures only. The directory also holds acceptance-test ANSWER files
# (`*_answer.json`, read by test_acceptance.py), which are not trips. Globbing
# them in fed them to the CLI, which crashed with a raw traceback - and the scan
# then ran over the crash. In the sandbox that "passed" (no `v5` in the path), so
# two parametrisations had been certifying a traceback as clean output.
TRIP_FIXTURES = sorted(
    p.name for p in TRIPS.glob("*.json") if not p.name.endswith("_answer.json")
)


def test_the_discovered_set_is_every_loadable_trip_and_nothing_else():
    """The glob is checked against the loader, not trusted."""
    from src.trip_loader import TripFixtureError, load_trip_fixture

    for p in sorted(TRIPS.glob("*.json")):
        try:
            load_trip_fixture(p)
            loadable = True
        except TripFixtureError:
            loadable = False
        assert loadable == (p.name in TRIP_FIXTURES), (
            f"{p.name}: loadable={loadable} but "
            f"{'in' if p.name in TRIP_FIXTURES else 'not in'} the scanned set"
        )


@pytest.mark.parametrize("fixture", TRIP_FIXTURES)
def test_no_release_or_finding_reference_reaches_the_reader(fixture):
    code, output = _run(fixture)
    assert output.strip(), f"{fixture} produced no output to check"
    # A scan of a crash is not a scan.
    assert "Traceback (most recent call last)" not in output, output[-1500:]
    assert code == 0, f"{fixture} exited {code}; nothing was rendered to scan"
    offenders = _scan(output)
    assert not offenders, (
        f"{fixture}: this project's own history reached the reader:\n  "
        + "\n  ".join(offenders)
    )


def test_the_checkout_folder_name_is_not_a_changelog_leak():
    root = Path("/Users/someone/Downloads/points-optimizer-v5")
    line = f'  File "{root}/src/main.py", line 698, in run_fixture'
    assert _scan(line) != [], "sanity: a foreign root is not scrubbed, so v5 matches"
    assert _scrub_repo_root(line, root).count("v5") == 0
    # Only the root is removed: a real reference elsewhere on the line survives.
    leak = f"{root}/data/x.csv - the assumption that made v5 wrong"
    assert "v5" in _scrub_repo_root(leak, root)


def test_an_answer_file_passed_as_a_trip_is_one_clean_error():
    code, output = _run("trip_001_answer.json")
    assert code == 1
    assert "Traceback" not in output
    assert "is not a trip fixture" in output
    assert "ANSWER file" in output


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
