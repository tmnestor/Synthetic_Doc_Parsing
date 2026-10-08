"""A taxpayer's marks on a bank statement: highlighter and pen (spec sections 4 and 7; Task 4).

Two overlay primitives, registered in `OVERLAY_DRAWERS` on import, plus what they share with the
`handwriting` primitive (generators/overlay/handwriting.py): finding a statement row on the page,
the lanes a mark or note may sit in, and the test that a mark is clear of every protected box.

Rows are found by their recorded scored boxes, never by counting table lines: row `i` of
`DX_ANNOTATED_ROWS` is a ground-truth row position, and the table may print an opening balance, date
group rows or credits between them. A row is the union of its named fields' boxes indexed `[i]`
(`TRANSACTION_AMOUNTS_PAID[3]`), widened to every printed ink box in its vertical band, so a band runs
from the date to the balance. A row with no recorded box for the field a block anchors on fails fast.

- `highlight` (gated on `DX_ANNOTATED_ROWS`): one band per annotated row, padded past the row's ink by
  `overlay.highlighter.pad_px`, its four corners leaning by up to `end_jitter_px`, its top and bottom
  edges wandering by up to `edge_jitter_px`, drawn supersampled, its ink uneven along its length and
  denser where the pen starts and stops (`density_wobble`, `end_density`, `end_length_px`), in a colour
  the block does not exclude (`exclude_colours`); every band of the statement is drawn into one mask
  (the stronger weight wins where two meet), so overlapping bands are multiplied once. The engine applies
  the tint and checks contrast. A tint records no ink.
- `ink_marks` (gated on `DX_ROW_MARKS`): per annotated row, the kind `DX_ROW_MARKS` gives it: a tick in
  the block's `tick_lane`, level with the row; a loop round the row's `target_field` value, clearing its
  padded box by `overlay.ink.circle.gap_px`; or an underline `underline.gap_px` below that padded box.
  Strokes are jittered polylines of varying width, drawn at four times the page resolution and reduced,
  so their edges are antialiased, in one pen colour per statement. Each records a distractor mark. A
  kind that cannot be placed clear of every padded protected box fails fast; it is never swapped for
  another kind (Decision 9).

Lanes (`LANES`) are where a mark or a note may sit: `left_margin` and `right_margin` (between the page
inset and the content box, level with the row), `after_description` (the gap between a row's description
ink and the next ink to its right, level with the row) and `below_column` (under a column, below the
table, its right edge a drawn offset from the column's: the first position, scanning down from the
table's last row, where the box is clear).
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from generators.distractors.config import CircleShape, HighlighterSettings, Taper, TickShape, UnderlineShape
from generators.distractors.paths import DISTRACTORS_PATH
from generators.exporters.geometry import InkRecord
from generators.layout_dsl.binding import is_present
from generators.overlay.registry import OVERLAY_DRAWERS, OverlayContext, OverlayDrawing, OverlayMark, Tint
from generators.overlay.verify import Box, OverlayError, ProtectedBox

HIGHLIGHT_TYPE = "highlight"
INK_MARKS_TYPE = "ink_marks"
# Every key each block carries; each is required, and validation refuses any other.
HIGHLIGHT_BLOCK_KEYS = ("type", "when", "rows", "row_fields", "exclude_colours")
INK_MARKS_BLOCK_KEYS = ("type", "when", "rows", "marks", "target_field", "kinds", "tick_lane")
# The marks `ink_marks` can draw; a block's `kinds` lists those it may be asked for.
MARK_KINDS = ("tick", "circle", "underline")
# Where a tick or a note may sit relative to its row (see the module docstring).
LANES = ("right_margin", "after_description", "left_margin", "below_column")
# The lanes a tick may use: a tick is placed by its row alone, and these need no field but the row's.
TICK_LANES = ("left_margin", "right_margin")
# The lanes that belong to no row: a note is tried there only once every row's own lanes are full.
ROWLESS_LANES = ("below_column",)
# Strokes are drawn at this multiple of the page resolution and reduced, which antialiases their edges.
# A rendering technique, not a tunable: it changes no mark's size or place.
SUPERSAMPLE = 4

Point = tuple[float, float]


def describe_boxes(boxes: Sequence[ProtectedBox]) -> str:
    """The boxes a refusal lists, one per line."""
    return "\n".join(f"              - {entry.describe()}" for entry in boxes)


def refuse(
    ctx: OverlayContext, title: str, what: str, key: str, expected: str, recover: str
) -> OverlayError:
    """A four-part overlay refusal naming the case, layout and block."""
    site = ctx.site
    return OverlayError(
        f"{title}\n"
        f"  What:     {site.describe()}: {what}\n"
        f"  Where:    {site.layout_path} -> {site.block_path}{key}; settings in {DISTRACTORS_PATH} -> overlay\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


# --- rows -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One annotated statement row as printed.

    Attributes:
        index: The ground-truth row position (`DX_ANNOTATED_ROWS` member).
        band: The row's extent: its fields' boxes, widened to every printed ink box in its vertical band.
        boxes: Field name -> that field's recorded box for this row, for the fields that were recorded.
    """

    index: int
    band: Box
    boxes: dict[str, Box]

    @property
    def centre_y(self) -> float:
        """The row's vertical centre."""
        return (self.band[1] + self.band[3]) / 2


def row_indices(ctx: OverlayContext, block: dict, param: str) -> list[int]:
    """The ground-truth row positions a block's `rows`-like key names, e.g. `0|4|9` -> [0, 4, 9].

    Raises:
        OverlayError: The key's value is absent or not a pipe list of whole numbers.
    """
    key = str(block[param])
    fields = ctx.entry["fields"]
    raw = str(fields[key]) if is_present(fields, key) else ""
    parts = raw.split("|") if raw else []
    if not parts or not all(part.isdigit() for part in parts):
        raise refuse(
            ctx,
            "Annotated rows cannot be read.",
            f"{param} {key} is {raw!r}, not a pipe list of ground-truth row positions.",
            f".{param}",
            f"{key} drawn by its item's rule as row positions, e.g. 0|4|9.",
            f"check the rule of the item emitting {key} in {DISTRACTORS_PATH}.",
        )
    return [int(part) for part in parts]


def _overlap(top: int, bottom: int, record: InkRecord) -> int:
    return min(bottom, record.box[3]) - max(top, record.box[1])


def locate_rows(
    ctx: OverlayContext, indices: Sequence[int], fields: Sequence[str], *, anchor: str, key: str
) -> list[Row]:
    """Find each annotated row on the page by its recorded scored boxes.

    Args:
        ctx: The drawing context; `ctx.fields` holds the recorded boxes, `ctx.ink` the printed ink.
        indices: Ground-truth row positions.
        fields: The row fields whose boxes `[i]` make up row `i`.
        anchor: The field every row must have a recorded box for (the one a mark sits on).
        key: The block key naming the fields, for the refusal.

    Returns:
        One `Row` per index, in the order given.

    Raises:
        OverlayError: A row has no recorded box for `anchor` (it is not a row the table prints with that
            value, so the annotation would land on the wrong line).
    """
    printed = [r for r in ctx.ink if r.role != "distractor" and r.box[3] > r.box[1] and r.box[2] > r.box[0]]
    rows = []
    for index in indices:
        boxes = {
            name: ink_of(ctx, ctx.fields[f"{name}[{index}]"])
            for name in fields
            if f"{name}[{index}]" in ctx.fields
        }
        if anchor not in boxes:
            raise refuse(
                ctx,
                "Annotated row is not on the page.",
                f"row {index} has no recorded {anchor}[{index}] box, so its annotation has nowhere to go "
                f"(recorded {anchor} rows: "
                f"{sorted(int(k[len(anchor) + 1 : -1]) for k in ctx.fields if k.startswith(anchor + '['))}).",
                key,
                f"every annotated row a row the table prints with a {anchor} value (a debit row), e.g. "
                f"{anchor}[{index}] recorded by the table's column field: {anchor}.",
                "check the annotated_rows rule draws debit rows only, and that the layout's table records "
                f"{anchor} on every row.",
            )
        left = min(b[0] for b in boxes.values())
        top = min(b[1] for b in boxes.values())
        right = max(b[2] for b in boxes.values())
        bottom = max(b[3] for b in boxes.values())
        core_top, core_bottom = top, bottom
        for record in printed:
            if 2 * _overlap(core_top, core_bottom, record) >= record.box[3] - record.box[1]:
                left, right = min(left, record.box[0]), max(right, record.box[2])
                top, bottom = min(top, record.box[1]), max(bottom, record.box[3])
        rows.append(Row(index=index, band=(left, top, right, bottom), boxes=boxes))
    return rows


def ink_of(ctx: OverlayContext, field_box: Box) -> Box:
    """The drawn ink of a recorded field: every scored ink line lying mostly inside its field box.

    A field's recorded box is its lines' layout box; its ink records are where the glyphs fell, which is
    what protection pads and what a mark must ring. A description wrapped to two lines is two ink
    records, both inside its field box, so its ink is their union. Falls back to the field box when no
    scored ink lies in it.
    """

    def area(record: InkRecord) -> int:
        box = record.box
        return max(0, min(box[2], field_box[2]) - max(box[0], field_box[0])) * max(
            0, min(box[3], field_box[3]) - max(box[1], field_box[1])
        )

    scored = [
        record.box
        for record in ctx.ink
        if record.role == "scored"
        and 2 * area(record) >= (record.box[2] - record.box[0]) * (record.box[3] - record.box[1]) > 0
    ]
    if not scored:
        return field_box
    return (
        min(b[0] for b in scored),
        min(b[1] for b in scored),
        max(b[2] for b in scored),
        max(b[3] for b in scored),
    )


# --- clearance -------------------------------------------------------------------------------------------


class Occupancy:
    """Where opaque overlay ink may not go: every padded protected box and everything outside the inset.

    A summed-area table over the padded boxes answers 'is this box clear' in constant time, so a lane can
    be scanned pixel by pixel.
    """

    def __init__(self, ctx: OverlayContext) -> None:
        """Rasterise the context's protected boxes and inset.

        Args:
            ctx: The drawing context.
        """
        width, height = ctx.page.size
        self.size = (width, height)
        self.inset = ctx.settings.page_inset_px
        self.protected = ctx.protected
        mask = np.zeros((height, width), dtype=bool)
        for entry in ctx.protected:
            left, top, right, bottom = entry.box
            left, top = max(left, 0), max(top, 0)
            right, bottom = min(right, width), min(bottom, height)
            if right > left and bottom > top:
                mask[top:bottom, left:right] = True
        self.mask = mask
        table = np.zeros((height + 1, width + 1), dtype=np.int64)
        table[1:, 1:] = mask.cumsum(axis=0).cumsum(axis=1)
        self._table = table

    def inside(self, box: Box) -> bool:
        """Whether a box lies within the page inset."""
        left, top, right, bottom = box
        width, height = self.size
        return (
            left >= self.inset
            and top >= self.inset
            and right <= width - self.inset
            and bottom <= height - self.inset
        )

    def clear(self, box: Box) -> bool:
        """Whether a box is inside the inset and meets no padded protected box."""
        if not self.inside(box):
            return False
        left, top, right, bottom = box
        table = self._table
        return int(table[bottom, right] - table[top, right] - table[bottom, left] + table[top, left]) == 0

    def blockers(self, box: Box) -> list[ProtectedBox]:
        """Every padded protected box a box meets."""
        left, top, right, bottom = box
        return [
            entry
            for entry in self.protected
            if entry.box[0] < right and left < entry.box[2] and entry.box[1] < bottom and top < entry.box[3]
        ]

    def stroke_blockers(self, alpha: np.ndarray, offset: tuple[int, int]) -> list[ProtectedBox]:
        """Every padded protected box any inked pixel of a tile (alpha above zero) falls inside."""
        left, top = offset
        height, width = alpha.shape
        inked = alpha > 0
        met = []
        for entry in self.protected:
            x0, y0 = max(entry.box[0] - left, 0), max(entry.box[1] - top, 0)
            x1, y1 = min(entry.box[2] - left, width), min(entry.box[3] - top, height)
            if x1 > x0 and y1 > y0 and inked[y0:y1, x0:x1].any():
                met.append(entry)
        return met


# --- lanes -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneGeometry:
    """What the lanes are measured from on one page.

    Attributes:
        content_left: The content box's left edge (the layout's margin).
        content_right: The content box's right edge (margin plus content width).
        description_field: The field whose box a row's `after_description` lane starts after, or None.
        column: The column `below_column` sits under, as (left, right), or None.
        table_bottom: The lowest edge of the table's recorded rows, where `below_column` starts.
    """

    content_left: int
    content_right: int
    description_field: str | None
    column: tuple[int, int] | None
    table_bottom: int


def lane_geometry(
    ctx: OverlayContext, description_field: str | None, column_field: str | None
) -> LaneGeometry:
    """Measure the lanes of the page in `ctx` from its layout and recorded boxes.

    Args:
        ctx: The drawing context.
        description_field: The row field `after_description` follows, or None for a block without one.
        column_field: The row field whose column `below_column` sits under, or None.

    Returns:
        The page's lane geometry.
    """
    margin = int(ctx.layout["margin"])
    content_right = margin + int(ctx.layout["content_width"])

    def members(name: str | None) -> list[Box]:
        return [box for key, box in ctx.fields.items() if name is not None and key.startswith(f"{name}[")]

    columns = members(column_field)
    table = members(description_field) + columns
    return LaneGeometry(
        content_left=margin,
        content_right=content_right,
        description_field=description_field,
        column=(min(b[0] for b in columns), max(b[2] for b in columns)) if columns else None,
        table_bottom=max((b[3] for b in table), default=margin),
    )


def _next_ink_right(ctx: OverlayContext, row: Row, after: int) -> int | None:
    """The left edge of the nearest printed ink right of `after` in the row's band, or None."""
    lefts = [
        record.box[0]
        for record in ctx.ink
        if record.role != "distractor"
        and record.box[0] >= after
        and 2 * _overlap(row.band[1], row.band[3], record) >= record.box[3] - record.box[1]
    ]
    return min(lefts, default=None)


def place(
    size: tuple[int, int],
    lane: str,
    row: Row,
    geometry: LaneGeometry,
    occupancy: Occupancy,
    ctx: OverlayContext,
    *,
    below_dx: int = 0,
) -> tuple[Box | None, str]:
    """Find the first clear box of `size` in a lane for a row.

    Args:
        size: The box's (width, height).
        lane: One of `LANES`.
        row: The row the box belongs to.
        geometry: The page's lane geometry.
        occupancy: What the box may not meet.
        ctx: The drawing context.
        below_dx: In `below_column`, how far the box's right edge sits from the column's right edge
            (negative is to the left), so writing under a column is not typeset flush with it.

    Returns:
        (box, "") when placed, else (None, why the lane has no clear box).
    """
    width, height = size
    page_w, page_h = ctx.page.size
    inset = occupancy.inset
    top = round(row.centre_y - height / 2)
    if lane == "left_margin":
        xs, ys = range(geometry.content_left - width, inset - 1, -1), range(top, top + 1)
        region = (inset, top, geometry.content_left, top + height)
    elif lane == "right_margin":
        xs, ys = range(geometry.content_right, page_w - inset - width + 1), range(top, top + 1)
        region = (geometry.content_right, top, page_w - inset, top + height)
    elif lane == "after_description":
        description = row.boxes.get(geometry.description_field or "")
        if description is None:
            return None, f"row {row.index} has no recorded {geometry.description_field} box"
        stop = _next_ink_right(ctx, row, description[2])
        end = stop if stop is not None else geometry.content_right
        xs, ys = range(description[2], end - width + 1), range(top, top + 1)
        region = (description[2], top, end, top + height)
    elif lane == "below_column":
        if geometry.column is None:
            return None, "the table records no column to write under"
        left = geometry.column[1] + below_dx - width
        xs, ys = range(left, left + 1), range(geometry.table_bottom, page_h - inset - height + 1)
        region = (left, geometry.table_bottom, left + width, page_h - inset)
    else:
        return None, f"unknown lane {lane!r}"
    for y in ys:
        for x in xs:
            box = (x, y, x + width, y + height)
            if occupancy.clear(box):
                return box, ""
    first = (xs[0] if len(xs) else region[0], ys[0] if len(ys) else region[1])
    probe = (first[0], first[1], first[0] + width, first[1] + height)
    met = occupancy.blockers(probe)
    named = (
        "; ".join(entry.describe() for entry in met)
        or "none (the lane is narrower than the box, or leaves the inset)"
    )
    return None, (
        f"no {width}x{height} box in {region} is inside the page inset and clear; at its first position "
        f"{probe} it meets: {named}"
    )


# --- strokes ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stroke:
    """A pen stroke drawn into its own tile.

    Attributes:
        alpha: Ink coverage, mode `L`.
        offset: The tile's top-left on the page.
    """

    alpha: Image.Image
    offset: tuple[int, int]

    @property
    def box(self) -> Box:
        """The stroke's ink box on the page, right and bottom exclusive."""
        bbox = self.alpha.getbbox()
        left, top = self.offset
        if bbox is None:
            return (left, top, left, top)
        return (bbox[0] + left, bbox[1] + top, bbox[2] + left, bbox[3] + top)


def wobble(count: int, amplitude: float, rng: random.Random) -> list[float]:
    """A smooth random offset per point: control values every few points, linearly interpolated.

    Args:
        count: Points along the path.
        amplitude: The largest offset either way.
        rng: The generator.

    Returns:
        `count` offsets in [-amplitude, amplitude].
    """
    if count <= 0:
        return []
    step = max(2, count // 4)
    controls = [rng.uniform(-amplitude, amplitude) for _ in range(count // step + 2)]
    return [
        controls[i // step] + (controls[i // step + 1] - controls[i // step]) * (i % step) / step
        for i in range(count)
    ]


def draw_stroke(points: Sequence[Point], widths: Sequence[float]) -> Stroke:
    """Draw a polyline whose width varies point by point, antialiased.

    Args:
        points: Page coordinates of the path.
        widths: The pen width at each point.

    Returns:
        The stroke's tile and offset.
    """
    reach = max(widths) / 2 + 2
    left = math.floor(min(x for x, _ in points) - reach)
    top = math.floor(min(y for _, y in points) - reach)
    right = math.ceil(max(x for x, _ in points) + reach)
    bottom = math.ceil(max(y for _, y in points) + reach)
    scale = SUPERSAMPLE
    big = Image.new("L", ((right - left) * scale, (bottom - top) * scale), 0)
    draw = ImageDraw.Draw(big)
    scaled = [((x - left) * scale, (y - top) * scale) for x, y in points]
    for i, (x, y) in enumerate(scaled):
        radius = widths[i] * scale / 2
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
        if i + 1 < len(scaled):
            width = round((widths[i] + widths[i + 1]) / 2 * scale)
            draw.line((x, y, *scaled[i + 1]), fill=255, width=max(1, width))
    return Stroke(alpha=big.reduce(scale), offset=(left, top))


def _sampled(path: Sequence[Point], spacing: float) -> list[Point]:
    """Points every `spacing` pixels along a polyline."""
    out: list[Point] = [path[0]]
    for (x0, y0), (x1, y1) in zip(path, path[1:], strict=False):
        steps = max(1, round(math.hypot(x1 - x0, y1 - y0) / spacing))
        out += [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps) for k in range(1, steps + 1)]
    return out


def _jittered(points: Sequence[Point], amplitude: float, rng: random.Random) -> list[Point]:
    """Move each point along its path's normal by a smooth random offset."""
    offsets = wobble(len(points), amplitude, rng)
    moved = []
    for i, (x, y) in enumerate(points):
        ax, ay = points[max(i - 1, 0)]
        bx, by = points[min(i + 1, len(points) - 1)]
        length = math.hypot(bx - ax, by - ay) or 1.0
        nx, ny = -(by - ay) / length, (bx - ax) / length
        moved.append((x + nx * offsets[i], y + ny * offsets[i]))
    return moved


def _taper(count: int, width: float, taper: Taper) -> list[float]:
    """A width per point that swells from `taper.start` to full in the middle and falls to `taper.end`."""
    if count == 1:
        return [width]
    widths = []
    for i in range(count):
        t = i / (count - 1)
        floor = taper.start if t < 0.5 else taper.end
        widths.append(width * (floor + (1 - floor) * math.sin(math.pi * t)))
    return widths


def tick_stroke(shape: TickShape, width: float, jitter: float, rng: random.Random) -> Stroke:
    """A tick drawn in a `shape.size_px` square at the page origin: a short down-stroke, a long rising one.

    Its proportions, the bow of the long stroke and the taper as the pen lifts are drawn on `rng` within
    `overlay.ink.tick`, so no two ticks are alike.
    """
    size = shape.size_px

    def point(spec: tuple[tuple[float, float], tuple[float, float]]) -> Point:
        return rng.uniform(*spec[0]) * size, rng.uniform(*spec[1]) * size

    knee = point(shape.knee)
    start = point(shape.start)
    tip = point(shape.tip)
    bow = rng.uniform(*shape.bow) * size
    mid = ((knee[0] + tip[0]) / 2 + bow, (knee[1] + tip[1]) / 2 + bow)
    points = _jittered(_sampled([start, knee, mid, tip], shape.spacing_px), jitter, rng)
    return draw_stroke(points, _taper(len(points), width, shape.taper))


def loop_stroke(
    target: Box,
    *,
    reach: tuple[float, float],
    width: float,
    jitter: float,
    shape: CircleShape,
    rng: random.Random,
) -> Stroke:
    """A loop round a box: an ellipse of semi-axes `reach`, started anywhere, overrunning its start.

    The overrun (`overlay.ink.circle.overrun` of a turn) drifts outward by up to `jitter`, so the two ends
    do not meet exactly, as a pen's do not.
    """
    cx, cy = (target[0] + target[2]) / 2, (target[1] + target[3]) / 2
    a, b = reach
    start = rng.uniform(0, 2 * math.pi)
    sweep = 2 * math.pi * (1 + rng.uniform(*shape.overrun)) * rng.choice((1, -1))
    count = max(24, round(math.pi * (a + b) / shape.spacing_px))
    drift = rng.uniform(0, jitter)
    radial = wobble(count, jitter, rng)
    points = []
    for i in range(count):
        t = i / (count - 1)
        angle = start + sweep * t
        grow = drift * t + radial[i]
        points.append(((a + grow) * math.cos(angle) + cx, (b + grow) * math.sin(angle) + cy))
    return draw_stroke(points, _taper(count, width, shape.taper))


def underline_stroke(
    target: Box, *, y: float, width: float, jitter: float, shape: UnderlineShape, rng: random.Random
) -> Stroke:
    """A line under a box at height `y`, starting a little before it and running a little past it."""
    lead = rng.uniform(*shape.lead_jitters) * jitter
    trail = rng.uniform(*shape.lead_jitters) * jitter
    path = _sampled(
        [(target[0] - lead, y), (target[2] + trail, y + rng.uniform(-jitter, jitter) / 2)], shape.spacing_px
    )
    offsets = wobble(len(path), jitter / 2, rng)
    points = [(x, py + offsets[i]) for i, (x, py) in enumerate(path)]
    return draw_stroke(points, _taper(len(points), width, shape.taper))


def pen_colour(ctx: OverlayContext) -> tuple[int, int, int]:
    """The statement's one pen, drawn on its `pen` page seed: shared by every mark and note on it."""
    colours = ctx.settings.ink.colours
    return colours[ctx.page_rng("pen").randrange(len(colours))]


def strokes_layer(
    ctx: OverlayContext, strokes: Sequence[Stroke], colour: tuple[int, int, int]
) -> Image.Image:
    """Every stroke on one page-sized RGBA layer in one colour; where strokes cross, the darker wins."""
    alpha = Image.new("L", ctx.page.size, 0)
    for stroke in strokes:
        tile = Image.new("L", ctx.page.size, 0)
        tile.paste(stroke.alpha, stroke.offset)
        alpha = Image.fromarray(np.maximum(np.asarray(alpha), np.asarray(tile)), mode="L")
    layer = Image.new("RGBA", ctx.page.size, (*colour, 0))
    layer.putalpha(alpha)
    return layer


# --- highlight -------------------------------------------------------------------------------------------


def draw_highlight(block: dict, ctx: OverlayContext) -> OverlayDrawing:
    """Draw a `highlight` block; see the module docstring.

    Args:
        block: The block (`type`, `when`, `rows`, `row_fields`, `exclude_colours`), already validated.
        ctx: The drawing context, its `rng` seeded on the block's overlay seed.

    Returns:
        A tint whose one mask holds every band; no ink.

    Raises:
        OverlayError: A row has no recorded box for the last of `row_fields`.
    """
    settings = ctx.settings.highlighter
    fields = [str(name) for name in block["row_fields"]]
    rows = locate_rows(ctx, row_indices(ctx, block, "rows"), fields, anchor=fields[-1], key=".row_fields")
    rng = ctx.rng
    excluded = {str(name) for name in block["exclude_colours"]}
    names = sorted(name for name in settings.colours if name not in excluded)
    colour = settings.colours[names[rng.randrange(len(names))]]
    opacity = rng.uniform(*settings.opacity)
    mask = np.zeros((ctx.page.size[1], ctx.page.size[0]), dtype=np.float64)
    bands: list[Box] = []
    marks: list[OverlayMark] = []
    for row in rows:
        tile, origin = highlight_band(row.band, settings, opacity, rng)
        height, width = tile.shape
        left, top = origin
        # A band reaching past the page is kept to the page here; the engine's inset check refuses it.
        x0, y0 = max(left, 0), max(top, 0)
        x1, y1 = min(left + width, mask.shape[1]), min(top + height, mask.shape[0])
        region = mask[y0:y1, x0:x1]
        np.maximum(region, tile[y0 - top : y1 - top, x0 - left : x1 - left], out=region)
        rows_inked, cols_inked = np.nonzero(tile)
        band = (
            left + int(cols_inked.min()),
            top + int(rows_inked.min()),
            left + int(cols_inked.max()) + 1,
            top + int(rows_inked.max()) + 1,
        )
        bands.append(band)
        marks.append(OverlayMark(key=str(block["when"]), kind=HIGHLIGHT_TYPE, box=band, opaque=False))
    tint = Image.fromarray(np.rint(np.clip(mask, 0.0, 1.0) * 255).astype(np.uint8), mode="L")
    return OverlayDrawing(marks=tuple(marks), tint=Tint(mask=tint, colour=colour, bands=tuple(bands)))


def highlight_band(
    band: Box, settings: HighlighterSettings, opacity: float, rng: random.Random
) -> tuple[np.ndarray, tuple[int, int]]:
    """One highlighter stroke over a row band, as a felt tip leaves it.

    The band is padded past the row's ink by `pad_px`; each end corner leans or runs short or long by up
    to `end_jitter_px`; the top and bottom edges wander along the length by up to `edge_jitter_px`. The
    outline is drawn supersampled and reduced, so its edges are antialiased. The ink is then uneven: its
    strength is `opacity` times a smooth factor varying by up to `density_wobble` along the band, rising
    to `end_density` within `end_length_px` of each end, where the pen starts and stops; never above
    full strength.

    Args:
        band: The row's printed extent (left, top, right, bottom).
        settings: `overlay.highlighter`.
        opacity: The statement's highlighter strength, in (0, 1].
        rng: The block's generator.

    Returns:
        The band's blend weights in [0, 1] (rows by columns) and the page position of its top-left.
    """
    left, top, right, bottom = band
    pad, lean, edge = settings.pad_px, settings.end_jitter_px, settings.edge_jitter_px
    x0, x1 = left - pad, right + pad
    y0, y1 = top - pad, bottom + pad
    corners = [rng.uniform(-lean, lean) for _ in range(4)]
    count = max(2, (x1 - x0) // 16 + 1)
    top_wander = wobble(count, edge, rng)
    bottom_wander = wobble(count, edge, rng)
    upper = [
        (x0 + corners[0] + (x1 + corners[1] - x0 - corners[0]) * i / (count - 1), y0 + top_wander[i])
        for i in range(count)
    ]
    lower = [
        (x0 + corners[3] + (x1 + corners[2] - x0 - corners[3]) * i / (count - 1), y1 + bottom_wander[i])
        for i in range(count)
    ]
    outline = upper + lower[::-1]
    origin_x = math.floor(min(x for x, _ in outline)) - 1
    origin_y = math.floor(min(y for _, y in outline)) - 1
    width = math.ceil(max(x for x, _ in outline)) + 2 - origin_x
    height = math.ceil(max(y for _, y in outline)) + 2 - origin_y
    scale = SUPERSAMPLE
    big = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(big).polygon(
        [((x - origin_x) * scale, (y - origin_y) * scale) for x, y in outline], fill=255
    )
    coverage = np.asarray(big.reduce(scale), dtype=np.float64) / 255.0
    columns = np.arange(width, dtype=np.float64) + origin_x
    start, end = min(x0 + corners[0], x0 + corners[3]), max(x1 + corners[1], x1 + corners[2])
    to_end = np.minimum(columns - start, end - columns).clip(min=0.0)
    ramp = (1.0 - to_end / settings.end_length_px).clip(min=0.0)
    along = np.asarray(wobble(width, settings.density_wobble, rng))
    strength = opacity * (1.0 + along) * (1.0 + (settings.end_density - 1.0) * ramp)
    weights = (coverage * strength[np.newaxis, :]).clip(0.0, 1.0)
    return weights, (origin_x, origin_y)


# --- ink marks -------------------------------------------------------------------------------------------


def _mark_kinds(ctx: OverlayContext, block: dict, count: int) -> list[str]:
    key = str(block["marks"])
    fields = ctx.entry["fields"]
    kinds = str(fields[key]).split("|") if is_present(fields, key) else []
    if len(kinds) != count or any(kind not in block["kinds"] for kind in kinds):
        raise refuse(
            ctx,
            "Row marks do not match the annotated rows.",
            f"{key} is {fields.get(key)!r}: {len(kinds)} kind(s) for {count} annotated row(s), each of "
            f"which must be one of the block's kinds {list(block['kinds'])}.",
            ".marks",
            f"one kind per annotated row, aligned with {block['rows']}, e.g. tick|circle|underline.",
            f"check the row_marks rule's marks in {DISTRACTORS_PATH}, and that it requires the item "
            f"emitting {block['rows']}.",
        )
    return kinds


def draw_ink_marks(block: dict, ctx: OverlayContext) -> OverlayDrawing:
    """Draw an `ink_marks` block; see the module docstring.

    Args:
        block: The block (`type`, `when`, `rows`, `marks`, `target_field`, `kinds`, `tick_lane`), already
            validated.
        ctx: The drawing context, its `rng` seeded on the block's overlay seed.

    Returns:
        Every stroke on one layer, each stroke's box as a distractor mark.

    Raises:
        OverlayError: The marks do not align with the rows, a row has no recorded target box, or a mark
            cannot be placed clear of every padded protected box (named) and inside the page inset.
    """
    ink = ctx.settings.ink
    target_field = str(block["target_field"])
    rows = locate_rows(
        ctx, row_indices(ctx, block, "rows"), [target_field], anchor=target_field, key=".target_field"
    )
    kinds = _mark_kinds(ctx, block, len(rows))
    occupancy = Occupancy(ctx)
    geometry = lane_geometry(ctx, None, None)
    clearance = ctx.settings.clearance_px
    rng = ctx.rng
    strokes: list[Stroke] = []
    marks: list[OverlayMark] = []
    for row, kind in zip(rows, kinds, strict=True):
        width = rng.uniform(*ink.stroke_px)
        target = row.boxes[target_field]
        reach = width / 2 + ink.jitter_px
        if kind == "tick":
            stroke = tick_stroke(ink.tick, width, ink.jitter_px, rng)
            box = stroke.box
            size = (box[2] - box[0], box[3] - box[1])
            placed, why = place(size, str(block["tick_lane"]), row, geometry, occupancy, ctx)
            if placed is None:
                raise _unplaced(ctx, block, row, kind, why)
            stroke = Stroke(
                stroke.alpha, (stroke.offset[0] + placed[0] - box[0], stroke.offset[1] + placed[1] - box[1])
            )
        elif kind == "circle":
            half_w = (target[2] - target[0]) / 2 + clearance + reach
            half_h = (target[3] - target[1]) / 2 + clearance + reach
            b = half_h + ink.circle.gap_px
            a = half_w / math.sqrt(1 - (half_h / b) ** 2) + ink.circle.gap_px
            stroke = loop_stroke(
                target, reach=(a, b), width=width, jitter=ink.jitter_px, shape=ink.circle, rng=rng
            )
        else:
            y = target[3] + clearance + ink.underline.gap_px + reach
            stroke = underline_stroke(
                target, y=y, width=width, jitter=ink.jitter_px, shape=ink.underline, rng=rng
            )
        alpha = np.asarray(stroke.alpha)
        met = occupancy.stroke_blockers(alpha, stroke.offset)
        if met or not occupancy.inside(stroke.box):
            reason = (
                f"its stroke at {stroke.box} meets {len(met)} protected box(es):\n{describe_boxes(met)}"
                if met
                else f"its stroke at {stroke.box} leaves the page inset ({occupancy.inset}px)"
            )
            raise _unplaced(ctx, block, row, kind, reason)
        strokes.append(stroke)
        marks.append(OverlayMark(key=str(block["when"]), kind=kind, box=stroke.box, opaque=True))
    return OverlayDrawing(
        marks=tuple(marks),
        layer=strokes_layer(ctx, strokes, pen_colour(ctx)),
        mark_ink=tuple(stroke.box for stroke in strokes),
    )


def _unplaced(ctx: OverlayContext, block: dict, row: Row, kind: str, why: str) -> OverlayError:
    lane = f" in lane {block['tick_lane']}" if kind == "tick" else ""
    return refuse(
        ctx,
        "Overlay ink mark cannot be placed.",
        f"the {kind} for row {row.index} (band {row.band}){lane}: {why}.",
        ".tick_lane" if kind == "tick" else ".target_field",
        "every mark clear of every padded protected box (overlay.clearance_px) and inside the page inset: a "
        "tick in its lane level with its row, a loop or underline round the row's amount, e.g. "
        "overlay: {ink: {tick: {size_px: 34}, circle: {gap_px: 8}, underline: {gap_px: 6}}}.",
        f"choose another tick_lane for {ctx.site.block_path}, or make the marks smaller in overlay.ink "
        f"(a {kind} is never swapped for another kind).",
    )


OVERLAY_DRAWERS[HIGHLIGHT_TYPE] = draw_highlight
OVERLAY_DRAWERS[INK_MARKS_TYPE] = draw_ink_marks
