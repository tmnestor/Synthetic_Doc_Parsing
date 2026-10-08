"""Load and fail-fast-validate per-field pixel budgets from a layout dict.

Budgets are the single source of truth for how a variable field is allowed to
fit its box. Every key is required — no silent defaults (see the project's
engineering standards).
"""

REQUIRED_BUDGET_KEYS = ("width", "fit", "min_font", "max_lines")
ALLOWED_FITS = ("shrink", "wrap", "shrink_then_wrap")


class LayoutBudgetError(RuntimeError):
    """Raised when a layout's field_budgets block is missing or malformed."""


def _err(what: str, *, layout_path: str, key_path: str, expected: str, recover: str) -> LayoutBudgetError:
    """Build a four-element fail-fast diagnostic error."""
    return LayoutBudgetError(
        "Invalid field_budgets.\n"
        f"  What:     {what}\n"
        f"  Where:    {layout_path} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def field_budget(layout: dict, layout_id: str, field: str, *, layout_path: str) -> dict:
    """Return the validated budget dict for `field` in `layout`.

    Args:
        layout: The single-layout dict (contains a `field_budgets` mapping).
        layout_id: The layout's id, used only in diagnostics.
        field: The variable field whose budget is requested.
        layout_path: Absolute/relative path to the layout YAML, for diagnostics.

    Returns:
        The validated budget dict with all of REQUIRED_BUDGET_KEYS.

    Raises:
        LayoutBudgetError: budgets block, field entry, or a required key is
            missing, or a value is out of range / an invalid enum.
    """
    budgets = layout.get("field_budgets")
    if not isinstance(budgets, dict):
        raise _err(
            f"layout '{layout_id}' has no field_budgets block.",
            layout_path=layout_path,
            key_path=f"{layout_id}.field_budgets",
            expected="a mapping of FIELD -> {width, fit, min_font, max_lines}.",
            recover=f"add a field_budgets block under {layout_id}.",
        )
    entry = budgets.get(field)
    if not isinstance(entry, dict):
        raise _err(
            f"field '{field}' has no budget in layout '{layout_id}'.",
            layout_path=layout_path,
            key_path=f"{layout_id}.field_budgets.{field}",
            expected="width (int px), fit (shrink|wrap|shrink_then_wrap), "
            "min_font (int), max_lines (int >= 1).",
            recover=f"add {field}: {{width, fit, min_font, max_lines}}.",
        )
    missing = [k for k in REQUIRED_BUDGET_KEYS if k not in entry]
    if missing:
        raise _err(
            f"field '{field}' budget missing key(s): {missing}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.field_budgets.{field}",
            expected=f"all of {list(REQUIRED_BUDGET_KEYS)} present.",
            recover=f"add {missing} to {field}.",
        )
    if entry["fit"] not in ALLOWED_FITS:
        raise _err(
            f"field '{field}' has invalid fit {entry['fit']!r}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.field_budgets.{field}.fit",
            expected=f"one of {list(ALLOWED_FITS)}.",
            recover="set fit to an allowed value.",
        )
    for k in ("width", "min_font", "max_lines"):
        if not isinstance(entry[k], int) or entry[k] < 1:
            raise _err(
                f"field '{field}' has invalid {k}={entry[k]!r}.",
                layout_path=layout_path,
                key_path=f"{layout_id}.field_budgets.{field}.{k}",
                expected=f"{k} must be a positive int.",
                recover=f"set {k} to a positive integer.",
            )
    return entry
