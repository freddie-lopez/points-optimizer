"""
A real UI server on an ephemeral port, for tests. `http.client` only: conftest
points the proxy variables at a dead proxy, which `requests`/urllib would honour.

The engine's trips directory and wallet are injectable, so a test that writes a
trip writes it under tmp - never into tests/fixtures/trips/.
"""
from __future__ import annotations

import http.client
import json
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

from src.ui.api import route
from src.ui.engine import Engine
from src.ui.server import UIServer

ROOT = Path(__file__).resolve().parent.parent
REPO_TRIPS = ROOT / "tests" / "fixtures" / "trips"
WALLETS = ROOT / "tests" / "fixtures" / "wallets"


def copy_trips(dest: Path, names=None) -> Path:
    """A private trips directory holding copies of the committed fixtures."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted(REPO_TRIPS.glob("*.json")):
        if names is None or p.name in names:
            shutil.copy(p, dest / p.name)
    return dest


def write_wallet(path: Path, balances=None, cards=None) -> Path:
    path.write_text(json.dumps({
        "balances": {"UR": 160000} if balances is None else balances,
        "cards": ["Chase Sapphire Preferred"] if cards is None else cards,
    }))
    return path


@contextmanager
def running_server(engine: Optional[Engine] = None, **engine_kw):
    engine = engine or Engine(**engine_kw)
    logs = []
    srv = UIServer(engine, route, port=0, log=logs.append)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02},
                              daemon=True)
    thread.start()
    try:
        yield Client(srv, logs)
    finally:
        srv.shutdown()


class Response:
    def __init__(self, status: int, headers: Dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self):
        return json.loads(self.text)


class Client:
    def __init__(self, srv: UIServer, logs):
        self.srv = srv
        self.logs = logs
        self.port = srv.port
        self.token = srv.token
        self.origin = f"http://127.0.0.1:{srv.port}"

    def request(self, method: str, path: str, body=None, *, token=True, origin=True,
                host: Optional[str] = "default", headers=None, raw: Optional[bytes] = None,
                content_type="application/json") -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
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
            return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()

    def get(self, path, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw) -> Response:
        return self.request("POST", path, body if body is not None else {}, **kw)
