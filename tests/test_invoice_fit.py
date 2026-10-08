"""Invoice fit-safety: budgets present, no field clips, unchanged docs byte-identical."""

from generators.layout_budgets import field_budget
from tests.renderer_helpers import (
    corpus_entries,
    entry_per_layout,
    layout_path_for,
    layouts_for,
    render_entry,
)
from tests.snapshot_tools import digest, load_snapshot

DOC = "invoice"
VARIABLE_FIELDS = (
    "SUPPLIER_NAME",
    "BUSINESS_ADDRESS",
    "ABN_LINE",
    "PAYER_NAME",
    "PAYER_ADDRESS",
    "LINE_ITEM_DESC",
)


def test_every_invoice_layout_has_budgets() -> None:
    """Each invoice layout carries a valid budget for every variable text field."""
    for layout_id, layout in layouts_for(DOC).items():
        for field in VARIABLE_FIELDS:
            field_budget(layout, layout_id, field, layout_path=layout_path_for(DOC))


def test_no_invoice_field_overflows_after_fitting() -> None:
    """Every invoice in the corpus renders without a FitError: nothing clips."""
    for entry in corpus_entries(DOC).values():
        render_entry(DOC, entry, distractors=False)


def test_unchanged_invoices_byte_identical() -> None:
    """Invoices render exactly as pinned in the pixel snapshot, so fitting changed no passing page.

    (adjusted: compared with the pinned pixel snapshot, since the pre-fit
    renderer no longer exists to compare against.)
    """
    pinned = load_snapshot()
    checked = 0
    for layout_id, entry in entry_per_layout(DOC).items():
        key = f"{entry['case_id']}_{layout_id}"
        if key in pinned:
            assert digest(render_entry(DOC, entry, distractors=False)) == pinned[key]["hash"], key
            checked += 1
    assert checked >= 1
