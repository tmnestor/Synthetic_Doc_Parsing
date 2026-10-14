"""Write one OmniDocBench page from a render's draw-time records, and check the invariants it keeps.

A render's `geometry_out` carries every line of ink it drew, each naming its block and (inside a table)
its cell, and every block it opened. `build_page` turns those into one OmniDocBench page: a `layout_dets`
entry per block that drew ink, its `poly` the union of its ink boxes, its category by the precedence of
Decision 5, its reading order by Decision 6, and its `text` (Decision 3) or, for a table, its `html`
(Decision 7), with the rows of any table joined to it (Decision 17). `check_page` checks the six
invariants of the design's section 5.1 against the records the page was built from, and `map_polys`
re-projects every `poly` onto a new frame (a degraded photograph).

OmniDocBench's own field names (`layout_dets`, `poly`, `category_type`, `page_info`, ...) are contract
constants, not configuration.
"""

import copy
import html
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from generators.exporters.geometry import BlockRecord, InkRecord
from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig

# Decision 9: the corpus is printed in English.
LANGUAGE = "en"
# The prefix of an overlay site's block kind, `overlay:<type>`.
OVERLAY_PREFIX = "overlay:"
_CELL = re.compile(r"<td[^>]*>(.*?)</td>")

# The evaluator's text normalisation (src/core/preprocess/data_preprocess.py at the pinned commit:
# `normalized_text`, which is `clean_string(textblock2unicode(text))`), mirrored for invariant 6.
# `replace_textcircle` maps circled digits and letters, and `\textcircled{...}`, to the character inside.
_CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(20)}
_CIRCLED.update({chr(0x24D0 + i): chr(ord("a") + i) for i in range(26)})
_CIRCLED.update({chr(0x24B6 + i): chr(ord("A") + i) for i in range(26)})
_TEXTCIRCLED = re.compile(r"\\textcircled\s*\{\s*([^{}]+?)\s*\}")
# `textblock2unicode` rewrites an inline formula span (`inline_reg`) whose content holds a LaTeX marker.
_INLINE = re.compile(r"\$(.*?)\$|\\\((.*?)\\\)")
_LATEX_ESCAPE = re.compile(r"\\([\\_&%^])")
# `clean_string` keeps word characters and CJK ideographs only.
_NOT_KEPT = re.compile(r"[^\w\u4e00-\u9fff]")
_CONTROL_SPELLINGS = ("\\t", "\\n", "\t", "\n", "/t", "/n")


class PageContext(NamedTuple):
    """What a page is, beyond what its render records: its file and the slices it is scored under.

    Attributes:
        image_path: The page image's file name.
        doc_type: The document type drawn (`receipt`, `invoice`, `bank_statement`).
        layout_id: The layout drawn.
        half: The eval-set half the page belongs to.
        tier: The degradation tier, `none` for a clean half.
        layout_scored: Whether the page counts for layout detection (Decision 11).
    """

    image_path: str
    doc_type: str
    layout_id: str
    half: str
    tier: str
    layout_scored: bool


def page_where(case_id: str, layout_id: str, mode: str) -> str:
    """The render a diagnostic names: `CASE_ID / layout_id (mode)`."""
    return f"{case_id} / {layout_id} ({mode})"


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> str:
    """Word a four-part diagnostic, each part on a labelled line."""
    return (
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _collapse(text: str) -> str:
    """Collapse every run of whitespace to one space, and trim the ends."""
    return " ".join(text.split())


def _category(block: BlockRecord, config: ParsingConfig) -> str | None:
    """Return a block's category by Decision 5's precedence, or None when nothing maps it."""
    if block.parse_category is not None:
        return block.parse_category
    if block.anchor is not None and block.anchor in config.anchors:
        return config.anchors[block.anchor]
    if block.kind.startswith(OVERLAY_PREFIX):
        return config.overlays.get(block.kind.removeprefix(OVERLAY_PREFIX))
    return config.primitives.get(block.kind)


def _unmapped(block: BlockRecord, where: str) -> str:
    """Word the refusal for a block whose kind no category map covers."""
    overlay = block.kind.startswith(OVERLAY_PREFIX)
    section = "overlays" if overlay else "primitives"
    name = block.kind.removeprefix(OVERLAY_PREFIX) if overlay else block.kind
    return _diagnostic(
        "A block has no OmniDocBench category.",
        what=f"block {block.id} at {block.path} is of kind {block.kind!r}, which "
        f"category_map.{section} does not map, and it declares no parse_category.",
        where=f"{where}, block {block.path}; {PARSING_CONFIG_PATH} -> category_map.{section}.{name}",
        expected=f"every drawn kind mapped to a category_vocabulary value, e.g. category_map.{section}: "
        f"{{{name}: text_block, ...}}",
        recover=f"add {name!r} to category_map.{section} in config/parsing_config.yml, or give the block a "
        "parse_category.",
    )


def _poly(boxes: list[tuple[int, int, int, int]]) -> list[int]:
    """Return the union of `boxes` as four corners clockwise from top-left: x1, y1, ..., x4, y4."""
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return [left, top, right, top, right, bottom, left, bottom]


class _Grid(NamedTuple):
    """A table block's drawn cells.

    Attributes:
        rows: Row to column to the cell's lines, in draw order.
        spans: Row to span, for a row whose one cell spans the table (a date sub-header).
        extents: (row, column) to the cell's ink x-extent, (left, right).
    """

    rows: dict[int, dict[int, list[str]]]
    spans: dict[int, int]
    extents: dict[tuple[int, int], tuple[int, int]]


def _grid(block: BlockRecord, ink: list[InkRecord]) -> _Grid:
    """Gather a table block's lines by cell; a mark, or a line with no cell in the columns, is left out."""
    n = block.columns or 0
    grid = _Grid({}, {}, {})
    for record in ink:
        if record.block != block.id or record.mark or record.cell is None:
            continue
        cell = record.cell
        if not 0 <= cell.column < n:
            continue
        grid.rows.setdefault(cell.row, {}).setdefault(cell.column, []).append(record.text)
        if cell.span > 1:
            grid.spans[cell.row] = cell.span
        left, right = grid.extents.get((cell.row, cell.column), (record.box[0], record.box[2]))
        grid.extents[(cell.row, cell.column)] = (min(left, record.box[0]), max(right, record.box[2]))
    return grid


def _column_edges(grid: _Grid) -> dict[int, tuple[int, int]]:
    """Each column's horizontal span: the union of its cells' ink x-extents, spanning rows aside."""
    edges: dict[int, tuple[int, int]] = {}
    for (row, column), (left, right) in grid.extents.items():
        if row in grid.spans:
            continue
        low, high = edges.get(column, (left, right))
        edges[column] = (min(low, left), max(high, right))
    return edges


def _placed(joined: _Grid, edges: dict[int, tuple[int, int]], n: int) -> _Grid:
    """Re-grid a joined table's cells onto the target's columns.

    Each cell goes to the target column whose horizontal span best overlaps the cell's ink x-extent (ties,
    and cells overlapping no column, go to the column whose centre is nearest); two cells landing in one
    column are joined in their own column order. A spanning row spans the target's N columns.
    """

    def column_for(extent: tuple[int, int]) -> int:
        left, right = extent

        def fit(column: int) -> tuple[int, int]:
            low, high = edges[column]
            return max(0, min(right, high) - max(left, low)), -abs((left + right) - (low + high))

        return max(sorted(edges), key=fit)

    placed = _Grid({}, {row: n for row in joined.spans}, {})
    for row, cells in joined.rows.items():
        for column, lines in sorted(cells.items()):
            target = 0 if row in joined.spans else column_for(joined.extents[(row, column)])
            placed.rows.setdefault(row, {}).setdefault(target, []).extend(lines)
    return placed


def _rows_html(grid: _Grid, n: int) -> list[str]:
    """Write a grid's rows, 0 to its last drawn row, as `<tr>` of N `<td>` or one spanning `<td>`."""
    parts = []
    for row in range(max(grid.rows, default=-1) + 1):
        cells = grid.rows.get(row, {})
        parts.append("<tr>")
        if row in grid.spans:
            text = " ".join(" ".join(lines) for _, lines in sorted(cells.items()))
            parts.append(f'<td colspan="{grid.spans[row]}">{html.escape(text, quote=False)}</td>')
        else:
            for column in range(n):
                text = " ".join(cells.get(column, []))
                parts.append(f"<td>{html.escape(text, quote=False)}</td>")
        parts.append("</tr>")
    return parts


def table_html(block: BlockRecord, ink: list[InkRecord], joined: Sequence[BlockRecord] = ()) -> str:
    """Write a table block's grid as HTML (Decision 7), and the rows of any table joined to it.

    Every row the table drew is one `<tr>` of `<td>`; no `<thead>` or `<th>`. A cell's text is its lines
    joined by one space in draw order (a two-line header label, a sub-line after its main text). A row
    whose one cell spans the table (a date sub-header) is one `<td colspan="N">`; every other row has N
    cells, a cell nothing was drawn in being empty. A code mark's payload is not text and is left out.
    Ink without a cell, or in a cell outside the declared columns, has no place in the grid and is left
    out too: `check_page` refuses it.

    A joined table's rows (Decision 17) follow, in draw order, each still N cells wide: each of its cells
    sits in this table's column whose horizontal span (the union of the ink x-extents of that column's
    cells) best overlaps the cell's own ink.

    Args:
        block: The table block, carrying its declared column count.
        ink: The page's ink records; only this block's, and the joined blocks', are read.
        joined: The tables whose rows this one takes (`parse_join: previous_table`), in draw order.

    Returns:
        The table as one line of HTML, its text escaped.
    """
    n = block.columns or 0
    grid = _grid(block, ink)
    edges = _column_edges(grid)
    parts = ["<table>", *_rows_html(grid, n)]
    for extra in joined:
        parts.extend(_rows_html(_placed(_grid(extra, ink), edges, n), n))
    parts.append("</table>")
    return "".join(parts)


def _join_targets(
    blocks: list[BlockRecord], owned: dict[int, list[InkRecord]], where: str
) -> dict[int, int]:
    """Map each inked joined table to the table that takes its rows (Decision 17).

    The target is the nearest table drawn before it that is written: inkless tables are passed over, and a
    joined table's own target serves the next join, so a chain of joins appends to one table.

    Raises:
        ValueError: A joined table has no written table before it, or one whose columns drew nothing.
    """
    targets: dict[int, int] = {}
    last: int | None = None
    for block in blocks:
        if block.kind != "table" or not owned.get(block.id):
            continue
        if block.join is None:
            last = block.id
            continue
        if last is None or not _column_edges(_grid(blocks[last], owned[last])):
            raise ValueError(
                _diagnostic(
                    "A joined table has no table to join.",
                    what=f"table block {block.id} at {block.path} sets parse_join: {block.join}, but no "
                    "table with drawn columns was written before it on this page, so its rows have nowhere "
                    "to go.",
                    where=f"{where}, block {block.path} -> parse_join",
                    expected="a table drawn, with ink in its columns, before the joined one, e.g. the "
                    "transaction table above a one-row totals table.",
                    recover="remove parse_join from the table in its layout YAML, or move the table after "
                    "the table its rows belong to.",
                )
            )
        targets[block.id] = last
    return targets


def build_page(geometry: dict, *, context: PageContext, config: ParsingConfig) -> dict:
    """Build one OmniDocBench page from a render's `geometry_out`.

    One `layout_dets` entry per block that drew ink, in draw order (Decision 4: a block with none is
    left out), except a table joined to the table before it (Decision 17), whose rows and ink are that
    table's. `anno_id` is the block's draw sequence and `order` its 1-based position after a stable sort
    by `(parse_order, draw sequence)` (Decision 6). A table carries `html`; a block in a textless category
    carries nothing; every other block carries `text`, its non-mark lines joined by one space.

    Args:
        geometry: The render's `geometry_out`, carrying `width`, `height`, `ink` and `blocks`.
        context: The page's file and attributes.
        config: The validated parsing configuration.

    Returns:
        The page as OmniDocBench's JSON structure.

    Raises:
        ValueError: A block that drew ink has a kind no category map covers and no `parse_category`, or
            a joined table has no table written before it.
    """
    ink: list[InkRecord] = geometry["ink"]
    blocks: list[BlockRecord] = geometry["blocks"]
    owned: dict[int, list[InkRecord]] = {}
    for record in ink:
        if record.block is not None:
            owned.setdefault(record.block, []).append(record)
    where = page_where(Path(context.image_path).stem, context.layout_id, context.half)
    targets = _join_targets(blocks, owned, where)
    joined: dict[int, list[BlockRecord]] = {}
    for block_id, target in targets.items():
        joined.setdefault(target, []).append(blocks[block_id])
    inked = [block for block in blocks if owned.get(block.id) and block.id not in targets]
    ranked = sorted(inked, key=lambda block: (block.parse_order, block.id))
    # 1-based, as OmniDocBench numbers its own pages: its matcher reads an order of 0 as no order at all.
    order = {block.id: position for position, block in enumerate(ranked, start=1)}

    dets = []
    for block in inked:
        category = _category(block, config)
        if category is None:
            raise ValueError(_unmapped(block, where))
        records = owned[block.id] + [r for extra in joined.get(block.id, []) for r in owned[extra.id]]
        det: dict = {
            "category_type": category,
            "poly": _poly([record.box for record in records]),
            "ignore": False,
            "order": order[block.id],
            "anno_id": block.id,
            "attribute": {},
            "line_with_spans": [],
        }
        if block.kind == "table":
            det["html"] = table_html(block, ink, joined.get(block.id, []))
        elif category not in config.textless_categories:
            det["text"] = " ".join(record.text for record in records if not record.mark)
        dets.append(det)

    return {
        "layout_dets": dets,
        "page_info": {
            "page_no": 0,
            "height": geometry["height"],
            "width": geometry["width"],
            "image_path": context.image_path,
            "page_attribute": {
                "language": LANGUAGE,
                "doc_type": context.doc_type,
                "layout_id": context.layout_id,
                "half": context.half,
                "tier": context.tier,
                "layout_scored": context.layout_scored,
            },
        },
        "extra": {"relation": []},
    }


def map_polys(
    page: dict, transform: Callable[[list[float]], list[float]], *, width: int, height: int
) -> dict:
    """Return a copy of `page` with every `poly` passed through `transform`, on a page of the new size.

    Args:
        page: A page `build_page` wrote.
        transform: Maps one `poly` (eight numbers, four corners) to its corners on the new frame.
        width: The new frame's width in pixels.
        height: The new frame's height in pixels.

    Returns:
        The re-projected page; `page` itself is left unchanged.
    """
    mapped = copy.deepcopy(page)
    for det in mapped["layout_dets"]:
        det["poly"] = transform(det["poly"])
    mapped["page_info"]["width"] = width
    mapped["page_info"]["height"] = height
    return mapped


def check_page(page: dict, geometry: dict, *, where: str, config: ParsingConfig) -> list[str]:
    """Check the six invariants of the design's section 5.1 on one page.

    1. Every ink record belongs to exactly one recorded block.
    2. (Decision 8's form.) Every line in a table block carries a cell inside the declared columns.
    3. Every scored value (`geometry["texts"]`, whitespace collapsed) is found in some block's `text` or
       some table cell's text, collapsed the same way.
    4. Every `poly` lies within the page.
    5. Every block that drew ink has a category, and every `category_type` is in the vocabulary.
    6. No block in one of the evaluator's ignored categories has text within the evaluator's ignored-match
       distance of a scored block's text, so the evaluator cannot drop a correct reading of the scored
       block as a match of the ignored one.

    Args:
        page: The page `build_page` (or `map_polys`) wrote from `geometry`.
        geometry: The render's `geometry_out`, carrying `ink`, `blocks` and `texts`.
        where: The render checked, `CASE_ID / layout_id (mode)`.
        config: The validated parsing configuration.

    Returns:
        One four-part diagnostic per broken invariant instance; empty when the page keeps them all.
    """
    blocks: dict[int, BlockRecord] = {block.id: block for block in geometry["blocks"]}
    ink: list[InkRecord] = geometry["ink"]
    errors: list[str] = []
    errors.extend(_ownership_errors(ink, blocks, where))
    errors.extend(_cell_errors(ink, blocks, where))
    errors.extend(_text_errors(page, geometry["texts"], where))
    errors.extend(_poly_errors(page, blocks, where))
    errors.extend(_category_errors(page, ink, blocks, where, config))
    errors.extend(_ignored_match_errors(page, blocks, where, config))
    return errors


class PageCheck:
    """Build and check the page of each render handed to it, collecting refusals.

    An instance is a `measure` callback for `check_overflow`, so `validate` checks the very render the
    overflow pass made instead of drawing any page again.

    Attributes:
        errors: Every refusal, in the order the renders were measured.
    """

    def __init__(
        self,
        config: ParsingConfig,
        *,
        doc_type: str,
        mode: str,
        then: Callable[[str, str, dict], None] | None,
    ) -> None:
        """Hold what each page needs.

        Args:
            config: The validated parsing configuration.
            doc_type: The extraction-schema document type the renders draw.
            mode: The render checked, `plain` or `distractor`; it is also the page's half.
            then: Another measure handed the same render first (the drawn-field guard or the density
                bounds), or None.
        """
        self.config = config
        self.doc_type = doc_type
        self.mode = mode
        self.then = then
        self.errors: list[str] = []

    def __call__(self, case_id: str, layout_id: str, geometry: dict) -> None:
        """Hand the render to `then`, then build its page and record every broken invariant.

        Args:
            case_id: The case rendered.
            layout_id: Its layout.
            geometry: The render's `geometry_out`.
        """
        if self.then is not None:
            self.then(case_id, layout_id, geometry)
        where = page_where(case_id, layout_id, self.mode)
        context = PageContext(f"{case_id}.png", self.doc_type, layout_id, self.mode, "none", True)
        try:
            page = build_page(geometry, context=context, config=self.config)
        except ValueError as exc:
            self.errors.append(str(exc))
            return
        self.errors.extend(check_page(page, geometry, where=where, config=self.config))


def _ownership_errors(ink: list[InkRecord], blocks: dict[int, BlockRecord], where: str) -> list[str]:
    """Invariant 1: no line is drawn outside a recorded block."""
    errors = []
    for record in ink:
        if record.block in blocks:
            continue
        owner = "no block" if record.block is None else f"block {record.block}, which was never opened"
        errors.append(
            _diagnostic(
                "A line of ink belongs to no block.",
                what=f"the line {record.text!r} at {record.box} was drawn in {owner}, so the page's text "
                "loses it.",
                where=f"{where}: the drawing path that drew {record.text!r}",
                expected="every line drawn inside a block scope: the layout walker opens one around each "
                "leaf primitive and parse_block container, the overlay engine one around each site.",
                recover="draw the line through render_blocks or the overlay engine, or open "
                "BoxRecorder.block_scope around the path that draws it.",
            )
        )
    return errors


def _cell_errors(ink: list[InkRecord], blocks: dict[int, BlockRecord], where: str) -> list[str]:
    """Invariant 2 (Decision 8): every line in a table block names a cell inside its declared columns."""
    errors = []
    for record in ink:
        block = blocks.get(record.block) if record.block is not None else None
        if block is None or block.kind != "table":
            continue
        columns = block.columns or 0
        cell = record.cell
        if cell is None:
            what = (
                f"the line {record.text!r} at {record.box} sits in table block {block.path} but names "
                "no cell."
            )
        elif cell.span < 1 or cell.column < 0 or cell.column + cell.span > columns:
            what = (
                f"the line {record.text!r} names cell {tuple(cell)} (row, column, span), outside the "
                f"table's declared {columns} columns."
            )
        else:
            continue
        errors.append(
            _diagnostic(
                "A table line has no place in the table's grid.",
                what=what,
                where=f"{where}, block {block.path}",
                expected=f"every line a table draws marked with Cell(row, column, span), 0 <= column and "
                f"column + span <= {columns}, e.g. Cell(2, 1, 1).",
                recover="mark the cell with set_cell before drawing the line in primitives_table.py, and "
                "declare the table's column count with set_table_columns.",
            )
        )
    return errors


def _text_errors(page: dict, texts: dict[str, str], where: str) -> list[str]:
    """Invariant 3: every scored value is found in some block's text or some table cell's text."""
    haystacks: list[str] = []
    for det in page["layout_dets"]:
        if "text" in det:
            haystacks.append(_collapse(det["text"]))
        if "html" in det:
            haystacks.extend(_collapse(html.unescape(cell)) for cell in _CELL.findall(det["html"]))
    errors = []
    for field, value in texts.items():
        wanted = _collapse(value)
        if not wanted or any(wanted in haystack for haystack in haystacks):
            continue
        errors.append(
            _diagnostic(
                "A scored value is not in the page's text.",
                what=f"field {field} was drawn as {wanted!r}, which no block's text and no table "
                "cell's text holds, so a parser cannot read it off the page.",
                where=f"{where}, field {field}",
                expected=f"{wanted!r} within one block's text or one cell's text (whitespace "
                "collapsed), e.g. a block reading 'Total $12.50' holds the value '$12.50'.",
                recover="draw the value inside a block scope, as one block's lines or one table cell, and "
                "record it with the string actually drawn.",
            )
        )
    return errors


def _poly_errors(page: dict, blocks: dict[int, BlockRecord], where: str) -> list[str]:
    """Invariant 4: every `poly` lies within its page."""
    width = page["page_info"]["width"]
    height = page["page_info"]["height"]
    errors = []
    for det in page["layout_dets"]:
        poly = det["poly"]
        xs, ys = poly[0::2], poly[1::2]
        if len(poly) == 8 and all(0 <= x <= width for x in xs) and all(0 <= y <= height for y in ys):
            continue
        block = blocks.get(det["anno_id"])
        path = block.path if block is not None else f"anno_id {det['anno_id']}"
        errors.append(
            _diagnostic(
                "A block's poly lies outside its page.",
                what=f"block {path} has poly {poly}, outside the {width} x {height} page.",
                where=f"{where}, block {path}",
                expected=f"eight numbers, four corners each with 0 <= x <= {width} and 0 <= y <= {height}, "
                "e.g. [10, 20, 110, 20, 110, 40, 10, 40].",
                recover="draw the block inside the page (a layout position or an overlay placement), "
                "or map the poly onto the frame the page was saved in.",
            )
        )
    return errors


def _category_errors(
    page: dict, ink: list[InkRecord], blocks: dict[int, BlockRecord], where: str, config: ParsingConfig
) -> list[str]:
    """Invariant 5: every inked block is mapped, and every category is in the vocabulary."""
    inked = sorted({record.block for record in ink if record.block is not None and record.block in blocks})
    errors = [
        _unmapped(blocks[block_id], where)
        for block_id in inked
        if _category(blocks[block_id], config) is None
    ]
    for det in page["layout_dets"]:
        category = det["category_type"]
        if category in config.category_vocabulary:
            continue
        block = blocks.get(det["anno_id"])
        path = block.path if block is not None else f"anno_id {det['anno_id']}"
        errors.append(
            _diagnostic(
                "A block's category is not an OmniDocBench category.",
                what=f"block {path} is written as category_type {category!r}, which category_vocabulary "
                "does not list.",
                where=f"{where}, block {path}; {PARSING_CONFIG_PATH} -> category_vocabulary",
                expected=f"one of {list(config.category_vocabulary)}, e.g. parse_category: title",
                recover="set the block's parse_category (or its category_map entry) to a "
                "category_vocabulary value.",
            )
        )
    return errors


def _uncircled(text: str) -> str:
    """The evaluator's `replace_textcircle`: a circled character, or `\\textcircled{x}`, reads as `x`."""
    text = _TEXTCIRCLED.sub(lambda match: match.group(1).strip(), text)
    return "".join(_CIRCLED.get(char, char) for char in text)


def evaluator_normalised(text: str) -> str:
    """Normalise text as the pinned evaluator does before it compares a prediction with a ground-truth line.

    Mirrors `normalized_text` in the evaluator's src/core/preprocess/data_preprocess.py, which is
    `clean_string(textblock2unicode(text))`: circled characters are uncircled; an inline formula span whose
    content holds a LaTeX marker is rewritten as text; the literal and escaped tab and newline spellings are
    removed; and every character that is not a word character or a CJK ideograph is dropped. The evaluator
    rewrites a formula span through pylatexenc's LaTeX-to-text conversion, which is not mirrored: here the
    span reads as its content, which is what the evaluator falls back to when that conversion fails, and
    which keeps the same letters, digits and underscores for a plain value. The corpus draws no LaTeX, so no
    ground-truth line takes that path.

    Args:
        text: A block's text.

    Returns:
        The text as the evaluator compares it.
    """
    text = _uncircled(text)

    def rewrite(match: re.Match[str]) -> str:
        content = match.group(1) if match.group(1) is not None else match.group(2)
        bare = _LATEX_ESCAPE.sub("", content)
        if not any(char in bare for char in "\\^_"):
            return match.group(0)
        return bare.strip()

    text = _uncircled(_INLINE.sub(rewrite, text))
    for spelling in _CONTROL_SPELLINGS:
        text = text.replace(spelling, "")
    return _NOT_KEPT.sub("", text)


def normalised_edit_distance(first: str, second: str) -> float:
    """Return the evaluator's distance: the Levenshtein distance over the longer string's length.

    Mirrors `compute_edit_distance_matrix_new` in the evaluator's src/core/matching/match.py, which gives
    two empty strings 0.
    """
    longest = max(len(first), len(second))
    if longest == 0:
        return 0.0
    previous = list(range(len(second) + 1))
    for row, left in enumerate(first, start=1):
        current = [row]
        for column, right in enumerate(second, start=1):
            current.append(
                min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (left != right))
            )
        previous = current
    return previous[-1] / longest


def _ignored_match_errors(
    page: dict, blocks: dict[int, BlockRecord], where: str, config: ParsingConfig
) -> list[str]:
    """Invariant 6: no ignored block's text lies within the evaluator's match distance of a scored block's.

    The evaluator (`match_gt2pred_quick` in its src/core/matching/match_quick.py) matches the ignored
    categories' lines first and drops every prediction whose normalised edit distance to one is below the
    threshold, so a correct reading of a scored block close to an ignored one is dropped unscored. A block
    whose normalised text is empty is left out, as the evaluator leaves it out.
    """
    threshold = config.ignored_match_max_distance
    ignored: list[tuple[dict, str]] = []
    scored: list[tuple[dict, str]] = []
    for det in page["layout_dets"]:
        if "text" not in det:
            continue
        normal = evaluator_normalised(det["text"])
        if normal:
            group = ignored if det["category_type"] in config.ignored_categories else scored
            group.append((det, normal))

    def path(det: dict) -> str:
        block = blocks.get(det["anno_id"])
        return block.path if block is not None else f"anno_id {det['anno_id']}"

    errors = []
    for ignored_det, ignored_text in ignored:
        for scored_det, scored_text in scored:
            # The distance is at least the length difference, so a pair that far apart cannot match.
            longest = max(len(ignored_text), len(scored_text))
            if abs(len(ignored_text) - len(scored_text)) / longest >= threshold:
                continue
            distance = normalised_edit_distance(ignored_text, scored_text)
            if distance >= threshold:
                continue
            category = ignored_det["category_type"]
            errors.append(
                _diagnostic(
                    "An ignored block nearly repeats a scored block.",
                    what=f"block {path(ignored_det)} ({category}, which the evaluator ignores) reads "
                    f"{ignored_det['text']!r}, at normalised edit distance {distance:.3f} from block "
                    f"{path(scored_det)} ({scored_det['category_type']}) reading {scored_det['text']!r}; "
                    f"below ignored_match_max_distance {threshold}, the evaluator would drop a correct "
                    "reading of the scored block as a match of the ignored one.",
                    where=f"{where}, blocks {path(ignored_det)} and {path(scored_det)}; "
                    f"{PARSING_CONFIG_PATH} -> ignored_match_max_distance",
                    expected=f"every {category} block at least {threshold} from every scored block's text, "
                    "or the repeating block scored, e.g. parse_category: text_block on a remittance slip's "
                    "invoice number line.",
                    recover=f"give block {path(ignored_det)} a scored parse_category (text_block, or figure "
                    "for a code) in its layout YAML if it is document content rather than running page "
                    "furniture, or change its text so it no longer repeats the scored block.",
                )
            )
    return errors
