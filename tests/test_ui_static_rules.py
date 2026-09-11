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
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "app.css").read_text()

BANNED = [
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
    "new Function", "DOMParser", "createContextualFragment", "srcdoc",
]


@pytest.mark.parametrize("needle", BANNED)
def test_app_js_never_uses_an_html_or_code_string_api(needle):
    assert needle not in APP


def test_set_timeout_is_only_ever_given_a_function():
    for m in re.finditer(r"setTimeout\(\s*([^,\s]+)", APP):
        assert not m.group(1).startswith(("\"", "'", "`")), m.group(0)


@pytest.mark.parametrize("pattern", [r"\|\|\s*0(?![.\d])", r"\?\?\s*0(?![.\d])"])
def test_an_unknown_never_becomes_zero_in_javascript(pattern):
    assert not re.search(pattern, APP), re.search(pattern, APP).group(0)


def test_no_inline_script_or_handler_in_the_page():
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", HTML, re.S):
        assert body.strip() == ""
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", HTML, re.I)
    assert "javascript:" not in HTML.lower() and "javascript:" not in APP.lower()


def test_the_page_loads_only_its_own_script_and_style():
    scripts = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", HTML)
    assert scripts == ["/static/app.js"]
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
]


@pytest.mark.parametrize("testid", TESTIDS)
def test_every_inventoried_element_has_its_testid(testid):
    quoted = [f'"{testid}"', f"'{testid}'", f'data-testid="{testid}']
    assert any(q in APP or q in HTML for q in quoted), testid


@pytest.mark.parametrize("name", ["app.js", "index.html", "app.css"])
def test_no_changelog_in_anything_the_page_ships(name):
    text = (STATIC / name).read_text()
    hits = [m.group(0) for m in CHANGELOG.finditer(text) if not ALLOWED.search(m.group(0))]
    assert not hits, f"{name}: {hits}"


def test_the_design_tokens_are_the_plans():
    for token in ("--ink: #0A0809", "--coal: #121011", "--ash: #1B1718", "--seam: #2B2325",
                  "--bone: #ECE4E3", "--smoke: #9C8F8F", "--oxblood: #7A1620",
                  "--ember: #B4323C", "--win: #5E9E73", "--warn: #C8923A",
                  "--unknown: #7D7475", "--display: 28px"):
        assert token in CSS, token
