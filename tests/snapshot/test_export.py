"""The snapshot export: its configuration, the four paired halves, and every way it refuses.

The shared small snapshot (the session `export` fixture, the first three cases of each type, from a fake
clean repository state) is read-only. A test that needs a refusal, a replacement or a check forced to
fail exports its own single-case snapshot under its own tmp_path.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from generators import abn_register
from generators.degradation import degrade_receipt, load_tiers, tier_seed
from generators.drawn_fields import DrawnFieldsCheck
from generators.exporters.eval_projection import load_extraction_schema
from generators.labels.sidecar import SIDECAR_NAME
from generators.overflow_check import LABELLED_VARIED_MODE
from generators.schema import field_names_for
from generators.snapshot import export as export_module
from generators.snapshot.config import HALVES, load_snapshot_config, resolve_output_root
from generators.snapshot.export import SnapshotError, SnapshotSummary, export_snapshot
from generators.snapshot.repo_state import RepoState, RepoStateError, TreeChange
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.snapshot.git_tools import EXPECTED_STATUS, apply_to_clone, tree_files
from tests.eval_export_tools import (
    CONFIG_PATH,
    FAKE_STATE,
    SMALL_CASES,
    STAMP,
    STAMP_DATE,
    clean_state,
    read_csv,
    read_jsonl,
    scratch_config,
    single_case_config,
)

SCHEMA = load_extraction_schema()
CLEAN = ("plain", "distractor", "labelled_varied")
TIERS = load_tiers(CONFIG_PATH)
TYPE_KEYS = {"bank_statement": "bank_statements", "invoice": "invoices", "receipt": "receipts"}


def _listing(root: Path) -> dict[str, bytes]:
    """Every file under `root` (dot-directories included), path -> bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _entries(root: Path) -> list[str]:
    """Every path under `root`, files and directories."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _receipt_config(tmp_path: Path) -> Path:
    return single_case_config(tmp_path, ["receipts"])


def _export(config: Path, state: Callable[[], RepoState] = clean_state) -> SnapshotSummary:
    return export_snapshot(config, today=STAMP_DATE, repo_state=state)


# --- configuration and root ------------------------------------------------------------------------


def test_the_shipped_config_loads() -> None:
    config = load_snapshot_config(CONFIG_PATH)
    assert config.output_root == (REPO_ROOT / "../evaluation_data").resolve()
    assert config.half_prefixes == {half: half for half in HALVES}
    assert config.snapshot_dir_prefix == "snapshot" and config.geometry_dir == "geometry"


def _bad(key: str, value: object) -> Callable[[dict], None]:
    def mutate(config: dict) -> None:
        config["eval_set"][key] = value

    return mutate


@pytest.mark.parametrize(
    ("key", "value", "named"),
    [
        ("snapshot_dir_prefix", "nested/snapshot", "eval_set.snapshot_dir_prefix"),
        ("snapshot_dir_prefix", 7, "eval_set.snapshot_dir_prefix"),
        ("snapshot_dir_prefix", "", "eval_set.snapshot_dir_prefix"),
        ("geometry_dir", "..", "eval_set.geometry_dir"),
        ("geometry_dir", ["geometry"], "eval_set.geometry_dir"),
        ("csv_name", "nested/ground_truth.csv", "eval_set.csv_name"),
        ("jsonl_name", 3, "eval_set.jsonl_name"),
        ("document_types", "receipts", "eval_set.document_types"),
        ("half_prefixes", {"plain": "plain"}, "eval_set.half_prefixes"),
        ("half_prefixes", ["plain", "distractor"], "eval_set.half_prefixes"),
        (
            "half_prefixes",
            {"plain": "same", "distractor": "same", "labelled_varied": "lv", "degraded": "degraded"},
            "eval_set.half_prefixes",
        ),
        (
            "half_prefixes",
            {"plain": "a/b", "distractor": "distractor", "labelled_varied": "lv", "degraded": "degraded"},
            "eval_set.half_prefixes.plain",
        ),
        ("output_root", 5, "eval_set.output_root"),
        ("output_root", "", "eval_set.output_root"),
    ],
)
def test_a_mistyped_or_empty_key_is_refused(tmp_path: Path, key: str, value: object, named: str) -> None:
    path = scratch_config(tmp_path, mutate=_bad(key, value))
    with pytest.raises(ValueError) as err:
        load_snapshot_config(path)
    assert_diagnostic_error(err.value)
    assert named in str(err.value) and str(path.resolve()) in str(err.value)


def test_a_stale_block_holding_only_superseded_keys_is_told_so(tmp_path: Path) -> None:
    """The superseded-key refusal comes before the presence check, so a stale block is not told that
    output_root is missing."""

    def stale(config: dict) -> None:
        keep = ("document_types", "csv_name", "jsonl_name")
        config["eval_set"] = {k: v for k, v in config["eval_set"].items() if k in keep}
        config["eval_set"]["clean_dir_prefix"] = "synthetic"
        config["eval_set"]["degraded_dir_prefix"] = "degraded"

    path = scratch_config(tmp_path, mutate=stale)
    with pytest.raises(ValueError) as err:
        load_snapshot_config(path)
    assert_diagnostic_error(err.value)
    assert "superseded" in str(err.value) and "output_root" not in str(err.value).split("Expected")[0]


@pytest.mark.parametrize("key", ["clean_dir_prefix", "degraded_dir_prefix"])
def test_a_superseded_key_is_refused_naming_its_replacement(tmp_path: Path, key: str) -> None:
    path = scratch_config(tmp_path, mutate=_bad(key, "synthetic"))
    with pytest.raises(ValueError) as err:
        load_snapshot_config(path)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert f"eval_set.{key}" in message and "half_prefixes" in message and str(path.resolve()) in message


def test_a_missing_root_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "generation_config.yml"
    with pytest.raises(ValueError) as err:
        resolve_output_root(str(tmp_path / "absent"), config_path=config)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "does not exist" in message and f"{config.resolve()} -> eval_set.output_root" in message
    assert "output_root: ../evaluation_data" in message


def test_a_root_inside_the_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as err:
        resolve_output_root("tests", config_path=tmp_path / "generation_config.yml")
    assert_diagnostic_error(err.value)
    assert "inside the repository" in str(err.value)


def test_a_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    afile = tmp_path / "afile"
    afile.write_text("")
    with pytest.raises(ValueError) as err:
        resolve_output_root(str(afile), config_path=tmp_path / "generation_config.yml")
    assert_diagnostic_error(err.value)
    assert "is not a directory" in str(err.value)


def test_a_relative_root_resolves_against_the_repository_root(tmp_path: Path) -> None:
    assert resolve_output_root("../evaluation_data", config_path=tmp_path / "x.yml") == (
        REPO_ROOT.parent / "evaluation_data"
    )


# --- the small snapshot ----------------------------------------------------------------------------


def test_four_halves_of_nine_images(export: SnapshotSummary) -> None:
    for half in HALVES:
        assert len(list(export.half_dirs[half].glob("*.png"))) == 9, half
        assert export.total(half) == 9
    for half in CLEAN:
        assert export.counts[half] == dict.fromkeys(TYPE_KEYS, SMALL_CASES)
    assert export.counts["degraded"] == {"receipt": SMALL_CASES * len(TIERS)}


def test_the_clean_halves_pair_by_filename_and_share_their_ground_truth(export: SnapshotSummary) -> None:
    names = [{p.name for p in export.half_dirs[half].glob("*.png")} for half in CLEAN]
    assert names[0] == names[1] == names[2]
    for name in ("ground_truth.csv", "ground_truth.jsonl"):
        contents = {(export.half_dirs[half] / name).read_bytes() for half in CLEAN}
        assert len(contents) == 1, name


def test_the_halves_differ_where_they_should(export: SnapshotSummary) -> None:
    """Distractors change every page; label variation changes some receipts and invoices, never a statement."""
    plain, distractor, varied = (export.half_dirs[half] for half in CLEAN)
    for image in plain.glob("*.png"):
        assert image.read_bytes() != (distractor / image.name).read_bytes(), image.name
    for image in distractor.glob("*_bank_statement.png"):
        assert image.read_bytes() == (varied / image.name).read_bytes(), image.name


def test_the_sidecar_holds_one_row_per_labelled_varied_image(export: SnapshotSummary) -> None:
    directory = export.half_dirs["labelled_varied"]
    rows = [json.loads(line) for line in (directory / SIDECAR_NAME).read_text().splitlines()]
    files = [row["image_file"] for row in rows]
    assert files == sorted(p.name for p in directory.glob("*.png"))
    assert list(export.sidecar_rows) == rows
    for row in rows:
        if row["doc_type"] == "bank_statement":
            assert row["styles"] == {}
    assert not any(
        (export.half_dirs[half] / SIDECAR_NAME).exists() for half in HALVES if half != "labelled_varied"
    )


def test_degraded_receipts_photograph_the_distractor_render(export: SnapshotSummary) -> None:
    """The light and moderate tiers equal degrade_receipt of the distractor render, not of the plain one.

    The heavy tier is not bit-reproducible (Decision 1), so it is not compared.
    """
    receipts = yaml.safe_load((REPO_ROOT / "ground_truth" / "receipts.yml").read_text())
    case = next(iter(receipts))
    seed = receipts[case]["degradation_seed"]
    distractor = Image.open(export.half_dirs["distractor"] / f"{case}_receipt.png").convert("RGB")
    plain = Image.open(export.half_dirs["plain"] / f"{case}_receipt.png").convert("RGB")
    for index, tier in enumerate(TIERS[:2]):
        shipped = np.asarray(Image.open(export.half_dirs["degraded"] / f"{case}_receipt_{tier.suffix}.png"))
        expected = np.asarray(degrade_receipt(distractor, tier, tier_seed(seed, index)))
        assert np.array_equal(shipped, expected), tier.name
        from_plain = np.asarray(degrade_receipt(plain, tier, tier_seed(seed, index)))
        assert shipped.shape != from_plain.shape or not np.array_equal(shipped, from_plain), tier.name


def test_the_file_set_is_exactly_the_layout(export: SnapshotSummary) -> None:
    stamp_dirs = {f"{half}_{STAMP}" for half in HALVES}
    expected = set(stamp_dirs) | {"geometry", "geometry/plain.jsonl", "geometry/distractor.jsonl"}
    expected |= {"geometry/labelled_varied.jsonl", f"labelled_varied_{STAMP}/{SIDECAR_NAME}"}
    expected |= {"README.md", "MANIFEST.json", "SHA256SUMS", "tools", "tools/verify_snapshot.py"}
    expected |= {"tools/check_extractions_match_gt.py", "tools/score_extractions.ipynb"}
    for half in HALVES:
        directory = export.half_dirs[half]
        expected |= {f"{directory.name}/ground_truth.csv", f"{directory.name}/ground_truth.jsonl"}
        expected |= {f"{directory.name}/{p.name}" for p in directory.glob("*.png")}
    assert set(_entries(export.path)) == expected
    assert not list(export.path.parent.glob(".*.partial"))


def test_geometry_holds_scored_boxes_only_under_generic_filenames(export: SnapshotSummary) -> None:
    for half in CLEAN:
        rows = read_jsonl(export.path / "geometry" / f"{half}.jsonl")
        assert [row["image_file"] for row in rows] == sorted(
            p.name for p in export.half_dirs[half].glob("*.png")
        )
        for row in rows:
            assert sorted(row) == ["boxes", "case_id", "height", "image_file", "width"]
            scored = set(
                field_names_for(TYPE_KEYS[row["image_file"].removesuffix(".png").split("_", 1)[1]])
            )
            assert row["boxes"], row["image_file"]
            for key in row["boxes"]:
                assert re.sub(r"\[\d+\]$", "", key) in scored, (half, row["image_file"], key)


def test_nothing_leaks_into_the_snapshot(export: SnapshotSummary) -> None:
    """No local path, register file, TFN-shaped key or label key; ground-truth columns are the schema's."""
    banned = [str(Path.home()), str(REPO_ROOT), "abn_register", ".npy"]
    for path in export.path.rglob("*"):
        if not path.is_file() or path.suffix == ".png":
            continue
        text = path.read_text()
        for token in banned:
            assert token not in text, (path, token)
        assert not re.search(r"tfn|tax file", text, re.IGNORECASE), path
        if path.name.startswith("ground_truth"):
            assert "LABEL_" not in text and "label_styles" not in text, path
    for half in HALVES:
        header, _ = read_csv(export.half_dirs[half] / "ground_truth.csv")
        types = {"receipt"} if half == "degraded" else set(TYPE_KEYS)
        expected = {field for doc_type in types for field in SCHEMA.get_extraction_fields(doc_type)}
        assert set(header) == {"image_file", *expected}, half


def test_plain_renders_out_of_bounds_are_reported_not_refused(export: SnapshotSummary) -> None:
    """Every plain render is measured; no plain render carries distractor text, below every share floor."""
    from generators.distractors.config import load_distractor_config

    bounds = load_distractor_config().density
    assert set(export.density) == set(CLEAN)
    for half in CLEAN:
        assert set(export.density[half]) == {p.name for p in export.half_dirs[half].glob("*.png")}
    below = [
        name
        for name, report in export.density["plain"].items()
        if report.distractor_share < bounds[name.removesuffix(".png").split("_", 1)[1]].distractor_share_min
    ]
    assert below, "no plain render is out of bounds, so this test proves nothing"


# --- refusals and publishing -----------------------------------------------------------------------


def test_a_check_failing_before_rendering_leaves_the_root_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    before = _entries(root)

    def renders_nothing(*args: object, **kwargs: object) -> None:
        raise AssertionError("rendered after a pre-render check failed")

    monkeypatch.setattr(
        export_module, "validate_entry", lambda case_id, entry: [f"{case_id}: invented failure"]
    )
    with pytest.raises(SnapshotError) as err:
        export_snapshot(
            config, today=STAMP_DATE, repo_state=clean_state, renderers={"receipts": renders_nothing}
        )
    assert_diagnostic_error(err.value)
    assert "invented failure" in str(err.value) and "before rendering" in str(err.value)
    assert _entries(root) == before


def test_a_mid_export_failure_leaves_no_staging_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("invented interruption")

    monkeypatch.setattr(export_module, "degrade_receipt", broken)
    with pytest.raises(RuntimeError, match="invented interruption"):
        _export(config)
    assert _entries(root) == []


def test_an_earlier_snapshot_of_the_same_name_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's ruling: a same-day re-export replaces the earlier snapshot, and a leftover staging
    directory is cleared. The register loader raising shows the export never reads the ABN register,
    and the repository's own status is unchanged by an export."""
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    stale = root / f"snapshot_{STAMP}" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("earlier")
    leftover = root / f".snapshot_{STAMP}.partial" / "half.txt"
    leftover.parent.mkdir()
    leftover.write_text("interrupted")

    def no_register(*args: object, **kwargs: object) -> None:
        raise AssertionError("the export read the ABN register")

    monkeypatch.setattr(abn_register, "load_abn_register", no_register)
    status = ["git", "-C", str(REPO_ROOT), "status", "--porcelain"]
    before = subprocess.run(status, capture_output=True, text=True, check=True).stdout
    summary = _export(config)
    assert subprocess.run(status, capture_output=True, text=True, check=True).stdout == before
    assert summary.path == stale.parent
    assert not stale.exists() and not leftover.parent.exists()
    assert summary.total("plain") == 1 and summary.total("degraded") == len(TIERS)


# The state the mid-export tests start from: one modified file, at the fake commit.
_DIRTY_START = RepoState(
    commit=FAKE_STATE.commit,
    changes=(TreeChange("config/invented.yml", " M", "a" * 64),),
    patch=b"invented patch\n",
)


@pytest.mark.parametrize(
    ("start", "end", "named"),
    [
        (FAKE_STATE, RepoState(commit="fedcba9876543210fedcba9876543210fedcba98"), "fedcba98765432"),
        (
            FAKE_STATE,
            RepoState(
                commit=FAKE_STATE.commit,
                changes=(TreeChange("generators/receipt.py", " M", "b" * 64),),
                patch=b"x",
            ),
            "generators/receipt.py",
        ),
        (
            _DIRTY_START,
            RepoState(
                commit=FAKE_STATE.commit,
                changes=(TreeChange("config/invented.yml", " M", "c" * 64),),
                patch=b"edited again\n",
            ),
            "config/invented.yml",
        ),
        (_DIRTY_START, FAKE_STATE, "config/invented.yml"),
    ],
    ids=["new_commit", "newly_dirty", "edited_again", "reverted"],
)
def test_a_tree_changed_during_the_export_fails_fast_and_publishes_nothing(
    tmp_path: Path, start: RepoState, end: RepoState, named: str
) -> None:
    """Review Focus 1: a commit made, or a file edited or reverted, while rendering leaves no snapshot,
    since it would mix two states. A tree that was already modified at the start is not the failure."""
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    states = iter([start, end])
    with pytest.raises(SnapshotError) as err:
        _export(config, lambda: next(states))
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "changed while the snapshot was rendered" in message and named in message
    assert "would mix two states" in message and "leave the repository untouched" in message
    assert _entries(root) == []


def test_a_dirty_tree_exports_when_it_stays_the_same(tmp_path: Path) -> None:
    """Decision 4 as revised: a modified tree is recorded, not refused."""
    config = _receipt_config(tmp_path)
    summary = _export(config, lambda: _DIRTY_START)
    manifest = json.loads((summary.path / "MANIFEST.json").read_text())
    assert manifest["dirty"] is True
    assert manifest["working_tree_changes"] == [
        {"path": "config/invented.yml", "sha256": "a" * 64, "status": " M"}
    ]
    assert (summary.path / "working_tree.patch").read_bytes() == b"invented patch\n"


def test_a_dirty_export_records_every_change_with_its_hash(
    dirty_export: tuple[SnapshotSummary, Path],
) -> None:
    summary, repo = dirty_export
    manifest = json.loads((summary.path / "MANIFEST.json").read_text())
    assert manifest["dirty"] is True
    recorded = {row["path"]: row for row in manifest["working_tree_changes"]}
    assert {path: row["status"] for path, row in recorded.items()} == EXPECTED_STATUS
    assert recorded["gone.txt"]["sha256"] == "deleted"
    for path, row in recorded.items():
        if path != "gone.txt":
            assert row["sha256"] == hashlib.sha256((repo / path).read_bytes()).hexdigest()
    assert "ignored.log" not in recorded
    assert [row["path"] for row in manifest["working_tree_changes"]] == sorted(recorded)


def test_a_dirty_export_carries_a_patch_that_reproduces_the_tree(
    dirty_export: tuple[SnapshotSummary, Path], tmp_path: Path
) -> None:
    summary, repo = dirty_export
    patch = summary.path / "working_tree.patch"
    manifest = json.loads((summary.path / "MANIFEST.json").read_text())
    assert manifest["files"]["working_tree.patch"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert "  working_tree.patch" in (summary.path / "SHA256SUMS").read_text()
    clone = apply_to_clone(repo, patch, tmp_path / "clone")
    expected = {path: data for path, data in tree_files(repo).items() if path != "ignored.log"}
    assert tree_files(clone) == expected


def test_a_clean_export_has_no_patch_and_no_change_list(export: SnapshotSummary) -> None:
    manifest = json.loads((export.path / "MANIFEST.json").read_text())
    assert manifest["dirty"] is False and "working_tree_changes" not in manifest
    assert not (export.path / "working_tree.patch").exists()
    assert "working_tree.patch" not in manifest["files"]


def test_a_tightened_density_bound_refuses_the_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 3: a bound tightened in a scratch distractor catalogue refuses, nothing published."""
    catalogue = yaml.safe_load((REPO_ROOT / "config" / "distractors.yml").read_text())
    catalogue["density"]["receipt"]["scored_ink_ratio_max"] = 0.01
    scratch = tmp_path / "distractors.yml"
    scratch.write_text(yaml.safe_dump(catalogue, sort_keys=False))
    monkeypatch.setattr(export_module, "DISTRACTORS_PATH", scratch)
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    with pytest.raises(SnapshotError) as err:
        _export(config)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "while rendering" in message and "scored_ink_ratio" in message
    assert "(with distractors)" in message and f"({LABELLED_VARIED_MODE})" in message
    assert _entries(root) == []


def test_a_drawn_field_failure_on_a_labelled_varied_render_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 3: the drawn-field guard runs on the labelled varied render, and its failure refuses."""
    real = DrawnFieldsCheck.__call__

    def forced(self: DrawnFieldsCheck, case_id: str, layout_id: str, geometry: dict) -> None:
        real(self, case_id, layout_id, geometry)
        if self.render == LABELLED_VARIED_MODE:
            self.errors.append(f"{case_id}: invented drawn-field failure ({self.render} render)")

    monkeypatch.setattr(DrawnFieldsCheck, "__call__", forced)
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    with pytest.raises(SnapshotError) as err:
        _export(config)
    assert_diagnostic_error(err.value)
    assert "invented drawn-field failure (labelled varied render)" in str(err.value)
    assert _entries(root) == []


def _seed_earlier(root: Path) -> Path:
    stale = root / f"snapshot_{STAMP}" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("earlier")
    return stale


def test_a_failed_swap_restores_the_earlier_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: the earlier snapshot is renamed aside, not deleted, until the new one is in place."""
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    stale = _seed_earlier(root)
    real_rename = Path.rename

    def failing(self: Path, target: Path) -> Path:
        if self.name.endswith(".partial"):
            raise OSError("invented rename failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing)
    with pytest.raises(OSError, match="invented rename failure"):
        _export(config)
    assert stale.read_text() == "earlier"
    assert _entries(root) == [stale.parent.name, f"{stale.parent.name}/stale.txt"]


def test_a_failed_run_leaves_the_earlier_snapshot_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    stale = _seed_earlier(root)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("invented interruption")

    monkeypatch.setattr(export_module, "degrade_receipt", broken)
    with pytest.raises(RuntimeError, match="invented interruption"):
        _export(config)
    assert stale.read_text() == "earlier"
    assert _entries(root) == [stale.parent.name, f"{stale.parent.name}/stale.txt"]


def test_a_staging_path_that_is_a_file_is_refused(tmp_path: Path) -> None:
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    blocker = root / f".snapshot_{STAMP}.partial"
    blocker.write_text("not a directory")
    with pytest.raises(SnapshotError) as err:
        _export(config)
    assert_diagnostic_error(err.value)
    assert str(blocker) in str(err.value)
    assert blocker.read_text() == "not a directory" and _entries(root) == [blocker.name]


def test_git_failing_on_the_second_read_publishes_nothing(tmp_path: Path) -> None:
    config = _receipt_config(tmp_path)
    root = load_snapshot_config(config).output_root
    reads = iter([FAKE_STATE])

    def state() -> RepoState:
        try:
            return next(reads)
        except StopIteration:
            raise RepoStateError("invented: git vanished mid-export") from None

    with pytest.raises(RepoStateError, match="git vanished"):
        _export(config, state)
    assert _entries(root) == []
