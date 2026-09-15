"""
G. FONTS OFFLINE - fonts.googleapis.com is aborted by the harness (and refused
by the sandbox); the page must render in the fallback stack with nothing
clipped or overflowing at 400 and 1440.
H. SECURITY UNCHANGED - server.py/CSP byte-identical, no inline script or
style, no url() in app.css, the key never in the DOM, the static rules.
I. HOSTILE DATA on the new surfaces (.runrow, trip-row kinds).
J. DOCS - ui.md §4.7 vs app.css values; the mockup carries no marker change;
plan compliance greps; the coder's disclosed deviations vs the diff.
"""
import re
import subprocess

import pytest

from conftest import ROOT, SHOTS, doc_widths, open_trip, probe_server, q, run_search, text

CSS = (ROOT / "src" / "ui" / "static" / "app.css").read_text()
JS = (ROOT / "src" / "ui" / "static" / "app.js").read_text()
HTML = (ROOT / "src" / "ui" / "static" / "index.html").read_text()


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True).stdout


# ------------------------------------------------------------------- G fonts


@pytest.mark.parametrize("width", [1440, 400])
def test_G1_fallback_fonts_render_and_nothing_nowrap_overflows(browser, width):
    with probe_server("offline_b") as srv:
        with browser.page(srv.port, width=width, height=900) as pg:
            open_trip(pg, "trip_b_europe", "offline")
            fonts = pg.evaluate("""() => ({
              body: getComputedStyle(document.body).fontFamily,
              mono: getComputedStyle(document.querySelector('.mono')).fontFamily,
              loaded: Array.from(document.fonts).map((f) => f.family + ':' + f.status),
              figtree: document.fonts.check('14px Figtree'),
            })""")
            # every element with white-space: nowrap must fit its own box
            clipped = pg.evaluate("""() => Array.from(document.querySelectorAll('body *')).filter((n) => {
                const cs = getComputedStyle(n);
                if (cs.whiteSpace !== 'nowrap' || cs.display === 'none') return false;
                if (cs.overflowX === 'auto' || cs.overflowX === 'scroll' || cs.overflowX === 'hidden') return false;
                let p = n.parentElement;
                while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; p = p.parentElement; }
                return n.scrollWidth > n.clientWidth + 1 && n.clientWidth > 0;
              }).slice(0, 10).map((n) => n.tagName + '.' + n.className + ' ' + n.scrollWidth + '>' + n.clientWidth)""")
            # top bar items inside the viewport
            bar = pg.evaluate("""() => Array.from(document.querySelectorAll('.topbar-in > *, .bar-right > *')).map((n) => {
                const r = n.getBoundingClientRect(); return [n.className || n.tagName, Math.round(r.left), Math.round(r.right)]; })""")
            d = doc_widths(pg)
            off = list(pg.facts["offsite"])
            errs = list(pg.facts["errors"])
    assert off and all(u.startswith("https://fonts.googleapis.com/") for u in off), off
    assert "Figtree" in fonts["body"] and "Red Hat Mono" in fonts["mono"], fonts
    assert not [f for f in fonts["loaded"] if f.endswith(":loaded")], fonts["loaded"]
    assert errs == []
    assert d["sw"] <= width, d
    assert clipped == [], clipped
    assert all(l >= -1 and r <= width + 1 for _, l, r in bar), bar


def test_G2_the_only_offsite_request_is_the_google_fonts_stylesheet_and_it_is_the_new_families(browser):
    m = re.search(r'href="(https://fonts\.googleapis\.com/[^"]+)"', HTML)
    assert m and "Figtree" in m.group(1) and "Red+Hat+Mono" in m.group(1), m and m.group(1)
    assert HTML.count("https://") == 1
    with probe_server("search_ok") as srv:
        with browser.page(srv.port, width=1440) as pg:
            run_search(pg)
            off = set(pg.facts["offsite"])
    assert {u.split("?")[0] for u in off} == {"https://fonts.googleapis.com/css2"}, off


# --------------------------------------------------------------- H security


def test_H1_server_and_csp_are_byte_identical_to_the_base():
    """Re-pinned by the tester at d6be134 (search->trip round, plan
    docs/plans/search-to-trip.md 6 step 7, which names this pin): api.py and
    engine.py now carry that round's two POST routes and the delete engine
    (4.2/4.3) and are pinned to the coder's head d6be134 instead of the
    restyle base; serialize.py, main.py and formatter.py are still the bytes
    of 386b2fc; server.py is still the bytes of 16f53b7 and the CSP is
    byte-identical to the base. A pin, not a regression."""
    assert git("diff", "386b2fc..HEAD", "--", "src/ui/serialize.py", "src/main.py", "src/formatter.py") == ""
    assert git("diff", "d6be134..HEAD", "--", "src/ui/api.py", "src/ui/engine.py") == ""
    assert git("diff", "16f53b7..HEAD", "--", "src/ui/server.py") == ""
    server = (ROOT / "src" / "ui" / "server.py").read_text(encoding="utf-8")
    base = git("show", "386b2fc:src/ui/server.py")
    csp = re.compile(r"CSP = \((?:\s*\"[^\"]*\")+\s*\)")
    assert csp.search(server) and csp.search(server).group(0) == csp.search(base).group(0)


def test_H2_no_inline_script_style_or_handler_in_the_shell():
    """Re-pinned by the tester at 91081c9 (map-search round): the shell loads
    exactly two same-origin scripts, map.js then app.js (plan D7; defer runs
    them in document order). A pin, not a regression: still no inline script
    body, no style attribute, no handler attribute."""
    assert re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", HTML) == ["/static/map.js", "/static/app.js"]
    assert re.search(r"<script(?![^>]*\bsrc=\"/static/(app|map).js\")", HTML) is None
    assert re.search(r"<script[^>]*>(?!</script>)", HTML) is None
    assert "<style" not in HTML and " style=" not in HTML
    assert re.search(r"\son[a-z]+=", HTML) is None
    assert 'src="/static/app.js"' in HTML and 'href="/static/app.css"' in HTML


def test_H3_app_css_has_no_url_and_no_import():
    assert "url(" not in CSS and "@import" not in CSS and "http" not in CSS


def test_H4_app_js_still_builds_dom_with_textContent_only():
    assert "innerHTML" not in JS and "outerHTML" not in JS and "insertAdjacentHTML" not in JS
    # no NEW inline style (the base has one `d.style.marginTop`; the plan's 4.3 #4)
    base = git("show", "386b2fc:src/ui/static/app.js")
    assert JS.count(".style.") == base.count(".style.") == 2
    assert "setAttribute(\"style\"" not in JS and "cssText" not in JS
    assert "document.write" not in JS and "eval(" not in JS


def test_H5_the_key_never_reaches_the_dom_or_the_transcript(browser):
    from tests import _cli_golden as g
    from src import config

    key = g.FAKE_KEY
    mask = config.mask_key(key)
    with probe_server("live_b") as srv:
        with browser.page(srv.port, width=1440) as pg:
            open_trip(pg, "trip_b_europe", "live")
            pg.click(q("run-details") + " summary")
            html = pg.content()
            tr = pg.evaluate("() => document.querySelector('[data-testid=\"transcript\"] pre').textContent")
    assert key not in html and key not in tr
    assert mask not in html and mask not in tr
    assert "(masked key not sent to the browser)" in html


def test_H6_the_static_rules_and_security_suites_are_green():
    p = subprocess.run(["python3", "-O", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_ui_static_rules.py", "tests/test_ui_security.py"],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:]
    assert re.search(r"\b(\d+) passed", p.stdout), p.stdout[-300:]


def test_H7_the_testids_of_the_base_commit_all_survive():
    old = set(re.findall(r'"([a-z0-9-]+)"\)', git("show", "386b2fc:src/ui/static/app.js")))
    old |= set(re.findall(r'data-testid="([^"]+)"', git("show", "386b2fc:src/ui/static/index.html")))
    new = set(re.findall(r'"([a-z0-9-]+)"\)', JS)) | set(re.findall(r'data-testid="([^"]+)"', HTML))
    assert old - new == set(), old - new


# --------------------------------------------------------------- I hostile


@pytest.mark.parametrize("width", [1440, 400])
def test_I1_hostile_live_data_still_renders_as_text_on_the_new_layout(browser, width):
    with probe_server("hostile") as srv:
        with browser.page(srv.port, width=width, height=900) as pg:
            open_trip(pg, "trip_b_europe", "live")
            pg.click(q("leg-row-B4"))
            pg.wait_for_timeout(300)
            pwned = pg.evaluate("() => !!window.__pwned")
            imgs = pg.evaluate("() => document.querySelectorAll('main img, main script, #drawer-trips img').length")
            d = doc_widths(pg)
            drawer = text(pg, "#drawer-trips") or ""
            pg.screenshot(path=str(SHOTS / f"i1-hostile-{width}.png"))
    assert pwned is False and imgs == 0
    assert d["sw"] <= width, d
    assert "<img src=x onerror=" in drawer or "RIGHT-TO-LEFT" in drawer


def test_I2_hostile_search_renders_as_text(browser):
    with probe_server("hostile") as srv:
        with browser.page(srv.port, width=400, height=900) as pg:
            run_search(pg)
            for c in pg.query_selector_all('[data-testid^="cell-"]'):
                t = (c.inner_text() or "").strip()
                if t and t.lower() != "no space":
                    c.click()
                    pg.wait_for_timeout(400)
                    break
            pwned = pg.evaluate("() => !!window.__pwned")
            d = doc_widths(pg)
            drawer = text(pg, "#drawer-search") or ""
            imgs = pg.evaluate("() => document.querySelectorAll('main img, #drawer-search img').length")
    assert pwned is False and imgs == 0 and d["sw"] <= 400, d
    assert "onerror" in drawer.lower(), drawer[:300]


# ------------------------------------------------------------------ J docs


def css_token(name):
    m = re.search(r"--%s:\s*([^;]+);" % re.escape(name), CSS)
    return m and m.group(1).strip()


def test_J1_ui_md_4_7_tokens_match_app_css():
    doc = (ROOT / "docs" / "plans" / "ui.md").read_text()
    sec = doc.split("### 4.7")[1].split("### 4.8")[0]
    pairs = re.findall(r"`--([a-z0-9-]+) (#[0-9A-Fa-f]{6}|rgba\([^)]*\)|\d+px)`", sec)
    assert len(pairs) >= 12, pairs
    bad = [(n, v, css_token(n)) for n, v in pairs if (css_token(n) or "").replace(" ", "") != v.replace(" ", "")]
    assert bad == [], bad


def test_J2_ui_md_4_7_prose_matches_app_css_values():
    doc = (ROOT / "docs" / "plans" / "ui.md").read_text()
    sec = doc.split("### 4.7")[1].split("### 4.8")[0]
    bad = []
    # claims in the prose, checked against the stylesheet
    claims = [
        ("flex: 1 1 200px", ".seg button {" in CSS and "flex: 1 1 200px" in CSS.split(".seg button {")[1].split("}")[0]),
        ("rgba(95,211,160,.16)", "rgba(95,211,160,.16)" in CSS),
        ("rgba(240,184,90,.16) 0 3px, transparent 3px 7px", "rgba(240,184,90,.16) 0 3px, transparent 3px 7px" in CSS),
        ("rgba(15,20,27,.88)", "rgba(15,20,27,.88)" in CSS),
        ("backdrop-filter: blur(10px)", "backdrop-filter: blur(10px)" in CSS),
        ("0 0 0 1px var(--line), -12px 0 32px rgba(0,0,0,.55)", "0 0 0 1px var(--line), -12px 0 32px rgba(0,0,0,.55)" in CSS),
        ("padding 1px 7px", "padding: 1px 7px" in CSS),
        ("letter-spacing:.06em", "letter-spacing: .06em" in CSS),
        ("440px", "440px" in CSS),
        ("14px 16px padding", "padding: 14px 16px" in CSS),
    ]
    for needle, ok in claims:
        if needle in sec and not ok:
            bad.append(needle)
    assert bad == [], bad


def test_J3_the_mockup_reskin_changed_no_marker_text():
    def markers(html):
        body = html.split("</style>")[-1]
        return sorted(re.findall(r"UNKNOWN|NOT \$0|WITHHELD[^<]*|UNVERIFIED|unverified|NOT LOOKED UP|"
                                 r"NOT RECORDED|NEVER PRICED|API FAILED|UNREADABLE|\(badge\)|\(live\)|"
                                 r"\(snapshot[^)]*\)|CONFIRM BEFORE TRUSTING|REPARSED|exit \d[^<]*", body))
    old = markers(git("show", "386b2fc:docs/design/ui-mockup.html"))
    new = markers((ROOT / "docs" / "design" / "ui-mockup.html").read_text())
    assert old == new, (set(old) ^ set(new))


def test_J4_the_mockup_body_text_changed_only_where_the_plan_allows():
    def texts(html):
        body = html.split("</style>")[-1]
        return re.sub(r"<[^>]+>", "|", body)
    old = texts(git("show", "386b2fc:docs/design/ui-mockup.html"))
    new = texts((ROOT / "docs" / "design" / "ui-mockup.html").read_text())
    oldw = set(re.findall(r"[A-Za-z][A-Za-z $().:%'-]{3,}", old))
    neww = set(re.findall(r"[A-Za-z][A-Za-z $().:%'-]{3,}", new))
    gone = {w for w in oldw - neww if w.strip()}
    # the only text the plan lets go: the found-state key chip (D8)
    assert gone <= {"key: environment"}, gone


def test_J5_plan_compliance_greps():
    assert re.search(r"--(ink|coal|ash|seam|bone|smoke|oxblood|ember)\b", CSS) is None
    assert "font-stretch" not in CSS
    for p in ("app.css", "app.js", "index.html"):
        t = (ROOT / "src" / "ui" / "static" / p).read_text()
        assert not re.search(r"docs/plans/|docs/test-reports/|\bv\d\b|Step \d|\[HCML\]-\d", t), p
    assert "docs/design/restyle-ref/DirectionC.dc.html" in CSS  # the comment the plan allows


def test_J6_app_js_diff_is_exactly_the_three_enumerated_edits():
    """Re-pinned by the tester at d6be134 (search->trip round): the 56742af..HEAD
    half now counts 17 hunks and allows that round's enumerated strings (see
    the second allowlist below; docs/plans/search-to-trip.md 6 step 7). A
    pin, not a regression. The earlier history:
    The restyle's own diff (386b2fc..56742af, 6 hunks) is unchanged; on top
    of it the map round added its enumerated edits (docs/plans/map-search.md
    4.7). Re-pinned by the tester at 91081c9: the strings the map round added
    are the plan's 4.9 sentences (#9, #10, #13, #17, #18, the map.js-not-
    loaded reason), its testids, class names, DOM/ARIA tokens and the strings
    the restructured renderSearch re-emits verbatim. A pin, not a regression.
    Re-pinned again at c68e3eb: coder fix round 4 (c5c52fb) added one hunk -
    ensureMap's .catch, which shows string #2 with the error's message when
    mount() throws (manager should-fix; plan 4.9 #2 / 8) - and its three
    strings; git now groups the map round's diff into 7 hunks."""
    d = git("diff", "386b2fc..56742af", "--", "src/ui/static/app.js")
    assert d.count("\n@@") == 6, d.count("\n@@")
    added = [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
    strings = set(re.findall(r'"([^"]*)"', "\n".join(added)))
    allowed = {"key-source", "", "keysrc", "key: not found", "keysrc warn", "trow-broken",
               "trow-search", "trow-trip", "div", "span", "runrow", "runacts", "note", " ", "run-busy",
               # fix E1: the base's three key lines, re-split, plus the source suffix
               "Seats.aero key: (masked key not sent to the browser)",
               "Seats.aero key: not required (--from-snapshot replays committed bytes)",
               "Seats.aero key: not used (offline: no transport)", "   (source: ", ")",
               "replay", "(source: X)"}  # `run.mode === "replay"` and the comment
    assert strings <= allowed, strings - allowed
    d = git("diff", "56742af..HEAD", "--", "src/ui/static/app.js")
    assert d.count("\n@@") == 17, d.count("\n@@")   # 4.7's edits + the round-4 .catch (7) + search->trip's 14 hunks, as git groups them at d6be134
    added = [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
    strings = set(re.findall(r'"([^"]*)"', "\n".join(added)))
    map_allowed = {
        # plan 4.9 #9, #10, #13, #17, #18 and 8's map.js-not-loaded reason
        "Where are you flying?", "Type an airport or city, or pick it on the map.", "Type an airport or city.",
        "Show map", "Show results", "Suggestions", "The search has run: its results are under Show results.",
        "AIRPORT DATA UNREADABLE - /static/hubs.json could not be read (", "map.js not loaded). The map plots nothing.",
        "). The map plots nothing.",   # round 4: the .catch's tail of #2
        # testids (4.10) and the existing ones the restructured renderSearch re-emits
        "map-pane", "map-status", "search-pane-toggle", "search-ran", "search-result", "search-suggest-", "suggest-",
        "search-date", "search-from", "search-to", "search-run", "search-state", "search-strip", "search-window",
        "suggest-list-",
        # class names
        "search-head", "panel strip column", "strip-row", "fieldwrap", "suggest-slot", "suggest", "srow", ".srow",
        "sm", "mono", "dim", "note", "note with-map", "note without-map", "btn", "field", "label", "active", "result",
        # DOM / ARIA tokens
        "div", "span", "h1", "input", "button", "option", "listbox", "role", "aria-label", "aria-selected",
        "aria-expanded", "aria-controls", "aria-activedescendant", "aria-autocomplete", "autocomplete", "list",
        "off", "true", "false", "click", "blur", "input", "keydown", "mousedown", "ArrowDown", "ArrowUp", "Enter",
        "Escape", "date",
        # state keys and separators
        "from", "to", "map", "search", "", " ", ", ", "-", "?", "' + focusId + '",
        # existing wording re-emitted verbatim by the split renderSearch (pinned elsewhere)
        "From", "To", "Date", "SFO", "MAD", "YYYY-MM-DD", "LIVE unavailable", "Searches ", " to ",
        " · 1 traveller (award prices are per seat) · ", "single-route search is always LIVE and does not use the disk cache.",
        "Asking Seats.aero… nothing is shown until the whole answer is in.",
        "Search a route. Results show award space, whether your wallet can fund it, and ",
        "whether the taxes are trusted. A single-route search has no cash price, so it cannot say ",
        "POINTS or PAY CASH: add an award to a trip to score it.",
    }
    # Re-pinned by the tester at d6be134 (search->trip round; docs/plans/search-to-trip.md
    # 6 step 7 names this pin). The strings below are the coder report's list
    # ("Every new string literal in app.js"), checked against the diff by
    # search-to-trip-probes E5 before being copied here: P1-P9 (4.4), the nine
    # testids (4.5), class names, routes, hashes, DOM/ARIA tokens, state keys,
    # and the strings re-emitted from moved lines. A pin, not a regression.
    st_allowed = {
        # P1-P9 and their pieces
        "Add as trip", "Pick an award in the results first.", "Picked: ", "Prefilled from the search ",
        ". The award price the search showed is NOT written into this trip: only a LIVE or ",
        "REPLAY run can price it. Type the cash fare you found - the search cannot know it.",
        "required: the one thing a search cannot know", "Delete trip", "Before anything is removed", "Delete ",
        "Cancel", "(program not named)", " · ",
        # testids (4.5), and the two reused
        "search-add-trip", "search-add-trip-box", "search-add-trip-note", "nt-prefill", "trip-delete",
        "trip-delete-reason", "delete-confirm", "delete-confirm-go", "trip-deleted", "confirm-cancel", "nt-leg-1-cash",
        # class names
        "panel addtrip", "btn btn-warn", "btn btn-primary", "trip-acts", "panel refusal", "panel funding", "hint",
        "modal", "acts", "pre",
        # routes / API
        "/api/trips/", "/delete-preflight", "/delete", "POST", "HTTP ",
        # hashes
        "#new-trip", "#trips",
        # DOM / ARIA tokens
        "h3", "p", "dialog", "aria-modal", "scrim",
        # state keys / misc
        "cash", " sel",
        # re-emitted existing strings on moved lines
        "Cash per person (USD)", "2400",
        # in a comment only
        "ORIGIN → DEST · date · program · cabin",
    }
    assert strings <= map_allowed | st_allowed, strings - (map_allowed | st_allowed)


def test_J7_the_disclosed_deviations_are_what_the_diff_shows():
    seg = CSS.split(".seg button {")[1].split("}")[0]
    assert "flex: 1 1 0" in seg and "min-width: 200px" in seg and "max-width: 420px" in seg, seg
    assert "table.grid th.pin-left, table.grid th.pin-right { background: var(--raised); }" in CSS
    assert ".field input, .field select { background: var(--raised)" in CSS and "min-height: 36px" in CSS
    assert ".runrow .note { flex: 1 1 200px; min-width: 0; }" in CSS
    assert ".wordmark { font-weight: 600" in CSS and "display: flex" not in CSS.split(".wordmark {")[1].split("}")[0]
    mock = (ROOT / "docs" / "design" / "ui-mockup.html").read_text()
    assert re.search(r'id="key-source"[^>]*hidden', mock) or re.search(r'class="keysrc"[^>]*hidden', mock), "mockup key span"


def test_J8_undisclosed_css_changes_the_coder_did_not_list():
    """Selectors whose rules changed beyond a token rename. Informational:
    each one is checked by hand in the report."""
    d = git("diff", "386b2fc..HEAD", "--", "src/ui/static/app.css")
    changed = {}
    for line in d.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            m = re.match(r"[+-]([^{]+)\{", line)
            if m:
                changed.setdefault(m.group(1).strip(), []).append(line[0])
    # rules that are only removed (present at base, gone now)
    gone = sorted(s for s, ops in changed.items() if set(ops) == {"-"})
    # Re-test at 1f535aa: fix D5 (94ebb0c) rewrote `@media (max-width: 1180px)`
    # to `1179.98px`, which this parse reads as a removed rule. Read the whole
    # 7fba6e0..1f535aa CSS diff: that rewrite, the .runstrip/.seg:disabled rules
    # and the 720px block are its only content, so the needle is refreshed by
    # the tester (own probe; recorded in the report).
    assert gone == [".seg button:last-child", "@media (max-width: 1180px)"], gone
