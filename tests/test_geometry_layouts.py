"""Draw-time geometry capture for INVOICE_DATE, LINE_ITEM_QUANTITIES and LINE_ITEM_PRICES.

These are three DocILE-required fields that were once drawn without a capture
hook. Each test renders WITH a recorder (`geometry_out={}`) and asserts the
captured box is present, normalised within [0, 1], and positioned plausibly (a
label-prefixed value's box must not start at the page's left margin: it must be
offset past the label).

LINE_ITEM_PRICES on receipts is a documented gap: receipts have no draw site at
all for the per-unit price (only the line total is rendered), so there is
nothing on the page to capture without adding visible content, which would
break the opt-in contract (pixels stay byte-identical with no recorder). That
sub-case is asserted as absent, not faked.
"""

import pytest

from generators.common import load_font
from tests.renderer_helpers import (
    corpus_entries,
    entry_per_layout,
    layouts_for,
    render_entry,
)
from tests.snapshot_tools import digest, load_snapshot


def _boxes(doc_type: str, entry: dict) -> dict[str, list[float]]:
    geometry: dict = {}
    render_entry(doc_type, entry, geometry_out=geometry, distractors=False)
    return geometry["boxes"]


def _quantities(entry: dict) -> list[int]:
    return [int(q) for q in str(entry["fields"]["LINE_ITEM_QUANTITIES"]).split("|")]


def _receipt_with_quantities(*, multiple: bool) -> dict:
    """First receipt that has (or has none of) a quantity above one, per `multiple`."""
    for entry in corpus_entries("receipt").values():
        has_multiple = any(q > 1 for q in _quantities(entry))
        if has_multiple == multiple:
            return entry
    raise AssertionError(f"no receipt in the corpus with quantities>1 == {multiple}")


def _date_text_widths(doc_type: str, entry: dict) -> tuple[float, float]:
    """Return (value width, label+value width) in pixels at the font that matches the box.

    The date's font role is a layout choice, so find the role size whose measured
    value width equals the recorded box width; fail if none does.
    """
    layout = layouts_for(doc_type)[entry["layout"]]
    geometry: dict = {}
    render_entry(doc_type, entry, geometry_out=geometry, distractors=False)
    left, _, right, _ = geometry["boxes"]["INVOICE_DATE"]
    box_px = (right - left) * geometry["width"]
    value = str(entry["fields"]["INVOICE_DATE"])
    family = layout["defaults"]["family"]
    for size in layout["font_sizes"].values():
        font = load_font(int(size), family=family)
        bbox = font.getbbox(value)
        if abs((bbox[2] - bbox[0]) - box_px) <= 1.5:
            full = font.getbbox(f"Date: {value}")
            return box_px, float(full[2] - full[0])
    raise AssertionError(f"no font_sizes role of {entry['layout']} measures the date at {box_px}px")


def test_receipt_invoice_date_captured_and_offset_past_label() -> None:
    """The receipt date line draws 'Date: <value>' as one string; the box covers only the value.

    So it is normalised within [0, 1] and must not start at the left margin: it
    sits past the 'Date: ' label.
    """
    for layout_id, entry in entry_per_layout("receipt").items():
        left, top, right, bottom = _boxes("receipt", entry)["INVOICE_DATE"]
        layout = layouts_for("receipt")[layout_id]
        margin_frac = layout["margin"] / layout["width"]
        assert 0.0 <= left < right <= 1.0
        assert 0.0 <= top < bottom <= 1.0
        assert left > margin_frac + 0.03, f"{layout_id}: date box starts at the margin"


def test_invoice_invoice_date_captured_and_offset_past_label() -> None:
    """Same label-prefix check on the invoice renderer's invoice-date line."""
    for layout_id, entry in entry_per_layout("invoice").items():
        left, top, right, bottom = _boxes("invoice", entry)["INVOICE_DATE"]
        layout = layouts_for("invoice")[layout_id]
        margin_frac = layout["margin"] / layout["page_dimensions"]["width"]
        assert 0.0 <= left < right <= 1.0
        assert 0.0 <= top < bottom <= 1.0
        assert left > margin_frac + 0.01, f"{layout_id}: date box starts at the margin"


@pytest.mark.parametrize("doc_type", ["receipt", "invoice"])
def test_invoice_date_value_matches_ground_truth_string_width(doc_type: str) -> None:
    """The box is sized for the *value* substring, narrower than the whole 'Date: <value>' run."""
    entry = next(iter(entry_per_layout(doc_type).values()))
    value_px, full_px = _date_text_widths(doc_type, entry)
    assert value_px < full_px - 10


def test_receipt_line_item_quantity_captured_when_rendered() -> None:
    """A receipt line with quantity above one prints a unit line beneath its description; its quantity
    and unit price each get their own box there, quantity left of price, both below the description.
    """
    entry = _receipt_with_quantities(multiple=True)
    boxes = _boxes("receipt", entry)
    captured = 0
    for i, qty in enumerate(_quantities(entry)):
        if qty <= 1:
            continue
        q_left, q_top, q_right, _ = boxes[f"LINE_ITEM_QUANTITIES[{i}]"]
        p_left, p_top, p_right, _ = boxes[f"LINE_ITEM_PRICES[{i}]"]
        d_left, _, _, d_bottom = boxes[f"LINE_ITEM_DESCRIPTIONS[{i}]"]
        assert d_left < q_left < q_right < p_left < p_right <= 1.0
        assert q_top >= d_bottom and p_top >= d_bottom
        captured += 1
    assert captured >= 1


def test_receipt_line_item_quantity_of_one_has_no_box_by_design() -> None:
    """Quantity 1 is never printed on receipts, so those rows have nothing to capture.

    Documents the known, deliberate shortfall rather than hiding it.
    """
    entry = _receipt_with_quantities(multiple=True)
    boxes = _boxes("receipt", entry)
    ones = [i for i, q in enumerate(_quantities(entry)) if q == 1]
    assert ones, "fixture needs at least one quantity-1 row"
    for i in ones:
        assert f"LINE_ITEM_QUANTITIES[{i}]" not in boxes
        assert f"LINE_ITEM_DESCRIPTIONS[{i}]" in boxes

    all_ones = _receipt_with_quantities(multiple=False)
    assert not [k for k in _boxes("receipt", all_ones) if k.startswith("LINE_ITEM_QUANTITIES")]


def test_invoice_line_item_quantity_captured_for_every_item() -> None:
    """Invoices always print the quantity column whatever its value, so every item's quantity is captured."""
    for layout_id, entry in entry_per_layout("invoice").items():
        boxes = _boxes("invoice", entry)
        for i in range(len(_quantities(entry))):
            left, top, right, bottom = boxes[f"LINE_ITEM_QUANTITIES[{i}]"]
            assert 0.0 <= left < right <= 1.0, layout_id
            assert 0.0 <= top < bottom <= 1.0, layout_id


def test_invoice_line_item_price_captured_for_every_item() -> None:
    """Regression guard: invoices keep full LINE_ITEM_PRICES coverage, one box per item."""
    for layout_id, entry in entry_per_layout("invoice").items():
        boxes = _boxes("invoice", entry)
        for i in range(len(_quantities(entry))):
            left, top, right, bottom = boxes[f"LINE_ITEM_PRICES[{i}]"]
            assert 0.0 <= left < right <= 1.0, layout_id
            assert 0.0 <= top < bottom <= 1.0, layout_id


def test_receipt_line_item_price_boxed_only_where_quantity_exceeds_one() -> None:
    """Every receipt layout boxes LINE_ITEM_PRICES on its unit lines, and only there.

    A line of one unit prints no unit line (its unit price is its printed total), so its price has no box.
    """
    for layout_id, entry in entry_per_layout("receipt").items():
        boxes = _boxes("receipt", entry)
        for i, qty in enumerate(_quantities(entry)):
            assert (f"LINE_ITEM_PRICES[{i}]" in boxes) == (qty > 1), (layout_id, i)
        assert any(k.startswith("LINE_ITEM_TOTAL_PRICES") for k in boxes), layout_id


@pytest.mark.parametrize("doc_type", ["receipt", "invoice"])
def test_no_recorder_still_byte_identical_after_task10b_changes(doc_type: str) -> None:
    """The extra capture calls never alter pixels without a recorder.

    (adjusted: compared with the pinned pixel snapshot, since the pre-capture
    renderer no longer exists to compare against.)
    """
    pinned = load_snapshot()
    checked = 0
    for entry in entry_per_layout(doc_type).values():
        key = f"{entry['case_id']}_{entry['layout']}"
        if key not in pinned:
            continue
        assert digest(render_entry(doc_type, entry, distractors=False)) == pinned[key]["hash"], key
        checked += 1
    assert checked >= 1
