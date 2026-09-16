"""
F. Re-test at ecab878 (coder fix round 1): F1-F4 attacked independently, the
placeholder removal, and the C5 demotion.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import (SHOTS, delete_state, dialog, doc_widths, eng, go_trip, http_server, nt_values, patched_page,
                      pick_first_cell, preview, q, run_search, st_server, text, trip_ids, ui_built, write_trip, pinned)  # noqa: F401

UI = "sfo-mad-2027-01-15"


def _pf(c, trip_id):
    return c.post(f"/api/trips/{trip_id}/delete-preflight", {})


def _del(c, trip_id, cid):
    return c.post(f"/api/trips/{trip_id}/delete", {"confirm_id": cid})


def _focused(pg):
    return pg.evaluate("() => document.activeElement.tagName + ':' + document.activeElement.getAttribute('data-testid')")


# ------------------------------------------------------------------- F1


def test_F1a_the_a15_interleaving_now_answers_404_r4(tmp_path, pinned, monkeypatch):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        c1 = _pf(c, UI).json()["confirm_id"]
        c2 = _pf(c, UI).json()["confirm_id"]
        real = eng.Engine.redeem_confirm
        done = {"first": False}

        def redeem(self, confirm_id, digest, **kw):
            real(self, confirm_id, digest, **kw)
            if confirm_id == c2 and not done["first"]:
                done["first"] = True
                assert self.trip_delete(UI, {"confirm_id": c1})["lines"][0].startswith("Deleted ")

        monkeypatch.setattr(eng.Engine, "redeem_confirm", redeem)
        r = _del(c, UI, c2)
        assert not path.exists()
        assert (r.status, r.json()) == (404, {"error": "not_found",
                                              "message": f"No trip {UI!r} in {eng.display_path(c.trips_dir)}."}), r.text


def test_F1b_a_file_that_vanishes_between_the_listing_and_the_preflight_check(tmp_path, pinned, monkeypatch):
    """The preflight's own read: trip_path lists it, then the file is gone
    before deletable_reason reads it -> 404, not R1; and the detail GET too."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        real = eng.Engine.trip_path

        def vanish(self, trip_id):
            p = real(self, trip_id)
            if p.exists():
                p.unlink()
            return p

        monkeypatch.setattr(eng.Engine, "trip_path", vanish)
        r = _pf(c, UI)
        assert r.status == 404 and r.json()["error"] == "not_found", r.text
        assert not path.exists()
        ui_built(c.trips_dir)
        r = c.get(f"/api/trips/{UI}")
        # The detail GET raced the same way: the LOADER reads first and answers the
        # pre-existing 422 cannot_load with the OS's own sentence. Not this round's
        # route; recorded. (The delete routes answer 404.)
        assert r.status in (404, 422), r.text
        print("RACED GET /api/trips/{id}:", r.status, r.text[:80])
        ui_built(c.trips_dir)
        r = _del(c, UI, "x")
        # the digest read comes before the redeem: the vanished file is the 404
        assert r.status == 404 and r.json()["error"] == "not_found", r.text


def test_F1c_a_dangling_symlink_and_a_directory_swap_between_preflight_and_delete(tmp_path, pinned):
    outside = ui_built(tmp_path / "elsewhere", name="victim")
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        # a symlink whose target vanishes: not listed any more (is_file false) -> 404
        (c.trips_dir / "link.json").symlink_to(outside)
        assert "link" in [t["id"] for t in c.get("/api/trips").json()]
        assert _pf(c, "link").status == 409
        outside.unlink()
        assert "link" not in [t["id"] for t in c.get("/api/trips").json()]
        assert _pf(c, "link").status == 404 and _del(c, "link", "x").status == 404
        assert (c.trips_dir / "link.json").is_symlink()
        # a real file, preflight, then swapped for a DIRECTORY of the same name
        path = ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
        path.unlink()
        path.mkdir()
        (path / "inner.json").write_text("{}")
        r = _del(c, UI, cid)
        assert r.status == 404 and r.json()["error"] == "not_found", r.text
        assert path.is_dir() and (path / "inner.json").exists()
        shutil.rmtree(path)
        # a real file, preflight, then swapped for a dangling SYMLINK of the same name
        path = ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
        path.unlink()
        path.symlink_to(tmp_path / "nowhere.json")
        r = _del(c, UI, cid)
        assert r.status == 404, r.text
        assert path.is_symlink() and not path.exists()
        path.unlink()
        # ... and for a symlink to a live deletable file: R8, target intact
        target = ui_built(tmp_path / "elsewhere2", name="victim2")
        path = ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
        path.unlink()
        path.symlink_to(target)
        r = _del(c, UI, cid)
        assert r.status == 409 and r.json()["error"] in ("confirm_stale", "not_deletable"), r.text
        assert path.is_symlink() and target.exists()
        # the R1 sentence is still there for a file that IS unreadable
        path.unlink()
        path.write_text("{ not json")
        r = _pf(c, UI)
        assert r.status == 409 and r.json()["message"] == eng.NOT_DELETABLE_UNREADABLE.format(file=str(path))


# ------------------------------------------------------------------- F2


@pytest.mark.parametrize("width", [360, 400])
@pytest.mark.parametrize("n", [120, 300])
def test_F2_both_panels_break_a_long_name_at_phone_widths(browser, width, n):
    """300 chars: the builder refuses a name over 120, so the 300-char token is
    put into the server's `Wrote …`/`Deleted …` lines in the browser (a
    response patch), which is the only way a 300-char unbroken token reaches
    either panel; the CSS rule is what is under test."""
    name = "y" * 120
    long_tok = "z" * 300

    def stretch(body, req):
        if n == 300 and isinstance(body, dict) and isinstance(body.get("lines"), list) and body["lines"]:
            body["lines"][0] = body["lines"][0].replace(name, long_tok + name)
        return body

    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port, width=width, height=800,
                          patches={"/api/trips/create": stretch, "/delete": stretch}) as pg:
            pg.click(q("tab-trips")); pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            pg.fill(q("nt-name"), name); pg.fill(q("nt-leg-1-origin"), "SFO"); pg.fill(q("nt-leg-1-destination"), "MAD")
            pg.fill(q("nt-leg-1-date"), "2027-01-15"); pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg); write_trip(pg)
            pg.wait_for_selector(q("nt-wrote"), timeout=10000)
            wrote = text(pg, q("nt-wrote")) or ""
            assert wrote.startswith("Wrote ") and name in wrote and (n != 300 or long_tok in wrote)
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], ("nt-wrote", width, w)
            pg.screenshot(path=str(SHOTS / f"f2-wrote-{width}-{n}.png"), full_page=False)
            pg.wait_for_selector(q("trip-delete"), timeout=10000)
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], ("dialog", width, w)
            pg.click(q("delete-confirm-go")); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            assert name in (text(pg, q("trip-deleted")) or "") and (n != 300 or long_tok in text(pg, q("trip-deleted")))
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], ("trip-deleted", width, w)
            pg.screenshot(path=str(SHOTS / f"f2-deleted-{width}-{n}.png"), full_page=False)
            # the whole left column and main column too
            wide = pg.evaluate(f"""() => Array.from(document.querySelectorAll('body *'))
                .filter((n) => n.getBoundingClientRect().right > {width} + 1 && getComputedStyle(n).position !== 'fixed')
                .map((n) => n.tagName + '.' + n.className).slice(0, 5)""")
            assert wide == [], wide
            assert pg.facts["errors"] == []


# ------------------------------------------------------------------- F3


def test_F3a_keyboard_only_delete_dialog_esc_cancel_and_go(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            # Tab to Delete from the page top
            pg.focus(q("trip-row-" + UI))
            for _ in range(30):
                if _focused(pg) == "BUTTON:trip-delete":
                    break
                pg.keyboard.press("Tab")
            assert _focused(pg) == "BUTTON:trip-delete", _focused(pg)
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            assert _focused(pg) == "BUTTON:confirm-cancel"
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            assert _focused(pg) == "BUTTON:trip-delete", _focused(pg)
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.keyboard.press("Enter"); pg.wait_for_timeout(200)   # Enter on the focused Cancel
            assert pg.query_selector(q("delete-confirm")) is None and _focused(pg) == "BUTTON:trip-delete"
            # scrim click also returns focus
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.mouse.click(5, 5); pg.wait_for_timeout(200)
            assert _focused(pg) == "BUTTON:trip-delete", _focused(pg)
            assert (srv.trips_dir / f"{UI}.json").exists()
            # Tab -> Go, Enter: delete, focus on the list heading
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.keyboard.press("Tab"); assert _focused(pg) == "BUTTON:delete-confirm-go"
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            pg.wait_for_timeout(300)
            assert _focused(pg) == "H2:trip-list-heading", _focused(pg)
            assert pg.evaluate("() => document.activeElement.tabIndex") == -1
            # ... and Tab from there reaches the first trip row, not the top of the page
            pg.keyboard.press("Tab")
            assert _focused(pg).startswith("BUTTON:trip-row-"), _focused(pg)
            # the heading is not in the Tab order otherwise: Shift+Tab from the first row skips it
            pg.keyboard.press("Shift+Tab")
            assert _focused(pg) != "H2:trip-list-heading"
            assert not (srv.trips_dir / f"{UI}.json").exists()
            assert pg.facts["errors"] == []


def test_F3b_the_spend_dialog_esc_and_cancel_return_to_run_and_go_is_judged(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, "trip_b_europe")
            pg.click(q("mode-live")); pg.wait_for_timeout(100)
            pg.focus(q("run-go")); pg.keyboard.press("Enter")
            pg.wait_for_selector(q("run-confirm"), timeout=10000)
            assert _focused(pg) == "BUTTON:confirm-cancel"
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            assert _focused(pg) == "BUTTON:run-go", _focused(pg)
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("run-confirm"), timeout=10000)
            pg.keyboard.press("Enter"); pg.wait_for_timeout(200)
            assert _focused(pg) == "BUTTON:run-go", _focused(pg)
            # Go: the run starts; where is focus during and after? Recorded.
            pg.keyboard.press("Enter"); pg.wait_for_selector(q("run-confirm"), timeout=10000)
            pg.keyboard.press("Tab"); pg.keyboard.press("Enter")
            pg.wait_for_timeout(150)
            during = _focused(pg)
            pg.wait_for_selector(q("trip-result"), timeout=60000); pg.wait_for_timeout(300)
            after = _focused(pg)
            print("SPEND GO focus during:", during, "after:", after)
            assert pg.facts["errors"] == []


def test_F3c_a_dialog_opened_by_mouse_still_returns_focus_to_the_button(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            pg.click(q("confirm-cancel")); pg.wait_for_timeout(200)
            assert _focused(pg) == "BUTTON:trip-delete"
            # the opener was rebuilt meanwhile (a re-render): still found by testid
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm"), timeout=10000)
            srv.cmd(f"edit {UI} cash")
            pg.click(q("delete-confirm-go")); pg.wait_for_timeout(500)
            assert text(pg, q("banner-error")) == eng.DELETE_CONFIRM_STALE
            print("FOCUS after a refused Go (stale):", _focused(pg))
            assert pg.query_selector(q("trip-delete")) is not None


# ------------------------------------------------------------------- F4


def test_F4a_a_run_finishing_while_another_trips_detail_is_open_re_enables_its_buttons(browser):
    with st_server("slow_run") as srv:
        srv.cmd("mk other-trip MAD:AMS:2027-01-19:120 J")
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.click(q("mode-offline")); pg.wait_for_timeout(100)
            pg.click(q("run-go")); pg.wait_for_selector(q("run-busy"), timeout=10000)
            pg.click(q("trip-row-trip_b_europe")); pg.wait_for_selector(q("trip-delete-reason"), timeout=10000)
            assert delete_state(pg)["disabled"] is True
            assert pg.evaluate("() => document.querySelector('[data-testid=run-go]').textContent") == "Running…"
            pg.wait_for_function("""() => document.querySelector('[data-testid=run-go]').textContent === 'Run'""", timeout=20000)
            assert pg.query_selector(q("run-busy")) is None
            d = delete_state(pg)
            assert d["disabled"] is True and d["reason"].startswith("NOT DELETABLE"), "Trip B: disabled by R2, not by busy"
            assert pg.evaluate("() => location.hash") == "#trips/trip_b_europe"
            # and on a DELETABLE other trip: enabled once the run is done
            go_trip(pg, UI)
            pg.click(q("run-go")); pg.wait_for_selector(q("run-busy"), timeout=10000)
            pg.click(q("tab-trips")); pg.wait_for_timeout(100)
            pg.click(q("trip-row-other-trip")); pg.wait_for_selector(q("trip-delete"), timeout=10000)
            assert delete_state(pg)["disabled"] is True
            pg.wait_for_function("""() => { const b = document.querySelector('[data-testid=trip-delete]'); return b && !b.disabled; }""", timeout=20000)
            assert pg.evaluate("() => location.hash") == "#trips/other-trip"
            # the run's own trip keeps its chip
            go_trip(pg, UI)
            assert pg.query_selector(q("run-chip-0")) is not None
            assert pg.facts["errors"] == []


def test_F4b_a_run_finishing_while_the_new_trip_form_has_typed_input_wipes_nothing(browser):
    """The F4 fix exempts the new-trip view from the re-render so typed input
    survives - but the line above it, `if (S.tripId === id) go(...)`, still
    fires: the new-trip view keeps S.tripId, so when the run's trip is the
    selected one (the usual case) the page is navigated to the run result and
    the form is gone; `+ New trip` then starts a fresh S.nt. The input IS
    wiped. Pre-existing line (622d914:673), surfaced by this re-test."""
    with st_server("slow_run") as srv:
        with patched_page(browser, srv.port) as pg:
            go_trip(pg, UI)
            pg.click(q("mode-offline")); pg.wait_for_timeout(100)
            pg.click(q("run-go")); pg.wait_for_selector(q("run-busy"), timeout=10000)
            pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            pg.fill(q("nt-name"), "typed-while-running")
            pg.fill(q("nt-leg-1-origin"), "LH")   # half-typed
            pg.select_option(q("nt-cabin"), "J")
            pg.focus(q("nt-leg-1-destination")); pg.keyboard.type("SF")
            pg.wait_for_timeout(5500)
            where = pg.evaluate("() => location.hash")
            v = nt_values(pg)
            if v["name"] is None:
                # navigated away: is the typed input at least still in S.nt?
                pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
                kept = nt_values(pg)
                print("RUN FINISHED WHILE TYPING: hash", where, "form gone; after + New trip the name is", repr(kept["name"]))
            assert where == "#new-trip", ("the run's completion navigated away from the form", where)
            assert v["name"] == "typed-while-running" and v["origin"] == "LH" and v["destination"] == "SF" and v["cabin"] == "J", v
            assert v["focused"] == "nt-leg-1-destination", v["focused"]
            # the search view keeps its own state as well
            run_search(pg)
            pick_first_cell(pg); pg.keyboard.press("Escape")
            pg.click(q("tab-trips")); pg.wait_for_timeout(100)
            pg.click(q("trip-row-" + UI)); pg.wait_for_selector(q("run-go"), timeout=10000)
            pg.click(q("run-go")); pg.wait_for_selector(q("run-busy"), timeout=10000)
            pg.click(q("tab-search")); pg.wait_for_timeout(300)
            pg.wait_for_timeout(5500)
            assert pg.evaluate("() => document.querySelectorAll('td.cab.sel').length") == 1
            assert pg.facts["errors"] == []


# ------------------------------------------------------- placeholder and C5


def test_F5_no_cash_placeholder_and_one_coral_per_surface(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            def prim():
                return pg.evaluate("""() => Array.from(document.querySelectorAll('.btn-primary'))
                    .filter((b) => b.offsetParent !== null).map((b) => b.getAttribute('data-testid'))""")
            assert prim() == ["search-run", "search-add-trip"], "the disabled Add as trip is coral too (C5 counts it)"
            pick_first_cell(pg)
            assert sorted(prim()) == ["search-add-trip", "search-run"], prim()
            cls = pg.evaluate("() => document.querySelector('[data-testid=drawer-to-trip]').className")
            assert cls == "btn"
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            assert sorted(prim()) == ["search-add-trip", "search-run"]
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            ph = pg.evaluate("() => Array.from(document.querySelectorAll('[data-testid=new-trip-form] input')).map((i) => [i.getAttribute('data-testid'), i.placeholder])")
            assert ("nt-leg-1-cash", "") in [tuple(x) for x in ph], ph
            assert nt_values(pg)["hint"] == "required: the one thing a search cannot know"
            assert "2400" not in (text(pg, q("new-trip-form")) or "")
            # the typed form: the other placeholders (SFO/LHR/YYYY-MM-DD) are still there, cash has none
            pg.click(q("tab-trips")); pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            ph = dict(tuple(x) for x in pg.evaluate("() => Array.from(document.querySelectorAll('[data-testid=new-trip-form] input')).map((i) => [i.getAttribute('data-testid'), i.placeholder])"))
            assert ph["nt-leg-1-cash"] == "" and ph["nt-leg-1-date"] == "YYYY-MM-DD" and ph["nt-leg-1-origin"] == "SFO"
