"""
The JSON contract (docs/plans/ui.md 4.6, 7.1): the serializer never turns
"could not find out" into a clean value, and every marker the CLI prints for a
leg also reaches that leg's structured JSON.

Each golden scenario G1-G8 is run through the CLI's own `dispatch` (the golden
harness), and its FixtureRun is serialized exactly as the UI does.
"""
import json
import re

import pytest

from src.ui import serialize
from tests import _cli_golden as g

TRIP_SCENARIOS = ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"]

MARKERS = [
    "UNKNOWN", "NOT $0", "WITHHELD", "UNVERIFIED", "NOT LOOKED UP", "NOT RECORDED",
    "NOT KNOWN", "REPARSED", "API FAILED", "UNREADABLE", "BUDGET", "NEVER PRICED",
    "NOT ADDED", "CONFIRM BEFORE TRUSTING", "badge", "snapshot", "ASSUMPTION",
]


def _payload(name, tmp_path, monkeypatch):
    result = g.run_scenario(name, tmp_path / "w", monkeypatch)
    run = result.sink[0]
    out = {
        "run": run, "code": result.code, "transcript": result.text,
        "started_at": "x", "duration_s": 0.0, "calls_spent": 0,
        "argv_display": "x",
    }
    mode = "replay" if "--from-snapshot" in g.SCENARIOS[name].argv else (
        "offline" if "--offline" in g.SCENARIOS[name].argv else "live")
    payload = serialize.trip_run(out, mode, "trip", {"since_launch": 0, "cap": 1000})
    return result, run, payload


@pytest.fixture(scope="module")
def payloads(tmp_path_factory):
    # One run per scenario for the whole module (each takes about a second).
    from _pytest.monkeypatch import MonkeyPatch

    cache = {}
    for name in TRIP_SCENARIOS:
        mp = MonkeyPatch()
        try:
            cache[name] = _payload(name, tmp_path_factory.mktemp(name), mp)
        finally:
            mp.undo()
    return cache


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_the_payload_is_strict_json(payloads, name):
    _, _, payload = payloads[name]
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_exit_code_is_dispatchs(payloads, name):
    result, _, payload = payloads[name]
    assert payload["exit_code"] == result.code


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_no_clean_number_where_the_cli_shows_none(payloads, name):
    _, run, payload = payloads[name]
    for leg in payload["legs"]:
        cells = leg["cells"]
        if cells["surcharge"]["kind"] == "unknown":
            assert leg["surcharge"]["point"] is None
            assert leg["surcharge"]["low"] is None and leg["surcharge"]["high"] is None
        if cells["cash"]["kind"] == "unknown":
            assert leg["numbers"]["cash_usd"] is None
            assert leg["numbers"]["cash_pts"] is None
        if cells["score_points"]["kind"] in ("floor", "break_even", "none"):
            assert leg["numbers"]["score_points"] is None
        if leg["verdict"]["chip"] == "withheld":
            assert leg["numbers"]["score_points"] is None
    h = payload["headline"]
    if h["state"] == "range":
        assert h["pct"] is None
        assert h["pct_low"] is not None and h["pct_high"] is not None
    if h["state"] in ("withheld", "not_fundable"):
        assert h["pct"] is None and h["pct_low"] is None and h["pct_high"] is None
    if h["state"] == "single":
        assert h["pct"] is not None
    # The qualifier is always present on a number.
    if h["state"] in ("single", "range"):
        assert h["qualifier"].startswith("(") and h["qualifier"].endswith(")")


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_the_headline_text_is_the_cli_cell(payloads, name):
    result, _, payload = payloads[name]
    h = payload["headline"]
    flat = " ".join(result.text.split())
    assert " ".join(h["cli_value"].split()) in flat


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_every_leg_marker_in_the_cli_lines_is_also_in_structured_json(payloads, name):
    _, _, payload = payloads[name]
    for leg in payload["legs"]:
        cli_text = " ".join(line["text"] for line in leg["cli_lines"])
        cli_text += " " + " ".join(s["text"] for c in leg["cells"].values()
                                   for s in c["segments"])
        structured = dict(leg)
        structured.pop("cli_lines")
        blob = json.dumps(structured)
        for marker in MARKERS:
            if marker in cli_text:
                assert marker in blob, (name, leg["id"], marker)


@pytest.mark.parametrize("name", TRIP_SCENARIOS)
def test_every_cli_table_verdict_is_the_cells_verdict(payloads, name):
    result, run, payload = payloads[name]
    from src.formatter import leg_table_cells

    for r, leg in zip(run.results, payload["legs"]):
        assert leg["cells"]["verdict"] == leg_table_cells(r).verdict.to_json()


def test_G1_offline_trip_b_headline_is_the_range_with_its_badge_qualifier(payloads):
    _, _, payload = payloads["G1"]
    h = payload["headline"]
    assert (h["state"], h["text"], h["qualifier"]) == ("range", "2.04% - 11.03%", "(badge)")
    assert round(h["pct_low"], 2) == 2.04 and round(h["pct_high"], 2) == 11.03
    assert h["range_parts"] == [{"part": "surcharge", "legs": ["B3"]}]
    assert h["low_row"] == {"label": "low end = what is actually defensible",
                            "value": "2.04%  (badge)"}
    assert h["legs_counted_text"] == "0 of 4 legs live"
    assert payload["exit_code"] == 0 and payload["exit_label"] == "OK"
    b3 = next(l for l in payload["legs"] if l["id"] == "B3")
    assert b3["cells"]["verdict"]["segments"][0]["text"] == "WITHHELD (surch unknown)"
    assert b3["verdict"]["chip"] == "withheld"
    assert b3["cells"]["surcharge"]["kind"] == "unknown"
    assert b3["numbers"]["break_even"] == pytest.approx(36.0)


def test_G5_network_down_is_withheld_and_names_the_api_failure(payloads):
    _, _, payload = payloads["G5"]
    assert payload["exit_code"] == 3
    assert payload["headline"]["state"] == "withheld"
    b1 = next(l for l in payload["legs"] if l["id"] == "B1")
    assert b1["cells"]["provenance"]["kind"] == "api_failed"
    assert "API FAILED" in b1["cells"]["provenance"]["segments"][0]["text"]


def test_G8_not_fundable_carries_no_percentage(payloads):
    _, _, payload = payloads["G8"]
    h = payload["headline"]
    assert payload["exit_code"] == 4
    assert h["state"] == "not_fundable" and h["funding_note"]
    assert payload["funding_banner"][1]["text"] == "THIS TRIP CANNOT BE FUNDED FROM YOUR BALANCE."


def test_G6_replay_carries_the_manifest_hash_on_the_number(payloads):
    _, _, payload = payloads["G6"]
    h = payload["headline"]
    assert payload["replay"]["manifest_hash"].startswith("mh_")
    assert re.fullmatch(r"\(snapshot mh_[0-9a-f]+\)", h["qualifier"]), h["qualifier"]
    assert payload["calls"]["this_run"] is None


def test_G4_metal_line_carries_the_unverified_flag(payloads):
    _, _, payload = payloads["G4"]
    b4 = next(l for l in payload["legs"] if l["id"] == "B4")
    assert b4["metal"]["unverified"] is True
    assert b4["metal"]["line"].startswith("operating airline: VS by flight number (VS19).")
    b1 = next(l for l in payload["legs"] if l["id"] == "B1")
    assert b1["metal"]["unverified"] is False
    assert "NOT LOOKED UP" in b1["metal"]["line"]


def test_the_transcript_is_the_cli_output(payloads):
    result, _, payload = payloads["G1"]
    assert payload["transcript"] == result.text
