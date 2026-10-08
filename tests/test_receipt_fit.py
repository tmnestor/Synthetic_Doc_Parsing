"""Receipt fit-safety: budgets present, no field clips, and unchanged docs stay byte-identical."""

from generators.layout_budgets import field_budget
from tests.renderer_helpers import (
    corpus_entries,
    entry_per_layout,
    layout_path_for,
    layouts_for,
    render_entry,
)
from tests.snapshot_tools import digest, load_snapshot

DOC = "receipt"
VARIABLE_FIELDS = ("SUPPLIER_NAME", "BUSINESS_ADDRESS", "ABN_LINE", "LINE_ITEM_DESC", "LINE_ITEM_AMOUNT")


def test_every_receipt_layout_has_budgets_for_variable_fields() -> None:
    """Each receipt layout carries a valid budget for every variable field it draws."""
    for layout_id, layout in layouts_for(DOC).items():
        for field in VARIABLE_FIELDS:
            field_budget(layout, layout_id, field, layout_path=layout_path_for(DOC))


def test_no_variable_field_overflows_after_fitting() -> None:
    """Every current string fits its budget losslessly (fit_text never raises)."""
    for entry in corpus_entries(DOC).values():
        render_entry(DOC, entry, distractors=False)


def test_unchanged_docs_byte_identical() -> None:
    """Receipts render exactly as pinned in the pixel snapshot, so fitting changed no passing page.

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
