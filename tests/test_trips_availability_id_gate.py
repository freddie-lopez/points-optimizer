"""
Fix round 1, finding 4: the availability-id gate is a FULL match. An id with a
trailing newline (or any whitespace) builds no request, no file name and no
manifest cell.
"""
from unittest.mock import patch

import pytest

from src import seats_trips
from src.live_trip import LiveOptions, apply_live
from src.models import MetalStatus
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient, TripsLookupError
from src.trip_loader import load_trip_fixture
from tests import _trips_payloads as tp
from tests.test_metal_end_to_end import TRIPS, Stub, vs_row

NL_ID = tp.AVAIL_ID + "\n"


@pytest.mark.parametrize("bad", [NL_ID, tp.AVAIL_ID + "\r\n", " " + tp.AVAIL_ID, tp.AVAIL_ID + " "])
def test_whitespace_around_an_id_is_refused(bad):
    assert not seats_trips.valid_availability_id(bad)


def test_the_transport_sends_nothing_for_a_newline_id():
    SeatsClient.reset_call_budget()
    with patch("src.seats_client.requests.get") as get:
        with pytest.raises(TripsLookupError) as e:
            SeatsClient(api_key="k_test_000000000000").trips_raw(NL_ID)
    assert e.value.code == "AVAILABILITY_ID_INVALID"
    assert get.call_count == 0


class NLStub(Stub):
    def __call__(self, url, **kwargs):
        r = super().__call__(url, **kwargs)
        params = kwargs.get("params") or {}
        if url.endswith("/search") and params.get("origin_airport") == "LHR":
            row = vs_row()
            row["ID"] = NL_ID
            r.json.return_value = {"data": [row]}
        return r


def test_a_row_whose_id_ends_in_a_newline_is_not_looked_up_and_writes_no_row(tmp_path):
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    stub = NLStub(trips_payloads={NL_ID: tp.payload([tp.vs_direct()])})
    fixture = load_trip_fixture(TRIPS / "trip_b_europe.json")
    opts = LiveOptions(live=True, cache=cache, trip_id=fixture.id, trips_mode="auto")
    with patch("src.seats_client.requests.get", side_effect=stub):
        fixture, _ = apply_live(fixture, SeatsClient(api_key="k_test_000000000000"), opts)
    assert not [c for c in stub.calls if "/trips/" in c]
    b4 = next(l for l in fixture.legs if l.id == "B4")
    metal = b4.points_candidates[0].metal
    assert metal.status is MetalStatus.NOT_LOOKED_UP
    assert metal.reason_code == "AVAILABILITY_ID_INVALID"
    assert not (tmp_path / "s" / "trips_endpoint").exists()
