"""Invoice rendering fixes: the totals panel keeps its gap, line totals stay inside the margin.

Both are measured on drawn ink, on every invoice case, never on the layout's own numbers.
"""

import pytest

from generators.exporters.geometry import InkRecord
from tests.renderer_helpers import corpus_entries, layouts_for, render_entry

CASES = sorted(corpus_entries("invoice"))


def _walk(node: object):
    """Yield every dict nested anywhere in a layout body."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _declared_gap(layout: dict, label: str) -> int:
    """The `min_gap` the layout's YAML declares on the pair labelled `label`."""
    gaps = [
        int(block.get("min_gap", layout["defaults"]["pair_min_gap"]))
        for block in _walk(layout["body"])
        if block.get("type") == "pair" and block.get("label") == label
    ]
    assert gaps, f"layout declares no '{label}' pair"
    return min(gaps)


def _ink(case_id: str) -> tuple[dict, dict, list[InkRecord], dict]:
    entry = corpus_entries("invoice")[case_id]
    layout = layouts_for("invoice")[entry["layout"]]
    ink: list[InkRecord] = []
    geometry: dict = {}
    render_entry("invoice", entry, distractors=False, ink_out=ink, geometry_out=geometry)
    return entry, layout, ink, geometry


def _total_pair(ink: list[InkRecord], geometry: dict) -> tuple[InkRecord, InkRecord]:
    """The Total amount's ink and the ink of the label drawn on its row."""
    width = geometry["width"]
    height = geometry["height"]
    left, top, right, bottom = geometry["boxes"]["TOTAL_AMOUNT"]
    probe = (left * width, top * height, right * width, bottom * height)
    amount = next(
        r
        for r in ink
        if r.role == "scored"
        and abs(r.box[0] - probe[0]) <= 2
        and abs(r.box[2] - probe[2]) <= 2
        and abs(r.box[1] - probe[1]) <= 30
    )
    row = [
        r
        for r in ink
        if r is not amount
        and r.box[1] < amount.box[3]
        and r.box[3] > amount.box[1]
        and r.box[2] <= amount.box[0] + 200
    ]
    label = max((r for r in row if r.box[0] < amount.box[0]), key=lambda r: r.box[2])
    return label, amount


@pytest.mark.parametrize("case_id", CASES)
def test_total_label_and_amount_ink_keep_the_declared_gap(case_id: str) -> None:
    _, layout, ink, geometry = _ink(case_id)
    label, amount = _total_pair(ink, geometry)
    declared = _declared_gap(layout, "{LABEL_TOTAL_AMOUNT}")
    assert declared > 0, "the Total pair must declare a positive min_gap in YAML"
    gap = amount.box[0] - label.box[2]
    assert gap >= declared, f"{case_id}: Total label and amount are {gap}px apart; YAML declares {declared}"


@pytest.mark.parametrize("case_id", CASES)
def test_totals_panel_pairs_keep_a_declared_gap(case_id: str) -> None:
    """Every right-aligned totals pair declares a min_gap, so a long amount cannot reach its label."""
    _, layout, _, _ = _ink(case_id)
    # GST and Total draw their labels through label sites.
    for label in ("Subtotal", "{LABEL_GST_AMOUNT}", "{LABEL_TOTAL_AMOUNT}"):
        blocks = [b for b in _walk(layout["body"]) if b.get("type") == "pair" and b.get("label") == label]
        for block in blocks:
            assert int(block.get("min_gap", 0)) > 0, f"{case_id}: '{label}' pair declares no min_gap"


@pytest.mark.parametrize("case_id", CASES)
def test_line_total_ink_lies_within_the_content_region(case_id: str) -> None:
    entry, layout, ink, geometry = _ink(case_id)
    region_right = layout["margin"] + layout["content_width"]
    width = geometry["width"]
    cells = {k: v for k, v in geometry["boxes"].items() if k.startswith("LINE_ITEM_TOTAL_PRICES[")}
    assert cells, "no line-total cells were captured"
    for key, box in cells.items():
        assert box[2] * width <= region_right + 1, (
            f"{case_id} {key} ends at {box[2] * width:.0f} > {region_right}"
        )
    worst = max(r.box[2] for r in ink)
    assert worst <= region_right, (
        f"{case_id}: ink reaches x={worst}, past the content region ({region_right})"
    )
