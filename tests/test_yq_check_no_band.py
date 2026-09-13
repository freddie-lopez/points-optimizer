"""
Re-test 4, R4-1: a check that cannot tell includes_yq from excludes_yq backs no
verdict.

When no nonzero surcharge band is modelled for the (program, airline, cabin,
route), a site total equal to the row figure is ALSO what a fare with no
carrier surcharge shows. yq-check then prints no row, names the reason, points
to where the table does model a band, and writes a machine-readable marker into
the record. The loader refuses a record carrying the marker, and any record
whose band line is missing, NONE MODELLED or $0. The class: a lookup that is
not KNOWN on one airline writes the same marker, and the loader already refuses
its status (test_yq_airline_scope.py).
"""
import copy
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import trips_tools, yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import vs_row
from tests.test_trips_tools import Stub, yq_args
from tests.test_yq_airline_scope import HEADER, body

TODAY = date(2026, 9, 11)
# The literal the record carries; pinned here so a rename is a test failure.
NO_VERDICT_MARKER = "yq-check: NO VERDICT POSSIBLE"
BAND = "- modelled carrier surcharge band: $200-$350 (pt $275) one way (VS metal, cabin J)"


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def _w_row():
    row = copy.deepcopy(vs_row())
    row.update(WAvailable=True, WMileageCost="40000", WTotalTaxes=25000, WAirlines="VS, DL",
               WRemainingSeats=2)
    return row


def _run(tmp_path, cabin, row=None, trips=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub(row=row, trips=trips)):
        code = trips_tools.main(
            yq_args(tmp_path, cabin=cabin), read=lambda _: "y",
            console=Console(file=buf, width=500), today=TODAY,
        )
    out = " ".join(buf.getvalue().split())
    return code, out, next((tmp_path / "records").glob("*.md"))


def _w(tmp_path):
    trips = tp.payload([tp.vs_direct("VS19", cabin="premium", cost=40000, taxes=25000)])
    return _run(tmp_path, "W", row=_w_row(), trips=trips)


def test_a_cabin_with_no_band_prints_no_row_and_names_the_reason(tmp_path):
    code, out, rec = _w(tmp_path)
    assert "<VERDICT>" not in out
    assert "This check cannot back a verdict: no nonzero carrier surcharge band is modelled " \
           "for VS metal under Virgin Atlantic Flying Club, cabin W, on this route" in out
    assert "the check cannot tell includes_yq from excludes_yq" in out
    assert "For VS metal the surcharge table models a band for: cabin J on NA-EU routes - run the check there" in out
    # The rule is not printed as if it could decide.
    assert "THIS CHECK CANNOT BACK A VERDICT" in out
    assert "Site total about equal to the row figure" not in out


def test_the_record_carries_the_machine_readable_marker(tmp_path):
    _, _, rec = _w(tmp_path)
    text = rec.read_text()
    assert f"- {NO_VERDICT_MARKER} - no nonzero carrier surcharge band is modelled" in text
    assert "NONE MODELLED for VS metal" in text


def test_the_record_filled_honestly_still_backs_no_verdict(tmp_path, monkeypatch):
    """The Tester's end-to-end repro: fill the five fields, paste a VS row, load."""
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    argv_root = tmp_path
    _, _, rec = _w(argv_root)
    text = rec.read_text()
    for blank, value in (
        ("- date checked: ____", "- date checked: 2026-09-12"),
        ("- flight(s) shown: ____", "- flight(s) shown: VS19"),
        ("(yes / no): ____", "(yes / no): yes"),
        ("added up): ____", "added up): USD 250.00"),
        ("inconclusive): ____", "inconclusive): includes_yq"),
    ):
        text = text.replace(blank, value)
    assert "____" not in text
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / rec.name).write_text(text)
    csv = tmp_path / "yq.csv"
    csv.write_text(HEADER + f"\nvirginatlantic,VS,includes_yq,2026-09-11,docs/yq-checks/{rec.name},x\n")
    with pytest.raises(YqInclusionError, match="NO VERDICT POSSIBLE"):
        yq_inclusion.load(csv, today=TODAY, root=tmp_path)


def test_an_airline_with_no_band_anywhere_says_no_check_on_it_can_settle(tmp_path):
    code, out, rec = _run(tmp_path, "J", trips=tp.payload([tp.vs_direct("DL41")]))
    assert "<VERDICT>" not in out
    assert "no nonzero carrier surcharge band is modelled for DL metal" in out
    assert "models no band for this program on this airline at all" in out
    assert NO_VERDICT_MARKER in rec.read_text()


def test_no_single_known_airline_writes_the_marker_too(tmp_path):
    trips = tp.payload([tp.vs_direct("VS19", trip_id="t1"), tp.vs_direct("DL41", trip_id="t2")])
    code, out, rec = _run(tmp_path, "J", trips=trips)
    assert "<VERDICT>" not in out
    assert "is AMBIGUOUS, not one KNOWN airline" in out
    assert f"- {NO_VERDICT_MARKER} - the itinerary lookup for cabin J is AMBIGUOUS" in rec.read_text()


def test_a_banded_check_still_prints_its_row_and_no_marker(tmp_path):
    code, out, rec = _run(tmp_path, "J")
    assert "virginatlantic,VS,<VERDICT>,2026-09-11," in out
    assert NO_VERDICT_MARKER not in rec.read_text()


# -- the loader ------------------------------------------------------------------


def _load(tmp_path, text):
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.md").write_text(text)
    csv = tmp_path / "yq.csv"
    csv.write_text(HEADER + "\nvirginatlantic,VS,includes_yq,2026-09-10,docs/yq-checks/a.md,x\n")
    return yq_inclusion.load(csv, today=TODAY, root=tmp_path)


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda t: t.replace(BAND + "\n", ""), "0 'modelled carrier surcharge band' lines"),
        (lambda t: t.replace(BAND, BAND + "\n" + BAND), "2 'modelled carrier surcharge band' lines"),
        (lambda t: t.replace(BAND, "- modelled carrier surcharge band: NONE MODELLED for VS metal, "
                                   "so the site total cannot tell includes from excludes"),
         "no nonzero surcharge band"),
        (lambda t: t.replace(BAND, "- modelled carrier surcharge band: $0 one way"), "no nonzero surcharge band"),
        (lambda t: t.replace(BAND, "- modelled carrier surcharge band: $0-$0 (pt $0)"), "no nonzero surcharge band"),
        (lambda t: t.replace(BAND, BAND + "\n- yq-check: NO VERDICT POSSIBLE - no band"), "NO VERDICT POSSIBLE"),
        (lambda t: t.replace(BAND, BAND + "\n- YQ-CHECK:  no verdict possible"), "NO VERDICT POSSIBLE"),
    ],
    ids=["no-band-line", "two-band-lines", "none-modelled", "zero", "zero-range", "marker", "marker-case"],
)
def test_the_loader_refuses_a_record_without_a_nonzero_band_or_with_the_marker(tmp_path, mutate, needle):
    with pytest.raises(YqInclusionError, match=needle):
        _load(tmp_path, mutate(body()))


def test_the_marker_the_tool_writes_is_the_one_the_loader_refuses():
    assert yq_inclusion.NO_VERDICT_MARKER == NO_VERDICT_MARKER


def test_a_record_with_a_nonzero_band_and_no_marker_loads(tmp_path):
    assert _load(tmp_path, body())[("virginatlantic", "VS")].includes


@pytest.mark.parametrize("status", ["AMBIGUOUS", "UNKNOWN", "NOT LOOKED UP", "NONE"])
def test_the_class_any_status_but_known_backs_no_verdict(tmp_path, status):
    with pytest.raises(YqInclusionError, match="not KNOWN"):
        _load(tmp_path, body(status=status))
