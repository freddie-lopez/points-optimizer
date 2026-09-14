"""
B. The capture tool against a stubbed transport: every refusal path writes
nothing (no partial file, no .tmp), the announced call count is the spent
one, the key never prints, hostile route fields never become hubs or reach
the file unescaped, the prompt semantics match trips_tools.

Reuses the coder's Stub/ok helpers from tests/test_map_tools.py (a helper,
not a probe: nothing there is edited).
"""
import json
import pathlib
import sys
from io import StringIO

import pytest
from rich.console import Console

from conftest import ROOT, net_canary  # noqa: F401

sys.path.insert(0, str(ROOT))
from src import map_tools  # noqa: E402
from src.seats_client import SEATS_AERO_SOURCES, SeatsClient  # noqa: E402
from tests.test_map_tools import (FLAG_KEY, Stub, capture_args, nothing_written, ok,  # noqa: E402
                                  route, run, written)

N = len(SEATS_AERO_SOURCES)


@pytest.fixture(autouse=True)
def fresh_budget():
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def leftovers(tmp_path):
    out = tmp_path / "out"
    return sorted(p.name for p in out.iterdir()) if out.exists() else []


# ------------------------------------------------------- refusals write nothing


def test_B1_a_failed_disk_write_leaves_no_partial_file_and_says_so(tmp_path, monkeypatch):
    """ENOSPC half-way through the tmp write: the plan says every refusal is a
    named reason and nothing on disk."""
    real = pathlib.Path.write_text

    def half(self, text, *a, **k):
        if self.name.endswith(".tmp"):
            with open(self, "w") as f:
                f.write(text[: len(text) // 2])
            raise OSError(28, "No space left on device")
        return real(self, text, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", half)
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1, "an uncaught traceback is not exit 1 with a sentence"
    assert "nothing was written" in out.lower(), out[-300:]
    assert leftovers(tmp_path) == [], f"partial file left behind: {leftovers(tmp_path)}"


def test_B2_out_pointing_at_a_directory_is_a_refusal_not_a_traceback(tmp_path):
    (tmp_path / "out" / "hubs.json").mkdir(parents=True)
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 1
    assert "nothing was written" in out.lower(), out[-300:]
    assert leftovers(tmp_path) == ["hubs.json"], leftovers(tmp_path)


def test_B3_a_transport_exception_that_is_not_a_requests_error_is_recorded_not_a_crash(tmp_path):
    def boom(url, kw):
        raise RuntimeError("socket exploded")

    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(answers={"united": boom}))
    assert code in (1, 5), "an uncaught exception after calls were spent"
    if code == 5:
        assert "united" in written(tmp_path)["_meta"]["sources_failed"]


def test_B4_thresholds_that_cannot_be_met_by_the_sources_asked_spend_nothing(tmp_path):
    """--sources a,b with --min-sources 3 can never yield a hub: two calls are
    spent for a refusal the tool could have given before the prompt."""
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan,united",
                                       "--min-sources", "3"))
    assert code == 1 and nothing_written(tmp_path)
    assert len(stub.calls) == 0, f"{len(stub.calls)} calls spent on an impossible threshold"


def test_B5_duplicate_sources_are_asked_once(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes", "--sources", "aeroplan,aeroplan,united,united,virginatlantic"))
    assert len(stub.calls) == 3, f"{len(stub.calls)} calls for 3 distinct sources"
    assert "at most 3 " in out


def test_B6_an_existing_captured_file_is_not_overwritten_silently_by_a_run_with_gaps(tmp_path):
    """First a full capture; then a run where every source but one fails.
    The good file is replaced by a one-source file with exit 5 and no
    question asked."""
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert code == 0
    good = written(tmp_path)
    SeatsClient.reset_call_budget()
    answers = {s: (lambda url, kw: ok({"error": "x"}, status=500)) for s in SEATS_AERO_SOURCES if s != "aeroplan"}
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1"), stub=Stub(answers=answers))
    now = written(tmp_path)
    assert code == 5
    assert now == good or "overwrit" in out.lower() or "replac" in out.lower(), \
        "a good capture was silently replaced by a gappy one"


# ------------------------------------------------------------ call accounting


@pytest.mark.parametrize("extra,expected", [([], N), (["--sources", "aeroplan,united"], 2)])
def test_B7_the_announced_count_equals_the_calls_made(tmp_path, extra, expected):
    code, out, stub = run(capture_args(tmp_path, "--yes", *extra))
    assert f"at most {expected} Seats.aero API call(s)" in out
    assert len(stub.calls) == expected
    assert f"spent {expected} of 1,000" in out


def test_B8_a_429_on_source_three_stops_the_loop_at_three_calls(tmp_path):
    third = SEATS_AERO_SOURCES[2] if not isinstance(SEATS_AERO_SOURCES, dict) else list(SEATS_AERO_SOURCES)[2]
    code, out, stub = run(capture_args(tmp_path, "--yes", "--min-sources", "1"),
                          stub=Stub(answers={third: lambda url, kw: ok({"error": "x"}, status=429)}))
    assert len(stub.calls) == 3
    assert code == 5
    meta = written(tmp_path)["_meta"]
    assert sum(1 for v in meta["sources_failed"].values() if "rate limited" in v) == N - 3


def test_B9_an_exhausted_budget_makes_no_call_and_writes_nothing(tmp_path):
    for _ in range(SeatsClient.DAILY_CALL_CAP):
        SeatsClient._count_call()
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert len(stub.calls) == 0 and code == 1 and nothing_written(tmp_path)


# ----------------------------------------------------------------- the key


def test_B10_the_key_is_masked_in_the_banner_and_never_printed(tmp_path):
    code, out, stub = run(capture_args(tmp_path, "--yes"))
    assert FLAG_KEY not in out
    assert "flag…789" in out or "…" in out


def test_B11_the_key_in_a_body_is_refused_before_the_raw_dir_is_written(tmp_path):
    def leak(url, kw):
        rows = [route("SFO", "LHR") for _ in range(25)]
        rows[0]["ID"] = FLAG_KEY
        return ok(rows)

    code, out, stub = run(capture_args(tmp_path, "--yes", "--raw-dir", str(tmp_path / "raw")),
                          stub=Stub(answers={"united": leak}))
    assert code == 1 and nothing_written(tmp_path)
    raw = tmp_path / "raw"
    if raw.exists():
        for p in raw.iterdir():
            assert FLAG_KEY not in p.read_text(encoding="utf-8")


# --------------------------------------------------------- hostile fields


def test_B12_hostile_route_fields_never_become_hubs(tmp_path):
    def rows(source):
        base = [route("SFO", "LHR") for _ in range(25)]
        return base + [route("‮OFS", "LHR"), route("A" * 10000, "LHR"), route("sfo", "LHR"),
                       route("<b>", "LHR"), {"OriginAirport": 123, "DestinationAirport": None},
                       {"OriginAirport": ["SFO"], "DestinationAirport": {"a": 1}}, "row", 42, None]

    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    assert code == 0
    codes = [h["iata"] for h in written(tmp_path)["hubs"]]
    assert codes == ["LHR", "SFO"], codes


def test_B13_a_hostile_region_string_is_not_stored_verbatim(tmp_path):
    """OriginRegion is free text from the wire; it lands in hubs.json's
    `regions` with no length or character check and no test on the shape
    (the plan maps regions through regions.SEATS_AERO_REGIONS)."""
    hostile = "<img src=x onerror=alert(1)>" + "‮" + "R" * 10000

    def rows(source):
        return [route("SFO", "LHR", oreg=hostile) for _ in range(25)]

    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    doc = written(tmp_path)
    regions = doc["hubs"][0]["regions"] + doc["hubs"][1]["regions"]
    assert all(len(r) < 64 and "<" not in r for r in regions), \
        f"a 10 kB HTML string is stored as a region: {[r[:40] for r in regions]}"


def test_B14_the_dropped_count_line_names_non_object_rows_separately(tmp_path):
    def rows(source):
        return [route("SFO", "LHR") for _ in range(25)] + ["x", 42]

    code, out, stub = run(capture_args(tmp_path, "--yes"), stub=Stub(default=rows))
    # 2 non-object rows per source are reported as "route side(s) dropped for a
    # code that is not three upper-case letters or digits" - they had no code.
    assert f"{2 * N} route side(s) dropped for a code" not in out, \
        "rows that are not objects are counted as bad-code route sides"


# ------------------------------------------------------------- the prompt


@pytest.mark.parametrize("answer,calls", [("y", N), ("Y", N), (" yes ", N), ("n", 0), ("", 0),
                                          ("oui", 0), ("yes please", 0)])
def test_B15_only_y_or_yes_is_a_yes(tmp_path, answer, calls):
    code, out, stub = run(capture_args(tmp_path), answer=answer)
    assert len(stub.calls) == calls
    if calls == 0:
        assert code == 1 and "declined. No call was made and nothing was written." in out


def test_B16_eof_and_interrupt_at_the_prompt_are_refusals_with_the_sentence(tmp_path):
    for exc in (EOFError, KeyboardInterrupt):
        SeatsClient.reset_call_budget()

        def read(prompt, exc=exc):
            raise exc

        code, out, stub = run(capture_args(tmp_path), read=read)
        assert code == 1 and len(stub.calls) == 0
        assert ("no answer at the prompt (stdin closed or interrupted). No call was made and "
                "nothing was written. Pass --yes to run without the question.") in out


def test_B17_mark_searchable_on_the_committed_empty_file_is_a_byte_no_op(tmp_path):
    copy = tmp_path / "hubs.json"
    copy.write_bytes((ROOT / "data" / "hubs.json").read_bytes())
    buf = StringIO()
    assert map_tools.main(["mark-searchable", "--file", str(copy)], console=Console(file=buf, width=190)) == 0
    assert copy.read_bytes() == (ROOT / "data" / "hubs.json").read_bytes()


def test_B18_the_histogram_is_the_plans_grid():
    assert map_tools.HISTOGRAM_SOURCES == (1, 2, 3, 4, 5)
    assert map_tools.HISTOGRAM_ROUTES == (5, 10, 20, 50)
    assert (map_tools.DEFAULT_MIN_SOURCES, map_tools.DEFAULT_MIN_ROUTES) == (3, 20)


def test_B19_the_readme_documents_every_flag_the_parser_has():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    parser = map_tools.build_parser()
    sub = [a for a in parser._actions if hasattr(a, "choices") and a.choices][0]
    flags = set()
    for name, p in sub.choices.items():
        for a in p._actions:
            for s in a.option_strings:
                if s.startswith("--") and s != "--help":
                    flags.add(s)
    missing = sorted(f for f in flags if f not in readme)
    assert missing == [], f"flags the README never mentions: {missing}"
