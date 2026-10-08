"""Structural contract for evaluation-set export directories.

Pins the *shape* of an exported evaluation-set directory (filename pattern and
count, `ground_truth.csv` header and column order, and `ground_truth.jsonl` key
set and key order per document type) against `tests/fixtures/eval_format_baseline.json`.

Field *values* are deliberately never inspected: the corpus is reseeded
independently of this format, so a value-based comparison would fail for the
wrong reason forever. Shape is the only part of the dataset that is a contract
with external consumers, and shape is what stays meaningful as a regression signal.

Nothing outside the repository is read: a fresh export is written to a scratch
directory and compared with the fixture, which pins all three document types. The
fixture was re-captured from the exporter by tests/regenerate_eval_format_baseline.py;
test_fixture_describes_the_extraction_schema is its independent cross-check.

`assert_eval_export_matches_baseline` (tests/eval_export_tools.py) takes a
directory path so later stages can point it at any freshly produced export.
"""

import copy
from pathlib import Path

import pytest
import yaml

from generators.exporters.eval_projection import load_extraction_schema
from tests.conftest import REPO_ROOT
from tests.eval_export_tools import (
    assert_eval_export_matches_baseline,
    capture_shape,
    load_baseline,
    read_csv,
    read_jsonl,
)

SCHEMA = load_extraction_schema()
BANK_FIELDS = SCHEMA.get_extraction_fields("bank_statement")


@pytest.fixture(scope="module")
def clean(export: dict) -> Path:
    """The clean half of the shared session export (read-only)."""
    return Path(export["clean_dir"])


def test_fixture_describes_the_extraction_schema() -> None:
    """The fixture is the extraction schema's shape, derived independently of any export.

    Reading the contract from two places is only safe while they agree. Keys per type
    are the schema's field lists; the CSV header is the first record's fields (a bank
    statement, first by filename) then the remaining invoice fields; image counts are
    the ground truth's case counts for each type.
    """
    baseline = load_baseline()
    assert set(baseline["jsonl"]["keys_by_document_type"]) == {t.upper() for t in SCHEMA.document_fields}
    for doc_type, keys in baseline["jsonl"]["keys_by_document_type"].items():
        assert keys == ["filename", *SCHEMA.get_extraction_fields(doc_type)]
    invoice = SCHEMA.get_extraction_fields("invoice")
    assert baseline["csv"]["columns"] == [
        "image_file",
        *BANK_FIELDS,
        *[f for f in invoice if f not in BANK_FIELDS],
    ]
    assert set(baseline["images_per_suffix"]) == set(SCHEMA.document_fields)
    for doc_type, count in baseline["images_per_suffix"].items():
        cases = yaml.safe_load((REPO_ROOT / "ground_truth" / f"{doc_type}s.yml").read_text())
        assert count == len(cases), doc_type
    assert baseline["image_count"] == sum(baseline["images_per_suffix"].values())
    assert baseline["image_count"] == baseline["csv"]["row_count"] == baseline["jsonl"]["record_count"]


def test_fresh_export_matches_baseline(clean: Path) -> None:
    """A freshly exported clean directory has exactly the pinned shape, for all three types.

    Replaces `test_legacy_dataset_matches_baseline`, which read a dataset outside the
    repository. Image counts, filename pattern, CSV header and row count, and the JSONL
    key order of every document type including bank statements are all pinned.
    """
    assert_eval_export_matches_baseline(clean, load_baseline())
    shape = capture_shape(clean)
    assert shape == {k: v for k, v in load_baseline().items() if k != "_comment"}


def test_bank_statements_follow_the_schema(clean: Path) -> None:
    """Bank statements hold one image and one record each, with exactly the schema's keys in schema order."""
    images = sorted(clean.glob("*_bank_statement.png"))
    records = [
        r for r in read_jsonl(clean / "ground_truth.jsonl") if r["DOCUMENT_TYPE"] == "BANK_STATEMENT"
    ]
    assert images
    assert len(records) == len(images)
    for record in records:
        assert list(record) == ["filename", *BANK_FIELDS]


def test_header_leads_with_the_first_sorted_record(clean: Path) -> None:
    """Record order is the CSV's column order: the first record sorted by filename leads the header."""
    records = read_jsonl(clean / "ground_truth.jsonl")
    assert [r["filename"] for r in records] == sorted(r["filename"] for r in records)
    header, rows = read_csv(clean / "ground_truth.csv")
    assert header[: len(list(records[0]))] == ["image_file", *list(records[0])[1:]]
    assert [r["image_file"] for r in rows] == [r["filename"] for r in records]


@pytest.mark.parametrize(
    "tamper", ["missing_image", "swapped_columns", "reordered_keys", "bank_keys", "bank_count"]
)
def test_comparison_fails_when_the_shape_drifts(clean: Path, tmp_path: Path, tamper: str) -> None:
    """The comparison can fail: each deliberate drift from the pinned shape is caught.

    Guards against a check that silently passes whatever it is given.
    """
    baseline = copy.deepcopy(load_baseline())
    if tamper == "missing_image":
        baseline["images_per_suffix"]["receipt"] += 1
        baseline["image_count"] += 1
    elif tamper == "swapped_columns":
        cols = baseline["csv"]["columns"]
        cols[2], cols[3] = cols[3], cols[2]
    elif tamper == "reordered_keys":
        keys = baseline["jsonl"]["keys_by_document_type"]["RECEIPT"]
        keys[2], keys[3] = keys[3], keys[2]
    elif tamper == "bank_keys":
        baseline["jsonl"]["keys_by_document_type"]["BANK_STATEMENT"].pop()
    else:
        baseline["images_per_suffix"]["bank_statement"] -= 1
    with pytest.raises(AssertionError):
        assert_eval_export_matches_baseline(clean, baseline)
