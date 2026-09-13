"""
Harness for the restyle round's browser probes. Same rules as ../ui-probes:

  * RED = a defect that is present; GREEN = the attack held up.
  * NO BYTE LEAVES THIS MACHINE. The probe server refuses every connect();
    Chromium is launched with no proxy, every name but 127.0.0.1 unresolvable,
    and a route interceptor that aborts and RECORDS anything offsite. The
    Python test process itself carries the round-1 socket canary.
  * Nothing is written into the repo tree: trips, wallet, cache, snapshots are
    tmp copies made by the probe server.

Drives the page with Playwright's Python API (the same Chromium build the
round-1 drive.js uses) so the probes can call getComputedStyle, intercept
/api/state, block fonts, and screenshot in grayscale.
"""
import contextlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
UI_PROBES = HERE.parent / "ui-probes"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Round-1 guards and the socket canary, re-exported under their own names.
_spec = importlib.util.spec_from_file_location("ui_probes_conftest", UI_PROBES / "conftest.py")
_r1 = importlib.util.module_from_spec(_spec)
sys.modules["ui_probes_conftest"] = _r1
_spec.loader.exec_module(_r1)
isolated_environment = _r1.isolated_environment
no_network_egress = _r1.no_network_egress
net_canary = _r1.net_canary

PY = str(ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
# Screenshots go to a gitignored scratch dir so a run never dirties the tree;
# the committed `shots/` is the record cited by the report.
SHOTS = HERE / "shots-out"
SHOTS.mkdir(exist_ok=True)

CHROME_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    "--disable-background-networking", "--disable-component-update",
    "--disable-sync", "--no-first-run", "--no-default-browser-check",
    "--disable-default-apps", "--metrics-recording-only", "--no-pings",
    "--disable-features=OptimizationHints,MediaRouter,Translate",
    "--no-proxy-server",
]
CHROME_ENV = {"HTTPS_PROXY": "", "HTTP_PROXY": "", "https_proxy": "", "http_proxy": "",
              "NO_PROXY": "*", "no_proxy": "*"}


BASE_ROOT = None  # set by a test to run the server against the base-commit worktree


class Server:
    def __init__(self, scenario, root=None):
        import os

        root = root or BASE_ROOT or ROOT
        env = dict(os.environ, PO_PROBE_ROOT=str(root))
        self.proc = subprocess.Popen(
            [PY, str(HERE / "restyle_probe_server.py"), scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(root), env=env,
        )
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("probe server did not start: " +
                                 (self.proc.stderr.read() or "")[-3000:])
        self.info = json.loads(line)
        self.port = self.info["port"]
        self.base = f"http://127.0.0.1:{self.port}/"

    def cmd(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def attempts(self):
        return self.cmd("attempts")

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.err = self.proc.stderr.read()


@contextlib.contextmanager
def probe_server(scenario, root=None):
    srv = Server(scenario, root)
    try:
        yield srv
    finally:
        facts = None
        try:
            facts = srv.attempts()
        except Exception:  # noqa: BLE001
            pass
        srv.close()
    if facts is not None:
        assert facts["attempts"] == [], f"a real connection was attempted: {facts['attempts']}"


class Browser:
    """One Chromium for the whole session; a fresh context per page()."""

    def __init__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        import os

        env = dict(os.environ)
        env.update(CHROME_ENV)
        self.browser = self._pw.chromium.launch(executable_path=CHROME, args=CHROME_ARGS, env=env)

    @contextlib.contextmanager
    def page(self, port, width=1440, height=1000, state_patch=None, block_fonts=True,
             reduced_motion=None):
        """A page on the probe server. `state_patch(json) -> json` rewrites
        /api/state in the browser (the only way to give the page a state the
        engine cannot produce here, e.g. a path with no slashes)."""
        ctx = self.browser.new_context(viewport={"width": width, "height": height},
                                       reduced_motion=reduced_motion)
        offsite = []
        errors = []
        console = []

        def route_all(r):
            u = r.request.url
            if u.startswith("http://127.0.0.1:") or u.startswith("data:") or u == "about:blank":
                if state_patch and u.endswith("/api/state") and r.request.method == "GET":
                    resp = r.fetch()
                    body = resp.json()
                    return r.fulfill(status=resp.status, headers=dict(resp.headers),
                                     body=json.dumps(state_patch(body)))
                return r.continue_()
            offsite.append(u)
            return r.abort()

        ctx.route("**/*", route_all)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
        pg.facts = {"offsite": offsite, "errors": errors, "console": console}
        pg.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        pg.wait_for_selector('[data-testid="wordmark"]', timeout=20000)
        pg.wait_for_timeout(300)
        try:
            yield pg
        finally:
            ctx.close()

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()


@pytest.fixture(scope="session")
def browser():
    b = Browser()
    yield b
    b.close()


# ------------------------------------------------------------ page drivers


def q(t):
    return f'[data-testid="{t}"]'


def open_trip(pg, trip, mode="offline", run=True, options=False):
    pg.click(q("tab-trips"))
    pg.wait_for_timeout(150)
    pg.click(q("trip-row-" + trip))
    pg.wait_for_selector(q("trip-detail"), timeout=15000)
    if options:
        pg.click(q("run-options"))
    pg.click(q("mode-" + mode))
    pg.wait_for_timeout(150)
    if not run:
        return
    pg.click(q("run-go"))
    pg.wait_for_timeout(400)
    if pg.query_selector(q("run-confirm-go")):
        pg.click(q("run-confirm-go"))
    pg.wait_for_function("""() => {
      const r = document.querySelector('[data-testid="trip-result"]');
      const b = document.querySelector('[data-testid="banner-error"]');
      return !!r || (b && !b.hidden);
    }""", timeout=120000)
    pg.wait_for_timeout(500)


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
    pg.wait_for_timeout(400)


def text(pg, sel):
    return pg.evaluate("""(s) => { const n = document.querySelector(s);
        return (!n || n.hidden) ? null : n.innerText; }""", sel)


def transcript(pg):
    return pg.evaluate("""() => { const t = document.querySelector('[data-testid="transcript"] pre');
        return t ? t.textContent : null; }""")


def doc_widths(pg):
    return pg.evaluate("""() => ({ sw: document.documentElement.scrollWidth,
        cw: document.documentElement.clientWidth, bodySW: document.body.scrollWidth })""")


def box(pg, sel):
    return pg.evaluate("""(s) => { const n = document.querySelector(s);
        if (!n || n.hidden) return null; const r = n.getBoundingClientRect();
        const cs = getComputedStyle(n);
        return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom,
                 position: cs.position, display: cs.display }; }""", sel)


def styles(pg, sel, props):
    """Computed styles of EVERY element matching sel."""
    return pg.evaluate("""([s, props]) => Array.from(document.querySelectorAll(s)).map((n) => {
        const cs = getComputedStyle(n); const o = { text: n.textContent, hidden: n.hidden,
          cls: n.className, testid: n.getAttribute('data-testid') };
        props.forEach((p) => { o[p] = cs.getPropertyValue(p); }); return o; })""", [sel, props])


def open_all_legs(pg):
    """Click every leg row and collect the drawer text per leg."""
    ids = pg.evaluate("""() => Array.from(document.querySelectorAll('[data-testid^="leg-row-"]'))
        .map((n) => n.getAttribute('data-testid').replace('leg-row-', ''))""")
    out = {}
    for i in ids:
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(100)
        pg.click(q("leg-row-" + i))
        pg.wait_for_timeout(250)
        out[i] = text(pg, "#drawer-trips")
    return out


def grayscale(path_in, path_out):
    from PIL import Image

    im = Image.open(path_in).convert("L")
    im.save(path_out)
    return im


def inside(inner, outer, tol=1.0):
    return (inner["x"] >= outer["x"] - tol and inner["y"] >= outer["y"] - tol and
            inner["right"] <= outer["right"] + tol and inner["bottom"] <= outer["bottom"] + tol)


# --------------------------------------------------------- scenario driver

TRIP_OF = {
    "offline_b": ("trip_b_europe", "offline"), "offline_c": ("trip_c_lon_mry_surcharge", "offline"),
    "live_b": ("trip_b_europe", "live"), "replay_b": ("trip_b_europe", "replay"),
    "down_b": ("trip_b_europe", "live"), "g7_offline": ("g7_never_priced_couple", "offline"),
    "g7_live": ("g7_never_priced_couple", "live"), "exit4": ("trip_b_europe", "offline"),
    "no_wallet": ("trip_b_europe", "offline"), "wallet_error": ("trip_b_europe", "offline"),
    "no_key": ("trip_b_europe", "offline"), "hostile": ("trip_b_europe", "live"),
}
SEARCH_SCENARIOS = ("search_ok", "search_api_error", "search_no_awards")

CHIP_PROPS = ["border-style", "border-left-width", "border-left-style", "border-top-style",
              "background-image", "background-color", "font-variant-caps", "color",
              "border-left-color", "border-top-color", "display", "text-transform"]

DOM_TEXT_JS = """() => {
  // Everything the user can read, EXCEPT the transcript fold (that is the CLI's
  // text, not the page's rendering of it) and the drawer's CLI line.
  const c = document.body.cloneNode(true);
  c.querySelectorAll('[data-testid="transcript"], pre.cli, pre.transcript').forEach((n) => n.remove());
  c.querySelectorAll('[hidden]').forEach((n) => n.remove());
  return c.innerText;
}"""


def dom_text(pg):
    # innerText on a detached clone is empty; attach, read, remove.
    return pg.evaluate("""() => {
      const c = document.body.cloneNode(true);
      c.querySelectorAll('[data-testid="transcript"], pre.cli, pre.transcript').forEach((n) => n.remove());
      c.querySelectorAll('[hidden]').forEach((n) => n.remove());
      c.style.position = 'absolute'; c.style.left = '-99999px'; c.style.width = '1400px';
      document.documentElement.appendChild(c);
      const t = c.innerText; c.remove(); return t;
    }""")


def collect(pg):
    """What a scenario's page says and how its chips are drawn."""
    out = {}
    out["transcript"] = transcript(pg)
    out["text"] = dom_text(pg)
    out["chips"] = styles(pg, ".chip, .tag-unv, .tag, .tag-flag, .hl-part, .hl-value", CHIP_PROPS)
    out["cells"] = pg.evaluate("""() => Array.from(document.querySelectorAll('td[data-kind]')).map((td) => ({
        kind: td.getAttribute('data-kind'), text: td.innerText, testid: td.getAttribute('data-testid'),
        hasUnknownChip: !!td.querySelector('.chip-unknown') }))""")
    out["headlineValue"] = text(pg, q("headline-value"))
    out["exitChip"] = text(pg, q("exit-chip"))
    out["banner"] = text(pg, q("banner-error"))
    out["topbar"] = text(pg, ".topbar")
    out["doc"] = doc_widths(pg)
    out["pwned"] = pg.evaluate("() => !!window.__pwned")
    return out


_CACHE = {}


def drive(browser, scenario, width=1440, force=False):
    """Drive one scenario once per session (per width) and cache what it said."""
    key = (scenario, width)
    if key in _CACHE and not force:
        return _CACHE[key]
    with probe_server(scenario) as srv:
        with browser.page(srv.port, width=width) as pg:
            if scenario in SEARCH_SCENARIOS or scenario == "hostile_search":
                run_search(pg)
                out = collect(pg)
                out["drawers"] = {}
                # open the first cell that holds an award
                for c in pg.query_selector_all('[data-testid^="cell-"]'):
                    t = (c.inner_text() or "").strip()
                    if not t or t.lower() == "no space":
                        continue
                    c.click()
                    pg.wait_for_timeout(500)
                    d = text(pg, "#drawer-search")
                    if d:
                        out["drawers"]["search"] = d
                        out["drawer_chips"] = styles(pg, "#drawer-search .chip, #drawer-search .tag-unv",
                                                     CHIP_PROPS)
                        break
            else:
                trip, mode = TRIP_OF[scenario]
                open_trip(pg, trip, mode)
                out = collect(pg)
                out["drawers"] = open_all_legs(pg)
                # chips inside the drawers, leg by leg
                out["drawer_chips"] = {}
                ids = list(out["drawers"].keys())
                for i in ids:
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(80)
                    pg.click(q("leg-row-" + i))
                    pg.wait_for_timeout(200)
                    out["drawer_chips"][i] = styles(pg, "#drawer-trips .chip, #drawer-trips .tag-unv, "
                                                    "#drawer-trips .tag", CHIP_PROPS)
                    out["text"] += "\n" + (text(pg, "#drawer-trips") or "")
            out["facts"] = dict(pg.facts)
            pg.screenshot(path=str(SHOTS / f"{scenario}-{width}.png"), full_page=False)
    _CACHE[key] = out
    return out
