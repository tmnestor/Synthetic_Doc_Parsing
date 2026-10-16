"""The overlay layer: when it runs, what it records, and the render-time refusals it raises."""

import copy
import hashlib
import random

import pytest
from PIL import Image, ImageDraw

import generators.bank_statement
import generators.invoice
import generators.receipt
from generators.distractors.config import DensityBounds, load_distractor_config
from generators.distractors.density import DensityCheck, measure_render
from generators.distractors.paths import DISTRACTORS_PATH
from generators.exporters.geometry import InkRecord
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.overlay.engine import OVERLAY_DRAWERS, OverlayError, apply_overlay, overlay_seed
from generators.overlay.registry import OverlayDrawing, Tint
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import selected
from tests.overlay.overlay_helpers import TEST_KEY, TEST_TYPE, overlaid, recorder_with
from tests.renderer_helpers import corpus_entries, layouts_for, renderer_for

# A corner every invoice and bank layout leaves blank: inside the page inset, above and left of the margin.
CORNER = [40, 40, 60, 60]
INVOICE = "CASE001"
BANK = "CASE001"


def _render(doc_type: str, entry: dict, layout: dict, **kwargs: object) -> Image.Image:
    return renderer_for(doc_type)(entry, layout, distractors=True, **kwargs)


def _refused(doc_type: str, case_id: str, blocks: list, *tokens: str) -> OverlayError:
    entry, layout = overlaid(doc_type, case_id, blocks)
    with pytest.raises(OverlayError) as err:
        _render(doc_type, entry, layout)
    assert_diagnostic_error(err.value)
    for token in (case_id, entry["layout"], f"{entry['layout']}.overlay[0]", *tokens):
        assert token in str(err.value), f"{token!r} missing from: {err.value}"
    return err.value


# --- when the overlay runs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc_type", "module", "name"),
    [
        ("invoice", generators.invoice, "apply_overlay"),
        ("bank_statement", generators.bank_statement, "apply_overlay"),
        ("receipt", generators.receipt, "apply_overlay"),
    ],
)
def test_plain_renders_never_call_the_overlay(
    monkeypatch: pytest.MonkeyPatch, doc_type: str, module: object, name: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(module, name, lambda *args, **kwargs: calls.append(kwargs["layout_id"]))
    for entry in list(corpus_entries(doc_type).values())[:5]:
        layout = layouts_for(doc_type)[entry["layout"]]
        renderer_for(doc_type)(entry, layout, distractors=False)
        renderer_for(doc_type)(entry, layout, distractors=False, geometry_out={})
    assert calls == []
    # The patched name is the one the renderer calls: a distractor render reaches it.
    entry = next(iter(corpus_entries(doc_type).values()))
    _render(doc_type, entry, layouts_for(doc_type)[entry["layout"]])
    assert calls == [entry["layout"]]


def test_a_receipt_layout_with_an_overlay_is_refused_at_render() -> None:
    entry = next(iter(corpus_entries("receipt").values()))
    layout = copy.deepcopy(layouts_for("receipt")[entry["layout"]])
    layout["overlay"] = [{"type": TEST_TYPE, "when": "DX_STORE_NUMBER", "box": CORNER}]
    with pytest.raises(OverlayError) as err:
        _render("receipt", entry, layout)
    assert_diagnostic_error(err.value)
    assert f"{entry['layout']}.overlay" in str(err.value) and "receipts.yml" in str(err.value)
    assert "overlay.protect.receipt" in str(err.value) and "no_overlay" in str(err.value)


@pytest.mark.parametrize("doc_type", ["invoice", "bank_statement"])
def test_a_layout_without_an_overlay_list_is_refused_at_render(doc_type: str) -> None:
    entry = next(iter(corpus_entries(doc_type).values()))
    layout = {k: v for k, v in layouts_for(doc_type)[entry["layout"]].items() if k != "overlay"}
    with pytest.raises(OverlayError) as err:
        _render(doc_type, entry, layout)
    assert_diagnostic_error(err.value)
    assert "add overlay: [] to the layout" in str(err.value)


# --- entry_out --------------------------------------------------------------------------------------------


def _body(entry: dict, layout: dict, entry_out: dict | None = None) -> Image.Image:
    image = Image.new(
        "RGB", (int(layout["page_dimensions"]["width"]), int(layout["page_dimensions"]["height"]))
    )
    render_body(
        layout,
        entry,
        layout_id=entry["layout"],
        layout_path="config/layouts/invoices.yml",
        draw=ImageDraw.Draw(image),
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        distractors=True,
        entry_out=entry_out,
    )
    return image


def test_entry_out_carries_the_selected_dx_values() -> None:
    entry = corpus_entries("invoice")[INVOICE]
    layout = layouts_for("invoice")[entry["layout"]]
    out: dict = {}
    _body(entry, layout, entry_out=out)
    drawn = {key: value for key, value in out["fields"].items() if key.startswith("DX_")}
    assert drawn and drawn == selected(entry, layout)
    assert {k: v for k, v in out["fields"].items() if k in entry["fields"]} == entry["fields"]
    assert out["case_id"] == INVOICE


def test_omitting_entry_out_renders_identically() -> None:
    entry = corpus_entries("invoice")[INVOICE]
    layout = layouts_for("invoice")[entry["layout"]]
    assert _body(entry, layout).tobytes() == _body(entry, layout, entry_out={}).tobytes()


# --- what the overlay records ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["invoice", "bank_statement"])
def test_overlay_text_ink_reaches_geometry_and_ink_out(box_drawer: str, doc_type: str) -> None:
    case = INVOICE if doc_type == "invoice" else BANK
    entry, layout = overlaid(doc_type, case, [{"type": box_drawer, "when": TEST_KEY, "box": CORNER}])
    geometry: dict = {}
    ink: list[InkRecord] = []
    _render(doc_type, entry, layout, geometry_out=geometry, ink_out=ink)
    plain_overlay: dict = {}
    _render(doc_type, entry, {**layout, "overlay": []}, geometry_out=plain_overlay)
    # The overlay site is one block, after every body block.
    added = InkRecord(
        "distractor", (40, 40, 60, 60), False, "note", 30, False, block=len(plain_overlay["blocks"])
    )
    assert geometry["ink"] == [*plain_overlay["ink"], added]
    assert ink == geometry["ink"]
    # Ink stays internal: the exported boxes are the scored ones, unchanged by the overlay.
    assert geometry["boxes"] == plain_overlay["boxes"]


def test_a_stroke_records_a_mark_and_a_tint_records_nothing(box_drawer: str) -> None:
    mark_entry, mark_layout = overlaid(
        "invoice", INVOICE, [{"type": box_drawer, "when": TEST_KEY, "box": CORNER, "ink": "mark"}]
    )
    tint_entry, tint_layout = overlaid(
        "invoice",
        INVOICE,
        [{"type": box_drawer, "when": TEST_KEY, "box": CORNER, "mode": "tint", "colour": [255, 240, 120]}],
    )
    base: dict = {}
    _render("invoice", mark_entry, {**mark_layout, "overlay": []}, geometry_out=base)
    marked: dict = {}
    _render("invoice", mark_entry, mark_layout, geometry_out=marked)
    tinted: dict = {}
    tinted_image = _render("invoice", tint_entry, tint_layout, geometry_out=tinted)
    stroke = InkRecord("distractor", (40, 40, 60, 60), True, "", 0, False, block=len(base["blocks"]))
    assert marked["ink"] == [*base["ink"], stroke]
    assert tinted["ink"] == base["ink"]
    assert tinted_image.getpixel((50, 50)) == (255, 240, 120)


def test_measure_render_includes_the_overlay_text_ink(box_drawer: str) -> None:
    entry, layout = overlaid("invoice", INVOICE, [{"type": box_drawer, "when": TEST_KEY, "box": CORNER}])
    renderer = renderer_for("invoice")
    with_overlay = measure_render(renderer, entry, layout, True)
    without = measure_render(renderer, entry, {**layout, "overlay": []}, True)
    assert with_overlay.distractor_area == without.distractor_area + 20 * 20
    assert with_overlay.scored_area == without.scored_area
    assert with_overlay.distractor_share > without.distractor_share


def test_the_density_check_measures_the_composited_page(box_drawer: str) -> None:
    big = [100, 2600, 1700, 3300]  # a large text block low on the page, clear of every scored box
    entry, layout = overlaid("invoice", INVOICE, [{"type": box_drawer, "when": TEST_KEY, "box": big}])
    renderer = renderer_for("invoice")
    composited = measure_render(renderer, entry, layout, True)
    bare = measure_render(renderer, entry, {**layout, "overlay": []}, True)
    assert composited.scored_ink_ratio < bare.scored_ink_ratio
    # A ceiling between the two: the page without its overlay would pass; the composited page is the one
    # measured, and the floor set above its share is refused, naming the composited value.
    bounds = DensityBounds(
        scored_ink_ratio_max=1.0,
        distractor_share_min=composited.distractor_share + 0.01,
        page_fill_min=None,
    )
    check = DensityCheck(bounds, doc_type="invoice", config_path=DISTRACTORS_PATH)
    geometry: dict = {}
    _render("invoice", entry, layout, geometry_out=geometry)
    check(INVOICE, entry["layout"], geometry)
    assert len(check.errors) == 1
    assert_diagnostic_error(check.errors[0])
    assert f"distractor_share {composited.distractor_share:.4f}" in check.errors[0]


def test_a_composited_page_is_byte_identical_across_renders(box_drawer: str) -> None:
    block = {"type": box_drawer, "when": TEST_KEY, "box": CORNER, "jitter": True}
    entry, layout = overlaid("invoice", INVOICE, [block])
    first = _render("invoice", entry, layout)
    assert first.tobytes() == _render("invoice", entry, layout).tobytes()
    # The seeded draw is the block's own: the colour shift is the first draw of its overlay seed.
    shift = random.Random(overlay_seed(INVOICE, "invoice", 0)).randrange(0, 100)
    assert first.getpixel((50, 50)) == (200 - shift, 0, 0)


# --- apply_overlay directly -------------------------------------------------------------------------------


def _apply(blocks: list, fields: dict, ink: list[InkRecord], *, doc_type: str = "invoice"):
    image = Image.new("RGB", (400, 300), "white")
    recorder = recorder_with(ink, image.size)
    result = apply_overlay(
        image,
        {"overlay": blocks},
        {"case_id": "CASE999", "fields": fields},
        layout_id="synthetic_layout",
        layout_path="config/layouts/invoices.yml",
        doc_type=doc_type,
        recorder=recorder,
    )
    return image, recorder, result


def test_only_blocks_whose_key_is_present_draw_in_order(box_drawer: str) -> None:
    blocks = [
        {"type": box_drawer, "when": "DX_A", "box": [40, 40, 50, 50]},
        {"type": box_drawer, "when": "DX_ABSENT", "box": [60, 40, 70, 50]},
        {"type": box_drawer, "when": "DX_B", "box": [80, 40, 90, 50], "ink": "mark"},
        {"type": box_drawer, "when": "DX_EMPTY", "box": [100, 40, 110, 50]},
    ]
    image, recorder, result = _apply(blocks, {"DX_A": "x", "DX_B": "y", "DX_EMPTY": "NOT_FOUND"}, [])
    assert [mark.key for mark in result.marks] == ["DX_A", "DX_B"]
    assert result.drawn_for("DX_B")[0].box == (80, 40, 90, 50) and result.drawn_for("DX_B")[0].opaque
    assert result.drawn_for("DX_ABSENT") == []
    assert image.getpixel((65, 45)) == (255, 255, 255)
    assert recorder.ink_records() == [
        InkRecord("distractor", (40, 40, 50, 50), False, "note", 30, False, block=0),
        InkRecord("distractor", (80, 40, 90, 50), True, "", 0, False, block=1),
    ]


def test_drawings_out_hands_back_each_drawn_blocks_drawing_and_changes_nothing_drawn(
    box_drawer: str,
) -> None:
    blocks = [
        {"type": box_drawer, "when": "DX_A", "box": [40, 40, 50, 50]},
        {"type": box_drawer, "when": "DX_ABSENT", "box": [60, 40, 70, 50]},
        {"type": box_drawer, "when": "DX_B", "box": [80, 40, 90, 50], "ink": "mark"},
    ]
    fields = {"DX_A": "x", "DX_B": "y"}
    plain_image, plain_recorder, plain_result = _apply(blocks, fields, [])
    image = Image.new("RGB", (400, 300), "white")
    recorder = recorder_with([], image.size)
    drawings: dict = {}
    result = apply_overlay(
        image,
        {"overlay": blocks},
        {"case_id": "CASE999", "fields": fields},
        layout_id="synthetic_layout",
        layout_path="config/layouts/invoices.yml",
        doc_type="invoice",
        recorder=recorder,
        drawings_out=drawings,
    )
    assert list(drawings) == ["synthetic_layout.overlay[0]", "synthetic_layout.overlay[2]"]
    assert all(isinstance(drawing, OverlayDrawing) for drawing in drawings.values())
    assert drawings["synthetic_layout.overlay[2]"].marks == tuple(result.drawn_for("DX_B"))
    assert image.tobytes() == plain_image.tobytes()
    assert recorder.ink_records() == plain_recorder.ink_records()
    assert result == plain_result


def test_a_later_block_avoids_an_earlier_blocks_ink(box_drawer: str) -> None:
    blocks = [
        {"type": box_drawer, "when": "DX_A", "box": [40, 40, 50, 50], "ink": "mark"},
        {"type": box_drawer, "when": "DX_B", "box": [45, 45, 55, 55]},
    ]
    with pytest.raises(OverlayError, match="code mark"):
        _apply(blocks, {"DX_A": "x", "DX_B": "y"}, [])


def test_an_unregistered_type_is_refused_at_render() -> None:
    with pytest.raises(OverlayError) as err:
        _apply([{"type": "hologram", "when": "DX_A"}], {"DX_A": "x"}, [])
    assert_diagnostic_error(err.value)
    assert "hologram" in str(err.value) and "synthetic_layout.overlay[0].type" in str(err.value)


@pytest.mark.parametrize("case_id", [None, "", 7])
def test_an_entry_without_a_case_id_is_refused_not_seeded_by_a_fallback(
    box_drawer: str, case_id: object
) -> None:
    """Review Minor 2: no '<case>' fallback, which would give every such page the same overlay seed."""
    image = Image.new("RGB", (400, 300), "white")
    entry = {"fields": {"DX_A": "x"}, **({} if case_id is None else {"case_id": case_id})}
    with pytest.raises(OverlayError) as err:
        apply_overlay(
            image,
            {"overlay": [{"type": box_drawer, "when": "DX_A", "box": [40, 40, 50, 50]}]},
            entry,
            layout_id="synthetic_layout",
            layout_path="config/layouts/invoices.yml",
            doc_type="invoice",
            recorder=recorder_with([], image.size),
        )
    assert_diagnostic_error(err.value)
    assert f"case_id {case_id!r}" in str(err.value) and "synthetic_layout.overlay" in str(err.value)


def _shaped_drawer(layer: Image.Image | None, mask: Image.Image | None):  # noqa: ANN202
    def drawer(block: dict, ctx: object) -> OverlayDrawing:
        tint = Tint(mask=mask, colour=(255, 240, 120), bands=((40, 40, 50, 50),)) if mask else None
        return OverlayDrawing(marks=(), layer=layer, tint=tint)

    return drawer


@pytest.mark.parametrize(
    ("layer", "mask", "tokens"),
    [
        (Image.new("RGB", (400, 300)), None, ("layer of mode RGB", "mode RGBA")),
        (Image.new("RGBA", (200, 300)), None, ("size (200, 300)", "(400, 300)")),
        (None, Image.new("L", (400, 299)), ("tint mask", "size (400, 299)")),
        (None, Image.new("1", (400, 300)), ("tint mask of mode 1", "mode L")),
    ],
)
def test_a_drawing_of_the_wrong_mode_or_size_is_refused_naming_the_block(
    monkeypatch: pytest.MonkeyPatch, layer: Image.Image | None, mask: Image.Image | None, tokens: tuple
) -> None:
    """Review Minor 3: a bad layer or mask fails four-part, naming case, layout and block, not bare."""
    monkeypatch.setitem(OVERLAY_DRAWERS, "shaped", _shaped_drawer(layer, mask))
    with pytest.raises(OverlayError) as err:
        _apply([{"type": "shaped", "when": "DX_A"}], {"DX_A": "x"}, [])
    assert_diagnostic_error(err.value)
    for token in ("CASE999", "synthetic_layout", "synthetic_layout.overlay[0]", "'shaped'", *tokens):
        assert token in str(err.value), (token, str(err.value))


def test_each_block_draws_on_its_own_overlay_seed() -> None:
    expected = int.from_bytes(hashlib.sha256(b"CASE001:overlay:invoice:2").digest()[:8], "big")
    assert overlay_seed("CASE001", "invoice", 2) == expected
    assert len({overlay_seed("CASE001", "invoice", i) for i in range(4)}) == 4
    assert overlay_seed("CASE001", "invoice", 0) != overlay_seed("CASE001", "bank_statement", 0)


# --- render-time refusals ---------------------------------------------------------------------------------


def test_a_mark_on_a_scored_value_fails_at_render(box_drawer: str) -> None:
    _refused(
        "invoice",
        INVOICE,
        [{"type": box_drawer, "when": TEST_KEY, "near": {"role": "scored"}}],
        "scored ink",
        "invoices.yml",
        "overlay.clearance_px",
    )


def test_a_mark_below_a_scored_value_inside_its_padding_fails_at_render(box_drawer: str) -> None:
    # Centred on the scored line, moved down to its descender band: past the box, inside the padding.
    entry = corpus_entries("invoice")[INVOICE]
    geometry: dict = {}
    _render("invoice", entry, layouts_for("invoice")[entry["layout"]], geometry_out=geometry)
    first = next(r for r in geometry["ink"] if r.role == "scored")
    below = (first.box[3] - first.box[1]) // 2 + 2
    _refused(
        "invoice",
        INVOICE,
        [{"type": box_drawer, "when": TEST_KEY, "near": {"role": "scored", "dy": below, "size": 1}}],
        f"scored ink {first.box}",
    )


def test_text_is_protected_on_a_bank_statement_but_not_on_an_invoice(box_drawer: str) -> None:
    block = {"type": box_drawer, "when": TEST_KEY, "near": {"role": "text", "index": 0}}
    entry, layout = overlaid("invoice", INVOICE, [block])
    _render("invoice", entry, layout)
    _refused("bank_statement", BANK, [block], "text ink")
    policy = load_distractor_config().overlay.protect
    assert (policy["invoice"], policy["bank_statement"]) == ("scored_and_marks", "all_ink")


def test_a_mark_on_a_code_mark_fails_at_render(box_drawer: str, every_item_forced: None) -> None:
    _refused(
        "invoice", INVOICE, [{"type": box_drawer, "when": TEST_KEY, "near": {"role": "mark"}}], "code mark"
    )


def test_a_mark_outside_the_page_inset_fails_at_render(box_drawer: str) -> None:
    _refused(
        "invoice",
        INVOICE,
        [{"type": box_drawer, "when": TEST_KEY, "box": [5, 5, 15, 15]}],
        "page_inset_px",
        "(5, 5, 15, 15)",
    )


def test_a_dark_tint_over_a_scored_value_fails_contrast_at_render(box_drawer: str) -> None:
    entry = corpus_entries("invoice")[INVOICE]
    geometry: dict = {}
    _render("invoice", entry, layouts_for("invoice")[entry["layout"]], geometry_out=geometry)
    left, top, right, bottom = next(r for r in geometry["ink"] if r.role == "scored").box
    band = [left - 8, top - 4, right + 8, bottom + 4]
    dark = {"type": box_drawer, "when": TEST_KEY, "box": band, "mode": "tint", "colour": [80, 80, 80]}
    _refused("invoice", INVOICE, [dark], "min_ratio", "scored ink")
    light = {**dark, "colour": [255, 241, 118]}
    entry, layout = overlaid("invoice", INVOICE, [light])
    _render("invoice", entry, layout)
