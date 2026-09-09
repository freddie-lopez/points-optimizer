"""Adversarial probe G: last sweep."""
import copy, json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src.formatter import print_leg_results, print_live_leg_detail
from src.live_trip import LiveOptions, apply_live
from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_leg
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.surcharge import SurchargeTable
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TD = date(2026, 9, 15)
UR = 160_000


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key"); c.clear_cache(); SeatsClient.reset_call_budget()
    yield c
    c.clear_cache(); SeatsClient.reset_call_budget()


def _mock_response(p):
    r = MagicMock(); r.json.return_value = p; r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _render(fn, *a, **kw):
    buf = StringIO(); fn(*a, console=Console(file=buf, width=200, no_color=True), **kw)
    return buf.getvalue()


class _F:
    def __init__(self, legs): self.legs = legs


def test_G1_whitespace_only_carrier_list_falls_through_to_carrier_unknown():
    t = SurchargeTable(ROOT / "data" / "surcharges.csv")
    kw = dict(region="NA-EU", cabin="J", departure_country="GB", is_round_trip=True)
    for carriers, label in [([" ", "\t", ""], "whitespace only"),
                            ([None], "None entry"),
                            ([" BA "], "padded single")]:
        e = t.resolve_ambiguous_metal("British Airways Executive Club", carriers, **kw)
        print(f"{label:<18} -> {e.render():<26} confidence={e.confidence}")
    ws = t.resolve_ambiguous_metal("British Airways Executive Club",
                                   [" ", "\t", ""], **kw)
    assert not ws.is_known
    none_entry = t.resolve_ambiguous_metal("British Airways Executive Club",
                                           [None], **kw)
    # str(None) == "None" -> a carrier code literally called NONE
    print("None entry notes:", none_entry.notes[:140])
    assert not none_entry.is_known
    assert "NONE" in none_entry.notes


@patch("src.seats_client.requests.get")
def test_G2_a_live_leg_with_an_unknown_surcharge_shows_a_floor_and_a_break_even(
    mock_get, client, rm
):
    """Step 8's acceptance, re-checked: never a blank, a dash, or a $0.00."""
    row = copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])
    row["Route"]["Source"] = "flyingblue"       # a program with NO tier-5 zero
    row["YAirlines"] = "AF"                     # single carrier
    mock_get.return_value = _mock_response({"data": [row]})
    leg = Leg(id="L1", kind="flight", description="SFO->MAD",
              date=date(2027, 1, 15), origin="SFO", destination="MAD",
              cash_options=[CashOption(label="c", amount=900.0)])
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    print("carrier_source:", cand.carrier_source, "has_known_metal:",
          cand.has_known_metal)
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TD, show_alternatives=False)
    print("verdict:", res.verdict)
    print("floor  :", res.points_floor_usd, "break-even:", res.break_even_surcharge_usd)
    out = _render(print_leg_results, [res]) + _render(print_live_leg_detail, [res])
    print(out)
    assert res.points_floor_usd is not None
    assert res.break_even_surcharge_usd is not None
    assert "$0.00" not in out
    assert "UNKNOWN" in out


@patch("src.seats_client.requests.get")
def test_G3_the_api_tax_figure_is_dropped_entirely_on_the_unscoreable_path(
    mock_get, client, rm
):
    """
    On the floor+break-even path the candidate's cash_surcharge is forced to
    0.0, so the API's OWN known CAD 44.60 never reaches the floor. The floor is
    therefore lower than the least this can possibly cost.
    """
    row = copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])
    row["Route"]["Source"] = "flyingblue"
    row["YAirlines"] = "AF"
    mock_get.return_value = _mock_response({"data": [row]})
    leg = Leg(id="L1", kind="flight", description="SFO->MAD",
              date=date(2027, 1, 15), origin="SFO", destination="MAD",
              cash_options=[CashOption(label="c", amount=900.0)])
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TD, show_alternatives=False)
    print("candidate cash_surcharge:", cand.cash_surcharge)
    print("floor                    :", res.points_floor_usd)
    print("50,000 pts @1cpp         : 500.00   + known CAD 44.60 = 532.36")
    assert cand.cash_surcharge == 0.0
    assert res.points_floor_usd == 500.0, "the known $32.36 of taxes is NOT in the floor"
