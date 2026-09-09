"""
Adversarial probe D: multi-currency stranding at 160,000 UR, end-to-end
consequences of the parser holes, and CLI-level behaviour.
"""
import copy
import json
import subprocess
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src import config
from src.formatter import print_leg_results, print_trip_totals
from src.funding import all_plans, best_plan, plan_stranding_ceiling, residue_report
from src.live_trip import LiveOptions, apply_live, provenance_counts
from src.models import CashOption, Leg, PointsCandidate
from src.optimizer import evaluate_leg, evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient, parse_availability_row
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)
UR = 160_000


@pytest.fixture
def rm():
    return RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv",
                        ROOT / "data" / "programs.yaml")


@pytest.fixture
def rm_multi():
    return RatioManager(ROOT / "tests" / "fixtures" / "ratios_multicurrency_test.csv",
                        ROOT / "tests" / "fixtures" / "bonuses_test.csv",
                        ROOT / "data" / "programs.yaml")


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


def _mock_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _render(fn, *a, **kw):
    buf = StringIO()
    fn(*a, console=Console(file=buf, width=200, no_color=True), **kw)
    return buf.getvalue()


def _real_row():
    return copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])


class _F:
    def __init__(self, legs):
        self.legs = legs


def _leg(**kw):
    base = dict(id="L1", kind="flight", description="SFO->MAD",
                date=date(2027, 1, 15), origin="SFO", destination="MAD",
                cash_options=[CashOption(label="c", amount=395.0, currency="USD")],
                points_candidates=[])
    base.update(kw)
    return Leg(**base)


# =========================================================================
# D1. R1-R4 at Tsuki's real 160,000 UR, split across currencies
# =========================================================================


def test_D1_split_funding_at_160000_UR_strands_one_block_not_two(rm_multi):
    """R2's trap, with a real balance. 200,500 BA from 160,000 UR + 50,000 MR."""
    w = Wallet({"UR": UR, "MR": 50_000}, [CSP])
    plans = all_plans(w, "British Airways Executive Club", 200_500,
                      TRANSFER_DATE, rm_multi)
    for p in plans[:4]:
        print(f"{p.spend_summary():<40} delivered={p.delivered:,} "
              f"stranded={p.stranded_points} score=${p.score_usd:,.2f} "
              f"currencies={p.currencies_used}")
    best = plans[0]
    assert best.currencies_used == 2
    assert best.stranded_points <= 999, "one block, not two"
    assert best.delivered >= 200_500
    assert sum(best.per_currency_spend.values()) <= UR + 50_000


def test_D2_no_split_when_160000_UR_alone_suffices(rm_multi):
    w = Wallet({"UR": UR, "MR": 50_000}, [CSP])
    plans = all_plans(w, "British Airways Executive Club", 50_000,
                      TRANSFER_DATE, rm_multi)
    print([(p.spend_summary(), p.currencies_used) for p in plans])
    assert all(p.currencies_used <= 1 for p in plans)


def test_D3_max_stranded_zero_still_rejects_at_160000(rm_multi):
    w = Wallet({"UR": UR}, [CSP])
    strict = best_plan(w, "British Airways Executive Club", 57_300,
                       TRANSFER_DATE, rm_multi, max_stranded_points=0)
    loose = best_plan(w, "British Airways Executive Club", 57_300,
                      TRANSFER_DATE, rm_multi)
    print("strict:", strict, "| loose:", loose.spend_summary(),
          "stranded", loose.stranded_points)
    assert strict is None
    assert loose.stranded_points == 700


def test_D4_a_three_way_split_is_never_built_even_when_needed(rm_multi):
    """
    160,000 UR + 50,000 MR + 50,000 TY = 260,000 available. Ask for 250,000.
    Two currencies max out at 210,000, so the award is reported UNFUNDABLE even
    though the wallet holds enough. R4 is doing this on purpose - but the
    infeasible_reason blames the balance, not the split cap.
    """
    w = Wallet({"UR": UR, "MR": 50_000, "TY": 50_000}, [CSP])
    p = best_plan(w, "British Airways Executive Club", 250_000, TRANSFER_DATE, rm_multi)
    print("best_plan:", p)
    infeasible = all_plans(w, "British Airways Executive Club", 250_000,
                           TRANSFER_DATE, rm_multi, include_infeasible=True)
    print("reason:", infeasible[0].infeasible_reason)
    assert p is None
    assert "balance" in infeasible[0].infeasible_reason.lower() or \
           "short" in infeasible[0].infeasible_reason.lower()
    assert "split" not in infeasible[0].infeasible_reason.lower()
    assert "MAX_SPLIT" not in infeasible[0].infeasible_reason


def test_D5_the_stranding_ceiling_is_taken_from_the_underfilled_transfer_too(rm_multi):
    """
    plan_stranding_ceiling takes a max over EVERY transfer, including the ones
    R2 guarantees cannot overshoot. The permitted ceiling is therefore larger
    than the achievable stranding whenever the last currency has the smaller
    block.
    """
    from src.models import Ratio
    one_to_one = Ratio("UR", "X", "all", 1, 1, date(2000, 1, 1), date(2099, 12, 31))
    four_to_three = Ratio("MR", "X", "all", 4, 3, date(2000, 1, 1), date(2099, 12, 31))
    # UR under-fills first (block 999), MR is last (block 749)
    print("ceiling:", plan_stranding_ceiling([(one_to_one, 1000), (four_to_three, 1000)]))
    assert plan_stranding_ceiling([(one_to_one, 1000), (four_to_three, 1000)]) == 999
    assert plan_stranding_ceiling([(four_to_three, 1000)]) == 749


# =========================================================================
# D6. A parser hole reaching the user, end to end
# =========================================================================


@patch("src.seats_client.requests.get")
def test_D6_negative_taxes_become_a_cash_CREDIT_on_the_points_side(mock_get, client, rm):
    row = _real_row()
    row["YTotalTaxes"] = -50000        # CAD -500.00
    mock_get.return_value = _mock_response({"data": [row]})
    leg = _leg()
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    print("cash_surcharge:", cand.cash_surcharge, "captured:", cand.surcharge_captured)
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    print("surcharge:", res.surcharge.render(), "| points score:",
          res.points_total_score_usd, "| cash:", res.cash_total_score_usd)
    assert cand.cash_surcharge < 0
    assert res.points_total_score_usd < 500.0, "a negative tax made points cheaper"
    assert res.verdict == "points"
    assert res.margin_usd > 250
    out = _render(print_leg_results, [res])
    print(out)
    assert "$-362.80" in out and "captured" in out


@patch("src.seats_client.requests.get")
def test_D7_an_award_with_no_program_reaches_the_leg_as_a_nameless_candidate(
    mock_get, client, rm
):
    row = _real_row()
    row.pop("Route")                   # no program, no route, no region
    mock_get.return_value = _mock_response({"data": [row]})
    leg = _leg()
    apply_live(_F([leg]), client, LiveOptions(live=True))
    cand = leg.points_candidates[0]
    print("label  :", cand.label)
    print("program:", repr(cand.program))
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm, wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    print("verdict:", res.verdict)
    print("warnings:", res.warnings)
    assert cand.program == ""
    assert res.verdict == "cash (no points path)"
    # The user is told there is NO UR PARTNER for the leg. The truth is the
    # response did not name a program.
    assert any("not a transfer partner" in w for w in res.warnings)
    assert not any("did not name" in w.lower() for w in res.warnings)


# =========================================================================
# D8. The provenance note asserts a source it has not checked
# =========================================================================


def test_D8_a_non_live_run_claims_every_points_price_is_a_google_badge(rm):
    """Even when the candidate's own `source` says otherwise."""
    leg = _leg(points_candidates=[PointsCandidate(
        label="captured off the Aeroplan booking page", program="Air Canada Aeroplan",
        points=50000, source="manual_capture", operating_carrier="AC",
        carrier_source="captured", cabin="Y")])
    results = evaluate_trip([leg], {"UR": UR}, [CSP], rm,
                            wallet=Wallet({"UR": UR}, [CSP]),
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    counts = provenance_counts(results)
    print("provenance:", counts["margin_provenance"])
    print("note      :", counts["margin_provenance_note"])
    assert counts["margin_provenance"] == "badge"
    assert "Google Flights badge" in counts["margin_provenance_note"]
    assert results[0].best_points.source == "manual_capture"


# =========================================================================
# D9. CLI-level: an unconfigured cash currency
# =========================================================================


def test_D9_cli_exit_code_for_an_unconfigured_cash_currency(tmp_path):
    fixture = tmp_path / "bad_currency.json"
    fixture.write_text(json.dumps({
        "id": "t", "name": "t", "description": "t", "source": "synthetic",
        "legs": [{
            "id": "X1", "kind": "flight", "description": "NRT->SFO",
            "date": "2027-01-15", "origin": "NRT", "destination": "SFO",
            "cash_options": [
                {"label": "JAL", "amount": 90000.0, "currency": "JPY"}
            ],
            "points_candidates": [],
        }],
    }))
    out = subprocess.run(
        ["python", "-m", "src.main", "--trip-fixture", str(fixture),
         "--balance", f"UR={UR}", "--card", CSP, "--transfer-date", "2026-09-15"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    print("exit:", out.returncode)
    print(out.stdout[-900:])
    print("STDERR:", out.stderr[-400:])
    assert out.returncode == 1
    assert "No FX rate configured for 'JPY'" in out.stdout
    # It is a clean exit - but the whole trip is abandoned for one leg's
    # currency, and the message names no leg.
    assert "X1" not in out.stdout.split("Error:")[-1]


# =========================================================================
# D10. python -O over the whole suite's honesty invariants
# =========================================================================


def test_D10_reason_code_validation_also_survives_python_O():
    code = (
        "from src.models import Reason\n"
        "try:\n"
        "    Reason(code='MADE_UP', detail='x')\n"
        "    print('NO RAISE')\n"
        "except ValueError:\n"
        "    print('RAISED')\n"
    )
    for flags in ([], ["-O"]):
        r = subprocess.run(["python", *flags, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True)
        print(flags or "(none)", "->", r.stdout.strip())
        assert r.stdout.strip() == "RAISED"


def test_D11_surcharge_table_validate_is_never_called_at_load_time():
    """A corrupt production table is only caught by a test, never by a run."""
    import inspect
    from src import surcharge
    assert "validate" not in inspect.getsource(surcharge.SurchargeTable.__init__)
    assert "validate" not in inspect.getsource(surcharge.default_table)
    import subprocess
    r = subprocess.run(
        ["grep", "-rn", r"\.validate(", "--include=*.py", "src/"],
        cwd=str(ROOT), capture_output=True, text=True)
    print("actual .validate() call sites in src/:", r.stdout.strip() or "(none)")
    assert r.stdout.strip() == ""
