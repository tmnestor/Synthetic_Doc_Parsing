"""The repository state a snapshot is exported from: its commit, and any tracked file changed since.

A snapshot names the commit that produced it, so the export refuses a tree with tracked changes
(Decision 4): `git status --porcelain --untracked-files=no` must print nothing. Untracked files are not
refused; the code and YAML the export reads are tracked, and an untracked file affects nothing unless a
tracked one names it.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

# The most changed paths a refusal lists; the total is always given.
_LISTED_PATHS = 10


class RepoStateError(RuntimeError):
    """Git or the repository is missing, or the tree carries tracked changes."""


@dataclass(frozen=True)
class RepoState:
    """A repository's state at one moment.

    Attributes:
        commit: The full id of HEAD.
        dirty_paths: Tracked paths with changes (staged or not), as `git status` names them.
    """

    commit: str
    dirty_paths: tuple[str, ...]


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in `repo_root`, refusing four-part when git itself is missing."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as err:
        raise RepoStateError(
            "Git is not available.\n"
            "  What:     the `git` executable was not found, so the commit a snapshot comes from cannot "
            "be read.\n"
            f"  Where:    the eval-set command line, run in {repo_root}\n"
            "  Expected: `git rev-parse HEAD` prints the commit, e.g. a 40-character hexadecimal id.\n"
            "  Recover:  install git or put it on PATH, then export again."
        ) from err


def read_repo_state(repo_root: Path) -> RepoState:
    """Read HEAD and the tracked changes of the repository at `repo_root`.

    Args:
        repo_root: The repository's root directory.

    Returns:
        The state.

    Raises:
        RepoStateError: Git is missing, or `repo_root` is not a repository with a commit.
    """
    head = _git(repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RepoStateError(
            "No repository to export from.\n"
            f"  What:     `git rev-parse HEAD` failed in {repo_root}: {head.stderr.strip() or 'no output'}.\n"
            f"  Where:    {repo_root}\n"
            "  Expected: a git repository with at least one commit, so the snapshot can name the commit "
            "that produced it.\n"
            "  Recover:  run the export from the generator's repository checkout, after committing."
        )
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise RepoStateError(
            "Cannot read the repository's changes.\n"
            f"  What:     `git status` failed in {repo_root}: {status.stderr.strip() or 'no output'}.\n"
            f"  Where:    {repo_root}\n"
            "  Expected: git status --porcelain --untracked-files=no prints nothing.\n"
            "  Recover:  repair the repository, then export again."
        )
    paths = tuple(line[3:] for line in status.stdout.splitlines() if line.strip())
    return RepoState(commit=head.stdout.strip(), dirty_paths=paths)


def require_clean(state: RepoState, *, repo_root: Path) -> None:
    """Refuse a state with tracked changes, listing them.

    Args:
        state: The state read before (or after) rendering.
        repo_root: The repository's root, named in the diagnostic.

    Raises:
        RepoStateError: `state` carries tracked changes.
    """
    if not state.dirty_paths:
        return
    listed = ", ".join(state.dirty_paths[:_LISTED_PATHS])
    more = len(state.dirty_paths) - _LISTED_PATHS
    tail = f" (and {more} more)" if more > 0 else ""
    raise RepoStateError(
        "The working tree has tracked changes.\n"
        f"  What:     {len(state.dirty_paths)} tracked path(s) differ from {state.commit[:12] or 'HEAD'}: "
        f"{listed}{tail}; a snapshot must name the commit that produced it.\n"
        f"  Where:    {repo_root}\n"
        "  Expected: git status --porcelain --untracked-files=no prints nothing.\n"
        "  Recover:  commit or stash, then export again."
    )
