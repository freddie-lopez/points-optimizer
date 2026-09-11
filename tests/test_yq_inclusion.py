"""
data/yq_inclusion.csv and the cash rule it drives (cases A-E).

The committed table is header-only, so every source is UNVERIFIED and no score
moves. Every other test here builds a temporary table (or passes verdicts
directly on LiveOptions) and never touches the committed one.
"""
import copy
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src import yq_inclusion
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet
from src.yq_inclusion import YqInclusionError, YqVerdict
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import B4_ID, REAL, Stub, render, vs_row
from tests._trips_label_state import unverified_constants  # noqa: F401 - pins the label constants

ROOT = Path(__file__).parent.parent
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"
EVIDENCE = "docs/yq-checks/2026-09-10-virginatlantic.md"
FB_ID = "B1flyingblueAAAAAAAAAAAAAA1"


def verdict(source="virginatlantic", which="includes_yq", evidence=EVIDENCE):
    return {source: YqVerdict(source, which, date(2026, 9, 10), evidence)}


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv", ROOT / "data" / "programs.yaml"
    )


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


class FBStub(Stub):
    """B1 SFO->MAD becomes a Flying Blue J award; B4 stays Virgin Atlantic."""

    def __init__(self, fb_segments, airlines="AF, KL, DL", **kw):
        super().__init__(**kw)
        self.trips_payloads[FB_ID] = tp.payload([tp.trip(
            fb_segments, availability_id=FB_ID, source="flyingblue", cost=55000,
        )])
        self.airlines = airlines

    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "SFO":
            self.calls.append(url)
            row = copy.deepcopy(REAL["data"][0])
            row["ID"] = FB_ID
            row["Route"].update(Source="flyingblue")
            row.update(
                YAvailable=False, JAvailable=True, JMileageCost="55000",
                JTotalTaxes=20000, TaxesCurrency="USD", JAirlines=self.airlines,
                JRemainingSeats=2,
            )
            from unittest.mock import MagicMock

            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"data": [row]}
            r.raise_for_status.return_value = None
            return r
        return super().__call__(url, **kwargs)


def scored(rm, table, trips_mode="auto", stub=None):
    # The in-process award cache is keyed by route, so a second run in one test
    # would otherwise re-use the first run's search results.
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    fixture = load_trip_fixture(TRIP_B)
    opts = LiveOptions(
        live=True, cache=None, trip_id=fixture.id, trips_mode=trips_mode, yq_table=table
    )
    with patch("src.seats_client.requests.get", side_effect=stub or Stub()):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    results = evaluate_trip(
        fixture.legs, ratios_manager=rm,
        wallet=Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"]),
        transfer_date=date(2026, 9, 15), today=date(2026, 9, 8),
    )
    return {r.leg.id: r for r in results}, trip_totals(results)


def codes(result):
    return [x.code for x in result.reasons]


# ---------------------------------------------------------------------------
# The committed table
# ---------------------------------------------------------------------------


def test_the_committed_table_is_header_only():
    text = (ROOT / "data" / "yq_inclusion.csv").read_text()
    assert text.strip() == "source,verdict,verified_on,evidence,notes"
    assert yq_inclusion.load() == {}


def test_with_the_committed_table_b4_is_unscoreable_and_the_band_is_named_not_added(rm):
    legs, _ = scored(rm, None)
    b4 = legs["B4"]
    assert b4.points_total_score_usd == float("inf")
    assert "SURCHARGE_UNKNOWN" in codes(b4)
    note = b4.best_points.metal_surcharge_note
    assert "$200-$350 (pt $275)" in note
    assert "NOT ADDED" in note and "UNVERIFIED" in note
    assert "NOT ADDED" in render(list(legs.values()))
    # The surcharge note says why the named metal is not used, not that none is known.
    assert "names VS by flight number" in b4.surcharge.notes
    assert "no operating carrier is recorded" not in b4.surcharge.notes


# ---------------------------------------------------------------------------
# B: includes_yq
# ---------------------------------------------------------------------------


def test_includes_yq_scores_at_taxes_only_and_names_the_evidence(rm):
    legs, _ = scored(rm, verdict(which="includes_yq"))
    b4 = legs["B4"]
    cand = b4.best_points
    assert b4.points_total_score_usd != float("inf")
    assert b4.surcharge.confidence == "captured"
    assert b4.surcharge.amount_point == pytest.approx(cand.observed_taxes_usd)
    assert b4.points_total_score_usd == pytest.approx(
        b4.funding_plan.score_usd + cand.observed_taxes_usd
    ), "the taxes, once, and no band"
    assert "SURCHARGE_CAPTURED" in codes(b4)
    assert cand.observed_taxes_whole_because == f"yq_included_verified:{EVIDENCE}"
    assert cand.metal_surcharge_note == ""
    out = render(list(legs.values()))
    assert f"VERIFIED to include carrier-imposed surcharges (evidence: {EVIDENCE})" in out


def test_includes_yq_does_not_need_the_metal(rm):
    legs, _ = scored(rm, verdict(which="includes_yq"), trips_mode="off")
    assert legs["B4"].points_total_score_usd != float("inf")


# ---------------------------------------------------------------------------
# C and D: excludes_yq
# ---------------------------------------------------------------------------


def test_excludes_yq_with_known_vs_adds_the_halved_band_to_the_taxes(rm):
    legs, _ = scored(rm, verdict(which="excludes_yq"))
    b4 = legs["B4"]
    cand = b4.best_points
    assert cand.carrier_source == "seats_aero_trips"
    assert cand.has_known_metal
    assert b4.surcharge.is_known and b4.surcharge.confidence == "sourced"
    assert (b4.surcharge.amount_low, b4.surcharge.amount_point, b4.surcharge.amount_high) == (
        200.0, 275.0, 350.0
    )
    base = b4.funding_plan.score_usd + cand.observed_taxes_usd
    assert b4.points_total_score_usd == pytest.approx(base + 275.0)
    assert b4.points_score_low_usd == pytest.approx(base + 200.0)
    assert b4.points_score_high_usd == pytest.approx(base + 350.0)
    assert "SURCHARGE_MODELED" in codes(b4)
    assert not cand.observed_taxes_are_the_surcharge


@pytest.mark.parametrize(
    "trips_mode,stub",
    [("off", None), ("auto", Stub(trips_status={B4_ID: 404}))],
    ids=["not_looked_up", "unknown"],
)
def test_excludes_yq_without_known_metal_is_unscoreable_surcharge_unknown(rm, trips_mode, stub):
    legs, _ = scored(rm, verdict(which="excludes_yq"), trips_mode=trips_mode, stub=stub)
    b4 = legs["B4"]
    assert b4.points_total_score_usd == float("inf")
    assert "SURCHARGE_UNKNOWN" in codes(b4)
    assert "TAXES_UNKNOWN" not in codes(b4)
    assert "VERIFIED to EXCLUDE" in b4.best_points.source_note


@pytest.mark.usefixtures("unverified_constants")
def test_flying_blue_on_an_af_kl_itinerary_resolves_together(rm):
    stub = FBStub([tp.segment("KL606", "SFO", "AMS", 1), tp.segment("AF1401", "AMS", "MAD", 2)])
    legs, _ = scored(rm, verdict("flyingblue", "excludes_yq"), stub=stub)
    b1 = legs["B1"]
    metal = b1.best_points.metal
    assert metal.status is MetalStatus.KNOWN and set(metal.carriers) == {"KL", "AF"}
    assert b1.surcharge.is_known
    assert (b1.surcharge.amount_low, b1.surcharge.amount_high) == (75.0, 125.0)
    assert "OPERATING METAL AMBIGUOUS" in b1.surcharge.notes


@pytest.mark.usefixtures("unverified_constants")
def test_flying_blue_ambiguous_af_or_kl_resolves_too(rm):
    stub = FBStub([tp.segment("KL606", "SFO", "MAD", 1)])
    stub.trips_payloads[FB_ID]["data"].append(tp.trip(
        [tp.segment("AF84", "SFO", "MAD", 1)], trip_id="t2", availability_id=FB_ID,
        source="flyingblue", cost=55000,
    ))
    legs, _ = scored(rm, verdict("flyingblue", "excludes_yq"), stub=stub)
    b1 = legs["B1"]
    assert b1.best_points.metal.status is MetalStatus.AMBIGUOUS
    assert b1.surcharge.is_known


@pytest.mark.usefixtures("unverified_constants")
def test_flying_blue_on_af_plus_dl_does_not_resolve(rm):
    stub = FBStub([tp.segment("DL41", "SFO", "AMS", 1), tp.segment("AF1401", "AMS", "MAD", 2)])
    legs, _ = scored(rm, verdict("flyingblue", "excludes_yq"), stub=stub)
    b1 = legs["B1"]
    assert b1.best_points.metal.status is MetalStatus.KNOWN
    assert not b1.surcharge.is_known
    assert b1.points_total_score_usd == float("inf")
    assert "SURCHARGE_UNKNOWN" in codes(b1)


# ---------------------------------------------------------------------------
# Untrusted taxes win over every verdict
# ---------------------------------------------------------------------------


class TaxStub(Stub):
    def __init__(self, cents, **kw):
        super().__init__(**kw)
        self.cents = cents

    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            row = vs_row()
            row["JTotalTaxes"] = self.cents
            r.json.return_value = {"data": [row]}
        return r


@pytest.mark.parametrize("which", ["includes_yq", "excludes_yq"])
@pytest.mark.parametrize("cents", [0, 500], ids=["zero_means_not_reported", "below_uk_duty"])
def test_untrusted_taxes_stay_unscoreable_under_both_verdicts(rm, which, cents):
    legs, _ = scored(rm, verdict(which=which), stub=TaxStub(cents))
    b4 = legs["B4"]
    assert b4.points_total_score_usd == float("inf")
    assert "TAXES_UNKNOWN" in codes(b4)
    assert not b4.best_points.observed_taxes_are_the_surcharge


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------

GOOD_RECORD = (
    "# yq-check record: virginatlantic, 2026-09-10\n\n## Seats.aero\n\n"
    "- program: Virgin Atlantic Flying Club (source virginatlantic)\n\n"
    "## virginatlantic.com\n\n- taxes, fees and carrier-imposed charges for ONE adult: "
    "GBP 450.00\n- verdict (includes_yq / excludes_yq / inconclusive): includes_yq\n"
)


def _root(tmp_path, record=GOOD_RECORD, name="2026-09-10-virginatlantic.md"):
    (tmp_path / "docs" / "yq-checks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "yq-checks" / name).write_text(record)
    return tmp_path


def _table(tmp_path, *rows, header="source,verdict,verified_on,evidence,notes"):
    path = tmp_path / "yq.csv"
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def _load(tmp_path, *rows, **kw):
    return yq_inclusion.load(_table(tmp_path, *rows, **kw), today=date(2026, 9, 11), root=tmp_path)


def test_a_valid_row_loads(tmp_path):
    _root(tmp_path)
    table = _load(tmp_path, f"virginatlantic,includes_yq,2026-09-10,{EVIDENCE},JFK-LHR J")
    assert table["virginatlantic"].includes
    assert table["virginatlantic"].notes == "JFK-LHR J"


BAD = {
    "not_a_source": f"britishairways,includes_yq,2026-09-10,{EVIDENCE},",
    "unreported_source": f"qatar,includes_yq,2026-09-10,{EVIDENCE},",
    "verdict_unverified": f"virginatlantic,unverified,2026-09-10,{EVIDENCE},",
    "verdict_misspelt": f"virginatlantic,include_yq,2026-09-10,{EVIDENCE},",
    "future_date": f"virginatlantic,includes_yq,2026-09-12,{EVIDENCE},",
    "unparseable_date": f"virginatlantic,includes_yq,10/09/2026,{EVIDENCE},",
    "evidence_missing": "virginatlantic,includes_yq,2026-09-10,docs/yq-checks/nope.md,",
    "evidence_blank": "virginatlantic,includes_yq,2026-09-10,,",
    "evidence_outside": "virginatlantic,includes_yq,2026-09-10,README.md,",
    "evidence_absolute": "virginatlantic,includes_yq,2026-09-10,/etc/passwd,",
    "evidence_dotdot": "virginatlantic,includes_yq,2026-09-10,docs/yq-checks/../../README.md,",
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_the_validator_refuses_a_bad_row(tmp_path, name):
    _root(tmp_path)
    (tmp_path / "README.md").write_text("yq-check record")
    with pytest.raises(YqInclusionError):
        _load(tmp_path, BAD[name])


def test_the_validator_refuses_a_duplicate_source(tmp_path):
    _root(tmp_path)
    row = f"virginatlantic,includes_yq,2026-09-10,{EVIDENCE},"
    with pytest.raises(YqInclusionError, match="twice"):
        _load(tmp_path, row, row)


def test_the_validator_refuses_a_record_without_the_marker(tmp_path):
    _root(tmp_path, record=GOOD_RECORD.replace("yq-check record", "notes"))
    with pytest.raises(YqInclusionError, match="marker"):
        _load(tmp_path, f"virginatlantic,includes_yq,2026-09-10,{EVIDENCE},")


def test_the_validator_refuses_a_record_with_blanks(tmp_path):
    _root(tmp_path, record=GOOD_RECORD.replace("GBP 450.00", "____"))
    with pytest.raises(YqInclusionError, match="blanks"):
        _load(tmp_path, f"virginatlantic,includes_yq,2026-09-10,{EVIDENCE},")


def test_the_validator_refuses_a_wrong_header(tmp_path):
    _root(tmp_path)
    with pytest.raises(YqInclusionError, match="columns"):
        _load(tmp_path, header="source,verdict,verified_on,evidence")


def test_a_missing_table_is_an_error_not_an_empty_table(tmp_path):
    with pytest.raises(YqInclusionError):
        yq_inclusion.load(tmp_path / "absent.csv")


def test_a_bad_committed_table_stops_a_live_run_with_exit_1(tmp_path, monkeypatch, capsys):
    bad = _table(tmp_path, "qatar,includes_yq,2026-09-10,docs/yq-checks/x.md,")
    monkeypatch.setattr(yq_inclusion, "YQ_TABLE_PATH", bad)
    monkeypatch.setenv("SEATS_AERO_KEY", "k_test_000000000000")
    from tests.test_from_snapshot import run_cli

    with patch("src.seats_client.requests.get", side_effect=Stub()):
        code, out = run_cli(
            ["--trip-fixture", "trip_b_europe.json", "--balance", "UR=160000",
             "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
            capsys,
        )
    assert code == 1
    assert "qatar" in out
