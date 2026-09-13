"""
FINDING M-1: a fare that cannot be scored is refused BEFORE it is written, and
a fixture that already carries one loads as a refusal, never as a traceback.

`validate_cash` bounded NaN and infinity only. `1e308` is finite, so it was
accepted, written into `tests/fixtures/trips/`, and then crashed every run over
that directory: `cash_to_points_equivalent` divides by the valuation, overflows
to `inf`, and `int(inf)` raises OverflowError - a raw traceback from the CLI, a
500 from the UI, and a file the UI has no way to delete.

The bound is mechanical, not a made-up ceiling: the amount must be finite,
survive the round trip through the fixture's JSON, and have a finite
points-equivalent at the run's valuation.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from src import config, trip_builder
from src.trip_loader import TripFixtureError, load_trip_fixture

ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize("amount", ["1e308", "1e309", "-1e308", "inf", "-inf", "nan",
                                    "1.8e308", str(2.0 ** 1023)])
def test_an_unscoreable_fare_is_refused_before_anything_is_written(amount, tmp_path):
    with pytest.raises(trip_builder.TripBuilderError) as e:
        trip_builder.parse_leg_flag(f"SFO:LHR:2027-01-15:{amount}",
                                    today=date(2026, 9, 11))
    assert "--leg CASH_USD" in str(e.value)
    assert sorted(tmp_path.iterdir()) == []


@pytest.mark.parametrize("amount", ["2400", "0.01", "1e12", "999999999.99"])
def test_a_fare_anybody_could_actually_pay_is_still_accepted(amount):
    spec = trip_builder.parse_leg_flag(f"SFO:LHR:2027-01-15:{amount}",
                                       today=date(2026, 9, 11))
    assert spec.cash_usd == float(amount)
    assert config.unscoreable_cash_reason(spec.cash_usd) == ""


def test_the_reason_says_which_round_trip_the_amount_fails():
    assert config.unscoreable_cash_reason(1e308).startswith("1e+308 is too large to score")
    assert "points-equivalent overflows" in config.unscoreable_cash_reason(1e308)
    assert "not a finite amount" in config.unscoreable_cash_reason(float("inf"))
    assert "not a number" in config.unscoreable_cash_reason("lots")
    assert config.unscoreable_cash_reason(2400.0) == ""


def _broken_fixture(tmp_path, amount=1e308):
    fixture = {
        "id": "broken_cash", "name": "broken_cash", "description": "hand-written",
        "source": "test", "legs": [{
            "id": "L1", "kind": "flight", "description": "SFO->LHR",
            "date": "2027-01-15", "origin": "SFO", "destination": "LHR",
            "cash_options": [{"label": "huge", "amount": amount, "currency": "USD"}],
        }],
    }
    path = tmp_path / "broken_cash.json"
    path.write_text(json.dumps(fixture))
    return path


def test_a_fixture_that_already_carries_one_is_a_load_refusal(tmp_path):
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(_broken_fixture(tmp_path))
    assert "too large to score" in str(e.value)


def test_the_cli_reports_it_as_one_line_and_exits_1_with_no_traceback(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(_broken_fixture(tmp_path)),
         "--offline", "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1, proc.stdout[-500:]
    assert "Traceback" not in proc.stdout and "Traceback" not in proc.stderr
    assert "OverflowError" not in proc.stdout
    assert "too large to score" in proc.stdout


def test_the_ui_lists_it_as_cannot_load_and_never_500s(tmp_path):
    from tests._ui_harness import running_server

    trips = tmp_path / "trips"
    trips.mkdir()
    _broken_fixture(trips)
    with running_server(trips_dir=trips) as c:
        rows = c.get("/api/trips").json()
        (row,) = rows
        assert row["id"] == "broken_cash"
        assert "too large to score" in row["load_error"]
        assert row["legs"] is None
        detail = c.get("/api/trips/broken_cash")
        assert detail.status == 422 and "too large to score" in detail.json()["message"]
        run = c.post("/api/trips/broken_cash/run", {"mode": "offline", "options": {}})
    assert run.status == 200
    assert run.json()["exit_code"] == 1
    assert "too large to score" in run.json()["refusal"]["message"]


def test_the_ui_form_refuses_it_and_writes_nothing(tmp_path):
    from tests._ui_harness import running_server, write_wallet

    trips = tmp_path / "trips"
    trips.mkdir()
    body = {"name": "huge", "cabin": "Y", "legs": [
        {"origin": "SFO", "destination": "LHR", "date": "2099-01-15", "cabin": "Y",
         "cash": "1e308"}]}
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        draft = c.post("/api/trips/draft", body).json()
        assert draft["ok"] is False
        (err,) = [e for e in draft["errors"] if e["field"] == "cash"]
        assert "too large to score" in err["message"]
        created = c.post("/api/trips/create", dict(body, draft_hash="x" * 64))
    assert created.status == 400
    assert list(trips.iterdir()) == []
