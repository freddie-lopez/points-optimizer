"""
Harness for the map round's browser probes. Same rules as ../ui-probes and
../ui-restyle-probes:

  * RED = a defect that is present; GREEN = the attack held up.
  * NO BYTE LEAVES THIS MACHINE (the probe server refuses every connect(),
    Chromium resolves nothing but 127.0.0.1, offsite requests are aborted and
    recorded; the Python test process carries the round-1 socket canary).
  * Nothing is written into the repo tree: hub files are tmp, screenshots go
    to the gitignored shots-out/.

The restyle harness is imported for its Browser, page drivers and helpers;
this file adds the hub-file injection (`hub_server`) and map helpers.
"""
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
transcript = _r.transcript
dom_text = _r.dom_text
grayscale = _r.grayscale
PY = _r.PY

SHOTS = HERE / "shots-out"
SHOTS.mkdir(exist_ok=True)

from src import map_tools  # noqa: E402
from tests import _map_fixture  # noqa: E402


class Server:
    def __init__(self, scenario, hubs_file=None, land_file=None):
        env = dict(os.environ, PO_PROBE_ROOT=str(ROOT))
        if hubs_file is not None:
            env["PO_HUBS_FILE"] = str(hubs_file)
        if land_file is not None:
            env["PO_LAND_FILE"] = str(land_file)
        self.proc = subprocess.Popen(
            [PY, str(HERE / "map_probe_server.py"), scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(ROOT), env=env,
        )
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("probe server did not start: " +
                                 (self.proc.stderr.read() or "")[-3000:])
        self.info = json.loads(line)
        self.port = self.info["port"]

    def cmd(self, t):
        self.proc.stdin.write(t + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


@contextlib.contextmanager
def hub_server(scenario="search_ok", doc=None, hubs_text=None, missing=False, land_text=None):
    """A UI server whose /static/hubs.json is `doc` (a dict), `hubs_text`
    (bytes as given) or absent (`missing`). Default: the 12-hub synthetic."""
    tmp = Path(tempfile.mkdtemp(prefix="po-map-probe-hubs-"))
    hubs = tmp / "hubs.json"
    if not missing:
        if hubs_text is not None:
            hubs.write_text(hubs_text, encoding="utf-8")
        else:
            hubs.write_text(map_tools.render_document(doc or _map_fixture.synthetic_document()),
                            encoding="utf-8")
    land = None
    if land_text is not None:
        land = tmp / "land.json"
        land.write_text(land_text, encoding="utf-8")
    srv = Server(scenario, hubs_file=hubs, land_file=land)
    # The round-1 canary lets THIS test's own probe server through, nothing else.
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


@pytest.fixture(scope="session")
def browser():
    b = Browser()
    yield b
    b.close()


def synthetic(codes=None, routes=None, **meta):
    d = _map_fixture.synthetic_document(codes or _map_fixture.SYNTHETIC_CODES, routes)
    d["_meta"].update(meta)
    return d


# ------------------------------------------------------------- map drivers


def open_search(pg, wait_map=True):
    pg.click(q("tab-search"))
    pg.wait_for_selector(q("search-from"), timeout=15000)
    if wait_map:
        pg.wait_for_selector(q("map-status"), timeout=15000)
    pg.wait_for_timeout(250)


def status(pg):
    return text(pg, q("map-status"))


def provenance(pg):
    return text(pg, q("map-provenance"))


def markers(pg):
    """Every marker on the map: testid, kind, screen centre, label text."""
    return pg.evaluate("""() => Array.from(document.querySelectorAll('g.mk')).map((g) => {
        const c = g.querySelector('circle.dot, circle.cdot');
        const r = c.getBoundingClientRect();
        const t = g.querySelector('text');
        return { id: g.getAttribute('data-testid'), iata: g.getAttribute('data-iata'),
                 cluster: g.classList.contains('cl'), picked: g.classList.contains('pick'),
                 x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height,
                 label: t ? t.textContent : null, halo: !!g.querySelector('circle.halo') };
    })""")


def view(pg):
    return pg.evaluate("""() => {
        const s = document.querySelector('[data-testid="map-svg"]');
        return s ? s.getAttribute('viewBox') : null; }""")


def zoom_of(pg):
    vb = view(pg)
    if not vb:
        return None
    x, y, w, h = [float(v) for v in vb.split()]
    return {"x": x, "y": y, "w": w, "h": h, "z": 4000 / w}


def click_marker(pg, testid):
    """Click the marker's DOT (the g's box also spans the label, whose centre
    is ocean: pointer-events none on the text)."""
    pg.click(q(testid) + " circle.dot, " + q(testid) + " circle.cdot", force=True)
    pg.wait_for_timeout(200)


def wheel(pg, dy, at=None):
    b = box(pg, q("map-svg"))
    x = at[0] if at else b["x"] + b["w"] / 2
    y = at[1] if at else b["y"] + b["h"] / 2
    pg.mouse.move(x, y)
    pg.mouse.wheel(0, dy)
    pg.wait_for_timeout(120)


def drag(pg, dx, dy, start=None):
    b = box(pg, q("map-svg"))
    x = start[0] if start else b["x"] + b["w"] / 2
    y = start[1] if start else b["y"] + b["h"] / 2
    pg.mouse.move(x, y)
    pg.mouse.down()
    pg.mouse.move(x + dx / 2, y + dy / 2, steps=3)
    pg.mouse.move(x + dx, y + dy, steps=3)
    pg.mouse.up()
    pg.wait_for_timeout(120)


def route_d(pg):
    return pg.evaluate("""() => { const p = document.querySelector('[data-testid="map-route"]');
        return p ? p.getAttribute('d') : null; }""")


def values(pg):
    return pg.evaluate("""() => ({
        from: document.querySelector('[data-testid="search-from"]').value,
        to: document.querySelector('[data-testid="search-to"]').value })""")


def run_search_typed(pg, origin="SFO", destination="MAD", date="2027-01-15"):
    pg.fill(q("search-from"), origin)
    pg.fill(q("search-to"), destination)
    pg.fill(q("search-date"), date)
    pg.click(q("search-run"))
    pg.wait_for_selector(q("search-confirm-go"), timeout=15000)


def confirm_text(pg):
    return text(pg, q("search-confirm"))


def finish_search(pg):
    pg.click(q("search-confirm-go"))
    pg.wait_for_selector(q("search-table") + "," + q("search-state") + ":not([hidden])", timeout=60000)
    pg.wait_for_function("""() => !document.querySelector('[data-testid="search-run"]').disabled""",
                         timeout=60000)
    pg.wait_for_timeout(300)
