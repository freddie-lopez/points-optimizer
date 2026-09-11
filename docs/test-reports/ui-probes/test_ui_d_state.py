"""
D. LONG-LIVED PROCESS STATE (the architect's risk #1). The CLI was written for
one run per process. The UI runs many, in any order, for days. Anything that
leaks from one run into the next is a wrong answer with a clean face.

Each probe runs a mix of scenarios in ONE server process and compares what the
server said with the same run done first, or in a fresh process.

RED = a defect that exists. GREEN = held up.
"""
import json
import re

import pytest

from conftest import LIVE, OFFLINE, TRANSFER, g, response, server, walk_strings

SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}
REPLAY = {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}}
LIVE_FRESH = {"mode": "live", "options": {**LIVE["options"], "refresh": True}}


def norm(text):
    """The goldens' normalizer plus the run-to-run instants the UI adds."""
    masked, _ = g.normalize(text or "")
    masked = re.sub(r"\d+\.\d+ s", "<S>", masked)
    return masked


def body_without_run_ids(payload):
    text = json.dumps(payload, sort_keys=True)
    text = re.sub(r'"run_id": ?"[0-9a-f]{12}"', '"run_id": "<ID>"', text)
    text = re.sub(r'"(started_at|duration_s)": ?[^,}]+', r'"\1": "<T>"', text)
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<U>", text)
    # "since launch" legitimately grows; everything else must not move.
    text = re.sub(r'"since_launch": ?\d+', '"since_launch": <N>', text)
    return norm(text)


def test_D1_the_same_offline_run_is_identical_before_and_after_live_replay_and_search(ui):
    """Trip B offline, then a LIVE run, a REPLAY, a search and a wallet edit,
    then the SAME offline run again. Byte-identical, or something leaked."""
    first = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    ui.run_trip("trip_b_europe", REPLAY)
    ui.search(SEARCH)
    ui.post("/api/wallet", {"balances": {"UR": "160000"}, "cards": ["Chase Sapphire Preferred"]})
    again = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert body_without_run_ids(first) == body_without_run_ids(again)


def test_D2_a_live_run_is_identical_whether_it_is_the_first_or_the_fifth_run(ui):
    first = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    for _ in range(2):
        ui.run_trip("trip_b_europe", OFFLINE)
        ui.search(SEARCH)
    fifth = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    a, b = norm(first["transcript"]), norm(fifth["transcript"])
    # The call counter legitimately grows; strip the budget line before comparing.
    drop = re.compile(r"^.*budget: .* calls remaining today.*$", re.M)
    assert drop.sub("", a) == drop.sub("", b)


def test_D3_a_search_after_a_trip_run_does_not_inherit_the_trips_awards(ui):
    """The in-process cache has no TTL and is class-level: a trip run's SFO->MAD
    answer must not be handed to a later search (or the other way round)."""
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    n = ui.net.search_calls
    pf, r = ui.search(SEARCH)
    assert r.status == 200, r.text
    assert ui.net.search_calls > n, "the search was answered from the previous run's memory"


def test_D4_the_in_process_cache_is_cleared_between_two_identical_live_runs(ui):
    a = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    n = ui.net.search_calls
    b = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    assert ui.net.search_calls - n == 4
    assert a["calls"]["this_run"] == b["calls"]["this_run"]


def test_D5_a_429_on_one_run_does_not_silence_the_next_run(ui):
    """`saw_http_429` is sticky for the life of a client. A new run must get a
    new client, or one rate-limited run would quietly stop every later lookup."""
    import requests

    def rate_limited(url, **kw):
        r = response({"error": "rate limited"}, status=429)
        r.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
        return r

    ui.net.impl = rate_limited
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    ui.net.impl = g.Stub()
    run = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    assert run["calls"]["this_run"]["trips"] >= 1, (
        "the itinerary lookup was skipped: a 429 in an EARLIER run is still in force")


def test_D6_the_fx_table_and_valuation_are_the_same_on_every_run(ui):
    """`config` FX state is module-level. Nothing in the UI may move it."""
    a = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    ui.search(SEARCH)
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    b = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert a["context"]["fx_lines"] == b["context"]["fx_lines"]
    assert a["context"]["valuation_line"] == b["context"]["valuation_line"]


def test_D7_a_refusal_run_does_not_poison_the_next_good_run(ui):
    """A wallet error, a bad option and a replay refusal, then a clean run."""
    ui.post("/api/wallet", {"balances": {"ZZ": "1"}, "cards": []})
    ui.post("/api/trips/trip_b_europe/run", OFFLINE)
    ui.post("/api/trips/trip_b_europe/run", {"mode": "offline", "options": {"transfer_date": "x"}})
    ui.post("/api/wallet", {"balances": {"UR": "160000"}, "cards": ["Chase Sapphire Preferred"]})
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert run["exit_code"] == 0 and run["headline"]["text"] == "2.04% - 11.03%"


def test_D8_twenty_one_runs_evict_the_oldest_and_say_so(ui):
    ids = []
    for _ in range(21):
        ids.append(ui.run_trip("trip_b_europe", OFFLINE)[1].json()["run_id"])
    assert ui.get(f"/api/runs/{ids[-1]}").status == 200
    gone = ui.get(f"/api/runs/{ids[0]}")
    assert gone.status == 404 and "kept in memory" in gone.json()["message"]


def test_D9_the_run_store_is_never_a_place_a_key_or_a_stale_wallet_hides(ui):
    """A stored run is served again later. It must carry the same redactions."""
    from src import config

    run = ui.run_trip("trip_b_europe", LIVE)[1].json()
    again = ui.get(f"/api/runs/{run['run_id']}")
    assert again.status == 200
    text = again.text
    assert g.FAKE_KEY not in text and config.mask_key(g.FAKE_KEY) not in text
    assert json.loads(text)["transcript"] == run["transcript"]


def test_D10_state_running_flag_is_true_only_while_a_run_holds_the_slot(ui):
    assert ui.get("/api/state").json()["running"] is False
    with ui.engine.run_slot():
        assert ui.get("/api/state").json()["running"] is True
    assert ui.get("/api/state").json()["running"] is False


def test_D11_a_replay_run_does_not_leave_the_replay_transport_in_place(ui):
    """After a REPLAY, a LIVE run must really ask the transport again."""
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    ui.run_trip("trip_b_europe", REPLAY)
    n = ui.net.search_calls
    run = ui.run_trip("trip_b_europe", LIVE_FRESH)[1].json()
    assert ui.net.search_calls - n == 4
    assert run["headline"]["qualifier"] == "(live)"


def test_D12_offline_after_live_does_not_inherit_live_provenance(ui):
    ui.run_trip("trip_b_europe", LIVE_FRESH)
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert run["headline"]["provenance"] == "badge"
    assert "0 of 4" in run["headline"]["legs_counted_text"]
    for leg in run["legs"]:
        assert (leg["live"] or {}).get("state") in (None, "not_engaged", "offline"), leg["live"]
