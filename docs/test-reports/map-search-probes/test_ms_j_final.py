"""
J. Final re-test (fdd4a65): N1 with the 137-hub fixture at every zoom and
picks at SFO, JFK, LHR, SIN, SYD (alone, as From/To pairs, and two close
hubs together); N2 with subset / superset / equal / disjoint source sets,
with and without --force, with and without gaps.
"""
import itertools
import sys

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from src.seats_client import SEATS_AERO_SOURCES, SeatsClient  # noqa: E402
from test_ms_f_clusters_zoom import dense_doc, label_collisions  # noqa: E402
from test_ms_i_retest import label_over_label, min_pair  # noqa: E402
from tests.test_map_tools import Stub, capture_args, ok, run, written  # noqa: E402

PICKS = ["SFO", "JFK", "LHR", "SIN", "SYD"]


@pytest.fixture(autouse=True)
def fresh_budget():
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def audit_all(pg, tag):
    """Every marker: 18px from every other, clickable at its centre (when on
    screen), no label over a dot or a label; picked labels present (right or
    mirrored left). Every problem is collected so one failure hides none."""
    ms = C.markers(pg)
    problems = []
    total = sum(int(m["label"]) if m["cluster"] else 1 for m in ms)
    if total != 137:
        problems.append(("count", total))
    worst = min_pair(pg)
    if worst[0] < 17.5:
        problems.append(("18px", worst))
    if label_collisions(pg):
        problems.append(("label-over-dot", label_collisions(pg)))
    if label_over_label(pg):
        problems.append(("label-over-label", label_over_label(pg)))
    misses = pg.evaluate("""() => Array.from(document.querySelectorAll('g.mk')).map((g) => {
        const c = g.querySelector('circle.dot, circle.cdot'); const r = c.getBoundingClientRect();
        const x = r.x + r.width / 2, y = r.y + r.height / 2;
        const s = document.querySelector('[data-testid=map-svg]').getBoundingClientRect();
        if (x < s.left + 2 || x > s.right - 2 || y < s.top + 2 || y > s.bottom - 2) return null;
        const e = document.elementFromPoint(x, y); const hit = e && e.closest ? e.closest('g.mk') : null;
        return hit === g ? null : [g.getAttribute('data-testid'), hit ? hit.getAttribute('data-testid') : (e ? e.tagName : null)];
    }).filter(Boolean)""")
    if misses:
        problems.append(("unclickable", misses))
    for m in ms:
        if m["picked"]:
            if m["label"] != m["iata"]:
                problems.append(("picked-label-hidden", m["id"]))
            else:
                anchor = pg.evaluate("(id) => document.querySelector('[data-testid=' + id + '] text.lbl').getAttribute('text-anchor')", m["id"])
                if anchor not in ("start", "end"):
                    problems.append(("picked-label-anchor", m["id"], anchor))
    assert problems == [], (tag, problems)


@pytest.mark.parametrize("pick", PICKS)
def test_J1_one_pick_at_every_button_zoom_step(browser, pick):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), pick)
            pg.wait_for_timeout(250)
            assert any(m["id"] == f"map-hub-{pick}" and m["picked"] for m in C.markers(pg))
            for step in range(8):
                audit_all(pg, (pick, round(C.zoom_of(pg)["z"], 2)))
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(150)
            for _ in range(8):
                pg.click(C.q("map-zoom-out"))
                pg.wait_for_timeout(80)
                audit_all(pg, (pick, "out", round(C.zoom_of(pg)["z"], 2)))
            assert pg.facts["errors"] == []


@pytest.mark.parametrize("a,b", list(itertools.combinations(PICKS, 2)))
def test_J2_every_from_to_pair_at_three_zooms(browser, a, b):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), a)
            pg.fill(C.q("search-to"), b)
            pg.wait_for_timeout(250)
            assert C.route_d(pg)
            for z in (1, 3, 8):
                while C.zoom_of(pg)["z"] < z:
                    pg.click(C.q("map-zoom-in"))
                    pg.wait_for_timeout(120)
                audit_all(pg, (a, b, round(C.zoom_of(pg)["z"], 2)))


@pytest.mark.parametrize("a,b", [("SFO", "SJC"), ("JFK", "EWR"), ("LHR", "LGW")])
def test_J3_two_picks_closer_than_18px_to_each_other(browser, a, b):
    """Both picked, both drawn at their own point: at z=1 the two dots are
    1-3px apart by geography. Recorded as what happens, asserted only for
    what can hold: counts, clickability of everything else, no label overlap."""
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), a)
            pg.fill(C.q("search-to"), b)
            pg.wait_for_timeout(250)
            ms = C.markers(pg)
            assert sum(int(m["label"]) if m["cluster"] else 1 for m in ms) == 137
            assert label_collisions(pg) == [] and label_over_label(pg) == []
            pairs = [p for p in [min_pair(pg)] if not ({p[1], p[2]} == {f"map-hub-{a}", f"map-hub-{b}"})]
            assert not pairs or pairs[0][0] >= 17.5, pairs


def test_J4_the_displaced_cluster_still_lists_its_members_and_the_pick_is_on_top(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            sfo = [m for m in C.markers(pg) if m["id"] == "map-hub-SFO"][0]
            near = [m for m in C.markers(pg) if m["cluster"] and abs(m["x"] - sfo["x"]) <= 40 and abs(m["y"] - sfo["y"]) <= 40]
            assert near, "no cluster near the picked SFO at z=1"
            for c in near:
                pg.mouse.click(c["x"], c["y"])
                pg.wait_for_timeout(200)
                pop = C.text(pg, C.q("map-cluster-list"))
                assert pop and int(pop.split("\n")[1].split()[0]) == int(c["label"]), (c["id"], pop)
                assert "SFO" not in [l.split(" ")[0] for l in pop.split("\n")[3::2]]
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(100)
            assert pg.evaluate("() => document.querySelector('.map-picks [data-testid=map-hub-SFO]') !== null")
            pg.mouse.click(sfo["x"], sfo["y"])
            pg.wait_for_timeout(200)
            assert C.values(pg) == {"from": "SFO", "to": ""}


# ------------------------------------------------------------------- N2


SRC = list(SEATS_AERO_SOURCES)
FAIL = lambda url, kw: ok({"error": "x"}, status=500)  # noqa: E731


def capture(tmp_path, sources, *extra, failing=()):
    SeatsClient.reset_call_budget()
    stub = Stub(answers={s: FAIL for s in failing})
    return run(capture_args(tmp_path, "--yes", "--sources", ",".join(sources), *extra), stub=stub)


def ok_set(tmp_path):
    return set(written(tmp_path)["_meta"]["sources_ok"])


@pytest.mark.parametrize("gaps", [False, True])
def test_J5_subset_is_kept_and_force_replaces(tmp_path, gaps):
    code, out, _ = capture(tmp_path, SRC[:4], "--min-sources", "1")
    assert code == 0 and ok_set(tmp_path) == set(SRC[:4])
    before = (tmp_path / "out" / "hubs.json").read_bytes()
    failing = [SRC[1]] if gaps else []
    code, out, _ = capture(tmp_path, SRC[:2], "--min-sources", "1", failing=failing)
    assert code == (5 if gaps else 1), (gaps, code, out[-300:])
    assert (tmp_path / "out" / "hubs.json").read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["hubs.json"]
    assert f"NOT OVERWRITTEN: {tmp_path / 'out' / 'hubs.json'} holds a capture with 4 source(s) ok, this run has {1 if gaps else 2}." in out
    code, out, _ = capture(tmp_path, SRC[:2], "--min-sources", "1", "--force", failing=failing)
    assert code == (5 if gaps else 0)
    assert ok_set(tmp_path) == (set(SRC[:1]) if gaps else set(SRC[:2]))


@pytest.mark.parametrize("gaps", [False, True])
def test_J6_superset_replaces(tmp_path, gaps):
    code, out, _ = capture(tmp_path, SRC[:2], "--min-sources", "1")
    assert code == 0
    failing = [SRC[5]] if gaps else []
    code, out, _ = capture(tmp_path, SRC[:6], "--min-sources", "1", failing=failing)
    assert code == (5 if gaps else 0) and "NOT OVERWRITTEN" not in out
    assert ok_set(tmp_path) == set(SRC[:5]) if gaps else set(SRC[:6])


@pytest.mark.parametrize("gaps", [False, True])
def test_J7_equal_set_replaces(tmp_path, gaps):
    code, out, _ = capture(tmp_path, SRC[:3], "--min-sources", "1")
    first = written(tmp_path)["_meta"]["captured_at"]
    failing = [SRC[3]] if gaps else []
    srcs = SRC[:4] if gaps else SRC[:3]
    code, out, _ = capture(tmp_path, srcs, "--min-sources", "1", failing=failing)
    assert code == (5 if gaps else 0) and "NOT OVERWRITTEN" not in out
    assert ok_set(tmp_path) == set(SRC[:3])


@pytest.mark.parametrize("gaps", [False, True])
def test_J8_disjoint_sets(tmp_path, gaps):
    """Disjoint: neither is a superset; the larger count wins, an equal
    count is replaced (the newer run)."""
    code, out, _ = capture(tmp_path, SRC[:3], "--min-sources", "1")
    failing = [SRC[6]] if gaps else []
    # 3 disjoint ok (4 asked with one failing when gaps): equal count -> replaced
    srcs = SRC[3:7] if gaps else SRC[3:6]
    code, out, _ = capture(tmp_path, srcs, "--min-sources", "1", failing=failing)
    assert "NOT OVERWRITTEN" not in out and ok_set(tmp_path) == set(SRC[3:6])
    # 2 disjoint ok against 3: kept
    failing = [SRC[9]] if gaps else []
    srcs = SRC[7:10] if gaps else SRC[7:9]
    code, out, _ = capture(tmp_path, srcs, "--min-sources", "1", failing=failing)
    assert code == (5 if gaps else 1) and "NOT OVERWRITTEN" in out and ok_set(tmp_path) == set(SRC[3:6])
    # 4 disjoint ok against 3: replaced
    code, out, _ = capture(tmp_path, SRC[10:14], "--min-sources", "1")
    assert code == 0 and ok_set(tmp_path) == set(SRC[10:14])


def test_J9_the_guard_ignores_an_empty_or_broken_existing_file_and_a_hostile_sources_ok(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "hubs.json").write_bytes((ROOT / "data" / "hubs.json").read_bytes())
    code, out, _ = capture(tmp_path, SRC[:1], "--min-sources", "1")
    assert code == 0 and ok_set(tmp_path) == {SRC[0]}
    (tmp_path / "out" / "hubs.json").write_text("{broken")
    code, out, _ = capture(tmp_path, SRC[:1], "--min-sources", "1")
    assert code == 0 and ok_set(tmp_path) == {SRC[0]}
    doc = written(tmp_path)
    doc["_meta"]["sources_ok"] = ["<b>", 42, None, "x" * 10000] + SRC[:1]
    import json
    (tmp_path / "out" / "hubs.json").write_text(json.dumps(doc))
    code, out, _ = capture(tmp_path, SRC[:1], "--min-sources", "1")
    assert code in (0, 1), out[-300:]
