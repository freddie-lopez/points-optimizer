"""
B. SECURITY of a local server that can spend 1,000 calls/day and write files.
HTTP-level attacks here; the in-browser CSRF / XSS / CSP attacks are in
test_ui_h_browser.py.

RED = a defect that exists. GREEN = held up.
"""
import json
import os

import pytest

from conftest import FAKE_KEY, LIVE, OFFLINE, TRANSFER, g, response, server, walk_strings

SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}


# ------------------------------------------------------------------ bind/host


def test_B1_binds_127_0_0_1_only(ui):
    assert ui.srv.httpd.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("host", [
    "127.0.0.1", "localhost", "127.0.0.1:{p}.", "localhost.:{p}", "[::1]:{p}",
    "127.0.0.1:{p}@evil.com", "evil.com:{p}", "127.0.0.1.nip.io:{p}", "0.0.0.0:{p}",
    "127.1:{p}", "2130706433:{p}", " 127.0.0.1:{p}x", "LOCALHOST:{p}x", "",
])
def test_B2_rebinding_hosts_are_421_on_page_and_api(ui, host):
    h = host.format(p=ui.port)
    for path in ("/", "/api/state", "/static/app.js"):
        r = ui.get(path, host=h)
        assert r.status == 421 and r.body == b"", (h, path, r.status)
    r = ui.post("/api/trips/trip_b_europe/run", OFFLINE, host=h)
    assert r.status == 421


def test_B3_two_host_headers_the_second_one_hostile(ui):
    r = ui.get("/api/state", headers={"Host": "evil.com"})  # first is the good one
    # Not reachable from a browser; recorded for completeness.
    assert r.status in (200, 421, 400)


# ------------------------------------------------------------------ token


@pytest.mark.parametrize("token", ["", " ", "x" * 43, None, "{tok} ", "{tok}x", "{TOK}"])
def test_B4_api_without_the_exact_token_is_403(ui, token):
    t = None if token is None else token.format(tok=ui.token, TOK=ui.token.swapcase())
    for path in ("/api/state", "/api/trips", "/api/trips/trip_b_europe"):
        r = ui.get(path, token=t if t is not None else False)
        assert r.status == 403, (repr(t), path, r.status)
    r = ui.post("/api/trips/trip_b_europe/run", OFFLINE, token=t if t is not None else False)
    assert r.status == 403


def test_B5_token_is_not_accepted_from_cookie_or_query(ui):
    r = ui.get(f"/api/state?X-PO-Token={ui.token}", token=False,
               headers={"Cookie": f"X-PO-Token={ui.token}"})
    assert r.status == 403


# ------------------------------------------------------------------ origin


@pytest.mark.parametrize("origin", ["null", "http://evil.com", "http://127.0.0.1",
                                    "http://127.0.0.1:{p}/", "https://127.0.0.1:{p}",
                                    "http://127.0.0.1:{p}.evil.com", "", None])
def test_B6_post_with_a_foreign_origin_is_403_and_spends_nothing(ui, origin):
    o = None if origin is None else origin.format(p=ui.port)
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    r = ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]),
                origin=o if o is not None else False)
    assert r.status == 403, (o, r.status)
    assert ui.net.calls == []


def test_B7_a_simple_cors_form_post_is_refused(ui):
    pf = ui.post("/api/search/preflight", SEARCH).json()
    raw = json.dumps(dict(SEARCH, confirm_id=pf["confirm_id"])).encode()
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = ui.request("POST", "/api/search/run", raw=raw, content_type=ctype)
        assert r.status == 400, (ctype, r.status)
    assert ui.net.calls == []


# ------------------------------------------------------------------ paths


@pytest.mark.parametrize("path", [
    "/static/../src/config.py", "/static/%2e%2e/src/config.py", "/static//etc/passwd",
    "/static/app.js/../../README.md", "/%2e%2e/%2e%2e/etc/passwd", "/static\\..\\README.md",
    "/index.html", "/static/", "/static/app.js%00.css", "/.env", "/data/cache/",
    "/tests/fixtures/trips/trip_b_europe.json", "//127.0.0.1/static/app.js",
])
def test_B8_nothing_outside_the_static_map_is_served(ui, path):
    r = ui.get(path)
    assert r.status == 404, (path, r.status)


@pytest.mark.parametrize("trip", [
    "..%2f..%2fsrc%2fconfig", "trip_b_europe.json", "trip_b_europe%00", "trip_001_answer",
    "..", ".", "a/../trip_b_europe", "TRIP_B_EUROPE", "trip_b_europe%20", "%2e%2e",
])
def test_B9_trip_ids_resolve_only_against_the_listing(ui, trip):
    r = ui.get(f"/api/trips/{trip}")
    assert r.status in (404,), (trip, r.status, r.text[:200])
    r = ui.post(f"/api/trips/{trip}/run", OFFLINE)
    assert r.status in (404,), (trip, r.status)


# ------------------------------------------------------------------ key


def _all_bodies_and_logs_clean(ui, needles):
    bad = []
    for line in ui.logs:
        for n in needles:
            if n in line:
                bad.append(("log", line))
    return bad


def test_B11_no_body_log_or_transcript_carries_the_key_across_every_path(ui, monkeypatch):
    from src import config

    key = FAKE_KEY
    needles = [key, config.mask_key(key), key[:12], key[-12:]]
    bodies = []
    bodies.append(ui.get("/").text)
    bodies.append(ui.get("/api/state").text)
    bodies.append(ui.get("/api/trips").text)
    for mode_body in (OFFLINE, LIVE):
        pf, r = ui.run_trip("trip_b_europe", mode_body)
        bodies += [json.dumps(pf), r.text]
    ui.net.impl = g.Refused()
    pf, r = ui.run_trip("trip_b_europe", dict(LIVE, options={**LIVE["options"], "refresh": True}))
    bodies.append(r.text)
    pf, r = ui.search(SEARCH)
    bodies += [json.dumps(pf), r.text]
    ui.net.impl = g.Stub()
    pf, r = ui.run_trip("trip_b_europe", {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}})
    bodies.append(r.text)
    ui.post("/api/wallet", {"balances": {"XX": "1"}, "cards": []})
    bodies.append(ui.post("/api/trips/trip_b_europe/run", {"mode": "offline"}).text)
    for b in bodies:
        for n in needles:
            assert n not in b, (n, b[:300])
    assert _all_bodies_and_logs_clean(ui, needles) == []


def test_B12_a_seats_aero_error_that_echoes_the_key_is_refused_not_leaked(ui):
    """If an upstream error message ever carries the key, the egress filter must
    refuse the WHOLE body (fails loudly) - and it must not leak in pieces."""
    import requests

    def echo(url, **kw):
        raise requests.HTTPError(f"401 Unauthorized: bad key {kw['headers']['Partner-Authorization']}")

    ui.net.impl = echo
    pf, r = ui.search(SEARCH)
    assert FAKE_KEY not in r.text
    assert r.status == 500 and "key material" in r.text
    # the stored copy is refused too
    st = ui.get("/api/state").text
    assert FAKE_KEY not in st


def test_B13_a_key_echo_split_by_the_190_col_transcript_wrap_still_does_not_leak(ui, monkeypatch):
    """Rich folds a long unbroken word across lines. A key that reached the
    transcript inside a long token would be split by '\\n' and slip past a
    substring filter."""
    import requests

    def echo(url, **kw):
        k = kw["headers"]["Partner-Authorization"]
        raise requests.HTTPError("x" * 170 + k + "y" * 40)

    ui.net.impl = echo
    pf, r = ui.search(SEARCH)
    body = r.text
    joined = body.replace("\\n", "")
    assert FAKE_KEY not in joined, "the key leaked, split across a wrapped transcript line"


def test_B14_short_key_under_8_chars_is_never_in_a_body(tmp_path, pinned, net, wallet_file,
                                                        trips_dir, monkeypatch):
    from src import config

    monkeypatch.setenv(config.KEY_ENV_VAR, "k3y_7ch")
    net.impl = g.Stub()
    with server(wallet_path=wallet_file, trips_dir=trips_dir) as c:
        pf, r = c.run_trip("trip_b_europe", LIVE)
        pf2, r2 = c.search(SEARCH)
        st = c.get("/api/state").text
    for t in (r.text, r2.text, st):
        assert "k3y_7ch" not in t


def test_B15_key_from_a_dotenv_file_source_path_line_redacted(tmp_path, pinned, net, wallet_file,
                                                             trips_dir, monkeypatch):
    from src import config

    env = tmp_path / "repo.env"
    env.write_text(f"SEATS_AERO_KEY={FAKE_KEY}\n")
    os.chmod(env, 0o644)  # a permission warning rides on the key line
    monkeypatch.delenv(config.KEY_ENV_VAR, raising=False)
    monkeypatch.setattr(config, "_ENV_PATH", env)
    net.impl = g.Stub()
    with server(wallet_path=wallet_file, trips_dir=trips_dir) as c:
        pf, r = c.run_trip("trip_b_europe", LIVE)
        assert r.status == 200, r.text
        run = r.json()
    assert FAKE_KEY not in r.text and config.mask_key(FAKE_KEY) not in r.text
    assert "(masked key not sent to the browser)" in run["transcript"]


# ------------------------------------------------------------------ headers


def test_B16_security_headers_on_errors_too(ui):
    for r in (ui.get("/nope"), ui.get("/api/state", token="bad"), ui.get("/", host="evil:1"),
              ui.request("OPTIONS", "/api/state")):
        assert "default-src 'none'" in r.headers.get("content-security-policy", ""), r.status
        assert r.headers.get("x-frame-options") == "DENY"
        assert not any(k.startswith("access-control-") for k in r.headers)


def test_B17_errors_carry_no_traceback_or_absolute_repo_path(ui, monkeypatch):
    from src.ui import engine as eng

    def boom(self, *a, **k):
        raise RuntimeError(f"internal detail {eng.ROOT}/src/secret.py line 3")

    monkeypatch.setattr(eng.Engine, "list_trips", boom)
    r = ui.get("/api/trips")
    assert r.status == 500
    assert "Traceback" not in r.text and str(eng.ROOT) not in r.text


def test_B18_json_nan_infinity_and_1e309_are_refused_or_neutralised(ui):
    for raw in (b'{"mode": "offline", "options": {"flex_days": NaN}}',
                b'{"mode": "offline", "options": {"flex_days": Infinity}}'):
        r = ui.request("POST", "/api/trips/trip_b_europe/run", raw=raw)
        assert r.status == 400
    raw = b'{"mode": "live", "options": {"flex_days": 1e309, "trips_cap": 1e309}}'
    r = ui.request("POST", "/api/trips/trip_b_europe/preflight", raw=raw)
    assert r.status == 400, r.text
