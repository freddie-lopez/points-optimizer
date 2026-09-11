"""
`dispatch` + sinks (UI plan step 2): the CLI hands its structured run to a sink.

The local UI never recomputes anything the CLI decided. It passes a list as
`sink` and receives exactly one FixtureRun, SearchRun or RunRefusal per
invocation, whose exit code is the one `dispatch` returned. These tests pin that
contract on every golden scenario, and pin that `score_fixture` prints nothing
after the pre-scoring banners (so the split changed no byte - the goldens prove
the bytes, this proves the seam is where the plan says it is).
"""
import io

import pytest
from rich.console import Console

from src import main as cli
from tests import _cli_golden as g


@pytest.mark.parametrize("name", sorted(g.SCENARIOS, key=lambda n: int(n[1:])))
def test_the_sink_receives_exactly_one_run_with_the_returned_exit_code(
    name, tmp_path, monkeypatch
):
    result = g.run_scenario(name, tmp_path / "work", monkeypatch)
    assert len(result.sink) == 1, result.sink
    run = result.sink[0]
    assert run.exit_code == result.code
    if result.code in (1, 2):
        assert isinstance(run, cli.RunRefusal)
        # The message is the text the terminal printed, markup removed.
        first = run.message.splitlines()[0]
        assert first.strip() and first.strip()[:40] in " ".join(result.text.split())
    elif "--origin" in g.SCENARIOS[name].argv:
        assert isinstance(run, cli.SearchRun)
    else:
        assert isinstance(run, cli.FixtureRun)


def test_the_refusal_kinds(tmp_path, monkeypatch):
    kinds = {}
    for name in ("G12", "G13"):
        result = g.run_scenario(name, tmp_path / name, monkeypatch)
        kinds[name] = result.sink[0].kind
    assert kinds == {"G12": "wallet", "G13": "replay_refused"}


def test_a_flag_conflict_is_a_refusal_in_the_sink(monkeypatch):
    buf = io.StringIO()
    args = cli.build_parser().parse_args(
        ["--trip-fixture", "trip_b_europe.json", "--offline", "--live"]
    )
    sink = []
    code = cli.dispatch(args, Console(file=buf, width=190), sink)
    assert code == 1
    assert [type(r).__name__ for r in sink] == ["RunRefusal"]
    assert sink[0].kind == "conflict"
    assert sink[0].message.startswith("Error: --offline and --live are mutually exclusive.")
    assert "[red]" not in sink[0].message


@pytest.mark.parametrize("name", ["G1", "G4", "G5", "G6"])
def test_score_fixture_prints_only_the_pre_scoring_banners(name, tmp_path, monkeypatch):
    """A console spy: whatever score_fixture printed, followed by what
    print_fixture_report printed, is the whole CLI output - and the first half
    contains no table, no live banner and no headline."""
    full = g.run_scenario(name, tmp_path / "full", monkeypatch)

    # Same scenario again, split at the seam by hand.
    scen = g.SCENARIOS[name]
    workdir = tmp_path / "split"
    workdir.mkdir()
    if scen.before:
        g.run_scenario(scen.before, workdir, monkeypatch)
    seen = {}

    real_score = cli.score_fixture

    def spy(args, console):
        run = real_score(args, console)
        seen["before_report"] = console.file.getvalue()
        return run

    monkeypatch.setattr(cli, "score_fixture", spy)
    split = g.run_scenario(name, workdir, monkeypatch)
    before = seen["before_report"]
    assert split.text == full.text
    for marker in ("Per-leg: cash vs points", "LIVE TRIP MODE", "beats paying cash",
                   "Residue", "Per-leg detail"):
        assert marker not in before, marker
    assert "Valuation:" in before


def test_fixture_run_carries_the_key_source_and_never_the_key(tmp_path, monkeypatch):
    result = g.run_scenario("G4", tmp_path / "w", monkeypatch)
    run = result.sink[0]
    assert run.key_source == "environment"
    blob = repr(vars(run.args)) + repr(run.key_source)
    assert g.FAKE_KEY not in blob


def test_search_run_counts_the_calls_it_spent(tmp_path, monkeypatch):
    result = g.run_scenario("G9", tmp_path / "w", monkeypatch)
    run = result.sink[0]
    assert run.calls_spent == result.transport.search_calls == 1
    assert run.awards and run.strategies


def test_unfundable_reason_is_the_chain_run_search_printed(tmp_path, monkeypatch):
    result = g.run_scenario("G11", tmp_path / "w", monkeypatch)
    run = result.sink[0]
    assert run.strategies == []
    for award in run.awards:
        assert cli.unfundable_reason(award) in " ".join(result.text.split())
