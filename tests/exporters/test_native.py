"""Tests for the native statement schema (spec section 7).

Statement fixtures are invented (dates in 2031, amounts and names made up).
"""

import pytest

from generators.exporters.native import STATEMENT_TYPES, TRANSACTION_COLUMNS, to_native
from tests.conftest import assert_diagnostic_error


def _statement(**overrides: str) -> dict[str, str]:
    """An invented three-row statement: one debit, one credit, one debit."""
    fields = {
        "DOCUMENT_TYPE": "BANK_STATEMENT",
        "SUPPLIER_NAME": "Fixture Mutual Bank",
        "STATEMENT_DATE_RANGE": "01/03/2031 - 31/03/2031",
        "ACCOUNT_HOLDER": "Imogen Quill",
        "TRANSACTION_DATES": "02/03/2031|05/03/2031|09/03/2031",
        "TRANSACTION_DESCRIPTIONS": "CARD PURCHASE ALPHA|SALARY CREDIT|CARD PURCHASE BETA",
        "TRANSACTION_AMOUNTS_PAID": "13.60|NOT_FOUND|7.25",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|2500.00|NOT_FOUND",
    }
    fields.update(overrides)
    return fields


def test_statement_transactions_are_unzipped_per_row() -> None:
    """The four index-aligned register columns become one dict per row, member i of each column together."""
    record = to_native(_statement())
    assert record["transactions"] == [
        {
            "date": "02/03/2031",
            "description": "CARD PURCHASE ALPHA",
            "amount_paid": "13.60",
            "amount_received": "",
        },
        {
            "date": "05/03/2031",
            "description": "SALARY CREDIT",
            "amount_paid": "",
            "amount_received": "2500.00",
        },
        {
            "date": "09/03/2031",
            "description": "CARD PURCHASE BETA",
            "amount_paid": "7.25",
            "amount_received": "",
        },
    ]


def test_statement_scalars_are_preserved_alongside_transactions() -> None:
    """Scalar fields pass through unchanged and the raw TRANSACTION_* columns are not duplicated."""
    record = to_native(_statement())
    assert record["SUPPLIER_NAME"] == "Fixture Mutual Bank"
    assert record["STATEMENT_DATE_RANGE"] == "01/03/2031 - 31/03/2031"
    assert record["ACCOUNT_HOLDER"] == "Imogen Quill"
    assert not set(TRANSACTION_COLUMNS) & set(record)


def test_non_statement_document_has_no_transactions_array() -> None:
    """A receipt that happens to carry TRANSACTION_* columns never gets a transactions array."""
    record = to_native(_statement(DOCUMENT_TYPE="RECEIPT"))
    assert "transactions" not in record
    assert record["SUPPLIER_NAME"] == "Fixture Mutual Bank"


def test_not_found_is_dropped() -> None:
    """A top-level NOT_FOUND (or empty) scalar is dropped rather than emitted."""
    record = to_native(_statement(ACCOUNT_HOLDER="NOT_FOUND", SUPPLIER_NAME=""))
    assert "ACCOUNT_HOLDER" not in record
    assert "SUPPLIER_NAME" not in record
    assert record["STATEMENT_DATE_RANGE"] == "01/03/2031 - 31/03/2031"


def test_ragged_transaction_lists_are_rejected() -> None:
    """Register columns with different member counts cannot be aligned and are rejected."""
    with pytest.raises(ValueError, match="TRANSACTION_DATES"):
        to_native(_statement(TRANSACTION_DATES="02/03/2031|05/03/2031"))


def test_ragged_transaction_lists_raise_a_four_element_diagnostic() -> None:
    """The mismatch error must be a fail-fast diagnostic, not a bare ValueError: it must name what
    disagrees, where to fix it, what a valid entry looks like, and how to recover, per the project's
    fail-fast standard."""
    with pytest.raises(ValueError) as err:
        to_native(_statement(TRANSACTION_AMOUNTS_PAID="13.60"))
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "TRANSACTION_AMOUNTS_PAID" in message
    assert "ground_truth/bank_statements.yml" in message


def test_per_member_not_found_sentinel_becomes_empty_string() -> None:
    """The real corpus (ground_truth/bank_statements.yml) writes an absent register member as the literal
    NOT_FOUND token between pipes, e.g. 'TRANSACTION_AMOUNTS_PAID: 219.04|NOT_FOUND'. That must be scrubbed
    to "" like a genuinely empty member: the NOT_FOUND sentinel must never survive into a transactions
    row."""
    fields = _statement(
        TRANSACTION_DATES="02/03/2031|05/03/2031",
        TRANSACTION_DESCRIPTIONS="CARD PURCHASE ALPHA|SALARY CREDIT",
        TRANSACTION_AMOUNTS_PAID="219.04|NOT_FOUND",
        TRANSACTION_AMOUNTS_RECEIVED="NOT_FOUND|1.00",
    )
    rows = to_native(fields)["transactions"]
    assert rows[0]["amount_paid"] == "219.04"
    assert rows[0]["amount_received"] == ""
    assert rows[1]["amount_paid"] == ""
    assert rows[1]["amount_received"] == "1.00"
    # A genuinely empty member is scrubbed to the same empty string.
    empty = to_native(_statement(TRANSACTION_AMOUNTS_PAID="13.60||7.25"))["transactions"]
    assert empty[1]["amount_paid"] == ""


def test_not_found_sentinel_never_appears_anywhere_in_output() -> None:
    """Sweeps every emitted string (top-level and per-transaction-row) for the literal NOT_FOUND token,
    rather than checking just the one field the other tests target."""
    record = to_native(_statement(ACCOUNT_HOLDER="NOT_FOUND"))
    strings: list[str] = []
    for value in record.values():
        if isinstance(value, list):
            for row in value:
                strings.extend(row.values())
        else:
            strings.append(value)
    assert strings  # the sweep is not vacuous
    assert not [s for s in strings if "NOT_FOUND" in s]
    assert any(isinstance(v, list) for v in record.values())


def test_statement_missing_amounts_received_column_defaults_to_empty() -> None:
    """A statement entry with no TRANSACTION_AMOUNTS_RECEIVED column at all (not merely an empty value for
    it) must still carry the key with "" on each transaction row."""
    fields = _statement()
    del fields["TRANSACTION_AMOUNTS_RECEIVED"]
    rows = to_native(fields)["transactions"]
    assert len(rows) == 3
    assert all(row["amount_received"] == "" for row in rows)
    assert [row["amount_paid"] for row in rows] == ["13.60", "", "7.25"]


def test_statement_type_with_no_register_columns_present_has_no_transactions_key() -> None:
    """A statement-type document with none of the TRANSACTION_* columns present must omit the
    'transactions' key entirely, not emit an empty list."""
    fields = {k: v for k, v in _statement().items() if k not in TRANSACTION_COLUMNS}
    record = to_native(fields)
    assert "transactions" not in record
    assert record["SUPPLIER_NAME"] == "Fixture Mutual Bank"
    # All register columns absent by sentinel behaves the same as absent by omission.
    sentinel = to_native({**fields, **{column: "NOT_FOUND" for column in TRANSACTION_COLUMNS}})
    assert "transactions" not in sentinel


def test_structural_shape_of_a_statement_record() -> None:
    """Structural assertion alongside the content assertions above: every transaction row has exactly the
    four expected keys, in the schema's column mapping, and 'transactions' is a list of dicts."""
    transactions = to_native(_statement())["transactions"]
    assert isinstance(transactions, list)
    assert all(isinstance(row, dict) for row in transactions)
    for row in transactions:
        assert list(row) == ["date", "description", "amount_paid", "amount_received"]
        assert list(row) == list(TRANSACTION_COLUMNS.values())


def test_document_type_membership_constants() -> None:
    """Guards the module's public constants against silent drift."""
    assert STATEMENT_TYPES == frozenset({"BANK_STATEMENT"})
    assert TRANSACTION_COLUMNS == {
        "TRANSACTION_DATES": "date",
        "TRANSACTION_DESCRIPTIONS": "description",
        "TRANSACTION_AMOUNTS_PAID": "amount_paid",
        "TRANSACTION_AMOUNTS_RECEIVED": "amount_received",
    }
