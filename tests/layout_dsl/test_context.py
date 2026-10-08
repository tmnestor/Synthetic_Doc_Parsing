"""Region arithmetic: the only place nesting geometry lives."""

import dataclasses

import pytest
from PIL import Image, ImageDraw

from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.context import Region, RenderContext
from tests.conftest import assert_diagnostic_error


def test_indent_narrows_and_shifts() -> None:
    inner = Region(x=10, width=100).indent(5)
    assert inner == Region(x=15, width=95)


def test_indent_applies_right_inset() -> None:
    inner = Region(x=10, width=100).indent(5, 20)
    assert inner == Region(x=15, width=75)
    assert inner.right == 90


def test_divide_splits_evenly_with_gap() -> None:
    left, right = Region(x=0, width=100).divide(2, gap=10)
    assert (left.x, left.width) == (0, 45)
    assert (right.x, right.width) == (55, 45)


def test_divide_rejects_gap_wider_than_region() -> None:
    with pytest.raises(ValueError, match="no usable column width"):
        Region(x=0, width=10).divide(3, gap=10)


def test_right_edge() -> None:
    assert Region(x=12, width=396).right == 408


def test_divide_last_column_reaches_region_right() -> None:
    """Floor division drops up to n-1 px, so the remainder is handed to the leftmost columns."""
    for width, n, gap in ((101, 2, 0), (100, 3, 0), (100, 3, 7), (1601, 3, 4), (7, 7, 0)):
        region = Region(x=5, width=width)
        columns = region.divide(n, gap=gap)
        assert columns[-1].right == region.right, (width, n, gap)
        assert max(c.width for c in columns) - min(c.width for c in columns) <= 1
        for before, after in zip(columns, columns[1:]):
            assert after.x == before.right + gap


def test_divide_widths_places_explicit_columns() -> None:
    """A fixed 400px column at the right edge: the exact-fit boundary sum(widths) == region.width."""
    region = Region(x=0, width=1600)
    narrow, fixed = region.divide_widths([1200, 400], gap=0)
    assert (narrow.x, narrow.width) == (0, 1200)
    assert (fixed.x, fixed.width) == (1200, 400)
    assert fixed.right == region.right


def test_divide_widths_steps_by_width_plus_gap_like_divide() -> None:
    """Each column's x is the previous right edge plus the gap, as `divide` steps."""
    columns = Region(x=20, width=300).divide_widths([50, 70, 90], gap=10)
    assert [c.x for c in columns] == [20, 80, 160]
    for before, after in zip(columns, columns[1:]):
        assert after.x == before.right + 10
    evenly = Region(x=20, width=300).divide(3, gap=10)
    for before, after in zip(evenly, evenly[1:]):
        assert after.x == before.right + 10


def test_divide_widths_rejects_a_sum_that_overflows_the_region() -> None:
    with pytest.raises(ValueError, match="overflows the region"):
        Region(x=0, width=100).divide_widths([60, 41], gap=0)
    with pytest.raises(ValueError, match="overflows the region"):
        Region(x=0, width=100).divide_widths([50, 50], gap=1)


def test_divide_widths_accepts_a_sum_exactly_equal_to_the_region() -> None:
    """Boundary: sum(widths) + gap * (n - 1) == self.width must not raise."""
    columns = Region(x=0, width=101).divide_widths([50, 50], gap=1)
    assert columns[-1].right == 101


def test_region_errors_are_diagnostic() -> None:
    with pytest.raises(ValueError) as indent_err:
        Region(x=0, width=10).indent(5, 5)
    assert_diagnostic_error(indent_err.value)
    assert "padding" in str(indent_err.value)

    with pytest.raises(ValueError) as divide_err:
        Region(x=0, width=10).divide(3, gap=10)
    assert_diagnostic_error(divide_err.value)
    assert "gap" in str(divide_err.value)

    with pytest.raises(ValueError) as widths_err:
        Region(x=0, width=100).divide_widths([80, 80], gap=0)
    assert_diagnostic_error(widths_err.value)
    assert "widths" in str(widths_err.value)


def _context(cls: type = RenderContext, **extra: object) -> RenderContext:
    recorder = BoxRecorder(10, 10)
    return cls(
        draw=ImageDraw.Draw(Image.new("RGB", (10, 10))),
        entry={"fields": {}},
        layout={"id": 1},
        layout_id="L",
        layout_path="p.yml",
        region=Region(x=1, width=8),
        recorder=recorder,
        render_children=lambda blocks, ctx, y: y,
        ink_recorder=recorder,
        dx_gated=True,
        **extra,
    )


def test_within_changes_only_the_region() -> None:
    ctx = _context()
    child = ctx.within(Region(x=2, width=3))
    assert child.region == Region(x=2, width=3)
    for field in dataclasses.fields(RenderContext):
        if field.name != "region":
            assert getattr(child, field.name) is getattr(ctx, field.name), field.name


def test_within_keeps_a_field_added_later() -> None:
    """A field the context gains later is carried into a child region without editing `within`."""
    extended = dataclasses.make_dataclass(
        "Extended", [("later", object, dataclasses.field(default=None))], bases=(RenderContext,)
    )
    marker = object()
    child = _context(extended, later=marker).within(Region(x=2, width=3))
    assert type(child) is extended
    assert child.later is marker  # type: ignore[attr-defined]
