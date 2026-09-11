"""
Re-test 3 (fixes b9d7db2..347e6d7). The round-2 fixes, attacked for their whole
class, and the NEW behaviour they added: one trips snapshot file per id, a
refused capture deleting its own cache entry, capture ending with the label
check, the R2-5 forgery checks (false refusals of an honest capture), and the
R2-7 escaping change (text lost or changed beyond the label).

RED = a defect that exists. GREEN = held up.
"""
import copy
import io
import json
import os
import subprocess
import tarfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from conftest import (  # noqa: F401
    BASE, B4_VS, ROOT, Stub, aid_for, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src import config, seats_trips, trips_tools
from src.models import METAL_REASONS, MetalLookup, MetalStatus
from src.response_cache import ResponseCache
from src.yq_inclusion import YqVerdict
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row

PY = str(ROOT / ".venv" / "bin" / "python")
KEY = "PROBEKEYr3q1w2e3r4t5y6u7"
LABEL = seats_trips.PARSER_UNVERIFIED_LABEL


# ===========================================================================
# One trips snapshot file per id (R2-2)
# ===========================================================================


def test_a_re_fetch_of_the_same_id_with_the_same_bytes_adds_no_file(tmp_path):
    snap = tmp_path / "s"
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=snap, ttl_seconds=0)
    for _ in range(3):
        evaluate(Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])}), cache=cache)
    files = list((snap / "trips_endpoint").glob("*.json"))
    rows = [l for l in (snap / "trips_endpoint" / "MANIFEST.md").read_text().splitlines() if "| trips:" in l]
    assert len(files) == 1 and len(rows) == 3


def test_search_snapshots_are_still_deduplicated_across_requests(tmp_path):
    """The per-id rule is trips only: identical search bytes for two legs still share one file."""
    snap = tmp_path / "s"
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=snap, ttl_seconds=0)
    same = [row(source="aeroplan", cost="50000", taxes=4460, currency="CAD", airlines="AC", origin="SFO",
                dest="MAD", iso="2027-01-15", cabin="Y", rid="Sameidxxxxxxxxxxxxxxxxxxxx")]
    evaluate(Stub(rows_for=lambda o, d, iso: same), cache=cache, trips_mode="off")
    search_files = list(snap.glob("*.json"))
    assert len(search_files) == 1, [p.name for p in search_files]


# ===========================================================================
# A refused capture deletes its own cache entry (R2-3)
# ===========================================================================


def _search_side(pages_for_lhr, trips=None):
    log = []

    def side(url, **kw):
        params = dict(kw.get("params") or {})
        log.append((url, params))
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        if url.endswith("/search"):
            if (params.get("origin_airport"), params.get("destination_airport")) == ("LHR", "SFO"):
                body = pages_for_lhr[1] if params.get("cursor") and len(pages_for_lhr) > 1 else pages_for_lhr[0]
            else:
                from conftest import aeroplan_row, ROUTES
                o, d = params["origin_airport"], params["destination_airport"]
                body = {"data": [aeroplan_row(o, d, ROUTES[(o, d)][1])]}
        else:
            body = trips or tp.payload([tp.vs_direct()])
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    side.log = log
    return side


def _capture(tmp_path, side, *extra, out="real"):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=side):
        code = trips_tools.main(
            ["capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27", "--source",
             "virginatlantic", "--out-dir", str(tmp_path / out), "--api-key", KEY, "--yes", *extra],
            read=lambda p: "y", console=Console(file=buf, width=400))
    return code, " ".join(buf.getvalue().split())


def test_a_capture_served_a_complete_trip_run_entry_does_not_delete_it(tmp_path, capsys, monkeypatch):
    """The trip run wrote a COMPLETE 2-page LHR-SFO entry; a capture reads it (0 calls) and leaves it."""
    pages = [{"data": [vs_row()], "hasMore": True, "cursor": "p2"}, {"data": [], "hasMore": False}]
    side = _search_side(pages)
    run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch, stub=side)
    before = sorted(p.name for p in Path(config.CACHE_DIR).glob("*.json"))
    side.log.clear()
    code, out = _capture(tmp_path, side)
    after = sorted(p.name for p in Path(config.CACHE_DIR).glob("*.json"))
    assert not [u for u, p in side.log if u.endswith("/search")], "the capture re-searched"
    assert before == after


def test_a_refused_capture_deletes_only_its_own_key(tmp_path, capsys, monkeypatch):
    pages = [{"data": [vs_row()], "hasMore": True, "cursor": "p2"}, {"data": [], "hasMore": False}]
    side = _search_side(pages)
    run_cli(BASE + ["--allow-badge-fallback", "--trips", "off"], capsys, monkeypatch,
            stub=_search_side([{"data": [vs_row()]}]))
    # the trip run above cached every leg; drop only LHR-SFO so the capture has to fetch it
    lhr = [p for p in Path(config.CACHE_DIR).glob("*.json") if '"LHR"' in p.read_text()[:4000]
           and '"SFO"' in p.read_text()[:4000]]
    for p in lhr:
        p.unlink()
    others = sorted(p.name for p in Path(config.CACHE_DIR).glob("*.json"))
    code, out = _capture(tmp_path, side)
    assert code == 1 and "not kept in the cache" in out
    assert sorted(p.name for p in Path(config.CACHE_DIR).glob("*.json")) == others


def test_a_complete_one_page_capture_search_stays_cached(tmp_path):
    side = _search_side([{"data": [vs_row()], "hasMore": False}])
    code, out = _capture(tmp_path, side)
    assert code == 0, out[-300:]
    assert list(Path(config.CACHE_DIR).glob("*.json"))


# ===========================================================================
# capture ends with the label check (R2-4) - can an honest capture be refused?
# ===========================================================================


def _honest_side(trips_body, text_fn=json.dumps, row_=None):
    def side(url, **kw):
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        if url.endswith("/search"):
            body = {"data": [copy.deepcopy(row_ or vs_row())]}
            r.json.return_value = body
            r.text = json.dumps(body)
        else:
            r.text = text_fn(trips_body)
            r.json.return_value = json.loads(r.text)
        return r

    return side


HONEST_TEXT = {
    "compact": lambda b: json.dumps(b, separators=(",", ":")),
    "crlf_pretty": lambda b: json.dumps(b, indent=2).replace("\n", "\r\n"),
    "trailing_newline": lambda b: json.dumps(b) + "\n",
    "unicode_escaped": lambda b: json.dumps(b, ensure_ascii=True),
    "exponent_floats": lambda b: json.dumps(b).replace('"Distance": 5000', '"Distance": 5e3'),
    "sorted_keys": lambda b: json.dumps(b, sort_keys=True),
}


@pytest.mark.parametrize("how", sorted(HONEST_TEXT))
def test_an_honest_capture_flips_whatever_harmless_form_the_body_arrives_in(tmp_path, how):
    body = tp.payload([tp.vs_direct()])
    body["data"][0]["AvailabilitySegments"][0]["AircraftName"] = "Boeing 787-9 Dreamliner (Zürich)"
    code, out = _capture(tmp_path, _honest_side(body, HONEST_TEXT[how]))
    assert code == 0, out[-600:]
    assert "CAPTURED CLEAN" in out


def test_the_requested_cabin_need_not_be_the_matched_one(tmp_path):
    """--cabin W on a row whose W price has no itinerary, while J matches: the label check passes."""
    r = vs_row()
    r.update(WAvailable=True, WMileageCost="45000", WTotalTaxes=30000, WAirlines="VS", WRemainingSeats=2)
    code, out = _capture(tmp_path, _honest_side(tp.payload([tp.vs_direct()]), row_=r), "--cabin", "W")
    assert code == 0, out[-600:]


def test_an_honest_capture_whose_row_price_has_no_itinerary_is_refused_with_its_reason(tmp_path):
    """Observation (not a defect): a clean parse that matches no award in its row cannot flip - and says why."""
    body = tp.payload([tp.vs_direct(cost=62000)])
    code, out = _capture(tmp_path, _honest_side(body))
    assert code == 5
    assert "no award in the recorded availability row is matched" in out
    assert "Do NOT set" in out


def test_an_honest_capture_outside_a_real_dir_says_so(tmp_path):
    code, out = _capture(tmp_path, _honest_side(tp.payload([tp.vs_direct()])), out="captures")
    assert code == 5 and "not the real/ capture directory" in out


# ===========================================================================
# R2-6 and R2-7, for the whole class
# ===========================================================================


@pytest.mark.parametrize("code", sorted(METAL_REASONS[MetalStatus.NOT_LOOKED_UP]))
def test_every_not_looked_up_code_on_a_one_carrier_row_says_no_lookup_was_made(code):
    m = MetalLookup(status=MetalStatus.NOT_LOOKED_UP, reason_code=code, detail="x",
                    possible_carriers=("VS",), row_carriers=("VS",))
    assert "No lookup was made on this run" in m.render() and "This lookup" not in m.render()


@pytest.mark.parametrize("code", sorted(METAL_REASONS[MetalStatus.UNKNOWN]))
def test_every_unknown_code_on_a_one_carrier_row_keeps_this_lookup(code):
    m = MetalLookup(status=MetalStatus.UNKNOWN, reason_code=code, detail="x",
                    possible_carriers=("VS",), row_carriers=("VS",))
    assert "This lookup established nothing further" in m.render()


def _metal_lines_without_label(text):
    import re
    blocks = text.splitlines()
    pat = re.compile(r"by flight number|metal (its|the) itinerary lookup found|itinerary lookup names|"
                     r"[A-Z]{2}(, [A-Z]{2})* metal under|on [A-Z]{2} metal")
    # A surcharge rule's own citation (the surcharges.csv note "CONFIRMED BY
    # SOURCE: ... Upper Class on VS metal ...", printed as a source line and as a
    # "Surcharge note") describes the table row's scope, not this flight.
    return [" ".join(b.split())[:200] for b in blocks
            if pat.search(b) and LABEL not in " ".join(b.split()) and not b.strip().startswith("source:") and "CONFIRMED BY SOURCE" not in b]


@pytest.mark.parametrize("scenario", ["excludes_known", "excludes_ambiguous", "af_alternative", "other_award"])
def test_every_line_naming_looked_up_metal_carries_the_label(scenario):
    from src.formatter import print_alternatives
    kw = {}
    rows_for = vs_b4_rows()
    trips = {B4_VS: tp.payload([vs_itinerary(B4_VS)])}
    if scenario.startswith("excludes"):
        kw["yq_table"] = {"virginatlantic": YqVerdict("virginatlantic", "excludes_yq", date(2026, 9, 10),
                                                      "docs/yq-checks/x.md")}
        if scenario == "excludes_ambiguous":
            trips = {B4_VS: tp.payload([vs_itinerary(B4_VS), vs_itinerary(B4_VS, "DL41", trip_id="t2")])}
    if scenario == "af_alternative":
        rows_for = vs_b4_rows(airlines="VS, AF, DL")
        trips = {B4_VS: tp.payload([vs_itinerary(B4_VS, "AF83")])}
    if scenario == "other_award":
        from conftest import aeroplan_row

        def rows_for(o, d, iso):
            if (o, d) == ("LHR", "SFO"):
                return [aeroplan_row(o, d, iso), row(rid=B4_VS, iso=iso)]
            return None
    from src.formatter import print_leg_detail, print_leg_results, print_live_leg_detail, print_trip_totals
    results, totals, text, opts, fx = evaluate(Stub(rows_for=rows_for, trips=trips), **kw)
    buf = StringIO()
    wide = Console(file=buf, width=6000, no_color=True)  # one printed line = one text line, no wrapping
    res = list(results.values())
    for fn in (print_leg_results, print_leg_detail, print_live_leg_detail, print_alternatives):
        fn(res, console=wide)
    print_trip_totals(totals, console=wide)
    bare = _metal_lines_without_label(buf.getvalue())
    assert bare == [], bare
    if scenario == "excludes_known":
        rule = [l for l in buf.getvalue().splitlines()
                if l.strip().startswith("Surcharge:") and "captured from source" not in l]  # B4's modelled rule
        assert rule and all(LABEL in l for l in rule), rule


# --- R2-7's escaping: nothing lost or changed beyond the label ----------------------

RENDER = Path(__file__).parent / "_r3_cli_render.py"
SCENARIOS = ["known", "404", "off", "cap", "single", "af"]


def _tree_at(commit, tmp_path):
    data = subprocess.run(["git", "archive", commit, "src", "data", "tests/fixtures"], cwd=ROOT,
                          capture_output=True, check=True).stdout
    dst = tmp_path / commit
    dst.mkdir()
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dst)
    return dst


def _norm(text):
    import re
    text = re.sub(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(\+00:00|Z)?", "<TS>", text)
    text = re.sub(r"\d{8}T\d{4}Z", "<STAMP>", text)
    text = re.sub(r"/tmp/[^\s)]+", "<TMP>", text)
    text = re.sub(r"\d+ minutes? ago", "<AGO>", text)
    text = text.replace(" " + LABEL, "").replace("No lookup was made on this run",
                                                 "This lookup established nothing further")
    return [l.rstrip() for l in text.splitlines()]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_full_cli_output_is_the_round_2_output_plus_the_label_and_nothing_else(tmp_path, scenario):
    """
    Full `main()` output (3000 columns, no wrapping) at 9bf7c90 vs HEAD, same stub:
    after removing the parser label and the R2-6 wording, every line is identical.
    """
    old = _tree_at("9bf7c90", tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "SEATS_AERO_KEY"}
    env["PYTHONPATH"] = str(ROOT / "tests" / "_child_guard")
    outs = {}
    for name, root in (("old", old), ("new", ROOT)):
        r = subprocess.run([PY, str(RENDER), str(root), scenario], cwd=root, env=env,
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-800:]
        outs[name] = _norm(r.stdout)
    assert outs["new"] == outs["old"]
