"""The table primitive lays provider rows out in frame/grouping styles and records each cell's box.

Every test draws a real `table` block through `draw_table` onto a real PIL canvas against a real receipt
layout (its `defaults:` are the real ones; the face, role table, row height and a few budgets are replaced so
each behaviour has something to discriminate). Pixel claims are proven by drawing the expected picture by hand
on a blank canvas and comparing, with a negative control wherever a wrong picture could pass by accident.
Recorded-box claims assert exact coordinates read back from a `BoxRecorder`, never merely "inside the budget".
"""

import copy
from typing import Any
from dataclasses import dataclass
from decimal import Decimal

import pytest
from PIL import Image, ImageChops, ImageDraw

from generators.common import FitError, Font, fit_text, fmt_amount, load_font
from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.context import Region
from generators.layout_dsl.primitives_table import (
    TableError,
    _draw_row,
    column_x,
    draw_table,
)
from generators.layout_dsl.providers import get_provider
from generators.layout_dsl.schema import FRAMES, GROUPINGS
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, make_drawing_ctx, real_layout

PAGE_W = 820
PAGE_H = 600
REGION = Region(x=10, width=800)
RIGHT = REGION.right  # 810
FAMILY = "carlito"
ROW_H = 30
HEADER_H = 20  # the body role's line advance, which is the header's default height
Y0 = 100
ROW0 = Y0 + HEADER_H  # first row's top under a plain/bordered/filled header
FILL = "#E8F0FE"
FILL_RGB = (232, 240, 254)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

# Invented values; none appear in ground_truth/.
BANK_ENTRY = {
    "case_id": "CASE999",
    "layout": LAYOUT_ID,
    "fields": {
        "TRANSACTION_DATES": "05/03/2031|05/03/2031|09/03/2031",
        "TRANSACTION_DESCRIPTIONS": "Quillon Hardware|Wattle Cafe|Banksia Fuel",
        "TRANSACTION_AMOUNTS_PAID": "40.00|NOT_FOUND|12.5",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|77.40|NOT_FOUND",
        "ACCOUNT_BALANCE": "300.00",
    },
}
EMPTY_ENTRY = {"case_id": "CASE999", "layout": LAYOUT_ID, "fields": {}}
ITEMS_ENTRY = {
    "case_id": "CASE999",
    "layout": LAYOUT_ID,
    "fields": {
        "LINE_ITEM_DESCRIPTIONS": "Quokka Biscuits|Lamp",
        "LINE_ITEM_QUANTITIES": "3|1",
        "LINE_ITEM_PRICES": "2.35|9.5",
        "LINE_ITEM_TOTAL_PRICES": "7.05|9.5",
    },
}

COL_DATE = {"key": "date", "label": "Date", "align": "left", "x": 0}
COL_DESC = {
    "key": "description",
    "label": "Description",
    "align": "left",
    "x": 200,
    "budget": "DESC",
    "field": "TRANSACTION_DESCRIPTIONS",
}
COL_DEBIT = {
    "key": "debit",
    "label": "Debit",
    "align": "right",
    "x_right": -420,
    "field": "TRANSACTION_AMOUNTS_PAID",
}
COL_CREDIT = {
    "key": "credit",
    "label": "Credit",
    "align": "right",
    "x_right": -210,
    "field": "TRANSACTION_AMOUNTS_RECEIVED",
}
COL_BALANCE = {
    "key": "balance",
    "label": "Balance",
    "align": "right",
    "x_right": 0,
    "last_row_field": "ACCOUNT_BALANCE",
}
BANK_COLUMNS = [COL_DATE, COL_DESC, COL_DEBIT, COL_CREDIT, COL_BALANCE]
# Unbudgeted, unrecorded columns: plain text at a known position, for pixel comparison.
PLAIN_DESC = {"key": "description", "label": "Description", "align": "left", "x": 0}
PLAIN_BALANCE = {"key": "balance", "label": "Balance", "align": "right", "x_right": 0}
DATE_DESC = [COL_DATE, COL_DESC]  # nothing drawn near the right edge, so x=800 probes stay clear


def table_layout(**defaults) -> dict:
    """Return a real receipt layout with a bank-style face, role table and budgets, plus `defaults`."""
    layout = real_layout([])
    layout["font_sizes"] = {"body": 18, "subheader": 28, "sub": 14}
    layout["row_height"] = ROW_H
    layout["defaults"]["family"] = FAMILY
    layout["defaults"]["table_header"] = True
    layout["defaults"]["line_advance"] = {"body": 20, "subheader": 30, "sub": 16}
    layout["defaults"].update(defaults)
    layout["field_budgets"] = {
        "DESC": {"width": 200, "fit": "wrap", "min_font": 14, "max_lines": 3},
        "AMT": {"width": 100, "fit": "shrink", "min_font": 10, "max_lines": 1},
        "WIDE": {"width": 300, "fit": "wrap", "min_font": 14, "max_lines": 4},
        "NARROW": {"width": 200, "fit": "wrap", "min_font": 14, "max_lines": 4},
    }
    return layout


def table(**overrides) -> dict:
    """Return a plain, ungrouped bank table block with the five bank columns, plus `overrides`."""
    block = {
        "type": "table",
        "rows": "bank_transactions",
        "frame": "plain",
        "grouping": "none",
        "columns": copy.deepcopy(BANK_COLUMNS),
    }
    block.update(overrides)
    return block


@dataclass
class Run:
    """The outcome of drawing one table: the final y, the page and the recorded boxes."""

    y: int
    image: Image.Image
    recorder: BoxRecorder | None

    def boxes(self) -> dict[str, list[int]]:
        """Return every recorded box back in pixels, keyed by field."""
        assert self.recorder is not None
        return {
            field: [
                round(left * PAGE_W),
                round(top * PAGE_H),
                round(right * PAGE_W),
                round(bottom * PAGE_H),
            ]
            for field, (left, top, right, bottom) in self.recorder.as_dict().items()
        }


def context(layout: dict, entry: dict | None = None, *, record: bool = True):
    """Return a (context, image, recorder) triple over a fresh page."""
    recorder = BoxRecorder(PAGE_W, PAGE_H) if record else None
    ctx, image = make_drawing_ctx(
        layout,
        entry if entry is not None else BANK_ENTRY,
        region=REGION,
        page_width=PAGE_W,
        recorder=recorder,
        walker=False,
    )
    return ctx, image, recorder


def run(
    block: dict,
    *,
    layout: dict | None = None,
    entry: dict | None = None,
    y: int = Y0,
    record: bool = True,
) -> Run:
    """Draw `block` through `draw_table` and return the outcome."""
    ctx, image, recorder = context(layout if layout is not None else table_layout(), entry, record=record)
    end = draw_table(block, ctx, y)
    return Run(end, image, recorder)


def draw_row(row: dict, columns: list, *, index: int | None = 0, is_last: bool = False, **kwargs):
    """Draw one row through `_draw_row` directly; return (bottom, image, recorder)."""
    layout = kwargs.pop("layout", None) or table_layout()
    ctx, image, recorder = context(layout)
    params: dict[str, Any] = {
        "size": 18,
        "frame": "plain",
        "grouping": "none",
        "row_height": ROW_H,
        "family": FAMILY,
        "inset_y": 0,
        "cell_line_spacing": "row_height",
    }
    params.update(kwargs)
    bottom = _draw_row(row, columns, ctx, Y0, index=index, is_last=is_last, **params)
    return bottom, image, recorder


def reg(size: int = 18, *, bold: bool = False, family: str = FAMILY) -> Font:
    """Load the bank face at `size`."""
    return load_font(size, family=family, bold=bold)


def width_of(text: str, font: Font) -> int:
    """Return a string's ink width the way the primitives measure it."""
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])


def height_of(text: str, font: Font) -> int:
    """Return a string's ink height the way the primitives measure it."""
    bbox = font.getbbox(text)
    return int(bbox[3] - bbox[1])


def blank() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Return a blank page and a surface to draw the expected picture on."""
    image = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    return image, ImageDraw.Draw(image)


def same(a: Image.Image, b: Image.Image) -> bool:
    """Report whether two images are pixel-identical."""
    return ImageChops.difference(a, b).getbbox() is None


def ink_in(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """Report whether anything non-white lies inside `box` (left, top, right, bottom exclusive)."""
    return ImageChops.invert(image.convert("L").crop(box)).getbbox() is not None


def has_colour(image: Image.Image, box: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> bool:
    """Report whether any pixel inside `box` is exactly `rgb`."""
    crop = image.convert("RGB").crop(box)
    colours = crop.getcolors(maxcolors=crop.width * crop.height) or []
    return any(colour == rgb for _, colour in colours)


def paint_text(
    draw: ImageDraw.ImageDraw, column: dict, text: str, y: int, font: Font, fill="black"
) -> None:
    """Draw `text` the way a column positions it: left at its x, or right-aligned to its x_right."""
    if column.get("align") == "right":
        right = RIGHT + int(column["x_right"])
        draw.text((right - width_of(text, font), y), text, font=font, fill=fill)
    else:
        draw.text((REGION.x + int(column["x"]), y), text, font=font, fill=fill)


def paint_labels(draw: ImageDraw.ImageDraw, columns: list, y: int, font: Font) -> None:
    """Draw every column's heading on one line at `y`."""
    for column in columns:
        paint_text(draw, column, column["label"], y, font)


def provider_rows(params: dict | None = None, entry: dict | None = None) -> list[dict]:
    """Return the rows the `bank_transactions` provider yields for `entry`."""
    return get_provider("bank_transactions")(entry if entry is not None else BANK_ENTRY, params or {})


def row_text(row: dict, key: str) -> str:
    """Return the text a cell draws for `row[key]`: amounts as `$` currency, everything else verbatim."""
    value = row[key]
    return fmt_amount(value) if isinstance(value, Decimal) else str(value)


def paint_rows(
    draw: ImageDraw.ImageDraw, rows: list[dict], columns: list, *, bold: dict[int, set[str]] | None = None
) -> None:
    """Draw `rows` at a ROW_H pitch from Y0, one cell per column; `bold` names bold cells by row index."""
    for position, row in enumerate(rows):
        for column in columns:
            text = row_text(row, str(column["key"]))
            if text in ("", "NOT_FOUND"):
                continue
            is_bold = column["key"] in (bold or {}).get(position, set())
            paint_text(draw, column, text, Y0 + position * ROW_H, reg(bold=is_bold))


def blank_row(image: Image.Image, y: int, x0: int = 12, x1: int = 809) -> bool:
    """Report whether the horizontal span at `y` is entirely white."""
    return all(image.getpixel((x, y)) == WHITE for x in range(x0, x1))


def black_row(image: Image.Image, y: int, x0: int = 10, x1: int = 811) -> bool:
    """Report whether the horizontal span at `y` is entirely black."""
    return all(image.getpixel((x, y)) == BLACK for x in range(x0, x1))


def text_box(
    text: str, *, left: int | None = None, right: int | None = None, top: int, font: Font
) -> list[int]:
    """Return the box an unbudgeted cell records: anchored at `left`, or ending at `right`."""
    width = width_of(text, font)
    if left is None:
        assert right is not None
        left = right - width
    return [left, top, left + width, top + height_of(text, font)]


# --- column geometry ----------------------------------------------------------------------------------------


def test_column_x_resolves_left_offset() -> None:
    """A column with `x` anchors at the region's left edge plus that offset."""
    ctx, _, _ = context(table_layout())
    assert column_x({"x": 50}, ctx) == 60


def test_column_x_resolves_right_offset() -> None:
    """A column with `x_right` anchors at the region's right edge plus that (negative) offset."""
    ctx, _, _ = context(table_layout())
    assert column_x({"x_right": -30}, ctx) == 780
    narrower = ctx.within(Region(x=100, width=400))
    assert column_x({"x_right": -30}, narrower) == 470
    assert column_x({"x": 50}, narrower) == 150


# --- advance, recording and the synthetic row ------------------------------------------------------------


def test_table_advances_by_header_plus_rows() -> None:
    """A headed table advances by its header height plus one row height per transaction."""
    assert run(table()).y == Y0 + HEADER_H + 3 * ROW_H
    assert run(table(header=False)).y == Y0 + 3 * ROW_H
    assert run(table(header_height=44)).y == Y0 + 44 + 3 * ROW_H


def test_table_records_each_row_description() -> None:
    """Each real row's description is recorded under its indexed field at its own row's coordinates."""
    boxes = run(table()).boxes()
    for index, text in enumerate(["Quillon Hardware", "Wattle Cafe", "Banksia Fuel"]):
        top = ROW0 + index * ROW_H
        # A budgeted cell records its fitted extent, advancing by the row pitch per line.
        assert boxes[f"TRANSACTION_DESCRIPTIONS[{index}]"] == [
            210,
            top,
            210 + width_of(text, reg()),
            top + ROW_H,
        ]


def test_synthetic_opening_row_is_not_recorded() -> None:
    """The synthetic opening row is drawn but carries no ground-truth identity, so it records no box."""
    params = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
    result = run(table(params=params))
    plain = run(table())
    assert set(result.boxes()) == set(plain.boxes())
    assert set(result.boxes()) == {
        "TRANSACTION_DESCRIPTIONS[0]",
        "TRANSACTION_DESCRIPTIONS[1]",
        "TRANSACTION_DESCRIPTIONS[2]",
        "TRANSACTION_AMOUNTS_PAID[0]",
        "TRANSACTION_AMOUNTS_PAID[2]",
        "TRANSACTION_AMOUNTS_RECEIVED[1]",
        "ACCOUNT_BALANCE",
    }
    # It is nonetheless on the page: its label sits in the description column of the first band.
    assert ink_in(result.image, (200, ROW0 + 1, 500, ROW0 + ROW_H))


def test_synthetic_opening_row_does_not_shift_real_row_indices() -> None:
    """Inserting the synthetic opening row must not renumber the real rows beneath it."""
    params = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
    with_row = run(table(params=params)).boxes()
    without = run(table()).boxes()
    for index in range(3):
        key = f"TRANSACTION_DESCRIPTIONS[{index}]"
        # Same index, same text: only the vertical position moves, by exactly the synthetic row's height.
        assert with_row[key][0::2] == without[key][0::2]
        assert with_row[key][1] == without[key][1] + ROW_H
        assert with_row[key][3] == without[key][3] + ROW_H
    assert "TRANSACTION_DESCRIPTIONS[3]" not in with_row


def test_decimal_cells_format_as_amounts() -> None:
    """A Decimal cell draws through fmt_amount (`$9.50`), not as its raw string."""
    block = table(
        rows="pipe_fields",
        header=False,
        params={"fields": {"price": "LINE_ITEM_PRICES"}, "decimal_keys": ["price"]},
        columns=[{"key": "price", "label": "Price", "align": "right", "x_right": 0}],
    )
    result = run(block, entry=ITEMS_ENTRY)
    expected, draw = blank()
    for index, text in enumerate(["$2.35", "$9.50"]):
        draw.text((RIGHT - width_of(text, reg()), Y0 + index * ROW_H), text, font=reg(), fill="black")
    assert same(result.image, expected)
    raw, raw_draw = blank()
    for index, text in enumerate(["2.35", "9.5"]):
        raw_draw.text((RIGHT - width_of(text, reg()), Y0 + index * ROW_H), text, font=reg(), fill="black")
    assert not same(result.image, raw)


def test_every_frame_and_grouping_combination_renders() -> None:
    """Every frame x grouping pair draws, records every real row and advances by the documented amount."""
    for frame in FRAMES:
        none_advance = 0
        for grouping in ("none", "inline", "dedicated_row"):
            assert grouping in GROUPINGS
            block = table(frame=frame, grouping=grouping, fill_color=FILL, dividers=[{"x": 150}])
            result = run(block)
            assert ink_in(result.image, (0, Y0, PAGE_W, result.y + 1)), (frame, grouping)
            assert {f"TRANSACTION_DESCRIPTIONS[{i}]" for i in range(3)} <= set(result.boxes())
            advance = result.y - Y0
            if grouping == "none":
                none_advance = advance
                assert advance >= HEADER_H + 3 * ROW_H
            elif grouping == "inline":
                assert advance == none_advance, (frame, "inline draws no extra row")
            else:
                # Two date sub-header rows plus the default 10px gap between the two groups.
                assert advance == none_advance + 2 * ROW_H + 10, (frame, "dedicated_row")
    assert set(GROUPINGS) == {"none", "inline", "dedicated_row"}


# --- row height and row inset diagnostics -----------------------------------------------------------------


def test_missing_row_height_raises_diagnostic_with_four_elements() -> None:
    """With no row_height on the block or the layout the table fails fast with a four-part diagnostic."""
    layout = table_layout()
    del layout["row_height"]
    with pytest.raises(TableError) as exc_info:
        run(table(), layout=layout)
    assert_diagnostic_error(exc_info.value)
    assert "row_height" in str(exc_info.value)
    assert LAYOUT_ID in str(exc_info.value)


def test_dedicated_row_grouping_with_a_row_inset_is_rejected() -> None:
    """An inset moves the cells but not the bold date sub-header row grouping inserts.

    The two together have no defined meaning, so the table refuses to render them.
    """
    with pytest.raises(TableError) as exc_info:
        run(table(grouping="dedicated_row", row_inset_y=4))
    assert_diagnostic_error(exc_info.value)
    assert "row_inset_y" in str(exc_info.value)
    assert "dedicated_row" in str(exc_info.value)


def test_dedicated_row_grouping_rejects_an_inset_inherited_from_the_layout() -> None:
    """The block need not carry the inset itself: `defaults.table_row_inset_y` reaches the same code.

    That is why this cannot live in schema.py's `_validate_table`, which never sees the layout's defaults.
    """
    layout = table_layout(table_row_inset_y=4)
    with pytest.raises(TableError) as exc_info:
        run(table(grouping="dedicated_row"), layout=layout)
    assert_diagnostic_error(exc_info.value)
    assert "table_row_inset_y" in str(exc_info.value)


def test_dedicated_row_grouping_accepts_a_zero_inset() -> None:
    """An explicit zero inset alongside dedicated_row grouping is fine, even over a non-zero layout default."""
    layout = table_layout(table_row_inset_y=4)
    result = run(table(grouping="dedicated_row", row_inset_y=0), layout=layout)
    assert result.y == Y0 + HEADER_H + 2 * ROW_H + 10 + 3 * ROW_H
    assert run(table(grouping="dedicated_row", row_inset_y=0)).y == result.y


def test_a_row_inset_is_still_allowed_without_dedicated_row_grouping() -> None:
    """Grouping `none` and `inline` accept an inset, and it leaves the table's advance unchanged."""
    for grouping in ("none", "inline"):
        inset = run(table(grouping=grouping, row_inset_y=4))
        assert inset.y == run(table(grouping=grouping)).y == Y0 + HEADER_H + 3 * ROW_H


def test_block_row_height_used_when_layout_omits_it() -> None:
    """A table block's own row_height stands in for a layout that declares none, and wins over one that does."""
    layout = table_layout()
    del layout["row_height"]
    assert run(table(row_height=40), layout=layout).y == Y0 + HEADER_H + 3 * 40
    assert run(table(row_height=40)).y == Y0 + HEADER_H + 3 * 40


# --- cell formatting and geometry ------------------------------------------------------------------------


def test_debit_cell_renders_with_currency_formatting() -> None:
    """A debit cell must be drawn with the same glyphs as fmt_amount, not the raw string.

    The bank_transactions provider coerces real (non-sentinel) debit/credit amounts to Decimal so _cell_text
    formats them through fmt_amount, matching the legacy renderer's `$100.00` rather than a bare `100.00`.
    """
    boxes = run(table()).boxes()
    top = ROW0
    assert boxes["TRANSACTION_AMOUNTS_PAID[0]"] == text_box("$40.00", right=390, top=top, font=reg())
    assert boxes["TRANSACTION_AMOUNTS_PAID[2]"] == text_box(
        "$12.50", right=390, top=top + 2 * ROW_H, font=reg()
    )
    assert boxes["TRANSACTION_AMOUNTS_PAID[0]"] != text_box("40.00", right=390, top=top, font=reg())
    assert boxes["TRANSACTION_AMOUNTS_RECEIVED[1]"] == text_box(
        "$77.40", right=600, top=top + ROW_H, font=reg()
    )


def test_right_aligned_column_with_field_records_geometry() -> None:
    """Right-aligned amount columns must record geometry, same as the description column."""
    boxes = run(table()).boxes()
    left, top, right, bottom = boxes["TRANSACTION_AMOUNTS_PAID[0]"]
    assert right == RIGHT - 420
    assert (left, top, right, bottom) == tuple(text_box("$40.00", right=390, top=ROW0, font=reg()))
    assert boxes["TRANSACTION_AMOUNTS_RECEIVED[1]"][2] == RIGHT - 210


def test_synthetic_row_records_no_amount_geometry() -> None:
    """A synthetic row's amount cells are drawn but record nothing, indexed or otherwise."""
    params = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
    result = run(table(params=params))
    assert set(result.boxes()) == set(run(table()).boxes())
    # Its balance is on the page (right-aligned in the first band) ...
    synthetic_band = (RIGHT - 100, ROW0 + 1, RIGHT + 1, ROW0 + ROW_H)
    assert ink_in(result.image, synthetic_band)
    # ... yet the only balance box is the closing balance, on the final real row, not on the synthetic row.
    closing = result.boxes()["ACCOUNT_BALANCE"]
    assert closing[1] == ROW0 + ROW_H + 2 * ROW_H


def test_amount_field_index_stability_with_and_without_synthetic_row() -> None:
    """Real rows must keep identical FIELD[i] keys whether or not a synthetic row precedes them."""
    params = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
    with_row = run(table(params=params)).boxes()
    without = run(table()).boxes()
    assert set(with_row) == set(without)
    for key in (
        "TRANSACTION_AMOUNTS_PAID[0]",
        "TRANSACTION_AMOUNTS_PAID[2]",
        "TRANSACTION_AMOUNTS_RECEIVED[1]",
    ):
        assert with_row[key][0::2] == without[key][0::2]
        assert with_row[key][1] == without[key][1] + ROW_H
        assert with_row[key][3] == without[key][3] + ROW_H


def test_right_aligned_budgeted_column_is_not_silently_ignored() -> None:
    """A `budget` on a right-aligned column must actually fit/record, not go inert."""
    entry: dict[str, Any] = copy.deepcopy(BANK_ENTRY)
    entry["fields"]["TRANSACTION_AMOUNTS_PAID"] = "123456789.50|NOT_FOUND|12.5"
    columns = [COL_DATE, COL_DESC, {**COL_DEBIT, "budget": "AMT"}]
    result = run(table(columns=columns), entry=entry)
    text = "$123,456,789.50"
    fitted = fit_text(
        text, width=100, fit="shrink", min_font=10, max_lines=1, nominal_size=18, family=FAMILY
    )
    assert fitted.size < 18, "the budget must discriminate: this amount cannot fit at the nominal size"
    fitted_width = width_of(text, reg(fitted.size))
    assert fitted_width <= 100
    # The fitted right edge is the column anchor and the box spans exactly the shrunk text over one row pitch.
    assert result.boxes()["TRANSACTION_AMOUNTS_PAID[0]"] == [390 - fitted_width, ROW0, 390, ROW0 + ROW_H]
    assert fitted_width < width_of(text, reg())
    # An unbudgeted twin draws the same amount at the nominal size, wider than the budget.
    twin = run(table(), entry=entry).boxes()["TRANSACTION_AMOUNTS_PAID[0]"]
    assert twin[2] - twin[0] == width_of(text, reg()) > 100


def test_last_row_field_records_once_unindexed_on_final_real_row() -> None:
    """`last_row_field` records the closing balance once, unindexed, on the final row, and only there."""
    boxes = run(table()).boxes()
    assert boxes["ACCOUNT_BALANCE"] == text_box("$300.00", right=RIGHT, top=ROW0 + 2 * ROW_H, font=reg())
    assert [key for key in boxes if key.startswith("ACCOUNT_BALANCE")] == ["ACCOUNT_BALANCE"]


def test_last_row_field_still_targets_the_final_real_row_with_synthetic_prepended() -> None:
    """A prepended synthetic row must not shift which row counts as 'last'.

    Absolute pixel position legitimately shifts down by one row height (the synthetic row occupies space
    above it); the invariant under test is that the closing balance is still recorded exactly once,
    unindexed, and that its horizontal extent (unaffected by the vertical shift) matches.
    """
    params = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
    with_row = run(table(params=params)).boxes()["ACCOUNT_BALANCE"]
    without = run(table()).boxes()["ACCOUNT_BALANCE"]
    assert with_row[0::2] == without[0::2]
    assert with_row[1] == without[1] + ROW_H
    assert with_row == text_box("$300.00", right=RIGHT, top=ROW0 + 3 * ROW_H, font=reg())


def test_frame_and_grouping_axes_compose_into_distinct_output() -> None:
    """Neither axis may be a no-op, and combinations must not collapse onto each other.

    This replaces the old single-enum "every style draws differently" check: with two independent axes,
    distinctness has to be demonstrated across combinations, not just across a flat list of named styles.
    """
    images = {}
    for frame in FRAMES:
        for grouping in GROUPINGS:
            block = table(frame=frame, grouping=grouping, fill_color=FILL)
            images[(frame, grouping)] = run(block).image.tobytes()
    assert len(images) == len(FRAMES) * len(GROUPINGS)
    assert len(set(images.values())) == len(images), "two combinations drew the same picture"


# --- currency, grouping and dividers ----------------------------------------------------------------------


def test_currency_plain_strips_dollar_sign() -> None:
    """A `currency: plain` column must drop the $ prefix fmt_amount otherwise adds."""
    columns = [COL_DATE, COL_DESC, {**COL_DEBIT, "currency": "plain"}]
    boxes = run(table(columns=columns)).boxes()
    assert boxes["TRANSACTION_AMOUNTS_PAID[0]"] == text_box("40.00", right=390, top=ROW0, font=reg())
    assert width_of("$40.00", reg()) > width_of("40.00", reg())


def test_inline_grouping_blanks_repeated_date_and_skips_its_divider() -> None:
    """Two same-date rows: the second must draw no date text and no divider above it."""
    result = run(table(frame="bordered", grouping="inline", columns=DATE_DESC))
    image = result.image
    row1, row2 = ROW0 + ROW_H, ROW0 + 2 * ROW_H
    assert ink_in(image, (12, ROW0 + 1, 190, row1))  # first row of the group carries the date
    assert not ink_in(image, (12, row1 + 1, 190, row2))  # the repeat is blank
    assert blank_row(image, row1)  # and no divider is drawn above it
    assert black_row(image, row2)  # the new date's row is divided from the one above
    assert ink_in(image, (12, row2 + 1, 190, row2 + ROW_H))  # and carries its own date


def test_inline_grouping_does_not_insert_a_dedicated_date_row() -> None:
    """Unlike `dedicated_row`, `inline` grouping must draw exactly one row per transaction."""
    inline = run(table(grouping="inline"))
    dedicated = run(table(grouping="dedicated_row"))
    assert inline.y == Y0 + HEADER_H + 3 * ROW_H
    assert dedicated.y == inline.y + 2 * ROW_H + 10
    for index in range(3):
        assert inline.boxes()[f"TRANSACTION_DESCRIPTIONS[{index}]"][1] == ROW0 + index * ROW_H
    # dedicated_row pushes each group's rows down by the sub-header rows (and gap) above them.
    tops = [dedicated.boxes()[f"TRANSACTION_DESCRIPTIONS[{i}]"][1] for i in range(3)]
    assert tops == [ROW0 + ROW_H, ROW0 + 2 * ROW_H, ROW0 + 4 * ROW_H + 10]


def test_dividers_are_drawn_across_header_and_body() -> None:
    """Divider x-positions must appear as vertical line calls spanning the table."""
    dividers = [{"x": 150}, {"x_right": -320}]
    result = run(table(frame="bordered", dividers=dividers))
    for x in (REGION.x + 150, RIGHT - 320):
        for y in range(Y0, result.y + 1):
            assert result.image.getpixel((x, y)) == BLACK, (x, y)
    assert result.y == ROW0 + 3 * ROW_H
    bare = run(table(frame="bordered"))
    assert bare.image.getpixel((REGION.x + 150, ROW0 + 15)) == WHITE


def test_multiline_header_label_draws_each_line() -> None:
    """A label containing a newline draws one line per segment, one line advance apart."""
    column = {**COL_DATE, "label": "Date of\nTransaction"}
    result = run(table(columns=[column]), entry=EMPTY_ENTRY)
    expected, draw = blank()
    bold = reg(bold=True)
    draw.text((10, Y0), "Date of", font=bold, fill="black")
    draw.text((10, Y0 + HEADER_H), "Transaction", font=bold, fill="black")
    assert same(result.image, expected)
    first_only, first_draw = blank()
    first_draw.text((10, Y0), "Date of", font=bold, fill="black")
    assert not same(result.image, first_only)
    assert result.y == Y0 + HEADER_H


# --- filled frame -----------------------------------------------------------------------------------------


def test_filled_frame_fills_the_header_bar_with_fill_color() -> None:
    """The header bar itself must be filled with fill_color, not merely outlined."""
    result = run(table(frame="filled", fill_color=FILL, header_height=30), entry=EMPTY_ENTRY)
    expected, draw = blank()
    draw.rectangle([(REGION.x, Y0), (RIGHT, Y0 + 30)], fill=FILL)
    paint_labels(draw, BANK_COLUMNS, Y0 + (30 - HEADER_H) // 2, reg(bold=True))
    assert same(result.image, expected)
    assert result.image.getpixel((450, Y0 + 15)) == FILL_RGB
    assert result.image.getpixel((450, Y0 + 31)) == WHITE


def test_filled_frame_fills_each_dedicated_group_row_not_only_the_header() -> None:
    """NAB fills the header bar AND each date sub-header row it inserts.

    ENTRY has two distinct dates, so dedicated_row grouping inserts two date sub-header rows; each must be
    filled in addition to the header bar.
    """
    result = run(table(frame="filled", grouping="dedicated_row", fill_color=FILL, columns=DATE_DESC))
    image = result.image
    first_group = ROW0  # directly under the header
    second_group = ROW0 + ROW_H + 2 * ROW_H + 10  # two rows, then the default gap
    assert image.getpixel((800, Y0 + 5)) == FILL_RGB  # the header bar
    for group in (first_group, second_group):
        assert image.getpixel((800, group + 15)) == FILL_RGB
        assert image.getpixel((800, group + ROW_H)) == FILL_RGB
        assert image.getpixel((800, group + ROW_H + 1)) == WHITE
    assert image.getpixel((800, second_group - 5)) == WHITE  # the gap between the groups is not filled
    assert image.getpixel((800, ROW0 + ROW_H + 15)) == WHITE  # transaction rows are not filled


def test_filled_frame_with_none_grouping_only_fills_the_header() -> None:
    """No dedicated row exists without dedicated_row grouping, so nothing else fills."""
    result = run(table(frame="filled", grouping="none", fill_color=FILL))
    assert has_colour(result.image, (0, Y0, PAGE_W, Y0 + HEADER_H + 1), FILL_RGB)
    assert not has_colour(result.image, (0, Y0 + HEADER_H + 1, PAGE_W, PAGE_H), FILL_RGB)


def test_filled_frame_draws_no_dividers() -> None:
    """NAB's header bar has no interior column dividers, unlike `bordered`."""
    dividers = [{"x": 150}]
    filled = run(table(frame="filled", fill_color=FILL, dividers=dividers))
    x = REGION.x + 150
    assert all(filled.image.getpixel((x, y)) != BLACK for y in range(Y0, filled.y + 1))
    bordered = run(table(frame="bordered", dividers=dividers))
    assert all(bordered.image.getpixel((x, y)) == BLACK for y in range(Y0, bordered.y + 1))


def test_fill_inset_absent_leaves_the_group_row_fill_at_full_row_height() -> None:
    """With no fill_inset the group row's fill spans the whole row height."""
    result = run(table(frame="filled", grouping="dedicated_row", fill_color=FILL, columns=DATE_DESC))
    assert result.image.getpixel((800, ROW0 + ROW_H)) == FILL_RGB
    assert result.image.getpixel((800, ROW0 + ROW_H + 1)) == WHITE


def test_fill_inset_shrinks_the_group_row_fill_by_the_given_pixels() -> None:
    """A fill_inset of 6 stops the group row's fill 6px short of the row height."""
    result = run(
        table(frame="filled", grouping="dedicated_row", fill_color=FILL, fill_inset=6, columns=DATE_DESC)
    )
    assert result.image.getpixel((800, ROW0 + ROW_H - 6)) == FILL_RGB
    assert result.image.getpixel((800, ROW0 + ROW_H - 5)) == WHITE
    # The row's advance is the full row height all the same: the second group starts where it always did.
    assert result.y == Y0 + HEADER_H + 2 * ROW_H + 10 + 3 * ROW_H


def test_fill_height_absent_fills_the_full_header_height() -> None:
    """Without fill_height the bar is as tall as the header (the rectangle's edge row is included)."""
    result = run(table(frame="filled", fill_color=FILL, header_height=44), entry=EMPTY_ENTRY)
    assert result.image.getpixel((800, Y0 + 44)) == FILL_RGB
    assert result.image.getpixel((800, Y0 + 45)) == WHITE
    assert result.y == Y0 + 44


def test_fill_height_overrides_the_fill_without_changing_the_row_advance() -> None:
    """NAB fills a 44px bar but advances 50px; header_height alone cannot express that gap."""
    result = run(
        table(frame="filled", fill_color=FILL, header_height=50, fill_height=44), entry=EMPTY_ENTRY
    )
    assert result.image.getpixel((800, Y0 + 44)) == FILL_RGB
    assert result.image.getpixel((800, Y0 + 45)) == WHITE
    assert result.y == Y0 + 50
    assert run(table(frame="filled", fill_color=FILL, header_height=50), entry=EMPTY_ENTRY).y == Y0 + 50


def test_label_inset_y_absent_uses_computed_centring() -> None:
    """Unchanged default: filled/bordered headers centre the label block."""
    result = run(table(frame="filled", fill_color=FILL, header_height=44), entry=EMPTY_ENTRY)
    expected, draw = blank()
    draw.rectangle([(REGION.x, Y0), (RIGHT, Y0 + 44)], fill=FILL)
    paint_labels(draw, BANK_COLUMNS, Y0 + (44 - HEADER_H) // 2, reg(bold=True))
    assert same(result.image, expected)
    pinned, pinned_draw = blank()
    pinned_draw.rectangle([(REGION.x, Y0), (RIGHT, Y0 + 44)], fill=FILL)
    paint_labels(pinned_draw, BANK_COLUMNS, Y0, reg(bold=True))
    assert not same(result.image, pinned)


# --- gaps and synthetic-row placement ------------------------------------------------------------------------


def test_group_gap_default_matches_the_old_hardcoded_ten_pixels() -> None:
    """Absent group_gap must reproduce today's CBA behaviour exactly (10px)."""
    default = run(table(grouping="dedicated_row"))
    assert default.y == Y0 + HEADER_H + 2 * ROW_H + 10 + 3 * ROW_H
    assert same(default.image, run(table(grouping="dedicated_row", group_gap=10)).image)


def test_group_gap_zero_matches_nabs_real_behaviour() -> None:
    """NAB's legacy renderer inserts no gap between consecutive date groups."""
    result = run(table(grouping="dedicated_row", group_gap=0))
    assert result.y == Y0 + HEADER_H + 2 * ROW_H + 3 * ROW_H
    tops = [result.boxes()[f"TRANSACTION_DESCRIPTIONS[{i}]"][1] for i in range(3)]
    assert tops == [ROW0 + ROW_H, ROW0 + 2 * ROW_H, ROW0 + 4 * ROW_H]


LEADING = {"opening_balance": True, "opening_balance_label": "Opening Balance"}
DEFERRED = {"brought_forward": True, "brought_forward_label": "Brought forward"}


def test_synthetic_row_placement_defaults_to_leading() -> None:
    """Absent synthetic_row_placement: the synthetic row renders before any group header."""
    result = run(table(grouping="dedicated_row", params=LEADING, columns=DATE_DESC))
    first, second = ROW0, ROW0 + ROW_H
    assert ink_in(result.image, (200, first + 1, 500, first + ROW_H))  # the synthetic label
    assert not ink_in(result.image, (12, first + 1, 190, first + ROW_H))  # no date header yet
    assert ink_in(result.image, (12, second + 1, 190, second + ROW_H))  # the group header follows
    assert not ink_in(result.image, (200, second + 1, 500, second + ROW_H))


def test_synthetic_row_placement_after_first_group_header() -> None:
    """NAB's real order: the group header for the first date, then Brought forward."""
    block = table(
        grouping="dedicated_row",
        params=DEFERRED,
        columns=DATE_DESC,
        synthetic_row_placement="after_first_group_header",
    )
    result = run(block)
    first, second = ROW0, ROW0 + ROW_H
    assert ink_in(result.image, (12, first + 1, 190, first + ROW_H))  # the group header comes first
    assert not ink_in(result.image, (200, first + 1, 500, first + ROW_H))
    assert ink_in(result.image, (200, second + 1, 500, second + ROW_H))  # then the synthetic label
    assert not ink_in(result.image, (12, second + 1, 190, second + ROW_H))
    assert result.y == run(table(grouping="dedicated_row", params=LEADING)).y


def test_synthetic_row_placement_after_first_group_header_is_not_recorded() -> None:
    """The deferred synthetic row still carries no ground-truth identity."""
    block = table(
        grouping="dedicated_row", params=DEFERRED, synthetic_row_placement="after_first_group_header"
    )
    result = run(block)
    assert set(result.boxes()) == set(run(table(grouping="dedicated_row")).boxes())
    # Group header, then the synthetic row, then the first real row: indices are not renumbered.
    assert result.boxes()["TRANSACTION_DESCRIPTIONS[0]"][1] == ROW0 + 2 * ROW_H
    assert "TRANSACTION_DESCRIPTIONS[3]" not in result.boxes()


# --- currency suffix, bold and last-row targeting ------------------------------------------------------------


def test_currency_suffix_appends_after_the_formatted_amount() -> None:
    """A column's currency_suffix is appended after the formatted amount (NAB's `Cr`)."""
    columns = [COL_DATE, COL_DESC, {**COL_BALANCE, "currency_suffix": "Cr"}]
    box = run(table(columns=columns)).boxes()["ACCOUNT_BALANCE"]
    assert box == text_box("$300.00 Cr", right=RIGHT, top=ROW0 + 2 * ROW_H, font=reg())


def carried_forward_block(column: dict, **overrides) -> dict:
    """Return a headerless table of one description column and a trailing bold 'Closing' row."""
    params = {"carried_forward": True, "carried_forward_label": "Closing"}
    return table(header=False, params=params, columns=[column], **overrides)


DESCRIPTIONS = ["Quillon Hardware", "Wattle Cafe", "Banksia Fuel", "Closing"]


def paint_descriptions(draw: ImageDraw.ImageDraw, x: int, *, bold_from: int | None) -> None:
    """Draw the four descriptions at a ROW_H pitch, bold from row `bold_from` on."""
    for position, text in enumerate(DESCRIPTIONS):
        is_bold = bold_from is not None and position >= bold_from
        draw.text((x, Y0 + position * ROW_H), text, font=reg(bold=is_bold), fill="black")


def test_bold_row_uses_bold_font_for_unbudgeted_cells() -> None:
    """A row a provider marks with `bold: True` (e.g. carried_forward) draws in bold, unlike ordinary rows.

    Traced against legacy's font_body_bold Carried-forward row.
    """
    result = run(carried_forward_block(PLAIN_DESC))
    expected, draw = blank()
    paint_descriptions(draw, 10, bold_from=3)
    assert same(result.image, expected)
    wrong, wrong_draw = blank()
    paint_descriptions(wrong_draw, 10, bold_from=None)
    assert not same(result.image, wrong)


def test_bold_row_threads_through_the_budgeted_path_too() -> None:
    """A budgeted (fitted) cell must also honour the row's bold flag, not just plain cells."""
    column = {"key": "description", "label": "Description", "align": "left", "x": 0, "budget": "DESC"}
    result = run(carried_forward_block(column))
    expected, draw = blank()
    paint_descriptions(draw, 10, bold_from=3)
    assert same(result.image, expected)
    wrong, wrong_draw = blank()
    paint_descriptions(wrong_draw, 10, bold_from=None)
    assert not same(result.image, wrong)


def test_rows_without_a_bold_key_render_regular_as_before() -> None:
    """Absent `bold` on a row (every row type except carried_forward): unchanged behaviour."""
    result = run(table(header=False, columns=[PLAIN_DESC]))
    expected, draw = blank()
    for position, text in enumerate(DESCRIPTIONS[:3]):
        draw.text((10, Y0 + position * ROW_H), text, font=reg(), fill="black")
    assert same(result.image, expected)
    bold, bold_draw = blank()
    for position, text in enumerate(DESCRIPTIONS[:3]):
        bold_draw.text((10, Y0 + position * ROW_H), text, font=reg(bold=True), fill="black")
    assert not same(result.image, bold)


def test_currency_suffix_absent_by_default() -> None:
    """COLUMNS' balance column has no currency_suffix, so it must render unchanged."""
    box = run(table()).boxes()["ACCOUNT_BALANCE"]
    assert box == text_box("$300.00", right=RIGHT, top=ROW0 + 2 * ROW_H, font=reg())


def test_is_last_targets_a_trailing_synthetic_row_over_the_last_real_row() -> None:
    """carried_forward's trailing row, not the last transaction, gets last_row_field."""
    params = {"carried_forward": True, "carried_forward_label": "Closing"}
    boxes = run(table(params=params)).boxes()
    # The carried-forward row is bold, so the recorded extent is the bold glyphs' width.
    assert boxes["ACCOUNT_BALANCE"] == text_box(
        "$300.00", right=RIGHT, top=ROW0 + 3 * ROW_H, font=reg(bold=True)
    )
    assert [key for key in boxes if key.startswith("ACCOUNT_BALANCE")] == ["ACCOUNT_BALANCE"]


def test_is_last_without_carried_forward_still_targets_the_last_real_row() -> None:
    """No trailing synthetic row: unchanged behaviour, same as before this round."""
    boxes = run(table()).boxes()
    assert boxes["ACCOUNT_BALANCE"] == text_box("$300.00", right=RIGHT, top=ROW0 + 2 * ROW_H, font=reg())


# --- sub-lines -----------------------------------------------------------------------------------------------


SUB_LINE = {"key": "reference", "role": "sub", "color": "#999999", "offset_y": 18, "height": 12}
REFERENCES = {
    "references": True,
    "reference_prefix": "Ref: ",
    "reference_pad_char": ".",
    "reference_pad_width": 3,
}


def test_sub_line_draws_beneath_the_main_cell_and_extends_row_height() -> None:
    """A column's sub_line renders its own text and adds its declared height once."""
    column = {**PLAIN_DESC, "sub_line": SUB_LINE}
    result = run(table(header=False, params=REFERENCES, columns=[column]))
    rows = provider_rows(REFERENCES)
    pitch = ROW_H + 12
    expected, draw = blank()
    for position, row in enumerate(rows):
        top = Y0 + position * pitch
        draw.text((10, top), row["description"], font=reg(), fill="black")
        draw.text((10, top + 18), row["reference"], font=reg(14), fill="#999999")
    assert same(result.image, expected)
    assert result.y == Y0 + 3 * pitch


def test_sub_line_absent_when_row_has_no_data_for_its_key() -> None:
    """Without `references: true`, no row carries a `reference` key: no sub_line, no extra height."""
    column = {**PLAIN_DESC, "sub_line": SUB_LINE}
    result = run(table(header=False, columns=[column]))
    assert result.y == Y0 + 3 * ROW_H
    assert same(result.image, run(table(header=False, columns=[PLAIN_DESC])).image)


def test_label_inset_y_overrides_computed_centring() -> None:
    """NAB's real offset (y+10 inside a 44px bar) which the centring formula cannot reproduce."""
    result = run(
        table(frame="filled", fill_color=FILL, header_height=44, label_inset_y=10), entry=EMPTY_ENTRY
    )
    expected, draw = blank()
    draw.rectangle([(REGION.x, Y0), (RIGHT, Y0 + 44)], fill=FILL)
    paint_labels(draw, BANK_COLUMNS, Y0 + 10, reg(bold=True))
    assert same(result.image, expected)
    centred = run(table(frame="filled", fill_color=FILL, header_height=44), entry=EMPTY_ENTRY)
    assert not same(result.image, centred.image)


# --- ruled frame and header rules ----------------------------------------------------------------------------


def test_ruled_frame_draws_no_line_under_each_row() -> None:
    """Legacy CBA and ANZ both draw zero per-row rules; only filled/bordered frames decorate rows."""
    result = run(table(frame="ruled", columns=DATE_DESC))
    top_rule, below_rule = Y0, Y0 + 12 + HEADER_H
    assert black_row(result.image, top_rule)
    assert black_row(result.image, below_rule)
    assert result.y == below_rule + 16 + 3 * ROW_H
    for y in range(below_rule + 1, result.y + 1):
        assert result.image.getpixel((800, y)) == WHITE, y


def test_header_rule_top_false_skips_the_rule_above_the_labels() -> None:
    """With header_rule_top false there is no rule above the labels, and no 12px advance for it."""
    result = run(table(frame="ruled", header_rule_top=False), entry=EMPTY_ENTRY)
    assert blank_row(result.image, Y0)
    assert black_row(result.image, Y0 + HEADER_H)
    assert result.y == Y0 + HEADER_H + 16


def test_header_rule_top_defaults_true_and_draws_both_rules() -> None:
    """By default the rule above the labels is drawn, and the labels sit 12px below it."""
    result = run(table(frame="ruled"), entry=EMPTY_ENTRY)
    assert black_row(result.image, Y0)
    assert black_row(result.image, Y0 + 12 + HEADER_H)
    assert result.y == Y0 + 12 + HEADER_H + 16
    expected, draw = blank()
    draw.line([(REGION.x, Y0), (RIGHT, Y0)], fill="black", width=1)
    paint_labels(draw, BANK_COLUMNS, Y0 + 12, reg(bold=True))
    draw.line([(REGION.x, Y0 + 12 + HEADER_H), (RIGHT, Y0 + 12 + HEADER_H)], fill="black", width=1)
    assert same(result.image, expected)
    unshifted, unshifted_draw = blank()
    unshifted_draw.line([(REGION.x, Y0), (RIGHT, Y0)], fill="black", width=1)
    paint_labels(unshifted_draw, BANK_COLUMNS, Y0, reg(bold=True))
    unshifted_draw.line(
        [(REGION.x, Y0 + 12 + HEADER_H), (RIGHT, Y0 + 12 + HEADER_H)], fill="black", width=1
    )
    assert not same(result.image, unshifted)


def test_header_rule_gap_overrides_the_default_16px_advance() -> None:
    """ANZ's below-rule advance is 14, not CBA's 16."""
    default = run(table(frame="ruled"), entry=EMPTY_ENTRY)
    anz = run(table(frame="ruled", header_rule_gap=14), entry=EMPTY_ENTRY)
    assert default.y - anz.y == 2
    assert anz.y == Y0 + 12 + HEADER_H + 14
    assert same(default.image, anz.image)


# --- rule_above ----------------------------------------------------------------------------------------------


def test_rule_above_draws_a_black_rule_before_the_row_with_a_12px_gap() -> None:
    """A row marked `rule_above` is separated by a fresh black rule, then drawn 12px below it."""
    params = {"label": "Totals at end of period"}
    columns = [PLAIN_DESC, {**COL_DEBIT}, {**COL_CREDIT}]
    result = run(table(rows="bank_transaction_totals", header=False, params=params, columns=columns))
    totals = get_provider("bank_transaction_totals")(BANK_ENTRY, params)[0]
    expected, draw = blank()
    draw.line([(REGION.x, Y0), (RIGHT, Y0)], fill="black", width=1)
    bold = reg(bold=True)
    draw.text((10, Y0 + 12), "Totals at end of period", font=bold, fill="black")
    paint_text(draw, COL_DEBIT, fmt_amount(totals["debit"]), Y0 + 12, bold)
    paint_text(draw, COL_CREDIT, fmt_amount(totals["credit"]), Y0 + 12, bold)
    assert same(result.image, expected)
    assert result.y == Y0 + 12 + ROW_H
    assert fmt_amount(totals["debit"]) == "$52.50"
    assert fmt_amount(totals["credit"]) == "$77.40"


def test_rule_above_absent_by_default_draws_no_rule() -> None:
    """Ordinary rows draw no rule above themselves."""
    result = run(table(header=False, columns=DATE_DESC))
    for y in range(Y0, result.y):
        assert result.image.getpixel((800, y)) == WHITE
    assert not black_row(result.image, Y0)
    assert result.y == Y0 + 3 * ROW_H


# --- capture -------------------------------------------------------------------------------------------------


def test_capture_false_records_no_field_boxes() -> None:
    """tax_invoice_mixed renders the same line items twice. The second table must not record boxes.

    One ground-truth value has one box.
    """
    assert run(table(capture=False)).boxes() == {}


def test_capture_default_true_still_records_field_boxes() -> None:
    """Default (capture omitted) must keep recording: capture: false is opt-in.

    It is not a silent behaviour change for every other table.
    """
    boxes = run(table()).boxes()
    assert "TRANSACTION_DESCRIPTIONS[0]" in boxes
    assert "ACCOUNT_BALANCE" in boxes
    assert run(table(capture=True)).boxes() == boxes


def test_capture_false_still_renders_the_table_geometry() -> None:
    """capture: false suppresses recording only.

    The table itself still draws (same y-advance and same pixels as the default), so it is not a silent
    no-op render.
    """
    quiet = run(table(capture=False))
    recorded = run(table())
    assert quiet.y == recorded.y == Y0 + HEADER_H + 3 * ROW_H
    assert same(quiet.image, recorded.image)
    assert ink_in(quiet.image, (REGION.x, Y0, RIGHT, quiet.y))


def test_bank_transaction_totals_row_renders_through_the_table() -> None:
    """The totals provider's single row draws through the table, and records nothing (it is synthetic)."""
    params = {"label": "Totals at end of period"}
    result = run(table(rows="bank_transaction_totals", header=False, params=params))
    assert result.boxes() == {}
    assert result.y == Y0 + 12 + ROW_H
    assert black_row(result.image, Y0)
    assert ink_in(result.image, (200, Y0 + 12, 500, Y0 + 12 + ROW_H))
    assert ink_in(result.image, (RIGHT - 420 - 100, Y0 + 12, RIGHT - 420 + 1, Y0 + 12 + ROW_H))


# --- per-cell bold ------------------------------------------------------------------------------------------


BOLD_COLUMNS = [PLAIN_DESC, PLAIN_BALANCE]


def paint_bold_rows(rows: list[dict], bold: dict[int, set[str]]) -> Image.Image:
    """Draw `rows` description and balance by hand, bold where `bold` names a cell."""
    expected, draw = blank()
    paint_rows(draw, rows, BOLD_COLUMNS, bold=bold)
    return expected


def test_cell_bold_collection_bolds_only_the_named_column() -> None:
    """ANZ's 'BALANCE BROUGHT FORWARD' row: label bold, balance value not.

    A single row with two different font weights, which only a per-column (not per-row) bold spec can
    express.
    """
    params = {
        "brought_forward": True,
        "brought_forward_label": "BALANCE BROUGHT FORWARD",
        "brought_forward_bold": ["description"],
    }
    result = run(table(header=False, params=params, columns=BOLD_COLUMNS))
    rows = provider_rows(params)
    assert same(result.image, paint_bold_rows(rows, {0: {"description"}}))
    assert not same(result.image, paint_bold_rows(rows, {0: {"description", "balance"}}))
    assert not same(result.image, paint_bold_rows(rows, {}))


def test_cell_bold_true_still_bolds_every_cell() -> None:
    """The whole-row form (NAB's Carried forward, ANZ's totals row) is unchanged."""
    params = {"carried_forward": True, "carried_forward_label": "Carried forward"}
    result = run(table(header=False, params=params, columns=BOLD_COLUMNS))
    rows = provider_rows(params)
    assert same(result.image, paint_bold_rows(rows, {3: {"description", "balance"}}))
    assert not same(result.image, paint_bold_rows(rows, {3: {"description"}}))


def test_cell_bold_empty_collection_bolds_nothing_and_does_not_crash() -> None:
    """An empty bold collection renders every cell regular (and does not raise)."""
    row = {"description": "Plain row", "balance": Decimal("5"), "synthetic": False}
    expected, draw = blank()
    paint_text(draw, PLAIN_DESC, "Plain row", Y0, reg())
    paint_text(draw, PLAIN_BALANCE, "$5.00", Y0, reg())
    empties: list[Any] = [set(), [], ()]
    for empty in empties:
        _, image, _ = draw_row({**row, "bold": empty}, BOLD_COLUMNS)
        assert same(image, expected)


def test_cell_bold_unknown_column_name_raises_four_element_diagnostic() -> None:
    """A misspelled *_bold entry (e.g. a params: typo) must fail loudly.

    Silently rendering the row entirely unbolded is indistinguishable, at a glance, from the typo never
    having been caught at all.
    """
    params = {
        "brought_forward": True,
        "brought_forward_label": "Brought forward",
        "brought_forward_bold": ["descripton"],
    }
    with pytest.raises(TableError) as exc_info:
        run(table(header=False, params=params, columns=BOLD_COLUMNS))
    assert_diagnostic_error(exc_info.value)
    assert "descripton" in str(exc_info.value)
    assert "description" in str(exc_info.value)


def test_cell_bold_unknown_column_name_raises_before_drawing_any_cell() -> None:
    """The check happens before the row's cells draw.

    A bad row is rejected outright, not partially rendered then rejected.
    """
    params = {
        "brought_forward": True,
        "brought_forward_label": "Brought forward",
        "brought_forward_bold": ["descripton"],
    }
    ctx, image, _ = context(table_layout())
    with pytest.raises(TableError):
        draw_table(table(header=False, params=params, columns=BOLD_COLUMNS), ctx, Y0)
    assert not ink_in(image, (0, 0, PAGE_W, PAGE_H))


def test_rule_above_does_not_draw_before_an_invalid_bold_spec_raises() -> None:
    """A row combining rule_above with an invalid bold collection must raise before marking the canvas.

    The separator rule must not be drawn first.
    """
    row = {"description": "Totals", "balance": Decimal("5"), "rule_above": True, "bold": {"nope"}}
    ctx, image, _ = context(table_layout())
    with pytest.raises(TableError) as exc_info:
        _draw_row(
            row,
            BOLD_COLUMNS,
            ctx,
            Y0,
            size=18,
            frame="plain",
            grouping="none",
            row_height=ROW_H,
            index=None,
            is_last=False,
            family=FAMILY,
            inset_y=0,
            cell_line_spacing="row_height",
        )
    assert_diagnostic_error(exc_info.value)
    assert "nope" in str(exc_info.value)
    assert not ink_in(image, (0, 0, PAGE_W, PAGE_H))


# --- face, prefix and role ------------------------------------------------------------------------------------


def test_budgeted_cell_honours_the_layouts_monospace_face() -> None:
    """A budgeted cell used to omit mono= entirely, silently drawing every receipt line item in the sans face."""
    column = {"key": "description", "label": "Description", "align": "left", "x": 0, "budget": "DESC"}
    mono = run(table(header=False, columns=[column]), layout=table_layout(family="liberation_mono"))
    expected, draw = blank()
    for position, text in enumerate(DESCRIPTIONS[:3]):
        font = reg(family="liberation_mono")
        draw.text((10, Y0 + position * ROW_H), text, font=font, fill="black")
    assert same(mono.image, expected)
    sans = run(table(header=False, columns=[column]))
    assert not same(mono.image, sans.image)


PREFIX_COLUMNS = [
    {
        "key": "description",
        "label": "Description",
        "align": "left",
        "x": 0,
        "budget": "DESC",
        "field": "LINE_ITEM_DESCRIPTIONS",
        "prefix_key": "quantity_prefix",
        "prefix_field": "LINE_ITEM_QUANTITIES",
    },
    {"key": "total", "label": "Amount", "align": "right", "x_right": 0, "field": "LINE_ITEM_TOTAL_PRICES"},
]
PREFIX_ROW = {
    "description": "3x Quokka Biscuits",
    "quantity_prefix": "3x ",
    "total": "7.05",
    "synthetic": False,
}


def test_prefix_key_records_the_prefixs_own_box_inside_the_cell() -> None:
    """The prefix's own sub-box starts at the cell's left edge and is narrower than the cell's box."""
    _, _, recorder = draw_row(PREFIX_ROW, PREFIX_COLUMNS, index=0)
    assert recorder is not None
    boxes = recorder.as_dict()
    cell = boxes["LINE_ITEM_DESCRIPTIONS[0]"]
    prefix = boxes["LINE_ITEM_QUANTITIES[0]"]
    assert prefix[0] == cell[0]
    assert prefix[2] < cell[2]
    assert prefix[1] == cell[1]
    assert prefix[3] <= cell[3]


def test_prefix_is_not_recorded_when_the_row_has_none() -> None:
    """A row carrying no prefix records no prefix box."""
    row = {**PREFIX_ROW, "description": "Lamp", "quantity_prefix": ""}
    _, _, recorder = draw_row(row, PREFIX_COLUMNS, index=0)
    assert recorder is not None
    assert set(recorder.as_dict()) == {"LINE_ITEM_DESCRIPTIONS[0]", "LINE_ITEM_TOTAL_PRICES[0]"}


def test_prefix_is_not_recorded_on_a_synthetic_row() -> None:
    """Synthetic rows carry no index and are never recorded.

    The prefix box must follow the same rule as the cell's own `field`.
    """
    row = {**PREFIX_ROW, "synthetic": True}
    _, _, recorder = draw_row(row, PREFIX_COLUMNS, index=None)
    assert recorder is not None and recorder.as_dict() == {}
    # The same row as a real, indexed row does record both, so the empty result is the rule, not a blind spot.
    _, _, control = draw_row({**row, "synthetic": False}, PREFIX_COLUMNS, index=0)
    assert control is not None
    assert {"LINE_ITEM_DESCRIPTIONS[0]", "LINE_ITEM_QUANTITIES[0]"} <= set(control.as_dict())


UNIT_FORMAT = "  {quantity} @ ${price}"


def items_block(**overrides) -> dict:
    """Return a headerless receipt line-items table whose description column carries a unit sub-line."""
    columns = [
        {
            "key": "description",
            "label": "Description",
            "align": "left",
            "x": 0,
            "budget": "DESC",
            "field": "LINE_ITEM_DESCRIPTIONS",
            "sub_line": {
                "key": "unit_line",
                "budget": "UNIT",
                "offset_y": ROW_H,
                "height": ROW_H,
                "span_fields": {
                    "key": "unit_line_spans",
                    "fields": {"quantity": "LINE_ITEM_QUANTITIES", "price": "LINE_ITEM_PRICES"},
                },
            },
        },
        {
            "key": "total",
            "label": "Amount",
            "align": "right",
            "x_right": 0,
            "field": "LINE_ITEM_TOTAL_PRICES",
        },
    ]
    block = table(
        rows="receipt_line_items",
        header=False,
        params={
            "fields": {
                "description": "LINE_ITEM_DESCRIPTIONS",
                "quantity": "LINE_ITEM_QUANTITIES",
                "price": "LINE_ITEM_PRICES",
                "total": "LINE_ITEM_TOTAL_PRICES",
            },
            "unit_line_format": UNIT_FORMAT,
        },
        columns=columns,
    )
    block.update(overrides)
    return block


def items_layout() -> dict:
    """The table layout with a single-line UNIT budget for the sub-line."""
    layout = table_layout()
    layout["field_budgets"]["UNIT"] = {"width": 200, "fit": "shrink", "min_font": 10, "max_lines": 1}
    return layout


def test_unit_sub_line_records_quantity_and_price_boxes_beneath_the_description() -> None:
    """A quantity-3 line prints '  3 @ $2.35' one row below its description, each value boxed where it lands."""
    result = run(items_block(), layout=items_layout(), entry=ITEMS_ENTRY)
    boxes = result.boxes()
    font = reg()
    text = "  3 @ $2.35"
    line_y = Y0 + ROW_H
    q_lead = font.getlength("  ")
    p_lead = font.getlength("  3 @ $")
    q_box, p_box = boxes["LINE_ITEM_QUANTITIES[0]"], boxes["LINE_ITEM_PRICES[0]"]
    assert q_box[0] == int(10 + q_lead + font.getbbox("3")[0])
    assert p_box[0] == int(10 + p_lead + font.getbbox("2.35")[0])
    assert q_box[1] >= line_y and p_box[1] >= line_y
    assert q_box[2] < p_box[0]
    assert p_box[2] <= 10 + width_of(text, font) + 1
    # The quantity-3 row advances two rows; the quantity-1 row below it starts after both.
    assert boxes["LINE_ITEM_DESCRIPTIONS[1]"][1] == Y0 + 2 * ROW_H


def test_unit_sub_line_is_not_drawn_for_a_quantity_of_one() -> None:
    """A quantity-1 line prints no unit line and records neither value."""
    boxes = run(items_block(), layout=items_layout(), entry=ITEMS_ENTRY).boxes()
    assert "LINE_ITEM_QUANTITIES[1]" not in boxes
    assert "LINE_ITEM_PRICES[1]" not in boxes
    assert set(boxes) == {
        "LINE_ITEM_DESCRIPTIONS[0]",
        "LINE_ITEM_DESCRIPTIONS[1]",
        "LINE_ITEM_QUANTITIES[0]",
        "LINE_ITEM_PRICES[0]",
        "LINE_ITEM_TOTAL_PRICES[0]",
        "LINE_ITEM_TOTAL_PRICES[1]",
    }


def test_unit_sub_line_shrinks_to_its_budget() -> None:
    """A unit line wider than its budget shrinks to fit rather than running past it."""
    layout = items_layout()
    layout["field_budgets"]["UNIT"] = {"width": 60, "fit": "shrink", "min_font": 6, "max_lines": 1}
    boxes = run(items_block(), layout=layout, entry=ITEMS_ENTRY).boxes()
    assert boxes["LINE_ITEM_PRICES[0]"][2] <= 10 + 60


def test_unit_sub_line_that_cannot_fit_fails() -> None:
    """A unit line that cannot fit its budget even at the minimum size fails rather than clipping."""
    layout = items_layout()
    layout["field_budgets"]["UNIT"] = {"width": 20, "fit": "shrink", "min_font": 14, "max_lines": 1}
    with pytest.raises(FitError):
        run(items_block(), layout=layout, entry=ITEMS_ENTRY)


def test_unit_sub_line_on_a_synthetic_row_records_nothing() -> None:
    """A synthetic row's sub-line draws but records no box, like its cells."""
    row = {
        "description": "Quokka Biscuits",
        "total": "7.05",
        "unit_line": "  3 @ $2.35",
        "unit_line_spans": {"quantity": (2, 3), "price": (7, 11)},
        "synthetic": True,
    }
    columns = items_block()["columns"]
    _, _, recorder = draw_row(row, columns, index=None, layout=items_layout())
    assert recorder is not None and recorder.as_dict() == {}
    _, _, control = draw_row({**row, "synthetic": False}, columns, index=0, layout=items_layout())
    assert control is not None
    assert {"LINE_ITEM_QUANTITIES[0]", "LINE_ITEM_PRICES[0]"} <= set(control.as_dict())


def role_layout(**defaults) -> dict:
    """Return a layout whose `body` role is 20px and whose `subheader` role is 28px, like an invoice page."""
    layout = table_layout(**defaults)
    layout["font_sizes"] = {"body": 20, "subheader": 28}
    return layout


ROLE_COLUMNS = [COL_DATE, PLAIN_DESC | {"x": 200}, PLAIN_BALANCE]


def paint_role_table(size: int, *, header_bold: bool = True, row_height: int = 36) -> Image.Image:
    """Draw the role-test table by hand at font `size`."""
    expected, draw = blank()
    paint_labels(draw, ROLE_COLUMNS, Y0, reg(size, bold=header_bold))
    for position, row in enumerate(provider_rows()):
        top = Y0 + 30 + position * row_height
        for column in ROLE_COLUMNS:
            paint_text(draw, column, row_text(row, str(column["key"])), top, reg(size))
    return expected


def test_table_draws_every_label_and_cell_at_its_own_role() -> None:
    """draw_table used to resolve `resolve_role(layout, "body")`, a hardcoded role name deciding a pixel outcome.

    An invoice table draws at `subheader` (28px) on a page whose `body` role is the 20px supplier-name line.
    """
    layout = role_layout(role="subheader")
    result = run(table(columns=ROLE_COLUMNS, row_height=36), layout=layout)
    assert same(result.image, paint_role_table(28))
    assert not same(result.image, paint_role_table(20))
    assert result.y == Y0 + 30 + 3 * 36


def test_table_role_block_key_wins_over_the_layout_default() -> None:
    """The test above covers the omitted case (defaults.role, which every bank and receipt table relies on).

    This covers the override.
    """
    layout = role_layout(role="body")
    result = run(table(columns=ROLE_COLUMNS, row_height=36, role="subheader"), layout=layout)
    assert same(result.image, paint_role_table(28))
    body = run(table(columns=ROLE_COLUMNS, row_height=36), layout=layout)
    assert same(
        body.image, run(table(columns=ROLE_COLUMNS, row_height=36, role="body"), layout=layout).image
    )
    assert not same(body.image, result.image)


def header_only(layout: dict | None = None, **overrides) -> Run:
    """Draw just the bank header (the entry has no rows)."""
    return run(table(**overrides), layout=layout, entry=EMPTY_ENTRY)


def test_header_bold_false_draws_regular_weight_column_headings() -> None:
    """`header_bold: false` draws the headings at regular weight."""
    result = header_only(header_bold=False)
    expected, draw = blank()
    paint_labels(draw, BANK_COLUMNS, Y0, reg())
    assert same(result.image, expected)
    bold, bold_draw = blank()
    paint_labels(bold_draw, BANK_COLUMNS, Y0, reg(bold=True))
    assert not same(result.image, bold)


def test_header_bold_defaults_to_the_layout_default_which_is_bold() -> None:
    """With no block key the heading weight is the layout's `table_header_bold`, bold in the real layouts."""
    bold_expected, bold_draw = blank()
    paint_labels(bold_draw, BANK_COLUMNS, Y0, reg(bold=True))
    assert same(header_only().image, bold_expected)
    regular_expected, regular_draw = blank()
    paint_labels(regular_draw, BANK_COLUMNS, Y0, reg())
    assert same(header_only(table_layout(table_header_bold=False)).image, regular_expected)


# --- inset, label placement and line spacing ------------------------------------------------------------------


def test_row_inset_y_shifts_cell_ink_without_changing_the_row_pitch() -> None:
    """Legacy draws each cell at `y + 12` inside a 52px row and still advances exactly 52.

    The inset must not leak into the table's own flow.
    """
    block = table(row_height=52, row_inset_y=12)
    result = run(block)
    plain = run(table(row_height=52))
    assert result.y == plain.y == Y0 + HEADER_H + 3 * 52
    boxes, flat = result.boxes(), plain.boxes()
    for index in range(3):
        key = f"TRANSACTION_DESCRIPTIONS[{index}]"
        # The fitted cell's box moves down by the inset, keeping its full row-pitch height.
        assert boxes[key][1] == flat[key][1] + 12
        assert boxes[key][3] - boxes[key][1] == 52
        assert boxes[key][0::2] == flat[key][0::2]
    assert boxes["TRANSACTION_AMOUNTS_PAID[0]"][1] == ROW0 + 12
    assert boxes["TRANSACTION_AMOUNTS_PAID[2]"][1] == ROW0 + 2 * 52 + 12


def test_label_x_and_label_align_position_the_heading_apart_from_its_cells() -> None:
    """The legacy invoice header draws "Total" left-aligned at the column's own left edge.

    Its amounts right-align 200px further along.
    """
    column = {"key": "balance", "label": "Total", "align": "right", "x_right": 0, "label_x": 600}
    left_aligned = {**column, "label_align": "left"}
    rows = provider_rows()
    for spec, label_left in ((left_aligned, True), (column, False)):
        result = run(table(columns=[spec]))
        expected, draw = blank()
        label_font = reg(bold=True)
        if label_left:
            draw.text((REGION.x + 600, Y0), "Total", font=label_font, fill="black")
        else:
            draw.text(
                (REGION.x + 600 - width_of("Total", label_font), Y0), "Total", font=label_font, fill="black"
            )
        for position, row in enumerate(rows):
            text = fmt_amount(row["balance"])
            draw.text(
                (RIGHT - width_of(text, reg()), ROW0 + position * ROW_H), text, font=reg(), fill="black"
            )
        assert same(result.image, expected), spec
    # The cells did not follow the label: the first balance still ends at the region's right edge.
    assert run(table(columns=[left_aligned])).y == Y0 + HEADER_H + 3 * ROW_H


LONG_ENTRY = {
    "case_id": "CASE999",
    "layout": LAYOUT_ID,
    "fields": {
        "TRANSACTION_DATES": "05/03/2031",
        "TRANSACTION_DESCRIPTIONS": "Quillon Hardware and Building Supplies",
        "TRANSACTION_AMOUNTS_PAID": "40.00",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND",
        "ACCOUNT_BALANCE": "300.00",
    },
}


def fitted_description(budget_name: str, size: int):
    """Return the real fit of the long description under a layout budget at `size`."""
    budget = table_layout()["field_budgets"][budget_name]
    return fit_text(
        "Quillon Hardware and Building Supplies",
        width=budget["width"],
        fit=budget["fit"],
        min_font=budget["min_font"],
        max_lines=budget["max_lines"],
        nominal_size=size,
        family=FAMILY,
    )


def test_cell_line_spacing_font_records_the_fitted_fonts_own_height() -> None:
    """A budgeted cell in a 52px invoice row records a 28px-tall box.

    Legacy passed no line_spacing to draw_fitted_left at all, so the box is the fitted font's own height.
    """
    layout = table_layout(role="subheader", table_cell_line_spacing="font")
    columns = [{**COL_DESC, "x": 0, "budget": "WIDE"}]
    single = run(table(columns=columns, row_height=52, header=False), layout=layout)
    assert single.y == Y0 + 3 * 52, "the row pitch is the declared 52, whatever the cell's own height"
    quillon = single.boxes()["TRANSACTION_DESCRIPTIONS[0]"]
    assert quillon == [10, Y0, 10 + width_of("Quillon Hardware", reg(28)), Y0 + 28]
    # A wrapped cell advances by that font height per line, and its right edge is its widest fitted line.
    fit = fitted_description("NARROW", 28)
    assert len(fit.lines) >= 2 and fit.size == 28
    columns = [{**COL_DESC, "x": 0, "budget": "NARROW"}]
    wrapped = run(table(columns=columns, row_height=52, header=False), layout=layout, entry=LONG_ENTRY)
    widest = max(width_of(line, reg(28)) for line in fit.lines)
    assert wrapped.boxes()["TRANSACTION_DESCRIPTIONS[0]"] == [10, Y0, 10 + widest, Y0 + 28 * len(fit.lines)]
    assert wrapped.y == Y0 + max(52, 28 * len(fit.lines))


def test_cell_line_spacing_row_height_records_the_row_pitch() -> None:
    """The default, and what every bank and receipt table's legacy renderer did."""
    layout = table_layout(role="subheader")
    columns = [{**COL_DESC, "x": 0, "budget": "WIDE"}]
    single = run(table(columns=columns, row_height=52, header=False), layout=layout)
    quillon = single.boxes()["TRANSACTION_DESCRIPTIONS[0]"]
    assert quillon == [10, Y0, 10 + width_of("Quillon Hardware", reg(28)), Y0 + 52]
    fit = fitted_description("NARROW", 28)
    columns = [{**COL_DESC, "x": 0, "budget": "NARROW"}]
    wrapped = run(table(columns=columns, row_height=52, header=False), layout=layout, entry=LONG_ENTRY)
    widest = max(width_of(line, reg(28)) for line in fit.lines)
    assert wrapped.boxes()["TRANSACTION_DESCRIPTIONS[0]"] == [10, Y0, 10 + widest, Y0 + 52 * len(fit.lines)]
    assert wrapped.y == Y0 + 52 * len(fit.lines)


def test_unknown_cell_line_spacing_raises_a_four_element_diagnostic() -> None:
    """Checked at render time as well as in schema.py.

    The value may come from defaults.table_cell_line_spacing, which schema.py never reads.
    """
    with pytest.raises(TableError) as from_block:
        run(table(cell_line_spacing="bogus"))
    assert_diagnostic_error(from_block.value)
    assert "bogus" in str(from_block.value)
    with pytest.raises(TableError) as from_layout:
        run(table(), layout=table_layout(table_cell_line_spacing="bogus"))
    assert_diagnostic_error(from_layout.value)
    assert "table_cell_line_spacing" in str(from_layout.value)
    assert "row_height" in str(from_layout.value) and "font" in str(from_layout.value)
