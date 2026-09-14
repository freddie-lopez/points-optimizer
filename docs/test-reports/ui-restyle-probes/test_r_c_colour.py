"""
C. COLOUR SEMANTICS. --win is POINTS only, never a cash figure; the coral
accent2 is the one primary action; no marker's ONLY distinction is colour.
Static (app.css) AND rendered (every element's computed colour).
"""
import re

import pytest

from conftest import ROOT, drive

CSS = (ROOT / "src" / "ui" / "static" / "app.css").read_text()
WIN = "rgb(95, 211, 160)"
ACCENT2 = "rgb(255, 138, 101)"
ACCENT = "rgb(125, 169, 255)"
WARN = "rgb(240, 184, 90)"


def rules_using(token):
    """Selectors of every rule whose declarations mention the token."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        sel, body = m.group(1).strip(), m.group(2)
        if token in body and not sel.startswith(":root"):
            out.append(sel)
    return out


def test_C1_win_is_used_by_exactly_one_rule_the_POINTS_chip():
    assert rules_using("var(--win)") == [".chip-points"], rules_using("var(--win)")
    assert "#5FD3A0" not in CSS.split("}", 1)[1].upper()


def test_C2_accent2_is_used_only_by_the_primary_button():
    """Re-pinned by the tester at 91081c9 (map-search round, decision A): the
    route line on the map is the ONE other use of coral, as the approved
    RoutePicked mockup draws it (docs/plans/map-search.md D12; manager review
    runs/map-search/manager-review.md). Dashed and 1.5px, it cannot read as a
    button. A pin, not a regression."""
    sels = rules_using("var(--accent2)") + rules_using("var(--accent2-hover)")
    assert all(".btn-primary" in s or s.split("\n")[-1].strip() == ".route" for s in sels), sels
    assert sum(1 for s in sels if s.split("\n")[-1].strip() == ".route") == 1
    assert "#FF8A65" not in CSS.split("}", 1)[1].upper()


@pytest.fixture(scope="module", params=["offline_b", "offline_c", "live_b", "search_ok", "g7_offline"])
def painted(request, browser):
    """Every visible element's computed colour, background and class."""
    from conftest import probe_server, open_trip, run_search, TRIP_OF, SEARCH_SCENARIOS, styles, q

    name = request.param
    with probe_server(name) as srv:
        with browser.page(srv.port, width=1440) as pg:
            if name in SEARCH_SCENARIOS:
                run_search(pg)
                for c in pg.query_selector_all('[data-testid^="cell-"]'):
                    t = (c.inner_text() or "").strip()
                    if t and t.lower() != "no space":
                        c.click()
                        pg.wait_for_timeout(400)
                        break
            else:
                trip, mode = TRIP_OF[name]
                open_trip(pg, trip, mode)
                pg.click(q("leg-row-B3") if trip == "trip_b_europe" else '[data-testid^="leg-row-"]')
                pg.wait_for_timeout(300)
            els = pg.evaluate("""() => Array.from(document.querySelectorAll('body *')).filter((n) => {
                const cs = getComputedStyle(n);
                return cs.display !== 'none' && cs.visibility !== 'hidden' && n.offsetParent !== null;
              }).map((n) => { const cs = getComputedStyle(n); return {
                tag: n.tagName, cls: n.className && n.className.baseVal === undefined ? n.className : '',
                testid: n.getAttribute('data-testid'), text: (n.textContent || '').slice(0, 80),
                color: cs.color, bg: cs.backgroundColor, bgi: cs.backgroundImage,
                own: Array.from(n.childNodes).some((c) => c.nodeType === 3 && c.textContent.trim()),
              }; })""")
            yield name, els


def test_C3_no_cash_amount_is_painted_in_the_win_green(painted):
    name, els = painted
    bad = []
    for e in els:
        if e["color"] == WIN and "chip-points" not in e["cls"].split():
            bad.append((e["tag"], e["cls"], e["text"]))
        if e["own"] and e["color"] == WIN and re.search(r"[$€£]\s?\d", e["text"]):
            bad.append(("cash in green", e["cls"], e["text"]))
    assert bad == [], (name, bad)


def test_C4_the_coral_fill_is_on_primary_buttons_only(painted):
    name, els = painted
    bad = [(e["tag"], e["cls"], e["text"]) for e in els
           if e["bg"] in (ACCENT2, "rgb(255, 161, 132)") and "btn-primary" not in e["cls"].split()]
    assert bad == [], (name, bad)
    coral_text = [(e["tag"], e["cls"], e["text"]) for e in els
                  if e["color"] == ACCENT2 and e["own"]]
    assert coral_text == [], (name, coral_text)


def test_C5_how_many_primary_actions_are_on_screen_at_once(painted):
    """The plan calls coral 'the ONE primary action'. Count what is visible."""
    name, els = painted
    prim = [(e["testid"], e["text"].strip()) for e in els
            if "btn-primary" in e["cls"].split()]
    assert len(prim) <= 1, (name, prim)


def test_C6_no_marker_chip_is_distinguished_by_colour_alone(painted):
    """Every chip with a warn/win colour must ALSO have a form (fill, edge,
    hatch, dashed) or a word that says what it is."""
    name, els = painted
    bad = []
    for e in els:
        cls = e["cls"].split()
        if "chip" not in cls:
            continue
        kinds = [c for c in cls if c.startswith("chip-")]
        text = e["text"].strip()
        if not text:
            bad.append(("empty chip", cls))
        # colour-only kinds must carry a word that says what they are
        if kinds and kinds[0] in ("chip-modeled", "chip-indirect", "chip-exitwarn", "chip-neutral",
                                  "chip-notfund", "chip-cash"):
            if not re.search(r"[A-Za-z]{3,}", text):
                bad.append(("wordless", kinds, text))
    assert bad == [], (name, bad)
