"""
Manager review, should-fix 1: `--trips auto` does not spend a lookup on an
award whose source Seats.aero reports no taxes for (KrisFlyer `singapore`, and
`qatar`, `turkish`). Its taxes are never believed and the YQ loader refuses a
row for it, so no lookup can move its number. It reads NOT_LOOKED_UP /
NOT_NEEDED_TAXES_UNREPORTED, is not counted as a missing lookup, and
`--trips all` still looks it up.
"""
import dataclasses
import json
from unittest.mock import MagicMock, patch

import pytest

from src.live_trip import LiveOptions, _why_not_looked_up, apply_live
from src.models import METAL_NOT_NEEDED_REASONS, METAL_REASONS, MetalStatus
from src.seats_client import TAXES_UNREPORTED_SOURCES, SeatsClient, parse_availability_row
from src.surcharge import default_table
from src.trip_loader import load_trip_fixture
from tests.test_metal_end_to_end import TRIPS, Stub, vs_row


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


def _row(source):
    row = vs_row()
    row["Route"]["Source"] = source
    row["JAirlines"] = "SQ"
    return row


def test_the_reason_is_classified_as_not_needed():
    assert "NOT_NEEDED_TAXES_UNREPORTED" in METAL_REASONS[MetalStatus.NOT_LOOKED_UP]
    assert "NOT_NEEDED_TAXES_UNREPORTED" in METAL_NOT_NEEDED_REASONS


def test_krisflyer_is_a_direct_partner_so_only_this_rule_skips_it():
    award = next(a for a in parse_availability_row(_row("singapore")) if a.award_type == "J")
    assert award.ur_transferable is True
    assert _why_not_looked_up(award, default_table(), "auto") == (
        "NOT_NEEDED_TAXES_UNREPORTED", "singapore"
    )
    assert _why_not_looked_up(award, default_table(), "all") is None


@pytest.mark.parametrize("source", sorted(TAXES_UNREPORTED_SOURCES))
def test_every_source_without_reported_taxes_is_skipped_in_auto(source):
    # Forced to a direct partner so the rule under test is the one that fires
    # (qatar and turkish are not UR partners and already read NOT_DIRECT_PARTNER).
    base = next(a for a in parse_availability_row(_row("singapore")) if a.award_type == "J")
    award = dataclasses.replace(base, program_source_code=source, ur_transferable=True)
    assert _why_not_looked_up(award, default_table(), "auto") == (
        "NOT_NEEDED_TAXES_UNREPORTED", source
    )


class SQStub(Stub):
    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            self.calls.append(url)
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status.return_value = None
            body = {"data": [_row("singapore")]}
            r.json.return_value = body
            r.text = json.dumps(body)
            return r
        return super().__call__(url, **kwargs)


def _live(mode):
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    opts = LiveOptions(live=True, cache=None, trip_id=fixture.id, trips_mode=mode)
    stub = SQStub()
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    b4 = next(l for l in fixture.legs if l.id == "B4")
    return b4.points_candidates[0].metal, [c for c in stub.calls if "/trips/" in c]


def test_a_krisflyer_award_costs_no_call_in_auto_and_is_looked_up_in_all():
    metal, trips_calls = _live("auto")
    assert trips_calls == []
    assert metal.status is MetalStatus.NOT_LOOKED_UP
    assert metal.reason_code == "NOT_NEEDED_TAXES_UNREPORTED"
    assert "Seats.aero reports no taxes for the singapore source" in metal.render()
    SeatsClient.CACHE.clear()
    SeatsClient.reset_call_budget()
    metal, trips_calls = _live("all")
    assert any(c.endswith("/trips/" + vs_row()["ID"]) for c in trips_calls)
    assert metal.status is not MetalStatus.NOT_LOOKED_UP
