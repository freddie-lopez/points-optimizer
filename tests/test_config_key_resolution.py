"""
v5 Step 1: where the Seats.aero key comes from, and what may be printed about it.

The four sources are a PRIORITY ORDER, not a search. Each test below removes the
higher-priority sources explicitly rather than trusting the ambient environment,
because a test that passes only because the sandbox happens to have no
~/.config file is not testing an order.
"""
import os
from pathlib import Path

import pytest

from src import config
from src.main import build_parser, print_key_banner


REAL_SHAPED_KEY = "pro_" + "x" * 24 + "jwV"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No key anywhere. Every source is pointed at a path that does not exist."""
    monkeypatch.delenv(config.KEY_ENV_VAR, raising=False)
    monkeypatch.setattr(config, "_ENV_PATH", tmp_path / "repo" / ".env")
    monkeypatch.setattr(config, "USER_CONFIG_ENV_PATH", tmp_path / "user" / ".env")
    monkeypatch.setattr(config, "_ENV_INJECTED", {})
    return tmp_path


def _write_env(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# a comment\n{config.KEY_ENV_VAR}={value}\n")
    return path


# ---------------------------------------------------------------------------
# The four-source order
# ---------------------------------------------------------------------------


def test_flag_wins_over_everything(isolated, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, "from_env")
    _write_env(config._ENV_PATH, "from_repo")
    _write_env(config.USER_CONFIG_ENV_PATH, "from_user")

    res = config.resolve_key("from_flag")
    assert res.key == "from_flag"
    assert res.source == config.KEY_SOURCE_FLAG


def test_env_wins_over_both_files(isolated, monkeypatch):
    monkeypatch.setenv(config.KEY_ENV_VAR, "from_env")
    _write_env(config._ENV_PATH, "from_repo")
    _write_env(config.USER_CONFIG_ENV_PATH, "from_user")

    res = config.resolve_key(None)
    assert res.key == "from_env"
    assert res.source == config.KEY_SOURCE_ENV


def test_repo_env_wins_over_user_config(isolated):
    _write_env(config._ENV_PATH, "from_repo")
    _write_env(config.USER_CONFIG_ENV_PATH, "from_user")

    res = config.resolve_key(None)
    assert res.key == "from_repo"
    assert res.source == config.KEY_SOURCE_REPO_ENV
    assert res.path == config._ENV_PATH


def test_user_config_is_the_last_resort(isolated):
    _write_env(config.USER_CONFIG_ENV_PATH, "from_user")

    res = config.resolve_key(None)
    assert res.key == "from_user"
    assert res.source == config.KEY_SOURCE_USER_CONFIG
    assert res.path == config.USER_CONFIG_ENV_PATH


def test_a_key_injected_from_the_repo_file_is_not_reported_as_environment(
    isolated, monkeypatch
):
    """
    `load_env()` copies the repo file into os.environ at import.

    Without the injection record, every key that came out of ./.env would be
    reported as having come from the environment - a false provenance line on
    every live run, printed next to a masked key as if it were checked.
    """
    path = _write_env(config._ENV_PATH, "from_repo")
    monkeypatch.setattr(config, "_ENV_INJECTED", {})
    config.load_env(path, config.KEY_SOURCE_REPO_ENV)

    res = config.resolve_key(None)
    assert res.key == "from_repo"
    assert res.source == config.KEY_SOURCE_REPO_ENV, (
        "a key injected from ./.env must not claim to be an environment variable"
    )


def test_a_flag_means_the_environment_is_never_read(isolated, monkeypatch):
    """
    THE SENTINEL TEST. With a flag given, the env value must not appear in the
    resolution at all - not in the key, not in the source, not in the repr.
    """
    sentinel = "SENTINEL_VALUE_THAT_MUST_NOT_BE_READ"
    monkeypatch.setenv(config.KEY_ENV_VAR, sentinel)

    res = config.resolve_key("from_flag")
    blob = f"{res.key}|{res.source}|{res.path}|{res!r}|{res.describe()}"
    assert sentinel not in blob


def test_no_key_anywhere_names_all_four_locations_in_order(isolated):
    with pytest.raises(config.KeyResolutionError) as excinfo:
        config.resolve_key(None)
    message = str(excinfo.value)
    for label in config.KEY_SOURCE_ORDER:
        assert label in message
    assert str(config._ENV_PATH) in message
    assert str(config.USER_CONFIG_ENV_PATH) in message
    # Priority order, not an unordered list.
    positions = [message.index(label) for label in config.KEY_SOURCE_ORDER]
    assert positions == sorted(positions)


def test_no_key_anywhere_exits_1_through_the_cli(isolated, capsys):
    """The CLI surfaces it as exit 1, not as a traceback."""
    from src.main import main
    import sys

    argv = sys.argv
    sys.argv = ["prog", "--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
                "--balance", "UR=160000"]
    try:
        code = main()
    finally:
        sys.argv = argv
    assert code == 1
    assert config.KEY_SOURCE_USER_CONFIG in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


def test_mask_key_shape():
    assert config.mask_key(REAL_SHAPED_KEY) == "pro_…jwV"


def test_a_short_key_masks_to_the_ellipsis_alone():
    """
    Masking a 6-character secret as `abcd…def` would print all of it. The safe
    direction on a short key is to show NOTHING, not to show most of it.
    """
    for short in ("", "a", "pro_x", "pro_xyz"):
        masked = config.mask_key(short)
        assert masked == "…"
        assert short == "" or short not in masked


def test_the_mask_cannot_be_used_to_reconstruct_the_key():
    masked = config.mask_key(REAL_SHAPED_KEY)
    assert len(masked) < len(REAL_SHAPED_KEY)
    assert REAL_SHAPED_KEY not in masked
    # The 24 hidden characters are hidden.
    assert "x" not in masked


def test_the_banner_never_contains_the_full_key(isolated, capsys):
    from rich.console import Console

    res = config.KeyResolution(REAL_SHAPED_KEY, config.KEY_SOURCE_USER_CONFIG,
                               config.USER_CONFIG_ENV_PATH)
    print_key_banner(Console(width=190), res)
    out = capsys.readouterr().out
    assert REAL_SHAPED_KEY not in out
    assert "pro_…jwV" in out
    assert config.KEY_SOURCE_USER_CONFIG in out


def test_repr_of_a_resolution_does_not_leak_the_key():
    """A traceback that prints the object must not print the secret."""
    res = config.KeyResolution(REAL_SHAPED_KEY, config.KEY_SOURCE_ENV)
    assert REAL_SHAPED_KEY not in repr(res)


def test_not_required_banner_names_no_key_at_all(capsys):
    from rich.console import Console

    print_key_banner(Console(width=190), None, note="--from-snapshot replays bytes")
    out = capsys.readouterr().out
    assert "not required" in out


# ---------------------------------------------------------------------------
# The client takes its key from resolve_key, not from os.getenv
# ---------------------------------------------------------------------------


def test_client_key_comes_from_resolve_key(isolated):
    from src.seats_client import SeatsClient

    _write_env(config.USER_CONFIG_ENV_PATH, REAL_SHAPED_KEY)
    client = SeatsClient()
    assert client.api_key == REAL_SHAPED_KEY
    assert client.key_resolution.source == config.KEY_SOURCE_USER_CONFIG


def test_client_flag_beats_environment(isolated, monkeypatch):
    from src.seats_client import SeatsClient

    monkeypatch.setenv(config.KEY_ENV_VAR, "env_key_value")
    client = SeatsClient("flag_key_value")
    assert client.api_key == "flag_key_value"
    assert client.key_resolution.source == config.KEY_SOURCE_FLAG


def test_client_with_no_key_raises_naming_all_four(isolated):
    from src.seats_client import SeatsClient

    with pytest.raises(ValueError) as excinfo:
        SeatsClient()
    for label in config.KEY_SOURCE_ORDER:
        assert label in str(excinfo.value)


def test_api_key_flag_is_on_the_parser():
    args = build_parser().parse_args(["--api-key", "abc"])
    assert args.api_key == "abc"
