"""
K. Round-3 attack (51a4fe1): synthetic hub files built to force every new
path in map.js's label placement - each of the twelve picked-label positions
(right, left, above, below, four diagonals, four of the outer ring), the
reserve-before-move ordering (a displaced cluster whose first free spot is
the reserved label spot), and the last resort (every spot taken: label right,
no hit rect, pointer-events none). Every case runs the full audit: 18px
between all markers, every marker returns itself from elementFromPoint at
its centre, no label over any dot or label, the picked label present, and
the last-resort label never taking a click.

Screen offsets are converted to lat/lon with the projection's inverse at the
zoom-1 scale the pane really has (measured once from the SVG's CTM).
"""
import math
import sys

import pytest

import conftest as C
from conftest import ROOT, hub_server, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from test_ms_f_clusters_zoom import label_collisions  # noqa: E402
from test_ms_i_retest import label_over_label, min_pair  # noqa: E402

W, H, LAT_MIN, LAT_MAX = 4000, 2080, -60.0, 83.0
PICK = ("SFO", 37.618806, -122.375417)

# The positions exactly as map.js declares them: (name, tx, ty, anchor).
POS = [("right", 7, 4, "start"), ("left", -7, 4, "end"), ("above", -10, -10, "start"),
       ("below", -10, 18, "start"), ("above-right", 6, -8, "start"), ("above-left", -6, -8, "end"),
       ("below-right", 6, 16, "start"), ("below-left", -6, 16, "end"), ("right-2", 22, 4, "start"),
       ("left-2", -22, 4, "end"), ("above-2", -10, -24, "start"), ("below-2", -10, 32, "start")]


def box_of(p):
    _, tx, ty, anchor = p
    return [tx - 22, tx + 1, ty - 12, ty + 4] if anchor == "end" else [tx - 1, tx + 22, ty - 12, ty + 4]


def hits(box, x, y, r):
    return box[0] < x + r and box[1] > x - r and box[2] < y + r and box[3] > y - r


def miller(lat):
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


def project(lon, lat):
    top, bot = miller(LAT_MAX), miller(LAT_MIN)
    return (lon + 180) / 360 * W, (top - miller(lat)) / (top - bot) * H


def unproject(X, Y):
    top, bot = miller(LAT_MAX), miller(LAT_MIN)
    m = top - Y / H * (top - bot)
    lat = math.degrees((math.atan(math.exp(m / 1.25)) - math.pi / 4) / 0.4)
    return X / W * 360 - 180, lat


# Candidate blockers: 2-hub clusters (r 10.5) at screen offsets at least 19px
# (Chebyshev) from the pick so they STAY (only markers within 18px are moved),
# and at least 19px from each other so they never merge into one cluster.
CANDIDATES = {}
for _x in (-32, -26, -19, -8, 0, 8, 19, 26, 32):
    for _y in (-32, -26, -19, -8, 0, 8, 19, 26, 32):
        if max(abs(_x), abs(_y)) >= 19:
            CANDIDATES["%d,%d" % (_x, _y)] = (_x, _y)


def first_free(blockers):
    for i, p in enumerate(POS):
        if not any(hits(box_of(p), CANDIDATES[b][0], CANDIDATES[b][1], 10.5) for b in blockers):
            return i
    return None


def apart(names):
    pts = [CANDIDATES[n] for n in names]
    return all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) >= 19 for i, p in enumerate(pts) for q in pts[i + 1:])


def blockers_for(target):
    """The smallest set of non-merging blockers whose first free label
    position is POS[target] (None = every position taken)."""
    import itertools
    names = sorted(CANDIDATES)
    if target is None:
        # every position taken: the ring of eight at 26px plus the four at 19/±8
        chosen = ["26,0", "-26,0", "0,-26", "0,26", "26,-26", "-26,-26", "26,26", "-26,26"]
        assert apart(chosen) and first_free(chosen) is None, (chosen, first_free(chosen))
        return chosen
    for size in range(0, 4):
        for combo in itertools.combinations(names, size):
            if first_free(combo) == target and apart(combo):
                return list(combo)
    raise AssertionError(f"no blocker set forces position {target}")


# right-2 and left-2 cannot be forced by dots alone: any staying marker that
# covers the inner box covers the outer one too. They are reached when the
# inner boxes are taken by the OTHER pick's label (placed first: more routes).
# (second pick offset, its routes, staying clusters, its expected position)
TWO_PICK = {
    8: ((-20, 0), [(-40, -28), (-40, 12)], None),     # the other pick is the last resort
    9: ((-15, -13), [(26, 0), (0, 26), (-40, -24)], 2),  # the other pick goes above
}


def build(s, blockers, extra=(), clusters_px=()):
    """A hub file: the pick, a 2-hub cluster at each blocker's screen offset
    (at zoom 1, scale s px/unit), `clusters_px` (dx, dy) 2-hub clusters, and
    `extra` (code, dx, dy[, routes]) singles."""
    px, py = project(PICK[2], PICK[1])
    hubs = [{"iata": PICK[0], "name": "Pick", "city": "Pick", "country": "US", "lat": PICK[1], "lon": PICK[2],
             "routes": 900, "sources": ["a", "b", "c"], "regions": [], "searchable": True}]
    n = 0

    def hub(code, dx, dy, routes):
        lon, lat = unproject(px + dx / s, py + dy / s)
        return {"iata": code, "name": "N " + code, "city": "City " + code, "country": "US", "lat": lat, "lon": lon,
                "routes": routes, "sources": ["a", "b", "c"], "regions": [], "searchable": True}

    for dx, dy in [CANDIDATES[b] for b in blockers] + list(clusters_px):
        for _ in range(2):
            n += 1
            hubs.append(hub("B%02d" % n, dx, dy, 500 - n))
    for e in extra:
        hubs.append(hub(e[0], e[1], e[2], e[3] if len(e) > 3 else 100))
    doc = C.synthetic(codes=["SFO"])
    doc["hubs"] = sorted(hubs, key=lambda h: h["iata"])
    return doc


@pytest.fixture(scope="module")
def scale(browser):
    with hub_server() as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            return pg.evaluate("() => document.querySelector('[data-testid=map-svg]').getScreenCTM().a")


def audit(pg, tag, expect_total):
    ms = C.markers(pg)
    problems = []
    total = sum(int(m["label"]) if m["cluster"] else 1 for m in ms)
    if total != expect_total:
        problems.append(("count", total, expect_total))
    worst = min_pair(pg)
    if worst and worst[0] < 17.5:
        problems.append(("18px", worst))
    if label_collisions(pg):
        problems.append(("label-over-dot", label_collisions(pg)))
    if label_over_label(pg):
        problems.append(("label-over-label", label_over_label(pg)))
    misses = pg.evaluate("""() => Array.from(document.querySelectorAll('g.mk')).map((g) => {
        const c = g.querySelector('circle.dot, circle.cdot'); const r = c.getBoundingClientRect();
        const x = r.x + r.width / 2, y = r.y + r.height / 2;
        const s = document.querySelector('[data-testid=map-svg]').getBoundingClientRect();
        if (x < s.left + 2 || x > s.right - 2 || y < s.top + 2 || y > s.bottom - 2) return null;   // off the pane
        const e = document.elementFromPoint(x, y); const hit = e && e.closest ? e.closest('g.mk') : null;
        return hit === g ? null : [g.getAttribute('data-testid'), hit ? hit.getAttribute('data-testid') : (e ? e.tagName : null)];
    }).filter(Boolean)""")
    if misses:
        problems.append(("unclickable", misses))
    pick = [m for m in ms if m["picked"]]
    if len(pick) != 1 or pick[0]["label"] != "SFO":
        problems.append(("picked-label", pick))
    assert problems == [], (tag, problems)


def label_info(pg):
    return pg.evaluate("""() => { const g = document.querySelector('[data-testid=map-hub-SFO]'); const t = g.querySelector('text.lbl');
        return { x: Number(t.getAttribute('x')), y: Number(t.getAttribute('y')), anchor: t.getAttribute('text-anchor'),
                 hit: !!g.querySelector('rect.lblhit'), pe: t.getAttribute('pointer-events'),
                 tb: (() => { const b = t.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; })() }; }""")


@pytest.mark.parametrize("target", list(range(len(POS))))
def test_K1_each_of_the_twelve_picked_label_positions_is_reached_and_clean(browser, scale, target):
    if target in TWO_PICK:
        off, clusters, other_pos = TWO_PICK[target]
        blockers = []
        doc = build(scale, [], extra=[("M01", off[0], off[1], 950)], clusters_px=clusters)
        total = 2 + 2 * len(clusters)
    else:
        blockers = blockers_for(target)
        doc = build(scale, blockers)
        total = 1 + 2 * len(blockers)
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            if target in TWO_PICK:
                pg.fill(C.q("search-to"), "M01")
            pg.wait_for_timeout(250)
            if target in TWO_PICK:
                # the second pick's label, placed first, must be where the simulation says
                m = pg.evaluate("""() => { const t = document.querySelector('[data-testid=map-hub-M01] text.lbl');
                    return t && [Number(t.getAttribute('x')), Number(t.getAttribute('y')), t.getAttribute('text-anchor'),
                                 !!t.closest('g').querySelector('rect.lblhit'), t.getAttribute('pointer-events')]; }""")
                if other_pos is None:
                    assert m == [7, 4, "start", False, "none"], m
                else:
                    assert m == [POS[other_pos][1], POS[other_pos][2], POS[other_pos][3], True, None], m
                # the two picks may be closer than 18px to each other (both drawn at
                # their own point, by design); every other pair keeps the rule
                ms = C.markers(pg)
                for i in range(len(ms)):
                    for j in range(i + 1, len(ms)):
                        if ms[i]["picked"] and ms[j]["picked"]:
                            continue
                        d = max(abs(ms[i]["x"] - ms[j]["x"]), abs(ms[i]["y"] - ms[j]["y"]))
                        assert d >= 17.5, (ms[i]["id"], ms[j]["id"], d)
                assert label_over_label(pg) == []
                assert [c for c in label_collisions(pg) if c[0] != "map-hub-M01"] == [], label_collisions(pg)
            else:
                audit(pg, (POS[target][0], blockers), total)
            li = label_info(pg)
            name, tx, ty, anchor = POS[target]
            assert (li["x"], li["y"], li["anchor"], li["hit"], li["pe"]) == (tx, ty, anchor, True, None), (name, li)
            # the label's own hit rect picks (a no-op pick of From's airport keeps the value)
            pg.mouse.click(li["tb"][0], li["tb"][1])
            pg.wait_for_timeout(150)
            # (with both fields filled the click on From's airport replaces To: D10)
            assert C.values(pg) == {"from": "SFO", "to": "SFO" if target in TWO_PICK else ""}
            assert C.text(pg, C.q("map-cluster-list")) is None, "the label click reached a cluster"
            pg.screenshot(path=str(C.SHOTS / f"K1-{target:02d}-{name}.png"),
                          clip={"x": li["tb"][0] - 70, "y": li["tb"][1] - 50, "width": 140, "height": 100})
            assert pg.facts["errors"] == []


def test_K2_last_resort_label_right_without_hit_rect_never_steals_a_click(browser, scale):
    blockers = blockers_for(None)
    doc = build(scale, blockers)
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            ms = C.markers(pg)
            # everything but the picked label is clean
            problems = []
            worst = min_pair(pg)
            if worst[0] < 17.5:
                problems.append(("18px", worst))
            misses = pg.evaluate("""() => Array.from(document.querySelectorAll('g.mk')).map((g) => {
                const c = g.querySelector('circle.dot, circle.cdot'); const r = c.getBoundingClientRect();
                const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); const hit = e && e.closest ? e.closest('g.mk') : null;
                return hit === g ? null : [g.getAttribute('data-testid'), hit ? hit.getAttribute('data-testid') : (e ? e.tagName : null)];
            }).filter(Boolean)""")
            if misses:
                problems.append(("unclickable", misses))
            if label_over_label(pg):
                problems.append(("label-over-label", label_over_label(pg)))
            assert problems == [], problems
            li = label_info(pg)
            assert (li["x"], li["y"], li["anchor"], li["hit"], li["pe"]) == (7, 4, "start", False, "none"), li
            assert [m for m in ms if m["picked"]][0]["label"] == "SFO"
            # the label sits over the east cluster: a click on the text opens THAT cluster's list
            under = pg.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); const g = e.closest('g.mk'); return g ? g.getAttribute('data-testid') : e.tagName; }", li["tb"])
            assert under.startswith("map-cluster-"), under
            pg.mouse.click(li["tb"][0], li["tb"][1])
            pg.wait_for_timeout(200)
            assert C.text(pg, C.q("map-cluster-list")) is not None, "the last-resort label stole the click"
            assert C.values(pg) == {"from": "SFO", "to": ""}
            pg.keyboard.press("Escape")
            pg.screenshot(path=str(C.SHOTS / "K2-last-resort.png"), clip={"x": li["tb"][0] - 70, "y": li["tb"][1] - 50, "width": 140, "height": 100})


def test_K3_reserve_before_move_a_displaced_cluster_never_lands_on_the_label_spot(browser, scale):
    """A 2-hub cluster 10px east of the pick must move; its first candidate
    spot (east, 18.6px) is exactly under the reserved 'right' label. It must
    go elsewhere and the label must stay right with its hit rect."""
    doc = build(scale, [], extra=[("M01", 10, 0), ("M02", 10, 0)])
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            audit(pg, "reserve", 3)
            li = label_info(pg)
            assert (li["x"], li["anchor"], li["hit"]) == (7, "start", True), li
            ms = {m["id"]: m for m in C.markers(pg)}
            sfo, cl = ms["map-hub-SFO"], [m for m in ms.values() if m["cluster"]][0]
            dx, dy = cl["x"] - sfo["x"], cl["y"] - sfo["y"]
            assert not (dx > 0 and abs(dy) < 8), f"the cluster landed on the reserved right spot: {dx:.1f},{dy:.1f}"
            assert max(abs(dx), abs(dy)) >= 17.5
            pg.mouse.click(cl["x"], cl["y"])
            pg.wait_for_timeout(200)
            assert (C.text(pg, C.q("map-cluster-list")) or "").startswith("2 airports near") or "M01" in (C.text(pg, C.q("map-cluster-list")) or "")
            pg.screenshot(path=str(C.SHOTS / "K3-reserve.png"), clip={"x": sfo["x"] - 70, "y": sfo["y"] - 50, "width": 140, "height": 100})


def test_K4_a_moved_single_and_the_pick_share_nothing(browser, scale):
    """N1a's case on purpose: one single hub 6px from the pick, one 2-cluster
    12px the other side; both must move to free 18px spots and be clickable."""
    doc = build(scale, [], extra=[("M01", 6, -4), ("M02", -12, 3), ("M03", -12, 3)])
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            pg.wait_for_timeout(250)
            audit(pg, "moved-single", 4)
            ms = {m["id"]: m for m in C.markers(pg)}
            assert "map-hub-M01" in ms and any(m["cluster"] for m in ms.values())
            pg.mouse.click(ms["map-hub-M01"]["x"], ms["map-hub-M01"]["y"])
            pg.wait_for_timeout(200)
            assert C.values(pg) == {"from": "SFO", "to": "M01"}
            pg.fill(C.q("search-to"), "")          # M01 is a neighbour again, not a pick
            pg.wait_for_timeout(250)
            for z in range(7):
                pg.click(C.q("map-zoom-in"))
                pg.wait_for_timeout(400)      # recluster is rAF-throttled
                audit(pg, ("moved-single", round(C.zoom_of(pg)["z"], 2)), 4)


def test_K5_two_picks_each_get_a_label_that_avoids_the_other(browser, scale):
    doc = build(scale, ["-26,0"], extra=[("M01", 0, 26)])
    with hub_server(doc=doc) as srv:
        with browser.page(srv.port) as pg:
            C.open_search(pg)
            pg.fill(C.q("search-from"), "SFO")
            pg.fill(C.q("search-to"), "M01")
            pg.wait_for_timeout(250)
            ms = C.markers(pg)
            picks = [m for m in ms if m["picked"]]
            assert sorted(m["label"] for m in picks) == ["M01", "SFO"]
            assert label_collisions(pg) == [] and label_over_label(pg) == []
            assert pg.evaluate("() => document.querySelectorAll('.map-picks rect.lblhit').length") == 2
            assert min_pair(pg)[0] >= 17.5
            assert C.route_d(pg)
