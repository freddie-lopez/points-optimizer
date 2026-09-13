"""
The two label constants, pinned for tests that describe the UNVERIFIED state.

`TRIPS_SCHEMA_VERIFIED_BY` and `TRIPS_TOTALTAXES_UNIT` are meant to be flipped
once a real capture exists. A test that asserts what the tool prints or scores
WHILE they are unflipped must say so - by pinning them here - so that flipping
them (the one step Tsuki is asked to take) does not turn the suite red. Tests of
the flipped state patch them the other way (tests/test_trips_label_flip.py).

Both are read at call time, so patching the module attribute is enough.
"""
import pytest

from src import seats_trips


@pytest.fixture
def unverified_constants(monkeypatch):
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "")
    monkeypatch.setattr(seats_trips, "TRIPS_TOTALTAXES_UNIT", "unverified")
