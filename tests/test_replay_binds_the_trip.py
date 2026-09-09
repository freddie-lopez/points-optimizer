"""
H-1: the manifest hash binds the ITINERARY, not just the leg id.

`SnapshotTransport.search_raw` took origin, destination and date_range and
discarded all three; `verify_covers_legs` compared leg ids only. So editing
B1's origin scored MRY->MAD out of `B1_SFO_MAD_2027-01-15.json` with exit 0 and
the SAME 16 hex characters the unedited fixture printed - one certificate, two
itineraries. A row whose trip_id was blanked to `-` matched every trip.
"""
from datetime import date, datetime, timezone

import pytest

from src import snapshot_replay
from src.models import DateRange
from src.seats_client import SeatsAeroError


def _row(**over):
    base = dict(
        fetched_at=datetime(2027, 1, 5, 9, tzinfo=timezone.utc),
        fetched_at_text="2027-01-05T09:00:00Z",
        leg_id="B1",
        route="SFO->MAD",
        dates="2027-01-15..2027-01-15",
        rows_seen="1",
        awards="1",
        state="ok",
        snapshot_cell="B1.json",
        snapshot_name="B1.json",
        is_refetch=False,
        content_hash="a" * 16,
        parser_version="v",
        trip_id="trip_b_europe",
        line_no=1,
    )
    base.update(over)
    return snapshot_replay.ManifestRow(**base)


def test_a_route_column_is_read_and_compared():
    row = _row()
    assert row.route_pair == ("SFO", "MAD")
    assert row.covers("SFO", "MAD", date(2027, 1, 15)) == ""
    assert "different route" in row.covers("MRY", "MAD", date(2027, 1, 15))
    assert "another date" in row.covers("SFO", "MAD", date(2027, 1, 16))


def test_a_flex_capture_still_covers_a_date_inside_its_span():
    row = _row(dates="2027-01-12..2027-01-18")
    assert row.covers("SFO", "MAD", date(2027, 1, 15)) == ""
    assert row.covers("SFO", "MAD", date(2027, 1, 19)) != ""


def test_an_unreadable_route_column_is_unknown_and_never_a_match():
    assert "UNKNOWN" in _row(route="???").covers("SFO", "MAD", date(2027, 1, 15))


def test_verify_covers_legs_refuses_a_row_for_another_route():
    problems = snapshot_replay.verify_covers_legs(
        [_row()],
        [snapshot_replay.LegQuery("B1", "MRY", "MAD", date(2027, 1, 15))],
    )
    assert [p.kind for p in problems] == ["row_does_not_match_the_leg"]


def test_verify_covers_legs_accepts_the_row_that_answers_the_question():
    assert (
        snapshot_replay.verify_covers_legs(
            [_row()],
            [snapshot_replay.LegQuery("B1", "SFO", "MAD", date(2027, 1, 15))],
        )
        == []
    )


def test_an_untagged_row_no_longer_matches_every_trip():
    selection = snapshot_replay.select_replay_set([_row(trip_id=None)], "trip_b_europe")
    assert [p.kind for p in selection.problems] == ["trip_id_unknown"]


def test_an_untagged_row_is_fine_when_no_trip_is_being_matched():
    selection = snapshot_replay.select_replay_set([_row(trip_id=None)], None)
    assert selection.problems == []


def test_the_hash_covers_the_itinerary_that_was_scored(tmp_path):
    import json

    from src import response_cache

    pages = [{"data": []}]
    (tmp_path / "B1.json").write_text(
        json.dumps({"_meta": {"content_hash": response_cache.content_hash(pages)},
                    "pages": pages})
    )
    rows = [_row()]
    sfo = snapshot_replay.manifest_hash(
        rows, tmp_path,
        itinerary=[snapshot_replay.LegQuery("B1", "SFO", "MAD", date(2027, 1, 15))],
        trip_id="trip_b_europe",
    )
    mry = snapshot_replay.manifest_hash(
        rows, tmp_path,
        itinerary=[snapshot_replay.LegQuery("B1", "MRY", "MAD", date(2027, 1, 15))],
        trip_id="trip_b_europe",
    )
    other_trip = snapshot_replay.manifest_hash(
        rows, tmp_path,
        itinerary=[snapshot_replay.LegQuery("B1", "SFO", "MAD", date(2027, 1, 15))],
        trip_id="another_trip",
    )
    assert len({sfo, mry, other_trip}) == 3, "one certificate covered several trips"


def test_the_transport_refuses_to_answer_a_question_it_was_not_asked(tmp_path):
    """Defence in depth: even with verification skipped, no wrong-route answer."""
    import json

    from src import response_cache

    pages = [{"data": []}]
    (tmp_path / "B1.json").write_text(
        json.dumps({"_meta": {"content_hash": response_cache.content_hash(pages)},
                    "pages": pages})
    )
    transport = snapshot_replay.SnapshotTransport([_row()], tmp_path, "mh_x")
    with pytest.raises(SeatsAeroError, match="does not answer this leg's question"):
        transport.search_raw(
            "MRY", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)), leg_id="B1"
        )
