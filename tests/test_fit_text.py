"""Tests for fit_text lossless fitting in generators/common.py."""

import pytest

from generators.common import FitError, FitResult, _text_width, fit_text
from tests.conftest import assert_diagnostic_error

FAMILY = "carlito"
TEXT = "Industrial Pressure Washer Hose Fitting Kit"
NOMINAL = 24


def width_at(size: int, text: str = TEXT) -> int:
    return _text_width(text, size, family=FAMILY, bold=False)


def fit(text: str = TEXT, **overrides: object) -> FitResult:
    params: dict = {
        "width": 10_000,
        "fit": "shrink_then_wrap",
        "min_font": 10,
        "max_lines": 2,
        "nominal_size": NOMINAL,
        "family": FAMILY,
    }
    params.update(overrides)
    return fit_text(text, **params)


def test_fits_as_is_returns_nominal_single_line() -> None:
    """A string that fits at the nominal size comes back untouched, for every strategy."""
    for strategy in ("shrink", "wrap", "shrink_then_wrap"):
        result = fit(width=width_at(NOMINAL), fit=strategy)
        assert result.lines == [TEXT]
        assert result.size == NOMINAL
        assert result.line_height > 0


def test_shrink_reduces_size_until_it_fits() -> None:
    """Shrink keeps the whole string on one line and picks the largest size that fits."""
    target = 17
    result = fit(width=width_at(target), fit="shrink")
    assert result.lines == [TEXT]
    assert result.size < NOMINAL
    assert width_at(result.size) <= width_at(target)
    assert result.size >= target
    assert width_at(result.size + 1) > width_at(target)


def test_shrink_raises_fiterror_below_floor() -> None:
    """If even min_font is too wide, shrink fails loudly rather than truncating."""
    with pytest.raises(FitError):
        fit(width=width_at(12) - 1, fit="shrink", min_font=12)
    # The same width is fine once the floor is lowered, so the floor is the cause.
    assert fit(width=width_at(12) - 1, fit="shrink", min_font=8).lines == [TEXT]


def test_wrap_splits_into_allowed_lines() -> None:
    """Wrap breaks at word boundaries, at the nominal size, every line within the width."""
    width = width_at(NOMINAL) // 2 + 40
    result = fit(width=width, fit="wrap", max_lines=3)
    assert 2 <= len(result.lines) <= 3
    assert result.size == NOMINAL
    assert " ".join(result.lines) == TEXT
    assert all(width_at(NOMINAL, line) <= width for line in result.lines)


def test_wrap_raises_when_exceeds_max_lines() -> None:
    """Wrap fails when the string needs more lines than allowed, or a single word is too wide."""
    width = width_at(NOMINAL) // 3
    with pytest.raises(FitError):
        fit(width=width, fit="wrap", max_lines=2)
    assert len(fit(width=width, fit="wrap", max_lines=6).lines) > 2
    with pytest.raises(FitError):
        fit("Pneumatic", width=width_at(NOMINAL, "Pneumatic") - 1, fit="wrap", max_lines=5)


def test_shrink_then_wrap_prefers_shrink_then_wraps() -> None:
    """shrink_then_wrap is wrap-first: at each size it wraps within max_lines before shrinking.

    With one line allowed it can only shrink; with two or more it wraps at the nominal size.
    Never truncates: the wrapped lines always rejoin to the exact string.
    (adjusted: wrap-first is intended, per Tod, 6 October 2026; the design doc was stale)
    """
    shrunk = fit(width=width_at(16), fit="shrink_then_wrap", max_lines=1)
    assert shrunk.lines == [TEXT]
    assert shrunk.size < NOMINAL

    floor = 12
    width = width_at(floor) // 2 + 30
    wrapped = fit(width=width, fit="shrink_then_wrap", min_font=floor, max_lines=3)
    assert len(wrapped.lines) > 1
    assert " ".join(wrapped.lines) == TEXT
    assert wrapped.size >= floor
    assert all(width_at(wrapped.size, line) <= width for line in wrapped.lines)

    # Wrap-first: a few pixels too narrow for one line at nominal size, two lines allowed.
    narrow = width_at(NOMINAL) - 3
    first = fit(width=narrow, fit="shrink_then_wrap", max_lines=2)
    assert first.size == NOMINAL
    assert len(first.lines) == 2
    assert " ".join(first.lines) == TEXT
    assert all(width_at(NOMINAL, line) <= narrow for line in first.lines)


def test_impossible_fit_raises_four_element_diagnostic() -> None:
    """An unfittable string raises a FitError carrying What, Where, Expected and Recover."""
    with pytest.raises(FitError) as err:
        fit(width=20, fit="shrink_then_wrap", max_lines=2)
    assert_diagnostic_error(err.value)
    assert "field_budgets" in str(err.value)
    assert TEXT in str(err.value)
