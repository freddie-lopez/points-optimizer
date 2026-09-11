"""
Run ONE CLI invocation against a given source tree, in a fresh process, and
print its output. Used by test_ui_g_cli_parity.py to diff the CLI of the
pre-UI tree (3c104b3, extracted with `git archive`) against this tree.

    python cli_diff_runner.py <tree> <scenario-json>

The scenario is {"argv": [...], "transport": null|"stub"|"refused"|"offdate"|
"page3"|"endless"|"empty", "before": [argv...]|null, "key": bool, "fixture": path|null}.

Harness (the suite's own rules, applied by hand because conftest is not loaded):
HOME and every POINTS_OPTIMIZER_* location point into a tmp dir; no key unless
the scenario asks for the fake one; the clock is pinned exactly as the goldens
pin it; `requests.get` is the scenario's stub; and a socket canary refuses and
reports ANY connect(), so no byte can leave the machine.
"""
import contextlib
import copy
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

tree = Path(sys.argv[1]).resolve()
scen = json.loads(sys.argv[2])
HERE = Path(__file__).resolve().parent
UI_ROOT = HERE.parent.parent.parent

work = Path(tempfile.mkdtemp(prefix="po-cli-diff-"))
os.environ["HOME"] = str(work / "home")
os.environ["POINTS_OPTIMIZER_ENV_FILE"] = str(work / "absent.env")
for var in ("POINTS_OPTIMIZER_CACHE_DIR", "POINTS_OPTIMIZER_SNAPSHOT_DIR", "FORCE_COLOR",
            "NO_COLOR", "COLUMNS", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
    os.environ.pop(var, None)
os.environ.pop("SEATS_AERO_KEY", None)
for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ[var] = "http://127.0.0.1:9"

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

sys.path.insert(0, str(tree))
os.chdir(tree)

from src import config  # noqa: E402

config._ENV_PATH = work / "absent.env"
config.USER_CONFIG_ENV_PATH = work / "home" / ".config" / "points-optimizer" / ".env"
config._ENV_INJECTED.clear()

# the goldens' pins and stubs (pure helpers; `src` above is the TREE's)
sys.path.insert(1, str(UI_ROOT))
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("_golden_rt", UI_ROOT / "tests" / "_cli_golden.py")
g = importlib.util.module_from_spec(spec)
sys.modules["_golden_rt"] = g
spec.loader.exec_module(g)

from unittest.mock import MagicMock, patch  # noqa: E402

from src import response_cache, seats_client, seats_trips  # noqa: E402

os.chdir(work)
config.CACHE_DIR = Path("cache")
config.SNAPSHOT_DIR = Path("snapshots")
config.date = g._PinnedDate
response_cache._utcnow = lambda: g.PINNED_NOW
seats_client.datetime = g._PinnedDatetime
seats_trips.TRIPS_SCHEMA_VERIFIED_BY = ""
seats_trips.TRIPS_TOTALTAXES_UNIT = "unverified"
try:
    from src import trip_builder
    trip_builder.date = g._PinnedDate
except Exception:  # noqa: BLE001
    pass

if scen.get("key"):
    os.environ["SEATS_AERO_KEY"] = g.FAKE_KEY


def resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


def transport(kind):
    base = g.Stub()
    if kind == "stub":
        return base
    if kind == "refused":
        return g.Refused()
    if kind == "empty":
        return lambda url, **kw: resp({"data": [], "hasMore": False})
    if kind == "endless":
        def endless(url, **kw):
            n = int((kw.get("params") or {}).get("cursor") or 1)
            return resp({"data": [], "hasMore": True, "cursor": str(n + 1)})
        return endless
    if kind == "page3":
        def page3(url, **kw):
            p = kw.get("params") or {}
            if not url.endswith("/search"):
                return base(url, **kw)
            n = int(p.get("cursor") or 1)
            r = base(url, **{**kw, "params": {k: v for k, v in p.items() if k != "cursor"}})
            body = copy.deepcopy(r.json.return_value)
            for row in body["data"]:
                row["ID"] = (f"pg{n}" + row["ID"])[:27]
            body["hasMore"] = n < 3
            if n < 3:
                body["cursor"] = str(n + 1)
            return resp(body)
        return page3
    if kind == "offdate":
        return base  # G7's L2 is Jan 29; the stub's LHR-SFO row is Jan 27
    if kind == "ondate":
        def ondate(url, **kw):
            p = kw.get("params") or {}
            if url.endswith("/search") and (p.get("origin_airport"), p.get("destination_airport")) == ("LHR", "SFO"):
                row = g.vs_row()
                row["Date"], row["ParsedDate"] = p.get("start_date"), p.get("start_date") + "T00:00:00Z"
                return resp({"data": [row]})
            return base(url, **kw)
        return ondate
    raise KeyError(kind)


def invoke(argv):
    from src.seats_client import SeatsClient

    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    import src.main as cli

    buf = io.StringIO()
    old = sys.argv
    sys.argv = ["python -m src.main"] + list(argv)
    try:
        with contextlib.redirect_stdout(buf):
            try:
                code = cli.main()
            except SystemExit as e:
                code = f"SystemExit({e.code})"
    finally:
        sys.argv = old
    return code, buf.getvalue()


stack = contextlib.ExitStack()
with stack:
    if scen.get("transport"):
        stack.enter_context(patch("src.seats_client.requests.get",
                                  side_effect=transport(scen["transport"])))
    for before in scen.get("before") or []:
        invoke(before)
    if scen.get("drop"):
        for p in sorted(Path("snapshots").glob(scen["drop"])):
            p.unlink()
    code, out = invoke(scen["argv"])

masked, _ = g.normalize(out.replace(str(work), "<WORK>"))
sys.stdout.write(json.dumps({"code": code, "out": masked, "attempts": [str(a) for a in ATTEMPTS]}))
