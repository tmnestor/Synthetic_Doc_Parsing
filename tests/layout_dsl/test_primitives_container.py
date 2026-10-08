"""Nesting containers (panel, split) lay children into child regions through the injected walker.

Children are real blocks rendered through the engine's walker onto a real canvas. Region claims are read back
from a `BoxRecorder` (right-aligned and left-aligned text pin a column's edges exactly) and line claims from
the pixels.
"""

import pytest
from PIL import Image, ImageChops

from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.context import Region
from generators.layout_dsl.primitives_container import ContainerError, draw_panel, draw_split
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import MARGIN, PAGE_WIDTH, make_drawing_ctx, real_layout

PAGE_HEIGHT = 600
REGION = Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN)
# Invented values; none appear in ground_truth/.
FIELDS = {"PAYER_NAME": "Jarrah Quill", "SUPPLIER_NAME": "Mallee Parts"}


def container_layout(**defaults) -> dict:
    """Return a real receipt layout with `defaults` overridden."""
    layout = real_layout([])
    layout["defaults"].update(defaults)
    return layout


def text(content: str = "{PAYER_NAME}", **extra) -> dict:
    """Return a one-line text block (a 20px advance in this layout)."""
    return {"type": "text", "content": content, **extra}


def spacer(height: int) -> dict:
    """Return an inkless spacer block."""
    return {"type": "spacer", "height": height}


def context(layout: dict | None = None, *, walker: bool = True, recorder: BoxRecorder | None = None):
    """Return a (context, image) pair carrying `FIELDS`."""
    entry = {"case_id": "CASE999", "layout": "x", "fields": FIELDS}
    return make_drawing_ctx(layout or container_layout(), entry, walker=walker, recorder=recorder)


def box_px(recorder: BoxRecorder, field: str) -> list[int]:
    """Return a recorded box back in pixels."""
    left, top, right, bottom = recorder.as_dict()[field]
    return [
        round(left * PAGE_WIDTH),
        round(top * PAGE_HEIGHT),
        round(right * PAGE_WIDTH),
        round(bottom * PAGE_HEIGHT),
    ]


def ink_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the bounding box of everything that is not white."""
    return ImageChops.invert(image.convert("L")).getbbox()


def test_container_without_injected_walker_fails_loudly() -> None:
    """A panel built on a context with no `render_children` raises a four-element diagnostic."""
    ctx, _ = context(walker=False)
    with pytest.raises(ContainerError) as exc_info:
        draw_panel({"type": "panel", "children": [text()]}, ctx, 0)
    assert_diagnostic_error(exc_info.value)
    assert "render_children" in str(exc_info.value)


def test_panel_advances_past_its_children() -> None:
    """The cursor ends past the padded children: top padding, children, bottom padding."""
    ctx, _ = context()
    block = {"type": "panel", "padding": 8, "children": [text(), text()]}
    assert draw_panel(block, ctx, 100) == 100 + 8 + 2 * 20 + 8


def test_panel_honours_fixed_height() -> None:
    """A fixed `height` sets the panel's bottom regardless of how short its children are."""
    ctx, _ = context()
    block = {"type": "panel", "padding": 8, "height": 120, "children": [text()]}
    assert draw_panel(block, ctx, 100) == 220


def test_split_returns_tallest_column() -> None:
    """The cursor ends at the bottom of the tallest column."""
    ctx, _ = context()
    block = {"type": "split", "children": [[text()], [text(), text(), text()], [text(), text()]]}
    assert draw_split(block, ctx, 50) == 50 + 3 * 20


def test_split_gives_each_column_its_own_non_overlapping_region() -> None:
    """Each column draws into its own slice of the region, left to right, with no overlap."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = context(recorder=recorder)
    gap = 10
    block = {
        "type": "split",
        "gap": gap,
        "children": [
            [text(align="right", field="LEFT_COL")],
            [text("{SUPPLIER_NAME}", align="left", field="RIGHT_COL")],
        ],
    }
    draw_split(block, ctx, 20)
    first, second = REGION.divide(2, gap=gap)
    left_box, right_box = box_px(recorder, "LEFT_COL"), box_px(recorder, "RIGHT_COL")
    assert left_box[2] == first.right
    assert right_box[0] == second.x
    assert left_box[2] < right_box[0]


def test_split_widths_gives_each_column_its_explicit_region() -> None:
    """Invoice totals occupy a fixed column at the right edge, which equal division cannot express.

    Explicit `widths` (here 96 and 200, with a 20px gap) place each column exactly, and the default equal
    division would have put the second column's right edge elsewhere.
    """
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = context(recorder=recorder)
    block = {
        "type": "split",
        "gap": 20,
        "widths": [96, 200],
        "children": [
            [text(align="right", field="LEFT_COL")],
            [text("{SUPPLIER_NAME}", align="right", field="RIGHT_COL")],
        ],
    }
    draw_split(block, ctx, 20)
    assert box_px(recorder, "LEFT_COL")[2] == MARGIN + 96
    assert box_px(recorder, "RIGHT_COL")[2] == MARGIN + 96 + 20 + 200
    equal_second = REGION.divide(2, gap=20)[1]
    assert equal_second.right != MARGIN + 96 + 20 + 200


def test_split_without_widths_still_divides_equally() -> None:
    """Absent `widths`, draw_split's default path is unchanged: Region.divide's equal columns."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = context(recorder=recorder)
    block = {
        "type": "split",
        "gap": 10,
        "children": [
            [text(align="right", field="A")],
            [text("{SUPPLIER_NAME}", align="right", field="B")],
        ],
    }
    draw_split(block, ctx, 20)
    first, second = REGION.divide(2, gap=10)
    assert first.width == second.width
    assert box_px(recorder, "A")[2] == first.right
    assert box_px(recorder, "B")[2] == second.right


def test_split_without_injected_walker_fails_loudly() -> None:
    """A split on a context with no `render_children` raises a four-element diagnostic."""
    ctx, _ = context(walker=False)
    with pytest.raises(ContainerError) as exc_info:
        draw_split({"type": "split", "children": [[text()], [text()]]}, ctx, 0)
    assert_diagnostic_error(exc_info.value)


def test_panel_with_fixed_height_overflowing_children_fails() -> None:
    """Children taller than the declared fixed height raise rather than overdrawing the border."""
    ctx, _ = context()
    block = {"type": "panel", "padding": 8, "height": 30, "children": [text(), text()]}
    with pytest.raises(ContainerError) as exc_info:
        draw_panel(block, ctx, 100)
    assert_diagnostic_error(exc_info.value)
    assert "height: 30" in str(exc_info.value)
    assert "overflows" in str(exc_info.value)


def test_panel_border_rectangle_encloses_padded_children() -> None:
    """The border runs from the region's left to its right edge and encloses the children plus padding."""
    ctx, image = context(container_layout(panel_border_color="red"))
    padding = 8
    block = {"type": "panel", "padding": padding, "children": [text(), text()]}
    bottom = draw_panel(block, ctx, 100)
    red = (255, 0, 0)
    assert image.getpixel((MARGIN, 120)) == red
    assert image.getpixel((REGION.right, 120)) == red
    assert image.getpixel((MARGIN + 50, 100)) == red
    assert image.getpixel((MARGIN + 50, bottom)) == red
    # Black ink is the children: it sits inside the border, clear of the left edge by the padding.
    ink = image.convert("L").point(lambda v: 255 if v < 30 else 0).getbbox()
    assert ink is not None
    assert ink[0] >= MARGIN + padding
    assert 100 + padding <= ink[1] < ink[3] <= bottom - padding + 4


def test_split_without_divider_draws_no_line() -> None:
    """With no `divider:`, an inkless split leaves the page untouched."""
    ctx, image = context()
    draw_split({"type": "split", "gap": 10, "children": [[spacer(30)], [spacer(30)]]}, ctx, 20)
    assert ink_box(image) is None


def test_split_with_divider_draws_a_line_at_the_gap_midpoint() -> None:
    """Westpac's rewards panel splits into two equal columns with a ruled line between them.

    The line sits at the midpoint of the gap and runs the height of the tallest column.
    """
    ctx, image = context()
    block = {"type": "split", "gap": 10, "divider": True, "children": [[spacer(30)], [spacer(30)]]}
    bottom = draw_split(block, ctx, 20)
    first, second = REGION.divide(2, gap=10)
    divider_x = (first.right + second.x) // 2
    assert ink_box(image) == (divider_x, 20, divider_x + 1, bottom + 1)
    assert image.getpixel((divider_x, 20)) == (0, 0, 0)
    assert image.getpixel((divider_x, bottom)) == (0, 0, 0)


def test_split_divider_color_is_configurable() -> None:
    """A block's `divider_color`, or failing that the layout's `split_divider_color`, colours the line."""
    first, second = REGION.divide(2, gap=10)
    divider_x = (first.right + second.x) // 2
    children = [[spacer(30)], [spacer(30)]]

    ctx, image = context()
    draw_split(
        {"type": "split", "gap": 10, "divider": True, "divider_color": "red", "children": children}, ctx, 20
    )
    assert image.getpixel((divider_x, 30)) == (255, 0, 0)

    ctx, image = context(container_layout(split_divider_color="blue"))
    draw_split({"type": "split", "gap": 10, "divider": True, "children": children}, ctx, 20)
    assert image.getpixel((divider_x, 30)) == (0, 0, 255)
