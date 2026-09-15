"""
C. Search -> trip in the browser: the selection, the prefill, hostile rows,
the request bodies, byte identity three ways, and the builder's refusals
through the prefilled form.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (PY, ROOT, SHOTS, add_box, cells, doc_widths, g, nt_errors, nt_values, patched_page,
                      pick_cell, pick_first_cell, preview, q, run_search, st_server, text, trip_ids, write_trip)

CELL = "cell-2027-01-15-aeroplan-Y"
P2 = "Pick an award in the results first."


def _focused(pg):
    return pg.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-testid')")


def _drawer_open(pg):
    return text(pg, "#drawer-search") is not None


def _sel_ids(pg):
    return [c["id"] for c in cells(pg) if c["sel"]]


# ----------------------------------------------------------- selection


def test_C1_the_selection_state_machine_in_every_order(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            assert add_box(pg) == {"note": P2, "disabled": True, "present": True}
            assert _sel_ids(pg) == []
            # click -> drawer, highlight, P3
            pick_first_cell(pg)
            assert _drawer_open(pg) and _sel_ids(pg) == [CELL]
            assert add_box(pg)["note"] == "Picked: SFO→MAD · 2027-01-15 · Air Canada Aeroplan · Y"
            assert add_box(pg)["disabled"] is False
            # Esc -> drawer closed, selection kept, focus on the cell
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            assert not _drawer_open(pg) and _sel_ids(pg) == [CELL] and _focused(pg) == CELL
            assert add_box(pg)["disabled"] is False
            # × -> same
            pg.click(q(CELL)); pg.wait_for_timeout(200)
            pg.click("#drawer-search .x"); pg.wait_for_timeout(200)
            assert not _drawer_open(pg) and _sel_ids(pg) == [CELL] and _focused(pg) == CELL
            # Trips tab and back: kept
            pg.click(q("tab-trips")); pg.wait_for_timeout(200)
            pg.click(q("tab-search")); pg.wait_for_timeout(300)
            assert _sel_ids(pg) == [CELL] and add_box(pg)["disabled"] is False
            assert add_box(pg)["note"].startswith("Picked: ")
            # New trip tab (hash) and back: kept
            pg.evaluate("() => { location.hash = '#new-trip'; }"); pg.wait_for_timeout(200)
            pg.click(q("tab-search")); pg.wait_for_timeout(300)
            assert _sel_ids(pg) == [CELL]
            # Show map -> cleared, P2, disabled; Show results -> still cleared
            pg.click(q("search-pane-toggle")); pg.wait_for_timeout(300)
            assert pg.query_selector(q("search-add-trip-box")) is None, "the box goes with the table"
            pg.click(q("search-pane-toggle")); pg.wait_for_timeout(300)
            assert _sel_ids(pg) == [] and add_box(pg) == {"note": P2, "disabled": True, "present": True}
            # pick again, then a new run -> cleared
            pick_first_cell(pg)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            assert _sel_ids(pg) == [CELL]
            run_search(pg)
            assert _sel_ids(pg) == [] and add_box(pg)["disabled"] is True
            # pick, Add as trip, browser Back -> the search still has the pick (D17)
            pick_first_cell(pg)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            assert pg.evaluate("() => location.hash") == "#new-trip"
            pg.go_back(); pg.wait_for_timeout(300)
            assert pg.evaluate("() => location.hash") == "#search"
            assert _sel_ids(pg) == [CELL] and add_box(pg)["disabled"] is False
            # the form is still prefilled when going forward again (S.nt persists)
            pg.go_forward(); pg.wait_for_timeout(300)
            assert nt_values(pg)["name"] == "sfo-mad-2027-01-15"
            assert pg.facts["errors"] == []


def test_C2_keyboard_only_from_the_table_to_the_cash_field(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            pg.focus(q(CELL))
            pg.keyboard.press("Space"); pg.wait_for_timeout(250)
            assert _drawer_open(pg) and _sel_ids(pg) == [CELL]
            assert _focused(pg) is None or _focused(pg) != CELL, "focus moved into the drawer"
            pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
            assert _focused(pg) == CELL and _sel_ids(pg) == [CELL]
            # Tab forward until the add button has focus (bounded)
            for _ in range(40):
                if _focused(pg) == "search-add-trip":
                    break
                pg.keyboard.press("Tab")
            assert _focused(pg) == "search-add-trip", _focused(pg)
            pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
            v = nt_values(pg)
            assert v["focused"] == "nt-leg-1-cash" and v["cash"] == ""
            # Enter on the cell as well
            pg.click(q("tab-search")); pg.wait_for_timeout(200)
            pg.focus(q(CELL)); pg.keyboard.press("Enter"); pg.wait_for_timeout(250)
            assert _drawer_open(pg)
            # The drawer's own button leads to the same form
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            assert nt_values(pg)["focused"] == "nt-leg-1-cash"


def test_C3_both_buttons_build_the_same_form_and_a_j_cell_gives_a_j_trip(browser):
    with st_server("hostile_search") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg, "SFO", "MAD", "2027-01-15")
            pick_cell(pg, "-J")
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            a = nt_values(pg)
            assert a["cabin"] == "J" and a["legcabin"] == "" and a["legcabinLabel"] == "trip cabin (J)"
            assert a["name"] == "sfo-mad-2027-01-15" and a["cash"] == "" and a["legs"] == 1
            assert a["origin"] == "SFO" and a["destination"] == "MAD" and a["date"] == "2027-01-15"
            assert a["hint"] == "required: the one thing a search cannot know"
            assert a["prefill"].startswith("Prefilled from the search SFO→MAD · 2027-01-15 · ")
            assert a["prefill"].endswith("· J. The award price the search showed is NOT written into this trip: only a "
                                         "LIVE or REPLAY run can price it. Type the cash fare you found - the search cannot know it.")
            pg.click(q("tab-search")); pg.wait_for_timeout(200)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            b = nt_values(pg)
            assert a == b
            # the search cost (70000 J / 50000 Y) is nowhere on the form
            form = text(pg, q("new-trip-form"))
            assert "70,000" not in form and "70000" not in form and "50,000" not in form and "3236" not in form
            assert "32.36" not in form


# -------------------------------------------------------------- hostile


HOSTILE_DATES = [
    ("<img src=x onerror=\"window.__pwned=1\">", "not_iso"),
    ("2027-13-45", "iso_shape"),
    ("2027-02-30", "iso_shape"),
    ("‮2027-01-15‬", "not_iso"),
    ("2027-01-15T00:00:00Z", "not_iso"),
    ("15-01-2027", "not_iso"),
    ("D" * 5000, "not_iso"),
    ("", "not_iso"),
    (None, "not_iso"),
    (12345, "not_iso"),
    ("2020-01-15", "iso_shape"),
]


@pytest.mark.parametrize("date,kind", HOSTILE_DATES)
def test_C4_a_hostile_row_date_never_crashes_the_form_or_names_the_trip(browser, date, kind):
    def patch(body, req):
        for row in body.get("rows") or []:
            row["date"] = date
            row["program"] = '<b>P</b>‮PROG‬' + "N" * 3000
        return body

    with st_server("hostile_search") as srv:
        with patched_page(browser, srv.port, patches={"/api/search/run": patch}) as pg:
            run_search(pg)
            pick_first_cell(pg)
            note = add_box(pg)["note"]
            assert note.startswith("Picked: SFO→MAD · ") and "<b>P</b>" in note
            assert pg.evaluate("() => !!window.__pwned") is False
            assert pg.evaluate("() => document.querySelectorAll('img').length + Array.from(document.querySelectorAll('b')).filter((b) => b.textContent === 'P').length") == 0
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            v = nt_values(pg)
            assert v is not None and v["origin"] == "SFO" and v["destination"] == "MAD"
            assert v["cabin"] in ("Y", "W", "J", "F") and v["cash"] == ""
            assert v["date"] == ("" if date is None else str(date)), "the literal value, nothing else"
            if kind == "iso_shape":
                assert v["name"] == f"sfo-mad-{date}"
            else:
                assert v["name"] == "", v["name"]
            assert "<b>P</b>" in v["prefill"] and pg.evaluate("() => !!window.__pwned") is False
            assert pg.evaluate("() => document.querySelectorAll('img, script').length + Array.from(document.querySelectorAll('b')).filter((b) => b.textContent === 'P').length") == 2, "only the two shell scripts"
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            errs = dict(nt_errors(pg))
            assert "nt-error-1-date" in errs, errs
            assert "Leg 1 date" in errs["nt-error-1-date"]
            assert pg.query_selector(q("nt-echo")) is None
            assert pg.facts["errors"] == [], pg.facts["errors"]
            names = set(srv.ls())
            assert not any(n.startswith("sfo-mad-2027-13") or n.startswith("sfo-mad-2020") for n in names)


def test_C4b_a_hostile_program_and_source_code_in_the_cell_testid_and_the_notes(browser):
    with st_server("hostile_search") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            ids = [c["id"] for c in cells(pg)]
            assert ids and all(i.startswith("cell-2027-01-15-") for i in ids), ids
            pick_first_cell(pg)
            assert pg.evaluate("() => !!window.__pwned") is False
            note = add_box(pg)["note"]
            assert note.startswith("Picked: SFO→MAD · 2027-01-15 · ")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            v = nt_values(pg)
            assert v["name"] == "sfo-mad-2027-01-15"
            assert pg.evaluate("() => !!window.__pwned") is False
            assert pg.evaluate("() => document.querySelectorAll('img').length") == 0
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], w


def test_C4c_a_row_with_missing_fields_and_a_lowercase_route(browser):
    """`run.route` echoes the VALIDATED request (uppercased); a row with no
    program, no source_code and a missing cabins entry must not crash."""
    def patch(body, req):
        body["route"]["origin"] = "sfo"
        body["route"]["destination"] = "madx"
        for row in body.get("rows") or []:
            row["program"] = None
            row["source_code"] = None
        return body

    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port, patches={"/api/search/run": patch}) as pg:
            run_search(pg)
            ids = [c["id"] for c in cells(pg)]
            assert ids == ["cell-2027-01-15-none-Y"], ids
            pg.click(q(ids[0])); pg.wait_for_timeout(250)
            assert add_box(pg)["note"] == "Picked: sfo→madx · 2027-01-15 · (program not named) · Y"
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            v = nt_values(pg)
            assert v["name"] == "sfo-madx-2027-01-15" and v["origin"] == "sfo" and v["destination"] == "madx"
            assert "(program not named)" in v["prefill"]
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            errs = dict(nt_errors(pg))
            assert "nt-error-1-destination" in errs, errs
            assert pg.facts["errors"] == []


# --------------------------------------------------------------- bodies


def test_C5_the_draft_and_create_bodies_carry_exactly_the_typed_keys_and_no_award(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg, "SFO", "JFK", "2027-03-05")
            pick_first_cell(pg)
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            assert nt_values(pg)["name"] == "sfo-jfk-2027-03-05"
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            assert pg.query_selector(q("nt-echo")) is not None
            write_trip(pg)
            posts = [p for p in pg.facts["posts"] if p["url"] in ("/api/trips/draft", "/api/trips/create")]
            assert [p["url"] for p in posts] == ["/api/trips/draft", "/api/trips/create"]
            draft, create = posts[0]["body"], posts[1]["body"]
            assert draft == {"name": "sfo-jfk-2027-03-05", "cabin": "Y",
                             "legs": [{"origin": "SFO", "destination": "JFK", "date": "2027-03-05", "cabin": "Y", "cash": "2400"}]}
            assert set(create) == {"name", "cabin", "legs", "draft_hash"}
            assert {k: v for k, v in create.items() if k != "draft_hash"} == draft
            blob = json.dumps(pg.facts["posts"])
            for needle in ("50000", "50,000", "32.36", "532", "from_search", "run_id", "program", "Aeroplan", "focus", "seats"):
                assert needle not in blob.split('"/api/search/run"')[-1] or needle in ("program",), needle
            body_after_search = json.dumps([p for p in pg.facts["posts"] if p["url"].startswith("/api/trips")])
            for needle in ("50000", "32.36", "from_search", "run_id", "Aeroplan", "focus", "seats", "taxes", "miles"):
                assert needle not in body_after_search, needle
            path = srv.trips_dir / "sfo-jfk-2027-03-05.json"
            assert path.exists()
            fx = json.loads(path.read_text())
            assert all("points_candidates" not in leg for leg in fx["legs"])
            raw = path.read_text()
            assert "50000" not in raw and "32.36" not in raw and "Aeroplan" not in raw and "aeroplan" not in raw
            assert pg.evaluate("() => location.hash") == "#trips/sfo-jfk-2027-03-05"
            assert "sfo-jfk-2027-03-05" in trip_ids(pg)


def test_C6_byte_identity_three_ways_prefilled_typed_and_the_cli(browser, tmp_path):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            # 1. prefilled
            run_search(pg, "SFO", "JFK", "2027-03-05")
            pick_first_cell(pg)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            echo_prefilled = text(pg, q("nt-echo"))
            write_trip(pg)
            path = srv.trips_dir / "sfo-jfk-2027-03-05.json"
            prefilled = path.read_bytes()
            # delete it from the page, so the same name can be typed
            pg.wait_for_selector(q("trip-delete"), timeout=10000)
            pg.click(q("trip-delete")); pg.wait_for_selector(q("delete-confirm-go"), timeout=10000)
            pg.click(q("delete-confirm-go")); pg.wait_for_selector(q("trip-deleted"), timeout=10000)
            assert not path.exists()
            # 2. typed by hand
            pg.click(q("tab-trips")); pg.wait_for_timeout(200)
            pg.click(q("new-trip")); pg.wait_for_selector(q("nt-name"), timeout=10000)
            v = nt_values(pg)
            assert v["name"] == "" and v["prefill"] is None and v["hint"] is None, "a fresh form, no note, no hint"
            pg.fill(q("nt-name"), "sfo-jfk-2027-03-05")
            pg.fill(q("nt-leg-1-origin"), "SFO")
            pg.fill(q("nt-leg-1-destination"), "JFK")
            pg.fill(q("nt-leg-1-date"), "2027-03-05")
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            assert text(pg, q("nt-echo")) == echo_prefilled
            write_trip(pg)
            typed = path.read_bytes()
            assert typed == prefilled
            posts = [p["body"] for p in pg.facts["posts"] if p["url"] == "/api/trips/create"]
            assert len(posts) == 2 and posts[0] == posts[1]
    # 3. the CLI: argv -> build_parser -> dispatch -> run_new_trip, on the pinned day, into tmp
    code = (
        "import sys; from pathlib import Path\n"
        "from tests import _cli_golden as g\n"
        "from src import trip_builder\n"
        f"trip_builder.FIXTURE_DIR = Path({str(tmp_path)!r})\n"
        "trip_builder.date = g._PinnedDate\n"
        "sys.argv = ['prog', '--new-trip', 'sfo-jfk-2027-03-05', '--leg', 'SFO:JFK:2027-03-05:2400', '--cabin', 'Y']\n"
        "from src.main import main\n"
        "sys.exit(main())\n"
    )
    env = dict(os.environ, NO_COLOR="1", COLUMNS="200")
    r = subprocess.run([PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    cli = (tmp_path / "sfo-jfk-2027-03-05.json").read_bytes()
    assert cli == prefilled, "the CLI's file differs from the page's"
    assert not (ROOT / "tests" / "fixtures" / "trips" / "sfo-jfk-2027-03-05.json").exists()


# ------------------------------------------------------------ refusals


REFUSALS = [
    ("date", "15-01-2027", "nt-error-1-date"),
    ("date", "2027-02-30", "nt-error-1-date"),
    ("date", "2020-01-15", "nt-error-1-date"),
    ("date", "", "nt-error-1-date"),
    ("destination", "SFO", "nt-error-1-destination"),
    ("destination", "XXX", "nt-error-1-destination"),
    ("destination", "MADR", "nt-error-1-destination"),
    ("origin", "sf", "nt-error-1-origin"),
    ("cash", "0", "nt-error-1-cash"),
    ("cash", "-1", "nt-error-1-cash"),
    ("cash", "abc", "nt-error-1-cash"),
    ("cash", "2,400", "nt-error-1-cash"),
    ("cash", "nan", "nt-error-1-cash"),
    ("cash", "inf", "nt-error-1-cash"),
    ("cash", "$2400", "nt-error-1-cash"),
    ("cash", "", "nt-error-1-cash"),
    ("name", "a/b", "nt-error-trip-name"),
    ("name", "../escape", "nt-error-trip-name"),
    ("name", "", "nt-error-trip-name"),
    ("name", "x" * 121, "nt-error-trip-name"),
    ("name", "trip_b_europe", "exists"),
]


@pytest.mark.parametrize("field,value,err", REFUSALS)
def test_C7_every_builder_refusal_is_reachable_through_the_prefilled_form_and_writes_nothing(browser, field, value, err):
    with st_server("ui_built") as srv:
        before = srv.ls()
        with patched_page(browser, srv.port) as pg:
            run_search(pg, "SFO", "JFK", "2027-03-05")
            pick_first_cell(pg)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            pg.fill(q("nt-leg-1-cash"), "2400")
            sel = q("nt-name") if field == "name" else q(f"nt-leg-1-{field}")
            pg.fill(sel, value)
            preview(pg)
            errs = dict(nt_errors(pg))
            if err == "exists":
                assert errs == {}, errs
                write_trip(pg)
                errs = nt_errors(pg)
                msgs = [m for t, m in errs if t == "nt-error-trip-name"]
                assert len(msgs) == 2 and "already exists" in msgs[0] and "--force" in msgs[0], errs
                assert msgs[1] == "Choose another name."
                assert pg.evaluate("() => location.hash") == "#new-trip"
            else:
                assert err in errs, (field, value, errs)
                assert pg.query_selector(q("nt-echo")) is None and pg.query_selector(q("nt-write")) is None
                # the refusal is the builder's own wording, inline, not a banner
                assert text(pg, q("banner-error")) is None
                assert errs[err].strip() != ""
            # the prefill note survives the refusal; nothing was written
            assert nt_values(pg)["prefill"] is not None
        assert srv.ls() == before, "a refusal wrote or moved something"


def test_C7b_a_large_finite_cash_and_a_fractional_cent_are_accepted(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg, "SFO", "JFK", "2027-03-05")
            pick_first_cell(pg)
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            pg.fill(q("nt-leg-1-cash"), "1e9")
            preview(pg)
            assert "$1,000,000,000.00" in (text(pg, q("nt-echo")) or "")
            pg.fill(q("nt-leg-1-cash"), "395.999")
            preview(pg)
            assert "395.999" in (text(pg, q("nt-echo")) or "") or "$395.999" in (text(pg, q("nt-echo")) or "") or "396.00" in (text(pg, q("nt-echo")) or "")


# ------------------------------------------------------- no points, ever


def test_C8_a_prefilled_trip_has_no_points_price_and_offline_says_so(browser):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg, "SFO", "JFK", "2027-03-05")
            pick_first_cell(pg)
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            pg.fill(q("nt-leg-1-cash"), "2400")
            preview(pg)
            write_trip(pg)
            pg.wait_for_selector(q("trip-detail"), timeout=10000)
            flags = text(pg, q("trip-flags")) or ""
            assert "THIS FIXTURE CARRIES NO POINTS PRICES AT ALL" in flags
            legs = text(pg, q("fixture-legs")) or ""
            assert "none" in legs.lower()
            pg.click(q("mode-offline")); pg.wait_for_timeout(100)
            pg.click(q("run-go"))
            pg.wait_for_selector(q("trip-result"), timeout=60000)
            pg.wait_for_timeout(300)
            hv = text(pg, q("headline-value")) or ""
            assert "0.00%" in hv and "(none)" in hv, hv
            page = text(pg, "#trip-main") or ""
            assert "no points prices" in page.lower() or "never priced" in page.lower(), page[:500]
            raw = (srv.trips_dir / "sfo-jfk-2027-03-05.json").read_text()
            fx = json.loads(raw)
            assert all("points_candidates" not in leg for leg in fx["legs"])


# ------------------------------------------------------- filter and widths


def test_C9_the_cabin_filter_hiding_the_picked_cabin_keeps_the_pick(browser):
    """Plan 7 hands this to the tester: decided in the report (Low, not a
    defect: the box still NAMES the pick, and the button adds what it names)."""
    with st_server("hostile_search") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            pick_cell(pg, "-J")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.select_option(q("search-cabin"), "Y"); pg.wait_for_timeout(300)
            assert [c["id"] for c in cells(pg) if c["id"].endswith("-J")] == []
            b = add_box(pg)
            assert b["disabled"] is False and b["note"].endswith(" · J"), b
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            assert nt_values(pg)["cabin"] == "J", "the button adds what the box names"


@pytest.mark.parametrize("width", [400, 899, 1180, 1440])
def test_C10_the_box_does_not_widen_the_page_and_the_drawer_button_is_reachable(browser, width):
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port, width=width, height=900) as pg:
            run_search(pg)
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"] and w["bodySW"] <= w["cw"], (width, w)
            pick_first_cell(pg)
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], (width, w)
            assert pg.is_visible(q("drawer-to-trip"))
            pg.screenshot(path=str(SHOTS / f"c10-search-picked-{width}.png"), full_page=False)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
            assert pg.is_visible(q("search-add-trip"))
            bx = pg.evaluate("""() => { const r = document.querySelector('[data-testid="search-add-trip-box"]').getBoundingClientRect();
                return { right: r.right, w: r.width }; }""")
            assert bx["right"] <= width + 1, (width, bx)
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            w = doc_widths(pg)
            assert w["sw"] <= w["cw"], (width, w)
            pg.screenshot(path=str(SHOTS / f"c10-prefilled-{width}.png"), full_page=False)
            assert pg.facts["errors"] == []


# ------------------------------------------------------ map, legs, cancel


def test_C11_the_map_still_works_with_a_picked_row_and_the_pick_goes_with_the_table(browser):
    with st_server("ui_built", hubs=True) as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            pick_first_cell(pg)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(100)
            pg.click(q("search-pane-toggle")); pg.wait_for_timeout(600)
            assert pg.query_selector(q("map-svg")) is not None
            assert pg.query_selector(q("map-route")) is not None, "the route line of the search that ran"
            assert len(pg.query_selector_all("g.mk")) > 0
            # a marker click on the map still fills a field (the map is unbroken)
            pg.click(q("map-hub-SYD") + " circle.dot, " + q("map-hub-SYD") + " circle.cdot", force=True)
            pg.wait_for_timeout(300)
            vals = pg.evaluate("() => [document.querySelector('[data-testid=\"search-from\"]').value, document.querySelector('[data-testid=\"search-to\"]').value]")
            assert "SYD" in vals, vals
            pg.click(q("search-pane-toggle")); pg.wait_for_timeout(300)
            assert _sel_ids(pg) == [] and add_box(pg)["disabled"] is True
            assert pg.facts["errors"] == []


def test_C12_add_leg_and_remove_the_prefilled_leg_keeps_the_note_and_moves_the_hint(browser):
    """Recorded: the note says what was prefilled even after that leg is
    removed, and the hint sits under whichever leg is first. Judged Low in
    the report (the note is history, the hint is a nudge; nothing is sent)."""
    with st_server("ui_built") as srv:
        with patched_page(browser, srv.port) as pg:
            run_search(pg)
            pick_first_cell(pg)
            pg.click(q("drawer-to-trip")); pg.wait_for_timeout(300)
            pg.click(q("nt-add-leg")); pg.wait_for_timeout(200)
            v = nt_values(pg)
            assert v["legs"] == 2 and v["hint"] is not None
            hint2 = pg.evaluate("""() => { const n = document.querySelector('[data-testid="nt-leg-2-cash"]');
                const h = n && n.parentElement.querySelector('.hint'); return h ? h.textContent : null; }""")
            assert hint2 is None, "the hint is on the prefilled leg only"
            pg.fill(q("nt-leg-2-origin"), "MAD"); pg.fill(q("nt-leg-2-destination"), "AMS")
            pg.fill(q("nt-leg-2-date"), "2027-01-19"); pg.fill(q("nt-leg-2-cash"), "120")
            pg.locator('[data-testid="nt-leg-1"] button').last.click(); pg.wait_for_timeout(200)
            v = nt_values(pg)
            assert v["legs"] == 1 and v["origin"] == "MAD"
            print("AFTER REMOVING THE PREFILLED LEG: note", v["prefill"] is not None, "hint", v["hint"])
            pg.fill(q("nt-name"), "mad-ams")
            preview(pg)
            assert pg.query_selector(q("nt-echo")) is not None
            body = [p["body"] for p in pg.facts["posts"] if p["url"] == "/api/trips/draft"][-1]
            assert body["legs"] == [{"origin": "MAD", "destination": "AMS", "date": "2027-01-19", "cabin": "Y", "cash": "120"}]
            # Cancel drops the form; the search keeps its pick
            pg.locator('[data-testid="new-trip-form"] button:has-text("Cancel")').click(); pg.wait_for_timeout(300)
            pg.click(q("tab-search")); pg.wait_for_timeout(300)
            assert _sel_ids(pg) == [CELL]
            pg.click(q("search-add-trip")); pg.wait_for_timeout(300)
            v = nt_values(pg)
            assert v["legs"] == 1 and v["origin"] == "SFO" and v["name"] == "sfo-mad-2027-01-15", "a fresh prefill"
