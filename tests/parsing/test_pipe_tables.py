"""A markdown pipe table becomes the one-line html table the evaluator reads without loss.

Every table and line of text here is invented for the test.
"""

from scripts.pipe_tables import pipe_tables_to_html

ROWS = "<tr><td>Part</td><td>Cost</td></tr><tr><td>Flange</td><td>7.10</td></tr>"


def test_a_table_with_outer_pipes_becomes_one_line_of_html_and_the_text_around_it_stays() -> None:
    markdown = "Opening words\n\n| Part | Cost |\n|---|---|\n| Flange | 7.10 |\n\nClosing words\n"
    assert pipe_tables_to_html(markdown) == f"Opening words\n\n<table>{ROWS}</table>\n\nClosing words\n"


def test_a_table_without_outer_pipes_is_read_the_same_way() -> None:
    assert pipe_tables_to_html("Part | Cost\n--- | ---\nFlange | 7.10\n") == f"<table>{ROWS}</table>\n"


def test_alignment_colons_and_trailing_spaces_change_nothing() -> None:
    markdown = "| Part | Cost | \n|:---|---:| \n| Flange | 7.10 | \n"
    assert pipe_tables_to_html(markdown) == f"<table>{ROWS}</table>\n"


def test_a_cell_keeps_its_colon_its_dollar_sign_and_its_dashes() -> None:
    markdown = "| Label | Sum |\n|---|---|\n| Ref: Z9 | $7.10 |\n| --- | - |\n"
    assert pipe_tables_to_html(markdown) == (
        "<table><tr><td>Label</td><td>Sum</td></tr><tr><td>Ref: Z9</td><td>$7.10</td></tr>"
        "<tr><td>---</td><td>-</td></tr></table>\n"
    )


def test_an_empty_cell_stays_an_empty_cell() -> None:
    markdown = "| Part | In | Out |\n|---|---|---|\n| Flange |  | 7.10 |\n"
    assert "<tr><td>Flange</td><td></td><td>7.10</td></tr>" in pipe_tables_to_html(markdown)


def test_an_escaped_pipe_stays_inside_its_cell() -> None:
    markdown = "| Part | Note |\n|---|---|\n| Flange | left \\| right |\n"
    assert "<td>left | right</td>" in pipe_tables_to_html(markdown)


def test_a_bare_ampersand_and_a_bare_less_than_are_escaped_and_an_entity_or_a_tag_is_not() -> None:
    markdown = "| Part | Note |\n|---|---|\n| Nut & Bolt | 3 < 4 |\n| Nut &amp; Bolt | one<br>two |\n"
    converted = pipe_tables_to_html(markdown)
    assert converted.count("<td>Nut &amp; Bolt</td>") == 2
    assert "<td>3 &lt; 4</td>" in converted
    assert "<td>one<br>two</td>" in converted


def test_a_short_row_is_padded_and_a_long_row_is_cut_to_the_header() -> None:
    markdown = "| Part | Cost |\n|---|---|\n| Flange |\n| Washer | 0.40 | spare |\n"
    converted = pipe_tables_to_html(markdown)
    assert "<tr><td>Flange</td><td></td></tr>" in converted
    assert "<tr><td>Washer</td><td>0.40</td></tr>" in converted


def test_the_table_ends_at_the_first_line_without_a_pipe() -> None:
    markdown = "| Part | Cost |\n|---|---|\n| Flange | 7.10 |\nClosing words\n"
    assert pipe_tables_to_html(markdown) == f"<table>{ROWS}</table>\nClosing words\n"


def test_two_tables_on_a_page_are_each_converted() -> None:
    table = "| Part | Cost |\n|---|---|\n| Flange | 7.10 |\n"
    assert pipe_tables_to_html(f"{table}\nBetween\n\n{table}") == (
        f"<table>{ROWS}</table>\n\nBetween\n\n<table>{ROWS}</table>\n"
    )


def test_a_header_only_table_is_one_row() -> None:
    assert pipe_tables_to_html("| Part | Cost |\n|---|---|\n") == (
        "<table><tr><td>Part</td><td>Cost</td></tr></table>\n"
    )


def test_text_that_is_not_a_table_is_returned_unchanged() -> None:
    for markdown in (
        "A line with a | pipe in it\n\nAnother line\n",
        "Heading\n---\nA setext heading is not a table\n",
        "| Part | Cost |\n|---|\n| the header and the rule disagree on the columns |\n",
        "<table><tr><td>Part: A</td><td>$7.10</td></tr></table>\n",
        "",
    ):
        assert pipe_tables_to_html(markdown) == markdown


def test_windows_line_endings_are_read() -> None:
    markdown = "| Part | Cost |\r\n|---|---|\r\n| Flange | 7.10 |\r\n"
    assert pipe_tables_to_html(markdown) == f"<table>{ROWS}</table>\n"
