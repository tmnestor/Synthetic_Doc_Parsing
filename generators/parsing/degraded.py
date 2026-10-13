"""Carry a receipt's OmniDocBench page through the camera warp, and measure what no homography models.

A degraded receipt is its distractor render photographed on a desk (`degrade_receipt`). The warp is one
homography, so a block's box on the flat page maps to a true quadrilateral on the frame: `degraded_page`
builds the clean page from the render's records and pushes every `poly` through the homography the warp
applied, on a page the frame's size. Text, tables and reading order are scored from markdown and need no
transform.

Augraphy's ink and paper phases run on the flat page before the warp. A fold displaces pixels locally,
which no homography models; `fold_displacement_px` measures how far, on a page of fiducial dots, so a
tier whose fold moves ink further than `fold_displacement_max_px` is kept out of `layout_scored_tiers`
(Decision 11).
"""

import math

import numpy as np
from PIL import Image, ImageDraw

from generators.degradation import Tier, degrade_receipt_mapped
from generators.degradation.augment import apply_augraphy
from generators.degradation.camera import homography_transform
from generators.parsing.config import ParsingConfig
from generators.parsing.omnidocbench import PageContext, build_page, map_polys

# The eval-set half a degraded page belongs to.
DEGRADED_HALF = "degraded"
# The fiducial page: a black square dot of FOLD_DOT_PX pixels every FOLD_SPACING_PX pixels, each found
# again within a FOLD_WINDOW_PX square window centred where it was drawn.
FOLD_DOT_PX = 5
FOLD_SPACING_PX = 40
FOLD_WINDOW_PX = 20
# A window whose darkest pixel is within this many grey levels of its lightest holds no dot to locate.
_DOT_CONTRAST = 48


def degraded_page(
    image: Image.Image,
    geometry: dict,
    *,
    tier: Tier,
    seed: int,
    image_path: str,
    doc_type: str,
    layout_id: str,
    config: ParsingConfig,
) -> tuple[Image.Image, dict]:
    """Degrade one render and write its OmniDocBench page on the photographed frame.

    Args:
        image: The render photographed (the receipt's distractor render).
        geometry: That render's `geometry_out`.
        tier: The severity tier applied.
        seed: The (case, tier) seed, as `tier_seed` derives it.
        image_path: The degraded image's file name.
        doc_type: The document type drawn.
        layout_id: The layout drawn.
        config: The validated parsing configuration.

    Returns:
        The degraded frame, and its page: the clean page's blocks with every `poly` mapped onto the frame,
        `page_info` the frame's size, `half` degraded, `tier` the tier's name and `layout_scored` whether
        the tier is listed in `layout_scored_tiers`.

    Raises:
        ValueError: A block that drew ink has no category (as `build_page` raises).
        AugmentationError: The tier names an unregistered augmentation.
    """
    frame, m = degrade_receipt_mapped(image, tier, seed)
    scored = tier.name in config.layout_scored_tiers
    context = PageContext(image_path, doc_type, layout_id, DEGRADED_HALF, tier.name, scored)
    page = build_page(geometry, context=context, config=config)
    return frame, map_polys(page, homography_transform(m), width=frame.width, height=frame.height)


def _dots(size: tuple[int, int]) -> list[tuple[int, int]]:
    """Every dot centre on a page of `size`, each with its whole window on the page."""
    width, height = size
    half = FOLD_WINDOW_PX // 2
    first = FOLD_SPACING_PX // 2
    xs = range(first, width - half + 1, FOLD_SPACING_PX)
    ys = range(first, height - half + 1, FOLD_SPACING_PX)
    return [(x, y) for y in ys for x in xs]


def fold_displacement_px(tier: Tier, seeds: range, *, size: tuple[int, int]) -> float:
    """The worst distance a tier's Augraphy phases move ink, over a page of fiducial dots.

    A white page of `size` carries a black dot of `FOLD_DOT_PX` pixels every `FOLD_SPACING_PX` pixels.
    For each seed, only the tier's ink and paper phases are applied (`apply_augraphy`); the camera warp is
    a homography and is mapped exactly. Each dot is found again as the centroid of the darker half of the
    grey range within a `FOLD_WINDOW_PX` window centred where it was drawn, so a lighting gradient across
    the window does not pull it. A dot no longer found in its window (moved out, or buried under a fold's
    dark wedge) counts as moved half a window, the least it can have moved to be lost.

    Args:
        tier: The tier whose phases are measured.
        seeds: The seeds applied, one augmented page each.
        size: The page's (width, height) in pixels.

    Returns:
        The largest distance, in pixels, of any dot from where it was drawn, over every seed.

    Raises:
        AugmentationError: The tier names an unregistered augmentation.
    """
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    reach = FOLD_DOT_PX // 2
    dots = _dots(size)
    for x, y in dots:
        draw.rectangle([x - reach, y - reach, x + reach, y + reach], fill="black")
    half = FOLD_WINDOW_PX // 2
    worst = 0.0
    for seed in seeds:
        grey = np.asarray(apply_augraphy(page, tier, seed).convert("L"), dtype=np.float64)
        for x, y in dots:
            window = grey[y - half : y + half, x - half : x + half]
            low, high = float(window.min()), float(window.max())
            if high - low < _DOT_CONTRAST:
                worst = max(worst, float(half))
                continue
            rows, cols = np.nonzero(window < (low + high) / 2)
            dx = float(cols.mean()) - half
            dy = float(rows.mean()) - half
            worst = max(worst, math.hypot(dx, dy))
    return worst
