"""Tests for the shared normalisation rules (spec section 3)."""

import pytest

from generators.exporters.normalise import (
    canonical_identifier,
    is_present,
    present_fields,
    split_pipe_list,
    zip_line_items,
)
from tests.conftest import assert_diagnostic_error


def test_split_pipe_list_splits_on_pipe() -> None:
    """A pipe-delimited value splits into its members, in order."""
    assert split_pipe_list("4.73|8.87|1.00") == ["4.73", "8.87", "1.00"]


def test_split_pipe_list_handles_single_value() -> None:
    """A single-item list has no pipe: a bare '3.76' is a one-member list."""
    assert split_pipe_list("3.76") == ["3.76"]


def test_split_pipe_list_strips_whitespace() -> None:
    """Members are stripped of surrounding whitespace, interior spaces are kept."""
    assert split_pipe_list(" 4.73 | two words |  8.87") == ["4.73", "two words", "8.87"]


def test_zip_line_items_pairs_by_index() -> None:
    """The four index-aligned lists become one dict per item, member i of each list together."""
    fields = {
        "LINE_ITEM_DESCRIPTIONS": "Alpha|Beta",
        "LINE_ITEM_QUANTITIES": "2|1",
        "LINE_ITEM_PRICES": "3.00|4.50",
        "LINE_ITEM_TOTAL_PRICES": "6.00|4.50",
    }
    assert zip_line_items(fields) == [
        {"description": "Alpha", "quantity": "2", "unit_price": "3.00", "total_price": "6.00"},
        {"description": "Beta", "quantity": "1", "unit_price": "4.50", "total_price": "4.50"},
    ]


def test_zip_line_items_returns_empty_for_missing_columns() -> None:
    """If none of the LINE_ITEM_* columns are present, return an empty list."""
    assert zip_line_items({"SUPPLIER_NAME": "Fixture Pty Ltd"}) == []


def test_zip_line_items_rejects_count_mismatch() -> None:
    """A short zip would silently emit a partial menu that scores as a near-match."""
    fields = {
        "LINE_ITEM_DESCRIPTIONS": "Alpha|Beta",
        "LINE_ITEM_QUANTITIES": "2",
        "LINE_ITEM_PRICES": "3.00|4.50",
        "LINE_ITEM_TOTAL_PRICES": "6.00|4.50",
    }
    with pytest.raises(ValueError, match="LINE_ITEM_QUANTITIES") as err:
        zip_line_items(fields)
    assert_diagnostic_error(err.value)


def test_canonical_identifier_spaced_is_unchanged() -> None:
    """The spaced form leaves the stored identifier exactly as it is."""
    assert canonical_identifier("11 222 333 444", "spaced") == "11 222 333 444"


def test_canonical_identifier_digits_only_strips_spaces() -> None:
    """The digits-only form removes every space."""
    assert canonical_identifier("11 222 333 444", "digits_only") == "11222333444"


def test_canonical_identifier_rejects_unknown_form() -> None:
    """An unrecognised form fails with a diagnostic listing the allowed forms."""
    with pytest.raises(ValueError, match="dotted") as err:
        canonical_identifier("11 222 333 444", "dotted")
    assert_diagnostic_error(err.value)
    assert "spaced" in str(err.value)
    assert "digits_only" in str(err.value)


@pytest.mark.parametrize("value", [None, "", "NOT_FOUND"])
def test_is_present_rejects_absent_markers(value: str | None) -> None:
    """None, the empty string and the NOT_FOUND sentinel are all absent."""
    assert is_present(value) is False


@pytest.mark.parametrize("value", ["12.00", "x", "0", "not_found"])
def test_is_present_accepts_a_real_value(value: str) -> None:
    """Any other string is present, including '0' and a lower-case near-miss of the sentinel."""
    assert is_present(value) is True


def test_present_fields_drops_not_found() -> None:
    """Absent entries are removed and present ones keep their values."""
    assert present_fields({"A": "x", "B": "NOT_FOUND", "C": "", "D": "y"}) == {"A": "x", "D": "y"}


def test_present_fields_keeps_all_present() -> None:
    """A mapping with no absent value is returned with identical content, as a new dict."""
    original = {"A": "x", "B": "y"}
    result = present_fields(original)
    assert result == original
    assert result is not original


def test_present_fields_empty_when_all_absent() -> None:
    """present_fields takes dict[str, str]; None-absence is covered by is_present above."""
    assert present_fields({"A": "NOT_FOUND", "B": ""}) == {}
