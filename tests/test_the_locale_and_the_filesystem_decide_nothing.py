"""
MAC-A, THE PROPERTY: what this tool prints, and which file a row cites, are the
same on every machine. Neither the shell's locale nor the filesystem's opinion
about capital letters is allowed a vote.

Two things the Tester's macOS round left open:

* `python -m src.main` took stdout's encoding from the LOCALE. Under `LANG=C`
  that is ASCII, and this tool's output is not: rich draws its tables with
  box-drawing characters and every masked key carries U+2026. So a run scored a
  whole trip and then died with a UnicodeEncodeError partway through printing
  it - the work done, the answer lost, a traceback in its place. The tool now
  writes UTF-8 wherever it runs (`config.use_utf8_output`, called from the
  process entry points only). FORCED rather than refused: refusing under a C
  locale takes the tool away from any CI that has not set one, to protect a
  terminal that might render a box character oddly - and that terminal still
  gets every figure, where a crash gives nothing.

* `yq_inclusion`'s evidence check asked the FILESYSTEM whether the file exists.
  macOS says yes to `docs/yq-checks/2026-09-11-VirginAtlantic.md` and Linux says
  no, so a mis-cased row would back a verdict on Tsuki's machine and be refused
  in CI. A row that is evidence on one computer and not on another is not
  evidence. The spelling is now compared with the names the directory lists,
  which is a rule this file applies rather than a question it asks the disk -
  and that is why it can be tested HERE, on a case-sensitive filesystem.
"""
import io
import subprocess
import sys
from pathlib import Path

import pytest

from src import config
from src.yq_inclusion import YqInclusionError, _spelt_differently_on_disk, load
from tests.test_yq_record_checks import TODAY, record

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"

# A locale with no UTF-8 in it, and the three settings that stop CPython from
# quietly upgrading it. This is a C-locale CI box, reproduced.
C_LOCALE = {
    "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0",
}
ARGV_TAIL = ["--offline", "--balance", "UR=160000", "--card",
             "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"]


def run_cli_in(env_extra, fixture="trip_b_europe.json"):
    """A REAL process, because the encoding of stdout is a property of one."""
    import os

    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", fixture, *ARGV_TAIL],
        cwd=ROOT, capture_output=True, encoding="utf-8", env=env,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------- the locale


@pytest.mark.parametrize("fixture", ["trip_b_europe.json",
                                     "trip_c_lon_mry_surcharge.json"])
def test_the_cli_prints_its_whole_report_under_a_c_locale(fixture):
    code, text = run_cli_in(C_LOCALE, fixture)
    assert "Traceback" not in text, text[-600:]
    assert "UnicodeEncodeError" not in text, text[-600:]
    assert code in (0, 1, 2, 3, 4), (code, text[-300:])
    # It did not merely survive - it printed the report, box drawing and all.
    assert "─" in text or "|" in text, text[-300:]
    assert len(text.splitlines()) > 50, len(text.splitlines())


def test_the_c_locale_run_says_the_same_thing_as_the_utf8_one():
    """Not just 'it did not crash': the same text, character for character."""
    utf8_code, utf8_text = run_cli_in({"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
    c_code, c_text = run_cli_in(C_LOCALE)
    assert (utf8_code, utf8_text) == (c_code, c_text)


def test_use_utf8_output_leaves_a_stream_it_does_not_own_alone():
    """It is called from the entry points, and a StringIO (or anything without
    reconfigure) must come through untouched rather than raise."""
    buf = io.StringIO()
    was_out, was_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        config.use_utf8_output()
    finally:
        sys.stdout, sys.stderr = was_out, was_err
    assert buf.getvalue() == ""


def test_the_entry_points_are_the_only_place_it_is_called():
    """In-process callers - the UI, the tests - must not have the real stdout
    reconfigured under them. Parsed as code, not grepped."""
    import ast

    for name in ("src/main.py", "src/trips_tools.py", "src/ui/__main__.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and "use_utf8_output" in ast.unparse(node.func)
        ]
        assert len(calls) == 1, (name, len(calls))
        # ... and it is inside the `if __name__ == "__main__"` block, which is
        # the only code in these files that a `python -m` run reaches and an
        # import does not.
        guards = [
            n for n in tree.body
            if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)
        ]
        assert len(guards) == 1, name
        assert any(
            isinstance(node, ast.Call) and "use_utf8_output" in ast.unparse(node.func)
            for node in ast.walk(guards[0])
        ), f"{name}: use_utf8_output is not in the __main__ guard"


# ----------------------------------------------------------- the filesystem


# One real record, spelt the way yq-check writes it, and a one-row table that
# cites it. The row is legitimate in every respect except the spelling under
# test, so a refusal can only be about the spelling.
RECORD = record()


def write_table(tmp_path, evidence):
    table = tmp_path / "yq.csv"
    table.write_text(
        "source,airline,verdict,verified_on,evidence,notes\n"
        f"virginatlantic,VS,includes_yq,2026-09-10,{evidence},x\n",
        encoding="utf-8",
    )
    return table


def evidence_tree(tmp_path, filename="2026-09-11-virginatlantic.md"):
    (tmp_path / "docs" / "yq-checks").mkdir(parents=True)
    (tmp_path / "docs" / "yq-checks" / filename).write_text(RECORD, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "cited",
    [
        "docs/yq-checks/2026-09-11-VirginAtlantic.md",
        "docs/yq-checks/2026-09-11-VIRGINATLANTIC.MD",
        "docs/YQ-Checks/2026-09-11-virginatlantic.md",
        "Docs/yq-checks/2026-09-11-virginatlantic.md",
    ],
)
def test_a_miscased_evidence_path_is_refused_by_the_rule(tmp_path, cited):
    """
    On macOS every one of these IS the file. The refusal must not depend on
    that, so it is made here - and the first two are caught by the new check
    while the last two are already caught by the `docs/yq-checks` prefix rule.
    Either way the row does not load, on any filesystem.
    """
    root = evidence_tree(tmp_path)
    table = write_table(tmp_path, evidence=cited)
    with pytest.raises(YqInclusionError) as refused:
        load(table, today=TODAY, root=root)
    assert cited in str(refused.value)


def test_the_refusal_names_the_spelling_that_is_actually_there(tmp_path):
    root = evidence_tree(tmp_path)
    cited = "docs/yq-checks/2026-09-11-VirginAtlantic.md"
    table = write_table(tmp_path, evidence=cited)
    with pytest.raises(YqInclusionError, match="is spelt"):
        load(table, today=TODAY, root=root)
    assert _spelt_differently_on_disk(root, Path(cited)) == \
        "docs/yq-checks/2026-09-11-virginatlantic.md"


def test_the_exactly_spelt_row_still_loads(tmp_path):
    root = evidence_tree(tmp_path)
    table = write_table(tmp_path, evidence="docs/yq-checks/2026-09-11-virginatlantic.md")
    assert load(table, today=TODAY, root=root)[("virginatlantic", "VS")].includes


def test_an_absent_file_is_still_absent_not_miscased(tmp_path):
    """The two refusals are different sentences and must not be confused: a file
    that is simply not there has no spelling to report."""
    root = evidence_tree(tmp_path)
    assert _spelt_differently_on_disk(root, Path("docs/yq-checks/nothing.md")) == ""
    table = write_table(tmp_path, evidence="docs/yq-checks/nothing.md")
    with pytest.raises(YqInclusionError, match="does not exist"):
        load(table, today=TODAY, root=root)


def test_the_committed_table_is_spelt_exactly(tmp_path):
    """The rule applied to the real data/yq_inclusion.csv, so it can never be
    the thing that is wrong."""
    import csv

    table = ROOT / "data" / "yq_inclusion.csv"
    with table.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            evidence = (row.get("evidence") or "").strip()
            if evidence:
                assert _spelt_differently_on_disk(ROOT, Path(evidence)) == "", evidence


def test_a_miscased_directory_names_the_whole_path_not_just_the_directory(tmp_path):
    """
    MAC-B. When the mis-cased component is a directory, the correction has to
    carry the rest of the path with it. Truncating at the directory would answer
    'the file you mean is spelt <a directory>', which is a second wrong answer
    wearing a correction's clothes.

    Unreachable through `load` at this head - `docs/yq-checks/` is flat - but the
    evidence rule permits a subdirectory, so the helper is checked directly.
    """
    (tmp_path / "docs" / "yq-checks" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "yq-checks" / "sub" / "b.md").write_text(RECORD, encoding="utf-8")
    assert _spelt_differently_on_disk(tmp_path, Path("docs/yq-checks/SUB/b.md")) == \
        "docs/yq-checks/sub/b.md"
    # And a file that is missing under a correctly spelt directory still has no
    # spelling to report.
    assert _spelt_differently_on_disk(tmp_path, Path("docs/yq-checks/sub/nothing.md")) == ""


def test_a_correction_is_only_offered_for_a_file_that_is_there(tmp_path):
    """
    MAC-C, the Manager's finding. A row that mis-spells a directory AND names a
    file that does not exist used to be told 'is spelt docs/yq-checks/sub/
    nothing.md on disk' - a correction to a file nobody has. That is 'I could
    not find it' rendered as 'the one you mean is X'.
    """
    (tmp_path / "docs" / "yq-checks" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "yq-checks" / "sub" / "b.md").write_text(RECORD, encoding="utf-8")
    # A real file behind a mis-cased directory is still corrected, whole.
    assert _spelt_differently_on_disk(tmp_path, Path("docs/yq-checks/SUB/b.md")) == \
        "docs/yq-checks/sub/b.md"
    # An absent file behind a mis-cased directory has no spelling to report.
    assert _spelt_differently_on_disk(tmp_path, Path("docs/yq-checks/SUB/nothing.md")) == ""
    # ... and mis-cased at both levels is corrected only because both exist.
    (tmp_path / "docs" / "yq-checks" / "sub" / "Mixed.md").write_text(RECORD, encoding="utf-8")
    assert _spelt_differently_on_disk(tmp_path, Path("docs/yq-checks/SUB/MIXED.MD")) == \
        "docs/yq-checks/sub/Mixed.md"


def test_the_row_with_a_miscased_directory_and_no_file_says_does_not_exist(tmp_path):
    root = evidence_tree(tmp_path)
    (tmp_path / "docs" / "yq-checks" / "sub").mkdir()
    table = write_table(tmp_path, evidence="docs/yq-checks/SUB/nothing.md")
    with pytest.raises(YqInclusionError) as refused:
        load(table, today=TODAY, root=root)
    assert "does not exist" in str(refused.value), refused.value
    assert "is spelt" not in str(refused.value), refused.value
