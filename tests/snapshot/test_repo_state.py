"""The repository state a snapshot is exported from, read from a real git repository under tmp_path."""

import subprocess
from pathlib import Path

import pytest

from generators.snapshot import repo_state
from generators.snapshot.repo_state import RepoState, RepoStateError, read_repo_state, require_clean
from tests.conftest import assert_diagnostic_error


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository with one commit of one tracked file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tester@example.invalid")
    _git(root, "config", "user.name", "Tester")
    (root / "tracked.txt").write_text("first\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "first")
    return root


def test_a_clean_commit_passes(repo: Path) -> None:
    state = read_repo_state(repo)
    assert len(state.commit) == 40 and state.dirty_paths == ()
    require_clean(state, repo_root=repo)


def test_a_modified_tracked_file_is_refused_naming_it(repo: Path) -> None:
    (repo / "tracked.txt").write_text("changed\n")
    state = read_repo_state(repo)
    assert state.dirty_paths == ("tracked.txt",)
    with pytest.raises(RepoStateError) as err:
        require_clean(state, repo_root=repo)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "tracked.txt" in message and str(repo) in message
    assert "git status --porcelain --untracked-files=no prints nothing" in message
    assert "commit or stash, then export again" in message


def test_an_untracked_file_is_not_refused(repo: Path) -> None:
    (repo / "untracked.txt").write_text("new\n")
    state = read_repo_state(repo)
    assert state.dirty_paths == ()
    require_clean(state, repo_root=repo)


def test_outside_a_repository_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    with pytest.raises(RepoStateError) as err:
        read_repo_state(plain)
    assert_diagnostic_error(err.value)
    assert str(plain) in str(err.value)


def test_missing_git_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(repo_state.subprocess, "run", no_git)
    with pytest.raises(RepoStateError) as err:
        read_repo_state(repo)
    assert_diagnostic_error(err.value)


def test_a_refusal_lists_at_most_ten_paths_and_the_total() -> None:
    state = RepoState(commit="f" * 40, dirty_paths=tuple(f"file_{n:02d}.txt" for n in range(13)))
    with pytest.raises(RepoStateError) as err:
        require_clean(state, repo_root=Path("/invented/root"))
    message = str(err.value)
    assert "file_09.txt" in message and "file_10.txt" not in message
    assert "13 tracked path(s)" in message and "(and 3 more)" in message
