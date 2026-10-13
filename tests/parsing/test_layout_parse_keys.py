"""The grouping keys the matcher check set (the design's section 5.3), each checked on a real render.

Each test renders the first case of every layout that draws the block the key is on, as the distractor
render the probe pages are drawn from, and reads the block back out of the page `build_page` writes: the
'TAX INVOICE' heading is a `title`, the statement banner is a page `header`, and the statement totals
table is written as the last row of the transaction table. The invoice contact strip stays `text_block`:
the evaluator matches ignored categories such as `header` first and removes every prediction close to
one, so a header contact email would swallow a scored payment line on the same domain.
"""

import re

import pytest

from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, load_parsing_config
from generators.parsing.omnidocbench import PageContext, build_page
from tests.layout_dsl.test_table_cells import resolve
from tests.renderer_helpers import entry_per_layout, layouts_for, render_entry

# The labels the contact strip's lines print before their values.
CONTACT_PREFIXES = ("Phone:", "Mobile:", "Freecall:", "Email:", "Telephone:", "Support:")
INVOICE_LAYOUTS = list(layouts_for("invoice"))
BANNER_LAYOUTS = [
    layout_id
    for layout_id, layout in layouts_for("bank_statement").items()
    if any(block.get("type") == "banner" for block in layout["body"])
]
TOTALS_LAYOUTS = [
    layout_id
    for layout_id, layout in layouts_for("bank_statement").items()
    if any(block.get("rows") == "bank_transaction_totals" for block in layout["body"])
]


@pytest.fixture(scope="module")
def config() -> ParsingConfig:
    """The shipped parsing configuration."""
    return load_parsing_config(PARSING_CONFIG_PATH)


def written(
    doc_type: str, layout_id: str, config: ParsingConfig
) -> tuple[dict, dict, list[tuple[dict, dict]]]:
    """Render a layout's first case (distractor half) and pair each written entry with its layout block.

    Returns:
        The render's geometry, its page, and each body block's `layout_dets` entry with the block dict that
        drew it.
    """
    entry = entry_per_layout(doc_type)[layout_id]
    geometry: dict = {}
    render_entry(doc_type, entry, distractors=True, geometry_out=geometry)
    context = PageContext("page.png", doc_type, layout_id, "distractor", "none", True)
    page = build_page(geometry, context=context, config=config)
    layout = layouts_for(doc_type)[layout_id]
    paths = {det["anno_id"]: geometry["blocks"][det["anno_id"]].path for det in page["layout_dets"]}
    # An overlay site's path names the layout's overlay list, not a body block; it has no block dict here.
    pairs = [
        (det, resolve(layout, paths[det["anno_id"]]))
        for det in page["layout_dets"]
        if paths[det["anno_id"]].startswith("body")
    ]
    return geometry, page, pairs


@pytest.mark.parametrize("layout_id", INVOICE_LAYOUTS)
def test_the_tax_invoice_heading_is_a_title(layout_id: str, config: ParsingConfig) -> None:
    """Recommendation 2: the heading every invoice layout prints is written as a `title`."""
    _, _, pairs = written("invoice", layout_id, config)
    headings = [det for det, block in pairs if block.get("content") == "TAX INVOICE"]
    assert headings, f"{layout_id}: no heading written"
    for det in headings:
        assert det["category_type"] == "title", layout_id
        assert det["text"] == "TAX INVOICE"


@pytest.mark.parametrize("layout_id", INVOICE_LAYOUTS)
def test_the_invoice_contact_strip_stays_text(layout_id: str, config: ParsingConfig) -> None:
    """Recommendation 3, withdrawn for invoices: each contact line of the letterhead is its own `text_block`
    (line-level blocks stay the default), and no block on an invoice is a header."""
    _, page, pairs = written("invoice", layout_id, config)
    contact = [det for det, block in pairs if str(block.get("content", "")).startswith(CONTACT_PREFIXES)]
    assert contact, f"{layout_id}: the first case draws no contact line"
    for det in contact:
        assert det["category_type"] == "text_block", (layout_id, det["text"])
        assert det["text"].startswith(CONTACT_PREFIXES)
    assert not [det for det in page["layout_dets"] if det["category_type"] == "header"], layout_id


@pytest.mark.parametrize("layout_id", BANNER_LAYOUTS)
def test_the_statement_banner_is_a_page_header(layout_id: str, config: ParsingConfig) -> None:
    """Recommendation 3: the statement banner is a `header`; the account panels beneath it stay text."""
    _, page, pairs = written("bank_statement", layout_id, config)
    (banner,) = [det for det, block in pairs if block.get("type") == "banner"]
    assert banner["category_type"] == "header"
    assert banner["text"] == layouts_for("bank_statement")[layout_id]["logo_text"]
    headers = [det for det in page["layout_dets"] if det["category_type"] == "header"]
    assert headers == [banner], layout_id
    holder = [det for det, block in pairs if block.get("label") == "Account Holder"]
    assert holder and all(det["category_type"] == "text_block" for det in holder), layout_id


def test_the_layouts_under_test_are_the_ones_that_draw_the_blocks() -> None:
    """The banner and the totals table are drawn by the two ANZ layouts; a change to that shows here."""
    assert BANNER_LAYOUTS == TOTALS_LAYOUTS == ["anz_standard", "anz_modern"]


@pytest.mark.parametrize("layout_id", TOTALS_LAYOUTS)
def test_the_statement_totals_are_the_last_row_of_the_transaction_table(
    layout_id: str, config: ParsingConfig
) -> None:
    """Recommendation 7: the one-row totals table joins the transaction table as its last row, N cells wide,
    each in its own column; it is written as no table of its own, and its ink widens the table's poly."""
    geometry, page, pairs = written("bank_statement", layout_id, config)
    tables = [(det, block) for det, block in pairs if block.get("type") == "table"]
    ((det, block),) = tables
    assert block["rows"] == "bank_transactions"
    n = len(block["columns"])
    rows = re.findall(r"<tr>(.*?)</tr>", det["html"])
    last = re.findall(r"<td>(.*?)</td>", rows[-1])
    assert len(last) == n
    (totals,) = [b for b in geometry["blocks"] if b.join == "previous_table"]
    assert totals.kind == "table"
    totals_ink = [r for r in geometry["ink"] if r.block == totals.id]
    by_column = {r.cell.column: r.text for r in totals_ink if r.cell is not None}
    # The totals table shares the transaction table's columns, so each cell keeps its column index.
    assert last == [by_column.get(column, "") for column in range(n)]
    assert last[1] == "Totals at end of period"
    assert det["poly"][5] == max(r.box[3] for r in totals_ink)
