"""`scripts/verify_snapshot.py`: a snapshot checked against its own manifest (Review Focus 4).

Every test copies the shared small snapshot into its own tmp_path before touching it.
"""

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from generators.snapshot.export import SnapshotSummary
from scripts import verify_snapshot
from scripts.verify_snapshot import inspect, verify
from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "verify_snapshot.py"


@pytest.fixture
def copy(export: SnapshotSummary, tmp_path: Path) -> Path:
    """A private copy of the small snapshot."""
    target = tmp_path / export.path.name
    shutil.copytree(export.path, target)
    return target


def _image(root: Path) -> Path:
    return next(iter(sorted(root.glob("plain_*/*.png"))))


def _rewrite_manifest(root: Path, edit) -> None:
    path = root / "MANIFEST.json"
    manifest = json.loads(path.read_text())
    edit(manifest)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _run(
    root: Path, *, cwd: Path | None = None, argv: list[str] | None = None
) -> subprocess.CompletedProcess:
    """Run the shipped copy under a bare interpreter (-I: isolated, no site-packages, no user paths)."""
    args = argv if argv is not None else [str(root)]
    return subprocess.run(
        [sys.executable, "-I", "-S", str(root / "tools" / "verify_snapshot.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def test_an_untouched_copy_passes(copy: Path) -> None:
    assert verify(copy) == []
    result = _run(copy)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PASS:")


def test_the_default_snapshot_is_the_parent_of_tools(copy: Path, tmp_path: Path) -> None:
    result = _run(copy, cwd=tmp_path, argv=[])
    assert result.returncode == 0, result.stderr


def test_one_flipped_byte_in_an_image_fails(copy: Path) -> None:
    """The stage's exit criterion."""
    image = _image(copy)
    data = bytearray(image.read_bytes())
    data[len(data) // 2] ^= 0x01
    image.write_bytes(bytes(data))
    relative = image.relative_to(copy).as_posix()
    assert verify(copy) == [f"changed file: {relative}"]
    result = _run(copy)
    assert result.returncode == 1 and relative in result.stderr


def test_a_dirty_snapshot_verifies_on_its_own_and_ships_no_patch(
    dirty_export: tuple[SnapshotSummary, Path], tmp_path: Path
) -> None:
    """The patch stays beside the snapshot, so a dirty snapshot copied alone verifies and carries none."""
    summary, _ = dirty_export
    copy = tmp_path / summary.path.name
    shutil.copytree(summary.path, copy)
    assert verify(copy) == [] and _run(copy).returncode == 0
    assert not list(copy.rglob("working_tree.patch"))


def test_a_missing_file_fails(copy: Path) -> None:
    image = _image(copy)
    image.unlink()
    relative = image.relative_to(copy).as_posix()
    problems = verify(copy)
    assert f"missing file: {relative}" in problems
    assert any(problem.startswith("count mismatch: plain_") for problem in problems)
    assert _run(copy).returncode == 1


def test_an_extra_file_fails(copy: Path) -> None:
    (copy / "figures").mkdir()
    (copy / "figures" / "f1.png").write_bytes(b"invented")
    assert verify(copy) == ["extra file: figures/f1.png"]
    result = _run(copy)
    assert result.returncode == 1 and "figures/f1.png" in result.stderr


def test_a_ds_store_is_tolerated_with_a_note(copy: Path) -> None:
    (copy / ".DS_Store").write_bytes(b"\0")
    problems, notes = inspect(copy)
    assert problems == [] and notes == ["tolerated .DS_Store: .DS_Store"]
    result = _run(copy)
    assert result.returncode == 0 and "note: tolerated .DS_Store" in result.stdout


def test_a_tampered_checksum_file_fails(copy: Path) -> None:
    sums = copy / "SHA256SUMS"
    lines = sums.read_text().splitlines()
    digest, _, path = lines[0].partition("  ")
    lines[0] = f"{'0' * 64}  {path}"
    sums.write_text("\n".join(lines) + "\n")
    assert verify(copy) == [f"SHA256SUMS disagrees with MANIFEST.json: {path}"]
    assert _run(copy).returncode == 1


def test_an_edited_count_fails(copy: Path) -> None:
    def edit(manifest: dict) -> None:
        manifest["counts"]["distractor"]["total"] += 1

    _rewrite_manifest(copy, edit)
    problems = verify(copy)
    assert len(problems) == 1 and problems[0].startswith("count mismatch: distractor_")
    assert _run(copy).returncode == 1


@pytest.mark.parametrize("bad", ["../outside.png", "/etc/hosts", "plain/../../x.png"])
def test_a_manifest_path_that_climbs_out_or_is_absolute_fails(copy: Path, bad: str) -> None:
    def edit(manifest: dict) -> None:
        manifest["files"][bad] = "0" * 64

    _rewrite_manifest(copy, edit)
    problems = verify(copy)
    assert f"unsafe manifest path: {bad}" in problems
    result = _run(copy)
    assert result.returncode == 1 and bad in result.stderr


def test_a_missing_manifest_fails(copy: Path) -> None:
    (copy / "MANIFEST.json").unlink()
    assert verify(copy)[0].startswith("missing manifest")
    assert _run(copy).returncode == 1


def test_many_problems_are_capped_with_the_total(copy: Path) -> None:
    for index in range(verify_snapshot.LISTED + 5):
        (copy / f"extra{index:03d}.txt").write_text("x")
    problems = verify(copy)
    assert len(problems) == verify_snapshot.LISTED + 1
    assert problems[-1].endswith(f"and 5 more ({verify_snapshot.LISTED + 5} in all)")


def test_the_verifier_imports_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT.read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module is not None
            modules.add(node.module.split(".")[0])
    assert modules and modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)
