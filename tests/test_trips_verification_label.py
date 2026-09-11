"""
What flips the trips parser's UNVERIFIED label, and the TotalTaxes unit.

Both constants are pinned in BOTH branches. With the committed values (empty /
"unverified") the label is on every parse-derived line and in the banner. The
flipped branch is proven on a capture the real tool writes into tmp, and the
constant, if anyone sets it, must name a real capture that passes the same check.
"""
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.formatter import print_live_banner
from src.live_trip import LiveOptions, MetalPassReport
from src.models import MetalLookup, MetalStatus
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_trips_tools import Stub, capture_args

ROOT = Path(__file__).parent.parent
REAL_DIR = ROOT / "tests" / "fixtures" / "seats_aero" / "trips_endpoint" / "real"
SYNTHETIC_DIR = REAL_DIR.parent / "synthetic"
LABEL = "[trips parser UNVERIFIED against a real Seats.aero response - built from the published schema only]"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


# ---------------------------------------------------------------------------
# The committed branch
# ---------------------------------------------------------------------------


def test_the_committed_constants_are_the_unverified_ones_or_are_backed():
    name = seats_trips.TRIPS_SCHEMA_VERIFIED_BY
    if name:
        assert seats_trips.schema_verification_problems(name, REAL_DIR) == []
    assert seats_trips.totaltaxes_unit_problems(
        seats_trips.TRIPS_TOTALTAXES_UNIT, REAL_DIR
    ) == []


@pytest.mark.skipif(seats_trips.TRIPS_SCHEMA_VERIFIED_BY != "", reason="the label has been flipped")
def test_while_the_constant_is_empty_the_label_is_on_every_parse_derived_line():
    lookups = [
        MetalLookup(status=MetalStatus.KNOWN, carriers=("VS",), matched_trips=1,
                    provenance="seats_aero_trips", parser_verified=True),
        MetalLookup(status=MetalStatus.AMBIGUOUS, carrier_sets=(("VS",), ("DL",)),
                    possible_carriers=("VS", "DL"), matched_trips=2, provenance="seats_aero_trips"),
        MetalLookup(status=MetalStatus.UNKNOWN, reason_code="NO_MATCH", detail="x",
                    possible_carriers=("VS",)),
    ]
    for lookup in lookups:
        assert LABEL in lookup.render()
    assert seats_trips.trips_parser_label() == LABEL


@pytest.mark.skipif(seats_trips.TRIPS_SCHEMA_VERIFIED_BY != "", reason="the label has been flipped")
def test_while_the_constant_is_empty_the_banner_carries_the_label():
    buf = StringIO()
    opts = LiveOptions(live=True, trips_mode="auto")
    opts.metal_report = MetalPassReport(mode="auto", cap=10)
    print_live_banner([], opts, None, Console(file=buf, width=190))
    assert LABEL in " ".join(buf.getvalue().split())


def test_every_committed_real_capture_parses_without_required_field_drift():
    for path in sorted(REAL_DIR.glob("*.json")):
        parsed, envelope = seats_trips.capture_parse(path)
        assert parsed is not None, envelope
        assert parsed.required_drift == [], (path.name, parsed.required_drift)
        assert parsed.envelope_error is None, path.name


def test_the_real_directory_holds_no_synthetic_file():
    for path in sorted(REAL_DIR.glob("*.json")):
        parsed, envelope = seats_trips.capture_parse(path)
        assert envelope["_meta"].get("synthetic") is False, path.name


# ---------------------------------------------------------------------------
# The flipped branch, on a capture the tool writes into tmp
# ---------------------------------------------------------------------------


def _capture(tmp_path, stub=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=stub or Stub()):
        code = trips_tools.main(
            capture_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=190)
        )
    real = tmp_path / "real"
    return code, next(real.glob("*.json")), real


def test_a_clean_capture_passes_the_flip_check_and_the_label_drops(tmp_path, monkeypatch):
    code, path, real = _capture(tmp_path)
    assert code == 0
    assert seats_trips.schema_verification_problems(path.name, real) == []
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", path.name)
    parsed, envelope = seats_trips.capture_parse(path)
    facts = seats_trips.AwardFacts(
        availability_id=envelope["_meta"]["availability_id"], source_code="virginatlantic",
        cabin="J", cost=60000, row_carriers=("VS", "DL"), origin="LHR", destination="SFO",
    )
    lookup = seats_trips.match_award(parsed, facts)
    assert lookup.status is MetalStatus.KNOWN
    assert "UNVERIFIED" not in lookup.render()
    assert seats_trips.trips_parser_label() == ""


def test_a_capture_with_drift_fails_the_flip_check(tmp_path):
    drifted = tp.vs_direct(cost="60000")
    code, path, real = _capture(tmp_path, stub=Stub(trips=tp.payload([drifted])))
    assert code == 5
    problems = seats_trips.schema_verification_problems(path.name, real)
    assert any("required-field drift" in p for p in problems)


def test_the_constant_can_never_name_a_synthetic_file():
    problems = seats_trips.schema_verification_problems("openapi_example.json", SYNTHETIC_DIR)
    assert problems
    assert any("synthetic" in p or "real/" in p for p in problems)
    assert seats_trips.schema_verification_problems("../synthetic/openapi_example.json", REAL_DIR)


def test_a_tampered_or_orphaned_capture_fails(tmp_path):
    code, path, real = _capture(tmp_path)
    path.with_suffix(".raw.txt").unlink()
    assert any(".raw.txt" in p for p in seats_trips.schema_verification_problems(path.name, real))
    text = path.read_text().replace('"MileageCost": 60000', '"MileageCost": 60001')
    path.write_text(text)
    assert any("content_hash" in p for p in seats_trips.schema_verification_problems(path.name, real))


def test_a_missing_file_fails():
    assert seats_trips.schema_verification_problems("nope.json", REAL_DIR)


# ---------------------------------------------------------------------------
# TRIPS_TOTALTAXES_UNIT
# ---------------------------------------------------------------------------


def test_cents_is_allowed_only_on_a_capture_whose_row_matches(tmp_path):
    code, path, real = _capture(tmp_path)
    assert seats_trips.totaltaxes_unit_problems("cents", real) == []


def test_cents_is_refused_when_the_figures_differ(tmp_path):
    code, path, real = _capture(tmp_path, stub=Stub(trips=tp.payload([tp.vs_direct(taxes=450)])))
    assert seats_trips.totaltaxes_unit_problems("cents", real)


def test_cents_is_refused_with_no_capture_and_units_is_refused_always(tmp_path):
    assert seats_trips.totaltaxes_unit_problems("cents", tmp_path)
    code, path, real = _capture(tmp_path)
    assert seats_trips.totaltaxes_unit_problems("units", real)
    assert seats_trips.totaltaxes_unit_problems("pounds", real)
