"""The `handwriting` overlay primitive: a taxpayer's margin notes and running total (Task 4).

A handwriting block is gated on the note's or the sum's key (`when: DX_MARGIN_NOTE`) and writes its
`content` (`"{DX_MARGIN_NOTE}"`, `"= {DX_ANNOTATION_TOTAL}"`) beside one of the rows `rows` names. The
statement's hand, one face and weight from `overlay.handwriting.hands`, is drawn once per page on its
`hand` page seed, so every note and sum on a statement is one person's writing; the line's size is
drawn per block from `size_px`, and each glyph is drawn on its own, its size, slant and baseline jittered
within `glyph_jitter`, so no two letters are alike.

Where it goes: the annotated rows are taken in an order shuffled on the block's overlay seed and, for
each, the block's `lanes` in a shuffled order (see generators/overlay/annotation.py for the lanes; a
row is found by its `description_field` and `column_field` boxes). The first box clear of every padded
protected box (every printed line and every earlier overlay mark, on a bank statement) and inside the
page inset is used. With none, the render fails naming every row and lane tried and what blocked each.
The note is drawn in the statement's pen on its own layer and records distractor text ink.
"""

import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from generators.common import load_font
from generators.distractors.config import GlyphJitter, Hand
from generators.layout_dsl.binding import interpolate
from generators.overlay.annotation import (
    ROWLESS_LANES,
    Occupancy,
    Row,
    lane_geometry,
    locate_rows,
    pen_colour,
    place,
    refuse,
    row_indices,
)
from generators.overlay.registry import (
    OVERLAY_DRAWERS,
    OverlayContext,
    OverlayDrawing,
    OverlayMark,
    TextInk,
)
from generators.overlay.verify import Box

HANDWRITING_TYPE = "handwriting"
# Every key a handwriting block carries; each is required, and validation refuses any other.
HANDWRITING_BLOCK_KEYS = ("type", "when", "content", "rows", "description_field", "column_field", "lanes")


def statement_hand(ctx: OverlayContext) -> Hand:
    """The one hand a statement is written in, drawn on its `hand` page seed."""
    hands = ctx.settings.handwriting.hands
    return hands[ctx.page_rng("hand").randrange(len(hands))]


@lru_cache(maxsize=None)
def font_size_for(hand: Hand, x_height: int) -> int:
    """The font size at which a hand's lower-case letters stand `x_height` pixels tall.

    The faces differ widely at one font size (at 40, Caveat's x is 16px tall and Kalam's 21px), and
    their figures differ more (Indie Flower's are no taller than its x), so a line's size is its
    x-height, which the letters of a note fill, and each face's font size is solved for it: every hand
    then writes at the same scale. The smallest size reaching the height is used.

    Args:
        hand: The face and weight.
        x_height: The height of an 'x' in pixels.

    Returns:
        The font size.
    """
    size = 1
    while True:
        top, bottom = load_font(size, family=hand.family, bold=hand.bold).getbbox("x")[1::2]
        if bottom - top >= x_height:
            return size
        size += 1


def write(text: str, hand: Hand, size: int, jitter: GlyphJitter, rng: random.Random) -> Image.Image:
    """Write a line glyph by glyph, each with its own size, slant and baseline drawn on `rng`.

    Args:
        text: The line.
        hand: The face and weight.
        size: The line's font size; each glyph's differs by up to `jitter.size_px`.
        jitter: The per-glyph variation.
        rng: The generator every draw comes from.

    Returns:
        The line's ink coverage (mode `L`), cropped to its ink.
    """
    room = size + jitter.size_px
    canvas = Image.new("L", (round(room * (len(text) + 2)), 4 * room), 0)
    baseline = 2 * room
    x = float(room)
    for char in text:
        glyph_size = size + rng.randint(-jitter.size_px, jitter.size_px)
        font = load_font(glyph_size, family=hand.family, bold=hand.bold)
        advance = font.getlength(char)
        slant = math.tan(math.radians(rng.uniform(-jitter.slant_deg, jitter.slant_deg)))
        lift = rng.randint(-jitter.baseline_px, jitter.baseline_px)
        if char.strip():
            tile = Image.new("L", (round(advance) + 2 * room, 3 * room), 0)
            ImageDraw.Draw(tile).text((room, 2 * room), char, font=font, anchor="ls", fill=255)
            # Shear about the glyph's own baseline: a positive slant leans its top to the right.
            tile = tile.transform(
                tile.size,
                Image.Transform.AFFINE,
                (1, slant, -slant * 2 * room, 0, 1, 0),
                resample=Image.Resampling.BICUBIC,
            )
            left, top = round(x) - room, baseline - 2 * room + lift
            region = canvas.crop((left, top, left + tile.width, top + tile.height))
            canvas.paste(ImageChops.lighter(region, tile), (left, top))
        x += advance
    bbox = canvas.getbbox()
    return canvas.crop(bbox) if bbox is not None else Image.new("L", (1, 1), 0)


def _step_away(
    box: Box,
    lane: str,
    height: int,
    occupancy: Occupancy,
    step_away: tuple[float, float],
    rng: random.Random,
) -> Box:
    """Move a placed note a little further from the print it was placed against, where that stays clear.

    `place` returns the first clear position, which sits exactly one clearance from the printed line
    before it; a person leaves a varying gap. Along the lane's own direction (rightward after a
    description or in the right margin, leftward in the left margin, downward under a column) the note
    moves by a `step_away` fraction of its own height (`overlay.handwriting.step_away`), drawn on `rng`,
    to the farthest of those positions still clear.
    """
    dx, dy = {"after_description": (1, 0), "right_margin": (1, 0), "left_margin": (-1, 0)}.get(lane, (0, 1))
    wanted = round(rng.uniform(*step_away) * height)
    for step in range(wanted, 0, -1):
        moved = (box[0] + dx * step, box[1] + dy * step, box[2] + dx * step, box[3] + dy * step)
        if occupancy.clear(moved):
            return moved
    return box


def draw_handwriting(block: dict, ctx: OverlayContext) -> OverlayDrawing:
    """Draw a handwriting block; see the module docstring.

    Args:
        block: The block (`type`, `when`, `content`, `rows`, `description_field`, `column_field`,
            `lanes`), already validated.
        ctx: The drawing context, its `rng` seeded on the block's overlay seed.

    Returns:
        The line on a page-sized RGBA layer, its ink box as distractor text.

    Raises:
        OverlayError: An annotated row is not on the page, or no row and lane has a clear box for the line.
    """
    settings = ctx.settings.handwriting
    rng = ctx.rng
    text = interpolate(str(block["content"]), ctx.entry["fields"])
    hand = statement_hand(ctx)
    size = font_size_for(hand, rng.randint(*settings.size_px))
    line = write(text, hand, size, settings.glyph_jitter, rng)
    description, column = str(block["description_field"]), str(block["column_field"])
    rows = locate_rows(
        ctx, row_indices(ctx, block, "rows"), [description, column], anchor=column, key=".column_field"
    )
    rng.shuffle(rows)
    occupancy = Occupancy(ctx)
    geometry = lane_geometry(ctx, description, column)
    tried: list[str] = []
    # A lane beside a row is tried for every row before a lane that belongs to none (below_column),
    # so a note sits by a row it annotates wherever one has room.
    row_lanes = [str(lane) for lane in block["lanes"] if lane not in ROWLESS_LANES]
    rowless = [str(lane) for lane in block["lanes"] if lane in ROWLESS_LANES]
    attempts: list[tuple[Row, str]] = []
    for row in rows:
        rng.shuffle(row_lanes)
        attempts += [(row, lane) for lane in row_lanes]
    attempts += [(rows[0], lane) for lane in rowless]
    # Written under a column, the line ends roughly under it, never flush with its edge as if typeset.
    below_dx = rng.randint(*settings.below_column_dx_px)
    for row, lane in attempts:
        box, why = place(line.size, lane, row, geometry, occupancy, ctx, below_dx=below_dx)
        if box is None:
            tried.append(f"row {row.index}, {lane}: {why}")
            continue
        box = _step_away(box, lane, line.height, occupancy, settings.step_away, rng)
        layer = Image.new("RGBA", ctx.page.size, (*pen_colour(ctx), 0))
        alpha = Image.new("L", ctx.page.size, 0)
        alpha.paste(line, box[:2])
        layer.putalpha(alpha)
        return OverlayDrawing(
            marks=(OverlayMark(key=str(block["when"]), kind=HANDWRITING_TYPE, box=box, opaque=True),),
            layer=layer,
            text_ink=(TextInk(box=box, text=text, size=size, bold=hand.bold),),
        )
    listing = "\n".join(f"              - {entry}" for entry in tried)
    raise refuse(
        ctx,
        "Handwritten note cannot be placed.",
        f"'{text}' in {hand.family}{' bold' if hand.bold else ''} at font size {size} ({line.width}x{line.height}px) "
        f"fits no lane of any annotated row:\n{listing}",
        ".lanes",
        "a lane with a clear box for the line beside at least one annotated row, e.g. lanes: "
        "after_description, left_margin, right_margin and below_column for a note, below_column for a sum.",
        f"add a lane to {ctx.site.block_path}.lanes, or write smaller in overlay.handwriting.size_px.",
    )


OVERLAY_DRAWERS[HANDWRITING_TYPE] = draw_handwriting
