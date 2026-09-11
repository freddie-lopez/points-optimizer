"""
Re-test 2: e64c32a (re-test fixes) and fcb70f8 (Manager round: the
multi-traveller guard, post-APD certain-cash sentence, search APD floors,
relocation banner, PYTHONUSERBASE, README tax rules).

RED = defect present. Offline: every transport is a stub or a committed file.
"""
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import (
    BASE_ARGV, CSP, ROOT, TRIP_B, UNSET, evaluate, flat, only, row, run_cli,
)

B1 = ("SFO", "MAD")
B4 = ("LHR", "SFO")
PARTY = "cash (multi-traveller points not priced)"
APD_Y = 138.108
APD_J = 330.376


def _rows(*specs):
    return lambda o, d, i: [row(origin=o, dest=d, iso=i, **s) for s in specs]


def _mut(leg_id, **over):
    def mut(fx):
        for leg in fx.legs:
            if leg.id == leg_id:
                for k, v in over.items():
                    setattr(leg, k, v)
    return mut


def _trip_b_file(tmp_path, **per_leg):
    """A copy of Trip B with per-leg overrides, e.g. B1={'travelers': 2}."""
    data = json.loads(TRIP_B.read_text())
    for leg in data["legs"]:
        leg.update(per_leg.get(leg["id"], {}))
    p = tmp_path / "trip_b_europe.json"
    p.write_text(json.dumps(data))
    return p


def _argv_for(path, *extra):
    return ["--trip-fixture", str(path), "--balance", "UR=160000", "--card", CSP,
            "--transfer-date", "2026-09-15", *extra]


UA_B1 = dict(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA")


# ===========================================================================
# RED
# ===========================================================================


def test_R2_1_a_party_leg_with_an_unpriced_partner_gets_no_one_seat_break_even(tmp_path):
    """
    `elif result.break_even_programs:` is checked BEFORE `elif party_candidates:`.
    A 2-traveller B1 that lists a reachable unpriced partner (Trip A's fixture
    shape) gets verdict `cash (points unpriced)` and 'Points would win below
    39,500 points' - the break-even for ONE seat's award price against the
    PARTY's cash. With the couple's $790 entered, that reads 'below 79,000': a
    50,000-per-seat award (100,000 for two) looks like a win. No
    PARTY_PRICING_UNVERIFIED reason, so the trip row does not count the leg.
    """
    res, totals, out = evaluate(
        only(B1, _rows(UA_B1)), tmp_path,
        fixture_mutator=_mut("B1", travelers=2,
                             unpriced_partner_programs=["United MileagePlus"]),
    )
    b1 = res["B1"]
    assert "B1" in totals["legs_party_pricing_unverified_ids"]
    assert "Points would win below" not in (b1.verdict_reason or "")
    assert "Break-even is 39,500 points" not in flat(out)


@pytest.mark.parametrize("travelers", [0, -1])
def test_R2_2_a_flight_leg_for_zero_or_negative_travellers_is_not_scored_as_one_seat(travelers, tmp_path):
    """
    The guard reads `int(travelers or 1) > 1`, and the loader accepts any int.
    travelers 0 or -1 scores a POINTS win ($256 vs $395) as one seat. A leg for
    no one (or minus one) is malformed input; --new-trip refuses it, the
    fixture loader does not.
    """
    p = _trip_b_file(tmp_path, B1={"travelers": travelers})
    from src.trip_loader import TripFixtureError, load_trip_fixture

    try:
        load_trip_fixture(p)
    except TripFixtureError:
        return  # refused at load: fine
    res, _, _ = evaluate(only(B1, _rows(dict(UA_B1, cost="20000"))), tmp_path,
                         fixture_path=p)
    assert res["B1"].verdict != "points"


def test_R2_3_search_dedup_does_not_hide_a_cheaper_award_behind_a_known_one(capsys, monkeypatch):
    """
    e64c32a made dedup use the ranking rule, so a KNOWN-cash award now always
    beats an unknown one of the same program/date/cabin - and the unknown one
    is DELETED, not ranked after. United 30,000 (no tax figure) vanishes behind
    United 50,000 + $56: 20,000 fewer points of real availability never shown.
    The ranking already puts unknowns after knowns; deleting them hides awards.
    """
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP]
    rows = [row(**UA_B1, rid="a"),
            row(source="united", cost="30000", taxes=UNSET, currency="USD",
                airlines="UA", rid="b")]
    code, raw, _ = run_cli(argv, lambda *_: rows, capsys, monkeypatch)
    assert code == 0
    assert "30,000" in raw, "the 30,000-point United award was dropped by dedup"


def test_R2_4_the_party_guard_does_not_erase_the_indirect_and_unattributed_counts(tmp_path):
    """
    The guard `continue`s before the attribution / partner / indirect checks, so
    on a 2-traveller leg a Qatar award (indirect) and an unnamed-program award
    reach the trip block as nothing but 'party not priced'; an American-only
    leg (no UR path at all) is told to 'price it by hand: 2 x the points'.
    """
    res, totals, _ = evaluate(
        only(B4, lambda o, d, i: [
            row(source="qatar", cost="33000", taxes=0, currency="USD",
                airlines="QR", origin=o, dest=d, iso=i, rid="q"),
            row(source="", cost="25000", taxes=5600, currency="USD",
                airlines="UA", origin=o, dest=d, iso=i, rid="u"),
        ]),
        tmp_path, fixture_mutator=_mut("B4", travelers=2),
    )
    assert res["B4"].verdict == PARTY
    assert "B4" in totals["legs_indirect_path_unverified_ids"]
    assert "B4" in totals["legs_award_unattributed_ids"]


def test_R2_5_a_trip_whose_flights_were_never_priced_does_not_quote_a_live_margin(tmp_path, capsys, monkeypatch):
    """
    A couple's trip built with `--new-trip --travelers 2`: every flight leg is
    (correctly) not scored. The headline still reads 'Optimizer beats paying
    cash by 0.00% (live)', margin provenance 'live', exit 0 - a single quotable
    number saying points save nothing, on a trip where points were never
    evaluated. PARTY_PRICING_UNVERIFIED is COUNTED, not WIDENS, and does not
    touch `margin_withheld`, so 'could not price' reaches the headline as 0%.
    """
    from src import trip_builder

    flights = [trip_builder.parse_leg_flag("SFO:MAD:2027-01-15:790"),
               trip_builder.parse_leg_flag("LHR:SFO:2027-01-27:964")]
    fixture = trip_builder.build_fixture("couple_probe", flights, [], 2, "Y")
    path = trip_builder.write_fixture(fixture, directory=tmp_path)
    code, _, text = run_cli(_argv_for(path),
                            lambda o, d, i: [row(origin=o, dest=d, iso=i, **UA_B1)],
                            capsys, monkeypatch)
    assert "Flight legs for 2+ travellers" in text, "precondition"
    assert code != 0 or not re.search(r"beats paying cash by │ 0\.00% \(live\)", text)


def test_R2_6_search_mode_names_a_relocated_key_file(capsys, monkeypatch, tmp_path):
    """
    Should-fix 4: `print_relocation_banner` is called from `build_live` only
    (trip runs). A single-route search resolves its key through the same
    POINTS_OPTIMIZER_ENV_FILE and prints nothing about it.
    """
    monkeypatch.setenv("POINTS_OPTIMIZER_ENV_FILE", str(tmp_path / "elsewhere.env"))
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP]
    code, _, text = run_cli(argv, lambda *_: [row(**UA_B1)], capsys, monkeypatch)
    assert code == 0
    assert "POINTS_OPTIMIZER_ENV_FILE is set" in text


def test_R2_7_search_for_two_says_the_html_export_was_skipped(tmp_path, capsys, monkeypatch):
    """--passengers 2 --html returns before the export; nothing says so."""
    monkeypatch.chdir(tmp_path)
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP, "--passengers", "2", "--html"]
    code, _, text = run_cli(argv, lambda *_: [row(**UA_B1)], capsys, monkeypatch)
    assert not (tmp_path / "results.html").exists(), "precondition"
    assert "html" in text.lower(), "the --html request was dropped without a word"


# ===========================================================================
# GREEN
# ===========================================================================


def test_a_party_leg_live_is_not_scored_anywhere_and_is_counted(tmp_path, capsys, monkeypatch):
    p = _trip_b_file(tmp_path, B1={"travelers": 2})
    code, raw, text = run_cli(_argv_for(p), only(B1, _rows(UA_B1)), capsys, monkeypatch)
    assert code == 0
    b1_row = next(l for l in raw.splitlines() if "│ B1" in l)
    assert "PAY CASH (party o" in b1_row and "priced for ONE seat" in b1_row
    assert "Verdict: CASH (MULTI-TRAVELLER POINTS NOT PRICED)" in text
    assert "This flight leg is for 2 travellers" in text
    assert re.search(r"Flight legs for 2\+ travellers - points NOT │ 1 \(B1\)", text)
    assert re.search(r"Points spent │ 0 ", text)
    assert "Legs where points win │ 0" in text


def test_a_party_leg_on_the_offline_badge_path_is_not_scored(tmp_path, capsys, monkeypatch):
    p = _trip_b_file(tmp_path, B2={"travelers": 3})
    code, raw, _ = run_cli(_argv_for(p, "--offline"), lambda *a: None, capsys, monkeypatch, key=None)
    assert code == 0
    b2_row = next(l for l in raw.splitlines() if "│ B2" in l)
    assert "PAY CASH (party o" in b2_row and "badge" in b2_row
    assert "This flight leg is for 3 travellers" in flat(raw)


def test_a_party_leg_on_replay_is_not_scored(tmp_path, capsys, monkeypatch):
    from docs_v5 import build_corpus  # type: ignore

    manifest, _ = build_corpus(tmp_path / "corpus")
    p = _trip_b_file(tmp_path, B3={"travelers": 2})
    code, raw, _ = run_cli(_argv_for(p, "--from-snapshot", str(manifest)),
                           lambda *a: None, capsys, monkeypatch, key=None)
    assert code == 0
    b3_row = next(l for l in raw.splitlines() if "│ B3" in l)
    assert "PAY CASH (party o" in b3_row and "snapshot" in b3_row


def test_hotel_legs_for_two_are_unaffected(tmp_path):
    res, totals, _ = evaluate(lambda *a: None, tmp_path)
    for leg_id in ("B5", "B6", "B7"):
        assert res[leg_id].leg.travelers == 2
        assert res[leg_id].verdict != PARTY
    assert not set(totals["legs_party_pricing_unverified_ids"]) & {"B5", "B6", "B7"}


def test_new_trip_with_two_travellers_end_to_end_scores_no_flight_leg(tmp_path, capsys, monkeypatch):
    from src import trip_builder

    flights = [trip_builder.parse_leg_flag("SFO:MAD:2027-01-15:790"),
               trip_builder.parse_leg_flag("LHR:SFO:2027-01-27:964")]
    fixture = trip_builder.build_fixture("couple_probe", flights, [], 2, "Y")
    path = trip_builder.write_fixture(fixture, directory=tmp_path)
    code, raw, text = run_cli(_argv_for(path), lambda o, d, i: [row(origin=o, dest=d, iso=i, **UA_B1)],
                              capsys, monkeypatch)
    assert code == 0
    assert "Legs where points win │ 0" in text
    assert text.count("CASH (MULTI-TRAVELLER POINTS NOT PRICED)") == 2


def test_search_for_two_names_no_top_strategy_and_exports_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP, "--passengers", "2", "--html"]
    code, raw, text = run_cli(argv, lambda *_: [row(**UA_B1)], capsys, monkeypatch)
    assert code == 0
    assert "PRICED FOR ONE SEAT" in text
    assert "Top strategy" not in text
    assert not (tmp_path / "results.html").exists()


@pytest.mark.parametrize("o,d,cabin,cost,floor", [
    ("LHR", "SFO", "J", "60000", 600.0 + APD_J),
    ("LHR", "SFO", "F", "90000", 900.0 + APD_J),
    ("MAN", "SFO", "Y", "30000", 300.0 + APD_Y),
    ("SFO", "MAD", "Y", "30000", 300.0),
])
def test_search_unknown_cash_floor_carries_owed_uk_apd_only_on_uk_departures(
    o, d, cabin, cost, floor, capsys, monkeypatch
):
    argv = ["--origin", o, "--destination", d, "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP]
    rows = [row(source="united", cost=cost, taxes=UNSET, currency="USD",
                airlines="UA", origin=o, dest=d, cabin=cabin)]
    code, raw, _ = run_cli(argv, lambda *_: rows, capsys, monkeypatch)
    assert code == 0
    assert f">= ${floor:,.2f}" in raw


def test_the_inert_certain_cash_sentence_quotes_the_post_apd_floor(tmp_path, capsys, monkeypatch):
    """Should-fix 1: a master-style corpus (Aeroplan 50,000 + CAD 44.60 on B4)."""
    from docs_v5 import build_corpus  # type: ignore

    manifest, _ = build_corpus(tmp_path)
    code, _, text = run_cli(BASE_ARGV + ["--from-snapshot", str(manifest)],
                            lambda *a: None, capsys, monkeypatch, key=None)
    assert code == 0
    assert "at least $638.11" in text
    assert "vs at least $500.00" not in text
    b4 = text.split("B4 - LON->MRY")[1].split("B5 - ")[0]
    assert "carrier-imposed surcharge is UNKNOWN, but" not in b4


def test_the_relocation_banner_prints_on_live_runs_and_never_offline(tmp_path, capsys, monkeypatch):
    code, _, live = run_cli(BASE_ARGV, lambda *a: None, capsys, monkeypatch)
    assert "POINTS_OPTIMIZER_CACHE_DIR is set:" in live
    code, _, offline = run_cli(BASE_ARGV + ["--offline"], lambda *a: None, capsys, monkeypatch, key=None)
    assert "is set:" not in offline, "the no-changelog scanner renders --offline output"


def test_a_preset_pythonuserbase_is_kept_and_children_inherit_it(tmp_path):
    probe = tmp_path / "test_ub.py"
    probe.write_text(
        "import os, subprocess, sys\n"
        "def test_ub():\n"
        "    out = subprocess.run([sys.executable, '-c', 'import os;print(os.environ.get(\"PYTHONUSERBASE\"))'],\n"
        "                         capture_output=True, text=True).stdout.strip()\n"
        "    assert out == '/custom/userbase', out\n"
    )
    conf = tmp_path / "conftest.py"
    conf.write_text(f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
                    "from tests.conftest import isolated_environment, no_network_egress  # noqa\n")
    env = dict(os.environ, PYTHONUSERBASE="/custom/userbase")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(probe)], cwd=tmp_path, capture_output=True, text=True,
                          env=env, timeout=120)
    assert proc.returncode == 0, proc.stdout[-800:]


def test_the_session_home_is_removed_at_exit(tmp_path):
    import glob
    import tempfile

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "points-optimizer-test-home-*")))
    probe = tmp_path / "test_nothing.py"
    probe.write_text("def test_nothing():\n    pass\n")
    (tmp_path / "conftest.py").write_text(
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "from tests.conftest import isolated_environment, no_network_egress  # noqa\n")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(probe)], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-500:]
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "points-optimizer-test-home-*")))
    assert after <= before, sorted(after - before)
