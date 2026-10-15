"""The read-back: a compiled page's blocks, cells and words reshaped into stage one's records.

Most pages are invented bodies on a scratch copy of the receipt layout; the gate layouts' first cases
check the whole path end to end, through stage one's own `build_page` and `check_page`.
"""

import dataclasses
import math
from pathlib import Path

import pytest

from generators.exporters.geometry import BlockRecord, Cell, InkRecord
from generators.layout_dsl.primitives_text import resolve_role
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.omnidocbench import PageContext, build_page, check_page
from generators.typst.compile import CompiledPage, compile_page, rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, PageSpec, layout_page, load_typeset_config
from generators.typst.readback import Measured, ReadbackError, layout_errors, page_geometry
from generators.typst.translate import GATE_LAYOUTS, Translation, page_scale, translate_page
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, RECEIPTS_PATH, real_layout
from tests.renderer_helpers import corpus_entries, layouts_for

CONFIG = load_typeset_config(TYPESET_CONFIG_PATH)
PARSING = load_parsing_config(PARSING_CONFIG_PATH)
ROLL = PageSpec(width_mm=80.0, height_mm=None)
GATE_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}
WHERE = "CASE997 / receipt_thermal_80mm (typeset)"

# Invented values: none appear in ground_truth/.
ENTRY = {
    "case_id": "CASE997",
    "layout": LAYOUT_ID,
    "fields": {
        "SUPPLIER_NAME": "Pellucid Orchard Cooperative of the Upper Wendlemere Valley Growers",
        "INVENTED_ITEMS": "Quince paste",
        "INVENTED_AMOUNTS": "6.35",
    },
}
HEADING = {"type": "text", "content": "Hollowfen Larder"}
TABLE_COLUMNS: list[dict] = [
    {"key": "item", "label": "Item", "align": "left", "x": 0},
    {"key": "amount", "label": "Amount", "align": "right", "x_right": 0},
]
TABLE = {
    "type": "table",
    "rows": "pipe_fields",
    "frame": "bordered",
    "grouping": "none",
    "header": True,
    "params": {"fields": {"item": "INVENTED_ITEMS", "amount": "INVENTED_AMOUNTS"}},
    "columns": TABLE_COLUMNS,
}
SUPPLIER = {
    "type": "text",
    "content": "{SUPPLIER_NAME}",
    "budget": "SUPPLIER_NAME",
    "field": "SUPPLIER_NAME",
}


def translate(body: list, tmp_path: Path) -> Translation:
    """Translate `body` on a scratch copy of the receipt layout, as a plain page."""
    return translate_page(
        real_layout(body),
        ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=RECEIPTS_PATH,
        page=ROLL,
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    )


def read_back(translation: Translation, tmp_path: Path, *, extra: str = "") -> tuple[CompiledPage, dict]:
    """Compile a translation's page (with `extra` markup appended) and read its geometry back."""
    compiled = compile_page(translation.source + extra, workdir=tmp_path, config=CONFIG)
    return compiled, page_geometry(compiled, dpi=CONFIG.raster_dpi, translation=translation)


def page_of(geometry: dict, layout_id: str = LAYOUT_ID, doc_type: str = "receipt") -> dict:
    """Build the OmniDocBench page of a read-back geometry with stage one's own writer."""
    context = PageContext("CASE997.png", doc_type, layout_id, "typeset_plain", "none", True)
    return build_page(geometry, context=context, config=PARSING)


def lines_of(geometry: dict, block: int) -> list[InkRecord]:
    """Return a block's text lines, in the order the geometry holds them."""
    return [record for record in geometry["ink"] if record.block == block and not record.mark]


# --- words land in their blocks and cells ---------------------------------------------------------------


@pytest.fixture
def two_blocks(tmp_path: Path) -> tuple[Translation, CompiledPage, dict]:
    """An invented two-block page: a heading, then a 2 x 2 table (a header row and one item row)."""
    translation = translate([HEADING, TABLE], tmp_path)
    compiled, geometry = read_back(translation, tmp_path)
    return translation, compiled, geometry


def test_the_geometry_has_stage_ones_shape(two_blocks: tuple[Translation, CompiledPage, dict]) -> None:
    translation, compiled, geometry = two_blocks
    assert geometry["blocks"] == translation.blocks
    assert geometry["texts"] == translation.texts
    scale = CONFIG.raster_dpi / 72
    assert (geometry["width"], geometry["height"]) == (
        math.ceil(compiled.width_pt * scale),
        math.ceil(compiled.height_pt * scale),
    )
    assert rasterise(compiled.pdf, dpi=CONFIG.raster_dpi).size == (geometry["width"], geometry["height"])
    assert all(isinstance(record, InkRecord) for record in geometry["ink"])
    assert isinstance(geometry["measured"], Measured)


def test_each_word_lands_in_its_block_and_cell(two_blocks: tuple[Translation, CompiledPage, dict]) -> None:
    translation, _, geometry = two_blocks
    heading, table = translation.blocks
    assert [line.text for line in lines_of(geometry, heading.id)] == ["Hollowfen Larder"]
    assert all(line.cell is None for line in lines_of(geometry, heading.id))
    cells = {line.cell: line.text for line in lines_of(geometry, table.id)}
    assert cells == {
        Cell(0, 0, 1): "Item",
        Cell(0, 1, 1): "Amount",
        Cell(1, 0, 1): "Quince paste",
        Cell(1, 1, 1): "6.35",
    }


def test_a_line_is_its_words_in_order_with_its_size_and_role(
    two_blocks: tuple[Translation, CompiledPage, dict],
) -> None:
    translation, _, geometry = two_blocks
    (line,) = lines_of(geometry, translation.blocks[0].id)
    assert line.text == "Hollowfen Larder"
    assert line.role == "text" and not line.mark and not line.bold
    layout = real_layout([])
    # A line's size is in the geometry's own unit, the PNG's pixels, as stage one's is in its canvas's.
    size_pt = resolve_role(layout, "body") * page_scale(layout, ROLL, canvas_dpi=CONFIG.canvas_dpi)
    assert line.size == round(size_pt * CONFIG.raster_dpi / 72)
    left, top, right, bottom = line.box
    assert 0 <= left < right <= geometry["width"] and 0 <= top < bottom <= geometry["height"]


def test_lines_are_boxed_in_the_png_pixels_within_their_block(
    two_blocks: tuple[Translation, CompiledPage, dict],
) -> None:
    _, _, geometry = two_blocks
    measured: Measured = geometry["measured"]
    for line in geometry["ink"]:
        left, top, right, bottom = measured.boxes[line.block]
        assert left - 1 <= line.box[0] and line.box[2] <= right + 1
        assert top - 1 <= line.box[1] and line.box[3] <= bottom + 1


def test_the_page_passes_stage_ones_checks(two_blocks: tuple[Translation, CompiledPage, dict]) -> None:
    _, _, geometry = two_blocks
    page = page_of(geometry)
    assert check_page(page, geometry, where=WHERE, config=PARSING) == []
    assert layout_errors(geometry, where=WHERE) == []
    table = page["layout_dets"][1]
    assert table["html"] == (
        "<table><tr><td>Item</td><td>Amount</td></tr><tr><td>Quince paste</td><td>6.35</td></tr></table>"
    )


def test_a_two_line_cell_keeps_its_lines_in_order(tmp_path: Path) -> None:
    item, amount = TABLE_COLUMNS
    table = {**TABLE, "columns": [item, {**amount, "label": "Amount\nin dollars"}]}
    translation = translate([table], tmp_path)
    _, geometry = read_back(translation, tmp_path)
    header = [line.text for line in geometry["ink"] if line.cell == Cell(0, 1, 1)]
    assert header == ["Amount", "in dollars"]


# --- marks ----------------------------------------------------------------------------------------------


def test_a_code_mark_takes_its_blocks_box_and_stage_ones_payload(tmp_path: Path) -> None:
    barcode = {
        "type": "barcode",
        "content": "WX-30517",
        "symbology": "code128",
        "height": 40,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": True,
        "caption_gap": 4,
    }
    translation = translate([HEADING, barcode], tmp_path)
    _, geometry = read_back(translation, tmp_path)
    block = translation.blocks[1]
    marks = [record for record in geometry["ink"] if record.mark]
    assert marks == [
        InkRecord(
            "distractor", geometry["measured"].pixel_box(block.id), True, "WX-30517", 0, False, block.id
        )
    ]
    # The caption is in the text layer, so it is a line of the barcode's block, as stage one records it.
    assert [line.text for line in lines_of(geometry, block.id)] == ["WX-30517"]
    assert check_page(page_of(geometry), geometry, where=WHERE, config=PARSING) == []


# --- Review Focus 1: a value Typst wraps ----------------------------------------------------------------


def test_a_wrapped_value_passes_invariant_three(tmp_path: Path) -> None:
    translation = translate([SUPPLIER], tmp_path)
    _, geometry = read_back(translation, tmp_path)
    (block,) = translation.blocks
    assert len(lines_of(geometry, block.id)) >= 2, "the value must wrap for this test to mean anything"
    assert check_page(page_of(geometry), geometry, where=WHERE, config=PARSING) == []


def test_a_value_split_across_two_blocks_fails_naming_the_field(tmp_path: Path) -> None:
    halves = [{"type": "text", "content": "Pellucid Orchard"}, {"type": "text", "content": "Cooperative"}]
    translation = translate(halves, tmp_path)
    split = dataclasses.replace(translation, texts={"SUPPLIER_NAME": "Pellucid Orchard Cooperative"})
    _, geometry = read_back(split, tmp_path)
    errors = check_page(page_of(geometry), geometry, where=WHERE, config=PARSING)
    (error,) = errors
    assert_diagnostic_error(error)
    assert "field SUPPLIER_NAME" in error


# --- Review Focus 2: text the page does not show --------------------------------------------------------


def test_a_hidden_word_outside_every_block_fails_invariant_one(tmp_path: Path) -> None:
    translation = translate([HEADING], tmp_path)
    hidden = '#place(top + left, dx: -8pt, dy: -8pt, text(font: "Liberation Mono", size: 1pt, fill: white)[Smuggled])\n'
    _, geometry = read_back(translation, tmp_path, extra=hidden)
    (stray,) = [record for record in geometry["ink"] if record.block is None]
    assert stray.text == "Smuggled"
    errors = check_page(page_of(geometry), geometry, where=WHERE, config=PARSING)
    assert any("belongs to no block" in error and "'Smuggled'" in error for error in errors)
    for error in errors:
        assert_diagnostic_error(error)


# --- refusals of the read-back itself -------------------------------------------------------------------


def test_a_page_missing_a_block_record_is_refused_four_part(tmp_path: Path) -> None:
    translation = translate([HEADING], tmp_path)
    compiled = compile_page(translation.source, workdir=tmp_path, config=CONFIG)
    extra = BlockRecord(7, "text", "body[7]", None, None, 0, None)
    with pytest.raises(ReadbackError) as exc_info:
        page_geometry(
            compiled,
            dpi=CONFIG.raster_dpi,
            translation=dataclasses.replace(translation, blocks=[*translation.blocks, extra]),
        )
    assert_diagnostic_error(exc_info.value)
    assert "body[7]" in str(exc_info.value)


def test_a_word_inside_two_blocks_is_refused_four_part(tmp_path: Path) -> None:
    translation = translate([HEADING], tmp_path)
    compiled = compile_page(translation.source, workdir=tmp_path, config=CONFIG)
    (only,) = compiled.blocks
    twin = dataclasses.replace(only, record={**only.record, "id": 1, "path": "body[1]"})
    doubled = dataclasses.replace(compiled, blocks=[only, twin])
    second = translation.blocks[0]._replace(id=1, path="body[1]")
    with pytest.raises(ReadbackError) as exc_info:
        page_geometry(
            doubled,
            dpi=CONFIG.raster_dpi,
            translation=dataclasses.replace(translation, blocks=[*translation.blocks, second]),
        )
    assert_diagnostic_error(exc_info.value)
    assert "body[0]" in str(exc_info.value) and "body[1]" in str(exc_info.value)


# --- layout checks --------------------------------------------------------------------------------------


def with_box(geometry: dict, block: int, box: tuple[float, float, float, float]) -> dict:
    """Return a copy of `geometry` whose measured box for `block` is `box`."""
    measured: Measured = geometry["measured"]
    return {**geometry, "measured": measured._replace(boxes={**measured.boxes, block: box})}


def test_layout_errors_refuse_two_overlapping_blocks(
    two_blocks: tuple[Translation, CompiledPage, dict],
) -> None:
    translation, _, geometry = two_blocks
    heading, table = translation.blocks
    left, top, right, bottom = geometry["measured"].boxes[table.id]
    moved = with_box(geometry, heading.id, (left, top + 2, right, top + 20))
    (error,) = layout_errors(moved, where=WHERE)
    assert_diagnostic_error(error)
    assert heading.path in error and table.path in error and "overlap" in error


def test_layout_errors_refuse_a_box_off_the_page(
    two_blocks: tuple[Translation, CompiledPage, dict],
) -> None:
    translation, _, geometry = two_blocks
    heading = translation.blocks[0]
    left, top, right, bottom = geometry["measured"].boxes[heading.id]
    moved = with_box(geometry, heading.id, (left, top - 2 * bottom, right, top - bottom))
    errors = layout_errors(moved, where=WHERE)
    assert any("off its page" in error and heading.path in error for error in errors)
    for error in errors:
        assert_diagnostic_error(error)


def test_layout_errors_refuse_a_box_out_of_its_column(
    two_blocks: tuple[Translation, CompiledPage, dict],
) -> None:
    translation, _, geometry = two_blocks
    heading = translation.blocks[0]
    left, top, right, bottom = geometry["measured"].boxes[heading.id]
    _, column_right = geometry["measured"].columns[heading.id]
    moved = with_box(geometry, heading.id, (left, top, column_right + 5, bottom))
    (error,) = layout_errors(moved, where=WHERE)
    assert_diagnostic_error(error)
    assert "out of its column" in error and heading.path in error


def test_boxes_and_the_page_round_outward_so_a_box_at_the_page_edge_stays_on_it() -> None:
    measured = Measured(
        page=(100.4, 200.6), boxes={0: (0.7, 190.2, 100.4, 200.6)}, columns={0: (0.0, 100.4)}
    )
    assert measured.pixel_box(0) == (0, 190, 101, 201)
    assert measured.size == (101, 201)


def test_rounding_ignores_measurement_noise_at_a_whole_pixel() -> None:
    measured = Measured(page=(120.0, 120.0), boxes={0: (9.9999999, 10.0, 50.0000001, 60.0)}, columns={})
    assert measured.pixel_box(0) == (10, 10, 50, 60)


# --- the gate cases, end to end -------------------------------------------------------------------------


def gate_geometry(layout_file: str, layout_id: str, distractors: bool, tmp_path: Path) -> tuple:
    """Translate, compile and read back a gate layout's first case in ground_truth/."""
    doc_type = GATE_DOC_TYPES[Path(layout_file).stem]
    layout = layouts_for(doc_type)[layout_id]
    entry = next(e for e in corpus_entries(doc_type).values() if e["layout"] == layout_id)
    layout_path = REPO_ROOT / layout_file
    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        page=layout_page(layout, layout_path=str(layout_path), layout_id=layout_id),
        distractors=distractors,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    compiled, geometry = read_back(translation, tmp_path)
    return doc_type, entry, compiled, geometry


@pytest.mark.parametrize("distractors", [False, True], ids=["plain", "distractor"])
@pytest.mark.parametrize(("layout_file", "layout_id"), GATE_LAYOUTS)
def test_a_gate_case_read_back_passes_stage_ones_checks(
    layout_file: str, layout_id: str, distractors: bool, tmp_path: Path
) -> None:
    doc_type, entry, compiled, geometry = gate_geometry(layout_file, layout_id, distractors, tmp_path)
    where = f"{entry['case_id']} / {layout_id} (typeset)"
    page = page_of(geometry, layout_id, doc_type)
    assert check_page(page, geometry, where=where, config=PARSING) == []
    assert layout_errors(geometry, where=where) == []


def test_the_gate_receipt_keeps_every_poly_inside_its_png(tmp_path: Path) -> None:
    # Review Focus 3: a receipt's height comes from Typst, so rounding must not cut a box at its foot.
    layout_file, layout_id = next(gate for gate in GATE_LAYOUTS if "receipts" in gate[0])
    doc_type, _, compiled, geometry = gate_geometry(layout_file, layout_id, True, tmp_path)
    image = rasterise(compiled.pdf, dpi=CONFIG.raster_dpi)
    scale = CONFIG.raster_dpi / 72
    assert image.size == (geometry["width"], geometry["height"])
    assert image.size[1] == math.ceil(compiled.height_pt * scale)
    for det in page_of(geometry, layout_id, doc_type)["layout_dets"]:
        xs, ys = det["poly"][0::2], det["poly"][1::2]
        assert all(0 <= x <= image.size[0] for x in xs) and all(0 <= y <= image.size[1] for y in ys)
