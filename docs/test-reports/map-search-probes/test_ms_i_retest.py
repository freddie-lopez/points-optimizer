"""
I. Re-test round (f1fe286): each fix attacked on its own terms, plus the new
strings #16-#20 byte-exact.
"""
import json
import os
import pathlib
import sys

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from src import map_tools  # noqa: E402
from src.seats_client import SEATS_AERO_SOURCES, SeatsClient  # noqa: E402
from test_ms_f_clusters_zoom import dense_doc, label_collisions  # noqa: E402
from tests.test_map_tools import Stub, capture_args, ok, route, run, written  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_budget():
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def label_over_label(pg):
    return pg.evaluate("""() => { const ts = Array.from(document.querySelectorAll('g.mk text.lbl')).map(t => [t.closest('g').getAttribute('data-testid'), t.getBoundingClientRect()]); const out = [];
      for (let i = 0; i < ts.length; i++) for (let j = i + 1; j < ts.length; j++) { const a = ts[i][1], b = ts[j][1];
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) out.push([ts[i][0], ts[j][0]]); } return out; }""")


def min_pair(pg):
    ms = C.markers(pg)
    worst = None
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            d = max(abs(ms[i]["x"] - ms[j]["x"]), abs(ms[i]["y"] - ms[j]["y"]))
            if worst is None or d < worst[0]:
                worst = (d, ms[i]["id"], ms[j]["id"])
    return worst


# ------------------------------------------------------------- L7 / L2


def test_I1_no_label_overlaps_a_dot_or_a_label_at_any_zoom_without_a_pick(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            for _ in range(8):
                assert label_collisions(pg) == [], C.zoom_of(pg)["z"]
                assert label_over_label(pg) == [], C.zoom_of(pg)["z"]
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(150)
            pg.click(C.q("map-reset"))
            b = C.box(pg, C.q("map-svg"))
            for _ in range(12):
                C.wheel(pg, -250, at=(b["x"] + 700, b["y"] + 250))
                assert label_collisions(pg) == [], C.zoom_of(pg)["z"]
                assert label_over_label(pg) == [], C.zoom_of(pg)["z"]


def test_I2_a_picked_label_does_not_sit_on_a_cluster(browser):
    """The fix exempts picked labels (`picked || labelFits`): pick LHR and its
    label lies over the AMS/LCY cluster at every zoom step."""
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "LHR")
            pg.wait_for_timeout(200)
            hits = []
            for _ in range(8):
                hits += [(round(C.zoom_of(pg)["z"], 2), h) for h in label_collisions(pg)]
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(150)
            pg.screenshot(path=str(C.SHOTS / "I2-picked-label-over-cluster.png"))
            assert hits == [], hits


def test_I3_a_pushed_cluster_keeps_18px_from_every_other_marker(browser):
    """L2's push moves a cluster 18px from the picked hub - and into another
    cluster's 18px zone (LAS vs MRY at 13px after picking SFO)."""
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert min_pair(pg)[0] >= 17.5
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            worst = min_pair(pg)
            pg.screenshot(path=str(C.SHOTS / "I3-pushed-cluster.png"), clip={"x": 480, "y": 300, "width": 220, "height": 120})
            assert worst[0] >= 17.5, worst
            assert label_collisions(pg) == [], label_collisions(pg)


def test_I4_a_hidden_label_hub_is_still_clickable_and_a_pushed_cluster_still_lists_its_members(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            for _ in range(3):
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(120)
            hidden = [m for m in C.markers(pg) if not m["cluster"] and not m["label"] and 400 < m["x"] < 1420 and 70 < m["y"] < 700]
            assert hidden, "no hidden-label single on screen at z=3.4"
            pg.mouse.click(hidden[0]["x"], hidden[0]["y"])
            pg.wait_for_timeout(200)
            assert C.values(pg)["from"] == hidden[0]["iata"]
            pg.click(C.q("map-reset"))
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            sfo = [m for m in C.markers(pg) if m["id"] == "map-hub-SFO"][0]
            cl = [m for m in C.markers(pg) if m["cluster"] and abs(m["x"] - sfo["x"]) < 30 and abs(m["y"] - sfo["y"]) < 30]
            assert cl, "no pushed cluster beside the picked SFO"
            pg.mouse.click(cl[0]["x"], cl[0]["y"])
            pg.wait_for_timeout(200)
            pop = C.text(pg, C.q("map-cluster-list"))
            assert pop and "SFO" not in pop.split("\n")[0]


# ------------------------------------------------------------- L9 / L10 / L11


def test_I5_the_accessibility_tree_exposes_the_markers_and_tab_reaches_them(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            snap = pg.accessibility.snapshot(root=pg.query_selector(C.q("map-svg")))
            s = json.dumps(snap or {})
            assert snap and snap.get("role") == "group"
            assert s.count('"role": "button"') >= 5 and "SYD" in s
            pg.focus(C.q("search-run"))
            seen = []
            for _ in range(12):
                pg.keyboard.press("Tab")
                seen.append(pg.evaluate("() => document.activeElement.getAttribute('data-testid') || document.activeElement.tagName"))
            assert any(x and x.startswith("map-hub-") for x in seen) and any(x and x.startswith("map-cluster-") for x in seen), seen
            assert "map-svg" not in seen, "tabindex -1 must keep the svg out of the tab order"


def test_I6_ocean_click_closes_the_list_but_a_click_on_the_list_does_not(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-LHR")
            pb = C.box(pg, C.q("map-cluster-list"))
            pg.mouse.click(pb["x"] + pb["w"] / 2, pb["y"] + 8)
            pg.wait_for_timeout(150)
            assert C.text(pg, C.q("map-cluster-list")) is not None
            b = C.box(pg, C.q("map-svg"))
            pg.mouse.click(b["x"] + 40, b["y"] + b["h"] - 80)
            pg.wait_for_timeout(150)
            assert C.text(pg, C.q("map-cluster-list")) is None
            # and a click on another marker opens that one instead
            C.click_marker(pg, "map-cluster-JFK")
            assert C.text(pg, C.q("map-cluster-list")).startswith("3 airports near New York")
            C.click_marker(pg, "map-cluster-LHR")
            assert C.text(pg, C.q("map-cluster-list")).startswith("London")


# ------------------------------------------------------------- M1 / M2 / I1 / I2


@pytest.mark.parametrize("w,results", [(1180, 448), (1300, 568), (1440, 708)])
def test_I7_the_220px_column_is_usable_and_the_results_get_their_width(browser, w, results):
    with hub_server() as srv:
        with browser.page(srv.port, width=w) as pg:
            C.open_search(pg)
            C.run_search_typed(pg)
            C.finish_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(300)
                    break
            assert pg.evaluate("() => getComputedStyle(document.querySelector('.search-grid')).gridTemplateColumns") == f"220px {results}px 440px"
            col = C.box(pg, "#search-main")
            for t in ("search-from", "search-to", "search-date", "search-date-to", "search-cabin", "search-run", "search-pane-toggle"):
                bx = C.box(pg, C.q(t))
                assert bx["x"] >= col["x"] - 1 and bx["right"] <= col["right"] + 1, (t, bx, col)
                if t != "search-pane-toggle":
                    assert bx["w"] >= 150, (t, bx["w"])
            assert C.doc_widths(pg)["bodySW"] <= w
            assert pg.evaluate("() => Array.from(document.querySelectorAll('#search-main *')).filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible').length") == 0
            pg.fill(C.q("search-from"), "s")
            pg.wait_for_timeout(200)
            sb = C.box(pg, C.q("search-suggest-from"))
            assert sb["x"] >= col["x"] - 1 and sb["right"] <= col["right"] + 1
            assert pg.evaluate("() => Array.from(document.querySelectorAll('.srow .mono')).every(m => m.getBoundingClientRect().width >= 20)")
            pg.keyboard.press("Escape")
            pg.fill(C.q("search-from"), "SFO")
            pg.fill(C.q("search-to"), "LHR")
            pg.click(C.q("search-run"))
            pg.wait_for_selector(C.q("search-confirm-go"))
            assert "--origin SFO --destination LHR" in C.confirm_text(pg)
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(150)
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => document.getElementById('drawer-search').hidden")
            assert not pg.evaluate("() => document.getElementById('page').classList.contains('has-drawer')")
            assert pg.evaluate("() => getComputedStyle(document.querySelector('.search-grid')).gridTemplateColumns").startswith("360px ")
            assert C.text(pg, C.q("search-ran")) == "The search has run: its results are under Show results."
            pg.click(C.q("search-pane-toggle"))
            pg.wait_for_timeout(300)
            assert C.text(pg, C.q("search-ran")) is None
            assert pg.facts["errors"] == []


@pytest.mark.parametrize("w,shown", [(899, "Type an airport or city."), (900, "Type an airport or city, or pick it on the map.")])
def test_I8_one_subline_per_width(browser, w, shown):
    with hub_server() as srv:
        with browser.page(srv.port, width=w) as pg:
            C.open_search(pg, wait_map=w >= 900)
            vis = pg.evaluate("() => Array.from(document.querySelectorAll('.search-head .note')).filter(n => getComputedStyle(n).display !== 'none').map(n => n.textContent)")
            assert vis == [shown], vis


# ------------------------------------------------------------- L3 / L4 / L5 (#16)


@pytest.mark.parametrize("land,reason", [('{"w": 1}', "not a land file"), ('{"w":', "SyntaxError"), (None, "HTTP 404")])
def test_I9_string_16_for_every_land_failure_and_the_form_still_searches(browser, land, reason):
    with hub_server(land_text=land if land is not None else "") as srv:
        if land is None:
            os.remove(str(srv.info["hubs_file"]).replace("hubs.json", "land.json"))
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.status(pg) == f"AIRPORT DATA UNREADABLE - /static/land.json could not be read ({reason}). The map plots nothing."
            assert pg.facts["errors"] == []
            C.run_search_typed(pg)
            assert "--origin SFO --destination MAD" in C.confirm_text(pg)


# ------------------------------------------------------------- L1 / L12 / L13 / L14 (#19, #20)


def test_I10_every_disk_failure_is_the_sentence_and_leaves_nothing(tmp_path, monkeypatch):
    real = pathlib.Path.write_text

    def half(self, text, *a, **k):
        if self.name.endswith(".tmp"):
            with open(self, "w") as f:
                f.write(text[: len(text) // 2])
            raise OSError(28, "No space left on device")
        return real(self, text, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", half)
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1 and "could not be written (OSError:" in out and out.endswith("Nothing was written.")
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
    monkeypatch.setattr(pathlib.Path, "write_text", real)
    SeatsClient.reset_call_budget()
    (tmp_path / "d" / "hubs.json").mkdir(parents=True)
    code, out, stub = run(["capture-hubs", "--out", str(tmp_path / "d" / "hubs.json"), "--api-key", "flag_key_for_map_tools_0123456789", "--yes"])
    assert code == 1 and "could not be written (IsADirectoryError:" in out
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["hubs.json"]
    SeatsClient.reset_call_budget()
    (tmp_path / "f").write_text("x")
    code, out, stub = run(["capture-hubs", "--out", str(tmp_path / "f" / "hubs.json"), "--api-key", "flag_key_for_map_tools_0123456789", "--yes"])
    assert code == 1 and "could not be written (" in out and "Nothing was written." in out


def test_I11_impossible_threshold_refused_before_any_call_and_before_the_prompt(tmp_path):
    asked = []
    code, out, stub = run(capture_args(tmp_path, "--sources", "aeroplan,united", "--min-sources", "3"),
                          read=lambda p: asked.append(p) or "y")
    assert code == 1 and len(stub.calls) == 0 and asked == []
    assert ("--min-sources 3 can never be met by the 2 source(s) asked; no airport could be a hub. "
            "No call was made and nothing was written.") in out
    SeatsClient.reset_call_budget()
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan,united"))
    assert code == 0 and written(tmp_path)["_meta"]["thresholds"]["min_sources"] == 2
    assert ("--min-sources not given: using 2, the number of sources asked (the default 3 could never "
            "be met). Recorded in _meta.thresholds.") in out


def test_I12_duplicate_sources_asked_once_in_order(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "united, AEROPLAN,united,aeroplan,virginatlantic"))
    assert [kw["params"]["source"] for _, kw in stub.calls] == ["united", "aeroplan", "virginatlantic"]
    assert "at most 3 Seats.aero API call(s)" in out
    assert written(tmp_path)["_meta"]["sources_asked"] == ["united", "aeroplan", "virginatlantic"]


def test_I13_a_more_complete_capture_is_kept_without_force_and_replaced_with_it(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 0
    before = (tmp_path / "out" / "hubs.json").read_bytes()
    fail = {s: (lambda url, kw: ok({"error": "x"}, status=500)) for s in list(SEATS_AERO_SOURCES)[1:]}
    SeatsClient.reset_call_budget()
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1"), stub=Stub(answers=fail))
    assert code == 5
    assert (tmp_path / "out" / "hubs.json").read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["hubs.json"]
    assert (f"NOT OVERWRITTEN: {tmp_path / 'out' / 'hubs.json'} holds a capture with 28 source(s) ok, this run "
            f"has 1. The existing file is kept and nothing was written. Pass --force to replace it with this run.") in out
    SeatsClient.reset_call_budget()
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1", "--force"), stub=Stub(answers=fail))
    assert code == 5 and len(written(tmp_path)["_meta"]["sources_ok"]) == 1 and "WRITTEN WITH GAPS" in out


def test_I14_a_gapless_one_source_run_does_not_silently_replace_a_28_source_capture(tmp_path):
    """`--sources aeroplan` answers fully (no gaps) and so passes the guard:
    the 28-source file is replaced by a 1-source one without a word."""
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 0 and len(written(tmp_path)["_meta"]["sources_ok"]) == 28
    SeatsClient.reset_call_budget()
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan"))
    now = len(written(tmp_path)["_meta"]["sources_ok"])
    assert now == 28 or "NOT OVERWRITTEN" in out, f"replaced by a {now}-source capture silently"


def test_I15_regions_are_stored_as_codes_and_free_text_is_counted(tmp_path):
    def rows(source):
        return [route("SFO", "LHR", oreg="<img>" + "R" * 10000, dreg="Europe") for _ in range(25)]

    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    by = {h["iata"]: h for h in written(tmp_path)["hubs"]}
    assert by["LHR"]["regions"] == ["EU"] and by["SFO"]["regions"] == []
    assert "<img>" not in json.dumps(written(tmp_path))
    assert (f"{25 * 28} routes seen across 28 source(s); 0 route side(s) dropped for a code that is not three "
            f"upper-case letters or digits; 0 row(s) that were not objects ignored; {25 * 28} region label(s) "
            f"not in the known Seats.aero regions counted, not stored.") in out


def test_I16_the_readme_names_every_flag():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for f in ("--api-key", "--file", "--force", "--sources", "--out", "--raw-dir", "--min-sources", "--min-routes", "--yes"):
        assert f in readme, f
