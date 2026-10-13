"""The overlay layer: marks drawn over a finished distractor page (spec section 4).

A layout's `overlay:` list runs after `render_body`, on the flat page, before anything else touches it
(degradation, export). Each block whose `when:` key the render selected is drawn in order by its
registered primitive (`OVERLAY_DRAWERS`), composited, verified and recorded:

1. A tint (highlighter) is multiplied over the page once, and every ink box it meets is held to the
   contrast threshold (`check_contrast`).
2. An opaque layer (stamp, ink, handwriting) is alpha-composited after `check_inset` and `check_clear`
   prove no pixel of it lies within the page inset or inside a padded protected box.
3. The block's text ink (with its string, size and weight) and mark ink are recorded on the page's
   `BoxRecorder` as distractor ink, before the renderer fills `geometry_out` and `ink_out`, so density
   is measured on the composited page.

A plain render never calls `apply_overlay`, so plain pages are unchanged. Every renderer calls it on a
distractor render; a document type whose `overlay.protect` policy is `no_overlay` (receipts, in
config/distractors.yml) draws nothing, and a layout of that type that lists a block is refused.

Importing this module registers every overlay primitive (`stamp`, `highlight`, `ink_marks`,
`handwriting`) in `OVERLAY_DRAWERS`.

What is drawn comes from catalogue values on the distractor seeds; how it is drawn (placement order,
colour, rotation, jitter) comes from each block's own overlay seed (`overlay_seed`), so adding a block
moves no existing value or selection and no other block's draws.
"""

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

import generators.overlay.annotation  # noqa: F401  registers the highlight and ink_marks primitives
import generators.overlay.handwriting  # noqa: F401  registers the handwriting primitive
import generators.overlay.stamp  # noqa: F401  registers the stamp primitive
from generators.distractors.config import NO_OVERLAY, load_distractor_config
from generators.distractors.paths import DISTRACTORS_PATH, REPO_ROOT
from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.binding import is_present
from generators.overlay.registry import (
    OVERLAY_DRAWERS,
    OverlayContext,
    OverlayDrawing,
    OverlayMark,
)
from generators.overlay.verify import (
    OverlayError,
    OverlaySite,
    check_clear,
    check_contrast,
    check_inset,
    protected_boxes,
)

__all__ = [
    "OVERLAY_DRAWERS",
    "OverlayError",
    "OverlayMark",
    "OverlayResult",
    "apply_overlay",
    "overlay_seed",
]


@dataclass(frozen=True)
class OverlayResult:
    """Every mark an overlay drew, in draw order.

    Attributes:
        marks: One `OverlayMark` per drawn mark.
    """

    marks: tuple[OverlayMark, ...]

    def drawn_for(self, key: str) -> list[OverlayMark]:
        """Return the marks drawn for one `DX_` key.

        Args:
            key: A block's `when:` key.

        Returns:
            Its marks, in draw order; empty when the key drew nothing.
        """
        return [mark for mark in self.marks if mark.key == key]


def overlay_seed(case_id: str, doc_type: str, block_index: int) -> int:
    """Return the seed for how one overlay block is drawn on one document (Decision 8).

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `invoice`.
        block_index: The block's position in the layout's `overlay:` list.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:overlay:{doc_type}:{block_index}")`, big-endian.
    """
    text = f"{case_id}:overlay:{doc_type}:{block_index}"
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def _absolute(layout_path: str | Path) -> Path:
    path = Path(layout_path)
    return path if path.is_absolute() else REPO_ROOT / path


def _overlay_blocks(layout: dict, *, layout_id: str, layout_path: Path) -> list:
    blocks = layout.get("overlay")
    if isinstance(blocks, list):
        return blocks
    raise OverlayError(
        "Layout has no overlay list.\n"
        f"  What:     layout '{layout_id}' has overlay {blocks!r}, not a list of blocks.\n"
        f"  Where:    {layout_path} -> {layout_id}.overlay\n"
        "  Expected: overlay: [] or a list of blocks, each with type and when, e.g.\n"
        "              overlay: []\n"
        "  Recover:  add overlay: [] to the layout, then run `python -m generators.pipeline validate`."
    )


def _refuse_overlay(blocks: list, *, layout_id: str, layout_path: Path, doc_type: str) -> OverlayError:
    """The refusal for a layout of a `no_overlay` document type that lists overlay blocks."""
    return OverlayError(
        "Layout declares an overlay its document type does not carry.\n"
        f"  What:     layout '{layout_id}' lists {len(blocks)} overlay block(s), but overlay.protect."
        f"{doc_type} is {NO_OVERLAY}: a {doc_type} carries inline distractors only.\n"
        f"  Where:    {layout_path} -> {layout_id}.overlay; {DISTRACTORS_PATH} -> overlay.protect.{doc_type}\n"
        f"  Expected: overlay: [] on every {doc_type} layout, e.g. overlay: [].\n"
        f"  Recover:  set {layout_id}.overlay to [] and draw the marks as inline blocks in its body."
    )


def apply_overlay(
    image: Image.Image,
    layout: dict,
    entry: dict,
    *,
    layout_id: str,
    layout_path: str | Path,
    doc_type: str,
    recorder: BoxRecorder,
) -> OverlayResult:
    """Draw a layout's overlay blocks onto a distractor render, verifying and recording each.

    Args:
        image: The flat RGB page, drawn on in place.
        layout: The resolved layout dict, carrying `overlay`.
        entry: The provider-enriched entry (`render_body`'s `entry_out`), carrying `case_id`.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML (made absolute for diagnostics).
        doc_type: The extraction-schema document type, as `overlay.protect` keys it.
        recorder: The page's recorder; its ink defines what is protected and receives the overlay's ink.

    Returns:
        Every mark drawn.

    Raises:
        OverlayError: The layout has no overlay list, or lists blocks for a `no_overlay` document type;
            the entry has no case_id; a block names an unregistered type; a drawer returns a layer or
            mask that is not page-sized in the right mode; or a mark meets a protected box, leaves the
            page inset, or leaves highlighted text below contrast.
    """
    path = _absolute(layout_path)
    blocks = _overlay_blocks(layout, layout_id=layout_id, layout_path=path)
    settings = load_distractor_config().overlay
    policy = settings.protect[doc_type]
    if blocks and policy == NO_OVERLAY:
        raise _refuse_overlay(blocks, layout_id=layout_id, layout_path=path, doc_type=doc_type)
    if not blocks:
        return OverlayResult(marks=())
    case_id = entry.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise OverlayError(
            "Overlay cannot be seeded.\n"
            f"  What:     the entry rendered on layout '{layout_id}' has case_id {case_id!r}, so its "
            "overlay seed, sha256('{case_id}:overlay:{doc_type}:{block_index}'), cannot be derived.\n"
            f"  Where:    {path} -> {layout_id}.overlay; the entry handed to the {doc_type} renderer\n"
            "  Expected: an entry carrying a non-empty case_id, e.g. {'case_id': 'CASE001', 'layout': ..., "
            "'fields': {...}}.\n"
            "  Recover:  set case_id on the entry before rendering it (the pipeline and the snapshot "
            "tools do)."
        )
    marks: list[OverlayMark] = []
    for index, block in enumerate(blocks):
        when = block.get("when")
        if not isinstance(when, str) or not is_present(entry["fields"], when):
            continue
        kind = str(block.get("type"))
        site = OverlaySite(
            case_id=case_id,
            layout_id=layout_id,
            layout_path=path,
            block_path=f"{layout_id}.overlay[{index}]",
            block_type=kind,
            doc_type=doc_type,
        )
        drawer = OVERLAY_DRAWERS.get(kind)
        if drawer is None:
            raise OverlayError(
                "Overlay block cannot be drawn.\n"
                f"  What:     {site.describe()}: no overlay primitive is registered for type '{kind}'.\n"
                f"  Where:    {path} -> {site.block_path}.type\n"
                f"  Expected: one of {sorted(OVERLAY_DRAWERS)}.\n"
                "  Recover:  use a registered overlay type, or register a drawer in OVERLAY_DRAWERS "
                "(generators/overlay/registry.py)."
            )
        ink = tuple(recorder.ink_records())
        fields = _field_boxes(recorder)
        ctx = OverlayContext(
            page=image,
            entry=entry,
            layout=layout,
            site=site,
            settings=settings,
            policy=policy,
            protected=tuple(protected_boxes(ink, policy, settings.clearance_px, fields=fields)),
            ink=ink,
            rng=random.Random(overlay_seed(case_id, doc_type, index)),
            fields=fields,
        )
        drawing = drawer(block, ctx)
        _composite(image, drawing, ctx)
        # Each drawn site is one block, after every body block in draw order.
        with recorder.block_scope(
            f"overlay:{kind}", path=site.block_path, anchor=None, parse_category=None, parse_order=0
        ):
            for line in drawing.text_ink:
                recorder.record_ink(line.box, "distractor", text=line.text, size=line.size, bold=line.bold)
            for box in drawing.mark_ink:
                recorder.record_ink(box, "distractor", text="", size=0, bold=False, mark=True)
        marks.extend(drawing.marks)
    return OverlayResult(marks=tuple(marks))


def _field_boxes(recorder: BoxRecorder) -> dict[str, tuple[int, int, int, int]]:
    """The recorder's scored field boxes back in pixels, so a refusal can name the field it protects."""
    return {
        field: (
            round(left * recorder.width),
            round(top * recorder.height),
            round(right * recorder.width),
            round(bottom * recorder.height),
        )
        for field, (left, top, right, bottom) in recorder.as_dict().items()
    }


def _check_shape(part: Image.Image, mode: str, what: str, image: Image.Image, ctx: OverlayContext) -> None:
    """Refuse a drawer's layer or tint mask that is not page-sized in the mode the engine composites."""
    if part.mode == mode and part.size == image.size:
        return
    site = ctx.site
    raise OverlayError(
        "Overlay drawing cannot be composited.\n"
        f"  What:     {site.describe()}: its drawer returned a {what} of mode {part.mode} and size "
        f"{part.size}, on a page of size {image.size}.\n"
        f"  Where:    {site.layout_path} -> {site.block_path}.type; the drawer registered for "
        f"'{site.block_type}' in OVERLAY_DRAWERS (generators/overlay/)\n"
        f"  Expected: a {what} of mode {mode} and size {image.size}, e.g. "
        f"Image.new('{mode}', ctx.page.size, 0).\n"
        f"  Recover:  build the {what} at ctx.page.size in mode {mode} in the '{site.block_type}' drawer."
    )


def _composite(image: Image.Image, drawing: OverlayDrawing, ctx: OverlayContext) -> None:
    """Apply a drawing's tint, then its opaque layer, verifying each before it lands."""
    if drawing.tint is not None:
        _check_shape(drawing.tint.mask, "L", "tint mask", image, ctx)
    if drawing.layer is not None:
        _check_shape(drawing.layer, "RGBA", "layer", image, ctx)
    settings = ctx.settings
    if drawing.tint is not None:
        tint = drawing.tint
        extent = tint.mask.getbbox()
        if extent is not None:
            check_inset(extent, image.size, settings.page_inset_px, site=ctx.site)
            before = image.copy()
            multiplied = ImageChops.multiply(image, Image.new("RGB", image.size, tint.colour))
            image.paste(Image.composite(multiplied, image, tint.mask))
            check_contrast(before, image, tint.bands, ctx.ink, settings.contrast, site=ctx.site)
    if drawing.layer is not None:
        alpha = drawing.layer.getchannel("A")
        extent = alpha.getbbox()
        if extent is not None:
            check_inset(extent, image.size, settings.page_inset_px, site=ctx.site)
            check_clear(
                alpha,
                ctx.protected,
                site=ctx.site,
                mark=extent,
                clearance=settings.clearance_px,
                policy=ctx.policy,
            )
            image.paste(Image.alpha_composite(image.convert("RGBA"), drawing.layer).convert("RGB"))
