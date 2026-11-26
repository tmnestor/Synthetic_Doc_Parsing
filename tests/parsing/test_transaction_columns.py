"""`scripts/transaction_columns.py`: are a statement's amounts in the right columns?

Every table here is invented for the test. The first group holds the two properties a check handed to
someone else must have: a perfect table is judged wholly right, and a table with one thing wrong is
charged for that thing and nothing else.
"""

import ast
import sys

import pytest

from scripts.pipe_tables import pipe_tables_to_html
from scripts.transaction_columns import KINDS, amount, amount_columns, judge_table, table_rows
from tests.conftest import REPO_ROOT, assert_diagnostic_error

HEADER = ["Day", "Details", "Debits", "Credits (-)", "Balance"]
ROWS = [
    ["", "Brought forward", "", "", "$900.10 CR"],
    ["03/02/2031", "QUILL & NIB STATIONERS", "$41.25", "", "$858.85 CR"],
    ["03/02/2031", "TALLOW ST BAKERY", "$6.80", "", "$852.05 CR"],
    ["04/02/2031", "PAY MARROWBONE TACKLE", "", "$1,204.50", "$2,056.55 CR"],
    ["05/02/2031", "TALLOW ST BAKERY", "$6.80", "", "$2,049.75 CR"],
    ["", "Totals", "$54.85", "$1,204.50", ""],
]


def _html(rows: list, *, header: list = HEADER) -> str:
    cells = lambda row: "".join(f"<td>{cell.replace('&', '&amp;')}</td>" for cell in row)
    return "<table>" + "".join(f"<tr>{cells(row)}</tr>" for row in [header, *rows]) + "</table>"


def _pipe(rows: list, *, header: list = HEADER) -> str:
    lines = ["| " + " | ".join(row) + " |" for row in [header, *rows]]
    lines.insert(1, "|" + "---|" * len(header))
    return "\n".join(lines) + "\n"


def _changed(index: int, **cells: str) -> list:
    """`ROWS` with some cells of one row replaced, each named by its heading."""
    rows = [list(row) for row in ROWS]
    for heading, text in cells.items():
        rows[index][["day", "details", "debits", "credits", "balance"].index(heading)] = text
    return rows


def _counts(judgement: dict) -> dict:
    return {kind: judgement[kind] for kind in KINDS if judgement[kind]}


TRUTH = _html(ROWS)


@pytest.mark.parametrize("parsed", [TRUTH, pipe_tables_to_html(_pipe(ROWS))], ids=["html", "pipe"])
def test_a_perfect_table_is_judged_wholly_right_in_both_table_forms(parsed: str) -> None:
    judgement = judge_table(TRUTH, parsed)
    assert judgement["rows_with_an_amount"] == 5
    assert _counts(judgement) == {"right": 5}
    assert judgement["rows_added"] == 0 and judgement["findings"] == []
    assert judgement["columns"] == "header"


@pytest.mark.parametrize(
    ("rows", "kind"),
    [
        (_changed(1, debits="", credits="$41.25"), "wrong_column"),
        (_changed(3, debits="$1,204.50", credits=""), "wrong_column"),
        (_changed(1, credits="$41.25"), "wrong_column"),
        (_changed(5, debits="$1,204.50", credits="$54.85"), "wrong_column"),
        (_changed(1, debits="$47.25"), "amount_misread"),
        (_changed(5, credits="$1,204.60"), "amount_misread"),
        (_changed(2, debits=""), "amount_missing"),
        (ROWS[:2] + ROWS[3:], "row_missing"),
    ],
    ids=[
        "a debit written as a credit",
        "a credit written as a debit",
        "an amount in both columns",
        "the two totals exchanged",
        "one digit of a debit wrong",
        "one digit of a total wrong",
        "an amount left out",
        "a row left out",
    ],
)
def test_one_thing_wrong_is_charged_once_and_nothing_else_is(rows: list, kind: str) -> None:
    for parsed in (_html(rows), pipe_tables_to_html(_pipe(rows))):
        judgement = judge_table(TRUTH, parsed)
        assert _counts(judgement) == {"right": 4, kind: 1}
        assert judgement["rows_added"] == 0
        (finding,) = judgement["findings"]
        assert finding["kind"] == kind


def test_the_finding_shows_the_row_in_each_table() -> None:
    (finding,) = judge_table(TRUTH, _html(_changed(1, debits="", credits="$41.25")))["findings"]
    assert finding["truth"] == ROWS[1]
    assert finding["parsed"] == ["03/02/2031", "QUILL & NIB STATIONERS", "", "$41.25", "$858.85 CR"]


def test_a_misread_description_does_not_lose_the_row() -> None:
    judgement = judge_table(TRUTH, _html(_changed(3, details="PAY MARROWB0NE TACKLE")))
    assert _counts(judgement) == {"right": 5}


def test_how_an_amount_is_written_is_not_what_is_judged() -> None:
    rows = _changed(3, credits="1204.50")
    rows[1][2] = "$ 41.25"
    assert _counts(judge_table(TRUTH, _html(rows))) == {"right": 5}
    assert amount("$1,204.50") == amount("1204.50") == "1204.50"
    assert amount("-$7.15") == "-7.15"


def test_a_dash_in_a_cell_that_should_be_empty_is_an_empty_cell() -> None:
    rows = [[cell or "-" for cell in row] for row in ROWS]
    assert _counts(judge_table(TRUTH, _html(rows))) == {"right": 5}
    assert amount("-") == "" and amount("n/a") == ""


def test_a_row_the_parser_invented_is_counted_apart() -> None:
    rows = [*ROWS[:3], ["04/02/2031", "GHOST LEDGER FEE", "$12.00", "", "$840.05 CR"], *ROWS[3:]]
    judgement = judge_table(TRUTH, _html(rows))
    assert _counts(judgement) == {"right": 5} and judgement["rows_added"] == 1


def test_columns_in_another_order_are_followed_by_their_headings() -> None:
    """A parser that exchanges the two columns, headings and all, has kept every amount's meaning."""
    exchanged = [[row[0], row[1], row[3], row[2], row[4]] for row in [HEADER, *ROWS]]
    judgement = judge_table(TRUTH, _html(exchanged[1:], header=exchanged[0]))
    assert _counts(judgement) == {"right": 5} and judgement["columns"] == "header"


def test_a_header_the_parser_misread_falls_back_on_position() -> None:
    header = ["Day", "Details", "Dehits", "Crebits (-)", "Balance"]
    judgement = judge_table(TRUTH, _html(_changed(1, debits="", credits="$41.25"), header=header))
    assert judgement["columns"] == "position"
    assert _counts(judgement) == {"right": 4, "wrong_column": 1}


def test_a_table_whose_columns_cannot_be_told_apart_is_not_judged() -> None:
    merged = [[row[0], row[1], row[2] or row[3], row[4]] for row in ROWS]
    judgement = judge_table(TRUTH, _html(merged, header=["Day", "Details", "Amount", "Balance"]))
    assert judgement["columns"] == "not identified"
    assert _counts(judgement) == {"not_judged": 5} and len(judgement["findings"]) == 5


def test_a_page_with_no_table_has_every_row_missing() -> None:
    judgement = judge_table(TRUTH, "")
    assert judgement["columns"] == "no table" and _counts(judgement) == {"row_missing": 5}


def test_a_date_row_across_the_table_is_no_transaction_however_it_is_written() -> None:
    dated = TRUTH.replace(
        "<tr><td>03/02/2031</td><td>QUILL", '<tr><td colspan="5">03/02/2031</td></tr><tr><td></td><td>QUILL'
    )
    as_ordinary_row = dated.replace(
        '<td colspan="5">03/02/2031</td>', "<td>03/02/2031</td><td></td><td></td><td></td><td></td>"
    )
    assert judge_table(dated, dated)["rows_with_an_amount"] == 5
    for parsed in (dated, as_ordinary_row):
        assert _counts(judge_table(dated, parsed)) == {"right": 5}


def test_markup_and_header_cells_in_the_parsed_table_are_read_through() -> None:
    parsed = TRUTH.replace("<td>Debits</td>", "<th><b>Debits</b></th>").replace(
        "<td>$41.25</td>", "<td><strong>$41.25</strong></td>"
    )
    assert table_rows(parsed)[0][2] == "Debits"
    assert _counts(judge_table(TRUTH, parsed)) == {"right": 5}


@pytest.mark.parametrize(
    ("header", "found"),
    [
        (["Date", "Description", "Withdrawal", "Deposit", "Balance"], (2, 3)),
        (["Date", "Particulars", "Debits", "Credits", "Balance"], (2, 3)),
        (["Date of Transaction", "Description", "Debits", "Credits (-)", "Balance"], (2, 3)),
        (["Date", "Credit", "Debit"], (2, 1)),
        (["Date", "Description", "Amount", "Balance"], None),
        (["Debit", "Debits", "Credit"], None),
    ],
)
def test_the_two_columns_are_found_by_their_headings(header: list, found: object) -> None:
    assert amount_columns(header) == found


def test_ground_truth_that_is_not_a_statement_table_is_refused_with_a_diagnostic() -> None:
    with pytest.raises(ValueError, match="not a statement") as caught:
        judge_table(_html([["Oak peg", "$2.05"]], header=["Item", "Each"]), TRUTH)
    assert_diagnostic_error(caught.value)


def test_the_module_imports_only_the_standard_library() -> None:
    tree = ast.parse((REPO_ROOT / "scripts" / "transaction_columns.py").read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules and modules <= set(sys.stdlib_module_names)
