"""
L. RE-TEST at 1f535aa (fix round 1). Each fix attacked on its own:
  D1 - the key-error fold with the LONGEST error text the engine can build
       (two ~270-char single-segment paths), fold open, at 400 AND 360.
  K1 - the bar at 400 and 360: height, nothing clipped, every focusable item
       in the bar reachable by Tab and inside the viewport.
  K4 - the disabled card still reads as disabled with colour removed.
  D5 - 1179 / 1180 / 1181.
"""
import io

import pytest
from PIL import Image

from conftest import SHOTS, box, doc_widths, inside, open_trip, probe_server, q, styles


@pytest.mark.parametrize("width", [400, 360])
def test_L1_longest_key_error_text_fold_open(browser, width):
    with probe_server("no_key_longpath") as srv:
        with browser.page(srv.port, width=width, height=800) as pg:
            open_trip(pg, "trip_b_europe", "offline", run=False)
            pg.click(q("key-error") + " summary")
            pg.wait_for_timeout(200)
            longest = pg.evaluate("""() => Math.max(...document.querySelector('[data-testid="key-error"] pre')
                .textContent.split('\\n').map((l) => l.length))""")
            d = doc_widths(pg)
            strip, run, opts = box(pg, q("run-strip")), box(pg, q("run-go")), box(pg, q("run-options"))
            pre = pg.evaluate("""() => { const p = document.querySelector('[data-testid="key-error"] pre');
                return { sw: p.scrollWidth, cw: p.clientWidth, right: p.getBoundingClientRect().right,
                         text: p.textContent }; }""")
            pg.screenshot(path=str(SHOTS / f"l1-longkey-{width}.png"), full_page=True)
    assert longest >= 280, longest
    assert d["sw"] <= width and d["bodySW"] <= width, d
    assert strip["right"] <= width and inside(run, strip) and inside(opts, strip), (strip, run, opts)
    assert run["right"] <= width and run["x"] >= 0
    # the text is all still there, scrolling inside its own box
    assert pre["sw"] > pre["cw"] and pre["right"] <= width, pre
    assert "RELOCATED by POINTS_OPTIMIZER_ENV_FILE" in pre["text"] and "e" * 250 in pre["text"]


@pytest.mark.parametrize("width", [400, 360])
@pytest.mark.parametrize("scenario", ["offline_b", "no_key", "no_wallet"])
def test_L2_bar_at_phone_widths(browser, scenario, width):
    with probe_server(scenario) as srv:
        with browser.page(srv.port, width=width, height=700) as pg:
            pg.click(q("tab-trips"))
            pg.wait_for_timeout(200)
            bar = box(pg, ".topbar")
            items = pg.evaluate("""() => Array.from(document.querySelectorAll('.topbar *')).filter((n) => {
                const cs = getComputedStyle(n); return cs.display !== 'none' && n.getBoundingClientRect().width > 0; })
              .map((n) => { const r = n.getBoundingClientRect(); const cs = getComputedStyle(n);
                return { id: n.getAttribute('data-testid') || n.className || n.tagName, l: r.left, r: r.right,
                         t: r.top, b: r.bottom, clipped: cs.whiteSpace === 'nowrap' && n.scrollWidth > n.clientWidth + 1,
                         text: (n.textContent || '').slice(0, 30) }; })""")
            # Tab through the bar from the top of the document
            # a fresh load: Chromium keeps a focus-navigation start point after a click
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_selector(q("wordmark"))
            pg.wait_for_timeout(400)
            reached = []
            for _ in range(12):
                pg.keyboard.press("Tab")
                a = pg.evaluate("""() => { const a = document.activeElement; if (!a) return null;
                    const r = a.getBoundingClientRect(); return { id: a.getAttribute('data-testid') || a.className,
                      inBar: !!a.closest('.topbar'), l: r.left, r: r.right, t: r.top, b: r.bottom }; }""")
                if not a or not a["inBar"]:
                    if reached:
                        break
                    continue
                reached.append(a)
            focusables = pg.evaluate("""() => Array.from(document.querySelectorAll('.topbar button, .topbar a, .topbar [tabindex]'))
                .filter((n) => !n.disabled && n.tabIndex >= 0).map((n) => n.getAttribute('data-testid') || n.className)""")
            pg.screenshot(path=str(SHOTS / f"l2-bar-{scenario}-{width}.png"))
    assert bar["h"] <= 106, (scenario, width, bar["h"])
    assert [i for i in items if i["clipped"]] == [], [i for i in items if i["clipped"]]
    out = [i for i in items if i["l"] < -0.5 or i["r"] > width + 0.5]
    assert out == [], out
    assert [r["id"] for r in reached] == focusables, (reached, focusables)
    assert all(0 <= r["l"] and r["r"] <= width and 0 <= r["t"] and r["b"] <= bar["h"] + 1 for r in reached), reached


def test_L3_disabled_card_reads_as_disabled_without_colour(browser):
    test_L3 = test_L3_disabled_card_reads_as_disabled_without_colour
    with probe_server("no_key") as srv:
        ctx = browser.browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1:") else r.abort())
        pg = ctx.new_page()
        pg.goto(srv.base, wait_until="domcontentloaded")
        pg.wait_for_selector(q("wordmark"))
        pg.wait_for_timeout(300)
        open_trip(pg, "trip_b_europe", "offline", run=False)
        off = pg.query_selector(q("mode-live"))      # disabled
        on = pg.query_selector(q("mode-offline"))    # pressed
        rep = pg.query_selector(q("mode-replay"))    # also disabled here (no manifest)
        a = Image.open(io.BytesIO(off.screenshot(type="png"))).convert("L")
        b = Image.open(io.BytesIO(on.screenshot(type="png"))).convert("L")
        st = styles(pg, ".seg button", ["opacity", "cursor"])
        tcol = styles(pg, ".seg button .t", ["color"])
        ctx.close()

    def title_lum(im):
        # the title sits in the top-left; take the brightest pixel in that band
        return max(im.getpixel((x, y)) for x in range(20, 120) for y in range(20, 60))

    def edge(im):
        return sum(im.getpixel((1, y)) for y in range(im.height)) / im.height

    assert title_lum(a) < title_lum(b) - 40, (title_lum(a), title_lum(b))
    assert edge(a) < edge(b) - 15, (edge(a), edge(b))
    dis = [s for s in st if s["text"].startswith("LIVE") or s["text"].startswith("REPLAY")]
    assert all(s["opacity"] == "0.8" for s in dis), dis
    assert [c["color"] for c in tcol if c["text"] == "LIVE"] == ["rgb(140, 152, 168)"], tcol
    assert [c["color"] for c in tcol if c["text"] == "OFFLINE"] == ["rgb(125, 169, 255)"], tcol
    test_L3.cursors = [s["cursor"] for s in dis]


def test_L3b_a_disabled_card_does_not_offer_a_pointer_cursor(browser):
    """`.seg button { cursor: pointer }` outranks `button:disabled { cursor:
    not-allowed }` (same specificity, later rule). Pre-existing at 386b2fc."""
    with probe_server("no_key") as srv:
        with browser.page(srv.port, width=1440, height=900) as pg:
            open_trip(pg, "trip_b_europe", "offline", run=False)
            st = styles(pg, ".seg button:disabled", ["cursor"])
    assert st and all(s["cursor"] == "not-allowed" for s in st), st


@pytest.mark.parametrize("width", [1179, 1180, 1181])
def test_L4_drawer_docks_at_1180_exactly(browser, width):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=width, height=800) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            pg.click(q("leg-row-B3"))
            pg.wait_for_timeout(300)
            pg.evaluate("() => window.scrollTo(0, 600)")
            pg.wait_for_timeout(200)
            d, bar, head = box(pg, "#drawer-trips"), box(pg, ".topbar"), box(pg, "#drawer-trips .dhead")
            cols = styles(pg, ".trips-grid", ["grid-template-columns"])[0]["grid-template-columns"]
            dw = doc_widths(pg)
    assert dw["sw"] <= width, dw
    if width < 1180:
        assert d["position"] == "fixed" and d["y"] == 0 and d["h"] == 800, d
        assert len(cols.split()) == 2, cols
    else:
        assert d["position"] == "sticky" and d["y"] >= bar["bottom"] - 1 and head["y"] >= bar["bottom"] - 1, (d, bar)
        assert len(cols.split()) == 3, cols
        assert d["right"] <= width, d
