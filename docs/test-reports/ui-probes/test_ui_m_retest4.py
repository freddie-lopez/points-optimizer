"""
M. RE-TEST 4: the round-3 fixes (R3-1 products, R3-2 the export, R3-3 the
number in the message), attacked one multiplication further out than each one
reaches — the caller that converts before the guard runs, and the rate that
multiplies after it.

RED = a defect that exists. GREEN = the fix held up.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import OFFLINE, ROOT, g, response

TRIPS = ROOT / "tests" / "fixtures" / "trips"
PY = str(ROOT / ".venv" / "bin" / "python")
BIG_INT = 10 ** 400
SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}


def env_for(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"),
               POINTS_OPTIMIZER_ENV_FILE=str(tmp_path / "absent.env"),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("SEATS_AERO_KEY", None)
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env[var] = "http://127.0.0.1:9"
    return env


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def _hotel(fx):
    return [l for l in fx["legs"] if l["kind"] == "hotel"][0]


def written(tmp_path, name, base, mutate):
    fx = json.loads((TRIPS / base).read_text())
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx, indent=1))
    return path


def run_cli(path, tmp_path, extra=(), balance="UR=160000"):
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(path), "--offline",
                        "--balance", balance, "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15", *extra],
                       cwd=str(ROOT), capture_output=True, text=True, env=env_for(tmp_path))
    return p.returncode, p.stdout + p.stderr


# =================================== R3-1: what the guard still does not see


BIG_INT_MONEY = {
    "cash_bigint": ("trip_a_mry_nyc.json",
                    lambda fx: _flight(fx)["cash_options"][0].__setitem__("amount", BIG_INT)),
    "fee_bigint": ("trip_a_mry_nyc.json",
                   lambda fx: _flight(fx).__setitem__(
                       "mandatory_fees", [{"label": "probe", "amount": BIG_INT,
                                           "currency": "USD"}])),
}


@pytest.mark.parametrize("name", sorted(BIG_INT_MONEY))
def test_M1_a_huge_int_in_a_money_field_is_a_refusal_not_a_traceback(tmp_path, name):
    """R3-3's note says `float(10 ** 400)` raising OverflowError 'used to let a
    huge int in a money field escape the guard entirely'. The guard function
    catches it now - but `trip_loader._finite_amount` calls `float(value)`
    ITSELF, one line before the guard, inside `except (TypeError, ValueError)`.
    """
    base, mutate = BIG_INT_MONEY[name]
    path = written(tmp_path, name, base, mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, out[-700:]
    assert code == 1 and "Error:" in out, (code, out[-300:])


@pytest.mark.parametrize("name", sorted(BIG_INT_MONEY))
def test_M2_the_ui_answers_the_same_fixture_without_an_internal_error(ui, tmp_path, name):
    base, mutate = BIG_INT_MONEY[name]
    path = written(tmp_path, name, base, mutate)
    shutil.copy(path, ui.trips_dir / path.name)
    listing = ui.get("/api/trips")
    assert listing.status == 200
    row = [t for t in listing.json() if t["id"] == name]
    assert row, [t["id"] for t in listing.json()]
    error = row[0]["load_error"] or ""
    assert "OverflowError" not in error, error
    run = ui.post(f"/api/trips/{name}/run", OFFLINE)
    assert run.status != 500, run.text[:200]


FX_OVERFLOW = {
    # 1.6e306 EUR passes the bound (1.6e306 / 0.01 is finite) and is then
    # MULTIPLIED by the FX rate before it is scored.
    "cash_in_eur": ("trip_a_mry_nyc.json",
                    lambda fx: _flight(fx).__setitem__(
                        "cash_options", [{"label": "PROBE EUR", "amount": 1.6e306,
                                          "currency": "EUR"}])),
    "fee_in_eur": ("trip_a_mry_nyc.json",
                   lambda fx: _flight(fx).__setitem__(
                       "mandatory_fees", [{"label": "probe", "amount": 1.6e306,
                                           "currency": "EUR"}])),
}


@pytest.mark.parametrize("name", sorted(FX_OVERFLOW))
def test_M3_an_amount_that_only_overflows_after_the_fx_rate_is_applied(tmp_path, name):
    """The bound is currency-blind: it checks the number in the file, and the
    figure that is scored is that number times an FX rate."""
    base, mutate = FX_OVERFLOW[name]
    path = written(tmp_path, name, base, mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, out[-700:]
    assert code in (0, 1, 3, 4), code


@pytest.mark.parametrize("name", sorted(FX_OVERFLOW))
def test_M4_the_ui_does_not_500_on_an_fx_overflow(ui, tmp_path, name):
    base, mutate = FX_OVERFLOW[name]
    path = written(tmp_path, name, base, mutate)
    shutil.copy(path, ui.trips_dir / path.name)
    assert ui.get("/api/trips").status == 200
    run = ui.post(f"/api/trips/{name}/run", OFFLINE)
    assert run.status != 500, run.text[:200]


PRODUCTS = {
    "nights_x_points_per_night": ("trip_b_europe.json", lambda fx: (
        _hotel(fx).__setitem__("nights", 10 ** 200),
        _hotel(fx).__setitem__("points_candidates", [
            {"label": "PROBE", "program": "World of Hyatt", "points_per_night": 10 ** 200,
             "source": "synthetic_test_fixture", "source_note": "PROBE"}]))),
    "points_x_travellers": ("trip_b_europe.json", lambda fx: (
        _hotel(fx).__setitem__("travelers", 10 ** 200),
        _hotel(fx).__setitem__("points_candidates", [
            {"label": "PROBE", "program": "World of Hyatt", "points": 10 ** 200,
             "source": "synthetic_test_fixture", "source_note": "PROBE"}]))),
    "cash_x_travellers": ("trip_a_mry_nyc.json", lambda fx: (
        _flight(fx).__setitem__("travelers", 10 ** 300),
        _flight(fx)["cash_options"][0].__setitem__("amount", 1e300))),
    "fee_x_nights_x_travellers": ("trip_b_europe.json", lambda fx: (
        _hotel(fx).__setitem__("nights", 10 ** 100),
        _hotel(fx).__setitem__("travelers", 10 ** 100),
        _hotel(fx).__setitem__("mandatory_fees", [
            {"label": "probe", "amount": 1e100, "currency": "USD"}]))),
}


@pytest.mark.parametrize("name", sorted(PRODUCTS))
def test_M5_a_product_the_file_determines_is_a_clean_refusal(tmp_path, name):
    base, mutate = PRODUCTS[name]
    path = written(tmp_path, name, base, mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback (most recent call last)" not in out, out[-700:]
    assert code in (0, 1, 3, 4), code
    if code == 1:
        assert "multiply past what a run can hold" in out or "too large to score" in out, \
            out[-300:]


def test_M6_a_refusal_names_the_fixtures_own_fields_not_the_arithmetic(tmp_path):
    base, mutate = PRODUCTS["nights_x_points_per_night"]
    path = written(tmp_path, "m_named", base, mutate)
    code, out = run_cli(path, tmp_path)
    assert "points_per_night x nights" in out, out[-400:]
    assert "leg 'B5'" in out, out[-400:]
    assert "inf is not a finite amount" not in out


def test_M7_realistic_big_numbers_are_untouched(tmp_path):
    """30 nights at 12,000 points a night, and a 1,000,000-point award."""
    def mutate(fx):
        h = _hotel(fx)
        h["nights"] = 30
        h["points_candidates"] = [{"label": "Hyatt 12,000/night", "program": "World of Hyatt",
                                   "points_per_night": 12000,
                                   "source": "google_badge_unverified",
                                   "source_note": "PROBE realistic"}]
        _flight(fx)["points_candidates"][0]["points"] = 1000000

    path = written(tmp_path, "m_realistic", "trip_b_europe.json", mutate)
    code, out = run_cli(path, tmp_path, balance="UR=3000000")
    assert code in (0, 3, 4) and "Error:" not in out, out[-400:]
    assert "360,000" in out, "30 x 12,000 was not carried"
    assert "1,000,000" in out


@pytest.mark.parametrize("extra", [
    ("--valuation-cpp", "1e-10"), ("--valuation-cpp", "1e300"),
    ("--transfer-increment", str(10 ** 300)), ("--max-stranded-points", str(10 ** 300)),
    ("--valuation-cpp", "0.000001"),
])
def test_M8_a_run_time_knob_cannot_turn_a_legal_fixture_into_a_traceback(tmp_path, extra):
    path = written(tmp_path, "m_plain_" + extra[1][:6].replace(".", ""), "trip_a_mry_nyc.json",
                   lambda fx: None)
    code, out = run_cli(path, tmp_path, extra=extra)
    assert "Traceback (most recent call last)" not in out, (extra, out[-500:])


def test_M9_an_absurd_number_from_seats_aero_is_not_a_crash_either(ui):
    """The loader guards the FILE. The search path takes its numbers from the
    API, which no guard here has ever seen."""
    def row(cost, taxes):
        r = copy.deepcopy(g.REAL["data"][0])
        r["YMileageCost"] = cost
        r["YTotalTaxes"] = taxes
        return r

    for cost, taxes in (("1" + "0" * 400, 3236), (1e308, 3236), ("50000", 10 ** 400),
                        ("-50000", 3236)):
        ui.net.impl = lambda url, _c=cost, _t=taxes, **kw: response(
            {"data": [row(_c, _t)], "hasMore": False})
        pf, r = ui.search(SEARCH)
        assert r.status == 200, (cost, r.status, r.text[:200])
        payload = r.json()
        assert payload["exit_code"] in (0, 1), payload["exit_code"]
        assert "Traceback" not in (payload.get("transcript") or "")


# ================================ R3-3: the number in every message that has one


def digits(text):
    return sum(1 for c in text if c.isdigit())


def test_M10_no_message_anywhere_prints_a_401_digit_number(ui, tmp_path):
    for name, mutate in (
            ("m_points_big", lambda fx: _flight(fx)["points_candidates"][0].__setitem__(
                "points", BIG_INT)),
            ("m_travellers_big", lambda fx: _flight(fx).__setitem__("travelers", BIG_INT)),
    ):
        path = written(tmp_path, name, "trip_a_mry_nyc.json", mutate)
        code, out = run_cli(path, tmp_path)
        assert digits(out) < 200, f"the CLI printed {digits(out)} digits"
        assert "a 401-digit number" in out, out[-300:]
        shutil.copy(path, ui.trips_dir / path.name)
        row = [t for t in ui.get("/api/trips").json() if t["id"] == name][0]
        assert digits(row["load_error"] or "") < 60, row["load_error"][:200]
        detail = ui.get(f"/api/trips/{name}")
        assert digits(detail.text) < 60, detail.text[:200]
        run = ui.post(f"/api/trips/{name}/run", OFFLINE)
        assert run.status == 200, run.text[:200]
        payload = run.json()
        message = (payload.get("refusal") or {}).get("message", "") + \
            (payload.get("transcript") or "")
        assert digits(message) < 200, digits(message)


@pytest.mark.parametrize("flag,value", [
    ("--travelers", str(BIG_INT)),
    ("--hotel", f"HOTEL:2027-01-15:{BIG_INT}:1000"),
    ("--leg", f"SFO:LHR:2027-01-15:{BIG_INT}"),
])
def test_M11_the_builders_own_flags_shorten_it_too(tmp_path, flag, value):
    cmd = [PY, "-m", "src.main", "--new-trip", "m_probe", "--leg", "SFO:LHR:2027-01-15:2400"]
    cmd += ["--leg", value] if flag == "--leg" else [flag, value]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env_for(tmp_path))
    out = p.stdout + p.stderr
    assert p.returncode == 1, out[-300:]
    assert digits(out) < 100, f"the refusal printed {digits(out)} digits"
    assert not (TRIPS / "m_probe.json").exists()


# ========================================================= R3-2: the export


@pytest.fixture(scope="module")
def export_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("export4")
    tar = subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT), check=True,
                         capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    env = dict(os.environ, HOME=str(d / "home_"), PYTHONDONTWRITEBYTECODE="1")
    env.pop("SEATS_AERO_KEY", None)
    p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs"],
                       cwd=str(d), capture_output=True, text=True, env=env, timeout=1800)
    return d, p.stdout + p.stderr


def test_M12_the_unpacked_export_has_no_failures(export_run):
    _, out = export_run
    failed = [l.split(" ", 1)[1].strip() for l in out.splitlines() if l.startswith("FAILED ")]
    assert failed == [], "\n".join(failed)
    summary = [l for l in out.splitlines() if " passed" in l or " failed" in l][-1]
    assert "failed" not in summary and "passed" in summary, summary


def test_M13_the_export_skips_only_the_documented_gates_and_the_git_checks(export_run):
    """A green export bought by skipping something that should run is not green."""
    _, out = export_run
    skipped = [l for l in out.splitlines() if l.startswith("SKIPPED")]
    for line in skipped:
        assert ("no .git here" in line
                or "has not been run" in line), line
    git_skips = [l for l in skipped if "no .git here" in l]
    assert len(git_skips) == 4, git_skips
    assert all("test_the_delivered_tree_is_portable.py" in l for l in git_skips)


def test_M14_those_four_checks_really_run_in_a_clone(  ):
    """The other half: in this worktree, which has .git, they are not skipped."""
    p = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                        "tests/test_the_delivered_tree_is_portable.py"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "skipped" not in out.splitlines()[-1], out[-300:]
    assert p.returncode == 0, out[-500:]
