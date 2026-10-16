"""Overlays as images (Decision 8): a drawing cut to its site, and the image Typst places.

Drawings here are made by hand: a layer and a tint mask with a few known pixels, so each cut can be read
pixel by pixel. Pages are typeset from the real layouts in tests/typst/test_layouts.py.
"""

from pathlib import Path

import pytest
from PIL import Image

from generators.overlay.registry import OverlayDrawing, OverlayMark, Tint
from generators.typst.compile import compile_page, rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from generators.typst.overlays import (
    drawing_site,
    overlay_images,
    place_overlays,
    png_bytes,
    save_overlays,
    typst_image,
)

CONFIG = load_typeset_config(TYPESET_CONFIG_PATH)
PAGE = (200, 100)
INK = (20, 30, 160, 255)
TINT = (250, 230, 90)


def _layer(*boxes: tuple[int, int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", PAGE, (0, 0, 0, 0))
    for box in boxes:
        layer.paste(INK, box)
    return layer


def _mask(*boxes: tuple[int, int, int, int], weight: int = 120) -> Image.Image:
    mask = Image.new("L", PAGE, 0)
    for box in boxes:
        mask.paste(weight, box)
    return mask


def _alpha(image: Image.Image, xy: tuple[int, int]) -> int:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    return int(pixel[3])


def _rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    return pixel


def _drawing(layer: Image.Image | None = None, mask: Image.Image | None = None) -> OverlayDrawing:
    tint = None if mask is None else Tint(mask=mask, colour=TINT, bands=((10, 10, 60, 20),))
    return OverlayDrawing(marks=(OverlayMark("DX_A", "tick", (0, 0, 1, 1), True),), layer=layer, tint=tint)


def test_a_site_is_the_extent_of_the_layers_ink_and_the_tints_mask_together() -> None:
    drawing = _drawing(layer=_layer((70, 40, 80, 50)), mask=_mask((10, 10, 60, 20)))
    assert drawing_site(drawing) == (10, 10, 80, 50)
    assert drawing_site(_drawing(layer=_layer((70, 40, 80, 50)))) == (70, 40, 80, 50)
    assert drawing_site(_drawing(mask=_mask((10, 10, 60, 20)))) == (10, 10, 60, 20)


def test_a_drawing_that_put_nothing_on_the_page_has_no_site_and_is_not_placed() -> None:
    empty = _drawing(layer=_layer(), mask=_mask())
    assert drawing_site(empty) is None
    assert place_overlays({"x.overlay[0]": empty}, {"x.overlay[0]": 7}) == []


def test_the_cut_images_are_the_sites_size_and_carry_stage_ones_pixels() -> None:
    drawing = _drawing(layer=_layer((70, 40, 80, 50)), mask=_mask((10, 10, 60, 20)))
    site = (10, 10, 80, 50)
    under, over = overlay_images(drawing, site_px=site)
    assert under is not None and over is not None
    assert under.size == over.size == (70, 40)
    assert under.mode == over.mode == "RGBA"
    # The tint: its colour, with stage one's mask as alpha; nothing outside the band.
    assert under.getpixel((0, 0)) == (*TINT, 120)
    assert _alpha(under, (60, 35)) == 0
    # The layer: stage one's ink where it drew, transparent elsewhere.
    assert over.getpixel((60, 30)) == INK
    assert _alpha(over, (0, 0)) == 0


def test_a_part_the_drawing_lacks_is_none() -> None:
    under, over = overlay_images(_drawing(layer=_layer((70, 40, 80, 50))), site_px=(70, 40, 80, 50))
    assert under is None and over is not None
    under, over = overlay_images(_drawing(mask=_mask((10, 10, 60, 20))), site_px=(10, 10, 60, 20))
    assert under is not None and over is None


def test_placing_keeps_draw_order_block_ids_and_the_tints_bands() -> None:
    first = _drawing(mask=_mask((10, 10, 60, 20)))
    second = _drawing(layer=_layer((70, 40, 80, 50)))
    placed = place_overlays(
        {"x.overlay[0]": first, "x.overlay[1]": second}, {"x.overlay[0]": 4, "x.overlay[1]": 5}
    )
    assert [(overlay.block, overlay.path) for overlay in placed] == [
        (4, "x.overlay[0]"),
        (5, "x.overlay[1]"),
    ]
    assert placed[0].bands == ((10, 10, 60, 20),) and placed[1].bands == ()


def test_png_bytes_are_the_same_for_the_same_image() -> None:
    image = _layer((70, 40, 80, 50))
    assert png_bytes(image) == png_bytes(image.copy())
    assert png_bytes(image)[:8] == b"\x89PNG\r\n\x1a\n"


def test_save_overlays_writes_each_part_as_a_png_named_by_block(tmp_path: Path) -> None:
    drawing = _drawing(layer=_layer((70, 40, 80, 50)), mask=_mask((10, 10, 60, 20)))
    save_overlays(place_overlays({"x.overlay[0]": drawing}, {"x.overlay[0]": 3}), tmp_path / "overlays")
    assert sorted(path.name for path in (tmp_path / "overlays").iterdir()) == [
        "overlay_3_over.png",
        "overlay_3_under.png",
    ]


@pytest.mark.parametrize("under_text", [True, False])
def test_typst_places_the_image_from_the_source_alone_and_text_over_a_background_tint_stays_readable(
    tmp_path: Path, under_text: bool
) -> None:
    # A solid tint 72 pt square (300 px at 300 dpi) placed in the page's background at (36pt, 36pt), with a
    # line of invented text over it; or the same image placed over the text.
    tint = Image.new("RGBA", (300, 300), (*TINT, 255))
    image = typst_image(tint, width="72pt", height="72pt")
    # The background is placed from the page's corner, the body's content from inside its 40 pt margin.
    background = f"place(top + left, dx: 36pt, dy: 36pt, {image})" if under_text else "[]"
    foreground = "" if under_text else f"#place(top + left, dx: -4pt, dy: -4pt, {image})\n"
    source = (
        f'#import "/{CONFIG.template}": *\n'
        f"#set page(width: 200pt, height: 200pt, margin: 40pt, background: {background})\n"
        "#show: typeset\n"
        '#block-record(id: 0, kind: "text", path: "body[0]", align: left, dsl-lines((dsl-line(font: '
        '"Liberation Sans", size: 20pt, bold: true, fill: black, ascent: 16pt, advance: 24pt, '
        '"Wombat"),)))\n'
        f"{foreground}"
    )
    compiled = compile_page(source, workdir=tmp_path, config=CONFIG)
    page = rasterise(compiled.pdf, dpi=CONFIG.raster_dpi)
    # The paper inside the square takes the tint; outside it, none.
    assert page.getpixel((round(100 * 300 / 72), round(100 * 300 / 72))) == TINT
    assert page.getpixel((round(150 * 300 / 72), round(150 * 300 / 72))) == (255, 255, 255)
    darkest = min(
        sum(_rgb(page, (x, y)))
        for x in range(round(40 * 300 / 72), round(100 * 300 / 72))
        for y in range(round(42 * 300 / 72), round(58 * 300 / 72))
    )
    # Beneath the text, the tint leaves the letters black; over it, an opaque image hides them.
    assert (darkest < 60) is under_text
