"""
The golden scenarios, driven through the UI's API instead of the CLI (shared by
the parity probes and the browser server). Each returns the run JSON (or a list
of JSONs for multi-step scenarios) and the transport used.
"""
import shutil
from pathlib import Path
from unittest.mock import patch

from conftest import LIVE, OFFLINE, ROOT, TRANSFER, g, response

G7_INPUT = ROOT / "tests" / "fixtures" / "cli_golden" / "inputs" / "g7_never_priced_couple.json"
SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}
REPLAY = {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}}

TRIP_SCENARIOS = ["G1_offline_b", "G2_offline_a", "G3_offline_c", "G4_live_b", "G5_down_b",
                  "G6_replay_b", "G7_never_couple_offline", "G7L_couple_live",
                  "G8_exit4", "G12_no_wallet", "G13_replay_refused"]
SEARCH_SCENARIOS = ["G9_search_ok", "G10_search_api_error", "G11_search_none_fundable",
                    "S_no_awards"]


def install_g7(trips_dir: Path) -> str:
    shutil.copy(G7_INPUT, trips_dir / "g7_never_priced_couple.json")
    return "g7_never_priced_couple"


def run(ui, name, monkeypatch=None):
    """Run one named scenario on a live `ui` client. Returns the run JSON."""
    net = ui.net
    net.impl = g.Stub()
    if name == "G1_offline_b":
        return ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    if name == "G2_offline_a":
        return ui.run_trip("trip_a_mry_nyc", OFFLINE)[1].json()
    if name == "G3_offline_c":
        return ui.run_trip("trip_c_lon_mry_surcharge", OFFLINE)[1].json()
    if name == "G4_live_b":
        return ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))[1].json()
    if name == "G5_down_b":
        net.impl = g.Refused()
        return ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))[1].json()
    if name == "G6_replay_b":
        ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))
        net.impl = g.Refused()
        return ui.run_trip("trip_b_europe", REPLAY)[1].json()
    if name == "G7_never_couple_offline":
        tid = install_g7(ui.trips_dir)
        return ui.run_trip(tid, OFFLINE)[1].json()
    if name == "G7L_couple_live":
        tid = install_g7(ui.trips_dir)
        return ui.run_trip(tid, dict(LIVE, options={**LIVE["options"], "refresh": True}))[1].json()
    if name == "G8_exit4":
        from src import optimizer

        g._REAL_FUNDING_REPORT = optimizer.trip_funding_report
        with patch("src.optimizer.trip_funding_report", side_effect=g._overdrawn_funding_report):
            return ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    if name == "G12_no_wallet":
        eng = ui.engine
        saved = eng.wallet_path, eng.session_wallet
        eng.wallet_path, eng.session_wallet = None, None
        try:
            return ui.post("/api/trips/trip_b_europe/run", OFFLINE).json()
        finally:
            eng.wallet_path, eng.session_wallet = saved
    if name == "G13_replay_refused":
        from src import config

        ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))
        for p in sorted(Path(config.SNAPSHOT_DIR).glob("B2_*.json")):
            p.unlink()
        net.impl = g.Refused()
        return ui.run_trip("trip_b_europe", REPLAY)[1].json()
    if name == "G9_search_ok":
        return ui.search(SEARCH)[1].json()
    if name == "G10_search_api_error":
        net.impl = g.Refused()
        return ui.search(SEARCH)[1].json()
    if name == "G11_search_none_fundable":
        ui.post("/api/wallet", {"balances": {"MR": "100000"}, "cards": []})
        try:
            return ui.search(SEARCH)[1].json()
        finally:
            ui.post("/api/wallet", {"balances": {"UR": "160000"},
                                    "cards": ["Chase Sapphire Preferred"]})
    if name == "S_no_awards":
        net.impl = lambda url, **kw: response({"data": [], "hasMore": False})
        return ui.search(SEARCH)[1].json()
    raise KeyError(name)
