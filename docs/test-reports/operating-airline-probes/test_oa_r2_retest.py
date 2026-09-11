"""
Re-test 2 (fixes f3f0869..1990866). Each round-1 fix checked for the whole
CLASS, not only the input its probe used, and the NEW behaviour the fixes added
attacked: the shared pagination reader, the <VERDICT> flow and the stricter YQ
loader, the new label-flip checks, cache reads after the cap / a 429, the search
429, the party skip, the one-page capture search, mixed-cabin drift, and the new
replay refusals.

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
import requests
from rich.console import Console

from conftest import (  # noqa: F401
    BASE, B4_VS, ROOT, TRIP_B, Stub, aid_for, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src import config, seats_trips, trips_tools, yq_inclusion
from src.models import MetalLookup, MetalStatus
from src.response_cache import ResponseCache, content_hash
from src.seats_client import SeatsClient
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row

PY = str(ROOT / ".venv" / "bin" / "python")
KEY = "PROBEKEYr2x9y8z7w6v5u4t3"
TODAY = date(2026, 9, 11)


# ===========================================================================
# 1. Class closure of the round-1 fixes
# ===========================================================================


@pytest.mark.parametrize("suffix", ["\r", "\t", " ", "\x0b", "\x0c", " ", " ", "\x00", "\n\n"])
@pytest.mark.parametrize("where", ["after", "before"])
def test_M4_class_no_whitespace_or_control_character_passes_the_id_gate(suffix, where):
    aid = B4_VS + suffix if where == "after" else suffix + B4_VS
    assert not seats_trips.valid_availability_id(aid)


@pytest.mark.parametrize("aid", ["B4virxxxxxxxxxxxxxxxxx１２", "B4virıxxxxxxxxxxxxxxxxxxxx"])
def test_M4_class_non_ascii_letters_and_digits_do_not_pass(aid):
    assert not seats_trips.valid_availability_id(aid)


def test_M4_class_the_transport_sends_nothing_for_a_carriage_return_id():
    sent = []
    with patch("src.seats_client.requests.get", side_effect=lambda *a, **k: sent.append(a)):
        with pytest.raises(Exception) as e:
            SeatsClient(api_key="k_probe_r2").trips_raw(B4_VS + "\r")
    assert getattr(e.value, "code", "") == "AVAILABILITY_ID_INVALID"
    assert sent == []


@pytest.mark.parametrize("extra", [
    {"hasMore": "True"}, {"hasMore": " YES "}, {"hasMore": 1}, {"has_more": "1"}, {"has_more": True},
    {"cursor": 0.5}, {"next_cursor": "x"}, {"skip": "3"}, {"skip": 3.0}, {"count": "2"}, {"count": 2.0},
])
def test_M5_class_every_spelling_the_shared_reader_knows_blocks_known(extra):
    payload = tp.payload([vs_itinerary(B4_VS)], **extra)
    parsed = seats_trips.parse_trips_payload(payload, B4_VS, ("LHR", "SFO"))
    facts = seats_trips.AwardFacts(B4_VS, "virginatlantic", "J", 60000, ("VS", "DL"), "LHR", "SFO")
    assert seats_trips.match_award(parsed, facts).reason_code == "INCOMPLETE"


@pytest.mark.parametrize("extra", [{"hasMore": False}, {"hasMore": "false"}, {"skip": 0}, {"count": 1},
                                   {"count": "abc"}, {"cursor": ""}, {"cursor": None}])
def test_M5_class_signals_that_say_nothing_more_do_not_block_known(extra):
    payload = tp.payload([vs_itinerary(B4_VS)], **extra)
    parsed = seats_trips.parse_trips_payload(payload, B4_VS, ("LHR", "SFO"))
    facts = seats_trips.AwardFacts(B4_VS, "virginatlantic", "J", 60000, ("VS", "DL"), "LHR", "SFO")
    assert seats_trips.match_award(parsed, facts).status is MetalStatus.KNOWN


# --- H1 / M2: the stricter loader --------------------------------------------------


def _record(root, body, name="2026-09-10-virginatlantic.md"):
    d = root / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return f"docs/yq-checks/{name}"


def _body(source="virginatlantic", verdict="includes_yq", title_source=None, extra=""):
    return (
        f"# yq-check record: {title_source or source}, 2026-09-10\n\n## Seats.aero\n\n"
        f"- program: Virgin Atlantic Flying Club (source {source})\n{extra}\n"
        f"## site\n\n- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00\n"
        f"- verdict (includes_yq / excludes_yq / inconclusive): {verdict}\n"
    )


def _load(root, ev, source="virginatlantic", verdict="includes_yq"):
    csv = root / "yq.csv"
    csv.write_text("source,verdict,verified_on,evidence,notes\n" f"{source},{verdict},2026-09-10,{ev},x\n")
    return yq_inclusion.load(csv, today=TODAY, root=root)


@pytest.mark.parametrize("said", ["includes_yq.", "includes-yq", "include_yq", "`includes_yq`", "**includes_yq**",
                                  "includes_yq excludes_yq", "includes_yq?", "yes", "IN", "excludes_yq",
                                  "inconclusive", "Inconclusive - site total differs"])
def test_H1_class_every_near_miss_verdict_line_is_refused(tmp_path, said):
    ev = _record(tmp_path, _body(verdict=said))
    with pytest.raises(YqInclusionError):
        _load(tmp_path, ev)


@pytest.mark.parametrize("said", ["includes_yq", "Includes_YQ", "  includes_yq   ", "INCLUDES_YQ\t"])
def test_H1_a_legitimately_filled_verdict_line_loads(tmp_path, said):
    ev = _record(tmp_path, _body(verdict=said))
    assert _load(tmp_path, ev)["virginatlantic"].includes


def test_H1_a_record_saved_with_crlf_line_endings_loads(tmp_path):
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True)
    (d / "2026-09-10-virginatlantic.md").write_bytes(_body().replace("\n", "\r\n").encode())
    assert _load(tmp_path, "docs/yq-checks/2026-09-10-virginatlantic.md")["virginatlantic"].includes


@pytest.mark.parametrize("variant", ["two_title_sources", "second_program_line", "notes_name_other"])
def test_M2_class_a_record_naming_another_source_never_backs_it(tmp_path, variant):
    if variant == "two_title_sources":
        body = _body(title_source="virginatlantic, flyingblue")
    elif variant == "second_program_line":
        body = _body(extra="- program: Air France-KLM Flying Blue (source flyingblue)\n")
    else:
        body = _body(extra="- note: same check as (source flyingblue)\n")
    ev = _record(tmp_path, body)
    with pytest.raises(YqInclusionError):
        _load(tmp_path, ev, source="flyingblue")


def _yq_stub(trips=None, row_=None):
    calls = []

    def side(url, **kw):
        calls.append(url)
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        body = {"data": [copy.deepcopy(row_ or vs_row())]} if url.endswith("/search") else (
            trips or tp.payload([tp.vs_direct()]))
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    side.calls = calls
    return side


def test_the_whole_verdict_flow_a_record_the_tool_wrote_filled_by_hand_loads(tmp_path, monkeypatch):
    """
    yq-check -> fill every blank FIELD -> the printed row with <VERDICT> replaced
    -> load. NEW finding: the record's own intro prose ("fill every ____ from the
    program's own site") contains the blank marker, so a record whose every field
    is filled is still refused as "still has ____ blanks". The Coder's test
    `test_the_record_it_writes_is_refused_until_the_blanks_are_filled` masks this
    by replacing EVERY "____" in the file, the prose included.
    """
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    rec_dir = tmp_path / "docs" / "yq-checks"
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=_yq_stub()):
        code = trips_tools.main(
            ["yq-check", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27", "--source",
             "virginatlantic", "--cabin", "J", "--out-dir", str(tmp_path / "real"), "--record-dir", str(rec_dir),
             "--api-key", KEY, "--yes"],
            read=lambda p: "y", console=Console(file=buf, width=400), today=TODAY)
    out = buf.getvalue()
    template = [l.strip() for l in out.splitlines() if l.strip().startswith("virginatlantic,<VERDICT>,")][0]
    rec = next(rec_dir.glob("*.md"))
    # Fill the five FIELDS the site half asks for - exactly what the record says
    # to do - and nothing else.
    fills = {
        "- date checked: ____": "- date checked: 2026-09-12",
        "- flight(s) shown: ____": "- flight(s) shown: VS19 LHR-SFO 11:00",
        "- taxes, fees and carrier-imposed charges for ONE adult: ____":
            "- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00",
        "- separate carrier-imposed charge line (if any): ____":
            "- separate carrier-imposed charge line (if any): none shown",
        "- verdict (includes_yq / excludes_yq / inconclusive): ____":
            "- verdict (includes_yq / excludes_yq / inconclusive): includes_yq",
    }
    text = rec.read_text()
    for blank, filled in fills.items():
        assert blank in text, blank
        text = text.replace(blank, filled)
    rec.write_text(text)
    csv = tmp_path / "yq.csv"
    csv.write_text("source,verdict,verified_on,evidence,notes\n" + template.replace("<VERDICT>", "includes_yq") + "\n")
    assert yq_inclusion.load(csv, today=TODAY, root=tmp_path)["virginatlantic"].includes


# ===========================================================================
# 2. New behaviour
# ===========================================================================

GRID = [
    ({}, None), ({"hasMore": True}, None), ({"hasMore": True, "cursor": "c"}, None),
    ({"hasMore": "yes", "skip": "500"}, {"skip": "0"}), ({"hasMore": 1, "skip": 500}, {"skip": "500"}),
    ({"hasMore": "1", "skip": "1000"}, {"skip": "500"}), ({"has_more": True, "next_cursor": 7}, None),
    ({"hasMore": False, "cursor": "c"}, None), ({"hasMore": None, "cursor": "c"}, None),
    ({"hasMore": "maybe", "skip": 3}, None), ({"skip": "abc", "hasMore": True}, None),
    ({"hasMore": True, "skip": 2.5}, {"skip": "1"}), ({"cursor": 0, "hasMore": True}, None),
    ({"hasMore": "TRUE", "count": 900}, None),
]

GRID_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from src.seats_client import SeatsClient
grid = json.loads(sys.argv[2])
out = []
for payload, sent in grid:
    try:
        out.append(list(SeatsClient._next_page_params(payload, sent)))
    except Exception as e:
        out.append(["RAISED", type(e).__name__])
print(json.dumps(out, sort_keys=True, default=str))
'''


def test_the_search_pagination_reader_answers_exactly_as_master_did(tmp_path):
    """`_next_page_params` now reads through `pagination_signals`: same answers as a17497d on a grid."""
    data = subprocess.run(["git", "archive", "a17497d", "src", "data"], cwd=ROOT, capture_output=True,
                          check=True).stdout
    master = tmp_path / "master"
    master.mkdir()
    tarfile.open(fileobj=io.BytesIO(data)).extractall(master)
    script = tmp_path / "grid.py"
    script.write_text(GRID_SCRIPT)
    env = {k: v for k, v in os.environ.items() if k != "SEATS_AERO_KEY"}
    env["PYTHONPATH"] = str(ROOT / "tests" / "_child_guard")
    res = {}
    for name, root in (("master", master), ("branch", ROOT)):
        r = subprocess.run([PY, str(script), str(root), json.dumps(GRID)], cwd=root, env=env,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-800:]
        res[name] = r.stdout
    assert res["branch"] == res["master"]


# --- replay: a legitimate corpus -------------------------------------------------

B3_FB = aid_for("B3", "flb")


def _two_lookup_rows(o, d, iso):
    if (o, d) == ("AMS", "LHR"):
        return [row(source="flyingblue", cost="30000", taxes=9000, currency="EUR", airlines="AF, KL",
                    origin=o, dest=d, iso=iso, rid=B3_FB)]
    if (o, d) == ("LHR", "SFO"):
        return [row(rid=B4_VS, iso=iso)]
    return None


def _corpus(tmp_path, trips):
    snap = tmp_path / "corpus"
    cache = ResponseCache(cache_dir=tmp_path / "cache", snapshot_dir=snap, ttl_seconds=0)
    results, *_ = evaluate(Stub(rows_for=_two_lookup_rows, trips=trips), cache=cache)
    return snap, results


class _NoNet:
    calls = 0

    def __call__(self, *a, **k):
        _NoNet.calls += 1
        raise AssertionError("replay touched requests.get")


def _replay(snap, capsys, monkeypatch):
    code, out, fl, _ = run_cli(BASE + ["--from-snapshot", str(snap / "MANIFEST.md")], capsys, monkeypatch,
                               stub=_NoNet(), key=None)
    return code, fl


def test_two_lookups_that_returned_identical_bytes_still_replay(tmp_path, capsys, monkeypatch):
    """
    NEW (from the L13 fix). The archive deduplicates snapshots by CONTENT, across
    requests: when two different availability ids get byte-identical responses
    (two awards on one route whose itinerary lists are both empty), the second
    manifest row points at the FIRST id's file. The new id check then reads that
    file's `_meta.availability_id`, finds the other id, and refuses the WHOLE
    replay as "trips_snapshot_is_another_lookup" - for a corpus the tool wrote
    itself from one honest live run.
    """
    empty = tp.payload([])
    snap, live = _corpus(tmp_path, {B3_FB: empty, B4_VS: empty})
    assert live["B4"].best_points.metal.reason_code == "EMPTY_DATA"
    rows = [l for l in (snap / "trips_endpoint" / "MANIFEST.md").read_text().splitlines() if "| trips:" in l]
    assert len(rows) == 2 and "(re-fetch, identical)" in rows[1], "precondition: the archive deduplicated"
    code, fl = _replay(snap, capsys, monkeypatch)
    assert code != 1, fl[:900]
    assert "EMPTY itinerary list for availability " + B4_VS in fl


def test_one_id_recorded_for_two_legs_with_the_same_bytes_is_not_a_conflict(tmp_path):
    """
    "Same bytes recorded twice is not a conflict" (fix report). A twin flight leg
    cannot be built into a replayable corpus (the SEARCH manifest refuses a leg
    whose search was a cache hit - pre-existing, outside this feature), so the
    trips side is checked directly: the B4 row copied under B3, same file.
    """
    from src import snapshot_replay

    snap, _ = _corpus(tmp_path, {B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    man = snap / "trips_endpoint" / "MANIFEST.md"
    text = man.read_text()
    line = [l for l in text.splitlines() if "| trips:" + B4_VS in l][0]
    twin = line.replace("| B4 |", "| B3 |", 1).replace(line.split("|")[1], " 2026-12-31T23:59:59Z ", 1)
    man.write_text(text + twin + "\n")
    out = snapshot_replay.load_trips_replay_set(snap, "trip_b_europe", leg_ids=["B1", "B2", "B3", "B4"])
    assert [p.kind for p in out.problems] == [], [p.render() for p in out.problems]


def test_an_old_corpus_with_no_trips_folder_still_replays_after_the_new_checks(tmp_path, capsys, monkeypatch):
    snap, _ = _corpus(tmp_path, {B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    import shutil
    shutil.rmtree(snap / "trips_endpoint")
    code, fl = _replay(snap, capsys, monkeypatch)
    assert code != 1 and "operating airline: NOT RECORDED" in fl


# --- capture: the one-page search --------------------------------------------------


def test_a_refused_capture_does_not_leave_a_truncated_search_for_the_next_trip_run(tmp_path, capsys, monkeypatch):
    """
    NEW (from the L17 fix). `capture` sets MAX_PAGES = 1 and refuses a search that
    says there is more - but `search_raw` has already CACHED that one-page,
    INCOMPLETE result in the runtime cache (only a budget failure is kept out),
    under the same key a trip run uses for that leg (origin, destination, one
    day). The next trip run inside the TTL is served page one only, and never
    fetches the pages it would otherwise have followed.
    """
    row_ = vs_row()
    page1 = {"data": [row_], "hasMore": True, "cursor": "p2"}
    page2 = {"data": [], "hasMore": False}

    def search_side(url, **kw):
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        params = kw.get("params") or {}
        if url.endswith("/search"):
            if (params.get("origin_airport"), params.get("destination_airport")) == ("LHR", "SFO"):
                body = page2 if params.get("cursor") else page1
            else:
                from conftest import aeroplan_row, ROUTES
                o, d = params["origin_airport"], params["destination_airport"]
                body = {"data": [aeroplan_row(o, d, ROUTES[(o, d)][1])]}
        else:
            body = tp.payload([tp.vs_direct()])
        r.json.return_value = body
        r.text = json.dumps(body)
        side.log.append((url, dict(params)))
        return r

    side = search_side
    side.log = []
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=side):
        code = trips_tools.main(
            ["capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27", "--source",
             "virginatlantic", "--out-dir", str(tmp_path / "real"), "--api-key", KEY, "--yes"],
            read=lambda p: "y", console=Console(file=buf, width=250))
    assert code == 1, "precondition: the paginating search was refused"
    side.log.clear()
    code2, out, fl, _ = run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch, stub=side)
    lhr = [p for u, p in side.log if u.endswith("/search") and p.get("origin_airport") == "LHR"]
    open(os.environ.get("R2_DUMP", os.devnull), "w").write(out)
    assert lhr, "the trip run was served the capture's truncated one-page search from the cache"


# --- the label flip ---------------------------------------------------------------


def _cap(tmp_path, argv_extra, trips=None, row_=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=_yq_stub(trips=trips, row_=row_)):
        code = trips_tools.main(["capture", *argv_extra, "--out-dir", str(tmp_path / "real"), "--api-key", KEY,
                                 "--yes"], read=lambda p: "y", console=Console(file=buf, width=250))
    return code, " ".join(buf.getvalue().split())


ROUTE_ARGS = ["--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27", "--source", "virginatlantic"]


@pytest.mark.parametrize("rewrite", ["crlf", "trailing_newlines", "pretty", "compact", "key_order"])
def test_a_genuine_capture_still_flips_after_harmless_byte_changes_to_its_raw_body(tmp_path, rewrite):
    code, out = _cap(tmp_path, ROUTE_ARGS)
    assert code == 0
    path = next((tmp_path / "real").glob("*.json"))
    raw = path.with_suffix(".raw.txt")
    page = json.loads(raw.read_text())
    text = {
        "crlf": json.dumps(page, indent=2).replace("\n", "\r\n"),
        "trailing_newlines": raw.read_text() + "\n\n\n",
        "pretty": json.dumps(page, indent=4),
        "compact": json.dumps(page, separators=(",", ":")),
        "key_order": json.dumps(page, sort_keys=True),
    }[rewrite]
    raw.write_bytes(text.encode())
    assert seats_trips.schema_verification_problems(path.name, tmp_path / "real") == []


def test_capture_never_says_clean_and_set_the_constant_for_a_capture_the_flip_check_refuses(tmp_path):
    """
    `capture --availability-id` with no local row: the route is inferred, and the
    flip check now refuses it (fix 8). The tool still ends with exit 0 and
    "CAPTURED CLEAN. To flip the UNVERIFIED label, commit both files and set
    TRIPS_SCHEMA_VERIFIED_BY = ..." - advice the label test will reject.
    """
    code, out = _cap(tmp_path, ["--availability-id", B4_ID])
    path = next((tmp_path / "real").glob("*.json"))
    problems = seats_trips.schema_verification_problems(path.name, tmp_path / "real")
    assert problems, "precondition: the flip check refuses this capture"
    assert not (code == 0 and "set TRIPS_SCHEMA_VERIFIED_BY" in out), (code, out[-400:])


def test_the_committed_synthetic_page_cannot_verify_the_parser_under_any_wrapper(tmp_path):
    """
    The re-wrap needs three more fields now (a raw.txt equal to the page, an
    availability_row with the page's id, route_inferred False) - and then the
    published example still verifies the parser against itself. The one cheap,
    exact closure (refuse a page whose content hash is a committed synthetic
    page's) is not there.
    """
    syn = json.loads((ROOT / "tests/fixtures/seats_aero/trips_endpoint/synthetic/openapi_example.json").read_text())
    page = syn["pages"][0]
    aid = page["data"][0]["AvailabilityID"]
    segs = page["data"][0]["AvailabilitySegments"]
    o, d = segs[0]["OriginAirport"], segs[-1]["DestinationAirport"]
    real = tmp_path / "real"
    real.mkdir()
    env = {"_meta": {"captured_by": "src.trips_tools capture", "synthetic": False, "key_redacted": True,
                     "availability_id": aid, "content_hash": content_hash([page]), "route": f"{o}->{d}",
                     "route_inferred_from_itineraries": False,
                     "availability_row": {"ID": aid, "Route": {"OriginAirport": o, "DestinationAirport": d,
                                                               "Source": page["data"][0]["Source"]}}},
           "pages": [page]}
    (real / "forged.json").write_text(json.dumps(env))
    (real / "forged.raw.txt").write_text(json.dumps(page))
    assert seats_trips.schema_verification_problems("forged.json", real) != []


# --- cache after the cap / a 429 ------------------------------------------------------


def test_after_a_search_429_a_cached_lookup_is_read_and_nothing_is_sent(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    evaluate(Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])}), cache=cache)
    for p in (tmp_path / "c").glob("*.json"):
        p.unlink()
    base = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})

    def side(url, **kw):
        if url.endswith("/search") and (kw.get("params") or {}).get("origin_airport") == "SFO":
            base.calls.append((url, {}, {}))
            r = MagicMock()
            r.status_code = 429
            r.raise_for_status.side_effect = requests.HTTPError("429")
            return r
        return base(url, **kw)

    results, totals, text, opts, fx = evaluate(side, cache=cache)
    assert base.trips_calls == []
    assert results["B4"].best_points.metal.status is MetalStatus.KNOWN
    assert results["B4"].best_points.metal.served_from_cache


def test_refresh_after_the_cap_does_not_read_the_cache_it_was_told_to_bypass(tmp_path):
    from test_oa_c_budget import TRIPS3, three_rows
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    evaluate(Stub(rows_for=vs_b4_rows(), trips=TRIPS3), cache=cache)
    stub = Stub(rows_for=three_rows, trips=TRIPS3)
    results, *_ = evaluate(stub, cache=cache, trips_cap=1, refresh=True)
    assert len(stub.trips_calls) == 1
    assert results["B4"].best_points.metal.reason_code == "CAP_REACHED"


# --- L10 / L11 / L21 --------------------------------------------------------------


def _party(fx):
    for leg in fx.legs:
        if leg.id == "B4":
            leg.travelers = 2


def test_party_leg_all_mode_is_looked_up_and_printed_auto_is_named_and_free():
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, totals, text, opts, fx = evaluate(stub, fixture_mutator=_party)
    b4 = next(l for l in fx.legs if l.id == "B4")
    assert stub.trips_calls == []
    assert b4.points_candidates[0].metal.reason_code == "NOT_NEEDED_PARTY"
    assert "B4" not in totals["legs_metal_lookup_missing_ids"]
    stub2 = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, totals, text, opts, fx = evaluate(stub2, fixture_mutator=_party, trips_mode="all")
    assert [u for u in stub2.trips_calls if B4_VS in u]
    assert "operating airline: VS by flight number" in flat(text)


def test_a_mixed_cabin_itinerary_blocks_the_flip_but_not_a_live_known():
    payload = tp.payload([vs_itinerary(B4_VS), vs_itinerary(B4_VS, "DL41", trip_id="t2", mixed=30)])
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: payload})
    results, *_ = evaluate(stub)
    m = results["B4"].best_points.metal
    assert m.status is MetalStatus.KNOWN and m.carriers == ("VS",) and m.excluded_mixed == 1


@pytest.mark.parametrize("status,code", [
    (MetalStatus.NOT_LOOKED_UP, "CAP_REACHED"), (MetalStatus.NOT_LOOKED_UP, "TRIPS_OFF"),
    (MetalStatus.NOT_LOOKED_UP, "RATE_LIMITED_EARLIER"), (MetalStatus.NOT_RECORDED, "NO_TRIPS_SNAPSHOT"),
])
def test_a_line_about_a_lookup_that_was_never_made_does_not_speak_of_this_lookup(status, code):
    """
    Fix 6's single-carrier wording ("This lookup established nothing further;
    the award's own carrier list names one carrier") is used for EVERY non-KNOWN
    status with a one-carrier row - including NOT LOOKED UP and NOT RECORDED,
    where no lookup was made on this run.
    """
    m = MetalLookup(status=status, reason_code=code, detail="x", possible_carriers=("VS",),
                    row_carriers=("VS",), on_replay=(status is MetalStatus.NOT_RECORDED))
    assert "This lookup established" not in m.render(), m.render()


def test_M3_class_every_line_that_names_trips_metal_carries_the_parser_label():
    """
    Fix 2 labelled the two places round 1 named (alternatives, the yq-check
    block). Two more lines still name the looked-up metal with no parser label:
    the band note ("modelled carrier surcharge for VS metal ... NOT ADDED") and
    the surcharge note ("the itinerary lookup names VS by flight number").
    """
    import re
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, totals, text, opts, fx = evaluate(stub)
    label = seats_trips.PARSER_UNVERIFIED_LABEL
    lines = [l for l in re.split(r"\n(?=\s{2,}\S)", text) if re.search(r"VS metal|names VS by flight number", l)]
    assert lines, "precondition: lines naming the looked-up metal"
    bare = [" ".join(l.split())[:160] for l in lines if label not in " ".join(l.split())]
    assert bare == [], bare
