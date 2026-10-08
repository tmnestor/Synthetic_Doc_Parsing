"""Every compensated draw path keeps an overhanging glyph inside its region.

`tests/layout_dsl/test_glyph_bearing.py` sweeps faces, sizes and glyphs through the fitted helpers. Each
other path that draws text against a margin (an unbudgeted `text` block, left and right; the three `pair`
paths; the banner; `draw_text_left`, `draw_text_right` and `draw_line_item`) applies the compensation on
its own, so each is checked here with bold Carlito "Y", whose ink starts 1 px left of its pen, and is
shown to fail with that path's compensation removed.
"""

import copy

import pytest
from PIL import Image, ImageDraw

from generators import common
from generators.common import draw_line_item, draw_text_left, draw_text_right, load_font
from generators.layout_dsl import primitives_text
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks
from generators.loader import load_layout_registry
from tests.conftest import REPO_ROOT

BANK = REPO_ROOT / "config" / "layouts" / "bank_statements.yml"
LAYOUT_ID = "cba_standard"
WIDTH, MARGIN = 600, 60
ENTRY = {"case_id": "CASE999", "layout": LAYOUT_ID, "fields": {"TOTAL_AMOUNT": "40.00"}}
BOLD = {"family": "carlito", "bold": True}

# path -> (block list, which edge to check). Every string starts with an overhanging glyph.
DSL_PATHS: dict[str, tuple[list[dict], str]] = {
    "text_left": ([{"type": "text", "content": "Yarrow", **BOLD}], "left"),
    "text_right": ([{"type": "text", "content": "Yarrow", "align": "right", **BOLD}], "right"),
    "text_budget_left": (
        [{"type": "text", "content": "Yarrow", "budget": "SUPPLIER_NAME", **BOLD}],
        "left",
    ),
    "pair_inline": ([{"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}", **BOLD}], "left"),
    "pair_right_label": (
        [{"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}", "value_align": "right", **BOLD}],
        "left",
    ),
    "pair_right_value": (
        [{"type": "pair", "label": "Total", "value": "Yx", "value_align": "right", **BOLD}],
        "right",
    ),
    "pair_budget_label": (
        [{"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}", "budget": "SUPPLIER_NAME", **BOLD}],
        "left",
    ),
}


def _layout() -> dict:
    layout = copy.deepcopy(load_layout_registry(BANK)[LAYOUT_ID])
    layout["field_providers"] = []
    layout["distractor_items"] = []
    return layout


def _ink(image: Image.Image) -> tuple[int, int, int, int]:
    box = Image.eval(image.convert("L"), lambda v: 255 - v).getbbox()
    assert box is not None
    return box


def _render(blocks: list[dict]) -> Image.Image:
    layout = _layout()
    image = Image.new("RGB", (WIDTH, 300), "white")
    ctx = RenderContext(
        draw=ImageDraw.Draw(image),
        entry=ENTRY,
        layout=layout,
        layout_id=LAYOUT_ID,
        layout_path=str(BANK),
        region=Region(x=MARGIN, width=WIDTH - 2 * MARGIN),
        render_children=render_blocks,
    )
    render_blocks(blocks, ctx, 20)
    return image


def _crosses(image: Image.Image, edge: str) -> bool:
    left, _, right, _ = _ink(image)
    return left < MARGIN if edge == "left" else right > WIDTH - MARGIN


def _uncompensated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draw at the pen position on every path, as before the fix."""
    for module in (common, primitives_text):
        monkeypatch.setattr(module, "left_draw_x", lambda font, text, x: x)
        monkeypatch.setattr(
            module,
            "right_draw_x",
            lambda font, text, x_right: x_right - int(font.getbbox(text)[2] - font.getbbox(text)[0]),
        )


@pytest.mark.parametrize("path", sorted(DSL_PATHS))
def test_every_dsl_text_path_keeps_the_overhang_inside(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    blocks, edge = DSL_PATHS[path]
    assert not _crosses(_render(blocks), edge), path
    if edge == "left":
        _uncompensated(monkeypatch)
        assert _crosses(_render(blocks), edge), f"{path}: the check cannot fail"


def _banner_ink(image: Image.Image) -> tuple[int, int, int, int]:
    white = Image.eval(image.convert("L").crop((0, 0, WIDTH, 100)), lambda v: 255 if v > 0 else 0)
    box = white.getbbox()
    assert box is not None
    return box


def test_the_banner_keeps_the_overhang_inside(monkeypatch: pytest.MonkeyPatch) -> None:
    block = {
        "type": "banner",
        "content": "Yarrow",
        "height": 100,
        "color": "#000000",
        "text_y": 30,
        "text_color": "white",
        **BOLD,
    }
    assert _banner_ink(_render([block]))[0] >= MARGIN
    _uncompensated(monkeypatch)
    assert _banner_ink(_render([block]))[0] < MARGIN


def _common_paths() -> dict[str, tuple[Image.Image, str]]:
    font = load_font(28, family="carlito", bold=True)
    out: dict[str, tuple[Image.Image, str]] = {}
    for name in ("left", "right", "line_item"):
        image = Image.new("RGB", (WIDTH, 120), "white")
        draw = ImageDraw.Draw(image)
        if name == "left":
            draw_text_left(draw, "Yarrow", MARGIN, 20, font)
            out[name] = (image, "left")
        elif name == "right":
            draw_text_right(draw, "Yx", WIDTH - MARGIN, 20, font)
            out[name] = (image, "right")
        else:
            draw_line_item(draw, "Yield", "12.00", 20, font, MARGIN, WIDTH)
            out[name] = (image, "both")
    return out


def test_the_common_draw_helpers_keep_the_overhang_inside(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, (image, edge) in _common_paths().items():
        left, _, right, _ = _ink(image)
        assert left >= MARGIN and right <= WIDTH - MARGIN, (name, left, right)
    _uncompensated(monkeypatch)
    crossed = {name for name, (image, _) in _common_paths().items() if _ink(image)[0] < MARGIN}
    assert crossed == {"left", "line_item"}
