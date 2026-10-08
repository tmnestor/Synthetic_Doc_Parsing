"""Tests for the DocILE JSONL derivation (invoice-only scope, spec section 8.3).

Mirrors tests/test_derive_cord.py's structure. DocILE is restricted to
invoices: receipts structurally never render unit price or quantity-when-1,
so DOCILE_DOCUMENT_TYPES = frozenset({"INVOICE"}) here, superseding any
receipt+invoice count implied elsewhere. The real corpus emits one record per
invoice, not one per receipt and invoice.

Real geometry comes from the session-shared `derived` fixture (read-only);
everything written goes under tmp_path.
"""

import json
from pathlib import Path

import pytest
import yaml

from generators.derive_outputs import derive_docile
from generators.exporters.config import load_export_config
from tests.conftest import REPO_ROOT, assert_diagnostic_error

GT_DIR = REPO_ROOT / "ground_truth"


@pytest.fixture(scope="module")
def export_config() -> dict:
    return load_export_config(REPO_ROOT / "config" / "export_config.yml")


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _pick(tmp_path: Path, name: str, source: Path, doc_type: str, case_ids: list[str]) -> Path:
    """Write a ground-truth file holding the named real entries (which must have `doc_type`)."""
    data = yaml.safe_load(source.read_text())
    subset = {case_id: data[case_id] for case_id in case_ids}
    assert all(e["fields"]["DOCUMENT_TYPE"] == doc_type for e in subset.values())
    path = tmp_path / name
    path.write_text(yaml.safe_dump(subset))
    return path


def test_derive_docile_writes_one_line_per_invoice(
    derived: Path, export_config: dict, tmp_path: Path
) -> None:
    """The real corpus emits one record per invoice, each with kile and lir lists."""
    invoices = GT_DIR / "invoices.yml"
    out = derive_docile([invoices], derived / "geometry.jsonl", export_config, tmp_path / "docile.jsonl")
    records = _read(out)
    expected = yaml.safe_load(invoices.read_text())
    assert [r["case_id"] for r in records] == list(expected)
    assert len(records) == 55
    assert records[0]["kile"]
    assert records[0]["lir"]
    assert records[0]["image_file"].startswith("CASE001_")


def test_derive_docile_skips_non_invoice_document_types(
    derived: Path, export_config: dict, tmp_path: Path
) -> None:
    """Receipts and statements have no DocILE equivalent.

    Receipts structurally lack geometry for unit price and quantity-when-1
    (spec section 8.3, invoice-only scope).
    """
    files = [GT_DIR / "receipts.yml", GT_DIR / "bank_statements.yml"]
    out = derive_docile(files, derived / "geometry.jsonl", export_config, tmp_path / "docile.jsonl")
    assert _read(out) == []


def test_derive_docile_mixes_and_filters_multiple_document_types(
    derived: Path, export_config: dict, tmp_path: Path
) -> None:
    """Mixed gt_files emit only the invoices, concatenated in file order."""
    inv = yaml.safe_load((GT_DIR / "invoices.yml").read_text())
    first, second = list(inv)[:2]
    rec_id = next(iter(yaml.safe_load((GT_DIR / "receipts.yml").read_text())))
    files = [
        _pick(tmp_path, "r.yml", GT_DIR / "receipts.yml", "RECEIPT", [rec_id]),
        _pick(tmp_path, "i2.yml", GT_DIR / "invoices.yml", "INVOICE", [second]),
        _pick(tmp_path, "i1.yml", GT_DIR / "invoices.yml", "INVOICE", [first]),
    ]
    out = derive_docile(files, derived / "geometry.jsonl", export_config, tmp_path / "docile.jsonl")
    assert [r["case_id"] for r in _read(out)] == [second, first]


def test_derive_docile_creates_parent_directories(
    derived: Path, export_config: dict, tmp_path: Path
) -> None:
    """Missing parent directories of the output path are created."""
    first = next(iter(yaml.safe_load((GT_DIR / "invoices.yml").read_text())))
    gt = _pick(tmp_path, "i.yml", GT_DIR / "invoices.yml", "INVOICE", [first])
    out = tmp_path / "nested" / "dir" / "docile.jsonl"
    assert derive_docile([gt], derived / "geometry.jsonl", export_config, out) == out
    assert len(_read(out)) == 1


def test_derive_docile_raises_when_geometry_file_is_missing(export_config: dict, tmp_path: Path) -> None:
    """An absent geometry file raises FileNotFoundError carrying the four-element diagnostic."""
    missing = tmp_path / "no_geometry.jsonl"
    with pytest.raises(FileNotFoundError) as err:
        derive_docile([GT_DIR / "invoices.yml"], missing, export_config, tmp_path / "docile.jsonl")
    assert_diagnostic_error(err.value)
    assert str(missing.resolve()) in str(err.value)


def test_derive_docile_raises_when_an_invoice_has_no_geometry_record(
    derived: Path, export_config: dict, tmp_path: Path
) -> None:
    """Every invoice has captured geometry in the real corpus.

    An absent record always signals a real capture bug, never a structural
    absence, so it is a hard KeyError with the diagnostic.
    """
    inv = yaml.safe_load((GT_DIR / "invoices.yml").read_text())
    first, second = list(inv)[:2]
    gt = _pick(tmp_path, "i.yml", GT_DIR / "invoices.yml", "INVOICE", [first, second])
    second_image = f"{second}_{inv[second]['layout']}.png"
    kept = [
        line
        for line in (derived / "geometry.jsonl").read_text().splitlines()
        if json.loads(line)["image_file"] != second_image
    ]
    geometry = tmp_path / "geometry.jsonl"
    geometry.write_text("\n".join(kept) + "\n")
    with pytest.raises(KeyError) as err:
        derive_docile([gt], geometry, export_config, tmp_path / "docile.jsonl")
    assert_diagnostic_error(err.value.args[0])
    assert second_image in err.value.args[0]
