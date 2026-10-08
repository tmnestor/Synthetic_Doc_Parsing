"""Export a snapshot into a scratch root (shared by the eval-set and snapshot test modules).

Mirrors `derived_tools.derive_into`: copy `config/generation_config.yml` into the scratch root, point it
at absolute repository paths and at an output root under the scratch directory, so the export neither
depends on the working directory nor writes anywhere but `root`. The repository state is a fake clean
one, so the tests run from a working tree under development (Decision 4).
"""

import csv
import itertools
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml

from generators.snapshot.export import SnapshotSummary, export_snapshot
from generators.snapshot.repo_state import RepoState
from tests.conftest import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "generation_config.yml"
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "eval_format_baseline.json"
STAMP_DATE = date(2031, 3, 9)
STAMP = "20310309"
# The first cases of each type the small snapshot holds.
SMALL_CASES = 3
# A clean state at an invented commit id.
FAKE_STATE = RepoState(commit="0123456789abcdef0123456789abcdef01234567", dirty_paths=())


def clean_state() -> RepoState:
    """The fake clean repository state the test exports run from."""
    return FAKE_STATE


def scratch_config(root: Path, *, mutate: Callable[[dict], None] | None = None) -> Path:
    """Write a copy of the generation config under `root` with absolute input paths.

    The output root becomes `root/out` (created), so a snapshot lands under the scratch directory.

    Args:
        root: Scratch directory that receives the config copy.
        mutate: Optional callable given the parsed config to edit before writing.

    Returns:
        Path of the written config.
    """
    config = yaml.safe_load(CONFIG_PATH.read_text())
    for doc_cfg in config["document_types"].values():
        doc_cfg["ground_truth"] = str(REPO_ROOT / doc_cfg["ground_truth"])
        doc_cfg["layouts"] = str(REPO_ROOT / doc_cfg["layouts"])
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    config["eval_set"]["output_root"] = str(out)
    if mutate is not None:
        mutate(config)
    path = root / "generation_config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def small_config(root: Path, *, cases: int = SMALL_CASES, doc_types: list[str] | None = None) -> Path:
    """Write a scratch config whose ground truth holds the first `cases` entries of each listed type.

    Args:
        root: Scratch directory.
        cases: How many entries of each type to keep.
        doc_types: Keys of the top-level `document_types` block to export; all three when None.

    Returns:
        Path of the scratch config, with `eval_set.document_types` set to the types kept.
    """

    def mutate(config: dict) -> None:
        kept = doc_types if doc_types is not None else list(config["eval_set"]["document_types"])
        for dtype in kept:
            doc_cfg = config["document_types"][dtype]
            entries = yaml.safe_load(Path(doc_cfg["ground_truth"]).read_text())
            small = root / f"{dtype}.yml"
            small.write_text(
                yaml.safe_dump(dict(itertools.islice(entries.items(), cases)), sort_keys=False)
            )
            doc_cfg["ground_truth"] = str(small)
        config["eval_set"]["document_types"] = list(kept)

    return scratch_config(root, mutate=mutate)


def single_case_config(root: Path, doc_types: list[str]) -> Path:
    """Write a scratch config whose ground truth holds ONE case per listed type."""
    return small_config(root, cases=1, doc_types=doc_types)


def export_into(root: Path, *, cases: int | None = None) -> SnapshotSummary:
    """Export a snapshot under `root/out` from a fake clean state.

    Args:
        root: Scratch directory.
        cases: The first cases of each type to export; the whole corpus when None.

    Returns:
        The export's summary.
    """
    config = scratch_config(root) if cases is None else small_config(root, cases=cases)
    return export_snapshot(config, today=STAMP_DATE, repo_state=clean_state)


def read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file into a list of records."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Parse a CSV file into (header, rows)."""
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def load_baseline() -> dict:
    """Load the pinned structural baseline, `tests/fixtures/eval_format_baseline.json`."""
    return json.loads(BASELINE_PATH.read_text())


def small_baseline(cases: int = SMALL_CASES) -> dict:
    """The pinned baseline with its counts cut to a small snapshot of `cases` per type.

    Columns, key order, the filename pattern and the required files are the pinned contract unchanged;
    only the counts, which the full-corpus export asserts, follow the snapshot's size.
    """
    baseline = load_baseline()
    baseline["images_per_suffix"] = dict.fromkeys(baseline["images_per_suffix"], cases)
    total = cases * len(baseline["images_per_suffix"])
    baseline["image_count"] = total
    baseline["csv"]["row_count"] = total
    baseline["jsonl"]["record_count"] = total
    return baseline


def capture_shape(directory: Path) -> dict:
    """Describe the SHAPE of an exported directory: names, counts, columns, key order. Never values.

    Args:
        directory: An exported evaluation-set directory (the clean half).

    Returns:
        A mapping in the baseline fixture's format (without `_comment`).

    Raises:
        ValueError: records of one document type disagree on their keys.
    """
    names = sorted(p.name for p in directory.glob("*.png"))
    suffixes = sorted({n.split("_", 1)[1].removesuffix(".png") for n in names})
    header, rows = read_csv(directory / "ground_truth.csv")
    records = read_jsonl(directory / "ground_truth.jsonl")
    keys_by_type: dict[str, list[str]] = {}
    for record in records:
        keys = list(record)
        if keys_by_type.setdefault(record["DOCUMENT_TYPE"], keys) != keys:
            raise ValueError(f"{record['filename']}: key order differs within {record['DOCUMENT_TYPE']}")
    return {
        "filename_pattern": r"^CASE\d{3}_(" + "|".join(suffixes) + r")\.png$",
        "document_type_suffixes": suffixes,
        "image_count": len(names),
        "images_per_suffix": {s: sum(n.endswith(f"_{s}.png") for n in names) for s in suffixes},
        "required_files": ["ground_truth.csv", "ground_truth.jsonl"],
        "csv": {"columns": header, "row_count": len(rows)},
        "jsonl": {"record_count": len(records), "keys_by_document_type": keys_by_type},
    }


def assert_eval_export_matches_baseline(directory: Path, baseline: dict) -> None:
    """Assert an export directory has exactly the pinned shape, for every document type.

    Checked against the baseline's own pattern and lists, not against a shape captured
    from the same directory, so a drift in either direction fails.

    Args:
        directory: An exported evaluation-set directory.
        baseline: The parsed baseline fixture.

    Raises:
        AssertionError: any shape property differs from the baseline.
    """
    for required in baseline["required_files"]:
        assert (directory / required).is_file(), f"{required} missing from {directory}"

    pinned = re.compile(baseline["filename_pattern"])
    images = sorted(p.name for p in directory.glob("*.png"))
    for name in images:
        assert pinned.match(name), f"{name} does not match {baseline['filename_pattern']}"
    assert len(images) == baseline["image_count"]
    for suffix, count in baseline["images_per_suffix"].items():
        found = [n for n in images if n.endswith(f"_{suffix}.png")]
        assert len(found) == count, f"{suffix}: {len(found)} images, baseline pins {count}"

    header, rows = read_csv(directory / "ground_truth.csv")
    assert header == baseline["csv"]["columns"]
    assert len(rows) == baseline["csv"]["row_count"]

    records = read_jsonl(directory / "ground_truth.jsonl")
    assert len(records) == baseline["jsonl"]["record_count"]
    by_type = baseline["jsonl"]["keys_by_document_type"]
    for record in records:
        assert record["DOCUMENT_TYPE"] in by_type, f"unpinned document type: {record['DOCUMENT_TYPE']}"
        assert list(record) == by_type[record["DOCUMENT_TYPE"]], f"key order differs: {record['filename']}"
