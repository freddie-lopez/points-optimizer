"""
The Seats.aero source map: every published source is named, and a program UR
reaches only through British Airways Avios is never reported as "no path".

The published table (developers.seats.aero/reference/concepts-copy, read
2026-09-10) lists 26 source codes. Chase UR's airline partners (June 2026) are
Aer Lingus, Aeroplan, Flying Blue, British Airways, Iberia, JetBlue, KrisFlyer,
Southwest, United and Virgin Atlantic - six of which are Seats.aero sources.

Qatar Privilege Club and Finnair Plus are not Chase partners, but BA Club Avios
combine into both (BA's own combine page lists them). Before this, `qatar` was
unmapped - the Mac's first real snapshot carried one - and mapping it naively
would have printed "not a transfer partner of any currency you hold - no points
path", which is false about Tsuki's wallet.
"""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import VERDICT_INDIRECT_PATH, evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import (
    SEATS_AERO_SOURCES,
    parse_availability_row,
    resolve_indirect_path,
    resolve_source,
)
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
REAL_ROW = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)["data"][0]

PUBLISHED_SOURCES = {
    "eurobonus", "virginatlantic", "aeromexico", "american", "delta", "etihad",
    "united", "emirates", "aeroplan", "alaska", "velocity", "qantas",
    "connectmiles", "azul", "smiles", "flyingblue", "jetblue", "qatar",
    "turkish", "singapore", "ethiopian", "saudia", "finnair", "lufthansa",
    "frontier", "spirit",
}
DIRECT_UR = {"united", "aeroplan", "virginatlantic", "flyingblue", "jetblue", "singapore"}
INDIRECT_VIA_BA = {"qatar", "finnair"}


@pytest.mark.parametrize("code", sorted(PUBLISHED_SOURCES))
def test_every_published_source_is_named(code):
    program, _, note = resolve_source(code)
    assert program, f"{code} is in Seats.aero's published table and still unmapped"
    assert program != code, "a source code is not a program name"
    assert note


def test_the_direct_ur_set_is_exactly_chases_list_intersected_with_seats_aero():
    direct = {c for c, s in SEATS_AERO_SOURCES.items() if s.ur_transferable}
    assert direct == DIRECT_UR


def test_only_the_avios_family_carries_an_indirect_path():
    indirect = {c for c, s in SEATS_AERO_SOURCES.items() if s.indirect_ur_path}
    assert indirect == INDIRECT_VIA_BA


@pytest.mark.parametrize("code", sorted(INDIRECT_VIA_BA))
def test_the_indirect_path_names_its_hops_and_conditions(code):
    path = resolve_indirect_path(code)
    assert "Chase UR -> British Airways Club Avios" in path
    assert "30 days" in path
    assert "Not scored" in path
    assert SEATS_AERO_SOURCES[code].ur_transferable is False, "no DIRECT transfer"


def test_the_qatar_path_states_the_companion_rule():
    """Tsuki books for two. A path that silently fails for the second seat is not one."""
    assert "COMPANION" in resolve_indirect_path("qatar")


def test_an_unmapped_code_is_still_unattributed():
    program, ur, note = resolve_source("hawaiianairlines")
    assert program == "" and ur is None
    assert "NOT in the source->program map" in note


def test_a_parsed_qatar_award_says_indirect_and_never_no_path():
    row = copy.deepcopy(REAL_ROW)
    row["Route"]["Source"] = "qatar"
    (award,) = parse_availability_row(row)
    assert award.program == "Qatar Privilege Club"
    assert award.indirect_ur_path
    assert "INDIRECT UR PATH ONLY" in award.source_note
    assert "NO CHASE UR PATH" not in award.source_note
    # Qatar's taxes are not reported, so the figure the row carried is not believed.
    assert award.cash_component_known is False


# ---------------------------------------------------------------------------
# The scorer: an indirect-only award is its own verdict, and not "no path"
# ---------------------------------------------------------------------------


def _leg(candidates):
    return Leg(
        id="X1",
        kind="flight",
        description="LHR-SFO test leg",
        date=date(2027, 1, 27),
        cash_options=[CashOption(label="cash", amount=482.0)],
        points_candidates=candidates,
        origin="LHR",
        destination="SFO",
        cabin="Y",
    )


def _candidate(program, points, indirect=""):
    return PointsCandidate(
        label=f"LIVE {program} {points}",
        program=program,
        points=points,
        indirect_ur_path=indirect,
    )


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


def _evaluate(leg, rm):
    wallet = Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"])
    results = evaluate_trip(
        [leg], ratios_manager=rm, wallet=wallet,
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 10),
    )
    return results[0], trip_totals(results, wallet)


def test_an_indirect_only_leg_is_not_a_no_partner_leg(rm):
    r, totals = _evaluate(
        _leg([_candidate("Qatar Privilege Club", 33000, resolve_indirect_path("qatar"))]),
        rm,
    )
    assert r.verdict == VERDICT_INDIRECT_PATH
    assert "NOT a finding that no points path exists" in r.verdict_reason
    assert "COMPANION" in r.verdict_reason
    assert totals["legs_no_partner_ids"] == []
    assert totals["legs_indirect_path_unverified_ids"] == ["X1"]
    assert not any("no points path" in w for w in r.warnings)
    assert any(x.code == "INDIRECT_PATH_UNVERIFIED" for x in r.reasons)


def test_the_same_program_without_the_path_is_still_no_partner(rm):
    """The indirect branch keys on the PATH, not on the program name."""
    r, totals = _evaluate(_leg([_candidate("Qantas Frequent Flyer", 30000)]), rm)
    assert r.verdict == "cash (no points path)"
    assert totals["legs_no_partner_ids"] == ["X1"]


def test_a_direct_partner_beside_an_indirect_one_is_still_scored(rm):
    """An indirect award must not hide a scoreable direct one on the same leg."""
    r, _ = _evaluate(
        _leg([
            _candidate("Qatar Privilege Club", 20000, resolve_indirect_path("qatar")),
            _candidate("United MileagePlus", 30000),
        ]),
        rm,
    )
    assert r.has_points_path
    assert r.best_points.program == "United MileagePlus"
    assert any("reachable indirectly" in w for w in r.warnings)
