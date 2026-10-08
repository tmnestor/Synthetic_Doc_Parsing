"""Tests for the fail-fast field_budgets loader."""

import pytest

from generators.layout_budgets import LayoutBudgetError, field_budget
from tests.conftest import assert_diagnostic_error
from tests.renderer_helpers import layout_path_for, layouts_for

LAYOUT_PATH = "config/layouts/example.yml"


def layout_with(**entry: object) -> dict:
    """A one-field layout whose SUPPLIER_NAME budget is valid unless overridden or removed."""
    budget: dict = {"width": 400, "fit": "shrink", "min_font": 10, "max_lines": 1}
    budget.update(entry)
    return {"field_budgets": {"SUPPLIER_NAME": budget}}


def get(layout: dict) -> dict:
    return field_budget(layout, "example_layout", "SUPPLIER_NAME", layout_path=LAYOUT_PATH)


def test_returns_valid_budget() -> None:
    """A complete budget is returned as-is, and every real layout's budgets load."""
    assert get(layout_with()) == {"width": 400, "fit": "shrink", "min_font": 10, "max_lines": 1}
    for doc_type in ("receipt", "invoice", "bank_statement"):
        for layout_id, layout in layouts_for(doc_type).items():
            for field in layout["field_budgets"]:
                budget = field_budget(layout, layout_id, field, layout_path=layout_path_for(doc_type))
                assert budget["fit"] in ("shrink", "wrap", "shrink_then_wrap")


def test_missing_field_budgets_block_is_diagnostic() -> None:
    """A layout with no field_budgets block names the layout in the diagnostic."""
    with pytest.raises(LayoutBudgetError) as err:
        get({})
    assert_diagnostic_error(err.value)
    assert "example_layout.field_budgets" in str(err.value)
    assert LAYOUT_PATH in str(err.value)


def test_missing_field_entry_is_diagnostic() -> None:
    """A field absent from the block names the field and its key path."""
    with pytest.raises(LayoutBudgetError) as err:
        field_budget(layout_with(), "example_layout", "BUSINESS_ADDRESS", layout_path=LAYOUT_PATH)
    assert_diagnostic_error(err.value)
    assert "example_layout.field_budgets.BUSINESS_ADDRESS" in str(err.value)


def test_missing_required_key_is_diagnostic() -> None:
    """A budget lacking a required key names the missing key; there is no silent default."""
    layout = layout_with()
    del layout["field_budgets"]["SUPPLIER_NAME"]["max_lines"]
    with pytest.raises(LayoutBudgetError) as err:
        get(layout)
    assert_diagnostic_error(err.value)
    assert "max_lines" in str(err.value)


def test_invalid_fit_enum_is_diagnostic() -> None:
    """An unknown fit strategy is rejected with the allowed values listed."""
    with pytest.raises(LayoutBudgetError) as err:
        get(layout_with(fit="truncate"))
    assert_diagnostic_error(err.value)
    assert "truncate" in str(err.value)
    assert "shrink_then_wrap" in str(err.value)


@pytest.mark.parametrize(
    ("key", "bad"),
    [("width", 0), ("min_font", -3), ("max_lines", 0), ("width", "wide"), ("max_lines", 1.5)],
)
def test_invalid_numeric_value_is_diagnostic(key: str, bad: object) -> None:
    """A non-positive or non-integer width, min_font or max_lines is rejected with its key path."""
    with pytest.raises(LayoutBudgetError) as err:
        get(layout_with(**{key: bad}))
    assert_diagnostic_error(err.value)
    assert f"field_budgets.SUPPLIER_NAME.{key}" in str(err.value)
