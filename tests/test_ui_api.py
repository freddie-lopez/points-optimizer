"""
The UI's API end to end (docs/plans/ui.md 4.5, steps 4 and 7-11), against a real
server on an ephemeral port. Every run goes through the CLI's own `dispatch`;
the transport is stubbed exactly as the goldens stub it, and nothing is written
into the repo tree (trips go to a tmp directory the engine is given).
"""
import json
from pathlib import Path

import pytest

from src import config
from tests import _cli_golden as g
from tests._ui_harness import copy_trips, running_server, write_wallet

TRANSFER = {"transfer_date": "2026-09-15"}


@pytest.fixture
def pinned(monkeypatch):
    """The goldens' pinned date and clock, so a UI transcript can be compared
    with a golden byte for byte."""
    from src import response_cache, seats_client, seats_trips

    monkeypatch.setattr(config, "date", g._PinnedDate)
    monkeypatch.setattr(response_cache, "_utcnow", lambda: g.PINNED_NOW)
    monkeypatch.setattr(seats_client, "datetime", g._PinnedDatetime)
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "")
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "unverified")


@pytest.fixture
def client(tmp_path, pinned):
    wallet = write_wallet(tmp_path / "wallet.json")
    with running_server(wallet_path=wallet) as c:
        yield c


def _golden_body(name):
    text = g.golden_path(name).read_text()
    return "".join(text.splitlines(True)[3:])


# ------------------------------------------------------------------- trips


def test_trip_listing_names_every_fixture_and_hides_answer_files(client):
    rows = client.get("/api/trips").json()
    ids = [r["id"] for r in rows]
    assert "trip_b_europe" in ids and "trip_a_mry_nyc" in ids
    assert not any(i.endswith("_answer") for i in ids)
    b = next(r for r in rows if r["id"] == "trip_b_europe")
    assert (b["legs"], b["flights"], b["hotels"]) == (7, 4, 3)
    assert b["fixture_has_points_prices"] is True and b["live_only"] is False
    assert b["load_error"] is None


def test_an_unloadable_fixture_is_listed_with_its_error_not_hidden(tmp_path, pinned):
    trips = copy_trips(tmp_path / "trips", names=["trip_b_europe.json"])
    (trips / "broken.json").write_text("{not json")
    with running_server(trips_dir=trips) as c:
        rows = c.get("/api/trips").json()
    broken = next(r for r in rows if r["id"] == "broken")
    assert broken["load_error"].startswith("TripFixtureError: broken.json is not valid JSON")


def test_trip_detail_is_the_fixture_without_scoring(client):
    d = client.get("/api/trips/trip_b_europe").json()
    assert d["name"].startswith("TRIP B")
    b1 = d["legs"][0]
    assert b1["id"] == "B1" and b1["points_candidates"][0]["unverified"] is True
    assert "headline" not in d


def test_an_unknown_trip_id_is_404(client):
    assert client.get("/api/trips/nope").status == 404


# ----------------------------------------------------------- OFFLINE trip B


def test_offline_trip_b_is_the_golden_g1_run(client):
    body = {"mode": "offline", "options": TRANSFER}
    pf = client.post("/api/trips/trip_b_europe/preflight", body).json()
    assert pf["confirm_id"] is None and pf["max_calls"] == 0
    r = client.post("/api/trips/trip_b_europe/run", body)
    assert r.status == 200, r.text
    run = r.json()
    assert run["exit_code"] == 0 and run["exit_label"] == "OK"
    h = run["headline"]
    assert (h["state"], h["text"], h["qualifier"]) == ("range", "2.04% - 11.03%", "(badge)")
    assert "pct" in h and h["pct"] is None
    assert run["calls"]["this_run"] == {"search": 0, "trips": 0}
    assert run["argv_display"].startswith(
        ".venv/bin/python -m src.main --trip-fixture trip_b_europe.json --offline --wallet ")
    # The transcript IS the CLI's output: equal to golden G1, which was recorded
    # with --balance/--card flags instead of the wallet file (same wallet).
    assert run["transcript"] == _golden_body("G1")
    # And the result is kept for the session.
    again = client.get(f"/api/runs/{run['run_id']}").json()
    assert again["headline"] == h


def test_a_run_with_no_wallet_is_the_clis_exit_2_refusal(tmp_path, pinned):
    with running_server() as c:
        run = c.post("/api/trips/trip_b_europe/run",
                     {"mode": "offline", "options": TRANSFER}).json()
    assert run["exit_code"] == 2
    assert run["refusal"]["kind"] == "wallet"
    assert run["refusal"]["message"].startswith("Wallet error: No currencies supplied.")
    assert "headline" not in run


@pytest.mark.parametrize("options,field", [
    ({"transfer_date": "2026-13-40"}, "transfer_date"),
    ({"transfer_date": "tomorrow"}, "transfer_date"),
])
def test_bad_options_are_refused_before_any_argv_exists(client, options, field):
    r = client.post("/api/trips/trip_b_europe/run", {"mode": "offline", "options": options})
    assert r.status == 400
    assert r.json()["field"] == field


def test_an_unknown_mode_is_400(client):
    assert client.post("/api/trips/trip_b_europe/run", {"mode": "fast"}).status == 400
