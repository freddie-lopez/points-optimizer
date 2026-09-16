"""
Harness for the search->trip round's probes. Same rules as ../ui-probes,
../ui-restyle-probes and ../map-search-probes:

  * RED = a defect that is present; GREEN = the attack held up.
  * NO BYTE LEAVES THIS MACHINE (the probe server refuses every connect(),
    Chromium resolves nothing but 127.0.0.1, offsite requests are aborted and
    recorded; the Python test process carries the round-1 socket canary).
  * Nothing is written into the repo tree: every trips directory is a tmp
    copy, screenshots go to the gitignored shots-out/. NO TEST HERE DELETES
    OR EDITS A COMMITTED FIXTURE; a tripwire at the end of the HTTP probes
    checks that the repository's tests/fixtures/trips/ did not move.

The restyle harness is imported for its Browser, page drivers and helpers;
this file adds the round's probe server (`st_server`), an in-process server
for the HTTP-level probes (`http_server`), and a page whose /api responses
can be rewritten and whose POST bodies are recorded (`patched_page`).
"""
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
RESTYLE = HERE.parent / "ui-restyle-probes"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("restyle_conftest", RESTYLE / "conftest.py")
_r = importlib.util.module_from_spec(_spec)
sys.modules["restyle_conftest"] = _r
_spec.loader.exec_module(_r)

isolated_environment = _r.isolated_environment
no_network_egress = _r.no_network_egress
net_canary = _r.net_canary
Browser = _r.Browser
q = _r.q
text = _r.text
box = _r.box
doc_widths = _r.doc_widths
dom_text = _r.dom_text
open_trip = _r.open_trip
PY = _r.PY

SHOTS = HERE / "shots-out"
SHOTS.mkdir(exist_ok=True)

from tests import _cli_golden as g  # noqa: E402
from tests._ui_harness import REPO_TRIPS, copy_trips, running_server, write_wallet  # noqa: E402
from src import trip_builder  # noqa: E402
from src.ui import engine as eng  # noqa: E402

TRANSFER = {"transfer_date": "2026-09-15"}
LIVE = {"mode": "live", "options": {**TRANSFER, "trips": "auto", "trips_cap": 10}}
OFFLINE = {"mode": "offline", "options": dict(TRANSFER)}


def repo_trips_snapshot():
    """Name -> (mtime_ns, size) of every entry of the REPOSITORY's fixtures
    dir, plus the directory's own mtime. Compared before/after every probe
    that deletes: the committed corpus must never move."""
    out = {"__dir__": os.stat(REPO_TRIPS).st_mtime_ns}
    for p in sorted(REPO_TRIPS.iterdir()):
        st = os.stat(p, follow_symlinks=False)
        out[p.name] = (st.st_mtime_ns, st.st_size)
    return out


REPO_BEFORE = repo_trips_snapshot()


# ------------------------------------------------------------ probe server


class Server:
    def __init__(self, scenario, hubs_file=None):
        env = dict(os.environ, PO_PROBE_ROOT=str(ROOT))
        if hubs_file is not None:
            env["PO_HUBS_FILE"] = str(hubs_file)
        self.proc = subprocess.Popen(
            [PY, str(HERE / "st_probe_server.py"), scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(ROOT), env=env,
        )
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("probe server did not start: " +
                                 (self.proc.stderr.read() or "")[-3000:])
        self.info = json.loads(line)
        self.port = self.info["port"]
        self.trips_dir = Path(self.info["trips_dir"])

    def cmd(self, t):
        self.proc.stdin.write(t + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def ls(self):
        return self.cmd("ls")

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


@contextlib.contextmanager
def st_server(scenario="ui_built", hubs=False):
    """`hubs=True` serves the map round's 12-hub synthetic hub file (SFO, MAD,
    JFK, LHR, ...) at /static/hubs.json, from tmp; data/hubs.json is never read."""
    hubs_file = None
    if hubs:
        import tempfile
        from src import map_tools
        from tests import _map_fixture
        tmp = Path(tempfile.mkdtemp(prefix="po-st-probe-hubs-"))
        hubs_file = tmp / "hubs.json"
        hubs_file.write_text(map_tools.render_document(_map_fixture.synthetic_document()), encoding="utf-8")
    srv = Server(scenario, hubs_file=hubs_file)
    _r._r1.ALLOWED_PORTS.add(srv.port)
    try:
        yield srv
    finally:
        facts = None
        try:
            facts = srv.cmd("attempts")
        except Exception:  # noqa: BLE001
            pass
        srv.close()
    if facts is not None:
        assert facts["attempts"] == [], f"a real connection was attempted: {facts['attempts']}"
    assert repo_trips_snapshot() == REPO_BEFORE, "the repository's fixtures dir moved"


# ------------------------------------------------- in-process HTTP server


def ui_built(directory, name="sfo-mad-2027-01-15", leg="SFO:MAD:2027-01-15:2400", cabin="Y"):
    """A fixture exactly as the page (and --new-trip) writes one."""
    return trip_builder.new_trip_from_flags(name, [leg], [], cabin=cabin, directory=directory,
                                            today=g.PINNED_TODAY)


@contextlib.contextmanager
def http_server(tmp_path, names=None, engine=None, **kw):
    """A real UIServer in this process on a tmp copy of the fixtures, with
    the round-1 socket canary letting only its port through."""
    from unittest.mock import patch

    trips = copy_trips(tmp_path / "trips", names=names)
    wallet = write_wallet(tmp_path / "w.json")
    os.environ[__import__("src.config", fromlist=["x"]).KEY_ENV_VAR] = g.FAKE_KEY
    with patch("src.seats_client.requests.get", side_effect=g.Stub()):
        if engine is None:
            engine = eng.Engine(trips_dir=trips, wallet_path=wallet)
        with running_server(engine=engine) as c:
            _r._r1.ALLOWED_PORTS.add(c.port)
            c.trips_dir = trips
            c.engine = engine
            yield c
    assert repo_trips_snapshot() == REPO_BEFORE, "the repository's fixtures dir moved"


@pytest.fixture
def pinned(monkeypatch):
    """The builder's clock on the goldens' day, as the probe server pins it."""
    monkeypatch.setattr(trip_builder, "date", g._PinnedDate)
    from src import config
    monkeypatch.setattr(config, "date", g._PinnedDate)


def tree(directory):
    """Every entry under a directory (recursively): path -> (lstat mtime, size, is_link)."""
    out = {}
    for root, dirs, files in os.walk(directory):
        for n in dirs + files:
            p = Path(root) / n
            st = os.lstat(p)
            out[str(p.relative_to(directory))] = (st.st_mtime_ns, st.st_size, os.path.islink(p))
    return out


@pytest.fixture(scope="session")
def browser():
    b = Browser()
    yield b
    b.close()


# --------------------------------------------------------- page with patches


@contextlib.contextmanager
def patched_page(browser, port, width=1440, height=1000, patches=None):
    """A page on the probe server whose /api responses can be rewritten in
    the browser (`patches`: {url suffix: fn(json, request) -> json}) and whose
    every POST body to /api/* is recorded on pg.facts["posts"]."""
    ctx = browser.browser.new_context(viewport={"width": width, "height": height})
    offsite, errors, console, posts = [], [], [], []

    def route_all(r):
        u = r.request.url
        if u.startswith("http://127.0.0.1:") or u.startswith("data:") or u == "about:blank":
            if r.request.method == "POST" and "/api/" in u:
                try:
                    posts.append({"url": u.split(f":{port}", 1)[1], "body": json.loads(r.request.post_data or "null")})
                except ValueError:
                    posts.append({"url": u, "body": r.request.post_data})
            for suffix, fn in (patches or {}).items():
                if u.endswith(suffix):
                    resp = r.fetch()
                    body = resp.json()
                    return r.fulfill(status=resp.status, headers=dict(resp.headers),
                                     body=json.dumps(fn(body, r.request)))
            return r.continue_()
        offsite.append(u)
        return r.abort()

    ctx.route("**/*", route_all)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    pg.facts = {"offsite": offsite, "errors": errors, "console": console, "posts": posts}
    pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    pg.wait_for_selector('[data-testid="wordmark"]', timeout=20000)
    pg.wait_for_timeout(300)
    try:
        yield pg
    finally:
        ctx.close()


# ------------------------------------------------------------ page drivers


def run_search(pg, origin="SFO", destination="MAD", date="2027-01-15"):
    pg.click(q("tab-search"))
    pg.wait_for_timeout(150)
    pg.fill(q("search-from"), origin)
    pg.fill(q("search-to"), destination)
    pg.fill(q("search-date"), date)
    pg.click(q("search-run"))
    pg.wait_for_timeout(400)
    if pg.query_selector(q("search-confirm-go")):
        pg.click(q("search-confirm-go"))
    pg.wait_for_selector(q("search-table") + "," + q("search-state"), timeout=60000)
    pg.wait_for_function("""() => !document.querySelector('[data-testid="search-run"]').disabled""",
                         timeout=60000)
    pg.wait_for_timeout(300)


def cells(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('td.cab.pick')).map((td) => ({
        id: td.getAttribute('data-testid'), sel: td.classList.contains('sel'), text: td.innerText }))""")


def pick_first_cell(pg):
    ids = [c["id"] for c in cells(pg)]
    assert ids, "no award cell to pick"
    pg.locator("td.cab.pick").nth(0).click()
    pg.wait_for_timeout(250)
    return ids[0]


def pick_cell(pg, suffix):
    """Click the first award cell whose testid ends with `suffix` (the testid
    itself may carry a hostile source code, so never as a CSS selector)."""
    ids = [c["id"] for c in cells(pg)]
    i = [n for n, x in enumerate(ids) if x.endswith(suffix)]
    assert i, (suffix, ids)
    pg.locator("td.cab.pick").nth(i[0]).click()
    pg.wait_for_timeout(250)
    return ids[i[0]]


def add_box(pg):
    return {"note": text(pg, q("search-add-trip-note")),
            "disabled": pg.evaluate("""() => { const b = document.querySelector('[data-testid="search-add-trip"]');
                return b ? b.disabled : null; }"""),
            "present": pg.query_selector(q("search-add-trip-box")) is not None}


def nt_values(pg):
    return pg.evaluate("""() => {
        const v = (t) => { const n = document.querySelector('[data-testid="' + t + '"]'); return n ? n.value : null; };
        return { name: v('nt-name'), cabin: v('nt-cabin'), origin: v('nt-leg-1-origin'), destination: v('nt-leg-1-destination'),
                 date: v('nt-leg-1-date'), legcabin: v('nt-leg-1-cabin'), cash: v('nt-leg-1-cash'),
                 legs: document.querySelectorAll('[data-testid^="nt-leg-"][data-testid$="-origin"]').length,
                 prefill: (() => { const n = document.querySelector('[data-testid="nt-prefill"]'); return n ? n.innerText : null; })(),
                 hint: (() => { const n = document.querySelector('[data-testid="nt-leg-1-cash"]');
                    const h = n && n.parentElement.querySelector('.hint'); return h ? h.textContent : null; })(),
                 focused: document.activeElement ? document.activeElement.getAttribute('data-testid') : null,
                 legcabinLabel: (() => { const s = document.querySelector('[data-testid="nt-leg-1-cabin"]');
                    return s ? s.options[s.selectedIndex].textContent : null; })() }; }""")


def preview(pg):
    pg.click(q("nt-preview"))
    pg.wait_for_function("""() => document.querySelector('[data-testid="nt-echo"]') ||
        document.querySelector('[data-testid^="nt-error-"]') ||
        !document.querySelector('[data-testid="banner-error"]').hidden""", timeout=15000)
    pg.wait_for_timeout(150)


def nt_errors(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('[data-testid^="nt-error-"]'))
        .map((n) => [n.getAttribute('data-testid'), n.textContent])""")


def write_trip(pg):
    pg.click(q("nt-write"))
    pg.wait_for_function("""() => document.querySelector('[data-testid="nt-wrote"]') ||
        document.querySelector('[data-testid="trip-detail"]') ||
        !document.querySelector('[data-testid="banner-error"]').hidden ||
        document.querySelector('[data-testid^="nt-error-"]')""", timeout=15000)
    pg.wait_for_timeout(300)


def go_trip(pg, trip_id):
    pg.click(q("tab-trips"))
    pg.wait_for_timeout(150)
    pg.click(q("trip-row-" + trip_id))
    pg.wait_for_selector(q("trip-detail") + "," + q("trip-error"), timeout=15000)
    pg.wait_for_timeout(200)


def delete_state(pg):
    return pg.evaluate("""() => { const b = document.querySelector('[data-testid="trip-delete"]');
        const r = document.querySelector('[data-testid="trip-delete-reason"]');
        return { present: !!b, disabled: b ? b.disabled : null, cls: b ? b.className : null,
                 reason: r ? r.innerText : null,
                 bg: b ? getComputedStyle(b).backgroundColor : null,
                 color: b ? getComputedStyle(b).color : null }; }""")


def wait_delete_settled(pg, trip_name, timeout=25000):
    """Wait until `trip-delete` is present AND has stopped moving.

    A bare `wait_for_selector(q("trip-delete"))` after clicking a trip row is
    a PROBE RACE, not a product defect: `hashchange` is delivered
    asynchronously, so at the moment the click returns the page is still
    showing the PREVIOUS trip's detail - including its `trip-delete`. The
    selector matches that stale button, and a moment later `onHash` sets
    `S.trip = null`, renders `Loading…` and the button is gone; the next read
    then sees `present: false, disabled: None`. (This is what made F4a fail
    about 3 runs in 10, with and without the F5 fix alike.)

    The settle is three things at once: the detail on screen is the one that
    was asked for (its `h1` is `trip_name`), `trip-delete` exists, and its
    `disabled` is the same across two animation frames - so the value the
    caller then asserts is the button's final state, not a frame of a render
    still in flight.
    """
    pg.evaluate("() => { window.__settle = null; }")
    pg.wait_for_function(
        # Synchronous on purpose: wait_for_function polls on requestAnimationFrame
        # and reads a returned Promise as a truthy value, so a promise-based
        # settle resolves on the first poll and is no better than the bare
        # selector. The counter below IS the settle: three consecutive frames
        # with the asked-for trip's own detail on screen and `trip-delete` at
        # the same `disabled` value.
        """([name]) => {
            const d = document.querySelector('[data-testid="trip-detail"]');
            const h = d && d.querySelector('h1');
            const b = document.querySelector('[data-testid="trip-delete"]');
            const key = (h && h.textContent === name && b) ? (name + '|' + b.disabled) : null;
            if (key === null) { window.__settle = null; return false; }
            if (window.__settle && window.__settle.key === key) { window.__settle.n += 1; }
            else { window.__settle = { key: key, n: 1 }; }
            return window.__settle.n >= 3;
        }""",
        arg=[trip_name], timeout=timeout)


def dialog(pg):
    return pg.evaluate("""() => { const d = document.querySelector('[data-testid="delete-confirm"]');
        if (!d) return null;
        return { text: d.innerText, heading: d.querySelector('h3').textContent,
                 label: d.querySelector('.label').textContent,
                 lines: Array.from(d.querySelectorAll('p')).map((p) => p.textContent),
                 go: d.querySelector('[data-testid="delete-confirm-go"]').textContent,
                 goCls: d.querySelector('[data-testid="delete-confirm-go"]').className,
                 cancel: d.querySelector('[data-testid="confirm-cancel"]').textContent,
                 focused: document.activeElement.getAttribute('data-testid'),
                 role: d.getAttribute('role'), modal: d.getAttribute('aria-modal'),
                 w: d.getBoundingClientRect().width, right: d.getBoundingClientRect().right }; }""")


def trip_ids(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('[data-testid^="trip-row-"]'))
        .map((n) => n.getAttribute('data-testid').replace('trip-row-', ''))""")
