"""
The search->trip round's probe server: the restyle round's server (same
canary, same tmp HOME, same stubbed transport, same pinned clock) plus what
this round's probes need and it never had:

  * UI-built trips in the tmp trips dir (written by the builder itself), made
    on demand from stdin so a test can delete, re-make and hand-edit them;
  * a hostile Seats.aero transport whose row carries a hostile SOURCE (the
    program name and the cell testid) - the parser refuses a non-ISO Date, so
    a hostile date can only be injected in the browser (the probes do that);
  * the engine's run lock held from stdin ("lock" / "unlock"), and a run that
    takes seconds ("slow_run"), so a delete can be tried mid-run;
  * a listing of the tmp trips dir with mtimes and sha256 ("ls").

    PO_PROBE_ROOT=... python st_probe_server.py <scenario>

Prints one JSON line ({"port": N, ...}) and serves until "quit" on stdin.

NO BYTE LEAVES THIS MACHINE: socket.connect is refused for every address, HOME
and every POINTS_OPTIMIZER_* location are tmp, the key is the goldens' fake.
Nothing under the repository's tests/fixtures/ is ever the target of anything.
"""
import copy
import hashlib
import json
import os
import shutil
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

work = Path(tempfile.mkdtemp(prefix="po-ui-st-probe-"))
os.environ["HOME"] = str(work / "home")
os.environ["POINTS_OPTIMIZER_ENV_FILE"] = str(work / "absent.env")
for var in ("POINTS_OPTIMIZER_CACHE_DIR", "POINTS_OPTIMIZER_SNAPSHOT_DIR", "FORCE_COLOR",
            "NO_COLOR", "COLUMNS"):
    os.environ.pop(var, None)
for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ[var] = "http://127.0.0.1:9"

from unittest.mock import patch  # noqa: E402

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
os.environ["SEATS_AERO_KEY"] = g.FAKE_KEY

if scenario == "one_trip":
    trips_dir = work / "trips"
    trips_dir.mkdir()
else:
    trips_dir = copy_trips(work / "trips")
wallet_file = write_wallet(work / "wallet.json")


def ui_built(name="sfo-mad-2027-01-15", leg="SFO:MAD:2027-01-15:2400", cabin="Y"):
    """A fixture exactly as the page (and --new-trip) writes one, in the TMP dir."""
    return trip_builder.new_trip_from_flags(name, [leg], [], cabin=cabin, directory=trips_dir,
                                            today=g.PINNED_TODAY)


if scenario in ("ui_built", "one_trip", "slow_run", "hostile_search"):
    ui_built()
if scenario == "ui_built":
    ui_built("second-trip", "MAD:AMS:2027-01-19:120", "J")
    ui_built("x" * 120, "LHR:SFO:2027-01-27:900", "J")

XSS = ('<img src=x onerror="window.__pwned=1">' '</pre><script>window.__pwned=1</script>'
       "‮RIGHT-TO-LEFT‬")
LONG = "L" * 5000


def hostile_search(route, params):
    """One row whose SOURCE (program) is hostile: the table's program column,
    the cell testid, P3 and P4 all render it. The date is ISO (the parser
    refuses anything else); the browser probes inject the hostile dates."""
    row = copy.deepcopy(g.REAL["data"][0])
    row["Route"]["OriginAirport"], row["Route"]["DestinationAirport"] = route
    row["Route"]["Source"] = XSS + " " + LONG[:2000]
    row["Source"] = row["Route"]["Source"]
    iso = params.get("start_date") or "2027-01-15"
    row["Date"], row["ParsedDate"] = iso, f"{iso}T00:00:00Z"
    row["ID"] = "hostilerow00000000000000001"
    row["YAirlines"] = XSS
    row["YAvailable"], row["JAvailable"] = True, True
    row["JMileageCost"], row["JTotalTaxes"], row["JRemainingSeats"] = "70000", 3236, 1
    return {"data": [row]}


STUB = g.Stub()
TRANSPORTS = {
    "ui_built": STUB,
    "one_trip": STUB,
    "slow_run": STUB,
    "hostile_search": g.Stub(search_override=hostile_search),
}
transport = TRANSPORTS[scenario]
patch("src.seats_client.requests.get", side_effect=transport).start()

from src.ui import server as ui_server  # noqa: E402
from src.ui.api import route  # noqa: E402
from src.ui.engine import Engine  # noqa: E402
from src.ui.server import UIServer  # noqa: E402

# As the map round's server: the hub file is the one the environment names.
hubs_file = os.environ.get("PO_HUBS_FILE")
if hubs_file:
    ui_server.STATIC_FILES["/static/hubs.json"] = (Path(hubs_file), "application/json; charset=utf-8")

engine = Engine(trips_dir=trips_dir, wallet_path=wallet_file)
if scenario == "slow_run":
    import time
    _real_run = engine.trip_run

    def _slow(*a, **k):
        time.sleep(4)
        return _real_run(*a, **k)
    engine.trip_run = _slow

logs = []
srv = UIServer(engine, route, port=0, log=logs.append)

print(json.dumps({
    "port": srv.port,
    "token": srv.token,
    "work": str(work),
    "trips_dir": str(trips_dir),
    "wallet": str(wallet_file),
}), flush=True)

threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                 daemon=True).start()


def listing():
    out = {}
    for p in sorted(trips_dir.iterdir()):
        st = os.stat(p, follow_symlinks=False)
        out[p.name] = {"mtime": st.st_mtime_ns, "link": p.is_symlink(),
                       "sha": hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
    return out


def edit(name, kind):
    path = trips_dir / f"{name}.json"
    d = json.loads(path.read_text())
    if kind == "points":
        d["legs"][0]["points_candidates"] = []
    elif kind == "source":
        d["source"] = "screenshot, captured 2026-09-14 by hand"
    elif kind == "flag":
        d["trip_level_flags"] = []
    elif kind == "cashsrc":
        d["legs"][0]["cash_options"][0]["source"] = "screenshot"
    elif kind == "extracash":
        d["legs"][0]["cash_options"].append(dict(d["legs"][0]["cash_options"][0], source="google_flights"))
    elif kind == "cash":
        d["legs"][0]["cash_options"][0]["amount"] = 1.0
    elif kind == "broken":
        path.write_text("{ not json")
        return
    path.write_text(json.dumps(d, indent=2) + "\n")


try:
    for line in sys.stdin:
        cmd = line.strip().split(" ")
        if cmd[0] == "attempts":
            print(json.dumps({"attempts": [str(a) for a in ATTEMPTS],
                              "calls": getattr(transport, "calls", None) and
                              len(transport.calls), "logs": logs[-40:]}), flush=True)
        elif cmd[0] == "ls":
            print(json.dumps(listing()), flush=True)
        elif cmd[0] == "mk":
            p = ui_built(*cmd[1:])
            print(json.dumps({"path": str(p)}), flush=True)
        elif cmd[0] == "edit":
            edit(cmd[1], cmd[2])
            print(json.dumps({"ok": True}), flush=True)
        elif cmd[0] == "rm":
            (trips_dir / cmd[1]).unlink()
            print(json.dumps({"ok": True}), flush=True)
        elif cmd[0] == "lock":
            print(json.dumps({"locked": engine._lock.acquire(blocking=False)}), flush=True)
        elif cmd[0] == "unlock":
            engine._lock.release()
            print(json.dumps({"locked": False}), flush=True)
        elif cmd[0] == "logs":
            print(json.dumps({"logs": logs}), flush=True)
        elif cmd[0] == "quit":
            break
except KeyboardInterrupt:
    pass
shutil.rmtree(work, ignore_errors=True)
