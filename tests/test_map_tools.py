"""
`python -m src.map_tools capture-hubs` and `mark-searchable`, against a
stubbed transport. The transport is injected (`get=`), the key is passed by
FLAG so it is not in the environment and the key check is a real check, every
output path is under tmp, and conftest's socket canary is armed: no test here
can reach seats.aero.

Every refusal path is asserted to exit 1 with its sentence and to leave the
output directory EMPTY; every written file passes the shape check the map's
data test applies to the committed file.
"""
import json
import re
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from rich.console import Console

from src import map_tools, regions
from src.seats_client import SEATS_AERO_SOURCES, SeatsClient

FLAG_KEY = "flag_key_for_map_tools_0123456789"
N_SOURCES = len(SEATS_AERO_SOURCES)


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def route(o, d, oreg="North America", dreg="Europe"):
    return {"ID": f"{o}{d}", "OriginAirport": o, "OriginRegion": oreg,
            "DestinationAirport": d, "DestinationRegion": dreg, "NumDaysOut": 300,
            "Distance": 5000, "Source": "x"}


def routes_for(source: str, n_routes: int = 25):
    """n routes SFO<->LHR plus one each to a long-tail airport per source."""
    rows = []
    for i in range(n_routes):
        rows.append(route("SFO", "LHR") if i % 2 == 0 else route("LHR", "SFO", "Europe", "North America"))
    rows.append(route("SFO", "MRY"))          # searchable, but only this source
    rows.append(route("LHR", "XYZ"))          # not in airports.csv
    rows.append(route("SFO", "LON"))          # a metro code: no coordinates
    return rows


class Stub:
    """`requests.get`, keyed by the source in `params`. `answers` maps a source
    to a callable(url, kwargs) -> response, or to a plain routes list."""

    def __init__(self, answers=None, default=None):
        self.calls = []
        self.answers = answers or {}
        self.default = default if default is not None else routes_for

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        source = kwargs["params"]["source"]
        ans = self.answers.get(source)
        if callable(ans):
            return ans(url, kwargs)
        rows = ans if ans is not None else self.default(source)
        return ok(rows)


def ok(payload, status=200, text=None):
    r = MagicMock()
    r.status_code = status
    body = json.dumps(payload) if text is None else text
    r.text = body
    if text is None:
        r.json.return_value = payload
    else:
        def _json():
            return json.loads(text)
        r.json.side_effect = _json
    return r


def http(status):
    return lambda url, kw: ok({"error": "x"}, status=status)


def raises(exc):
    def _raise(url, kw):
        raise exc
    return _raise


def run(argv, *, stub=None, answer="y", read=None):
    buf = StringIO()
    stub = stub or Stub()
    code = map_tools.main(argv, read=read or (lambda prompt: answer),
                          console=Console(file=buf, width=190), get=stub)
    return code, " ".join(buf.getvalue().split()), stub


def capture_args(tmp_path, *extra):
    return ["capture-hubs", "--out", str(tmp_path / "out" / "hubs.json"),
            "--api-key", FLAG_KEY, *extra]


def written(tmp_path):
    return json.loads((tmp_path / "out" / "hubs.json").read_text(encoding="utf-8"))


def nothing_written(tmp_path):
    out = tmp_path / "out"
    return not out.exists() or list(out.iterdir()) == []


# ------------------------------------------------------------ the happy path


def test_every_source_answering_writes_the_file_with_its_invariants(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 0, out
    assert len(stub.calls) == N_SOURCES
    for url, kw in stub.calls:
        assert url == f"{SeatsClient.BASE_URL}/routes"
        assert kw["headers"]["Partner-Authorization"] == FLAG_KEY
        assert kw["timeout"] == 30
    doc = written(tmp_path)
    assert map_tools.hubs_file_problems(doc) == []
    meta = doc["_meta"]
    assert meta["captured_by"] == "src.map_tools capture-hubs"
    assert meta["endpoint"] == "/partnerapi/routes"
    assert meta["sources_asked"] == list(SEATS_AERO_SOURCES)
    assert meta["sources_ok"] == list(SEATS_AERO_SOURCES)
    assert meta["sources_failed"] == {} and meta["sources_incomplete"] == []
    assert meta["thresholds"] == {"min_sources": 3, "min_routes": 20}
    assert meta["routes_seen"] == N_SOURCES * 28
    assert meta["engine_airports_sha256"] == map_tools.airports_csv_sha256()
    assert meta["synthetic"] is False and meta["key_redacted"] is True
    assert "note" not in meta
    codes = [h["iata"] for h in doc["hubs"]]
    # SFO and LHR meet 3 sources / 20 routes; XYZ and LON do (one route per
    # source, 28 sources) too - MRY (one route per source) does as well.
    assert codes == sorted(codes)
    by = {h["iata"]: h for h in doc["hubs"]}
    assert by["SFO"]["routes"] == N_SOURCES * 27 and by["SFO"]["searchable"] is True
    assert by["SFO"]["sources"] == sorted(SEATS_AERO_SOURCES)
    assert by["SFO"]["lat"] is not None and by["SFO"]["city"] == "San Francisco"
    assert by["SFO"]["regions"] == ["NA"]   # mapped through regions.SEATS_AERO_REGIONS
    assert by["XYZ"]["searchable"] is False
    assert by["LON"]["lat"] is None and by["LON"]["lon"] is None
    assert FLAG_KEY not in json.dumps(doc)
    assert f"wrote {tmp_path / 'out' / 'hubs.json'}" in out
    assert "every source answered" in out


def test_the_console_states_the_call_count_and_asks_first(tmp_path):
    code, out, stub = run(capture_args(tmp_path))
    assert code == 0
    assert (f"This will make at most {N_SOURCES} Seats.aero API call(s): one GET "
            f"/partnerapi/routes per source ({N_SOURCES} sources). This process has spent 0 of "
            f"1,000; Seats.aero also counts your other runs today, which this tool cannot see.") in out
    assert "Seats.aero key: flag" in out and FLAG_KEY not in out
    assert f"This process has spent {N_SOURCES} of 1,000 today." in out
    assert "hubs at each threshold pair" in out
    assert re.search(r"\d+ hubs \(\d+ searchable, \d+ without coordinates, \d+ not in data/airports.csv\)", out)
    assert "XYZ,," in out   # the candidate line for the unsearchable airport


def test_calls_are_counted_on_the_shared_budget(tmp_path):
    run(capture_args(tmp_path, "--yes"))
    assert SeatsClient._budget_remaining() == SeatsClient.DAILY_CALL_CAP - N_SOURCES


def test_sources_limits_the_calls(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan, united,virginatlantic"))
    assert code == 0
    assert [kw["params"]["source"] for _, kw in stub.calls] == ["aeroplan", "united", "virginatlantic"]
    assert "at most 3 Seats.aero API call(s)" in out
    assert written(tmp_path)["_meta"]["sources_asked"] == ["aeroplan", "united", "virginatlantic"]


def test_raw_dir_holds_the_verbatim_bodies(tmp_path):
    raw = tmp_path / "raw"
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "united", "--min-sources", "1",
                                       "--raw-dir", str(raw)))
    assert code == 0
    assert (raw / "routes_united.json").read_text(encoding="utf-8") == json.dumps(routes_for("united"))


def test_a_list_body_and_a_data_envelope_both_read(tmp_path):
    stub = Stub(answers={"united": lambda u, k: ok({"data": routes_for("united")})})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 0
    assert "united: ok (28 routes)" in out


# ---------------------------------------------------------------- refusals


def test_declined_makes_no_call_and_writes_nothing(tmp_path):
    code, out, stub = run(capture_args(tmp_path), answer="n")
    assert code == 1
    assert "declined. No call was made and nothing was written." in out
    assert stub.calls == [] and nothing_written(tmp_path)


def test_a_closed_stdin_is_not_a_yes(tmp_path):
    def eof(prompt):
        raise EOFError
    code, out, stub = run(capture_args(tmp_path), read=eof)
    assert code == 1
    assert ("no answer at the prompt (stdin closed or interrupted). No call was made and "
            "nothing was written. Pass --yes to run without the question.") in out
    assert stub.calls == [] and nothing_written(tmp_path)


def test_no_key_is_the_resolution_error_verbatim(tmp_path, monkeypatch):
    monkeypatch.delenv("SEATS_AERO_KEY", raising=False)
    code, out, stub = run(["capture-hubs", "--yes", "--out", str(tmp_path / "out" / "hubs.json")])
    assert code == 1
    assert "No Seats.aero API key found." in out
    assert stub.calls == [] and nothing_written(tmp_path)


def test_a_bogus_source_is_a_usage_refusal(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "united,bogus"))
    assert code == 1
    assert "--sources names 'bogus'" in out and "No call was made and nothing was written." in out
    assert stub.calls == [] and nothing_written(tmp_path)


def test_every_source_failing_writes_nothing(tmp_path):
    stub = Stub(default=lambda s: None, answers={s: http(500) for s in SEATS_AERO_SOURCES})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 1
    assert f"every source failed ({N_SOURCES} of {N_SOURCES}); nothing was written." in out
    assert nothing_written(tmp_path)


def test_thresholds_nobody_meets_write_nothing_and_show_the_histogram(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-routes", "100000"))
    assert code == 1
    assert ("0 airports met the thresholds (--min-sources 3, --min-routes 100000); nothing was "
            "written. The histogram above shows what lower thresholds would give.") in out
    assert "hubs at each threshold pair" in out
    assert nothing_written(tmp_path)


def test_the_key_echoed_in_a_body_is_refused(tmp_path):
    rows = routes_for("united") + [route("SFO", "LHR") | {"note": FLAG_KEY}]
    stub = Stub(answers={"united": lambda u, k: ok(rows)})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 1
    assert "contains the Seats.aero key this run resolved" in out and "Nothing was written." in out
    assert FLAG_KEY not in out
    assert nothing_written(tmp_path)


def test_the_key_in_a_hub_field_is_refused_before_the_write(tmp_path, monkeypatch):
    """The second layer: the assembled JSON is grepped for the key even when
    no body carried it verbatim (here: the key arrives as an airport name)."""
    monkeypatch.setattr(map_tools, "load_coordinates",
                        lambda: {"SFO": {"name": FLAG_KEY, "city": "", "country": "", "lat": "1", "lon": "2"}})
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1
    assert "REFUSING TO WRITE" in out and "Nothing was written." in out
    assert nothing_written(tmp_path)


@pytest.mark.parametrize("flag", [["--min-sources", "0"], ["--min-routes", "-1"]])
def test_thresholds_below_one_are_refused(tmp_path, flag):
    code, out, stub = run(capture_args(tmp_path, "--yes", *flag))
    assert code == 1 and stub.calls == [] and nothing_written(tmp_path)


def test_no_subcommand_prints_help_and_exits_1():
    code, out, _ = run([])
    assert code == 1 and "capture-hubs" in out


# --------------------------------------------------------------------- gaps


def test_one_failing_source_is_exit_5_and_listed(tmp_path):
    stub = Stub(answers={"smiles": http(500), "azul": lambda u, k: ok("a string"),
                         "delta": raises(requests.Timeout()),
                         "qantas": raises(requests.ConnectionError())})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 5
    meta = written(tmp_path)["_meta"]
    assert meta["sources_failed"] == {"smiles": "HTTP 500", "azul": "unreadable: top level is a str",
                                      "delta": "Timeout", "qantas": "ConnectionError"}
    assert "smiles" not in meta["sources_ok"] and "aeroplan" in meta["sources_ok"]
    assert "WRITTEN WITH GAPS" in out


def test_a_429_stops_the_loop_and_the_rest_are_not_asked(tmp_path):
    third = list(SEATS_AERO_SOURCES)[2]
    stub = Stub(answers={third: http(429)})
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "2"), stub=stub)
    assert code == 5
    assert len(stub.calls) == 3
    meta = written(tmp_path)["_meta"]
    assert meta["sources_failed"][third] == "HTTP 429"
    not_asked = [s for s, why in meta["sources_failed"].items() if why == "not asked: rate limited"]
    assert len(not_asked) == N_SOURCES - 3
    assert len(meta["sources_ok"]) == 2


def test_a_cursor_is_recorded_not_followed_and_its_routes_still_count(tmp_path):
    stub = Stub(answers={"united": lambda u, k: ok({"data": routes_for("united"), "hasMore": True,
                                                    "cursor": "abc"})})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 5
    assert len(stub.calls) == N_SOURCES
    doc = written(tmp_path)
    assert doc["_meta"]["sources_incomplete"] == ["united"]
    assert "united" in {h["iata"]: h for h in doc["hubs"]}["SFO"]["sources"]


def test_a_bad_airport_code_is_dropped_and_counted(tmp_path):
    rows = routes_for("united") + [route("<b>", "sfo"), {"OriginAirport": 12, "DestinationAirport": None}]
    stub = Stub(answers={"united": lambda u, k: ok(rows)})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 0
    assert "4 route side(s) dropped" in out
    assert not any(h["iata"] in ("<b>", "sfo") for h in written(tmp_path)["hubs"])


def test_not_json_is_unreadable(tmp_path):
    stub = Stub(answers={"united": lambda u, k: ok(None, text="<html>")})
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=stub)
    assert code == 5
    assert written(tmp_path)["_meta"]["sources_failed"]["united"].startswith("unreadable: not JSON")


def test_more_than_400_hubs_warns(tmp_path):
    many = [route(f"{i:03d}", f"{chr(65 + i // 26)}{chr(65 + i % 26)}Z") for i in range(210)]
    stub = Stub(default=lambda s: many)
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-routes", "1"), stub=stub)
    assert code == 0
    assert "420 hubs is above the 400 the map was sized for" in out


def test_a_disk_error_is_a_refusal_with_nothing_left_beside_the_target(tmp_path, monkeypatch):
    import pathlib
    real = pathlib.Path.write_text

    def half(self, text, *a, **k):
        if self.name.endswith(".tmp"):
            with open(self, "w") as f:
                f.write(text[: len(text) // 2])
            raise OSError(28, "No space left on device")
        return real(self, text, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", half)
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1
    assert "could not be written (OSError" in out and "Nothing was written." in out
    assert nothing_written(tmp_path)


def test_out_naming_a_directory_is_a_refusal_and_leaves_no_tmp(tmp_path):
    (tmp_path / "out" / "hubs.json").mkdir(parents=True)
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1 and "Nothing was written." in out
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["hubs.json"]


def test_any_transport_exception_is_recorded_as_a_failed_source(tmp_path):
    def boom(url, kw):
        raise RuntimeError("socket exploded")
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(answers={"united": boom}))
    assert code == 5
    assert written(tmp_path)["_meta"]["sources_failed"]["united"] == "RuntimeError"


def test_an_impossible_threshold_is_refused_before_the_prompt(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--sources", "aeroplan,united", "--min-sources", "3"))
    assert code == 1 and stub.calls == [] and nothing_written(tmp_path)
    assert ("--min-sources 3 can never be met by the 2 source(s) asked; no airport could be a "
            "hub. No call was made and nothing was written.") in out


def test_the_default_threshold_follows_a_short_sources_list_and_says_so(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan,united"))
    assert code == 0 and len(stub.calls) == 2
    assert ("--min-sources not given: using 2, the number of sources asked (the default 3 "
            "could never be met). Recorded in _meta.thresholds.") in out
    assert written(tmp_path)["_meta"]["thresholds"] == {"min_sources": 2, "min_routes": 20}


def test_duplicate_sources_are_asked_once(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan,aeroplan,united,united,virginatlantic"))
    assert code == 0 and len(stub.calls) == 3 and "at most 3 " in out
    assert written(tmp_path)["_meta"]["sources_asked"] == ["aeroplan", "united", "virginatlantic"]


def test_a_gappy_run_does_not_replace_a_more_complete_capture_without_force(tmp_path):
    assert run(capture_args(tmp_path, "--yes"))[0] == 0
    good = written(tmp_path)
    SeatsClient.reset_call_budget()
    answers = {s: http(500) for s in SEATS_AERO_SOURCES if s != "aeroplan"}
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1"), stub=Stub(answers=answers))
    assert code == 5 and written(tmp_path) == good
    assert (f"NOT OVERWRITTEN: {tmp_path / 'out' / 'hubs.json'} holds a capture with {N_SOURCES} "
            f"source(s) ok, this run has 1. The existing file is kept and nothing was written. "
            f"Pass --force to replace it with this run.") in out
    SeatsClient.reset_call_budget()
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1", "--force"),
                          stub=Stub(answers=answers))
    assert code == 5 and written(tmp_path)["_meta"]["sources_ok"] == ["aeroplan"]


def test_a_complete_run_replaces_a_gappy_file_without_force(tmp_path):
    answers = {s: http(500) for s in SEATS_AERO_SOURCES if s != "aeroplan"}
    assert run(capture_args(tmp_path, "--yes", "--min-sources", "1"), stub=Stub(answers=answers))[0] == 5
    SeatsClient.reset_call_budget()
    assert run(capture_args(tmp_path, "--yes"))[0] == 0
    assert len(written(tmp_path)["_meta"]["sources_ok"]) == N_SOURCES


def test_regions_are_stored_as_codes_and_unknown_labels_are_counted_not_stored(tmp_path):
    hostile = "<img src=x onerror=alert(1)>" + "R" * 10000

    def rows(source):
        return [route("SFO", "LHR", oreg=hostile, dreg="Europe") for _ in range(25)]
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    by = {h["iata"]: h for h in written(tmp_path)["hubs"]}
    assert by["SFO"]["regions"] == [] and by["LHR"]["regions"] == ["EU"]
    assert f"{25 * N_SOURCES} region label(s) not in the known Seats.aero regions counted, not stored" in out


def test_rows_that_are_not_objects_are_named_separately(tmp_path):
    def rows(source):
        return [route("SFO", "LHR") for _ in range(25)] + ["x", 42]
    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    assert "0 route side(s) dropped for a code" in out
    assert f"{2 * N_SOURCES} row(s) that were not objects ignored" in out


# ---------------------------------------------------------- mark-searchable


def test_mark_searchable_flips_flags_against_a_changed_airports_csv(tmp_path, monkeypatch):
    run(capture_args(tmp_path, "--yes"))
    path = tmp_path / "out" / "hubs.json"
    before = written(tmp_path)
    assert {h["iata"]: h["searchable"] for h in before["hubs"]}["XYZ"] is False
    # A tmp copy of airports.csv that gains XYZ and loses MRY.
    csv_path = tmp_path / "airports.csv"
    lines = (map_tools.AIRPORTS_CSV).read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not l.startswith("MRY,")] + ["XYZ,Somewhere,US,NA"]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    regions._airports.cache_clear()
    monkeypatch.setattr(regions, "DATA_DIR", tmp_path)
    monkeypatch.setattr(map_tools, "AIRPORTS_CSV", csv_path)
    try:
        buf = StringIO()
        code = map_tools.main(["mark-searchable", "--file", str(path)],
                              console=Console(file=buf, width=190), get=Stub())
    finally:
        regions._airports.cache_clear()
    assert code == 0
    assert "2 hub(s) changed searchable; 0 calls made." in buf.getvalue()
    after = json.loads(path.read_text(encoding="utf-8"))
    flags = {h["iata"]: h["searchable"] for h in after["hubs"]}
    assert flags["XYZ"] is True and flags["MRY"] is False and flags["SFO"] is True
    assert after["_meta"]["engine_airports_sha256"] != before["_meta"]["engine_airports_sha256"]
    assert map_tools.hubs_file_problems(after) == []


def test_mark_searchable_refuses_a_file_it_did_not_write(tmp_path):
    path = tmp_path / "hubs.json"
    path.write_text('{"hubs": []}', encoding="utf-8")
    buf = StringIO()
    code = map_tools.main(["mark-searchable", "--file", str(path)], console=Console(file=buf, width=190))
    assert code == 1
    assert "is not a hubs file this tool wrote" in buf.getvalue()
    assert path.read_text(encoding="utf-8") == '{"hubs": []}'


def test_mark_searchable_on_the_empty_file_keeps_its_provenance_null(tmp_path):
    path = tmp_path / "hubs.json"
    path.write_text(map_tools.render_document(map_tools.empty_document()), encoding="utf-8")
    code = map_tools.main(["mark-searchable", "--file", str(path)], console=Console(file=StringIO()))
    assert code == 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["_meta"]["engine_airports_sha256"] is None
    assert map_tools.hubs_file_problems(doc) == []


# ------------------------------------------------------------- shape check


@pytest.mark.parametrize("edit,problem", [
    (lambda d: d["hubs"].append({"iata": "SFO"}), "lacks"),
    (lambda d: d["hubs"].append(dict(_h(), iata="sfo")), "not three upper-case"),
    (lambda d: d["hubs"].append(dict(_h(), routes=0)), "routes must be an integer >= 1"),
    (lambda d: d["hubs"].append(dict(_h(), sources=[])), "sources must be a non-empty list"),
    (lambda d: d["hubs"].append(dict(_h(), lat=None)), "both present or both null"),
    (lambda d: d["_meta"].pop("routes_seen"), "_meta.routes_seen is missing"),
    (lambda d: d["_meta"].__setitem__("key_redacted", False), "key_redacted must be true"),
    (lambda d: d["_meta"].pop("note"), "empty-state note"),
    (lambda d: (d["hubs"].extend([dict(_h(), iata="SFO"), dict(_h(), iata="LHR")]),
                d["_meta"].__setitem__("captured_at", "2026-09-20T00:00:00Z"),
                d["_meta"].pop("note")), "sorted by iata"),
])
def test_the_shape_check_names_each_way_a_file_can_be_wrong(edit, problem):
    doc = map_tools.empty_document()
    edit(doc)
    problems = map_tools.hubs_file_problems(doc)
    assert any(problem in p for p in problems), problems


def _h():
    return {"iata": "SFO", "name": "n", "city": "c", "country": "US", "lat": 1.0, "lon": 2.0,
            "routes": 3, "sources": ["united"], "regions": [], "searchable": True}


@pytest.mark.parametrize("doc", ["x", [], {"_meta": {}, "hubs": []}, {"_meta": {}, "hubs": {}}])
def test_the_shape_check_refuses_non_documents(doc):
    assert map_tools.hubs_file_problems(doc)
