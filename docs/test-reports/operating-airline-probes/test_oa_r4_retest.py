"""
Re-test 4 (fix round 3, from the Manager review: b3bc640..ea79ce9). The new
behaviour attacked: D1(b) airline scoping and case C keyed by (source, airline),
the must-fix-2 yq-check arithmetic and record, NOT_NEEDED_TAXES_UNREPORTED,
--refresh on capture / yq-check, the must-fix-3 messages, and the should-fix-5a
rewording.

RED = a defect that exists. GREEN = held up.
"""
import copy
import json
import re
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from conftest import (  # noqa: F401
    BASE, B4_VS, ROOT, Stub, aid_for, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
    YQ_HEADER, yq_load, yq_record_body, yq_table, yq_write_record,
)
from src import config, seats_trips, trips_tools
from src.models import MetalStatus
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import vs_row

KEY = "PROBEKEYr4a1s2d3f4g5h6j7"
TODAY = date(2026, 9, 11)


def seg(flight, o="LHR", d="SFO", order=1):
    return tp.segment(flight, o, d, order, AvailabilityID=B4_VS)


OUT = {
    "known_vs": tp.payload([vs_itinerary(B4_VS)]),
    "known_dl": tp.payload([vs_itinerary(B4_VS, "DL41")]),
    "known_af": tp.payload([vs_itinerary(B4_VS, "AF83")]),
    "known_vs_dl": tp.payload([tp.trip([seg("VS19", "LHR", "JFK", 1), seg("DL41", "JFK", "SFO", 2)],
                                       availability_id=B4_VS, cost=60000)]),
    "ambiguous": tp.payload([vs_itinerary(B4_VS), vs_itinerary(B4_VS, "DL41", trip_id="t2")]),
    "404": (404, None),
}


def _b4(table, outcome, airlines="VS, DL, AF", trips_mode="auto"):
    stub = Stub(rows_for=vs_b4_rows(airlines=airlines), trips={B4_VS: OUT[outcome]})
    results, totals, text, opts, fx = evaluate(stub, yq_table=table, trips_mode=trips_mode)
    return results["B4"], flat(text)


# ===========================================================================
# D1(b): a verdict reaches only KNOWN metal on its airline
# ===========================================================================


@pytest.mark.parametrize("outcome,why", [
    ("known_dl", "is DL by flight number, a different airline"),
    ("known_af", "is AF by flight number, a different airline"),
    ("known_vs_dl", "is several airlines by flight number (VS, DL), not one"),
    ("ambiguous", "is AMBIGUOUS"),
    ("404", "is UNKNOWN (HTTP_404)"),
])
def test_a_vs_verdict_does_not_reach_other_metal_and_the_line_says_why(outcome, why):
    b4, text = _b4(yq_table("includes_yq"), outcome)
    assert b4.points_total_score_usd == float("inf")
    assert "A YQ check is recorded for the 'virginatlantic' source on VS" in text
    assert why in text


def test_trips_off_leaves_a_recorded_verdict_inert_and_says_never_looked_up():
    b4, text = _b4(yq_table("includes_yq"), "known_vs", trips_mode="off")
    assert b4.points_total_score_usd == float("inf")
    assert "NOT LOOKED UP" in text and "does not apply here" in text


def test_a_verdict_for_a_valid_but_different_airline_does_not_reach_vs():
    b4, text = _b4(yq_table("includes_yq", airline="DL"), "known_vs")
    assert b4.points_total_score_usd == float("inf")


def test_two_airlines_two_verdicts_each_reaches_only_its_own_metal():
    table = {**yq_table("includes_yq", airline="VS"), **yq_table("excludes_yq", airline="AF")}
    vs, _ = _b4(table, "known_vs")
    af, text = _b4(table, "known_af")
    assert vs.points_total_score_usd == pytest.approx(vs.funding_plan.score_usd + vs.best_points.observed_taxes_usd)
    # case C on AF metal under Virgin: the verdict applies, no band is modelled -> unknown, never taxes-only
    assert af.points_total_score_usd == float("inf")
    assert "SURCHARGE_UNKNOWN" in {r.code for r in af.reasons}


def test_case_c_is_keyed_by_source_and_airline():
    b4, _ = _b4(yq_table("excludes_yq", airline="VS"), "known_vs")
    base = b4.funding_plan.score_usd + b4.best_points.observed_taxes_usd
    assert (b4.points_score_low_usd - base, b4.points_score_high_usd - base) == pytest.approx((200.0, 350.0))
    other, _ = _b4(yq_table("excludes_yq", source="flyingblue", airline="VS"), "known_vs")
    assert other.points_total_score_usd == float("inf"), "a Flying Blue row reached a Virgin award"


# --- the loader's side of D1 and must-fix 2 ------------------------------------------


@pytest.mark.parametrize("csv_airline", ["VS", "vs", " VS ", "Vs\t"])
def test_airline_column_case_and_whitespace_normalise(tmp_path, csv_airline):
    ev = yq_write_record(tmp_path, yq_record_body(checked="vs", operated_code="Vs"))
    t = yq_load(tmp_path, f"virginatlantic,{csv_airline},includes_yq,2026-09-10,{ev},x")
    assert list(t) == [("virginatlantic", "VS")]


@pytest.mark.parametrize("kw,why", [
    ({"status": "AMBIGUOUS"}, "not KNOWN"),
    ({"status": "UNKNOWN"}, "not KNOWN"),
    ({"status": "NOT LOOKED UP"}, "not KNOWN"),
    ({"checked": "DL"}, "was run on DL metal and the row says VS"),
    ({"checked": "NONE - the lookup did not name one KNOWN airline, so this record cannot back a verdict"},
     "the row says VS"),
    ({"operated_code": "DL"}, "operated by DL, and the row says VS"),
    ({"operated": "no"}, "does not confirm"),
    ({"operated": "y"}, "does not confirm"),
    ({"operated": "yes."}, "does not confirm"),
    ({"operated": "yes (VS3 operated by Virgin)"}, "does not confirm"),
    ({"operated": "n/a"}, "does not confirm"),
])
def test_a_record_that_does_not_establish_the_rows_airline_is_refused(tmp_path, kw, why):
    ev = yq_write_record(tmp_path, yq_record_body(**kw))
    with pytest.raises(YqInclusionError, match=re.escape(why)):
        yq_load(tmp_path, f"virginatlantic,VS,includes_yq,2026-09-10,{ev},x")


@pytest.mark.parametrize("answer", ["yes", "Yes", "YES", "  yes  "])
def test_a_plain_yes_in_any_case_loads(tmp_path, answer):
    ev = yq_write_record(tmp_path, yq_record_body(operated=answer))
    assert yq_load(tmp_path, f"virginatlantic,VS,includes_yq,2026-09-10,{ev},x")


def test_a_record_with_two_operated_by_lines_is_refused(tmp_path):
    body = yq_record_body().replace(
        "- total taxes", "- the site shows this flight operated by VS itself, not a codeshare partner "
        "(yes / no): no\n- total taxes")
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="2 'the site shows this flight operated by' lines"):
        yq_load(tmp_path, f"virginatlantic,VS,includes_yq,2026-09-10,{ev},x")


# ===========================================================================
# The must-fix-2 yq-check arithmetic
# ===========================================================================


def _jfk_lhr_row(cabin="J", cost="60000", taxes=25000, currency="USD", extra_cabins=None):
    return row(source="virginatlantic", cost=cost, taxes=taxes, currency=currency, airlines="VS, DL",
               origin="JFK", dest="LHR", iso="2027-02-10", cabin=cabin, rid=B4_VS, extra_cabins=extra_cabins)


def _itin(flight="VS4", cabin="business", cost=60000, o="JFK", d="LHR"):
    return tp.trip([tp.segment(flight, o, d, 1, AvailabilityID=B4_VS)], availability_id=B4_VS,
                   cabin=cabin, cost=cost)


def _yq(tmp_path, row_, trips, cabin="J", o="JFK", d="LHR", day="2027-02-10", source="virginatlantic"):
    def side(url, **kw):
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        body = {"data": [copy.deepcopy(row_)]} if url.endswith("/search") else trips
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=side):
        code = trips_tools.main(
            ["yq-check", "--origin", o, "--destination", d, "--date", day, "--source", source, "--cabin", cabin,
             "--out-dir", str(tmp_path / "real"), "--record-dir", str(tmp_path / "docs" / "yq-checks"),
             "--api-key", KEY, "--yes"],
            read=lambda p: "y", console=Console(file=buf, width=500), today=TODAY)
    out = " ".join(buf.getvalue().split())
    recs = list((tmp_path / "docs" / "yq-checks").glob("*.md"))
    return code, out, (recs[0] if recs else None)


def test_the_band_is_one_way_and_added_to_the_usd_row_figure_for_vs_j_from_jfk(tmp_path):
    code, out, rec = _yq(tmp_path, _jfk_lhr_row(), tp.payload([_itin()]))
    assert "modelled carrier surcharge band: $200-$350 (pt $275) one way (VS metal, cabin J)" in out
    assert "row figure + band: $450.00-$600.00 (row $250.00 + band)" in out
    assert "Site total about equal to the row figure ($250.00): includes_yq" in out


def test_the_band_for_flying_blue_on_kl_metal_is_its_own_one_way_band(tmp_path):
    r = _jfk_lhr_row()
    r["Route"]["Source"] = "flyingblue"
    r["Route"]["DestinationAirport"] = "AMS"
    r["JAirlines"] = "KL, AF"
    trips = tp.payload([tp.trip([tp.segment("KL642", "JFK", "AMS", 1, AvailabilityID=B4_VS)],
                                availability_id=B4_VS, source="flyingblue", cost=60000)])
    code, out, rec = _yq(tmp_path, r, trips, d="AMS", source="flyingblue")
    assert "$75-$125 (pt $100) one way (KL metal, cabin J)" in out
    assert "row figure + band: $325.00-$375.00" in out


def _fill(rec, verdict="includes_yq", total="USD 250.00"):
    text = rec.read_text()
    text = text.replace("- date checked: ____", "- date checked: 2026-09-12")
    text = text.replace("- flight(s) shown: ____", "- flight(s) shown: VS4 JFK-LHR")
    text = re.sub(r"(operated by \S+ itself, not a codeshare partner \(yes / no\)): ____", r"\1: yes", text)
    text = re.sub(r"(its lines added up\)): ____", rf"\1: {total}", text)
    text = text.replace("inconclusive): ____", f"inconclusive): {verdict}")
    assert "____" not in text
    rec.write_text(text)


def _template(out, airline="VS"):
    m = re.search(rf"(virginatlantic,{airline},<VERDICT>,\S+,\S+,[A-Z]{{3}}-[A-Z]{{3}} [A-Z] \S+)", out)
    return m.group(1) if m else None


def test_a_record_that_says_the_check_cannot_tell_includes_from_excludes_backs_no_verdict(tmp_path, monkeypatch):
    """
    NEW. yq-check in a cabin (here W) or on a route with no modelled band for the
    airline writes into the record "modelled carrier surcharge band: NONE
    MODELLED for VS metal, so the site total cannot tell includes from
    excludes" and "likely INCONCLUSIVE", and STILL prints the row template. The
    rule it prints then still says "site total about equal to the row figure:
    includes_yq". The loader never reads the band line, so the record - which
    states in its own words that it cannot tell - backs an includes_yq row that
    scores every Virgin award on VS metal at taxes only. With no band, a site
    total equal to the row figure is equally what a carrier that levies no
    surcharge on that fare would show: it is not evidence of inclusion.
    """
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    r = _jfk_lhr_row(extra_cabins={"W": {"cost": "40000", "taxes": 25000, "airlines": "VS, DL"}})
    trips = tp.payload([_itin(), _itin(cabin="premium", cost=40000)])
    code, out, rec = _yq(tmp_path, r, trips, cabin="W")
    assert "NONE MODELLED for VS metal, so the site total cannot tell includes from excludes" in rec.read_text()
    template = _template(out)
    if template is not None:
        # End to end: fill the record honestly (site total = row figure) and load the row it printed.
        _fill(rec, total="USD 250.00")
        try:
            loaded = yq_load(tmp_path, template.replace("<VERDICT>", "includes_yq"))
        except YqInclusionError:
            loaded = None
        assert loaded is None, (
            "a record that says it cannot tell includes from excludes backed "
            f"{sorted(loaded)} -> includes_yq")


def test_a_record_with_a_band_and_a_filled_site_half_loads_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    code, out, rec = _yq(tmp_path, _jfk_lhr_row(), tp.payload([_itin()]))
    _fill(rec)
    template = _template(out)
    assert template
    t = yq_load(tmp_path, template.replace("<VERDICT>", "includes_yq"))
    assert t[("virginatlantic", "VS")].includes


def test_no_template_when_the_lookup_names_no_single_known_airline(tmp_path):
    trips = tp.payload([_itin(), _itin("DL1", )])
    trips["data"][1]["ID"] = "t2"
    code, out, rec = _yq(tmp_path, _jfk_lhr_row(), trips)
    assert "This check cannot back a verdict" in out and "<VERDICT>" not in out


# ===========================================================================
# NOT_NEEDED_TAXES_UNREPORTED, --refresh, messages, rewording
# ===========================================================================


def _sq_rows(o, d, iso):
    if (o, d) == ("LHR", "SFO"):
        return [row(source="singapore", cost="70000", taxes=30000, currency="SGD", airlines="SQ",
                    rid=B4_VS, iso=iso)]
    return None


def test_auto_spends_no_call_on_an_unreported_taxes_source_and_all_does():
    stub = Stub(rows_for=_sq_rows, trips={B4_VS: tp.payload([])})
    results, totals, text, opts, fx = evaluate(stub)
    b4 = next(l for l in fx.legs if l.id == "B4").points_candidates[0]
    assert stub.trips_calls == []
    assert b4.metal.reason_code == "NOT_NEEDED_TAXES_UNREPORTED"
    assert "B4" not in totals["legs_metal_lookup_missing_ids"]
    stub2 = Stub(rows_for=_sq_rows, trips={B4_VS: tp.payload([])})
    evaluate(stub2, trips_mode="all")
    assert [u for u in stub2.trips_calls if B4_VS in u]


def _warm_and_refresh(tmp_path, capsys, monkeypatch, extra=()):
    side_log = []

    def side(url, **kw):
        side_log.append(url)
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        body = {"data": [copy.deepcopy(vs_row())]} if url.endswith("/search") else tp.payload([tp.vs_direct()])
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    buf = StringIO()
    argv = ["capture", "--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27", "--source",
            "virginatlantic", "--out-dir", str(tmp_path / "real"), "--api-key", KEY, "--yes", *extra]
    with patch("src.seats_client.requests.get", side_effect=side):
        trips_tools.main(argv, read=lambda p: "y", console=Console(file=buf, width=400))
        side_log.clear()
        buf2 = StringIO()
        code = trips_tools.main(argv + ["--refresh"], read=lambda p: "y", console=Console(file=buf2, width=400))
    return code, " ".join(buf2.getvalue().split()), list(side_log)


def test_refresh_spends_exactly_the_calls_it_announces(tmp_path, capsys, monkeypatch):
    code, out, calls = _warm_and_refresh(tmp_path, capsys, monkeypatch)
    assert "at most 2 Seats.aero API call(s): 1 search (--refresh: never served from the disk cache) + 1 trips" in out
    assert sum(u.endswith("/search") for u in calls) == 1 and sum("/trips/" in u for u in calls) == 1
    # the warm run in this same process spent 2 (search + trips); the pre-call line must say so
    assert "This process has spent 2 of 1,000" in out


def test_the_drift_message_names_a_test_that_exists(tmp_path):
    test_file, test_name = trips_tools.DRIFT_TEST.split("::")
    assert f"def {test_name}(" in (ROOT / test_file).read_text()


CHANGELOG = re.compile(r"\bv[0-9]\b|\bRe-test\b|\bR[0-9]-[0-9]\b|[Mm]ust-fix|[Ss]hould-fix|Manager review|"
                       r"\bfinding [A-Z]-?[0-9]|\bL1[0-9]\b|\bround [0-9]\b|\bthe v0 bug\b")


def test_no_changelog_text_in_capture_yq_check_or_the_record(tmp_path, monkeypatch):
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    code, out, rec = _yq(tmp_path, _jfk_lhr_row(), tp.payload([_itin()]))
    for text in (out, rec.read_text()):
        assert not CHANGELOG.search(text), CHANGELOG.search(text).group(0)


@pytest.mark.parametrize("which", [None, "includes_yq", "excludes_yq"])
@pytest.mark.parametrize("outcome", ["known_vs", "known_dl", "ambiguous", "404"])
def test_no_changelog_text_in_live_output(which, outcome):
    table = yq_table(which) if which else {}
    b4, text = _b4(table, outcome)
    assert not CHANGELOG.search(text), CHANGELOG.search(text).group(0)
