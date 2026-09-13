"""
A. THE RECURRING FAILURE, IN THE JSON THE PAGE IS BUILT FROM, and the claim the
whole design rests on: "a UI run IS a CLI invocation".

A1-A3 hold every golden scenario's UI transcript against the golden file the
CLI produced at 3d8b99d - the UI's own output, not a re-run of the CLI.
A4-A12 are the unknown-discipline invariants across every scenario.

RED = a defect that exists. GREEN = held up.
"""
import json
import math
import re

import pytest

import scenarios as S
from conftest import ROOT, g

GOLDEN = ROOT / "tests" / "fixtures" / "cli_golden"

# Scenario -> golden file, for the goldens whose argv the UI builds exactly
# (the UI always adds --show-alternatives, so only these two match; every other
# scenario is held against the CLI directly in A1b).
GOLDENS = {"G1_offline_b": "G1", "G3_offline_c": "G3"}

# The lines a UI run necessarily has and a golden cannot: the redacted key line,
# the relocation banner this harness causes, and the budget counter, which the
# UI deliberately keeps across runs (D7).
PREAMBLE = re.compile(
    r"^(Seats\.aero key: .*|  POINTS_OPTIMIZER_[A-Z_]+ is set: .*|"
    r"  Seats\.aero budget: .*)$", re.M)
ALL = S.TRIP_SCENARIOS + S.SEARCH_SCENARIOS


def golden_body(name):
    text = (GOLDEN / f"{name}.txt").read_text()
    head, _, body = text.partition("\n\n")
    exit_code = int(re.search(r"^# exit: (\d+)$", head, re.M).group(1))
    return exit_code, body


def clean(text, work=None):
    """The goldens' normalizer, plus the tmp locations a UI run necessarily has."""
    masked, _ = g.normalize(text or "")
    masked = re.sub(r"/tmp/[^\s'\"]+", "<TMP>", masked)
    masked = re.sub(r"[ \t]+$", "", masked, flags=re.M)
    return masked.strip("\n")


@pytest.fixture(scope="module")
def runs(request):
    return {}


def run_once(ui, name, cache):
    if name not in cache:
        cache[name] = S.run(ui, name)
    return cache[name]


@pytest.mark.parametrize("name", sorted(GOLDENS))
def test_A1_the_uis_transcript_is_the_clis_own_output(ui, name):
    """D1/D2: the page shows the CLI's text because it IS the CLI's text."""
    payload = S.run(ui, name)
    want_exit, want = golden_body(GOLDENS[name])
    assert payload["exit_code"] == want_exit, (payload["exit_code"], want_exit)
    got = clean(PREAMBLE.sub("", payload["transcript"]))
    want = clean(PREAMBLE.sub("", want))
    if got != want:
        import difflib
        diff = "\n".join(list(difflib.unified_diff(want.splitlines(), got.splitlines(),
                                                   "golden", "ui", lineterm="", n=1))[:40])
        raise AssertionError(diff)


OFFLINE_SCENARIOS = ["G1_offline_b", "G2_offline_a", "G3_offline_c",
                     "G7_never_couple_offline"]


@pytest.mark.parametrize("name", OFFLINE_SCENARIOS)
def test_A1b_the_transcript_is_what_that_argv_prints_in_a_plain_cli_process(ui, name):
    """Stronger than a golden: run the CLI in this process with the argv the UI
    itself printed as "Equivalent command", and compare."""
    import io
    import shlex

    from rich.console import Console

    from src import main as cli
    from src.seats_client import SeatsClient

    payload = S.run(ui, name)
    argv = shlex.split(payload["argv_display"])[3:]
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    buf = io.StringIO()
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190))
    assert code == payload["exit_code"]
    assert clean(PREAMBLE.sub("", buf.getvalue())) == clean(
        PREAMBLE.sub("", payload["transcript"]))


@pytest.mark.parametrize("name", ALL)
def test_A2_every_run_serialises_without_a_nan_or_an_infinity(ui, name):
    payload = S.run(ui, name)
    text = json.dumps(payload, allow_nan=False)
    assert "Infinity" not in text and "NaN" not in text


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A3_a_range_or_withheld_headline_carries_no_single_percentage(ui, name):
    payload = S.run(ui, name)
    h = payload.get("headline")
    if not h:
        return
    if h["state"] in ("range", "withheld", "not_fundable"):
        assert h.get("pct") is None, h
    if h["state"] == "single":
        assert h.get("pct_low") is None and h.get("pct_high") is None, h
        assert h.get("qualifier"), "a single headline number with no qualifier"


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A4_an_unknown_never_carries_a_number(ui, name):
    payload = S.run(ui, name)
    bad = []
    for leg in payload.get("legs") or []:
        cells, nums = leg["cells"], leg["numbers"]
        if cells["cash"]["kind"] == "unknown" and nums.get("cash_usd") is not None:
            bad.append((leg["id"], "cash"))
        sur = leg.get("surcharge")
        if sur and not sur["known"]:
            if sur.get("point") is not None:
                bad.append((leg["id"], "surcharge.point"))
        if leg["taxes"]["status"] in ("unknown", "unconvertible") and \
                leg["taxes"].get("usd") is not None:
            bad.append((leg["id"], "taxes.usd"))
        if cells["score_points"]["kind"] in ("floor", "break_even") and \
                nums.get("score_points") is not None:
            bad.append((leg["id"], "score_points"))
    assert bad == [], bad


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A5_exit_3_and_4_are_never_a_quotable_number(ui, name):
    payload = S.run(ui, name)
    if payload["exit_code"] in (3, 4):
        h = payload.get("headline") or {}
        assert h.get("pct") is None and h.get("pct_low") is None, h
        assert h.get("state") in ("withheld", "not_fundable"), h


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A6_every_leg_marker_in_the_cli_lines_is_also_in_the_structure(ui, name):
    """7.1: a marker that lives only in `cli_lines`/`transcript` is a marker the
    structured page can lose."""
    payload = S.run(ui, name)
    tokens = ["UNKNOWN", "NOT $0", "WITHHELD", "UNVERIFIED", "NOT LOOKED UP",
              "NOT RECORDED", "API FAILED", "NEVER PRICED", "NOT ADDED",
              "CONFIRM BEFORE TRUSTING", "UNREADABLE", "REPARSED"]
    bad = []
    for leg in payload.get("legs") or []:
        cli = " ".join(l["text"] for l in leg.get("cli_lines", []))
        rest = json.dumps({k: v for k, v in leg.items() if k != "cli_lines"})
        for t in tokens:
            if t in cli and t not in rest:
                bad.append((leg["id"], t))
    assert bad == [], bad


def test_A7_a_search_cell_with_unknown_taxes_carries_no_dollar_figure(ui):
    payload = S.run(ui, "G9_search_ok")
    for row in payload["rows"]:
        for cabin, cell in row["cabins"].items():
            if cell is None:
                continue
            if not cell["taxes"]["known"]:
                assert cell["taxes"]["usd"] is None, (row["date"], cabin, cell["taxes"])
                assert cell["total"] is None or cell["total_is_floor"], cell


def test_A8_an_api_error_search_is_not_a_finding_of_no_award_space(ui):
    payload = S.run(ui, "G10_search_api_error")
    assert payload["state"] == "api_error"
    assert payload["rows"] == [] or payload["rows"] is None
    assert "NOT a finding" in (payload.get("api_error_note") or "") or \
        "API FAILURE" in (payload["transcript"] or "").upper()


def test_A9_a_truncated_search_never_reads_as_a_clean_empty_one(ui):
    payload = S.run(ui, "S_no_awards")
    assert payload["state"] in ("no_awards", "api_error")
    assert payload.get("coverage_note"), "an empty answer with no coverage note"


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A10_the_exit_label_matches_the_exit_code(ui, name):
    payload = S.run(ui, name)
    labels = {0: "OK", 1: "ERROR — NOTHING SCORED", 2: "WALLET ERROR", 3: "WITHHELD",
              4: "NOT EXECUTABLE"}
    assert payload["exit_label"] == labels[payload["exit_code"]]


def test_A11_a_withheld_couple_trip_reaches_exit_3_through_the_api(ui):
    from conftest import LIVE, response

    tid = S.install_g7(ui.trips_dir)
    base = g.Stub()

    def on_date(url, **kw):
        p = kw.get("params") or {}
        if url.endswith("/search") and (p.get("origin_airport"),
                                        p.get("destination_airport")) == ("LHR", "SFO"):
            row = g.vs_row()
            row["Date"], row["ParsedDate"] = "2027-01-29", "2027-01-29T00:00:00Z"
            return response({"data": [row]})
        return base(url, **kw)

    ui.net.impl = on_date
    body = dict(LIVE, options={**LIVE["options"], "refresh": True})
    pf, r = ui.run_trip(tid, body)
    payload = r.json()
    assert payload["exit_code"] == 3, payload["exit_code"]
    assert payload["headline"]["state"] == "withheld"
    assert "2+ travellers" in (payload["headline"]["withheld_reason"] or "")
    l2 = [l for l in payload["legs"] if l["id"] == "L2"][0]
    assert l2["numbers"]["score_points"] is None
    assert "not priced" in " ".join(s["text"] for s in l2["cells"]["verdict"]["segments"]).lower()


@pytest.mark.parametrize("name", S.TRIP_SCENARIOS)
def test_A12_no_leg_claims_a_partnership_it_did_not_check(ui, name):
    """F-1: 'not a partner' may only appear where a partner really was looked
    for - never on an API failure and never on a leg that was never priced."""
    payload = S.run(ui, name)
    for leg in payload.get("legs") or []:
        path = " ".join(s["text"] for s in leg["cells"]["path"]["segments"]).lower()
        prov = " ".join(s["text"] for s in leg["cells"]["provenance"]["segments"]).lower()
        if "api failed" in prov or leg["cells"]["path"]["kind"] == "never_priced":
            assert "not a partner" not in path, (name, leg["id"], path)
