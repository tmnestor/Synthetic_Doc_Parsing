"""`scripts/spanning_rows_as_pipe.py`: what a spanning row costs a parser that writes pipe tables.

The evaluator itself is not run here. These tests cover how a page is written and how the evaluator's
per-table results are summarised and judged. Every name and amount is invented for the test.
"""

import pytest

from scripts.spanning_rows_as_pipe import (
    failures,
    ordinary_rows,
    spanning_rows,
    summary,
    tables_with_spanning_rows,
    written_page,
)
from tests.conftest import assert_diagnostic_error

DATED = (
    "<table><tr><td>Day</td><td>Detail</td><td>Out</td></tr>"
    '<tr><td colspan="3">04/02/2031</td></tr>'
    "<tr><td></td><td>Quill &amp; Nib</td><td>$7.15</td></tr></table>"
)
PLAIN = "<table><tr><td>Item</td><td>Each</td></tr><tr><td>Oak peg</td><td>$2.05</td></tr></table>"


def _page(image: str, table: str) -> dict:
    return {
        "page_info": {"image_path": f"plain_20310102/{image}"},
        "layout_dets": [
            {"category_type": "text_block", "order": 1, "ignore": False, "text": "Tallow Street Savings"},
            {"category_type": "table", "order": 2, "ignore": False, "html": table},
            {"category_type": "text_block", "order": 3, "ignore": True, "text": "a cropped line"},
        ],
    }


def test_a_spanning_cell_becomes_a_plain_cell_and_the_empty_cells_it_covered() -> None:
    assert spanning_rows(DATED) == 1 and spanning_rows(PLAIN) == 0
    assert ordinary_rows(DATED) == DATED.replace(
        '<td colspan="3">04/02/2031</td>', "<td>04/02/2031</td><td></td><td></td>"
    )
    assert ordinary_rows(PLAIN) == PLAIN


def test_a_cell_that_spans_rows_is_refused_with_a_diagnostic() -> None:
    with pytest.raises(ValueError, match="rowspan") as caught:
        ordinary_rows('<table><tr><td rowspan="2">Day</td><td>Out</td></tr></table>')
    assert_diagnostic_error(caught.value)


def test_a_page_is_written_whole_with_its_date_row_as_an_ordinary_pipe_row() -> None:
    written = written_page(_page("CASE901_bank_statement.png", DATED))
    assert "Tallow Street Savings" in written and "a cropped line" not in written
    assert "| 04/02/2031 |  |  |" in written
    assert "|  | Quill & Nib | $7.15 |" in written
    assert "colspan" not in written and "<table" not in written


def test_the_summary_separates_the_tables_with_spanning_rows_from_the_rest() -> None:
    pages = [
        _page("CASE901_bank_statement.png", DATED),
        _page("CASE902_bank_statement.png", DATED),
        _page("CASE903_receipt.png", PLAIN),
    ]
    assert tables_with_spanning_rows(pages) == {
        "CASE901_bank_statement.png": 1,
        "CASE902_bank_statement.png": 1,
    }
    rows = [
        _row("CASE901_bank_statement.png", teds=0.5, edit=0.25),
        _row("CASE902_bank_statement.png", teds=0.75, edit=0.125),
        _row("CASE903_receipt.png", teds=1.0, edit=0.0),
    ]
    assert summary(pages, rows) == {
        "tables": 3,
        "tables_with_spanning_rows": 2,
        "spanning_rows": 2,
        "their_teds_mean": 0.625,
        "their_teds_lowest": 0.5,
        "their_teds_highest": 0.75,
        "their_teds_structure_only_mean": 0.625,
        "their_table_edit_dist_mean": 0.1875,
        "lowest_teds_of_a_table_without_one": 1.0,
    }


def _row(image: str, *, teds: float, edit: float) -> dict:
    return {"img_id": image, "metric": {"TEDS": teds, "TEDS_structure_only": teds, "Edit_dist": edit}}


def _record(**changes: object) -> dict:
    return {
        "set": "plain",
        "tables_with_spanning_rows": 2,
        "text_block_edit_dist": 0.0,
        "reading_order_edit_dist": 0.0,
        "lowest_teds_of_a_table_without_one": 1.0,
        **changes,
    }


def test_a_run_charged_only_for_its_spanning_rows_passes() -> None:
    assert failures([_record(), {"set": "degraded", "tables_with_spanning_rows": 0}]) == []
    assert failures([_record(lowest_teds_of_a_table_without_one=None)]) == []


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"text_block_edit_dist": 0.004}, "the text or the reading order was charged"),
        ({"reading_order_edit_dist": 0.036}, "the text or the reading order was charged"),
        ({"lowest_teds_of_a_table_without_one": 0.989}, "a table with no spanning row scored 0.989"),
    ],
)
def test_a_run_charged_for_anything_else_fails(changes: dict, named: str) -> None:
    (line,) = failures([_record(**changes)])
    assert line.startswith("plain: ") and named in line
