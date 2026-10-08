"""Small real git repositories under tmp_path, for the working-tree recording tests.

`make_repo` commits three files; `make_dirty` then modifies one, deletes one, adds an untracked text file
and an untracked binary file, so one state carries every kind of change the manifest records.
"""

import subprocess
from pathlib import Path

# The invented contents a dirty repository carries (none of them is corpus content).
COMMITTED = {"kept.txt": "alpha\n", "edited.txt": "first line\n", "gone.txt": "to be deleted\n"}
EDITED = "first line\nsecond line\n"
UNTRACKED_TEXT = "an untracked note\n"
UNTRACKED_BINARY = bytes(range(256)) * 4
# The status codes `git status --porcelain` gives each change make_dirty makes.
EXPECTED_STATUS = {"edited.txt": " M", "gone.txt": " D", "notes/new.txt": "??", "blob.bin": "??"}


def git(root: Path, *args: str) -> str:
    """Run git in `root`, failing the test on a non-zero exit; return its standard output."""
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def make_repo(root: Path) -> Path:
    """Initialise `root` as a repository with one commit of the COMMITTED files."""
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "commit.gpgsign", "false")
    for name, text in COMMITTED.items():
        (root / name).write_text(text)
    (root / ".gitignore").write_text("ignored.log\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    return root


def make_dirty(root: Path) -> None:
    """Modify, delete and add untracked files in a `make_repo` repository (plus one ignored file)."""
    (root / "edited.txt").write_text(EDITED)
    (root / "gone.txt").unlink()
    (root / "notes").mkdir()
    (root / "notes" / "new.txt").write_text(UNTRACKED_TEXT)
    (root / "blob.bin").write_bytes(UNTRACKED_BINARY)
    (root / "ignored.log").write_text("never recorded\n")


def tree_files(root: Path) -> dict[str, bytes]:
    """Every file under `root` outside `.git`, POSIX path -> bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def apply_to_clone(repo: Path, patch: Path, destination: Path) -> Path:
    """Clone `repo` at HEAD into `destination` and apply `patch` there with `git apply`."""
    subprocess.run(
        ["git", "clone", "-q", str(repo), str(destination)], check=True, capture_output=True, text=True
    )
    git(destination, "apply", "--binary", str(patch))
    return destination
