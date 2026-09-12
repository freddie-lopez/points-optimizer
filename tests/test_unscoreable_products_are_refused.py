"""
FINDINGS R3-1 and R3-3: the products, and the number in the message.

R3-1  R2-3 bounded each field on its own, and the arithmetic multiplies them.
      `nights = 10 ** 200` passes (its product with the valuation is finite) and
      so does `points_per_night = 10 ** 200`; `hotels.award_points_for`
      multiplies them into `10 ** 400` and `funding._score` raises
      `OverflowError: int too large to convert to float` - a traceback from the
      CLI, a 500 from the UI. The same class as M-1 and R2-3, one multiplication
      further along, so the guard now checks the FIGURES THAT REACH ARITHMETIC:
      every product the file itself determines.

R3-3  `unscoreable_count_reason` shortened `10 ** 400` to "a 401-digit number"
      and `trip_loader._count` then wrapped the reason in `{value!r}`, so the
      reader got 401 digits anyway. Shortening now happens where the printing
      does, and every message that names a value goes through it.
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
TRIPS = ROOT / "tests" / "fixtures" / "trips"
BIG = 10 ** 200  # each field passes the per-field bound; pairs of them do not


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def _hotel(fx):
    return [l for l in fx["legs"] if l["kind"] == "hotel"][0]


def _nights_x_points_per_night(fx):
    h = _hotel(fx)
    h["nights"] = BIG
    h["points_candidates"] = [{"label": "SYNTHETIC", "program": "World of Hyatt",
                               "points_per_night": BIG, "source": "synthetic_test_fixture"}]


def _party_x_points(fx):
    h = _hotel(fx)
    h["travelers"] = BIG
    h["points_candidates"] = [{"label": "SYNTHETIC", "program": "World of Hyatt",
                               "points": BIG, "source": "synthetic_test_fixture"}]


PRODUCTS = {
    "nights_x_points_per_night": ("trip_b_europe.json", _nights_x_points_per_night),
    "party_x_points": ("trip_b_europe.json", _party_x_points),
    "party_x_cash": ("trip_a_mry_nyc.json",
                     lambda fx: _flight(fx).__setitem__("travelers", 10 ** 306)),
    "party_x_fee": ("trip_a_mry_nyc.json", lambda fx: (
        _flight(fx).__setitem__("travelers", 10 ** 300),
        _flight(fx).__setitem__("mandatory_fees", [
            {"label": "probe", "amount": 1e300, "currency": "USD", "per": "person_night"}]),
        _flight(fx).__setitem__("nights", 2))),
}


def written(tmp_path, name):
    base, mutate = PRODUCTS[name]
    fx = json.loads((TRIPS / base).read_text())
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx))
    return path


# ------------------------------------------------------------------ the rule


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_a_product_no_run_could_score_is_a_load_refusal(name, tmp_path):
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(written(tmp_path, name))
    assert "too large to score" in str(e.value)
    # It names WHICH product, not just a field, or the reader cannot find it.
    assert " x " in str(e.value), str(e.value)


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_the_cli_reports_it_as_one_line_and_exits_1(name, tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(written(tmp_path, name)),
         "--offline", "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"],
        cwd=ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out[-400:]
    assert "Traceback" not in out and "OverflowError" not in out
    assert "too large to score" in proc.stdout


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_the_ui_lists_it_as_cannot_load_and_never_500s(name, tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    (trips / f"{name}.json").write_bytes(written(tmp_path, name).read_bytes())
    with running_server(trips_dir=trips) as c:
        (row,) = c.get("/api/trips").json()
        assert "too large to score" in row["load_error"]
        assert row["legs"] is None
        assert c.get(f"/api/trips/{name}").status == 422
        run = c.post(f"/api/trips/{name}/run", {"mode": "offline", "options": {}})
    assert run.status == 200 and run.json()["exit_code"] == 1


def test_a_stay_anybody_could_actually_book_still_loads_and_multiplies(tmp_path):
    """The bound is the arithmetic. A real per-night award is untouched."""
    fx = json.loads((TRIPS / "trip_b_europe.json").read_text())
    h = _hotel(fx)
    h["nights"] = 3
    h["points_candidates"] = [{"label": "SYNTHETIC", "program": "World of Hyatt",
                               "points_per_night": 12000, "source": "synthetic_test_fixture"}]
    path = tmp_path / "fine.json"
    path.write_text(json.dumps(fx))
    leg = [l for l in load_trip_fixture(path).legs if l.kind == "hotel"][0]
    assert leg.nights == 3 and leg.points_candidates[0].points_per_night == 12000


def test_a_per_night_price_with_no_nights_is_still_the_old_data_error(tmp_path):
    """Zero nights is an input error raised where it always was, not an
    overflow refused here."""
    from src.hotels import HotelDataError, award_points_for

    fx = json.loads((TRIPS / "trip_b_europe.json").read_text())
    h = _hotel(fx)
    h["nights"] = 0
    h["points_candidates"] = [{"label": "SYNTHETIC", "program": "World of Hyatt",
                               "points_per_night": 12000, "source": "synthetic_test_fixture"}]
    path = tmp_path / "nonights.json"
    path.write_text(json.dumps(fx))
    leg = [l for l in load_trip_fixture(path).legs if l.kind == "hotel"][0]
    with pytest.raises(HotelDataError):
        award_points_for(leg.points_candidates[0], 0)


# ----------------------------------------------------------- at validation


def test_the_builder_refuses_a_party_total_it_could_not_score():
    spec = trip_builder.FlightSpec("SFO", "LHR", date(2099, 1, 15), 1e300)
    with pytest.raises(trip_builder.TripBuilderError) as e:
        trip_builder.build_fixture("party", [spec], [], 10 ** 20, "Y",
                                   today=date(2026, 9, 11))
    assert "cannot be scored" in str(e.value)


def test_the_builder_still_writes_the_trip_anybody_would_actually_build(tmp_path):
    fixture = trip_builder.build_fixture(
        "ok", [trip_builder.FlightSpec("SFO", "LHR", date(2099, 1, 15), 2400.0)],
        [trip_builder.HotelSpec("Hotel", date(2099, 1, 16), 3, 300.0)], 2, "Y",
        today=date(2026, 9, 11))
    path = trip_builder.write_fixture(fixture, directory=tmp_path)
    assert len(load_trip_fixture(path).legs) == 2


# ------------------------------------------------------------------- R3-3


def test_no_message_anywhere_prints_the_number_it_says_it_will_not_print(tmp_path):
    fx = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    _flight(fx)["points_candidates"][0]["points"] = 10 ** 400
    path = tmp_path / "bigint.json"
    path.write_text(json.dumps(fx))
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(path)
    message = str(e.value)
    assert "401-digit" in message
    assert "0" * 40 not in message, "the refusal printed the number"
    assert len(message) < 250, message[:120]


def test_the_cli_line_and_the_ui_row_are_both_short(tmp_path):
    fx = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    fx["id"] = "bigint"
    _flight(fx)["points_candidates"][0]["points"] = 10 ** 400
    trips = tmp_path / "trips"
    trips.mkdir()
    (trips / "bigint.json").write_text(json.dumps(fx))
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(trips / "bigint.json"),
         "--offline", "--balance", "UR=160000", "--card", "Chase Sapphire Preferred"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert "0" * 40 not in proc.stdout and "401-digit" in proc.stdout
    with running_server(trips_dir=trips) as c:
        (row,) = c.get("/api/trips").json()
    assert "401-digit" in row["load_error"]
    assert "0" * 40 not in row["load_error"]


@pytest.mark.parametrize("value,expected", [
    (2400.0, "2400.0"), (50000, "50000"), (-12, "-12"), (True, "True"),
])
def test_a_number_a_person_can_read_is_printed_unchanged(value, expected):
    assert config.short_number(value) == expected


def test_a_long_non_number_is_shortened_too_and_says_so():
    out = config.short_number("x" * 400)
    assert len(out) < 60 and "characters" in out
