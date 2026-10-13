"""The OmniDocBench page writer: one page from a render's draw-time records, and the invariants it keeps.

The first half builds a page from a hand-built geometry (every value invented, none in ground_truth/) and
checks it exactly; each invariant then fails, four-part, on a geometry built to break it. The second half
renders the first case of every layout and checks the page it writes (Review Focus 3 and 4).
"""

import copy
import html
import re
from collections import Counter

import pytest

from generators.exporters.geometry import BlockRecord, Cell, InkRecord
from generators.layout_dsl.defaults import resolve_param
from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, load_parsing_config
from generators.parsing.omnidocbench import (
    PageCheck,
    PageContext,
    build_page,
    check_page,
    map_polys,
    table_html,
)
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.test_block_scopes import ALL_LAYOUTS
from tests.layout_dsl.test_table_cells import REAL_LAYOUTS, resolve
from tests.renderer_helpers import entry_per_layout, layouts_for, render_entry

WHERE = "CASE999 / layout_scratch (plain)"
CONTEXT = PageContext(
    image_path="CASE999.png",
    doc_type="bank_statement",
    layout_id="layout_scratch",
    half="plain",
    tier="none",
    layout_scored=True,
)


@pytest.fixture(scope="module")
def config() -> ParsingConfig:
    """The shipped parsing configuration."""
    return load_parsing_config(PARSING_CONFIG_PATH)


def line(
    text: str, box: tuple[int, int, int, int], block: int | None, cell: Cell | None = None
) -> InkRecord:
    """One plain text line drawn in `block` (and `cell`)."""
    return InkRecord("text", box, False, text, 12, False, block, cell)


def mark(text: str, box: tuple[int, int, int, int], block: int) -> InkRecord:
    """One code mark or pen stroke drawn in `block`."""
    return InkRecord("distractor", box, True, text, 0, False, block, None)


def geometry() -> dict:
    """A page of seven blocks: a banner, a logo, a ranked text, an inkless text, a table, a footer, overlays.

    The table has three columns: a header whose first label is drawn on two lines, a date sub-header row
    spanning the table, a row whose date cell is blank and whose description carries a sub-line, and a row
    whose date cell is blank again.
    """
    blocks = [
        BlockRecord(0, "banner", "body[0]", None, None, 0, None),
        BlockRecord(1, "logo", "body[1]", None, None, 0, None),
        BlockRecord(2, "text", "body[2]", None, None, -1, None),
        BlockRecord(3, "text", "body[3]", None, None, 0, None),
        BlockRecord(4, "table", "body[4]", None, None, 0, 3),
        BlockRecord(5, "text", "body[5].children[0]", "foot", None, 0, None),
        BlockRecord(6, "overlay:stamp", "layout_scratch.overlay[0]", None, None, 0, None),
        BlockRecord(7, "overlay:ink_marks", "layout_scratch.overlay[1]", None, None, 0, None),
    ]
    ink = [
        line("Numbat Ledger", (20, 10, 220, 40), 0),
        mark("QT", (300, 10, 360, 70), 1),
        line("Ref 7Q-4410", (20, 50, 140, 66), 2),
        line("Date of", (20, 80, 80, 94), 4, Cell(0, 0, 1)),
        line("Entry", (20, 96, 70, 110), 4, Cell(0, 0, 1)),
        line("Details", (120, 80, 190, 94), 4, Cell(0, 1, 1)),
        line("Amount", (300, 80, 370, 94), 4, Cell(0, 2, 1)),
        line("17/08/2033", (20, 120, 110, 134), 4, Cell(1, 0, 3)),
        line("Bilby Freight", (120, 140, 230, 154), 4, Cell(2, 1, 1)),
        line("612.48", (310, 140, 370, 154), 4, Cell(2, 2, 1)),
        line("REF..07", (120, 158, 180, 168), 4, Cell(2, 1, 1)),
        line("Potoroo Bakery", (120, 180, 250, 194), 4, Cell(3, 1, 1)),
        line("93.06", (320, 180, 370, 194), 4, Cell(3, 2, 1)),
        line("Kindly retain", (20, 260, 130, 274), 5),
        line("this slip", (20, 276, 100, 290), 5),
        line("Lodged Wren", (250, 220, 360, 240), 6),
        mark("", (240, 200, 290, 215), 7),
    ]
    texts = {
        "BANK_NAME": "Numbat Ledger",
        "TRANSACTION_DESCRIPTIONS[0]": "Bilby\nFreight",
        "TRANSACTION_AMOUNTS_PAID[0]": "612.48",
    }
    return {"width": 400, "height": 300, "ink": ink, "blocks": blocks, "texts": texts}


TABLE_HTML = (
    "<table>"
    "<tr><td>Date of Entry</td><td>Details</td><td>Amount</td></tr>"
    '<tr><td colspan="3">17/08/2033</td></tr>'
    "<tr><td></td><td>Bilby Freight REF..07</td><td>612.48</td></tr>"
    "<tr><td></td><td>Potoroo Bakery</td><td>93.06</td></tr>"
    "</table>"
)


def det(category: str, poly: list[int], order: int, anno_id: int, **content: str) -> dict:
    """One expected `layout_dets` entry."""
    return {
        "category_type": category,
        "poly": poly,
        "ignore": False,
        "order": order,
        "anno_id": anno_id,
        "attribute": {},
        "line_with_spans": [],
        **content,
    }


EXPECTED_PAGE = {
    "layout_dets": [
        det("title", [20, 10, 220, 10, 220, 40, 20, 40], 2, 0, text="Numbat Ledger"),
        det("figure", [300, 10, 360, 10, 360, 70, 300, 70], 3, 1),
        det("text_block", [20, 50, 140, 50, 140, 66, 20, 66], 1, 2, text="Ref 7Q-4410"),
        det("table", [20, 80, 370, 80, 370, 194, 20, 194], 4, 4, html=TABLE_HTML),
        det("footer", [20, 260, 130, 260, 130, 290, 20, 290], 5, 5, text="Kindly retain this slip"),
        det("text_block", [250, 220, 360, 220, 360, 240, 250, 240], 6, 6, text="Lodged Wren"),
        det("abandon", [240, 200, 290, 200, 290, 215, 240, 215], 7, 7),
    ],
    "page_info": {
        "page_no": 0,
        "height": 300,
        "width": 400,
        "image_path": "CASE999.png",
        "page_attribute": {
            "language": "en",
            "doc_type": "bank_statement",
            "layout_id": "layout_scratch",
            "half": "plain",
            "tier": "none",
            "layout_scored": True,
        },
    },
    "extra": {"relation": []},
}


# --- build_page and table_html ---------------------------------------------------------------------------


def test_build_page_writes_every_inked_block_in_draw_order(config: ParsingConfig) -> None:
    """Exact page: ranks, categories, text joins, a textless figure and abandon, the inkless block omitted."""
    assert build_page(geometry(), context=CONTEXT, config=config) == EXPECTED_PAGE


def test_table_html_writes_a_rectangular_grid() -> None:
    """Two header lines in one cell, a spanning date row, blank cells kept, the sub-line in its cell."""
    page = geometry()
    assert table_html(page["blocks"][4], page["ink"]) == TABLE_HTML


def test_table_html_escapes_markup_in_cell_text() -> None:
    """A cell's text is HTML text: an ampersand or angle bracket is escaped, never read as markup."""
    block = BlockRecord(0, "table", "body[0]", None, None, 0, 1)
    ink = [line("Wren & <Robin>", (0, 0, 10, 10), 0, Cell(0, 0, 1))]
    assert table_html(block, ink) == "<table><tr><td>Wren &amp; &lt;Robin&gt;</td></tr></table>"


def test_a_parse_category_outranks_the_anchor_and_the_primitive(config: ParsingConfig) -> None:
    """Decision 5: parse_category first, then the anchor, then the primitive."""
    page = geometry()
    page["blocks"][5] = page["blocks"][5]._replace(parse_category="page_footnote")
    built = build_page(page, context=CONTEXT, config=config)
    assert [d["category_type"] for d in built["layout_dets"] if d["anno_id"] == 5] == ["page_footnote"]


def test_a_positive_parse_order_reads_after_every_unranked_block(config: ParsingConfig) -> None:
    """Decision 6: parse_order 1 sorts last; ties keep draw order."""
    page = geometry()
    page["blocks"][0] = page["blocks"][0]._replace(parse_order=1)
    built = build_page(page, context=CONTEXT, config=config)
    assert {d["anno_id"]: d["order"] for d in built["layout_dets"]} == {
        2: 1,
        1: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 6,
        0: 7,
    }


def test_order_counts_from_one_because_the_evaluator_reads_zero_as_no_order(config: ParsingConfig) -> None:
    """Decision 6: the first block reads as order 1. OmniDocBench numbers its own pages from 1, and its
    matcher takes an order of 0 as absent, so a 0-based first block would drop out of reading order."""
    built = build_page(geometry(), context=CONTEXT, config=config)
    orders = sorted(d["order"] for d in built["layout_dets"])
    assert orders == list(range(1, len(orders) + 1))


def test_a_code_mark_payload_never_enters_text(config: ParsingConfig) -> None:
    """Decision 3: a text block holding a caption and a barcode's payload carries the caption alone."""
    page = geometry()
    page["ink"].append(mark("4410772", (20, 66, 140, 76), 2))
    built = build_page(page, context=CONTEXT, config=config)
    (ranked,) = [d for d in built["layout_dets"] if d["anno_id"] == 2]
    assert ranked["text"] == "Ref 7Q-4410"
    assert ranked["poly"] == [20, 50, 140, 50, 140, 76, 20, 76]


def test_a_block_kind_with_no_category_fails_four_part(config: ParsingConfig) -> None:
    """A primitive the category map does not cover cannot be written, and the refusal says how to fix it."""
    page = geometry()
    page["blocks"][2] = page["blocks"][2]._replace(kind="sparkline")
    with pytest.raises(ValueError, match="sparkline") as raised:
        build_page(page, context=CONTEXT, config=config)
    assert_diagnostic_error(raised.value)


# --- parse_join: previous_table --------------------------------------------------------------------------


def joined_geometry() -> dict:
    """A three-column table, a text block, then a two-column table joined to the first.

    The joined table's label (its column 0) lies over the first table's Details column and its amount (its
    column 1) over the Amount column, so each lands in the column its ink overlaps, not the one its index
    names. Its second row draws only an amount, wrapped over two lines.
    """
    blocks = [
        BlockRecord(0, "table", "body[0]", None, None, 0, 3),
        BlockRecord(1, "text", "body[1]", None, None, 0, None),
        BlockRecord(2, "table", "body[2]", None, None, 0, 2, "previous_table"),
    ]
    ink = [
        line("Date", (20, 10, 60, 24), 0, Cell(0, 0, 1)),
        line("Details", (120, 10, 190, 24), 0, Cell(0, 1, 1)),
        line("Amount", (300, 10, 370, 24), 0, Cell(0, 2, 1)),
        line("09/11/2034", (20, 30, 110, 44), 0, Cell(1, 0, 1)),
        line("Quokka Supplies", (120, 30, 260, 44), 0, Cell(1, 1, 1)),
        line("45.10", (320, 30, 370, 44), 0, Cell(1, 2, 1)),
        line("Brought to account", (20, 60, 200, 74), 1),
        line("Period totals", (130, 90, 250, 104), 2, Cell(0, 0, 1)),
        line("45.10", (320, 90, 370, 104), 2, Cell(0, 1, 1)),
        line("Net", (330, 110, 370, 124), 2, Cell(1, 1, 1)),
        line("86.03", (330, 126, 370, 140), 2, Cell(1, 1, 1)),
    ]
    return {"width": 400, "height": 200, "ink": ink, "blocks": blocks, "texts": {}}


JOINED_HTML = (
    "<table>"
    "<tr><td>Date</td><td>Details</td><td>Amount</td></tr>"
    "<tr><td>09/11/2034</td><td>Quokka Supplies</td><td>45.10</td></tr>"
    "<tr><td></td><td>Period totals</td><td>45.10</td></tr>"
    "<tr><td></td><td></td><td>Net 86.03</td></tr>"
    "</table>"
)


def test_a_joined_table_is_written_as_rows_of_the_table_before_it(config: ParsingConfig) -> None:
    """Exact page: the joined table has no entry of its own; its rows close the first table's html, each
    cell in the column its ink overlaps; its ink widens the first table's poly; reading order skips it."""
    built = build_page(joined_geometry(), context=CONTEXT, config=config)
    assert built["layout_dets"] == [
        det("table", [20, 10, 370, 10, 370, 140, 20, 140], 1, 0, html=JOINED_HTML),
        det("text_block", [20, 60, 200, 60, 200, 74, 20, 74], 2, 1, text="Brought to account"),
    ]


def test_a_joined_page_keeps_every_invariant(config: ParsingConfig) -> None:
    """The joined table's ink is owned, celled within its own declared columns, and its text is read."""
    page = joined_geometry()
    page["texts"] = {"TOTAL_AMOUNT": "86.03", "SUPPLIER_NAME": "Quokka Supplies"}
    assert errors_for(page, config) == []


def test_a_joined_table_still_has_its_cells_checked(config: ParsingConfig) -> None:
    """Joining does not loosen invariant 2: a joined line outside its own declared columns is refused."""
    page = joined_geometry()
    page["ink"].append(line("Stray", (200, 150, 240, 164), 2, Cell(2, 2, 1)))
    (error,) = errors_for(page, config)
    assert "Stray" in error and "2 columns" in error and "body[2]" in error


def test_a_joined_cell_overlapping_no_column_goes_to_the_nearest_centre() -> None:
    """A cell in the gap between two columns lands in the column whose centre is nearest, not the one whose
    edge is nearest: here 5px from Details' edge but nearer Amount's centre."""
    page = joined_geometry()
    blocks = page["blocks"]
    ink = [r for r in page["ink"] if r.block == 0] + [line("Note", (265, 150, 290, 164), 2, Cell(0, 0, 1))]
    assert table_html(blocks[0], ink, [blocks[2]]).endswith(
        "<tr><td></td><td></td><td>Note</td></tr></table>"
    )


def test_a_join_skips_an_inkless_table_and_follows_a_chain(config: ParsingConfig) -> None:
    """The rows go to the nearest earlier table that was written: an inkless table between is passed over,
    and a second joined table appends to the same target after the first."""
    page = joined_geometry()
    page["blocks"][1] = BlockRecord(1, "table", "body[1]", None, None, 0, 3)
    page["ink"] = [record for record in page["ink"] if record.block != 1]
    page["blocks"].append(BlockRecord(3, "table", "body[3]", None, None, 0, 1, "previous_table"))
    page["ink"].append(line("End", (20, 150, 60, 164), 3, Cell(0, 0, 1)))
    built = build_page(page, context=CONTEXT, config=config)
    (table,) = built["layout_dets"]
    assert table["anno_id"] == 0 and table["order"] == 1
    assert table["html"] == JOINED_HTML.replace(
        "</table>", "<tr><td>End</td><td></td><td></td></tr></table>"
    )


def test_a_join_with_no_table_written_before_it_fails_four_part(config: ParsingConfig) -> None:
    """A joined table drawn before any written table has nowhere to put its rows."""
    page = joined_geometry()
    page["blocks"][0] = page["blocks"][0]._replace(kind="text", columns=None)
    page["ink"] = [record._replace(cell=None) if record.block == 0 else record for record in page["ink"]]
    with pytest.raises(ValueError, match="previous_table") as raised:
        build_page(page, context=CONTEXT, config=config)
    assert_diagnostic_error(raised.value)
    assert "body[2]" in str(raised.value) and WHERE in str(raised.value)


# --- check_page ------------------------------------------------------------------------------------------


def test_a_well_formed_page_passes_every_invariant(config: ParsingConfig) -> None:
    """The hand-built page breaks none of the five invariants."""
    page = geometry()
    assert (
        check_page(build_page(page, context=CONTEXT, config=config), page, where=WHERE, config=config) == []
    )


def errors_for(page: dict, config: ParsingConfig, *, built: dict | None = None) -> list[str]:
    """Build (unless given) and check `page`; every error is four-part and names WHERE."""
    built = build_page(page, context=CONTEXT, config=config) if built is None else built
    errors = check_page(built, page, where=WHERE, config=config)
    for error in errors:
        assert_diagnostic_error(error)
        assert WHERE in error
    return errors


def test_invariant_1_refuses_an_orphan_ink_record(config: ParsingConfig) -> None:
    """A line drawn outside every block is lost from the page's text."""
    page = geometry()
    page["ink"].append(line("Stray words", (20, 240, 90, 252), None))
    (error,) = errors_for(page, config)
    assert "Stray words" in error and "no block" in error


def test_invariant_1_refuses_ink_naming_an_unknown_block(config: ParsingConfig) -> None:
    """A line naming a block that was never opened is owned by nothing."""
    page = geometry()
    page["ink"].append(line("Stray words", (20, 240, 90, 252), 42))
    (error,) = errors_for(page, config)
    assert "Stray words" in error and "42" in error


def test_invariant_2_refuses_table_ink_without_a_cell(config: ParsingConfig) -> None:
    """Decision 8: a line inside a table block that names no cell has no place in the grid."""
    page = geometry()
    page["ink"].append(line("Loose total", (120, 200, 200, 214), 4))
    (error,) = errors_for(page, config)
    assert "Loose total" in error and "body[4]" in error


def test_invariant_2_refuses_a_cell_beyond_the_declared_columns(config: ParsingConfig) -> None:
    """Decision 8: a cell index outside the table's declared column count."""
    page = geometry()
    page["ink"].append(line("Extra column", (380, 140, 398, 154), 4, Cell(2, 3, 1)))
    (error,) = errors_for(page, config)
    assert "Extra column" in error and "3 columns" in error


def test_invariant_3_refuses_a_scored_value_absent_from_every_block(config: ParsingConfig) -> None:
    """A value the page's text does not hold cannot be read off the page; the error names the field."""
    page = geometry()
    page["texts"]["TOTAL_AMOUNT"] = "705.54"
    (error,) = errors_for(page, config)
    assert "TOTAL_AMOUNT" in error and "705.54" in error


def test_invariant_3_finds_a_value_in_a_table_cell_and_collapses_whitespace(config: ParsingConfig) -> None:
    """A wrapped value (lines joined by a newline) matches the cell its lines were drawn in."""
    page = geometry()
    page["texts"] = {"TRANSACTION_DESCRIPTIONS[1]": "Potoroo \n Bakery"}
    assert errors_for(page, config) == []


def test_invariant_3_does_not_find_a_value_split_across_two_cells(config: ParsingConfig) -> None:
    """Cells are searched one at a time: a value spanning two of them is not on the page as one string."""
    page = geometry()
    page["texts"] = {"TRANSACTION_DESCRIPTIONS[1]": "Bakery 93.06"}
    (error,) = errors_for(page, config)
    assert "TRANSACTION_DESCRIPTIONS[1]" in error


def test_invariant_4_refuses_a_poly_outside_the_page(config: ParsingConfig) -> None:
    """A corner beyond the page's width cannot locate its block."""
    page = geometry()
    built = build_page(page, context=CONTEXT, config=config)
    built["layout_dets"][0]["poly"] = [20, 10, 420, 10, 420, 40, 20, 40]
    (error,) = errors_for(page, config, built=built)
    assert "body[0]" in error and "420" in error


def test_invariant_5_refuses_a_category_outside_the_vocabulary(config: ParsingConfig) -> None:
    """A parse_category the vocabulary does not list is not an OmniDocBench category."""
    page = geometry()
    page["blocks"][2] = page["blocks"][2]._replace(parse_category="chart")
    (error,) = errors_for(page, config)
    assert "chart" in error and "category_vocabulary" in error


# --- PageCheck -------------------------------------------------------------------------------------------


def test_page_check_hands_the_render_on_then_collects_each_refusal(config: ParsingConfig) -> None:
    """The wrapped measure sees the render first; a broken invariant and an unmapped kind are both kept."""
    seen: list[tuple[str, str]] = []
    check = PageCheck(
        config, doc_type="bank_statement", mode="distractor", then=lambda c, lay, g: seen.append((c, lay))
    )
    broken = geometry()
    broken["texts"]["TOTAL_AMOUNT"] = "705.54"
    check("CASE999", "layout_scratch", broken)
    unmapped = geometry()
    unmapped["blocks"][2] = unmapped["blocks"][2]._replace(kind="sparkline")
    check("CASE998", "layout_scratch", unmapped)
    assert seen == [("CASE999", "layout_scratch"), ("CASE998", "layout_scratch")]
    first, second = check.errors
    assert "CASE999 / layout_scratch (distractor)" in first and "TOTAL_AMOUNT" in first
    assert "CASE998 / layout_scratch (distractor)" in second and "sparkline" in second
    for error in check.errors:
        assert_diagnostic_error(error)


def test_page_check_passes_a_well_formed_page_without_a_wrapped_measure(config: ParsingConfig) -> None:
    """With nothing to hand on, a page keeping every invariant records nothing."""
    check = PageCheck(config, doc_type="bank_statement", mode="plain", then=None)
    check("CASE999", "layout_scratch", geometry())
    assert check.errors == []


def test_check_page_reports_an_unmapped_kind(config: ParsingConfig) -> None:
    """Invariant 5 on records alone: a drawn kind no category map covers is refused four-part."""
    page = geometry()
    built = build_page(page, context=CONTEXT, config=config)
    page["blocks"][2] = page["blocks"][2]._replace(kind="sparkline")
    (error,) = errors_for(page, config, built=built)
    assert "sparkline" in error and "category_map.primitives" in error


# --- map_polys -------------------------------------------------------------------------------------------


def test_map_polys_with_the_identity_returns_an_equal_page(config: ParsingConfig) -> None:
    """The identity moves nothing and keeps the size."""
    built = build_page(geometry(), context=CONTEXT, config=config)
    assert map_polys(built, lambda poly: poly, width=400, height=300) == built


def test_map_polys_moves_every_corner_and_resizes_the_page(config: ParsingConfig) -> None:
    """A shift moves all four corners of every poly; the page takes the new size; the input is untouched."""
    built = build_page(geometry(), context=CONTEXT, config=config)
    before = copy.deepcopy(built)

    def shift(poly: list[float]) -> list[float]:
        return [value + (5 if index % 2 == 0 else 7) for index, value in enumerate(poly)]

    mapped = map_polys(built, shift, width=410, height=320)
    assert built == before
    assert (mapped["page_info"]["width"], mapped["page_info"]["height"]) == (410, 320)
    for old, new in zip(built["layout_dets"], mapped["layout_dets"], strict=True):
        assert new["poly"] == shift(old["poly"])
        assert {k: v for k, v in new.items() if k != "poly"} == {
            k: v for k, v in old.items() if k != "poly"
        }


# --- real layouts ----------------------------------------------------------------------------------------


def page_context(doc_type: str, layout_id: str, half: str) -> PageContext:
    """A clean page's context."""
    return PageContext("page.png", doc_type, layout_id, half, "none", True)


@pytest.mark.parametrize("distractors", [False, True], ids=["plain", "distractor"])
@pytest.mark.parametrize(("doc_type", "layout_id"), ALL_LAYOUTS)
def test_every_layout_writes_a_page_that_keeps_every_invariant(
    doc_type: str, layout_id: str, distractors: bool, config: ParsingConfig
) -> None:
    """Review Focus 4 (and 1): on the first case of every layout, every scored value is in the page text."""
    entry = entry_per_layout(doc_type)[layout_id]
    geometry_out: dict = {}
    render_entry(doc_type, entry, distractors=distractors, geometry_out=geometry_out)
    half = "distractor" if distractors else "plain"
    page = build_page(geometry_out, context=page_context(doc_type, layout_id, half), config=config)
    where = f"{entry['case_id']} / {layout_id} ({half})"
    assert check_page(page, geometry_out, where=where, config=config) == []
    assert page["layout_dets"], f"{where}: no block written"


def test_a_real_value_forced_to_differ_from_the_drawing_fails_naming_its_field(
    config: ParsingConfig,
) -> None:
    """Review Focus 4's negative: the real check is not vacuous; a value the page does not hold is refused."""
    doc_type, layout_id = ALL_LAYOUTS[0]
    entry = entry_per_layout(doc_type)[layout_id]
    geometry_out: dict = {}
    render_entry(doc_type, entry, distractors=False, geometry_out=geometry_out)
    assert geometry_out["texts"], "the render recorded no scored value to check"
    field = next(iter(geometry_out["texts"]))
    geometry_out["texts"][field] = "Xq 705.54 Zv"  # invented; absent from ground_truth/
    page = build_page(geometry_out, context=page_context(doc_type, layout_id, "plain"), config=config)
    where = f"{entry['case_id']} / {layout_id} (plain)"
    (error,) = check_page(page, geometry_out, where=where, config=config)
    assert_diagnostic_error(error)
    assert field in error and where in error


@pytest.mark.parametrize(("doc_type", "layout_id"), REAL_LAYOUTS)
def test_every_real_table_is_written_as_a_rectangular_grid(
    doc_type: str, layout_id: str, config: ParsingConfig
) -> None:
    """Review Focus 3: N columns in every row, the header row holds the labels, no ink lost or invented."""
    entry = entry_per_layout(doc_type)[layout_id]
    layout = layouts_for(doc_type)[layout_id]
    geometry_out: dict = {}
    render_entry(doc_type, entry, distractors=False, geometry_out=geometry_out)
    page = build_page(geometry_out, context=page_context(doc_type, layout_id, "plain"), config=config)
    tables = {d["anno_id"]: d for d in page["layout_dets"] if d["category_type"] == "table"}
    assert tables, f"{layout_id}: no table written"
    for anno_id, written in tables.items():
        block = geometry_out["blocks"][anno_id]
        spec = resolve(layout, block.path)
        columns = spec["columns"]
        n = len(columns)
        rows = re.findall(r"<tr>(.*?)</tr>", written["html"])
        for row in rows:
            spans = [int(span or 1) for span in re.findall(r'<td(?: colspan="(\d+)")?>', row)]
            assert sum(spans) == n, (layout_id, row)
        # A table joined to this one (parse_join) writes its rows here, so its ink is this table's too.
        owners = {anno_id}
        for later in geometry_out["blocks"][anno_id + 1 :]:
            if later.kind == "table" and later.id not in tables:
                if later.join is None:
                    break
                owners.add(later.id)
        ink = [r for r in geometry_out["ink"] if r.block in owners and not r.mark]
        header = resolve_param(
            spec, layout, "table_header", layout_id=layout_id, layout_path="<layout>", block_key="header"
        )
        if header:
            labels = [" ".join(s for s in str(c["label"]).split("\n") if s.strip()) for c in columns]
            first = [html.unescape(cell) for cell in re.findall(r"<td>(.*?)</td>", rows[0])]
            assert [" ".join(label.split()) for label in labels] == [" ".join(c.split()) for c in first]
        cell_words = Counter(
            html.unescape(" ".join(re.findall(r"<td[^>]*>(.*?)</td>", written["html"]))).split()
        )
        assert cell_words == Counter(" ".join(r.text for r in ink).split()), layout_id
