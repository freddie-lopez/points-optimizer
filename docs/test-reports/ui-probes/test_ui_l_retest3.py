"""
L. RE-TEST 3: the round-2 fixes (R2-1 .. R2-4), attacked where each one's own
argument is thinnest — the delivered EXPORT rather than this worktree, the
products of two bounded numbers, the sentence that is restated in place, and
focus after the page changes underneath the drawer.

RED = a defect that exists. GREEN = the fix held up.
"""
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest
from rich.console import Console

from conftest import OFFLINE, ROOT, g

TRIP_A = ROOT / "tests" / "fixtures" / "trips" / "trip_a_mry_nyc.json"
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
TRIP_C = ROOT / "tests" / "fixtures" / "trips" / "trip_c_lon_mry_surcharge.json"
PY = str(ROOT / ".venv" / "bin" / "python")


def env_for(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"),
               POINTS_OPTIMIZER_ENV_FILE=str(tmp_path / "absent.env"),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("SEATS_AERO_KEY", None)
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env[var] = "http://127.0.0.1:9"
    return env


# ==================================================== R2-1: the delivered tree


@pytest.fixture(scope="module")
def export_tree(tmp_path_factory):
    """`git archive HEAD` unpacked into a clean directory: the bytes a person
    who is handed this branch actually gets, with no .git and no .venv."""
    d = tmp_path_factory.mktemp("export")
    tar = subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT), check=True,
                         capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    return d


def test_L1_the_exported_tree_carries_no_symlink_and_no_venv(export_tree):
    links = [p for p in export_tree.rglob("*") if p.is_symlink()]
    assert links == [], links
    assert not (export_tree / ".venv").exists()
    assert (export_tree / "src" / "ui" / "server.py").is_file()
    assert (export_tree / "tests" / "fixtures" / "trips" / "trip_b_europe.json").is_file()


def test_L2_no_shipped_source_names_a_path_that_only_exists_on_the_build_machine(export_tree):
    bad = []
    for sub in ("src", "tests", "data"):
        for path in (export_tree / sub).rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".csv", ".json", ".md"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in ("/home/claude", "/tmp/claude", "/Users/"):
                if needle in text and "Users/someone" not in text:
                    bad.append((str(path.relative_to(export_tree)), needle))
    assert bad == [], bad


def test_L3_the_suite_passes_in_the_export_the_way_he_would_run_it(export_tree, tmp_path):
    """The whole point of R2-1: a copy of this branch, no git, no venv inside
    it, run with an interpreter from outside. Nothing may depend on the
    worktree it was built in."""
    p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf"],
                       cwd=str(export_tree), capture_output=True, text=True,
                       env=env_for(tmp_path), timeout=1800)
    out = p.stdout + p.stderr
    failed = [line.split(" ", 1)[1].strip() for line in out.splitlines()
              if line.startswith("FAILED ")]
    summary = out.strip().splitlines()[-1] if out.strip() else "(no output)"
    assert failed == [], f"{summary}\n" + "\n".join(failed)


def test_L4_the_export_has_no_crlf_in_anything_python_reads_as_source(export_tree):
    bad = []
    for path in export_tree.rglob("*.py"):
        if b"\r\n" in path.read_bytes():
            bad.append(str(path.relative_to(export_tree)))
    assert bad == [], bad


# =============================================== R2-3: numbers that multiply


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def _hotel(fx):
    return [l for l in fx["legs"] if l["kind"] == "hotel"][0]


def written(tmp_path, name, base, mutate):
    fx = copy.deepcopy(json.loads(base.read_text()))
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx, indent=1))
    return path


def run_cli(path, tmp_path, extra=()):
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(path), "--offline",
                        "--balance", "UR=160000", "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15", *extra],
                       cwd=str(ROOT), capture_output=True, text=True, env=env_for(tmp_path))
    return p.returncode, p.stdout + p.stderr


def nights_times_points(fx):
    h = _hotel(fx)
    h["nights"] = 10 ** 200
    h["points_candidates"] = [{"label": "PROBE", "program": "World of Hyatt",
                               "points_per_night": 10 ** 200,
                               "source": "synthetic_test_fixture", "source_note": "PROBE"}]


PRODUCTS = {
    # each field passes the new per-field bound; their PRODUCT is what reaches
    # the scale
    "nights_x_points_per_night": (TRIP_B, nights_times_points),
    "travelers_x_points": (TRIP_B, lambda fx: (
        _hotel(fx).__setitem__("travelers", 10 ** 200),
        _hotel(fx).__setitem__("points_candidates", [
            {"label": "PROBE", "program": "World of Hyatt", "points": 10 ** 200,
             "source": "synthetic_test_fixture", "source_note": "PROBE"}]))),
    "travelers_x_cash": (TRIP_A, lambda fx: _flight(fx).__setitem__("travelers", 10 ** 300)),
    "travelers_x_fee": (TRIP_A, lambda fx: (
        _flight(fx).__setitem__("travelers", 10 ** 300),
        _flight(fx).__setitem__("mandatory_fees", [
            {"label": "probe", "amount": 1e300, "currency": "USD"}]))),
}


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_L5_two_bounded_numbers_whose_product_is_not_bounded(tmp_path, name):
    """R2-3 bounds each field on its own. A fixture can carry two of them."""
    base, mutate = PRODUCTS[name]
    path = written(tmp_path, name, base, mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, (name, out[-600:])
    assert code in (0, 1, 3, 4), (name, code)


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_L6_the_ui_never_500s_on_one_of_those_either(ui, tmp_path, name):
    base, mutate = PRODUCTS[name]
    path = written(tmp_path, name, base, mutate)
    shutil.copy(path, ui.trips_dir / path.name)
    assert ui.get("/api/trips").status == 200
    run = ui.post(f"/api/trips/{name}/run", OFFLINE)
    assert run.status != 500, (name, run.text[:300])


def test_L7_a_refusal_never_prints_a_401_digit_number(tmp_path):
    path = written(tmp_path, "probe_bigint_points", TRIP_A,
                   lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
                       "points", 10 ** 400))
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, out[-600:]
    assert "0000000000000000000000000000000000000000" not in out, "the refusal printed the number"
    assert "401-digit" in out, out[-400:]


def test_L8_a_big_but_legal_award_price_is_neither_refused_nor_truncated(tmp_path):
    """The bound must not become a ceiling on award prices, and a number the
    CLI does print must be printed in full."""
    for points in (10 ** 12, 10 ** 30):
        path = written(tmp_path, f"probe_big_{points}", TRIP_A,
                       lambda fx, n=points: _flight(fx)["points_candidates"][0].__setitem__(
                           "points", n))
        code, out = run_cli(path, tmp_path)
        assert "Error:" not in out and code in (0, 3, 4), (points, code, out[-400:])
        assert "Traceback" not in out
    # one that the wallet can actually fund, so the figure is printed
    path = written(tmp_path, "probe_big_funded", TRIP_A,
                   lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
                       "points", 1_000_000))
    code, out = run_cli(path, tmp_path, extra=("--balance", "UR=2000000"))
    assert "1,000,000" in out, out[-800:]


@pytest.mark.parametrize("field,value", [
    ("travelers", 10 ** 400), ("nights", 10 ** 400),
])
def test_L9_a_count_that_cannot_be_scored_is_a_load_refusal_not_a_crash(tmp_path, field, value):
    def mutate(fx):
        leg = _hotel(fx) if field == "nights" else _flight(fx)
        leg[field] = value

    path = written(tmp_path, f"probe_{field}", TRIP_B, mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, out[-600:]
    assert code == 1 and "Error:" in out, (code, out[-400:])


# =============================================== R2-2: the restated sentence


def uk_leg(tmp_path, program, metal, points, cash, name):
    src = json.loads(TRIP_C.read_text())
    leg = copy.deepcopy([l for l in src["legs"] if l["id"] == "C2"][0])
    leg["points_candidates"][0].update(program=program, marketing_carrier=metal,
                                       operating_carrier=metal, points=points,
                                       label=f"PROBE: {program} on {metal}")
    leg.update(id="P1", origin="LHR", destination="JFK", description="PROBE: LHR->JFK J")
    leg["cash_options"] = [{"label": f"PROBE ${cash}", "amount": float(cash), "currency": "USD"}]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"id": name, "name": "PROBE", "description": "SYNTHETIC",
                                "source": "SYNTHETIC", "trip_level_flags": ["SYNTHETIC"],
                                "legs": [leg]}, indent=1))
    return path


def score_in_process(path):
    from src import main as cli

    argv = ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
            "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"]
    sink, buf = [], io.StringIO()
    cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), sink)
    return sink[0].results[0], buf.getvalue()


def test_L10_the_restated_sentence_quotes_the_scored_band_and_stays_in_place(tmp_path, pinned):
    r, text = score_in_process(uk_leg(tmp_path, "British Airways Executive Club", "IB",
                                      50000, 1450, "probe_double"))
    codes = [x.code for x in r.reasons]
    assert codes.count("VERDICT_SENSITIVE") == 1, codes
    detail = [x.detail for x in r.reasons if x.code == "VERDICT_SENSITIVE"][0]
    assert f"${r.points_score_low_usd:,.2f}" in detail and \
        f"${r.points_score_high_usd:,.2f}" in detail, detail
    assert "UK Air Passenger Duty of $330.38 is counted in both ends." in detail
    # in place: it was written during evaluate_leg, before the APD reason
    assert codes.index("VERDICT_SENSITIVE") < codes.index("APD_ADDED"), codes
    assert sum(1 for w in r.warnings if w.startswith("VERDICT SENSITIVE:")) == 1


def test_L11_re_deciding_the_same_answer_changes_nothing_at_all(tmp_path, pinned):
    from src import optimizer

    r, _ = score_in_process(uk_leg(tmp_path, "British Airways Executive Club", "IB",
                                   50000, 1450, "probe_idem"))
    before_reasons = [(x.code, x.detail) for x in r.reasons]
    before_warnings = list(r.warnings)
    for _ in range(3):
        optimizer.set_verdict_sensitivity(r, apd_usd=r.apd_added_usd or 0.0)
    assert [(x.code, x.detail) for x in r.reasons] == before_reasons
    assert r.warnings == before_warnings


def test_L12_a_flag_that_flips_off_and_on_leaves_exactly_one_of_each(tmp_path, pinned):
    from src import optimizer

    r, _ = score_in_process(uk_leg(tmp_path, "British Airways Executive Club", "IB",
                                   50000, 1450, "probe_twice"))
    cash = r.cash_total_score_usd
    r.cash_total_score_usd = 1.0                      # no longer straddles
    optimizer.set_verdict_sensitivity(r, apd_usd=330.38)
    assert [x for x in r.reasons if x.code == "VERDICT_SENSITIVE"] == []
    assert [w for w in r.warnings if w.startswith("VERDICT SENSITIVE:")] == []
    r.cash_total_score_usd = cash                     # straddles again
    optimizer.set_verdict_sensitivity(r, apd_usd=330.38)
    assert len([x for x in r.reasons if x.code == "VERDICT_SENSITIVE"]) == 1
    assert len([w for w in r.warnings if w.startswith("VERDICT SENSITIVE:")]) == 1


def test_L13_no_other_reason_or_warning_is_touched_by_the_restatement(tmp_path, pinned):
    from src import optimizer

    r, _ = score_in_process(uk_leg(tmp_path, "British Airways Executive Club", "IB",
                                   50000, 1450, "probe_others"))
    others_before = [(x.code, x.detail) for x in r.reasons if x.code != "VERDICT_SENSITIVE"]
    warn_before = [w for w in r.warnings if not w.startswith("VERDICT SENSITIVE:")]
    r.points_score_low_usd -= 1.0
    optimizer.set_verdict_sensitivity(r, apd_usd=330.38)
    assert [(x.code, x.detail) for x in r.reasons if x.code != "VERDICT_SENSITIVE"] == others_before
    assert [w for w in r.warnings if not w.startswith("VERDICT SENSITIVE:")] == warn_before


def test_L14_a_leg_with_no_apd_does_not_get_a_duty_clause(tmp_path, pinned):
    src = json.loads(TRIP_C.read_text())
    leg = copy.deepcopy([l for l in src["legs"] if l["id"] == "C2"][0])
    leg["points_candidates"][0].update(program="Virgin Atlantic Flying Club",
                                       marketing_carrier="VS", operating_carrier="VS",
                                       points=50000, label="PROBE")
    leg.update(id="P1", origin="JFK", destination="LHR")     # not a UK departure
    leg["cash_options"] = [{"label": "PROBE $780", "amount": 780.0, "currency": "USD"}]
    path = tmp_path / "probe_noapd.json"
    path.write_text(json.dumps({"id": "probe_noapd", "name": "PROBE",
                                "description": "SYNTHETIC", "source": "SYNTHETIC",
                                "trip_level_flags": ["SYNTHETIC"], "legs": [leg]}, indent=1))
    r, text = score_in_process(path)
    detail = [x.detail for x in r.reasons if x.code == "VERDICT_SENSITIVE"][0]
    assert r.verdict_sensitive is True
    assert "Air Passenger Duty" not in detail, detail
    assert not r.apd_added_usd


def test_L15_the_drawer_shows_the_same_sentence_the_terminal_does(ui, tmp_path):
    path = uk_leg(tmp_path, "British Airways Executive Club", "IB", 50000, 1450, "probe_drawer")
    shutil.copy(path, ui.trips_dir / "probe_drawer.json")
    run = ui.run_trip("probe_drawer", OFFLINE)[1].json()
    leg = run["legs"][0]
    detail = [r["detail"] for r in leg["reasons"] if r["code"] == "VERDICT_SENSITIVE"][0]
    transcript = run["transcript"]
    # the same figures, and the terminal's own warning line
    assert f"{leg['numbers']['score_low']:,.2f}" in detail
    assert f"{leg['numbers']['score_high']:,.2f}" in detail
    assert "VERDICT SENSITIVE" in transcript
    flat = " ".join(transcript.split())
    assert "reverses inside the surcharge estimate's own range" in flat
    assert leg["verdict"]["sensitive"] is True


# ======================================================== R2-4: where focus is


@pytest.fixture(scope="module")
def focus_runs():
    from browser import HERE, Server

    out = {}
    for width in (1440, 400):
        srv = Server("offline_b")
        try:
            p = subprocess.run(["node", str(HERE / "focus.js"),
                                json.dumps({"port": srv.port, "width": width})],
                               capture_output=True, text=True, timeout=300)
            assert p.stdout, p.stderr[-800:]
            out[width] = json.loads(p.stdout)
            assert not out[width].get("fatal"), out[width]["fatal"][:300]
        finally:
            srv.close()
    return out


@pytest.mark.parametrize("width", [1440, 400])
def test_L16_escape_puts_focus_back_on_the_row_it_came_from(focus_runs, width):
    run = focus_runs[width]
    assert run["afterEnter"]["label"] == "Close detail", run["afterEnter"]
    assert run["afterEscape"]["testid"] == run["openedFrom"], run["afterEscape"]
    assert run["afterEscape"]["drawerHidden"] is True
    assert run["errors"] == [], run["errors"]


@pytest.mark.parametrize("width", [1440, 400])
def test_L17_the_close_button_does_the_same(focus_runs, width):
    assert focus_runs[width]["afterCloseButton"]["testid"] == "leg-row-B4"


@pytest.mark.parametrize("width", [1440, 400])
def test_L18_focus_is_never_left_on_an_element_that_no_longer_exists(focus_runs, width):
    run = focus_runs[width]
    for key in ("afterEscape", "afterCloseButton", "afterRerunWithDrawerOpen",
                "afterTabSwitchEscape", "afterSecondEscape"):
        state = run[key]
        assert state["inDocument"], (key, state)
        assert not state["insideHidden"], (key, state)


def test_L19_switching_tabs_with_the_drawer_open_leaves_a_consistent_page(focus_runs):
    for width, run in focus_runs.items():
        assert run["searchViewVisible"] is True, width
        assert run["drawerBackOnTrips"] is True, width
