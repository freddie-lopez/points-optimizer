"""
H. The -O structural guards, the flag conflicts, and what the README promises.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASE, B4_VS, ROOT, Stub, flat, run_cli, vs_b4_rows, vs_itinerary  # noqa: F401
from tests import _trips_payloads as tp

PY = str(ROOT / ".venv" / "bin" / "python")


def _tree(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT / "src", dst / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "data", dst / "data", ignore=shutil.ignore_patterns("cache"))
    return dst


def _import_O(repo, module="src.models"):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([PY, "-O", "-c", f"import {module}"], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_real_tree_imports_under_O(tmp_path):
    r = _import_O(_tree(tmp_path), "src.main")
    assert r.returncode == 0, r.stderr[-800:]


@pytest.mark.parametrize("edit", [
    # way ten: drop the trip-level answer for a new code
    ('    "METAL_UNKNOWN": TripTreatment(\n        COUNTED_AT_TRIP_LEVEL, totals_key="legs_metal_unknown"\n    ),\n', ""),
    # way ten: a new *_UNKNOWN reason with no declared answer
    None,
    # the MetalLookup storage classification: an invariant-read field left unclassified
    ('        "provenance",\n    }\n)\n# Carried by the transport', '    }\n)\n# Carried by the transport'),
])
def test_the_structural_guards_still_refuse_at_import_under_O(tmp_path, edit):
    repo = _tree(tmp_path)
    if edit is None:
        opt = repo / "src" / "optimizer.py"
        text = opt.read_text()
        anchor = '            "METAL_UNKNOWN",\n'
        assert anchor in text
        text = text.replace(anchor, anchor, 1)
        text = text.replace(
            "    return result\n\n\ndef ",
            '    if False:\n        result.add_reason("METAL_CABIN_UNKNOWN", "x")\n    return result\n\n\ndef ', 1)
        opt.write_text(text)
    else:
        models = repo / "src" / "models.py"
        text = models.read_text()
        assert edit[0] in text, "the probe's anchor moved"
        models.write_text(text.replace(edit[0], edit[1], 1))
    r = _import_O(repo)
    assert r.returncode != 0, "a broken classification imported under -O"
    assert "ValueError" in r.stderr
    assert ("way (10)" in r.stderr) or ("MetalLookup" in r.stderr and "provenance" in r.stderr), r.stderr[-600:]


@pytest.mark.parametrize("argv_extra,needle", [
    (["--offline", "--trips", "auto"], "--offline"),
    (["--offline", "--trips-cap", "5"], "--offline"),
    (["--from-snapshot", "x/MANIFEST.md", "--trips", "off"], "--from-snapshot"),
    (["--from-snapshot", "x/MANIFEST.md", "--trips-cap", "3"], "--from-snapshot"),
    (["--trips", "AUTO"], "not one of"),
    (["--trips", ""], "not one of"),
    (["--trips-cap", "0"], "1 to 50"),
    (["--trips-cap", "51"], "1 to 50"),
    (["--trips-cap", "-1"], "1 to 50"),
    (["--trips-cap", "1.5"], "1 to 50"),
    (["--trips-cap", "²"], "1 to 50"),
])
def test_every_bad_flag_exits_1_with_a_named_reason_and_no_traceback(argv_extra, needle, capsys, monkeypatch):
    try:
        code, out, fl, stub = run_cli(BASE + argv_extra, capsys, monkeypatch, stub=Stub())
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"crashed: {type(e).__name__}: {e}")
    assert code == 1, (code, fl[-300:])
    assert needle in fl
    assert stub.calls == []


def test_single_route_search_refuses_trips_and_prints_the_footer(capsys, monkeypatch):
    code, out, fl, stub = run_cli(["--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
                                   "--balance", "UR=160000", "--trips", "all"], capsys, monkeypatch, stub=Stub())
    assert code == 1 and stub.calls == [] and "single-route search" in fl
    code, out, fl, stub = run_cli(["--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
                                   "--balance", "UR=160000"], capsys, monkeypatch, stub=Stub(rows_for=vs_b4_rows()))
    assert "operating airline: NOT LOOKED UP - single-route search does not call the trips endpoint" in fl
    assert stub.trips_calls == []


def test_the_readme_does_not_promise_a_free_cache_hit_the_code_does_not_give():
    """
    README: "--trips-cap N | ... Cache hits are free" and "A cache hit is free".
    After the cap is reached a lookup the disk cache could answer reads
    CAP_REACHED (admitted in the coder report, sized in test_oa_c). The README's
    Known limitations list does not mention it, nor the other admitted trips
    gaps (duplicate trips rows for one id, rows for legs not in the trip, the
    inferred route of `capture --availability-id`).
    """
    text = (ROOT / "README.md").read_text()
    lim = text[text.index("Known limitations"):]
    assert "cap is checked before the cache" in lim or "cache hit" in lim.lower()[:4000]


def _assertions_in(source: str) -> set:
    """Every assertion a test file makes, as text. Comments, imports, harness
    keywords and formatting are not assertions and are not in here."""
    import ast

    tree = ast.parse(source)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            out.add(ast.unparse(node.test))
        elif isinstance(node, ast.Call) and "raises" in ast.unparse(node.func):
            out.add(ast.unparse(node))
    return out


def _at(rev: str, path: str) -> str:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def test_the_existing_contract_tests_still_make_every_promise_they_made():
    """
    RE-SCOPED BY THE TESTER, macOS ROUND 2. This pinned four contract files
    BYTE for byte since a17497d, so that nobody could make a failing contract
    pass by editing the test that states it. That is the right thing to guard
    and the wrong way to say it: the MAC-A fix added `encoding="utf-8"` to one
    subprocess call in `test_no_changelog_in_user_output.py` - a HARNESS line,
    changing how the child's bytes are decoded, touching no assertion - and
    without it the test, not the tool, is what fails on a C-locale machine.
    A byte pin cannot tell that apart from gutting a contract.

    So it now says what it means: every assertion those files made at a17497d
    is still made at HEAD. Adding assertions is allowed. Changing or deleting
    one is not, and neither is quietly weakening one, because the text of the
    expression is what is compared.
    """
    names = ["tests/test_verdicts_are_documented.py", "tests/test_exit_codes_are_documented.py",
             "tests/test_live_first_defaults.py", "tests/test_no_changelog_in_user_output.py"]
    for name in names:
        was, now = _at("a17497d", name), (ROOT / name).read_text(encoding="utf-8")
        assert was, f"{name} did not exist at a17497d"
        lost = _assertions_in(was) - _assertions_in(now)
        assert not lost, f"{name} no longer makes {len(lost)} assertion(s) it made: {sorted(lost)}"


def test_no_pre_existing_test_file_has_lost_an_assertion():
    """The same rule over the whole suite: a file that existed at a17497d may
    gain tests and may have its harness fixed, but may not stop asserting
    something it asserted."""
    r = subprocess.run(["git", "diff", "--name-status", "a17497d", "HEAD", "--", "tests/"],
                       cwd=ROOT, capture_output=True, text=True)
    modified = [l.split("\t", 1)[1] for l in r.stdout.splitlines()
                if l.startswith("M") and l.endswith(".py")]
    for name in modified:
        was, now = _at("a17497d", name), (ROOT / name).read_text(encoding="utf-8")
        lost = _assertions_in(was) - _assertions_in(now)
        assert not lost, f"{name} no longer makes {len(lost)} assertion(s) it made: {sorted(lost)}"
    assert set(modified) <= {"tests/test_no_changelog_in_user_output.py"}, (
        f"pre-existing test files changed beyond the declared MAC-A harness fix: "
        f"{sorted(set(modified) - {'tests/test_no_changelog_in_user_output.py'})}")
