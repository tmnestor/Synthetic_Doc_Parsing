"""`scripts/transaction_columns_check.py`: the transaction check proved on a snapshot's own statements.

Every table here is invented for the test.
"""

import json
from pathlib import Path

import pytest

from scripts.transaction_columns import judge_table
from scripts.transaction_columns_check import (
    FAULTS,
    as_pipe,
    failures,
    one_fault,
    read_back,
    spoiled,
    statement_tables,
)
from tests.conftest import assert_diagnostic_error

TABLE = (
    "<table><tr><td>Day</td><td>Details</td><td>Debits</td><td>Credits</td><td>Balance</td></tr>"
    "<tr><td></td><td>Brought forward</td><td></td><td></td><td>900.10</td></tr>"
    '<tr><td colspan="5">03/02/2031</td></tr>'
    "<tr><td></td><td>QUILL &amp; NIB</td><td>41.25</td><td></td><td>858.85</td></tr>"
    "<tr><td></td><td>TALLOW ST BAKERY</td><td>6.80</td><td></td><td>852.05</td></tr>"
    "<tr><td></td><td>PAY MARROWBONE</td><td></td><td>1,204.57</td><td>2,056.62</td></tr>"
    "<tr><td></td><td>Totals</td><td>48.05</td><td>1,204.57</td><td></td></tr></table>"
)
MIDDLE = "<tr><td></td><td>TALLOW ST BAKERY</td><td>6.80</td><td></td><td>852.05</td></tr>"


def _page(image: str, doc_type: str, table: str) -> dict:
    return {
        "page_info": {"image_path": f"plain_20310102/{image}", "page_attribute": {"doc_type": doc_type}},
        "layout_dets": [
            {"category_type": "text_block", "text": "Tallow Street Savings"},
            {"category_type": "table", "html": table},
        ],
    }


def test_only_bank_statements_have_their_tables_taken() -> None:
    pages = [
        _page("CASE901_bank_statement.png", "bank_statement", TABLE),
        _page("CASE902_receipt.png", "receipt", "<table><tr><td>Oak peg</td></tr></table>"),
    ]
    assert statement_tables(pages) == {"CASE901_bank_statement.png": TABLE}


def test_each_fault_spoils_the_middle_row_that_holds_one_amount_and_no_other() -> None:
    assert spoiled(TABLE, "amount_in_the_other_column") == TABLE.replace(
        MIDDLE, "<tr><td></td><td>TALLOW ST BAKERY</td><td></td><td>6.80</td><td>852.05</td></tr>"
    )
    assert spoiled(TABLE, "one_digit_changed") == TABLE.replace("<td>6.80</td>", "<td>6.87</td>")
    assert spoiled(TABLE, "row_left_out") == TABLE.replace(MIDDLE, "")


def test_a_digit_that_is_already_the_replacement_is_changed_to_another() -> None:
    table = TABLE.replace("<td>6.80</td>", "<td>6.87</td>")
    assert "<td>6.83</td>" in spoiled(table, "one_digit_changed")


def test_a_table_not_written_as_the_export_writes_it_is_refused() -> None:
    with pytest.raises(ValueError, match="not written as this check expects") as caught:
        spoiled(TABLE.replace("<table>", '<table border="1">'), "row_left_out")
    assert_diagnostic_error(caught.value)


def test_the_pipe_form_is_what_the_launcher_makes_of_a_pipe_table() -> None:
    parsed = as_pipe(TABLE)
    assert "colspan" not in parsed and "<td>QUILL &amp; NIB</td>" in parsed
    assert judge_table(TABLE, parsed)["right"] == 4


@pytest.mark.parametrize("fault", list(FAULTS))
def test_one_fault_is_named_alone_in_both_table_forms(fault: str) -> None:
    result = one_fault({"CASE901_bank_statement.png": TABLE}, fault)
    assert result == {"judged": 2, "named_that_fault_alone": 2, "exceptions": []}


def test_saved_evaluator_results_are_read_back_and_judged(tmp_path: Path) -> None:
    tables = {"CASE901_bank_statement.png": TABLE, "CASE903_bank_statement.png": TABLE}
    saved = tmp_path / "table_result.json"
    saved.write_text(
        json.dumps(
            [
                {"img_id": "CASE901_bank_statement.png", "pred": TABLE},
                {"img_id": "CASE902_receipt.png", "pred": "<table><tr><td>Oak peg</td></tr></table>"},
                {"img_id": "CASE903_bank_statement.png", "pred": spoiled(TABLE, "row_left_out")},
            ]
        )
    )
    total = read_back(tables, saved)
    assert total["statements"] == 2 and total["rows_with_an_amount"] == 8
    assert total["right"] == 7 and total["row_missing"] == 1 and total["rows_added"] == 0


def test_a_run_with_no_saved_result_is_refused_with_a_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        read_back({"CASE901_bank_statement.png": TABLE}, tmp_path / "absent.json")
    assert_diagnostic_error(caught.value)


def _record(**changes: dict) -> dict:
    perfect = {"rows_with_an_amount": 8, "right": 8, "rows_added": 0}
    return {
        "set": "plain",
        "read_back": {"complete, tables as html": {**perfect, **changes.get("read_back", {})}},
        "one_fault": {
            "row_left_out": {
                "judged": 4,
                "named_that_fault_alone": 4,
                "exceptions": [],
                **changes.get("fault", {}),
            }
        },
    }


def test_a_record_that_meets_both_requirements_passes() -> None:
    assert failures([_record()]) == []


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"read_back": {"right": 7}}, "a perfect transcription is not judged right"),
        ({"read_back": {"rows_added": 1}}, "a perfect transcription is not judged right"),
        ({"fault": {"named_that_fault_alone": 3, "exceptions": [{}]}}, "1 of 4 tables"),
    ],
)
def test_a_record_that_fails_a_requirement_is_reported(changes: dict, named: str) -> None:
    (line,) = failures([_record(**changes)])
    assert line.startswith("plain, ") and named in line
