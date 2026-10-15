"""The translator: a layout tree and an entry's values, written as one page of Typst markup.

Most claims are read off the markup of a scratch receipt layout whose body is swapped for a few invented
blocks (see `tests.layout_dsl.dsl_helpers`). The records the translation hands back are checked against
what stage one's own renderers record for the gate layouts' first cases, and one test compiles a page to
check the record wrappers report their boxes.
"""

import copy
import json
import re
import subprocess
from pathlib import Path

import pypdfium2
import pytest
import segno
from PIL import ImageFont

from generators.common import load_font
from generators.layout_dsl.code128 import encode_code128
from generators.layout_dsl.engine import PRIMITIVE_DRAWERS, SCOPED_LEAVES
from generators.layout_dsl.schema import FRAMES, GROUPINGS
from generators.loader import load_layout_registry
from generators.parsing.omnidocbench import OVERLAY_PREFIX
from generators.typst.config import TYPESET_CONFIG_PATH, PageSpec, layout_page, load_typeset_config
from generators.typst.translate import (
    GATE_LAYOUTS,
    SUPPORTED_OPTIONS,
    TRANSLATED,
    Translation,
    TranslateError,
    gate_primitives,
    page_scale,
    translate_page,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, RECEIPTS_PATH, real_layout
from tests.renderer_helpers import corpus_entries, layouts_for, render_entry

CONFIG = load_typeset_config(TYPESET_CONFIG_PATH)
ROLL = PageSpec(width_mm=80.0, height_mm=None)
A4 = PageSpec(width_mm=210.0, height_mm=297.0)
GATE_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}

# Invented values: none appear in ground_truth/.
ENTRY: dict = {
    "case_id": "CASE998",
    "layout": LAYOUT_ID,
    "fields": {
        "TOTAL_AMOUNT": "41.07",
        "SUPPLIER_NAME": "Quillon Paper Goods",
        "INVENTED_ITEMS": "Kettle descaler | Linen tea towel",
        "INVENTED_AMOUNTS": "7.15 | 12.40",
    },
}
TEXT = {"type": "text", "content": "Marrowbank Tide Tables"}
OTHER_TEXT = {"type": "text", "content": "Sallow Creek Almanac"}
TABLE_COLUMNS: list[dict] = [
    {"key": "item", "label": "Item", "align": "left", "x": 0},
    {"key": "amount", "label": "Amount", "align": "right", "x_right": 0},
]
TABLE: dict = {
    "type": "table",
    "rows": "pipe_fields",
    "frame": "bordered",
    "grouping": "none",
    "header": True,
    "params": {"fields": {"item": "INVENTED_ITEMS", "amount": "INVENTED_AMOUNTS"}},
    "columns": TABLE_COLUMNS,
}

# One minimal body per primitive the receipt layout can draw, each holding at least one block a parser
# finds.
BODIES: dict[str, list] = {
    "text": [TEXT],
    "pair": [{"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}"}],
    "block": [{"type": "block", "heading": "Harbour Notes", "lines": ["Moorings", "", "Slipway"]}],
    "rule": [TEXT, {"type": "rule"}, OTHER_TEXT],
    "spacer": [TEXT, {"type": "spacer", "height": 9}, OTHER_TEXT],
    "split": [{"type": "split", "children": [[TEXT], [OTHER_TEXT]]}],
    "stack": [{"type": "stack", "children": [TEXT, OTHER_TEXT]}],
    "panel": [{"type": "panel", "padding": 4, "children": [TEXT]}],
    "table": [TABLE],
    "logo": [
        {
            "type": "logo",
            "content": "{SUPPLIER_NAME}",
            "style": "circle",
            "size": 48,
            "min_text_size": 12,
            "inset": 0,
            "text_color": "white",
        }
    ],
    "barcode": [
        {
            "type": "barcode",
            "content": "QZ-48213",
            "symbology": "code128",
            "height": 40,
            "module_px": 2,
            "quiet_zone": 10,
            "human_readable": True,
            "caption_gap": 4,
        }
    ],
    "qrcode": [
        {
            "type": "qrcode",
            "content": "https://quillon.example.test",
            "module_px": 3,
            "quiet_zone": 4,
            "error_level": "m",
        }
    ],
}


def translate(body: list, tmp_path: Path, *, entry: dict | None = None) -> Translation:
    """Translate `body` on a scratch copy of the receipt layout, as a plain page."""
    return translate_page(
        real_layout(body),
        entry if entry is not None else ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=RECEIPTS_PATH,
        page=ROLL,
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    )


def own_walk(blocks: list) -> set[str]:
    """Collect every primitive type in a block list and its nested children."""
    found: set[str] = set()
    for block in blocks:
        found.add(str(block["type"]))
        children = block.get("children")
        if children is None:
            continue
        columns = children if block["type"] == "split" else [children]
        for column in columns:
            found |= own_walk(column)
    return found


def gate_case(stem: str, layout_id: str) -> tuple[dict, dict]:
    """Return a gate layout and its first case in ground_truth/ (Decision 9)."""
    doc_type = GATE_DOC_TYPES[stem]
    entry = next(e for e in corpus_entries(doc_type).values() if e["layout"] == layout_id)
    return layouts_for(doc_type)[layout_id], entry


def call_args(source: str, name: str) -> list[str]:
    """Return the argument text of every `name(...)` call opening a line of `source`, up to `body:`."""
    return re.findall(rf"{name}\((id: [^\n]*)", source)


# --- the gate primitives --------------------------------------------------------------------------------


def test_the_gate_primitives_are_every_type_the_gate_layouts_use() -> None:
    expected: set[str] = set()
    for layout_file, layout_id in GATE_LAYOUTS:
        expected |= own_walk(load_layout_registry(REPO_ROOT / layout_file)[layout_id]["body"])
    assert gate_primitives() == frozenset(expected)
    assert expected <= set(BODIES)


def test_the_translator_handles_every_primitive_stage_one_draws() -> None:
    assert TRANSLATED == frozenset(PRIMITIVE_DRAWERS)


def test_every_primitive_a_layout_uses_is_translated() -> None:
    used: set[str] = set()
    for layout_file in sorted((REPO_ROOT / "config" / "layouts").glob("*.yml")):
        for layout in load_layout_registry(layout_file).values():
            used |= own_walk(layout["body"])
    assert used <= TRANSLATED
    # Every primitive but `banner`, which paints from a sheet's corner, has a body on the receipt layout.
    assert set(BODIES) == TRANSLATED - {"banner"}


def test_no_shipped_layout_opens_a_block_around_a_container() -> None:
    # Read-back joins the words on one baseline into one line (Decision 3). Inside one block holding a
    # split, that would interleave its columns row by row, unlike stage one's column-by-column draw, so a
    # layout setting `parse_block: true` needs the read-back taught first.
    def opened(blocks: list) -> list[str]:
        found = []
        for block in blocks:
            if block.get("parse_block") is True:
                found.append(str(block["type"]))
            children = block.get("children") or []
            for column in children if block["type"] == "split" else [children]:
                found += opened(column)
        return found

    for layout_file in sorted((REPO_ROOT / "config" / "layouts").glob("*.yml")):
        for layout_id, layout in load_layout_registry(layout_file).items():
            assert opened(layout["body"]) == [], layout_id


# The template call each primitive's body is written as: a text, a block and a pair drawn as one string
# are written as the lines stage one drew.
CALLS = {"text": "dsl-lines(", "block": "dsl-lines(", "pair": "dsl-lines("}


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_each_primitive_translates_to_its_template_call_and_its_records(kind: str, tmp_path: Path) -> None:
    translation = translate(BODIES[kind], tmp_path)
    assert CALLS.get(kind, f"dsl-{kind}(") in translation.source
    assert translation.blocks, "every minimal body holds a block a parser finds"
    records = call_args(translation.source, "block-record")
    assert len(records) == len(translation.blocks)
    for record, block in zip(records, translation.blocks, strict=True):
        assert record.startswith(f'id: {block.id}, kind: "{block.kind}", path: "{block.path}"')
    if kind in SCOPED_LEAVES:
        assert [block.kind for block in translation.blocks] == [kind]


def test_a_page_imports_the_configured_template_and_sets_its_physical_page(tmp_path: Path) -> None:
    source = translate([TEXT], tmp_path).source
    assert f'#import "/{CONFIG.template}": *' in source
    assert "#show: typeset" in source
    assert re.search(r"#set page\(width: 80(\.0+)?mm, height: auto,", source)


def test_a_sheet_has_its_fixed_height(tmp_path: Path) -> None:
    invoices = REPO_ROOT / "config" / "layouts" / "invoices.yml"
    layout = copy.deepcopy(load_layout_registry(invoices)["tax_invoice_standard"])
    layout.update(body=[TEXT], field_providers=[], distractor_items=[])
    sheet = translate_page(
        layout,
        {**ENTRY, "layout": "tax_invoice_standard"},
        layout_id="tax_invoice_standard",
        layout_path=invoices,
        page=A4,
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    assert re.search(r"#set page\(width: 210(\.0+)?mm, height: 297(\.0+)?mm,", sheet.source)


def test_a_roll_converts_pixels_at_its_width_over_the_layout_width() -> None:
    roll = load_layout_registry(REPO_ROOT / "config" / "layouts" / "receipts.yml")[LAYOUT_ID]
    expected = 80.0 / roll["width"] * 72 / 25.4
    assert page_scale(roll, ROLL, canvas_dpi=CONFIG.canvas_dpi) == pytest.approx(expected)


def test_a_sheet_converts_pixels_at_the_canvas_resolution() -> None:
    sheet = load_layout_registry(REPO_ROOT / "config" / "layouts" / "invoices.yml")["tax_invoice_standard"]
    assert page_scale(sheet, A4, canvas_dpi=CONFIG.canvas_dpi) == pytest.approx(72 / CONFIG.canvas_dpi)


@pytest.mark.parametrize(
    ("stem", "layout_id"), [("invoices", "tax_invoice_standard"), ("bank_statements", "westpac_standard")]
)
def test_a_sheet_centres_its_content_within_the_page_width(
    stem: str, layout_id: str, tmp_path: Path
) -> None:
    layout_path = REPO_ROOT / "config" / "layouts" / f"{stem}.yml"
    layout = copy.deepcopy(load_layout_registry(layout_path)[layout_id])
    layout.update(body=[TEXT], field_providers=[], distractor_items=[])
    source = translate_page(
        layout,
        {**ENTRY, "layout": layout_id},
        layout_id=layout_id,
        layout_path=layout_path,
        page=A4,
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    ).source
    found = re.search(r"margin: \(left: ([\d.]+)pt, right: ([\d.]+)pt, top: ([\d.]+)pt", source)
    assert found is not None, source
    left, right, top = (float(found.group(i)) for i in (1, 2, 3))
    scale = 72 / CONFIG.canvas_dpi
    content = layout["content_width"] * scale
    a4_width = 210.0 * 72 / 25.4
    # The content's own margins are equal, whatever the canvas's margins were.
    assert left == pytest.approx(right, abs=1e-3)
    assert left + content + right == pytest.approx(a4_width, abs=1e-3)
    assert top == pytest.approx(layout["margin"] * scale, abs=1e-3)


def test_text_is_set_in_the_font_the_layout_names(tmp_path: Path) -> None:
    source = translate([TEXT], tmp_path).source
    # The receipt layout's family is liberation_mono, whose face Typst knows by its own name.
    assert 'font: "Liberation Mono"' in source


def test_a_text_is_written_as_the_value_stage_one_draws(tmp_path: Path) -> None:
    source = translate(
        [TEXT, {"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}"}], tmp_path
    ).source
    assert '"Marrowbank Tide Tables"' in source
    # Stage one draws an unbudgeted, left-aligned pair as one string.
    assert '"Yield: 41.07"' in source


def test_a_block_gated_off_by_when_is_not_written(tmp_path: Path) -> None:
    gated = {"type": "text", "content": "Thistlemoor Gate Notice", "when": "ABSENT_FIELD"}
    translation = translate([TEXT, gated], tmp_path)
    assert "Thistlemoor Gate Notice" not in translation.source
    assert [block.path for block in translation.blocks] == ["body[0]"]


def test_a_table_has_one_cell_record_per_cell(tmp_path: Path) -> None:
    translation = translate([TABLE], tmp_path)
    cells = re.findall(r"dsl-cell\(block: (\d+), row: (\d+), col: (\d+), span: (\d+)", translation.source)
    # A header row and two item rows, two columns each.
    assert sorted((int(r), int(c)) for _, r, c, _ in cells) == [(r, c) for r in range(3) for c in range(2)]
    assert {int(b) for b, *_ in cells} == {translation.blocks[0].id}
    assert '"Linen tea towel"' in translation.source
    assert '"12.40"' in translation.source


# --- refusals -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "where", "kind"),
    [
        (
            [TEXT, {"type": "split", "children": [[TEXT], [{"type": "marquee"}]]}],
            "body[1].children[1][0]",
            "marquee",
        ),
        ([{"type": "lantern", "lines": ["Hollin"], "when": "ABSENT_FIELD"}], "body[0]", "lantern"),
    ],
)
def test_an_untranslated_primitive_is_refused_four_part(
    body: list, where: str, kind: str, tmp_path: Path
) -> None:
    with pytest.raises(TranslateError) as exc_info:
        translate(body, tmp_path)
    message = str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
    assert f"{RECEIPTS_PATH.resolve()} -> {LAYOUT_ID}.{where}" in message
    assert f"'{kind}'" in message
    assert str(sorted(TRANSLATED)) in message


def test_an_untranslated_table_option_is_refused_four_part(tmp_path: Path) -> None:
    grouped = {**TABLE, "grouping": "fortnightly"}
    with pytest.raises(TranslateError) as exc_info:
        translate([TEXT, grouped], tmp_path)
    message = str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
    assert f"{RECEIPTS_PATH.resolve()} -> {LAYOUT_ID}.body[1].grouping" in message
    assert "'fortnightly'" in message


def test_every_table_grouping_and_frame_and_a_split_divider_are_translated() -> None:
    assert set(SUPPORTED_OPTIONS["table"]["grouping"]) == set(GROUPINGS)
    assert set(SUPPORTED_OPTIONS["table"]["frame"]) == set(FRAMES)
    assert True in SUPPORTED_OPTIONS["split"]["divider"]
    assert "qrcode" not in SUPPORTED_OPTIONS, "a qrcode's caption is translated, whatever it says"


@pytest.mark.parametrize(
    ("body", "where", "names"),
    [
        (
            [{"type": "stack", "children": [TEXT, {**OTHER_TEXT, "anchor": "foot"}]}],
            "body[0].children[1].anchor",
            "anchored to the foot",
        ),
        (
            [
                {
                    "type": "split",
                    "children": [[TEXT], [{"type": "stack", "anchor": "foot", "children": [TEXT]}]],
                }
            ],
            "body[0].children[1][0].anchor",
            "anchored to the foot",
        ),
        (
            [
                {
                    "type": "stack",
                    "children": [{"type": "banner", "content": "Ferry", "height": 40, "color": "navy"}],
                }
            ],
            "body[0].children[0]",
            "'banner' block is nested",
        ),
        (
            [
                {
                    **TABLE,
                    "columns": [TABLE_COLUMNS[0], {**TABLE_COLUMNS[1], "sub_line": {"key": "item"}}],
                }
            ],
            "body[0].columns[1].sub_line",
            "right-aligned table column carries a sub_line",
        ),
    ],
    ids=["foot-in-stack", "foot-in-split", "nested-banner", "right-sub-line"],
)
def test_a_placement_the_translator_cannot_make_is_refused_four_part(
    body: list, where: str, names: str, tmp_path: Path
) -> None:
    with pytest.raises(TranslateError) as exc_info:
        translate(body, tmp_path)
    message = str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
    assert f"{RECEIPTS_PATH.resolve()} -> {LAYOUT_ID}.{where}" in message
    assert names in message


# --- the records are stage one's -----------------------------------------------------------------------


@pytest.mark.parametrize("distractors", [False, True], ids=["plain", "distractor"])
@pytest.mark.parametrize(("layout_file", "layout_id"), GATE_LAYOUTS)
def test_a_gate_case_carries_the_blocks_and_texts_stage_one_records(
    layout_file: str, layout_id: str, distractors: bool, tmp_path: Path
) -> None:
    layout, entry = gate_case(Path(layout_file).stem, layout_id)
    geometry: dict = {}
    render_entry(
        GATE_DOC_TYPES[Path(layout_file).stem], entry, distractors=distractors, geometry_out=geometry
    )
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
    # Overlay sites are drawn after the body, so they are the last blocks; Task 5 places them.
    body_blocks = [b for b in geometry["blocks"] if not b.kind.startswith(OVERLAY_PREFIX)]
    assert translation.blocks == body_blocks
    assert translation.texts == geometry["texts"]
    assert len(call_args(translation.source, "block-record")) == len(body_blocks)


# --- the record wrappers report their boxes ------------------------------------------------------------


def typst(source: str, *args: str) -> subprocess.CompletedProcess:
    """Run Typst on `source` (read from stdin) over the repository's fonts alone, rooted at the repository.

    The page imports the template by its path from the root, so it is fed on stdin: a page file would
    have to sit inside the root.
    """
    return subprocess.run(
        [
            "typst",
            *args,
            "--root",
            str(REPO_ROOT),
            "--font-path",
            str(REPO_ROOT / CONFIG.font_dir),
            "--ignore-system-fonts",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )


def compile_page(source: str, pdf: Path) -> None:
    """Compile a page, asserting Typst neither fails nor warns."""
    done = typst(source, "compile", "-", str(pdf))
    assert done.returncode == 0, done.stderr
    assert "warning" not in done.stderr, done.stderr


def query(source: str, label: str) -> list[dict]:
    """Return the value of every labelled metadata element on a page."""
    done = typst(source, "query", "-", f"<{label}>", "--field", "value")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_compiled_page_reports_each_record_with_its_box(tmp_path: Path) -> None:
    translation = translate([TEXT, {"type": "spacer", "height": 9}, TABLE], tmp_path)
    compile_page(translation.source, tmp_path / "page.pdf")

    blocks = query(translation.source, "block-record")
    assert [(b["id"], b["kind"], b["path"]) for b in blocks] == [
        (b.id, b.kind, b.path) for b in translation.blocks
    ]
    for value in blocks:
        assert {"anchor", "category", "order", "columns", "join"} <= set(value)
        assert value["page"] == 1
        assert value["w"] > 0 and value["h"] > 0
    text, table = blocks
    assert table["columns"] == 2
    assert table["y"] >= text["y"] + text["h"], "the table sits below the text"

    cells = query(translation.source, "cell-record")
    assert sorted((c["row"], c["col"]) for c in cells) == [(r, c) for r in range(3) for c in range(2)]
    for cell in cells:
        assert cell["block"] == table["id"]
        assert {"span", "page", "x", "y", "w", "h"} <= set(cell)
        assert table["x"] <= cell["x"] + 1e-3 and cell["x"] + cell["w"] <= table["x"] + table["w"] + 1e-3


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_each_gate_primitive_compiles_and_reports_its_records(kind: str, tmp_path: Path) -> None:
    translation = translate(BODIES[kind], tmp_path)
    compile_page(translation.source, tmp_path / "page.pdf")
    assert [b["id"] for b in query(translation.source, "block-record")] == [
        b.id for b in translation.blocks
    ]


@pytest.mark.parametrize("distractors", [False, True], ids=["plain", "distractor"])
@pytest.mark.parametrize(("layout_file", "layout_id"), GATE_LAYOUTS)
def test_a_gate_case_compiles_to_one_page_without_warnings(
    layout_file: str, layout_id: str, distractors: bool, tmp_path: Path
) -> None:
    layout, entry = gate_case(Path(layout_file).stem, layout_id)
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
    pdf = tmp_path / "page.pdf"
    compile_page(translation.source, pdf)
    assert len(pypdfium2.PdfDocument(str(pdf))) == 1
    assert len(query(translation.source, "block-record")) == len(translation.blocks)


# --- each record's box is its element's own -------------------------------------------------------------


def roll_column() -> tuple[float, float]:
    """Return the left and right edges, in points, of the scratch receipt layout's content column."""
    layout = real_layout([])
    scale = page_scale(layout, ROLL, canvas_dpi=CONFIG.canvas_dpi)
    left = layout["margin"] * scale
    return left, left + layout["content_width"] * scale


def boxes(body: list, tmp_path: Path) -> list[dict]:
    """Compile `body` on the scratch receipt layout and return its block records in draw order."""
    source = translate(body, tmp_path).source
    compile_page(source, tmp_path / "page.pdf")
    return query(source, "block-record")


def test_a_short_left_aligned_text_reports_its_own_width(tmp_path: Path) -> None:
    (record,) = boxes([{"type": "text", "content": "Wren"}], tmp_path)
    left, right = roll_column()
    assert record["x"] == pytest.approx(left, abs=1.0)
    assert 0 < record["w"] < (right - left) / 4


def test_a_right_aligned_text_ends_at_its_column_edge(tmp_path: Path) -> None:
    (record,) = boxes([{"type": "text", "content": "Wren", "align": "right"}], tmp_path)
    left, right = roll_column()
    assert record["x"] + record["w"] == pytest.approx(right, abs=1.0)
    assert record["w"] < (right - left) / 4


def test_a_right_aligned_text_in_a_split_ends_at_its_own_column_edge(tmp_path: Path) -> None:
    body = [
        {"type": "split", "children": [[TEXT], [{"type": "text", "content": "Wren", "align": "right"}]]}
    ]
    _, record = boxes(body, tmp_path)
    _, right = roll_column()
    assert record["x"] + record["w"] == pytest.approx(right, abs=1.0)


def test_a_centred_logo_reports_a_box_centred_in_its_column(tmp_path: Path) -> None:
    (record,) = boxes([{**BODIES["logo"][0], "align": "center"}], tmp_path)
    left, right = roll_column()
    scale = page_scale(real_layout([]), ROLL, canvas_dpi=CONFIG.canvas_dpi)
    assert record["x"] + record["w"] / 2 == pytest.approx((left + right) / 2, abs=1.0)
    assert record["w"] == pytest.approx(BODIES["logo"][0]["size"] * scale, abs=0.5)


def test_a_qrcode_reports_its_drawn_size(tmp_path: Path) -> None:
    block = BODIES["qrcode"][0]
    (record,) = boxes([block], tmp_path)
    modules = len(
        segno.make(block["content"], error=block["error_level"], micro=False, boost_error=False).matrix
    )
    scale = page_scale(real_layout([]), ROLL, canvas_dpi=CONFIG.canvas_dpi)
    side = (modules + 2 * block["quiet_zone"]) * block["module_px"] * scale
    assert record["w"] == pytest.approx(side, abs=0.5)
    assert record["h"] == pytest.approx(side, abs=0.5)


def ink_lines(pdf: Path, box: dict) -> list[tuple[float, float]]:
    """Return each text line drawn inside a record's box, top to bottom, as (top, right edge) in points.

    Characters come from the PDF's text layer; a character belongs to the box when its centre lies in it.
    """
    page = pypdfium2.PdfDocument(str(pdf))[0]
    height = page.get_height()
    text = page.get_textpage()
    chars: list[tuple[float, float]] = []
    for index in range(text.count_chars()):
        if not text.get_text_range(index, 1).strip():
            continue
        left, bottom, right, top = text.get_charbox(index)
        x, y = (left + right) / 2, height - (top + bottom) / 2
        if box["x"] <= x <= box["x"] + box["w"] and box["y"] <= y <= box["y"] + box["h"]:
            # The loose box spans the font's height, so the characters of one line share its top.
            chars.append((height - text.get_charbox(index, loose=True)[3], right))
    lines: list[tuple[float, float]] = []
    for top, right in sorted(chars):
        if lines and top - lines[-1][0] < 2.0:
            lines[-1] = (lines[-1][0], max(lines[-1][1], right))
        else:
            lines.append((top, right))
    return lines


def test_a_wrapped_right_aligned_text_keeps_each_line_on_its_right_edge(tmp_path: Path) -> None:
    long = "Marrowbank tide tables and soundings for the reef, the bar and the outer moorings"
    block = {"type": "text", "content": long, "align": "right", "budget": "DISTRACTOR_PARAGRAPH"}
    translation = translate([block], tmp_path)
    pdf = tmp_path / "page.pdf"
    compile_page(translation.source, pdf)
    (record,) = query(translation.source, "block-record")
    left, right = roll_column()
    lines = ink_lines(pdf, record)
    assert len(lines) >= 2, "the text must wrap for this test to mean anything"
    # Exactly the lines stage one wrapped it to: Typst does not re-flow them.
    assert len(lines) == len(translation.ink)
    assert record["w"] <= right - left + 1e-3
    for _, line_right in lines:
        assert line_right == pytest.approx(record["x"] + record["w"], abs=1.0)
    scale = page_scale(real_layout([]), ROLL, canvas_dpi=CONFIG.canvas_dpi)
    advance = real_layout([])["defaults"]["line_advance"]["body"] * scale
    assert record["h"] == pytest.approx(len(lines) * advance, abs=0.5)


def test_each_line_is_set_at_the_size_stage_one_fitted_it_to(tmp_path: Path) -> None:
    # PAYMENT_LINE shrinks a line to fit the roll's width, onto one line.
    long = "Marrowbank tide tables and soundings for the reef bar"
    block = {"type": "text", "content": long, "budget": "PAYMENT_LINE"}
    translation = translate([block], tmp_path)
    assert len(translation.ink) == 1
    nominal = real_layout([])["font_sizes"]["body"]
    sizes = {record.size for record in translation.ink}
    assert len(sizes) == 1 and sizes.pop() < nominal, (
        "stage one must shrink the text for this to mean anything"
    )
    scale = page_scale(real_layout([]), ROLL, canvas_dpi=CONFIG.canvas_dpi)
    for record in translation.ink:
        assert f"size: {record.size * scale:.4f}pt" in translation.source
        assert _string_literal(record.text) in translation.source


def test_a_line_stage_one_drew_past_its_column_stays_one_line_and_is_boxed_where_it_lies(
    tmp_path: Path,
) -> None:
    long = "Marrowbank tide tables and soundings for the reef, the bar and the outer moorings"
    translation = translate([{"type": "text", "content": long}], tmp_path)
    pdf = tmp_path / "page.pdf"
    compile_page(translation.source, pdf)
    (record,) = query(translation.source, "block-record")
    left, right = roll_column()
    assert len(ink_lines(pdf, record)) == 1
    assert record["x"] == pytest.approx(left, abs=1.0)
    assert record["w"] > right - left, "the box is the line's own, past the column, as stage one drew it"


def _string_literal(text: str) -> str:
    """Return text as the translator writes it in a Typst string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_a_multi_line_cell_in_a_right_aligned_column_keeps_each_line_on_its_right_edge(
    tmp_path: Path,
) -> None:
    item, amount = TABLE_COLUMNS
    layout = real_layout([])
    advance = layout["defaults"]["line_advance"]["body"]
    table = {
        **TABLE,
        "header_height": 2 * advance,
        "columns": [item, {**amount, "label": "Amount\nin dollars"}],
    }
    source = translate([table], tmp_path).source
    pdf = tmp_path / "page.pdf"
    compile_page(source, pdf)
    (cell,) = [c for c in query(source, "cell-record") if (c["row"], c["col"]) == (0, 1)]
    lines = ink_lines(pdf, cell)
    assert len(lines) == 2
    for _, line_right in lines:
        assert line_right == pytest.approx(cell["x"] + cell["w"], abs=1.0)
    scale = page_scale(layout, ROLL, canvas_dpi=CONFIG.canvas_dpi)
    # The first line takes its pitch; the last is as tall as its face.
    font = load_font(layout["font_sizes"]["body"], family="liberation_mono", bold=True)
    assert isinstance(font, ImageFont.FreeTypeFont)
    ascent, descent = font.getmetrics()
    assert cell["h"] == pytest.approx((advance + ascent + descent) * scale, abs=0.5)


def test_a_barcode_reports_its_drawn_size(tmp_path: Path) -> None:
    block = BODIES["barcode"][0]
    (record,) = boxes([block], tmp_path)
    scale = page_scale(real_layout([]), ROLL, canvas_dpi=CONFIG.canvas_dpi)
    bars = (len(encode_code128(block["content"])) + 2 * block["quiet_zone"]) * block["module_px"] * scale
    advance = real_layout([])["defaults"]["line_advance"]["body"]
    # The bars, the gap, then the caption's one line; the caption is narrower than the bars.
    assert record["w"] == pytest.approx(bars, abs=0.5)
    assert record["h"] == pytest.approx((block["height"] + block["caption_gap"] + advance) * scale, abs=0.5)


def test_a_qrcode_caption_sits_under_its_symbol(tmp_path: Path) -> None:
    block = {**BODIES["qrcode"][0], "caption": "Scan for tides"}
    translation = translate([block], tmp_path)
    assert '"Scan for tides"' in translation.source
    compile_page(translation.source, tmp_path / "page.pdf")
    (record,) = query(translation.source, "block-record")
    modules = len(
        segno.make(block["content"], error=block["error_level"], micro=False, boost_error=False).matrix
    )
    layout = real_layout([])
    scale = page_scale(layout, ROLL, canvas_dpi=CONFIG.canvas_dpi)
    side = (modules + 2 * block["quiet_zone"]) * block["module_px"]
    # The symbol, then the caption's one line directly beneath it.
    assert record["h"] == pytest.approx(
        (side + layout["defaults"]["line_advance"]["body"]) * scale, abs=0.5
    )


def test_a_split_divider_runs_down_the_middle_of_its_gap(tmp_path: Path) -> None:
    short = [[{"type": "text", "content": "Wren"}], [{"type": "text", "content": "Tern"}]]
    split = {"type": "split", "gap": 20, "divider": True, "children": short}
    source = translate([split], tmp_path).source
    pdf = tmp_path / "page.pdf"
    compile_page(source, pdf)
    first, second = query(source, "block-record")
    layout = real_layout([])
    scale = page_scale(layout, ROLL, canvas_dpi=CONFIG.canvas_dpi)
    left, _ = roll_column()
    half = (layout["content_width"] - 20) / 2
    middle = left + (half + 10) * scale
    image = pypdfium2.PdfDocument(str(pdf))[0].render(scale=4).to_pil().convert("L")
    row = round((first["y"] + first["h"] / 2) * 4)
    assert min(image.getpixel((round(middle * 4) + dx, row)) for dx in (-2, -1, 0, 1, 2)) < 128
    assert image.getpixel((round((middle - 4) * 4), row)) > 200, "only the divider is drawn in the gap"


def test_a_sub_line_is_drawn_in_its_own_colour_and_size(tmp_path: Path) -> None:
    item, amount = TABLE_COLUMNS
    sub_line = {"key": "note", "color": "#999999", "offset_y": 24, "height": 20}
    table = {
        **TABLE,
        "params": {"fields": {**TABLE["params"]["fields"], "note": "INVENTED_NOTES"}},
        "columns": [{**item, "sub_line": sub_line}, amount],
    }
    entry = {**ENTRY, "fields": {**ENTRY["fields"], "INVENTED_NOTES": "Ref 4471 | Ref 9032"}}
    translation = translate([table], tmp_path, entry=entry)
    grey = "fill: rgb(153, 153, 153)"
    notes = [line for line in translation.source.splitlines() if "Ref 4471" in line or "Ref 9032" in line]
    assert notes and all(grey in line for line in notes)
    assert grey not in "\n".join(line for line in translation.source.splitlines() if line not in notes)
    compile_page(translation.source, tmp_path / "page.pdf")


def test_a_banner_paints_from_the_page_corner_and_sets_its_text_where_stage_one_draws_it(
    tmp_path: Path,
) -> None:
    banks = REPO_ROOT / "config" / "layouts" / "bank_statements.yml"
    registry = load_layout_registry(banks)
    layout_id = next(
        i for i, layout in registry.items() if any(b["type"] == "banner" for b in layout["body"])
    )
    layout = copy.deepcopy(registry[layout_id])
    banner = {
        "type": "banner",
        "content": "Gullwing Tidal Survey",
        "height": 120,
        "color": "#0061B5",
        "text_y": 30,
    }
    layout.update(
        body=[banner, {"type": "spacer", "height": 140}, TEXT], field_providers=[], distractor_items=[]
    )
    translation = translate_page(
        layout,
        {**ENTRY, "layout": layout_id},
        layout_id=layout_id,
        layout_path=banks,
        page=A4,
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    assert "dsl-banner(" in translation.source
    pdf = tmp_path / "page.pdf"
    compile_page(translation.source, pdf)
    text, below = query(translation.source, "block-record")
    scale = 72 / CONFIG.canvas_dpi
    a4_width = 210.0 * 72 / 25.4
    assert text["y"] == pytest.approx(30 * scale, abs=0.5)
    assert text["x"] == pytest.approx((a4_width - layout["content_width"] * scale) / 2, abs=0.5)
    assert below["y"] == pytest.approx((layout["margin"] + 140) * scale, abs=0.5), (
        "the banner moves no flow"
    )
    image = pypdfium2.PdfDocument(str(pdf))[0].render(scale=1).to_pil().convert("RGB")
    # The bar runs from the page's own corner, full bleed.
    assert image.getpixel((1, 1)) == pytest.approx((0, 97, 181), abs=3)
    assert image.getpixel((image.width - 2, round(119 * scale) - 1)) == pytest.approx((0, 97, 181), abs=3)
