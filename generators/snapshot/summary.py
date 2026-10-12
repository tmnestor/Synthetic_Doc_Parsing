"""What one snapshot export produced: the record the command reports and the manifest is built from."""

from dataclasses import dataclass
from pathlib import Path

from generators.distractors.density import DensityReport
from generators.snapshot.repo_state import RepoState


@dataclass(frozen=True)
class SnapshotSummary:
    """What one export produced, for the command's report and the manifest.

    Attributes:
        path: The snapshot directory, under the name it is published as.
        stamp: Its date stamp, `%Y%m%d`.
        repo_state: The repository state it was rendered from (clean or modified, and unchanged at
            publish).
        half_dirs: Half -> its directory inside the snapshot.
        counts: Half -> extraction document type -> number of images (the degraded half names
            receipts only).
        sidecar_rows: The labelled varied half's sidecar rows, sorted by image_file.
        density: Clean half -> image_file -> the render's density report (plain renders measured only).
        tiers: The degradation tiers, as (name, filename suffix), in declared order.
        geometry_dir: The name of the snapshot's geometry directory.
        csv_name: The ground-truth CSV file name in each half.
        jsonl_name: The ground-truth JSONL file name in each half.
        manifest_sha256: The SHA-256 of the published MANIFEST.json (empty until it is written).
        provenance_dir: The provenance directory written beside the snapshot (None until it is).
    """

    path: Path
    stamp: str
    repo_state: RepoState
    half_dirs: dict[str, Path]
    counts: dict[str, dict[str, int]]
    sidecar_rows: tuple[dict, ...]
    density: dict[str, dict[str, DensityReport]]
    tiers: tuple[tuple[str, str], ...]
    geometry_dir: str
    csv_name: str
    jsonl_name: str
    manifest_sha256: str = ""
    provenance_dir: Path | None = None

    def total(self, half: str) -> int:
        """Return a half's image count across document types."""
        return sum(self.counts[half].values())
