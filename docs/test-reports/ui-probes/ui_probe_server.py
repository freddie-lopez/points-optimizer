"""
Start ONE real UI server for a browser probe, in this process, with a stubbed
transport and a pinned clock, on an ephemeral port. Prints one JSON line
({"port": N, "trips_dir": ..., "snapshot_dir": ...}) and then serves until it
is killed.

    python ui_probe_server.py <scenario>

Scenarios differ only in the transport that is armed and in what is prepared
before the browser arrives (a LIVE run for REPLAY, a hostile-string payload,
...). The browser drives the app itself: it clicks the mode, presses Run,
confirms, and reads the DOM.

NO BYTE LEAVES THIS MACHINE: socket.connect is refused for every address (this
process never dials out; it only accepts), HOME and every POINTS_OPTIMIZER_*
location are tmp, and the key is the goldens' fake one.
"""
import copy
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

ATTEMPTS = []


def _refuse(self, address, *a, **k):
    ATTEMPTS.append(address)
    raise OSError(f"CANARY: connect({address!r})")


def _refuse_cc(address, *a, **k):
    ATTEMPTS.append(address)
    raise OSError(f"CANARY: create_connection({address!r})")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse_cc

work = Path(tempfile.mkdtemp(prefix="po-ui-probe-"))
os.environ["HOME"] = str(work / "home")
os.environ["POINTS_OPTIMIZER_ENV_FILE"] = str(work / "absent.env")
for var in ("POINTS_OPTIMIZER_CACHE_DIR", "POINTS_OPTIMIZER_SNAPSHOT_DIR", "FORCE_COLOR",
            "NO_COLOR", "COLUMNS"):
    os.environ.pop(var, None)
for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ[var] = "http://127.0.0.1:9"

from unittest.mock import MagicMock, patch  # noqa: E402

from src import config, response_cache, seats_client, seats_trips, trip_builder  # noqa: E402

config._ENV_PATH = work / "absent.env"
config.USER_CONFIG_ENV_PATH = work / "home" / ".config" / "points-optimizer" / ".env"
config._ENV_INJECTED.clear()
config.CACHE_DIR = work / "cache"
config.SNAPSHOT_DIR = work / "snapshots"

from tests import _cli_golden as g  # noqa: E402
from tests import _trips_payloads as tp  # noqa: E402
from tests._ui_harness import copy_trips, write_wallet  # noqa: E402

config.date = g._PinnedDate
trip_builder.date = g._PinnedDate
response_cache._utcnow = lambda: g.PINNED_NOW
seats_client.datetime = g._PinnedDatetime
seats_trips.TRIPS_SCHEMA_VERIFIED_BY = ""
seats_trips.TRIPS_TOTALTAXES_UNIT = "unverified"
os.environ["SEATS_AERO_KEY"] = g.FAKE_KEY

scenario = sys.argv[1]

trips_dir = copy_trips(work / "trips")
wallet_file = write_wallet(work / "wallet.json")
if scenario.startswith("g7"):
    shutil.copy(ROOT / "tests" / "fixtures" / "cli_golden" / "inputs" /
                "g7_never_priced_couple.json", trips_dir / "g7_never_priced_couple.json")

XSS = ('<img src=x onerror="window.__pwned=1">' '</pre><script>window.__pwned=1</script>'
       "‮RIGHT-TO-LEFT‬")
LONG = "L" * 5000


def hostile_row(base_route=("SFO", "MAD")):
    row = copy.deepcopy(g.REAL["data"][0])
    row["Route"]["OriginAirport"], row["Route"]["DestinationAirport"] = base_route
    row["Route"]["Source"] = "aeroplan"
    row["Date"], row["ParsedDate"] = "2027-01-15", "2027-01-15T00:00:00Z"
    row["ID"] = "hostilerow000000000000000" + "01"
    row["YAirlines"] = XSS + ", " + LONG
    row["Source"] = "aeroplan"
    return row


def hostile_search(route, params):
    if route == ("LHR", "SFO"):
        r = g.vs_row()
        r["JAirlines"] = XSS + ", VS"
        return {"data": [r]}
    return {"data": [hostile_row(route)]}


def hostile_trips(aid):
    return tp.payload([tp.trip([tp.segment(XSS[:40] + "19", "LHR", "SFO", 1,
                                           AvailabilityID=aid, aircraft=LONG[:200])],
                               availability_id=aid, source="virginatlantic",
                               cabin="business", cost=60000)])


def ondate_search(route, params):
    """G7's L2 (LHR->SFO, 2027-01-29, TWO travellers) really does get an award."""
    if route == ("LHR", "SFO"):
        row = g.vs_row()
        row["Date"] = params.get("start_date")
        row["ParsedDate"] = params.get("start_date") + "T00:00:00Z"
        return {"data": [row]}
    return {"data": [g.REAL["data"][0]]}


def empty(url, **kw):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"data": [], "hasMore": False}
    r.text = "{}"
    return r


STUB = g.Stub()
TRANSPORTS = {
    "offline_b": STUB,
    "live_b": STUB,
    "down_b": g.Refused(),
    "replay_b": STUB,
    "g7_offline": STUB,
    "g7_live": STUB,
    "g7_live_ondate": g.Stub(search_override=ondate_search),
    "search_ok": STUB,
    "search_api_error": g.Refused(),
    "search_none_fundable": STUB,
    "search_no_awards": empty,
    "hostile": g.Stub(search_override=hostile_search,
                      trips_payloads={g.B4_ID: hostile_trips(g.B4_ID)}),
    "no_wallet": STUB,
    "newtrip": STUB,
    "exit4": STUB,
}
transport = TRANSPORTS[scenario]

patcher = patch("src.seats_client.requests.get", side_effect=transport)
patcher.start()

from src.ui.api import route  # noqa: E402
from src.ui.engine import Engine  # noqa: E402
from src.ui.server import UIServer  # noqa: E402

engine = Engine(trips_dir=trips_dir,
                wallet_path=None if scenario == "no_wallet" else wallet_file)
if scenario == "exit4":
    from src import optimizer
    g._REAL_FUNDING_REPORT = optimizer.trip_funding_report
    patch("src.optimizer.trip_funding_report",
          side_effect=g._overdrawn_funding_report).start()

logs = []
srv = UIServer(engine, route, port=0, log=logs.append)

if scenario == "replay_b":
    # One LIVE run so a manifest exists, exactly as the UI itself would write it.
    opts = {"mode": "live", "options": {"transfer_date": "2026-09-15", "trips": "auto",
                                        "trips_cap": 10, "refresh": True}}
    pf = engine.trip_preflight("trip_b_europe", opts)
    engine.trip_run("trip_b_europe", dict(opts, confirm_id=pf["confirm_id"]))

print(json.dumps({
    "port": srv.port,
    "token": srv.token,
    "work": str(work),
    "trips_dir": str(trips_dir),
    "wallet": str(wallet_file),
    "snapshot_dir": str(config.SNAPSHOT_DIR),
}), flush=True)

threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                 daemon=True).start()
try:
    for line in sys.stdin:
        if line.strip() == "attempts":
            print(json.dumps({"attempts": [str(a) for a in ATTEMPTS],
                              "calls": getattr(transport, "calls", None) and
                              len(transport.calls)}), flush=True)
        if line.strip() == "quit":
            break
except KeyboardInterrupt:
    pass
