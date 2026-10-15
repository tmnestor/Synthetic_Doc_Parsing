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

import pytest

from generators.layout_dsl.engine import SCOPED_LEAVES
from generators.loader import load_layout_registry
from generators.parsing.omnidocbench import OVERLAY_PREFIX
from generators.typst.config import TYPESET_CONFIG_PATH, PageSpec, layout_page, load_typeset_config
from generators.typst.translate import (
    GATE_LAYOUTS,
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
GATE_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}

# Invented values: none appear in ground_truth/.
ENTRY = {
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
TABLE = {
    "type": "table",
    "rows": "pipe_fields",
    "frame": "bordered",
    "grouping": "none",
    "header": True,
    "params": {"fields": {"item": "INVENTED_ITEMS", "amount": "INVENTED_AMOUNTS"}},
    "columns": [
        {"key": "item", "label": "Item", "align": "left", "x": 0},
        {"key": "amount", "label": "Amount", "align": "right", "x_right": 0},
    ],
}

# One minimal body per gate primitive, each holding at least one block a parser finds.
BODIES: dict[str, list] = {
    "text": [TEXT],
    "pair": [{"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}"}],
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
    assert set(BODIES) == expected


def test_the_translator_handles_exactly_the_gate_primitives() -> None:
    assert TRANSLATED == gate_primitives()


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_each_gate_primitive_translates_to_its_template_call_and_its_records(
    kind: str, tmp_path: Path
) -> None:
    translation = translate(BODIES[kind], tmp_path)
    assert f"dsl-{kind}(" in translation.source
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
        page=PageSpec(width_mm=210.0, height_mm=297.0),
        distractors=False,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    assert re.search(r"#set page\(width: 210(\.0+)?mm, height: 297(\.0+)?mm,", sheet.source)


def test_pixels_convert_at_the_page_width_over_the_layout_width() -> None:
    receipts = load_layout_registry(REPO_ROOT / "config" / "layouts" / "receipts.yml")
    invoices = load_layout_registry(REPO_ROOT / "config" / "layouts" / "invoices.yml")
    roll = receipts[LAYOUT_ID]
    sheet = invoices["tax_invoice_standard"]
    assert page_scale(roll, ROLL) == pytest.approx(80.0 / roll["width"] * 72 / 25.4)
    a4 = PageSpec(width_mm=210.0, height_mm=297.0)
    assert page_scale(sheet, a4) == pytest.approx(210.0 / sheet["page_dimensions"]["width"] * 72 / 25.4)


def test_text_is_set_in_the_font_the_layout_names(tmp_path: Path) -> None:
    source = translate([TEXT], tmp_path).source
    # The receipt layout's family is liberation_mono, whose face Typst knows by its own name.
    assert 'font: "Liberation Mono"' in source


def test_a_text_is_written_as_the_value_stage_one_draws(tmp_path: Path) -> None:
    source = translate(
        [TEXT, {"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}"}], tmp_path
    ).source
    assert '"Marrowbank Tide Tables"' in source
    assert 'label: "Yield: "' in source
    assert '"41.07"' in source


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
        ([{"type": "block", "lines": ["Hollin"], "when": "ABSENT_FIELD"}], "body[0]", "block"),
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
    grouped = {**TABLE, "grouping": "inline"}
    with pytest.raises(TranslateError) as exc_info:
        translate([TEXT, grouped], tmp_path)
    message = str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
    assert f"{RECEIPTS_PATH.resolve()} -> {LAYOUT_ID}.body[1].grouping" in message
    assert "'inline'" in message


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
        assert table["x"] <= cell["x"] and cell["x"] + cell["w"] <= table["x"] + table["w"] + 1e-6


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_each_gate_primitive_compiles_and_reports_its_records(kind: str, tmp_path: Path) -> None:
    translation = translate(BODIES[kind], tmp_path)
    compile_page(translation.source, tmp_path / "page.pdf")
    assert [b["id"] for b in query(translation.source, "block-record")] == [
        b.id for b in translation.blocks
    ]


@pytest.mark.parametrize(("layout_file", "layout_id"), GATE_LAYOUTS)
def test_a_gate_case_compiles_without_warnings(layout_file: str, layout_id: str, tmp_path: Path) -> None:
    layout, entry = gate_case(Path(layout_file).stem, layout_id)
    layout_path = REPO_ROOT / layout_file
    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        page=layout_page(layout, layout_path=str(layout_path), layout_id=layout_id),
        distractors=True,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    compile_page(translation.source, tmp_path / "page.pdf")
    assert len(query(translation.source, "block-record")) == len(translation.blocks)
