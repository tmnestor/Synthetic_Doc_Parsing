"""Overlays as images (the plan's Decision 8): stage one's marks, cut out and placed on the typeset page.

Handwriting, ink marks, highlights and the paid stamp are drawn by stage one's own overlay code: the
translator runs `apply_overlay` on its stage-one draw of the page, so every mark is drawn from the same
seeds, at the same place and with the same checks as on stage one's page, and keeps each block's
`OverlayDrawing` (`apply_overlay(drawings_out=...)`). A drawer hands back page-sized parts, an opaque
RGBA layer, a highlighter tint or both; `overlay_images` cuts each down to a transparent image the size
of the block's site, which the page then places at the site's position, at the page's scale:

- an opaque layer (a stamp, a pen mark, handwriting) over the body, as stage one composites it last;
- a tint beneath the body, as an RGBA image of the tint's colour whose alpha is stage one's mask, so the
  printed text lies over it and stays in the PDF's text layer. Typst has no blend modes, so the tint is
  not multiplied: over white paper and black ink the two are the same, and only ink that is neither
  (grey text, a shaded band) differs, multiply darkening it where this leaves it as printed.

Images reach Typst inside the page's own source (`image(bytes(...))`), as PNG bytes: the page compiles
from stdin rooted at the repository, which a work directory need not lie in, and a source that carries
its images compiles to the same bytes wherever it is compiled. Each image is also written to the page's
overlay directory as a PNG, for inspection.
"""

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from generators.overlay.registry import OverlayDrawing
from generators.overlay.verify import Box


@dataclass(frozen=True)
class PlacedOverlay:
    """One overlay block of a typeset page: stage one's drawing of it, cut to its site.

    Attributes:
        block: The block's record id (stage one's `BlockRecord.id`).
        path: The block's path, `<layout>.overlay[n]`.
        site: The site, in stage-one pixels (left, top, right, bottom), right and bottom exclusive: the
            extent of everything the block drew, its layer's ink and its tint together.
        under: The tint as a site-sized RGBA image (its colour, alpha the tint's mask), or None.
        over: The opaque layer as a site-sized RGBA image, or None.
        bands: The tint's band boxes in stage-one pixels, which the contrast check holds ink under.
    """

    block: int
    path: str
    site: Box
    under: Image.Image | None
    over: Image.Image | None
    bands: tuple[Box, ...]


def _union(boxes: list[Box]) -> Box:
    """Return the smallest box holding every one of `boxes`."""
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def drawing_site(drawing: OverlayDrawing) -> Box | None:
    """Return the extent of everything a drawing put on the page, or None when it drew nothing.

    Args:
        drawing: One block's drawing, as its drawer returned it.

    Returns:
        The union of the layer's ink (alpha above zero) and the tint's mask, in page pixels, right and
        bottom exclusive.
    """
    extents: list[Box] = []
    if drawing.layer is not None and (layer := drawing.layer.getchannel("A").getbbox()) is not None:
        extents.append(layer)
    if drawing.tint is not None and (tint := drawing.tint.mask.getbbox()) is not None:
        extents.append(tint)
    return _union(extents) if extents else None


def overlay_images(
    drawing: OverlayDrawing, *, site_px: Box
) -> tuple[Image.Image | None, Image.Image | None]:
    """Cut a block's drawing down to transparent images the size of its site.

    Args:
        drawing: One block's drawing, its layer and tint page-sized as the engine composites them.
        site_px: The site in page pixels (`drawing_site`), right and bottom exclusive.

    Returns:
        (under, over): the tint as an RGBA image of its colour with the mask as alpha, and the opaque
        layer, each `site_px`'s size; None for a part the drawing does not have.
    """
    under = over = None
    if drawing.tint is not None:
        mask = drawing.tint.mask.crop(site_px)
        under = Image.new("RGBA", mask.size, (*drawing.tint.colour, 255))
        under.putalpha(mask)
    if drawing.layer is not None:
        over = drawing.layer.crop(site_px)
    return under, over


def place_overlays(drawings: dict[str, OverlayDrawing], block_ids: dict[str, int]) -> list[PlacedOverlay]:
    """Cut each drawn overlay block to its site, in draw order.

    Args:
        drawings: Each drawn block's drawing, by block path, as `apply_overlay(drawings_out=...)` fills it.
        block_ids: Each block path's record id.

    Returns:
        One `PlacedOverlay` per block that drew anything, in draw order.
    """
    placed: list[PlacedOverlay] = []
    for path, drawing in drawings.items():
        site = drawing_site(drawing)
        if site is None:
            continue
        under, over = overlay_images(drawing, site_px=site)
        bands = drawing.tint.bands if drawing.tint is not None else ()
        placed.append(PlacedOverlay(block_ids[path], path, site, under, over, tuple(bands)))
    return placed


def png_bytes(image: Image.Image) -> bytes:
    """Return an image as PNG bytes; the same image always gives the same bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def typst_image(image: Image.Image, *, width: str, height: str) -> str:
    """Return a Typst `image` call carrying the image's PNG bytes, sized `width` by `height`.

    Args:
        image: The image.
        width: Its width on the page, as Typst writes a length.
        height: Its height on the page, as Typst writes a length.
    """
    data = ",".join(str(byte) for byte in png_bytes(image))
    return f'image(bytes(({data},)), format: "png", width: {width}, height: {height})'


def save_overlays(placed: list[PlacedOverlay], overlay_dir: Path) -> None:
    """Write each placed overlay's images to `overlay_dir` as PNGs named by block id, for inspection.

    Args:
        placed: The page's placed overlays.
        overlay_dir: The page's overlay directory; created when anything is written.
    """
    for overlay in placed:
        for part, image in (("under", overlay.under), ("over", overlay.over)):
            if image is not None:
                overlay_dir.mkdir(parents=True, exist_ok=True)
                (overlay_dir / f"overlay_{overlay.block}_{part}.png").write_bytes(png_bytes(image))
