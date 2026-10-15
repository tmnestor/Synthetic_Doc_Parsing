"""Translate a layout tree, with one entry's values, into the Typst markup of one page.

Content is never chosen here. The page is first drawn exactly as stage one draws it: `render_body` with a
recorder, so the field providers, `when:` gating, label and currency formatting and table row providers
are the PIL engine's own. From that draw the translator takes every block it opened (stage one's
`BlockRecord`s), the string it recorded for each scored field, and, through `render_body(drawn_out=...)`,
which blocks were drawn and the lines and marks each one drew. It then walks the same layout tree and
writes one template call (`generators/typst/template.typ`) per drawn block, each block a parser finds
wrapped in a `block-record` and each table cell in a `cell-record`, so a typeset page carries the same
text as its stage-one twin.

Typesetting replaces pixel fitting: a text's string, or a pair's value, is written whole (the lines
stage one wrapped it to joined by a space) at its role's nominal size, and Typst wraps it within its
region; a table cell keeps the lines stage one drew in it, each at the size it was drawn. Every length is
a stage-one pixel value from the layout, converted at the page's own scale (`page_scale`).

Overlays (the marks a distractor page draws over the finished body) are not yet placed: a page carries
its body's blocks only.
"""

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import segno
from PIL import Image, ImageColor, ImageDraw, ImageFont

from generators.common import font_path, font_spec
from generators.distractors.paths import REPO_ROOT
from generators.exporters.geometry import BlockRecord, BoxRecorder, InkRecord
from generators.layout_budgets import field_budget
from generators.layout_dsl.binding import interpolate
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.engine import SCOPED_CONTAINERS, SCOPED_LEAVES, DrawnBlock, render_body
from generators.layout_dsl.primitives_codes import _chevron_depth, _fit_initials, barcode_geometry
from generators.layout_dsl.primitives_table import _resolve_row_height
from generators.layout_dsl.primitives_text import line_advance, resolve_role
from generators.loader import load_layout_registry
from generators.typst.config import PageSpec, TypesetConfig

# The primitive types the translator writes; a layout using any other is refused.
TRANSLATED: frozenset[str] = frozenset(
    {"barcode", "logo", "panel", "pair", "qrcode", "rule", "spacer", "split", "stack", "table", "text"}
)

# The gate layouts of the plan's Decision 9, each with the file that holds it.
GATE_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("config/layouts/invoices.yml", "tax_invoice_standard"),
    ("config/layouts/receipts.yml", "receipt_thermal_80mm"),
    ("config/layouts/bank_statements.yml", "westpac_standard"),
)

# Options a translated primitive may carry, by type, with the values the translator typesets. A block
# carrying any other value is refused rather than drawn some other way. An absent key reads as None.
SUPPORTED_OPTIONS: dict[str, dict[str, tuple[object, ...]]] = {
    "table": {"grouping": ("none",), "frame": ("plain", "filled", "bordered")},
    "split": {"divider": (None, False)},
    "qrcode": {"caption": (None,)},
}

_PT_PER_MM = 72 / 25.4
_ALIGNS = {"left": "left", "center": "center", "right": "right"}


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
    """

    source: str
    blocks: list[BlockRecord]
    texts: dict[str, str]


@dataclass(frozen=True)
class _StageOne:
    """What stage one's engine drew for the page: its records, its ink and each drawn block's share."""

    blocks: list[BlockRecord]
    texts: dict[str, str]
    ink: list[InkRecord]
    drawn: dict[str, DrawnBlock]
    entry: dict


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


def page_scale(layout: dict, page: PageSpec) -> float:
    """Return the points per stage-one pixel of a layout typeset on its physical page.

    A sheet's pixels are its `page_dimensions.width` across the page's width; a roll's are its `width`.

    Args:
        layout: The resolved layout dict.
        page: The layout's physical page.

    Returns:
        Points per pixel.
    """
    return page.width_mm * _PT_PER_MM / _pixel_width(layout, page)


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
    recorder = BoxRecorder(width, height)
    enriched: dict = {}
    drawn: dict[str, DrawnBlock] = {}
    render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=str(layout_path),
        draw=ImageDraw.Draw(Image.new("RGB", (width, height), "white")),
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        distractors=distractors,
        recorder=recorder,
        entry_out=enriched,
        drawn_out=drawn,
    )
    return _StageOne(recorder.block_records(), recorder.texts(), recorder.ink_records(), drawn, enriched)


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
    """Return a layout colour (a name or hex string) as a Typst `rgb(...)` call."""
    return f"rgb{tuple(ImageColor.getrgb(str(value)))}"


def _array(items: list[str]) -> str:
    """Return a Typst array of already-written items; a one-item array keeps its trailing comma."""
    return f"({', '.join(items)},)" if items else "()"


def _code(calls: list[str]) -> str:
    """Return a Typst code block joining `calls`' content, or empty content for none."""
    return "{\n" + "\n".join(calls) + "\n}" if calls else "[]"


class _Writer:
    """Write one page's body as template calls, reading each drawn block's content from stage one."""

    def __init__(
        self, stage_one: _StageOne, layout: dict, *, layout_id: str, layout_path: Path, scale: float
    ) -> None:
        self.stage_one = stage_one
        self.layout = layout
        self.layout_id = layout_id
        self.layout_path = str(layout_path)
        self.scale = scale
        self.records = {record.path: record for record in stage_one.blocks}
        self.scratch = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
        self.writers: dict[str, Callable[[dict, Region, str, list[InkRecord], DrawnBlock, bool], str]] = {
            "text": self.text,
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

    def block_style(self, block: dict, *, bold: bool) -> str:
        """Return a text-bearing block's line style at its role's nominal size."""
        size = resolve_role(self.layout, str(self.param(block, "role")))
        advance = line_advance(self.layout, block, layout_id=self.layout_id, layout_path=self.layout_path)
        return self.line_style(
            family=str(self.param(block, "family")),
            size=size,
            bold=bold,
            fill=self.param(block, "color"),
            advance=advance,
        )

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
            if opens:
                call = self.record(path, call)
            if block.get("anchor") == "foot":
                call = f"dsl-foot({call})"
            calls.append(call)
        return calls

    def record(self, path: str, call: str) -> str:
        """Wrap a block's call in the `block-record` of the block stage one opened at `path`."""
        record = self.records[path]

        def value(item: object) -> str:
            return "none" if item is None else _string(item) if isinstance(item, str) else str(item)

        return (
            f'block-record(id: {record.id}, kind: "{record.kind}", path: "{record.path}", '
            f"anchor: {value(record.anchor)}, category: {value(record.parse_category)}, "
            f"order: {record.parse_order}, columns: {value(record.columns)}, join: {value(record.join)},\n"
            f"{call})"
        )

    def travel(self, drawn: DrawnBlock) -> str:
        """Return a gap as tall as the cursor travel of a block that drew no ink."""
        return f"dsl-spacer(height: {self.pt(drawn.y_end - drawn.y_start)})"

    # --- the primitives -------------------------------------------------------------------------------

    def text(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`text`: the one string stage one drew, its wrapped lines joined, at its role's size."""
        lines = [record.text for record in ink]
        if not lines:
            return self.travel(drawn)
        align = _ALIGNS[str(self.param(block, "align"))]
        style = self.block_style(block, bold=bool(self.param(block, "bold")))
        return f"dsl-text(align: {align}, {style}, {_string(' '.join(lines))})"

    def pair(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`pair`: its label as stage one composes it, and the value stage one drew after it."""
        label = interpolate(block["label"], self.stage_one.entry["fields"])
        label += str(self.param(block, "pair_separator", "separator"))
        lines = [record.text for record in ink]
        if label.strip():
            if not lines or lines[0] != label:
                raise _refusal(
                    f"stage one drew {lines[:1]} first for this pair, not its label {label!r}.",
                    layout_path=Path(self.layout_path),
                    where=f"{self.layout_id}.{path}",
                    expected="a pair's label drawn as its first line, then its value.",
                    recover="report this: the pair primitive and the translator disagree on what a "
                    "pair draws.",
                )
            lines = lines[1:]
        if not lines and not label.strip():
            return self.travel(drawn)
        value_align = _ALIGNS[str(self.param(block, "pair_value_align", "value_align"))]
        style = self.block_style(block, bold=bool(self.param(block, "bold")))
        value = _string(" ".join(lines))
        return f"dsl-pair(value-align: {value_align}, label: {_string(label)}, {style}, {value})"

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
        """`split`: its columns at the widths stage one divides its region into."""
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
        return f"dsl-split(widths: {widths_pt}, gap: {self.pt(gap)},\n" + ",\n".join(bodies) + ")"

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
        align = _ALIGNS[str(self.param(block, "align"))]
        return (
            f'dsl-logo(size: {self.pt(size)}, style: "{style}", fill: {fill}, align: {align}, '
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
        align = _ALIGNS[str(self.param(block, "align"))]
        return (
            f"dsl-barcode(runs: {_array([f'({start}, {length})' for start, length in runs])}, "
            f"modules: {len(modules)}, unit: {self.pt(unit)}, height: {self.pt(int(block['height']))}, "
            f"quiet: {self.pt(int(block['quiet_zone']) * unit)}, fill: {_colour(fill)}, align: {align}, "
            f"gap: {self.pt(int(block['caption_gap']))}, caption: {caption_call})"
        )

    def qrcode(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`qrcode`: the symbol stage one encodes for its content, as runs of dark modules."""
        content = next(record.text for record in ink if record.mark)
        matrix = segno.make(content, error=str(block["error_level"]), micro=False, boost_error=False).matrix
        runs = [
            f"({row}, {start}, {length})"
            for row, cells in enumerate(matrix)
            for start, length in _runs("".join("1" if dark else "0" for dark in cells))
        ]
        unit = int(block["module_px"])
        fill = _colour(self.param(block, "color"))
        align = _ALIGNS[str(self.param(block, "align"))]
        return (
            f"dsl-qrcode(runs: {_array(runs)}, modules: {len(matrix)}, unit: {self.pt(unit)}, "
            f"quiet: {self.pt(int(block['quiet_zone']) * unit)}, fill: {fill}, align: {align})"
        )

    def table(
        self, block: dict, region: Region, path: str, ink: list[InkRecord], drawn: DrawnBlock, inside: bool
    ) -> str:
        """`table`: a grid whose every cell is a `cell-record` holding the lines stage one drew in it."""
        record = self.records[path]
        columns = block["columns"]
        bounds = _column_bounds(
            columns,
            self.param(block, "table_dividers", "dividers"),
            region,
            lambda name: int(
                field_budget(self.layout, self.layout_id, name, layout_path=self.layout_path)["width"]
            ),
        )
        frame = str(block["frame"])
        header = bool(self.param(block, "table_header", "header"))
        advance = line_advance(self.layout, block, layout_id=self.layout_id, layout_path=self.layout_path)
        row_height = _resolve_row_height(block, self.context(region))
        row_inset = int(self.param(block, "table_row_inset_y", "row_inset_y"))
        header_height = int(block["header_height"]) if "header_height" in block else advance
        family = str(self.param(block, "family"))

        cells: dict[tuple[int, int], list[InkRecord]] = {}
        spans: dict[int, int] = {}
        for line in ink:
            if line.cell is not None:
                cells.setdefault((line.cell.row, line.cell.column), []).append(line)
                if line.cell.span > 1:
                    spans[line.cell.row] = line.cell.span
        rows = max((row for row, _ in cells), default=-1) + 1

        written: list[str] = []
        for row in range(rows):
            for col in range(len(columns) if row not in spans else 1):
                lines = cells.get((row, col), [])
                if row in spans:
                    left, right, top = 0, 0, 0
                    bottom = max(0, row_height - advance)
                    align = "left"
                elif header and row == 0:
                    left, right, align = _label_insets(columns[col], bounds[col], region)
                    count = len(str(columns[col]["label"]).split("\n"))
                    if block.get("label_inset_y") is not None:
                        top = int(block["label_inset_y"])
                    elif frame in ("bordered", "filled"):
                        top = max(0, (header_height - advance * count) // 2)
                    else:
                        top = 0
                    bottom = max(0, header_height - top - advance * count)
                else:
                    left, right, align = _cell_insets(columns[col], bounds[col], region)
                    top = row_inset
                    bottom = max(0, row_height - row_inset - advance)
                styled = [
                    "dsl-line("
                    + self.line_style(
                        family=family, size=line.size, bold=line.bold, fill="black", advance=advance
                    )
                    + f", {_string(line.text)})"
                    for line in lines
                ]
                inset = (
                    f"(left: {self.pt(left)}, right: {self.pt(right)}, top: {self.pt(top)}, "
                    f"bottom: {self.pt(bottom)})"
                )
                written.append(
                    f"dsl-cell(block: {record.id}, row: {row}, col: {col}, "
                    f"span: {spans.get(row, 1)}, inset: {inset}, align: {_ALIGNS[align]}, {_array(styled)})"
                )
        fill = _colour(block["fill_color"]) if block.get("fill_color") is not None else "none"
        widths = _array([self.pt(end - start) for start, end in bounds])
        return (
            f'dsl-table(widths: {widths}, frame: "{frame}", fill: {fill}, stroke: {self.pt(1)} + black, '
            f"header: {str(header).lower()},\n" + ",\n".join(written) + ")"
        )


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
    """Return a column's (or divider's) anchor, in pixels from the region's left edge."""
    return int(spec["x"]) if "x" in spec else region.width + int(spec["x_right"])


def _column_bounds(
    columns: list, dividers: object, region: Region, budget_width: Callable[[str], int]
) -> list[tuple[int, int]]:
    """Return each column's (start, end), in pixels from the region's left edge.

    The table's dividers bound its columns when there is one between each pair. Otherwise a column
    starts at its own anchor when it is left-aligned and ends at it when it is right-aligned; between a
    left-aligned column and a right-aligned one, the boundary is the right column's header `label_x`, else
    the left column's anchor plus its fit budget's width (`budget_width`), else the midpoint of the two
    anchors.
    """
    anchors = [_anchor(column, region) for column in columns]
    if isinstance(dividers, list) and len(dividers) == len(columns) - 1:
        cuts = [_anchor(divider, region) for divider in dividers]
    else:
        cuts = []
        for index in range(len(columns) - 1):
            this, after = columns[index], columns[index + 1]
            if after.get("align") != "right":
                cuts.append(anchors[index + 1])
            elif this.get("align") == "right":
                cuts.append(anchors[index])
            elif "label_x" in after:
                cuts.append(int(after["label_x"]))
            elif "budget" in this:
                cuts.append(min(anchors[index] + budget_width(str(this["budget"])), anchors[index + 1]))
            else:
                cuts.append((anchors[index] + anchors[index + 1]) // 2)
    edges = [0, *cuts, region.width]
    return list(zip(edges, edges[1:], strict=False))


def _cell_insets(column: dict, bound: tuple[int, int], region: Region) -> tuple[int, int, str]:
    """Return a data cell's left and right insets within its column, and its alignment."""
    anchor = _anchor(column, region)
    if column.get("align") == "right":
        return 0, max(0, bound[1] - anchor), "right"
    return max(0, anchor - bound[0]), 0, "left"


def _label_insets(column: dict, bound: tuple[int, int], region: Region) -> tuple[int, int, str]:
    """Return a header label's insets and alignment: at its own `label_x` when it declares one."""
    spec = {"x": column["label_x"]} if "label_x" in column else column
    align = str(column.get("label_align", column.get("align")))
    anchor = _anchor(spec, region)
    if align == "right":
        return 0, max(0, bound[1] - anchor), "right"
    return max(0, anchor - bound[0]), 0, "left"


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
        The page's source, with stage one's block records and drawn field strings for it.

    Raises:
        TranslateError: The layout uses a primitive, or a primitive option, with no translation.
    """
    _refuse_untranslated(layout["body"], prefix="body", layout_id=layout_id, layout_path=layout_path)
    stage_one = _draw_stage_one(
        layout, entry, layout_id=layout_id, layout_path=layout_path, page=page, distractors=distractors
    )
    scale = page_scale(layout, page)
    writer = _Writer(stage_one, layout, layout_id=layout_id, layout_path=layout_path, scale=scale)
    margin = int(layout["margin"])
    right = _pixel_width(layout, page) - margin - int(layout["content_width"])
    height = "auto" if page.height_mm is None else f"{page.height_mm:g}mm"
    body = writer.blocks(
        layout["body"], Region(x=margin, width=int(layout["content_width"])), "body", in_block=False
    )
    source = "\n".join(
        [
            f"// {layout_id}, {entry.get('case_id', '')}: written by generators/typst/translate.py.",
            f'#import "/{config.template}": *',
            f"#set page(width: {page.width_mm:g}mm, height: {height}, margin: (left: {writer.pt(margin)}, "
            f"right: {writer.pt(right)}, top: {writer.pt(margin)}, bottom: {writer.pt(margin)}))",
            "#show: typeset",
            f"#{_code(body)}",
            "",
        ]
    )
    return Translation(source=source, blocks=stage_one.blocks, texts=stage_one.texts)
