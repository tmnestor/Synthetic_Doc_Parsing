"""Tests for the native JSONL derivation (spec section 7).

Mirrors tests/test_derive_cord.py's structure. Native covers the one document
type with no CORD or DocILE equivalent: bank statements.
"""

import json
from pathlib import Path

import pytest
import yaml

from generators.derive_outputs import (
    CORD_DOCUMENT_TYPES,
    DOCILE_DOCUMENT_TYPES,
    NATIVE_DOCUMENT_TYPES,
    derive_native,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error


def _entry(doc_type: str, **extra: str) -> dict:
    fields = {
        "DOCUMENT_TYPE": doc_type,
        "SUPPLIER_NAME": "Pellam Test Bank",
        "STATEMENT_DATE_RANGE": "01/04/2031 - 30/04/2031",
        "TRANSACTION_DATES": "02/04/2031|05/04/2031",
        "TRANSACTION_DESCRIPTIONS": "Coffee Stop|Pay Run",
        "TRANSACTION_AMOUNTS_PAID": "4.50|NOT_FOUND",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|900.00",
        "ACCOUNT_BALANCE": "1000.00",
    }
    fields.update(extra)
    return {"layout": "some_bank", "degradation_seed": 1, "fields": fields}


def _gt(tmp_path: Path, name: str, entries: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(entries))
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_derive_native_writes_one_line_per_document(tmp_path: Path) -> None:
    """One line per statement, with its scalar fields and a scrubbed transactions array."""
    gt = _gt(tmp_path, "gt.yml", {"CASE921": _entry("BANK_STATEMENT"), "CASE922": _entry("BANK_STATEMENT")})
    records = _read(derive_native([gt], tmp_path / "native.jsonl"))
    assert [r["case_id"] for r in records] == ["CASE921", "CASE922"]
    assert records[0]["image_file"] == "CASE921_some_bank.png"
    assert records[0]["transactions"] == [
        {"date": "02/04/2031", "description": "Coffee Stop", "amount_paid": "4.50", "amount_received": ""},
        {"date": "05/04/2031", "description": "Pay Run", "amount_paid": "", "amount_received": "900.00"},
    ]
    assert "NOT_FOUND" not in json.dumps(records)


def test_derive_native_skips_document_types_with_a_cord_equivalent(tmp_path: Path) -> None:
    """Receipts and invoices have a CORD equivalent and must not appear here.

    The whole corpus partitions into CORD xor native, never both.
    """
    gt = _gt(tmp_path, "gt.yml", {"CASE923": _entry("RECEIPT"), "CASE924": _entry("INVOICE")})
    assert _read(derive_native([gt], tmp_path / "native.jsonl")) == []


def test_derive_native_mixes_and_filters_multiple_document_types(tmp_path: Path) -> None:
    """Mixed gt_files emit only statements, concatenated in file order."""
    first = _gt(tmp_path, "a.yml", {"CASE925": _entry("RECEIPT"), "CASE926": _entry("BANK_STATEMENT")})
    second = _gt(tmp_path, "b.yml", {"CASE927": _entry("BANK_STATEMENT"), "CASE928": _entry("INVOICE")})
    records = _read(derive_native([first, second], tmp_path / "native.jsonl"))
    assert [r["case_id"] for r in records] == ["CASE926", "CASE927"]


def test_derive_native_creates_parent_directories(tmp_path: Path) -> None:
    """Missing parent directories of the output path are created."""
    gt = _gt(tmp_path, "gt.yml", {"CASE929": _entry("BANK_STATEMENT")})
    out = tmp_path / "x" / "y" / "native.jsonl"
    assert derive_native([gt], out) == out
    assert out.exists()


def test_derive_native_propagates_ragged_transaction_diagnostic(tmp_path: Path) -> None:
    """Mismatched TRANSACTION_* list lengths surface to_native's four-element diagnostic."""
    entry = _entry("BANK_STATEMENT", TRANSACTION_DATES="02/04/2031")
    gt = _gt(tmp_path, "gt.yml", {"CASE930": entry})
    with pytest.raises(ValueError) as err:
        derive_native([gt], tmp_path / "native.jsonl")
    assert_diagnostic_error(err.value)


def test_native_document_types_constant() -> None:
    """Native membership is exactly BANK_STATEMENT, the one type with no CORD or DocILE equivalent.

    It is disjoint from the CORD set, and together they partition the live corpus.
    """
    assert NATIVE_DOCUMENT_TYPES == frozenset({"BANK_STATEMENT"})
    assert NATIVE_DOCUMENT_TYPES.isdisjoint(CORD_DOCUMENT_TYPES)
    assert DOCILE_DOCUMENT_TYPES <= CORD_DOCUMENT_TYPES
    live = set()
    for name in ("bank_statements", "receipts", "invoices"):
        data = yaml.safe_load((REPO_ROOT / "ground_truth" / f"{name}.yml").read_text())
        live |= {entry["fields"]["DOCUMENT_TYPE"] for entry in data.values()}
    assert live == NATIVE_DOCUMENT_TYPES | CORD_DOCUMENT_TYPES
