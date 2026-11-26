"""Rewrite each markdown pipe table in a page of markdown as a one-line html table.

The pinned OmniDocBench evaluator reads an html table without loss, but its reader of pipe tables is
faulty: it drops every cell that holds a colon, it loses the row after one that ends in a space, it does
not see a table written without outer pipes, and it reads the text between two dollar signs in a table as
a formula. A page's pipe tables are therefore rewritten as html before the evaluator sees the page, by
`omnidocbench_formula_free.py`.

A table is read as GitHub Flavored Markdown defines it: a header row, then a delimiter row whose cells
hold only hyphens with an optional colon at either end, then the body rows. The header and the delimiter
row must have the same number of cells, and the delimiter row must hold a pipe, so a setext heading's
underline is not taken for one. The outer pipes are optional, a pipe written `\\|` stays in its cell, a
short row is padded with empty cells and a long row is cut to the header's width. One departure from the
specification: the table ends at the first line that is blank or holds no pipe, where the specification
would take a line of plain text straight after the table as one more row.

Cell text is kept as written, except that an ampersand that starts no entity and a less-than sign that
starts no tag are escaped, so the cell is the html a ground-truth cell is. Standard library only, and no
syntax newer than Python 3.9, because this runs in the evaluator's environment.
"""

import re

_PIPE = re.compile(r"(?<!\\)\|")
_DELIMITER_CELL = re.compile(r":?-+:?")
_BARE_AMPERSAND = re.compile(r"&(?!#?\w+;)")
_BARE_LESS_THAN = re.compile(r"<(?![A-Za-z/!])")


def _cells(line: str) -> list:
    """The cells of one row: split at each pipe that is not escaped, outer pipes dropped."""
    text = line.strip()
    text = text.removeprefix("|")
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _PIPE.split(text)]


def _is_row(line: str) -> bool:
    """Whether a line can be a row of a table: it is not blank and holds a pipe that is not escaped."""
    return bool(line.strip()) and _PIPE.search(line) is not None


def _is_delimiter_row(line: str) -> bool:
    """Whether a line is a table's delimiter row: a row whose every cell is hyphens and optional colons."""
    return _is_row(line) and all(_DELIMITER_CELL.fullmatch(cell) for cell in _cells(line))


def _html_cell(text: str) -> str:
    """One cell as html, with a bare ampersand and a bare less-than sign escaped."""
    return "<td>" + _BARE_LESS_THAN.sub("&lt;", _BARE_AMPERSAND.sub("&amp;", text)) + "</td>"


def _html_table(rows: list) -> str:
    """Rows of cells as one line of html, every row as wide as the first."""
    width = len(rows[0])
    lines = []
    for row in rows:
        cells = (row + [""] * width)[:width]
        lines.append("<tr>" + "".join(_html_cell(cell) for cell in cells) + "</tr>")
    return "<table>" + "".join(lines) + "</table>"


def pipe_tables_to_html(markdown: str) -> str:
    """Rewrite every pipe table in `markdown` as a one-line html table.

    Args:
        markdown: One page of markdown.

    Returns:
        The page with each pipe table replaced by `<table><tr><td>...</td></tr>...</table>` on one line.
        A page that holds no pipe table is returned unchanged, to the byte; one that holds a table comes
        back with `\\n` line endings.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    out = []
    found = False
    index = 0
    while index < len(lines):
        header = lines[index]
        is_table = (
            index + 1 < len(lines)
            and _is_row(header)
            and _is_delimiter_row(lines[index + 1])
            and len(_cells(header)) == len(_cells(lines[index + 1]))
        )
        if not is_table:
            out.append(header)
            index += 1
            continue
        found = True
        rows = [_cells(header)]
        index += 2
        while index < len(lines) and _is_row(lines[index]):
            rows.append(_cells(lines[index]))
            index += 1
        out.append(_html_table(rows))
    return "\n".join(out) if found else markdown
