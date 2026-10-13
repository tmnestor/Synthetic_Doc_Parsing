"""The table primitive and its frame/grouping axes.

Row data comes from a named provider; this module only lays it out. Column
positions resolve against the current region, so a table nested inside a
container positions correctly without knowing it is nested.
"""

import math
from dataclasses import replace
from decimal import Decimal

from PIL import ImageDraw

from generators.common import (
    Font,
    InkSink,
    draw_fitted_left,
    draw_fitted_right,
    draw_separator_line,
    draw_text_left,
    draw_text_right,
    fit_text,
    fmt_amount,
    left_draw_x,
    load_font,
)
from generators.exporters.geometry import BoxRecorder, Cell
from generators.layout_budgets import field_budget
from generators.layout_dsl.context import RenderContext
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.primitives_text import font_for, line_advance, resolve_role
from generators.layout_dsl.providers import get_provider

_ABSENT = "NOT_FOUND"

# Public: schema.py imports this as the single source of truth for validating
# a table column's `align:` key. Deliberately narrower than
# primitives_text.ALIGNMENTS — a column's cells dispatch on exactly
# `align == "right"` (see `_draw_row`/`_draw_header` below) and treat every
# other value, including "center", as left; there is no third option here.
COLUMN_ALIGNMENTS = ("left", "right")

# Public: schema.py imports this to validate a table's `cell_line_spacing:` key
# (layout default `table_cell_line_spacing`). It selects the per-line advance a
# *budgeted* cell's fitted text draws with, which is also the height of the box
# it records:
#   row_height -- the table's own row pitch, what every bank and receipt table's
#                 legacy renderer passed.
#   font       -- the fitted font's own line height, i.e. no line_spacing at
#                 all, which is what the legacy invoice renderer passed. Its
#                 line-item description cells sit in a 52px band but record a
#                 28px-tall box, so the two are genuinely independent.
CELL_LINE_SPACINGS = ("row_height", "font")

# Frames whose header label block is vertically centred within header_height
# rather than pinned to its top: `bordered` draws an outlined box, `filled`
# a solid one, and both need their labels centred inside it, matching the
# legacy Westpac (bordered) and NAB (filled) header bars.
_BOXED_FRAMES = ("bordered", "filled")


class TableError(RuntimeError):
    """Raised when a table block cannot resolve the geometry it needs to render."""


def column_x(column: dict, ctx: RenderContext) -> int:
    """Resolve a column's anchor x-coordinate against the current region.

    Args:
        column: The column spec, carrying `x` or `x_right`.
        ctx: Render context supplying the region.

    Returns:
        The absolute pixel x: an anchor's left edge for `align: left`, or its
        right edge for `align: right`.
    """
    if "x" in column:
        return ctx.region.x + int(column["x"])
    return ctx.region.right + int(column["x_right"])


def _label_anchor(column: dict) -> dict:
    """Return the anchor spec this column's *header label* positions against.

    A column's header label normally sits at the same anchor as its cells. The
    legacy invoice renderer breaks that: its "Unit Price" and "Total" labels
    are drawn at the column's own left edge while the amounts beneath them
    right-align 200px further along, so the label and the cells have genuinely
    independent anchors. `label_x` declares the label's own offset from the
    region's left edge; without it the column's own `x`/`x_right` is used
    unchanged, which is every bank and receipt column.

    Args:
        column: The column spec.

    Returns:
        A spec `column_x` can resolve — the column itself, or a one-key
        override carrying only the label's own offset.
    """
    if "label_x" in column:
        return {"x": column["label_x"]}
    return column


def _mark_cell(ctx: RenderContext, cell: Cell | None) -> None:
    """Mark the ink drawn from now on as drawn in `cell` (None once the table's cells are done).

    Cells go through `ctx.ink_recorder`, which `capture: false` leaves in place, so a table that records no
    scored boxes still records its grid.
    """
    if ctx.ink_recorder is not None:
        ctx.ink_recorder.set_cell(cell)


def _resolve_row_height(block: dict, ctx: RenderContext) -> int:
    """Resolve the table's row height from the block, falling back to the layout.

    Args:
        block: The `table` block, which may carry its own `row_height`.
        ctx: Render context supplying the layout.

    Returns:
        The row height in pixels.

    Raises:
        TableError: If neither the block nor the layout defines `row_height`.
    """
    if "row_height" in block:
        return int(block["row_height"])
    if "row_height" in ctx.layout:
        return int(ctx.layout["row_height"])
    raise TableError(
        "Table cannot resolve a row height.\n"
        "  What:     neither the table block nor the layout defines row_height.\n"
        f"  Where:    {ctx.layout_path} -> {ctx.layout_id} (a table block)\n"
        "  Expected: row_height: <int px> on the layout, or on the table block "
        "itself, e.g. {type: table, row_height: 60, ...}.\n"
        "  Recover:  add row_height to the layout (config/layouts/*.yml), or set "
        "it on this table block if it needs a value other than the layout's."
    )


def _resolve_cell_line_spacing(block: dict, ctx: RenderContext) -> str:
    """Resolve and check the per-line advance a budgeted cell draws with.

    Checked here rather than only in `schema.py` because the value may come
    from the layout's `defaults.table_cell_line_spacing` as well as from the
    block's own `cell_line_spacing:` key, and schema.py validates the body's
    blocks, not the values inside a `defaults:` mapping. An unrecognised
    string would otherwise silently select the `font` branch — pixel-identical
    for an unbudgeted table, and a wrong box height for a budgeted one.

    Args:
        block: The `table` block.
        ctx: Render context supplying the layout.

    Returns:
        One of `CELL_LINE_SPACINGS`.

    Raises:
        TableError: If the resolved value is not one of `CELL_LINE_SPACINGS`.
    """
    value = str(
        resolve_param(
            block,
            ctx.layout,
            "table_cell_line_spacing",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="cell_line_spacing",
        )
    )
    if value not in CELL_LINE_SPACINGS:
        raise TableError(
            "Unknown table cell line spacing.\n"
            f"  What:     cell_line_spacing {value!r} is not a recognised mode.\n"
            f"  Where:    {ctx.layout_path} -> {ctx.layout_id} (a table block's "
            "`cell_line_spacing`, or defaults.table_cell_line_spacing)\n"
            f"  Expected: one of {list(CELL_LINE_SPACINGS)} — 'row_height' advances a "
            "budgeted cell by the table's own row pitch, 'font' by the fitted font's "
            "own line height.\n"
            f"  Recover:  set cell_line_spacing: to one of {list(CELL_LINE_SPACINGS)}, or "
            f"fix {ctx.layout_id}.defaults.table_cell_line_spacing."
        )
    return value


def _cell_text(row: dict, column: dict) -> str:
    """Render one cell's value as display text.

    A column may set `currency: plain` to drop the `$` prefix `fmt_amount`
    otherwise adds — Westpac's legacy renderer prints amounts as `1,234.56`,
    not `$1,234.56`, and the ground truth the table draws must match the
    bank's real formatting, not just land in the right place. A column may
    also set `currency_suffix` (e.g. NAB's `"Cr"`) to append a fixed suffix
    after the formatted amount — a separate, composable concern from the `$`
    prefix, since a suffix is a per-column display choice, not a property
    `fmt_amount` itself needs to know about.
    """
    value = row.get(column["key"], "")
    if isinstance(value, Decimal):
        text = fmt_amount(value)
        if column.get("currency") == "plain":
            text = text.lstrip("$")
        suffix = column.get("currency_suffix")
        return f"{text} {suffix}" if suffix else text
    text = str(value)
    return "" if text == _ABSENT else text


def _cell_bold(row: dict, column_key: str) -> bool:
    """Resolve whether one cell renders bold.

    `row["bold"]` is `True` for a uniformly bold row — NAB's "Carried
    forward" and ANZ's "Totals at end of period", both drawn in
    `font_body_bold` throughout. It may instead be a collection of column
    keys for a row bold in only some of its cells — ANZ's "BALANCE BROUGHT
    FORWARD" row, whose legacy renderer draws the description in
    `font_body_bold` but the balance value in the plain `font_body`, a mix
    a single per-row flag cannot express. Anything else (absent, `False`, or
    an empty collection) renders every cell in the row at regular weight, the
    default for every other provider-set and real row. A non-empty collection
    naming a column this table does not have is a caller error, not a silent
    no-op — `_validate_bold_spec` rejects it before any cell in the row is
    drawn (see there for why that check lives here rather than in schema.py).

    `row["bold"]` is provider-set row *data*, not layout configuration —
    unlike a block's own `bold` key (see `draw_text_block`/`draw_banner`),
    which is a real `PARAMETER_DEFAULTS` entry, this one must never resolve
    through the layout's `defaults:`: which row a provider marks bold is a
    fact about the row, not a per-layout style an author would toggle, and
    every bank layout happening to seed `bold: false` would otherwise mask a
    genuine provider bug (a row nobody meant to bold, silently un-bolding
    the moment a layout picked a different default).
    """
    spec = row.get("bold", False)
    if spec is True:
        return True
    if spec:
        return column_key in spec
    return False


def _validate_bold_spec(row: dict, columns: list) -> None:
    """Fail fast if a row's `bold` collection names a column the table lacks.

    `row["bold"]` is provider-set data (see `_cell_bold`), not a layout YAML
    key, so schema.py — which validates the static `body:` tree and knows
    nothing about any specific provider's row shape — cannot check it; a
    provider's row list does not exist until a table actually calls it, and
    `python -m generators.pipeline validate` never invokes providers (the
    same reason a typo in e.g. `pipe_fields`'s `fields:` mapping is a
    render-time `ProviderError`, not a validate-time one). This is the one
    place both the row's `bold` collection and the table's real `columns`
    are in scope together, so it is the one place able to catch a typo (a
    misspelled `*_bold` entry in a table's `params:`) before it silently
    renders as "nothing bold" — indistinguishable, at a glance, from the
    typo never having been caught at all.

    Args:
        row: The row dict about to be drawn.
        columns: The table's column specs.

    Raises:
        TableError: If `row["bold"]` is a non-empty collection containing a
            key absent from every column.
    """
    spec = row.get("bold", False)
    if spec is True or not spec:
        return
    known = {column["key"] for column in columns}
    unknown = sorted(set(spec) - known)
    if unknown:
        raise TableError(
            "Row bold spec names an unknown column.\n"
            f"  What:     row['bold'] names column key(s) {unknown}, which this table has no "
            "column for.\n"
            "  Where:    a row provider's 'bold' collection (e.g. a *_bold entry in a table's "
            "params:) versus that table's columns:.\n"
            f"  Expected: bold column keys drawn from this table's real columns: {sorted(known)}.\n"
            "  Recover:  fix the typo in the *_bold param, or add the missing column to the "
            "table's columns:."
        )


def draw_table(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a table's header and rows.

    Args:
        block: The `table` block. Two more keys support `grouping:
            dedicated_row`: `group_gap` (default 10px, the gap inserted
            between consecutive date sub-header rows — CBA's real value;
            NAB's legacy renderer inserts none, so NAB's layout sets
            `group_gap: 0`), and `synthetic_row_placement` (default
            `leading`, a provider's synthetic row renders first, before any
            group header — CBA's "Opening Balance"; NAB's "Brought forward"
            instead renders *under* the first date-group header, expressed
            as `synthetic_row_placement: after_first_group_header`). Two more
            support `frame: ruled`: `header_rule_top` (default True, whether
            the rule above the header labels is drawn at all — ANZ's legacy
            header rules only *below* its labels, never above) and
            `header_rule_gap` (default 16, the y-advance after that below
            rule — ANZ's is 14, not CBA's 16). A third key, `capture`
            (default True, via the `table_capture` layout default), suppresses
            all geometry recording for this table when False — invoice's
            `tax_invoice_mixed` draws the same `LINE_ITEM_*` list into two
            tables (a taxable/GST-free split display), and a second recorded
            box for the same field would collide with the first, since one
            ground-truth value has exactly one bounding box. Four more are
            typographic: `role` (the font-size role every label and cell
            draws at — a table used to be pinned to "body" in Python, which
            the invoice tables, drawn one role larger than their own page's
            name lines, cannot express), `header_bold` (the column headings'
            weight, `table_header_bold`), `row_inset_y` (the cells' ink inset
            inside the row band, `table_row_inset_y` — see `_draw_row`; it
            moves cells only, not the dedicated date sub-header row `grouping:
            dedicated_row` draws below, so combining the two is rejected
            below rather than given an arbitrary meaning) and
            `cell_line_spacing` (a budgeted cell's per-line advance,
            `table_cell_line_spacing` — see `CELL_LINE_SPACINGS`).
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        TableError: If no row height is available from the block or layout,
            a row's `bold` collection (see `_validate_bold_spec`) names a
            column this table does not have, or `grouping: dedicated_row` is
            combined with a non-zero `row_inset_y`.
    """
    if not bool(
        resolve_param(
            block,
            ctx.layout,
            "table_capture",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="capture",
        )
    ):
        ctx = replace(ctx, recorder=None)

    frame = block["frame"]
    grouping = block["grouping"]
    fill_color = block.get("fill_color")
    fill_inset = int(
        resolve_param(
            block,
            ctx.layout,
            "table_fill_inset",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="fill_inset",
        )
    )
    group_gap = int(
        resolve_param(
            block,
            ctx.layout,
            "table_group_gap",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="group_gap",
        )
    )
    synthetic_after_header = block.get("synthetic_row_placement") == "after_first_group_header"
    label_inset_y = block.get("label_inset_y")
    if label_inset_y is not None:
        label_inset_y = int(label_inset_y)
    header_rule_top = bool(
        resolve_param(
            block,
            ctx.layout,
            "table_header_rule_top",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="header_rule_top",
        )
    )
    header_rule_gap = int(
        resolve_param(
            block,
            ctx.layout,
            "table_header_rule_gap",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="header_rule_gap",
        )
    )
    columns = block["columns"]
    if ctx.ink_recorder is not None:
        ctx.ink_recorder.set_table_columns(len(columns))
    dividers = resolve_param(
        block,
        ctx.layout,
        "table_dividers",
        layout_id=ctx.layout_id,
        layout_path=ctx.layout_path,
        block_key="dividers",
    )
    row_height = _resolve_row_height(block, ctx)
    role = str(
        resolve_param(block, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    text_size = resolve_role(ctx.layout, role)
    header_bold = bool(
        resolve_param(
            block,
            ctx.layout,
            "table_header_bold",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="header_bold",
        )
    )
    row_inset_y = int(
        resolve_param(
            block,
            ctx.layout,
            "table_row_inset_y",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="row_inset_y",
        )
    )
    # `row_inset_y` insets the cells' ink inside their row band, but the bold
    # date sub-header row `grouping: dedicated_row` inserts is drawn by this
    # function directly and takes no inset -- so combining the two silently
    # splits one table's vertical rhythm in half. No layout does it, and what
    # it should mean has never been decided, so it fails here rather than
    # rendering something nobody chose. This cannot live in schema.py's
    # `_validate_table`: `row_inset_y` may come from the layout's `defaults:`,
    # which that function does not see.
    if grouping == "dedicated_row" and row_inset_y:
        raise TableError(
            "Undefined table row geometry.\n"
            f"  What:     grouping: dedicated_row is combined with row_inset_y {row_inset_y}, "
            "which moves the cells but not the bold date sub-header row grouping inserts.\n"
            f"  Where:    {ctx.layout_path} -> {ctx.layout_id} (a table block's `row_inset_y`, "
            "or defaults.table_row_inset_y)\n"
            "  Expected: row_inset_y: 0 alongside grouping: dedicated_row, e.g.\n"
            "              {type: table, grouping: dedicated_row, row_inset_y: 0, ...}\n"
            "            — or grouping: none / inline if the cells really need an inset.\n"
            "  Recover:  set row_inset_y: 0 on this table block (it overrides "
            f"{ctx.layout_id}.defaults.table_row_inset_y), or change the table's grouping."
        )
    cell_line_spacing = _resolve_cell_line_spacing(block, ctx)
    family = str(
        resolve_param(block, ctx.layout, "family", layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    )
    advance = line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    params = {
        **block.get("params", {}),
        **{param: ctx.layout[key] for param, key in (block.get("params_from_layout") or {}).items()},
    }
    rows = get_provider(block["rows"])(ctx.entry, params)

    # A leading synthetic row normally renders first, ahead of any group
    # header (CBA's Opening Balance). NAB's Brought-forward row instead
    # belongs *under* the first date-group header, so it is set aside here
    # and spliced in once that header has been drawn, below.
    deferred_synthetic = None
    if synthetic_after_header and rows and rows[0].get("synthetic"):
        deferred_synthetic = rows[0]
        rows = rows[1:]

    # Every row the table draws (header, date sub-header, synthetic and data alike), counted from 0, so
    # each line of ink can name its cell.
    drawn_row = 0
    if resolve_param(
        block,
        ctx.layout,
        "table_header",
        layout_id=ctx.layout_id,
        layout_path=ctx.layout_path,
        block_key="header",
    ):
        header_height = int(block["header_height"]) if "header_height" in block else advance
        fill_height = int(block["fill_height"]) if "fill_height" in block else header_height
        y = _draw_header(
            columns,
            ctx,
            y,
            size=text_size,
            bold=header_bold,
            frame=frame,
            header_height=header_height,
            fill_height=fill_height,
            dividers=dividers,
            fill_color=fill_color,
            label_inset_y=label_inset_y,
            header_rule_top=header_rule_top,
            header_rule_gap=header_rule_gap,
            family=family,
            advance=advance,
            cell_row=drawn_row,
        )
        drawn_row += 1

    table_body_start = y
    total_rows = len(rows)
    index = 0
    previous_date = None
    first_row = True
    for position, row in enumerate(rows):
        synthetic = bool(row.get("synthetic"))
        if grouping == "dedicated_row" and not synthetic and row.get("date") != previous_date:
            if previous_date is not None:
                y += group_gap  # Gap between date groups; 0 for NAB, 10 (CBA's real value) by default.
            if frame == "filled":
                ctx.draw.rectangle(
                    [(ctx.region.x, y), (ctx.region.right, y + row_height - fill_inset)], fill=fill_color
                )
            # The date sub-header is one cell spanning the table (Decision 7).
            _mark_cell(ctx, Cell(drawn_row, 0, len(columns)))
            draw_text_left(
                ctx.draw,
                str(row.get("date", "")),
                ctx.region.x,
                y,
                load_font(text_size, family=family, bold=True),
                ink=ctx.ink(ctx.ink_role()),
            )
            previous_date = row.get("date")
            y += row_height
            drawn_row += 1

            if deferred_synthetic is not None:
                y = _draw_row(
                    deferred_synthetic,
                    columns,
                    ctx,
                    y,
                    size=text_size,
                    frame=frame,
                    grouping=grouping,
                    row_height=row_height,
                    index=None,
                    is_last=False,
                    family=family,
                    inset_y=row_inset_y,
                    cell_line_spacing=cell_line_spacing,
                    first_row=first_row,
                    is_new_group=False,
                    cell_row=drawn_row,
                )
                drawn_row += 1
                first_row = False
                deferred_synthetic = None

        is_new_group = not synthetic and row.get("date") != previous_date
        y = _draw_row(
            row,
            columns,
            ctx,
            y,
            size=text_size,
            frame=frame,
            grouping=grouping,
            row_height=row_height,
            index=None if synthetic else index,
            is_last=(position == total_rows - 1),
            family=family,
            inset_y=row_inset_y,
            cell_line_spacing=cell_line_spacing,
            first_row=first_row,
            is_new_group=is_new_group,
            cell_row=drawn_row,
        )
        drawn_row += 1
        if grouping == "inline" and not synthetic:
            previous_date = row.get("date")
        if not synthetic:
            index += 1
        first_row = False
    _mark_cell(ctx, None)

    if frame == "bordered":
        # Mirrors the legacy renderers' one-shot outer box + column dividers,
        # drawn once across the whole table body rather than per row — the
        # header already closed the top edge, so only left/right/bottom and
        # the interior dividers remain.
        ctx.draw.line([(ctx.region.x, table_body_start), (ctx.region.x, y)], fill="black")
        ctx.draw.line([(ctx.region.right, table_body_start), (ctx.region.right, y)], fill="black")
        ctx.draw.line([(ctx.region.x, y), (ctx.region.right, y)], fill="black")
        for divider in dividers:
            dx = column_x(divider, ctx)
            ctx.draw.line([(dx, table_body_start), (dx, y)], fill="black")

    return y


def _draw_header(
    columns: list,
    ctx: RenderContext,
    y: int,
    *,
    size: int,
    bold: bool,
    frame: str,
    header_height: int,
    fill_height: int,
    dividers: list,
    fill_color: str | None,
    label_inset_y: int | None,
    header_rule_top: bool,
    header_rule_gap: int,
    family: str,
    advance: int,
    cell_row: int,
) -> int:
    """Draw the column-header row in the table's frame.

    `header_height` is the label row's own advance — a function of the header
    font's line height, not the data row pitch (`row_height`). The two are
    independent: a table's data rows may be taller or shorter than its single
    header line, and conflating them drifts the header away from wherever the
    legacy renderer being compared against actually puts it.

    The `bordered` frame additionally decorates: a bordered rectangle spans
    the header height and `dividers` cut it into columns, matching the legacy
    Westpac renderer's bordered header. The `filled` frame instead fills a
    rectangle of `fill_height` (defaulting to `header_height`, but settable
    independently — legacy NAB fills a 44px bar and then advances 50px, a gap
    `header_height` alone cannot express) with `fill_color` and draws no
    dividers, matching the legacy NAB renderer's light-blue header bar.

    Both box frames centre their labels vertically within the header rather
    than pinning them to its top by default — the geometry-only equivalence
    harness cannot see this (no field box is recorded for header labels), and
    it would otherwise silently regress to bare text. `label_inset_y`, when
    given, overrides that computed centring with an exact declared offset
    from `y`, for a legacy renderer (like NAB, whose labels sit at `y + 10`
    inside a 44px bar) whose real offset the centring formula does not
    reproduce. A label may contain "\\n" for a legacy-matching multi-line
    header cell (e.g. Westpac's "Date of" / "Transaction"); each line is
    positioned relative to that same start, one `advance` apart.

    `frame: ruled` draws a rule both above and below the header labels by
    default (CBA's real header). `header_rule_top`, when False, skips the
    rule above entirely — no line, no advance — for a legacy renderer (like
    ANZ) whose header rules only below. `header_rule_gap` overrides the
    below rule's own advance (default 16, CBA's real value; ANZ's is 14).
    Both are no-ops for every other frame, which draws no rule here at all.

    `bold` is the label row's own weight (layout default `table_header_bold`,
    block key `header_bold`), not the layout-wide `bold` default: every bank
    and receipt layout sets `bold: false` layout-wide yet draws its column
    headings bold, while the legacy invoice renderer draws its headings at
    regular weight, so the two cannot share one key.

    A column may position its label independently of its own cells with
    `label_x`/`label_align` — see `_label_anchor`.

    Each label is one cell of row `cell_row`, its lines included, in its column's position.
    """
    font = load_font(size, family=family, bold=bold)
    ink = ctx.ink(ctx.ink_role())
    if frame == "ruled":
        if header_rule_top:
            draw_separator_line(ctx.draw, ctx.region.x, ctx.region.right, y, color="black")
            y += 12
    elif frame == "bordered":
        ctx.draw.rectangle([(ctx.region.x, y), (ctx.region.right, y + header_height)], outline="black")
        for divider in dividers:
            dx = column_x(divider, ctx)
            ctx.draw.line([(dx, y), (dx, y + header_height)], fill="black")
    elif frame == "filled":
        ctx.draw.rectangle([(ctx.region.x, y), (ctx.region.right, y + fill_height)], fill=fill_color)

    for column_index, column in enumerate(columns):
        _mark_cell(ctx, Cell(cell_row, column_index, 1))
        x = column_x(_label_anchor(column), ctx)
        lines = str(column["label"]).split("\n")
        if label_inset_y is not None:
            start = y + label_inset_y
        elif frame in _BOXED_FRAMES:
            block_height = advance * len(lines)
            start = y + max(0, (header_height - block_height) // 2)
        else:
            start = y
        label_align = column.get("label_align", column.get("align"))
        for position, text in enumerate(lines):
            line_y = start + position * advance
            if label_align == "right":
                draw_text_right(ctx.draw, text, x_right=x, y=line_y, font=font, ink=ink)
            else:
                draw_text_left(ctx.draw, text, x, line_y, font, ink=ink)

    y += header_height
    if frame == "ruled":
        draw_separator_line(ctx.draw, ctx.region.x, ctx.region.right, y, color="black")
        y += header_rule_gap
    return y


def _date_is_redundant(grouping: str, is_new_group: bool) -> bool:
    """Report whether a row's own date cell would repeat its group's date.

    No bank prints the date twice on one grouped line. Both grouping modes
    carry the date somewhere else, so the cell is dropped:

    - `dedicated_row` puts the date on a header row of its own, so *every*
      row beneath it repeats — the cell is always redundant.
    - `inline` has no header row; the first row of a group carries the date
      itself, so only the rows after it repeat.

    `none` has nothing to repeat: the date belongs in every row.

    Args:
        grouping: The table's grouping mode.
        is_new_group: Whether this row opens a new date group.

    Returns:
        True when the date cell should be left blank.
    """
    if grouping == "dedicated_row":
        return True
    return grouping == "inline" and not is_new_group


def _draw_row(
    row: dict,
    columns: list,
    ctx: RenderContext,
    y: int,
    *,
    size: int,
    frame: str,
    grouping: str,
    row_height: int,
    index: int | None,
    is_last: bool,
    family: str,
    inset_y: int,
    cell_line_spacing: str,
    cell_row: int,
    first_row: bool = False,
    is_new_group: bool = True,
) -> int:
    """Draw one row; `index` is None for synthetic rows, which are not recorded.

    `cell_row` is the row's position among every row the table draws; each cell's ink, and each
    sub-line's, is marked as drawn in that row and its column.

    `is_last` marks the final row in the table's own row list — real or
    synthetic — which is where a column's `last_row_field`, if any, records
    unindexed geometry *instead of* its ordinary indexed `field`, matching
    legacy renderers that record a closing balance once. For CBA (a leading
    synthetic row, or none) this is always the final real transaction; for
    NAB (a *trailing* synthetic "Carried forward" row, via `carried_forward`
    on the provider) it is that synthetic row instead, since
    `last_row_field`'s recording does not gate on `index is not None` the
    way ordinary `field` recording does. No bank column carries both `field`
    and `last_row_field` (verified across all 8 layouts), so selecting one
    name per cell for a single draw is exhaustive — see the loop below.

    `first_row`/`is_new_group` drive the `bordered` frame combined with
    `inline` grouping: plain `bordered` (grouping `none`) draws a divider
    above every row but the first; `bordered` + `inline` draws one only when
    `is_new_group` is true (and blanks the `date` cell otherwise), matching
    the legacy Westpac renderer's date-grouped table, which shows one row per
    transaction — never a dedicated date-only row the way `dedicated_row`
    grouping does for CBA/NAB.

    Cells are drawn first, and `bottom` is derived from the tallest cell's own
    returned advance — the same advance `draw_fitted_left`/`draw_fitted_right`
    already compute while wrapping a budgeted cell — rather than a second,
    separate wrap computation that could drift out of sync with the one the
    draw call actually used. Only then is the row's own decoration (a border
    or rule, which needs the final `bottom`) drawn.

    A row renders bold throughout when a provider marks it `row["bold"] =
    True` — NAB's "Carried forward" row, which legacy draws in
    `font_body_bold`, unlike its "Brought forward"/"Opening Balance" leading
    rows, which stay regular. `row["bold"]` may instead be a collection of
    column keys, bolding only those cells — ANZ's "BALANCE BROUGHT FORWARD"
    row, whose description is bold but its balance value is not (see
    `_cell_bold`). This is provider-set row data, not a layout
    YAML key: which specific row is bold is a fact about legacy's renderer
    (which row it is), not a per-layout style choice an author would toggle,
    exactly parallel to how `synthetic` itself is provider-set rather than
    YAML-configurable. A collection naming a column this table does not have
    raises `TableError` (`_validate_bold_spec`) before any cell draws, rather
    than silently rendering the row entirely unbolded.

    A row draws a fresh black rule above itself, with a fixed 12px gap
    before its own content, when a provider marks it `row["rule_above"] =
    True` — ANZ's "Totals at end of period" row, which legacy separates from
    the transaction table with its own rule rather than continuing the flow.
    Like `bold`, this is provider-set row data, not a layout YAML key, and —
    unlike every other row decoration here — is drawn regardless of `frame`,
    since it is a fact about this one row, not a frame-wide style.

    `inset_y` (layout default `table_row_inset_y`, block key `row_inset_y`)
    insets every cell's *ink* inside the row's own band without changing the
    band: the legacy invoice renderer draws each cell at `y + 12` inside a
    52px row and still advances exactly 52, so `_draw_cell` subtracts the
    inset back off whatever bottom it reports (see there). It is the row
    counterpart of the header's `label_inset_y`, and deliberately does not
    move a column's `sub_line`, whose `offset_y` is already measured from the
    row's own start.
    """
    _validate_bold_spec(row, columns)

    if row.get("rule_above"):
        draw_separator_line(ctx.draw, ctx.region.x, ctx.region.right, y, color="black")
        y += 12

    regular_font = load_font(size, family=family, bold=False)
    bold_font = load_font(size, family=family, bold=True)
    bottom = y + row_height  # Floor: every unbudgeted cell is exactly one row tall.

    for column_index, column in enumerate(columns):
        if column["key"] == "date" and _date_is_redundant(grouping, is_new_group):
            continue
        x = column_x(column, ctx)
        text = _cell_text(row, column)
        if not text:
            continue

        cell_bold = _cell_bold(row, column["key"])
        font = bold_font if cell_bold else regular_font
        right = column.get("align") == "right"
        budget = None
        budget_name = column.get("budget")
        if budget_name is not None:
            budget = field_budget(ctx.layout, ctx.layout_id, budget_name, layout_path=ctx.layout_path)

        # `last_row_field` (unindexed, only on the table's final row) and
        # `field` (indexed, every row) are mutually exclusive on every real
        # column, so a single draw picks whichever one applies rather than
        # drawing the cell twice under two names. A second `draw.text` at
        # the identical position is not a no-op: PIL alpha-composites each
        # call's antialiased glyph mask onto what is already there, so the
        # "redraw" this used to do measurably darkened every soft edge
        # whenever a recorder was present -- i.e. always in production
        # (generators/pipeline.py always passes geometry_out={}), never in
        # a bare render_via_dsl() call. 22/55 real bank entries differed by
        # 267-1109px between the two before this fix.
        last_row_field = column.get("last_row_field")
        field = column.get("field")
        if last_row_field is not None and is_last:
            record_field = last_row_field
            recorder = ctx.recorder
        else:
            record_field = f"{field}[{index}]" if field is not None and index is not None else None
            recorder = ctx.recorder if index is not None else None

        # A `prefix_key` column carries a second, narrower ground-truth value
        # inside its own cell text (e.g. a "2x " quantity marker a provider
        # concatenates onto a description and re-exposes under its own row
        # key; no shipped layout uses one since receipts print a unit line
        # beneath the description instead, see `_draw_sub_lines`). Recording it
        # measures the already-drawn prefix (see `draw_fitted_left`); it never
        # draws anything extra. Indexed like `field`, and only on real rows.
        prefix_key = column.get("prefix_key")
        prefix = str(row.get(prefix_key, "")) if prefix_key is not None else ""
        prefix_field = column.get("prefix_field")
        record_prefix = (
            f"{prefix_field}[{index}]"
            if prefix and prefix_field is not None and index is not None
            else None
        )

        _mark_cell(ctx, Cell(cell_row, column_index, 1))
        cell_bottom = _draw_cell(
            ctx.draw,
            text,
            x,
            y,
            right=right,
            budget=budget,
            size=size,
            row_height=row_height,
            font=font,
            bold=cell_bold,
            family=family,
            inset_y=inset_y,
            cell_line_spacing=cell_line_spacing,
            recorder=recorder,
            field=record_field,
            prefix=prefix or None,
            prefix_field=record_prefix,
            # Scored when the cell records a scored field, whether or not this table captures geometry.
            ink=ctx.ink(ctx.ink_role(field=record_field)),
        )
        bottom = max(bottom, cell_bottom)

    bottom += _draw_sub_lines(row, columns, ctx, y, index, cell_row=cell_row)

    if frame == "bordered" and not first_row and (grouping != "inline" or is_new_group):
        draw_separator_line(ctx.draw, ctx.region.x, ctx.region.right, y, color="black")

    return bottom


def _draw_sub_lines(
    row: dict, columns: list, ctx: RenderContext, y: int, index: int | None, *, cell_row: int
) -> int:
    """Draw each column's sub-line, if it has data this row, and report its height.

    A column may carry `sub_line: {key, role, color, offset_y, height}` to
    render a second line of text beneath its main cell — NAB's dotted-leader
    reference number beneath the transaction description. `key` names the row
    key the text comes from; `role` (default "body") resolves a font size the
    same way any other text primitive does; `offset_y` (default 0) positions
    it as an offset from the row's own start `y` — not the wrapped main
    cell's bottom — matching the legacy renderer, which draws the reference
    line at a fixed offset regardless of how many lines the description
    wrapped to (a legacy quirk, reproduced rather than "fixed", since
    equivalence means matching the renderer being replaced, bugs included).
    `height` is a flat addition to the row's own advance, contributed once
    per row even if more than one column happens to carry a sub_line with
    data (the tallest of them wins, not their sum).

    A sub-line that prints scored values carries two more keys: `budget`, the
    field budget it is fitted to (it shrinks, or fails, rather than running
    past its column), and `span_fields: {key, fields}`, where `key` names the
    row key holding each value's (start, end) character extent within the
    line and `fields` maps each extent's name to the field its box is recorded
    against, indexed like a cell (`FIELD[i]`, real rows only). A receipt's
    `2 @ $3.50` unit line records LINE_ITEM_QUANTITIES[i] and
    LINE_ITEM_PRICES[i] this way: the line is drawn once, and each value's box
    is measured where it landed at the size the fit chose.

    Args:
        row: The row dict; a sub_line draws only when `row[sub_line["key"]]`
            is present and not the absent-value sentinel.
        columns: The table's column specs.
        ctx: Render context.
        y: The row's own start y, before any wrapping advance.
        index: The row's ground-truth index, or None for a synthetic row
            (whose sub-line, if any, records nothing).
        cell_row: The row's position among every row the table draws; a sub-line's ink is marked as
            drawn in that row and the declaring column's cell.

    Returns:
        The extra height claimed by the tallest sub_line drawn this row (0 if
        no column has a sub_line, or none has data for this row).

    Raises:
        TableError: A budgeted sub-line fitted to more than one line, so its
            values' boxes cannot be measured on a single line.
    """
    extra = 0
    for column_index, column in enumerate(columns):
        sub_line = column.get("sub_line")
        if sub_line is None:
            continue
        text = str(row.get(sub_line["key"], ""))
        if not text or text == _ABSENT:
            continue
        x = column_x(column, ctx)
        role = str(
            resolve_param(
                sub_line, ctx.layout, "role", layout_id=ctx.layout_id, layout_path=ctx.layout_path
            )
        )
        size = resolve_role(ctx.layout, role)
        offset_y = int(
            resolve_param(
                sub_line,
                ctx.layout,
                "table_offset_y",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="offset_y",
            )
        )
        color = str(
            resolve_param(
                sub_line, ctx.layout, "color", layout_id=ctx.layout_id, layout_path=ctx.layout_path
            )
        )
        span_fields = sub_line.get("span_fields")
        records = (
            {
                f"{field}[{index}]": span
                for name, field in span_fields["fields"].items()
                if (span := row.get(span_fields["key"], {}).get(name)) is not None
            }
            if span_fields is not None and index is not None
            else {}
        )
        ink = ctx.ink(ctx.ink_role(field=next(iter(records), None)))
        budget_name = sub_line.get("budget")
        if budget_name is None:
            font = font_for(
                ctx.layout, sub_line, size, layout_id=ctx.layout_id, layout_path=ctx.layout_path
            )
        else:
            family = str(
                resolve_param(
                    sub_line, ctx.layout, "family", layout_id=ctx.layout_id, layout_path=ctx.layout_path
                )
            )
            budget = field_budget(ctx.layout, ctx.layout_id, budget_name, layout_path=ctx.layout_path)
            fitted = fit_text(
                text,
                width=budget["width"],
                fit=budget["fit"],
                min_font=budget["min_font"],
                max_lines=budget["max_lines"],
                nominal_size=size,
                family=family,
            )
            if len(fitted.lines) != 1:
                raise TableError(
                    f"sub-line {text!r} fitted to {len(fitted.lines)} lines on layout '{ctx.layout_id}'.\n"
                    f"  What:     a budgeted sub-line must stay on one line, so each value's box can be "
                    f"measured where it lands.\n"
                    f"  Where:    {ctx.layout_path} -> layouts.{ctx.layout_id}.field_budgets.{budget_name}\n"
                    f"  Expected: a single-line fit, e.g. {budget_name}: "
                    "{width: 318, fit: shrink, min_font: 10, max_lines: 1}\n"
                    f"  Recover:  set field_budgets.{budget_name} to fit: shrink with max_lines: 1."
                )
            font = load_font(fitted.size, family=family)
        line_y = y + offset_y
        draw_x = left_draw_x(font, text, x)
        _mark_cell(ctx, Cell(cell_row, column_index, 1))
        draw_text_left(ctx.draw, text, x, line_y, font, fill=color, ink=ink)
        if ctx.recorder is not None:
            for field, (start, end) in records.items():
                lead = font.getlength(text[:start])
                left, top, right, bottom = font.getbbox(text[start:end])
                ctx.recorder.record(
                    field,
                    (
                        int(draw_x + lead + left),
                        line_y + int(top),
                        int(math.ceil(draw_x + lead + right)),
                        line_y + int(bottom),
                    ),
                    text=text[start:end],
                )
        sub_line_height = int(
            resolve_param(
                sub_line,
                ctx.layout,
                "table_sub_line_height",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="height",
            )
        )
        extra = max(extra, sub_line_height)
    return extra


def _draw_cell(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    *,
    right: bool,
    budget: dict | None,
    size: int,
    row_height: int,
    font: Font,
    bold: bool = False,
    family: str,
    inset_y: int,
    cell_line_spacing: str,
    recorder: BoxRecorder | None,
    field: str | None,
    prefix: str | None = None,
    prefix_field: str | None = None,
    ink: InkSink | None = None,
) -> int:
    """Draw one cell, dispatching on alignment and whether it has a fit budget.

    `font` already carries the row's weight and face (see `_draw_row`) for the
    unbudgeted path below; `bold` and `family` are threaded separately to
    `draw_fitted_left`/`draw_fitted_right`, which build their own font
    internally from `nominal_size` and do not accept a pre-built `Font`.
    Omitting the face there silently drew every budgeted cell of a
    monospace layout in the sans face -- invisible for the eight sans bank
    layouts, but wrong for all six receipt layouts.

    `prefix`/`prefix_field` record a sub-box for a leading run of the cell's
    own text (a line item's "2x " quantity marker) -- see `draw_fitted_left`,
    the only helper that supports it, hence the left-budgeted path only.
    `_validate_table` (schema.py) rejects a column declaring them anywhere
    else, so an unreachable combination fails at startup rather than
    silently recording nothing.

    `ink`, when given, receives each drawn line's ink box. It is independent of
    `recorder`: a table with `capture: false` records no scored geometry but
    still draws, so its cells still count as ink.

    `inset_y` shifts the ink down inside the row's band and is then subtracted
    back off the reported bottom, so an inset never changes the table's pitch
    -- the legacy invoice renderer draws its cells at `y + 12` and still
    advances exactly one `row_height`. `cell_line_spacing` selects what a
    *budgeted* cell advances per fitted line, and therefore how tall a box it
    records: `row_height` (every bank and receipt table) or `font`, the fitted
    font's own line height, which is what the legacy invoice renderer used --
    it passed no `line_spacing` at all.

    Returns:
        The cell's own bottom y, net of `inset_y`: the wrapped advance from
        `draw_fitted_left`/`draw_fitted_right` for a budgeted cell, or
        `y + row_height` for an unbudgeted (always single-line) cell.
    """
    if budget is not None:
        line_spacing = row_height if cell_line_spacing == "row_height" else None
        if right:
            return (
                draw_fitted_right(
                    draw,
                    text,
                    x,
                    y + inset_y,
                    budget=budget,
                    nominal_size=size,
                    family=family,
                    bold=bold,
                    line_spacing=line_spacing,
                    recorder=recorder,
                    field=field,
                    ink=ink,
                )
                - inset_y
            )
        return (
            draw_fitted_left(
                draw,
                text,
                x,
                y + inset_y,
                budget=budget,
                nominal_size=size,
                family=family,
                bold=bold,
                line_spacing=line_spacing,
                recorder=recorder,
                field=field,
                prefix=prefix,
                prefix_field=prefix_field,
                ink=ink,
            )
            - inset_y
        )
    if right:
        draw_text_right(
            draw, text, x_right=x, y=y + inset_y, font=font, recorder=recorder, field=field, ink=ink
        )
    else:
        draw_text_left(draw, text, x, y + inset_y, font, recorder=recorder, field=field, ink=ink)
    return y + row_height
