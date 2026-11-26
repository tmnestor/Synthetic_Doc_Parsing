"""Check, row by row, that the amounts of a bank statement's table are in the right columns.

    from transaction_columns import judge_table
    judgement = judge_table(truth_html, parsed_html)

A table score such as TEDS says how much of a table was reproduced. It does not know a withdrawal from a
deposit: an amount written in the wrong one of the two columns costs two cells of some two hundred, and
the page still scores about 0.99. For anyone who goes on to use the transactions as figures that is the
worst mistake a transcription can hold, so this module counts it directly, beside the table scores and
not in place of them.

Every row of the ground-truth table that holds an amount in its withdrawals or its deposits column is
paired with a row of the parsed table, and judged:

- `right`: the parsed row holds the same amounts in the same two columns;
- `wrong_column`: an amount is in the other column, or in both, or in a column that should be empty;
- `amount_misread`: the amounts are in the right columns and at least one differs;
- `amount_missing`: the parsed row holds no amount in either column;
- `row_missing`: no row of the parsed table is this row;
- `not_judged`: the parsed table's withdrawals and deposits columns could not be told apart.

Amounts are compared as figures: on their digits, decimal point and minus sign alone, so `$1,204.50` and
`1204.50` are the same amount here. How faithfully the cell was transcribed is the table scores' business.

The parsed table's two columns are found by its own header (a column whose heading begins `withdraw` or
`debit`, and one whose heading begins `deposit` or `credit`); failing that, by position when it has as
many columns as the ground truth's. Otherwise no row of that table is judged.

This file needs the standard library only, and no syntax newer than Python 3.9.
"""

import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

# How a column's heading must begin, letters only and in lower case, to be that column.
WITHDRAWAL_HEADINGS = ("withdraw", "debit")
DEPOSIT_HEADINGS = ("deposit", "credit")
KINDS = ("right", "wrong_column", "amount_misread", "amount_missing", "row_missing", "not_judged")
# How alike two rows must be, from 0 to 1, for a parsed row that is not identical to stand for a
# ground-truth row.
_ALIKE = 0.6
_NOT_A_FIGURE = re.compile(r"[^0-9.\-]")
_NOT_COMPARED = re.compile(r"[^0-9a-z]")
_NOT_A_LETTER = re.compile(r"[^a-z]")


class _Rows(HTMLParser):
    """Collect the text of each cell of each row of the first table in a piece of html."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._cell = None
        self._tables = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._tables += 1
        elif self._tables != 1:
            return
        elif tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append([])
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._tables == 1:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def table_rows(table_html: str) -> list:
    """The rows of an html table, each a list of its cells' text.

    Args:
        table_html: One table as html. Markup inside a cell is dropped and its text kept; a cell that
            spans columns is one cell.

    Returns:
        One list of strings per row, with entities decoded and runs of white space made one space. Rows
        without a cell are left out. An empty list when the html holds no table.
    """
    parser = _Rows()
    parser.feed(table_html)
    parser.close()
    return [row for row in parser.rows if row]


def amount(cell: str) -> str:
    """A cell's amount as a figure: its digits, decimal point and minus sign, and nothing else.

    A cell with no digit holds no amount, so a dash written in an empty cell is an empty cell.
    """
    figure = _NOT_A_FIGURE.sub("", cell)
    return figure if any(character.isdigit() for character in figure) else ""


def amount_columns(header: list):
    """Which two cells of a header row head the withdrawals and the deposits.

    Args:
        header: The cells of a table's first row.

    Returns:
        (index of the withdrawals column, index of the deposits column), or None unless exactly one
        heading begins as each kind does.
    """
    letters = [_NOT_A_LETTER.sub("", cell.lower()) for cell in header]
    withdrawals = [i for i, text in enumerate(letters) if text.startswith(WITHDRAWAL_HEADINGS)]
    deposits = [i for i, text in enumerate(letters) if text.startswith(DEPOSIT_HEADINGS)]
    if len(withdrawals) == 1 and len(deposits) == 1:
        return withdrawals[0], deposits[0]
    return None


def _signature(row: list) -> str:
    """What a row says, whichever cells say it: its filled cells, on letters and digits alone."""
    return "|".join(text for text in (_NOT_COMPARED.sub("", cell.lower()) for cell in row) if text)


def _paired(truth: list, parsed: list) -> dict:
    """Pair rows of the ground truth with rows of the parsed table, in order.

    Rows that say the same thing are paired first, by the longest runs of them; between those runs, a
    ground-truth row takes the most alike of the parsed rows not yet passed, if one is alike enough.

    Returns:
        Index of a ground-truth row -> index of its parsed row.
    """
    ours, theirs = [_signature(row) for row in truth], [_signature(row) for row in parsed]
    pairs: dict = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ours, theirs, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs.update(zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            start = j1
            for i in range(i1, i2):
                best, best_score = None, _ALIKE
                for j in range(start, j2):
                    score = SequenceMatcher(None, ours[i], theirs[j], autojunk=False).ratio()
                    if score > best_score:
                        best, best_score = j, score
                if best is not None:
                    pairs[i] = best
                    start = best + 1
    return pairs


def _cell(row: list, index: int) -> str:
    return row[index] if index < len(row) else ""


def _judge(truth: tuple, parsed: tuple) -> str:
    """Judge one row's two amounts, each a (withdrawal, deposit) pair of figures."""
    if parsed == truth:
        return "right"
    if not any(parsed):
        return "amount_missing"
    if parsed == truth[::-1]:
        return "wrong_column"
    if [bool(figure) for figure in parsed] == [bool(figure) for figure in truth]:
        return "amount_misread"
    return "wrong_column"


def judge_table(truth_html: str, parsed_html: str) -> dict:
    """Judge every row of a ground-truth statement table that holds an amount.

    Args:
        truth_html: The ground-truth table, its first row the header.
        parsed_html: The table a parser wrote for the same page, as html; an empty string when it wrote
            none.

    Returns:
        `rows_with_an_amount`; a count under each name in `KINDS`, which add up to it; `rows_added`, the
        parsed rows that hold an amount and stand for no ground-truth row; `columns`, how the parsed
        table's two columns were found (`header`, `position`, `not identified` or `no table`); and
        `findings`, one entry for every row not judged right, with the row as it is in each table.

    Raises:
        ValueError: The ground-truth table has no header that names a withdrawals and a deposits column.
    """
    truth = table_rows(truth_html)
    ours = amount_columns(truth[0]) if truth else None
    if ours is None:
        raise ValueError(
            "The ground-truth table is not a statement's table of transactions.\n"
            f"  What: its first row is {truth[0] if truth else 'absent'}, which does not head exactly one "
            "withdrawals column and one deposits column.\n"
            "  Where: the `html` of the table block handed to judge_table as truth_html\n"
            f"  Expected: one heading that begins with one of {WITHDRAWAL_HEADINGS} and one that begins "
            f"with one of {DEPOSIT_HEADINGS}, such as Date | Details | Debits | Credits | Balance.\n"
            "  Recover: hand judge_table the table of a bank statement page only; for a statement whose "
            "columns are headed otherwise, add the heading to WITHDRAWAL_HEADINGS or DEPOSIT_HEADINGS."
        )
    wanted = {
        index: (amount(_cell(row, ours[0])), amount(_cell(row, ours[1])))
        for index, row in enumerate(truth)
        if index and len(row) == len(truth[0])
    }
    wanted = {index: figures for index, figures in wanted.items() if any(figures)}
    judgement: dict = dict.fromkeys(KINDS, 0)
    judgement.update(rows_with_an_amount=len(wanted), rows_added=0, columns="no table", findings=[])

    parsed = table_rows(parsed_html)
    theirs = None
    if parsed:
        theirs = amount_columns(parsed[0])
        judgement["columns"] = "header"
        if theirs is None and len(parsed[0]) == len(truth[0]):
            theirs, judgement["columns"] = ours, "position"
        elif theirs is None:
            judgement["columns"] = "not identified"
    if theirs is None:
        kind = "not_judged" if parsed else "row_missing"
        judgement[kind] = len(wanted)
        judgement["findings"] = [{"kind": kind, "truth": truth[index], "parsed": None} for index in wanted]
        return judgement

    pairs = _paired(truth, parsed)
    for index, figures in wanted.items():
        row = parsed[pairs[index]] if index in pairs else None
        kind = (
            "row_missing"
            if row is None
            else _judge(figures, (amount(_cell(row, theirs[0])), amount(_cell(row, theirs[1]))))
        )
        judgement[kind] += 1
        if kind != "right":
            judgement["findings"].append({"kind": kind, "truth": truth[index], "parsed": row})
    taken = set(pairs.values())
    judgement["rows_added"] = sum(
        1
        for index, row in enumerate(parsed)
        if index and index not in taken and (amount(_cell(row, theirs[0])) or amount(_cell(row, theirs[1])))
    )
    return judgement
