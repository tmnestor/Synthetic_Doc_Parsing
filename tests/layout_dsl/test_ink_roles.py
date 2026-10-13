"""Every drawn text line and code mark is recorded as ink with its role.

Roles: `scored` for a block or cell carrying `field:`, `distractor` for a block that is gated on a
`DX_` key (directly or through an enclosing container) or whose text references one, else `text`.
Code marks are `distractor` boxes flagged as marks, covering the symbol and its caption.
"""

import copy

import pytest
from PIL import Image, ImageChops, ImageDraw

from generators.exporters.geometry import BoxRecorder, InkRecord
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.layout_dsl.primitives_codes import initials
from generators.layout_dsl.primitives_text import font_for, resolve_role
from tests.distractor_helpers import walk_blocks
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, MARGIN, PAGE_WIDTH, RECEIPTS_PATH, real_layout
from tests.renderer_helpers import corpus_entries, layouts_for, render_entry

# Invented values, absent from ground_truth/.
FIELDS = {
    "INVOICE_DATE": "01/02/2031",
    "TOTAL_AMOUNT": "40.00",
    "DX_STORE_NUMBER": "Store 7041",
    "DX_SURVEY_CODE": "90417733",
    "DX_SURVEY_URL": "https://survey.example.test/r/2031-7Q4K",
    "DX_TRANSACTION_ID": "4471902833016620",
    "DX_EAN": "931234500001",
    "SHOP": "Quokka Lane Provisions",
}
ENTRY = {"case_id": "CASE999", "layout": LAYOUT_ID, "fields": FIELDS}


def draw_body(
    body: list, *, page_width: int = PAGE_WIDTH, extra: dict | None = None
) -> tuple[list[InkRecord], BoxRecorder, Image.Image]:
    """Render `body` on the real receipt layout (plus `extra` keys); return its ink, recorder and page."""
    layout = {**real_layout(body), **(extra or {})}
    image = Image.new("RGB", (page_width, 900), "white")
    recorder = BoxRecorder(page_width, 900)
    render_body(
        layout,
        ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=ImageDraw.Draw(image),
        region=Region(x=MARGIN, width=page_width - 2 * MARGIN),
        y=0,
        distractors=False,
        recorder=recorder,
    )
    return recorder.ink_records(), recorder, image


def roles(records: list[InkRecord]) -> list[str]:
    return [record.role for record in records]


def ink_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    """The bounding box of every non-white pixel."""
    box = ImageChops.invert(image.convert("L")).getbbox()
    assert box is not None, "the page is blank"
    return box


def assert_text_close(box: tuple[int, ...], pixels: tuple[int, ...]) -> None:
    """A glyph box from the font's metrics lies within one antialiased pixel of the painted pixels."""
    assert all(abs(a - b) <= 1 for a, b in zip(box, pixels, strict=True)), (box, pixels)


def text_box(
    text: str, x: int, y: int, *, bold: bool = False, role: str = "body"
) -> tuple[int, int, int, int]:
    layout = real_layout([])
    size = resolve_role(layout, role)
    font = font_for(layout, {}, size, bold=bold, layout_id=LAYOUT_ID, layout_path=str(RECEIPTS_PATH))
    left, top, right, bottom = font.getbbox(text)
    return (x + int(left), y + int(top), x + int(right), y + int(bottom))


# --- text ---------------------------------------------------------------------


def test_a_scored_text_line_is_scored_ink_at_its_exact_ink_box() -> None:
    records, _, image = draw_body([{"type": "text", "content": "{INVOICE_DATE}", "field": "INVOICE_DATE"}])
    assert records == [
        InkRecord(
            "scored",
            text_box("01/02/2031", MARGIN, 0),
            False,
            "01/02/2031",
            resolve_role(real_layout([]), "body"),
            False,
            block=0,
        )
    ]
    assert_text_close(records[0].box, ink_bbox(image))


def test_a_plain_text_line_is_text_ink() -> None:
    records, _, _ = draw_body([{"type": "text", "content": "THANK YOU"}])
    assert roles(records) == ["text"]


def test_text_referencing_a_dx_key_is_distractor_ink() -> None:
    records, _, _ = draw_body([{"type": "text", "content": "{DX_STORE_NUMBER}"}])
    assert roles(records) == ["distractor"]


def test_text_gated_on_a_dx_key_is_distractor_ink() -> None:
    """Ruling 8: a `when:` naming a DX key alone makes the block distractor ink."""
    records, _, _ = draw_body([{"type": "text", "content": "Scan for rewards", "when": "DX_STORE_NUMBER"}])
    assert roles(records) == ["distractor"]


def test_gating_on_a_scored_key_does_not_make_distractor_ink() -> None:
    records, _, _ = draw_body([{"type": "text", "content": "PAID", "when": "TOTAL_AMOUNT"}])
    assert roles(records) == ["text"]


def test_children_of_a_dx_gated_container_are_distractor_ink() -> None:
    body = [
        {
            "type": "split",
            "when": "DX_STORE_NUMBER",
            "children": [[{"type": "text", "content": "Open 7 days"}], [{"type": "text", "content": "Hi"}]],
        },
        {"type": "text", "content": "after"},
    ]
    records, _, _ = draw_body(body)
    assert roles(records) == ["distractor", "distractor", "text"]


def test_a_scored_box_never_counts_as_distractor_even_when_dx_gated() -> None:
    body = [
        {"type": "text", "content": "{TOTAL_AMOUNT}", "field": "TOTAL_AMOUNT", "when": "DX_STORE_NUMBER"}
    ]
    records, _, _ = draw_body(body)
    assert roles(records) == ["scored"]


def test_a_wrapped_budgeted_line_records_one_box_per_drawn_line() -> None:
    long = "Return within thirty days with proof of purchase for a full refund or exchange"
    layout = real_layout([])
    budget = dict(layout["field_budgets"]["SUPPLIER_NAME"], fit="wrap", max_lines=4, min_font=8)
    layout["field_budgets"]["WRAPPED"] = budget
    layout["body"] = [{"type": "text", "content": long, "budget": "WRAPPED"}]
    image = Image.new("RGB", (PAGE_WIDTH, 900), "white")
    recorder = BoxRecorder(PAGE_WIDTH, 900)
    render_body(
        layout,
        ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=ImageDraw.Draw(image),
        region=Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN),
        y=0,
        distractors=False,
        recorder=recorder,
    )
    records = recorder.ink_records()
    assert len(records) >= 2
    assert roles(records) == ["text"] * len(records)
    tops = [record.box[1] for record in records]
    assert tops == sorted(tops) and len(set(tops)) == len(tops)


# --- pair, block, banner --------------------------------------------------------


def test_a_scored_pair_records_its_label_as_text_and_its_value_as_scored() -> None:
    body = [{"type": "pair", "label": "Date", "value": "{INVOICE_DATE}", "field": "INVOICE_DATE"}]
    records, recorder, _ = draw_body(body)
    assert roles(records) == ["text", "scored"]
    left, top, right, bottom = recorder.as_dict()["INVOICE_DATE"]
    assert records[1].box[0] == pytest.approx(left * PAGE_WIDTH, abs=2)


def test_a_right_aligned_scored_pair_records_label_and_value() -> None:
    body = [
        {
            "type": "pair",
            "label": "TOTAL",
            "value": "{TOTAL_AMOUNT}",
            "value_align": "right",
            "separator": "",
            "field": "TOTAL_AMOUNT",
        }
    ]
    records, _, image = draw_body(body)
    assert roles(records) == ["text", "scored"]
    assert records[1].box[2] <= PAGE_WIDTH - MARGIN + 1


def test_a_pair_whose_value_is_a_dx_key_is_all_distractor_ink() -> None:
    records, _, _ = draw_body([{"type": "pair", "label": "Store", "value": "{DX_STORE_NUMBER}"}])
    assert roles(records) == ["distractor", "distractor"]


def test_a_block_records_its_heading_and_every_line() -> None:
    body = [{"type": "block", "heading": "Returns", "lines": ["Keep this receipt", "{DX_STORE_NUMBER}"]}]
    records, _, _ = draw_body(body)
    assert roles(records) == ["distractor"] * 3


def test_a_banner_records_its_text() -> None:
    body = [{"type": "banner", "height": 40, "color": "navy", "content": "WELCOME"}]
    records, _, _ = draw_body(body, extra={"page_dimensions": {"width": PAGE_WIDTH, "height": 900}})
    assert roles(records) == ["text"]


def test_a_character_rule_is_not_text_ink() -> None:
    records, _, _ = draw_body([{"type": "rule", "fill_char": "-"}])
    assert records == []


# --- code marks -----------------------------------------------------------------


def _mark(records: list[InkRecord]) -> InkRecord:
    marks = [record for record in records if record.mark]
    assert len(marks) == 1
    return marks[0]


def test_a_barcode_mark_is_its_exact_drawn_rectangle_with_caption() -> None:
    block = {
        "type": "barcode",
        "content": "{DX_TRANSACTION_ID}",
        "symbology": "code128",
        "height": 60,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": True,
        "caption_gap": 4,
        "when": "DX_TRANSACTION_ID",
    }
    records, _, image = draw_body([block])
    mark = _mark(records)
    assert mark.role == "distractor"
    assert_text_close(mark.box, ink_bbox(image))
    captions = [record for record in records if not record.mark]
    assert roles(captions) == ["distractor"]


def test_a_barcode_without_caption_is_its_bars_alone() -> None:
    block = {
        "type": "barcode",
        "content": "{DX_EAN}",
        "symbology": "ean13",
        "height": 50,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": False,
        "caption_gap": 4,
    }
    records, _, image = draw_body([block])
    assert records == [InkRecord("distractor", ink_bbox(image), True, FIELDS["DX_EAN"], 0, False, block=0)]


def test_a_qrcode_mark_is_its_exact_drawn_rectangle_with_caption() -> None:
    block = {
        "type": "qrcode",
        "content": "{DX_SURVEY_URL}",
        "caption": "Code {DX_SURVEY_CODE}",
        "module_px": 3,
        "quiet_zone": 4,
        "error_level": "m",
    }
    records, _, image = draw_body([block])
    assert_text_close(_mark(records).box, ink_bbox(image))
    assert roles(records) == ["distractor", "distractor"]


def test_a_qrcode_without_caption_is_its_modules_exactly() -> None:
    block = {
        "type": "qrcode",
        "content": "{DX_SURVEY_URL}",
        "module_px": 3,
        "quiet_zone": 4,
        "error_level": "m",
    }
    records, _, image = draw_body([block])
    assert records == [
        InkRecord("distractor", ink_bbox(image), True, FIELDS["DX_SURVEY_URL"], 0, False, block=0)
    ]


def test_a_logo_mark_is_its_shape_box() -> None:
    block = {
        "type": "logo",
        "content": "{SHOP}",
        "style": "square",
        "size": 64,
        "text_color": "white",
        "min_text_size": 10,
        "inset": 0,
        "when": "DX_STORE_NUMBER",
    }
    records, _, image = draw_body([block])
    assert records == [
        InkRecord("distractor", ink_bbox(image), True, initials(FIELDS["SHOP"]), 0, False, block=0)
    ]


# --- tables and whole renders ---------------------------------------------------

# Invented rows, absent from ground_truth/.
_ROWS = [
    {"date": "03/02/2031", "item": "Flat white", "note": "Ref 0417"},
    {"date": "04/02/2031", "item": "Banana bread", "note": "Ref 0418"},
]


def _dx_table(when: str | None) -> dict:
    """A headed table with a dedicated date row and a sub-line per row, optionally gated with `when`."""
    return {
        "type": "table",
        "rows": "ink_role_test_rows",
        "frame": "plain",
        "grouping": "dedicated_row",
        "header": True,
        "row_inset_y": 0,
        "columns": [
            {
                "key": "item",
                "label": "Item",
                "align": "left",
                "x": 0,
                "sub_line": {"key": "note", "offset_y": 16, "height": 16},
            },
        ],
        **({"when": when} if when else {}),
    }


@pytest.fixture
def table_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve `_ROWS` for the test table's `rows:` name, without registering a provider for good."""
    from generators.layout_dsl import primitives_table

    real = primitives_table.get_provider
    monkeypatch.setattr(
        primitives_table,
        "get_provider",
        lambda name: (
            (lambda entry, params: [dict(r) for r in _ROWS]) if name == "ink_role_test_rows" else real(name)
        ),
    )


def test_every_line_of_a_dx_gated_table_is_distractor_ink(table_rows: None) -> None:
    """Header labels, each dedicated date row, each cell and each sub-line inherit the table's DX gate."""
    records, _, _ = draw_body([_dx_table("DX_STORE_NUMBER")])
    # One header label, then per row: its date row, its cell and its sub-line.
    assert roles(records) == ["distractor"] * 7


def test_the_same_table_ungated_is_text_ink(table_rows: None) -> None:
    records, _, _ = draw_body([_dx_table(None)])
    assert roles(records) == ["text"] * 7


def test_a_child_of_a_dx_gated_panel_is_distractor_ink() -> None:
    body = [
        {
            "type": "panel",
            "when": "DX_STORE_NUMBER",
            "children": [{"type": "text", "content": "Open 7 days"}],
        },
        {"type": "text", "content": "after"},
    ]
    records, _, _ = draw_body(body)
    assert roles(records) == ["distractor", "text"]


def _receipt_with_items() -> dict:
    return next(
        e for e in corpus_entries("receipt").values() if len(e["fields"]["LINE_ITEM_DESCRIPTIONS"]) > 0
    )


def test_a_table_records_scored_cells_even_when_capture_is_off() -> None:
    entry = _receipt_with_items()
    layout = copy.deepcopy(layouts_for("receipt")[entry["layout"]])
    on: dict = {}
    render_entry_with(layout, entry, on)
    # The receipt body is three stacks; the table sits inside the first.
    tables = [block for block in walk_blocks(layout["body"]) if block.get("type") == "table"]
    assert tables
    for block in tables:
        block["capture"] = False
    off: dict = {}
    render_entry_with(layout, entry, off)
    assert not any(key.startswith("LINE_ITEM_DESCRIPTIONS") for key in off["boxes"])
    assert off["ink"] == on["ink"]


def render_entry_with(layout: dict, entry: dict, geometry_out: dict) -> None:
    from generators.receipt import render_receipt

    render_receipt(entry, layout, distractors=False, geometry_out=geometry_out)


@pytest.mark.parametrize("doc_type", ["receipt", "invoice", "bank_statement"])
def test_a_plain_render_records_no_distractor_ink_and_inks_every_scored_box(doc_type: str) -> None:
    for case_id, entry in corpus_entries(doc_type).items():
        geometry: dict = {}
        render_entry(doc_type, entry, distractors=False, geometry_out=geometry)
        records: list[InkRecord] = geometry["ink"]
        assert records, case_id
        assert "distractor" not in roles(records), case_id
        scored = [record.box for record in records if record.role == "scored"]
        width, height = geometry["width"], geometry["height"]
        for field, (left, top, right, bottom) in geometry["boxes"].items():
            box = (left * width, top * height, right * width, bottom * height)
            assert any(_overlaps(box, ink) for ink in scored), f"{case_id} {field} has no scored ink"


def _overlaps(a: tuple[float, ...], b: tuple[int, ...]) -> bool:
    return a[0] < b[2] + 1 and b[0] < a[2] + 1 and a[1] < b[3] + 1 and b[1] < a[3] + 1


@pytest.mark.parametrize("doc_type", ["receipt", "invoice", "bank_statement"])
def test_recording_ink_changes_no_pixel(doc_type: str) -> None:
    entry = next(iter(corpus_entries(doc_type).values()))
    bare = render_entry(doc_type, entry, distractors=False)
    recorded = render_entry(doc_type, entry, distractors=False, geometry_out={})
    assert ImageChops.difference(bare, recorded).getbbox() is None
