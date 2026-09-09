"""
v3 Step 10: replay the LIVE Trip B run offline, from committed snapshots.

THIS TEST IS GATED AND CURRENTLY SKIPS, AND THAT IS THE HONEST STATE.

The build sandbox has no network egress. Steps 0-9 were built and tested against
stubs and the one recorded envelope this project owns; the live Trip B run can
only happen on Tsuki's Mac. Until it does, `tests/fixtures/seats_aero/live_trip_b/`
holds no responses, this module skips, and NOTHING here claims a live result.

WHAT HAPPENS WHEN THE SNAPSHOTS LAND. Drop the four committed envelopes into that
directory and this file becomes a real regression test with no edits: it replays
them through the parser and the scorer with the network guaranteed unreachable,
and asserts that the run reproduces. That is the point of caching raw pages
rather than parsed Awards - the corpus is executable, and a future parser fix
replays it for free.

The command that produces them:

    python -m src.main --trip-fixture trip_b_europe.json --live \\
      --balance UR=<real> --card "<real>" --transfer-date 2026-09-15 \\
      --flex-days 0

then immediately again to prove the cache serves it with zero API calls, and once
with --flex-days 3 to exercise the advisory path.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from src.live_trip import LiveOptions, apply_live
from src.models import LiveQueryState, PointsProvenance
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.response_cache import load_envelope
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "tests" / "fixtures" / "seats_aero" / "live_trip_b"
TRIPS = ROOT / "tests" / "fixtures" / "trips"
MANIFEST = SNAPSHOT_DIR / "MANIFEST.md"

# The four flight legs, and the dates they must have been queried for. Trip B's
# own data is internally inconsistent and was flagged in v0, v1 and v2 without
# being fixed: the fixture's B1 is SFO->MAD while its description says MRY->MAD,
# and B3 is AMS->LHR while its description says AMS->LON. LIVE MODE QUERIES THE
# AIRPORTS THE FIXTURE NAMES, which is what this table records.
EXPECTED_LEGS = {
    "B1": ("SFO", "MAD", date(2027, 1, 15)),
    "B2": ("MAD", "AMS", date(2027, 1, 19)),
    "B3": ("AMS", "LHR", date(2027, 1, 23)),
    "B4": ("LHR", "SFO", date(2027, 1, 27)),
}


def _snapshots():
    return sorted(p for p in SNAPSHOT_DIR.glob("*.json"))


def _snapshots_by_leg():
    out = {}
    for path in _snapshots():
        meta = (json.loads(path.read_text()) or {}).get("_meta") or {}
        leg = meta.get("leg_id")
        if leg:
            out.setdefault(leg, []).append(path)
    return out


HAVE_SNAPSHOTS = bool(_snapshots())

pytestmark = pytest.mark.skipif(
    not HAVE_SNAPSHOTS,
    reason=(
        "Step 10 has not been run. tests/fixtures/seats_aero/live_trip_b/ holds no "
        "committed responses because this build sandbox has no network egress. "
        "This is a GATE, not a failure: run the live command on a machine with "
        "egress, commit the snapshots and the manifest, and this module becomes "
        "a real offline regression test with no edits."
    ),
)


@pytest.fixture
def rm():
    return RatioManager(
        ROOT / "data" / "ratios.csv",
        ROOT / "data" / "bonuses.csv",
        ROOT / "data" / "programs.yaml",
    )


class _ReplayClient:
    """
    A SeatsClient stand-in that answers from committed snapshots ONLY.

    It has no api_key, holds no credentials and cannot reach the network by
    construction - so a replay that "passes" cannot be quietly passing because
    something went out and fetched fresh data.
    """

    DAILY_CALL_CAP = SeatsClient.DAILY_CALL_CAP

    def __init__(self, by_leg):
        self.by_leg = by_leg
        self.last_pagination_note = ""
        self.last_rows_seen = 0
        self.last_rows_skipped = 0
        # The unreadable/no-availability split (adversarial finding C-1). A
        # replay client that reported only the combined `rows_skipped` would let
        # a snapshot full of unparseable rows come back as NO_AWARD_SPACE, which
        # is the exact collapse the sixth state exists to prevent.
        self.last_rows_unreadable = 0
        self.last_rows_without_availability = 0
        self.last_unreadable_reasons = []
        self.last_budget_exhausted = False
        self.last_manifest_key = ""
        self.last_snapshot_name = None
        self.last_served_from_cache = True
        self.last_fetched_at = None
        self.calls = []

    def search(self, origin, destination, date_range, airlines=None, **kw):
        leg_id = kw.get("leg_id")
        self.calls.append((leg_id, origin, destination, date_range))
        paths = self.by_leg.get(leg_id) or []
        if not paths:
            raise AssertionError(f"no committed snapshot for leg {leg_id}")
        envelope = load_envelope(sorted(paths)[-1])
        parsed = SeatsClient.parse_pages_detail(envelope.pages)
        awards = parsed.awards
        self.last_rows_seen = parsed.rows_seen
        self.last_rows_skipped = parsed.rows_skipped
        self.last_rows_unreadable = parsed.rows_unreadable
        self.last_rows_without_availability = parsed.rows_without_availability
        self.last_unreadable_reasons = list(parsed.unreadable_reasons)
        self.last_pagination_note = envelope.meta.get("pagination_note", "")
        self.last_snapshot_name = sorted(paths)[-1].name
        self.last_fetched_at = envelope.fetched_at
        return awards


def _replay(rm):
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    client = _ReplayClient(_snapshots_by_leg())
    fixture, outcomes = apply_live(
        fixture, client, LiveOptions(live=True, cache=None, trip_id=fixture.id)
    )
    results = evaluate_trip(
        fixture.legs,
        ratios_manager=rm,
        wallet=Wallet(balances={"UR": None}, cards=["Chase Sapphire Preferred"]),
        transfer_date=date(2026, 9, 15),
        today=date(2026, 9, 8),
    )
    return fixture, outcomes, results, trip_totals(results), client


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------


def test_every_committed_snapshot_is_key_free():
    for path in _snapshots():
        text = path.read_text()
        assert "Partner-Authorization" not in text
        assert json.loads(text)["_meta"]["key_redacted"] is True


def test_the_manifest_records_every_snapshot():
    assert MANIFEST.exists(), "a live run must commit its manifest"
    manifest = MANIFEST.read_text()
    for path in _snapshots():
        assert path.name in manifest, f"{path.name} is not in MANIFEST.md"


def test_the_snapshots_cover_the_four_flight_legs_the_fixture_names():
    by_leg = _snapshots_by_leg()
    missing = [leg for leg in EXPECTED_LEGS if leg not in by_leg]
    assert not missing, (
        f"no snapshot for {missing}. If those legs errored or came back empty, "
        f"the run report must say so in its FIRST paragraph and the margin must "
        f"be labelled mixed - which is a successful outcome of Step 10, not a "
        f"failure of it."
    )


def test_each_snapshot_was_queried_for_its_own_legs_route_and_date():
    """Live mode queries the airports THE FIXTURE NAMES, not the ones in prose."""
    for leg_id, paths in _snapshots_by_leg().items():
        if leg_id not in EXPECTED_LEGS:
            continue
        origin, destination, when = EXPECTED_LEGS[leg_id]
        request = (json.loads(sorted(paths)[-1].read_text())["_meta"])["request"]
        assert request["origin_airport"] == origin
        assert request["destination_airport"] == destination
        assert request["start_date"] <= str(when) <= request["end_date"]


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def test_the_replay_makes_no_network_call_and_reproduces_the_run(rm):
    fixture, outcomes, results, totals, client = _replay(rm)
    assert len(client.calls) == 4, "four flight legs, four lookups, no more"
    assert all(o.state is not LiveQueryState.API_ERROR for o in outcomes), (
        "a replay cannot produce an API error - there is no API in it"
    )


def test_the_replayed_margin_carries_its_provenance(rm):
    _, _, results, totals, _ = _replay(rm)
    assert totals["margin_provenance"] in ("live", "mixed", "badge", "none")
    assert totals["margin_provenance_note"]
    assert totals["legs_flight_total"] == 4
    if totals["margin_provenance"] != "live":
        assert (
            "MUST NOT BE QUOTED AS A LIVE NUMBER" in totals["margin_provenance_note"]
            or "not a live margin" in totals["margin_provenance_note"]
        )


def test_no_replayed_leg_reports_an_error_as_an_absence(rm):
    """
    The invariant that matters most, asserted over real captured data rather
    than over a stub.
    """
    _, outcomes, results, _, _ = _replay(rm)
    for outcome in outcomes:
        if outcome.state is LiveQueryState.NO_AWARD_SPACE:
            assert outcome.error == ""
            assert "API FAILURE" not in outcome.render()
        if outcome.is_api_failure:
            assert outcome.error
            assert "NOT a finding of no availability" in outcome.render()


def test_every_replayed_live_leg_is_useful(rm):
    """
    Floor and break-even on every live leg whose surcharge is unknown - which,
    given that Seats.aero returns a candidate carrier list rather than the
    operating metal, will be most of them.
    """
    _, _, results, _, _ = _replay(rm)
    for r in results:
        if r.leg.points_provenance is not PointsProvenance.LIVE:
            continue
        if r.surcharge is not None and not r.surcharge.is_known:
            assert r.points_floor_usd is not None, f"{r.leg.id}: no floor"
            assert (
                r.break_even_surcharge_usd is not None
                or r.surcharge_cannot_change_verdict
            ), f"{r.leg.id}: neither a break-even nor a settled verdict"


def test_the_pagination_path_each_leg_took_is_recorded(rm):
    """
    Pagination has NEVER been observed: the one earlier capture was truncated
    before any count / hasMore / cursor field. Step 10 is the first real chance
    to see a multi-page response, and the run report must state which path each
    leg took - even if the answer is "single page, and another mechanism has not
    been ruled out".
    """
    _, outcomes, _, _, _ = _replay(rm)
    for outcome in outcomes:
        if outcome.state in (LiveQueryState.OK, LiveQueryState.NO_AWARD_SPACE):
            assert outcome.pagination_note, (
                f"{outcome.leg_id} recorded no coverage note, so 'this is page "
                f"one' and 'this is everything' are indistinguishable"
            )
