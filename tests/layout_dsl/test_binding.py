"""Field binding: `{FIELD}` substitution and presence tests, nothing more."""

import pytest

from generators.layout_dsl.binding import BindingError, interpolate, is_present, referenced_fields


def test_referenced_fields_finds_all_placeholders() -> None:
    template = "{SUPPLIER_NAME} / {BUSINESS_ABN} / {SUPPLIER_NAME} {LINE_2}"
    assert referenced_fields(template) == ["SUPPLIER_NAME", "BUSINESS_ABN", "SUPPLIER_NAME", "LINE_2"]


def test_referenced_fields_ignores_lowercase_braces() -> None:
    assert referenced_fields("{lower} {Mixed} {1BAD} {}") == []
    assert referenced_fields("{lower} {REAL}") == ["REAL"]


def test_referenced_fields_on_literal_returns_empty() -> None:
    assert referenced_fields("Tax Invoice") == []
    assert referenced_fields("") == []


def test_interpolate_substitutes_values() -> None:
    fields = {"A": "alpha", "B_2": 42}
    assert interpolate("{A}-{B_2}!", fields) == "alpha-42!"
    assert interpolate("no placeholders", fields) == "no placeholders"


def test_interpolate_raises_on_unknown_field() -> None:
    with pytest.raises(BindingError) as exc_info:
        interpolate("Hello {MISSING}", {"A": "alpha"})
    message = str(exc_info.value)
    assert "MISSING" in message
    assert "'A'" in message  # lists what is available
    assert "Remediation:" in message


def test_interpolate_renders_not_found_as_empty() -> None:
    assert interpolate("[{A}]", {"A": "NOT_FOUND"}) == "[]"


def test_is_present_false_for_missing_blank_and_not_found() -> None:
    fields = {"BLANK": "", "NF": "NOT_FOUND", "NONE": None}
    assert not is_present(fields, "ABSENT")
    assert not is_present(fields, "BLANK")
    assert not is_present(fields, "NF")
    assert not is_present(fields, "NONE")


def test_is_present_true_for_real_value() -> None:
    assert is_present({"A": "x"}, "A")
    assert is_present({"A": 0}, "A")
    assert is_present({"A": "0.00"}, "A")
