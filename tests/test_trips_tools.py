"""
`python -m src.trips_tools capture` and `yq-check`, stubbed.

Every test points --out-dir and --record-dir at tmp, passes the key by FLAG
(so it is not in the environment and the file grep is a real test), and patches
`requests.get`. Nothing is written into the tree.
"""
import copy
import json
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src import config, response_cache, seats_trips, trips_tools
from src.seats_client import SeatsClient
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, vs_row
from tests._trips_label_state import unverified_constants  # noqa: F401 - pins the label constants

ROOT = Path(__file__).parent.parent
FLAG_KEY = "flag_key_for_trips_tools_0123456789"
TODAY = date(2026, 9, 11)


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


class Stub:
    """Search answers with B4's VS row; trips with `trips` (a payload)."""

    def __init__(self, trips=None, row=None, console_buf=None, raw_text=None):
        self.calls = []
        self.trips = trips if trips is not None else tp.payload([tp.vs_direct()])
        self.row = row if row is not None else vs_row()
        self.console_buf = console_buf
        self.output_at_first_call = None
        self.raw_text = raw_text

    def __call__(self, url, **kwargs):
        if self.output_at_first_call is None and self.console_buf is not None:
            self.output_at_first_call = self.console_buf.getvalue()
        self.calls.append(url)
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        body = {"data": [copy.deepcopy(self.row)]} if url.endswith("/search") else self.trips
        r.json.return_value = body
        r.text = self.raw_text if (self.raw_text is not None and "/trips/" in url) else json.dumps(body)
        return r


def run(argv, *, stub=None, answer="y"):
    buf = StringIO()
    stub = stub or Stub()
    stub.console_buf = buf
    with patch("src.seats_client.requests.get", side_effect=stub):
        code = trips_tools.main(
            argv, read=lambda prompt: answer, console=Console(file=buf, width=190), today=TODAY
        )
    return code, " ".join(buf.getvalue().split()), stub


def capture_args(tmp_path, *extra):
    return [
        "capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
        "--source", "virginatlantic", "--out-dir", str(tmp_path / "real"),
        "--api-key", FLAG_KEY, *extra,
    ]


def yq_args(tmp_path, *extra, source="virginatlantic", cabin="J"):
    return [
        "yq-check", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
        "--source", source, "--cabin", cabin, "--out-dir", str(tmp_path / "real"),
        "--record-dir", str(tmp_path / "records"), "--api-key", FLAG_KEY, *extra,
    ]


# ---------------------------------------------------------------------------
# Asking before spending
# ---------------------------------------------------------------------------


def test_the_call_count_line_is_printed_before_any_request(tmp_path):
    code, out, stub = run(capture_args(tmp_path))
    assert stub.output_at_first_call is not None
    first = " ".join(stub.output_at_first_call.split())
    assert (
        "This will make at most 2 Seats.aero API call(s): 1 search (0 if served from "
        "the disk cache) + 1 trips. This process has spent 0 of 1,000; Seats.aero also "
        "counts your other runs today, which this tool cannot see." in first
    )


def test_answering_no_makes_no_call_and_exits_1(tmp_path):
    code, out, stub = run(capture_args(tmp_path), answer="n")
    assert code == 1
    assert stub.calls == []
    assert "declined" in out
    assert not (tmp_path / "real").exists()


def test_yes_skips_the_question(tmp_path):
    code, _, stub = run(capture_args(tmp_path, "--yes"), answer="n")
    assert code == 0
    assert len(stub.calls) == 2


def test_the_key_banner_is_printed_masked(tmp_path):
    _, out, _ = run(capture_args(tmp_path))
    assert "Seats.aero key:" in out and "(source:" in out
    assert FLAG_KEY not in out


def test_no_key_captures_nothing(tmp_path):
    args = [a for a in capture_args(tmp_path) if a not in ("--api-key", FLAG_KEY)]
    code, out, stub = run(args)
    assert code == 1
    assert stub.calls == []
    assert "No Seats.aero API key found" in out


# ---------------------------------------------------------------------------
# The capture
# ---------------------------------------------------------------------------


def test_a_capture_writes_the_envelope_and_the_raw_body_with_no_key(tmp_path):
    code, out, stub = run(capture_args(tmp_path), stub=Stub(raw_text='{"data": "verbatim"}'))
    # The raw body here is deliberately NOT the page, so the label check refuses
    # the file and capture says so with exit 5 (Re-test 2, R2-4). The envelope
    # and the verbatim body are still written.
    assert code == trips_tools.EXIT_DRIFT, out
    assert "CANNOT FLIP THE UNVERIFIED LABEL" in out
    files = sorted((tmp_path / "real").iterdir())
    names = [p.name for p in files]
    assert names == [
        f"2027-01-27_virginatlantic_LHRSFO_{B4_ID}.json",
        f"2027-01-27_virginatlantic_LHRSFO_{B4_ID}.raw.txt",
    ]
    envelope = json.loads(files[0].read_text())
    meta = envelope["_meta"]
    assert meta["captured_by"] == "src.trips_tools capture"
    assert meta["synthetic"] is False
    assert meta["key_redacted"] is True
    assert meta["http_status"] == 200
    assert meta["trips_parser_version"] == seats_trips.TRIPS_PARSER_VERSION
    assert meta["request"] == {
        "path": f"/partnerapi/trips/{B4_ID}",
        "params": {"include_filtered": "false", "min_cabin_pct": "100"},
    }
    assert meta["availability_row"]["ID"] == B4_ID
    assert meta["search_request"]["origin_airport"] == "LHR"
    assert meta["content_hash"] == response_cache.content_hash(envelope["pages"])
    assert "captured_at" in meta
    assert files[1].read_text() == '{"data": "verbatim"}'
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert FLAG_KEY not in path.read_text()
            assert "Partner-Authorization" not in path.read_text()
    assert "headers" not in json.dumps(meta)


def test_a_clean_capture_prints_the_drift_report_and_how_to_flip(tmp_path):
    code, out, _ = run(capture_args(tmp_path))
    assert code == 0
    for needle in (
        "DRIFT REPORT",
        "required-field failures (per itinerary and field): - none",
        "unknown keys (listed, never an error): - none",
        "cabin values seen: business",
        "flight-number shapes seen: AA99",
        "MixedCabinPct present on 0 itinerary(ies)",
        "pagination keys: none",
        "J: 1 of 1 matched itinerary(ies) carry TotalTaxes == the row's JTotalTaxes (45000)",
        "J: operating airline: VS by flight number (VS19)",
        "CAPTURED CLEAN",
        "TRIPS_SCHEMA_VERIFIED_BY",
    ):
        assert needle in out, needle


def test_injected_drift_exits_5_and_still_writes_the_file(tmp_path):
    drifted = tp.vs_direct(cost="60000", NewKey=1)
    del drifted["ID"]
    code, out, _ = run(capture_args(tmp_path), stub=Stub(trips=tp.payload([drifted], extra=1)))
    assert code == 5
    assert "CAPTURED WITH DRIFT" in out
    assert "ID is None" in out
    assert "undocumented itinerary key 'NewKey'" in out
    assert "undocumented top-level key 'extra'" in out
    assert len(list((tmp_path / "real").glob("*.json"))) == 1


def test_an_empty_capture_cannot_flip_the_label(tmp_path):
    code, out, _ = run(capture_args(tmp_path), stub=Stub(trips=tp.payload([])))
    assert code == 5
    assert "no itinerary to verify against" in out


def test_unknown_keys_alone_are_reported_but_do_not_block(tmp_path):
    t = tp.vs_direct(NewKey=1)
    code, out, _ = run(capture_args(tmp_path), stub=Stub(trips=tp.payload([t])))
    assert code == 0
    assert "undocumented itinerary key 'NewKey'" in out


def test_a_trips_failure_captures_nothing(tmp_path):
    class Fail(Stub):
        def __call__(self, url, **kwargs):
            r = super().__call__(url, **kwargs)
            if "/trips/" in url:
                r.status_code = 404
            return r

    code, out, _ = run(capture_args(tmp_path), stub=Fail())
    assert code == 1
    assert "HTTP_404" in out
    assert not (tmp_path / "real").exists()


def test_key_material_in_the_response_is_refused(tmp_path):
    code, out, _ = run(
        capture_args(tmp_path), stub=Stub(trips=tp.payload([tp.vs_direct()], echo=FLAG_KEY))
    )
    assert code == 1
    assert "REFUSING TO WRITE" in out
    assert not (tmp_path / "real").exists() or not list((tmp_path / "real").iterdir())


def test_two_or_no_matching_rows_are_refused_with_a_list(tmp_path):
    code, out, stub = run(capture_args(tmp_path), stub=Stub(row={**vs_row(), "Date": "2027-01-28"}))
    assert code == 1
    assert "0 availability rows match source 'virginatlantic' on 2027-01-27" in out
    assert not any("/trips/" in c for c in stub.calls)


def test_id_mode_reads_the_row_locally_with_no_search_call(tmp_path):
    cache_dir = Path(config.CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "entry.json").write_text(json.dumps({"_meta": {}, "pages": [{"data": [vs_row()]}]}))
    args = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", FLAG_KEY]
    code, out, stub = run(args)
    assert code == 0, out
    assert [c for c in stub.calls if c.endswith("/search")] == []
    assert "This will make at most 1 Seats.aero API call(s): 1 trips" in out


def test_id_mode_without_a_local_row_says_the_evidence_is_missing(tmp_path):
    args = ["capture", "--availability-id", B4_ID, "--out-dir", str(tmp_path / "real"),
            "--api-key", FLAG_KEY]
    code, out, _ = run(args)
    assert (
        "no availability row found locally; unit and cross-check evidence NOT "
        "available from this capture" in out
    )
    assert "INFERRED from the itineraries" in out
    assert list((tmp_path / "real").glob("*.json"))


@pytest.mark.parametrize(
    "argv,needle",
    [
        (["capture"], "give --availability-id ID"),
        (["capture", "--availability-id", "../etc"], "not a plain 10-64 character id"),
        (["capture", "--availability-id", B4_ID, "--origin", "LHR"], "EITHER"),
        (["capture", "--origin", "LHR", "--destination", "SFO", "--date", "Jan 27",
          "--source", "virginatlantic"], "not YYYY-MM-DD"),
        (["capture", "--availability-id", B4_ID, "--cabin", "Z"], "not one of Y, W, J, F"),
    ],
)
def test_usage_errors_capture_nothing(tmp_path, argv, needle):
    code, out, stub = run(argv + ["--api-key", FLAG_KEY])
    assert code == 1
    assert needle in out
    assert stub.calls == []


def test_an_argparse_error_is_exit_1_not_2():
    assert trips_tools.main(["frobnicate"], console=Console(file=StringIO())) == 1


# ---------------------------------------------------------------------------
# yq-check
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("unverified_constants")
def test_yq_check_prints_every_field_and_writes_a_record_with_blanks(tmp_path):
    code, out, _ = run(yq_args(tmp_path))
    assert code == 0, out
    for needle in (
        "program: Virgin Atlantic Flying Club (source virginatlantic)",
        "route: LHR->SFO on 2027-01-27, cabin J",
        "miles: '60000' (JMileageCost)",
        "row taxes: raw 45000 GBP = GBP 450.00 read as cents = $609.30.",
        "flights: VS19 LHR",
        "flight-number carrier: VS",
        "seats: 2",
        "per-itinerary TotalTaxes: raw 45000 GBP (unit NOT VERIFIED",
        "read 'taxes, fees and carrier-imposed charges' for ONE adult on the same flight",
        "about equal to the row figure means includes_yq",
        "about the row figure plus a separate carrier-charge line means excludes_yq",
        "anything else is inconclusive, record nothing",
        "virginatlantic.com",
    ):
        assert needle in out, needle
    record = tmp_path / "records" / "2026-09-11-virginatlantic.md"
    text = record.read_text()
    assert "yq-check record" in text
    assert "____" in text
    assert f"virginatlantic,VS,<VERDICT>,2026-09-11,{record}," in out
    assert "virginatlantic,includes_yq" not in out and "virginatlantic,excludes_yq" not in out
    assert "likely INCONCLUSIVE" not in out


def test_the_record_it_writes_is_refused_until_the_blanks_are_filled(tmp_path):
    run(yq_args(tmp_path))
    from src.yq_inclusion import YqInclusionError, load

    rel = "docs/yq-checks/2026-09-11-virginatlantic.md"
    (tmp_path / "docs" / "yq-checks").mkdir(parents=True)
    (tmp_path / rel).write_text((tmp_path / "records" / "2026-09-11-virginatlantic.md").read_text())
    table = tmp_path / "yq.csv"
    table.write_text(f"source,airline,verdict,verified_on,evidence,notes\nvirginatlantic,VS,includes_yq,2026-09-11,{rel},\n")
    with pytest.raises(YqInclusionError, match="blanks"):
        load(table, today=TODAY, root=tmp_path)
    # Fill the five FIELD lines exactly as the record asks, and nothing else: a
    # blanket replace of every marker would also rewrite any prose that quoted
    # it and hide a record that can never load (Re-test 2, R2-1).
    fills = {
        "- date checked: ____": "- date checked: 2026-09-12",
        "- flight(s) shown: ____": "- flight(s) shown: VS19 LHR-SFO",
        "- taxes, fees and carrier-imposed charges for ONE adult: ____":
            "- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00",
        "- separate carrier-imposed charge line (if any): ____":
            "- separate carrier-imposed charge line (if any): none shown",
        "- verdict (includes_yq / excludes_yq / inconclusive): ____":
            "- verdict (includes_yq / excludes_yq / inconclusive): includes_yq",
    }
    text = (tmp_path / rel).read_text()
    for blank, filled in fills.items():
        assert text.count(blank) == 1, blank
        text = text.replace(blank, filled)
    (tmp_path / rel).write_text(text)
    assert load(table, today=TODAY, root=tmp_path)[("virginatlantic", "VS")].includes


def test_yq_check_refuses_qatar_before_any_call(tmp_path):
    code, out, stub = run(yq_args(tmp_path, source="qatar"))
    assert code == 1
    assert stub.calls == []
    assert "reports no taxes for 'qatar'" in out


def test_yq_check_refuses_a_zero_tax_figure_before_the_trips_call(tmp_path):
    row = vs_row()
    row["JTotalTaxes"] = 0
    code, out, stub = run(yq_args(tmp_path), stub=Stub(row=row))
    assert code == 1
    assert "0 means not reported" in out
    assert not any("/trips/" in c for c in stub.calls)


def test_yq_check_warns_on_dl_metal(tmp_path):
    trips = tp.payload([tp.trip([tp.segment("DL41", "LHR", "SFO", 1)])])
    code, out, _ = run(yq_args(tmp_path), stub=Stub(trips=trips))
    assert "likely INCONCLUSIVE - pick a flight on VS metal" in out
    assert "no nonzero surcharge row" in out
    assert "WARNING: likely INCONCLUSIVE" in (tmp_path / "records" / "2026-09-11-virginatlantic.md").read_text()


def test_yq_check_warns_when_the_metal_is_not_known(tmp_path):
    code, out, _ = run(yq_args(tmp_path), stub=Stub(trips=tp.payload([])))
    assert "likely INCONCLUSIVE - pick a flight on VS metal (the flight-number carrier is not known)" in out
