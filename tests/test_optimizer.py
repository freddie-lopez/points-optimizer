"""Test transfer path finder and core optimizer."""
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models import Award, DateRange, Trip
from src.optimizer import find_transfer_paths, optimize
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient


DATA = Path(__file__).parent.parent / "data"
CSR = "Chase Sapphire Reserve"
CSP = "Chase Sapphire Preferred"


@pytest.fixture
def ratio_manager():
    """Create a RatioManager with the production ratio table."""
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def seats_client():
    """Create a mock SeatsClient."""
    client = SeatsClient(api_key="test_key")
    client.clear_cache()
    yield client
    client.clear_cache()


@pytest.fixture
def sample_trip():
    """Create a sample trip for testing."""
    return Trip(
        origin="SFO",
        destination="LHR",
        date_range=DateRange(
            from_date=date(2026, 5, 1), to_date=date(2026, 5, 31)
        ),
        balances={
            "UR": 250000,
            "United MileagePlus": 40000,
        },
        cards_held=[CSR, CSP],
        num_passengers=2,
        description="Transatlantic to London",
    )


def test_find_transfer_paths_sizes_to_demand(ratio_manager):
    """
    Transfers are sized to what the award costs, not to the whole balance.

    This test previously asserted that a 100k UR balance produced a path
    delivering 100,000 points. That was the stranded-points bug: it moved the
    entire balance regardless of the award price.
    """
    paths = find_transfer_paths(
        user_balances={"UR": 100000},
        user_cards=[CSR],
        target_program="United MileagePlus",
        check_date=date(2026, 5, 1),
        ratios_manager=ratio_manager,
        points_needed=60000,
    )

    assert len(paths) == 1
    assert paths[0].total_transferred == 60000
    assert paths[0].target_points_received == 60000
    assert paths[0].stranded_points == 0


def test_find_transfer_paths_uses_existing_balance_first(ratio_manager):
    """Points already in the destination program offset the transfer."""
    paths = find_transfer_paths(
        user_balances={"UR": 100000, "United MileagePlus": 40000},
        user_cards=[CSR],
        target_program="United MileagePlus",
        check_date=date(2026, 5, 1),
        ratios_manager=ratio_manager,
        points_needed=60000,
    )

    transfer_paths = [p for p in paths if p.transfers]
    assert transfer_paths, "expected a path that tops up the existing balance"
    # Only the 20,000 shortfall needs transferring, not the full 60,000.
    assert transfer_paths[0].total_transferred == 20000
    assert transfer_paths[0].existing_target_points_used == 40000


def test_find_transfer_paths_respects_dates(ratio_manager):
    """Ratio effective dates are honoured across the Oct 1 2026 Hyatt change."""
    before = find_transfer_paths(
        user_balances={"UR": 200000},
        user_cards=[CSP],
        target_program="World of Hyatt",
        check_date=date(2026, 9, 30),
        ratios_manager=ratio_manager,
        points_needed=90000,
    )
    # Before the change Sapphire Preferred is 1:1, so 90k Hyatt costs 90k UR.
    assert before[0].total_transferred == 90000

    after = find_transfer_paths(
        user_balances={"UR": 200000},
        user_cards=[CSP],
        target_program="World of Hyatt",
        check_date=date(2026, 10, 1),
        ratios_manager=ratio_manager,
        points_needed=90000,
    )
    # After the change it is 4:3, so 90k Hyatt costs 120k UR.
    assert after[0].total_transferred == 120000
    assert after[0].transfers[0].ratio == "4:3"


def test_find_transfer_paths_no_stranding(ratio_manager):
    """Every returned path is feasible and strands less than one increment."""
    paths = find_transfer_paths(
        user_balances={"UR": 100000},
        user_cards=[CSR],
        target_program="United MileagePlus",
        check_date=date(2026, 5, 1),
        ratios_manager=ratio_manager,
        points_needed=57300,
    )

    assert paths
    assert all(p.feasible for p in paths)
    assert all(p.stranded_points < 1000 for p in paths)
    # 57,300 rounds up to a 58,000 transfer, stranding 700.
    assert paths[0].total_transferred == 58000
    assert paths[0].stranded_points == 700


@patch("src.optimizer.SeatsClient.search")
def test_optimize_returns_top_n(mock_search, ratio_manager, sample_trip):
    """Test that optimize returns top N strategies."""
    # Mock Seats.aero response
    mock_search.return_value = [
        Award(
            date=date(2026, 5, 3),
            program="United MileagePlus",
            award_type="J",
            cost=120000,
            cash_component=200,
            airline="UA",
            route="SFO-LHR",
            seats_available=2,
        ),
        Award(
            date=date(2026, 5, 4),
            program="United MileagePlus",
            award_type="J",
            cost=120000,
            cash_component=200,
            airline="UA",
            route="SFO-LHR",
            seats_available=1,
        ),
        Award(
            date=date(2026, 5, 5),
            program="Air France-KLM Flying Blue",
            award_type="J",
            cost=90000,
            cash_component=150,
            airline="AF",
            route="SFO-LHR",
            seats_available=3,
        ),
    ]

    seats_client = SeatsClient(api_key="test_key")
    results = optimize(sample_trip, seats_client, ratio_manager, max_results=2)

    assert len(results) <= 2
    assert all(s.award is not None for s in results)
    assert all(s.points_cost > 0 for s in results)


@patch("src.optimizer.SeatsClient.search")
def test_optimize_ranks_by_total_value(mock_search, ratio_manager, sample_trip):
    """Test that strategies are ranked by total value."""
    mock_search.return_value = [
        Award(
            date=date(2026, 5, 3),
            program="United MileagePlus",
            award_type="J",
            cost=120000,
            cash_component=500,  # High cash cost
            airline="UA",
            route="SFO-LHR",
            seats_available=2,
        ),
        Award(
            date=date(2026, 5, 5),
            program="Air France-KLM Flying Blue",
            award_type="J",
            cost=100000,
            cash_component=100,  # Low cash cost
            airline="AF",
            route="SFO-LHR",
            seats_available=3,
        ),
    ]

    seats_client = SeatsClient(api_key="test_key")
    results = optimize(sample_trip, seats_client, ratio_manager, valuation_cpp=0.02)

    # Flying Blue should rank higher (lower total value with lower cash cost)
    if len(results) >= 2:
        assert results[0].total_value <= results[1].total_value


@patch("src.optimizer.SeatsClient.search")
def test_optimize_deduplicates(mock_search, ratio_manager, sample_trip):
    """Test that duplicate awards keep only the cheapest option."""
    mock_search.return_value = [
        Award(
            date=date(2026, 5, 3),
            program="United MileagePlus",
            award_type="J",
            cost=120000,
            cash_component=200,
            airline="UA",
            route="SFO-LHR",
            seats_available=2,
        ),
        Award(
            date=date(2026, 5, 3),  # Same date, type, and program
            program="United MileagePlus",
            award_type="J",
            cost=125000,
            cash_component=200,
            airline="UA",
            route="SFO-LHR",
            seats_available=1,
        ),
    ]

    seats_client = SeatsClient(api_key="test_key")
    results = optimize(sample_trip, seats_client, ratio_manager, max_results=5)

    # Should keep only one United result (the cheaper one at 120000)
    united_results = [r for r in results if r.award.program == "United MileagePlus"]
    assert len(united_results) == 1
    assert united_results[0].points_cost == 120000


@patch("src.optimizer.SeatsClient.search")
def test_optimize_handles_api_failure(mock_search, ratio_manager, sample_trip):
    """Test that optimizer handles API failures gracefully."""
    mock_search.side_effect = RuntimeError("API unavailable")

    seats_client = SeatsClient(api_key="test_key")
    results = optimize(sample_trip, seats_client, ratio_manager)

    # Should return empty list on failure
    assert len(results) == 0
