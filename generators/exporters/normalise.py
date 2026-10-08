"""Normalisation rules applied before emitting any target schema.

Implements section 3 of docs/GroundTruth_Export_Spec.md. Pure functions only — no
filesystem access, no config loading, no logging.
"""

NOT_FOUND = "NOT_FOUND"

IDENTIFIER_FORMS = ("spaced", "digits_only")

LINE_ITEM_COLUMNS: dict[str, str] = {
    "LINE_ITEM_DESCRIPTIONS": "description",
    "LINE_ITEM_QUANTITIES": "quantity",
    "LINE_ITEM_PRICES": "unit_price",
    "LINE_ITEM_TOTAL_PRICES": "total_price",
}


def split_pipe_list(value: str) -> list[str]:
    """Split a pipe-delimited ground-truth list into its members.

    A single-member list is stored without a delimiter, so this returns a
    one-element list in that case.

    Args:
        value: The raw field value, e.g. '4.73|8.87'.

    Returns:
        The member strings, each stripped of surrounding whitespace.
    """
    return [part.strip() for part in str(value).split("|")]


def zip_line_items(fields: dict[str, str]) -> list[dict[str, str]]:
    """Zip the four index-aligned line-item lists into per-item dicts.

    Args:
        fields: A document's field mapping.

    Returns:
        One dict per line item with keys description, quantity, unit_price
        and total_price. Empty if the document has no line items.

    Raises:
        ValueError: If the four lists do not have matching member counts.
    """
    if not any(column in fields for column in LINE_ITEM_COLUMNS):
        return []

    columns = {column: split_pipe_list(fields.get(column, "")) for column in LINE_ITEM_COLUMNS}
    counts = {column: len(members) for column, members in columns.items()}
    if len(set(counts.values())) != 1:
        msg = (
            f"What: Line-item list lengths disagree: {counts}.\n"
            f"Where: The four LINE_ITEM_* fields in your ground-truth data.\n"
            f"Expected: All four LINE_ITEM_* lists must have equal member counts "
            f"(pipe-delimited members for LINE_ITEM_DESCRIPTIONS, LINE_ITEM_QUANTITIES, "
            f"LINE_ITEM_PRICES, and LINE_ITEM_TOTAL_PRICES).\n"
            f"Recover: Correct the offending entry in ground_truth/ so every "
            f"LINE_ITEM_* list has the same number of pipe-delimited members, then "
            f"re-run 'python -m generators.pipeline validate'."
        )
        raise ValueError(msg) from None

    count = next(iter(counts.values()))
    return [
        {key: columns[column][index] for column, key in LINE_ITEM_COLUMNS.items()} for index in range(count)
    ]


def validate_identifier_form(form: str) -> None:
    """Validate that form is a recognised identifier form.

    Shared by every export path that accepts an identifier_form, so the
    allowed-values list and its diagnostic live in exactly one place.

    Args:
        form: Either 'spaced' or 'digits_only'.

    Raises:
        ValueError: If form is not a recognised identifier form.
    """
    if form not in IDENTIFIER_FORMS:
        msg = (
            f"What: Unknown identifier form '{form}'.\n"
            f"Where: abn_tfn_canonical_form or abn_tfn_equality_form in "
            f"config/export_config.yml.\n"
            f"Expected: One of {list(IDENTIFIER_FORMS)}.\n"
            f"Recover: Correct the form name to either 'spaced' or 'digits_only' in "
            f"config/export_config.yml."
        )
        raise ValueError(msg) from None


def canonical_identifier(value: str, form: str) -> str:
    """Render an ABN or TFN in the configured canonical form.

    Args:
        value: The stored identifier, space-separated, e.g. '79 104 332 181'.
        form: Either 'spaced' or 'digits_only'.

    Returns:
        The identifier in the requested form.

    Raises:
        ValueError: If form is not a recognised identifier form.
    """
    validate_identifier_form(form)
    if form == "digits_only":
        return value.replace(" ", "")
    return value


def is_present(value: str | None) -> bool:
    """Report whether a field value carries real data.

    Args:
        value: A raw field value.

    Returns:
        False for None, the empty string, and the NOT_FOUND sentinel.
    """
    return value is not None and value != "" and value != NOT_FOUND


def present_fields(fields: dict[str, str]) -> dict[str, str]:
    """Drop absent fields so they are never emitted into a target schema.

    Args:
        fields: A document's field mapping.

    Returns:
        Only those entries whose value is present.
    """
    return {key: value for key, value in fields.items() if is_present(value)}
