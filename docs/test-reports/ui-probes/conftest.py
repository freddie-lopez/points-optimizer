"""Shared harness for the UI Tester probes (round 1).

These live OUTSIDE `testpaths` on purpose: each test asserts the CORRECT
behaviour, so a RED test here is a defect that is still present and a GREEN
test is an attack that held up. Run with:

    .venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/ui-probes

NO TEST HERE MAKES A NETWORK CALL.
  * `requests.get` is replaced for the whole test by a switchable stub (`net`),
    routed by URL exactly as the goldens' Stub is.
  * A socket CANARY is armed in every test: any connect() to anything that is
    not the loopback port of a server this test started is recorded, refused,
    and fails the test. So "no network" is proved here, not assumed.
  * conftest's own guards (no key, tmp HOME, tmp cache/snapshots, dead proxy)
    are imported and active.

Nothing is written into the repo tree: trips go to tmp copies, the wallet is a
tmp file, the cache and the snapshot corpus are conftest's tmp directories.

Uses only pytest features present in 7.4.0 (fixtures, monkeypatch, tmp_path,
parametrize).
"""
import contextlib
import copy
import http.client
import json
import socket
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
HERE = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.conftest import isolated_environment, no_network_egress  # noqa: E402,F401
from tests import _cli_golden as g  # noqa: E402
from tests import _trips_payloads as tp  # noqa: E402
from tests._ui_harness import copy_trips, write_wallet  # noqa: E402,F401

TRANSFER = {"transfer_date": "2026-09-15"}
LIVE = {"mode": "live", "options": {**TRANSFER, "trips": "auto", "trips_cap": 10}}
OFFLINE = {"mode": "offline", "options": dict(TRANSFER)}
CSP = "Chase Sapphire Preferred"
FAKE_KEY = g.FAKE_KEY
REAL = g.REAL

# ---------------------------------------------------------------------------
# The canary
# ---------------------------------------------------------------------------

ALLOWED_PORTS = set()


class Canary:
    def __init__(self):
        self.attempts = []


@pytest.fixture(autouse=True)
def net_canary(monkeypatch):
    """Refuse and record every connect() that is not to a loopback port a probe
    server of THIS test is listening on."""
    canary = Canary()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_cc = socket.create_connection

    def _ok(address):
        try:
            host, port = address[0], address[1]
        except (TypeError, IndexError):
            return False
        return host in ("127.0.0.1", "localhost") and port in ALLOWED_PORTS

    def connect(self, address, *a, **k):
        if _ok(address):
            return real_connect(self, address, *a, **k)
        canary.attempts.append(address)
        raise OSError(f"PROBE CANARY: a real connection to {address!r} was attempted")

    def connect_ex(self, address, *a, **k):
        if _ok(address):
            return real_connect_ex(self, address, *a, **k)
        canary.attempts.append(address)
        raise OSError(f"PROBE CANARY: connect_ex({address!r})")

    def create_connection(address, *a, **k):
        if _ok(address):
            return real_cc(address, *a, **k)
        canary.attempts.append(address)
        raise OSError(f"PROBE CANARY: create_connection({address!r})")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield canary
    ALLOWED_PORTS.clear()
    assert canary.attempts == [], f"a real network connection was attempted: {canary.attempts}"


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Net:
    """requests.get for the whole test. `impl` is swapped by the test; every
    call is recorded in `calls` whatever impl answered it."""

    def __init__(self):
        self.impl = None
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, dict(kwargs.get("params") or {}),
                           dict(kwargs.get("headers") or {})))
        if self.impl is None:
            raise AssertionError(f"PROBE: a transport call with no transport armed: {url}")
        return self.impl(url, **kwargs)

    @property
    def search_calls(self):
        return sum(1 for c in self.calls if c[0].endswith("/search"))

    @property
    def trips_calls(self):
        return sum(1 for c in self.calls if "/trips/" in c[0])


def response(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


@pytest.fixture
def net():
    n = Net()
    with patch("src.seats_client.requests.get", side_effect=n):
        yield n


# ---------------------------------------------------------------------------
# Pinned clock (the goldens'), fresh budget
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned(monkeypatch):
    from src import config, response_cache, seats_client, seats_trips, trip_builder

    seats_client.SeatsClient.CACHE.clear()
    seats_client.SeatsClient.CACHE_META.clear()
    seats_client.SeatsClient.reset_call_budget()
    monkeypatch.setattr(trip_builder, "date", g._PinnedDate)
    monkeypatch.setattr(config, "date", g._PinnedDate)
    monkeypatch.setattr(response_cache, "_utcnow", lambda: g.PINNED_NOW)
    monkeypatch.setattr(seats_client, "datetime", g._PinnedDatetime)
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "")
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "unverified")
    yield
    seats_client.SeatsClient.CACHE.clear()
    seats_client.SeatsClient.CACHE_META.clear()
    seats_client.SeatsClient.reset_call_budget()


# ---------------------------------------------------------------------------
# A real server on an ephemeral port
# ---------------------------------------------------------------------------


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class Client:
    def __init__(self, srv, logs, engine):
        self.srv, self.logs, self.engine = srv, logs, engine
        self.port, self.token = srv.port, srv.token
        self.origin = f"http://127.0.0.1:{srv.port}"

    def request(self, method, path, body=None, *, token=True, origin=True, host="default",
                headers=None, raw=None, content_type="application/json", timeout=120):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host == "default":
                conn.putheader("Host", f"127.0.0.1:{self.port}")
            elif host is not None:
                conn.putheader("Host", host)
            if token is True:
                conn.putheader("X-PO-Token", self.token)
            elif isinstance(token, str):
                conn.putheader("X-PO-Token", token)
            if method == "POST":
                if origin is True:
                    conn.putheader("Origin", self.origin)
                elif isinstance(origin, str):
                    conn.putheader("Origin", origin)
            data = raw if raw is not None else (
                json.dumps(body).encode() if body is not None else None)
            if data is not None:
                if content_type:
                    conn.putheader("Content-Type", content_type)
                conn.putheader("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.endheaders()
            if data is not None:
                conn.send(data)
            r = conn.getresponse()
            return Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    # conveniences ---------------------------------------------------------

    def run_trip(self, trip, body):
        """preflight (+confirm when LIVE) then run. Returns (preflight, run Resp)."""
        pf = self.post(f"/api/trips/{trip}/preflight", body)
        assert pf.status == 200, pf.text
        pfj = pf.json()
        b = dict(body)
        if pfj.get("confirm_id"):
            b["confirm_id"] = pfj["confirm_id"]
        return pfj, self.post(f"/api/trips/{trip}/run", b)

    def search(self, body):
        pf = self.post("/api/search/preflight", body)
        if pf.status != 200:
            return pf.json(), pf
        pfj = pf.json()
        b = dict(body, confirm_id=pfj.get("confirm_id"))
        return pfj, self.post("/api/search/run", b)


@contextlib.contextmanager
def server(engine=None, **engine_kw):
    from src.ui.api import route
    from src.ui.engine import Engine
    from src.ui.server import UIServer

    engine = engine or Engine(**engine_kw)
    logs = []
    srv = UIServer(engine, route, port=0, log=logs.append)
    ALLOWED_PORTS.add(srv.port)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        yield Client(srv, logs, engine)
    finally:
        srv.shutdown()


@pytest.fixture
def trips_dir(tmp_path):
    return copy_trips(tmp_path / "trips")


@pytest.fixture
def wallet_file(tmp_path):
    return write_wallet(tmp_path / "wallet.json")


@pytest.fixture
def ui(tmp_path, pinned, net, trips_dir, wallet_file, monkeypatch):
    """A server with a wallet, a private trips dir, the key set and `net` armed
    with the goldens' Stub. `ui.net` is the transport."""
    from src import config

    monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    net.impl = g.Stub()
    with server(wallet_path=wallet_file, trips_dir=trips_dir) as c:
        c.net = net
        c.trips_dir = trips_dir
        c.wallet_file = wallet_file
        yield c


# ---------------------------------------------------------------------------
# Data builders
# ---------------------------------------------------------------------------


def vs_row(**kw):
    return g.vs_row()


def aeroplan_row(origin, dest, iso, rid=None):
    r = copy.deepcopy(REAL["data"][0])
    r["Route"]["OriginAirport"], r["Route"]["DestinationAirport"] = origin, dest
    r["Date"], r["ParsedDate"] = iso, f"{iso}T00:00:00Z"
    r["ID"] = rid or (f"row{origin}{dest}" + "0" * 27)[:27]
    return r


def flat(text):
    import re

    return re.sub(r"\s+", " ", text or "")


def walk_strings(obj, skip_keys=()):
    """Every string in a JSON value, except under the given keys."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            yield from walk_strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v, skip_keys)
