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
# ---------------------------------------------------------------------------
# BEFORE ANYTHING IMPORTS `src`. `src.config` runs `load_env()` AT IMPORT, and
# test modules import `src.*` during COLLECTION - before any fixture exists. So
# module-level test code (a skipif, a parametrize list) saw the developer's real
# key, `config._ENV_INJECTED` held it, and the per-test snapshot below restored
# it into os.environ after every test. The key and the key files are removed
# from this PROCESS here, once, at the first moment pytest reads this file.
# ---------------------------------------------------------------------------
import os as _os
import tempfile as _tempfile

REAL_HOME = _os.path.expanduser("~")
_SESSION_HOME = _tempfile.mkdtemp(prefix="points-optimizer-test-home-")


def _remove_session_home():
    import shutil

    shutil.rmtree(_SESSION_HOME, ignore_errors=True)


import atexit as _atexit  # noqa: E402

_atexit.register(_remove_session_home)
_os.environ.pop("SEATS_AERO_KEY", None)
_os.environ["POINTS_OPTIMIZER_ENV_FILE"] = _os.path.join(_SESSION_HOME, "absent.env")
_os.environ["HOME"] = _SESSION_HOME

import pytest  # noqa: E402
import requests  # noqa: E402


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


# ---------------------------------------------------------------------------
# THE SUITE OWNS ITS ENVIRONMENT - in this process AND in every child.
#
# The guard above only patches `requests` in THIS process. Most CLI tests run
# `python -m src.main` as a SUBPROCESS, which it cannot reach, and on Tsuki's
# Mac seven tests failed because of what leaked into those children:
#
#   * his REAL key. SEATS_AERO_KEY is exported from ~/.zshrc, children inherit
#     os.environ, and nothing removed it. They reached the live API.
#   * a FAKE key, in the sandbox. `config.load_env()` writes os.environ
#     directly, and a key-resolution test called it after
#     `monkeypatch.delenv(..., raising=False)` - which records nothing for an
#     absent variable - so "from_repo" leaked into every later test. Run alone,
#     the seven "no network" tests exited 1 ("no key"), not 3. They only ever
#     passed because of another test's leak meeting the sandbox's egress block.
#   * his real runtime CACHE. `main` builds its cache on config.CACHE_DIR, the
#     repo's data/cache/, and archives into config.SNAPSHOT_DIR - the COMMITTED
#     corpus. A test asking for Trip B's own B1 got a cache hit from his live
#     run. A pytest run could write fixtures.
#
# So, per test: the environment is snapshotted and restored whole; the key is
# gone; HOME, the key files, the cache and the snapshot directory point into
# tmp; and every child gets the same, through environment variables it reads
# at import, plus a sitecustomize that makes `requests` raise ConnectionError.
# "No network" is now a stated property of the harness, not of the machine.
# ---------------------------------------------------------------------------
import os
from pathlib import Path

CHILD_GUARD_DIR = Path(__file__).parent / "_child_guard"
# Refused before any socket opens: port 9 on loopback is the discard port and
# nothing listens there. A belt to the sitecustomize's braces, so that a child
# that somehow skipped site customisation still cannot reach the internet.
DEAD_PROXY = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path_factory):
    from src import config

    snapshot = dict(os.environ)
    home = tmp_path_factory.mktemp("home")
    state = tmp_path_factory.mktemp("state")

    monkeypatch.delenv(config.KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("POINTS_OPTIMIZER_ENV_FILE", str(state / "absent.env"))
    monkeypatch.setenv("POINTS_OPTIMIZER_CACHE_DIR", str(state / "cache"))
    monkeypatch.setenv("POINTS_OPTIMIZER_SNAPSHOT_DIR", str(state / "snapshots"))
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            p for p in (str(CHILD_GUARD_DIR), os.environ.get("PYTHONPATH", "")) if p
        ),
    )
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(var, DEAD_PROXY)
    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    # In-process, the module already imported with the real paths.
    monkeypatch.setattr(config, "_ENV_PATH", state / "absent.env")
    monkeypatch.setattr(
        config, "USER_CONFIG_ENV_PATH", home / ".config" / "points-optimizer" / ".env"
    )
    monkeypatch.setattr(config, "_ENV_INJECTED", {})
    monkeypatch.setattr(config, "CACHE_DIR", state / "cache")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", state / "snapshots")

    yield

    # Anything written to os.environ WITHOUT monkeypatch (config.load_env is
    # the known case) is undone here, before the next test can inherit it.
    os.environ.clear()
    os.environ.update(snapshot)
