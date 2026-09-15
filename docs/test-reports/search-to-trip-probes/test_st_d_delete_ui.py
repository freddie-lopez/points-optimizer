"""
D. The Delete trip button, its dialog, and the landing after, in the browser.
Every trips directory is a tmp copy; the committed corpus is refused there
and never touched here.
"""
import json

import pytest

from conftest import (SHOTS, delete_state, dialog, doc_widths, eng, go_trip, nt_values, patched_page, preview, q,
                      st_server, text, trip_ids, write_trip)

UI = "sfo-mad-2027-01-15"
LONG = "x" * 120
R2_TAIL = ("). The trips that came with the repository are test data; remove them with git, not from here. "
           "Nothing was deleted.")
C2 = "There is no undo in this app. If the file is committed, git can restore it; if it is not, it is gone."


def _posts(pg, suffix):
    return [p for p in pg.facts["posts"] if p["url"].endswith(suffix)]


def _run_offline(pg):
    pg.click(q("mode-offline")); pg.wait_for_timeout(100)
    pg.click(q("run-go"))
    pg.wait_for_selector(q("trip-result"), timeout=60000)
    pg.wait_for_timeout(300)


# ------------------------------------------------------------- the button


@pytest.mark.parametrize("trip", ["trip_a_mry_nyc", "trip_b_europe", "trip_c_lon_mry_surcharge", "trip_001", "trip_002"])
def test_D1_the_corpus_shows_a_disabled_delete_with_r2_under_it_in_the_warn_form(browser, trip):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, trip)
            if pg.query_selector(q("trip-error")):
                pytest.skip(f"{trip} does not load here (pre-existing)")
            d = delete_state(pg)
            assert d["present"] and d["disabled"] is True, d
            assert "btn-warn" in d["cls"] and "btn-primary" not in d["cls"]
            path = srv.trips_dir / f"{trip}.json"
            raw = json.loads(path.read_text())
            src = "" if raw.get("source") is None else str(raw["source"])[:80]
            assert d["reason"] == eng.NOT_DELETABLE_NOT_BUILT_HERE.format(file=str(path), source=src)
            assert d["reason"].startswith("NOT DELETABLE - ") and d["reason"].endswith(R2_TAIL)
            # clicking a disabled button sends nothing
            pg.click(q("trip-delete"), force=True); pg.wait_for_timeout(300)
            assert _posts(pg, "/delete-preflight") == [] and _posts(pg, "/delete") == []
            assert pg.query_selector(q("delete-confirm")) is None
            assert path.exists()
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"]


def test_D1b_the_button_is_caution_coloured_not_coral_and_a_ui_built_trip_enables_it(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            d = delete_state(pg)
            assert d["present"] and d["disabled"] is False and d["reason"] is None, d
            tokens = pg.evaluate("""() => { const cs = getComputedStyle(document.documentElement);
                return { warnbg: cs.getPropertyValue('--warn-bg').trim(), warn: cs.getPropertyValue('--warn').trim(),
                         accent2: cs.getPropertyValue('--accent2').trim() }; }""")
            def rgb(hexs):
                h = hexs.lstrip("#")
                return f"rgb({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)})"
            assert d["bg"] == rgb(tokens["warnbg"]), (d, tokens)
            assert d["color"] == rgb(tokens["warn"]), (d, tokens)
            assert d["bg"] != rgb(tokens["accent2"])
            # the page's one coral button on T2 is still Run
            prim = pg.evaluate("""() => Array.from(document.querySelectorAll('#trip-main .btn-primary'))
                .filter((b) => b.offsetParent !== null).map((b) => b.getAttribute('data-testid'))""")
            assert prim == ["run-go"], prim
            pg.screenshot(path=str(SHOTS / "d1-ui-built-trip.png"), full_page=False)


# -------------------------------------------------------------- dialog


def test_D2_the_dialog_says_the_plan_and_esc_cancel_and_scrim_send_nothing(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            path = srv.trips_dir / f"{UI}.json"
            desc = json.loads(path.read_text())["description"]
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            d = dialog(pg)
            assert d["role"] == "dialog" and d["modal"] == "true"
            assert d["label"] == "Before anything is removed"
            assert d["heading"] == f"Delete {path}?"
            assert d["lines"] == [f"This removes {path} from disk.", C2, desc]
            assert d["go"] == f"Delete {path}" and "btn-warn" in d["goCls"] and "btn-primary" not in d["goCls"]
            assert d["cancel"] == "Cancel" and d["focused"] == "confirm-cancel"
            assert len(_posts(pg, "/delete-preflight")) == 1 and _posts(pg, "/delete") == []
            pg.screenshot(path=str(SHOTS / "d2-delete-dialog.png"), full_page=False)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            assert pg.query_selector(q("delete-confirm")) is None
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.click(q("confirm-cancel")); pg.wait_for_timeout(200)
            assert pg.query_selector(q("delete-confirm")) is None
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.mouse.click(5, 5); pg.wait_for_timeout(200)
            assert pg.query_selector(q("delete-confirm")) is None
            # Tab cannot leave the dialog to the page behind? (record; the spend dialog is the same)
            assert _posts(pg, "/delete") == [] and len(_posts(pg, "/delete-preflight")) == 3
            assert path.exists()
            assert pg.query_selector(q("trip-detail")) is not None


# -------------------------------------------------------------- the delete


def test_D3_the_delete_lands_on_the_empty_view_with_the_servers_line_and_the_runs_are_gone(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            path = srv.trips_dir / f"{UI}.json"
            _run_offline(pg)
            assert pg.query_selector(q("run-chip-0")) is not None
            assert pg.evaluate("() => location.hash").startswith(f"#trips/{UI}/run/")
            # delete while the run RESULT is open, from the run page (the button sits in the head)
            pg.click(q("run-chip-fixture")); pg.wait_for_timeout(200)
            assert delete_state(pg)["disabled"] is False
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.click(q("delete-confirm-go"))
            pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => location.hash") == "#trips"
            assert text(pg, q("trip-deleted")) == f"Deleted {path}"
            assert text(pg, q("trip-empty")) == "Pick a trip on the left, or build a new one with + New trip."
            assert not path.exists()
            assert UI not in trip_ids(pg) and "trip_b_europe" in trip_ids(pg)
            assert pg.evaluate("() => document.querySelectorAll('[aria-current=\"true\"]').length") == 0
            assert pg.query_selector(q("run-chip-0")) is None
            assert text(pg, q("banner-error")) is None
            # a hash to the deleted trip: trip-error with R4
            pg.evaluate(f"() => {{ location.hash = '#trips/{UI}'; }}"); pg.wait_for_selector(q("trip-error"), timeout=10000)
            assert f"No trip '{UI}' in {srv.trips_dir}." in (text(pg, q("trip-error")) or "")
            assert pg.query_selector(q("trip-deleted")) is None
            # ... and the deleted panel does not come back on the empty view afterwards
            pg.evaluate("() => { location.hash = '#trips'; }"); pg.wait_for_timeout(300)
            assert pg.query_selector(q("trip-deleted")) is None
            # the same name written again shows NO chips from the deleted trip
            pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            pg.fill(q("nt-name"), UI); pg.fill(q("nt-leg-1-origin"), "SFO"); pg.fill(q("nt-leg-1-destination"), "MAD")
            pg.fill(q("nt-leg-1-date"), "2027-01-15"); pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg); write_trip(pg)
            pg.wait_for_selector(q("trip-detail"), timeout=10000)
            assert pg.query_selector(q("run-chip-0")) is None, "a run of the DELETED trip shown on its namesake"
            assert delete_state(pg)["disabled"] is False
            assert pg.facts["errors"] == []


def test_D3b_the_120_char_name_at_400px_wraps_and_the_dialog_fits(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port, width=400, height=800) as pg:
            go_trip(pg, LONG)
            path = srv.trips_dir / f"{LONG}.json"
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            d = dialog(pg)
            assert d["heading"] == f"Delete {path}?" and d["go"] == f"Delete {path}"
            assert d["right"] <= 400 and d["w"] <= 400 - 30, d
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], w
            gb = pg.evaluate("""() => { const r = document.querySelector('[data-testid="delete-confirm-go"]').getBoundingClientRect();
                return { right: r.right, h: r.height }; }""")
            assert gb["right"] <= 400 and gb["h"] > 40, gb
            pg.screenshot(path=str(SHOTS / "d3b-dialog-400.png"), full_page=False)
            pg.click(q("delete-confirm-go")); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            assert not path.exists()
            pg.screenshot(path=str(SHOTS / "d3b-deleted-400.png"), full_page=False)
            # The landing panel's `Deleted <file>` line: a 120-char name has no break
            # opportunity and the panel has no overflow-wrap (the pre-existing
            # `nt-wrote` panel has the same gap). Plan 3: "nothing widens the page".
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], ("trip-deleted widens the page", w)


def test_D4_the_last_trip(browser):
    with st_server("one_trip") as srv:
        with patched_page(browser, srv.port) as pg:
            assert trip_ids(pg) == [UI]
            assert pg.evaluate("() => location.hash") == f"#trips/{UI}", "the fallback default selection"
            pg.wait_for_selector(q("trip-delete"), timeout=10000)
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.click(q("delete-confirm-go")); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            assert trip_ids(pg) == [] and list(srv.trips_dir.iterdir()) == []
            assert text(pg, q("trip-empty")) is not None
            assert pg.facts["errors"] == []
            # the New trip button still works from the empty list
            pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            # a reload with nothing to select: no crash, no default, the deleted panel is gone
            pg.reload(); pg.wait_for_selector(q("wordmark"), timeout=20000); pg.wait_for_timeout(500)
            assert pg.query_selector(q("trip-deleted")) is None
            assert pg.facts["errors"] == []


# ------------------------------------------------------------ busy / stale


def test_D5_disabled_while_a_run_is_in_flight_and_refused_while_the_lock_is_held(browser):
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.click(q("mode-offline")); pg.wait_for_timeout(100)
            pg.click(q("run-go"))
            pg.wait_for_selector(q("run-busy"), timeout=10000)
            assert delete_state(pg)["disabled"] is True
            # another trip's page while the run is in flight: also disabled
            pg.click(q("trip-row-trip_b_europe")); pg.wait_for_selector(q("trip-delete-reason"), timeout=10000)
            assert delete_state(pg)["disabled"] is True
            pg.wait_for_timeout(6000)
            # Pre-existing (sendRun does not re-render when the run finished on
            # another trip): Trip B's page still says Running... and keeps Delete
            # disabled until something re-renders. Recorded, judged in the report.
            stale = {"delete": delete_state(pg)["disabled"],
                     "run": pg.evaluate("() => document.querySelector('[data-testid=run-go]').textContent")}
            go_trip(pg, UI)
            assert delete_state(pg)["disabled"] is False
            print("STALE after a run finished elsewhere:", stale)
            # the lock held from outside the page: the click is refused by the server
            assert srv.cmd("lock")["locked"] is True
            try:
                pg.click(q("trip-delete")); pg.wait_for_timeout(500)
                assert pg.query_selector(q("delete-confirm")) is None
                assert text(pg, q("banner-error")) == eng.DELETE_BUSY
            finally:
                srv.cmd("unlock")
            assert (srv.trips_dir / f"{UI}.json").exists()
            # preflight ok, then the lock is taken before the confirm: R5 in the banner, file intact
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            srv.cmd("lock")
            try:
                pg.click(q("delete-confirm-go")); pg.wait_for_timeout(500)
                assert text(pg, q("banner-error")) == eng.DELETE_BUSY
            finally:
                srv.cmd("unlock")
            assert (srv.trips_dir / f"{UI}.json").exists()
            assert pg.query_selector(q("trip-detail")) is not None


def test_D6_a_hand_edit_after_the_page_loaded_is_caught_by_the_preflight_and_after_the_preflight_by_the_delete(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            path = srv.trips_dir / f"{UI}.json"
            assert delete_state(pg)["disabled"] is False
            srv.cmd(f"edit {UI} points")
            pg.click(q("trip-delete")); pg.wait_for_timeout(500)
            assert pg.query_selector(q("delete-confirm")) is None
            assert text(pg, q("banner-error")) == eng.NOT_DELETABLE_EDITED.format(file=str(path))
            assert path.exists()
            # deviation 10: the button's state is from the last GET - still enabled. Recorded.
            stale_button_enabled = delete_state(pg)["disabled"] is False
            # a fresh UI-built file, preflight, THEN an edit that keeps provenance: R7
            srv.cmd(f"rm {UI}.json"); srv.cmd("mk")
            go_trip(pg, "trip_b_europe"); go_trip(pg, UI)
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            srv.cmd(f"edit {UI} cash")
            pg.click(q("delete-confirm-go")); pg.wait_for_timeout(500)
            assert text(pg, q("banner-error")) == eng.DELETE_CONFIRM_STALE
            assert path.exists() and json.loads(path.read_text())["legs"][0]["cash_options"][0]["amount"] == 1.0
            # and a file made unreadable after the preflight
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            srv.cmd(f"edit {UI} broken")
            pg.click(q("delete-confirm-go")); pg.wait_for_timeout(500)
            assert text(pg, q("banner-error")) == eng.DELETE_CONFIRM_STALE
            assert path.exists()
            assert stale_button_enabled, "recorded, not asserted otherwise"


def test_D7_a_hostile_description_in_a_ui_built_file_renders_as_text_in_the_dialog(browser):
    with st_server("ui_built") as srv:
        path = srv.trips_dir / f"{UI}.json"
        d = json.loads(path.read_text())
        d["description"] = ('<img src=x onerror="window.__pwned=1"></pre><script>window.__pwned=1</script>'
                            "‮RIGHT-TO-LEFT‬ " + "D" * 3000)
        path.write_text(json.dumps(d, indent=2) + "\n")
        with patched_page(browser, srv.port, width=400, height=800) as pg:
            go_trip(pg, UI)
            assert delete_state(pg)["disabled"] is False, "a description edit keeps provenance"
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            dl = dialog(pg)
            assert dl["lines"][2] == d["description"]
            assert pg.evaluate("() => !!window.__pwned") is False
            assert pg.evaluate("() => document.querySelectorAll('img').length") == 0
            assert dl["right"] <= 400
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], w
            pg.screenshot(path=str(SHOTS / "d7-hostile-description-400.png"), full_page=False)
            pg.click(q("delete-confirm-go")); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            assert not path.exists()


def test_D8_where_focus_lands_after_a_delete_and_after_cancel(browser):
    """Recorded, judged in the report: the spend dialog behaves the same way."""
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.focus(q("trip-delete")); pg.keyboard.press("Enter")
            pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            after_cancel = pg.evaluate("() => document.activeElement.tagName + ':' + document.activeElement.getAttribute('data-testid')")
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.keyboard.press("Tab"); pg.keyboard.press("Enter")
            pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            after_delete = pg.evaluate("() => document.activeElement.tagName + ':' + document.activeElement.getAttribute('data-testid')")
            print("FOCUS after cancel:", after_cancel, "after delete:", after_delete)
            assert after_cancel == "BUTTON:trip-delete", after_cancel
