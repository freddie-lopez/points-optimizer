"""
The two manager design calls from round 1.

RUN STRIP. The three mode buttons are one control and are read across, so they
stay the same width. The reason REPLAY is unavailable carries a filesystem path
of no fixed length, which was inside the button and stretched it; it now sits
on its own line under the control, shortened, with the whole path in the title.

TRIP LISTING. `trip_001.json` and `trip_002.json` are the acceptance suite's
original inputs - a route, a date range and balances, with no `legs` key at
all. They load, so "CANNOT LOAD" would be false; "0 legs - 0 flights" reads as
a finding about the trip rather than a fact about the file. The listing says
which it is, in words taken from the file.
"""
import json
from pathlib import Path

from src.ui.engine import Engine, _no_legs_note
from tests._ui_harness import copy_trips, running_server

STATIC = Path(__file__).resolve().parent.parent / "src" / "ui" / "static"
APP_JS = (STATIC / "app.js").read_text()
APP_CSS = (STATIC / "app.css").read_text()


# ------------------------------------------------------------- the run strip


def test_the_reason_replay_is_unavailable_is_short_and_the_path_travels_beside_it(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "SNAPSHOT_DIR", str(tmp_path / "no" / "snapshots"))
    monkeypatch.setattr("src.ui.engine._replay_manifest_candidates", lambda: [])
    modes = Engine(trips_dir=copy_trips(tmp_path / "trips")).state()["modes"]
    assert modes["replay_manifests"] == []
    assert modes["replay_reason"] == "no snapshot to replay yet"
    assert len(modes["replay_reason"]) <= 40, "this string sits inside a mode button"
    detail = modes["replay_reason_detail"]
    assert "{path}" in detail["text"]
    assert detail["path"].endswith("MANIFEST.md")
    assert "MANIFEST.md" not in modes["replay_reason"]


def test_a_manifest_that_exists_leaves_both_fields_empty(tmp_path):
    modes = Engine(trips_dir=copy_trips(tmp_path / "trips")).state()["modes"]
    if modes["replay_manifests"]:
        assert modes["replay_reason"] is None
        assert modes["replay_reason_detail"] is None


def test_the_three_segments_are_one_width_and_the_note_is_its_own_line():
    assert "flex: 1 1 0" in APP_CSS, "the mode buttons size to their own text again"
    assert ".mode-note" in APP_CSS
    assert 'tid(el("span", "mode-note"), "replay-unavailable")' in APP_JS
    assert "pathEl.title = detail.path" in APP_JS, "the whole path must survive somewhere"


def test_the_shortener_never_drops_the_last_two_parts():
    """shortPath is JS; this pins the rule it implements, so a rewrite has one."""
    assert 'function shortPath(' in APP_JS
    assert '"…/" + parts.slice(-2).join("/")' in APP_JS


# ----------------------------------------------------------- the trip listing


def test_the_acceptance_inputs_are_named_for_what_they_are(tmp_path):
    trips = copy_trips(tmp_path / "trips")
    with running_server(trips_dir=trips) as c:
        rows = {t["id"]: t for t in c.get("/api/trips").json()}
        detail = c.get("/api/trips/trip_001").json()
    for name in ("trip_001", "trip_002"):
        row = rows[name]
        assert row["legs"] == 0 and row["load_error"] is None
        note = row["no_legs_note"]
        assert "no `legs` key" in note["text"] and "search request" in note["text"]
        raw = json.loads((trips / f"{name}.json").read_text())
        assert f"{raw['origin']}->{raw['destination']}" in note["text"]
        assert raw["date_range"]["from"] in note["text"]
        assert raw["date_range"]["to"] in note["text"]
        # The sidebar row gets the short form; the page gets the whole thing.
        assert note["short"] == f"a single-route search request for {raw['origin']}->{raw['destination']}"
        assert len(note["short"]) < len(note["text"])
    assert detail["no_legs_note"] == rows["trip_001"]["no_legs_note"]


def test_a_trip_with_legs_carries_no_such_note(tmp_path):
    with running_server(trips_dir=copy_trips(tmp_path / "trips")) as c:
        rows = {t["id"]: t for t in c.get("/api/trips").json()}
        detail = c.get("/api/trips/trip_b_europe").json()
    assert rows["trip_b_europe"]["no_legs_note"] is None
    assert rows["trip_b_europe"]["legs"] > 0
    assert detail["no_legs_note"] is None


def test_an_empty_legs_list_is_not_described_as_a_search_request(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"id": "empty", "name": "empty", "legs": []}))
    note = _no_legs_note(path)
    assert "`legs` list is empty" in note["text"]
    assert "search request" not in note["text"] and "search request" not in note["short"]


def test_the_page_shows_the_note_instead_of_a_row_of_zeroes():
    assert 'el("span", "flag", "NOT A PER-LEG TRIP")' in APP_JS
    assert APP_JS.count("t.no_legs_note") >= 1
    assert 'tid(el("div", "panel nolegs"), "fixture-no-legs")' in APP_JS
