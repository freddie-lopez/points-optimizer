"""
FINDINGS L-1 to L-4, the four small ones from round 1.

L-1  "N since launch" was read off Seats.aero's DAILY counter, which zeroes at
     the date change, so a server left running overnight reported fewer calls
     than it had spent. The two numbers are now two numbers.
L-2  The terminal loses the surcharge's confidence word: it is printed as
     "[modeled]" and rich reads that as a style tag and eats it. The drawer
     takes the word from the engine field instead. The CLI line is an older
     defect than this round and is filed, not fixed - the test below pins that
     it is still there, so nobody thinks it was.
L-3  Both drawers carried data-testid="drawer".
L-4  /static/app.js was readable cross-origin as a <script>.
"""
import json
import re
from datetime import date as real_date
from pathlib import Path

import pytest

from src.seats_client import SeatsClient
from src.ui.engine import Engine
from tests._ui_harness import copy_trips, running_server, write_wallet

STATIC = Path(__file__).resolve().parent.parent / "src" / "ui" / "static"


# ------------------------------------------------------------------ L-1


def test_the_launch_count_survives_midnight(monkeypatch, tmp_path):
    engine = Engine(trips_dir=copy_trips(tmp_path / "trips"))
    before = engine.calls_state()
    SeatsClient._count_call()
    SeatsClient._count_call()
    mid = engine.calls_state()
    assert mid["since_launch"] == before["since_launch"] + 2

    class Tomorrow(real_date):
        @classmethod
        def today(cls):
            return real_date.today().fromordinal(real_date.today().toordinal() + 1)

    monkeypatch.setattr("src.seats_client.date", Tomorrow)
    after = engine.calls_state()
    assert after["since_launch"] == mid["since_launch"], "the launch count reset at midnight"
    assert after["spent_today"] == 0, "the daily budget is the one that resets"
    assert after["remaining"] == SeatsClient.DAILY_CALL_CAP


def test_the_two_counts_are_both_published_and_labelled(tmp_path):
    with running_server(trips_dir=copy_trips(tmp_path / "trips")) as c:
        calls = c.get("/api/state").json()["calls"]
    assert set(calls) == {"since_launch", "spent_today", "cap", "remaining"}
    js = (STATIC / "app.js").read_text()
    assert "since launch" in js and "today" in js
    assert ' since launch / " + fmtInt' not in js, "the counts were on one scale"


def test_a_new_engine_starts_its_own_count_at_zero(tmp_path):
    SeatsClient._count_call()
    assert Engine(trips_dir=copy_trips(tmp_path / "trips")).calls_state()["since_launch"] == 0


# ------------------------------------------------------------------ L-2


def test_the_drawer_takes_the_confidence_word_from_the_engine_field():
    js = (STATIC / "app.js").read_text()
    assert "leg.surcharge.confidence" in js
    assert "surcharge-confidence" in js
    assert ".chip-modeled" in (STATIC / "app.css").read_text()


def test_the_api_carries_the_confidence_word_even_though_the_terminal_drops_it(tmp_path):
    trips = copy_trips(tmp_path / "trips", names=["trip_b_europe.json"])
    with running_server(trips_dir=trips, wallet_path=write_wallet(tmp_path / "w.json")) as c:
        run = c.post("/api/trips/trip_b_europe/run", {"mode": "offline", "options": {}}).json()
    assert run["exit_code"] in (0, 3, 4), run.get("refusal")
    legs = [l for l in run["legs"] if l["surcharge"] and l["surcharge"]["known"]]
    assert legs, "the fixture no longer has a leg with a known surcharge"
    assert {l["surcharge"]["confidence"] for l in legs} <= {"modeled", "captured"}
    # Pinned deliberately: the terminal's own line still loses the word.
    said = [l for l in legs if "[modeled]" in " ".join(x["text"] for x in l["cli_lines"])]
    assert not said, "the CLI now prints it - fix this test and drop the drawer's fallback"


# ------------------------------------------------------------------ L-3


def test_every_data_testid_in_the_page_is_unique():
    ids = re.findall(r'data-testid="([^"]+)"', (STATIC / "index.html").read_text())
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert dupes == [], f"a querySelector would silently pick the first: {dupes}"
    assert {"drawer-trips", "drawer-search"} <= set(ids)


# ------------------------------------------------------------------ L-4


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/app.css", "/api/state"])
def test_nothing_this_server_serves_is_readable_cross_origin(path, tmp_path):
    with running_server(trips_dir=copy_trips(tmp_path / "trips")) as c:
        r = c.get(path)
    assert r.status == 200, path
    assert r.headers.get("cross-origin-resource-policy") == "same-origin", path
    assert r.headers.get("x-frame-options") == "DENY"
