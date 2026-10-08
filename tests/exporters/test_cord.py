"""Tests for the CORD gt_parse mapper (spec Mapping A)."""

import pytest

from generators.exporters.cord import to_cord


def _receipt() -> dict[str, str]:
    """An invented receipt in the spec's worked-example shape (two quantity-1 rows)."""
    return {
        "DOCUMENT_TYPE": "RECEIPT",
        "SUPPLIER_NAME": "Quillfeather Stationers",
        "BUSINESS_ABN": "11 222 333 444",
        "BUSINESS_ADDRESS": "9 Example Way, Testville VIC 3999",
        "INVOICE_DATE": "14/02/2031",
        "IS_GST_INCLUDED": "true",
        "GST_AMOUNT": "1.24",
        "TOTAL_AMOUNT": "13.60",
        "LINE_ITEM_DESCRIPTIONS": "Gel Pen Set|Index Cards 100pk",
        "LINE_ITEM_QUANTITIES": "1|1",
        "LINE_ITEM_PRICES": "4.73|8.87",
        "LINE_ITEM_TOTAL_PRICES": "4.73|8.87",
    }


def _invoice() -> dict[str, str]:
    return {
        **_receipt(),
        "DOCUMENT_TYPE": "INVOICE",
        "PAYER_NAME": "Hollis Pemberton",
        "PAYER_ADDRESS": "77 Sample St, Mocktown NSW 2999",
    }


def test_case001_matches_the_spec_worked_example() -> None:
    """A receipt in the spec section 4.2 shape maps to exactly the documented tree.

    (adjusted: the live CASE001 no longer carries the spec's values, so the
    fixture is an invented receipt of the same shape.)
    """
    assert to_cord(_receipt(), "spaced") == {
        "menu": [
            {"nm": "Gel Pen Set", "cnt": "1", "unitprice": "4.73", "price": "4.73"},
            {"nm": "Index Cards 100pk", "cnt": "1", "unitprice": "8.87", "price": "8.87"},
        ],
        "sub_total": {"tax_price": "1.24"},
        "total": {"total_price": "13.60"},
        "extension": {
            "supplier_name": "Quillfeather Stationers",
            "business_abn": "11 222 333 444",
            "business_address": "9 Example Way, Testville VIC 3999",
            "invoice_date": "14/02/2031",
        },
    }


def test_is_gst_included_is_not_emitted() -> None:
    """The IS_GST_INCLUDED boolean is generator metadata: neither key nor value is emitted.

    The value 'true' must not appear anywhere in the emitted tree either.
    """
    tree = to_cord(_invoice(), "spaced")
    text = str(tree)
    assert "IS_GST_INCLUDED" not in text
    assert "is_gst_included" not in text.lower()
    assert "true" not in text.lower()


def test_document_type_is_not_emitted() -> None:
    """DOCUMENT_TYPE is not emitted: neither the key nor the value 'RECEIPT'."""
    tree = to_cord(_receipt(), "spaced")
    text = str(tree)
    assert "DOCUMENT_TYPE" not in text
    assert "RECEIPT" not in text


def test_not_found_fields_are_absent() -> None:
    """NOT_FOUND is never emitted: the sentinel is dropped, not carried as a value."""
    fields = {**_receipt(), "BUSINESS_ADDRESS": "NOT_FOUND", "GST_AMOUNT": "NOT_FOUND"}
    tree = to_cord(fields, "spaced")
    assert "NOT_FOUND" not in str(tree)
    assert "business_address" not in tree["extension"]
    assert "sub_total" not in tree


def test_invoice_adds_payer_to_extension() -> None:
    """An invoice's payer name and address land under extension; a receipt has none."""
    tree = to_cord(_invoice(), "spaced")
    assert tree["extension"]["payer_name"] == "Hollis Pemberton"
    assert tree["extension"]["payer_address"] == "77 Sample St, Mocktown NSW 2999"
    assert "payer_name" not in to_cord(_receipt(), "spaced")["extension"]


def test_digits_only_form_applies_to_abn() -> None:
    """The digits_only identifier form strips the spaces from the ABN."""
    assert to_cord(_receipt(), "digits_only")["extension"]["business_abn"] == "11222333444"
    assert to_cord(_receipt(), "spaced")["extension"]["business_abn"] == "11 222 333 444"


def test_digits_only_form_does_not_strip_other_extension_fields() -> None:
    """Identifier-form scoping is limited to BUSINESS_ABN alone.

    INVOICE_DATE is overridden to a value that itself contains spaces, so a
    blanket space-stripping regression has something to strip in every field.
    """
    fields = {**_invoice(), "INVOICE_DATE": "02 03 2031"}
    extension = to_cord(fields, "digits_only")["extension"]
    assert extension["business_abn"] == "11222333444"
    assert extension["invoice_date"] == "02 03 2031"
    assert extension["business_address"] == "9 Example Way, Testville VIC 3999"
    assert extension["supplier_name"] == "Quillfeather Stationers"
    assert extension["payer_name"] == "Hollis Pemberton"
    assert extension["payer_address"] == "77 Sample St, Mocktown NSW 2999"


def test_line_item_totals_sum_to_the_document_total() -> None:
    """Arithmetic sanity: 4.73 + 8.87 == 13.60."""
    tree = to_cord(_receipt(), "spaced")
    summed = sum(float(row["price"]) for row in tree["menu"])
    assert summed == pytest.approx(float(tree["total"]["total_price"]))


def test_line_item_unit_price_and_total_price_map_to_distinct_columns() -> None:
    """LINE_ITEM_PRICES maps to unitprice and LINE_ITEM_TOTAL_PRICES to price.

    A quantity of 2 makes the two genuinely differ (12.50 against 25.00), so a
    swap of the two mappings is caught.
    """
    fields = {
        **_receipt(),
        "LINE_ITEM_DESCRIPTIONS": "Desk Lamp",
        "LINE_ITEM_QUANTITIES": "2",
        "LINE_ITEM_PRICES": "12.50",
        "LINE_ITEM_TOTAL_PRICES": "25.00",
        "TOTAL_AMOUNT": "25.00",
    }
    (row,) = to_cord(fields, "spaced")["menu"]
    assert row == {"nm": "Desk Lamp", "cnt": "2", "unitprice": "12.50", "price": "25.00"}
    assert float(row["cnt"]) * float(row["unitprice"]) == pytest.approx(float(row["price"]))


def test_no_line_items_omits_menu_key() -> None:
    """A record with no LINE_ITEM_* columns at all omits 'menu' rather than emit an empty list."""
    fields = {k: v for k, v in _receipt().items() if not k.startswith("LINE_ITEM_")}
    tree = to_cord(fields, "spaced")
    assert "menu" not in tree
    assert "total" in tree


def test_gst_amount_absent_omits_sub_total_key() -> None:
    """GST_AMOUNT absent (via the NOT_FOUND sentinel) omits 'sub_total' entirely."""
    tree = to_cord({**_receipt(), "GST_AMOUNT": "NOT_FOUND"}, "spaced")
    assert "sub_total" not in tree


def test_total_amount_absent_omits_total_key() -> None:
    """TOTAL_AMOUNT absent (via an omitted key) omits 'total' entirely."""
    fields = {k: v for k, v in _receipt().items() if k != "TOTAL_AMOUNT"}
    assert "total" not in to_cord(fields, "spaced")


def test_all_extension_sources_absent_omits_extension_key() -> None:
    """With every extension source absent, 'extension' is omitted rather than empty.

    Mixes both forms absence takes: NOT_FOUND sentinels for some columns, and
    simply omitted keys for the rest.
    """
    fields = {
        k: v
        for k, v in _invoice().items()
        if k not in {"INVOICE_DATE", "PAYER_NAME", "PAYER_ADDRESS", "BUSINESS_ADDRESS"}
    }
    fields["SUPPLIER_NAME"] = "NOT_FOUND"
    fields["BUSINESS_ABN"] = "NOT_FOUND"
    tree = to_cord(fields, "spaced")
    assert "extension" not in tree
    assert "menu" in tree
