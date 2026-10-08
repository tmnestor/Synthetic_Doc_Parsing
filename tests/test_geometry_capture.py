"""Draw-time geometry capture across all three renderers.

Verifies the opt-in contract end to end: passing `geometry_out=None` (the
default) must render pixel-identical to omitting the argument, and passing
`geometry_out={}` must never crash, for every layout of every renderer. The
overlay stage depends on this contract: width, height and normalised boxes are
populated in place, and nothing is recorded unless the caller asks.
"""

import pytest
from PIL import Image

from tests.renderer_helpers import (
    entry_per_layout,
    layouts_for,
    pixels_equal,
    render_entry,
    renderer_for,
)
from tests.snapshot_tools import DOC_TYPES

ALL_TYPES = tuple(DOC_TYPES)


def _all_layout_cases() -> list[tuple[str, str]]:
    return [(t, layout_id) for t in ALL_TYPES for layout_id in layouts_for(t)]


def _count_tables(blocks: list) -> int:
    """Count `table` blocks anywhere in a body, descending into nested lists and dicts."""
    total = 0
    for block in blocks:
        if isinstance(block, list):
            total += _count_tables(block)
        elif isinstance(block, dict):
            total += block.get("type") == "table"
            for value in block.values():
                if isinstance(value, list):
                    total += _count_tables(value)
    return total


@pytest.mark.parametrize("doc_type", ALL_TYPES)
def test_default_call_has_no_geometry_out_and_returns_an_image(doc_type: str) -> None:
    """The plain two-arg call (no geometry_out at all) must still work."""
    entry = next(iter(entry_per_layout(doc_type).values()))
    image = renderer_for(doc_type)(entry, layouts_for(doc_type)[entry["layout"]], distractors=False)
    assert isinstance(image, Image.Image)
    assert image.width > 0
    assert image.height > 0


@pytest.mark.parametrize("doc_type", ALL_TYPES)
def test_geometry_out_none_is_identical_to_omitting_it(doc_type: str) -> None:
    """Explicit geometry_out=None must render exactly like the bare call."""
    entry = next(iter(entry_per_layout(doc_type).values()))
    bare = render_entry(doc_type, entry, distractors=False)
    explicit = render_entry(doc_type, entry, geometry_out=None, distractors=False)
    assert pixels_equal(bare, explicit)


@pytest.mark.parametrize("doc_type", ALL_TYPES)
def test_recording_geometry_does_not_change_pixels(doc_type: str) -> None:
    """Opting in must not alter a single pixel: capture only measures what was drawn."""
    entry = next(iter(entry_per_layout(doc_type).values()))
    assert pixels_equal(
        render_entry(doc_type, entry, distractors=False),
        render_entry(doc_type, entry, geometry_out={}, distractors=False),
    )


@pytest.mark.parametrize(("doc_type", "layout_id"), _all_layout_cases())
def test_every_layout_across_every_renderer_captures_geometry_without_crashing(
    doc_type: str, layout_id: str
) -> None:
    """geometry_out={} must populate width/height/boxes (and the internal ink) for every layout id.

    Written as one case per layout (not per document) because the contract is per
    layout. It would have caught the old tax_invoice_mixed duplicate-table crash
    before it reached `pipeline generate`.
    """
    entry = entry_per_layout(doc_type)[layout_id]
    geometry: dict = {}
    image = render_entry(doc_type, entry, geometry_out=geometry, distractors=False)

    assert set(geometry) == {"width", "height", "boxes", "texts", "ink", "labels", "regions"}
    assert geometry["width"] == image.width
    assert geometry["height"] == image.height
    boxes = geometry["boxes"]
    assert boxes, f"{layout_id}: no field box was captured"
    for field, (left, top, right, bottom) in boxes.items():
        assert 0.0 <= left < right <= 1.0, f"{layout_id} {field}: bad x extent {left}, {right}"
        assert 0.0 <= top < bottom <= 1.0, f"{layout_id} {field}: bad y extent {top}, {bottom}"


def test_invoice_mixed_layout_only_captures_the_first_table() -> None:
    """tax_invoice_mixed records each line item exactly once, with no duplicate-table collision.

    (adjusted: the layout no longer draws a second, GST-free table over the same
    list. The invariant that survives is that the layout carries a single
    line-item table and every item is recorded once, so the recorder never sees a
    second draw of the same LINE_ITEM_* key.)
    """
    layout = layouts_for("invoice")["tax_invoice_mixed"]
    assert _count_tables(layout["body"]) == 1

    entry = entry_per_layout("invoice")["tax_invoice_mixed"]
    items = entry["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|")
    geometry: dict = {}
    render_entry("invoice", entry, geometry_out=geometry, distractors=False)

    described = sorted(k for k in geometry["boxes"] if k.startswith("LINE_ITEM_DESCRIPTIONS["))
    assert described == sorted(f"LINE_ITEM_DESCRIPTIONS[{i}]" for i in range(len(items)))
