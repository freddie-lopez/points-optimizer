"""
O. RE-TEST 6: the extended boundary (one rule for write and read, the
price/amount split, `scoreable_points`), and CENT-LEVEL ARITHMETIC, which I
named as untested at the end of re-test 5.

RED = a defect that exists. GREEN = the attack held up.
"""
import copy
import json
import re
import subprocess

import pytest

from conftest import LIVE, OFFLINE, ROOT, g, response

TRIPS = ROOT / "tests" / "fixtures" / "trips"
PY = str(ROOT / ".venv" / "bin" / "python")


def cell(leg, name):
    return "".join(s["text"] for s in leg["cells"][name]["segments"])


def money(text):
    found = re.findall(r"\$\s?(-?[\d,]+\.\d{2})", text or "")
    return [float(x.replace(",", "")) for x in found]


def totals_of(run):
    return {r["label"].strip(): r["value"].strip() for r in run["totals_rows"]}


def fixture(ui, name, base, mutate):
    fx = json.loads((TRIPS / base).read_text())
    fx["id"] = name
    mutate(fx)
    (ui.trips_dir / f"{name}.json").write_text(json.dumps(fx))
    return name


# ======================================= 1. one rule, both sides of the file


SPELLINGS = [
    "2400", " 2400 ", "2400.00", "+2400", "2,400", "$2400", "0", "-0.0", "0.0", "0.001",
    "0.004", "0.005", "-50", "1e3", "1E3", "1_000", "0x10", "١٢٣", "٣.٥", "nan", "inf",
    "", "   ", "abc", "2400\n", 0, -0.0, 0.0, 2400, 2400.5, True, False, None, [], {},
    1e-320, 10 ** 400,
]


@pytest.mark.parametrize("value", SPELLINGS, ids=lambda v: repr(v)[:14])
def test_O1_what_the_builder_refuses_to_write_the_loader_refuses_to_read(value):
    """One rule, both surfaces. A value either goes in and comes back, or is
    refused on both sides - whatever spelling it arrives in."""
    from src import config
    from src import trip_builder as tb

    try:
        written = tb.validate_cash(value, "--leg CASH_USD")
        write_ok, write_msg = True, ""
    except tb.TripBuilderError as e:
        write_ok, write_msg, written = False, str(e), None
    try:
        read = config.scoreable_price(value, "leg 'X' cash option amount")
        read_ok, read_msg = True, ""
    except config.UnscoreableNumber as e:
        read_ok, read_msg, read = False, str(e), None
    assert write_ok == read_ok, (
        f"{value!r}: the builder says {'ok' if write_ok else write_msg[:80]} and the "
        f"loader says {'ok' if read_ok else read_msg[:80]}")
    if write_ok:
        assert written == read
    else:
        # the same rule, so the same reason - compared on the sentence, not on
        # the quoted value (the builder strips a string before quoting it, so
        # "   " is reported as "''" there and as "'   '" at load).
        def rule(msg):
            tail = msg.split(": ", 1)[-1]
            for marker in ("is not a number", "is refused", "is not a finite amount",
                           "too large to score", "does not survive", "is not a whole number"):
                if marker in tail:
                    return marker
            return tail[:40]

        assert rule(read_msg) == rule(write_msg), (write_msg[:120], read_msg[:120])


def test_O2_a_zero_surcharge_is_still_a_real_figure(ui):
    """The split the fix rests on: a $0.00 FARE is silence, a $0.00 carrier
    SURCHARGE is a finding this codebase insists on printing."""
    name = fixture(ui, "o2_zero_surcharge", "trip_a_mry_nyc.json",
                   lambda fx: [l for l in fx["legs"] if l["kind"] == "flight"][0]
                   ["points_candidates"][0].__setitem__("cash_surcharge", 0.0))
    run = ui.run_trip(name, OFFLINE)[1].json()
    assert run["exit_code"] in (0, 3, 4), run.get("refusal")
    b = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    zero_surcharges = [l["id"] for l in b["legs"] if cell(l, "surcharge").strip() == "$0.00"]
    assert zero_surcharges, "no leg prints a $0.00 surcharge any more"


def test_O3_a_zero_or_negative_award_price_from_the_api_is_unreadable_not_no_partner(ui):
    """R5-2's shape on the live path: the value must not be dropped into a
    partnership claim, and it must not be scored either."""
    base = g.Stub()

    def row(cost):
        r = copy.deepcopy(g.REAL["data"][0])
        r["Date"], r["ParsedDate"] = "2027-01-15", "2027-01-15T00:00:00Z"
        r["YMileageCost"] = cost
        return r

    for cost in ("0", "-50000", "0.5", ""):
        def impl(url, _c=cost, **kw):
            p = kw.get("params") or {}
            if url.endswith("/search") and (p.get("origin_airport"),
                                            p.get("destination_airport")) == ("SFO", "MAD"):
                return response({"data": [row(_c)], "hasMore": False})
            return base(url, **kw)

        ui.net.impl = impl
        run = ui.run_trip("trip_b_europe",
                          dict(LIVE, options={**LIVE["options"], "refresh": True}))[1].json()
        b1 = [l for l in run["legs"] if l["id"] == "B1"][0]
        assert "not a partner" not in cell(b1, "path"), (cost, cell(b1, "path"))
        assert "UNREADABLE" in cell(b1, "provenance"), (cost, cell(b1, "provenance"))
        assert b1["numbers"]["points_required"] in (None, 0) or \
            b1["numbers"]["points_required"] > 0


def test_O4_a_refused_award_price_says_what_the_silence_would_have_claimed(ui, tmp_path):
    """One bad candidate refuses the FILE rather than being dropped. That is the
    trade the fix makes, and the message has to earn it."""
    fx = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    fx["id"] = "o4_mixed"
    flight = [l for l in fx["legs"] if l["kind"] == "flight"][0]
    bad = copy.deepcopy(flight["points_candidates"][0])
    bad["points"] = 0
    flight["points_candidates"].append(bad)
    path = tmp_path / "o4_mixed.json"
    path.write_text(json.dumps(fx))
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(path), "--offline",
                        "--balance", "UR=160000", "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == 1 and "Traceback" not in out
    assert "not a partner" in out and "nothing checked" in out, out[-300:]


def test_O5_every_committed_fixture_still_loads_and_scores(ui):
    """The other half of a new refusal: nothing legal may have become illegal."""
    for trip in ("trip_a_mry_nyc", "trip_b_europe", "trip_c_lon_mry_surcharge"):
        run = ui.run_trip(trip, OFFLINE)[1].json()
        assert run["exit_code"] in (0, 3, 4), (trip, run.get("refusal"))
        assert run.get("legs"), trip
    b = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert b["headline"]["text"] == "2.04% - 11.03%" and b["headline"]["qualifier"] == "(badge)"


@pytest.mark.parametrize("amount,ok", [
    ("0.01", True), ("1", True), ("2400", True), ("199999.99", True), ("0.005", True),
    ("0", False), ("-1", False), ("0.004", False),
])
def test_O6_realistic_prices_are_accepted_and_only_silence_is_refused(ui, amount, ok):
    body = {"name": f"o6_{amount.replace('.', '_').replace('-', 'm')}", "cabin": "Y",
            "legs": [{"origin": "SFO", "destination": "LHR", "date": "2027-01-15",
                      "cash": amount}]}
    r = ui.post("/api/trips/draft", body)
    assert r.status == 200
    assert bool(r.json().get("ok")) is ok, (amount, r.text[:160])


def test_O7_a_wallet_balance_no_one_can_read_is_refused_in_words(ui, tmp_path):
    """R5-3."""
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture",
                        str(TRIPS / "trip_a_mry_nyc.json"), "--offline", "--balance",
                        f"UR={10 ** 400}", "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert sum(1 for c in out if c.isdigit()) < 200, "the wallet banner printed the digits"
    assert "401-digit number" in out, out[-200:]
    r = ui.post("/api/wallet", {"balances": {"UR": str(10 ** 400)}, "cards": []})
    assert sum(1 for c in r.text if c.isdigit()) < 200, r.text[:200]


def test_O8_a_deeply_nested_fixture_is_a_refusal_now(ui, tmp_path):
    """R5-4."""
    import sys as _sys

    deep = {"id": "o8_deep", "name": "PROBE", "description": "x", "source": "x",
            "trip_level_flags": [], "legs": []}
    node = deep
    for _ in range(400):
        node["notes"] = [{"deeper": {}}]
        node = node["notes"][0]["deeper"]
    old = _sys.getrecursionlimit()
    _sys.setrecursionlimit(20000)
    try:
        (tmp_path / "o8_deep.json").write_text(json.dumps(deep))
    finally:
        _sys.setrecursionlimit(old)
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(tmp_path / "o8_deep.json"),
                        "--offline", "--balance", "UR=160000", "--card",
                        "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out[-400:]
    assert p.returncode == 1 and "Error:" in out
    import shutil

    shutil.copy(tmp_path / "o8_deep.json", ui.trips_dir / "o8_deep.json")
    assert ui.get("/api/trips").status == 200
    assert ui.post("/api/trips/o8_deep/run", OFFLINE).status != 500


# =========================================== 2. cent-level arithmetic


def test_O9_a_negative_fee_is_not_a_discount(ui):
    """`mandatory_fees.amount` is read as an AMOUNT, not a price, so a negative
    one is accepted - and it feeds the money exactly as a negative fare would."""
    name = fixture(ui, "o9_neg_fee", "trip_a_mry_nyc.json",
                   lambda fx: [l for l in fx["legs"] if l["kind"] == "flight"][0].__setitem__(
                       "mandatory_fees", [{"label": "PROBE credit", "amount": -500.0,
                                           "currency": "USD"}]))
    run = ui.run_trip(name, OFFLINE)[1].json()
    if run.get("refusal"):
        return                      # refused: that is the fix
    leg = run["legs"][0]
    assert leg["numbers"]["score_cash"] is None or leg["numbers"]["score_cash"] >= 0, (
        f"a negative fee made the leg's cash side {leg['numbers']['score_cash']}: "
        f"{cell(leg, 'score_cash')}")
    assert "-" not in cell(leg, "cash_pts"), cell(leg, "cash_pts")


def test_O10_the_trip_total_is_the_sum_of_the_legs_to_the_cent(ui):
    for trip in ("trip_a_mry_nyc", "trip_b_europe", "trip_c_lon_mry_surcharge"):
        run = ui.run_trip(trip, OFFLINE)[1].json()
        shown = [money(cell(l, "score_cash")) for l in run["legs"]]
        total = totals_of(run).get("Pay cash for everything")
        assert total, trip
        assert abs(sum(x[0] for x in shown if x) - money(total)[0]) < 0.005, (
            trip, sum(x[0] for x in shown if x), total)


def test_O11_the_residue_balances_exactly(ui):
    for trip in ("trip_b_europe", "trip_c_lon_mry_surcharge"):
        run = ui.run_trip(trip, OFFLINE)[1].json()
        for row in run["residue_rows"]:
            if row.get("unconstrained"):
                continue
            start = int(row["starting"].replace(",", ""))
            spent = int(row["spent"].replace(",", ""))
            left = int(row["remaining"].replace(",", ""))
            assert start - spent == left, (trip, row)


def test_O12_a_table_of_sub_cent_figures_still_adds_up_on_the_page(ui):
    """Four legs at $10.005: each row prints $10.01 and the total prints $40.02,
    so the table does not add up. Reachable with real data through FX: a
    converted amount is sub-cent far more often than a captured fare is."""
    def mutate(fx):
        base = [l for l in fx["legs"] if l["kind"] == "flight"][0]
        legs = []
        for i in range(4):
            leg = copy.deepcopy(base)
            leg["id"] = f"P{i + 1}"
            leg["points_candidates"] = []
            leg.pop("mandatory_fees", None)
            leg["cash_options"] = [{"label": "PROBE", "amount": 10.005, "currency": "USD"}]
            legs.append(leg)
        fx["legs"] = legs

    name = fixture(ui, "o12_subcent", "trip_a_mry_nyc.json", mutate)
    run = ui.run_trip(name, OFFLINE)[1].json()
    shown = [money(cell(l, "score_cash"))[0] for l in run["legs"]]
    total = money(totals_of(run)["Pay cash for everything"])[0]
    assert abs(sum(shown) - total) < 0.005, (
        f"the rows print {shown} (sum {sum(shown):.2f}) and the total prints {total:.2f}")


def test_O13_a_foreign_currency_leg_rounds_the_same_way_everywhere(ui):
    """Trip B's hotels are quoted in EUR and GBP: the converted figure the table
    shows and the one the total uses must agree to the cent."""
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    for leg in run["legs"]:
        shown = money(cell(leg, "cash"))
        if not shown or not leg.get("cash"):
            continue
        exact = leg["cash"].get("amount_usd")
        if exact is None:
            continue
        assert abs(round(exact, 2) - shown[0]) < 0.005, (leg["id"], exact, shown)
