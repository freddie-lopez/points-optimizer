"""
Fix round 1, Tester finding 22: `--trips-cap ²` exits 1 with the named reason.

"²".isdigit() is True and int("²") raises; the value is now parsed inside the
check, so every non-number gets the same line and no request is made.
"""
import re

import pytest

from src.seats_client import SeatsClient
from tests.test_trips_flags import cli


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_trips_cap_unicode_01")
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


@pytest.mark.parametrize("value", ["²", "³", "¹⁰", "1_0", "+5", "5.0", ""])
def test_a_cap_that_is_not_a_plain_whole_number_names_why(capsys, value):
    code, out, stub = cli(capsys, "--trips-cap", value)
    assert code == 1
    assert "not a whole number from 1 to 50" in out
    assert "invalid literal" not in out
    assert stub.calls == []
    assert not re.search(r"\d+\.\d\d%", out)


def test_a_full_width_digit_is_still_a_number(capsys):
    code, out, _ = cli(capsys, "--trips-cap", "５")
    assert "not a whole number" not in out
    assert code != 1
