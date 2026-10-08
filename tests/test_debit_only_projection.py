"""The extraction projection must emit debit-only bank transaction rows.

The canonical register in ground_truth/bank_statements.yml is the FULL table:
index-aligned TRANSACTION_* columns where a credit row carries NOT_FOUND in the
paid column. That is correct for the register and for transaction linking, and is
not changed here.

The extraction contract (config/extraction_schema.yml, bank_statement) asks for
five fields and has no home for a credit amount, so projecting the full register
would leave placeholder rows a model is never asked to produce. The projection
drops the credit rows, and only the projection: the register is not touched.
"""

import copy
from pathlib import Path

import yaml

from generators.eval_set import NOT_FOUND, _drop_credit_rows, project_fields
from generators.exporters.eval_projection import load_extraction_schema
from tests.conftest import REPO_ROOT


def _bank(**overrides: str) -> dict[str, str]:
    """A projected bank record with credits at positions 1 and 3 (invented values)."""
    record = {
        "DOCUMENT_TYPE": "BANK_STATEMENT",
        "STATEMENT_DATE_RANGE": "01/02/2031 - 28/02/2031",
        "LINE_ITEM_DESCRIPTIONS": "Rent | Salary | Fuel | Refund | Groceries",
        "TRANSACTION_DATES": "02/02/2031 | 03/02/2031 | 04/02/2031 | 05/02/2031 | 06/02/2031",
        "TRANSACTION_AMOUNTS_PAID": "$10.00 | NOT_FOUND | $30.00 | NOT_FOUND | $50.00",
    }
    record.update(overrides)
    return record


class TestDropsCreditRows:
    def test_every_register_column_is_filtered_together(self) -> None:
        """Every index-aligned register column loses the same positions, so rows stay aligned."""
        result = _drop_credit_rows(_bank())
        assert result["LINE_ITEM_DESCRIPTIONS"] == "Rent | Fuel | Groceries"
        assert result["TRANSACTION_DATES"] == "02/02/2031 | 04/02/2031 | 06/02/2031"
        assert result["TRANSACTION_AMOUNTS_PAID"] == "$10.00 | $30.00 | $50.00"

    def test_no_placeholder_survives_the_paid_column(self) -> None:
        """No NOT_FOUND remains in the paid column after projection."""
        result = _drop_credit_rows(_bank())
        assert NOT_FOUND not in result["TRANSACTION_AMOUNTS_PAID"]

    def test_scalars_untouched(self) -> None:
        """Scalar fields (document type, statement range) pass through unchanged."""
        before = _bank()
        result = _drop_credit_rows(before)
        assert result["DOCUMENT_TYPE"] == before["DOCUMENT_TYPE"]
        assert result["STATEMENT_DATE_RANGE"] == before["STATEMENT_DATE_RANGE"]

    def test_key_order_is_preserved(self) -> None:
        """The JSONL key order is the contract, so the filtered record keeps the input's order."""
        before = _bank()
        assert list(_drop_credit_rows(before)) == list(before)

    def test_input_not_mutated(self) -> None:
        """The input mapping is left exactly as it was; a new dict is returned."""
        before = _bank()
        snapshot = copy.deepcopy(before)
        result = _drop_credit_rows(before)
        assert before == snapshot
        assert result is not before

    def test_separator_style_is_preserved(self) -> None:
        """A column joined with a bare pipe stays bare; one with spaced pipes stays spaced."""
        compact = _bank(
            LINE_ITEM_DESCRIPTIONS="Rent|Salary|Fuel|Refund|Groceries",
            TRANSACTION_AMOUNTS_PAID="$10.00|NOT_FOUND|$30.00|NOT_FOUND|$50.00",
        )
        result = _drop_credit_rows(compact)
        assert result["LINE_ITEM_DESCRIPTIONS"] == "Rent|Fuel|Groceries"
        assert result["TRANSACTION_AMOUNTS_PAID"] == "$10.00|$30.00|$50.00"
        assert result["TRANSACTION_DATES"] == "02/02/2031 | 04/02/2031 | 06/02/2031"


class TestLeavesEverythingElseAlone:
    def test_non_bank_untouched(self) -> None:
        """A record of any other document type is returned unchanged, whatever it holds."""
        record = _bank(DOCUMENT_TYPE="INVOICE")
        assert _drop_credit_rows(record) == record

    def test_already_debit_only_unchanged(self) -> None:
        """A register with no credit rows is returned as it was."""
        record = _bank(
            LINE_ITEM_DESCRIPTIONS="Rent | Fuel",
            TRANSACTION_DATES="02/02/2031 | 04/02/2031",
            TRANSACTION_AMOUNTS_PAID="$10.00 | $30.00",
        )
        assert _drop_credit_rows(record) == record

    def test_all_rows_credit_unchanged(self) -> None:
        """Emitting an empty register would be a silently wrong answer key, so it is left alone."""
        record = _bank(TRANSACTION_AMOUNTS_PAID=" | ".join([NOT_FOUND] * 5))
        assert _drop_credit_rows(record) == record

    def test_absent_paid_column_unchanged(self) -> None:
        """With no paid column at all there is nothing to identify credits by."""
        record = _bank()
        del record["TRANSACTION_AMOUNTS_PAID"]
        assert _drop_credit_rows(record) == record

    def test_mismatched_column_length_is_left_whole(self) -> None:
        """A multi-value column whose length differs from the paid column is not filtered by its indices."""
        record = _bank(TRANSACTION_DATES="02/02/2031 | 03/02/2031")
        result = _drop_credit_rows(record)
        assert result["TRANSACTION_DATES"] == "02/02/2031 | 03/02/2031"
        assert result["TRANSACTION_AMOUNTS_PAID"] == "$10.00 | $30.00 | $50.00"

    def test_scalar_paid_value_unchanged(self) -> None:
        """A single paid value (no pipe) is not a register and is returned unchanged."""
        record = _bank(TRANSACTION_AMOUNTS_PAID="$10.00")
        assert _drop_credit_rows(record) == record


def test_projection_applies_the_filter_to_the_real_register() -> None:
    """End to end: a real bank statement from the ground truth projects with no credit placeholders.

    Guards the wiring, not just the helper: `project_fields` must call the filter.
    """
    path = REPO_ROOT / "ground_truth" / "bank_statements.yml"
    schema = load_extraction_schema()
    entries = yaml.safe_load(path.read_text())
    saw_credit = False
    for case_id, entry in entries.items():
        fields = entry["fields"]
        full = str(fields["TRANSACTION_AMOUNTS_PAID"]).split("|")
        saw_credit |= any(item.strip() == NOT_FOUND for item in full)
        projected = project_fields(str(case_id), fields, "BANK_STATEMENT", schema, Path(path))
        paid = projected["TRANSACTION_AMOUNTS_PAID"].split(" | ")
        assert NOT_FOUND not in paid, case_id
        assert len(projected["LINE_ITEM_DESCRIPTIONS"].split(" | ")) == len(paid), case_id
        assert len(projected["TRANSACTION_DATES"].split(" | ")) == len(paid), case_id
    assert saw_credit, "the register carries no credit rows, so this test would prove nothing"
