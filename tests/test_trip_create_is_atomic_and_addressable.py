"""
FINDINGS M-2 and M-3: writing a trip from the UI.

M-2  `write_fixture` checked `path.exists()` and then wrote. A one-shot CLI
     cannot race itself; the UI is a threaded server, so two creates of one name
     both passed the check, both reported "Wrote ...", and one trip silently
     overwrote the other. The create is now exclusive (O_EXCL): the kernel
     decides who wins and everybody else gets the writer's own refusal.

M-3  The app must be able to address what it says it wrote. A name longer than
     the URL can carry is refused with the builder's wording instead of being
     written and then 404ing (or raising ENAMETOOLONG as an unexpected error),
     and a trip a user names `..._answer` is listed and runnable - the
     acceptance ANSWER files are recognised by not loading as a trip, not by
     their name.
"""
import json
import threading
from datetime import date
from pathlib import Path

import pytest

from src import trip_builder
from tests._ui_harness import copy_trips, running_server, write_wallet

LEG = {"origin": "SFO", "destination": "LHR", "date": "2099-01-15", "cabin": "Y",
       "cash": "2400"}


def _draft_body(name, cash="2400"):
    return {"name": name, "cabin": "Y", "legs": [dict(LEG, cash=cash)]}


# ------------------------------------------------------------------ M-2


def test_two_writers_of_one_name_produce_one_file_and_one_success(tmp_path):
    fixture = trip_builder.build_fixture(
        "race", [trip_builder.FlightSpec("SFO", "LHR", date(2099, 1, 15), 2400.0)],
        [], 1, "Y", today=date(2026, 9, 11))
    results = []
    barrier = threading.Barrier(8)

    def write(i):
        barrier.wait()
        try:
            results.append(trip_builder.write_fixture(dict(fixture), directory=tmp_path))
        except trip_builder.TripBuilderError as e:
            results.append(e)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wrote = [r for r in results if isinstance(r, Path)]
    refused = [r for r in results if isinstance(r, trip_builder.TripBuilderError)]
    assert len(wrote) == 1 and len(refused) == 7
    assert all("Refusing to overwrite" in str(e) for e in refused)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["race.json"]


def test_the_ui_create_never_reports_two_successes_for_one_name(tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        bodies = []
        for i in range(8):
            body = _draft_body("race_ui", cash=str(1000 + i))
            body["draft_hash"] = c.post("/api/trips/draft", body).json()["draft_hash"]
            bodies.append(body)
        out = []
        barrier = threading.Barrier(len(bodies))

        def fire(b):
            barrier.wait()
            out.append(c.post("/api/trips/create", b))

        threads = [threading.Thread(target=fire, args=(b,)) for b in bodies]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for r in out if r.status == 200]
        assert len(ok) == 1, [r.status for r in out]
        assert all(r.json()["error"] == "refused" for r in out if r.status != 200)
        listed = [t["id"] for t in c.get("/api/trips").json()]
    assert listed == ["race_ui"]
    written = json.loads((trips / "race_ui.json").read_text())
    assert len(written["legs"]) == 1


def test_force_still_overwrites_for_the_cli(tmp_path):
    fixture = trip_builder.build_fixture(
        "over", [trip_builder.FlightSpec("SFO", "LHR", date(2099, 1, 15), 2400.0)],
        [], 1, "Y", today=date(2026, 9, 11))
    trip_builder.write_fixture(fixture, directory=tmp_path)
    fixture["description"] = "second"
    trip_builder.write_fixture(fixture, directory=tmp_path, force=True)
    assert json.loads((tmp_path / "over.json").read_text())["description"] == "second"


# ------------------------------------------------------------------ M-3


@pytest.mark.parametrize("length", [121, 200, 300, 60000])
def test_a_name_the_app_cannot_address_is_refused_in_the_builders_words(length, tmp_path):
    with pytest.raises(trip_builder.TripBuilderError) as e:
        trip_builder.validate_name("x" * length)
    assert f"is {length} characters" in str(e.value)
    assert f"longest name this tool can address is {trip_builder.MAX_NAME_LENGTH}" in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_the_longest_addressable_name_is_accepted_and_addressable(tmp_path):
    name = "x" * trip_builder.MAX_NAME_LENGTH
    assert trip_builder.validate_name(name) == name
    trips = tmp_path / "trips"
    trips.mkdir()
    body = _draft_body(name)
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        body["draft_hash"] = c.post("/api/trips/draft", body).json()["draft_hash"]
        assert c.post("/api/trips/create", body).status == 200
        assert name in [t["id"] for t in c.get("/api/trips").json()]
        assert c.get(f"/api/trips/{name}").status == 200


def test_a_giant_name_is_refused_by_the_draft_not_by_a_500(tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        r = c.post("/api/trips/draft", _draft_body("x" * 60000))
        assert r.status == 200 and r.json()["ok"] is False
        assert r.json()["errors"][0]["field"] == "name"
        created = c.post("/api/trips/create", dict(_draft_body("x" * 60000), draft_hash="x"))
        assert created.status == 400
    assert list(trips.iterdir()) == []


def test_a_trip_named_answer_is_written_listed_and_runnable(tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    body = _draft_body("probe_answer")
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        body["draft_hash"] = c.post("/api/trips/draft", body).json()["draft_hash"]
        assert c.post("/api/trips/create", body).status == 200
        assert "probe_answer" in [t["id"] for t in c.get("/api/trips").json()]
        assert c.get("/api/trips/probe_answer").status == 200
        run = c.post("/api/trips/probe_answer/run", {"mode": "offline", "options": {}})
    assert run.json()["exit_code"] in (0, 3, 4)


def test_a_real_acceptance_answer_file_is_still_not_offered_as_a_trip(tmp_path):
    trips = copy_trips(tmp_path / "trips")
    assert (trips / "trip_001_answer.json").is_file()
    with running_server(trips_dir=trips) as c:
        ids = [t["id"] for t in c.get("/api/trips").json()]
    assert "trip_001_answer" not in ids
    assert "trip_001" in ids and "trip_b_europe" in ids
