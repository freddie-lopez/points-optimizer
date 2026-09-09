"""
Adversarial probe F: pagination truncation reported as completeness,
and a simulated disk-full during the cache write.
"""
import copy
import errno
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.live_trip import LiveOptions, query_leg
from src.models import CashOption, DateRange, Leg, LiveQueryState
from src.response_cache import ResponseCache
from src.seats_client import SeatsClient

ROOT = Path("/home/claude/points-optimizer")
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"


@pytest.fixture
def client():
    c = SeatsClient(api_key="test_key")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


def _mock_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def _row(day=date(2027, 1, 15)):
    r = copy.deepcopy(json.loads(REAL_FIXTURE.read_text())["data"][0])
    r["Date"] = str(day)
    r["ParsedDate"] = f"{day}T00:00:00Z"
    return r


def _leg():
    return Leg(id="L1", kind="flight", description="SFO->MAD",
               date=date(2027, 1, 15), origin="SFO", destination="MAD",
               cash_options=[CashOption(label="c", amount=395.0)])


# =========================================================================
# F1. "hasMore: true" with no cursor is reported as a COMPLETE single page
# =========================================================================


@patch("src.seats_client.requests.get")
def test_F1_hasMore_true_without_a_cursor_is_reported_as_a_complete_single_page(
    mock_get, client
):
    """
    The client's own comment says: 'Told there is more, given no way to ask for
    it. Do not fabricate an offset scheme; stop and let the note record the
    gap.' The note does not record the gap - it asserts the opposite.
    """
    mock_get.return_value = _mock_response({"data": [_row()], "hasMore": True})
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    note = client.last_pagination_note
    print("http calls:", mock_get.call_count)
    print("note      :", note)
    assert mock_get.call_count == 1
    assert "none of them indicated a further page" in note
    assert "INCOMPLETE" not in note          # main.py only reddens on INCOMPLETE
    assert "hasMore" in note                 # the marker is named, then contradicted


@patch("src.seats_client.requests.get")
def test_F2_a_truncated_result_whose_first_page_is_unparseable_becomes_a_finding(
    mock_get, client
):
    """
    Page 1 carries rows the parser cannot read, hasMore says there are more, and
    the client cannot ask for them. The leg reports NO award space as a FINDING.
    """
    bad = _row()
    bad["Date"] = "15/01/2027"
    bad["ParsedDate"] = "15/01/2027"
    mock_get.return_value = _mock_response({"data": [bad], "hasMore": True})
    outcome, awards = query_leg(_leg(), client, LiveOptions(live=True))
    print("state :", outcome.state)
    print("note  :", outcome.pagination_note)
    print("render:", outcome.render())
    assert outcome.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in outcome.render()
    assert "INCOMPLETE" not in outcome.pagination_note


@patch("src.seats_client.requests.get")
def test_F3_hasMore_with_a_skip_field_of_zero_also_stops_silently(mock_get, client):
    mock_get.return_value = _mock_response(
        {"data": [_row()], "hasMore": True, "skip": 0})
    client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 1, 15)))
    print("calls:", mock_get.call_count, "note:", client.last_pagination_note)
    # skip=0 is falsy-but-present; _next_page_params returns {"skip": "0"},
    # which re-requests page one for ever, up to the 25-page cap.
    assert mock_get.call_count == 25
    assert "INCOMPLETE" in client.last_pagination_note


# =========================================================================
# F4. Disk full during the cache write turns a SUCCESSFUL call into an
#     API failure, and spends the budget for nothing.
# =========================================================================


@patch("src.seats_client.requests.get")
def test_F4_enospc_during_the_cache_write_loses_a_successful_response(
    mock_get, client, tmp_path
):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    mock_get.return_value = _mock_response({"data": [_row()]})
    before = SeatsClient._budget_remaining()

    real_write = Path.write_text

    def _enospc(self, *a, **kw):
        if self.suffix == ".json":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(self, *a, **kw)

    with patch.object(Path, "write_text", _enospc):
        outcome, awards = query_leg(_leg(), client,
                                    LiveOptions(live=True, cache=cache))
    after = SeatsClient._budget_remaining()
    print("budget spent:", before - after)
    print("http calls  :", mock_get.call_count)
    print("state       :", outcome.state)
    print("error       :", outcome.error)
    print("render      :", outcome.render())
    assert mock_get.call_count == 1
    assert before - after == 1, "an API call was spent"
    assert outcome.state is LiveQueryState.API_ERROR
    assert "No space left on device" in outcome.error
    # The response WAS received and parsed successfully; the run reports
    # "COULD NOT BE REACHED".
    assert "COULD NOT BE REACHED" in outcome.render()
    assert awards == []
