"""The distractor checks on a typeset page: density and overlay placement, on Typst's geometry (Ruling P2).

Stage one's checks run here unchanged in their logic; only what they are handed is the typeset page's:

- Density (`DensityCheck`, bounds in config/distractors.yml) reads `geometry["ink"]` and
  `geometry["height"]`, the read-back's lines (each with the role stage one drew it with) and the
  overlay ink moved to where Typst placed each site.
- Overlay placement (`generators/overlay/verify.py`) holds every overlay block, in draw order, to the
  page inset (`check_inset`), to the padded boxes of the ink its document type protects (`check_clear`,
  the protected boxes built by `protected_boxes` from the typeset ink, with the typeset field boxes
  naming each scored one), and every highlighter band to the contrast threshold (`check_contrast`), on
  the typeset PNG before and after the overlays. A block's mask is its image's alpha, placed on a
  page-sized canvas where Typst placed the block, covering every pixel the image touches.
"""

from pathlib import Path

from PIL import Image, ImageChops

from generators.distractors.config import load_distractor_config
from generators.distractors.paths import REPO_ROOT
from generators.exporters.geometry import InkRecord
from generators.overlay.verify import (
    Box,
    OverlayError,
    OverlaySite,
    check_clear,
    check_contrast,
    check_inset,
    protected_boxes,
)
from generators.parsing.omnidocbench import OVERLAY_PREFIX
from generators.typst.compile import compile_page, rasterise
from generators.typst.config import TypesetConfig
from generators.typst.overlays import PlacedOverlay
from generators.typst.readback import pixel_ceil, pixel_floor
from generators.typst.typeset import TypesetResult


def _placed(
    mask: Image.Image, origin: tuple[float, float], factor: float, size: tuple[int, int]
) -> Image.Image:
    """Place a site's mask (mode `L`) on a page-sized canvas at `origin`, scaled by `factor`.

    An image at a fractional position is placed at both whole-pixel roundings of it and the results
    combined, so the canvas covers every pixel the placed image touches; one on a whole pixel (every sheet's
    overlay, as the translator keeps stage one's pixel grid) is placed there alone.
    """
    if factor != 1:
        mask = mask.resize((max(1, round(mask.width * factor)), max(1, round(mask.height * factor))))
    canvas = Image.new("L", size, 0)
    for x in {pixel_floor(origin[0]), pixel_ceil(origin[0])}:
        for y in {pixel_floor(origin[1]), pixel_ceil(origin[1])}:
            layer = Image.new("L", size, 0)
            layer.paste(mask, (x, y))
            canvas = ImageChops.lighter(canvas, layer)
    return canvas


def _band(band: Box, overlay: PlacedOverlay, origin: tuple[float, float], factor: float) -> Box:
    """Return a tint band, in stage-one pixels, where it lies on the typeset PNG, rounded outward."""
    left, top = overlay.site[:2]
    return (
        pixel_floor(origin[0] + (band[0] - left) * factor),
        pixel_floor(origin[1] + (band[1] - top) * factor),
        pixel_ceil(origin[0] + (band[2] - left) * factor),
        pixel_ceil(origin[1] + (band[3] - top) * factor),
    )


def _field_pixels(geometry: dict) -> dict[str, Box]:
    """The page's scored field boxes back in pixels, as the overlay engine hands them to its checks."""
    width, height = int(geometry["width"]), int(geometry["height"])
    return {
        field: (round(left * width), round(top * height), round(right * width), round(bottom * height))
        for field, (left, top, right, bottom) in geometry["boxes"].items()
    }


def overlay_errors(
    result: TypesetResult,
    *,
    case_id: str,
    layout_id: str,
    layout_path: Path,
    doc_type: str,
    config: TypesetConfig,
    workdir: Path,
) -> list[str]:
    """Run stage one's overlay placement checks on a typeset page's overlays.

    Args:
        result: The typeset page.
        case_id: The case typeset.
        layout_id: Its layout.
        layout_path: The layout YAML, named in a refusal.
        doc_type: The extraction-schema document type, as `overlay.protect` keys it.
        config: The typesetting configuration (its raster resolution).
        workdir: A directory for the page compiled without its overlays, when a contrast check needs it.

    Returns:
        One four-part refusal per overlay block that fails a check (the first check it fails); empty when
        every block passes or the page has none.
    """
    overlays = result.translation.overlays
    if not overlays:
        return []
    settings = load_distractor_config().overlay
    policy = settings.protect[doc_type]
    geometry = result.geometry
    size = (int(geometry["width"]), int(geometry["height"]))
    factor = result.translation.scale * config.raster_dpi / 72
    blocks = {block.id: block for block in geometry["blocks"]}
    sites = {overlay.block for overlay in overlays}
    ink: list[InkRecord] = [record for record in geometry["ink"] if record.block not in sites]
    fields = _field_pixels(geometry)
    before: Image.Image | None = None
    path = layout_path if layout_path.is_absolute() else REPO_ROOT / layout_path
    errors: list[str] = []
    for overlay in overlays:
        site = OverlaySite(
            case_id=case_id,
            layout_id=layout_id,
            layout_path=path,
            block_path=overlay.path,
            block_type=blocks[overlay.block].kind.removeprefix(OVERLAY_PREFIX),
            doc_type=doc_type,
        )
        origin = geometry["measured"].boxes[overlay.block][:2]
        protected = tuple(protected_boxes(ink, policy, settings.clearance_px, fields=fields))
        try:
            if overlay.under is not None:
                mask = _placed(overlay.under.getchannel("A"), origin, factor, size)
                extent = mask.getbbox()
                if extent is not None:
                    check_inset(extent, size, settings.page_inset_px, site=site)
                    if before is None:
                        bare = compile_page(result.translation.bare_source, workdir=workdir, config=config)
                        before = rasterise(bare.pdf, dpi=config.raster_dpi)
                    bands = [_band(band, overlay, origin, factor) for band in overlay.bands]
                    check_contrast(before, result.image, bands, ink, settings.contrast, site=site)
            if overlay.over is not None:
                alpha = _placed(overlay.over.getchannel("A"), origin, factor, size)
                extent = alpha.getbbox()
                if extent is not None:
                    check_inset(extent, size, settings.page_inset_px, site=site)
                    check_clear(
                        alpha,
                        protected,
                        site=site,
                        mark=extent,
                        clearance=settings.clearance_px,
                        policy=policy,
                    )
        except OverlayError as err:
            errors.append(str(err))
        # A later block is held clear of this one's ink too, as stage one's engine holds it.
        ink.extend(record for record in geometry["ink"] if record.block == overlay.block)
    return errors
