"""Translate a layout tree, with one entry's values, into the Typst markup of one page.

Content is never chosen here. The page is first drawn exactly as stage one draws it: `render_body` with a
recorder, so the field providers, `when:` gating, label and currency formatting and table row providers
are the PIL engine's own. From that draw the translator takes every block it opened (stage one's
`BlockRecord`s), the string it recorded for each scored field, and, through `render_body(drawn_out=...)`,
which blocks were drawn and the lines and marks each one drew. It then walks the same layout tree and
writes one template call (`generators/typst/template.typ`) per drawn block, each block a parser finds
wrapped in a `block-record` and each table cell in a `cell-record`, so a typeset page carries the same
text as its stage-one twin.

Typst never fits text (Ruling P7): every line of text is one stage one drew, at the size, in the face and
colour and with the string stage one's fitting gave it, read from that same draw (`_TracingDraw`). A
field stage one shrank is set at the shrunk size; one it wrapped keeps its lines; one it drew as one line
stays one line (`dsl-line` never wraps). Typst places the lines: a block's lines one under another at
the pitch stage one drew them, aligned in their region; a table's cells, rules and shading each where
stage one drew them, inside a block as tall as stage one's table. Every length is a stage-one pixel value,
converted to points by `page_scale`: at the canvas resolution for a sheet, whose content is centred
across the A4 width, and at the roll's width for a receipt.

Overlays (the marks a distractor page draws over the finished body) are not yet placed: a page carries
its body's blocks only.
"""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import segno
from PIL import Image, ImageColor, ImageDraw, ImageFont

from generators.common import font_path, font_spec
from generators.distractors.paths import REPO_ROOT
from generators.exporters.geometry import BlockRecord, BoxRecorder, Cell, InkRecord
from generators.layout_dsl.binding import interpolate
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.engine import (
    PRIMITIVE_DRAWERS,
    SCOPED_CONTAINERS,
    SCOPED_LEAVES,
    DrawnBlock,
    render_body,
)
from generators.layout_dsl.primitives_codes import _chevron_depth, _fit_initials, barcode_geometry
from generators.layout_dsl.primitives_text import line_advance, resolve_role
from generators.layout_dsl.schema import FRAMES, GROUPINGS
from generators.loader import load_layout_registry
from generators.typst.config import PageSpec, TypesetConfig

# The primitive types the translator writes: every primitive stage one draws. A layout using any other
# is refused.
TRANSLATED: frozenset[str] = frozenset(PRIMITIVE_DRAWERS)

# The gate layouts of the plan's Decision 9, each with the file that holds it.
GATE_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("config/layouts/invoices.yml", "tax_invoice_standard"),
    ("config/layouts/receipts.yml", "receipt_thermal_80mm"),
    ("config/layouts/bank_statements.yml", "westpac_standard"),
)

# Options a translated primitive may carry, by type, with the values the translator typesets. A block
# carrying any other value is refused rather than drawn some other way. An absent key reads as None.
SUPPORTED_OPTIONS: dict[str, dict[str, tuple[object, ...]]] = {
    "table": {"grouping": GROUPINGS, "frame": FRAMES},
    "split": {"divider": (None, False, True)},
}

_PT_PER_MM = 72 / 25.4
_ALIGNS = {"left": "left", "center": "center", "right": "right"}
# The leaves whose own `align:` places them in their region; every other block sits at its left edge.
_SELF_ALIGNED = frozenset({"text", "logo", "barcode", "qrcode"})


class TranslateError(RuntimeError):
    """Raised when a layout uses a primitive, or a primitive option, the translator does not typeset."""


@dataclass(frozen=True)
class Translation:
    """One translated page.

    Attributes:
        source: The page's whole Typst source.
        blocks: Stage one's block records for the page, in draw order; each is a `block-record` in
            `source` with the same id.
        texts: The string drawn for each scored field, as stage one's recorder holds it.
        ink: Stage one's ink records for the same draw, in draw order. The read-back takes from them what
            the PDF cannot say: each code mark's payload, and the role of the lines in each block or cell.
        overshoot: For each block whose stage-one ink runs out of the column it was drawn in, how far, in
            points at the page's scale, past the column's (left, right) edge; a block whose ink stays in
            its column is absent. The layout checks allow the typeset block no further (Ruling P9).
    """

    source: str
    blocks: list[BlockRecord]
    texts: dict[str, str]
    ink: list[InkRecord]
    overshoot: dict[int, tuple[float, float]]


@dataclass(frozen=True)
class _Drawn:
    """One line of text stage one drew, as its draw call gave it.

    Attributes:
        text: The string drawn.
        x: The x it was drawn at (the pen's start), in pixels.
        width: Its advance width at its size, in pixels: the line runs from `x` to `x + width`.
        y: The y it was drawn at (the top of the face's ascent), in pixels.
        face: The family name Typst finds the face by.
        size: The font size, in pixels.
        bold: Whether the face is the bold one.
        ascent: The face's ascent at `size`, in pixels.
        descent: The face's descent at `size`, in pixels.
        fill: The colour it was drawn in, as Pillow took it.
        path: The path of the block whose scope was open, or None outside any.
        cell: The table cell it was drawn in, or None.
    """

    text: str
    x: int
    width: float
    y: int
    face: str
    size: int
    bold: bool
    ascent: int
    descent: int
    fill: object
    path: str | None
    cell: Cell | None


@dataclass(frozen=True)
class _Paint:
    """One filled rectangle stage one painted (a rule, a frame's edge, a shaded band), inside a block.

    Attributes:
        box: (left, top, right, bottom) in pixels, every edge inclusive, as Pillow paints it.
        fill: The colour, as Pillow took it.
        path: The path of the block whose scope was open, or None outside any.
    """

    box: tuple[int, int, int, int]
    fill: object
    path: str | None


class _TracingRecorder(BoxRecorder):
    """Stage one's recorder, which also says which block's scope, and which cell, is open."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        self.open_path: str | None = None
        self.open_cell: Cell | None = None

    @contextmanager
    def block_scope(
        self,
        kind: str,
        *,
        path: str,
        anchor: str | None,
        parse_category: str | None,
        parse_order: int,
        join: str | None,
    ) -> Iterator[None]:
        """Open stage one's block scope, noting its path while it is the outermost one open."""
        outer = self.open_path
        self.open_path = path if outer is None else outer
        try:
            with super().block_scope(
                kind,
                path=path,
                anchor=anchor,
                parse_category=parse_category,
                parse_order=parse_order,
                join=join,
            ):
                yield
        finally:
            self.open_path = outer
            if outer is None:
                self.open_cell = None

    def set_cell(self, cell: Cell | None) -> None:
        """Mark the cell ink is drawn in, as stage one's recorder does, noting it."""
        super().set_cell(cell)
        self.open_cell = cell


class _TracingDraw(ImageDraw.ImageDraw):
    """A Pillow drawing surface that keeps every line of text and every rectangle or straight line drawn.

    Stage one's primitives draw through it unchanged; the translator reads back what they drew: each
    line's string, face, size, colour and place, and each rule and shaded band a table paints.
    """

    def __init__(self, image: Image.Image, recorder: _TracingRecorder) -> None:
        super().__init__(image)
        self.recorder = recorder
        self.lines: list[_Drawn] = []
        self.paints: list[_Paint] = []

    def text(
        self, xy: Any, text: Any, fill: Any = None, font: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        """Draw a line of text, keeping it."""
        if font is not None and isinstance(text, str) and "\n" not in text:
            size, bold = font_spec(font)
            ascent, descent = font.getmetrics()
            self.lines.append(
                _Drawn(
                    text,
                    int(xy[0]),
                    float(font.getlength(text)),
                    int(xy[1]),
                    str(font.getname()[0]),
                    size,
                    bold,
                    int(ascent),
                    int(descent),
                    fill,
                    self.recorder.open_path,
                    self.recorder.open_cell,
                )
            )
        super().text(xy, text, fill, font, *args, **kwargs)

    def line(self, xy: Any, fill: Any = None, width: int = 0, joint: str | None = None) -> None:
        """Draw a straight line, keeping it as the band of pixels it paints when it is level or upright."""
        points = _points(xy)
        thickness = max(1, width)
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            if y0 == y1 or x0 == x1:
                low = (thickness - 1) // 2
                box = (
                    (min(x0, x1), y0 - low, max(x0, x1), y0 - low + thickness - 1)
                    if y0 == y1
                    else (x0 - low, min(y0, y1), x0 - low + thickness - 1, max(y0, y1))
                )
                self.paints.append(_Paint(box, fill, self.recorder.open_path))
        super().line(xy, fill, width, joint)

    def rectangle(self, xy: Any, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        """Draw a rectangle, keeping its filled area and each edge of its outline."""
        (x0, y0), (x1, y1) = _points(xy)[:2]
        path = self.recorder.open_path
        if fill is not None:
            self.paints.append(_Paint((x0, y0, x1, y1), fill, path))
        if outline is not None and width > 0:
            w = width - 1
            for box in (
                (x0, y0, x1, y0 + w),
                (x0, y1 - w, x1, y1),
                (x0, y0, x0 + w, y1),
                (x1 - w, y0, x1, y1),
            ):
                self.paints.append(_Paint(box, outline, path))
        super().rectangle(xy, fill, outline, width)


def _points(xy: Any) -> list[tuple[int, int]]:
    """Return Pillow coordinates (pairs, or a flat sequence) as a list of integer points."""
    flat: list[float] = []
    for item in xy:
        if isinstance(item, (tuple, list)):
            flat.extend(item)
        else:
            flat.append(item)
    return [(int(flat[i]), int(flat[i + 1])) for i in range(0, len(flat) - 1, 2)]


@dataclass(frozen=True)
class _StageOne:
    """What stage one's engine drew for the page: its records, its ink and each drawn block's share.

    Attributes:
        blocks: Its block records.
        texts: The string recorded for each scored field.
        ink: Its ink records.
        drawn: Each drawn block's share of the ink and the cursor, by path.
        entry: The provider-enriched entry the body was drawn from.
        lines: Every line of text drawn, in draw order.
        paints: Every rule, frame edge and shaded band painted, in draw order.
    """

    blocks: list[BlockRecord]
    texts: dict[str, str]
    ink: list[InkRecord]
    drawn: dict[str, DrawnBlock]
    entry: dict
    lines: list[_Drawn]
    paints: list[_Paint]


def _child_lists(block: dict, path: str) -> list[tuple[str, list]]:
    """Return a container's child block lists, each with the path prefix its children are named under."""
    children = block.get("children")
    if children is None:
        return []
    if block.get("type") == "split":
        return [(f"{path}.children[{column}]", list(blocks)) for column, blocks in enumerate(children)]
    return [(f"{path}.children", list(children))]


def _walk_types(blocks: list) -> set[str]:
    """Return every primitive type in a block list and its nested children."""
    found: set[str] = set()
    for block in blocks:
        found.add(str(block.get("type")))
        for _, children in _child_lists(block, ""):
            found |= _walk_types(children)
    return found


def gate_primitives() -> frozenset[str]:
    """Return every primitive type the gate layouts (`GATE_LAYOUTS`) use, read from their trees.

    Returns:
        The set of `type:` values found anywhere in the three gate layouts' bodies.
    """
    found: set[str] = set()
    for layout_file, layout_id in GATE_LAYOUTS:
        found |= _walk_types(load_layout_registry(REPO_ROOT / layout_file)[layout_id]["body"])
    return frozenset(found)


def page_scale(layout: dict, page: PageSpec, *, canvas_dpi: int) -> float:
    """Return the points per stage-one pixel of a layout typeset on its physical page.

    A sheet's canvas is drawn at `canvas_dpi` (exactly A4 high, narrower than A4), so its pixels convert at
    that resolution and keep their stage-one physical size. A roll's pixel `width` spans the roll's width,
    since its height follows its content.

    Args:
        layout: The resolved layout dict.
        page: The layout's physical page.
        canvas_dpi: The resolution of stage one's sheet canvases (`TypesetConfig.canvas_dpi`).

    Returns:
        Points per pixel.
    """
    if page.height_mm is None:
        return page.width_mm * _PT_PER_MM / _pixel_width(layout, page)
    return 72 / canvas_dpi


def _pixel_width(layout: dict, page: PageSpec) -> int:
    """Return the width, in stage-one pixels, a layout draws its page at: a sheet's or a roll's."""
    return int(layout["width"]) if page.height_mm is None else int(layout["page_dimensions"]["width"])


def _refusal(what: str, *, layout_path: Path, where: str, expected: str, recover: str) -> TranslateError:
    """Build a four-part refusal naming the layout file and the dotted path inside it."""
    return TranslateError(
        "Cannot translate layout.\n"
        f"  What:     {what}\n"
        f"  Where:    {layout_path.resolve()} -> {where}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _refuse_untranslated(blocks: list, *, prefix: str, layout_id: str, layout_path: Path) -> None:
    """Refuse the first block, drawn or not, whose type or option the translator does not typeset."""
    for position, block in enumerate(blocks):
        path = f"{prefix}[{position}]"
        kind = block.get("type")
        if kind not in TRANSLATED:
            raise _refusal(
                f"primitive '{kind}' has no Typst translation.",
                layout_path=layout_path,
                where=f"{layout_id}.{path}",
                expected=f"one of {sorted(TRANSLATED)} (TRANSLATED in generators/typst/translate.py).",
                recover="use a translated primitive in this layout, or add its function to "
                "generators/typst/template.typ and its translation to generators/typst/translate.py.",
            )
        for key, allowed in SUPPORTED_OPTIONS.get(str(kind), {}).items():
            value = block.get(key)
            if value not in allowed:
                shown = ", ".join("absent" if item is None else repr(item) for item in allowed)
                example = f"{key}: {allowed[0]}" if allowed[0] is not None else f"no {key} key at all"
                raise _refusal(
                    f"a '{kind}' block's {key} {value!r} has no Typst translation.",
                    layout_path=layout_path,
                    where=f"{layout_id}.{path}.{key}",
                    expected=f"{key} one of: {shown}; e.g. {example}.",
                    recover=f"change this block's {key}, or teach generators/typst/translate.py (and "
                    "SUPPORTED_OPTIONS there) to typeset it.",
                )
        if block.get("parse_block") is True:
            raise _refusal(
                f"a '{kind}' container sets parse_block: true, which joins its children's lines into one "
                "block.",
                layout_path=layout_path,
                where=f"{layout_id}.{path}.parse_block",
                expected="no parse_block key on a container, e.g. {type: split, children: [[...], [...]]}; "
                "each leaf inside then records a block of its own.",
                recover="remove parse_block from this container; the translator does not yet typeset a "
                "parse_block container (generators/typst/translate.py would need to write its children's "
                "lines into one record).",
            )
        nested = prefix != "body"
        if nested and block.get("anchor") == "foot":
            raise _refusal(
                f"a '{kind}' block nested inside a container is anchored to the foot of the page.",
                layout_path=layout_path,
                where=f"{layout_id}.{path}.anchor",
                expected="anchor: foot only on a block directly in the layout's body, e.g. "
                "body: [..., {type: stack, anchor: foot, children: [...]}].",
                recover="move the foot-anchored block out to the body, or teach "
                "generators/typst/translate.py to place a nested foot block.",
            )
        if nested and kind == "banner":
            raise _refusal(
                "a 'banner' block is nested inside a container, but a banner paints from the page's own "
                "top-left corner.",
                layout_path=layout_path,
                where=f"{layout_id}.{path}",
                expected="a banner directly in the layout's body, e.g. body: [{type: banner, ...}, ...].",
                recover="move the banner out to the body, or teach generators/typst/translate.py to "
                "place a nested banner.",
            )
        if kind == "table":
            for index, column in enumerate(block.get("columns", [])):
                if column.get("sub_line") is not None and column.get("align") == "right":
                    raise _refusal(
                        "a right-aligned table column carries a sub_line, which stage one draws "
                        "left-aligned from the column's anchor.",
                        layout_path=layout_path,
                        where=f"{layout_id}.{path}.columns[{index}].sub_line",
                        expected="sub_line only on a left-aligned column, e.g. "
                        "{key: description, align: left, x: 0, sub_line: {key: reference}}.",
                        recover="move the sub_line to a left-aligned column, or teach "
                        "generators/typst/translate.py to place a cell's lines at two alignments.",
                    )
        for child_prefix, children in _child_lists(block, path):
            _refuse_untranslated(
                children, prefix=child_prefix, layout_id=layout_id, layout_path=layout_path
            )


def _draw_stage_one(
    layout: dict, entry: dict, *, layout_id: str, layout_path: Path, page: PageSpec, distractors: bool
) -> _StageOne:
    """Draw the page's body as stage one's renderer does, keeping every record the translator reads.

    The canvas and content region are the renderer's own: a sheet is `page_dimensions` in size, a roll
    `width` by `canvas_ceiling`, and the body starts at the margin inside `content_width`.
    """
    if page.height_mm is None:
        width, height = int(layout["width"]), int(layout["canvas_ceiling"])
    else:
        width, height = int(layout["page_dimensions"]["width"]), int(layout["page_dimensions"]["height"])
    recorder = _TracingRecorder(width, height)
    surface = _TracingDraw(Image.new("RGB", (width, height), "white"), recorder)
    enriched: dict = {}
    drawn: dict[str, DrawnBlock] = {}
    render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=str(layout_path),
        draw=surface,
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        distractors=distractors,
        recorder=recorder,
        entry_out=enriched,
        drawn_out=drawn,
    )
    return _StageOne(
        recorder.block_records(),
        recorder.texts(),
        recorder.ink_records(),
        drawn,
        enriched,
        surface.lines,
        surface.paints,
    )


@functools.cache
def _font_name(family: str, bold: bool) -> str:
    """Return the family name Typst finds a vendored face by, read from the face's own file."""
    return str(ImageFont.truetype(str(font_path(family, bold=bold)), 12).getname()[0])


@functools.cache
def _ascent(family: str, bold: bool, size: int) -> int:
    """Return a vendored face's ascent at `size`, in pixels, as stage one's Pillow fonts measure it."""
    return int(ImageFont.truetype(str(font_path(family, bold=bold)), size).getmetrics()[0])


def _string(text: str) -> str:
    """Return `text` as a Typst string literal."""
    for raw, escape in (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        text = text.replace(raw, escape)
    return f'"{text}"'


def _colour(value: object) -> str:
    """Return a colour (a layout's name or hex string, or a Pillow RGB tuple) as a Typst `rgb(...)` call."""
    if isinstance(value, tuple):
        return f"rgb{tuple(int(channel) for channel in value[:3])}"
    if not isinstance(value, str):
        raise TypeError(f"cannot write the colour {value!r} as rgb(...): expected a name, hex or RGB tuple")
    return f"rgb{tuple(ImageColor.getrgb(value)[:3])}"


def _array(items: list[str]) -> str:
    """Return a Typst array of already-written items; a one-item array keeps its trailing comma."""
    return f"({', '.join(items)},)" if items else "()"


def _code(calls: list[str]) -> str:
    """Return a Typst code block joining `calls`' content, or empty content for none."""
    return "{\n" + "\n".join(calls) + "\n}" if calls else "[]"


class _Writer:
    """Write one page's body as template calls, reading each drawn block's content from stage one."""

    def __init__(
        self,
        stage_one: _StageOne,
        layout: dict,
        *,
        layout_id: str,
        layout_path: Path,
        scale: float,
        page_origin: tuple[float, float],
        page_width: float,
    ) -> None:
        """Prepare to write a page.

        Args:
            stage_one: What stage one drew for the page.
            layout: The resolved layout dict.
            layout_id: The layout's id.
            layout_path: The layout YAML, named in a refusal.
            scale: Points per stage-one pixel (`page_scale`).
            page_origin: Where the page's content area starts, in stage-one pixels from the page's own
                top-left corner: its left and top margins.
            page_width: The page's width in stage-one pixels.
        """
        self.stage_one = stage_one
        self.layout = layout
        self.layout_id = layout_id
        self.layout_path = str(layout_path)
        self.scale = scale
        self.page_origin = page_origin
        self.page_width = page_width
        self.records = {record.path: record for record in stage_one.blocks}
        self.overshoot: dict[int, tuple[float, float]] = {}
        self.scratch = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
        self.writers: dict[str, Callable[[dict, Region, str, list[InkRecord], DrawnBlock, bool], str]] = {
            "text": self.text,
            "block": self.text,
            "banner": self.banner,
            "pair": self.pair,
            "rule": self.rule,
            "spacer": self.spacer,
            "split": self.split,
            "stack": self.stack,
            "panel": self.panel,
            "table": self.table,
            "logo": self.logo,
            "barcode": self.barcode,
            "qrcode": self.qrcode,
        }

    # --- units and styles -----------------------------------------------------------------------------

    def pt(self, pixels: float) -> str:
        """Return a stage-one pixel length in points, as Typst writes it."""
        return f"{pixels * self.scale:.4f}pt"

    def param(self, block: dict, key: str, block_key: str | None = None) -> Any:
        """Resolve a primitive parameter exactly as stage one's primitives do."""
        return resolve_param(
            block,
            self.layout,
            key,
            layout_id=self.layout_id,
            layout_path=self.layout_path,
            block_key=block_key,
        )

    def context(self, region: Region) -> RenderContext:
        """Return a scratch render context over `region`, for stage one's own measuring helpers."""
        return RenderContext(
            draw=self.scratch,
            entry=self.stage_one.entry,
            layout=self.layout,
            layout_id=self.layout_id,
            layout_path=self.layout_path,
            region=region,
        )

    def line_style(self, *, family: str, size: int, bold: bool, fill: object, advance: int) -> str:
        """Return the style arguments of a line box: its face, size, weight, colour, ascent and advance."""
        ascent = _ascent(family, bold, size)
        return (
            f'font: "{_font_name(family, bold)}", size: {self.pt(size)}, bold: {str(bold).lower()}, '
            f"fill: {_colour(fill)}, ascent: {self.pt(ascent)}, advance: {self.pt(advance)}"
        )

    def drawn_lines(self, path: str) -> list[_Drawn]:
        """Return every line of text stage one drew inside the block at `path`, in draw order."""
        return [line for line in self.stage_one.lines if line.path == path and line.text.strip()]

    def line(self, line: _Drawn, advance: float) -> str:
        """Write one drawn line as stage one drew it, in a line box `advance` pixels tall."""
        return (
            f"dsl-line(width: {self.pt(line.width)}, "
            f'font: "{line.face}", size: {self.pt(line.size)}, bold: {str(line.bold).lower()}, '
            f"fill: {_colour(line.fill)}, ascent: {self.pt(line.ascent)}, advance: {self.pt(advance)}, "
            f"{_string(line.text)})"
        )

    def pitched(self, lines: list[_Drawn], *, last: float, path: str) -> list[str]:
        """Write lines one under another at the pitch stage one drew them, the last `last` pixels tall."""
        written: list[str] = []
        for index, line in enumerate(lines):
            advance = lines[index + 1].y - line.y if index + 1 < len(lines) else last
            if advance <= 0:
                raise RuntimeError(
                    f"{self.layout_id}.{path}: stage one drew {line.text!r} at y={line.y} and the next line "
                    f"no lower, so the lines cannot stack"
                )
            written.append(self.line(line, advance))
        return written

    # --- the walk -------------------------------------------------------------------------------------

    def blocks(self, blocks: list, region: Region, prefix: str, *, in_block: bool) -> list[str]:
        """Write every drawn block of a list, each a block record when it opens one.

        Args:
            blocks: The block list.
            region: The region stage one drew the list in, in pixels.
            prefix: The list's path, e.g. `body` or `body[3].children[1]`.
            in_block: Whether an enclosing container already opened the block these draw into.

        Returns:
            One Typst call per drawn block, in draw order.
        """
        calls: list[str] = []
        for position, block in enumerate(blocks):
            path = f"{prefix}[{position}]"
            drawn = self.stage_one.drawn.get(path)
            if drawn is None:
                continue  # gated off by `when:`, so stage one drew nothing
            kind = str(block["type"])
            opens = not in_block and (
                kind in SCOPED_LEAVES or (kind in SCOPED_CONTAINERS and block.get("parse_block") is True)
            )
            ink = self.stage_one.ink[drawn.ink_start : drawn.ink_end]
            call = self.writers[kind](block, region, path, ink, drawn, in_block or opens)
            # A leaf is drawn at its own size and placed by its record (or, inside a block an enclosing
            # container opened, by `dsl-align`), so the record's box is where the leaf sits.
            align = _ALIGNS[str(self.param(block, "align"))] if kind in _SELF_ALIGNED else "left"
            if opens:
                call = self.record(path, call, align)
                self.note_overshoot(path, region)
            elif kind in SCOPED_LEAVES:
                call = f"dsl-align(align: {align}, {call})"
            if kind == "banner":
                call = self.banner_frame(block, path, call)
            if block.get("anchor") == "foot":
                call = f"dsl-foot({call})"
            calls.append(call)
        return calls

    def note_overshoot(self, path: str, region: Region) -> None:
        """Note how far, if at all, the lines stage one drew for a block run out of the column it was drawn in.

        A line spans its advance width from where it was drawn, as a typeset line's box spans its own, so
        the two engines' overshoots compare like with like.
        """
        block_id = self.records[path].id
        lines = self.drawn_lines(path)
        ink = [record.box for record in self.stage_one.ink if record.block == block_id and not record.mark]
        if not lines or not ink:
            return
        # Only a side the ink itself passes is allowed any overshoot (ink boxes' right edges are exclusive,
        # as is the region's), so a block stage one kept inside its column gets none.
        left = region.x - min(line.x for line in lines) if min(box[0] for box in ink) < region.x else 0.0
        passes_right = max(box[2] for box in ink) > region.right
        right = max(line.x + line.width for line in lines) - region.right if passes_right else 0.0
        left, right = max(0.0, left), max(0.0, right)
        if left or right:
            self.overshoot[block_id] = (left * self.scale, right * self.scale)

    def record(self, path: str, call: str, align: str) -> str:
        """Wrap a block's call in the `block-record` stage one opened at `path`, placed at `align`."""
        record = self.records[path]

        def value(item: object) -> str:
            return "none" if item is None else _string(item) if isinstance(item, str) else str(item)

        return (
            f'block-record(id: {record.id}, kind: "{record.kind}", path: "{record.path}", '
            f"anchor: {value(record.anchor)}, category: {value(record.parse_category)}, "
            f"order: {record.parse_order}, columns: {value(record.columns)}, join: {value(record.join)}, "
            f"align: {align},\n{call})"
        )

    def travel(self, drawn: DrawnBlock) -> str:
        """Return a gap as tall as the cursor travel of a block that drew no ink."""
        return f"dsl-spacer(height: {self.pt(drawn.y_end - drawn.y_start)})"

    # --- the primitives -------------------------------------------------------------------------------

    def text(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`text` and `block`: the lines stage one drew, one under another at the pitch it drew them."""
        lines = self.drawn_lines(path)
        if not lines:
            return self.travel(drawn)
        written = self.pitched(lines, last=drawn.y_end - lines[-1].y, path=path)
        lead = lines[0].y - drawn.y_start
        call = f"dsl-lines({_array(written)})"
        # A first line stage one left blank (it draws nothing, yet takes its pitch) opens with its gap.
        return call if lead == 0 else f"{{\ndsl-spacer(height: {self.pt(lead)})\n{call}\n}}"

    def banner(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`banner`: its one line of text, which `banner_frame` places on the bar."""
        lines = self.drawn_lines(path)
        if not lines:
            return "[]"
        return f"dsl-lines({_array([self.line(line, line.ascent + line.descent) for line in lines])})"

    def banner_frame(self, block: dict, path: str, call: str) -> str:
        """Place a banner's bar across the page from its top-left corner, and its text where stage one drew it.

        Stage one paints the bar from the page's own corner, whatever the cursor, and draws its text at the
        page's `text_y`, so neither moves the flow.
        """
        left, top = self.page_origin
        text_y = int(self.param(block, "banner_text_y", "text_y"))
        return (
            f"dsl-banner(dx: {self.pt(-left)}, dy: {self.pt(-top)}, width: {self.pt(self.page_width)}, "
            f"height: {self.pt(int(block['height']))}, fill: {_colour(block['color'])}, "
            f"text-dy: {self.pt(text_y - top)},\n{call})"
        )

    def pair(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`pair`: its label line as stage one drew it, and beside it the value lines stage one fitted."""
        label_text = interpolate(block["label"], self.stage_one.entry["fields"])
        label_text += str(self.param(block, "pair_separator", "separator"))
        lines = self.drawn_lines(path)
        if not lines:
            return self.travel(drawn)
        value_align = str(self.param(block, "pair_value_align", "value_align"))
        if block.get("budget") is None and value_align == "left":
            # Stage one drew the label and the value as one string, on one line.
            return f"dsl-lines({_array(self.pitched(lines, last=drawn.y_end - lines[0].y, path=path))})"
        label: _Drawn | None = None
        if label_text.strip():
            if lines[0].text != label_text:
                raise _refusal(
                    f"stage one drew {lines[0].text!r} first for this pair, not its label {label_text!r}.",
                    layout_path=Path(self.layout_path),
                    where=f"{self.layout_id}.{path}",
                    expected="a pair's label drawn as its first line, then its value.",
                    recover="report this: the pair primitive and the translator disagree on what a "
                    "pair draws.",
                )
            label, lines = lines[0], lines[1:]
        label_call = "none" if label is None else self.line(label, drawn.y_end - label.y)
        if not lines:
            return f"dsl-pair(value-align: {_ALIGNS[value_align]}, label: {label_call}, [])"
        values = self.pitched(lines, last=drawn.y_end - lines[-1].y, path=path)
        # Stage one draws the label and the value from one top; Typst sets the value's first line on the
        # label's baseline, so the two read as one line.
        shift = 0 if label is None else label.ascent - lines[0].ascent
        return (
            f"dsl-pair(value-align: {_ALIGNS[value_align]}, shift: {self.pt(shift)}, label: {label_call}, "
            f"dsl-lines({_array(values)}))"
        )

    def rule(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`rule`: a drawn line, or a dashed line through a line box for a rule of repeated glyphs."""
        dashed = str(self.param(block, "rule_fill_char", "fill_char")) != "none"
        advance = line_advance(self.layout, block, layout_id=self.layout_id, layout_path=self.layout_path)
        return (
            f"dsl-rule(thickness: {self.pt(int(self.param(block, 'rule_thickness', 'thickness')))}, "
            f"fill: {_colour(self.param(block, 'color'))}, "
            f"above: {self.pt(int(self.param(block, 'rule_pad_above', 'pad_above')))}, "
            f"below: {self.pt(int(self.param(block, 'rule_pad_below', 'pad_below')))}, "
            f"dashed: {str(dashed).lower()}, advance: {self.pt(advance if dashed else 0)})"
        )

    def spacer(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`spacer`: the layout's gap."""
        return f"dsl-spacer(height: {self.pt(int(self.param(block, 'spacer_height', 'height')))})"

    def split(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`split`: its columns at the widths stage one divides its region into, and its divider if any."""
        gap = int(self.param(block, "split_gap", "gap"))
        widths = block.get("widths")
        columns = block["children"]
        if widths is not None:
            regions = region.divide_widths([int(w) for w in widths], gap=gap)
        else:
            regions = region.divide(len(columns), gap=gap)
        bodies = [
            _code(self.blocks(list(children), column_region, f"{path}.children[{column}]", in_block=inside))
            for column, (children, column_region) in enumerate(zip(columns, regions, strict=True))
        ]
        widths_pt = _array([self.pt(r.width) for r in regions])
        divider = "none"
        if block.get("divider"):
            divider = f"{self.pt(1)} + {_colour(self.param(block, 'split_divider_color', 'divider_color'))}"
        return (
            f"dsl-split(widths: {widths_pt}, gap: {self.pt(gap)}, divider: {divider},\n"
            + ",\n".join(bodies)
            + ")"
        )

    def stack(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`stack`: its children one under another."""
        children = self.blocks(block["children"], region, f"{path}.children", in_block=inside)
        return f"dsl-stack({_code(children)})"

    def panel(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`panel`: its children inside a 1 px border, inset by its padding."""
        padding = int(self.param(block, "panel_padding", "padding"))
        inner = region.indent(padding, padding)
        border = _colour(self.param(block, "panel_border_color", "border_color"))
        height = self.pt(int(block["height"])) if "height" in block else "auto"
        children = _code(self.blocks(block["children"], inner, f"{path}.children", in_block=inside))
        return (
            f"dsl-panel(padding: {self.pt(padding)}, stroke: {self.pt(1)} + {border}, height: {height},\n"
            f"{children})"
        )

    def logo(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`logo`: its shape, with the initials stage one drew at the size stage one fitted them to."""
        letters = next(record.text for record in ink if record.mark)
        size = int(block["size"])
        style = str(block["style"])
        fitted = _fit_initials(letters, block, self.context(region), style, size)
        if fitted is None:  # stage one refuses such a logo before the translator runs
            raise RuntimeError(f"logo initials {letters!r} at {path} fit no size, yet stage one drew them")
        text_size = font_spec(fitted[0])[0]
        family = str(self.param(block, "family"))
        initials = (
            f'text(font: "{_font_name(family, True)}", size: {self.pt(text_size)}, weight: "bold", '
            f"fill: {_colour(block['text_color'])}, {_string(letters)})"
        )
        notch = _chevron_depth(size) if style == "chevron" else 0
        fill = _colour(self.param(block, "color"))
        return (
            f'dsl-logo(size: {self.pt(size)}, style: "{style}", fill: {fill}, '
            f"notch: {self.pt(notch)}, initials: {initials})"
        )

    def barcode(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`barcode`: stage one's module pattern as bars, and its caption beneath."""
        content = next(record.text for record in ink if record.mark)
        modules, _bars_span, caption = barcode_geometry(
            content,
            block,
            self.layout,
            self.scratch,
            layout_id=self.layout_id,
            layout_path=self.layout_path,
        )
        runs = _runs(modules)
        fill = self.param(block, "color")
        caption_call = "none"
        if caption is not None:
            size = resolve_role(self.layout, str(self.param(block, "role")))
            advance = line_advance(
                self.layout, block, layout_id=self.layout_id, layout_path=self.layout_path
            )
            style = self.line_style(
                family=str(self.param(block, "family")), size=size, bold=False, fill=fill, advance=advance
            )
            caption_call = f"dsl-line({style}, {_string(caption.text)})"
        unit = int(block["module_px"])
        return (
            f"dsl-barcode(runs: {_array([f'({start}, {length})' for start, length in runs])}, "
            f"modules: {len(modules)}, unit: {self.pt(unit)}, height: {self.pt(int(block['height']))}, "
            f"quiet: {self.pt(int(block['quiet_zone']) * unit)}, fill: {_colour(fill)}, "
            f"gap: {self.pt(int(block['caption_gap']))}, caption: {caption_call})"
        )

    def qrcode(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`qrcode`: the symbol stage one encodes for its content, as runs of dark modules, and its caption."""
        content = next(record.text for record in ink if record.mark)
        matrix = segno.make(content, error=str(block["error_level"]), micro=False, boost_error=False).matrix
        runs = [
            f"({row}, {start}, {length})"
            for row, cells in enumerate(matrix)
            for start, length in _runs("".join("1" if dark else "0" for dark in cells))
        ]
        unit = int(block["module_px"])
        fill = _colour(self.param(block, "color"))
        caption = "none"
        lines = self.drawn_lines(path)
        if lines:
            # Stage one draws the caption directly under the symbol and advances one line past it.
            caption = f"dsl-lines({_array(self.pitched(lines, last=drawn.y_end - lines[-1].y, path=path))})"
        return (
            f"dsl-qrcode(runs: {_array(runs)}, modules: {len(matrix)}, unit: {self.pt(unit)}, "
            f"quiet: {self.pt(int(block['quiet_zone']) * unit)}, fill: {fill}, caption: {caption})"
        )

    def table(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`table`: every cell a `cell-record` holding the lines stage one drew in it, placed where stage one
        drew them, over the rules and shading stage one painted, in a block as tall as stage one's table.
        """
        record = self.records[path]
        columns = block["columns"]
        header = bool(self.param(block, "table_header", "header"))
        top = drawn.y_start

        def placed(x: int, y: int) -> tuple[int, int]:
            return x - region.x, y - top

        items: list[str] = []
        for paint in self.stage_one.paints:
            if paint.path != path:
                continue
            left, upper = placed(paint.box[0], paint.box[1])
            items.append(
                f"dsl-rect(dx: {self.pt(left)}, dy: {self.pt(upper)}, "
                f"width: {self.pt(paint.box[2] - paint.box[0] + 1)}, "
                f"height: {self.pt(paint.box[3] - paint.box[1] + 1)}, fill: {_colour(paint.fill)})"
            )

        cells: dict[Cell, list[_Drawn]] = {}
        for line in self.drawn_lines(path):
            if line.cell is None:
                raise RuntimeError(
                    f"{self.layout_id}.{path}: stage one drew {line.text!r} outside any cell"
                )
            cells.setdefault(line.cell, []).append(line)
        for cell, lines in cells.items():
            if header and cell.row == 0:
                spec = {"x": columns[cell.column]["label_x"]} if "label_x" in columns[cell.column] else None
                anchor = _anchor(spec or columns[cell.column], region)
                align = str(columns[cell.column].get("label_align", columns[cell.column].get("align")))
            elif cell.span > 1:
                anchor, align = 0, "left"  # a date sub-header row, drawn from the table's left edge
            else:
                anchor, align = (
                    _anchor(columns[cell.column], region),
                    str(columns[cell.column].get("align")),
                )
            dx, width = (0, anchor) if align == "right" else (anchor, region.width - anchor)
            written = self.pitched(lines, last=lines[-1].ascent + lines[-1].descent, path=path)
            items.append(
                f"dsl-cell(block: {record.id}, row: {cell.row}, col: {cell.column}, span: {cell.span}, "
                f"dx: {self.pt(dx)}, dy: {self.pt(lines[0].y - top)}, width: {self.pt(width)}, "
                f"align: {_ALIGNS.get(align, 'left')}, {_array(written)})"
            )
        return f"dsl-table(height: {self.pt(drawn.y_end - top)},\n" + ",\n".join(items) + ")"


def _runs(bits: str) -> list[tuple[int, int]]:
    """Return each run of '1's in a module string as (start, length)."""
    runs: list[tuple[int, int]] = []
    start = None
    for index, bit in enumerate(f"{bits}0"):
        if bit == "1" and start is None:
            start = index
        elif bit != "1" and start is not None:
            runs.append((start, index - start))
            start = None
    return runs


def _anchor(spec: dict, region: Region) -> int:
    """Return a column's (or a header label's) anchor, in pixels from the region's left edge."""
    return int(spec["x"]) if "x" in spec else region.width + int(spec["x_right"])


def translate_page(
    layout: dict,
    entry: dict,
    *,
    layout_id: str,
    layout_path: Path,
    page: PageSpec,
    distractors: bool,
    config: TypesetConfig,
    overlay_dir: Path,
) -> Translation:
    """Translate one page: a layout's body, with an entry's values, as the Typst source of one page.

    Args:
        layout: The resolved layout dict.
        entry: The ground-truth entry, with its `case_id` and `fields`.
        layout_id: The layout's id.
        layout_path: The layout YAML, named in a refusal.
        page: The layout's physical page (`generators.typst.config.layout_page`).
        distractors: Whether the page draws its distractors, as stage one's distractor render does.
        config: The typesetting configuration, naming the template the page imports.
        overlay_dir: Where the page's overlay images go; the body's primitives write none.

    Returns:
        The page's source, with stage one's block records, drawn field strings and ink for it.

    Raises:
        TranslateError: The layout uses a primitive, or a primitive option, with no translation.
    """
    _refuse_untranslated(layout["body"], prefix="body", layout_id=layout_id, layout_path=layout_path)
    stage_one = _draw_stage_one(
        layout, entry, layout_id=layout_id, layout_path=layout_path, page=page, distractors=distractors
    )
    scale = page_scale(layout, page, canvas_dpi=config.canvas_dpi)
    margin = int(layout["margin"])
    # The content is centred across the page with equal left and right margins, whatever stage one's
    # canvas margins were; a roll's content keeps its symmetric stage-one margins.
    page_pixels = page.width_mm * _PT_PER_MM / scale
    left = right = (page_pixels - int(layout["content_width"])) / 2
    writer = _Writer(
        stage_one,
        layout,
        layout_id=layout_id,
        layout_path=layout_path,
        scale=scale,
        page_origin=(left, margin),
        page_width=page_pixels,
    )
    height = "auto" if page.height_mm is None else f"{page.height_mm:g}mm"
    body = writer.blocks(
        layout["body"], Region(x=margin, width=int(layout["content_width"])), "body", in_block=False
    )
    source = "\n".join(
        [
            f"// {layout_id}, {entry.get('case_id', '')}: written by generators/typst/translate.py.",
            f'#import "/{config.template}": *',
            f"#set page(width: {page.width_mm:g}mm, height: {height}, margin: (left: {writer.pt(left)}, "
            f"right: {writer.pt(right)}, top: {writer.pt(margin)}, bottom: {writer.pt(margin)}))",
            "#show: typeset",
            f"#{_code(body)}",
            "",
        ]
    )
    return Translation(
        source=source,
        blocks=stage_one.blocks,
        texts=stage_one.texts,
        ink=stage_one.ink,
        overshoot=writer.overshoot,
    )
