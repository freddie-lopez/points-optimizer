"""
Suite-wide guards.

NO TEST MAY MAKE A NETWORK CALL. This is not a style preference:

  * the build sandbox has no egress at all, so a test that reaches out does not
    fail cleanly - it hangs and then fails for the wrong reason;
  * the Seats.aero partner plan allows 1,000 calls/day, and a CI suite that
    spends from that budget is a CI suite that stops working on a busy day;
  * every "live" behaviour in this codebase is meant to be provable offline
    against a committed envelope or a stub. If a live test can only pass with a
    network, it is not testing what it claims to test.

The guard replaces requests.get / requests.post / Session.get with a function
that raises. A test that legitimately exercises the HTTP path patches it itself
(`@patch("src.seats_client.requests.get")`), which takes effect after this
fixture and is restored before it.
"""
import pytest
import requests


class NetworkAccessAttempted(AssertionError):
    """Raised when a test tries to reach the network."""


@pytest.fixture(autouse=True)
def no_network_egress(monkeypatch):
    def _refuse(*args, **kwargs):
        raise NetworkAccessAttempted(
            "A test attempted a real network call. Every live-path test in this "
            "suite must run off a stub or a committed response envelope - see "
            "tests/conftest.py for why. Patch src.seats_client.requests.get in "
            "the test if you meant to exercise the HTTP path."
        )

    monkeypatch.setattr(requests, "get", _refuse)
    monkeypatch.setattr(requests, "post", _refuse)
    monkeypatch.setattr(requests.Session, "get", _refuse)
    monkeypatch.setattr(requests.Session, "request", _refuse)
