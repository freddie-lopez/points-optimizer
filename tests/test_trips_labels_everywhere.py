"""
Fix round 1, finding 3: every line derived from the trips parse carries the
UNVERIFIED parser label and says the carrier is the flight-number (marketing)
carrier - including the same-metal alternatives and the yq-check block.
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from src import trips_tools
from src.formatter import print_alternatives
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_alternatives import AFStub, _af
from tests.test_trips_tools import Stub, yq_args
from tests.test_yq_inclusion import rm, scored  # noqa: F401 - fixture reuse

LABEL = "[trips parser UNVERIFIED against a real Seats.aero response - built from the published schema only]"


def test_an_alternative_from_trips_metal_carries_the_label_and_the_marketing_caveat(rm):
    SeatsClient.CACHE.clear()
    legs, _ = scored(rm, None, stub=AFStub([_af()]))
    alts = legs["B4"].alternatives
    assert alts
    for alt in alts:
        assert LABEL in alt.note
        assert "MARKETING carrier named by flight number" in alt.note
    buf = StringIO()
    print_alternatives(list(legs.values()), Console(file=buf, width=250))
    out = " ".join(buf.getvalue().split())
    assert LABEL in out, "the bracketed label must survive rich markup"


def test_an_alternative_from_known_metal_is_unchanged():
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
    assert alts and all("UNVERIFIED" not in a.note for a in alts)


def test_the_yq_block_flights_and_carrier_lines_carry_the_label(tmp_path):
    SeatsClient.reset_call_budget()
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub()):
        trips_tools.main(yq_args(tmp_path), read=lambda _: "y",
                         console=Console(file=buf, width=250), today=date(2026, 9, 11))
    out = " ".join(buf.getvalue().split())
    block = out.split("YQ CHECK - compare against the program's own site", 1)[1]
    carrier = block.split("flight-number carrier:", 1)[1].split("seats:", 1)[0]
    flights = block.split("flights:", 1)[1].split("flight-number carrier:", 1)[0]
    assert LABEL in carrier and "MARKETING carrier by flight number" in carrier
    assert LABEL in flights
    record = next((tmp_path / "records").glob("*.md")).read_text()
    assert record.count(LABEL) >= 2, "the record copies the labelled lines"
