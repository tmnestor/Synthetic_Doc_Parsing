"""The snapshot check's own steps: how a page is written back, and how a left-out line is judged.

The evaluator runs themselves are the script's work, kept in `scripts/perfect_score_snapshot.json`.
Every line of text here is invented for the test.
"""

from scripts.perfect_score_snapshot import (
    compared_length,
    failures,
    left_out_block,
    left_out_judgement,
    pipe_table,
    written_page,
)
from scripts.pipe_tables import pipe_tables_to_html

TABLE = (
    "<table><tr><td>Part</td><td>Cost</td></tr><tr><td>Nut &amp; Bolt: A|B</td><td>$7.10</td></tr></table>"
)


def _text(order: int, text: str, *, ignore: bool = False) -> dict:
    return {"category_type": "text_block", "order": order, "text": text, "ignore": ignore}


def _page() -> dict:
    return {
        "page_info": {"image_path": "set/CASE900_invoice.png"},
        "layout_dets": [
            _text(1, "First line"),
            _text(2, "Second line"),
            {"category_type": "table", "order": 3, "html": TABLE},
            _text(4, "Third line"),
            _text(5, "A cropped line", ignore=True),
        ],
    }


def test_a_table_written_as_pipes_reads_back_as_the_same_html() -> None:
    piped = pipe_table(TABLE)
    assert piped.splitlines()[:2] == ["| Part | Cost |", "|---|---|"]
    assert pipe_tables_to_html(piped) == TABLE


def test_a_table_with_a_spanning_cell_stays_html() -> None:
    spanning = '<table><tr><td colspan="2">Total</td></tr><tr><td>Part</td><td>Cost</td></tr></table>'
    assert pipe_table(spanning) == spanning


def test_the_line_left_out_is_the_middle_scored_text_block() -> None:
    assert left_out_block(_page())["text"] == "Second line"


def test_a_page_is_written_complete_or_less_one_line_in_either_table_form() -> None:
    page = _page()
    complete = written_page(page, transcription="complete", tables="html")
    assert complete == f"First line\n\nSecond line\n\n{TABLE}\n\nThird line\n"  # no cropped line
    less = written_page(page, transcription="one_line_left_out", tables="pipe")
    assert "Second line" not in less
    assert "| Part | Cost |" in less and "<table>" not in less


def _row(gt: str, pred: str, edit: float, edits: int) -> dict:
    return {"img_id": "CASE900_invoice.png", "gt": gt, "pred": pred, "edit": edit, "Edit_num": edits}


def test_only_letters_digits_and_underscores_are_counted() -> None:
    assert compared_length("Second line") == 10
    assert compared_length("Ref: Z-9, $7.10 __") == 10


def test_a_line_charged_once_against_nothing_is_charged_exactly_and_as_a_row_of_its_own() -> None:
    rows = [_row("First line", "First line", 0, 0), _row("Second line", "", 1, 10)]
    assert left_out_judgement([_page()], rows) == {
        "pages_charged_exactly_the_line": 1,
        "of_which_as_a_row_of_its_own": 1,
        "exceptions": [],
    }


def test_a_line_merged_with_its_neighbour_is_charged_exactly_but_not_as_a_row_of_its_own() -> None:
    rows = [_row("First lineSecond line", "First line", 0.5, 10)]
    judged = left_out_judgement([_page()], rows)
    assert (judged["pages_charged_exactly_the_line"], judged["of_which_as_a_row_of_its_own"]) == (1, 0)
    assert judged["exceptions"] == []


def test_a_line_paired_with_other_text_and_charged_for_its_length_is_an_exception() -> None:
    judged = left_out_judgement(
        [_page()], [_row("Second line", "7.10 | Nut and a good deal more", 0.9, 21)]
    )
    assert judged["pages_charged_exactly_the_line"] == 0
    assert judged["exceptions"] == [
        {
            "image": "CASE900_invoice.png",
            "left_out": "Second line",
            "characters_left_out": 10,
            "edits_charged": 21,
            "charged": [{"gt": "Second line", "pred": "7.10 | Nut and a good deal more", "edit": 0.9}],
        }
    ]


def test_a_second_charged_line_or_no_charge_at_all_is_an_exception() -> None:
    two = left_out_judgement(
        [_page()], [_row("Second line", "", 1, 10), _row("Third line", "Thrid line", 0.2, 2)]
    )
    assert two["exceptions"][0]["edits_charged"] == 12
    assert left_out_judgement([_page()], [])["exceptions"][0]["edits_charged"] == 0


def _record(**changes: object) -> dict:
    record = {
        "set": "plain",
        "pages": 3,
        "transcription": "complete",
        "tables": "pipe",
        "launcher": "formula_free",
        "text_block_edit_dist": 0.0,
        "table_teds": 1.0,
        "table_teds_structure_only": 1.0,
        "table_edit_dist": 0.0,
        "reading_order_edit_dist": 0.0,
    }
    return {**record, **changes}


def test_only_the_formula_free_runs_can_fail() -> None:
    assert failures([_record(), _record(launcher="unmodified", table_teds=0.9)]) == []
    assert len(failures([_record(table_teds=0.9)])) == 1
    left_out = {"transcription": "one_line_left_out", "text_block_edit_dist": 0.1}
    assert failures([_record(**left_out, reading_order_edit_dist=0.1, exceptions=[])]) == []
    assert len(failures([_record(**left_out, exceptions=[{"image": "x"}])])) == 1
    assert len(failures([_record(**left_out, table_teds=0.9, exceptions=[])])) == 1
