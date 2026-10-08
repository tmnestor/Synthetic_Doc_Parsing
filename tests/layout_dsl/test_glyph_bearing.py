"""Text never inks past the margin it is aligned to, whatever glyph it starts or ends with.

A glyph can ink outside its own advance: bold Carlito "Y" reaches 1 px left of its origin, as do "j" and
"w" in most faces. Left-aligned text drawn at a margin used to cross it by that pixel. The text primitive
now compensates (`left_draw_x`, `right_draw_x` in generators/common.py), and this sweep proves it for
every vendored face, both weights, every size a layout can reach (from the smallest budget `min_font` to
the largest declared type size) and every capital, digit and lower-case letter, as the first glyph of a
left-aligned line and the last glyph of a right-aligned one.
"""

import string

import pytest
from PIL import Image, ImageDraw

from generators import common
from generators.common import (
    FONT_FAMILIES,
    draw_fitted_left,
    draw_fitted_right,
    load_font,
    vendored_weights,
)
from tests.renderer_helpers import layouts_for
from tests.snapshot_tools import DOC_TYPES

_GLYPHS = string.ascii_uppercase + string.digits + string.ascii_lowercase
_MARGIN = 40
_CANVAS = (360, 160)


def _sizes() -> range:
    """Every size from the smallest budget `min_font` to the largest declared type size of any layout."""
    sizes: set[int] = set()
    floors: set[int] = set()
    for doc_type in DOC_TYPES:
        for layout in layouts_for(doc_type).values():
            sizes |= {int(v) for v in layout.get("font_sizes", {}).values()}
            floors |= {
                int(b["min_font"]) for b in layout.get("field_budgets", {}).values() if "min_font" in b
            }
    return range(min(floors), max(sizes) + 1)


def _ink(text: str, family: str, bold: bool, size: int, align: str) -> tuple[int, int]:
    """Draw one line through the fitted helpers and return its ink's left and right columns."""
    image = Image.new("L", _CANVAS, 0)
    draw = ImageDraw.Draw(image)
    budget = {"width": _CANVAS[0], "fit": "shrink", "min_font": size, "max_lines": 1}
    if align == "left":
        draw_fitted_left(
            draw,
            text,
            _MARGIN,
            40,
            budget=budget,
            nominal_size=size,
            family=family,
            bold=bold,
            fill="white",
        )
    else:
        draw_fitted_right(
            draw,
            text,
            _CANVAS[0] - _MARGIN,
            40,
            budget=budget,
            nominal_size=size,
            family=family,
            bold=bold,
            fill="white",
        )
    box = image.getbbox()
    assert box is not None, (text, family, bold, size)
    return box[0], box[2]


def _overhangs(families: tuple[str, ...] = tuple(FONT_FAMILIES)) -> list[tuple[str, bool, int, str, str]]:
    """Every (family, bold, size, glyph, side) whose ink crosses its margin."""
    found = []
    for family in families:
        for bold in vendored_weights(family):
            for size in _sizes():
                for glyph in _GLYPHS:
                    left, _ = _ink(f"{glyph}x", family, bold, size, "left")
                    if left < _MARGIN:
                        found.append((family, bold, size, glyph, "left"))
                    _, right = _ink(f"x{glyph}", family, bold, size, "right")
                    if right > _CANVAS[0] - _MARGIN:
                        found.append((family, bold, size, glyph, "right"))
    return found


def test_the_sweep_covers_the_sizes_the_layouts_use():
    sizes = _sizes()
    assert sizes.start <= 10 and sizes.stop > 48


@pytest.mark.slow
def test_no_glyph_inks_past_its_margin_in_any_face_weight_or_size():
    assert _overhangs() == []


def test_without_compensation_bold_carlito_y_crosses_the_left_margin(monkeypatch):
    """The sweep can fail: drawn at the pen position, as before, bold Carlito "Y" inks 1 px past."""
    monkeypatch.setattr(common, "left_draw_x", lambda font, text, x: x)
    left, _ = _ink("Yx", "carlito", True, 28, "left")
    assert left == _MARGIN - 1
    found = [hit for hit in _overhangs(("carlito",)) if hit[3] == "Y"]
    assert ("carlito", True, 28, "Y", "left") in found


def test_compensation_moves_only_a_line_whose_first_glyph_overhangs():
    font = load_font(28, family="carlito", bold=True)
    assert common.left_draw_x(font, "Yarrow Legal", 100) == 101
    assert common.left_draw_x(font, "Ashby Legal", 100) == 100
    width = font.getbbox("Total")[2]
    assert common.right_draw_x(font, "Total", 500) == 500 - width
