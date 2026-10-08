"""Export an evaluation set into a scratch root (shared by the eval-set test modules).

Mirrors `derived_tools.derive_into`: copy `config/generation_config.yml` into the
scratch root and point it at absolute repository paths, so the export neither
depends on the working directory nor writes anywhere but `root`.
"""

import csv
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml

from generators.eval_set import export_eval_set
from tests.conftest import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "generation_config.yml"
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "eval_format_baseline.json"
STAMP_DATE = date(2031, 3, 9)
STAMP = "20310309"


def scratch_config(root: Path, *, mutate: Callable[[dict], None] | None = None) -> Path:
    """Write a copy of the generation config under `root` with absolute input paths.

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
    if mutate is not None:
        mutate(config)
    path = root / "generation_config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def single_case_config(root: Path, doc_types: list[str]) -> Path:
    """Write a scratch config whose ground truth holds ONE case per listed type.

    Lets a test drive the real exporter end to end in seconds. The first entry of
    each type's ground truth is copied verbatim into a scratch YAML under `root`.

    Args:
        root: Scratch directory.
        doc_types: Keys of the top-level `document_types` block to keep.

    Returns:
        Path of the scratch config, with `eval_set.document_types` set to `doc_types`.
    """

    def mutate(config: dict) -> None:
        for dtype in doc_types:
            doc_cfg = config["document_types"][dtype]
            entries = yaml.safe_load(Path(doc_cfg["ground_truth"]).read_text())
            first = next(iter(entries))
            small = root / f"{dtype}.yml"
            small.write_text(yaml.safe_dump({first: entries[first]}, sort_keys=False))
            doc_cfg["ground_truth"] = str(small)
        config["eval_set"]["document_types"] = list(doc_types)

    return scratch_config(root, mutate=mutate)


def export_into(root: Path) -> dict:
    """Run the full export under `root/out` with the real corpus.

    Args:
        root: Scratch directory.

    Returns:
        The summary dict from `export_eval_set`, plus `out` (the parent directory).
    """
    out = root / "out"
    summary = export_eval_set(scratch_config(root), out, today=STAMP_DATE)
    return {**summary, "out": str(out)}


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
