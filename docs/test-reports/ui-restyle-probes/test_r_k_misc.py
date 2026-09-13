"""
K. THE REST. The top bar's height at 400 (it is sticky: every px is a px of
phone the page never gets back), the bar over a scrolled page, the LIVE
confirm modal at 400, contrast of the new tokens on their grounds, the
disabled mode card's caption.
"""
import pytest

from conftest import SHOTS, box, doc_widths, open_trip, probe_server, q, styles, text

BASE = None  # set by the report run: a worktree of 386b2fc, for "new vs pre-existing"


def lum(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def rgb(s):
    import re
    m = re.match(r"rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)", s)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4) or 1))


def blend(fg, bg, alpha):
    return tuple(round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


@pytest.mark.parametrize("scenario", ["offline_b", "no_key", "no_wallet"])
def test_K1_top_bar_height_at_400(browser, scenario):
    with probe_server(scenario) as srv:
        with browser.page(srv.port, width=400, height=700) as pg:
            pg.click(q("tab-trips"))
            pg.wait_for_timeout(200)
            bar = box(pg, ".topbar")
            rows = pg.evaluate("""() => { const ys = new Set();
                document.querySelectorAll('.topbar-in > *, .bar-right > *').forEach((n) => {
                  const r = n.getBoundingClientRect(); if (r.height) ys.add(Math.round(r.top)); });
                return ys.size; }""")
            pg.screenshot(path=str(SHOTS / f"k1-bar-{scenario}-400.png"))
    # the bar is sticky; more than a quarter of a 700px phone is a real cost
    assert bar["h"] <= 700 * 0.2, (scenario, bar["h"], "rows", rows)


def test_K2_the_bar_stays_above_a_scrolled_table_and_stays_legible(browser):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=1440, height=700) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            r = pg.evaluate("""() => {
              const t = document.querySelector('[data-testid="legs-table"]');
              t.scrollIntoView({ block: 'start' });
              window.scrollBy(0, -20);
              const bar = document.querySelector('.topbar').getBoundingClientRect();
              const hit = document.elementFromPoint(700, bar.height / 2);
              const wm = document.querySelector('[data-testid="wordmark"]');
              return { hitInBar: !!hit.closest('.topbar'), hit: hit.tagName + '.' + hit.className,
                       barBg: getComputedStyle(document.querySelector('.topbar')).backgroundColor,
                       wmColor: getComputedStyle(wm).color, blur: getComputedStyle(document.querySelector('.topbar')).backdropFilter };
            }""")
            pg.screenshot(path=str(SHOTS / "k2-bar-scrolled.png"))
    assert r["hitInBar"], r
    assert r["blur"] == "blur(10px)", r
    assert rgb(r["barBg"])[3] >= 0.85, r


def test_K3_the_live_confirm_modal_at_400(browser):
    with probe_server("live_b") as srv:
        with browser.page(srv.port, width=400, height=700) as pg:
            open_trip(pg, "trip_b_europe", "live", run=False)
            pg.click(q("run-go"))
            pg.wait_for_selector(q("run-confirm"))
            d = doc_widths(pg)
            m = box(pg, ".modal")
            prim = pg.evaluate("""() => Array.from(document.querySelectorAll('.modal .btn-primary'))
                .map((b) => b.textContent)""")
            top = styles(pg, ".modal", ["border-top-width", "border-top-color"])[0]
            pg.screenshot(path=str(SHOTS / "k3-confirm-400.png"))
    assert d["sw"] <= 400, d
    assert m["x"] >= 0 and m["right"] <= 400, m
    assert len(prim) == 1 and prim[0].startswith("Spend up to"), prim
    assert top["border-top-width"] == "2px" and top["border-top-color"] == "rgb(125, 169, 255)", top


def test_K4_contrast_of_the_new_tokens_on_their_grounds(browser):
    """Computed, not from the sheet: text colour vs the first opaque ancestor
    background. WCAG AA body text 4.5:1; UI/large 3:1."""
    with probe_server("no_key") as srv:
        with browser.page(srv.port, width=1440, height=900) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            pg.click(q("leg-row-B3"))
            pg.wait_for_timeout(300)
            samples = pg.evaluate("""() => {
              const pick = {
                'muted on bg (.trow .m)': '.trow .m', 'muted on panel (.hl-value .q)': '.hl-value .q',
                'label (.label)': 'main .label', 'warn .hl-part': '.hl-part', 'route accent': '.route',
                'legid': '.legid', 'key chip': '[data-testid="key-source"]',
                'disabled card caption': '.seg button:disabled .s', 'disabled card title': '.seg button:disabled .t',
                'pressed card title': '.seg button[aria-pressed="true"] .t', 'mode-note': '.mode-note',
                'chip-neutral': '.chip-neutral', 'chip-unknown': '.chip-unknown', 'tag-unv': '.tag-unv',
                'th': 'table.grid th', 'pill offline': '.pill-offline', 'transcript': 'pre.transcript',
                'kv .sub': '.kv .sub', 'search-request row name': '.trow-search .n',
              };
              const out = {};
              for (const [k, sel] of Object.entries(pick)) {
                const n = document.querySelector(sel); if (!n) { out[k] = null; continue; }
                let p = n, bg = null, opacity = 1; const chain = [];
                while (p) { const cs = getComputedStyle(p); opacity *= parseFloat(cs.opacity);
                  const b = cs.backgroundColor; if (b && b !== 'rgba(0, 0, 0, 0)') chain.push(b);
                  if (b && b.startsWith('rgb(')) { bg = b; break; } p = p.parentElement; }
                out[k] = { color: getComputedStyle(n).color, bg: bg, chain: chain, opacity: opacity, text: n.textContent.slice(0, 40) };
              }
              return out; }""")
    report = {}
    low = []
    for k, v in samples.items():
        if not v:
            continue
        ground = rgb(v["bg"])[:3]
        for layer in reversed(v["chain"][:-1]):
            c = rgb(layer)
            ground = blend(c[:3], ground, c[3])
        fg = rgb(v["color"])[:3]
        fg = blend(fg, ground, v["opacity"])
        ratio = round(contrast(fg, ground), 2)
        report[k] = ratio
        if ratio < 4.5:
            low.append((k, ratio, v["text"]))
    # print for the report; fail only for body-text roles below 3:1
    print("CONTRAST", report)
    assert [x for x in low if x[1] < 3.0] == [], low
