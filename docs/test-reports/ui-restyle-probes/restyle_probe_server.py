"""
The restyle round's probe server: ui_probe_server.py (round 1-6 harness) plus
the states this round needs and never had - no key, a key that comes and goes,
a wallet file that does not parse, a fixture file that does not parse, a
snapshot manifest whose path is very long.

    python restyle_probe_server.py <scenario>

Prints one JSON line ({"port": N, ...}) and serves until "quit" on stdin.
stdin commands: "attempts", "quit", "key off", "key on".

NO BYTE LEAVES THIS MACHINE: socket.connect is refused for every address, HOME
and every POINTS_OPTIMIZER_* location are tmp, the key is the goldens' fake.
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
# PO_PROBE_ROOT lets the same probe run against a checkout of the base commit
# (a git worktree of 386b2fc), so "new" and "pre-existing" can be told apart.
ROOT = Path(os.environ.get("PO_PROBE_ROOT") or HERE.parent.parent.parent).resolve()
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

work = Path(tempfile.mkdtemp(prefix="po-ui-restyle-probe-"))
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

scenario = sys.argv[1]

if scenario.startswith("no_key"):
    os.environ.pop("SEATS_AERO_KEY", None)
else:
    os.environ["SEATS_AERO_KEY"] = g.FAKE_KEY

trips_dir = copy_trips(work / "trips")
wallet_file = write_wallet(work / "wallet.json")
if scenario.startswith("g7"):
    shutil.copy(ROOT / "tests" / "fixtures" / "cli_golden" / "inputs" /
                "g7_never_priced_couple.json", trips_dir / "g7_never_priced_couple.json")
if scenario in ("broken_fixture", "hostile_list"):
    # A file that cannot load, next to the real ones - in the TMP dir only.
    (trips_dir / "broken_file.json").write_text("{ not json")
if scenario == "hostile_list":
    # A per-leg trip whose NAME and description are hostile, in the tmp dir only.
    src = json.loads((trips_dir / "trip_b_europe.json").read_text())
    src["name"] = ('<img src=x onerror="window.__pwned=1">' "‮RIGHT-TO-LEFT‬ "
                   + "N" * 5000)
    src["description"] = "D" * 3000
    (trips_dir / "hostile_name.json").write_text(json.dumps(src))
if scenario == "long_snapshot_path":
    # A REAL snapshot dir whose display path is > 200 chars.
    deep = work
    for i in range(6):
        deep = deep / ("segment_%d_" % i + "x" * 30)
    config.SNAPSHOT_DIR = deep / "snapshots"
if scenario == "no_key_longpath":
    # The longest key-error text the engine can produce: both file paths it
    # names are as long as a filesystem allows (PATH_MAX-ish, single segment
    # each, no break opportunity).
    config._ENV_PATH = work / ("env_" + "e" * 250) / ("relocated_" + "r" * 250 + ".env")
    config.USER_CONFIG_ENV_PATH = work / ("home_" + "h" * 250) / ".config" / "points-optimizer" / ".env"
    os.environ["POINTS_OPTIMIZER_ENV_FILE"] = str(config._ENV_PATH)
if scenario == "wallet_error":
    wallet_file.write_text("{ this is not a wallet")

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
    "offline_c": STUB,
    "live_b": STUB,
    "down_b": g.Refused(),
    "replay_b": STUB,
    "g7_offline": STUB,
    "g7_live": STUB,
    "search_ok": STUB,
    "search_api_error": g.Refused(),
    "search_no_awards": empty,
    "hostile": g.Stub(search_override=hostile_search,
                      trips_payloads={g.B4_ID: hostile_trips(g.B4_ID)}),
    "hostile_list": STUB,
    "no_wallet": STUB,
    "wallet_error": STUB,
    "no_key": STUB,
    "no_key_toggle": STUB,
    "no_key_longpath": STUB,
    "broken_fixture": STUB,
    "long_snapshot_path": STUB,
    "exit4": STUB,
    "slow_run": STUB,
}
transport = TRANSPORTS[scenario]

patcher = patch("src.seats_client.requests.get", side_effect=transport)
patcher.start()

from src.ui.api import route  # noqa: E402
from src.ui.engine import Engine  # noqa: E402
from src.ui.server import UIServer  # noqa: E402

engine = Engine(trips_dir=trips_dir,
                wallet_path=None if scenario == "no_wallet" else wallet_file)
if scenario == "slow_run":
    # A run that takes 3 s, so the page's in-flight state can be measured.
    import time
    _real_run = engine.trip_run

    def _slow(*a, **k):
        time.sleep(3)
        return _real_run(*a, **k)
    engine.trip_run = _slow
if scenario == "exit4":
    from src import optimizer
    g._REAL_FUNDING_REPORT = optimizer.trip_funding_report
    patch("src.optimizer.trip_funding_report",
          side_effect=g._overdrawn_funding_report).start()

logs = []
srv = UIServer(engine, route, port=0, log=logs.append)

if scenario == "replay_b":
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
        cmd = line.strip()
        if cmd == "attempts":
            print(json.dumps({"attempts": [str(a) for a in ATTEMPTS],
                              "calls": getattr(transport, "calls", None) and
                              len(transport.calls)}), flush=True)
        elif cmd == "key off":
            os.environ.pop("SEATS_AERO_KEY", None)
            print(json.dumps({"key": False}), flush=True)
        elif cmd == "key on":
            os.environ["SEATS_AERO_KEY"] = g.FAKE_KEY
            print(json.dumps({"key": True}), flush=True)
        elif cmd == "quit":
            break
except KeyboardInterrupt:
    pass
