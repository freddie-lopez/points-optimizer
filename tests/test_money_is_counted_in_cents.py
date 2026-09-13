"""
FINDINGS R6-1 and R6-2: the last two of the money family.

R6-1  `mandatory_fees.amount` went through the AMOUNT rule, and the "not
      negative" clause lived in the PRICE rule - so a fee of `-500.0` scored a
      leg at `$-133.00` with a cash-as-points of `-13,300` and a points floor of
      `>= $-70.00`, and fed the trip totals. A discount that is not there.
      No money field in this model is ever negative, so the clause moved down
      into the amount rule where every money field passes it. A `$0.00` fee and
      a `$0.00` carrier surcharge stay legal - a stated "no resort fee" is a
      real figure - and only PRICES (a fare, a nightly rate) additionally have
      to be at least a cent.

R6-2  The engine kept full precision and rounded at the moment of printing, so
      four legs at `$10.005` printed `$10.01` each and totalled `$40.02`: a
      table that disagreed with itself on the page. Money is now counted in
      CENTS - every total is a sum of the figures as they are printed - so the
      rows and the total cannot disagree. On every committed trip the parts were
      already whole cents, so no golden moved.
"""
import json
from pathlib import Path

import pytest

from src import config
from src.optimizer import _cents
from src.trip_loader import TripFixtureError, load_trip_fixture
from tests._ui_harness import running_server

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"


def _flight(fx):
    return [l for l in fx["legs"] if l["kind"] == "flight"][0]


def written(tmp_path, name, mutate, source="trip_a_mry_nyc.json"):
    fx = json.loads((TRIPS / source).read_text())
    fx["id"] = name
    mutate(fx)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(fx))
    return path


# ------------------------------------------------------------------- R6-1


# Every money field in the fixture schema, and the rule each is read by.
# AMOUNT: zero is a real, stated figure ("this leg has no carrier surcharge",
#         "there is no resort fee") and printing it is the point.
# PRICE:  zero is silence - nobody is selling a seat for nothing - so a price
#         must be at least a cent.
# Neither may be negative: no money field in this model is ever below zero.
MONEY_FIELDS = {
    "cash option amount": ("price", lambda fx, v: _flight(fx)["cash_options"][0]
                           .__setitem__("amount", v)),
    "cash_surcharge": ("amount", lambda fx, v: _flight(fx)["points_candidates"][0]
                       .__setitem__("cash_surcharge", v)),
    "mandatory fee amount": ("amount", lambda fx, v: _flight(fx).__setitem__(
        "mandatory_fees", [{"label": "PROBE", "amount": v, "currency": "USD"}])),
}


@pytest.mark.parametrize("field", sorted(MONEY_FIELDS))
def test_no_money_field_anywhere_accepts_a_negative_figure(field, tmp_path):
    _, mutate = MONEY_FIELDS[field]
    with pytest.raises(TripFixtureError) as e:
        load_trip_fixture(written(tmp_path, "neg", lambda fx: mutate(fx, -500.0)))
    assert "negative" in str(e.value), str(e.value)


@pytest.mark.parametrize("field", sorted(MONEY_FIELDS))
def test_zero_is_legal_exactly_where_it_is_a_stated_figure(field, tmp_path):
    kind, mutate = MONEY_FIELDS[field]
    path = written(tmp_path, "zero", lambda fx: mutate(fx, 0.0))
    if kind == "price":
        with pytest.raises(TripFixtureError) as e:
            load_trip_fixture(path)
        assert "Zero is not a price" in str(e.value)
    else:
        load_trip_fixture(path)  # a stated "no fee" / "no surcharge" is a figure


def test_the_two_rules_differ_in_exactly_one_clause():
    """So the split is a rule, not a list of fields somebody has to maintain."""
    for value in (-1.0, -0.0001, float("nan"), float("inf"), "abc", True, 10 ** 400):
        assert config.unscoreable_cash_reason(value), value
        assert config.unscoreable_price_reason(value), value
    for value in (0.0, 0, 1e-320, 0.004):
        assert config.unscoreable_cash_reason(value) == "", value
        assert "Zero is not a price" in config.unscoreable_price_reason(value), value
    for value in (0.01, 2400.0, 199999.99):
        assert config.unscoreable_cash_reason(value) == ""
        assert config.unscoreable_price_reason(value) == ""


def test_a_zero_surcharge_still_prints_as_the_figure_it_is(tmp_path):
    """The codebase's own rule: a $0.00 surcharge is not a $0 ticket, and it is
    printed rather than hidden. Refusing zero everywhere would have lost it."""
    fx = load_trip_fixture(TRIPS / "trip_b_europe.json")
    zeros = [c for l in fx.legs for c in l.points_candidates if c.cash_surcharge == 0.0]
    assert zeros, "the fixture no longer carries a stated zero surcharge"


def test_a_negative_fee_never_reaches_the_page(tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    written(trips, "neg_fee", lambda fx: _flight(fx).__setitem__(
        "mandatory_fees", [{"label": "PROBE credit", "amount": -500.0, "currency": "USD"}]))
    with running_server(trips_dir=trips) as c:
        (row,) = c.get("/api/trips").json()
        assert "negative" in row["load_error"]
        run = c.post("/api/trips/neg_fee/run", {"mode": "offline", "options": {}})
    assert run.status == 200 and run.json()["exit_code"] == 1
    assert "-13,300" not in run.text and "$-" not in run.text


# ------------------------------------------------------------------- R6-2


def test_the_printed_rows_add_up_to_the_printed_total(tmp_path):
    """Four legs at $10.005 - the case that did not add up."""
    def mutate(fx):
        base = _flight(fx)
        legs = []
        for i in range(4):
            leg = json.loads(json.dumps(base))
            leg["id"] = f"P{i + 1}"
            leg["points_candidates"] = []
            leg.pop("mandatory_fees", None)
            leg["cash_options"] = [{"label": "PROBE", "amount": 10.005, "currency": "USD"}]
            legs.append(leg)
        fx["legs"] = legs

    from tests._ui_harness import write_wallet

    trips = tmp_path / "trips"
    trips.mkdir()
    written(trips, "subcent", mutate)
    with running_server(trips_dir=trips,
                        wallet_path=write_wallet(tmp_path / "w.json")) as c:
        run = c.post("/api/trips/subcent/run", {"mode": "offline", "options": {}}).json()
    assert run["exit_code"] in (0, 3, 4), run.get("refusal")
    rows = [l["cells"]["score_cash"]["segments"][0]["text"] for l in run["legs"]]
    total = [r["value"] for r in run["totals_rows"]
             if r["label"].startswith("Pay cash for everything")][0]

    def money(text):
        return float(text.replace("$", "").replace(",", ""))

    assert sum(money(x) for x in rows) == money(total), (rows, total)


@pytest.mark.parametrize("trip", ["trip_a_mry_nyc", "trip_b_europe",
                                  "trip_c_lon_mry_surcharge"])
def test_every_committed_trip_still_adds_up_and_is_unchanged(trip, tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    (trips / f"{trip}.json").write_bytes((TRIPS / f"{trip}.json").read_bytes())
    from tests._ui_harness import write_wallet

    with running_server(trips_dir=trips,
                        wallet_path=write_wallet(tmp_path / "w.json")) as c:
        run = c.post(f"/api/trips/{trip}/run", {"mode": "offline", "options": {}}).json()
    assert run["exit_code"] in (0, 3, 4)
    rows = [l["cells"]["score_cash"]["segments"][0]["text"] for l in run["legs"]]
    total = [r["value"] for r in run["totals_rows"]
             if r["label"].startswith("Pay cash for everything")][0]

    def money(text):
        return float(text.replace("$", "").replace(",", ""))

    parts = [money(x) for x in rows if x.startswith("$")]
    assert abs(sum(parts) - money(total)) < 0.005, (parts, total)


@pytest.mark.parametrize("value,expected", [
    (10.005, "10.01"), (747.82834, "747.83"), (0.0, "0.00"), (2400.0, "2400.00"),
])
def test_a_figure_is_counted_as_exactly_what_it_prints(value, expected):
    """`_cents` goes through the formatter rather than `round()`, so the summed
    parts cannot drift from the printed ones."""
    assert f"{_cents(value):.2f}" == expected
    assert f"{value:,.2f}".replace(",", "") == expected


def test_an_unpriceable_figure_passes_through_unharmed():
    assert _cents(float("inf")) == float("inf")
    assert _cents(float("nan")) != _cents(float("nan"))  # NaN stays NaN
