"""The repository state a snapshot is exported from, read from a real git repository under tmp_path.

A modified tree is recorded, never refused (Decision 4, revised 9 October 2026): every changed tracked
path and every untracked, not-ignored path, with its status code and content hash, plus a patch that
reproduces the tree from the commit.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from generators.snapshot import repo_state
from generators.snapshot.repo_state import DELETED, RepoState, RepoStateError, TreeChange, read_repo_state
from tests.conftest import assert_diagnostic_error
from tests.snapshot.git_tools import (
    EDITED,
    EXPECTED_STATUS,
    UNTRACKED_BINARY,
    UNTRACKED_TEXT,
    apply_to_clone,
    git,
    make_dirty,
    make_repo,
    tree_files,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository with one commit."""
    return make_repo(tmp_path / "repo")


def test_a_clean_commit_records_no_changes_and_no_patch(repo: Path) -> None:
    state = read_repo_state(repo)
    assert len(state.commit) == 40 and state.changes == () and state.patch == b""
    assert state.dirty is False


def test_an_ignored_file_alone_leaves_the_tree_clean(repo: Path) -> None:
    (repo / "ignored.log").write_text("noise\n")
    assert read_repo_state(repo).dirty is False


def test_modified_deleted_and_untracked_paths_are_each_recorded(repo: Path) -> None:
    make_dirty(repo)
    state = read_repo_state(repo)
    assert state.dirty is True
    assert {change.path: change.status for change in state.changes} == EXPECTED_STATUS
    hashes = {change.path: change.sha256 for change in state.changes}
    assert hashes == {
        "edited.txt": _sha(EDITED.encode()),
        "gone.txt": DELETED,
        "notes/new.txt": _sha(UNTRACKED_TEXT.encode()),
        "blob.bin": _sha(UNTRACKED_BINARY),
    }
    assert [change.path for change in state.changes] == sorted(EXPECTED_STATUS)


def test_a_staged_change_is_recorded_with_its_index_status(repo: Path) -> None:
    (repo / "edited.txt").write_text(EDITED)
    git(repo, "add", "edited.txt")
    state = read_repo_state(repo)
    assert [(c.path, c.status) for c in state.changes] == [("edited.txt", "M ")]
    assert b"+second line" in state.patch


def test_a_rename_records_where_it_came_from(repo: Path) -> None:
    git(repo, "mv", "kept.txt", "moved.txt")
    (change,) = read_repo_state(repo).changes
    assert (change.path, change.status, change.original_path) == ("moved.txt", "R ", "kept.txt")
    assert change.as_record()["original_path"] == "kept.txt"


def test_the_patch_reproduces_the_modified_tree_from_the_commit(repo: Path, tmp_path: Path) -> None:
    make_dirty(repo)
    state = read_repo_state(repo)
    patch = tmp_path / "working_tree.patch"
    patch.write_bytes(state.patch)
    clone = apply_to_clone(repo, patch, tmp_path / "clone")
    expected = {path: data for path, data in tree_files(repo).items() if path != "ignored.log"}
    assert tree_files(clone) == expected


def test_an_excluded_directory_is_left_out(repo: Path) -> None:
    (repo / "out" / "snapshot_20310309").mkdir(parents=True)
    (repo / "out" / "snapshot_20310309" / "image.png").write_bytes(b"invented")
    assert read_repo_state(repo, exclude=(repo / "out",)).dirty is False
    assert [c.path for c in read_repo_state(repo).changes] == ["out/snapshot_20310309/image.png"]


def test_two_reads_of_one_state_are_equal_and_an_edit_between_them_is_not(repo: Path) -> None:
    make_dirty(repo)
    first = read_repo_state(repo)
    assert read_repo_state(repo) == first
    (repo / "edited.txt").write_text(EDITED + "third line\n")
    assert read_repo_state(repo) != first


def test_a_record_carries_path_status_and_hash_only_unless_renamed() -> None:
    record = TreeChange("config/invented.yml", " M", "a" * 64).as_record()
    assert record == {"path": "config/invented.yml", "sha256": "a" * 64, "status": " M"}


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


def test_a_failing_status_is_refused_four_part(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = subprocess.run

    def failing_status(args: list[str], **kwargs: object) -> object:
        if "status" in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: invented index corruption")
        return real(args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(repo_state.subprocess, "run", failing_status)
    with pytest.raises(RepoStateError) as err:
        read_repo_state(repo)
    assert_diagnostic_error(err.value)
    assert "invented index corruption" in str(err.value)


def test_the_state_is_a_value() -> None:
    assert RepoState(commit="f" * 40) == RepoState(commit="f" * 40, changes=(), patch=b"")
