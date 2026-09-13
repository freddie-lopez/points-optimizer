"""
N. RE-TEST 5. Two jobs.

1. THE BOUNDARY AS A PROPERTY, not as shapes: can any number from outside reach
   the scorer without passing `config.scoreable_amount` / `scoreable_count`, do
   the conversions refuse rather than return a wrong figure, and does the
   ArithmeticError backstop hide anything that should have been specific?

2. THE LIST I SAID I WOULD ATTACK NEXT (minus the real Seats.aero): wall-clock
   life, concurrency beyond two runs, the positional replay manifest ids, the
   small end of the number line, fixture SHAPE rather than magnitude.

RED = a defect that exists. GREEN = the attack held up.
"""
import copy
import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from conftest import LIVE, OFFLINE, ROOT, TRANSFER, g, response, write_wallet

TRIPS = ROOT / "tests" / "fixtures" / "trips"
PY = str(ROOT / ".venv" / "bin" / "python")
SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}
REPLAY = {"mode": "replay", "options": {**TRANSFER, "manifest_id": 0}}
LIVE_FRESH = {"mode": "live", "options": {**LIVE["options"], "refresh": True}}
BIG_INT = 10 ** 400


def env_for(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"),
               POINTS_OPTIMIZER_ENV_FILE=str(tmp_path / "absent.env"),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("SEATS_AERO_KEY", None)
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env[var] = "http://127.0.0.1:9"
    return env


def raw_fixture(tmp_path, name, replace_from, replace_to, base="trip_a_mry_nyc.json"):
    """A fixture edited as TEXT, so JSON literals (`NaN`, `1e400`, `-0.0`) can be
    written the way a hand-edited file would carry them."""
    text = (TRIPS / base).read_text()
    assert replace_from in text, replace_from
    text = text.replace(replace_from, replace_to, 1)
    text = text.replace(f'"id": "{base[:-5]}"', f'"id": "{name}"', 1)
    path = tmp_path / f"{name}.json"
    path.write_text(text)
    return path


def run_cli(path, tmp_path, extra=(), balance="UR=160000"):
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(path), "--offline",
                        "--balance", balance, "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15", *extra],
                       cwd=str(ROOT), capture_output=True, text=True, env=env_for(tmp_path))
    return p.returncode, p.stdout + p.stderr


# ============================================ 1. the boundary, as a property


NOT_A_PRICE = {
    "zero": ('"amount": 367.0', '"amount": 0'),
    "negative_zero": ('"amount": 367.0', '"amount": -0.0'),
    "negative": ('"amount": 367.0', '"amount": -50.0'),
    "true": ('"amount": 367.0', '"amount": true'),
    "denormal": ('"amount": 367.0', '"amount": 1e-320'),
}


@pytest.mark.parametrize("name", sorted(NOT_A_PRICE))
def test_N1_a_money_value_the_builder_refuses_is_not_scored_as_a_fare(tmp_path, name):
    """`trip_builder.validate_cash` refuses 0, negative and non-numbers in the
    project's own words ("Zero is not a price - it is silence, and this project
    has confused the two before"). The LOADER has no such rule, so a
    hand-edited or externally supplied fixture is scored on the figure."""
    old, new = NOT_A_PRICE[name]
    path = raw_fixture(tmp_path, f"n1_{name}", old, new)
    code, out = run_cli(path, tmp_path)
    assert "Traceback" not in out, out[-400:]
    quoted = [l for l in out.splitlines() if l.startswith("│ A1")]
    assert code == 1 or not quoted or not any(
        s in quoted[0] for s in ("$0.00", "$-0.00", "$-50.00", "$1.00")), (
        f"{name} was scored as a fare: {quoted[0][:120] if quoted else out[-200:]}")


@pytest.mark.parametrize("points", ["0", "-42600"])
def test_N2_a_points_price_that_cannot_be_read_is_not_reported_as_no_partner(tmp_path, points):
    """The recurring failure's own shape: a value the loader silently drops
    comes out as `none - not a partner`, which is a claim about partnerships
    that nothing checked."""
    path = raw_fixture(tmp_path, f"n2_{points.strip('-')}", '"points": 42600', f'"points": {points}')
    code, out = run_cli(path, tmp_path)
    assert "Traceback" not in out
    row = [l for l in out.splitlines() if l.startswith("│ A1")]
    assert code == 1 or not row or "not a partner" not in row[0], row[0][:140] if row else out[-200:]


def test_N3_an_absurd_number_from_the_api_is_a_refusal_not_a_wrong_figure(ui):
    """The search path's numbers never pass the loader's boundary."""
    def row(cost, taxes):
        r = copy.deepcopy(g.REAL["data"][0])
        r["YMileageCost"] = cost
        r["YTotalTaxes"] = taxes
        return r

    for cost, taxes in (("1" + "0" * 400, 3236), (1e308, 3236), ("50000", 10 ** 400),
                        ("-50000", 3236), ("50000.5", 3236)):
        ui.net.impl = lambda url, _c=cost, _t=taxes, **kw: response(
            {"data": [row(_c, _t)], "hasMore": False})
        pf, r = ui.search(SEARCH)
        assert r.status == 200, (cost, r.status, r.text[:200])
        payload = r.json()
        assert "Traceback" not in (payload.get("transcript") or "")
        for rw in payload.get("rows") or []:
            for cabin, cell in (rw.get("cabins") or {}).items():
                if cell is None:
                    continue
                assert cell["cost"] is None or 0 < cell["cost"] < 10 ** 9, (cost, cell["cost"])


def test_N4_an_absurd_number_inside_a_snapshot_is_a_refusal_not_a_crash(ui):
    """Replay reads bytes off disk that nothing in this process wrote today."""
    from src import config

    def row(cost):
        r = copy.deepcopy(g.REAL["data"][0])
        r["Route"].update(OriginAirport="MRY", DestinationAirport="MAD")
        r["YMileageCost"] = cost
        return r

    ui.net.impl = lambda url, **kw: response({"data": [row("1" + "0" * 400)], "hasMore": False})
    pf, r = ui.run_trip("trip_b_europe", LIVE_FRESH)
    assert r.status == 200, r.text[:300]
    ui.net.impl = g.Refused()
    manifest = list(ui.get("/api/state").json()["modes"]["replay_manifests"])
    if not manifest:
        pytest.skip("the live run archived no manifest to replay")
    pf, r = ui.run_trip("trip_b_europe", REPLAY)
    assert r.status in (200, 409), r.text[:200]
    if r.status == 200:
        assert "Traceback" not in (r.json().get("transcript") or "")


def test_N5_a_wallet_balance_no_one_can_read_is_refused_or_shortened(ui, tmp_path):
    """The wallet is an outside number too, and it is printed twice."""
    code, out = run_cli(TRIPS / "trip_a_mry_nyc.json", tmp_path, balance=f"UR={BIG_INT}")
    digits = sum(1 for c in out if c.isdigit())
    assert digits < 500, f"the wallet banner printed {digits} digits"
    r = ui.post("/api/wallet", {"balances": {"UR": str(BIG_INT)}, "cards": []})
    body = r.text
    assert sum(1 for c in body if c.isdigit()) < 500, "the API echoed a 401-digit balance"


def test_N6_the_conversions_refuse_instead_of_returning_a_wrong_figure():
    from src import config

    with pytest.raises(config.UnscoreableNumber):
        config.cash_to_points_equivalent(1e308, 1e-300)
    with pytest.raises(config.UnscoreableNumber):
        config.points_to_cash_equivalent(10 ** 400, 0.01)
    # the conversion itself is finite; the CHAIN is what R4-2 crashed on, and
    # the refusal has to happen at the step that overflows
    converted = config.convert_to_usd(1.6e306, "EUR")
    assert converted == pytest.approx(1.6e306 * config.FX_RATES_TO_USD["EUR"])
    with pytest.raises(config.UnscoreableNumber):
        config.cash_to_points_equivalent(converted)
    with pytest.raises(ValueError):
        config.convert_to_usd(1.0, "ZZZ")
    with pytest.raises(ValueError):
        config.cash_to_points_equivalent(100.0, 0)
    # and the ordinary answers are unchanged
    assert config.cash_to_points_equivalent(367.0) == 36700
    assert config.points_to_cash_equivalent(42600) == pytest.approx(426.0)
    assert config.convert_to_usd(100.0, "USD") == 100.0


def test_N7_the_backstop_does_not_swallow_a_refusal_that_has_a_name(tmp_path):
    """`ArithmeticError` is caught beside `ValueError` in `main`. A number that
    the boundary CAN name must still be named, not reported as
    'this run could not be scored'."""
    path = raw_fixture(tmp_path, "n7_named", '"amount": 367.0', f'"amount": {BIG_INT}')
    code, out = run_cli(path, tmp_path)
    assert code == 1
    assert "could not be scored: OverflowError" not in out, out[-300:]
    assert "cash option amount" in out, out[-300:]


def test_N8_a_legal_fixture_still_scores_exactly_as_it_did(ui):
    """The boundary must not have moved a real answer."""
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert run["exit_code"] == 0
    assert run["headline"]["text"] == "2.04% - 11.03%"
    assert run["headline"]["qualifier"] == "(badge)"
    def cell(leg, name):
        return "".join(s["text"] for s in leg["cells"][name]["segments"]).strip()

    rows = {l["id"]: cell(l, "score_points") for l in run["legs"]}
    assert rows["B1"] == "$500.00" and rows["B4"] == "$418.11", rows


# ==================================================== 2. wall-clock life


def test_N9_the_six_hour_disk_cache_expires_on_the_clock(ui, monkeypatch):
    """A cached search older than the TTL must be re-fetched, not served."""
    from src import config, response_cache

    ui.run_trip("trip_b_europe", LIVE)          # fills the disk cache
    first = ui.net.search_calls
    ui.run_trip("trip_b_europe", LIVE)          # same window: served from disk
    assert ui.net.search_calls == first, "the disk cache did not answer the second run"
    later = g.PINNED_NOW.replace(hour=(g.PINNED_NOW.hour + 7) % 24,
                                 day=g.PINNED_NOW.day + (1 if g.PINNED_NOW.hour + 7 > 23 else 0))
    monkeypatch.setattr(response_cache, "_utcnow", lambda: later)
    ui.run_trip("trip_b_europe", LIVE)
    assert ui.net.search_calls > first, "a cache entry older than the TTL was still served"


def test_N10_a_cached_answer_says_when_it_was_fetched_and_does_not_restamp_itself(ui, monkeypatch):
    """A stale answer must not present itself as a fresh one: the fetch instant
    the reader is shown is the one the bytes were fetched at, whatever the clock
    says now."""
    from src import response_cache

    ui.run_trip("trip_b_europe", LIVE)
    second = ui.run_trip("trip_b_europe", LIVE)[1].json()
    cached = [leg for leg in second["legs"]
              if (leg.get("live") or {}).get("served_from_cache")]
    assert cached, "the second run was not served from the disk cache at all"
    stamps = {leg["id"]: leg["live"]["cache_fetched_at"] for leg in cached}
    assert all(stamps.values()), stamps
    later = g.PINNED_NOW.replace(minute=(g.PINNED_NOW.minute + 45) % 60,
                                 hour=g.PINNED_NOW.hour + 1)
    monkeypatch.setattr(response_cache, "_utcnow", lambda: later)
    third = ui.run_trip("trip_b_europe", LIVE)[1].json()
    again = {leg["id"]: (leg.get("live") or {}).get("cache_fetched_at")
             for leg in third["legs"] if (leg.get("live") or {}).get("served_from_cache")}
    for leg_id, stamp in stamps.items():
        if leg_id in again:
            assert again[leg_id] == stamp, (
                f"{leg_id}: the cached answer re-stamped itself as fetched at "
                f"{again[leg_id]} when the bytes were fetched at {stamp}")
    for leg in third["legs"]:
        line = (leg.get("live") or {}).get("line") or ""
        if "served from cache" in line:
            assert "fetched" in line, line


# ============================================ 2. concurrency beyond two runs


def test_N11_a_wallet_edit_and_a_create_during_a_run_do_not_disturb_it(ui):
    gate = threading.Event()
    stub = g.Stub()

    def slow(url, **kw):
        gate.wait(15)
        return stub(url, **kw)

    ui.net.impl = slow
    pf = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__(
        "run", ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf["confirm_id"]))))
    t.start()
    time.sleep(0.4)
    draft = ui.post("/api/trips/draft", {"name": "n11_trip", "cabin": "Y", "legs": [
        {"origin": "SFO", "destination": "LHR", "date": "2027-01-15", "cash": "2400"}]})
    assert draft.status == 200, draft.text[:200]
    created = ui.post("/api/trips/create", dict(draft.json(), name="n11_trip", cabin="Y",
                                                legs=[{"origin": "SFO", "destination": "LHR",
                                                       "date": "2027-01-15", "cash": "2400"}],
                                                draft_hash=draft.json()["draft_hash"]))
    wallet = ui.post("/api/wallet", {"balances": {"UR": "1"}, "cards": []})
    state = ui.get("/api/state")
    gate.set()
    t.join(40)
    assert out["run"].status == 200, out["run"].text[:200]
    assert created.status in (200, 409), created.text[:200]
    assert wallet.status == 200 and state.status == 200
    run = out["run"].json()
    assert "UR: 160,000" in " ".join(run["context"]["wallet_block"]), run["context"]["wallet_block"]
    assert run["exit_code"] == 0


def test_N12_two_tabs_see_one_server_and_one_run_at_a_time(ui):
    """Two clients on the same server: the token works for both, and the lock
    is the server's, not the page's."""
    from conftest import Client

    second = Client(ui.srv, ui.logs, ui.engine)
    assert second.get("/api/state").status == 200
    gate = threading.Event()
    stub = g.Stub()
    ui.net.impl = lambda url, **kw: (gate.wait(15), stub(url, **kw))[1]
    pf1 = ui.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    pf2 = second.post("/api/trips/trip_b_europe/preflight", LIVE).json()
    res = {}
    t = threading.Thread(target=lambda: res.__setitem__(
        "a", ui.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf1["confirm_id"]))))
    t.start()
    time.sleep(0.5)
    res["b"] = second.post("/api/trips/trip_b_europe/run", dict(LIVE, confirm_id=pf2["confirm_id"]))
    gate.set()
    t.join(40)
    assert res["a"].status == 200, res["a"].text[:200]
    assert res["b"].status == 409 and res["b"].json()["error"] == "busy"


# ================================================ 2. the positional manifest id


def test_N13_a_manifest_that_appears_after_the_preflight_is_not_replayed_by_index(ui):
    """The id is an INDEX into a list read at request time. If the list grows
    between the preflight and the Run, index 0 can be a different manifest."""
    from src import config

    ui.run_trip("trip_b_europe", LIVE_FRESH)            # writes the real manifest
    before = ui.get("/api/state").json()["modes"]["replay_manifests"]
    assert before, "no manifest to replay"
    pf = ui.post("/api/trips/trip_b_europe/preflight", REPLAY).json()
    named = pf.get("manifest")
    # a second manifest appears first in the list (the corpus is sorted)
    corpus = ROOT / "tests" / "fixtures" / "seats_aero"
    intruder = corpus / "aaa_probe_manifest"
    intruder.mkdir(exist_ok=True)
    (intruder / "MANIFEST.md").write_text((ROOT / "tests" / "fixtures" / "seats_aero" /
                                           "README.md").read_text()[:0] + "| not a manifest |\n")
    try:
        after = ui.get("/api/state").json()["modes"]["replay_manifests"]
        assert [m["label"] for m in after] != [m["label"] for m in before], \
            "the intruder manifest was not picked up; this probe proved nothing"
        r = ui.post("/api/trips/trip_b_europe/run", REPLAY)
        assert r.status in (200, 400, 409), r.text[:200]
        if r.status == 200:
            used = r.json()["argv_display"]
            assert named.split("/")[-2] in used, (
                f"the run used a different manifest from the one the confirm named: "
                f"{named} vs {used}")
    finally:
        shutil.rmtree(intruder, ignore_errors=True)


# ================================================ 2. fixture shape, not size


def shaped(tmp_path, name, mutate, base="trip_a_mry_nyc.json"):
    fx = json.loads((TRIPS / base).read_text())
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx))
    return path


def test_N14_ten_thousand_legs_is_answered_or_refused_but_never_a_crash(tmp_path, ui):
    def mutate(fx):
        leg = copy.deepcopy([l for l in fx["legs"] if l["kind"] == "flight"][0])
        fx["legs"] = []
        for i in range(10000):
            one = copy.deepcopy(leg)
            one["id"] = f"L{i}"
            fx["legs"].append(one)

    path = shaped(tmp_path, "n14_many", mutate)
    assert path.stat().st_size > 1_000_000
    code, out = run_cli(path, tmp_path)
    assert "Traceback" not in out, out[-400:]
    shutil.copy(path, ui.trips_dir / path.name)
    assert ui.get("/api/trips").status == 200
    r = ui.post("/api/trips/n14_many/run", OFFLINE)
    assert r.status != 500, r.text[:200]


def test_N15_duplicate_leg_ids_are_not_silently_merged(tmp_path):
    def mutate(fx):
        first = copy.deepcopy(fx["legs"][0])
        second = copy.deepcopy(fx["legs"][0])
        second["cash_options"] = [{"label": "SECOND", "amount": 999.0, "currency": "USD"}]
        fx["legs"] = [first, second]

    path = shaped(tmp_path, "n15_dupes", mutate)
    code, out = run_cli(path, tmp_path)
    assert "Traceback" not in out
    rows = [l for l in out.splitlines() if l.startswith("│ A1")]
    assert code == 1 or len(rows) == 2, (
        f"two legs share an id and only {len(rows)} row(s) were printed: one leg vanished")


def test_N16_a_leg_id_that_collides_with_the_pages_own_selectors_is_still_text(tmp_path, ui):
    def mutate(fx):
        fx["legs"] = [copy.deepcopy(fx["legs"][0])]
        fx["legs"][0]["id"] = 'leg-row-B1"><img src=x onerror=alert(1)>'

    path = shaped(tmp_path, "n16_collide", mutate)
    shutil.copy(path, ui.trips_dir / path.name)
    listing = ui.get("/api/trips")
    assert listing.status == 200
    r = ui.post("/api/trips/n16_collide/run", OFFLINE)
    assert r.status in (200, 422), r.text[:200]
    if r.status == 200 and r.json().get("legs"):
        leg = r.json()["legs"][0]
        assert leg["id"] == 'leg-row-B1"><img src=x onerror=alert(1)>'


def test_N17_a_deeply_nested_fixture_is_refused_without_a_crash(tmp_path, ui):
    """400 levels of nesting: `json.loads` hits Python's recursion limit. The
    number boundary does not cover it (RecursionError is a RuntimeError, not an
    ArithmeticError), so it reaches the reader as a traceback and the UI as a
    500 - the one property the boundary states in its own comment."""
    import sys as _sys

    deep = {"id": "n17_deep", "name": "PROBE", "description": "x", "source": "x",
            "trip_level_flags": [], "legs": []}
    node = deep
    for _ in range(400):
        node["notes"] = [{"deeper": {}}]
        node = node["notes"][0]["deeper"]
    path = tmp_path / "n17_deep.json"
    old = _sys.getrecursionlimit()
    _sys.setrecursionlimit(20000)
    try:
        path.write_text(json.dumps(deep))
    finally:
        _sys.setrecursionlimit(old)
    code, out = run_cli(path, tmp_path)
    assert "Traceback" not in out, out[-400:]
    shutil.copy(path, ui.trips_dir / path.name)
    assert ui.get("/api/trips").status == 200
    assert ui.post("/api/trips/n17_deep/run", OFFLINE).status != 500
