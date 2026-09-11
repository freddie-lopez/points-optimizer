"""
Re-test 2, R2-7: every printed line that names the looked-up metal carries the
trips parser's UNVERIFIED label while TRIPS_SCHEMA_VERIFIED_BY is empty.

Checked on the printed output (one console line per print, so a label is on
the same line as the metal it qualifies), across the default flow, an
excludes_yq verdict (case C: the looked-up metal keys the band), and same-metal
alternatives from looked-up metal. Data lines (flags, warnings, provenance) are
escaped, so the bracketed label survives rich markup.
"""
import re
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import seats_trips, trips_tools
from src.formatter import print_alternatives, print_leg_detail, print_live_leg_detail
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_alternatives import AFStub, _af
from tests.test_trips_tools import Stub, yq_args
from tests.test_yq_inclusion import rm, scored, verdict  # noqa: F401 - fixture reuse

LABEL = seats_trips.PARSER_UNVERIFIED_LABEL
# Phrases that name metal the itinerary lookup found.
LOOKED_UP = re.compile(
    r"[A-Z0-9]{2}(, [A-Z0-9]{2})* metal under|by flight number|itinerary lookup found"
    r"|source: seats_aero_trips|metal=[A-Z0-9]{2}.*via|on [A-Z0-9]{2} metal - surcharge"
)

pytestmark = pytest.mark.skipif(
    seats_trips.TRIPS_SCHEMA_VERIFIED_BY != "", reason="the label has been flipped"
)


def _printed(legs):
    buf = StringIO()
    console = Console(file=buf, width=5000)
    results = list(legs.values())
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    print_alternatives(results, console)
    return buf.getvalue().splitlines()


def _bare(lines):
    return [l.strip()[:200] for l in lines if LOOKED_UP.search(l) and LABEL not in l]


def test_default_flow_band_and_surcharge_notes_carry_the_label(rm):
    SeatsClient.CACHE.clear()
    legs, _ = scored(rm, None)
    b4 = legs["B4"]
    assert b4.best_points.metal_surcharge_note.endswith(LABEL)
    assert "names VS by flight number" in b4.surcharge.notes and LABEL in b4.surcharge.notes
    lines = _printed(legs)
    assert any("VS metal under" in l for l in lines), "precondition: the band note"
    assert _bare(lines) == []


def test_case_c_lines_carry_the_label(rm):
    SeatsClient.CACHE.clear()
    legs, _ = scored(rm, verdict(which="excludes_yq"))
    b4 = legs["B4"]
    assert b4.best_points.carrier_source == "seats_aero_trips"
    assert "itinerary lookup found (VS, by flight number)" in b4.best_points.source_note
    assert "(VS, by flight number) is ADDED" in b4.best_points.source_note
    assert LABEL in b4.best_points.source_note
    lines = _printed(legs)
    assert any("source: seats_aero_trips" in l for l in lines), "precondition: legacy line"
    assert any("metal=VS" in l for l in lines), "precondition: the rule line"
    assert _bare(lines) == []


def test_alternatives_headline_carries_the_label(rm):
    SeatsClient.CACHE.clear()
    legs, _ = scored(rm, None, stub=AFStub([_af()]))
    alts = legs["B4"].alternatives
    assert alts and all(a.metal_label == LABEL for a in alts)
    lines = _printed(legs)
    assert any(re.search(r"on [A-Z0-9]{2} metal - surcharge", l) for l in lines)
    assert _bare(lines) == []


def test_the_inconclusive_warning_names_looked_up_metal_with_the_label(tmp_path):
    SeatsClient.reset_call_budget()
    # The Virgin Atlantic award flies a DL flight number: no nonzero surcharge
    # row for that metal, so the warning names the looked-up carrier.
    buf = StringIO()
    stub = Stub(trips=tp.payload([tp.vs_direct("DL41")]))
    with patch("src.seats_client.requests.get", side_effect=stub):
        trips_tools.main(yq_args(tmp_path), read=lambda _: "y",
                         console=Console(file=buf, width=5000), today=date(2026, 9, 11))
    warnings = [
        l for l in buf.getvalue().splitlines()
        if "likely INCONCLUSIVE" in l and "DL metal has no nonzero surcharge row" in l
    ]
    assert warnings, "precondition: the warning names the looked-up DL metal"
    assert all(LABEL in line for line in warnings)


def test_an_alternative_from_known_metal_has_no_label():
    from src.alternatives import find_same_metal_alternatives
    from src.models import PointsCandidate, SurchargeEstimate
    from src.ratio_manager import RatioManager
    from src.surcharge import default_table
    from tests.test_yq_inclusion import ROOT

    ratios = RatioManager(ROOT / "data/ratios.csv", ROOT / "data/bonuses.csv", ROOT / "data/programs.yaml")
    cand = PointsCandidate(
        label="x", program="British Airways Executive Club", points=50000, cabin="J",
        operating_carrier="BA", carrier_source="captured",
    )
    alts = find_same_metal_alternatives(
        cand, SurchargeEstimate.unknown(), "NA-EU", "US", ratios, default_table(),
        ["UR"], date(2026, 9, 15), 1000.0,
    )
    assert alts and all(a.metal_label == "" for a in alts)
