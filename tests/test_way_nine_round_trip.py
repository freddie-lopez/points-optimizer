"""
WAY (9): every invariant-bearing field survives every storage layer.

The v5 adversarial round found that all eight ways to fail to know were enforced
at `LiveLegOutcome` construction time and NONE of them survived a round trip
through storage: `ResponseCache.put` built `_meta` from a hand-written key list
that omitted `incomplete`, and `SnapshotTransport.search_raw` passed
`incomplete=False` literally. The same bytes answered ANSWERED_INCOMPLETE on the
fetch and "THIS IS A FINDING: there is no award to buy on this date" on every
read afterwards.

These tests are about the STRUCTURE of the fix rather than about the one field
that was missing: the persisted set is DERIVED from the fields the invariants
read, so a tenth field cannot be added and silently left out.
"""
import json
from datetime import date, datetime, timezone

import pytest

from src import models, response_cache, seats_client
from src.models import LiveLegOutcome, LiveQueryState, PointsProvenance
from src.response_cache import ResponseCache
from src.seats_client import RawSearchResult, SeatsClient


# ---------------------------------------------------------------------------
# The classification is derived, and it refuses to be incomplete
# ---------------------------------------------------------------------------


def test_the_invariant_fields_are_derived_from_the_invariants_themselves():
    """Not a maintained list: the AST of `__post_init__` is the source."""
    assert models.INVARIANT_FIELDS_DERIVED is True
    for expected in ("result_incomplete", "incomplete_reason", "served_from_cache"):
        assert expected in models.INVARIANT_FIELDS


def test_every_field_an_invariant_reads_is_classified_exactly_once():
    known = {f.name for f in __import__("dataclasses").fields(LiveLegOutcome)}
    declared = (
        set(models.RECOMPUTED_FROM_BYTES)
        | set(models.CARRIED_BY_THE_TRANSPORT)
        | set(models.LOCAL_TO_THIS_RUN)
    )
    assert (models.INVARIANT_FIELDS & known) - declared == set()


def test_a_tenth_invariant_field_that_is_not_classified_is_refused(monkeypatch):
    """
    The structural half. Adding a field to an invariant without saying how it
    is stored makes the module refuse to load - not a review comment, not a
    test somebody may skip.
    """
    monkeypatch.setattr(
        models, "INVARIANT_FIELDS", models.INVARIANT_FIELDS | {"snapshot_name"}
    )
    with pytest.raises(ValueError) as e:
        models._check_way_nine_classification()
    assert "way (9)" in str(e.value)
    assert "snapshot_name" in str(e.value)


def test_the_transport_must_be_able_to_carry_every_persisted_field():
    class Thin:
        pass

    Thin = __import__("dataclasses").dataclass(Thin)
    with pytest.raises(ValueError, match=r"way \(9\)"):
        models.assert_transport_carries(Thin)
    # The real one carries all of them, checked at import of seats_client too.
    models.assert_transport_carries(RawSearchResult)


def test_the_persisted_key_set_is_exactly_the_carried_classification():
    assert set(models.PERSISTED_PROVENANCE_KEYS) == set(
        models.CARRIED_BY_THE_TRANSPORT.values()
    )


# ---------------------------------------------------------------------------
# The disk cache round trip
# ---------------------------------------------------------------------------


def _cache(tmp_path):
    return ResponseCache(
        cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s", ttl_seconds=99999
    )


def test_put_persists_every_invariant_bearing_field(tmp_path):
    cache = _cache(tmp_path)
    written = cache.put(
        "k1",
        {"origin_airport": "LHR", "destination_airport": "SFO",
         "start_date": "2027-01-27", "end_date": "2027-01-27"},
        [{"data": [], "hasMore": True}],
        meta={
            "endpoint": "search",
            "incomplete": True,
            "incomplete_reason": "hasMore with no cursor.",
        },
    )
    for key in models.PERSISTED_PROVENANCE_KEYS:
        assert key in written.meta, f"{key} was dropped on the way to disk"
    hit = cache.get("k1")
    assert hit.meta["incomplete"] is True
    assert hit.meta["incomplete_reason"] == "hasMore with no cursor."
    # ...and the file on disk says so, not just the object in memory.
    envelope = json.loads(written.path.read_text())
    assert envelope["_meta"]["incomplete"] is True


def test_an_envelope_missing_a_persisted_key_is_refused():
    with pytest.raises(ValueError, match=r"way \(9\)"):
        response_cache.assert_persists_provenance(
            {"fetched_at": "2027-01-01T00:00:00Z"}, where="a test envelope"
        )


class _Reply:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture
def truncated_transport(monkeypatch):
    monkeypatch.setattr(
        seats_client.requests, "get", lambda *a, **k: _Reply({"data": [], "hasMore": True})
    )
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient._CALLS = {}
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient._CALLS = {}


def test_a_truncated_fetch_reads_back_truncated_from_the_disk_cache(
    tmp_path, truncated_transport
):
    """The end-to-end shape of way (9): run 1 and run 2 must AGREE."""
    from src.live_trip import LiveOptions, query_leg
    from src.models import Leg

    cache = _cache(tmp_path)
    leg = Leg(id="B4", kind="flight", description="LHR->SFO",
              date=date(2027, 1, 27), origin="LHR", destination="SFO", travelers=1)
    opts = LiveOptions(live=True, cache=cache, trip_id="t")

    first, _ = query_leg(leg, SeatsClient("testkey"), opts)
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    second, _ = query_leg(leg, SeatsClient("testkey"), opts)

    assert second.served_from_cache is True
    assert first.state is LiveQueryState.ANSWERED_INCOMPLETE
    assert second.state is first.state
    assert second.result_incomplete is True
    assert second.incomplete_reason
    assert "THIS IS A FINDING" not in second.render()
    assert "COVERAGE IS INCOMPLETE" in second.render()


def test_the_in_process_cache_is_a_storage_layer_too(tmp_path, truncated_transport):
    client = SeatsClient("testkey")
    from src.models import DateRange

    client.search("LHR", "SFO", DateRange(date(2027, 1, 27), date(2027, 1, 27)))
    assert client.last_incomplete is True
    client.search("LHR", "SFO", DateRange(date(2027, 1, 27), date(2027, 1, 27)))
    assert client.last_served_from_cache is True
    assert client.last_incomplete is True, "the in-process short-circuit dropped it"


# ---------------------------------------------------------------------------
# The snapshot replay round trip
# ---------------------------------------------------------------------------


def _envelope(tmp_path, name, pages, meta=None):
    meta = dict(meta or {})
    meta.setdefault("fetched_at", "2027-01-05T09:00:00Z")
    meta.setdefault("parser_version", seats_client.PARSER_VERSION)
    meta["content_hash"] = response_cache.content_hash(pages)
    path = tmp_path / name
    path.write_text(json.dumps({"_meta": meta, "pages": pages}))
    return path


def _row(tmp_path, name, leg_id="B4"):
    from src import snapshot_replay

    envelope = json.loads((tmp_path / name).read_text())
    return snapshot_replay.ManifestRow(
        fetched_at=datetime(2027, 1, 5, 9, tzinfo=timezone.utc),
        fetched_at_text="2027-01-05T09:00:00Z",
        leg_id=leg_id,
        route="LHR->SFO",
        dates="2027-01-27..2027-01-27",
        rows_seen="1",
        awards="0",
        state="ok",
        snapshot_cell=name,
        snapshot_name=name,
        is_refetch=False,
        content_hash=envelope["_meta"]["content_hash"][:16],
        parser_version=seats_client.PARSER_VERSION,
        trip_id="t",
        line_no=1,
    )


def test_a_replay_restores_the_persisted_coverage(tmp_path):
    from src import snapshot_replay
    from src.models import DateRange

    _envelope(
        tmp_path,
        "B4.json",
        [{"data": [], "hasMore": False}],
        meta={"incomplete": True, "incomplete_reason": "the budget broke here."},
    )
    transport = snapshot_replay.SnapshotTransport(
        [_row(tmp_path, "B4.json")], tmp_path, "mh_test"
    )
    raw = transport.search_raw(
        "LHR", "SFO", DateRange(date(2027, 1, 27), date(2027, 1, 27)), leg_id="B4"
    )
    assert raw.incomplete is True
    assert "budget broke" in raw.incomplete_reason


def test_a_replay_recomputes_coverage_from_bytes_that_never_stored_it(tmp_path):
    """
    A snapshot written before way (9) carries no coverage keys at all. Its own
    pages still say `hasMore` with no cursor, and that is not allowed to read as
    a complete answer just because nobody wrote the flag down.
    """
    from src import snapshot_replay
    from src.models import DateRange

    _envelope(tmp_path, "B4.json", [{"data": [], "hasMore": True}])
    transport = snapshot_replay.SnapshotTransport(
        [_row(tmp_path, "B4.json")], tmp_path, "mh_test"
    )
    raw = transport.search_raw(
        "LHR", "SFO", DateRange(date(2027, 1, 27), date(2027, 1, 27)), leg_id="B4"
    )
    assert raw.incomplete is True
    assert "RECOMPUTED FROM THE STORED BYTES" in raw.incomplete_reason


def test_coverage_of_pages_is_a_function_of_the_bytes():
    assert seats_client.coverage_of_pages([{"data": []}])[0] is False
    assert seats_client.coverage_of_pages([{"data": [], "hasMore": False}])[0] is False
    assert seats_client.coverage_of_pages([{"data": [], "hasMore": True}])[0] is True
    assert seats_client.coverage_of_pages(
        [{"data": [], "hasMore": True, "cursor": "abc"}]
    )[0] is True
    # No pages at all is not an answer about coverage either way.
    assert seats_client.coverage_of_pages([]) == (False, "")


def test_the_outcome_built_from_a_replayed_truncation_cannot_be_a_finding():
    """The construction-time invariant and the storage layer now agree."""
    with pytest.raises(ValueError, match="INCOMPLETE result set"):
        LiveLegOutcome(
            leg_id="B4",
            state=LiveQueryState.NO_AWARD_SPACE,
            provenance=PointsProvenance.SNAPSHOT,
            result_incomplete=True,
            incomplete_reason="replayed truncation.",
            replayed_from_snapshot=True,
            snapshot_content_hash="a" * 64,
        )
