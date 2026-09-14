"""
The local UI's gates (docs/plans/ui.md 4.4), against a REAL server on an
ephemeral port, spoken to with http.client.

Threat model: another website open in the same browser, and DNS rebinding.
"""
import os
import re
import socket
import subprocess
import sys
import time

import pytest

from src import config
from src.ui.server import UIServer
from src.ui.api import route
from src.ui.engine import Engine
from tests._ui_harness import ROOT, running_server, write_wallet

FAKE_KEY = "sec_fake_key_ABCDEFGHIJ"


@pytest.fixture
def client(tmp_path):
    with running_server(wallet_path=write_wallet(tmp_path / "wallet.json")) as c:
        yield c


# ---------------------------------------------------------------- bind / host


def test_it_binds_loopback_only(client):
    assert client.srv.httpd.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", ""])
def test_any_other_bind_address_is_refused(host):
    with pytest.raises(ValueError):
        UIServer(Engine(), route, port=0, host=host)


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/state"])
@pytest.mark.parametrize("host", ["evil.com", "127.0.0.1.evil.com", "localhost",
                                  "127.0.0.1", "evil.com:{port}", None])
def test_a_wrong_or_missing_host_is_421_with_an_empty_body(client, path, host):
    if host is not None:
        host = host.format(port=client.port)
    r = client.get(path, host=host)
    assert r.status == 421
    assert r.body == b""


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_the_two_loopback_hosts_are_accepted(client, host):
    r = client.get("/api/state", host=host.format(port=client.port))
    assert r.status == 200


# ---------------------------------------------------------------------- token


def test_the_token_is_only_in_the_page(client):
    page = client.get("/", token=False)
    assert page.status == 200
    assert f'<meta name="po-token" content="{client.token}">' in page.text
    for path in ("/static/app.js", "/static/app.css", "/static/map.js", "/static/land.json",
                 "/static/hubs.json"):
        assert client.token not in client.get(path, token=False).text


@pytest.mark.parametrize("token", [False, "wrong", ""])
def test_every_api_get_needs_the_token(client, token):
    r = client.get("/api/state", token=token)
    assert r.status == 403
    assert r.json() == {
        "error": "stale_page",
        "message": "This page is from an earlier launch of the app. Reload it.",
    }


def test_a_token_in_the_query_string_does_not_count(client):
    r = client.get(f"/api/state?token={client.token}&X-PO-Token={client.token}", token=False)
    assert r.status == 403


def test_a_post_needs_the_token_too(client):
    assert client.post("/api/wallet", {"balances": {}}, token=False).status == 403


def test_each_launch_has_its_own_token(tmp_path):
    with running_server() as a, running_server() as b:
        assert a.token != b.token
        assert a.get("/api/state", token=b.token).status == 403


# --------------------------------------------------------------------- origin


@pytest.mark.parametrize("origin", [False, "http://evil.com", "null",
                                    "http://127.0.0.1", "https://127.0.0.1:{port}",
                                    "http://127.0.0.1.evil.com:{port}"])
def test_a_post_with_a_foreign_or_missing_origin_is_403(client, origin):
    if isinstance(origin, str):
        origin = origin.format(port=client.port)
    r = client.post("/api/wallet", {"balances": {"UR": "1"}, "cards": []}, origin=origin)
    assert r.status == 403
    assert r.json()["error"] == "forbidden_origin"


def test_localhost_origin_is_accepted(client):
    r = client.post("/api/wallet", {"balances": {"UR": "160000"},
                                    "cards": ["Chase Sapphire Preferred"]},
                    origin=f"http://localhost:{client.port}",
                    host=f"localhost:{client.port}")
    assert r.status == 200


# ------------------------------------------------------------- method / shape


@pytest.mark.parametrize("method", ["OPTIONS", "PUT", "DELETE", "PATCH", "HEAD"])
def test_other_methods_are_405(client, method):
    r = client.request(method, "/api/state")
    assert r.status == 405


def test_no_cors_header_is_ever_sent(client):
    responses = [
        client.get("/"), client.get("/static/app.js"), client.get("/api/state"),
        client.get("/api/state", token=False), client.request("OPTIONS", "/api/state",
        headers={"Origin": "http://evil.com", "Access-Control-Request-Method": "POST"}),
        client.post("/api/wallet", {"balances": {}}, origin="http://evil.com"),
    ]
    for r in responses:
        assert not [h for h in r.headers if h.startswith("access-control-")], r.headers


def test_a_body_over_64kb_is_413(client):
    big = b'{"x": "' + b"a" * (65 * 1024) + b'"}'
    r = client.post("/api/wallet", raw=big)
    assert r.status == 413


@pytest.mark.parametrize("raw,ctype", [
    (b"not json", "application/json"),
    (b"[1, 2]", "application/json"),
    (b'{"a": NaN}', "application/json"),
    (b'{"balances": {}}', "text/plain"),
    (b'{"balances": {}}', ""),
    (b"\xff\xfe", "application/json"),
])
def test_a_body_that_is_not_one_json_object_is_400(client, raw, ctype):
    r = client.post("/api/wallet", raw=raw, content_type=ctype)
    assert r.status == 400


# ------------------------------------------------------------------ static map


@pytest.mark.parametrize("path", [
    "/static/../src/config.py", "/static/%2e%2e/src/config.py", "/%2e%2e/src/main.py",
    "/static/app.js/..", "/index.html", "/static/", "/src/config.py", "/.env",
    "/static/app.js%00", "//etc/passwd",
])
def test_nothing_outside_the_fixed_static_map_is_served(client, path):
    r = client.get(path)
    assert r.status == 404
    assert b"SEATS_AERO" not in r.body and b"import" not in r.body


def test_unknown_api_paths_are_404_and_a_trip_id_is_never_a_path(client):
    for path in ("/api/nope", "/api/trips/..%2f..%2fsrc", "/api/trips/../../etc",
                 "/api/runs/../x"):
        assert client.get(path).status == 404


# -------------------------------------------------------------------- headers


def test_the_security_headers_are_on_every_response(client):
    for r in (client.get("/"), client.get("/static/app.css"), client.get("/api/state"),
              client.get("/static/map.js"), client.get("/static/land.json"),
              client.get("/static/hubs.json"),
              client.get("/nope"), client.get("/api/state", token=False)):
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp and "form-action 'none'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"


def test_api_responses_are_not_cached(client):
    assert client.get("/api/state").headers["cache-control"] == "no-store"


def test_the_page_has_no_inline_script(client):
    page = client.get("/").text
    for tag in re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.S):
        assert tag.strip() == ""
    assert re.findall(r"<script\b[^>]*\bsrc=\"/static/app.js\"", page)
    # No inline event handler attribute anywhere (onclick=, onload=, ...).
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", page, re.I)


# ---------------------------------------------------------------------- state


def test_state_names_the_key_source_and_never_the_key(client, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    r = client.get("/api/state")
    assert r.status == 200
    body = r.json()
    assert body["key"] == {"found": True, "source": "environment", "path": None,
                           "error_text": None}
    assert FAKE_KEY not in r.text and config.mask_key(FAKE_KEY) not in r.text


def test_state_without_a_key_carries_the_resolution_error_verbatim(client):
    body = client.get("/api/state").json()
    assert body["key"]["found"] is False
    assert body["key"]["error_text"].startswith("No Seats.aero API key found.")
    assert body["modes"]["live"] is False
    assert body["modes"]["live_reason"] == "LIVE needs a Seats.aero key."


def test_the_request_log_is_method_path_status_only(client):
    client.get(f"/api/state?secret={client.token}")
    client.get("/api/state", token=False)
    assert "GET /api/state 200" in client.logs
    assert "GET /api/state 403" in client.logs
    assert all(client.token not in line and "?" not in line for line in client.logs)


# --------------------------------------------------------------------- egress


def test_a_body_containing_the_key_is_never_sent(client, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.setattr(client.srv.engine, "state", lambda: {"leak": FAKE_KEY})
    r = client.get("/api/state")
    assert r.status == 500
    assert r.json() == {"error": "internal",
                        "message": "Refused to send a response that contained key material."}
    assert FAKE_KEY not in r.text
    assert any("key material" in line for line in client.logs)
    assert not any(FAKE_KEY in line or config.mask_key(FAKE_KEY) in line
                   for line in client.logs)


def test_a_body_containing_the_mask_is_never_sent(client, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.setattr(client.srv.engine, "state",
                        lambda: {"leak": config.mask_key(FAKE_KEY)})
    assert client.get("/api/state").status == 500


def test_an_unexpected_exception_is_a_generic_500_and_the_traceback_stays_local(
    client, monkeypatch, capsys
):
    def boom():
        raise KeyError("secret detail")

    monkeypatch.setattr(client.srv.engine, "state", boom)
    r = client.get("/api/state")
    assert r.status == 500
    assert r.json() == {"error": "internal", "message": "Unexpected KeyError; see the terminal."}
    assert "secret detail" not in r.text


# --------------------------------------------------------------------- launch


def _launch(*args):
    return subprocess.Popen(
        [sys.executable, "-m", "src.ui", "--no-open", *args],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_the_launch_command_prints_the_url_and_serves(tmp_path):
    proc = _launch("--port", "0")
    try:
        line = proc.stdout.readline()
        m = re.fullmatch(r"Points optimizer UI: http://127\.0\.0\.1:(\d+)/  \(Ctrl-C to stop\)\n", line)
        assert m, line
        port = int(m.group(1))
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        assert conn.getresponse().status == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_a_busy_port_fails_loudly_and_never_moves():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        proc = _launch("--port", str(port))
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 1
        assert f"Port {port} on 127.0.0.1 is not available" in err
        assert "Points optimizer UI:" not in out
    finally:
        sock.close()


# ------------------------------------------------ the key, on every kind of run


def test_no_body_log_or_transcript_ever_carries_the_key_or_its_mask(tmp_path, monkeypatch):
    """LIVE, REPLAY, OFFLINE and refusals, with a fake key set: the key and its
    mask appear in no response body and no log line. The transcript's key line
    is replaced, and the source stays."""
    from unittest.mock import patch

    from tests import _cli_golden as g
    from tests._ui_harness import copy_trips

    monkeypatch.setenv(config.KEY_ENV_VAR, FAKE_KEY)
    mask = config.mask_key(FAKE_KEY)
    live = {"mode": "live", "options": {"transfer_date": "2026-09-15"}}
    bodies = []
    with patch("src.seats_client.requests.get", side_effect=g.Stub()):
        with running_server(wallet_path=write_wallet(tmp_path / "w.json"),
                            trips_dir=copy_trips(tmp_path / "t", ["trip_b_europe.json"])) as c:
            pf = c.post("/api/trips/trip_b_europe/preflight", live)
            bodies.append(pf)
            run = c.post("/api/trips/trip_b_europe/run", dict(live, confirm_id=pf.json()["confirm_id"]))
            bodies.append(run)
            assert run.status == 200
            assert ("Seats.aero key: (masked key not sent to the browser)   (source: environment)"
                    in run.json()["transcript"])
            for body in ({"mode": "replay", "options": {"manifest_id": 0}},
                         {"mode": "offline", "options": {}},
                         {"mode": "live", "options": {"trips": "all"}, "confirm_id": "x"}):
                bodies.append(c.post("/api/trips/trip_b_europe/run", body))
            search = {"origin": "SFO", "destination": "MAD", "date": "2099-01-15"}
            spf = c.post("/api/search/preflight", search)
            bodies.append(spf)
            bodies.append(c.post("/api/search/run", dict(search, confirm_id=spf.json()["confirm_id"])))
            bodies.append(c.post("/api/search/run", dict(search, confirm_id="forged")))
            bodies.append(c.post("/api/wallet", {"balances": {"XX": "1"}, "cards": []}))
            bodies.append(c.get("/api/state"))
            bodies.append(c.get("/api/trips"))
            logs = list(c.logs)
    for r in bodies:
        assert FAKE_KEY not in r.text and mask not in r.text, r.text[:300]
    assert not any(FAKE_KEY in line or mask in line for line in logs)
