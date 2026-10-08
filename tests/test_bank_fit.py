"""Bank statement fit-safety: transaction descriptions wrap losslessly instead of being truncated.

Budgets present, nothing clips, and documents whose descriptions already fit
stay byte-identical.
"""

from generators.bank_statement import _parse_transactions
from generators.common import FitResult, fit_text
from generators.layout_budgets import field_budget
from tests.renderer_helpers import (
    corpus_entries,
    entry_per_layout,
    layout_path_for,
    layouts_for,
    render_entry,
)
from tests.snapshot_tools import digest, load_snapshot

DOC = "bank_statement"


def fit_description(text: str, layout_id: str) -> FitResult:
    """Fit one description exactly as the row renderer does: layout body size, layout family."""
    layout = layouts_for(DOC)[layout_id]
    budget = field_budget(layout, layout_id, "TRANSACTION_DESC", layout_path=layout_path_for(DOC))
    return fit_text(
        text,
        width=budget["width"],
        fit=budget["fit"],
        min_font=budget["min_font"],
        max_lines=budget["max_lines"],
        nominal_size=layout["font_sizes"]["body"],
        family=layout["defaults"]["family"],
    )


def test_every_bank_layout_has_transaction_desc_budget_that_wraps() -> None:
    """Each bank layout budgets TRANSACTION_DESC with fit=wrap over at least two lines: lossless, no truncation."""
    for layout_id, layout in layouts_for(DOC).items():
        budget = field_budget(layout, layout_id, "TRANSACTION_DESC", layout_path=layout_path_for(DOC))
        assert budget["fit"] == "wrap", layout_id
        assert budget["max_lines"] >= 2, layout_id


def test_no_bank_description_overflows_after_fitting() -> None:
    """Every description in the corpus fits its layout's budget losslessly, and is never shortened."""
    checked = 0
    for case_id, entry in corpus_entries(DOC).items():
        for txn in _parse_transactions(entry["fields"]):
            result = fit_description(txn["description"], entry["layout"])
            assert " ".join(result.lines) == txn["description"], f"{case_id}: text altered by fitting"
            checked += 1
    assert checked > 0


def test_unchanged_bank_docs_byte_identical() -> None:
    """Statements whose descriptions all fit on one line render exactly as pinned in the pixel snapshot.

    (adjusted: compared with the pinned pixel snapshot, since the pre-fit
    renderer no longer exists to compare against.)
    """
    pinned = load_snapshot()
    entries = entry_per_layout(DOC)
    checked = 0
    for layout_id, entry in entries.items():
        key = f"{entry['case_id']}_{layout_id}"
        fits_unwrapped = all(
            len(fit_description(t["description"], layout_id).lines) == 1
            for t in _parse_transactions(entry["fields"])
        )
        if key in pinned and fits_unwrapped:
            assert digest(render_entry(DOC, entry, distractors=False)) == pinned[key]["hash"], key
            checked += 1
    assert checked >= 1
