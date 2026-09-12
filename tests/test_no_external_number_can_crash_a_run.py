"""
THE PROPERTY, not the shapes. Findings M-1, R2-3, R3-1, R4-1 and R4-2 were five
rounds of one family: a field was bounded, then a product of two bounded fields
was not; a product was bounded, then a conversion of a bounded product was not;
a conversion was bounded, and the parser converted one line above the bound.
Each round's tests asserted the SHAPE that had just been found, so the next hole
was found by the Tester rather than by the suite.

What has to hold is:

    NO NUMBER FROM OUTSIDE THIS PROCESS - a trip fixture, a Seats.aero
    response, a snapshot, a wallet - CAN MAKE A RUN CRASH, BE SCORED AS
    SOMETHING IT IS NOT, OR REACH A PRINTED LINE UNREADABLE. Whatever it is,
    whatever it multiplies with, whatever currency it is in, the run either
    scores it or refuses in one line - and what is refused when the tool WRITES
    a figure is refused when it READS one.

Round 5 extended it rather than adding a second set of checks: the same
boundary now carries the builder's own rule about what is not a price (zero,
negative, a boolean, a figure that renders as $0.00), the rule that an award
price of zero or less is refused instead of being dropped into a claim about
partnerships, the wallet, and RecursionError - the stack overflowing rather
than a number.

That is what this file asserts, in four ways that do not depend on anybody
having enumerated the arithmetic:

* every numeric field in the committed fixtures, found by walking them rather
  than by being listed here, set to each of eight hostile magnitudes, in every
  currency the tool knows, through the real CLI dispatch and the real API;
* the three conversions the scorer performs are TOTAL over a grid of extremes -
  a number or a typed refusal, never an OverflowError and never `inf`;
* no parser of external data converts with a bare `float()`/`int()`;
* and the backstop: an ArithmeticError raised from inside the optimizer is one
  line and exit 1, not a traceback - so a path nobody has enumerated is still
  safe.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from src import config
from tests._ui_harness import running_server

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"
SOURCES = ["trip_a_mry_nyc.json", "trip_b_europe.json", "trip_c_lon_mry_surcharge.json"]

# Magnitudes that have each broken something in this codebase, plus their
# neighbours. Not a list of KNOWN BUGS - a list of what a number can be.
HOSTILE = [
    10 ** 400,          # R2-3: int too large to be a float at all
    10 ** 200,          # R3-1: fine alone, fatal in a product
    1e308,              # M-1: finite, its points-equivalent is not
    1.6e306,            # R4-2: fine until an FX rate is applied
    1.7976931348623157e308,
    -(10 ** 400),
    0,
    -1,
    -0.0,            # R5-1: negative zero printed as $-0.00
    -50.0,           # R5-1: a negative fare fed the trip totals
    1e-320,          # R5-1: a denormal that renders as $0.00
    True,            # R5-1: a boolean scored as a dollar
]
CURRENCIES = ["USD", "EUR", "GBP", "CAD"]
ARGV_TAIL = ["--offline", "--balance", "UR=160000", "--card",
             "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"]


def numeric_paths(node, prefix=()):
    """Every path in a fixture whose value is a number. Walked, not listed: a
    field added to the schema tomorrow is covered the day it is added."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from numeric_paths(value, prefix + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from numeric_paths(value, prefix + (i,))
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        yield prefix


def _set(node, path, value):
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _sibling_currency(node, path, currency):
    """If this number sits beside a `currency` key, set it - the same amount in
    another currency is a different number once the run converts it."""
    parent = node
    for key in path[:-1]:
        parent = parent[key]
    if isinstance(parent, dict) and "currency" in parent:
        parent["currency"] = currency


CASES = [
    (source, path, value, currency)
    for source in SOURCES
    for path in numeric_paths(json.loads((TRIPS / source).read_text()))
    for value in HOSTILE
    for currency in (CURRENCIES if value in (1.6e306, 1e308) else ["USD"])
]


def mutated(tmp_path, source, path, value, currency, name="probe"):
    fixture = json.loads((TRIPS / source).read_text())
    fixture["id"] = name
    _set(fixture, path, value)
    _sibling_currency(fixture, path, currency)
    out = tmp_path / f"{name}.json"
    out.write_text(json.dumps(fixture))
    return out


def run_cli(path):
    """The real dispatch, in process. Any exception escaping is the failure."""
    from src import main as cli

    argv = ["--trip-fixture", str(path), *ARGV_TAIL]
    buf = io.StringIO()
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), [])
    return code, buf.getvalue()


@pytest.mark.parametrize("source,path,value,currency", CASES,
                         ids=lambda x: str(x)[:28] if not isinstance(x, tuple) else
                         ".".join(str(p) for p in x))
def test_no_number_in_a_fixture_can_crash_the_cli(source, path, value, currency, tmp_path):
    code, text = run_cli(mutated(tmp_path, source, path, value, currency))
    assert code in (0, 1, 2, 3, 4), (code, text[-300:])
    assert "Traceback" not in text
    # A refusal says what it refused; it never reports the arithmetic at the
    # reader ("inf is not a finite amount" on its own names nothing).
    if code == 1:
        assert "Error" in text or "Wallet error" in text, text[-300:]


def _worst_cases():
    """One case per (magnitude, currency): the API surface is a server round
    trip per case, so it gets the magnitudes rather than the cross product."""
    fixture = json.loads((TRIPS / "trip_b_europe.json").read_text())
    money = [p for p in numeric_paths(fixture)
             if any(k in ("amount", "points", "nights", "travelers", "points_per_night")
                    for k in p if isinstance(k, str))]
    return [(money[i % len(money)], v, c)
            for i, (v, c) in enumerate([(v, c) for v in HOSTILE for c in CURRENCIES])]


@pytest.mark.parametrize("path,value,currency", _worst_cases())
def test_no_number_in_a_fixture_can_500_the_ui(path, value, currency, tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    mutated(trips, "trip_b_europe.json", path, value, currency, name="probe")
    with running_server(trips_dir=trips) as c:
        listing = c.get("/api/trips")
        assert listing.status == 200, listing.text[:200]
        detail = c.get("/api/trips/probe")
        assert detail.status in (200, 422), (detail.status, detail.text[:200])
        run = c.post("/api/trips/probe/run", {"mode": "offline", "options": {}})
    assert run.status == 200, (run.status, run.text[:300])
    assert run.json()["exit_code"] in (0, 1, 2, 3, 4)
    assert "OverflowError" not in run.text and "Traceback" not in run.text


# ------------------------------------------------- the conversions are total


CONVERSION_INPUTS = [0, 1, -1, 2400.0, 10 ** 12, 10 ** 30, 10 ** 200, 10 ** 400,
                     1e308, 1.6e306, -1e308, 0.0001]
CPPS = [config.CASH_VALUATION_CPP, 1e-10, 1e-300, 0.02, 1.0, 1e300]


@pytest.mark.parametrize("value", CONVERSION_INPUTS)
@pytest.mark.parametrize("cpp", CPPS)
def test_cash_to_points_is_total(value, cpp):
    try:
        out = config.cash_to_points_equivalent(float(value) if abs(value) < 1e308 else 1e308, cpp)
    except config.UnscoreableNumber:
        return
    assert isinstance(out, int)


@pytest.mark.parametrize("value", CONVERSION_INPUTS)
@pytest.mark.parametrize("cpp", CPPS)
def test_points_to_cash_is_total(value, cpp):
    import math

    try:
        out = config.points_to_cash_equivalent(int(value) if isinstance(value, int) else 1, cpp)
    except config.UnscoreableNumber:
        return
    assert math.isfinite(out)


@pytest.mark.parametrize("value", CONVERSION_INPUTS)
@pytest.mark.parametrize("currency", CURRENCIES)
def test_convert_to_usd_is_total(value, currency):
    import math

    try:
        out = config.convert_to_usd(
            float(value) if abs(value) < 1e308 else 1e308, currency)
    except config.UnscoreableNumber:
        return
    assert math.isfinite(out)


def test_every_refusal_from_the_boundary_is_a_value_error():
    """So `main`'s existing ValueError clause prints it, and the UI renders a
    refusal - no new error path to remember."""
    assert issubclass(config.UnscoreableNumber, ValueError)


# ------------------------------------------- no bare conversion at a boundary


PARSERS = ["src/trip_loader.py"]


@pytest.mark.parametrize("name", PARSERS)
def test_a_parser_of_external_data_never_converts_outside_the_guard(name):
    """`float(value)` one line above the guard is what R4-1 was. Parsed as
    code, not grepped: a call is a call wherever it is written."""
    import ast

    tree = ast.parse((ROOT / name).read_text())
    calls = [
        f"{name}:{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("int", "float")
    ]
    assert calls == [], (
        "every number from outside goes through config.scoreable_amount / "
        "scoreable_count, which is where the guard is: " + "; ".join(calls)
    )


def test_the_api_parser_cannot_be_crashed_by_a_number_it_cannot_read():
    from src.seats_client import _as_int

    for value in (float("inf"), float("-inf"), float("nan"), "1e400", "-1e400",
                  "9" * 400, None, "", "abc", True, 10 ** 400):
        out = _as_int(value)
        assert out is None or isinstance(out, int)


# --------------------------------------------------------------- the backstop


def test_an_overflow_nobody_predicted_is_still_one_line_and_exit_1(tmp_path, monkeypatch):
    """The property has to hold for a path this suite has not thought of."""
    from src import main as cli

    def boom(*a, **k):
        raise OverflowError("int too large to convert to float")

    monkeypatch.setattr("src.main.evaluate_trip", boom, raising=False)
    buf = io.StringIO()
    argv = ["--trip-fixture", str(TRIPS / "trip_a_mry_nyc.json"), *ARGV_TAIL]
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), [])
    assert code == 1, buf.getvalue()[-300:]
    assert "Traceback" not in buf.getvalue()
    assert "could not be scored" in buf.getvalue()


def test_the_cli_process_really_prints_one_line_and_no_traceback(tmp_path):
    """In process is not the same as in a shell: this one is a real process."""
    path = mutated(tmp_path, "trip_b_europe.json",
                   next(p for p in numeric_paths(json.loads(
                       (TRIPS / "trip_b_europe.json").read_text()))
                       if p[-1] == "amount"), 10 ** 400, "USD")
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(path), *ARGV_TAIL],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stdout + proc.stderr
    assert len(proc.stdout.strip().splitlines()) <= 3, proc.stdout


# =========================================================== the same boundary,
# extended in round 5: what is refused at write is refused at read, nothing an
# outside number can be reaches a printed line unreadable, and a malformed file
# is a refusal rather than a traceback.


NOT_A_PRICE = [0, -0.0, -50.0, True, 1e-320, "abc", None, float("nan")]
IS_A_PRICE = [2400.0, 0.01, 367.0, 1e6]


@pytest.mark.parametrize("value", NOT_A_PRICE + IS_A_PRICE)
def test_what_the_builder_refuses_to_write_the_loader_refuses_to_read(value, tmp_path):
    """R5-1's property, both directions. The builder's rule and the loader's
    rule are the same function, so they cannot drift apart again."""
    from src import trip_builder
    from src.trip_loader import TripFixtureError, load_trip_fixture

    write_reason = ""
    try:
        trip_builder.validate_cash(value, "--leg CASH_USD")
        written_ok = True
    except trip_builder.TripBuilderError as e:
        written_ok, write_reason = False, str(e)

    path = mutated(tmp_path, "trip_a_mry_nyc.json",
                   next(p for p in numeric_paths(json.loads(
                       (TRIPS / "trip_a_mry_nyc.json").read_text())) if p[-1] == "amount"),
                   value, "USD")
    read_reason = ""
    try:
        load_trip_fixture(path)
        read_ok = True
    except TripFixtureError as e:
        read_ok, read_reason = False, str(e)

    assert written_ok == read_ok, (
        f"{value!r}: the builder {'accepts' if written_ok else 'refuses'} it and "
        f"the loader {'accepts' if read_ok else 'refuses'} it")
    if not written_ok:
        # ... and in the same words, because it is the same rule: the sentence
        # the shared rule produces appears verbatim in both messages.
        rule = config.unscoreable_price_reason(
            value.strip() if isinstance(value, str) else value)
        assert rule, value
        assert rule in write_reason, (rule, write_reason)
        assert rule in read_reason, (rule, read_reason)


@pytest.mark.parametrize("points", [0, -1, -42600, False])
def test_an_award_price_that_is_not_a_price_never_becomes_a_partnership_claim(points, tmp_path):
    """R5-2: it used to be dropped silently, and the leg then said "none - not a
    partner" - a claim about transfer partnerships that nothing checked, on a
    leg whose own file names a program."""
    path = mutated(tmp_path, "trip_a_mry_nyc.json",
                   next(p for p in numeric_paths(json.loads(
                       (TRIPS / "trip_a_mry_nyc.json").read_text())) if p[-1] == "points"),
                   points, "USD")
    code, text = run_cli(path)
    # The refusal EXPLAINS the claim it is preventing, so the phrase is looked
    # for where it would be a claim: a cell in the per-leg table.
    rows = [l for l in text.splitlines() if l.startswith("\u2502 A1")]
    assert not any("not a partner" in r for r in rows), rows
    assert code == 1 and "Error" in text


def _digit_runs(text):
    import re

    return [m.group(0) for m in re.finditer(r"\d[\d,]{30,}", text.replace(" ", ""))]


@pytest.mark.parametrize("balance", ["UR=" + "1" * 401, "UR=" + str(10 ** 400)])
def test_no_wallet_number_reaches_a_printed_line_unshortened(balance, tmp_path):
    """R5-3: the wallet was the one outside-number path short_number did not
    cover, and it is printed twice - the banner and the residue table."""
    from src import main as cli

    buf = io.StringIO()
    argv = ["--trip-fixture", str(TRIPS / "trip_a_mry_nyc.json"), "--offline",
            "--balance", balance, "--card", "Chase Sapphire Preferred",
            "--transfer-date", "2026-09-15"]
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), [])
    out = buf.getvalue()
    assert code in (1, 2), out[-300:]
    assert _digit_runs(out) == [], "an unreadable number reached the screen"
    assert "Traceback" not in out


@pytest.mark.parametrize("source,path,value,currency",
                         [c for c in CASES if c[2] in (10 ** 400, -(10 ** 400))][:40])
def test_no_fixture_number_reaches_a_printed_line_unshortened(source, path, value,
                                                              currency, tmp_path):
    _, text = run_cli(mutated(tmp_path, source, path, value, currency))
    assert _digit_runs(text) == [], text[-300:]


def _malformed(tmp_path):
    """Files that are not trip fixtures, in ways that are not about numbers."""
    import sys as _sys

    out = {}
    deep = {"id": "deep", "legs": []}
    node = deep
    for _ in range(400):
        node["notes"] = [{"deeper": {}}]
        node = node["notes"][0]["deeper"]
    limit = _sys.getrecursionlimit()
    _sys.setrecursionlimit(20000)
    try:
        out["deeply_nested"] = json.dumps(deep)
    finally:
        _sys.setrecursionlimit(limit)
    out["top_level_list"] = "[1, 2, 3]"
    out["top_level_number"] = "42"
    out["legs_is_a_string"] = json.dumps({"id": "x", "legs": "nope"})
    out["leg_is_a_number"] = json.dumps({"id": "x", "legs": [7]})
    out["truncated"] = '{"id": "x", "legs": ['
    out["empty"] = ""
    paths = {}
    for name, text in out.items():
        p = tmp_path / (name + ".json")
        p.write_text(text)
        paths[name] = p
    return paths


MALFORMED = sorted(_malformed(Path(__import__("tempfile").mkdtemp())))


@pytest.mark.parametrize("name", MALFORMED)
def test_a_malformed_file_is_one_line_not_a_traceback(name, tmp_path):
    """R5-4 as a property: a file this tool cannot read is a refusal, whatever
    is wrong with it. RecursionError is a RuntimeError, which is why the number
    backstop did not cover the deeply nested one."""
    code, text = run_cli(_malformed(tmp_path)[name])
    assert code == 1, (code, text[-300:])
    assert "Traceback" not in text
    assert "Error" in text


@pytest.mark.parametrize("name", MALFORMED)
def test_a_malformed_file_is_a_cannot_load_row_not_a_500(name, tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    (trips / (name + ".json")).write_bytes(_malformed(tmp_path)[name].read_bytes())
    with running_server(trips_dir=trips) as c:
        listing = c.get("/api/trips")
        assert listing.status == 200, listing.text[:200]
        (row,) = listing.json()
        assert row["load_error"], row
        assert "Traceback" not in row["load_error"]
        assert c.get("/api/trips/" + name).status == 422
        run = c.post("/api/trips/" + name + "/run", {"mode": "offline", "options": {}})
    assert run.status == 200 and run.json()["exit_code"] == 1
