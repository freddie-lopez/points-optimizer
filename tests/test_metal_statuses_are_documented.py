"""
Every MetalStatus and every reason code has a row in the README's
operating-airline section - the guard `test_verdicts_are_documented` gives the
verdicts, for the lookup's vocabulary.
"""
from pathlib import Path

from src.models import METAL_REASONS, MetalStatus

ROOT = Path(__file__).parent.parent


def _section():
    readme = (ROOT / "README.md").read_text()
    start = readme.index("## Operating airline (Seats.aero trips lookup)")
    return readme[start: readme.index("\n## ", start + 10)]


def test_every_status_has_a_row():
    section = _section()
    for status in MetalStatus:
        assert f"| `{status.name}` |" in section, status


def test_every_reason_code_has_a_row_under_its_status():
    section = _section()
    for status, reasons in METAL_REASONS.items():
        for reason in reasons:
            assert f"| `{status.name}` | `{reason}` |" in section, (status, reason)


def test_the_trips_tools_exit_codes_are_documented_after_the_main_table():
    readme = (ROOT / "README.md").read_text()
    main_table = readme.index("### Exit codes")
    next_heading = readme.index("###", main_table + 10)
    tools = readme.index("### trips_tools exit codes")
    assert tools > next_heading
    body = readme[tools: readme.index("\n#", tools + 10)]
    for code in ("| 0 |", "| 1 |", "| 5 |"):
        assert code in body


def test_the_flags_are_documented():
    readme = (ROOT / "README.md").read_text()
    assert "| `--trips auto\\|all\\|off` |" in readme
    assert "| `--trips-cap N` |" in readme
