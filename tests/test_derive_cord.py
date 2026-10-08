"""Tests for the CORD JSONL derivation."""

import json
from pathlib import Path

import pytest
import yaml

from generators.derive_outputs import derive_cord
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG = {"abn_tfn_canonical_form": "spaced"}


def _entry(doc_type: str, **extra: str) -> dict:
    fields = {
        "DOCUMENT_TYPE": doc_type,
        "SUPPLIER_NAME": "Halloran Fixtures",
        "BUSINESS_ABN": "10 203 040 506",
        "INVOICE_DATE": "05/06/2031",
        "GST_AMOUNT": "3.00",
        "TOTAL_AMOUNT": "33.00",
        "LINE_ITEM_DESCRIPTIONS": "Widget",
        "LINE_ITEM_QUANTITIES": "1",
        "LINE_ITEM_PRICES": "33.00",
        "LINE_ITEM_TOTAL_PRICES": "33.00",
    }
    fields.update(extra)
    return {"layout": "some_layout", "degradation_seed": 1, "fields": fields}


def _gt(tmp_path: Path, name: str, entries: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(entries))
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_derive_cord_writes_one_line_per_document(tmp_path: Path) -> None:
    """One JSONL line per receipt or invoice, each with case_id, image_file and gt_parse."""
    gt = _gt(tmp_path, "gt.yml", {"CASE901": _entry("RECEIPT"), "CASE902": _entry("INVOICE")})
    out = derive_cord([gt], CONFIG, tmp_path / "cord.jsonl")
    records = _read(out)
    assert [r["case_id"] for r in records] == ["CASE901", "CASE902"]
    assert records[0]["image_file"] == "CASE901_some_layout.png"
    assert records[0]["gt_parse"]["total"] == {"total_price": "33.00"}


def test_derive_cord_maps_unit_price_and_total_price_distinctly(tmp_path: Path) -> None:
    """The independent oracle for the unitprice/price mapping.

    Quantity 3 makes unit price and line total genuinely differ, so
    LINE_ITEM_PRICES -> menu.unitprice and LINE_ITEM_TOTAL_PRICES -> menu.price
    are pinned against literal expected values. A self-score cannot catch a swap
    because both sides come from the same to_cord call.
    """
    entry = _entry(
        "RECEIPT",
        LINE_ITEM_DESCRIPTIONS="Bolt Pack|Washer Pack",
        LINE_ITEM_QUANTITIES="3|1",
        LINE_ITEM_PRICES="4.00|2.50",
        LINE_ITEM_TOTAL_PRICES="12.00|2.50",
    )
    gt = _gt(tmp_path, "gt.yml", {"CASE903": entry})
    (record,) = _read(derive_cord([gt], CONFIG, tmp_path / "cord.jsonl"))
    assert record["gt_parse"]["menu"] == [
        {"nm": "Bolt Pack", "cnt": "3", "unitprice": "4.00", "price": "12.00"},
        {"nm": "Washer Pack", "cnt": "1", "unitprice": "2.50", "price": "2.50"},
    ]


def test_derive_cord_skips_non_receipt_document_types(tmp_path: Path) -> None:
    """Bank statements have no CORD equivalent (spec section 7)."""
    gt = _gt(tmp_path, "gt.yml", {"CASE904": _entry("BANK_STATEMENT")})
    assert _read(derive_cord([gt], CONFIG, tmp_path / "cord.jsonl")) == []


def test_derive_cord_mixes_and_filters_multiple_document_types(tmp_path: Path) -> None:
    """A gt_files list mixing eligible and ineligible types emits only the eligible ones.

    Multiple gt_files are concatenated in file order.
    """
    first = _gt(tmp_path, "a.yml", {"CASE905": _entry("BANK_STATEMENT"), "CASE906": _entry("RECEIPT")})
    second = _gt(tmp_path, "b.yml", {"CASE907": _entry("INVOICE"), "CASE908": _entry("BANK_STATEMENT")})
    records = _read(derive_cord([first, second], CONFIG, tmp_path / "cord.jsonl"))
    assert [r["case_id"] for r in records] == ["CASE906", "CASE907"]


def test_derive_cord_creates_parent_directories(tmp_path: Path) -> None:
    """Missing parent directories of the output path are created, as derive_csv does."""
    gt = _gt(tmp_path, "gt.yml", {"CASE909": _entry("RECEIPT")})
    out = tmp_path / "deep" / "er" / "cord.jsonl"
    assert derive_cord([gt], CONFIG, out) == out
    assert out.exists()


def test_derive_cord_applies_identifier_form_from_export_config(tmp_path: Path) -> None:
    """The config's abn_tfn_canonical_form flows through to to_cord, not just accepted and ignored."""
    gt = _gt(tmp_path, "gt.yml", {"CASE910": _entry("RECEIPT")})
    spaced = _read(derive_cord([gt], {"abn_tfn_canonical_form": "spaced"}, tmp_path / "s.jsonl"))
    digits = _read(derive_cord([gt], {"abn_tfn_canonical_form": "digits_only"}, tmp_path / "d.jsonl"))
    assert spaced[0]["gt_parse"]["extension"]["business_abn"] == "10 203 040 506"
    assert digits[0]["gt_parse"]["extension"]["business_abn"] == "10203040506"


def test_derive_cord_propagates_line_item_mismatch_diagnostic(tmp_path: Path) -> None:
    """Mismatched LINE_ITEM_* list lengths surface zip_line_items' four-element diagnostic."""
    entry = _entry("RECEIPT", LINE_ITEM_DESCRIPTIONS="A|B", LINE_ITEM_QUANTITIES="1")
    gt = _gt(tmp_path, "gt.yml", {"CASE911": entry})
    with pytest.raises(ValueError) as err:
        derive_cord([gt], CONFIG, tmp_path / "cord.jsonl")
    assert_diagnostic_error(err.value)


def test_derive_cord_real_corpus_is_receipts_plus_invoices(tmp_path: Path) -> None:
    """On the real ground truth the output has one record per receipt and invoice, no bank statement."""
    receipts = REPO_ROOT / "ground_truth" / "receipts.yml"
    invoices = REPO_ROOT / "ground_truth" / "invoices.yml"
    banks = REPO_ROOT / "ground_truth" / "bank_statements.yml"
    expected = len(yaml.safe_load(receipts.read_text())) + len(yaml.safe_load(invoices.read_text()))
    records = _read(derive_cord([banks, receipts, invoices], CONFIG, tmp_path / "cord.jsonl"))
    assert len(records) == expected
