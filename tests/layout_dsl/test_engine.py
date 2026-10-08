"""The walker: dispatch each block to its primitive drawer and report where a failure happened."""

import pytest

from generators.common import FitError
from generators.layout_budgets import LayoutBudgetError
from generators.layout_dsl.defaults import DefaultsError
from generators.layout_dsl.engine import PRIMITIVE_DRAWERS, EngineError, render_blocks, render_body
from generators.layout_dsl.schema import PRIMITIVES
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import (
    ENTRY,
    LAYOUT_ID,
    MARGIN,
    PAGE_WIDTH,
    RECEIPTS_PATH,
    canvas,
    make_ctx,
    real_layout,
    render,
)
from generators.layout_dsl.context import Region


def test_every_schema_primitive_has_a_drawer() -> None:
    assert set(PRIMITIVES) == set(PRIMITIVE_DRAWERS)
    assert all(callable(drawer) for drawer in PRIMITIVE_DRAWERS.values())


def test_render_blocks_advances_through_the_list() -> None:
    layout = real_layout([])
    blocks = [
        {"type": "spacer", "height": 5},
        {"type": "spacer", "height": 7},
        {"type": "spacer", "height": 11},
    ]
    assert render_blocks(blocks, make_ctx(layout), 100) == 123


def test_when_suppresses_a_block_whose_field_is_absent() -> None:
    layout = real_layout([])
    entry = {**ENTRY, "fields": {"TOTAL_AMOUNT": "1", "BLANK": "", "NF": "NOT_FOUND"}}
    for field in ("MISSING", "BLANK", "NF"):
        blocks = [{"type": "spacer", "height": 9, "when": field}]
        assert render_blocks(blocks, make_ctx(layout, entry), 40) == 40, field


def test_when_admits_a_block_whose_field_is_present() -> None:
    layout = real_layout([])
    blocks = [{"type": "spacer", "height": 9, "when": "TOTAL_AMOUNT"}]
    assert render_blocks(blocks, make_ctx(layout), 40) == 49


def test_unknown_primitive_fails_with_diagnostic() -> None:
    layout = real_layout([])
    with pytest.raises(EngineError) as exc_info:
        render_blocks([{"type": "hologram"}], make_ctx(layout), 0)
    assert_diagnostic_error(exc_info.value)
    assert "hologram" in str(exc_info.value)
    assert "spacer" in str(exc_info.value)  # lists the supported primitives


def test_nested_panel_renders_through_the_walker() -> None:
    layout = real_layout([])
    panel = {
        "type": "panel",
        "padding": 6,
        "children": [{"type": "spacer", "height": 10}, {"type": "spacer", "height": 10}],
    }
    assert render_blocks([panel], make_ctx(layout), 20) == 20 + 6 + 20 + 6


def test_render_body_missing_body_key_fails_with_diagnostic() -> None:
    layout = real_layout([])
    del layout["body"]
    _, draw = canvas()
    with pytest.raises(EngineError) as exc_info:
        render_body(
            layout,
            ENTRY,
            layout_id=LAYOUT_ID,
            layout_path=str(RECEIPTS_PATH),
            draw=draw,
            region=Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN),
            y=0,
            distractors=False,
        )
    assert_diagnostic_error(exc_info.value)
    assert LAYOUT_ID in str(exc_info.value)


def test_render_body_renders_a_valid_layout_and_returns_advanced_y() -> None:
    layout = real_layout(
        [
            {"type": "text", "content": "Total {TOTAL_AMOUNT}"},
            {"type": "spacer", "height": 8},
        ]
    )
    advance = layout["defaults"]["line_advance"]["body"]
    assert render(layout, y=30) == 30 + advance + 8


def test_render_body_injects_render_children_so_a_panel_renders_its_contents() -> None:
    layout = real_layout([{"type": "panel", "padding": 4, "children": [{"type": "spacer", "height": 25}]}])
    assert render(layout, y=0) == 4 + 25 + 4


def _failing_layout(inner: dict) -> dict:
    return real_layout([{"type": "spacer"}, {"type": "panel", "children": [{"type": "spacer"}, inner]}])


def test_render_body_reports_the_failing_blocks_nested_location() -> None:
    layout = _failing_layout({"type": "text", "content": "x", "budget": "NO_SUCH_BUDGET"})
    with pytest.raises(LayoutBudgetError) as exc_info:
        render(layout)
    message = str(exc_info.value)
    assert f"At:       {LAYOUT_ID}.body[1](panel)[1](text)" in message
    assert "NO_SUCH_BUDGET" in message


def test_render_body_reports_the_nested_location_of_a_missing_default() -> None:
    """The likeliest render-time DSL failure; only the walker knows which block asked."""
    layout = _failing_layout({"type": "text", "content": "x"})
    del layout["defaults"]["color"]
    with pytest.raises(DefaultsError) as exc_info:
        render(layout)
    assert_diagnostic_error(exc_info.value)
    assert f"At:       {LAYOUT_ID}.body[1](panel)[1](text)" in str(exc_info.value)


def test_render_body_reports_nested_location_for_a_fit_error() -> None:
    layout = _failing_layout(
        {"type": "text", "content": "{TOTAL_AMOUNT} far too long for ten pixels", "budget": "TIGHT"}
    )
    layout["field_budgets"] = {"TIGHT": {"width": 10, "fit": "shrink", "min_font": 10, "max_lines": 1}}
    with pytest.raises(FitError) as exc_info:
        render(layout)
    assert f"At:       {LAYOUT_ID}.body[1](panel)[1](text)" in str(exc_info.value)
