"""
map.js's data logic - projection, hub validation, autofill ranking, the
great-circle split - run in node against stubs, with no browser and no socket.

These need `node` (the sandbox has it; the ui-probes need it too). Where it
is absent they skip, and the ui-probes cover the same ground in Chromium.
The DOM parts (clustering, markers, popover) are the probes' territory: they
need a real layout to measure pixels.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import build_land_path  # noqa: E402
from tests import _map_fixture  # noqa: E402

MAP_JS = ROOT / "src" / "ui" / "static" / "map.js"
LAND = ROOT / "src" / "ui" / "static" / "land.json"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed here")

# A minimal window/document/fetch so map.js can be loaded and its data paths
# exercised. `fetch` answers from the two JSON strings the test hands in.
HARNESS = r"""
const fs = require("fs");
const di = process.argv.indexOf("--");
const a = di >= 0 ? process.argv.slice(di + 1) : process.argv.slice(1);
const [mapPath, landPath, hubsPath, hubsStatus, script] = [a[0], a[1],
  a[2], Number(a[3]), a[4]];
const landText = fs.readFileSync(landPath, "utf8"), hubsText = fs.readFileSync(hubsPath, "utf8");
global.window = global;
global.document = { createElement() { throw new Error("no DOM in this harness"); } };
window.addEventListener = () => {};
window.requestAnimationFrame = (f) => 0;
global.fetch = (path) => {
  const body = path.endsWith("land.json") ? landText : hubsText;
  const status = path.endsWith("land.json") ? 200 : hubsStatus;
  return Promise.resolve({
    ok: status === 200, status,
    json: () => { try { return Promise.resolve(JSON.parse(body)); } catch (e) { return Promise.reject(e); } },
  });
};
eval(fs.readFileSync(mapPath, "utf8"));
window.POMap.load().then((d) => {
  const out = eval(script);
  process.stdout.write(JSON.stringify(out));
});
"""


@pytest.fixture
def run_js(tmp_path):
    def run(script: str, hubs_doc=None, hubs_text=None, hubs_status=200):
        if hubs_text is None:
            hubs_text = json.dumps(hubs_doc if hubs_doc is not None else _map_fixture.synthetic_document())
        hubs_path = tmp_path / "hubs.json"
        hubs_path.write_text(hubs_text, encoding="utf-8")
        return _run_node(script, hubs_path, hubs_status)
    return run


def _run_node(script: str, hubs_path: Path, hubs_status: int):
    p = subprocess.run([NODE, "-e", HARNESS, "--", str(MAP_JS), str(LAND), str(hubs_path),
                        str(hubs_status), script],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout)


# -------------------------------------------------------------- projection


def test_the_browser_projects_a_hub_where_the_generator_projects_the_land(run_js):
    """Both implement Miller from the same four numbers in land.json."""
    out = run_js("d.hubs.map((h) => [h.iata, h.lat, h.lon, h.X, h.Y])")
    assert len(out) == 10   # SAT and SJU are not searchable
    for iata, lat, lon, x, y in out:
        px, py = build_land_path.project(lon, lat)
        assert abs(px - x) < 1e-6 and abs(py - y) < 1e-6, iata


def test_sfo_is_on_the_left_and_lhr_over_london(run_js):
    out = run_js("({sfo: window.POMap.hub('sfo'), lhr: window.POMap.hub('LHR')})")
    assert 0 < out["sfo"]["X"] < 4000 * 0.2
    assert 4000 * 0.49 < out["lhr"]["X"] < 4000 * 0.51
    assert out["lhr"]["Y"] < out["sfo"]["Y"]   # further north = higher up


# ------------------------------------------------------------ validation


def _doc(hubs, meta=None):
    base = _map_fixture.synthetic_document()
    if meta is not None:
        base["_meta"].update(meta)
    base["hubs"] = hubs
    return base


def _hub(**over):
    h = dict(_map_fixture.synthetic_document()["hubs"][0])
    h.update(over)
    return h


@pytest.mark.parametrize("bad,counter", [
    (dict(iata="<b>"), "dropped"),
    (dict(iata="SFOX"), "dropped"),
    (dict(iata="sfo"), "dropped"),
    (dict(routes=0), "dropped"),
    (dict(routes="12"), "dropped"),
    (dict(routes=1.5), "dropped"),
    (dict(sources=[]), "dropped"),
    (dict(searchable=False), "notSearchable"),
    (dict(searchable="true"), "notSearchable"),
    (dict(lat=None, lon=None), "noCoords"),
    (dict(lat="37"), "noCoords"),
    (dict(lat=95.0), "noCoords"),
    ("not an object", "dropped"),
])
def test_a_hub_that_fails_a_check_is_counted_not_plotted(run_js, bad, counter):
    hub = bad if isinstance(bad, str) else _hub(**bad)
    out = run_js("({plotted: d.set.plotted.length, total: d.set.total, dropped: d.set.dropped, "
                 "notSearchable: d.set.notSearchable, noCoords: d.set.noCoords, error: d.error})",
                 hubs_doc=_doc([hub]))
    assert out["error"] is None
    assert out["plotted"] == 0 and out["total"] == 1
    assert out[counter] == 1


def test_a_duplicate_code_is_plotted_once(run_js):
    out = run_js("d.set.plotted.length", hubs_doc=_doc([_hub(), _hub()]))
    assert out == 1


def test_hostile_strings_are_kept_as_text_and_odd_types_become_strings(run_js):
    hub = _hub(name='<img src=x onerror=alert(1)>', city=None, country=42)
    out = run_js("d.hubs.map((h) => [h.name, h.city, h.country])", hubs_doc=_doc([hub]))
    assert out == [['<img src=x onerror=alert(1)>', "", "42"]]


@pytest.mark.parametrize("text,status,reason", [
    ("", 404, "HTTP 404"),
    ('{"_meta": {}, "hubs": [', 200, "SyntaxError"),
    ('{"_meta": {}, "hubs": {}}', 200, "hubs is not a list"),
    ('"a string"', 200, "hubs is not a list"),
])
def test_an_unreadable_file_is_an_error_never_an_empty_map(run_js, text, status, reason):
    out = run_js("({error: d.error, hubs: d.hubs.length, sugg: window.POMap.suggest('sfo', 8).length})",
                 hubs_text=text, hubs_status=status)
    assert out == {"error": reason, "hubs": 0, "sugg": 0}


def test_the_empty_file_plots_nothing_and_suggests_nothing(run_js):
    out = run_js("({error: d.error, total: d.set.total, sugg: window.POMap.suggest('s', 8).length, "
                 "hub: window.POMap.hub('SFO')})", hubs_doc=_doc([]))
    assert out == {"error": None, "total": 0, "sugg": 0, "hub": None}


# --------------------------------------------------------------- autofill


@pytest.fixture
def codes(run_js):
    def _codes(query, max_rows=8):
        return run_js(f"window.POMap.suggest({json.dumps(query)}, {max_rows}).map((h) => h.iata)")
    return _codes


def test_ranking_exact_code_then_code_prefix_then_city_prefix_then_substring(codes):
    # "san": SAN is the exact code; SFO and SJC are city prefixes ("San
    # Francisco", "San Jose") ordered by route count. SAT and SJU are not
    # searchable in the fixture, so the map does not offer them.
    assert codes("san") == ["SAN", "SFO", "SJC"]
    # "sf": code prefix SFO first; SYD only by the substring in "Kingsford".
    assert codes("sf") == ["SFO", "SYD"]
    # "lon": city prefix "London" - LHR before LGW by route count.
    assert codes("lon") == ["LHR", "LGW"]
    # "new": city prefix "New York" (JFK, LGA) and "Newark" (EWR), by routes.
    assert codes("new") == ["JFK", "EWR", "LGA"]


def test_the_list_is_capped_case_insensitive_and_empty_for_blank_input(codes):
    assert codes("a", 2) == ["SFO", "JFK"]     # two highest route counts with an "a"
    assert codes("  SFO ")[0] == "SFO"      # SYD follows: "kingSFOrd" is a substring hit
    assert codes("   ") == []
    assert codes("zzz") == []


def test_hub_lookup_trims_and_uppercases_and_refuses_unplotted_codes(run_js):
    out = run_js("({a: window.POMap.hub(' lhr ').iata, b: window.POMap.hub('SAT'), c: window.POMap.hub('')})")
    assert out == {"a": "LHR", "b": None, "c": None}
