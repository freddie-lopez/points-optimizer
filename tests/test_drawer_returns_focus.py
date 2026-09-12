"""
FINDING R2-4: closing a drawer gives focus back to the element it was opened
from.

Closing re-renders the table, which destroys the element focus was on, so focus
fell to `<body>`: a keyboard reader who opened a leg in the middle of the table
lost their place and needed eighteen Tab presses to get back to the row they
were reading. Both drawers now record the testid they were opened from and put
focus back on the rebuilt element.

This is a static check of the rules the page implements. The DOM behaviour
itself is verified in Chromium (the Tester's `keyboard.js` probe `K23`, and the
scratch `esc.js` run recorded in the fix report): Esc from row B4 leaves focus
on `leg-row-B4`, and the close button from B2 leaves it on `leg-row-B2`.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "ui" / "static"
APP = (STATIC / "app.js").read_text()


def test_both_drawers_close_through_the_helper_that_restores_focus():
    assert "function closeDrawer(" in APP
    assert "function closeLegDrawer(" in APP and "function closeSearchDrawer(" in APP
    # No close path may clear the selection and re-render by hand: that is the
    # shape that dropped focus.
    stray = re.findall(r"S\.legSel = null; renderTrips\(\)", APP)
    assert stray == [], stray
    stray = re.findall(r"(?:q|S\.search)\.sel = null; renderSearch\(\)", APP)
    assert stray == [], stray


def test_escape_goes_through_the_same_helper_as_the_close_button():
    esc = APP[APP.index('if (e.key !== "Escape")'):]
    esc = esc[:esc.index("});")]
    assert "closeSearchDrawer()" in esc and "closeLegDrawer()" in esc
    assert 'btn("x", "×", closeLegDrawer)' in APP
    assert 'btn("x", "×", closeSearchDrawer)' in APP


def test_opening_records_where_it_came_from():
    assert APP.count("openedFrom(") == 3  # the definition and the two openers
    assert 'openedFrom("leg-row-" + leg.id)' in APP
    assert 'openedFrom("cell-" + row.date' in APP


@pytest.mark.parametrize("clears", [
    "S.tripId = id; S.trip = null; S.tripError = null; S.legSel = null; S.cameFrom = null;",
    "if (rid !== S.runId) { S.legSel = null; S.cameFrom = null; }",
    "S.legSel = null; S.cameFrom = null;",
])
def test_navigating_away_forgets_the_row_rather_than_focusing_a_stale_one(clears):
    assert clears in APP


def test_focus_is_restored_without_scrolling_the_page_out_from_under_the_reader():
    body = APP[APP.index("function closeDrawer("):]
    body = body[:body.index("function closeLegDrawer(")]
    assert "preventScroll: true" in body
    assert 'getAttribute("data-testid") === testid' in body, \
        "the lookup must not interpolate an id into a selector"
