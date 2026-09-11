"""
Tester probes: the suite owns its environment - key, cache, snapshots, network -
in-process AND in children, under interpreter flags that skip parts of startup.

These run under the same harness as tests/ (conftest imports it). Several are
meaningful only under the simulated Mac (exported key, repo .env, user-config
.env, warm data/cache/, a non-empty working-tree corpus, checkout folder named
points-optimizer-v5); in the sandbox they are trivially green. See the report.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

from conftest import ROOT

# Captured at COLLECTION time, before any fixture runs. `src.config` is imported
# by this point (the probe conftest imports tests.conftest, and test modules
# import src.*), and its module body calls load_env() on the REAL repo .env and
# the REAL ~/.config/points-optimizer/.env.
import src.config as _config  # noqa: E402

KEY_SEEN_AT_COLLECTION = os.environ.get("SEATS_AERO_KEY")
INJECTED_AT_COLLECTION = dict(_config._ENV_INJECTED)

MARKER = "network disabled by the points-optimizer test harness"
CORPUS = ROOT / "tests" / "fixtures" / "seats_aero" / "live_trip_b"


def _child(flags, code, cwd=ROOT, **env_over):
    env = dict(os.environ, **env_over)
    return subprocess.run(
        [sys.executable, *flags, "-c", code], cwd=cwd, capture_output=True,
        text=True, env=env, timeout=60,
    )


REQUEST = textwrap.dedent("""
    import sys
    try:
        import requests
    except ImportError:
        print("NO_REQUESTS"); sys.exit(0)
    try:
        requests.get("https://seats.aero/partnerapi/search", timeout=5)
        print("REACHED")
    except Exception as e:
        print("BLOCKED", type(e).__name__, str(e)[:160])
""")


# ===========================================================================
# GREEN
# ===========================================================================


@pytest.mark.parametrize("flags", [[], ["-O"], ["-E"], ["-S"], ["-I"], ["-s"]])
def test_no_child_reaches_the_network_whatever_its_startup_flags(flags):
    proc = _child(flags, REQUEST)
    assert "REACHED" not in proc.stdout, (flags, proc.stdout, proc.stderr)


@pytest.mark.parametrize("flags", [["-E"], ["-I"]])
def test_a_child_that_skips_pythonpath_still_reads_the_harness_state_dirs(flags, tmp_path):
    """-E / -I drop the sitecustomize guard, but POINTS_OPTIMIZER_* are not PYTHON*
    variables, so config still resolves the harness's paths. (-I also drops the
    cwd from sys.path, so the child is run from a script that adds the repo.)"""
    script = tmp_path / "probe.py"
    script.write_text(
        f"import sys, json; sys.path.insert(0, {str(ROOT)!r})\n"
        "from src import config\n"
        "print(json.dumps([str(config._ENV_PATH), str(config.CACHE_DIR), "
        "str(config.SNAPSHOT_DIR)]))\n"
    )
    proc = subprocess.run([sys.executable, *flags, str(script)], capture_output=True,
                          text=True, env=dict(os.environ), timeout=60)
    env_path, cache, snaps = json.loads(proc.stdout)
    assert env_path != str(ROOT / ".env")
    assert cache != str(ROOT / "data" / "cache" / "seats_aero")
    assert snaps != str(CORPUS)


def test_the_child_guard_chains_to_another_sitecustomize_and_still_installs(tmp_path):
    other = tmp_path / "other_site"
    other.mkdir()
    (other / "sitecustomize.py").write_text(
        "import os\nos.environ['OTHER_SITECUSTOMIZE_RAN'] = '1'\n"
        "import requests\nrequests.get = lambda *a, **k: 'OTHER_WINS'\n"
    )
    pp = os.environ["PYTHONPATH"] + os.pathsep + str(other)
    proc = _child([], "import os, requests\n"
                      "print(os.environ.get('OTHER_SITECUSTOMIZE_RAN'))\n"
                      "try:\n    print(requests.get('https://x'))\n"
                      "except Exception as e:\n    print('BLOCKED', e)\n",
                  PYTHONPATH=pp)
    assert proc.stdout.splitlines()[0] == "1", proc.stdout + proc.stderr
    assert "BLOCKED" in proc.stdout and MARKER in proc.stdout


def test_a_child_cannot_archive_into_the_working_tree_corpus(capsys):
    """A live CLI child with a key and every flag combination that could matter."""
    before = sorted(p.name for p in CORPUS.iterdir())
    for flags in ([], ["-E"]):
        subprocess.run(
            [sys.executable, *flags, "-m", "src.main", "--trip-fixture",
             "trip_b_europe.json", "--balance", "UR=160000", "--card",
             "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            env=dict(os.environ, SEATS_AERO_KEY="test_key_not_a_real_one"),
        )
    assert sorted(p.name for p in CORPUS.iterdir()) == before


def test_in_a_test_the_key_and_injected_state_are_gone():
    from src import config

    assert os.environ.get("SEATS_AERO_KEY") is None
    assert config._ENV_INJECTED == {}
    with pytest.raises(config.KeyResolutionError):
        config.resolve_key(None)


# ===========================================================================
# RED only under the simulated Mac (a repo .env / ~/.config .env present)
# ===========================================================================


def test_collection_time_code_cannot_see_the_developers_key():
    """
    `src.config` runs load_env() at IMPORT, on the real repo .env and the real
    user-config file, before any fixture exists. Every test module imports src.*
    at collection, so module-level code (a skipif, a parametrize list, a module
    constant) sees the developer's key and `_ENV_INJECTED` holds its value and
    path. The per-test fixture removes it for the test body; the snapshot it
    restores afterwards is the post-injection environment.
    """
    assert KEY_SEEN_AT_COLLECTION is None or KEY_SEEN_AT_COLLECTION.startswith("canary_EXPORTED"), (
        "collection saw a key that came from a FILE, not the shell"
    )
    assert "SEATS_AERO_KEY" not in INJECTED_AT_COLLECTION, (
        f"collection-time load_env injected a key from "
        f"{INJECTED_AT_COLLECTION.get('SEATS_AERO_KEY', ('', '', ''))[2]}"
    )
