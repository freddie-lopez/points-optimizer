"""
I. THE STRUCTURAL GUARDS, UNDER -O. Ways nine, ten and the metal classification
refuse at IMPORT, with `raise`, not `assert`, so `python -O` cannot switch them
off. The UI adds a package (`src/ui/`) next to the modules those guards scan.

These probes prove the guards still fire - by breaking a COPY of the tree in
tmp, never this one - and that `src/ui/` neither disturbs nor escapes them.

RED = a defect that exists. GREEN = held up.
"""
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT

ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"}


def _tree(tmp_path):
    dest = tmp_path / "tree"
    dest.mkdir()
    shutil.copytree(ROOT / "src", dest / "src")
    shutil.copytree(ROOT / "data", dest / "data")
    return dest


def _import(tree, flags=("-O",), module="src.models"):
    return subprocess.run([sys.executable, *flags, "-c", f"import {module}"],
                          cwd=str(tree), capture_output=True, text=True, env=ENV)


def test_I1_the_guards_import_cleanly_in_this_tree_with_and_without_O(tmp_path):
    tree = _tree(tmp_path)
    for flags in ((), ("-O",), ("-OO",)):
        p = _import(tree, flags)
        assert p.returncode == 0, (flags, p.stderr[-800:])


def test_I2_way_ten_still_refuses_under_O_when_a_leg_unknown_is_added(tmp_path):
    """Add an `add_reason("SOMETHING_UNKNOWN", ...)` with no trip-level answer."""
    tree = _tree(tmp_path)
    opt = tree / "src" / "optimizer.py"
    opt.write_text(opt.read_text() + '\n\ndef _probe_unknown(result):\n'
                   '    result.add_reason("PROBE_TESTER_UNKNOWN", "x")\n')
    for flags in ((), ("-O",), ("-OO",)):
        p = _import(tree, flags)
        assert p.returncode != 0, (flags, "way (10) did not refuse")
        assert "way (10)" in p.stderr, p.stderr[-500:]


def test_I3_a_guard_in_src_ui_cannot_hide_from_way_ten(tmp_path):
    """`_reason_codes_in_source()` globs `src/*.py` only. A reason code written
    inside `src/ui/` is therefore INVISIBLE to way ten. That is only safe while
    src/ui/ writes no reason codes at all."""
    import ast

    offenders = []
    for path in sorted((ROOT / "src" / "ui").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "add_reason":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders


def test_I4_the_ui_package_does_not_change_what_the_guards_discover(tmp_path):
    """Deleting src/ui/ must not change the discovered unknown vocabulary."""
    a = subprocess.run([sys.executable, "-c",
                        "import src.models as m; print(sorted(m.leg_level_unknowns()))"],
                       cwd=str(ROOT), capture_output=True, text=True, env=ENV)
    tree = _tree(tmp_path)
    shutil.rmtree(tree / "src" / "ui")
    b = subprocess.run([sys.executable, "-c",
                        "import src.models as m; print(sorted(m.leg_level_unknowns()))"],
                       cwd=str(tree), capture_output=True, text=True, env=ENV)
    assert a.returncode == 0 and b.returncode == 0, (a.stderr[-400:], b.stderr[-400:])
    assert a.stdout == b.stdout


def test_I5_the_ui_modules_import_under_O(tmp_path):
    for module in ("src.ui.server", "src.ui.engine", "src.ui.api", "src.ui.serialize"):
        p = subprocess.run([sys.executable, "-O", "-c", f"import {module}"],
                           cwd=str(ROOT), capture_output=True, text=True, env=ENV)
        assert p.returncode == 0, (module, p.stderr[-600:])
