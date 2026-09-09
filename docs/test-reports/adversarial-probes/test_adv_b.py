"""
Adversarial probe B: live mode, flex window, margin provenance, cache,
and the 160,000 UR feasibility ceiling.
"""
import copy
import json
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console

from src import config
from src.formatter import (
    print_leg_results,
    print_live_leg_detail,
    print_residue_report,
    print_trip_totals,
)
from src.live_trip import (
    LiveOptions,
    VERDICT_NO_LIVE_DATA,
    annotate_live_verdicts,
    apply_live,
    provenance_counts,
    query_leg,
)
from src.models import (
    CashOption,
    Leg,
    LiveLegOutcome,
    LiveQueryState,
    PointsCandidate,
    PointsProvenance,
)
from src.optimizer import evaluate_leg, evaluate_trip, trip_residue, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path("/home/claude/points-optimizer")
TRIPS = ROOT / "tests" / "fixtures" / "trips"
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
CSP = "Chase Sapphire Preferred"
TRANSFER_DATE = date(2026, 9, 15)
UR = 160_000        # Tsuki's real balance


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


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


def _row(day, cost=50000, taxes=4460, cur="CAD"):
    real = json.loads(REAL_FIXTURE.read_text())["data"][0]
    r = copy.deepcopy(real)
    r["Date"] = str(day)
    r["ParsedDate"] = f"{day}T00:00:00Z"
    r["YMileageCost"] = str(cost)
    r["YTotalTaxes"] = taxes
    r["TaxesCurrency"] = cur
    return r


class _Fixture:
    def __init__(self, legs):
        self.legs = legs
        self.id = "probe"
        self.name = "probe"


def _flight(lid, o, d, day, cash, cands=None, cash_opts=None):
    return Leg(
        id=lid, kind="flight", description=f"{o}->{d}", date=day,
        origin=o, destination=d,
        cash_options=cash_opts or [CashOption(label="c", amount=cash, currency="USD")],
        points_candidates=cands or [],
    )


# =========================================================================
# B1. --require-all-live does NOT withhold when a leg's API call failed
# =========================================================================


@patch("src.seats_client.requests.get")
def test_B1_an_api_error_leg_with_no_badge_still_yields_margin_provenance_live(
    mock_get, client, rm
):
    """
    provenance_counts() computes `live` from legs that HAVE candidates. A leg
    whose API call failed and which carries no badge is invisible to that test,
    so a run with a dead leg is labelled a LIVE margin and --require-all-live
    does not withhold it.
    """
    legs = [
        _flight("L1", "SFO", "MAD", date(2027, 1, 15), 395.0),
        _flight("L2", "MAD", "AMS", date(2027, 1, 19), 44.0),
    ]

    def side_effect(url, **kw):
        if kw["params"]["origin_airport"] == "MAD":
            raise requests.exceptions.ReadTimeout("HTTPSConnectionPool timeout")
        return _mock_response({"data": [_row(date(2027, 1, 15))]})

    mock_get.side_effect = side_effect
    apply_live(_Fixture(legs), client, LiveOptions(live=True))
    results = annotate_live_verdicts(
        evaluate_trip(legs, {"UR": UR}, [CSP], rm,
                      wallet=Wallet({"UR": UR}, [CSP]),
                      transfer_date=TRANSFER_DATE, show_alternatives=False)
    )
    totals = trip_totals(results)
    print("provenance      :", totals["margin_provenance"])
    print("note            :", totals["margin_provenance_note"])
    print("legs_api_error  :", totals["legs_api_error"], totals["legs_api_error_ids"])
    print("beat_cash_pct   :", totals["beat_cash_pct_low"], totals["beat_cash_pct_high"])

    assert totals["legs_api_error"] == 1
    # THE BUG: an API failure and provenance still reads "live".
    assert totals["margin_provenance"] == "live"
    out = _render(print_trip_totals, totals)
    assert "margin provenance" in out and "live" in out
    # --require-all-live is driven off exactly this string in main.py
    assert totals["margin_provenance"] == "live", (
        "--require-all-live will NOT withhold this margin"
    )


# =========================================================================
# B2. A promoted off-date award is scored against the WRONG date's cash price
# =========================================================================


@patch("src.seats_client.requests.get")
def test_B2_promoted_off_date_award_is_scored_against_the_cheapest_date_not_its_own(
    mock_get, client, rm
):
    """
    Section 4.5's escape hatch: capture cash for the award's date and the award
    is promoted. But evaluate_leg picks the CHEAPEST cash option across ALL
    dates, so the Jan-17 award is scored against the Jan-15 fare.
    """
    leg = _flight(
        "L1", "SFO", "MAD", date(2027, 1, 15), 0.0,
        cash_opts=[
            CashOption(label="Jan 15 fare", amount=395.0, currency="USD",
                       date=date(2027, 1, 15), source="captured_screenshot"),
            CashOption(label="Jan 17 fare", amount=900.0, currency="USD",
                       date=date(2027, 1, 17), source="captured_screenshot"),
        ],
    )
    # ONLY an off-date award comes back.
    mock_get.return_value = _mock_response({"data": [_row(date(2027, 1, 17))]})
    apply_live(_Fixture([leg]), client, LiveOptions(live=True, flex_days=3))

    print("date_shifted   :", leg.date_shifted, "->", leg.date_shifted_to)
    print("candidates     :", [c.label for c in leg.points_candidates])

    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm,
                       wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    print("cash used      :", res.best_cash.label, res.cash_total_score_usd)
    print("points score   :", res.points_total_score_usd)
    print("verdict        :", res.verdict)

    assert leg.date_shifted is True and leg.date_shifted_to == date(2027, 1, 17)
    # The award is FOR Jan 17 ($900 cash). It is scored against Jan 15 ($395).
    assert res.best_cash.date == date(2027, 1, 15)
    assert res.cash_total_score_usd == 395.0
    assert res.verdict == "cash"


@patch("src.seats_client.requests.get")
def test_B2b_the_bias_runs_the_other_way_too_and_hands_points_a_free_win(
    mock_get, client, rm
):
    """The same defect with the prices swapped: points 'win' a leg they lose."""
    leg = _flight(
        "L1", "SFO", "MAD", date(2027, 1, 15), 0.0,
        cash_opts=[
            CashOption(label="Jan 15 fare", amount=900.0, currency="USD",
                       date=date(2027, 1, 15), source="captured_screenshot"),
            CashOption(label="Jan 17 fare", amount=400.0, currency="USD",
                       date=date(2027, 1, 17), source="captured_screenshot"),
        ],
    )
    # An award only on the leg's OWN date (Jan 15, where cash is $900).
    mock_get.return_value = _mock_response({"data": [_row(date(2027, 1, 15))]})
    apply_live(_Fixture([leg]), client, LiveOptions(live=True, flex_days=3))
    res = evaluate_leg(leg, {"UR": UR}, [CSP], rm,
                       wallet=Wallet({"UR": UR}, [CSP]),
                       transfer_date=TRANSFER_DATE, show_alternatives=False)
    print("cash used   :", res.best_cash.label, res.cash_total_score_usd)
    print("points      :", res.points_total_score_usd, "verdict:", res.verdict)
    # An award for Jan 15 scored against a Jan-17 fare it cannot be booked with.
    assert res.best_cash.date == date(2027, 1, 17)
    assert res.cash_total_score_usd == 400.0
    assert leg.date_shifted is False   # nothing says a date was mixed


# =========================================================================
# B3. Feasibility: three legs each fund from the same 160,000 UR
# =========================================================================


def test_B3_the_trip_recommends_spending_more_UR_than_Tsuki_has(rm):
    """
    Every leg is funded independently against the FULL balance. At 160,000 UR
    the tool happily recommends a plan costing 210,000 UR, prints a headline
    percentage for it, and never marks the recommendation infeasible.
    """
    def leg(lid, cash):
        return Leg(
            id=lid, kind="flight", description=f"{lid}", date=date(2027, 1, 15),
            origin="SFO", destination="MAD",
            cash_options=[CashOption(label="c", amount=cash, currency="USD")],
            points_candidates=[
                PointsCandidate(
                    label=f"{lid} United", program="United MileagePlus",
                    points=70000, source="seats_aero",
                    operating_carrier="UA", carrier_source="seats_aero", cabin="Y",
                )
            ],
        )

    legs = [leg("L1", 900.0), leg("L2", 900.0), leg("L3", 900.0)]
    wallet = Wallet({"UR": UR}, [CSP])
    results = evaluate_trip(legs, {"UR": UR}, [CSP], rm, wallet=wallet,
                            transfer_date=TRANSFER_DATE, show_alternatives=False)
    totals = trip_totals(results)
    residue = trip_residue(results, wallet)

    print("verdicts     :", [r.verdict for r in results])
    print("points_spent :", totals["points_spent"])
    print("beat_cash_pct:", totals["beat_cash_pct"])
    print("residue      :", residue)

    assert [r.verdict for r in results] == ["points", "points", "points"]
    assert totals["points_spent"] == 210_000     # against a 160,000 balance
    assert totals["beat_cash_pct"] > 0
    assert residue["UR"]["remaining"] == -50_000
    assert residue["UR"]["overdrawn"] is True
    # Nothing in the headline says the plan cannot be executed:
    out = _render(print_trip_totals, totals)
    print(out)
    assert "OVERDRAW" not in out.upper()
    assert "infeasible" not in out.lower()
    assert f"{totals['beat_cash_pct']:.2f}%" in out


def test_B3b_the_residue_table_is_the_only_place_it_shows_and_only_as_a_note(rm):
    """The overdraft is a note in a table the CLI prints AFTER the headline."""
    wallet = Wallet({"UR": UR}, [CSP])
    report = {"UR": {"starting": UR, "spent": 210000, "remaining": -50000,
                     "unconstrained": False, "too_small_to_use": False,
                     "overdrawn": True}}
    out = _render(print_residue_report, report)
    print(out)
    assert "OVERDRAWN" in out


# =========================================================================
# B4. Stranding at a real balance: 160,000 UR
# =========================================================================


def test_B4_stranding_and_balance_ceiling_at_160000_UR(rm):
    """Does the ceiling actually bind at Tsuki's number? Exercise both sides."""
    from src.funding import best_plan, all_plans

    wallet = Wallet({"UR": UR}, [CSP])
    for need in (159_000, 160_000, 160_001, 161_000):
        p = best_plan(wallet, "United MileagePlus", need, TRANSFER_DATE, rm)
        print(f"need={need:>7,}  plan={'None' if p is None else p.spend_summary()}"
              f"  stranded={'-' if p is None else p.stranded_points}")
    assert best_plan(wallet, "United MileagePlus", 160_001, TRANSFER_DATE, rm) is None
    assert best_plan(wallet, "United MileagePlus", 160_000, TRANSFER_DATE, rm) is not None
    # 159,001 needs 160,000 UR (1,000-point increments) and strands 999.
    p = best_plan(wallet, "United MileagePlus", 159_001, TRANSFER_DATE, rm)
    print("159,001 ->", p.spend_summary(), "stranded", p.stranded_points)
    assert p.per_currency_spend["UR"] == 160_000 and p.stranded_points == 999


def test_B5_max_split_currencies_above_2_is_silently_ignored(rm):
    """
    config.MAX_SPLIT_CURRENCIES is documented as overridable. all_plans() only
    ever builds permutations of TWO, so raising it changes nothing and no
    warning is emitted.
    """
    import inspect
    from src import funding
    src = inspect.getsource(funding.all_plans)
    assert "permutations(sources, 2)" in src
    assert "max_split >= 2" in src
    # max_split is only ever compared against the literal 2; a value of 3 or 5
    # produces exactly the same plans as 2.
    assert "max_split >= 3" not in src and "range(2, max_split" not in src


# =========================================================================
# B6. Cache
# =========================================================================


@patch("src.seats_client.requests.get")
def test_B6_refresh_is_defeated_by_the_in_memory_cache(mock_get, client, tmp_path):
    """
    --refresh bypasses the DISK cache. It never reaches it: SeatsClient.CACHE
    short-circuits `search()` first, so a re-run in the same process returns
    stale parsed Awards and reports "served from cache; no API call made".
    """
    from src.models import DateRange
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    mock_get.return_value = _mock_response({"data": [_row(date(2027, 1, 15))]})
    dr = DateRange(date(2027, 1, 15), date(2027, 1, 15))

    a1 = client.search("SFO", "MAD", dr, cache=cache)
    calls_after_first = mock_get.call_count
    # now the upstream data changes
    mock_get.return_value = _mock_response({"data": [_row(date(2027, 1, 15), cost=25000)]})
    a2 = client.search("SFO", "MAD", dr, cache=cache, refresh=True)
    print("http calls:", mock_get.call_count, "(after first:", calls_after_first, ")")
    print("cost seen with --refresh:", a2[0].cost)
    print("note:", client.last_pagination_note)
    assert mock_get.call_count == calls_after_first, "--refresh made no HTTP call"
    assert a2[0].cost == 50000, "--refresh returned the STALE price"
    assert client.last_pagination_note == "served from cache; no API call made"


@patch("src.seats_client.requests.get")
def test_B6b_the_in_memory_cache_erases_the_live_fetched_at_timestamp(
    mock_get, client, tmp_path
):
    """
    An honesty invariant in the plan: `seats_aero_live` never renders without
    its fetched-at timestamp. The second call through the in-memory cache
    reports "Fetched during this run" for bytes fetched at some earlier time.
    """
    from src.models import DateRange
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    mock_get.return_value = _mock_response({"data": [_row(date(2027, 1, 15))]})
    dr = DateRange(date(2027, 1, 15), date(2027, 1, 15))
    client.search("SFO", "MAD", dr, cache=cache)
    first_stamp = client.last_fetched_at
    client.search("SFO", "MAD", dr, cache=cache)
    print("first fetched_at :", first_stamp)
    print("second fetched_at:", client.last_fetched_at)
    assert first_stamp is not None
    assert client.last_fetched_at is None
    assert client.last_served_from_cache is False   # claims it was NOT cached


def test_B6c_cache_ttl_boundary_is_inclusive(tmp_path):
    from datetime import datetime, timezone
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s",
                          ttl_seconds=3600)
    t0 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    cache.put("k", {"origin_airport": "SFO"}, [{"data": []}], now=t0)
    exactly = cache.get("k", now=t0 + timedelta(seconds=3600))
    one_more = cache.get("k", now=t0 + timedelta(seconds=3601))
    print("age==ttl ->", "HIT" if exactly else "miss")
    print("age>ttl  ->", "HIT" if one_more else "miss")
    assert exactly is not None and one_more is None


def test_B6d_a_corrupt_cache_file_is_a_miss_with_a_warning(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    cache.cache_dir.mkdir(parents=True)
    (cache.cache_dir / "k.json").write_text('{"pages": [ truncat')
    assert cache.get("k") is None
    print(cache.warnings)
    assert any("MISS" in w for w in cache.warnings)


def test_B6e_a_cache_file_whose_pages_are_an_empty_list_is_a_HIT(tmp_path):
    """
    A truncated/failed fetch that still wrote an envelope with zero pages is a
    valid cache HIT, and re-answers as NO_AWARD_SPACE for the whole TTL.
    """
    from datetime import datetime, timezone
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    t0 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    cache.put("k", {"origin_airport": "SFO"}, [], now=t0)
    hit = cache.get("k", now=t0 + timedelta(seconds=60))
    print("hit:", hit is not None, "pages:", None if hit is None else hit.pages)
    assert hit is not None and hit.pages == []


# =========================================================================
# B7. Budget
# =========================================================================


@patch("src.seats_client.requests.get")
def test_B7_budget_exhausted_inside_pagination_becomes_no_award_space(mock_get, client):
    """
    apply_live guards the budget BEFORE a leg. It does not guard pagination
    inside a leg. Exhaust the budget on page 1 of a paginated response and the
    leg answers with page-one data only; exhaust it before ANY page and the leg
    answers with an empty result the caller reads as a finding.
    """
    from src.models import DateRange
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP   # 1000/1000 spent
    outcome, awards = query_leg(
        Leg(id="L1", kind="flight", description="x", date=date(2027, 1, 15),
            origin="SFO", destination="MAD",
            cash_options=[CashOption(label="c", amount=1.0)]),
        client, LiveOptions(live=True),
    )
    print("http calls:", mock_get.call_count)
    print("state     :", outcome.state)
    print("render    :", outcome.render())
    assert mock_get.call_count == 0
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in outcome.render()
    SeatsClient.reset_call_budget()


@patch("src.seats_client.requests.get")
def test_B7b_pagination_can_spend_25_calls_on_one_leg(mock_get, client):
    from src.models import DateRange
    SeatsClient.reset_call_budget()
    mock_get.return_value = _mock_response(
        {"data": [_row(date(2027, 1, 15))], "hasMore": True, "cursor": "abc"}
    )
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    print("calls for ONE leg:", mock_get.call_count)
    print("note:", client.last_pagination_note[:120])
    assert mock_get.call_count == 25
    assert "INCOMPLETE" in client.last_pagination_note
    SeatsClient.reset_call_budget()


# =========================================================================
# B8. The invariant under python -O
# =========================================================================


def test_B8_the_live_outcome_invariant_survives_python_O():
    import subprocess
    code = (
        "from src.models import LiveLegOutcome, LiveQueryState, PointsProvenance\n"
        "try:\n"
        "    LiveLegOutcome(leg_id='X', state=LiveQueryState.NO_AWARD_SPACE,\n"
        "                   provenance=PointsProvenance.UNAVAILABLE, error='timeout')\n"
        "    print('NO RAISE')\n"
        "except ValueError:\n"
        "    print('RAISED')\n"
    )
    for flags in ([], ["-O"], ["-OO"]):
        out = subprocess.run(
            ["python", *flags, "-c", code], cwd=str(ROOT),
            capture_output=True, text=True,
        )
        print(flags or ["(none)"], "->", out.stdout.strip(), out.stderr[-200:])
        assert out.stdout.strip() == "RAISED"


# =========================================================================
# B9. Transfer-date boundary at the Oct 1 2026 Hyatt cliff
# =========================================================================


def test_B9_the_hyatt_cliff_on_both_sides_and_on_the_exact_day(rm):
    from src.funding import best_plan
    wallet = Wallet({"UR": UR}, [CSP])
    for d in (date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)):
        p = best_plan(wallet, "World of Hyatt", 45_000, d, rm)
        print(d, "->", p.spend_summary(), "stranded", p.stranded_points)
    assert best_plan(wallet, "World of Hyatt", 45_000, date(2026, 9, 30), rm
                     ).per_currency_spend["UR"] == 45_000
    assert best_plan(wallet, "World of Hyatt", 45_000, date(2026, 10, 1), rm
                     ).per_currency_spend["UR"] == 60_000
    assert best_plan(wallet, "World of Hyatt", 45_000, date(2026, 10, 2), rm
                     ).per_currency_spend["UR"] == 60_000
