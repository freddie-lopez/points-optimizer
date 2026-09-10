"""
The suite controls the network, the key and the cache - it does not inherit them.

Seven tests failed on Tsuki's Mac and passed in the sandbox. The triage called it
"the tests assume no network". Reading the code showed three inherited facts, and
no network was needed to reproduce all seven: exporting a key and warming
data/cache/ (the way his live run had) fails the same seven on the old suite in
the sandbox. These tests pin the harness that replaced those accidents
(`tests/conftest.py::isolated_environment` and `tests/_child_guard/`).

Also here: the live CLI's failure modes, one per way the transport can fail, and
its SUCCESS path - which had only ever run by accident on the Mac.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import config
from src.main import main
from src.seats_client import SeatsClient

ROOT = Path(__file__).parent.parent
REAL = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)
COMMITTED_CORPUS = ROOT / "tests" / "fixtures" / "seats_aero" / "live_trip_b"
# The developer's REAL user-config path (conftest records the real HOME before
# it moves HOME for the whole session). No child may resolve it.
from tests.conftest import REAL_HOME  # noqa: E402

REAL_USER_CONFIG = Path(REAL_HOME) / ".config" / "points-optimizer" / ".env"
MARKER = "network disabled by the points-optimizer test harness"
FAKE_KEY = "test_key_not_a_real_one"
CSP = "Chase Sapphire Preferred"
TRIP_B = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", CSP,
    "--transfer-date", "2026-09-15",
]
ROUTES = {
    ("SFO", "MAD"): "2027-01-15",
    ("MAD", "AMS"): "2027-01-19",
    ("AMS", "LHR"): "2027-01-23",
    ("LHR", "SFO"): "2027-01-27",
}


def _child(code: str, **extra_env):
    env = dict(os.environ, **extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
        env=env, timeout=60,
    )


def _corpus_listing():
    return sorted(p.name for p in COMMITTED_CORPUS.iterdir())


# ---------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------


def test_a_child_process_cannot_reach_the_network():
    proc = _child(
        "import requests\n"
        "requests.get('https://seats.aero/partnerapi/search', timeout=5)\n"
    )
    assert proc.returncode != 0
    assert MARKER in proc.stderr


def test_a_child_process_sees_no_key_and_no_real_state():
    """
    What the child's config module resolved, reported by the child itself. Every
    path must be the harness's; none may be the repo's real `.env`, the real
    user config, the real cache or the COMMITTED snapshot corpus.
    """
    proc = _child(
        "import json, os\n"
        "from src import config\n"
        "try:\n"
        "    key = config.resolve_key(None).source\n"
        "except config.KeyResolutionError:\n"
        "    key = None\n"
        "print(json.dumps({\n"
        "  'key': key,\n"
        "  'env_key': os.environ.get('SEATS_AERO_KEY'),\n"
        "  'repo_env': str(config._ENV_PATH),\n"
        "  'user_env': str(config.USER_CONFIG_ENV_PATH),\n"
        "  'cache': str(config.CACHE_DIR),\n"
        "  'snapshots': str(config.SNAPSHOT_DIR),\n"
        "}))\n"
    )
    assert proc.returncode == 0, proc.stderr
    seen = json.loads(proc.stdout)
    assert seen["key"] is None and seen["env_key"] is None, (
        "a child of the test suite resolved an API key it was not given"
    )
    assert seen["repo_env"] != str(ROOT / ".env")
    assert seen["user_env"] != str(REAL_USER_CONFIG)
    assert seen["user_env"].startswith(os.environ["HOME"]), "the harness HOME"
    assert seen["cache"] != str(ROOT / "data" / "cache" / "seats_aero")
    assert seen["snapshots"] != str(COMMITTED_CORPUS)


def test_a_live_cli_child_fails_as_an_api_failure_and_says_why():
    """
    The ordinary CLI, as a child, with a key and no network: every flight leg is
    an API failure, the margin is withheld (exit 3), and the harness's own
    message is what the tool reports - proof the child guard is in the path.
    """
    corpus_before = _corpus_listing()
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", *TRIP_B],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env=dict(os.environ, SEATS_AERO_KEY=FAKE_KEY),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, out[-2000:]
    assert "THIS IS AN API FAILURE" in out
    assert MARKER in out
    assert _corpus_listing() == corpus_before


# ---------------------------------------------------------------------------
# This process
# ---------------------------------------------------------------------------


# Captured at COLLECTION, before any fixture patches `_ENV_INJECTED` to {}.
_INJECTED_AT_COLLECTION = dict(config._ENV_INJECTED)
_KEY_AT_COLLECTION = os.environ.get("SEATS_AERO_KEY")


def test_nothing_was_injected_from_a_key_file_at_import():
    """`load_env()` ran at import, after conftest moved the key files away."""
    assert config.KEY_ENV_VAR not in _INJECTED_AT_COLLECTION
    assert _KEY_AT_COLLECTION is None


def test_the_developers_key_is_not_visible_in_process():
    assert config.KEY_ENV_VAR not in os.environ
    with pytest.raises(config.KeyResolutionError):
        config.resolve_key(None)


def test_1_a_direct_environ_write_is_made_here():
    """
    The exact leak: `config.load_env` writes os.environ without monkeypatch.
    The next test proves it did not survive. (Pytest runs a file in order.)
    """
    probe = Path(os.environ["HOME"]) / "leak.env"
    probe.write_text("SEATS_AERO_KEY=from_repo\n")
    config.load_env(probe, config.KEY_SOURCE_REPO_ENV)
    assert os.environ["SEATS_AERO_KEY"] == "from_repo"


def test_2_the_direct_environ_write_did_not_leak():
    assert "SEATS_AERO_KEY" not in os.environ, (
        "config.load_env's direct write leaked out of the previous test - the "
        "mechanism that made the seven 'no network' tests pass by accident"
    )


# ---------------------------------------------------------------------------
# The live CLI in-process: every failure mode, and the success path
# ---------------------------------------------------------------------------


def _run_cli(argv, capsys):
    old = sys.argv
    sys.argv = ["prog"] + argv
    try:
        code = main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out


@pytest.fixture
def fresh_client_state(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", FAKE_KEY)
    SeatsClient.reset_call_budget()
    SeatsClient(api_key=FAKE_KEY).clear_cache()
    yield
    SeatsClient.reset_call_budget()


def _http_error(status):
    def _fn(*a, **k):
        r = MagicMock()
        r.status_code = status
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status} Client Error", response=r
        )
        return r
    return _fn


def _malformed_json(*a, **k):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    return r


def _raise(exc):
    def _fn(*a, **k):
        raise exc
    return _fn


FAILURE_MODES = {
    "dns_or_refused": _raise(requests.exceptions.ConnectionError("Name or service not known")),
    "connect_timeout": _raise(requests.exceptions.ConnectTimeout("connect timeout")),
    "read_timeout": _raise(requests.exceptions.ReadTimeout("read timeout")),
    "http_401_bad_key": _http_error(401),
    "http_403_blocked": _http_error(403),
    "http_429_rate_limited": _http_error(429),
    "http_500": _http_error(500),
    "malformed_json": _malformed_json,
}


@pytest.mark.parametrize("mode", sorted(FAILURE_MODES))
def test_every_transport_failure_withholds_the_margin(mode, capsys, fresh_client_state):
    """
    Before this, exactly ONE failure mode had ever reached the CLI: the
    sandbox's egress block. Each of these is a different thing that happens to
    real users, and none of them may produce a percentage.
    """
    corpus_before = _corpus_listing()
    with patch("src.seats_client.requests.get", side_effect=FAILURE_MODES[mode]):
        code, out = _run_cli(list(TRIP_B), capsys)
    assert code == 3, out[-2000:]
    assert "WITHHELD" in out
    assert "THIS IS AN API FAILURE" in out
    assert "NOT a finding of no availability" in out
    assert _corpus_listing() == corpus_before


def _route_aware(*args, **kwargs):
    import copy

    params = kwargs.get("params") or {}
    route = (params.get("origin_airport"), params.get("destination_airport"))
    payload = {"data": copy.deepcopy(REAL["data"])}
    row = payload["data"][0]
    iso = ROUTES.get(route, params.get("start_date"))
    row["Date"] = iso
    row["ParsedDate"] = f"{iso}T00:00:00Z"
    row["Route"]["OriginAirport"], row["Route"]["DestinationAirport"] = route
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


def test_the_live_success_path_exits_0_and_archives_only_where_it_was_told(
    capsys, fresh_client_state
):
    """
    The path that ran on the Mac by accident, run on purpose: every leg
    answered, exit 0, a live margin - and the snapshots land in the harness's
    directory, never in the committed corpus.
    """
    corpus_before = _corpus_listing()
    with patch("src.seats_client.requests.get", side_effect=_route_aware):
        code, out = _run_cli(list(TRIP_B), capsys)
    assert code == 0, out[-2000:]
    assert "4 of 4 flight legs scored against live Seats.aero availability" in out
    assert "THIS IS AN API FAILURE" not in out
    assert _corpus_listing() == corpus_before
    archived = list(Path(config.SNAPSHOT_DIR).glob("*.json"))
    assert archived, "a live fetch archives its snapshot - into the harness dir"
