"""The overlay primitive registry and the types a drawer receives and returns.

A leaf module: it imports nothing from the layout DSL, so `generators/layout_dsl/schema.py` can read the
registered block types without an import cycle through `generators.overlay.engine`.

An overlay drawer is called as `drawer(block, ctx)` and returns an `OverlayDrawing`. It never paints on
the page itself: it hands back an opaque RGBA layer, a tint, or both, and the engine composites them,
verifies the result and records the ink. Later primitives (`stamp`, `highlight`, `ink_marks`,
`handwriting`) register themselves in `OVERLAY_DRAWERS`.
"""

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from generators.distractors.config import OverlaySettings
from generators.exporters.geometry import InkRecord
from generators.overlay.verify import Box, OverlaySite, ProtectedBox


@dataclass(frozen=True)
class OverlayMark:
    """One mark an overlay block drew.

    Attributes:
        key: The block's `when:` key, the `DX_` key the mark draws.
        kind: What was drawn, e.g. `stamp`, `tint`, `tick`, `note`.
        box: The mark's pixel box (left, top, right, bottom), right and bottom exclusive.
        opaque: True for ink on an opaque layer, False for a translucent tint.
    """

    key: str
    kind: str
    box: Box
    opaque: bool


@dataclass(frozen=True)
class Tint:
    """A translucent multiply over the page, such as highlighter.

    Every band of one block is drawn into the one mask, so overlapping bands darken once.

    Attributes:
        mask: A page-sized mode `L` image; each value is that pixel's blend weight (0 leaves it alone).
        colour: The RGB colour the page is multiplied by where the mask is set.
        bands: The box of each band, for the contrast check.
    """

    mask: Image.Image
    colour: tuple[int, int, int]
    bands: tuple[Box, ...]


@dataclass(frozen=True)
class TextInk:
    """One handwritten or stamped text line an overlay drew, and what it says.

    Attributes:
        box: The line's pixel ink box (left, top, right, bottom), right and bottom exclusive.
        text: The string written.
        size: The font size it was written at, in points.
        bold: Whether it was written in the bold weight.
    """

    box: Box
    text: str
    size: int
    bold: bool


@dataclass(frozen=True)
class OverlayDrawing:
    """What a drawer hands back for the engine to composite, verify and record.

    Overlay ink is distractor ink (Decision 7): `text_ink` boxes count as distractor text in the density
    metrics, `mark_ink` boxes as distractor marks (outside the text denominators, as code marks are). A
    tint records nothing, being a wash over text already counted.

    Attributes:
        marks: Every mark drawn, for `OverlayResult`.
        layer: A page-sized RGBA layer alpha-composited onto the page, or None.
        tint: A multiply tint applied before the layer, or None.
        text_ink: Handwritten or stamped text lines, each with its box, string, size and weight.
        mark_ink: Pixel boxes of strokes that are not text (ticks, loops, underlines); they say nothing.
    """

    marks: tuple[OverlayMark, ...]
    layer: Image.Image | None = None
    tint: Tint | None = None
    text_ink: tuple[TextInk, ...] = ()
    mark_ink: tuple[Box, ...] = ()


@dataclass(frozen=True)
class OverlayContext:
    """Everything a drawer may read while it draws one block.

    Attributes:
        page: The page as composited so far. Read it; never draw on it.
        entry: The provider-enriched entry (ground truth plus every selected `DX_` value).
        layout: The resolved layout dict.
        site: Where the block draws, for diagnostics.
        settings: `overlay:` from config/distractors.yml.
        policy: The protection policy for this document type.
        protected: The padded boxes opaque ink may not touch, from the page's ink so far.
        ink: The page's ink records so far, overlay ink of earlier blocks included.
        rng: The block's own generator, seeded `sha256("{case_id}:overlay:{doc_type}:{block_index}")`.
        fields: The page's recorded scored field boxes in pixels, keyed as the recorder keys them
            (`TRANSACTION_AMOUNTS_PAID[3]`), for a drawer that places marks by the rows they annotate.
    """

    page: Image.Image
    entry: dict
    layout: dict
    site: OverlaySite
    settings: OverlaySettings
    policy: str
    protected: tuple[ProtectedBox, ...]
    ink: tuple[InkRecord, ...]
    rng: random.Random
    fields: dict[str, Box] = field(default_factory=dict)

    @property
    def layout_path(self) -> Path:
        """The absolute path of the layout YAML."""
        return self.site.layout_path

    def page_rng(self, purpose: str) -> random.Random:
        """A generator for a draw made once per page whichever block makes it (the pen, the hand).

        Seeded `sha256("{case_id}:overlay:{doc_type}:{purpose}")`, the block seed's domain with a word in
        place of the block index, so every block on a page draws the same value and no block's own
        draws move.

        Args:
            purpose: What the draw is for, e.g. `pen` or `hand`.

        Returns:
            A fresh generator; the same purpose on the same page always draws the same values.
        """
        text = f"{self.site.case_id}:overlay:{self.site.doc_type}:{purpose}"
        return random.Random(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big"))


OverlayDrawer = Callable[[dict, OverlayContext], OverlayDrawing]

# Block type -> drawer. Empty until the overlay primitives register themselves.
OVERLAY_DRAWERS: dict[str, OverlayDrawer] = {}
