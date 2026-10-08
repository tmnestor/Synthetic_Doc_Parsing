"""The extraction projection keeps every receipt unit price, because every one is printed.

A receipt line of more than one unit prints a till-style unit line beneath its description
(`2 @ $3.50`), and a line of one unit prints its total, which is its unit price. The projection
therefore no longer blanks any unit price: what the answer key holds is on the page.
"""

from pathlib import Path

from generators import eval_set
from generators.eval_set import project_fields
from generators.exporters.eval_projection import load_extraction_schema


def test_the_blanking_filter_is_gone() -> None:
    """The old print-only filter must not come back: it would blank values the page now prints."""
    assert not hasattr(eval_set, "_blank_unprinted_unit_prices")


def test_receipt_keeps_every_unit_price() -> None:
    """End to end through `project_fields`: a quantity-3 line keeps its unit price (invented values)."""
    schema = load_extraction_schema()
    raw = {
        "DOCUMENT_TYPE": "RECEIPT",
        "LINE_ITEM_QUANTITIES": "1|3",
        "LINE_ITEM_PRICES": "5.00|3.24",
        "LINE_ITEM_TOTAL_PRICES": "5.00|9.72",
    }
    receipt = project_fields("CASE900", raw, "RECEIPT", schema, Path("receipts.yml"))
    assert receipt["LINE_ITEM_PRICES"] == "$5.00 | $3.24"
    assert receipt["LINE_ITEM_TOTAL_PRICES"] == "$5.00 | $9.72"


def test_invoice_is_unchanged() -> None:
    """An invoice of the same shape keeps its prices, as it always did."""
    schema = load_extraction_schema()
    raw = {
        "DOCUMENT_TYPE": "INVOICE",
        "LINE_ITEM_QUANTITIES": "1|3",
        "LINE_ITEM_PRICES": "5.00|3.24",
        "LINE_ITEM_TOTAL_PRICES": "5.00|9.72",
    }
    invoice = project_fields("CASE900", raw, "INVOICE", schema, Path("i.yml"))
    assert invoice["LINE_ITEM_PRICES"] == "$5.00 | $3.24"
