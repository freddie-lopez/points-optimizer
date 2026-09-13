"""
J. WHAT THE UI NOW SHOWS THAT THE ENGINE GETS WRONG. These are engine defects,
not UI ones - they are here because this round put a verdict chip on them and
because F-3 was a fix of exactly this shape (a sentence quoting a figure that is
not the one scored).

The fixture is SYNTHETIC and is built in tmp: a UK departure (LHR->JFK, J) on VS
metal, whose surcharge the table models as a BAND ($200-$350), so the leg
carries a range AND UK Air Passenger Duty at the same time.

RED = a defect that exists. GREEN = held up.
"""
import copy
import io
import json

import pytest
from rich.console import Console

from conftest import ROOT

TRIP_C = ROOT / "tests" / "fixtures" / "trips" / "trip_c_lon_mry_surcharge.json"


def probe_fixture(tmp_path, cash_usd):
    src = json.loads(TRIP_C.read_text())
    leg = copy.deepcopy([l for l in src["legs"] if l["id"] == "C2"][0])
    cand = leg["points_candidates"][0]
    cand.update(program="Virgin Atlantic Flying Club", marketing_carrier="VS",
                operating_carrier="VS", points=50000,
                label="PROBE SYNTHETIC: VS award LHR->JFK business")
    leg.update(id="P1", origin="LHR", destination="JFK",
               description="PROBE SYNTHETIC: LHR->JFK business on VS metal")
    leg["cash_options"] = [{"label": f"PROBE SYNTHETIC ${cash_usd} business fare LHR-JFK",
                            "amount": float(cash_usd), "currency": "USD"}]
    fixture = {
        "id": "probe_apd_sensitive",
        "name": "PROBE - UK departure whose verdict flips inside the surcharge band",
        "description": "SYNTHETIC tester probe. Never quote.",
        "source": "SYNTHETIC. Built by the tester from trip_c's C2 leg.",
        "trip_level_flags": ["SYNTHETIC probe fixture."],
        "legs": [leg],
    }
    path = tmp_path / "probe_apd_sensitive.json"
    path.write_text(json.dumps(fixture, indent=1))
    return path


def score(path):
    from src import main as cli

    argv = ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
            "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"]
    sink = []
    buf = io.StringIO()
    code = cli.dispatch(cli.build_parser().parse_args(argv), Console(file=buf, width=190), sink)
    return code, sink[0], buf.getvalue()


def test_J1_a_verdict_that_flips_inside_the_surcharge_band_after_apd_says_so(tmp_path, pinned):
    """VERDICT SENSITIVE is decided BEFORE UK APD is added to the points side,
    so a leg whose points side wins at the band's low end and loses at its high
    end ONCE THE DUTY IS IN is still reported as a settled POINTS verdict."""
    code, run, text = score(probe_fixture(tmp_path, 1150))
    r = run.results[0]
    wins_low = r.points_score_low_usd < r.cash_total_score_usd
    wins_high = r.points_score_high_usd < r.cash_total_score_usd
    assert (r.points_score_low_usd, r.points_score_high_usd) != (None, None)
    assert wins_low and not wins_high, (
        "the probe fixture no longer straddles the cash price; rebuild it")
    assert r.verdict_sensitive, (
        f"points score {r.points_score_low_usd:,.2f} - {r.points_score_high_usd:,.2f} "
        f"straddles ${r.cash_total_score_usd:,.2f} cash (UK APD ${r.apd_added_usd:,.2f} "
        f"is in both ends), yet the leg is reported as a settled "
        f"{r.verdict.upper()} verdict with no VERDICT SENSITIVE marker")


def test_J2_the_same_leg_without_apd_is_correctly_marked_sensitive(tmp_path, pinned):
    """The control: the same band and the same straddle WITHOUT a UK departure
    is flagged. So the rule works; it is the duty that slips past it."""
    path = probe_fixture(tmp_path, 780)
    fixture = json.loads(path.read_text())
    fixture["legs"][0].update(origin="JFK", destination="LHR")  # no UK departure
    path.write_text(json.dumps(fixture))
    code, run, text = score(path)
    r = run.results[0]
    assert r.points_score_low_usd < r.cash_total_score_usd < r.points_score_high_usd
    assert r.verdict_sensitive is True
    assert "VERDICT SENSITIVE" in text


def test_J3_the_f3_sentence_quotes_the_score_it_verdicted_on(tmp_path, pinned):
    """F-3, as fixed this round, on a leg the goldens do not cover."""
    code, run, text = score(probe_fixture(tmp_path, 1150))
    r = run.results[0]
    assert f"${r.points_total_score_usd:,.2f}" in r.verdict_reason, r.verdict_reason
    assert "UK APD" in r.verdict_reason
