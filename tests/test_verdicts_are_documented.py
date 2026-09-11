"""
Every verdict string the code can produce has a row in the README's verdict table.

The table said "five PAY CASH sub-states" while the code produced nine; the new
`cash (indirect path not scored)` would have been the tenth undocumented one. This
is the same guard `test_exit_codes_are_documented.py` gives the exit codes.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _verdicts_in_source():
    found = set()
    for path in (ROOT / "src").glob("*.py"):
        text = path.read_text()
        # VERDICT_X = "cash (...)" constants
        found |= set(re.findall(r'^VERDICT_\w+\s*=\s*"([^"]+)"', text, re.M))
        # result.verdict = "..." / r.verdict = "..." literals
        found |= set(re.findall(r'\.verdict\s*=\s*"([^"]+)"', text))
    return found


def test_the_scan_finds_the_verdicts_it_should():
    found = _verdicts_in_source()
    assert {"points", "cash", "cash (surcharge unknown)",
            "cash (indirect path not scored)"} <= found


def test_every_verdict_has_a_readme_row():
    readme = (ROOT / "README.md").read_text()
    table = readme.split("### The PAY CASH sub-states are distinct", 1)[1].split("\n---", 1)[0]
    missing = sorted(v for v in _verdicts_in_source() if f"| `{v}` |" not in table)
    assert not missing, f"verdicts with no README row: {missing}"
