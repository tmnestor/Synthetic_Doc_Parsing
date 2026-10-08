"""Native export for document types with no public-schema equivalent.

Implements section 7 of docs/GroundTruth_Export_Spec.md. Bank statements are
emitted in a project-defined schema rather than force-fitted into CORD or
DocILE.
"""

from generators.exporters.normalise import is_present, present_fields, split_pipe_list

STATEMENT_TYPES: frozenset[str] = frozenset({"BANK_STATEMENT"})

TRANSACTION_COLUMNS: dict[str, str] = {
    "TRANSACTION_DATES": "date",
    "TRANSACTION_DESCRIPTIONS": "description",
    "TRANSACTION_AMOUNTS_PAID": "amount_paid",
    "TRANSACTION_AMOUNTS_RECEIVED": "amount_received",
}


def to_native(fields: dict[str, str]) -> dict:
    """Build the native record for one statement document.

    Args:
        fields: The document's field mapping from ground_truth/*.yml.

    Returns:
        The present scalar fields, plus a 'transactions' array for statement
        types whose register columns are populated. A debit row has no
        amount_received and a credit row has no amount_paid; the ground truth
        stores that absent half as a NOT_FOUND member between pipes (verified
        against ground_truth/bank_statements.yml), and it is scrubbed to ''
        here rather than emitted as the literal sentinel — an empty '' member
        is legitimate data, never a sign of a dropped field.

    Raises:
        ValueError: If the transaction register columns have mismatched counts.
    """
    present = present_fields(fields)
    record: dict[str, str | list[dict[str, str]]] = {
        k: v for k, v in present.items() if k not in TRANSACTION_COLUMNS
    }

    if fields.get("DOCUMENT_TYPE") not in STATEMENT_TYPES:
        return record

    columns = {
        column: split_pipe_list(present[column]) for column in TRANSACTION_COLUMNS if column in present
    }
    if not columns:
        return record

    counts = {column: len(members) for column, members in columns.items()}
    if len(set(counts.values())) != 1:
        msg = (
            f"What: Transaction register list lengths disagree: {counts}.\n"
            f"Where: The TRANSACTION_* fields of the offending entry in "
            f"ground_truth/bank_statements.yml.\n"
            f"Expected: Every present TRANSACTION_* list is an index-aligned "
            f"column and must have the same number of pipe-delimited members, "
            f"e.g. TRANSACTION_DATES: '02/03/2023|05/03/2023' paired with "
            f"TRANSACTION_AMOUNTS_PAID: '13.60|NOT_FOUND' — two members each.\n"
            f"Recover: Correct the entry in ground_truth/ so every TRANSACTION_* "
            f"list has the same member count, then re-run "
            f"'python -m generators.pipeline validate'."
        )
        raise ValueError(msg) from None

    count = next(iter(counts.values()))
    record["transactions"] = [
        {
            key: columns[column][index] if column in columns and is_present(columns[column][index]) else ""
            for column, key in TRANSACTION_COLUMNS.items()
        }
        for index in range(count)
    ]
    return record
