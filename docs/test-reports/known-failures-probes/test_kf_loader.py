"""
Tester probes: a file that is not a trip is ONE clean line, exit 1, no traceback.

Each case runs the real CLI as a child (the harness's child guard and env apply).
"""
import json
import subprocess
import sys

import pytest

from conftest import ROOT

LEG = {"id": "L1", "kind": "flight", "description": "d", "date": "2027-01-01",
       "cash_options": [{"label": "a", "amount": 100}]}


def _leg(**over):
    leg = json.loads(json.dumps(LEG))
    for k, v in over.items():
        if v is KeyError:
            leg.pop(k, None)
        else:
            leg[k] = v
    return leg


def _trip(*legs, **top):
    return dict({"id": "x", "legs": list(legs)}, **top)


CLEAN = {
    "malformed": b'{"id": "x", "legs": [',
    "empty": b"",
    "top_list": b"[1, 2, 3]",
    "top_string": b'"a trip"',
    "top_null": b"null",
    "legs_dict": json.dumps({"id": "x", "legs": {"a": 1}}).encode(),
    "legs_string": json.dumps({"id": "x", "legs": "abc"}).encode(),
    "legs_null": json.dumps({"id": "x", "legs": None}).encode(),
    "leg_is_int": json.dumps({"id": "x", "legs": [1]}).encode(),
    "leg_is_null": json.dumps({"id": "x", "legs": [None]}).encode(),
    "no_id": json.dumps({"legs": []}).encode(),
    "leg_no_id": json.dumps(_trip(_leg(id=KeyError))).encode(),
    "leg_no_kind": json.dumps(_trip(_leg(kind=KeyError))).encode(),
    "leg_no_description": json.dumps(_trip(_leg(description=KeyError))).encode(),
    "leg_no_date": json.dumps(_trip(_leg(date=KeyError))).encode(),
    "leg_bad_date": json.dumps(_trip(_leg(date="2027-13-45"))).encode(),
    "leg_int_date": json.dumps(_trip(_leg(date=20270101))).encode(),
    "leg_null_date": json.dumps(_trip(_leg(date=None))).encode(),
    "cash_no_amount": json.dumps(_trip(_leg(cash_options=[{"label": "a"}]))).encode(),
    "cash_str_amount": json.dumps(_trip(_leg(cash_options=[{"label": "a", "amount": "x"}]))).encode(),
    "cash_options_str": json.dumps(_trip(_leg(cash_options="nope"))).encode(),
    "cand_no_program": json.dumps(_trip(_leg(points_candidates=[{"label": "p"}]))).encode(),
    "cand_str_points": json.dumps(_trip(_leg(points_candidates=[
        {"label": "p", "program": "United MileagePlus", "points": "lots"}]))).encode(),
    "fee_no_amount": json.dumps(_trip(_leg(mandatory_fees=[{"label": "f"}]))).encode(),
    "travelers_str": json.dumps(_trip(_leg(travelers="two"))).encode(),
    "answer_file": (ROOT / "tests" / "fixtures" / "trips" / "trip_001_answer.json").read_bytes(),
    "unicode_name_☃": b'{"id": "x", "legs": [',
}


def _run(path):
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", str(path), "--offline",
         "--balance", "UR=1000", "--card", "Chase Sapphire Preferred"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_a_bad_fixture_is_one_clean_line_naming_the_file(name, tmp_path):
    path = tmp_path / f"{name}.json"
    path.write_bytes(CLEAN[name])
    code, out = _run(path)
    assert "Traceback" not in out, out[-800:]
    assert code == 1, out[-800:]
    errors = [l for l in out.splitlines() if l.startswith("Error")]
    assert len(errors) == 1, out[-800:]
    assert path.name in errors[0], errors[0]


# ===========================================================================
# RED
# ===========================================================================


def test_a_directory_passed_as_the_fixture_is_not_a_traceback(tmp_path):
    d = tmp_path / "trip.json"
    d.mkdir()
    code, out = _run(d)
    assert "Traceback" not in out, out[-600:]
    assert code == 1


def test_non_utf8_bytes_name_the_file(tmp_path):
    p = tmp_path / "binary_trip.json"
    p.write_bytes(b"\xff\xfe\x00bin")
    code, out = _run(p)
    assert code == 1 and "Traceback" not in out
    assert "binary_trip.json" in out, out[-400:]


@pytest.mark.parametrize("name,leg", [
    ("currency_is_a_number", _leg(cash_options=[{"label": "a", "amount": 100, "currency": 5}])),
    ("amount_overflows_to_inf", None),
])
def test_a_loadable_fixture_with_a_bad_value_does_not_traceback_at_scoring(name, leg, tmp_path):
    p = tmp_path / f"{name}.json"
    if leg is None:
        # 1e400 parses as float('inf'); the loader accepts it, scoring crashes.
        p.write_text('{"id": "x", "legs": [{"id": "L1", "kind": "flight", '
                     '"description": "d", "date": "2027-01-01", '
                     '"cash_options": [{"label": "a", "amount": 1e400}]}]}')
    else:
        p.write_text(json.dumps(_trip(leg)))
    code, out = _run(p)
    assert "Traceback" not in out, out[-600:]
    assert code == 1


def test_a_malformed_cash_option_date_is_refused_not_read_as_the_legs_own_date(tmp_path):
    """
    PRE-EXISTING. `_maybe_date` turns 'tomorrow' (or '2027-02-30') into None,
    and None on a cash option MEANS 'the leg's own date' - so a fare captured
    for some other date is scored as the own-date fare, silently. H-5's shape
    through the loader.
    """
    p = tmp_path / "bad_cash_date.json"
    p.write_text(json.dumps(_trip(_leg(cash_options=[
        {"label": "a", "amount": 100, "date": "2027-02-30"}]))))
    code, out = _run(p)
    assert code == 1, "a malformed date was accepted and read as the leg's own date"
