"""
Re-test 2, R2-4: capture's final verdict is the label check's verdict.

"CAPTURED CLEAN ... set TRIPS_SCHEMA_VERIFIED_BY" (exit 0) is printed only for a
file `schema_verification_problems` accepts. A capture the check refuses - the
route inferred with no local row, a raw body that is not the page, an out-dir
that is not real/ - exits 5, lists the check's reasons, and says not to set the
constant.
"""
import pytest

from src import seats_trips, trips_tools
from src.seats_client import SeatsClient
from tests.test_metal_end_to_end import B4_ID
from tests.test_trips_tools import FLAG_KEY, Stub, capture_args, run

ADVICE = "set TRIPS_SCHEMA_VERIFIED_BY"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _verdict_agrees(code, out, directory):
    path = next(directory.glob("*.json"))
    problems = seats_trips.schema_verification_problems(path.name, directory)
    if problems:
        assert code == trips_tools.EXIT_DRIFT, out
        assert "CAPTURED CLEAN" not in out
        assert "CANNOT FLIP THE UNVERIFIED LABEL" in out
        assert "Do NOT set TRIPS_SCHEMA_VERIFIED_BY" in out
        for p in problems:
            assert " ".join(p.split()) in out
    else:
        assert code == 0, out
        assert "CAPTURED CLEAN" in out and ADVICE in out
    return problems


def test_an_inferred_route_capture_exits_5_and_gives_no_flip_advice(tmp_path):
    argv = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", FLAG_KEY, "--yes"]
    code, out, _ = run(argv)
    problems = _verdict_agrees(code, out, tmp_path / "real")
    assert any("route_inferred_from_itineraries" in p for p in problems)


def test_a_raw_body_that_is_not_the_page_exits_5(tmp_path):
    code, out, _ = run(capture_args(tmp_path), stub=Stub(raw_text='{"data": []}'))
    problems = _verdict_agrees(code, out, tmp_path / "real")
    assert any(".raw.txt" in p for p in problems)


def test_a_capture_outside_real_exits_5(tmp_path):
    argv = capture_args(tmp_path)
    argv[argv.index("--out-dir") + 1] = str(tmp_path / "elsewhere")
    code, out, _ = run(argv)
    problems = _verdict_agrees(code, out, tmp_path / "elsewhere")
    assert any("is not the real/ capture directory" in p for p in problems)


def test_a_genuine_capture_still_says_clean_and_the_check_agrees(tmp_path):
    code, out, _ = run(capture_args(tmp_path))
    assert _verdict_agrees(code, out, tmp_path / "real") == []
