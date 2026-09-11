"""
NETWORK GUARD FOR CHILD PROCESSES SPAWNED BY THE TEST SUITE.

`tests/conftest.py` puts this directory on PYTHONPATH for every test, so every
`subprocess.run([sys.executable, "-m", "src.main", ...])` imports this before
anything else. The in-process guard in conftest.py (monkeypatching `requests`)
cannot reach a child; before this existed, the suite's CLI tests reached the real
Seats.aero API from any machine with a network - with the developer's real key -
and "failed" by succeeding.

A child that tries to use `requests` gets a ConnectionError, which is exactly
what the tool sees when the network is down, so it exercises the real
failure path rather than a test-only one.

If another sitecustomize exists further down sys.path (some Python builds ship
one), it is still executed: shadowing it silently would change the child's
environment in ways no test asked for.
"""
import importlib.util
import os
import sys

MARKER = "network disabled by the points-optimizer test harness"


def _install():
    try:
        import requests
    except Exception:  # pragma: no cover - requests is a hard dependency
        return

    def _refuse(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            f"{MARKER}: a child process of the test suite attempted a real "
            f"network call. See tests/_child_guard/sitecustomize.py."
        )

    requests.get = _refuse
    requests.post = _refuse
    requests.Session.request = _refuse
    requests.Session.get = _refuse
    requests.Session.post = _refuse


def _chain_to_the_real_sitecustomize():
    here = os.path.dirname(os.path.abspath(__file__))
    for entry in sys.path:
        if not entry or os.path.abspath(entry) == here:
            continue
        candidate = os.path.join(entry, "sitecustomize.py")
        if os.path.isfile(candidate):
            spec = importlib.util.spec_from_file_location(
                "_chained_sitecustomize", candidate
            )
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:  # pragma: no cover - never break the child for this
                pass
            return


_chain_to_the_real_sitecustomize()
_install()
os.environ.setdefault("POINTS_OPTIMIZER_CHILD_GUARD", "1")
