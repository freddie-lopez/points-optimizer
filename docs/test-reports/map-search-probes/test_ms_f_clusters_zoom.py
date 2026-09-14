"""
F. Clustering at every zoom step with a 137-hub fixture (every airport the
engine accepts that has coordinates): counts add up, no two marker centres
within 18px, labels only at z >= 3, dots the same size at every zoom, zoom
and pan clamp, the picked-hub-never-clustered rule and what it costs, the
antimeridian route, popover clamping at the corners, performance with 400.
"""
import random
import sys
import time

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from src import map_tools, regions  # noqa: E402

COORDS = map_tools.load_coordinates()
KNOWN = [c for c in regions.known_airports() if c in COORDS]


def dense_doc():
    return C.synthetic(codes=KNOWN, routes={c: 1000 - i for i, c in enumerate(sorted(KNOWN))})


def audit(pg):
    ms = C.markers(pg)
    total = sum(int(m["label"]) if m["cluster"] else 1 for m in ms)
    worst = None
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            d = max(abs(ms[i]["x"] - ms[j]["x"]), abs(ms[i]["y"] - ms[j]["y"]))
            if worst is None or d < worst[0]:
                worst = (d, ms[i]["id"], ms[j]["id"])
    return ms, total, worst


def label_collisions(pg):
    return pg.evaluate("""() => { const gs = Array.from(document.querySelectorAll('g.mk')); const out = [];
      const dots = gs.map(g => ({id: g.getAttribute('data-testid'), r: g.querySelector('circle.dot, circle.cdot').getBoundingClientRect()}));
      gs.forEach(g => { const t = g.querySelector('text.lbl'); if (!t) return; const tr = t.getBoundingClientRect();
        dots.forEach(d => { if (d.id === g.getAttribute('data-testid')) return;
          if (tr.left < d.r.right && tr.right > d.r.left && tr.top < d.r.bottom && tr.bottom > d.r.top) out.push([g.getAttribute('data-testid'), d.id]); }); });
      return out; }""")


def test_F1_every_button_zoom_step_keeps_the_count_the_18px_rule_and_the_dot_size(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            assert C.provenance(pg) == "137 airports Seats.aero tracked on 2026-09-20 · 0 not plotted"
            sizes = set()
            for step in range(8):
                ms, total, worst = audit(pg)
                z = C.zoom_of(pg)["z"]
                assert total == 137, (step, total)
                assert worst[0] >= 18 - 0.5, (z, worst)
                labelled = [m for m in ms if not m["cluster"] and m["label"]]
                assert (len(labelled) > 0) == (z >= 3), (z, len(labelled))
                sizes |= {round(m["w"], 1) for m in ms if not m["cluster"]}
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(150)
            assert len(sizes) == 1, sizes
            assert C.zoom_of(pg)["z"] == 8
            for _ in range(20):
                pg.click(C.q("map-zoom-out"))
            pg.wait_for_timeout(200)
            assert C.zoom_of(pg) == {"x": 0.0, "y": 0.0, "w": 4000.0, "h": 2080.0, "z": 1.0}
            assert pg.facts["errors"] == []


def test_F2_wheel_dblclick_and_pan_clamp_to_the_world(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            b = C.box(pg, C.q("map-svg"))
            C.wheel(pg, -300, at=(b["x"] + 30, b["y"] + 30))
            v = C.zoom_of(pg)
            assert 1 < v["z"] < 2 and v["x"] >= 0 and v["y"] >= 0
            for _ in range(40):
                C.wheel(pg, -500)
            v = C.zoom_of(pg)
            assert v["z"] == 8 and v["w"] == 500 and v["h"] == 260
            ms, total, worst = audit(pg)
            assert total == 137 and worst[0] >= 17.5
            C.drag(pg, 4000, 4000)
            v = C.zoom_of(pg)
            assert v["x"] == 0 and v["y"] == 0
            for _ in range(16):
                C.drag(pg, -700, -500)
            v = C.zoom_of(pg)
            assert abs(v["x"] - 3500) < 1e-6 and abs(v["y"] - 1820) < 1e-6, v
            for _ in range(40):
                C.wheel(pg, 500)
            assert C.zoom_of(pg)["z"] == 1
            pg.dblclick(C.q("map-svg"), position={"x": 300, "y": 200})
            pg.wait_for_timeout(200)
            assert abs(C.zoom_of(pg)["z"] - 2) < 1e-9
            assert pg.facts["errors"] == []


def test_F3_a_click_hits_the_dot_at_8x_in_a_900px_pane(browser):
    """Plan §8: a wrong CTM shows up as clicks landing beside dots at high
    zoom. Zoom to 8x around SYD by wheel and click its dot's screen centre."""
    with hub_server() as srv:
        with browser.page(srv.port, width=900) as pg:
            C.open_search(pg)
            m = [m for m in C.markers(pg) if m["id"] == "map-hub-SYD"][0]
            for _ in range(30):
                C.wheel(pg, -500, at=(m["x"], m["y"]))
                m = [m for m in C.markers(pg) if m["id"] == "map-hub-SYD"][0]
            assert C.zoom_of(pg)["z"] == 8
            pg.mouse.click(m["x"], m["y"])
            pg.wait_for_timeout(200)
            assert C.values(pg)["from"] == "SYD"


def test_F4_a_picked_hub_is_never_clustered_and_what_that_costs(browser):
    """Coder deviation 6. Its consequence: the picked SFO marker is drawn ON
    TOP of the 2-cluster (SAN/SJC) that forms at the same pixel."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-SFO")
            pg.click(C.q("map-pick-SFO"))
            pg.wait_for_timeout(200)
            ms = {m["id"]: m for m in C.markers(pg)}
            assert "map-hub-SFO" in ms and ms["map-hub-SFO"]["picked"]
            assert "map-cluster-SFO" not in ms
            cl = [m for m in ms.values() if m["cluster"] and m["iata"] in ("SAN", "SJC")]
            assert cl, "the SAN/SJC cluster is gone"
            d = max(abs(cl[0]["x"] - ms["map-hub-SFO"]["x"]), abs(cl[0]["y"] - ms["map-hub-SFO"]["y"]))
            pg.screenshot(path=str(C.SHOTS / "F4-picked-over-cluster.png"), clip={"x": 480, "y": 300, "width": 200, "height": 120})
            assert d >= 18, f"the picked marker and the {cl[0]['id']} cluster are {d:.1f}px apart (18px rule)"


def test_F5_labels_do_not_sit_on_neighbouring_dots(browser):
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            for _ in range(3):
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(150)
            hits = label_collisions(pg)
            pg.screenshot(path=str(C.SHOTS / "F5-labels-z3.png"))
            assert hits == [], f"IATA labels drawn over another marker's dot at z=3.4: {hits}"


def test_F6_metro_pairs_never_separate_at_max_zoom(browser):
    """LHR/LGW (40 km), JFK/LGA/EWR, SFO/SJC/OAK stay one cluster at 8x: at
    z=8 in a 1,028px pane 18px is ~85 km. Recorded as a design limit (the
    list popover is the only way to them)."""
    with hub_server(doc=dense_doc()) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            for _ in range(7):
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(120)
            assert C.zoom_of(pg)["z"] == 8
            clusters = {m["id"]: m["label"] for m in C.markers(pg) if m["cluster"]}
            assert clusters == {}, f"clusters that no zoom can open: {clusters}"


def test_F7_the_route_crosses_the_antimeridian_in_two_segments_and_is_dashed_coral(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-SFO")
            pg.click(C.q("map-pick-SFO"))
            pg.wait_for_timeout(150)
            C.click_marker(pg, "map-hub-SYD")
            d = C.route_d(pg)
            assert d.count("M") == 2, d[:80]
            xs = [float(p.split(",")[0]) for p in d.replace("M", " ").replace("L", " ").split()]
            # no segment spans the map: every consecutive pair within a segment is close
            segs = d.split("M")[1:]
            for seg in segs:
                pts = [tuple(map(float, p.split(","))) for p in seg.split("L")]
                for a, b in zip(pts, pts[1:]):
                    assert abs(a[0] - b[0]) < 400, (a, b)
            st = pg.evaluate("() => { const p = document.querySelector('[data-testid=map-route]'); const cs = getComputedStyle(p); return [cs.stroke, cs.strokeDasharray, cs.strokeWidth, cs.fill]; }")
            assert st[0] == "rgb(255, 138, 101)" and st[1] != "none" and st[3] == "none"
            pg.click(C.q("map-zoom-in"))
            pg.wait_for_timeout(150)
            assert pg.evaluate("() => getComputedStyle(document.querySelector('[data-testid=map-route]')).strokeWidth") == "1.5px"
            pg.screenshot(path=str(C.SHOTS / "F7-sfo-syd.png"))


def test_F8_popover_is_clamped_inside_the_pane_at_the_corners(browser):
    """Zoom to 8x and pan so a cluster sits at each corner; the list stays
    8px inside the pane."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pane = C.box(pg, C.q("map-pane"))
            svg = C.box(pg, C.q("map-svg"))
            # bring the LHR-area cluster to each corner by wheel-zooming about it
            for corner in ("nw", "ne", "sw", "se"):
                pg.click(C.q("map-reset"))
                pg.wait_for_timeout(120)
                target = [m for m in C.markers(pg) if m["id"] == "map-cluster-LHR"][0]
                # 60px in from the right: the zoom controls sit in the top-right 44px
                tx = svg["x"] + (30 if corner[1] == "w" else svg["w"] - 60)
                ty = svg["y"] + (30 if corner[0] == "n" else svg["h"] - 30)
                # zoom about the cluster so it stays put, then pan it to the corner
                for _ in range(6):
                    C.wheel(pg, -300, at=(target["x"], target["y"]))
                    target = [m for m in C.markers(pg) if m["id"] == "map-cluster-LHR"][0]
                C.drag(pg, tx - target["x"], ty - target["y"], start=(target["x"], target["y"]))
                target = [m for m in C.markers(pg) if m["id"] == "map-cluster-LHR"][0]
                pg.mouse.click(target["x"], target["y"])
                pg.wait_for_timeout(200)
                pop = C.box(pg, C.q("map-cluster-list"))
                assert pop, corner
                assert C._r.inside(pop, pane, tol=1.0), (corner, pop, pane)
                assert pop["x"] >= pane["x"] + 7 and pop["right"] <= pane["right"] - 7, (corner, pop)
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(100)


def test_F9_escape_returns_focus_to_the_cluster_and_a_pan_closes_the_list(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            C.click_marker(pg, "map-cluster-JFK")
            assert pg.evaluate("() => document.activeElement.getAttribute('data-testid')") == "map-pick-JFK"
            pg.keyboard.press("ArrowDown")
            assert pg.evaluate("() => document.activeElement.getAttribute('data-testid')") == "map-pick-EWR"
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(100)
            assert C.text(pg, C.q("map-cluster-list")) is None
            assert pg.evaluate("() => document.activeElement.getAttribute('data-testid')") == "map-cluster-JFK"
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(100)
            assert C.text(pg, C.q("map-cluster-list"))
            b = C.box(pg, C.q("map-svg"))
            C.drag(pg, 40, 0, start=(b["x"] + 40, b["y"] + b["h"] - 40))   # not on the popover
            assert C.text(pg, C.q("map-cluster-list")) is None
            pg.evaluate("() => document.querySelector('[data-testid=map-cluster-JFK]').focus()")
            pg.keyboard.press(" ")
            pg.wait_for_timeout(100)
            assert C.text(pg, C.q("map-cluster-list"))
            C.wheel(pg, -100, at=(b["x"] + 40, b["y"] + b["h"] - 40))   # not on the popover
            assert C.text(pg, C.q("map-cluster-list")) is None
            pg.click(C.q("map-reset"))
            C.click_marker(pg, "map-cluster-JFK")
            pg.click(".map-pop .x")
            pg.wait_for_timeout(100)
            assert C.text(pg, C.q("map-cluster-list")) is None
            assert pg.evaluate("() => document.activeElement.getAttribute('data-testid')") == "map-cluster-JFK"


def test_F10_400_hubs_pan_without_long_tasks_and_zoom_keeps_the_node_count(browser):
    random.seed(1)
    extra = random.sample([c for c in COORDS if c not in KNOWN], 400 - len(KNOWN))
    codes = KNOWN + extra
    doc = C.synthetic(codes=codes, routes={c: random.randint(20, 900) for c in codes})
    for h in doc["hubs"]:
        h["searchable"] = True
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            reqs = []
            pg.on("request", lambda r: reqs.append(r.url))
            C.open_search(pg)
            assert C.provenance(pg).startswith("400 airports")
            pg.evaluate("""() => { window.__long = []; new PerformanceObserver((l) => l.getEntries().forEach(e => window.__long.push(e.duration))).observe({type: 'longtask', buffered: true}); }""")
            pg.click(C.q("map-zoom-in"))
            pg.click(C.q("map-zoom-in"))
            pg.wait_for_timeout(300)
            pg.evaluate("() => { window.__long = []; }")
            b = C.box(pg, C.q("map-svg"))
            x, y = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            pg.mouse.move(x, y)
            pg.mouse.down()
            t0, i = time.time(), 0
            while time.time() - t0 < 2:
                i += 1
                pg.mouse.move(x + (i % 40) * 3, y + (i % 30) * 2)
            pg.mouse.up()
            longs = pg.evaluate("() => window.__long")
            assert [d for d in longs if d > 50] == [], longs
            n0 = pg.evaluate("() => document.querySelectorAll('#map-pane *').length")
            for _ in range(20):
                pg.click(C.q("map-zoom-in"))
                pg.click(C.q("map-zoom-out"))
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => document.querySelectorAll('#map-pane *').length") == n0
            # keystrokes that never make a pick do not rebuild the markers
            pg.evaluate("""() => { window.__mut = 0; new MutationObserver((ms) => { window.__mut += ms.length; }).observe(document.querySelector('.map-markers'), {childList: true}); }""")
            pg.type(C.q("search-from"), "xq zq wq", delay=5)
            pg.wait_for_timeout(200)
            assert pg.evaluate("() => window.__mut") == 0
            pg.click(C.q("tab-trips"))
            pg.wait_for_timeout(200)
            pg.click(C.q("tab-search"))
            pg.wait_for_timeout(300)
            assert [u.split("/")[-1] for u in reqs if u.endswith("land.json") or u.endswith("hubs.json")] == ["land.json", "hubs.json"]


def test_F11_a_marker_label_is_clickable(browser):
    """Re-test round: the round-1 probe zoomed about the pane centre and
    clicked SYD's label off-screen (my error, noted in the report). Now: zoom
    by wheel ABOUT SYD so its label stays on screen, then click the text."""
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            m = [m for m in C.markers(pg) if m["id"] == "map-hub-SYD"][0]
            for _ in range(6):
                C.wheel(pg, -300, at=(m["x"], m["y"]))
                m = [m for m in C.markers(pg) if m["id"] == "map-hub-SYD"][0]
            assert C.zoom_of(pg)["z"] >= 3 and m["label"] == "SYD"
            r = pg.evaluate("() => { const b = document.querySelector('[data-testid=map-hub-SYD] text.lbl').getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; }")
            svg = C.box(pg, C.q("map-svg"))
            assert svg["x"] < r[0] and r[0] + r[2] < svg["right"] and svg["y"] < r[1] and r[1] + r[3] < svg["bottom"], (r, svg)
            pg.mouse.click(r[0] + r[2] / 2, r[1] + r[3] / 2)
            pg.wait_for_timeout(200)
            assert C.values(pg)["from"] == "SYD", "clicking the SYD label did not pick SYD"
            pg.fill(C.q("search-from"), "")
            pg.wait_for_timeout(150)
            pg.mouse.click(r[0] + 1, r[1] + 1)          # a corner of the box, between glyphs
            pg.wait_for_timeout(200)
            assert C.values(pg)["from"] == "SYD"
