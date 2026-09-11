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

    from src import trip_builder

    seats_client.SeatsClient.reset_call_budget()
    # The builder refuses past dates against today; the goldens' dates stay
    # in the future on any machine this way.
    monkeypatch.setattr(trip_builder, "date", g._PinnedDate)

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



# ------------------------------------------------------------------- search

SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15", "date_to": ""}


def _search(c, body=SEARCH):
    pf = c.post("/api/search/preflight", body)
    assert pf.status == 200, pf.text
    pf = pf.json()
    return pf, c.post("/api/search/run", dict(body, confirm_id=pf["confirm_id"])).json()


@pytest.fixture
def search_client(tmp_path, pinned, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, g.FAKE_KEY)
    with running_server(wallet_path=write_wallet(tmp_path / "wallet.json")) as c:
        yield c


def test_search_preflight_states_the_ceiling_and_the_window(search_client):
    with patch("src.seats_client.requests.get", side_effect=g.Stub()) as get:
        pf = search_client.post("/api/search/preflight", SEARCH).json()
    assert get.call_count == 0
    assert pf["max_calls"] == 25
    assert pf["window"] == {"from": "2027-01-15", "to": "2027-02-14"}
    assert pf["argv_display"].startswith(
        ".venv/bin/python -m src.main --origin SFO --destination MAD --date 2027-01-15 "
        "--max-results 200 --wallet ")


def test_search_without_a_confirm_spends_nothing(search_client):
    stub = g.Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        r = search_client.post("/api/search/run", SEARCH)
    assert r.status == 409 and stub.calls == []


@pytest.mark.parametrize("body,field,start", [
    (dict(SEARCH, origin="ZZZ"), "origin", "From: 'ZZZ' is not an airport this tool knows."),
    (dict(SEARCH, destination="MA"), "destination", "To: 'MA' is not a 3-letter IATA airport code"),
    (dict(SEARCH, date="2027-02-30"), "date", "Date: '2027-02-30' is not an ISO date"),
    (dict(SEARCH, date="2020-01-01"), "date", "Date: 2020-01-01 is in the past."),
    (dict(SEARCH, date_to="2027-01-01"), "date_to", "To date: 2027-01-01 is before the date"),
])
def test_a_typo_is_refused_in_the_builders_words_before_any_call(search_client, body, field, start):
    stub = g.Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        r = search_client.post("/api/search/preflight", body)
        r2 = search_client.post("/api/search/run", dict(body, confirm_id="x"))
    assert r.status == 400 and r2.status == 400
    errs = {e["field"]: e["message"] for e in r.json()["errors"]}
    assert errs[field].startswith(start), errs
    if field == "origin":
        assert "No correction is being suggested" in errs[field]
    assert stub.calls == []


def test_g9_search_row_is_the_real_aeroplan_capture(search_client):
    stub = g.Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        _, run = _search(search_client)
    assert run["state"] == "ok" and run["exit_code"] == 0
    assert run["calls"]["this_run"]["search"] == len(stub.calls) == 1
    (row,) = run["rows"]
    assert (row["date"], row["program"], row["source_code"]) == ("2027-01-15", "Air Canada Aeroplan", "aeroplan")
    y = row["cabins"]["Y"]
    assert (y["cost"], y["seats"], y["taxes"]["text"], y["fundable"], y["rank"]) == (50000, 9, "$32.36", True, 1)
    assert y["taxes"]["source_text"] == "CAD 44.60" and y["taxes"]["confirm"] is True
    assert row["cabins"]["W"] is None and row["cabins"]["J"] is None
    assert run["trips_footer"].startswith("operating airline: NOT LOOKED UP")
    assert "Seats.aero key: (masked key not sent to the browser)" in run["transcript"]


def test_g10_an_api_error_is_not_a_finding(search_client):
    with patch("src.seats_client.requests.get", side_effect=g.Refused()):
        _, run = _search(search_client)
    assert run["state"] == "api_error" and run["rows"] == []
    assert run["api_error"].startswith("Seats.aero API error:")


def test_g11_none_fundable_names_every_award_and_why(search_client):
    r = search_client.post("/api/wallet", {"balances": {"MR": "100000"}, "cards": []})
    assert r.status == 200 and r.json()["source"] == "entered in this session (not saved)"
    with patch("src.seats_client.requests.get", side_effect=g.Stub()):
        pf, run = _search(search_client)
    assert "--balance MR=100000" in pf["argv_display"]
    assert run["state"] == "none_fundable"
    assert run["header_line"].startswith("Seats.aero returned 1 award(s) for this route")
    y = run["rows"][0]["cabins"]["Y"]
    assert y["fundable"] is False and y["rank"] is None and y["total"] is None
    assert y["why_not"] in " ".join(run["transcript"].split())
    assert y["why_not"].startswith("a transfer partner, but no fundable path")


def test_every_search_cells_taxes_come_from_search_award_cash(search_client, monkeypatch):
    """Including the unknown case: a KrisFlyer J row whose taxes Seats.aero does
    not report. The cell carries no number and the rule's own note."""
    import copy

    from src import optimizer

    seen = []
    real = optimizer.search_award_cash

    def spy(trip, award):
        seen.append(award.program)
        return real(trip, award)

    monkeypatch.setattr(optimizer, "search_award_cash", spy)

    def rows(route, params):
        a = copy.deepcopy(g.REAL["data"][0])
        a["Date"], a["ParsedDate"] = "2027-01-15", "2027-01-15T00:00:00Z"
        b = copy.deepcopy(g.REAL["data"][0])
        b.update(ID="krisrow", Date="2027-01-16", ParsedDate="2027-01-16T00:00:00Z",
                 YAvailable=False, JAvailable=True, JMileageCost="88000", JTotalTaxes=0,
                 JRemainingSeats=2, JAirlines="SQ")
        b["Route"] = dict(b["Route"], Source="singapore")
        return {"data": [a, b]}

    with patch("src.seats_client.requests.get", side_effect=g.Stub(search_override=rows)):
        _, run = _search(search_client)
    assert "Singapore Airlines KrisFlyer" in seen and "Air Canada Aeroplan" in seen
    kris = next(r for r in run["rows"] if r["source_code"] == "singapore")["cabins"]["J"]
    assert kris["taxes"]["known"] is False
    assert kris["taxes"]["usd"] is None and kris["taxes"]["text"] is None
    assert "singapore" in kris["taxes"]["note"].lower() or "not" in kris["taxes"]["note"].lower()
    if kris["fundable"]:
        assert kris["total_is_floor"] is True and kris["total_text"].startswith(">= ")
    assert run["unknown_cash_line"] is None or "UNKNOWN (NOT $0)" in run["unknown_cash_line"]


# ----------------------------------------------------------------- new trip

from src import trip_builder  # noqa: E402

INPUTS = g.GOLDEN_DIR / "inputs"


def test_the_cli_flags_path_writes_the_same_bytes_as_before_per_leg_cabins(tmp_path):
    from datetime import date

    path = trip_builder.new_trip_from_flags(
        "byte_check", ["SFO:LHR:2027-01-15:2400", "LHR:SFO:2027-01-29:1850.5"],
        ["Hotel Kyoto: Gion:2027-01-16:3:500"], travelers=2, cabin="J",
        directory=tmp_path, today=date(2026, 9, 11))
    assert path.read_bytes() == (INPUTS / "new_trip_flags_before_per_leg_cabin.json").read_bytes()


def test_a_mixed_cabin_fixture_loads_with_each_legs_own_cabin(tmp_path):
    from datetime import date

    from src.trip_loader import load_trip_fixture

    fx = trip_builder.build_fixture(
        "mixed", [trip_builder.FlightSpec("SFO", "LHR", date(2027, 1, 15), 2400.0, cabin="J"),
                  trip_builder.FlightSpec("LHR", "SFO", date(2027, 1, 29), 900.0)],
        [], 1, "Y", today=date(2026, 9, 11))
    assert fx["description"].endswith("cabins by leg: L1 J, L2 Y.")
    path = trip_builder.write_fixture(fx, directory=tmp_path)
    legs = load_trip_fixture(path).legs
    assert [l.cabin for l in legs] == ["J", "Y"]
    assert "business" in legs[0].description and "economy" in legs[1].description


NT = {"name": "sfo_lhr_jan", "cabin": "J",
      "legs": [{"origin": "sfo", "destination": "LHR", "date": "2027-01-15", "cabin": "J",
                "cash": "2400"}]}


@pytest.fixture
def nt_client(tmp_path, pinned):
    trips = copy_trips(tmp_path / "trips", names=["trip_b_europe.json"])
    with running_server(wallet_path=write_wallet(tmp_path / "w.json"), trips_dir=trips) as c:
        c.trips_dir = trips
        yield c


def test_the_ui_previews_writes_and_runs_a_new_trip_offline(nt_client):
    d = nt_client.post("/api/trips/draft", NT).json()
    assert d["ok"] is True
    assert d["echo_lines"] == [
        "About to write sfo_lhr_jan.json:",
        "  L1  SFO->LHR, Jan 15 2027, one-way, business, 1 adult  ->  $2,400.00 USD",
        "  points_candidates: NONE ON ANY LEG. This trip is scoreable only with --live or "
        "--from-snapshot.",
    ]
    made = nt_client.post("/api/trips/create", dict(NT, draft_hash=d["draft_hash"])).json()
    assert made["id"] == "sfo_lhr_jan"
    assert made["lines"][1] == "This fixture has NO points prices. Score it LIVE or REPLAY."
    assert (nt_client.trips_dir / "sfo_lhr_jan.json").exists()
    listing = {t["id"]: t for t in nt_client.get("/api/trips").json()}
    assert listing["sfo_lhr_jan"]["live_only"] is True
    detail = nt_client.get("/api/trips/sfo_lhr_jan").json()
    assert detail["flags"] == [trip_builder.LIVE_ONLY_FLAG]
    run = nt_client.post("/api/trips/sfo_lhr_jan/run",
                         {"mode": "offline", "options": TRANSFER}).json()
    assert run["exit_code"] == 0
    assert run["trip"]["flags"] == [trip_builder.LIVE_ONLY_FLAG]
    (leg,) = run["legs"]
    assert leg["cells"]["path"]["segments"][0]["text"] == "never priced"
    assert leg["cells"]["verdict"]["segments"][0]["text"] == "PAY CASH (never priced)"
    assert "--trip-fixture " + str(nt_client.trips_dir / "sfo_lhr_jan.json") in run["argv_display"]


def test_an_existing_name_is_refused_in_the_writers_words(nt_client):
    d = nt_client.post("/api/trips/draft", dict(NT, name="trip_b_europe")).json()
    before = (nt_client.trips_dir / "trip_b_europe.json").read_bytes()
    r = nt_client.post("/api/trips/create", dict(NT, name="trip_b_europe", draft_hash=d["draft_hash"]))
    assert r.status == 409 and r.json()["error"] == "refused"
    assert r.json()["message"].endswith(
        "already exists and --force was not given. Refusing to overwrite: the file may hold "
        "captures nobody can reproduce.")
    assert (nt_client.trips_dir / "trip_b_europe.json").read_bytes() == before


@pytest.mark.parametrize("leg,field,start", [
    ({"cash": "0"}, "cash", "Leg 1 cash: a cash price of 0.0 is refused. Zero is not a price - it is silence"),
    ({"cash": "abc"}, "cash", "Leg 1 cash: 'abc' is not a number."),
    ({"origin": "SOF"}, "origin", "Leg 1 from: 'SOF' is not an airport this tool knows."),
    ({"date": "2027-02-30"}, "date", "Leg 1 date: '2027-02-30' is not an ISO date"),
    ({"destination": "SFO"}, "destination", "Leg 1: origin and destination are both SFO."),
    ({"cabin": "Q"}, "cabin", "--cabin 'Q' is not one of Y/W/J/F."),
])
def test_form_errors_are_the_builders_refusals_per_field(nt_client, leg, field, start):
    body = dict(NT, legs=[dict(NT["legs"][0], **leg)])
    d = nt_client.post("/api/trips/draft", body).json()
    assert d["ok"] is False
    msgs = [e["message"] for e in d["errors"] if e["field"] == field and e["leg"] == 1]
    assert msgs and msgs[0].startswith(start), d["errors"]


@pytest.mark.parametrize("name", ["../evil", "a/b", "", ".hidden", "x" * 0])
def test_a_trip_name_can_never_leave_the_trips_directory(nt_client, name):
    d = nt_client.post("/api/trips/draft", dict(NT, name=name)).json()
    assert d["ok"] is False and d["errors"][0]["field"] == "name"


def test_create_without_the_preview_hash_writes_nothing(nt_client):
    r = nt_client.post("/api/trips/create", NT)
    assert r.status == 409 and r.json()["error"] == "draft_changed"
    r = nt_client.post("/api/trips/create", dict(NT, draft_hash="0" * 64))
    assert r.status == 409
    assert not (nt_client.trips_dir / "sfo_lhr_jan.json").exists()


# ------------------------------------------------------------------- wallet


def test_with_no_wallet_every_run_is_the_clis_exit_2_refusal_and_spends_nothing(
    tmp_path, pinned, monkeypatch
):
    monkeypatch.setenv(config.KEY_ENV_VAR, g.FAKE_KEY)
    stub = g.Stub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        with running_server() as c:
            st = c.get("/api/state").json()["wallet"]
            assert st["missing"] is True and st["source"] is None
            runs = []
            for mode in ("offline", "live"):
                body = {"mode": mode, "options": TRANSFER}
                pf = c.post("/api/trips/trip_b_europe/preflight", body).json()
                runs.append(c.post("/api/trips/trip_b_europe/run",
                                   dict(body, confirm_id=pf["confirm_id"])).json())
            spf = c.post("/api/search/preflight", SEARCH).json()
            runs.append(c.post("/api/search/run", dict(SEARCH, confirm_id=spf["confirm_id"])).json())
    for run in runs:
        assert run["exit_code"] == 2 and run["refusal"]["kind"] == "wallet", run
        assert run["refusal"]["message"].startswith("Wallet error: No currencies supplied.")
    assert stub.calls == []


def test_an_edited_wallet_is_session_only_and_never_written(tmp_path, pinned):
    import os

    wallet = write_wallet(tmp_path / "wallet.json")
    before = (wallet.read_bytes(), os.stat(wallet).st_mtime_ns)
    with running_server(wallet_path=wallet) as c:
        st = c.get("/api/state").json()["wallet"]
        assert st["source"] == str(wallet) and st["from_file"] is True
        edited = c.post("/api/wallet", {"balances": {"UR": "120000", "MR": ""},
                                        "cards": ["Chase Sapphire Preferred"]}).json()
        assert edited["source"] == "entered in this session (not saved)"
        assert edited["balances"] == {"UR": 120000, "MR": None}
        assert "  MR: UNCONSTRAINED (balance not supplied)   valued at 1.00 cents/point" in edited["describe_lines"]
        run = c.post("/api/trips/trip_b_europe/run", {"mode": "offline", "options": TRANSFER}).json()
        assert "--balance UR=120000 --balance MR= --card 'Chase Sapphire Preferred'" in run["argv_display"]
        assert "  UR: 120,000   valued at 1.00 cents/point" in run["transcript"]
        # Putting the file's values back returns to --wallet PATH.
        back = c.post("/api/wallet", {"balances": {"UR": "160000"},
                                      "cards": ["Chase Sapphire Preferred"]}).json()
        assert back["source"] == str(wallet) and back["argv"] == ["--wallet", str(wallet)]
    assert (wallet.read_bytes(), os.stat(wallet).st_mtime_ns) == before


@pytest.mark.parametrize("body,start", [
    ({"balances": {"UR": "lots"}, "cards": []}, "--balance 'UR=lots': 'lots' is not a whole number"),
    ({"balances": {"XX": "1"}, "cards": []}, "Unknown currency 'XX'."),
    ({"balances": {"UR": "-5"}, "cards": []}, "Invalid balance for 'UR': -5"),
    ({"balances": {"UR": "1"}, "cards": ["Chase Sapphire Preferrd"]}, "Unknown card 'Chase Sapphire Preferrd'."),
    ({"balances": {}, "cards": []}, "No currencies supplied."),
])
def test_a_bad_wallet_is_refused_in_the_wallet_codes_words(client, body, start):
    r = client.post("/api/wallet", body).json()
    assert r["error"] == "wallet" and r["message"].startswith(start), r
    # ...and the session keeps the wallet it had.
    assert client.get("/api/state").json()["wallet"]["balances"] == {"UR": 160000}
