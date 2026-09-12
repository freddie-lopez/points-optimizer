"""
FINDING R2-1: the delivery is a bundle, and it must unpack on a machine that is
not this one.

`.gitignore` said `.venv/`. With the trailing slash git ignores a DIRECTORY of
that name and nothing else, so when a worktree shared another's interpreter
through a `.venv` SYMLINK, `git add -A` committed the symlink - a path on the
build machine, in the repository. Checking the branch out elsewhere then either
fails (a real .venv is in the way) or leaves a dangling link, and every
documented command starts `.venv/bin/python`.

The rule is checked against the INDEX and against `git archive`, because the
archive is what is actually delivered.
"""
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# R3-2. These checks ask git what is TRACKED, so they need a repository. A
# delivered copy - an unpacked `git archive` export - has none, and four red
# tests there would say nothing about the reader's tree. They are skipped, not
# weakened: in the clone the delivery is fetched into, they run.
pytestmark = pytest.mark.skipif(
    not (ROOT / ".git").exists(),
    reason="no .git here: this is an export, and these checks are about what git tracks",
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=True).stdout


def _tracked():
    return [line.split("\t", 1) for line in _git("ls-files", "-s").splitlines()]


def test_no_tracked_symlink_points_outside_the_repo():
    bad = []
    for meta, rel in _tracked():
        if not meta.startswith("120000"):
            continue
        target = os.readlink(ROOT / rel)
        if os.path.isabs(target) or not (ROOT / rel).resolve().is_relative_to(ROOT):
            bad.append((rel, target))
    assert bad == [], bad


def test_the_venv_is_not_tracked_however_it_is_made():
    assert [rel for _, rel in _tracked() if rel == ".venv" or rel.startswith(".venv/")] == []
    # The guard is about what git TRACKS, not about what happens to be on disk:
    # a checkout with no .venv yet is not a reason to fail.
    assert _git("check-ignore", "-v", ".venv").strip().endswith(".venv")
    assert "\n.venv\n" in "\n" + (ROOT / ".gitignore").read_text(), \
        ".venv must be listed without a trailing slash"


def test_the_archive_git_would_deliver_carries_no_venv_and_no_outside_path(tmp_path):
    """What `git archive` writes is the delivery; check that, not the worktree."""
    tar_path = tmp_path / "export.tar"
    with open(tar_path, "wb") as fh:
        subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT), stdout=fh, check=True)
    with tarfile.open(tar_path) as tf:
        members = tf.getmembers()
    assert members, "empty archive"
    names = [m.name for m in members]
    assert not [n for n in names if n == ".venv" or n.startswith(".venv/")], "a .venv is in the bundle"
    links = [(m.name, m.linkname) for m in members if m.issym() or m.islnk()]
    assert [(n, t) for n, t in links if os.path.isabs(t)] == [], links
    assert [n for n in names if n.startswith("/")] == []
    assert [n for n in names if ".." in Path(n).parts] == []


def test_git_add_everything_would_not_take_it_back():
    """The regression as it happened: `git add -A` with a .venv present. Nothing
    about it may appear in the status, as a link or as a directory."""
    status = _git("status", "--porcelain", "--untracked-files=all")
    assert [l for l in status.splitlines() if ".venv" in l] == [], status
