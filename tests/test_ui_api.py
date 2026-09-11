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
    with a golden byte for byte - and a fresh daily call counter, as a freshly
    launched app has."""
    from src import response_cache, seats_client, seats_trips

    seats_client.SeatsClient.reset_call_budget()

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


# ------------------------------------------------------------ LIVE / REPLAY
#
# The transport is the goldens' Stub (synthetic Trip B awards, VS19 on B4),
# patched at `requests.get` exactly where the CLI's client calls it. Snapshots
# and cache go to conftest's tmp directories.

from unittest.mock import patch  # noqa: E402

LIVE = {"mode": "live", "options": {**TRANSFER, "trips": "auto", "trips_cap": 10}}


@pytest.fixture
def live_client(tmp_path, pinned, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, g.FAKE_KEY)
    stub = g.Stub()
    wallet = write_wallet(tmp_path / "wallet.json")
    trips = copy_trips(tmp_path / "trips", names=["trip_b_europe.json"])
    with patch("src.seats_client.requests.get", side_effect=stub):
        with running_server(wallet_path=wallet, trips_dir=trips) as c:
            c.stub = stub
            c.trips_dir = trips
            yield c


def test_live_preflight_states_the_true_ceiling(live_client):
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    assert pf["max_calls"] == 4 * 25 + 10
    assert pf["breakdown"] == {"flight_legs": 4, "pages_per_search": 25, "lookup_cap": 10}
    assert pf["confirm_id"] and pf["blocked"] is None
    assert "--live --trips auto --trips-cap 10" in pf["argv_display"]
    off = dict(LIVE, options={**LIVE["options"], "trips": "off"})
    assert live_client.post("/api/trips/trip_b_europe/preflight", off).json()["max_calls"] == 100
    assert live_client.stub.calls == []


def test_live_without_a_confirm_spends_nothing(live_client):
    r = live_client.post("/api/trips/trip_b_europe/run", LIVE)
    assert r.status == 409 and r.json()["error"] == "confirm_required"
    r = live_client.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id="forged"))
    assert r.status == 409 and r.json()["error"] == "confirm_required"
    assert live_client.stub.calls == []


def test_live_with_a_confirm_reports_exactly_the_calls_it_made(live_client):
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    run = live_client.post("/api/trips/trip_b_europe/run",
                           dict(LIVE, confirm_id=pf["confirm_id"])).json()
    stub = live_client.stub
    assert run["calls"]["this_run"] == {"search": stub.search_calls, "trips": stub.trips_calls}
    assert (stub.search_calls, stub.trips_calls) == (4, 1)
    assert run["calls"]["since_launch"] == 5
    assert run["mode"] == "live" and run["exit_code"] == 0
    assert run["headline"]["qualifier"] == "(live)"
    # The confirm is single use.
    again = live_client.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert again.status == 409 and again.json()["error"] == "confirm_required"
    assert stub.search_calls == 4


def test_the_live_transcript_matches_golden_g4_apart_from_key_line_and_paths(
    live_client, monkeypatch
):
    # The relocation variables are conftest's, for CHILD processes; the goldens
    # were recorded without them (see tests/_cli_golden.py).
    for var in ("POINTS_OPTIMIZER_ENV_FILE", "POINTS_OPTIMIZER_CACHE_DIR",
                "POINTS_OPTIMIZER_SNAPSHOT_DIR"):
        monkeypatch.delenv(var, raising=False)
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    run = live_client.post("/api/trips/trip_b_europe/run",
                           dict(LIVE, confirm_id=pf["confirm_id"])).json()
    masked, _ = g.normalize(run["transcript"])
    want = _golden_body("G4").splitlines()
    got = masked.splitlines()
    import difflib

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=want, b=got, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            # The UI always passes --show-alternatives (G4 did not): its block.
            assert got[j1].startswith("No same-metal alternatives were found."), got[j1:j2]
            continue
        assert tag == "replace" and i2 - i1 == j2 - j1, (want[i1:i2], got[j1:j2])
        for a, b in zip(want[i1:i2], got[j1:j2]):
            if a.startswith("Seats.aero key: "):
                assert b == ("Seats.aero key: (masked key not sent to the browser)   "
                             "(source: environment)")
            else:
                assert a.startswith(("  snapshots: ", "  manifest:  ")), (a, b)


def test_a_changed_fixture_between_confirm_and_run_is_stale(live_client):
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    path = live_client.trips_dir / "trip_b_europe.json"
    path.write_text(path.read_text() + "\n")  # one byte more; nothing semantic
    r = live_client.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    assert r.status == 409
    assert r.json() == {"error": "confirm_stale",
                        "message": "The trip or options changed since you confirmed. Confirm again."}
    assert live_client.stub.calls == []


def test_changed_options_between_confirm_and_run_are_stale(live_client):
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    other = {"mode": "live", "options": {**LIVE["options"], "trips_cap": 50}}
    r = live_client.post("/api/trips/trip_b_europe/run", dict(other, confirm_id=pf["confirm_id"]))
    assert r.json()["error"] == "confirm_stale"
    assert live_client.stub.calls == []


def test_one_run_at_a_time(live_client):
    engine = live_client.srv.engine
    engine._lock.acquire()
    try:
        r = live_client.post("/api/trips/trip_b_europe/run",
                             {"mode": "offline", "options": TRANSFER})
    finally:
        engine._lock.release()
    assert r.status == 409
    assert r.json()["message"] == (
        "Another run is in progress. One run at a time: the call counter and caches are shared.")


def test_each_run_starts_with_an_empty_in_process_cache(live_client):
    from src.seats_client import SeatsClient

    for _ in range(2):
        pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
        live_client.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    # The second run was answered by the DISK cache (6h), never by the stale
    # in-process one: every leg says where its bytes came from.
    assert live_client.stub.search_calls == 4
    SeatsClient.CACHE["poison"] = []
    refresh = {"mode": "live", "options": {**LIVE["options"], "refresh": True}}
    pf = live_client.post("/api/trips/trip_b_europe/preflight", refresh).json()
    run = live_client.post("/api/trips/trip_b_europe/run", dict(refresh, confirm_id=pf["confirm_id"])).json()
    assert "poison" not in SeatsClient.CACHE
    assert run["calls"]["this_run"]["search"] == 4
    assert live_client.stub.search_calls == 8


def test_the_ceiling_holds_against_a_paginating_stub(tmp_path, pinned, monkeypatch):
    """hasMore + cursor, three pages per search: the max stated is still a max."""
    monkeypatch.setenv(config.KEY_ENV_VAR, g.FAKE_KEY)
    real = g.Stub()

    def pages(route, params):
        cursor = params.get("cursor")
        base = real.__class__(search_override=None)
        if cursor is None:
            body = {"data": [], "hasMore": True, "cursor": "p2"}
        elif cursor == "p2":
            body = {"data": [], "hasMore": True, "cursor": "p3"}
        else:
            body = {"data": [], "hasMore": False}
        return body

    stub = g.Stub(search_override=pages)
    with patch("src.seats_client.requests.get", side_effect=stub):
        with running_server(wallet_path=write_wallet(tmp_path / "w.json"),
                            trips_dir=copy_trips(tmp_path / "t", ["trip_b_europe.json"])) as c:
            pf = c.post("/api/trips/trip_b_europe/preflight", LIVE).json()
            run = c.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"])).json()
    assert len(stub.calls) == 12
    assert run["calls"]["this_run"]["search"] == 12
    assert len(stub.calls) <= pf["max_calls"]


def test_replay_of_the_live_runs_manifest_carries_the_hash(live_client):
    pf = live_client.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    live_client.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))
    calls = len(live_client.stub.calls)
    manifests = live_client.get("/api/state").json()["modes"]["replay_manifests"]
    assert manifests[0]["rows"] >= 4
    body = {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}}
    pf = live_client.post("/api/trips/trip_b_europe/preflight", body).json()
    assert pf["confirm_id"] is None and pf["max_calls"] == 0
    run = live_client.post("/api/trips/trip_b_europe/run", body).json()
    assert run["mode"] == "replay" and run["exit_code"] == 0
    assert run["headline"]["qualifier"].startswith("(snapshot mh_")
    assert run["calls"]["this_run"] is None
    assert "--from-snapshot" in run["argv_display"]
    assert len(live_client.stub.calls) == calls, "a replay asks nothing"


@pytest.mark.parametrize("mid", [5, -1, "0", True, None])
def test_a_replay_manifest_id_outside_the_list_is_400(live_client, mid):
    body = {"mode": "replay", "options": {**TRANSFER, "manifest_id": mid}}
    r = live_client.post("/api/trips/trip_b_europe/run", body)
    assert r.status == 400


def test_network_down_is_withheld_and_nothing_claims_a_partner(tmp_path, pinned, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, g.FAKE_KEY)
    with patch("src.seats_client.requests.get", side_effect=g.Refused()):
        with running_server(wallet_path=write_wallet(tmp_path / "w.json")) as c:
            pf = c.post("/api/trips/trip_b_europe/preflight", LIVE).json()
            run = c.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"])).json()
    assert run["exit_code"] == 3 and run["headline"]["state"] == "withheld"
    assert "not a partner" not in json.dumps([l["cells"] for l in run["legs"] if l["kind"] == "flight"])


def test_live_with_no_key_is_blocked_before_any_confirm(tmp_path, pinned):
    with running_server(wallet_path=write_wallet(tmp_path / "w.json")) as c:
        pf = c.post("/api/trips/trip_b_europe/preflight", LIVE).json()
        assert pf["confirm_id"] is None
        assert pf["blocked"]["kind"] == "key"
        assert pf["blocked"]["message"].startswith("No Seats.aero API key found.")
