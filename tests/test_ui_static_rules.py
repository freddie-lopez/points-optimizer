"""
Static rules for the page's own files (src/ui/static/*).

* XSS by construction: app.js builds the DOM with createElement/textContent and
  never parses a string as HTML, evaluates one as code, or schedules one.
* The JavaScript spelling of this project's recurring failure - `x || 0`,
  `x ?? 0`, an unknown quietly becoming zero - is banned outright.
* No inline script, no inline event handler: the CSP forbids them and the page
  must work under it.
* Every element the plan's screen inventory names carries its data-testid.
* No release numbers, finding ids or plan paths in anything the page ships
  (the no-changelog rule, same pattern as test_no_changelog_in_user_output).
"""
import re
from pathlib import Path

import pytest

from tests.test_no_changelog_in_user_output import ALLOWED, CHANGELOG

STATIC = Path(__file__).resolve().parent.parent / "src" / "ui" / "static"
APP = (STATIC / "app.js").read_text()
MAP = (STATIC / "map.js").read_text()
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "app.css").read_text()
# Both scripts the page loads: app.js, and map.js (the Search tab's map).
SCRIPTS = {"app.js": APP, "map.js": MAP}

BANNED = [
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
    "new Function", "DOMParser", "createContextualFragment", "srcdoc",
]


@pytest.mark.parametrize("script", sorted(SCRIPTS))
@pytest.mark.parametrize("needle", BANNED)
def test_app_js_never_uses_an_html_or_code_string_api(script, needle):
    assert needle not in SCRIPTS[script]


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_set_timeout_is_only_ever_given_a_function(script):
    for m in re.finditer(r"setTimeout\(\s*([^,\s]+)", SCRIPTS[script]):
        assert not m.group(1).startswith(("\"", "'", "`")), m.group(0)


@pytest.mark.parametrize("script", sorted(SCRIPTS))
@pytest.mark.parametrize("pattern", [r"\|\|\s*0(?![.\d])", r"\?\?\s*0(?![.\d])"])
def test_an_unknown_never_becomes_zero_in_javascript(script, pattern):
    text = SCRIPTS[script]
    assert not re.search(pattern, text), re.search(pattern, text).group(0)


def test_no_inline_script_or_handler_in_the_page():
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", HTML, re.S):
        assert body.strip() == ""
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", HTML, re.I)
    assert "javascript:" not in HTML.lower()
    for text in SCRIPTS.values():
        assert "javascript:" not in text.lower()


def test_the_page_loads_only_its_own_script_and_style():
    scripts = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", HTML)
    # map.js first: app.js mounts the map through window.POMap under `defer`,
    # which runs the two in document order.
    assert scripts == ["/static/map.js", "/static/app.js"]
    sheets = re.findall(r"<link rel=\"stylesheet\" href=\"([^\"]+)\"", HTML)
    assert all(s == "/static/app.css" or s.startswith("https://fonts.googleapis.com/")
               for s in sheets)


TESTIDS = [
    # G: global chrome
    "wordmark", "mode-pill", "calls-counter", "wallet-chip-", "key-source",
    "tab-search", "tab-trips", "banner-error", "no-wallet",
    # W
    "wallet-panel",
    # S
    "search-strip", "search-from", "search-to", "search-date", "search-date-to",
    "search-cabin", "search-run", "search-window", "search-confirm", "search-confirm-go",
    "search-state", "search-table", "search-table-scroll", "cell-", "search-footer",
    "drawer-to-trip",
    # T
    "trip-list", "trip-row-", "new-trip", "trip-detail", "fixture-legs", "run-strip",
    "mode-live", "mode-replay", "mode-offline", "run-options", "run-go", "run-confirm",
    "run-confirm-go", "new-trip-form", "nt-name", "nt-cabin", "nt-leg-", "nt-couple-hint",
    "nt-echo", "nt-write",
    # T6
    "trip-result", "exit-chip", "refusal", "funding-banner", "headline", "headline-value",
    "legs-table", "legs-table-scroll", "leg-row-", "verdict-", "totals", "trip-notes",
    "residue", "run-details", "transcript", "transcript-copy",
    # T7
    "drawer", "cli-lines-",
    # S0: the map, the autofill and the pane toggle
    "map-pane", "map-svg", "map-land", "map-hub-", "map-cluster-", "map-cluster-list",
    "map-pick-", "map-route", "map-status", "map-provenance", "map-zoom-in", "map-zoom-out",
    "map-reset", "search-suggest-", "suggest-", "search-pane-toggle", "search-result",
    "search-ran",
    # search -> trip, and delete (docs/plans/search-to-trip.md 4.5)
    "search-add-trip", "search-add-trip-box", "search-add-trip-note", "nt-prefill",
    "trip-delete", "trip-delete-reason", "delete-confirm", "delete-confirm-go", "trip-deleted", "trip-list-heading",
]


@pytest.mark.parametrize("testid", TESTIDS)
def test_every_inventoried_element_has_its_testid(testid):
    quoted = [f'"{testid}"', f"'{testid}'", f'data-testid="{testid}']
    assert any(q in APP or q in MAP or q in HTML for q in quoted), testid


@pytest.mark.parametrize("name", ["app.js", "map.js", "index.html", "app.css", "land.json"])
def test_no_changelog_in_anything_the_page_ships(name):
    text = (STATIC / name).read_text()
    hits = [m.group(0) for m in CHANGELOG.finditer(text) if not ALLOWED.search(m.group(0))]
    assert not hits, f"{name}: {hits}"


def test_the_design_tokens_are_the_plans():
    for token in ("--bg: #0F141B", "--panel: #171D26", "--raised: #1F2733", "--line: #2A3442",
                  "--text: #E9EDF3", "--muted: #8C98A8", "--accent: #7DA9FF",
                  "--accent2: #FF8A65", "--warn: #F0B85A", "--win: #5FD3A0",
                  "--unknown: #8791A0", "--display: 28px"):
        assert token in CSS, token
