"""The repository state a snapshot is exported from: its commit, and every change the working tree carries.

A snapshot names the commit that produced it. The export no longer refuses a modified tree (Decision 4,
as revised by the project owner on 9 October 2026, so config can be edited on a sandbox and still
exported); it records the modification instead. `read_repo_state` reads every path
`git status --porcelain --untracked-files=all` reports (changed tracked paths, and untracked paths that
are not ignored), each with its status code and the SHA-256 of its current content, plus a binary-safe
patch: `git diff HEAD --binary`, with each untracked file appended as a new-file diff. The snapshot
carries the patch as `working_tree.patch` and the change list in its manifest, so the snapshot alone
shows exactly what differed from the recorded commit.

The state is read twice, at the start and before publishing. Two reads that differ mean the tree changed
during the export, and the snapshot would mix two states; that alone is refused.
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The hash recorded for a changed path that no longer exists in the working tree.
DELETED = "deleted"
# The hash recorded for a reported path that is a directory (a nested repository, say), not a file.
DIRECTORY = "directory"

# git status's code for an untracked path.
UNTRACKED = "??"

_CHUNK = 1 << 20

# Options that keep a diff byte-exact and applicable whatever the user's git configuration says.
_DIFF_OPTIONS = (
    "--binary",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)


class RepoStateError(RuntimeError):
    """Git or the repository is missing, or the repository's state cannot be read."""


@dataclass(frozen=True)
class TreeChange:
    """One path `git status --porcelain` reports.

    Attributes:
        path: The path relative to the repository root, POSIX.
        status: Its two-character porcelain status code, e.g. ` M`, `D `, `??`.
        sha256: The hex SHA-256 of its current content, `deleted` when it no longer exists, or
            `directory` when git reports a directory.
        original_path: For a rename or copy, the path it came from; empty otherwise.
    """

    path: str
    status: str
    sha256: str
    original_path: str = ""

    def as_record(self) -> dict[str, str]:
        """The manifest's record of this change, keys sorted."""
        record = {"path": self.path, "sha256": self.sha256, "status": self.status}
        if self.original_path:
            record["original_path"] = self.original_path
        return dict(sorted(record.items()))


@dataclass(frozen=True)
class RepoState:
    """A repository's state at one moment.

    Attributes:
        commit: The full id of HEAD.
        changes: Every changed tracked path and every untracked, not-ignored path, sorted by path.
        patch: `git diff HEAD --binary`, followed by each untracked file as a new-file diff; empty when
            the tree is clean.
    """

    commit: str
    changes: tuple[TreeChange, ...] = ()
    patch: bytes = b""

    @property
    def dirty(self) -> bool:
        """Whether the working tree differs from the commit in any reported path."""
        return bool(self.changes)


def _missing_git(repo_root: Path, err: FileNotFoundError) -> RepoStateError:
    return RepoStateError(
        "Git is not available.\n"
        "  What:     the `git` executable was not found, so the commit a snapshot comes from cannot "
        "be read.\n"
        f"  Where:    the eval-set command line, run in {repo_root}\n"
        "  Expected: `git rev-parse HEAD` prints the commit, e.g. a 40-character hexadecimal id.\n"
        "  Recover:  install git or put it on PATH, then export again."
    )


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command in `repo_root` as text, refusing four-part when git itself is missing."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as err:
        raise _missing_git(repo_root, err) from err


def _git_bytes(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run one git command in `repo_root`, keeping its output as bytes (a patch is binary-safe)."""
    try:
        return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, check=False)
    except FileNotFoundError as err:
        raise _missing_git(repo_root, err) from err


def _unreadable(repo_root: Path, command: str, stderr: str) -> RepoStateError:
    return RepoStateError(
        "Cannot read the repository's working-tree changes.\n"
        f"  What:     `{command}` failed in {repo_root}: {stderr.strip() or 'no output'}.\n"
        f"  Where:    {repo_root}\n"
        f"  Expected: `{command}` succeeds, e.g. printing ` M config/generation_config.yml` or nothing.\n"
        "  Recover:  repair the repository (run the command by hand to see why), then export again."
    )


def _content_sha256(path: Path) -> str:
    """The SHA-256 of a path's current content: `deleted`, `directory`, a link's target, or its bytes."""
    if path.is_symlink():
        return hashlib.sha256(os.readlink(path).encode()).hexdigest()
    if not path.exists():
        return DELETED
    if path.is_dir():
        return DIRECTORY
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(path: str, excluded: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in excluded)


def _parse_status(output: str) -> list[tuple[str, str, str]]:
    """Parse `git status --porcelain -z` into (status, path, original path) triples."""
    fields = output.split("\0")
    parsed = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        original = ""
        if status[0] in "RC" or status[1] in "RC":
            original = fields[index]
            index += 1
        parsed.append((status, path.rstrip("/"), original))
    return parsed


def read_repo_state(repo_root: Path, *, exclude: tuple[Path, ...] = ()) -> RepoState:
    """Read HEAD, every working-tree change and the patch of the repository at `repo_root`.

    Args:
        repo_root: The repository's root directory.
        exclude: Directories whose paths are left out of the change list (the snapshot's output root,
            should it sit inside the repository).

    Returns:
        The state.

    Raises:
        RepoStateError: Git is missing, `repo_root` is not a repository with a commit, or its status or
            diff cannot be read.
    """
    head = _git(repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RepoStateError(
            "No repository to export from.\n"
            f"  What:     `git rev-parse HEAD` failed in {repo_root}: "
            f"{head.stderr.strip() or 'no output'}.\n"
            f"  Where:    {repo_root}\n"
            "  Expected: a git repository with at least one commit, so the snapshot can name the commit "
            "that produced it.\n"
            "  Recover:  run the export from the generator's repository checkout, after committing."
        )
    root = repo_root.resolve()
    excluded = tuple(
        path.resolve().relative_to(root).as_posix()
        for path in exclude
        if path.resolve().is_relative_to(root)
    )
    status_args = ("--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all")
    status = _git(repo_root, *status_args)
    if status.returncode != 0:
        raise _unreadable(repo_root, "git " + " ".join(status_args), status.stderr)
    changes = tuple(
        sorted(
            (
                TreeChange(path, code, _content_sha256(root / path), original)
                for code, path, original in _parse_status(status.stdout)
                if not _excluded(path, excluded)
            ),
            key=lambda change: (change.path, change.status),
        )
    )
    if not changes:
        return RepoState(commit=head.stdout.strip(), changes=(), patch=b"")

    diff = _git_bytes(repo_root, "diff", "HEAD", *_DIFF_OPTIONS)
    if diff.returncode != 0:
        raise _unreadable(repo_root, "git diff HEAD --binary", diff.stderr.decode(errors="replace"))
    parts = [diff.stdout]
    for change in changes:
        if change.status != UNTRACKED or change.sha256 == DIRECTORY:
            continue
        # `--no-index` exits 1 when the files differ, which a new file always does.
        new_file = _git_bytes(
            repo_root, "diff", "--no-index", *_DIFF_OPTIONS, "--", "/dev/null", change.path
        )
        if new_file.returncode not in (0, 1):
            raise _unreadable(
                repo_root,
                f"git diff --no-index --binary -- /dev/null {change.path}",
                new_file.stderr.decode(errors="replace"),
            )
        parts.append(new_file.stdout)
    return RepoState(commit=head.stdout.strip(), changes=changes, patch=b"".join(parts))
