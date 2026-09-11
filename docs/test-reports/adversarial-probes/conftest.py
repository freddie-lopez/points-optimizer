"""
The main suite's environment isolation, applied to these probes as well.

These live outside `testpaths` and assert defects are PRESENT (40 red / 38 green
is the recorded state). That count is only a regression signal if it does not
depend on the developer's machine - an exported key, a warm data/cache/, or a
reachable network - so the probes get the same harness as tests/.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.conftest import isolated_environment, no_network_egress  # noqa: E402,F401
