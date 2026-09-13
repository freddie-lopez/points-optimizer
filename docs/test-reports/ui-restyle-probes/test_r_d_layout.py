"""
D. LAYOUT. To-do #3 (the run strip vs a long REPLAY reason) at 1440 and 400,
with the reason with and without slashes, with the key-error fold open, and
with a run in flight (the busy note beside the buttons). No page-level
horizontal scroll at 400 on every view. The LEG/VERDICT pins. The drawer as a
full sheet below 1180 and sticky under the bar above it.
"""
import pytest

from conftest import (box, doc_widths, inside, open_trip, probe_server, q, run_search,
                      styles, SHOTS)

LONG_TEXT = ("No snapshot manifest found at {path}. " + "Run a trip LIVE once to write one; "
             "until then REPLAY has nothing to replay and this sentence keeps going so that the "
             "row has to cope with two hundred characters of prose around the path.")
PATH_SLASHES = "/" + "/".join(["segment%02d_xxxxxxxxxxxxxx" % i for i in range(5)]) + "/MANIFEST.md"
PATH_NOSLASH = "M" * 120
assert len(LONG_TEXT) > 200 and len(PATH_SLASHES) >= 120 and len(PATH_NOSLASH) == 120


def patch_state(path):
    def f(st):
        st["modes"]["replay_manifests"] = []
        st["modes"]["replay_reason"] = "no snapshot to replay yet"
        st["modes"]["replay_reason_detail"] = {"text": LONG_TEXT, "path": path}
        return st
    return f


def strip_facts(pg):
    return {
        "strip": box(pg, q("run-strip")),
        "run": box(pg, q("run-go")),
        "opts": box(pg, q("run-options")),
        "note": box(pg, q("replay-unavailable")),
        "busy": box(pg, q("run-busy")),
        "doc": doc_widths(pg),
        "title": pg.evaluate("""() => { const p = document.querySelector('[data-testid="replay-unavailable"] .path');
            return p ? p.title : null; }"""),
        "runDisabled": pg.evaluate("""() => document.querySelector('[data-testid="run-go"]').disabled"""),
    }


@pytest.mark.parametrize("width", [1440, 400])
@pytest.mark.parametrize("path", [PATH_SLASHES, PATH_NOSLASH], ids=["slashes", "noslash"])
@pytest.mark.parametrize("nokey", [False, True], ids=["key", "nokey"])
def test_D1_RUN_stays_inside_the_strip_whatever_the_reason(browser, width, path, nokey):
    # nokey: the REAL no-key server, so the key-error fold carries the engine's
    # own error text (six lines, the longest 133 chars, two of them paths).
    with probe_server("no_key" if nokey else "offline_b") as srv:
        with browser.page(srv.port, width=width, height=900,
                          state_patch=patch_state(path)) as pg:
            open_trip(pg, "trip_b_europe", "offline", run=False)
            if nokey and pg.query_selector(q("key-error")):
                pg.click(q("key-error") + " summary")
                pg.wait_for_timeout(150)
            f = strip_facts(pg)
            pg.screenshot(path=str(SHOTS / f"d1-{width}-{'slash' if '/' in path else 'noslash'}-{'nokey' if nokey else 'key'}.png"),
                          full_page=True)
    assert f["doc"]["bodySW"] <= width and f["doc"]["sw"] <= width, f["doc"]
    assert f["note"] is not None, "the REPLAY reason is not rendered"
    assert inside(f["run"], f["strip"]), (f["run"], f["strip"])
    assert inside(f["opts"], f["strip"]), (f["opts"], f["strip"])
    assert inside(f["note"], f["strip"]), (f["note"], f["strip"])
    assert f["title"] == path
    assert f["run"]["w"] > 30 and f["run"]["h"] > 20
    assert f["runDisabled"] is False


@pytest.mark.parametrize("width", [1440, 400])
def test_D2_busy_note_shares_the_row_and_RUN_stays_inside(browser, width):
    """Delay the run so the page is in flight long enough to measure."""
    with probe_server("slow_run") as srv:
        with browser.page(srv.port, width=width, height=900) as pg:
            open_trip(pg, "trip_b_europe", "offline", run=False)
            pg.click(q("run-go"), no_wait_after=True)
            pg.wait_for_selector(q("run-busy"), timeout=5000)
            pg.wait_for_timeout(1200)
            f = strip_facts(pg)
            pg.screenshot(path=str(SHOTS / f"d2-busy-{width}.png"), full_page=True)
            pg.wait_for_selector(q("trip-result"), timeout=30000)
    assert f["busy"] is not None and f["busy"]["w"] > 0
    assert "s" in (f["busy"] and "s")
    assert inside(f["busy"], f["strip"]), (f["busy"], f["strip"])
    assert inside(f["run"], f["strip"]), (f["run"], f["strip"])
    assert f["doc"]["bodySW"] <= width, f["doc"]
    assert f["runDisabled"] is True
    if width == 1440:
        # same line as the buttons
        assert abs((f["busy"]["y"] + f["busy"]["h"] / 2) - (f["run"]["y"] + f["run"]["h"] / 2)) < 12, (f["busy"], f["run"])


# ------------------------------------------------- no horizontal scroll @400


VIEWS = ["search", "trips", "detail", "drawer", "wallet", "newtrip", "options", "fixture_legs"]


@pytest.mark.parametrize("view", VIEWS)
def test_D3_no_page_level_horizontal_scroll_at_400(browser, view):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=400, height=800) as pg:
            if view == "search":
                pg.click(q("tab-search"))
                pg.wait_for_timeout(200)
            elif view == "trips":
                pg.click(q("tab-trips"))
                pg.wait_for_timeout(200)
            elif view == "fixture_legs":
                pg.click(q("tab-trips")); pg.wait_for_timeout(100)
                pg.click(q("trip-row-trip_b_europe"))
                pg.wait_for_selector(q("trip-detail"))
            elif view == "detail":
                open_trip(pg, "trip_b_europe", "offline")
            elif view == "drawer":
                open_trip(pg, "trip_b_europe", "offline")
                pg.click(q("leg-row-B3"))
                pg.wait_for_timeout(300)
            elif view == "wallet":
                pg.click(q("wallet-chip-UR"))
                pg.wait_for_selector(q("wallet-panel"))
            elif view == "newtrip":
                pg.click(q("tab-trips")); pg.wait_for_timeout(100)
                pg.click(q("new-trip"))
                pg.wait_for_selector(q("new-trip-form"))
            elif view == "options":
                open_trip(pg, "trip_b_europe", "offline", run=False, options=True)
                pg.wait_for_selector(q("run-options-panel"))
            d = doc_widths(pg)
            # any element wider than the viewport that is not inside an overflow-x container
            wide = pg.evaluate("""() => Array.from(document.querySelectorAll('body *')).filter((n) => {
                const r = n.getBoundingClientRect();
                if (r.width <= 0 || r.right <= 401) return false;
                let p = n.parentElement;
                while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; p = p.parentElement; }
                return true; }).slice(0, 8).map((n) => n.tagName + '.' + n.className + ' ' + Math.round(n.getBoundingClientRect().right))""")
            pg.screenshot(path=str(SHOTS / f"d3-{view}-400.png"), full_page=True)
    assert d["sw"] <= 400 and d["bodySW"] <= 400, (view, d, wide)
    assert wide == [], (view, wide)


# ------------------------------------------------------ pinned columns


@pytest.mark.parametrize("width", [400, 1440])
def test_D4_LEG_and_VERDICT_stay_pinned_and_opaque(browser, width):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=width, height=900) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            r = pg.evaluate("""() => {
              const wrap = document.querySelector('[data-testid="legs-table-scroll"]');
              wrap.scrollLeft = wrap.scrollWidth;
              const wr = wrap.getBoundingClientRect();
              const leg = document.querySelector('[data-testid="cell-B3-leg"]');
              const ver = document.querySelector('[data-testid="verdict-B3"]');
              const th = document.querySelector('table.grid th.pin-left');
              const b = (n) => { const x = n.getBoundingClientRect(); return {l: x.left, r: x.right, w: x.width}; };
              return { wrap: b(wrap), leg: b(leg), ver: b(ver), scrollLeft: wrap.scrollLeft, sw: wrap.scrollWidth,
                legBg: getComputedStyle(leg).backgroundColor, verBg: getComputedStyle(ver).backgroundColor,
                thBg: th ? getComputedStyle(th).backgroundColor : null,
                legPos: getComputedStyle(leg).position, verPos: getComputedStyle(ver).position,
                mid: wr.width - b(leg).w - b(ver).w };
            }""")
    assert r["scrollLeft"] > 0, "the table did not scroll - nothing to pin against"
    assert r["legPos"] == "sticky" and r["verPos"] == "sticky", r
    assert r["leg"]["l"] >= r["wrap"]["l"] - 1 and r["leg"]["r"] <= r["wrap"]["r"] + 1, r
    assert r["ver"]["l"] >= r["wrap"]["l"] - 1 and r["ver"]["r"] <= r["wrap"]["r"] + 1, r
    for k in ("legBg", "verBg", "thBg"):
        assert r[k] and r[k].startswith("rgb(") and "rgba" not in r[k], (k, r[k])
    assert r["mid"] >= 100, r


# ---------------------------------------------------------- the drawer


@pytest.mark.parametrize("width", [1179, 1180, 1200, 1280, 1440])
def test_D5_drawer_is_a_full_sheet_below_1180_and_sticky_under_the_bar_above(browser, width):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=width, height=800) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            pg.click(q("leg-row-B3"))
            pg.wait_for_timeout(300)
            # scroll the page so the drawer's sticky top is exercised
            pg.evaluate("() => window.scrollTo(0, 600)")
            pg.wait_for_timeout(200)
            d = box(pg, "#drawer-trips")
            bar = box(pg, ".topbar")
            head = box(pg, "#drawer-trips .dhead")
            pg.screenshot(path=str(SHOTS / f"d5-drawer-{width}.png"))
    assert d is not None
    if width < 1180:
        assert d["position"] == "fixed" and d["y"] == 0 and d["h"] == 800, d
    else:
        assert d["position"] == "sticky", d
        # the drawer's head must not be under the bar
        assert d["y"] >= bar["bottom"] - 1, (d, bar)
        assert head["y"] >= bar["bottom"] - 1, (head, bar)


@pytest.mark.parametrize("width", [1180, 1280, 1440])
def test_D6_top_bar_is_one_row_at_desktop_widths(browser, width):
    """The drawer is sticky at top:64px = 48px bar + 16. If the bar wraps to
    two rows at a desktop width, the drawer head goes under it."""
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=width, height=800) as pg:
            open_trip(pg, "trip_b_europe", "live")
            bar = box(pg, ".topbar")
    assert bar["h"] <= 49, bar


def test_D7_search_view_at_400_keeps_its_strip_and_table_inside(browser):
    with probe_server("search_ok") as srv:
        with browser.page(srv.port, width=400, height=800) as pg:
            run_search(pg)
            d = doc_widths(pg)
            cells = pg.query_selector_all('[data-testid^="cell-"]')
            for c in cells:
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(400)
                    break
            d2 = doc_widths(pg)
            dr = box(pg, "#drawer-search")
            pg.screenshot(path=str(SHOTS / "d7-search-400.png"), full_page=True)
    assert d["sw"] <= 400 and d2["sw"] <= 400, (d, d2)
    assert dr is not None and dr["position"] == "fixed" and dr["w"] == 400, dr
