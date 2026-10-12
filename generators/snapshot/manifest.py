"""The snapshot's MANIFEST.json and SHA256SUMS, and the provenance record written beside it.

The snapshot is published as is, to readers who never see the repository, so its manifest says what the
snapshot holds and nothing about how it was produced: each half's directory and counts, the degradation
tiers, the ground-truth and geometry names, the label-style and refusal counts from the sidecar, and the
SHA-256 of every file except `MANIFEST.json` and `SHA256SUMS` themselves. Every path is POSIX and
relative to the snapshot root. `SHA256SUMS` repeats the `files` map in the form `shasum -a 256 -c` reads
(Decision 10).

What produced it is the provenance record, `PROVENANCE.json`, in a directory beside the snapshot that is
never uploaded (Decision 9): the commit, the SHA-256 of every tracked file under `config/` and
`ground_truth/` and of `environment.yml`, the library versions, the density metrics of every clean
render, the published manifest's own SHA-256, and the earlier snapshot it follows. `dirty` says whether
the working tree differed from the commit; when it did, `working_tree_changes` lists every changed path
with its status code and content hash, and `working_tree.patch` beside the record holds the difference.
"""

import hashlib
import json
import platform
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from generators.eval_set import _DATE_FORMAT
from generators.labels.keys import LABEL_STYLES
from generators.snapshot.repo_state import DELETED, RepoState, RepoStateError
from generators.snapshot.summary import SnapshotSummary

# Contract constants: the names the spec fixes for the snapshot's own files.
MANIFEST_NAME = "MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS"
README_NAME = "README.md"
TOOLS_DIR = "tools"
# The provenance record, and the patch written beside it only when the tree was modified (what differed
# from the recorded commit, as `git apply` reads). Both live outside the snapshot.
PROVENANCE_NAME = "PROVENANCE.json"
PATCH_NAME = "working_tree.patch"

# Files neither hashed into `files` nor listed in SHA256SUMS: the manifest cannot vouch for itself.
UNHASHED = (MANIFEST_NAME, CHECKSUMS_NAME)

# The density metrics the manifest summarises, per render then document type.
METRICS = ("scored_ink_ratio", "distractor_share", "page_fill")

# Tracked inputs hashed, as git pathspecs relative to the repository root.
_CONFIG_INPUTS = ("config", "environment.yml")
_GROUND_TRUTH_INPUTS = ("ground_truth",)

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of one file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def hash_files(snapshot_root: Path) -> dict[str, str]:
    """Hash every file under the snapshot root except MANIFEST.json and SHA256SUMS.

    Args:
        snapshot_root: The snapshot (or staging) directory.

    Returns:
        POSIX path relative to the root -> hex SHA-256, sorted by path.
    """
    files = {}
    for path in sorted(snapshot_root.rglob("*")):
        relative = path.relative_to(snapshot_root).as_posix()
        if path.is_file() and relative not in UNHASHED:
            files[relative] = sha256_file(path)
    return dict(sorted(files.items()))


def tracked_inputs(repo_root: Path, pathspecs: tuple[str, ...]) -> dict[str, str]:
    """Hash the tracked files under the given pathspecs, as they stand in the working tree.

    The working tree is what the export read, so a modified input is hashed as modified, and a tracked
    input deleted from the working tree is recorded as `deleted`.

    Args:
        repo_root: The repository root.
        pathspecs: Paths relative to the root, as `git ls-files` takes them.

    Returns:
        POSIX path relative to the repository root -> hex SHA-256, sorted by path.

    Raises:
        RepoStateError: `git ls-files` fails.
    """
    listed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        raise RepoStateError(
            "Cannot list the tracked inputs.\n"
            f"  What:     `git ls-files` failed: {listed.stderr.strip() or 'no output'}.\n"
            f"  Where:    {repo_root}\n"
            "  Expected: a git repository whose config/, ground_truth/ and environment.yml are tracked.\n"
            "  Recover:  run the export from the generator's repository checkout."
        )
    names = sorted(name for name in listed.stdout.split("\0") if name)
    return {
        name: sha256_file(repo_root / name) if (repo_root / name).is_file() else DELETED for name in names
    }


def _versions() -> dict[str, str]:
    """The versions of Python and the libraries that shape the pixels."""
    import augraphy
    import cv2
    import numpy
    import PIL

    return {
        "augraphy": str(augraphy.__version__),
        "numpy": numpy.__version__,
        "opencv": cv2.__version__,
        "pillow": PIL.__version__,
        "python": platform.python_version(),
    }


def _label_counts(rows: tuple[dict, ...]) -> tuple[dict, dict]:
    """Count realised styles and refusals per document type, then field (then style or rule)."""
    styles: dict[str, dict[str, dict[str, int]]] = {}
    refusals: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        per_type = styles.setdefault(row["doc_type"], {})
        refused = refusals.setdefault(row["doc_type"], {})
        for field, pick in row["styles"].items():
            per_type.setdefault(field, dict.fromkeys(LABEL_STYLES, 0))[pick["style"]] += 1
            refused.setdefault(field, {})
        for refusal in row["refusals"]:
            counter = refused.setdefault(refusal["field"], {})
            counter[refusal["rule"]] = counter.get(refusal["rule"], 0) + 1
    ordered = {
        t: {f: dict(sorted(c.items())) for f, c in sorted(v.items())} for t, v in sorted(refusals.items())
    }
    return {t: dict(sorted(v.items())) for t, v in sorted(styles.items())}, ordered


def _doc_type(image_file: str) -> str:
    """The extraction document type of a generic filename, `CASE001_bank_statement.png`."""
    return image_file.removesuffix(".png").split("_", 1)[1]


def _metrics(density: dict) -> dict:
    """Min, median and max of each density metric, per clean render then document type."""
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for half, reports in sorted(density.items()):
        by_type: dict[str, list] = {}
        for image_file, report in reports.items():
            by_type.setdefault(_doc_type(image_file), []).append(report)
        out[half] = {
            doc_type: {
                metric: {
                    "max": round(max(values), 4),
                    "median": round(statistics.median(values), 4),
                    "min": round(min(values), 4),
                }
                for metric in METRICS
                for values in [[getattr(report, metric) for report in reports_of_type]]
            }
            for doc_type, reports_of_type in sorted(by_type.items())
        }
    return out


def _snapshot_date(summary: SnapshotSummary) -> str:
    return datetime.strptime(summary.stamp, _DATE_FORMAT).date().isoformat()


def describe_snapshot(summary: SnapshotSummary) -> dict:
    """Everything the manifest records except the `files` map (what the README is generated from).

    Nothing here says how the snapshot was produced; that is `describe_provenance`.

    Args:
        summary: The export's `SnapshotSummary` (its counts, sidecar rows, names and tiers).

    Returns:
        The manifest's description, keys sorted.
    """
    counts = {
        half: {"by_document_type": dict(sorted(per_type.items())), "total": sum(per_type.values())}
        for half, per_type in summary.counts.items()
    }
    style_counts, refusal_counts = _label_counts(summary.sidecar_rows)
    description = {
        "counts": counts,
        "degradation_tiers": [{"name": name, "suffix": suffix} for name, suffix in summary.tiers],
        "geometry_dir": summary.geometry_dir,
        "ground_truth_files": [summary.csv_name, summary.jsonl_name],
        "halves": {half: directory.name for half, directory in summary.half_dirs.items()},
        "label_refusal_counts": refusal_counts,
        "label_style_counts": style_counts,
        "snapshot_date": _snapshot_date(summary),
        "snapshot_dir": summary.path.name,
    }
    return dict(sorted(description.items()))


def build_manifest(summary: SnapshotSummary, *, snapshot_root: Path) -> dict:
    """Build the full manifest: the description plus the SHA-256 of every file now in the snapshot.

    Args:
        summary: The export's `SnapshotSummary`.
        snapshot_root: The directory whose files are hashed (the staging directory, before publishing).

    Returns:
        The manifest, keys sorted.
    """
    manifest = describe_snapshot(summary)
    manifest["files"] = hash_files(snapshot_root)
    return dict(sorted(manifest.items()))


def describe_provenance(
    summary: SnapshotSummary,
    *,
    repo_state: RepoState,
    repo_root: Path,
    manifest_sha256: str,
    previous: dict | None,
) -> dict:
    """The provenance record: what produced the snapshot, kept beside it and never published.

    Args:
        summary: The export's `SnapshotSummary` (its name, date and density reports).
        repo_state: The state the snapshot was rendered from; when it is dirty, the record carries
            `working_tree_changes`, one record per changed path.
        repo_root: The repository root, for the tracked inputs.
        manifest_sha256: The SHA-256 of the published MANIFEST.json, which cannot record itself.
        previous: The earlier snapshot this one follows (`PreviousSnapshot.as_record()`), or None.

    Returns:
        The record, keys sorted.
    """
    record = {
        "config_sha256": tracked_inputs(repo_root, _CONFIG_INPUTS),
        "dirty": repo_state.dirty,
        "generator_commit": repo_state.commit,
        "ground_truth_sha256": tracked_inputs(repo_root, _GROUND_TRUTH_INPUTS),
        "manifest_sha256": manifest_sha256,
        "metrics": _metrics(summary.density),
        "previous_snapshot": previous,
        "snapshot_date": _snapshot_date(summary),
        "snapshot_dir": summary.path.name,
        "versions": _versions(),
    }
    if repo_state.dirty:
        record["working_tree_changes"] = [change.as_record() for change in repo_state.changes]
    return dict(sorted(record.items()))


def write_provenance(record: dict, directory: Path, *, patch: bytes | None) -> Path:
    """Write PROVENANCE.json (and the working-tree patch, when there is one) into `directory`.

    Args:
        record: The provenance record.
        directory: The provenance directory (its staging copy, before publishing).
        patch: The working-tree patch of a modified tree, or None for a clean one.

    Returns:
        The path of PROVENANCE.json.
    """
    path = directory / PROVENANCE_NAME
    path.write_text(manifest_text(record))
    if patch is not None:
        (directory / PATCH_NAME).write_bytes(patch)
    return path


def manifest_text(manifest: dict) -> str:
    """The manifest as written: keys sorted, indent 2, a trailing newline."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def write_manifest(manifest: dict, snapshot_root: Path) -> Path:
    """Write MANIFEST.json into the snapshot root and return its path."""
    path = snapshot_root / MANIFEST_NAME
    path.write_text(manifest_text(manifest))
    return path


def write_checksums(manifest: dict, snapshot_root: Path) -> Path:
    """Write SHA256SUMS from the manifest's `files` map, one `<hash>  <path>` line per file, sorted.

    Args:
        manifest: The built manifest.
        snapshot_root: The snapshot root.

    Returns:
        The path written.
    """
    lines = "".join(f"{digest}  {path}\n" for path, digest in sorted(manifest["files"].items()))
    target = snapshot_root / CHECKSUMS_NAME
    target.write_text(lines)
    return target
