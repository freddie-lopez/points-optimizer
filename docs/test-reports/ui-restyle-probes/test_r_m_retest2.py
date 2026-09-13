"""
M. RE-TEST at 257960a. Fix E1 (999fff3): run-details names the key source in
every mode. Attacked in OFFLINE, REPLAY, no-key OFFLINE and a LIVE run against
the stub: the source must be right in each, in the `(source: X)` form, and the
key (and its mask) must never be anywhere in the page.
"""
import pytest

from conftest import open_trip, probe_server, q, text

CASES = [
    # scenario, mode, expected key line, expected (source: X) or None
    ("offline_b", "offline", "Seats.aero key: not used (offline: no transport)", "environment"),
    ("replay_b", "replay", "Seats.aero key: not required (--from-snapshot replays committed bytes)", "environment"),
    ("live_b", "live", "Seats.aero key: (masked key not sent to the browser)", "environment"),
    ("no_key", "offline", "Seats.aero key: not used (offline: no transport)", None),
]


@pytest.fixture(scope="module", params=CASES, ids=[c[0] + "-" + c[1] for c in CASES])
def case(request, browser):
    from tests import _cli_golden as g
    from src import config

    scenario, mode, line, source = request.param
    with probe_server(scenario) as srv:
        with browser.page(srv.port, width=1440) as pg:
            open_trip(pg, "trip_b_europe", mode)
            pg.click(q("run-details") + " summary")
            pg.wait_for_timeout(150)
            details = text(pg, q("run-details")) or ""
            html = pg.content()
            transcript = pg.evaluate("() => document.querySelector('[data-testid=\"transcript\"] pre').textContent")
            chip = pg.evaluate("() => { const k = document.querySelector('[data-testid=\"key-source\"]'); return [k.hidden, k.textContent]; }")
    return {"scenario": scenario, "mode": mode, "line": line, "source": source, "details": details,
            "html": html, "transcript": transcript, "chip": chip,
            "key": g.FAKE_KEY, "mask": config.mask_key(g.FAKE_KEY)}


def key_lines(details):
    return [l for l in details.splitlines() if l.startswith("Seats.aero key:")]


def test_M1_the_key_line_is_exactly_one_line_with_the_right_words(case):
    ls = key_lines(case["details"])
    assert len(ls) == 1, ls
    assert ls[0].startswith(case["line"]), ls[0]


def test_M2_the_source_is_right_for_the_mode(case):
    ls = key_lines(case["details"])[0]
    if case["source"] is None:
        assert "(source:" not in ls, ls
        assert case["chip"] == [False, "key: not found"], case["chip"]
    else:
        assert ls.endswith("(source: " + case["source"] + ")"), ls
        assert ls.count("(source:") == 1, ls
        assert case["chip"] == [True, ""], case["chip"]


def test_M3_the_key_and_its_mask_are_nowhere_in_the_page(case):
    for needle in (case["key"], case["mask"]):
        assert needle not in case["html"], (case["scenario"], needle[:6])
        assert needle not in case["transcript"], (case["scenario"], needle[:6])
    # and no "source" line ever carries a path to a key file in a found state
    # beyond what the CLI itself prints (environment / repo .env / user config)
    ls = key_lines(case["details"])[0]
    assert "SEATS_AERO_KEY=" not in ls


def test_M4_the_run_details_text_is_otherwise_the_base_commits(case):
    """Only the `(source: X)` suffix may differ from what 386b2fc printed."""
    ls = key_lines(case["details"])[0]
    stripped = ls.split("   (source:")[0]
    assert stripped == case["line"], (stripped, case["line"])
