"""The `stamp` overlay primitive: the PAID stamp a clerk puts on a settled invoice (spec sections 4 and 7).

A stamp block is gated on the stamp's word (`when: DX_PAID_STAMP`) and names it in `content`; its
`date_key` names the paying transaction's date, printed on a line under the word when that key is drawn,
or is the explicit `none`. Its `candidates` are the centres, as page fractions, where a clerk would put
the stamp: beside the title and invoice details, over the totals panel, or across the upper body.

How the stamp is drawn comes from the block's overlay seed, in this order: its colour, opacity, rotation
and speckle fraction from `overlay.stamp`, the speckle pattern, the strength and direction of its uneven
inking, the order the candidates are tried in, then each candidate's offset of up to `jitter_px` each
way. The word and date are set in the configured face inside a frame, rotated, and placed at the first
candidate whose rotated footprint (frame included) stays inside the page inset and clears every padded
protected box: at its jittered centre, or failing that at the candidate itself. With none legal, the
render fails naming every position tried and what blocked it. The knock-out is clumped and one-sided
(`knock_out`). The engine then composites the layer, checks its pixels again and records each text line
as distractor text ink.
"""

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from generators.common import load_font
from generators.distractors.config import StampSettings
from generators.distractors.paths import DISTRACTORS_PATH
from generators.layout_dsl.binding import interpolate, is_present
from generators.overlay.registry import (
    OVERLAY_DRAWERS,
    OverlayContext,
    OverlayDrawing,
    OverlayMark,
    TextInk,
)
from generators.overlay.verify import Box, OverlayError, ProtectedBox

STAMP_TYPE = "stamp"
# Every key a stamp block carries; each is required, and validation refuses any other.
STAMP_BLOCK_KEYS = ("type", "when", "content", "date_key", "candidates")
# The `date_key` value of a stamp that prints no date line.
NO_DATE = "none"
# A stamp marks a settled document: its item must be grouped under this (state, choice) of the
# document type's states in config/distractors.yml, so it never shares a page with an amount still owing.
PAID_STATE = ("payment", "paid")
# The date line prints a DD/MM/YYYY date; these stand in for every date when a block's size is proven.
DATE_SAMPLES = tuple(f"{d}{d}/{d}{d}/{d}{d}{d}{d}" for d in "0123456789")


@dataclass(frozen=True)
class StampArt:
    """A stamp's masks at its drawn rotation, all the same size.

    Attributes:
        ink: Ink coverage (mode `L`): the frame, the word and the date line.
        footprint: The filled frame and every ink pixel (mode `L`): what may not meet a protected box.
        lines: One coverage mask per text line, word first, for the text ink boxes.
    """

    ink: Image.Image
    footprint: Image.Image
    lines: tuple[Image.Image, ...]


def _texts(settings: StampSettings, word: str, date: str | None) -> list[tuple[str, int]]:
    lines = [(word, settings.font.size)]
    if date is not None:
        lines.append((date, settings.date_size))
    return lines


def _measure(settings: StampSettings, text: str, size: int) -> tuple[int, int, int, int]:
    font = load_font(size, family=settings.font.family, bold=settings.font.bold)
    left, top, right, bottom = font.getbbox(text)
    return int(left), int(top), int(right), int(bottom)


def tile_size(settings: StampSettings, word: str, date: str | None) -> tuple[int, int]:
    """Return the unrotated stamp's (width, height), frame included.

    Args:
        settings: `overlay.stamp`.
        word: The stamp's word.
        date: The date line, or None for a stamp without one.

    Returns:
        The frame's outer width and height in pixels.
    """
    boxes = [_measure(settings, text, size) for text, size in _texts(settings, word, date)]
    edge = settings.padding_px + settings.border_px
    inner_w = max(right - left for left, _, right, _ in boxes)
    inner_h = sum(bottom - top for _, top, _, bottom in boxes) + settings.padding_px * (len(boxes) - 1)
    return inner_w + 2 * edge, inner_h + 2 * edge


def rotated_extent(size: tuple[int, int], angles: tuple[float, float]) -> tuple[int, int]:
    """Return the largest axis-aligned (width, height) a box takes at any rotation in a range.

    Args:
        size: The unrotated (width, height).
        angles: (min, max) rotation in degrees.

    Returns:
        The widest width and the tallest height over the range, each rounded up, plus two pixels for
        the resampling fringe.
    """
    width, height = size
    low, high = angles
    critical = [low, high]
    for base in (math.degrees(math.atan2(height, width)), math.degrees(math.atan2(width, height))):
        critical += [angle for angle in (base, -base, 0.0) if low <= angle <= high]
    widest = tallest = 0.0
    for angle in critical:
        cos, sin = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
        widest = max(widest, width * cos + height * sin)
        tallest = max(tallest, width * sin + height * cos)
    return math.ceil(widest) + 2, math.ceil(tallest) + 2


def compose(settings: StampSettings, word: str, date: str | None, angle: float) -> StampArt:
    """Set a stamp's word and date in its frame, rotated counter-clockwise by `angle` degrees.

    Args:
        settings: `overlay.stamp`.
        word: The stamp's word.
        date: The date line, or None.
        angle: The rotation in degrees.

    Returns:
        The rotated masks.
    """
    width, height = tile_size(settings, word, date)
    edge = settings.padding_px + settings.border_px
    inner_w = width - 2 * edge
    frame = Image.new("L", (width, height), 0)
    ImageDraw.Draw(frame).rectangle((0, 0, width - 1, height - 1), outline=255, width=settings.border_px)
    filled = Image.new("L", (width, height), 255)
    lines: list[Image.Image] = []
    y = edge
    for text, size in _texts(settings, word, date):
        left, top, right, bottom = _measure(settings, text, size)
        line = Image.new("L", (width, height), 0)
        font = load_font(size, family=settings.font.family, bold=settings.font.bold)
        x = edge + (inner_w - (right - left)) // 2 - left
        ImageDraw.Draw(line).text((x, y - top), text, font=font, fill=255)
        lines.append(line)
        y += bottom - top + settings.padding_px

    def turn(mask: Image.Image) -> Image.Image:
        return mask.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=0)

    turned = [turn(line) for line in lines]
    ink = turn(frame)
    for line in turned:
        ink = ImageChops.lighter(ink, line)
    return StampArt(ink=ink, footprint=ImageChops.lighter(turn(filled), ink), lines=tuple(turned))


def _offset(box: tuple[int, int, int, int], left: int, top: int) -> Box:
    return (box[0] + left, box[1] + top, box[2] + left, box[3] + top)


def _blockers(
    footprint: np.ndarray, left: int, top: int, protected: tuple[ProtectedBox, ...]
) -> list[ProtectedBox]:
    """Every padded protected box any footprint pixel, placed at (left, top), falls inside."""
    height, width = footprint.shape
    met = []
    for entry in protected:
        p_left, p_top, p_right, p_bottom = entry.box
        x0, y0 = max(p_left - left, 0), max(p_top - top, 0)
        x1, y1 = min(p_right - left, width), min(p_bottom - top, height)
        if x1 > x0 and y1 > y0 and footprint[y0:y1, x0:x1].any():
            met.append(entry)
    return met


def knock_out(
    inked: np.ndarray, fraction: float, blob_px: int, gradient: float, tilt: float, seed: int
) -> np.ndarray:
    """Choose the ink a rubber stamp fails to leave: blotchy and gathered on one side.

    A smooth noise field (random values on a grid `blob_px` apart, interpolated bicubically) plus a ramp
    across the stamp at angle `tilt`, weighted by `gradient`, scores every pixel; the `fraction` of inked
    pixels scoring highest (rounded to a whole count) are knocked out. So the voids are clumps, not salt
    noise, and they gather where the stamp was pressed lightest.

    Args:
        inked: Which pixels of the stamp tile carry ink (rows by columns).
        fraction: The share of inked pixels to knock out.
        blob_px: The noise field's feature size in pixels.
        gradient: The ramp's weight against the noise (whose values lie in [0, 1]).
        tilt: The ramp's direction, in radians.
        seed: The pattern's seed.

    Returns:
        A boolean mask of the knocked-out pixels.
    """
    height, width = inked.shape
    count = int(inked.sum())
    if count == 0 or fraction <= 0:
        return np.zeros_like(inked)
    grid = np.random.default_rng(seed).random((height // blob_px + 2, width // blob_px + 2))
    field = Image.fromarray((grid * 255).astype(np.uint8), mode="L").resize(
        (width, height), resample=Image.Resampling.BICUBIC
    )
    noise = np.asarray(field, dtype=np.float64) / 255.0
    ys, xs = np.mgrid[0:height, 0:width]
    ramp = (xs - width / 2) * math.cos(tilt) + (ys - height / 2) * math.sin(tilt)
    span = float(np.abs(ramp).max()) or 1.0
    score = noise + gradient * (ramp / span + 1) / 2
    # Exactly round(fraction * count) pixels, the highest-scoring inked ones (ties broken by position).
    positions = np.flatnonzero(inked)
    chosen = positions[np.argsort(score.flat[positions], kind="stable")[count - round(fraction * count) :]]
    knocked = np.zeros(inked.size, dtype=bool)
    knocked[chosen] = True
    return knocked.reshape(inked.shape)


def _refuse(block: dict, ctx: OverlayContext, reasons: list[str]) -> OverlayError:
    site = ctx.site
    listing = "\n".join(f"              - {reason}" for reason in reasons)
    return OverlayError(
        "Overlay stamp cannot be placed.\n"
        f"  What:     {site.describe()}: none of its {len(block['candidates'])} candidate position(s), at "
        "its jittered centre or at the candidate itself, keeps the stamp's rotated footprint (frame "
        f"included) inside the page inset and clear of every protected box:\n{listing}\n"
        f"  Where:    {site.layout_path} -> {site.block_path}.candidates; settings in {DISTRACTORS_PATH} "
        "-> overlay.stamp and overlay.clearance_px\n"
        "  Expected: at least one candidate centre, as page fractions, where the stamp clears every scored "
        "box and code mark (padded by overlay.clearance_px) on every case, e.g. candidates: [{x: 0.72, "
        "y: 0.16}, {x: 0.45, y: 0.5}].\n"
        f"  Recover:  move or add candidates of {site.block_path} where a clerk stamps clear of the listed "
        "boxes (beside the title and details, over the totals panel, across the upper body), or make the "
        "stamp smaller in overlay.stamp (font.size, date_size, padding_px)."
    )


def draw_stamp(block: dict, ctx: OverlayContext) -> OverlayDrawing:
    """Draw a stamp block; see the module docstring.

    Args:
        block: The stamp block (`type`, `when`, `content`, `date_key`, `candidates`), already validated.
        ctx: The drawing context, its `rng` seeded on the block's overlay seed.

    Returns:
        The stamp on a page-sized RGBA layer, its footprint as the mark's box, and one text ink record
        (box, string, size and weight) per line.

    Raises:
        OverlayError: No candidate keeps the stamp inside the page inset and clear of every protected box.
    """
    settings = ctx.settings.stamp
    fields = ctx.entry["fields"]
    word = interpolate(str(block["content"]), fields)
    date_key = str(block["date_key"])
    date = str(fields[date_key]) if date_key != NO_DATE and is_present(fields, date_key) else None

    rng = ctx.rng
    colour = settings.colours[rng.randrange(len(settings.colours))]
    opacity = rng.uniform(*settings.opacity)
    angle = rng.uniform(*settings.rotation_deg)
    speckle = rng.uniform(*settings.speckle)
    speckle_seed = rng.getrandbits(64)
    gradient = rng.uniform(*settings.inking_gradient)
    tilt = rng.uniform(0, 2 * math.pi)
    order = list(range(len(block["candidates"])))
    rng.shuffle(order)
    # A clerk's hand lands near, not on, the spot: each candidate's centre moves by a drawn offset.
    nudges = {
        index: (rng.uniform(-1, 1) * settings.jitter_px, rng.uniform(-1, 1) * settings.jitter_px)
        for index in order
    }

    art = compose(settings, word, date, angle)
    footprint = np.asarray(art.footprint) > 0
    extent = art.footprint.getbbox()
    width, height = art.footprint.size
    page_w, page_h = ctx.page.size
    inset = ctx.settings.page_inset_px
    reasons: list[str] = []
    placed: tuple[int, int] | None = None
    for index in order:
        candidate = block["candidates"][index]
        exact = (float(candidate["x"]) * page_w, float(candidate["y"]) * page_h)
        nudge = nudges[index]
        # The jittered centre first; if it meets a protected box, the candidate itself, which is proven.
        for centre in (
            (round(exact[0] + nudge[0]), round(exact[1] + nudge[1])),
            (round(exact[0]), round(exact[1])),
        ):
            left, top = centre[0] - width // 2, centre[1] - height // 2
            label = f"candidates[{index}] {{x: {candidate['x']}, y: {candidate['y']}}} centred at {centre}"
            box = _offset(extent, left, top) if extent is not None else (left, top, left, top)
            problems = []
            if box[0] < inset or box[1] < inset or box[2] > page_w - inset or box[3] > page_h - inset:
                problems.append(
                    f"comes within overlay.page_inset_px ({inset}px) of the {page_w}x{page_h} page's edge"
                )
            met = _blockers(footprint, left, top, ctx.protected)
            if met:
                names = "; ".join(entry.describe() for entry in met)
                problems.append(f"meets {len(met)} protected box(es): {names}")
            if problems:
                reasons.append(f"{label}: its footprint {box} " + ", and ".join(problems))
                continue
            placed = (left, top)
            break
        if placed is not None:
            break
    if placed is None:
        raise _refuse(block, ctx, reasons)

    alpha = np.asarray(art.ink, dtype=np.float64) * opacity
    alpha[knock_out(alpha > 0, speckle, settings.speckle_blob_px, gradient, tilt, speckle_seed)] = 0
    tile = Image.new("RGBA", art.ink.size, (*colour, 0))
    tile.putalpha(Image.fromarray(np.rint(alpha).astype(np.uint8), mode="L"))
    layer = Image.new("RGBA", ctx.page.size, (0, 0, 0, 0))
    layer.paste(tile, placed)

    left, top = placed
    lines = tuple(
        TextInk(box=_offset(bbox, left, top), text=text, size=size, bold=settings.font.bold)
        for line, (text, size) in zip(art.lines, _texts(settings, word, date), strict=True)
        if (bbox := line.getbbox()) is not None
    )
    mark_box = _offset(extent, left, top) if extent is not None else (left, top, left, top)
    return OverlayDrawing(
        marks=(OverlayMark(key=str(block["when"]), kind=STAMP_TYPE, box=mark_box, opaque=True),),
        layer=layer,
        text_ink=lines,
    )


OVERLAY_DRAWERS[STAMP_TYPE] = draw_stamp
