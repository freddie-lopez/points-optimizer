"""
Fix round 1, Tester finding 16: the label-flip checks ask what the README says.

`schema_verification_problems` now requires the `.raw.txt` sibling to be the
verbatim body OF THE PAGE (non-empty, JSON, equal), the recorded availability
row to be the row of the captured id, and the route NOT to have been inferred
from the itineraries. `totaltaxes_unit_problems("cents")` considers only a
capture that passes all of that.

Captures are written by the real tool into tmp_path with a stubbed transport.
"""
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.response_cache import content_hash
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row
from tests.test_trips_tools import FLAG_KEY, Stub, capture_args

ROOT = Path(__file__).parent.parent
SYNTHETIC = ROOT / "tests/fixtures/seats_aero/trips_endpoint/synthetic/openapi_example.json"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _capture(tmp_path, argv=None, stub=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=stub or Stub()):
        code = trips_tools.main(
            argv or capture_args(tmp_path), read=lambda _: "y",
            console=Console(file=buf, width=190),
        )
    real = tmp_path / "real"
    return code, next(real.glob("*.json")), real


def _problems(path, real):
    return " | ".join(seats_trips.schema_verification_problems(path.name, real))


def test_a_genuine_capture_still_flips_both(tmp_path):
    code, path, real = _capture(tmp_path)
    assert code == 0
    assert seats_trips.schema_verification_problems(path.name, real) == []
    assert seats_trips.totaltaxes_unit_problems("cents", real) == []


# -- the .raw.txt must be the body of the page --------------------------------


@pytest.mark.parametrize(
    "raw,needle",
    [
        ("", "is empty"),
        ("   \n", "is empty"),
        ("<html>gateway</html>", "is not JSON"),
        (json.dumps({"data": []}), "a different body"),
    ],
    ids=["empty", "blank", "not-json", "other-body"],
)
def test_a_raw_body_that_does_not_match_the_page_blocks_the_flip(tmp_path, raw, needle):
    code, path, real = _capture(tmp_path)
    path.with_suffix(".raw.txt").write_text(raw)
    assert needle in _problems(path, real)
    assert seats_trips.totaltaxes_unit_problems("cents", real) != []


def test_the_synthetic_example_rewrapped_as_real_cannot_flip(tmp_path):
    syn = json.loads(SYNTHETIC.read_text())
    page = syn["pages"][0] if "pages" in syn else syn
    aid = page["data"][0]["AvailabilityID"]
    real = tmp_path / "real"
    real.mkdir()
    first = page["data"][0]["AvailabilitySegments"]
    env = {
        "_meta": {
            "captured_by": seats_trips.CAPTURED_BY, "synthetic": False, "key_redacted": True,
            "availability_id": aid, "content_hash": content_hash([page]),
            "route": f"{first[0]['OriginAirport']}->{first[-1]['DestinationAirport']}",
            "availability_row": None,
        },
        "pages": [page],
    }
    (real / "forged.json").write_text(json.dumps(env))
    (real / "forged.raw.txt").write_text("")
    problems = _problems(real / "forged.json", real)
    assert "is empty" in problems
    assert "no availability row is recorded" in problems


# -- the matcher must have run against the row of this id ----------------------


def test_an_inferred_route_capture_cannot_flip(tmp_path):
    argv = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", FLAG_KEY, "--yes"]
    code, path, real = _capture(tmp_path, argv=argv)
    assert json.loads(path.read_text())["_meta"]["route_inferred_from_itineraries"] is True
    problems = _problems(path, real)
    assert "route_inferred_from_itineraries is True" in problems
    assert "no availability row is recorded" in problems


def test_a_capture_whose_row_is_another_ids_cannot_flip(tmp_path):
    code, path, real = _capture(tmp_path)
    envelope = json.loads(path.read_text())
    envelope["_meta"]["availability_row"]["ID"] = "9zZ9yY8xX7wW6vV5uU4tT3sS2rR"
    path.write_text(json.dumps(envelope))
    assert "the recorded availability row is '9zZ9yY8xX7wW6vV5uU4tT3sS2rR'" in _problems(path, real)


def test_a_capture_that_does_not_say_how_its_route_was_found_cannot_flip(tmp_path):
    code, path, real = _capture(tmp_path)
    envelope = json.loads(path.read_text())
    del envelope["_meta"]["route_inferred_from_itineraries"]
    path.write_text(json.dumps(envelope))
    assert "route_inferred_from_itineraries is None" in _problems(path, real)


# -- cents rests only on a capture that passes the schema check ---------------


def test_a_hand_written_file_cannot_flip_cents(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    row = vs_row()
    page = tp.payload([tp.vs_direct(taxes=row["JTotalTaxes"])])
    env = {"_meta": {"synthetic": False, "availability_id": B4_ID, "availability_row": row,
                     "content_hash": "not-a-hash"}, "pages": [page]}
    (real / "hand_written.json").write_text(json.dumps(env))
    assert seats_trips.totaltaxes_unit_problems("cents", real) != []


def test_a_genuine_capture_with_its_raw_body_deleted_cannot_flip_cents(tmp_path):
    code, path, real = _capture(tmp_path)
    path.with_suffix(".raw.txt").unlink()
    assert seats_trips.totaltaxes_unit_problems("cents", real) != []
