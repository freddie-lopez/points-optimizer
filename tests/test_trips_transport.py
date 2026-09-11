"""
The trips transport: `SeatsClient.trips_raw`, with `requests.get` patched.

No test here reaches the network. What is pinned: the exact request, the ONE
budget counter shared with search, and that every way of getting no readable
response is a TripsLookupError with a code - never an empty payload.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import seats_trips
from src.models import DateRange
from src.seats_client import RawTripsResult, SeatsClient, TripsLookupError
from tests import _trips_payloads as tp


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key_trips_0001")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


def _response(payload=None, status=200, json_error=None, text="{}"):
    r = MagicMock()
    r.status_code = status
    if json_error is not None:
        r.json.side_effect = json_error
    else:
        r.json.return_value = payload
    r.text = text
    return r


def test_the_request_is_exactly_the_documented_one(client):
    with patch("src.seats_client.requests.get", return_value=_response(tp.payload([]))) as get:
        client.trips_raw(tp.AVAIL_ID)
    assert get.call_count == 1
    args, kwargs = get.call_args
    assert args[0] == f"https://seats.aero/partnerapi/trips/{tp.AVAIL_ID}"
    assert kwargs["params"] == {"include_filtered": "false", "min_cabin_pct": "100"}
    assert kwargs["headers"] == {
        "Partner-Authorization": "test_key_trips_0001",
        "Accept": "application/json",
    }
    assert kwargs["timeout"] == 15


def test_the_request_is_recorded_with_its_explicit_params(client):
    with patch("src.seats_client.requests.get", return_value=_response(tp.payload([]))):
        raw = client.trips_raw(tp.AVAIL_ID)
    assert raw.request == {
        "availability_id": tp.AVAIL_ID,
        "include_filtered": "false",
        "min_cabin_pct": "100",
    }
    assert raw.request_key
    assert raw.request_sent is True
    assert raw.served_from_cache is False


def test_a_trips_call_counts_on_the_same_counter_as_search(client):
    before = SeatsClient._calls_made
    with patch("src.seats_client.requests.get", return_value=_response({"data": []})):
        client.search_raw("LHR", "SFO", DateRange(date(2027, 1, 27), date(2027, 1, 27)))
        after_search = SeatsClient._calls_made
        client.trips_raw(tp.AVAIL_ID)
    assert after_search == before + 1
    assert SeatsClient._calls_made == before + 2


def test_a_budget_of_zero_sends_nothing(client):
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP
    with patch("src.seats_client.requests.get") as get:
        with pytest.raises(TripsLookupError) as e:
            client.trips_raw(tp.AVAIL_ID)
    assert get.call_count == 0
    assert e.value.code == "BUDGET_EXHAUSTED"
    assert e.value.request_sent is False
    assert SeatsClient._calls_made == SeatsClient.DAILY_CALL_CAP


@pytest.mark.parametrize(
    "bad", ["../x", "a/b", "?x=1", "x" * 200, "", None, "abc def ghijk", "short"]
)
def test_an_invalid_id_sends_nothing_and_builds_no_url(client, bad):
    before = SeatsClient._calls_made
    with patch("src.seats_client.requests.get") as get:
        with pytest.raises(TripsLookupError) as e:
            client.trips_raw(bad)
    assert get.call_count == 0
    assert e.value.code == "AVAILABILITY_ID_INVALID"
    assert SeatsClient._calls_made == before


@pytest.mark.parametrize(
    "response,code,status",
    [
        (_response(status=404, text=""), "HTTP_404", 404),
        (_response(status=429), "HTTP_429", 429),
        (_response(status=500), "HTTP_ERROR", 500),
        (_response(status=403), "HTTP_ERROR", 403),
        (_response(json_error=ValueError("Expecting value")), "JSON_ERROR", 200),
    ],
)
def test_each_http_failure_has_its_own_code(client, response, code, status):
    before = SeatsClient._calls_made
    with patch("src.seats_client.requests.get", return_value=response):
        with pytest.raises(TripsLookupError) as e:
            client.trips_raw(tp.AVAIL_ID)
    assert e.value.code == code
    assert e.value.http_status == status
    assert e.value.request_sent is True
    assert SeatsClient._calls_made == before + 1, "a sent request is counted"


@pytest.mark.parametrize(
    "exc,code",
    [
        (requests.Timeout("read timed out"), "TIMEOUT"),
        (requests.ConnectionError("refused"), "TRANSPORT_ERROR"),
        (requests.exceptions.SSLError("bad cert"), "TRANSPORT_ERROR"),
    ],
)
def test_transport_failures_have_their_own_codes(client, exc, code):
    with patch("src.seats_client.requests.get", side_effect=exc):
        with pytest.raises(TripsLookupError) as e:
            client.trips_raw(tp.AVAIL_ID)
    assert e.value.code == code
    assert e.value.request_sent is True


@pytest.mark.parametrize("payload", [[], None, "not an object", {"data": None}, 0])
def test_the_payload_is_kept_verbatim(client, payload):
    with patch("src.seats_client.requests.get", return_value=_response(payload)):
        raw = client.trips_raw(tp.AVAIL_ID)
    assert raw.payload == payload and type(raw.payload) is type(payload)


def test_the_raw_body_is_kept_for_a_capture(client):
    with patch(
        "src.seats_client.requests.get",
        return_value=_response(tp.payload([]), text='{"data": []}'),
    ):
        raw = client.trips_raw(tp.AVAIL_ID)
    assert raw.raw_text == '{"data": []}'


def test_coverage_is_carried_on_the_result(client):
    with patch(
        "src.seats_client.requests.get",
        return_value=_response(tp.payload([tp.vs_direct()], hasMore=True)),
    ):
        raw = client.trips_raw(tp.AVAIL_ID)
    assert raw.incomplete is True
    assert "hasMore" in raw.incomplete_reason


def test_trips_lookup_error_is_a_seats_aero_error():
    from src.seats_client import SeatsAeroError

    assert issubclass(TripsLookupError, SeatsAeroError)


def test_the_result_type_carries_every_persisted_provenance_key():
    from src import models

    fields = set(RawTripsResult.__dataclass_fields__)
    assert set(models.PERSISTED_PROVENANCE_KEYS) <= fields


def test_the_request_params_are_the_module_constant():
    assert seats_trips.TRIPS_REQUEST_PARAMS == {
        "include_filtered": "false",
        "min_cabin_pct": "100",
    }
