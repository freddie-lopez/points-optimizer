"""
FINDING R2-3: the bound M-1 put on money now covers every number that reaches
arithmetic.

M-1 bounded CASH and nothing else. `{"points": 10 ** 400}` is legal JSON, a
hand-written or externally supplied fixture can carry it, and it loaded fine and
then reached `funding._score`, where `points * valuation` raises
`OverflowError: int too large to convert to float` - a traceback out of the CLI
and a 500 from the UI, the exact class M-1 claimed to close. A traveller count
of the same size does the same thing one multiplication along, because the count
multiplies every money figure on the leg.

The rule is the same mechanical one: a whole number, surviving the round trip
through the fixture's JSON, whose product at the run's valuation is finite. It
is NOT a ceiling on what an award may cost - 1e308 points still loads and is
still reported as unfundable, exactly as before.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from src import config, trip_builder
from src.trip_loader import TripFixtureError, load_trip_fixture
from tests._ui_harness import running_server

ROOT = Path(__file__).parent.parent
TRIP_A = ROOT / "tests" / "fixtures" / "trips" / "trip_a_mry_nyc.json"


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def broken(tmp_path, mutate, name="broken_count"):
    fx = json.loads(TRIP_A.read_text())
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx))
    return path


HUGE = 10 ** 400
SHAPES = {
    "points": lambda fx: _flight(fx)["points_candidates"][0].__setitem__("points", HUGE),
    "travelers": lambda fx: _flight(fx).__setitem__("travelers", HUGE),
    "nights": lambda fx: _flight(fx).__setitem__("nights", HUGE),
    "points_per_night": lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
        "points_per_night", HUGE),
    "award_nights": lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
        "nights", HUGE),
}


# ------------------------------------------------------------------- the rule


def test_the_rule_names_the_arithmetic_and_not_a_ceiling():
    assert config.unscoreable_count_reason(50000) == ""
    assert config.unscoreable_count_reason(10 ** 300) == ""
    reason = config.unscoreable_count_reason(HUGE)
    assert "too large to score" in reason and "overflows" in reason
    assert config.unscoreable_count_reason("lots") == "'lots' is not a whole number."
    assert config.unscoreable_count_reason(True).endswith("is not a whole number.")


def test_a_huge_number_is_not_quoted_in_full_at_the_reader():
    """repr(10 ** 400) is 401 digits. An error nobody can read is not a fix."""
    reason = config.unscoreable_count_reason(HUGE)
    assert len(reason) < 200, reason[:120]
    assert "401-digit" in reason


# -------------------------------------------------------------------- at load


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_fixture_carrying_one_is_a_load_refusal_not_a_traceback(shape, tmp_path):
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(broken(tmp_path, SHAPES[shape]))
    assert "too large to score" in str(e.value)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_cli_reports_it_as_one_line_and_exits_1(shape, tmp_path):
    path = broken(tmp_path, SHAPES[shape])
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(path), "--offline",
         "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"],
        cwd=ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out[-400:]
    assert "Traceback" not in out and "OverflowError" not in out
    assert "too large to score" in proc.stdout


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_ui_lists_it_as_cannot_load_and_never_500s(shape, tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    broken(trips, SHAPES[shape], name=f"broken_{shape}")
    with running_server(trips_dir=trips) as c:
        (row,) = c.get("/api/trips").json()
        assert "too large to score" in row["load_error"]
        assert c.get(f"/api/trips/broken_{shape}").status == 422
        run = c.post(f"/api/trips/broken_{shape}/run", {"mode": "offline", "options": {}})
    assert run.status == 200 and run.json()["exit_code"] == 1


def test_an_award_price_nobody_could_pay_but_the_scorer_can_hold_still_loads(tmp_path):
    """The bound is the arithmetic, not an opinion about award prices."""
    path = broken(tmp_path, lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
        "points", 10 ** 30))
    fx = load_trip_fixture(path)
    assert [p.points for l in fx.legs for p in l.points_candidates][0] == 10 ** 30


# -------------------------------------------------------------- at validation


@pytest.mark.parametrize("value", ["1" * 400, str(HUGE)])
def test_the_builder_refuses_a_traveller_count_it_could_not_score(value, tmp_path):
    with pytest.raises(trip_builder.TripBuilderError) as e:
        trip_builder.validate_travelers(value)
    assert "too large to score" in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_the_builder_refuses_a_night_count_it_could_not_score():
    with pytest.raises(trip_builder.TripBuilderError) as e:
        trip_builder.validate_nights(str(HUGE), "--hotel NIGHTS")
    assert "too large to score" in str(e.value)


@pytest.mark.parametrize("value", ["1", "2", "9", "1000"])
def test_a_party_anybody_could_actually_book_is_still_accepted(value):
    assert trip_builder.validate_travelers(value) == int(value)


def test_the_builder_still_writes_a_fixture_that_loads(tmp_path):
    fixture = trip_builder.build_fixture(
        "ok_counts", [trip_builder.FlightSpec("SFO", "LHR", date(2099, 1, 15), 2400.0)],
        [], 2, "Y", today=date(2026, 9, 11))
    path = trip_builder.write_fixture(fixture, directory=tmp_path)
    assert load_trip_fixture(path).legs[0].travelers == 2
