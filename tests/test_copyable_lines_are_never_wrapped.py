"""
MAC-1, THE PROPERTY: a line this tool asks the reader to COPY arrives on ONE
line, byte for byte, whatever the console width and however long the path in it.

Found by Tsuki running the suite on his Mac. `yq-check`'s last act is to print
the row to paste into `data/yq_inclusion.csv`; the row carries the evidence
path, and on macOS a pytest tmp path is
`/private/var/folders/9w/<hash>/T/pytest-of-<user>/pytest-4/<test>/records/...`.
That pushed the row past the console's 190 columns, rich folded it mid-path, and
the substring the old test looked for was no longer in the output. In the
sandbox the tmp path is short, so it passed - the test was passing on the
LENGTH OF THE CHECKOUT'S TEMP DIRECTORY, not on anything the tool does.

It is not only a test problem. A `yq_inclusion` row decides whether a carrier
surcharge is added to a score - the one thing in this project that can let metal
move money - and it gets there by being copied out of a terminal by hand. A row
printed across two lines pastes wrong. So the property asserted here is the one
that matters: the line comes back WHOLE and EQUAL, not merely present.

Asserted at several widths, including widths far narrower than the 190 every
console this tool builds uses, because "it fits today" is what broke.
"""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from src import trips_tools
from src.formatter import print_copyable, print_live_banner
from src.seats_client import SeatsClient
from tests.test_trips_tools import FLAG_KEY, TODAY, Stub
from tests._trips_label_state import unverified_constants  # noqa: F401 - pins the label

# Narrower than anything real, the real one, and wider than anything real.
WIDTHS = [20, 40, 80, 190, 400]

# A directory as deep as a macOS pytest tmp path, built out of path segments
# with no spaces in them - a word rich cannot break at, which is exactly the
# shape that folds.
DEEP = ("private-var-folders-9w-0000gn-T", "pytest-of-freddielopez", "pytest-4",
        "test_yq_check_prints_every_fie0")


def deep_dir(tmp_path: Path, leaf: str) -> Path:
    out = tmp_path.joinpath(*DEEP, leaf)
    out.mkdir(parents=True, exist_ok=True)
    return out


def printed(fn, width: int) -> list:
    buf = io.StringIO()
    fn(Console(file=buf, width=width))
    return [line.rstrip() for line in buf.getvalue().splitlines()]


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


# --------------------------------------------------------------- the primitive


COPYABLE = [
    "/" + "/".join(DEEP) + "/records/2026-09-11-virginatlantic.md",
    "  virginatlantic,VS,<VERDICT>,2026-09-11,/" + "/".join(DEEP)
    + "/records/2026-09-11-virginatlantic.md,LHR-SFO J 2027-01-27",
    "  python -m src.main --trip-fixture " + "a" * 200 + ".json --live",
    # A path may contain square brackets; they are the reader's characters, not
    # rich markup, and they have to survive the round trip.
    "/tmp/a [draft] dir/trip.json",
    "short",
]


@pytest.mark.parametrize("text", COPYABLE)
@pytest.mark.parametrize("width", WIDTHS)
def test_a_copyable_line_is_one_line_and_unchanged_at_every_width(text, width):
    assert printed(lambda c: print_copyable(c, text), width) == [text]


@pytest.mark.parametrize("text", COPYABLE)
def test_a_copyable_line_does_not_depend_on_the_width_at_all(text):
    rendered = {w: printed(lambda c: print_copyable(c, text), w) for w in WIDTHS}
    assert len(set(map(tuple, rendered.values()))) == 1, rendered


@pytest.mark.parametrize("width", WIDTHS)
def test_a_style_does_not_change_the_characters(width):
    text = COPYABLE[1]
    assert printed(lambda c: print_copyable(c, text, "bold red"), width) == [text]


# ------------------------------------------------------ the yq-check CSV row


def yq_run(tmp_path, width):
    """yq-check with a macOS-shaped record directory, at `width`."""
    records = deep_dir(tmp_path, "records")
    argv = [
        "yq-check", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
        "--source", "virginatlantic", "--cabin", "J",
        "--out-dir", str(deep_dir(tmp_path, "real")), "--record-dir", str(records),
        "--api-key", FLAG_KEY, "--yes",
    ]
    buf = io.StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code = trips_tools.main(
            argv, read=lambda prompt: "y", console=Console(file=buf, width=width),
            today=TODAY,
        )
    lines = [line.rstrip() for line in buf.getvalue().splitlines()]
    return code, lines, records / "2026-09-11-virginatlantic.md"


def pasted_rows(lines):
    """The lines that ARE the CSV row, not the sentence that introduces it."""
    return [l for l in lines if ",<VERDICT>," in l]


@pytest.mark.usefixtures("unverified_constants")
@pytest.mark.parametrize("width", WIDTHS)
def test_the_row_the_reader_pastes_into_yq_inclusion_arrives_on_one_line(tmp_path, width):
    code, lines, record = yq_run(tmp_path, width)
    assert code == 0, lines
    row = (
        f"  virginatlantic,VS,<VERDICT>,2026-09-11,{record},"
        f"LHR-SFO J 2027-01-27"
    )
    # Exactly one line, equal character for character - not "contains".
    assert pasted_rows(lines) == [row], [l for l in lines if "virginatlantic,VS" in l]


@pytest.mark.usefixtures("unverified_constants")
@pytest.mark.parametrize("width", WIDTHS)
def test_every_path_yq_check_reports_arrives_on_one_line(tmp_path, width):
    code, lines, record = yq_run(tmp_path, width)
    assert code == 0, lines
    written = sorted(deep_dir(tmp_path, "real").iterdir()) + [record]
    for path in written:
        assert any(line.endswith(str(path)) or str(path) in line for line in lines), path
        # The path is inside ONE line, not spread over two.
        assert sum(str(path) in line for line in lines) >= 1
        for line in lines:
            assert not line.endswith(str(path)[:-1]), (line, path)


@pytest.mark.usefixtures("unverified_constants")
def test_the_copied_lines_do_not_depend_on_the_width_end_to_end(tmp_path):
    """Same run, five widths: the lines a reader copies are identical. (Each
    width gets its own tree, so the tree's own prefix is normalised away.)"""
    rows = {}
    for width in WIDTHS:
        root = tmp_path / str(width)
        _, lines, _ = yq_run(root, width)
        rows[width] = tuple(
            l.replace(str(root), "<ROOT>")
            for l in lines
            if ",<VERDICT>," in l or l.startswith("wrote ")
        )
    assert len(set(rows.values())) == 1, rows
    assert len(next(iter(rows.values()))) == 4  # 2 capture files, the record, the row


# -------------------------------------------- the other lines of the same kind


@pytest.mark.parametrize("width", WIDTHS)
def test_the_label_check_refusal_names_a_directory_that_is_not_folded(tmp_path, width):
    """A capture written outside real/ is refused by name; the name is a path."""
    out_dir = deep_dir(tmp_path, "elsewhere")
    argv = [
        "capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
        "--source", "virginatlantic", "--out-dir", str(out_dir),
        "--api-key", FLAG_KEY, "--yes",
    ]
    buf = io.StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code = trips_tools.main(
            argv, read=lambda prompt: "y", console=Console(file=buf, width=width),
            today=TODAY,
        )
    lines = [line.rstrip() for line in buf.getvalue().splitlines()]
    assert code == trips_tools.EXIT_DRIFT, lines
    assert f"  - {out_dir} is not the real/ capture directory" in lines


# NOT FIXED HERE, ON PURPOSE: src/main.py prints three lines of this kind - the
# relocation banner's directory, `Wrote PATH` from --new-trip, and the command
# --new-trip tells you to run next. None of them can pass 190 columns in
# practice (the paths are inside the repo or chosen by the person running it,
# the command is a fixed length), and main.py's every top-level definition is
# pinned to round 4 by
# docs/test-reports/operating-airline-probes/test_oa_r5_retest.py::
# test_main_py_changed_only_by_the_dispatch_split_the_ui_plan_declares, which
# allows only the UI plan's dispatch split. Changing them would turn a sixth
# probe red for a fold nobody can produce. Reported rather than done.


@pytest.mark.parametrize("width", WIDTHS)
def test_the_live_banner_never_folds_the_manifest_it_points_at(tmp_path, width):
    """`  manifest:  PATH` is what `--from-snapshot` is handed next."""
    snapshots = deep_dir(tmp_path, "snapshots")
    cache = SimpleNamespace(
        snapshot_dir=snapshots, manifest_path=snapshots / "MANIFEST.md", warnings=[]
    )
    lines = printed(lambda c: print_live_banner([], None, cache, c), width)
    assert f"  snapshots: {snapshots}" in lines
    assert f"  manifest:  {snapshots / 'MANIFEST.md'}" in lines


# ------------------------------------------------------------------ the record


@pytest.mark.usefixtures("unverified_constants")
def test_the_row_printed_at_a_narrow_width_still_loads_as_a_yq_inclusion_row(tmp_path):
    """
    The end of the chain: the line that came off a 40-column console is pasted
    into a table, with <VERDICT> replaced as instructed, and the loader takes
    it. A folded row could never do this.
    """
    from src.yq_inclusion import load

    code, lines, record = yq_run(tmp_path, 40)
    assert code == 0, lines
    (row,) = [l.strip() for l in pasted_rows(lines)]
    root = tmp_path / "root"
    rel = "docs/yq-checks/2026-09-11-virginatlantic.md"
    (root / "docs" / "yq-checks").mkdir(parents=True)
    text = record.read_text()
    for blank, filled in {
        "- date checked: ____": "- date checked: 2026-09-12",
        "- flight(s) shown: ____": "- flight(s) shown: VS19 LHR-SFO",
        "- the site shows this flight operated by VS itself, not a codeshare "
        "partner (yes / no): ____":
            "- the site shows this flight operated by VS itself, not a codeshare "
            "partner (yes / no): yes",
        "- total taxes, fees and carrier-imposed charges for ONE adult, as the "
        "site shows it (one combined figure, or its lines added up): ____":
            "- total taxes, fees and carrier-imposed charges for ONE adult, as "
            "the site shows it (one combined figure, or its lines added up): "
            "GBP 450.00",
        "- verdict (includes_yq / excludes_yq / inconclusive): ____":
            "- verdict (includes_yq / excludes_yq / inconclusive): includes_yq",
    }.items():
        assert text.count(blank) == 1, blank
        text = text.replace(blank, filled)
    (root / rel).write_text(text)
    # Pasted verbatim: only <VERDICT> and the evidence path (which points at the
    # record's home in the tree) are edited, exactly as the tool instructs.
    pasted = row.replace("<VERDICT>", "includes_yq").replace(str(record), rel)
    table = root / "yq.csv"
    table.write_text(
        "source,airline,verdict,verified_on,evidence,notes\n" + pasted + "\n"
    )
    assert load(table, today=TODAY, root=root)[("virginatlantic", "VS")].includes


# The expressions that ARE a path or the pasted row, wherever they are written.
# Named rather than pattern-matched: `cap.path.name` is a bare filename and
# belongs in ordinary prose, `cap.path` is an absolute path and does not.
PATH_EXPRESSIONS = {"cap.path", "cap.raw_path", "record", "evidence", "item",
                    "out_dir", "record_dir", "path"}


def test_the_tool_prints_no_path_through_a_wrapping_print():
    """
    The rule, as source: in the capture/yq-check tool, a `console.print` that
    interpolates a whole PATH does not exist - those lines go through
    `formatter.print_copyable`. Parsed as code rather than grepped, so a new
    call site cannot quietly reintroduce the fold.
    """
    import ast

    tree = ast.parse(Path(trips_tools.__file__).read_text())
    offenders = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)):
            continue
        if func.attr != "print" or not isinstance(func.value, ast.Name):
            continue
        if func.value.id != "console":
            continue
        for part in ast.walk(node):
            if not isinstance(part, ast.FormattedValue):
                continue
            if ast.unparse(part.value) in PATH_EXPRESSIONS:
                offenders.append(f"line {node.lineno}: {ast.unparse(node)[:90]}")
    assert offenders == [], (
        "a line carrying a whole path must go through formatter.print_copyable: "
        + "; ".join(offenders)
    )
