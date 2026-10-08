"""Tests for the DocILE KILE/LIR mapper (spec Mapping B).

DocILE is restricted to invoices (see generators/exporters/docile.py module
docstring), so every fixture here is an invoice with complete geometry:
every DocILE field renders on an invoice, so a missing box is always a real
capture bug, never a structural absence.

All example values are invented; boxes are distinct per field so a test can
tell a correct field-to-box mapping from an accidental one.
"""

import pytest

from generators.exporters.config import load_export_config
from generators.exporters.docile import KILE_COLUMNS, LIR_COLUMNS, to_docile
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG_PATH = REPO_ROOT / "config" / "export_config.yml"
TOTAL_KEYS = ("TOTAL_AMOUNT_GROSS", "TOTAL_AMOUNT_NET")


@pytest.fixture(scope="module")
def fieldtypes() -> dict[str, str]:
    return load_export_config(CONFIG_PATH)["docile_fieldtypes"]


def _fields(gst_included: str = "true") -> dict[str, str]:
    """An invented invoice with two line items, the second of quantity one."""
    return {
        "DOCUMENT_TYPE": "INVOICE",
        "SUPPLIER_NAME": "Fixture Fabrication Pty Ltd",
        "BUSINESS_ADDRESS": "9 Sample Way, Testville QLD 4999",
        "BUSINESS_ABN": "11 222 333 444",
        "INVOICE_DATE": "14/02/2031",
        "PAYMENT_DUE_DATE": "14/03/2031",
        "GST_AMOUNT": "2.50",
        "TOTAL_AMOUNT": "27.50",
        "IS_GST_INCLUDED": gst_included,
        "PAYER_NAME": "Imogen Quill",
        "PAYER_ADDRESS": "4 Mock Court, Placeholder VIC 3999",
        "LINE_ITEM_DESCRIPTIONS": "Widget Alpha|Widget Beta",
        "LINE_ITEM_QUANTITIES": "2|1",
        "LINE_ITEM_PRICES": "10.00|7.50",
        "LINE_ITEM_TOTAL_PRICES": "20.00|7.50",
    }


def _boxes() -> dict[str, list[float]]:
    """A distinct, valid box for every geometry key the fixture invoice needs."""
    keys = [*KILE_COLUMNS, "TOTAL_AMOUNT"]
    for index in range(2):
        keys.extend(f"{column}[{index}]" for column in LIR_COLUMNS)
    return {key: [0.1, 0.01 * n, 0.2, 0.01 * n + 0.005] for n, key in enumerate(keys, start=1)}


def test_kile_emits_one_entry_per_present_field(fieldtypes: dict[str, str]) -> None:
    """Structural: exactly 9 kile entries (8 KILE_COLUMNS + 1 total variant). Content: the fieldtype set
    matches every configured DocILE fieldtype."""
    result = to_docile(_fields(), _boxes(), fieldtypes)
    assert len(result["kile"]) == len(KILE_COLUMNS) + 1 == 9
    expected = {fieldtypes[column] for column in KILE_COLUMNS} | {fieldtypes["TOTAL_AMOUNT_GROSS"]}
    assert {entry["fieldtype"] for entry in result["kile"]} == expected
    # Every configured fieldtype is either emitted (kile or lir) or is the unused net variant.
    emitted = expected | {entry["fieldtype"] for entry in result["lir"]}
    assert set(fieldtypes.values()) - emitted == {fieldtypes["TOTAL_AMOUNT_NET"]}
    # Texts and boxes come from the matching ground-truth column and geometry key.
    by_type = {entry["fieldtype"]: entry for entry in result["kile"]}
    boxes = _boxes()
    for column in KILE_COLUMNS:
        entry = by_type[fieldtypes[column]]
        assert entry["text"] == _fields()[column]
        assert entry["bbox"] == boxes[column]


def test_gst_inclusive_selects_the_gross_total_fieldtype(fieldtypes: dict[str, str]) -> None:
    """Spec section 5.1: IS_GST_INCLUDED chooses between gross and net."""
    result = to_docile(_fields("true"), _boxes(), fieldtypes)
    totals = [e for e in result["kile"] if e["text"] == "27.50"]
    assert len(totals) == 1
    assert totals[0]["fieldtype"] == fieldtypes["TOTAL_AMOUNT_GROSS"]
    assert totals[0]["bbox"] == _boxes()["TOTAL_AMOUNT"]
    assert fieldtypes["TOTAL_AMOUNT_NET"] not in {e["fieldtype"] for e in result["kile"]}


def test_gst_exclusive_selects_the_net_total_fieldtype(fieldtypes: dict[str, str]) -> None:
    """A GST-exclusive invoice's total is the net total, and no gross fieldtype is emitted."""
    result = to_docile(_fields("false"), _boxes(), fieldtypes)
    totals = [e for e in result["kile"] if e["text"] == "27.50"]
    assert len(totals) == 1
    assert totals[0]["fieldtype"] == fieldtypes["TOTAL_AMOUNT_NET"]
    assert fieldtypes["TOTAL_AMOUNT_GROSS"] not in {e["fieldtype"] for e in result["kile"]}


def test_lir_groups_by_line_item_id(fieldtypes: dict[str, str]) -> None:
    """Structural: 8 lir entries total, 4 per row. Content: row 0's texts are the description, quantity and
    price/total (a quantity-one item collapses unit price and line total to one value), and indices align:
    description[0] and quantity[0] both carry line_item_id 0, description[1] and quantity[1] both carry
    line_item_id 1."""
    lir = to_docile(_fields(), _boxes(), fieldtypes)["lir"]
    assert len(lir) == 8
    rows: dict[int, dict[str, dict]] = {0: {}, 1: {}}
    for entry in lir:
        rows[entry["line_item_id"]][entry["fieldtype"]] = entry
    assert all(len(row) == 4 for row in rows.values())
    texts0 = {ft: e["text"] for ft, e in rows[0].items()}
    assert texts0 == {
        fieldtypes["LINE_ITEM_DESCRIPTIONS"]: "Widget Alpha",
        fieldtypes["LINE_ITEM_QUANTITIES"]: "2",
        fieldtypes["LINE_ITEM_PRICES"]: "10.00",
        fieldtypes["LINE_ITEM_TOTAL_PRICES"]: "20.00",
    }
    texts1 = {ft: e["text"] for ft, e in rows[1].items()}
    assert texts1[fieldtypes["LINE_ITEM_DESCRIPTIONS"]] == "Widget Beta"
    assert texts1[fieldtypes["LINE_ITEM_QUANTITIES"]] == "1"
    # Quantity one: unit price and line total are the same single value.
    assert texts1[fieldtypes["LINE_ITEM_PRICES"]] == texts1[fieldtypes["LINE_ITEM_TOTAL_PRICES"]] == "7.50"
    # Index alignment against the geometry keys.
    boxes = _boxes()
    assert rows[1][fieldtypes["LINE_ITEM_DESCRIPTIONS"]]["bbox"] == boxes["LINE_ITEM_DESCRIPTIONS[1]"]
    assert rows[0][fieldtypes["LINE_ITEM_QUANTITIES"]]["bbox"] == boxes["LINE_ITEM_QUANTITIES[0]"]


def test_every_entry_carries_a_page_and_bbox(fieldtypes: dict[str, str]) -> None:
    """Every kile and lir entry has page 0, a four-number bbox inside the unit square, a fieldtype and text."""
    result = to_docile(_fields(), _boxes(), fieldtypes)
    for entry in [*result["kile"], *result["lir"]]:
        assert entry["page"] == 0
        assert len(entry["bbox"]) == 4
        left, top, right, bottom = entry["bbox"]
        assert 0.0 <= left < right <= 1.0
        assert 0.0 <= top < bottom <= 1.0
        assert entry["fieldtype"] and entry["text"]


def test_missing_geometry_is_a_hard_error(fieldtypes: dict[str, str]) -> None:
    """A silently dropped field is absent from both predictions and truth, which inflates precision instead
    of failing. Every DocILE field on an invoice has geometry, so a missing box always signals a real
    capture bug."""
    boxes = _boxes()
    del boxes["SUPPLIER_NAME"]
    with pytest.raises(KeyError) as err:
        to_docile(_fields(), boxes, fieldtypes)
    assert "SUPPLIER_NAME" in str(err.value)
    assert_diagnostic_error(err.value)


def test_unknown_fieldtype_is_rejected_at_export_time(fieldtypes: dict[str, str]) -> None:
    """A present field with no configured DocILE fieldtype fails with a diagnostic naming the field."""
    trimmed = {k: v for k, v in fieldtypes.items() if k != "PAYER_NAME"}
    with pytest.raises(KeyError) as err:
        to_docile(_fields(), _boxes(), trimmed)
    assert "PAYER_NAME" in str(err.value)
    assert_diagnostic_error(err.value)


def test_missing_geometry_for_a_line_item_is_a_hard_error(fieldtypes: dict[str, str]) -> None:
    """The hard-error rule applies to LIR fields too, not just KILE fields."""
    boxes = _boxes()
    del boxes["LINE_ITEM_QUANTITIES[1]"]
    with pytest.raises(KeyError) as err:
        to_docile(_fields(), boxes, fieldtypes)
    assert "LINE_ITEM_QUANTITIES[1]" in str(err.value)
    assert_diagnostic_error(err.value)


@pytest.mark.parametrize("absent", ["NOT_FOUND", ""])
def test_absent_field_is_omitted_without_requiring_its_box(fieldtypes: dict[str, str], absent: str) -> None:
    """A field absent from ground truth (e.g. NOT_FOUND) must not be emitted, and must not require geometry
    either: only present fields are hard errors when their box is missing."""
    fields = _fields()
    fields["PAYMENT_DUE_DATE"] = absent
    boxes = _boxes()
    del boxes["PAYMENT_DUE_DATE"]
    result = to_docile(fields, boxes, fieldtypes)
    assert len(result["kile"]) == 8
    assert fieldtypes["PAYMENT_DUE_DATE"] not in {e["fieldtype"] for e in result["kile"]}
    # The omission is limited to that one field: the rest are still emitted.
    assert fieldtypes["PAYER_NAME"] in {e["fieldtype"] for e in result["kile"]}
    assert len(result["lir"]) == 8
