"""Pixel-level anchor test for draw-time bounding boxes (Task 12b).

Task 12's DocILE self-score is structurally blind to systematic bbox bugs:
the gold annotations, synthesised OCR and predictions all derive from the
same `to_docile` output, so a transposed axis or a wrong offset moves all
three in lockstep and still scores 1.0. Nothing else verifies that a captured
bounding box actually lands on the *rendered pixels* of its field's text.

For most fields the recorded box is exactly the renderer's own draw
coordinate, so it cannot drift from the glyphs. INVOICE_DATE is the
exception: `capture_label_prefixed_value` (generators/common.py) computes the
box by measuring the pixel width of the literal "Date: " label and shifting
the box right by that amount, so the recorded box covers the value, not the
label. That arithmetic is exactly the kind of bug that could be subtly wrong
while every geometry-only test still passes, because none of them look at
pixels.

This file renders real documents with a BoxRecorder, converts each
normalised box back to pixel coordinates, and measures the *actual ink* of
the rendered PIL image against it. Presence of ink is not enough: a later
stage builds an overlay on these boxes, so the tests pin the documented
contract with measured numbers.

The contract, as measured:

- Horizontally a box hugs its ink: left and right edges are within a few
  pixels of the first and last inked column (glyph side bearings only).
- Vertically a recorded text box starts at the renderer's *draw y*, not at
  the ink top. PIL draws a line with its font's bbox top as an internal
  offset, so the ink starts a few pixels below the box top. Boxes from
  `draw_text_right` and `capture_label_prefixed_value` are exactly as tall as
  the ink (so the box is the ink rectangle shifted up by that offset);
  boxes from `draw_fitted_left` are the whole line slot (draw y to the next
  line's y), which holds the bulk of the ink.

A negative control (a page region known to be empty) proves the check can
actually fail, rather than trivially passing on any crop.
"""

import functools
from collections.abc import Iterator

import numpy as np
import pytest

from tests.renderer_helpers import entry_per_layout, layouts_for, render_entry

INK_THRESHOLD = 128
# Side bearings only: a box edge may sit this far from the first or last inked column.
EDGE_TOLERANCE = 4
# The box shift and the ink shift of a tight box differ by at most this many pixels.
HEIGHT_TOLERANCE = 3
# The recorded draw y sits above the ink top by the font's bbox top; the largest seen is 14 px.
MAX_TOP_OFFSET = 16
# The "Date: " label is far wider than a side bearing.
MIN_LABEL_INK = 15
# Descender ink below a slot box's bottom edge, measured on every invoice layout: 3 px for the
# line-item description (slot = 28 px font size, ink 24-25 px starting 6-7 px below the draw y), none
# for the supplier. Allow 4.
DESCENDER_ALLOWANCE = 4

Box = tuple[int, int, int, int]


@functools.cache
def _render(doc_type: str, layout_id: str) -> tuple[np.ndarray, dict]:
    """Render one entry per layout once; return its ink mask and recorded geometry."""
    geometry: dict = {}
    image = render_entry(
        doc_type, entry_per_layout(doc_type)[layout_id], geometry_out=geometry, distractors=False
    )
    assert image.size == (geometry["width"], geometry["height"])
    return np.asarray(image.convert("L")) < INK_THRESHOLD, geometry


def _pixel_box(geometry: dict, field: str) -> Box:
    left, top, right, bottom = geometry["boxes"][field]
    width, height = geometry["width"], geometry["height"]
    return round(left * width), round(top * height), round(right * width), round(bottom * height)


def _assert_covers_text(ink: np.ndarray, box: Box) -> None:
    """Assert the pixels under `box` hold text: ink across most of the box's width.

    Raises:
        AssertionError: If the crop is blank, or its ink spans under 80% of the box width.
    """
    left, top, right, bottom = box
    crop = ink[top:bottom, left:right]
    columns = np.nonzero(crop.any(axis=0))[0]
    assert columns.size > 0, f"box {box} covers blank paper"
    spread = int(columns.max() - columns.min() + 1)
    assert spread >= 0.8 * (right - left), f"ink spans {spread}px of a {right - left}px box {box}"


def _ink_run(ink: np.ndarray, box: Box) -> tuple[int, int, int, int]:
    """Measure the first run of inked rows at or below the box top, within the box columns.

    Returns:
        (ink_top, ink_bottom, ink_left, ink_right), all inclusive pixel coordinates; the horizontal
        extent is measured a few pixels beyond the box columns so overhang is visible.
    """
    left, top, right, _ = box
    rows: np.ndarray = np.asarray(ink[:, left:right].any(axis=1))
    row = top
    while row < rows.size and not rows[row]:
        row += 1
    ink_top = row
    while row < rows.size and rows[row]:
        row += 1
    ink_bottom = row - 1
    margin = EDGE_TOLERANCE + 2
    columns = np.nonzero(ink[ink_top : ink_bottom + 1, left - margin : right + margin].any(axis=0))[0]
    return ink_top, ink_bottom, int(columns.min()) + left - margin, int(columns.max()) + left - margin


def _assert_hugs_ink_horizontally(ink: np.ndarray, box: Box) -> tuple[int, int]:
    """Assert the box's left and right edges sit on the first and last inked columns; return the run rows."""
    ink_top, ink_bottom, ink_left, ink_right = _ink_run(ink, box)
    assert -2 <= ink_left - box[0] <= EDGE_TOLERANCE, f"left edge {box[0]} vs ink {ink_left}"
    assert -EDGE_TOLERANCE <= ink_right - box[2] <= 2, f"right edge {box[2]} vs ink {ink_right}"
    return ink_top, ink_bottom


def _assert_tight_box_is_ink_shifted_up(ink: np.ndarray, box: Box) -> None:
    """Assert a tight text box is the ink rectangle shifted up by the font's bbox-top offset."""
    ink_top, ink_bottom = _assert_hugs_ink_horizontally(ink, box)
    _, top, _, bottom = box
    assert abs((ink_bottom - ink_top + 1) - (bottom - top)) <= HEIGHT_TOLERANCE, "box height != ink height"
    assert 0 < ink_top - top <= MAX_TOP_OFFSET, "box must start at the draw y, above the ink top"
    assert abs((ink_top - top) - (ink_bottom - bottom)) <= HEIGHT_TOLERANCE, (
        "box is not a pure shift of the ink"
    )


def _assert_slot_box_holds_ink(ink: np.ndarray, box: Box, slot_height: int) -> None:
    """Assert a line-slot box is exactly `slot_height` tall, starts at the draw y and holds the ink.

    `draw_fitted_left` records (x, draw y, x + width, y after advancing one line pitch per line),
    so a single-line box is exactly one pitch tall whatever the ink height.
    """
    ink_top, ink_bottom = _assert_hugs_ink_horizontally(ink, box)
    _, top, _, bottom = box
    assert bottom - top == slot_height, f"slot is {bottom - top}px tall, the line pitch is {slot_height}px"
    assert 0 <= ink_top - top <= MAX_TOP_OFFSET, "slot must start at the draw y, at or above the ink top"
    # Descenders may hang below the slot: the pitch can be shorter than the ink (measured 3 px on the
    # line-item description, 0 on the supplier, whose slot is far taller than its ink).
    assert ink_bottom - bottom <= DESCENDER_ALLOWANCE, f"ink hangs {ink_bottom - bottom}px below the slot"
    overlap = min(ink_bottom, bottom - 1) - max(ink_top, top) + 1
    assert overlap >= 0.8 * (ink_bottom - ink_top + 1), "slot holds under 80% of the ink rows"


def _blocks(node: object) -> Iterator[dict]:
    """Yield every dict nested anywhere in a layout body."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from _blocks(value)


def _supplier_pitch(layout_id: str) -> int:
    """The SUPPLIER_NAME text block's own `line_advance`: its slot is one such pitch per line."""
    layout = layouts_for("invoice")[layout_id]
    (block,) = [b for b in _blocks(layout["body"]) if b.get("field") == "SUPPLIER_NAME"]
    return int(block["line_advance"])


def _line_item_pitch(layout_id: str) -> int:
    """The line-item cell's slot: `table_cell_line_spacing: font`, i.e. the fitted font's own size.

    The description column draws at the layout's `subheader` size (28 px) and its 870 px budget never
    forces a shrink on the first row, so the slot is that size, not the 52 px table row.
    """
    layout = layouts_for("invoice")[layout_id]
    assert layout["defaults"]["table_cell_line_spacing"] == "font"
    return int(layout["font_sizes"]["subheader"])


def _blank_strip(ink: np.ndarray) -> Box:
    """A strip down the page's right margin (rows 5% to 95%), asserted blank so it is a true control."""
    height, width = ink.shape
    box = (width - 8, int(height * 0.05), width - 2, int(height * 0.95))
    assert not ink[box[1] : box[3], box[0] : box[2]].any(), "control strip is not actually blank"
    return box


def _layouts(doc_type: str) -> list[str]:
    return list(layouts_for(doc_type))


@pytest.fixture(scope="module")
def invoice_layout() -> str:
    return _layouts("invoice")[0]


@pytest.fixture(scope="module")
def receipt_layout() -> str:
    return _layouts("receipt")[0]


def test_blank_margin_region_fails_the_text_check_on_invoice(invoice_layout: str) -> None:
    """A known-empty strip of page must NOT pass `_assert_covers_text`. This is the negative control: without
    it, a test that simply measures "some darkness" could pass on any crop, including one on blank paper,
    which is exactly the failure mode this file exists to catch."""
    ink, geometry = _render("invoice", invoice_layout)
    with pytest.raises(AssertionError, match="blank paper"):
        _assert_covers_text(ink, _blank_strip(ink))
    # The same check passes on a real field of the same page, so it can tell the two apart.
    _assert_covers_text(ink, _pixel_box(geometry, "SUPPLIER_NAME"))


def test_blank_margin_region_fails_the_text_check_on_receipt(receipt_layout: str) -> None:
    """Same negative control on a receipt page (different aspect ratio/DPI)."""
    ink, geometry = _render("receipt", receipt_layout)
    assert ink.shape[1] < 500  # a narrow thermal page, nothing like the A4 invoice
    with pytest.raises(AssertionError, match="blank paper"):
        _assert_covers_text(ink, _blank_strip(ink))
    _assert_covers_text(ink, _pixel_box(geometry, "SUPPLIER_NAME"))


@pytest.mark.parametrize("layout_id", _layouts("invoice"))
def test_invoice_date_box_lands_on_the_value_pixels(layout_id: str) -> None:
    """The INVOICE_DATE box (offset past 'Date: ' by Task 10b) must cover ink."""
    ink, geometry = _render("invoice", layout_id)
    box = _pixel_box(geometry, "INVOICE_DATE")
    _assert_covers_text(ink, box)
    _assert_tight_box_is_ink_shifted_up(ink, box)


@pytest.mark.parametrize("layout_id", _layouts("invoice"))
def test_invoice_date_box_excludes_the_label_to_its_left(layout_id: str) -> None:
    """The region immediately LEFT of the captured box must ALSO contain ink. That region is where the
    "Date: " label itself sits. If the offset arithmetic were wrong and the box's left edge started at the
    line origin (i.e. included the label), there would be nothing left of the box to inspect. Finding ink
    here, with the value box starting to its right, is the positive proof that the box begins after the
    label rather than at the label's start."""
    ink, geometry = _render("invoice", layout_id)
    _assert_label_lies_left_of_box(ink, _pixel_box(geometry, "INVOICE_DATE"))


def _assert_label_lies_left_of_box(ink: np.ndarray, box: Box) -> None:
    left = box[0]
    ink_top, ink_bottom, _, _ = _ink_run(ink, box)
    band = ink[ink_top : ink_bottom + 1]
    start = max(left - 60, 0)
    columns = np.nonzero(band[:, start:left].any(axis=0))[0]
    assert columns.size > 0, "no label ink to the left of the box"
    label_left = int(columns.min()) + start
    label_right = int(columns.max()) + start
    assert label_left <= left - MIN_LABEL_INK, f"label starts only {left - label_left}px left of the box"
    # The label ends before the box does: a gap (the space after the colon) separates them.
    assert label_right < left - 1, "label ink touches the box edge"
    assert not band[:, left - 1].any(), "the column just left of the box is inked"


@pytest.mark.parametrize("layout_id", _layouts("receipt"))
def test_receipt_date_box_lands_on_value_and_excludes_label(layout_id: str) -> None:
    """Same label-offset anchor on the receipt renderer. generators/receipt.py draws INVOICE_DATE via the same
    `capture_label_prefixed_value("Date: ", ...)` helper as invoices (see the receipt_meta/title/metadata
    section branches), so it carries the same Task 10b offset-arithmetic risk and gets the same two-sided
    check."""
    ink, geometry = _render("receipt", layout_id)
    box = _pixel_box(geometry, "INVOICE_DATE")
    _assert_covers_text(ink, box)
    _assert_tight_box_is_ink_shifted_up(ink, box)
    _assert_label_lies_left_of_box(ink, box)


@pytest.mark.parametrize("layout_id", _layouts("invoice"))
def test_invoice_supplier_name_box_lands_on_text(layout_id: str) -> None:
    """SUPPLIER_NAME's box is the renderer's own draw_fitted_left extent: a sanity check that the anchoring
    method itself is sound, independent of any Task 10b offset arithmetic."""
    ink, geometry = _render("invoice", layout_id)
    box = _pixel_box(geometry, "SUPPLIER_NAME")
    _assert_covers_text(ink, box)
    _assert_slot_box_holds_ink(ink, box, _supplier_pitch(layout_id))


@pytest.mark.parametrize("layout_id", _layouts("invoice"))
def test_invoice_total_amount_box_lands_on_text(layout_id: str) -> None:
    """TOTAL_AMOUNT is captured via draw_text_right's own extent."""
    ink, geometry = _render("invoice", layout_id)
    box = _pixel_box(geometry, "TOTAL_AMOUNT")
    _assert_covers_text(ink, box)
    _assert_tight_box_is_ink_shifted_up(ink, box)
    # Right-aligned: the box's right edge is the renderer's right margin, so the ink ends on it.
    assert abs(_ink_run(ink, box)[3] - box[2]) <= EDGE_TOLERANCE


@pytest.mark.parametrize("layout_id", _layouts("invoice"))
def test_invoice_line_item_description_box_lands_on_text(layout_id: str) -> None:
    """LINE_ITEM_DESCRIPTIONS[0] is captured via draw_fitted_left's own extent."""
    ink, geometry = _render("invoice", layout_id)
    box = _pixel_box(geometry, "LINE_ITEM_DESCRIPTIONS[0]")
    _assert_covers_text(ink, box)
    _assert_slot_box_holds_ink(ink, box, _line_item_pitch(layout_id))
