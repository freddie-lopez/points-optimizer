"""
K. RE-TEST 2: attacking the round-1 FIXES, not re-running the round-1 probes.

Each section takes one fix and asks what it does at the edges the fix's own
argument depends on: the sensitivity re-decide when the flag does NOT change,
values other than cash that still overflow, the exclusive create under real
concurrency, names at the new limit and beside it, and what the delivered tree
carries.

RED = a defect that exists. GREEN = the fix held up.
"""
import copy
import io
import json
import os
import subprocess
import sys
import threading
import unicodedata

import pytest
from rich.console import Console

from conftest import LIVE, OFFLINE, ROOT, g

TRIP_C = ROOT / "tests" / "fixtures" / "trips" / "trip_c_lon_mry_surcharge.json"
TRIP_A = ROOT / "tests" / "fixtures" / "trips" / "trip_a_mry_nyc.json"
LEG = {"origin": "SFO", "destination": "LHR", "date": "2027-01-15", "cash": "2400"}


# =========================================================== H-1: sensitivity


def uk_leg_fixture(tmp_path, program, metal, points, cash, name="probe"):
    """A LHR departure in J whose chosen path's surcharge the table models as a
    BAND, so the leg carries a range AND UK Air Passenger Duty at once."""
    src = json.loads(TRIP_C.read_text())
    leg = copy.deepcopy([l for l in src["legs"] if l["id"] == "C2"][0])
    cand = leg["points_candidates"][0]
    cand.update(program=program, marketing_carrier=metal, operating_carrier=metal,
                points=points, label=f"PROBE SYNTHETIC: {program} on {metal} metal")
    leg.update(id="P1", origin="LHR", destination="JFK",
               description="PROBE SYNTHETIC: LHR->JFK business")
    leg["cash_options"] = [{"label": f"PROBE SYNTHETIC ${cash} fare", "amount": float(cash),
                            "currency": "USD"}]
    fixture = {"id": name, "name": "PROBE " + name, "description": "SYNTHETIC tester probe.",
               "source": "SYNTHETIC. Never quote.", "trip_level_flags": ["SYNTHETIC probe."],
               "legs": [leg]}
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fixture, indent=1))
    return path


def score(path, extra=()):
    from src import main as cli

    argv = ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
            "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15", *extra]
    sink, buf = [], io.StringIO()
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), sink)
    return code, sink[0], buf.getvalue()


def sensitive_reason(result):
    for r in result.reasons:
        if r.code == "VERDICT_SENSITIVE":
            return r.detail
    return ""


def test_K1_a_band_that_straddles_the_fare_BEFORE_and_AFTER_the_duty_quotes_the_scored_figures(
        tmp_path, pinned):
    """The re-decide returns early when the flag does not change, so the reason
    keeps the figures from before UK APD moved the band."""
    # BA Avios on IB metal: one-way band $522.50-$1,045.00, so the band is wider
    # than the $330.38 duty and both ends straddle $1,450 cash.
    path = uk_leg_fixture(tmp_path, "British Airways Executive Club", "IB", 50000, 1450,
                          "probe_double_straddle")
    code, run, text = score(path)
    r = run.results[0]
    assert r.verdict_sensitive is True
    assert r.points_score_low_usd < r.cash_total_score_usd < r.points_score_high_usd
    detail = sensitive_reason(r)
    assert f"${r.points_score_low_usd:,.2f}" in detail and \
        f"${r.points_score_high_usd:,.2f}" in detail, (
        f"the VERDICT SENSITIVE reason quotes the band as it stood BEFORE UK APD "
        f"($330.38) was added to both ends: {detail}")
    assert "Air Passenger Duty" in detail


def test_K2_a_flip_the_duty_removes_clears_the_marker(tmp_path, pinned):
    code, run, text = score(uk_leg_fixture(
        tmp_path, "Virgin Atlantic Flying Club", "VS", 50000, 800, "probe_flip_removed"))
    r = run.results[0]
    assert r.verdict_sensitive is False and sensitive_reason(r) == ""
    assert "VERDICT SENSITIVE" not in text
    assert r.points_score_low_usd > r.cash_total_score_usd


def test_K3_a_flip_the_duty_creates_is_marked_and_names_the_duty(tmp_path, pinned):
    """Round 1's H-1, the other way round."""
    code, run, text = score(uk_leg_fixture(
        tmp_path, "Virgin Atlantic Flying Club", "VS", 50000, 1150, "probe_flip_added"))
    r = run.results[0]
    assert r.verdict_sensitive is True
    detail = sensitive_reason(r)
    assert f"${r.points_score_low_usd:,.2f}" in detail
    assert f"${r.points_score_high_usd:,.2f}" in detail
    assert "Air Passenger Duty of $330.38" in detail
    assert "VERDICT SENSITIVE" in text


def test_K4_the_marker_survives_into_the_ui_json_with_the_scored_figures(ui, tmp_path):
    """Whatever the reason says, the page shows it: the drawer lists reason
    codes verbatim."""
    path = uk_leg_fixture(tmp_path, "Virgin Atlantic Flying Club", "VS", 50000, 1150, "probe_ui")
    import shutil

    shutil.copy(path, ui.trips_dir / "probe_ui.json")
    run = ui.run_trip("probe_ui", OFFLINE)[1].json()
    leg = run["legs"][0]
    detail = [r["detail"] for r in leg["reasons"] if r["code"] == "VERDICT_SENSITIVE"]
    assert detail, leg["reasons"]
    assert f"{leg['numbers']['score_low']:,.2f}" in detail[0]
    assert f"{leg['numbers']['score_high']:,.2f}" in detail[0]
    assert leg["verdict"]["sensitive"] is True


def test_K5_an_apd_leg_whose_taxes_are_unknown_is_still_not_a_settled_verdict(tmp_path, pinned):
    """APD on a leg whose cash side is UNKNOWN: the floor must carry the duty
    and nothing may be presented as settled."""
    src = json.loads(TRIP_C.read_text())
    leg = copy.deepcopy([l for l in src["legs"] if l["id"] == "C2"][0])
    cand = leg["points_candidates"][0]
    cand.update(program="Qatar Airways Privilege Club", marketing_carrier="QR",
                operating_carrier="QR", points=50000,
                label="PROBE SYNTHETIC: QR award LHR->JFK")
    leg.update(id="P1", origin="LHR", destination="JFK", description="PROBE: unknown taxes")
    leg["cash_options"] = [{"label": "PROBE $1,200", "amount": 1200.0, "currency": "USD"}]
    path = tmp_path / "probe_unknown_taxes.json"
    path.write_text(json.dumps({"id": "probe_unknown_taxes", "name": "PROBE",
                                "description": "SYNTHETIC", "source": "SYNTHETIC",
                                "trip_level_flags": ["SYNTHETIC"], "legs": [leg]}, indent=1))
    code, run, text = score(path)
    r = run.results[0]
    assert r.verdict != "points" or r.verdict_sensitive, (
        "a leg with an unknown cash side was recommended on points as settled")
    if r.apd_added_usd:
        assert "APD" in text.upper()


def test_K6_a_sensitivity_flag_and_the_text_agree_on_every_committed_fixture(ui):
    """`verdict_sensitive` is now recomputed after APD. It must still be true
    exactly when the CLI's own text says so - the text itself is pinned against
    the pre-UI CLI by test_ui_g_cli_parity.py, so this closes the loop on the
    one field no golden prints."""
    for trip in ("trip_a_mry_nyc", "trip_b_europe", "trip_c_lon_mry_surcharge"):
        run = ui.run_trip(trip, OFFLINE)[1].json()
        flagged = sorted(l["id"] for l in run["legs"] if l["verdict"]["sensitive"])
        in_text = sorted(
            l["id"] for l in run["legs"]
            if any("VERDICT SENSITIVE" in x["text"] for x in l["cli_lines"])
        )
        assert flagged == in_text, (trip, flagged, in_text)
        tag = sorted(
            l["id"] for l in run["legs"]
            if any("SENSITIVE" in s["text"].upper() for s in l["cells"]["verdict"]["segments"])
        )
        assert tag == flagged, (trip, tag, flagged)


# ================================================================ M-1: values


BROKEN = {
    "cash_huge": lambda fx: _flight(fx)["cash_options"][0].__setitem__("amount", 1e308),
    "cash_string": lambda fx: _flight(fx)["cash_options"][0].__setitem__("amount", "abc"),
    "cash_null": lambda fx: _flight(fx)["cash_options"][0].__setitem__("amount", None),
    "points_bigint": lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
        "points", 10 ** 400),
    "points_huge_float": lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
        "points", 1e308),
    "fee_huge": lambda fx: _flight(fx).__setitem__(
        "mandatory_fees", [{"label": "x", "amount": 1e308, "currency": "USD"}]),
    "legs_wrong_type": lambda fx: fx.__setitem__("legs", {"x": 1}),
    "travelers_huge": lambda fx: _flight(fx).__setitem__("travelers", 10 ** 20),
}


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def broken_fixture(tmp_path, name):
    fx = json.loads(TRIP_A.read_text())
    fx["id"] = name
    BROKEN[name](fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx, indent=1))
    return path


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_K7_a_hand_written_broken_fixture_is_one_clean_line_not_a_traceback(tmp_path, name):
    """M-1's claim: 'the CLI prints one line and exits 1 with no traceback'."""
    path = broken_fixture(tmp_path, name)
    env = dict(os.environ, HOME=str(tmp_path), POINTS_OPTIMIZER_ENV_FILE=str(tmp_path / "no.env"))
    env.pop("SEATS_AERO_KEY", None)
    p = subprocess.run([sys.executable, "-m", "src.main", "--trip-fixture", str(path),
                        "--offline", "--balance", "UR=160000", "--card",
                        "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
                       cwd=str(ROOT), capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    assert "Traceback (most recent call last)" not in out, (name, out[-700:])
    assert p.returncode in (0, 1, 3, 4), (name, p.returncode)


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_K8_the_ui_lists_a_broken_fixture_and_never_500s_on_it(ui, tmp_path, name):
    import shutil

    path = broken_fixture(tmp_path, name)
    shutil.copy(path, ui.trips_dir / path.name)
    listing = ui.get("/api/trips")
    assert listing.status == 200, listing.text[:300]
    row = [t for t in listing.json() if t["id"] == name]
    assert row, [t["id"] for t in listing.json()]
    detail = ui.get(f"/api/trips/{name}")
    assert detail.status in (200, 422), (name, detail.status, detail.text[:200])
    run = ui.post(f"/api/trips/{name}/run", OFFLINE)
    assert run.status != 500, (name, run.text[:300])


@pytest.mark.parametrize("cash", ["1e308", "1e307", "17976931348623157e292", "0.1e309",
                                  "2400.000000000000000001", "9007199254740993",
                                  "1e306", "-1e308", "nan", "inf"])
def test_K9_the_form_refuses_what_it_cannot_score_and_writes_nothing(ui, cash):
    before = sorted(p.name for p in ui.trips_dir.glob("*"))
    body = {"name": "probe_money", "cabin": "Y", "legs": [dict(LEG, cash=cash)]}
    draft = ui.post("/api/trips/draft", body)
    j = draft.json()
    if j.get("ok"):
        body["draft_hash"] = j["draft_hash"]
        created = ui.post("/api/trips/create", body)
        assert created.status == 200, (cash, created.text[:200])
        # Whatever it accepted must then score without an internal error.
        run = ui.post("/api/trips/probe_money/run", OFFLINE)
        assert run.status == 200, (cash, run.status, run.text[:200])
        assert run.json()["exit_code"] in (0, 3, 4)
    else:
        assert sorted(p.name for p in ui.trips_dir.glob("*")) == before, cash
        assert any("refus" in e["message"].lower() or "not a number" in e["message"].lower()
                   or "too large" in e["message"].lower() or "finite" in e["message"].lower()
                   for e in j["errors"]), (cash, j["errors"])


def test_K10_a_cli_valuation_that_makes_a_legal_fare_unscoreable_is_not_a_traceback(tmp_path):
    """The bound is taken at the DEFAULT valuation. `--valuation-cpp` moves it
    (the UI does not offer the flag; the CLI does)."""
    fx = json.loads(TRIP_A.read_text())
    fx["id"] = "probe_valuation"
    _flight(fx)["cash_options"][0]["amount"] = 1e300
    path = tmp_path / "probe_valuation.json"
    path.write_text(json.dumps(fx, indent=1))
    env = dict(os.environ, HOME=str(tmp_path), POINTS_OPTIMIZER_ENV_FILE=str(tmp_path / "no.env"))
    env.pop("SEATS_AERO_KEY", None)
    p = subprocess.run([sys.executable, "-m", "src.main", "--trip-fixture", str(path),
                        "--offline", "--balance", "UR=160000", "--card",
                        "Chase Sapphire Preferred", "--transfer-date", "2026-09-15",
                        "--valuation-cpp", "0.000001"],
                       cwd=str(ROOT), capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    assert "Traceback (most recent call last)" not in out, out[-700:]


# ============================================================== M-2: the write


def test_K11_eight_simultaneous_creates_of_one_name_leave_one_file_and_one_success(ui):
    bodies = []
    for i in range(8):
        body = {"name": "probe_excl", "cabin": "Y", "legs": [dict(LEG, cash=str(1000 + i))]}
        d = ui.post("/api/trips/draft", body).json()
        body["draft_hash"] = d["draft_hash"]
        bodies.append(body)
    barrier = threading.Barrier(len(bodies))
    out = []

    def fire(b):
        barrier.wait()
        out.append(ui.post("/api/trips/create", b))

    threads = [threading.Thread(target=fire, args=(b,)) for b in bodies]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    oks = [r for r in out if r.status == 200]
    assert len(oks) == 1, [r.status for r in out]
    assert all("already exists" in r.json().get("message", "") for r in out if r.status != 200)
    assert len(list(ui.trips_dir.glob("probe_excl*"))) == 1


def test_K12_a_refused_create_leaves_nothing_behind(ui, tmp_path):
    before = sorted(p.name for p in ui.trips_dir.glob("*"))
    # an existing name
    body = {"name": "trip_b_europe", "cabin": "Y", "legs": [dict(LEG)]}
    d = ui.post("/api/trips/draft", body).json()
    body["draft_hash"] = d["draft_hash"]
    assert ui.post("/api/trips/create", body).status == 409
    # a name the writer cannot use
    for name in ("probe_x" * 40, "../evil"):
        b2 = {"name": name, "cabin": "Y", "legs": [dict(LEG)]}
        d2 = ui.post("/api/trips/draft", b2).json()
        if d2.get("ok"):
            b2["draft_hash"] = d2["draft_hash"]
            assert ui.post("/api/trips/create", b2).status >= 400
    assert sorted(p.name for p in ui.trips_dir.glob("*")) == before
    assert not list(ui.trips_dir.glob("*.tmp")) and not list(ui.trips_dir.glob(".*"))


def test_K13_force_still_overwrites_for_the_cli(tmp_path):
    from src import trip_builder as tb

    fixture = tb.build_fixture("probe_force", [tb.FlightSpec("SFO", "LHR",
                                                             g.PINNED_TODAY.replace(year=2027),
                                                             2400.0)], [], 1, "Y",
                               today=g.PINNED_TODAY)
    first = tb.write_fixture(fixture, directory=tmp_path)
    again = tb.write_fixture(fixture, directory=tmp_path, force=True)
    assert first == again and first.is_file()
    with pytest.raises(tb.TripBuilderError):
        tb.write_fixture(fixture, directory=tmp_path)


# =============================================================== M-3: the name


@pytest.mark.parametrize("length", [118, 119, 120, 121, 122])
def test_K14_names_at_the_limit_are_written_and_addressable_or_refused(ui, length):
    name = "p" * length
    body = {"name": name, "cabin": "Y", "legs": [dict(LEG)]}
    d = ui.post("/api/trips/draft", body)
    j = d.json()
    if not j.get("ok"):
        assert length > 120, (length, j["errors"])
        return
    assert length <= 120, length
    body["draft_hash"] = j["draft_hash"]
    created = ui.post("/api/trips/create", body)
    assert created.status == 200, (length, created.text[:200])
    assert ui.get(f"/api/trips/{name}").status == 200, length
    assert name in [t["id"] for t in ui.get("/api/trips").json()]
    assert ui.post(f"/api/trips/{name}/run", OFFLINE).status == 200


@pytest.mark.parametrize("name", ["probe_café", "probe–dash", "probe space",
                                  "probe​zero", "ＰＲＯＢＥ", "probé",
                                  "probe.", "probe..x", "PROBE_upper"])
def test_K15_a_name_that_is_not_plain_ascii_is_refused_or_round_trips_exactly(ui, name):
    body = {"name": name, "cabin": "Y", "legs": [dict(LEG)]}
    j = ui.post("/api/trips/draft", body).json()
    if not j.get("ok"):
        return
    body["draft_hash"] = j["draft_hash"]
    created = ui.post("/api/trips/create", body)
    if created.status != 200:
        return
    trip_id = created.json()["id"]
    assert trip_id == name.strip(), (name, trip_id)
    assert ui.get(f"/api/trips/{trip_id}").status == 200, (name, trip_id)


def test_K16_two_names_that_normalise_to_each_other_do_not_collide_silently(ui):
    """On a case-insensitive or normalising filesystem (his Mac's APFS) two
    different names can be one file. Whatever happens, the app must not report
    two successes for one file."""
    pairs = [("probe_nfc_café", "probe_nfc_café"), ("probe_Case", "probe_case")]
    for a, b in pairs:
        ids = []
        for name in (a, b):
            body = {"name": name, "cabin": "Y", "legs": [dict(LEG)]}
            j = ui.post("/api/trips/draft", body).json()
            if not j.get("ok"):
                continue
            body["draft_hash"] = j["draft_hash"]
            r = ui.post("/api/trips/create", body)
            if r.status == 200:
                ids.append(r.json()["id"])
        files = sorted(p.name for p in ui.trips_dir.glob("probe_*"))
        assert len(ids) == len([f for f in files if f[:-5] in ids]), (a, b, ids, files)
        for trip_id in ids:
            assert ui.get(f"/api/trips/{trip_id}").status == 200, trip_id


# ================================================================== the lows


def test_K17_the_two_call_counts_are_two_numbers(ui, monkeypatch):
    """L-1: 'since launch' must not fall at midnight."""
    from datetime import date as real_date

    from src import seats_client

    ui.run_trip("trip_b_europe", LIVE)
    before = ui.get("/api/state").json()["calls"]
    assert before["since_launch"] == len(ui.net.calls) > 0
    assert before["spent_today"] == before["since_launch"]

    class Tomorrow(real_date):
        @classmethod
        def today(cls):
            return real_date.today().fromordinal(real_date.today().toordinal() + 1)

    monkeypatch.setattr(seats_client, "date", Tomorrow)
    after = ui.get("/api/state").json()["calls"]
    assert after["since_launch"] == before["since_launch"], after
    assert after["spent_today"] == 0, after


def test_K18_the_surcharge_confidence_chip_comes_from_the_engine_field(ui):
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    for leg in run["legs"]:
        sur = leg.get("surcharge")
        if not sur or not sur["known"]:
            continue
        assert sur.get("confidence"), leg["id"]
        # the CLI line is the one that loses the word; the field must not
        assert sur["confidence"] in ("modeled", "captured", "sourced"), sur["confidence"]


def test_K19_the_corp_header_is_on_every_response(ui):
    for r in (ui.get("/"), ui.get("/static/app.js"), ui.get("/api/state"),
              ui.get("/nope"), ui.get("/api/state", token="bad")):
        assert r.headers.get("cross-origin-resource-policy") == "same-origin", r.status


def test_K20_the_delivered_tree_carries_no_symlink_out_of_the_repo(  ):
    """A bundle is the delivery. A tracked symlink pointing at a path on the
    build machine breaks the checkout, or leaves a dangling `.venv` and with it
    every documented command."""
    out = subprocess.run(["git", "ls-files", "-s"], cwd=str(ROOT), capture_output=True,
                         text=True, check=True).stdout
    links = [line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("120000")]
    bad = []
    for rel in links:
        target = os.readlink(ROOT / rel)
        if os.path.isabs(target) or not (ROOT / rel).resolve().is_relative_to(ROOT):
            bad.append((rel, target))
    assert bad == [], bad


# ============================================================== M-4: keyboard


def _keyboard(width=1440):
    from browser import HERE, Server

    srv = Server("offline_b")
    try:
        p = subprocess.run(
            ["node", str(HERE / "keyboard.js"),
             json.dumps({"port": srv.port, "trip": "trip_b_europe", "mode": "offline",
                         "width": width})],
            capture_output=True, text=True, timeout=300)
        assert p.stdout, p.stderr[-800:]
        return json.loads(p.stdout)
    finally:
        srv.close()


@pytest.fixture(scope="module")
def kb():
    return _keyboard()


def test_K21_enter_opens_the_drawer_and_leaves_focus_inside_it(kb):
    assert kb["focused"]["testid"].startswith("leg-row-")
    assert kb["ring"]["outlineWidth"] == "2px"
    assert kb["afterEnter"]["drawerHidden"] is False, kb["afterEnter"]
    assert kb["afterEnter"]["focus"] == "Close detail", kb["afterEnter"]


def test_K22_space_opens_it_too_and_does_not_scroll_the_page(kb):
    assert kb["afterSpace"]["drawerHidden"] is False, kb["afterSpace"]
    assert kb["afterSpace"]["pageScrolled"] is False, kb["afterSpace"]


def test_K23_escape_gives_focus_back_to_the_row_it_came_from(kb):
    """The round trip: Tab to a row, Enter, Esc. Closing the drawer destroys the
    element focus was on, so focus falls to <body> and the reader's place in the
    table is gone - 18 Tab presses from the top to get back to the same row."""
    assert kb["escFocus"]["drawerHidden"] is True
    assert kb["escFocus"]["backOnTheRow"], kb["escFocus"]


def test_K24_the_drawer_can_be_left_by_tabbing_and_is_not_a_trap(kb):
    stops = kb["drawerTabStops"]
    assert stops, kb
    assert any(s and not s["inDrawer"] for s in stops), stops


# ================================================================ M-5: pinning


@pytest.fixture(scope="module")
def pinned_widths():
    from browser import ui_browser

    out = {}
    for width in (400, 1440):
        dom, _ = ui_browser("offline_b", kind="trip", trip="trip_b_europe", mode="offline",
                            width=width, height=900, sticky=True, name=f"k_pin_{width}")
        out[width] = dom
    return out


@pytest.mark.parametrize("width", [400, 1440])
def test_K25_the_leg_id_and_the_verdict_stay_on_screen_at_both_ends_of_the_scroll(
        pinned_widths, width):
    st = pinned_widths[width]["sticky"]
    assert st, "no sticky measurement"
    for phase in ("atStart", "atEnd"):
        for row in st[phase]["rows"]:
            assert row["legVisible"], (width, phase, row["id"], row["legBox"])
            assert row["verVisible"], (width, phase, row["id"], row["verBox"])


@pytest.mark.parametrize("width", [400, 1440])
def test_K26_a_pinned_cell_is_opaque_and_on_top_of_what_scrolls_under_it(
        pinned_widths, width):
    st = pinned_widths[width]["sticky"]
    for row in st["atEnd"]["rows"]:
        for css, box in (("legCss", "legHit"), ("verCss", "verHit")):
            assert row[css]["pos"] == "sticky", (width, row["id"], row[css])
            colour = row[css]["bg"]
            assert colour.startswith("rgb(") and "rgba(0, 0, 0, 0)" not in colour, colour
            assert row[box] == "self", (width, row["id"], row[box])


def test_K27_the_scrolling_middle_is_not_squeezed_out_at_phone_width(pinned_widths):
    """Two pinned columns inside a 368px frame leave what for the rest?"""
    st = pinned_widths[400]["sticky"]
    row = st["atStart"]["rows"][0]
    middle = st["atStart"]["wrap"]["w"] - row["legBox"]["w"] - row["verBox"]["w"]
    assert middle >= 100, f"only {middle:.0f}px of scrolling table between the pinned columns"
