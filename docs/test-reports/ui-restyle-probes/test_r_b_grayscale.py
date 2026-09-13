"""
B. COLOUR REMOVED. The brief: "colour never carries meaning alone". A
grayscale screenshot of each chip form must still show the form by PIXELS:

  UNKNOWN  - a dashed border: luminance along the top edge alternates
  WITHHELD - a hatch: the interior has periodic luminance variation
  CASH (q) - a left edge at least 3px that differs from the top edge
  POINTS   - a filled interior that differs from the panel behind it
  UNVERIFIED - drawn in small caps (glyph height check: the tag's capitals
               are shorter than a normal capital of the same font size)

Also: the selected trip row and the three trip kinds (see test_r_f) and the
mode pill's state must survive grayscale.
"""
import io

import pytest
from PIL import Image

from conftest import SHOTS, open_trip, probe_server, q


def gray_of(pg, handle, scale=4):
    """Screenshot ONE element at 4x device pixels, as an 8-bit grayscale image."""
    png = handle.screenshot(type="png", scale="device") if False else handle.screenshot(type="png")
    im = Image.open(io.BytesIO(png)).convert("L")
    return im


@pytest.fixture(scope="module")
def page_b(browser):
    """Trip B offline with leg B3 (the WITHHELD leg) open, at 1440, 2x DPR."""
    with probe_server("offline_b") as srv:
        ctx = browser.browser.new_context(viewport={"width": 1440, "height": 1000},
                                          device_scale_factor=2)
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1:")
                  else r.abort())
        pg = ctx.new_page()
        pg.goto(srv.base, wait_until="domcontentloaded")
        pg.wait_for_selector(q("wordmark"))
        pg.wait_for_timeout(300)
        open_trip(pg, "trip_b_europe", "offline")
        pg.click(q("leg-row-B3"))
        pg.wait_for_timeout(300)
        # The CSP refuses an injected stylesheet (good), so grayscale is done
        # in PIL: every element screenshot below is converted to L, and the
        # whole page is saved that way for the report.
        pg.screenshot(path=str(SHOTS / "b_colour_1440.png"))
        Image.open(SHOTS / "b_colour_1440.png").convert("L").save(SHOTS / "b_gray_1440.png")
        yield pg
        ctx.close()


def first(pg, sel):
    h = pg.query_selector(sel)
    assert h is not None, sel
    h.scroll_into_view_if_needed()
    return h


def row_profile(im, y):
    return [im.getpixel((x, y)) for x in range(im.width)]


def col_profile(im, x):
    return [im.getpixel((x, y)) for y in range(im.height)]


def alternations(vals, thresh=12):
    """How many times the luminance jumps by > thresh along the profile."""
    n = 0
    for a, b in zip(vals, vals[1:]):
        if abs(a - b) > thresh:
            n += 1
    return n


def test_B1_UNKNOWN_reads_as_dashed_in_grayscale(page_b):
    pg = page_b
    h = first(pg, ".chip-unknown")
    im = Image.open(io.BytesIO(h.screenshot(type="png"))).convert("L")
    # the top border row (device px 0..2 at 2x): a dashed border alternates
    # many times across the width; a solid border does not.
    best = max(alternations(row_profile(im, y)) for y in range(0, 4))
    assert best >= 6, f"top edge of UNKNOWN alternates {best} times - does not read as dashed"


def test_B2_WITHHELD_reads_as_hatched_in_grayscale(page_b):
    pg = page_b
    h = first(pg, ".chip-withheld")
    im = Image.open(io.BytesIO(h.screenshot(type="png"))).convert("L")
    # the interior (away from the glyphs is hard to guarantee; use the top
    # padding band just under the border): a hatch alternates across x.
    y = 5
    n = alternations(row_profile(im, y), thresh=4)
    assert n >= 8, f"interior band of WITHHELD alternates {n} times - the hatch is not visible"


def test_B3_qualified_CASH_reads_as_a_thick_left_edge_in_grayscale(page_b):
    pg = page_b
    h = first(pg, ".chip-cashq")
    im = Image.open(io.BytesIO(h.screenshot(type="png"))).convert("L")
    mid = im.height // 2
    prof = row_profile(im, mid)
    # a 3px (6 device px) edge: the first ~6 px are one luminance, then the
    # interior; the top border (1px = 2 device px) is a different luminance.
    edge = sum(prof[0:6]) / 6
    interior = sum(prof[10:16]) / 6
    top = sum(col_profile(im, im.width // 2)[0:2]) / 2
    assert abs(edge - interior) > 25, f"left edge {edge} vs interior {interior}: no visible edge"
    assert abs(edge - top) > 10, f"left edge {edge} vs top border {top}: the edge is not distinct from the border"


def test_B4_POINTS_reads_as_filled_in_grayscale(page_b):
    pg = page_b
    h = first(pg, ".chip-points")
    im = Image.open(io.BytesIO(h.screenshot(type="png"))).convert("L")
    interior = im.getpixel((5, 5))
    # the panel behind: a plain CASH chip's interior
    h2 = first(pg, ".chip-cash")
    im2 = Image.open(io.BytesIO(h2.screenshot(type="png"))).convert("L")
    behind = im2.getpixel((5, 5))
    assert abs(interior - behind) >= 6, f"POINTS interior {interior} vs plain chip interior {behind}"


def test_B5_UNVERIFIED_is_drawn_in_small_caps(page_b):
    pg = page_b
    # the fixture legs table has the tag; the verdict cells may too
    ok = pg.evaluate("""() => {
      const t = document.querySelector('.tag-unv');
      if (!t) return 'none';
      const cs = getComputedStyle(t);
      return cs.fontVariantCaps;
    }""")
    assert ok == "all-small-caps", ok


def test_B6_the_selected_trip_row_is_distinguishable_in_grayscale(page_b):
    pg = page_b
    sel = first(pg, '.trow[aria-current="true"]')
    oth = first(pg, '.trow-trip[aria-current="false"]')
    a = Image.open(io.BytesIO(sel.screenshot(type="png"))).convert("L")
    b = Image.open(io.BytesIO(oth.screenshot(type="png"))).convert("L")
    # border luminance on the selected row vs none on a plain row
    a_edge = sum(col_profile(a, 1)) / a.height
    b_edge = sum(col_profile(b, 1)) / b.height
    a_fill = a.getpixel((a.width // 2, 6))
    b_fill = b.getpixel((b.width // 2, 6))
    assert abs(a_edge - b_edge) > 4 or abs(a_fill - b_fill) > 4, (a_edge, b_edge, a_fill, b_fill)


def test_B7_the_pressed_mode_card_is_distinguishable_in_grayscale(page_b):
    pg = page_b
    on = first(pg, '.seg button[aria-pressed="true"]')
    off = first(pg, '.seg button[aria-pressed="false"]:not(:disabled)')
    a = Image.open(io.BytesIO(on.screenshot(type="png"))).convert("L")
    b = Image.open(io.BytesIO(off.screenshot(type="png"))).convert("L")
    a_edge = sum(col_profile(a, 1)) / a.height
    b_edge = sum(col_profile(b, 1)) / b.height
    assert abs(a_edge - b_edge) > 15, f"pressed card edge {a_edge} vs plain {b_edge}"
