"""The walker and the overlay open one block scope per block a parser can find, and name where it sits.

Each scratch layout is a real receipt layout with its body swapped (see `dsl_helpers`), drawn through
`render_body` onto a fresh page with a recorder, so every claim is read back from what the recorder holds.
"""

import copy
from contextlib import nullcontext

import pytest

from generators.exporters.geometry import BlockRecord, BoxRecorder
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import SCOPED_CONTAINERS, SCOPED_LEAVES, render_body
from generators.overlay import registry
from generators.parsing.config import BLOCK_PRIMITIVES
from tests.layout_dsl.dsl_helpers import LAYOUT_ID, MARGIN, PAGE_WIDTH, RECEIPTS_PATH, canvas, real_layout
from tests.layout_dsl.test_primitives_table import BANK_ENTRY, table, table_layout
from tests.overlay.overlay_helpers import TEST_KEY, TEST_TYPE, draw_test_box, overlaid
from tests.renderer_helpers import entry_per_layout, layouts_for, render_entry, renderer_for

# Invented values: none appear in ground_truth/.
ENTRY = {
    "case_id": "CASE999",
    "layout": LAYOUT_ID,
    "fields": {"SUPPLIER_NAME": "Quokka Lamp Co", "TOTAL_AMOUNT": "62.81"},
}

LEAVES: dict[str, dict] = {
    "text": {"type": "text", "content": "Yarrow and Sons"},
    "pair": {"type": "pair", "label": "Yield", "value": "{TOTAL_AMOUNT}"},
    "block": {"type": "block", "heading": "Wattle", "lines": ["Banksia line", "Grevillea line"]},
    "banner": {
        "type": "banner",
        "content": "Yarrow",
        "height": 100,
        "color": "#000000",
        "text_y": 30,
        "text_color": "white",
    },
    "logo": {
        "type": "logo",
        "content": "{SUPPLIER_NAME}",
        "style": "circle",
        "size": 80,
        "min_text_size": 12,
        "inset": 0,
        "text_color": "white",
    },
    "barcode": {
        "type": "barcode",
        "content": "TX-2031",
        "symbology": "code128",
        "height": 60,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": True,
        "caption_gap": 4,
    },
    "qrcode": {
        "type": "qrcode",
        "content": "https://survey.example.test",
        "module_px": 4,
        "quiet_zone": 4,
        "error_level": "m",
    },
}

TWO_TEXTS = [
    {"type": "text", "content": "Wattle line"},
    {"type": "text", "content": "Banksia line"},
]


def draw(body: list, *, layout: dict | None = None, entry: dict | None = None) -> BoxRecorder:
    """Render `body` on a scratch receipt layout (or `layout`) and return the page's recorder."""
    layout = layout if layout is not None else real_layout(body)
    layout["body"] = body
    layout["page_dimensions"] = {"width": PAGE_WIDTH, "height": 600}
    recorder = BoxRecorder(PAGE_WIDTH, 600)
    _, surface = canvas()
    render_body(
        layout,
        entry if entry is not None else ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=surface,
        region=Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN),
        y=0,
        distractors=False,
        recorder=recorder,
    )
    return recorder


def owners(recorder: BoxRecorder) -> list[int | None]:
    """The block id each ink record carries, in draw order."""
    return [record.block for record in recorder.ink_records()]


def test_the_scoped_kinds_are_the_parsing_block_primitives() -> None:
    """The walker scopes exactly the leaves and containers the parsing configuration maps to a category."""
    assert SCOPED_LEAVES | SCOPED_CONTAINERS == BLOCK_PRIMITIVES
    assert not SCOPED_LEAVES & SCOPED_CONTAINERS


@pytest.mark.parametrize("kind", sorted(LEAVES))
def test_each_leaf_yields_one_block_of_its_kind(kind: str) -> None:
    """A leaf records one block of its own kind at its body path, and owns every line it draws."""
    recorder = draw([copy.deepcopy(LEAVES[kind])])
    assert recorder.block_records() == [BlockRecord(0, kind, "body[0]", None, None, 0, None)]
    assert recorder.ink_records(), f"{kind} drew no ink"
    assert set(owners(recorder)) == {0}


def test_a_table_yields_one_block_carrying_its_column_count() -> None:
    """A table is a leaf: one block, its column count declared, every line of it owned."""
    block = table()
    recorder = draw([block], layout=table_layout(), entry=BANK_ENTRY)
    assert recorder.block_records() == [
        BlockRecord(0, "table", "body[0]", None, None, 0, len(block["columns"]))
    ]
    assert set(owners(recorder)) == {0}


def test_parse_keys_are_carried_onto_the_block() -> None:
    """`parse_category` and `parse_order` come from the block dict."""
    recorder = draw([{**LEAVES["text"], "parse_category": "title", "parse_order": -1}])
    assert recorder.block_records() == [BlockRecord(0, "text", "body[0]", None, "title", -1, None)]


@pytest.mark.parametrize("container", ["stack", "panel"])
def test_a_container_without_parse_block_yields_one_block_per_child(container: str) -> None:
    """A plain container records no block; each child records its own, at its nested path."""
    recorder = draw([{"type": "text", "content": "Above"}, {"type": container, "children": TWO_TEXTS}])
    assert [(b.kind, b.path) for b in recorder.block_records()] == [
        ("text", "body[0]"),
        ("text", "body[1].children[0]"),
        ("text", "body[1].children[1]"),
    ]
    assert owners(recorder) == [0, 1, 2]


@pytest.mark.parametrize("container", ["stack", "panel"])
def test_a_container_with_parse_block_yields_one_block_for_all_its_children(container: str) -> None:
    """`parse_block: true` opens the scope at the container, so its children's ink joins one block."""
    recorder = draw([{"type": container, "parse_block": True, "children": TWO_TEXTS}])
    assert recorder.block_records() == [BlockRecord(0, container, "body[0]", None, None, 0, None)]
    assert owners(recorder) == [0, 0]


def test_a_split_names_each_column_in_its_children_paths() -> None:
    """A split's children are a list per column, so a child's path names its column as well as its place."""
    recorder = draw([{"type": "split", "children": [TWO_TEXTS, [{"type": "text", "content": "Right"}]]}])
    assert [b.path for b in recorder.block_records()] == [
        "body[0].children[0][0]",
        "body[0].children[0][1]",
        "body[0].children[1][0]",
    ]


def test_a_split_with_parse_block_yields_one_block() -> None:
    """`parse_block: true` on a split gathers both columns into one block."""
    recorder = draw(
        [
            {
                "type": "split",
                "parse_block": True,
                "children": [TWO_TEXTS, [{"type": "text", "content": "R"}]],
            }
        ]
    )
    assert recorder.block_records() == [BlockRecord(0, "split", "body[0]", None, None, 0, None)]
    assert owners(recorder) == [0, 0, 0]


def test_a_foot_anchored_block_records_its_anchor() -> None:
    """A foot-anchored container with `parse_block` records `anchor: foot` on its one block."""
    recorder = draw(
        [
            {"type": "text", "content": "Body"},
            {"type": "stack", "anchor": "foot", "parse_block": True, "children": TWO_TEXTS},
        ]
    )
    assert recorder.block_records() == [
        BlockRecord(0, "text", "body[0]", None, None, 0, None),
        BlockRecord(1, "stack", "body[1]", "foot", None, 0, None),
    ]
    assert owners(recorder) == [0, 1, 1]


def test_a_gated_off_block_records_nothing() -> None:
    """A block whose `when:` key is absent is never drawn, so it opens no scope."""
    recorder = draw([{**LEAVES["text"], "when": "PAYER_NAME"}, {"type": "text", "content": "Shown"}])
    assert recorder.block_records() == [BlockRecord(0, "text", "body[1]", None, None, 0, None)]


@pytest.mark.parametrize("ink", ["text", "mark"])
def test_overlay_ink_lands_in_an_overlay_block(monkeypatch: pytest.MonkeyPatch, ink: str) -> None:
    """Each drawn overlay site opens one `overlay:<type>` block, after every body block, at its overlay path."""
    monkeypatch.setitem(registry.OVERLAY_DRAWERS, TEST_TYPE, draw_test_box)
    entry, layout = overlaid(
        "invoice", "CASE001", [{"type": TEST_TYPE, "when": TEST_KEY, "box": [40, 40, 60, 60], "ink": ink}]
    )
    geometry: dict = {}
    renderer_for("invoice")(entry, layout, distractors=True, geometry_out=geometry)
    *body, last = geometry["blocks"]
    assert last == BlockRecord(
        len(body), f"overlay:{TEST_TYPE}", f"{entry['layout']}.overlay[0]", None, None, 0, None
    )
    assert all(not b.kind.startswith("overlay:") for b in body)
    overlay_ink = [record for record in geometry["ink"] if record.block == last.id]
    assert len(overlay_ink) == 1
    assert overlay_ink[0].mark is (ink == "mark")
    assert overlay_ink[0] == geometry["ink"][-1]


# --- every real layout -----------------------------------------------------------------------------------

DOC_TYPES = ("receipt", "invoice", "bank_statement")
ALL_LAYOUTS = [(doc_type, layout_id) for doc_type in DOC_TYPES for layout_id in layouts_for(doc_type)]


def test_every_layout_is_covered() -> None:
    """The orphan test below runs over every registered layout; a registry that shrinks fails here."""
    assert len(ALL_LAYOUTS) == 22


@pytest.mark.parametrize("distractors", [False, True], ids=["plain", "distractor"])
@pytest.mark.parametrize(("doc_type", "layout_id"), ALL_LAYOUTS)
def test_no_ink_is_drawn_outside_a_block(doc_type: str, layout_id: str, distractors: bool) -> None:
    """Review Focus 1: on the first case of every layout, every line and mark belongs to a recorded block."""
    entry = entry_per_layout(doc_type)[layout_id]
    geometry: dict = {}
    render_entry(doc_type, entry, distractors=distractors, geometry_out=geometry)
    orphans = [record for record in geometry["ink"] if record.block is None]
    assert not orphans, f"{layout_id}: ink outside any block: {[(r.text, r.box) for r in orphans]}"
    ids = [block.id for block in geometry["blocks"]]
    assert ids == list(range(len(ids)))
    assert {record.block for record in geometry["ink"]} <= set(ids)


def _without_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every scope and cell marker a no-op, so a render is drawn exactly as before they existed."""
    monkeypatch.setattr(BoxRecorder, "block_scope", lambda self, kind, **kwargs: nullcontext())
    monkeypatch.setattr(BoxRecorder, "set_table_columns", lambda self, columns: None)
    monkeypatch.setattr(BoxRecorder, "set_cell", lambda self, cell: None)


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_recording_blocks_moves_no_pixel_and_no_box(monkeypatch: pytest.MonkeyPatch, doc_type: str) -> None:
    """Review Focus 2: the page, its scored boxes and its ink boxes are identical with and without scopes."""
    entry = next(iter(entry_per_layout(doc_type).values()))

    def render() -> tuple[bytes, dict, list]:
        geometry: dict = {}
        image = render_entry(doc_type, entry, distractors=True, geometry_out=geometry)
        boxes = [(r.role, r.box) for r in geometry["ink"]]
        return image.tobytes(), geometry["boxes"], boxes

    scoped = render()
    with monkeypatch.context() as patch:
        _without_scopes(patch)
        unscoped = render()
    assert scoped == unscoped
