"""
Manager review, must-fix 3: what capture and yq-check tell Tsuki to do.

  * After drift, no "capture again": the parser is fixed against the SAME file,
    which then verifies with no new call (proved here by fixing the parser).
  * Exit 5 says which one test stays red until the parser is fixed - and that
    it is expected - or that no test turns red.
  * Neither tool tells him to edit src/seats_trips.py or data/yq_inclusion.csv:
    he sends the files back.
"""
import re
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_trips_tools import Stub, capture_args, yq_args

ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def _run(argv, stub):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=stub):
        code = trips_tools.main(argv, read=lambda _: "y", console=Console(file=buf, width=400))
    return code, " ".join(buf.getvalue().split()), stub


def test_the_named_drift_test_exists():
    path, name = trips_tools.DRIFT_TEST.split("::")
    assert re.search(rf"^def {name}\(", (ROOT / path).read_text(), re.M)


def test_required_drift_says_do_not_capture_again_and_names_the_one_red_test(tmp_path):
    stub = Stub(trips=tp.payload([tp.vs_direct(cabin="premium economy")]))
    code, out, _ = _run(capture_args(tmp_path), stub)
    assert code == trips_tools.EXIT_DRIFT
    assert "then capture again" not in out
    assert "do NOT capture again" in out
    assert "the same file then verifies with no new call" in out
    assert f"exactly one test is red until the parser is fixed - {trips_tools.DRIFT_TEST}" in out
    assert "send both files back" in out


def test_the_same_file_verifies_once_the_parser_is_fixed_with_no_new_call(tmp_path, monkeypatch):
    stub = Stub(trips=tp.payload([tp.vs_direct(cabin="premium economy")]))
    _run(capture_args(tmp_path), stub)
    real = tmp_path / "real"
    name = next(real.glob("*.json")).name
    assert seats_trips.schema_verification_problems(name, real)
    calls = len(stub.calls)
    # The one-line parser fix: the cabin word is mapped.
    monkeypatch.setitem(seats_trips.CABIN_FROM_TRIPS, "premium economy", "J")
    with patch("src.seats_client.requests.get") as get:
        assert seats_trips.schema_verification_problems(name, real) == []
    assert get.call_count == 0 and len(stub.calls) == calls


def test_drift_no_test_reads_says_no_test_turns_red(tmp_path):
    code, out, _ = _run(capture_args(tmp_path), Stub(trips=tp.payload([])))
    assert code == trips_tools.EXIT_DRIFT
    assert "Committing these files turns no test red" in out
    assert "exactly one test is red" not in out


def test_a_refused_capture_says_no_test_turns_red(tmp_path):
    argv = ["capture", "--availability-id", tp.AVAIL_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", "flag_key_for_trips_tools_0123456789", "--yes"]
    code, out, _ = _run(argv, Stub())
    assert code == trips_tools.EXIT_DRIFT
    assert "CANNOT FLIP THE UNVERIFIED LABEL" in out
    assert "Committing these files turns no test red" in out


def test_a_clean_capture_says_send_the_files_not_edit_the_source(tmp_path):
    code, out, _ = _run(capture_args(tmp_path), Stub())
    assert code == 0
    assert "Send both files back; do not edit src/seats_trips.py yourself" in out
    assert "The Coder commits them and will set TRIPS_SCHEMA_VERIFIED_BY = " in out
    assert "commit both files and set" not in out


def test_yq_check_says_send_the_record_not_edit_the_table(tmp_path):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        trips_tools.main(yq_args(tmp_path), read=lambda _: "y",
                         console=Console(file=buf, width=400))
    out = " ".join(buf.getvalue().split())
    assert "send back the record and the two capture files" in out
    assert "Do not edit data/yq_inclusion.csv yourself" in out
    assert "add this row to data/yq_inclusion.csv" not in out
