"""
G. python -m src.trips_tools capture / yq-check, stubbed. Every run points
--out-dir and --record-dir at tmp.
"""
import copy
import json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from conftest import B4_VS, ROOT, vs_itinerary  # noqa: F401
from src import config, seats_trips, trips_tools
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row

KEY = "PROBEKEYtools7a6b5c4d3e2f1"
TODAY = date(2026, 9, 11)


class Stub:
    def __init__(self, trips=None, row=None, pages=None, buf=None):
        self.calls = []
        self.trips = trips if trips is not None else tp.payload([tp.vs_direct()])
        self.row = row if row is not None else vs_row()
        self.pages = pages  # list of search bodies, served in order
        self.buf = buf
        self.out_at_call = []

    def __call__(self, url, **kw):
        self.out_at_call.append(self.buf.getvalue() if self.buf else "")
        self.calls.append(url)
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        if url.endswith("/search"):
            if self.pages:
                body = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]
            else:
                body = {"data": [copy.deepcopy(self.row)]}
        else:
            body = self.trips
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    @property
    def search_calls(self):
        return [c for c in self.calls if c.endswith("/search")]


def run(argv, stub=None, answer="y", read=None):
    buf = StringIO()
    stub = stub or Stub()
    stub.buf = buf
    with patch("src.seats_client.requests.get", side_effect=stub):
        code = trips_tools.main(argv, read=read or (lambda p: answer),
                                console=Console(file=buf, width=250), today=TODAY)
    return code, " ".join(buf.getvalue().split()), stub


def cap(tmp_path, *extra, key=True):
    return ["capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
            "--source", "virginatlantic", "--out-dir", str(tmp_path / "real"),
            *(["--api-key", KEY] if key else []), *extra]


def yq(tmp_path, *extra, source="virginatlantic", cabin="J", key=True):
    return ["yq-check", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
            "--source", source, "--cabin", cabin, "--out-dir", str(tmp_path / "real"),
            "--record-dir", str(tmp_path / "rec"), *(["--api-key", KEY] if key else []), *extra]


def test_every_request_comes_after_the_call_count_line(tmp_path):
    code, out, stub = run(cap(tmp_path))
    assert stub.calls
    for seen in stub.out_at_call:
        assert "This will make at most" in seen


@pytest.mark.parametrize("answer", ["n", "", "no", " N ", "yes please", "Y E S"])
def test_anything_but_yes_makes_no_call(tmp_path, answer):
    code, out, stub = run(cap(tmp_path), answer=answer)
    if answer.strip().lower() in ("y", "yes"):
        return
    assert code == 1 and stub.calls == []
    assert not (tmp_path / "real").exists()


def test_a_closed_stdin_at_the_prompt_is_a_clean_refusal(tmp_path):
    """No --yes and no terminal (piped or closed stdin): `input` raises EOFError."""
    def eof(prompt):
        raise EOFError

    code, out, stub = run(cap(tmp_path), read=eof)
    assert code == 1 and stub.calls == []


def test_the_promised_maximum_holds_when_the_search_paginates(tmp_path):
    """
    "This will make at most 2 Seats.aero API call(s): 1 search + 1 trips" -
    then `search_raw` follows pagination up to MAX_PAGES (25). A one-day search
    whose response says hasMore spends more calls than the user agreed to.
    """
    row = vs_row()
    pages = [{"data": [], "hasMore": True, "cursor": f"c{i}"} for i in range(4)] + [{"data": [row]}]
    code, out, stub = run(cap(tmp_path), stub=Stub(pages=pages))
    assert len(stub.calls) <= 2, f"{len(stub.calls)} calls after promising at most 2"


@pytest.mark.parametrize("how", ["env", "dotenv", "flag"])
def test_the_key_never_reaches_the_capture_or_its_raw_body(tmp_path, monkeypatch, how):
    argv = cap(tmp_path, key=(how == "flag"))
    if how == "env":
        monkeypatch.setenv("SEATS_AERO_KEY", KEY)
    elif how == "dotenv":
        f = tmp_path / "x.env"
        f.write_text(f"SEATS_AERO_KEY={KEY}\n")
        monkeypatch.setattr(config, "_ENV_PATH", f)
    code, out, stub = run(argv)
    assert code in (0, 5)
    files = list((tmp_path / "real").iterdir())
    assert any(p.suffix == ".json" for p in files) and any(p.name.endswith(".raw.txt") for p in files)
    for p in files:
        assert KEY not in p.read_text()
    assert KEY not in out


def test_a_body_that_reflects_the_key_writes_nothing(tmp_path):
    code, out, stub = run(cap(tmp_path), stub=Stub(trips=tp.payload([tp.vs_direct()], echo=KEY)))
    assert code == 1
    assert not (tmp_path / "real").exists() or not list((tmp_path / "real").iterdir())


@pytest.mark.parametrize("source", ["qatar", "turkish", "singapore"])
def test_yq_check_refuses_unreported_sources_before_any_call(tmp_path, source):
    code, out, stub = run(yq(tmp_path, source=source))
    assert code == 1 and stub.calls == []


@pytest.mark.parametrize("taxes", [0, "0", None])
def test_yq_check_refuses_a_zero_tax_figure_before_the_trips_call(tmp_path, taxes):
    row = vs_row()
    row["JTotalTaxes"] = taxes
    code, out, stub = run(yq(tmp_path), stub=Stub(row=row))
    assert code == 1
    assert not [c for c in stub.calls if "/trips/" in c]


@pytest.mark.parametrize("taxes", [-100, 500])
def test_yq_check_refuses_a_tax_figure_the_tool_itself_does_not_believe(tmp_path, taxes):
    """
    -100 is corrupt (negative); GBP 5.00 on a UK departure in J is below the
    UK APD the tool says must be inside it. Both are "not believed" in scoring,
    yet yq-check prints them as the figure to compare against the site and
    writes a record that could back a verdict.
    """
    row = vs_row()
    row["JTotalTaxes"] = taxes
    code, out, stub = run(yq(tmp_path), stub=Stub(row=row))
    assert code == 1, "an untrusted row figure was offered for the YQ comparison"


def test_yq_check_warns_inconclusive_on_dl_metal(tmp_path):
    code, out, stub = run(yq(tmp_path), stub=Stub(trips=tp.payload([tp.vs_direct("DL41")])))
    assert "likely INCONCLUSIVE" in out


def test_yq_check_record_has_blanks_and_the_loader_refuses_it(tmp_path):
    from src import yq_inclusion

    code, out, stub = run(yq(tmp_path))
    rec = next((tmp_path / "rec").glob("*.md"))
    assert "____" in rec.read_text()
    assert "yq-check record" in rec.read_text()


def test_usage_errors_exit_1_and_help_exits_0(tmp_path):
    assert run(["capture"])[0] == 1
    assert run(["bogus"])[0] == 1
    assert run([])[0] == 1
    assert run(["capture", "--availability-id", "../x", "--out-dir", str(tmp_path)])[0] == 1
    assert run(["--help"])[0] == 0


def test_api_supplied_airport_codes_cannot_steer_or_crash_the_capture_write(tmp_path):
    """
    In --availability-id mode with no local row, the capture's FILENAME is built
    from the itineraries' own OriginAirport/DestinationAirport (the inferred
    route), unsanitized - unlike the trips snapshot names, which are. A "/" in
    one of those API strings makes the write raise FileNotFoundError out of
    main() (a traceback, after the trips call was spent) instead of a refusal
    or a written file.
    """
    t = tp.trip([tp.segment("VS19", "LH/R", "SFO", 1, AvailabilityID=B4_ID)], availability_id=B4_ID)
    argv = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"), "--api-key", KEY, "--yes"]
    try:
        code, out, stub = run(argv, stub=Stub(trips=tp.payload([t])))
    except OSError as e:  # the defect: an unhandled write error
        pytest.fail(f"capture crashed writing its file: {type(e).__name__}: {e}")
    assert code in (0, 1, 5)
    for p in (tmp_path / "real").rglob("*"):
        assert p.parent == tmp_path / "real", p


def test_an_inferred_route_capture_cannot_flip_the_label(tmp_path):
    """
    ADMITTED gap, sized: `capture --availability-id` with no local row infers
    the route from the itineraries ("the route check is then not evidence").
    But the flip check `schema_verification_problems` does not look at
    `route_inferred_from_itineraries`, so that capture - with no availability
    row, no matcher run, no unit evidence - is enough to set
    TRIPS_SCHEMA_VERIFIED_BY.
    """
    argv = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"), "--api-key", KEY, "--yes"]
    code, out, stub = run(argv, stub=Stub(trips=tp.payload([tp.vs_direct()])))
    path = next((tmp_path / "real").glob("*.json"))
    assert json.loads(path.read_text())["_meta"]["route_inferred_from_itineraries"] is True
    assert seats_trips.schema_verification_problems(path.name, tmp_path / "real") != []


def test_a_forged_real_capture_from_the_synthetic_example_cannot_flip_the_label(tmp_path):
    """
    "Can the UNVERIFIED label be flipped by a synthetic file?" Only by
    rewriting its _meta - but then yes: the flip check never compares the
    `.raw.txt` sibling with the page it vouches for (an EMPTY .raw.txt passes),
    so the published example, re-wrapped, verifies the parser against itself.
    """
    from src.response_cache import content_hash

    syn = json.loads((ROOT / "tests/fixtures/seats_aero/trips_endpoint/synthetic/openapi_example.json").read_text())
    page = syn["pages"][0] if "pages" in syn else syn
    aid = page["data"][0]["AvailabilityID"]
    real = tmp_path / "real"
    real.mkdir()
    env = {"_meta": {"captured_by": "src.trips_tools capture", "synthetic": False, "key_redacted": True,
                     "availability_id": aid, "content_hash": content_hash([page]),
                     "route": None, "availability_row": None},
           "pages": [page]}
    first = page["data"][0]["AvailabilitySegments"]
    env["_meta"]["route"] = f"{first[0]['OriginAirport']}->{first[-1]['DestinationAirport']}"
    (real / "forged.json").write_text(json.dumps(env))
    (real / "forged.raw.txt").write_text("")
    assert seats_trips.schema_verification_problems("forged.json", real) != []


def test_the_yq_block_carrier_line_carries_the_unverified_label(tmp_path):
    """
    The block Tsuki compares against virginatlantic.com, and that is copied into
    the evidence record, says "flight-number carrier: VS" and lists the flights
    with no UNVERIFIED parser label (the drift report above it does carry it).
    """
    code, out, stub = run(yq(tmp_path))
    block = out.split("YQ CHECK - compare against the program's own site", 1)[1]
    carrier_line = block.split("flight-number carrier:", 1)[1][:200]
    assert "UNVERIFIED" in carrier_line, carrier_line


def test_the_cents_flip_rests_only_on_a_genuine_capture(tmp_path):
    """
    `totaltaxes_unit_problems("cents")` globs real/*.json and accepts ANY
    envelope whose recorded row and one itinerary share a TotalTaxes. It does not
    ask what `schema_verification_problems` asks - captured_by, a matching
    content hash, a .raw.txt sibling - so a hand-written file (synthetic: false,
    no provenance, wrong hash) is enough to make per-itinerary taxes move numbers.
    """
    real = tmp_path / "real"
    real.mkdir()
    row = vs_row()
    page = tp.payload([tp.vs_direct(taxes=row["JTotalTaxes"])])
    env = {"_meta": {"synthetic": False, "availability_id": B4_ID, "availability_row": row,
                     "content_hash": "not-a-hash"}, "pages": [page]}
    (real / "hand_written.json").write_text(json.dumps(env))
    assert seats_trips.totaltaxes_unit_problems("cents", real) != []
