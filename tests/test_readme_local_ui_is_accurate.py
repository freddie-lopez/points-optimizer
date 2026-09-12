"""
The README's "Local UI" section is the only thing a reader has before they run
the app, so it is checked against the code rather than trusted.

Six rounds of fixes moved things it describes - the call counter became two
counts (L-1), Esc gained a focus rule (R2-4), the table gained pinned columns
(M-5), the trip list gained CANNOT LOAD and NOT A PER-LEG TRIP rows, names
gained a length limit (M-3). A README that still describes the old behaviour is
the same failure this project is about, on the page the reader meets first.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
UI = README[README.index("## Local UI"):README.index("## Live Trip Mode")]
# Flattened, so a needle that happens to straddle a line wrap still matches -
# the check is about what the section SAYS, not how it is wrapped.
FLAT = " ".join(UI.split())
STATIC = ROOT / "src" / "ui" / "static"


def test_the_section_exists_and_is_not_a_stub():
    assert len(UI.split()) > 400, "the Local UI section is too short to be complete"


@pytest.mark.parametrize("topic,needles", [
    ("the launch command", [".venv/bin/python -m src.ui", "--no-open", "--port"]),
    ("what LIVE spends", ["25 calls per flight leg", "itinerary-lookup cap",
                          "single-use", "five minutes"]),
    ("the two call counts", ["does not reset", "resets at midnight"]),
    ("the threat model", ["127.0.0.1", "Host", "per-launch token", "origin",
                          "Content-Security-Policy", "DNS rebinding"]),
    ("the key never leaving", ["The key never leaves the server"]),
    ("the manifest allowlist", ["MANIFEST.md", "never a path"]),
    ("in-memory only", ["memory only", "last 20 runs", "never written"]),
    ("one run at a time", ["One run at a time"]),
    ("the keyboard", ["Enter", "Space", "Esc", "focus", "focus trap"]),
    ("the pinned columns", ["LEG", "VERDICT", "pinned"]),
    ("phone width", ["720px", "400px"]),
    ("what the trip list says", ["CANNOT LOAD", "NOT A PER-LEG TRIP"]),
    ("every run is a CLI run", ["Equivalent command", "masked key not sent to the browser"]),
])
def test_the_section_covers(topic, needles):
    missing = [n for n in needles if n not in FLAT]
    assert missing == [], f"{topic}: the README does not mention {missing}"


def test_the_launch_command_is_the_one_that_exists():
    from src.ui import __main__ as entry  # noqa: F401

    assert (ROOT / "src" / "ui" / "__main__.py").is_file()
    for flag in ("--wallet", "--port", "--no-open"):
        assert flag in (ROOT / "src" / "ui" / "__main__.py").read_text(), flag


def test_the_default_port_in_the_readme_is_the_code_s_default():
    import argparse
    from src.ui.__main__ import build_parser

    default = next(a.default for a in build_parser()._actions
                   if isinstance(a, argparse.Action) and "--port" in a.option_strings)
    assert re.search(rf"http://127\.0\.0\.1:{default}/", FLAT), default


def test_the_numbers_it_quotes_are_the_code_s_numbers():
    from src.ui.engine import PAGES_PER_SEARCH, RUNS_KEPT
    from src.trip_builder import MAX_NAME_LENGTH

    assert f"up to {PAGES_PER_SEARCH} calls per flight leg" in FLAT
    assert f"last {RUNS_KEPT} runs" in FLAT
    assert f"{MAX_NAME_LENGTH} characters" in FLAT


def test_the_confirmation_lifetime_matches_the_server():
    from src.ui.engine import CONFIRM_TTL_SECONDS

    assert CONFIRM_TTL_SECONDS == 5 * 60
    assert "five minutes" in FLAT


def test_the_breakpoint_it_quotes_is_the_stylesheet_s():
    css = (STATIC / "app.css").read_text()
    assert "720px" in css, "the README quotes a breakpoint the stylesheet does not have"


def test_it_does_not_still_describe_the_counter_that_reset_at_midnight():
    assert '"since launch" for today' not in FLAT, \
        "L-1: the launch count and the daily budget are two numbers now"
