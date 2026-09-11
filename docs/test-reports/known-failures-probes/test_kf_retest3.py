"""
Re-test 3: a0b30b3 (fixes for Re-test 2). Can a party leg still produce a
quotable number? Does the new guard order put a mixed leg on the wrong verdict?

RED = defect present. Runs under the suite's harness (no key, no real state, no
network); every transport is a stub or a tmp corpus.
"""
import json
import re

import pytest

from conftest import CSP, TRIP_B, UNSET, evaluate, flat, only, row, run_cli

B1 = ("SFO", "MAD")
B4 = ("LHR", "SFO")
PARTY = "cash (multi-traveller points not priced)"
UA = dict(source="united", cost="50000", taxes=5600, currency="USD", airlines="UA")


def _mut(leg_id, **over):
    def mut(fx):
        for leg in fx.legs:
            if leg.id == leg_id:
                for k, v in over.items():
                    setattr(leg, k, v)
    return mut


def _trip_b_file(tmp_path, **per_leg):
    data = json.loads(TRIP_B.read_text())
    for leg in data["legs"]:
        leg.update(per_leg.get(leg["id"], {}))
    p = tmp_path / "trip_b_europe.json"
    p.write_text(json.dumps(data))
    return p


def _argv(path, *extra):
    return ["--trip-fixture", str(path), "--balance", "UR=160000", "--card", CSP,
            "--transfer-date", "2026-09-15", *extra]


# ===========================================================================
# RED
# ===========================================================================


def test_R3_1_a_withheld_party_trip_prints_no_dollar_saving(tmp_path, capsys, monkeypatch):
    """
    R2-5's fix withholds the PERCENTAGE (WITHHELD, exit 3), but the same table
    still prints 'Optimizer's recommendation $X' and 'Saving $0.00' above it -
    the withheld finding in dollars instead of percent. (The same rows print on
    a provenance-withheld run, which predates this branch; for a party trip the
    zero is exactly the 'could not price reported as zero value' R2-5 is about.)
    """
    p = _trip_b_file(tmp_path, B1={"travelers": 2}, B4={"travelers": 2})
    code, _, text = run_cli(_argv(p), only(B1, [row(**UA)]), capsys, monkeypatch)
    assert code == 3 and "WITHHELD" in text, "precondition"
    assert not re.search(r"Saving │ \$", text), "a dollar saving is quoted beside WITHHELD"


def test_R3_2_both_withholding_reasons_are_named(tmp_path, capsys, monkeypatch):
    """
    A trip withheld for TWO reasons - B2's live query failed (provenance mixed)
    AND B1 is for 2 travellers - names only the party reason: `margin_withheld_
    reason` replaces the provenance sentence in the 'withheld because' row and
    in the closing message. The API failure disappears from the trip block.
    """
    def rows_for(o, d, i):
        if (o, d) == ("MAD", "AMS"):
            raise __import__("requests").exceptions.ConnectionError("stub outage")
        return None

    p = _trip_b_file(tmp_path, B1={"travelers": 2})
    code, _, text = run_cli(_argv(p), rows_for, capsys, monkeypatch)
    assert code == 3
    assert "for 2+ travellers" in text, "precondition: the party reason is named"
    tail = text.split("THE TRIP MARGIN IS WITHHELD")[-1]
    assert "provenance" in tail or "did not come back live" in tail or "require-all-live" in tail, (
        "the live-data reason for withholding is no longer stated at trip level"
    )


def test_R3_3_a_party_leg_whose_only_unpriced_program_is_not_a_partner_is_not_withheld(tmp_path):
    """
    `elif party_leg and (party_candidates or leg.unpriced_partner_programs)`
    reads ALL unpriced programs, not the reachable ones. A 2-traveller leg whose
    only listed program is Delta SkyMiles (no UR path) gets the party verdict and
    PARTY_PRICING_UNVERIFIED - so the whole trip is withheld (exit 3) for a leg
    with nothing to price. Nothing here depends on the party size.
    """
    res, totals, _ = evaluate(
        lambda *a: [], tmp_path,
        fixture_mutator=_mut("B1", travelers=2, points_candidates=[],
                             unpriced_partner_programs=["Delta SkyMiles"]),
    )
    assert "B1" not in totals["legs_party_pricing_unverified_ids"]


# ===========================================================================
# GREEN
# ===========================================================================


def test_a_party_leg_mixing_reachable_indirect_and_unattributed_is_party_with_every_count(tmp_path):
    res, totals, _ = evaluate(
        only(B4, lambda o, d, i: [
            row(origin=o, dest=d, iso=i, rid="ua", **UA),
            row(source="qatar", cost="33000", taxes=0, currency="USD",
                airlines="QR", origin=o, dest=d, iso=i, rid="q"),
            row(source="", cost="25000", taxes=5600, currency="USD",
                airlines="UA", origin=o, dest=d, iso=i, rid="u"),
            row(source="american", cost="20000", taxes=5600, currency="USD",
                airlines="AA", origin=o, dest=d, iso=i, rid="aa"),
        ]),
        tmp_path, fixture_mutator=_mut("B4", travelers=2),
    )
    b4 = res["B4"]
    assert b4.verdict == PARTY
    assert b4.points_floor_usd is None and b4.best_points is None
    assert totals["legs_party_pricing_unverified_ids"] == ["B4"]
    assert totals["legs_indirect_path_unverified_ids"] == ["B4"]
    assert totals["legs_award_unattributed_ids"] == ["B4"]
    assert "B4" not in totals["legs_no_partner_ids"]


def test_a_party_leg_with_only_a_non_partner_award_is_no_path_and_not_withheld(tmp_path):
    res, totals, _ = evaluate(
        only(B4, [row(source="american", cost="20000", taxes=5600, currency="USD",
                      airlines="AA", origin="LHR", dest="SFO", iso="2027-01-27")]),
        tmp_path, fixture_mutator=_mut("B4", travelers=2),
    )
    assert res["B4"].verdict == "cash (no points path)"
    assert totals["legs_party_pricing_unverified"] == 0


@pytest.mark.parametrize("extra", [[], ["--allow-badge-fallback"], ["--offline"]])
def test_every_mode_withholds_a_party_trip(extra, tmp_path, capsys, monkeypatch):
    p = _trip_b_file(tmp_path, B2={"travelers": 2})
    key = None if "--offline" in extra else "test_key_not_a_real_one"
    code, raw, text = run_cli(_argv(p, *extra), only(("MAD", "AMS"), [row(
        source="united", cost="7500", taxes=2000, currency="USD", airlines="UA",
        origin="MAD", dest="AMS", iso="2027-01-19")]), capsys, monkeypatch, key=key)
    assert code == 3
    assert re.search(r"Optimizer beats paying cash by │ WITHHELD", text)
    assert not re.search(r"beats paying cash by │ [0-9]", text)
    assert "high end" not in text and "low end" not in text


def test_replay_withholds_a_party_trip(tmp_path, capsys, monkeypatch):
    from docs_v5 import build_corpus  # type: ignore

    manifest, _ = build_corpus(tmp_path / "corpus")
    p = _trip_b_file(tmp_path, B3={"travelers": 2})
    code, _, text = run_cli(_argv(p, "--from-snapshot", str(manifest)),
                            lambda *a: None, capsys, monkeypatch, key=None)
    assert code == 3
    assert re.search(r"beats paying cash by │ WITHHELD", text)
    assert not re.search(r"\(snapshot mh_[0-9a-f]+\)", text.split("WITHHELD")[0][-400:])


@pytest.mark.parametrize("value,ok", [
    (1, True), (2, True), ("2", True), (0, False), (-1, False), ("two", False),
    (2.0, False), (True, False), (None, False), ("", False),
])
def test_the_loader_accepts_only_a_whole_traveller_count(value, ok, tmp_path):
    from src.trip_loader import TripFixtureError, load_trip_fixture

    p = _trip_b_file(tmp_path, B1={"travelers": value})
    if ok:
        assert load_trip_fixture(p).legs[0].travelers == int(value)
    else:
        with pytest.raises(TripFixtureError):
            load_trip_fixture(p)


def test_search_keeps_a_cheaper_unknown_award_after_a_known_one(capsys, monkeypatch):
    argv = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
            "--balance", "UR=160000", "--card", CSP]
    rows = [row(**UA, rid="a"),
            row(source="united", cost="30000", taxes=UNSET, currency="USD",
                airlines="UA", rid="b"),
            row(source="united", cost="31000", taxes=UNSET, currency="USD",
                airlines="UA", rid="c")]
    code, raw, text = run_cli(argv, lambda *_: rows, capsys, monkeypatch)
    ranks = [l for l in raw.splitlines() if re.match(r"^│\s*\d\s", l)]
    assert "50,000" in ranks[0] and "$56.00" in ranks[0]
    assert any("30,000" in l and "UNKNOWN" in l for l in ranks)
    assert not any("31,000" in l for l in ranks), "unknown vs unknown still dedups"
    assert "Top strategy cash cost: $56.00" in text
