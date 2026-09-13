"""
Manager review, must-fix 1, as a regression test: with both label constants
EDITED IN SOURCE - the way the flip will really be done - the tests that read
them still pass.

A scratch copy of src/, data/ and tests/ is made in tmp. A genuine capture is
written into the copy's real/ directory by the stubbed capture tool, the
copy's `src/seats_trips.py` is edited to name it (and then also to set
TRIPS_TOTALTAXES_UNIT = "cents"), and pytest runs, in a child process, every
test file that reads either constant. Before the fix this was 6 failures, and
28 with "cents". The committed tree is never touched.

(The fix report also records the WHOLE suite in all three states.)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

# Every test file that reads TRIPS_SCHEMA_VERIFIED_BY or TRIPS_TOTALTAXES_UNIT,
# directly or through what they drive.
DEPENDENT = [
    "tests/test_metal_end_to_end.py",
    "tests/test_metal_lookup_model.py",
    "tests/test_seats_trips_parser.py",
    "tests/test_trip_taxes.py",
    "tests/test_trips_flags.py",
    "tests/test_trips_labels_everywhere.py",
    "tests/test_trips_labels_every_metal_line.py",
    "tests/test_trips_tools.py",
    "tests/test_yq_inclusion.py",
    "tests/test_trips_verification_label.py",
    "tests/test_trips_label_flip.py",
]

CAPTURE = r'''
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from rich.console import Console
from src import config, trips_tools
config.CACHE_DIR = root / "scratch-cache"
from tests.test_trips_tools import Stub, capture_args
real = root / "tests/fixtures/seats_aero/trips_endpoint/real"
argv = capture_args(Path("/unused"))
argv[argv.index("--out-dir") + 1] = str(real)
with patch("src.seats_client.requests.get", side_effect=Stub()):
    code = trips_tools.main(argv + ["--yes"], read=lambda _: "y",
                            console=Console(file=StringIO(), width=200))
print(code, next(real.glob("*.json")).name)
'''


def _copy(tmp_path):
    dst = tmp_path / "tree"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for name in ("src", "data", "tests"):
        shutil.copytree(ROOT / name, dst / name, ignore=ignore)
    shutil.copy(ROOT / "pytest.ini", dst / "pytest.ini")
    real = dst / "tests/fixtures/seats_aero/trips_endpoint/real"
    for p in real.glob("*"):
        p.unlink()
    return dst


def _pytest(dst):
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         *[f for f in DEPENDENT if (dst / f).is_file()]],
        cwd=dst, env=env, capture_output=True, text=True, timeout=600,
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:]
    return r.returncode, tail, r.stdout


@pytest.fixture(scope="module")
def flipped_tree(tmp_path_factory):
    committed = (ROOT / "src" / "seats_trips.py").read_text()
    if 'TRIPS_SCHEMA_VERIFIED_BY: str = ""' not in committed:
        pytest.skip("the label is already flipped in this tree: the whole suite is the check")
    dst = _copy(tmp_path_factory.mktemp("flip"))
    r = subprocess.run([sys.executable, "-c", CAPTURE, str(dst)], cwd=dst,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    code, name = r.stdout.split()
    assert code == "0", r.stdout
    shutil.rmtree(dst / "scratch-cache", ignore_errors=True)
    source = dst / "src" / "seats_trips.py"
    text = source.read_text()
    assert 'TRIPS_SCHEMA_VERIFIED_BY: str = ""' in text
    source.write_text(text.replace(
        'TRIPS_SCHEMA_VERIFIED_BY: str = ""', f'TRIPS_SCHEMA_VERIFIED_BY: str = "{name}"'
    ))
    return dst


def test_the_dependent_tests_pass_with_the_label_flipped_in_source(flipped_tree):
    code, tail, out = _pytest(flipped_tree)
    assert code == 0, "\n".join(l for l in out.splitlines() if l.startswith("FAILED")) or tail
    assert re.search(r"\d+ passed", tail)


def test_they_also_pass_with_the_unit_flipped_to_cents(flipped_tree):
    source = flipped_tree / "src" / "seats_trips.py"
    text = source.read_text()
    if 'TRIPS_TOTALTAXES_UNIT = "unverified"' not in text:
        pytest.skip("the unit is already flipped in this tree: the whole suite is the check")
    source.write_text(text.replace(
        'TRIPS_TOTALTAXES_UNIT = "unverified"', 'TRIPS_TOTALTAXES_UNIT = "cents"'
    ))
    code, tail, out = _pytest(flipped_tree)
    assert code == 0, "\n".join(l for l in out.splitlines() if l.startswith("FAILED")) or tail
