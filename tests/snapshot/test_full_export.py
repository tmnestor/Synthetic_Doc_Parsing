"""One full-corpus snapshot export, from a fake clean repository state into tmp_path (slow).

Module-scoped so every whole-corpus assertion reads the same export: four halves of 165 images, 55 per
type in each clean half, the eval-format baseline's pinned counts and columns on the plain half, every
render's checks passed (the export refuses on any failure, so a published snapshot is the proof; the
density reports show every render was measured), and the snapshot verifying against its own manifest.
"""

import shutil
from pathlib import Path

import pytest

from generators.snapshot.config import HALVES
from generators.snapshot.export import SnapshotSummary
from scripts.verify_snapshot import verify
from tests.eval_export_tools import (
    assert_eval_export_matches_baseline,
    capture_shape,
    export_into,
    load_baseline,
    read_jsonl,
)

pytestmark = pytest.mark.slow

CLEAN = ("plain", "distractor", "labelled_varied")


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> SnapshotSummary:
    """The whole corpus exported once (several minutes)."""
    return export_into(tmp_path_factory.mktemp("full_snapshot"))


def test_the_full_corpus_exports_four_halves_of_165(full: SnapshotSummary) -> None:
    for half in HALVES:
        assert len(list(full.half_dirs[half].glob("*.png"))) == 165, half
        assert full.total(half) == 165
    for half in CLEAN:
        assert full.counts[half] == {"bank_statement": 55, "invoice": 55, "receipt": 55}, half
    assert full.counts["degraded"] == {"receipt": 165}


def test_the_eval_format_baseline_holds_on_the_plain_half(full: SnapshotSummary) -> None:
    plain = full.half_dirs["plain"]
    assert_eval_export_matches_baseline(plain, load_baseline())
    assert capture_shape(plain) == {k: v for k, v in load_baseline().items() if k != "_comment"}


def test_every_render_was_checked_and_paired(full: SnapshotSummary) -> None:
    """Published only because every check passed; every clean render was measured and is paired."""
    names = {p.name for p in full.half_dirs["plain"].glob("*.png")}
    for half in CLEAN:
        assert set(full.density[half]) == names, half
        assert {p.name for p in full.half_dirs[half].glob("*.png")} == names, half
    assert [row["image_file"] for row in full.sidecar_rows] == sorted(names)
    for half in CLEAN:
        rows = read_jsonl(full.path / "geometry" / f"{half}.jsonl")
        assert len(rows) == 165, half
    assert not list(Path(full.path.parent).glob(".*.partial"))


def test_the_full_snapshot_verifies_and_one_flipped_byte_fails(
    full: SnapshotSummary, tmp_path: Path
) -> None:
    """The stage's exit criterion on the whole corpus: verify passes, then fails on one flipped byte."""
    assert verify(full.path) == []
    copy = tmp_path / full.path.name
    shutil.copytree(full.path, copy)
    image = sorted(copy.glob("degraded_*/*.png"))[-1]
    data = bytearray(image.read_bytes())
    data[-100] ^= 0xFF
    image.write_bytes(bytes(data))
    assert verify(copy) == [f"changed file: {image.relative_to(copy).as_posix()}"]
