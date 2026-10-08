"""Checks every composited overlay must pass: clearance, page inset and highlighted legibility.

The overlay draws on a finished page whose ink is already recorded (`BoxRecorder.record_ink`), so each
check works from those pixel-exact records rather than from normalised export geometry:

- `protected_boxes` pads the ink a document type protects (`overlay.protect`) by `overlay.clearance_px`.
- `check_clear` refuses any opaque overlay pixel (alpha above zero) inside a padded protected box. It
  tests the drawn stroke, not the mark's bounding box, so a loop may ring an amount it does not touch.
- `check_inset` refuses a mark nearer a page edge than `overlay.page_inset_px`.
- `contrast_ratio` and `check_contrast` hold every ink box a translucent tint meets to the WCAG
  contrast threshold in `overlay.contrast`: a scored box always, any other box only if it met the
  threshold before the tint (text already fainter than that is not made illegible by the overlay).

Each refusal is an `OverlayError` carrying What / Where / Expected / Recover lines that name the case,
the layout, the block path (`<layout>.overlay[n]`), the mark's box and every box it met.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

from generators.distractors.config import ContrastSettings
from generators.distractors.paths import DISTRACTORS_PATH
from generators.exporters.geometry import InkRecord

Box = tuple[int, int, int, int]


class OverlayError(RuntimeError):
    """Raised when an overlay block cannot be drawn legally, or is not declared as it must be."""


@dataclass(frozen=True)
class OverlaySite:
    """Where an overlay mark is being drawn, for diagnostics.

    Attributes:
        case_id: The case rendered.
        layout_id: Its layout.
        layout_path: The absolute path of the layout YAML.
        block_path: The block's dotted path, e.g. `tax_invoice_standard.overlay[0]`.
        block_type: The block's `type:`.
        doc_type: The extraction-schema document type.
    """

    case_id: str
    layout_id: str
    layout_path: Path
    block_path: str
    block_type: str
    doc_type: str

    def describe(self) -> str:
        """Return the case, layout and block named at the head of every refusal."""
        return f"{self.case_id} on layout '{self.layout_id}', {self.block_type} block {self.block_path}"


class ProtectedBox(NamedTuple):
    """One ink box overlay ink may not touch, padded by the clearance.

    Attributes:
        box: The padded pixel box (left, top, right, bottom), right and bottom exclusive.
        ink: The ink record it pads.
        field: The scored field the record draws (`TOTAL_AMOUNT`, `LINE_ITEM_PRICES[0]`), when known.
    """

    box: Box
    ink: InkRecord
    field: str | None = None

    def describe(self) -> str:
        """Return the record's role, its drawn box, its field and its padded box, as refusals list them."""
        kind = "code mark" if self.ink.mark else f"{self.ink.role} ink"
        named = f" [{self.field}]" if self.field else ""
        return f"{kind} {self.ink.box}{named} (padded {self.box})"


def _area(a: Box, b: Box) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _field_of(record: InkRecord, fields: dict[str, Box]) -> str | None:
    """The recorded scored field whose box overlaps a scored ink record most, or None."""
    if record.role != "scored" or not fields:
        return None
    name, box = max(fields.items(), key=lambda item: _area(record.box, item[1]))
    return name if _area(record.box, box) > 0 else None


def protected_boxes(
    ink: Sequence[InkRecord], policy: str, clearance: int, *, fields: dict[str, Box] | None = None
) -> list[ProtectedBox]:
    """Return the padded boxes opaque overlay ink may not touch under a protection policy.

    Args:
        ink: The page's ink records so far, in pixels.
        policy: `scored_and_marks` protects scored ink and every code mark; `all_ink` protects every
            record. (`no_overlay`, a type that carries no overlay, has nothing drawn to protect against.)
        clearance: Pixels added on every side of each protected record.
        fields: The page's recorded scored field boxes, in pixels, so a refusal can name the field a
            scored record draws; optional.

    Returns:
        The padded boxes, in the order their records were drawn.

    Raises:
        ValueError: If `policy` is `no_overlay` or not a known policy (the engine refuses an overlay on a
            `no_overlay` type before it gets here, and the catalogue loader admits no other policy).
    """
    if policy == "scored_and_marks":
        chosen = [record for record in ink if record.role == "scored" or record.mark]
    elif policy == "all_ink":
        chosen = list(ink)
    else:
        msg = (
            f"Overlay protection policy {policy!r} protects nothing an overlay may be drawn against. "
            f"Remediation: set overlay.protect.<type> in {DISTRACTORS_PATH} to scored_and_marks or "
            "all_ink for a type whose layouts draw an overlay."
        )
        raise ValueError(msg)
    return [
        ProtectedBox(
            (left - clearance, top - clearance, right + clearance, bottom + clearance),
            record,
            _field_of(record, fields or {}),
        )
        for record in chosen
        for left, top, right, bottom in [record.box]
    ]


def _refusal(site: OverlaySite, what: str, expected: str, recover: str) -> OverlayError:
    return OverlayError(
        "Overlay mark cannot be drawn.\n"
        f"  What:     {site.describe()}: {what}\n"
        f"  Where:    {site.layout_path} -> {site.block_path}; settings in {DISTRACTORS_PATH} -> overlay\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def check_clear(
    alpha: Image.Image,
    protected: Sequence[ProtectedBox],
    *,
    site: OverlaySite,
    mark: Box,
    clearance: int,
    policy: str,
) -> None:
    """Refuse an opaque overlay layer with any pixel inside a padded protected box.

    Args:
        alpha: The layer's alpha channel, page-sized, mode `L`; any value above zero is ink.
        protected: The padded boxes from `protected_boxes`.
        site: Where the mark is drawn.
        mark: The mark's box (its ink's bounding box), named in the refusal.
        clearance: The padding applied, named in the refusal.
        policy: The protection policy applied, named in the refusal.

    Raises:
        OverlayError: Naming the mark and every protected box it met.
    """
    pixels = np.asarray(alpha)
    height, width = pixels.shape
    met: list[ProtectedBox] = []
    for entry in protected:
        left, top, right, bottom = entry.box
        left, top = max(left, 0), max(top, 0)
        right, bottom = min(right, width), min(bottom, height)
        if right > left and bottom > top and pixels[top:bottom, left:right].any():
            met.append(entry)
    if not met:
        return
    listing = "\n".join(f"              - {entry.describe()}" for entry in met)
    raise _refusal(
        site,
        f"its ink at {mark} meets {len(met)} protected box(es):\n{listing}",
        f"no opaque overlay pixel inside any box the '{policy}' policy protects on a {site.doc_type}, each "
        f"padded by overlay.clearance_px ({clearance}px), e.g. overlay: {{clearance_px: {clearance}, "
        f"protect: {{{site.doc_type}: {policy}}}}}.",
        f"move the candidate positions or lanes of {site.block_path} away from the listed boxes, or revise "
        "overlay.protect or overlay.clearance_px in config/distractors.yml.",
    )


def check_inset(box: Box, page_size: tuple[int, int], inset: int, *, site: OverlaySite) -> None:
    """Refuse a mark nearer a page edge than the page inset.

    Args:
        box: The mark's box, right and bottom exclusive.
        page_size: The page's (width, height).
        inset: `overlay.page_inset_px`.
        site: Where the mark is drawn.

    Raises:
        OverlayError: Naming the mark's box and the inset rectangle it leaves.
    """
    width, height = page_size
    left, top, right, bottom = box
    allowed = (inset, inset, width - inset, height - inset)
    if left >= allowed[0] and top >= allowed[1] and right <= allowed[2] and bottom <= allowed[3]:
        return
    raise _refusal(
        site,
        f"its ink at {box} comes within overlay.page_inset_px ({inset}px) of the edge of the "
        f"{width}x{height} page.",
        f"every mark inside {allowed}, e.g. overlay: {{page_inset_px: {inset}}}.",
        f"move the candidate positions or lanes of {site.block_path} inward, or lower "
        "overlay.page_inset_px in config/distractors.yml.",
    )


def _luminance(image: Image.Image, box: Box) -> np.ndarray:
    """Return the WCAG relative luminance of every pixel inside `box`."""
    rgb = np.asarray(image.convert("RGB").crop(box), dtype=np.float64) / 255.0
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def contrast_ratio(image: Image.Image, box: Box, settings: ContrastSettings) -> float:
    """Return the WCAG contrast ratio of the ink inside a box.

    The paper's luminance is the box's `light_percentile`. The ink's is the `dark_percentile` of the
    box's ink pixels only: those nearer its darkest pixel than its paper (at or below their luminance
    midpoint). A percentile of the whole box would read paper, not ink, wherever ink covers fewer of its
    pixels than `dark_percentile` per cent (a one-glyph value such as '1', or a wide box with short
    text), and every such box would score a ratio near 1 under any tint. Highlighted paper stays above
    the midpoint while the ink is darker than it, so the tint is never read as ink.

    Args:
        image: The page.
        box: The ink box, right and bottom exclusive, with positive area.
        settings: `overlay.contrast`.

    Returns:
        (light + 0.05) / (dark + 0.05), from 1 (no contrast) to 21 (black on white).

    Raises:
        ValueError: If the box holds no pixels.
    """
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        msg = f"Ink box {box} holds no pixels, so it has no contrast. Remediation: pass a box with area."
        raise ValueError(msg)
    luminance = _luminance(image, box)
    light = float(np.percentile(luminance, settings.light_percentile))
    ink = luminance[luminance <= (light + float(luminance.min())) / 2]
    dark = float(np.percentile(ink, settings.dark_percentile))
    return (light + 0.05) / (dark + 0.05)


def _meets(box: Box, other: Box) -> bool:
    return box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]


def check_contrast(
    before: Image.Image,
    after: Image.Image,
    bands: Sequence[Box],
    ink: Sequence[InkRecord],
    settings: ContrastSettings,
    *,
    site: OverlaySite,
) -> None:
    """Refuse a tint that leaves any ink box it meets below the contrast threshold (Decision 6).

    A scored box under a band must meet `settings.min_ratio` after compositing. Any other box under a
    band must too if it met the ratio before; text already fainter than the threshold (a grey reference
    sub-line) is exempt, as the tint did not make it illegible. Boxes with no area hold no ink and are
    passed over.

    Args:
        before: The page before the tint.
        after: The page after the tint.
        bands: The tint's band boxes.
        ink: The page's ink records.
        settings: `overlay.contrast`.
        site: Where the tint is drawn.

    Raises:
        OverlayError: Naming every box below the threshold with its ratio before and after.
    """
    failures: list[str] = []
    for record in ink:
        left, top, right, bottom = record.box
        if right <= left or bottom <= top or not any(_meets(record.box, band) for band in bands):
            continue
        ratio_after = contrast_ratio(after, record.box, settings)
        if ratio_after >= settings.min_ratio:
            continue
        ratio_before = contrast_ratio(before, record.box, settings)
        if record.role != "scored" and ratio_before < settings.min_ratio:
            continue
        kind = "code mark" if record.mark else f"{record.role} ink"
        failures.append(f"{kind} {record.box}: contrast {ratio_before:.2f} before, {ratio_after:.2f} after")
    if not failures:
        return
    listing = "\n".join(f"              - {line}" for line in failures)
    raise _refusal(
        site,
        f"its tint over band(s) {list(bands)} leaves {len(failures)} box(es) below "
        f"overlay.contrast.min_ratio {settings.min_ratio}:\n{listing}",
        f"every scored box under a band, and every other box that met the ratio before, at a contrast of "
        f"at least {settings.min_ratio} after compositing, e.g. overlay: {{contrast: {{min_ratio: "
        f"{settings.min_ratio}, dark_percentile: {settings.dark_percentile:g}, light_percentile: "
        f"{settings.light_percentile:g}}}}}.",
        f"draw {site.block_path} in a lighter colour or at a lower opacity (its overlay settings in "
        "config/distractors.yml), or revise overlay.contrast.",
    )
