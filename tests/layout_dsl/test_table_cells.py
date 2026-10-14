"""Every line a table draws names its cell: the drawn row, the column, and the span (Review Focus 3).

The first half draws one scratch table through the walker and checks each line's cell exactly. The second
renders the first case of every bank layout and of one receipt and one invoice layout, and checks the cell
grid each table recorded is rectangular: every line in a table carries a cell inside the declared columns,
rows count up from 0 in draw order without a gap, the header row holds one line per label line, and a date
sub-header row spans the table alone.
"""

import re
from collections import Counter

import pytest
from PIL import Image, ImageDraw

from generators.exporters.geometry import BlockRecord, BoxRecorder, Cell, InkRecord
from generators.layout_dsl.context import Region
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.engine import render_body
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, RECEIPTS_PATH
from tests.layout_dsl.test_primitives_table import (
    BANK_ENTRY,
    COL_BALANCE,
    COL_CREDIT,
    COL_DATE,
    COL_DEBIT,
    COL_DESC,
    DEFERRED,
    PAGE_H,
    PAGE_W,
    REGION,
    table,
    table_layout,
)
from tests.renderer_helpers import entry_per_layout, layouts_for, render_entry

REFERENCES = {
    "references": True,
    "reference_prefix": "REF",
    "reference_pad_char": ".",
    "reference_pad_width": 2,
}
SUB_LINE = {"key": "reference", "role": "sub", "color": "#999999", "offset_y": 20, "height": 16}
COLUMNS = [
    {**COL_DATE, "label": "Date of\nEntry"},
    {**COL_DESC, "sub_line": SUB_LINE},
    COL_DEBIT,
    COL_CREDIT,
    COL_BALANCE,
]
AFTER = {"type": "text", "content": "After the table"}
# The bank table tests' entry with amounts of its own; invented, none appear in ground_truth/.
TABLE_ENTRY = {
    **BANK_ENTRY,
    "fields": {
        "TRANSACTION_DATES": "05/03/2031|05/03/2031|09/03/2031",
        "TRANSACTION_DESCRIPTIONS": "Quillon Hardware|Wattle Cafe|Banksia Fuel",
        "TRANSACTION_AMOUNTS_PAID": "41.37|NOT_FOUND|57.23",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|78.19|NOT_FOUND",
        "ACCOUNT_BALANCE": "301.59",
    },
}


def draw(block: dict) -> BoxRecorder:
    """Draw `block`, then one text line, through the walker on a scratch bank-style layout."""
    layout = table_layout()
    layout["body"] = [block, AFTER]
    recorder = BoxRecorder(PAGE_W, PAGE_H * 2)
    surface = ImageDraw.Draw(Image.new("RGB", (PAGE_W, PAGE_H * 2), "white"))
    render_body(
        layout,
        TABLE_ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=surface,
        region=Region(x=REGION.x, width=REGION.width),
        y=0,
        distractors=False,
        recorder=recorder,
    )
    return recorder


def cells_by_text(recorder: BoxRecorder) -> dict[str, list[Cell | None]]:
    """Each drawn string's cells, in draw order."""
    found: dict[str, list[Cell | None]] = {}
    for record in recorder.ink_records():
        found.setdefault(record.text, []).append(record.cell)
    return found


def grouped_table() -> dict:
    """A date-grouped table with a two-line header label, a deferred synthetic row and a sub-line."""
    return table(
        grouping="dedicated_row",
        columns=COLUMNS,
        params={**DEFERRED, **REFERENCES},
        synthetic_row_placement="after_first_group_header",
    )


def test_each_line_of_a_grouped_table_names_its_cell() -> None:
    """Header, date sub-header, synthetic, data and sub-line each land in the drawn row and column."""
    recorder = draw(grouped_table())
    header = [(r.text, r.cell) for r in recorder.ink_records()[:6]]
    assert header == [
        ("Date of", Cell(0, 0, 1)),
        ("Entry", Cell(0, 0, 1)),  # a two-line label is one cell (Decision 7)
        ("Description", Cell(0, 1, 1)),
        ("Debit", Cell(0, 2, 1)),
        ("Credit", Cell(0, 3, 1)),
        ("Balance", Cell(0, 4, 1)),
    ]
    found = cells_by_text(recorder)
    assert found["05/03/2031"] == [Cell(1, 0, 5)]  # the date sub-header spans the table
    assert found["Brought forward"] == [Cell(2, 1, 1)]  # the deferred synthetic row is a row
    assert found["Quillon Hardware"] == [Cell(3, 1, 1)]
    assert found["$41.37"] == [Cell(3, 2, 1)]
    assert found["Wattle Cafe"] == [Cell(4, 1, 1)]
    assert found["$78.19"] == [Cell(4, 3, 1)]
    assert found["09/03/2031"] == [Cell(5, 0, 5)]
    assert found["Banksia Fuel"] == [Cell(6, 1, 1)]
    assert found["$57.23"] == [Cell(6, 2, 1)]
    references = [r.cell for r in recorder.ink_records() if r.text.startswith("REF")]
    assert references == [Cell(3, 1, 1), Cell(4, 1, 1), Cell(6, 1, 1)]  # in the declaring column's cell
    assert found[AFTER["content"]] == [None]  # the cell clears when the table is done


def test_a_table_records_its_column_count_and_every_line_a_cell() -> None:
    """The table's block declares its column count, and no line of the table is left without a cell."""
    recorder = draw(grouped_table())
    table_block, after = recorder.block_records()
    assert table_block == BlockRecord(0, "table", "body[0]", None, None, 0, len(COLUMNS))
    assert after.kind == "text"
    assert all(r.cell is not None for r in recorder.ink_records() if r.block == table_block.id)


def test_rows_count_from_0_without_a_header() -> None:
    """With no header the first drawn row is row 0."""
    recorder = draw(table(header=False))
    found = cells_by_text(recorder)
    assert found["05/03/2031"] == [Cell(0, 0, 1), Cell(1, 0, 1)]  # ungrouped: every row prints its date
    assert found["Quillon Hardware"] == [Cell(0, 1, 1)]
    assert found["Banksia Fuel"] == [Cell(2, 1, 1)]


def test_a_table_that_captures_no_boxes_still_records_its_cells() -> None:
    """`capture: false` drops scored boxes only; the grid is still recorded (spec section 4.3)."""
    recorder = draw(table(capture=False))
    assert recorder.as_dict() == {}
    assert cells_by_text(recorder)["Wattle Cafe"] == [Cell(2, 1, 1)]


def test_inline_grouping_leaves_the_repeated_date_cell_without_ink() -> None:
    """A blanked date draws nothing, so its row holds no line in column 0; the other cells keep theirs."""
    recorder = draw(table(grouping="inline"))
    found = cells_by_text(recorder)
    assert found["05/03/2031"] == [Cell(1, 0, 1)]
    assert found["Wattle Cafe"] == [Cell(2, 1, 1)]
    assert not [r for r in recorder.ink_records() if r.cell is not None and r.cell[:2] == (2, 0)]


# --- real layouts ----------------------------------------------------------------------------------------

REAL_LAYOUTS = [
    *(("bank_statement", layout_id) for layout_id in layouts_for("bank_statement")),
    ("receipt", "receipt_thermal_80mm"),
    ("invoice", "tax_invoice_mixed"),
]


def resolve(layout: dict, path: str) -> dict:
    """Return the block dict a block path such as `body[3].children[0][1]` names."""
    node: object = layout
    for key, index in re.findall(r"(\w+)|\[(\d+)\]", path):
        node = node[key] if key else node[int(index)]  # type: ignore[index]
    assert isinstance(node, dict), path
    return node


def label_lines(columns: list) -> int:
    """The number of non-blank header label lines a table's columns draw."""
    return sum(1 for column in columns for line in str(column["label"]).split("\n") if line.strip())


@pytest.mark.parametrize(("doc_type", "layout_id"), REAL_LAYOUTS)
def test_every_real_table_records_a_rectangular_grid(doc_type: str, layout_id: str) -> None:
    """Review Focus 3 on the first case of each listed layout."""
    entry = entry_per_layout(doc_type)[layout_id]
    layout = layouts_for(doc_type)[layout_id]
    geometry: dict = {}
    render_entry(doc_type, entry, distractors=False, geometry_out=geometry)
    tables = [block for block in geometry["blocks"] if block.kind == "table"]
    assert tables, f"{layout_id}: no table block recorded"
    for block in tables:
        spec = resolve(layout, block.path)
        n = len(spec["columns"])
        assert block.columns == n, block
        ink: list[InkRecord] = [r for r in geometry["ink"] if r.block == block.id]
        assert ink, f"{layout_id} {block.path}: the table drew nothing"
        cells = [r.cell for r in ink]
        assert None not in cells, f"{layout_id} {block.path}: a table line has no cell (Decision 8)"
        for cell in cells:
            assert cell is not None
            assert 0 <= cell.column < n and cell.span in (1, n), (layout_id, cell)
        rows = [cell.row for cell in cells if cell is not None]
        assert rows == sorted(rows), f"{layout_id}: rows are not counted in draw order"
        assert sorted(set(rows)) == list(range(rows[-1] + 1)), f"{layout_id}: a drawn row has no line"
        lines_per_row = Counter(rows)
        header = resolve_param(
            spec, layout, "table_header", layout_id=layout_id, layout_path="<layout>", block_key="header"
        )
        if header:
            assert lines_per_row[0] == label_lines(spec["columns"]), f"{layout_id}: header lines"
        for cell in cells:
            if cell is not None and cell.span == n and n > 1:
                assert cell.column == 0 and lines_per_row[cell.row] == 1, (layout_id, cell)
