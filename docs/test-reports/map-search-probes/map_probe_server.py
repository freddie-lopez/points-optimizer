"""
The map round's probe server: the restyle round's server (same canary, same
tmp HOME, same stubbed transport, same pinned clock) plus ONE thing it never
had - the hub file the page fetches at /static/hubs.json is whatever the
environment names:

    PO_HUBS_FILE=/tmp/x/hubs.json python map_probe_server.py <scenario>

An absent file is a 404 (the server's own path), a broken one is served as
is. `data/hubs.json` in the tree is never touched.

Prints one JSON line ({"port": N, ...}) and serves until "quit" on stdin.
stdin commands: "attempts", "quit", "key off", "key on".

NO BYTE LEAVES THIS MACHINE: socket.connect is refused for every address, HOME
and every POINTS_OPTIMIZER_* location are tmp, the key is the goldens' fake.
"""
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
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

work = Path(tempfile.mkdtemp(prefix="po-ui-map-probe-"))
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


def empty(url, **kw):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"data": [], "hasMore": False}
    r.text = "{}"
    return r


STUB = g.Stub()
TRANSPORTS = {
    "search_ok": STUB,
    "search_api_error": g.Refused(),
    "search_no_awards": empty,
    "no_key": STUB,
    "slow_search": STUB,
}
transport = TRANSPORTS[scenario]

patcher = patch("src.seats_client.requests.get", side_effect=transport)
patcher.start()

from src.ui import server as ui_server  # noqa: E402
from src.ui.api import route  # noqa: E402
from src.ui.engine import Engine  # noqa: E402
from src.ui.server import UIServer  # noqa: E402

# THE ONE ADDITION: the hub file is the one the environment names.
hubs_file = os.environ.get("PO_HUBS_FILE")
if hubs_file:
    ui_server.STATIC_FILES["/static/hubs.json"] = (Path(hubs_file), "application/json; charset=utf-8")
land_file = os.environ.get("PO_LAND_FILE")
if land_file:
    ui_server.STATIC_FILES["/static/land.json"] = (Path(land_file), "application/json; charset=utf-8")

engine = Engine(trips_dir=trips_dir, wallet_path=wallet_file)
if scenario == "slow_search":
    import time
    _real = engine.search_run

    def _slow(*a, **k):
        time.sleep(3)
        return _real(*a, **k)
    engine.search_run = _slow

logs = []
srv = UIServer(engine, route, port=0, log=logs.append)

print(json.dumps({
    "port": srv.port,
    "token": srv.token,
    "work": str(work),
    "trips_dir": str(trips_dir),
    "wallet": str(wallet_file),
    "hubs_file": hubs_file,
}), flush=True)

threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                 daemon=True).start()
try:
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "attempts":
            print(json.dumps({"attempts": [str(a) for a in ATTEMPTS],
                              "calls": getattr(transport, "calls", None) and
                              len(transport.calls),
                              "logs": logs[-20:]}), flush=True)
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
