"""MANIFEST.json, SHA256SUMS and the shipped tools, read from the shared small snapshot (Decisions 9-12)."""

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from generators.labels.keys import LABEL_STYLES
from generators.snapshot.config import HALVES
from generators.snapshot.export import SHIPPED_NOTEBOOK, SnapshotSummary
from generators.snapshot.manifest import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    build_manifest,
    manifest_text,
    sha256_file,
    tracked_inputs,
)
from tests.conftest import REPO_ROOT
from tests.eval_export_tools import FAKE_STATE, STAMP
from tests.snapshot.git_tools import EDITED, make_dirty, make_repo


def _manifest(export: SnapshotSummary) -> dict:
    return json.loads((export.path / MANIFEST_NAME).read_text())


def _tracked(*pathspecs: str) -> set[str]:
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(listed.stdout.split())


def test_every_hash_is_recomputed_from_disk(export: SnapshotSummary) -> None:
    files = _manifest(export)["files"]
    on_disk = {
        p.relative_to(export.path).as_posix()
        for p in export.path.rglob("*")
        if p.is_file() and p.name not in (MANIFEST_NAME, CHECKSUMS_NAME)
    }
    assert set(files) == on_disk
    for path, digest in files.items():
        assert sha256_file(export.path / path) == digest, path


def test_the_manifest_and_checksums_are_not_in_the_map(export: SnapshotSummary) -> None:
    files = _manifest(export)["files"]
    assert MANIFEST_NAME not in files and CHECKSUMS_NAME not in files
    assert "README.md" in files and "tools/verify_snapshot.py" in files


def test_sha256sums_lines_equal_the_map(export: SnapshotSummary) -> None:
    files = _manifest(export)["files"]
    lines = (export.path / CHECKSUMS_NAME).read_text().splitlines()
    assert lines == [f"{digest}  {path}" for path, digest in sorted(files.items())]


def test_the_manifest_is_written_sorted_with_indent_two_and_a_trailing_newline(
    export: SnapshotSummary,
) -> None:
    text = (export.path / MANIFEST_NAME).read_text()
    assert text == manifest_text(json.loads(text)) and text.endswith("}\n")
    assert export.manifest_sha256 == sha256_file(export.path / MANIFEST_NAME)


def test_provenance(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    assert manifest["generator_commit"] == FAKE_STATE.commit and manifest["dirty"] is False
    assert manifest["snapshot_date"] == f"{STAMP[:4]}-{STAMP[4:6]}-{STAMP[6:]}"
    assert manifest["snapshot_dir"] == export.path.name
    assert set(manifest["versions"]) == {"augraphy", "numpy", "opencv", "pillow", "python"}


def test_the_inputs_hashed_are_exactly_the_tracked_config_and_ground_truth(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    assert set(manifest["config_sha256"]) == _tracked("config", "environment.yml")
    assert set(manifest["ground_truth_sha256"]) == _tracked("ground_truth")
    for block in ("config_sha256", "ground_truth_sha256"):
        for path, digest in manifest[block].items():
            assert sha256_file(REPO_ROOT / path) == digest, path
    assert not any("abn_register" in path for path in manifest["config_sha256"])


def test_counts_equal_what_is_on_disk(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    assert set(manifest["halves"]) == set(HALVES)
    for half, directory in manifest["halves"].items():
        images = sorted((export.path / directory).glob("*.png"))
        by_type = Counter(re.sub(r"_v\d+$", "", p.stem).split("_", 1)[1] for p in images)
        assert manifest["counts"][half] == {"by_document_type": dict(by_type), "total": len(images)}, half


def test_label_counts_equal_the_sidecar(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    directory = export.path / manifest["halves"]["labelled_varied"]
    rows = [json.loads(line) for line in (directory / "label_styles.jsonl").read_text().splitlines()]
    styles: dict = {}
    refusals: dict = {}
    for row in rows:
        for field, pick in row["styles"].items():
            styles.setdefault(row["doc_type"], {}).setdefault(field, dict.fromkeys(LABEL_STYLES, 0))
            styles[row["doc_type"]][field][pick["style"]] += 1
        for refusal in row["refusals"]:
            per_field = refusals.setdefault(row["doc_type"], {}).setdefault(refusal["field"], {})
            per_field[refusal["rule"]] = per_field.get(refusal["rule"], 0) + 1
    assert {t: v for t, v in manifest["label_style_counts"].items() if v} == styles
    actual_refusals = {
        t: {f: r for f, r in v.items() if r} for t, v in manifest["label_refusal_counts"].items()
    }
    assert {t: v for t, v in actual_refusals.items() if v} == refusals
    assert manifest["label_style_counts"]["bank_statement"] == {}


def test_metrics_summarise_every_clean_render(export: SnapshotSummary) -> None:
    metrics = _manifest(export)["metrics"]
    assert set(metrics) == {"plain", "distractor", "labelled_varied"}
    for half, by_type in metrics.items():
        assert set(by_type) == {"bank_statement", "invoice", "receipt"}, half
        for per_metric in by_type.values():
            assert set(per_metric) == {"scored_ink_ratio", "distractor_share", "page_fill"}
            for summary in per_metric.values():
                assert summary["min"] <= summary["median"] <= summary["max"]


def test_no_absolute_path_anywhere_in_the_manifest(export: SnapshotSummary) -> None:
    text = (export.path / MANIFEST_NAME).read_text()
    assert str(Path.home()) not in text and str(REPO_ROOT) not in text
    for path in _manifest(export)["files"]:
        assert not path.startswith("/") and ".." not in Path(path).parts


def test_build_manifest_rehashes_the_files_it_is_given(export: SnapshotSummary, tmp_path: Path) -> None:
    """A manifest built twice over the same files is identical: nothing in it depends on the clock."""
    rebuilt = build_manifest(export, repo_state=FAKE_STATE, repo_root=REPO_ROOT, snapshot_root=export.path)
    assert rebuilt == _manifest(export)


# --- the tools ---------------------------------------------------------------------------------------


def test_the_scripts_ship_byte_for_byte(export: SnapshotSummary) -> None:
    for name in ("verify_snapshot.py", "check_extractions_match_gt.py"):
        assert (export.path / "tools" / name).read_bytes() == (REPO_ROOT / "scripts" / name).read_bytes()
    assert sorted(p.name for p in (export.path / "tools").iterdir()) == sorted(
        ["check_extractions_match_gt.py", SHIPPED_NOTEBOOK, "verify_snapshot.py"]
    )


def test_the_notebook_ships_with_no_outputs(export: SnapshotSummary) -> None:
    shipped = json.loads((export.path / "tools" / SHIPPED_NOTEBOOK).read_text())
    source = json.loads((REPO_ROOT / SHIPPED_NOTEBOOK).read_text())
    code = [cell for cell in shipped["cells"] if cell["cell_type"] == "code"]
    assert code
    for cell in code:
        assert cell["outputs"] == [] and cell["execution_count"] is None
    assert [cell["source"] for cell in shipped["cells"]] == [cell["source"] for cell in source["cells"]]


def test_a_modified_input_is_hashed_as_it_stands_and_a_deleted_one_as_deleted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    make_dirty(repo)
    hashes = tracked_inputs(repo, ("edited.txt", "gone.txt"))
    assert hashes == {"edited.txt": sha256_file(repo / "edited.txt"), "gone.txt": "deleted"}
    assert (repo / "edited.txt").read_text() == EDITED
