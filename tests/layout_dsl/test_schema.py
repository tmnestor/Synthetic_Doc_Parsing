"""Startup validation of a layout body: structure, references, geometry, providers and layout keys.

Two entry points are exercised. `validate_body` checks one body tree against a set of known field names;
`validate_layout` adds everything that needs the surrounding layout (defaults, budgets against geometry,
`field_providers:`, line-advance coverage, and the keys the layout itself may carry). Every rejection must be a
`LayoutSchemaError` carrying the four-element diagnostic.
"""

import copy
from collections.abc import Callable

import pytest

from generators.layout_dsl import provider_registry
from generators.layout_dsl.schema import (
    LayoutSchemaError,
    _line_advance_roles,
    known_layout_keys,
    validate_body,
    validate_layout,
)
from generators.loader import load_layout_registry
from generators.schema import field_names_for
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import RECEIPTS_PATH, real_layout

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

LAYOUT_ID = "layout_under_test"
LAYOUT_PATH = str(RECEIPTS_PATH)
LAYOUT_DIR = REPO_ROOT / "config" / "layouts"

KNOWN = {
    "PAYER_NAME",
    "TOTAL_AMOUNT",
    "INVOICE_DATE",
    "LINE_ITEM_DESCRIPTIONS",
    "LINE_ITEM_QUANTITIES",
}


# --- builders ---------------------------------------------------------------------------------------------


def text(content: str = "{PAYER_NAME}", **extra) -> dict:
    """Return a minimal text block."""
    return {"type": "text", "content": content, **extra}


def pair(**extra) -> dict:
    """Return a minimal pair block."""
    return {"type": "pair", "label": "Total", "value": "{TOTAL_AMOUNT}", **extra}


def table(**extra) -> dict:
    """Return a minimal valid ruled, ungrouped table block."""
    block = {
        "type": "table",
        "rows": "pipe_fields",
        "columns": [
            {"key": "desc", "label": "Description", "align": "left", "x": 0},
            {"key": "amount", "label": "Amount", "align": "right", "x_right": 0},
        ],
        "frame": "ruled",
        "grouping": "none",
    }
    block.update(extra)
    return block


def banner(**extra) -> dict:
    """Return a minimal banner block."""
    return {"type": "banner", "height": 40, "color": "#112233", "content": "{PAYER_NAME}", **extra}


def check_body(body: list, known: set[str] | None = None) -> None:
    """Run `validate_body` with this module's layout id and path."""
    validate_body(
        body,
        layout_id=LAYOUT_ID,
        layout_path=LAYOUT_PATH,
        known_fields=KNOWN if known is None else known,
    )


def make_layout(
    body: list,
    *,
    budgets: dict | None = None,
    providers: list | None = None,
    content_width: int | None = None,
    defaults: dict | None = None,
    **extra,
) -> dict:
    """Build a layout from a real receipt layout, with the body, budgets and defaults swapped.

    Args:
        body: The block list.
        budgets: `field_budgets:` replacement; empty when omitted.
        providers: `field_providers:` list.
        content_width: Region width override.
        defaults: Entries merged over the real `defaults:`.
        **extra: Further top-level keys set on the layout.

    Returns:
        A layout dict `validate_layout` accepts, absent the override under test.
    """
    layout = real_layout(body, providers)
    # `footer_text` is read only when a body block names it through `from_layout:`.
    layout.pop("footer_text", None)
    # `unit_line_format` likewise, through the line-items table's `params_from_layout:`.
    layout.pop("unit_line_format", None)
    layout["field_budgets"] = budgets if budgets is not None else {}
    if content_width is not None:
        layout["content_width"] = content_width
    if defaults:
        layout["defaults"].update(defaults)
    layout.update(extra)
    # A layout built as an invoice (it declares service categories) is not a receipt layout, so it drops
    # the receipt's merchant categories, which only receipt layouts may carry.
    if "service_categories" in extra:
        layout.pop("merchant_categories", None)
    return layout


def check_layout(layout: dict, known: set[str] | None = None, doc_type: str = "receipts") -> None:
    """Run `validate_layout` with this module's layout id and path."""
    validate_layout(
        layout,
        layout_id=LAYOUT_ID,
        layout_path=LAYOUT_PATH,
        known_fields=KNOWN if known is None else known,
        doc_type=doc_type,
    )


def budget(width: int) -> dict:
    """Return a `field_budgets` entry of the given width."""
    return {"width": width, "fit": "shrink", "min_font": 10, "max_lines": 1}


def rejected(call: Callable[[], None], *tokens: str) -> LayoutSchemaError:
    """Assert `call` raises a four-element `LayoutSchemaError` whose text names every token."""
    with pytest.raises(LayoutSchemaError) as exc_info:
        call()
    assert_diagnostic_error(exc_info.value)
    for token in tokens:
        assert token in str(exc_info.value), f"{token!r} missing from: {exc_info.value}"
    return exc_info.value


def reject_body(body: list, *tokens: str, known: set[str] | None = None) -> LayoutSchemaError:
    """Assert `validate_body` rejects `body`."""
    return rejected(lambda: check_body(body, known), *tokens)


def reject_layout(layout: dict, *tokens: str) -> LayoutSchemaError:
    """Assert `validate_layout` rejects `layout`."""
    return rejected(lambda: check_layout(layout), *tokens)


# --- body structure ---------------------------------------------------------------------------------------


def test_accepts_a_minimal_valid_body() -> None:
    """A body of one text block naming a known field validates."""
    check_body([text()])


def test_rejects_unknown_primitive_and_lists_allowed() -> None:
    """An unknown `type:` is rejected, and the diagnostic lists the allowed primitives."""
    err = reject_body([{"type": "hologram"}], "hologram", "'table'", "'text'")
    assert "[0].type" in str(err)


def test_rejects_block_without_type() -> None:
    """A block with no `type:` key is rejected."""
    reject_body([{"content": "x"}], "no 'type' key")


def test_rejects_missing_required_key() -> None:
    """A primitive missing one of its required keys is rejected, naming the key."""
    reject_body([{"type": "pair", "label": "Total"}], "missing required", "value")


def test_rejects_unknown_field_reference() -> None:
    """A `{FIELD}` placeholder naming no known field is rejected."""
    reject_body([text("{NO_SUCH_FIELD}")], "NO_SUCH_FIELD")


def test_rejects_unknown_when_field() -> None:
    """A `when:` naming no known field is rejected."""
    reject_body([text(when="NO_SUCH_FIELD")], "'when'", "NO_SUCH_FIELD")


def test_rejects_unregistered_row_provider() -> None:
    """A table naming an unregistered row provider is rejected, listing the registered ones."""
    reject_body([table(rows="no_such_provider")], "no_such_provider", "pipe_fields")


def test_recurses_into_containers() -> None:
    """A bad block nested inside a panel is found, and the diagnostic path reaches into `children`."""
    err = reject_body([{"type": "panel", "children": [text("{NO_SUCH_FIELD}")]}], "NO_SUCH_FIELD")
    assert "children[0]" in str(err)


def test_recurses_into_split_columns() -> None:
    """A bad block nested in one column of a split is found, and the path names that column."""
    body = [{"type": "split", "children": [[text()], [text("{NO_SUCH_FIELD}")]]}]
    err = reject_body(body, "NO_SUCH_FIELD")
    assert "children[1]" in str(err)


# --- table frame, grouping and column shape ---------------------------------------------------------------


def test_rejects_table_column_without_key() -> None:
    """A table column with no `key:` is rejected."""
    reject_body([table(columns=[{"label": "Date", "x": 0}])], "missing 'key'")


def test_table_requires_frame() -> None:
    """A table with no `frame:` is rejected as missing a required key."""
    block = table()
    del block["frame"]
    reject_body([block], "missing required", "frame")


def test_table_requires_grouping() -> None:
    """A table with no `grouping:` is rejected as missing a required key."""
    block = table()
    del block["grouping"]
    reject_body([block], "missing required", "grouping")


def test_rejects_unknown_frame() -> None:
    """An unknown `frame:` is rejected, listing the allowed frames."""
    reject_body([table(frame="zebra")], "unknown frame 'zebra'", "bordered")


def test_rejects_unknown_grouping() -> None:
    """An unknown `grouping:` is rejected, listing the allowed groupings."""
    reject_body([table(grouping="spiral")], "unknown grouping 'spiral'", "dedicated_row")


def test_bordered_frame_with_inline_grouping_is_accepted() -> None:
    """Westpac premium's date-grouped bordered table (bordered frame, inline grouping) is not rejected."""
    check_body([table(frame="bordered", grouping="inline")])


def test_filled_frame_without_fill_color_is_rejected() -> None:
    """`frame: filled` draws a fill, so it requires `fill_color`."""
    reject_body([table(frame="filled")], "fill_color")


def test_fill_color_without_filled_frame_is_rejected() -> None:
    """A `fill_color` on a frame that never draws one is rejected."""
    reject_body([table(frame="ruled", fill_color="#E8F0FE")], "fill_color", "'ruled'")


def test_filled_frame_with_fill_color_is_accepted() -> None:
    """`frame: filled` with a `fill_color` validates."""
    check_body([table(frame="filled", fill_color="#E8F0FE")])


def test_accepts_fill_inset_fill_height_and_label_inset_y() -> None:
    """Fix round 1's three geometry overrides are all optional and schema-legal."""
    check_body([table(frame="filled", fill_color="#E8F0FE", fill_inset=4, fill_height=30, label_inset_y=2)])


def test_rejects_table_divider_without_x_or_x_right() -> None:
    """A table divider naming neither `x` nor `x_right` is rejected."""
    reject_body([table(frame="bordered", dividers=[{"width": 1}])], "divider 0", "x_right")


def test_accepts_table_dividers_with_x_and_x_right() -> None:
    """Dividers positioned by `x` or by `x_right` validate."""
    check_body([table(frame="bordered", dividers=[{"x": 100}, {"x_right": -320}])])


def test_rejects_dividers_on_a_non_bordered_frame() -> None:
    """`dividers` only cuts the header and body into columns under `frame: bordered`."""
    reject_body([table(frame="ruled", dividers=[{"x": 100}])], "dividers", "bordered")


def test_rejects_fill_height_on_a_non_filled_frame() -> None:
    """A `fill_height` on a frame that draws no fill is rejected."""
    reject_body([table(frame="ruled", fill_height=30)], "fill_height", "'ruled'")


def test_rejects_fill_inset_on_a_non_filled_frame() -> None:
    """A `fill_inset` on a frame that draws no fill is rejected."""
    reject_body([table(frame="ruled", fill_inset=4)], "fill_inset", "'ruled'")


def test_accepts_fill_height_and_fill_inset_with_filled_frame() -> None:
    """`fill_height` and `fill_inset` are legal alongside `frame: filled`."""
    check_body([table(frame="filled", fill_color="#E8F0FE", fill_height=30, fill_inset=4)])


def test_rejects_group_gap_on_non_dedicated_row_grouping() -> None:
    """`group_gap` is only read by `grouping: dedicated_row`, so it is rejected elsewhere."""
    reject_body([table(grouping="inline", group_gap=10)], "group_gap", "dedicated_row")


def test_accepts_group_gap_with_dedicated_row_grouping() -> None:
    """`group_gap` validates alongside `grouping: dedicated_row`."""
    check_body([table(grouping="dedicated_row", group_gap=10)])


def test_rejects_synthetic_row_placement_on_non_dedicated_row_grouping() -> None:
    """`synthetic_row_placement` needs a group header to be relative to, so it is rejected without one."""
    reject_body([table(grouping="none", synthetic_row_placement="leading")], "synthetic_row_placement")


def test_rejects_unknown_synthetic_row_placement_value() -> None:
    """An unknown `synthetic_row_placement` value is rejected, listing the allowed values."""
    reject_body(
        [table(grouping="dedicated_row", synthetic_row_placement="sideways")],
        "sideways",
        "after_first_group_header",
    )


def test_accepts_synthetic_row_placement_after_first_group_header() -> None:
    """NAB's brought-forward placement validates under `grouping: dedicated_row`."""
    check_body([table(grouping="dedicated_row", synthetic_row_placement="after_first_group_header")])


def test_rejects_column_sub_line_without_key() -> None:
    """A column `sub_line` with no `key:` is rejected."""
    columns = [{"key": "desc", "label": "D", "x": 0, "sub_line": {"role": "small"}}]
    reject_body([table(columns=columns)], "sub_line", "key")


def test_accepts_column_sub_line_with_key() -> None:
    """A column `sub_line` naming a `key:` validates; only the key is required."""
    columns = [{"key": "desc", "label": "D", "x": 0, "sub_line": {"key": "reference"}}]
    check_body([table(columns=columns)])


def test_accepts_split_divider_keys() -> None:
    """A split's `divider` and `divider_color` keys validate."""
    check_body(
        [{"type": "split", "children": [[text()], [text()]], "divider": True, "divider_color": "grey"}]
    )


# --- split widths -----------------------------------------------------------------------------------------


def split(widths: object) -> dict:
    """Return a two-column split carrying the given `widths:`."""
    return {"type": "split", "children": [[text()], [text()]], "widths": widths}


def test_accepts_split_with_widths_matching_children_count() -> None:
    """A split whose `widths` has one positive int per child column validates."""
    check_body([split([300, 100])])


def test_rejects_split_widths_with_wrong_entry_count() -> None:
    """A `widths` list whose length differs from the column count is rejected."""
    reject_body([split([100, 100, 100])], "3 entries", "2 columns")


def test_rejects_split_widths_that_is_empty() -> None:
    """An empty `widths` list is rejected."""
    reject_body([split([])], "non-empty list")


def test_rejects_split_widths_with_a_non_positive_entry() -> None:
    """A zero or negative `widths` entry is rejected."""
    reject_body([split([0, 100])], "positive ints")
    reject_body([split([-5, 100])], "positive ints")


def test_rejects_split_widths_with_a_non_int_entry() -> None:
    """A non-int `widths` entry (a float, string or bool) is rejected."""
    for bad in (1.5, "100", True):
        reject_body([split([bad, 100])], "positive ints")


def test_accepts_table_capture_true_and_false() -> None:
    """A table's `capture:` validates as either bool."""
    check_body([table(capture=True)])
    check_body([table(capture=False)])


def test_rejects_table_capture_that_is_not_a_bool() -> None:
    """A `capture:` that is not a bool (a string, or an int) is rejected."""
    reject_body([table(capture="yes")], "capture must be a bool")
    reject_body([table(capture=1)], "capture must be a bool")


def test_accepts_the_invoice_table_typography_keys() -> None:
    """The typography keys an invoice table carries (role, family, line spacing, insets) validate."""
    block = table(
        role="body",
        family="carlito",
        cell_line_spacing="font",
        row_inset_y=3,
        header_bold=True,
        header_height=40,
        row_height=50,
        line_advance=30,
    )
    check_body([block])


def test_rejects_unknown_cell_line_spacing() -> None:
    """An unknown `cell_line_spacing` is rejected, listing the allowed values."""
    reject_body([table(cell_line_spacing="loose")], "loose", "row_height", "font")


def test_accepts_a_column_label_anchor_that_differs_from_its_cells() -> None:
    """A column's `label_x` and `label_align` may differ from its own `x` and `align`."""
    columns = [{"key": "price", "label": "Unit Price", "align": "right", "x": 900, "label_x": 700}]
    columns[0]["label_align"] = "left"
    check_body([table(columns=columns)])


def test_rejects_unknown_column_label_align() -> None:
    """A `label_align` outside the column alignments (including 'center') is rejected."""
    columns = [{"key": "price", "label": "P", "x": 0, "label_align": "center"}]
    reject_body([table(columns=columns)], "label_align", "center")


def test_rejects_non_int_column_label_x() -> None:
    """A non-int `label_x` is rejected."""
    columns = [{"key": "price", "label": "P", "x": 0, "label_x": "10"}]
    reject_body([table(columns=columns)], "label_x")


# --- line-advance coverage --------------------------------------------------------------------------------


def test_a_tables_own_role_is_what_the_line_advance_coverage_check_requires() -> None:
    """A table draws labels and cells at its own role and resolves that role's advance.

    Covering only the layout's default role is therefore not enough, and covering the table's role is.
    """
    body = [table(role="ledger")]
    reject_layout(make_layout(body), "ledger")
    check_layout(make_layout(body, defaults={"line_advance": {"ledger": 24}}))


# --- budgets against geometry -----------------------------------------------------------------------------


def budgeted_table(width: int, name: str = "DESC") -> dict:
    """Return a table whose first of three columns carries the named budget, with 200px before the next."""
    return table(
        columns=[
            {"key": "desc", "label": "D", "align": "left", "x": 0, "budget": name},
            {"key": "qty", "label": "Q", "align": "left", "x": 200},
            {"key": "amount", "label": "A", "align": "right", "x_right": 0},
        ]
    )


def test_column_budget_matching_its_geometry_is_accepted() -> None:
    """A column budget exactly as wide as the room before the next column validates."""
    check_layout(make_layout([budgeted_table(200)], budgets={"DESC": budget(200)}))


def test_column_budget_wider_than_its_geometry_is_rejected() -> None:
    """A column budget wider than the room before the next column is rejected."""
    reject_layout(make_layout([budgeted_table(201)], budgets={"DESC": budget(201)}), "201px", "200px")


def test_column_naming_a_missing_budget_is_rejected() -> None:
    """A column naming a budget the layout does not define is rejected."""
    reject_layout(make_layout([budgeted_table(0, "NOPE")], budgets={"DESC": budget(200)}), "NOPE")


def test_panel_padding_wider_than_the_page_is_rejected() -> None:
    """A panel whose padding leaves no inner width is rejected."""
    body = [{"type": "panel", "padding": 200, "children": [text()]}]
    reject_layout(make_layout(body, content_width=396), "padding 200")


def test_split_with_too_large_a_gap_is_rejected() -> None:
    """A split whose gap leaves its columns no width is rejected."""
    body = [{"type": "split", "gap": 400, "children": [[text()], [text()]]}]
    reject_layout(make_layout(body, content_width=396), "gap 400")


def test_split_widths_summing_over_the_region_is_rejected() -> None:
    """Widths that are well-formed but overflow the page are rejected by the geometry check.

    This is distinct from the body-level widths and children count check.
    """
    body = [{"type": "split", "gap": 0, "widths": [300, 200], "children": [[text()], [text()]]}]
    reject_layout(make_layout(body, content_width=396), "500px", "396px")


def test_split_widths_exactly_filling_the_region_is_accepted() -> None:
    """Widths plus gaps that exactly fill the region validate."""
    body = [{"type": "split", "gap": 10, "widths": [190, 196], "children": [[text()], [text()]]}]
    check_layout(make_layout(body, content_width=396))


def test_split_gap_comes_from_the_layout_default_when_the_block_omits_it() -> None:
    """`draw_split` resolves `gap:` against `defaults.split_gap`, and so must validation.

    Checking a gap-less split at 0 would pass widths the renderer then cannot fit, and the overflow would
    surface as a wrong-looking page rather than a validate-time error naming the block.
    """
    body = [{"type": "split", "widths": [200, 196], "children": [[text()], [text()]]}]
    check_layout(make_layout(body, content_width=396, defaults={"split_gap": 0}))
    reject_layout(make_layout(body, content_width=396, defaults={"split_gap": 100}), "gap 100", "496px")


def test_panel_padding_comes_from_the_layout_default_when_the_block_omits_it() -> None:
    """The panel counterpart: `draw_panel` resolves `padding:` against `defaults.panel_padding`.

    A padding-less panel in a layout declaring a large one must be checked at that padding, not at 0.
    """
    body = [{"type": "panel", "children": [text(budget="WIDE")]}]
    budgets = {"WIDE": budget(300)}
    check_layout(make_layout(body, budgets=budgets, defaults={"panel_padding": 0}))
    reject_layout(make_layout(body, budgets=budgets, defaults={"panel_padding": 100}), "WIDE", "196px")


def test_budgeted_table_nested_in_a_split_with_widths_uses_the_explicit_column_width() -> None:
    """A budget that fits equal division but not the fixed-width totals column must be rejected.

    On a 1600px page, equal division gives 800px columns; the explicit widths give the second column 400px.
    """
    columns = [{"key": "amount", "label": "A", "align": "left", "x": 0, "budget": "TOTALS"}]
    inner = table(columns=columns)
    body = [{"type": "split", "gap": 0, "widths": [1200, 400], "children": [[text()], [inner]]}]
    budgets = {"TOTALS": budget(600)}
    reject_layout(make_layout(body, budgets=budgets, content_width=1600), "TOTALS", "400px")
    equal = [{"type": "split", "gap": 0, "children": [[text()], [inner]]}]
    check_layout(make_layout(equal, budgets=budgets, content_width=1600))


def test_validate_layout_rejects_a_layout_with_no_body_key() -> None:
    """`validate_layout` rejects a layout lacking `body:`."""
    layout = make_layout([text()])
    del layout["body"]
    reject_layout(layout, "no 'body' key")


def test_validate_layout_rejects_a_layout_with_no_content_width_key() -> None:
    """`validate_layout` rejects a layout lacking `content_width:`."""
    layout = make_layout([text()])
    del layout["content_width"]
    reject_layout(layout, "no 'content_width' key")


def test_budgeted_table_nested_in_a_panel_uses_the_narrowed_width() -> None:
    """A column budget inside a panel is checked against the panel's inner width, not the page's."""
    columns = [{"key": "desc", "label": "D", "align": "left", "x": 0, "budget": "DESC"}]
    body = [{"type": "panel", "padding": 100, "children": [table(columns=columns)]}]
    reject_layout(make_layout(body, budgets={"DESC": budget(300)}, content_width=396), "DESC", "196px")
    check_layout(make_layout(body, budgets={"DESC": budget(196)}, content_width=396))


# --- text and pair budgets --------------------------------------------------------------------------------


def test_text_budget_matching_its_region_is_accepted() -> None:
    """A text budget exactly as wide as its region validates."""
    check_layout(make_layout([text(budget="B")], budgets={"B": budget(396)}, content_width=396))


def test_text_budget_wider_than_its_region_is_rejected() -> None:
    """A text budget wider than its region is rejected."""
    reject_layout(make_layout([text(budget="B")], budgets={"B": budget(397)}, content_width=396), "397px")


def test_text_budget_naming_a_missing_budget_is_rejected() -> None:
    """A text block naming an undefined budget is rejected."""
    reject_layout(make_layout([text(budget="NOPE")], budgets={"B": budget(10)}), "NOPE")


def test_pair_budget_matching_its_region_is_accepted() -> None:
    """A pair budget exactly as wide as its region validates."""
    check_layout(make_layout([pair(budget="B")], budgets={"B": budget(396)}, content_width=396))


def test_pair_budget_wider_than_its_region_is_rejected() -> None:
    """A pair budget wider than its region is rejected."""
    reject_layout(make_layout([pair(budget="B")], budgets={"B": budget(397)}, content_width=396), "397px")


def test_accepts_pair_value_align_and_min_gap_keys() -> None:
    """A pair's `value_align` and `min_gap` keys validate."""
    check_body([pair(value_align="right", min_gap=12)])


def test_rejects_unknown_pair_value_align() -> None:
    """An unknown `value_align` is rejected, listing the allowed values."""
    reject_body([pair(value_align="middle")], "middle", "right")


def test_accepts_rule_fill_char_key() -> None:
    """A rule's `fill_char` key validates."""
    check_body([{"type": "rule", "fill_char": "="}])


def test_budgeted_right_aligned_pair_with_a_min_gap_is_rejected() -> None:
    """`draw_pair`'s budgeted path draws the label first and fits the value into what is left.

    It never repositions the label, so a declared `min_gap` is dropped with nothing on the page to show it.
    The combination was a written constraint while the primitive was built; this makes it enforceable.
    """
    block = pair(budget="B", value_align="right", min_gap=10)
    reject_layout(make_layout([block], budgets={"B": budget(300)}), "min_gap", "10")


def test_budgeted_right_aligned_pair_with_a_zero_min_gap_is_accepted() -> None:
    """Zero is the resolved value every shipped layout carries, and asks for nothing the path cannot give."""
    block = pair(budget="B", value_align="right", min_gap=0)
    check_layout(make_layout([block], budgets={"B": budget(300)}))


def test_budgeted_right_aligned_pair_rejects_a_block_level_min_gap_too() -> None:
    """A block's own `min_gap` is as unhonoured as a layout-level one."""
    block = pair(budget="B", min_gap=8)
    layout = make_layout([block], budgets={"B": budget(300)}, defaults={"pair_value_align": "right"})
    reject_layout(layout, "min_gap", "8")
    layout_level = make_layout(
        [pair(budget="B", value_align="right")],
        budgets={"B": budget(300)},
        defaults={"pair_min_gap": 8},
    )
    reject_layout(layout_level, "min_gap", "8")


def test_unbudgeted_right_aligned_pair_may_still_carry_a_min_gap() -> None:
    """The live invoice case has no budget, so `min_gap` does reposition the label and must stay legal."""
    check_layout(make_layout([pair(value_align="right", min_gap=40)]))


def test_left_aligned_pair_budget_is_still_checked_against_the_full_width() -> None:
    """With `pair_value_align: left` (the default) the check is unnarrowed, and `min_gap` plays no part."""
    block = pair(budget="B", value_align="left", min_gap=50)
    check_layout(make_layout([block], budgets={"B": budget(396)}, content_width=396))
    reject_layout(make_layout([block], budgets={"B": budget(397)}, content_width=396), "397px")


def test_text_budget_nested_in_a_panel_uses_the_narrowed_width() -> None:
    """A text budget inside a panel is checked against the panel's inner width."""
    body = [{"type": "panel", "padding": 100, "children": [text(budget="B")]}]
    reject_layout(make_layout(body, budgets={"B": budget(300)}, content_width=396), "300px", "196px")
    check_layout(make_layout(body, budgets={"B": budget(196)}, content_width=396))


def test_text_without_budget_is_unaffected_by_budget_validation() -> None:
    """A text block carrying no `budget:` validates whatever the layout's budgets hold."""
    check_layout(make_layout([text()], budgets={"UNUSED": budget(99999)}))
    check_layout(make_layout([text()], budgets={}))


# --- banner -----------------------------------------------------------------------------------------------


def test_accepts_a_minimal_banner_block() -> None:
    """A banner with its required keys and one content source validates."""
    check_body([banner()])


def test_banner_requires_content_height_and_color() -> None:
    """A banner without `height` or without `color` is rejected as missing a required key.

    (adjusted: the banner's height key is `height`, not `content_height`.)
    """
    no_height = banner()
    del no_height["height"]
    reject_body([no_height], "missing required", "height")
    no_color = banner()
    del no_color["color"]
    reject_body([no_color], "missing required", "color")


def test_banner_rejects_unknown_field_reference() -> None:
    """A banner's `{FIELD}` content naming no known field is rejected."""
    reject_body([banner(content="{NO_SUCH_FIELD}")], "NO_SUCH_FIELD")


def test_banner_accepts_its_optional_keys() -> None:
    """A banner's optional keys all validate together."""
    check_body([banner(text_color="white", role="body", text_y=4, bold=True, family="carlito")])


# --- header rule ------------------------------------------------------------------------------------------


def test_accepts_header_rule_top_and_header_rule_gap_with_ruled_frame() -> None:
    """`header_rule_top` and `header_rule_gap` validate under `frame: ruled`."""
    check_body([table(frame="ruled", header_rule_top=False, header_rule_gap=4)])


def test_rejects_header_rule_top_on_a_non_ruled_frame() -> None:
    """A `header_rule_top` on a frame that draws no header rule is rejected."""
    reject_body([table(frame="plain", header_rule_top=True)], "header_rule_top", "'plain'")


def test_rejects_header_rule_gap_on_a_non_ruled_frame() -> None:
    """A `header_rule_gap` on a frame that draws no header rule is rejected."""
    reject_body([table(frame="bordered", header_rule_gap=4)], "header_rule_gap", "'bordered'")


# --- line_advance -----------------------------------------------------------------------------------------


def test_line_advance_as_a_single_number_is_rejected() -> None:
    """The natural mistake for a one-font-size document, `line_advance: 20`, fails at validate time.

    It must not silently give every role the same flat advance at render time.
    """
    layout = make_layout([text()], defaults={"line_advance": 20})
    reject_layout(layout, "single number", "defaults.line_advance")


def test_line_advance_missing_a_used_role_is_rejected() -> None:
    """A body role the layout's `defaults.line_advance` does not cover is rejected."""
    reject_layout(make_layout([text(role="heading")]), "heading", "defaults.line_advance")


def test_line_advance_missing_role_inside_a_sub_line_is_rejected() -> None:
    """A role buried in a table column's `sub_line` must not be invisible to a top-level-only role scan.

    `bank_statements.yml`'s real `sub_line: {role: sub_description}` is the live example.
    """
    columns = [{"key": "desc", "label": "D", "x": 0, "sub_line": {"key": "ref", "role": "sub_description"}}]
    reject_layout(make_layout([table(columns=columns)]), "sub_description")


def test_line_advance_role_inside_a_container_is_reached() -> None:
    """A role nested inside a panel or split is still caught, as it is by every other structural check."""
    in_panel = [{"type": "panel", "children": [text(role="deep")]}]
    reject_layout(make_layout(in_panel), "deep")
    in_split = [{"type": "split", "children": [[text()], [text(role="deeper")]]}]
    reject_layout(make_layout(in_split), "deeper")


def test_line_advance_block_override_exempts_that_role_from_coverage() -> None:
    """A block's own bare-integer `line_advance:` means its role is never looked up in the layout's mapping.

    `validate_layout` must therefore not demand the layout cover it either.
    """
    check_layout(make_layout([text(role="override_only", line_advance=30)]))


def test_bank_layouts_pass_the_line_advance_checks() -> None:
    """Every shipped bank layout validates clean under both line-advance checks.

    The end-to-end version lives in tests/test_bank_dsl_validation.py; this pins it to `line_advance`.
    """
    layouts = load_layout_registry(LAYOUT_DIR / "bank_statements.yml")
    known = field_names_for("bank_statements")
    assert layouts
    for layout_id, layout in layouts.items():
        assert isinstance(layout["defaults"]["line_advance"], dict), layout_id
        used = _line_advance_roles(layout["body"], default_role=str(layout["defaults"]["role"]))
        assert used <= set(layout["defaults"]["line_advance"]), layout_id
        validate_layout(
            layout,
            layout_id=layout_id,
            layout_path="bank_statements.yml",
            known_fields=known,
            doc_type="bank_statements",
        )


# --- field providers --------------------------------------------------------------------------------------


def test_validate_layout_rejects_a_layout_with_no_field_providers_key() -> None:
    """`field_providers` is required: a layout that derives nothing must say `field_providers: []`.

    `validate_layout` is called directly, bypassing any auto-default, on a layout that carries `content_width`,
    `defaults`, `field_budgets` and `body` but deliberately no `field_providers` key.
    """
    layout = make_layout([text()], budgets={"B": budget(396)})
    del layout["field_providers"]
    reject_layout(layout, "no 'field_providers' key")


def test_field_providers_entry_naming_an_unregistered_provider_is_rejected() -> None:
    """A `field_providers:` entry naming an unregistered provider is rejected, listing the registered ones."""
    layout = make_layout([text()], providers=[{"name": "no_such_provider"}])
    reject_layout(layout, "no_such_provider", "receipt_pos")


def test_field_providers_entry_with_an_unknown_param_key_is_rejected() -> None:
    """A `params:` key the provider never declared is rejected."""
    layout = make_layout([text()], providers=[{"name": "receipt_pos", "params": {"bogus": 1}}])
    reject_layout(layout, "bogus", "receipt_pos")


def test_field_providers_entry_accepts_its_declared_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `params:` key the provider declares is accepted.

    No shipped field provider declares a param, so a throwaway one is registered for this test only.
    """

    def configurable(entry: dict, params: dict) -> dict[str, str]:
        return {"ZZ_TEST_VALUE": str(params["mode"])}

    monkeypatch.setitem(provider_registry._REGISTRY, "zz_configurable", configurable)
    monkeypatch.setitem(provider_registry._PARAM_KEYS, "zz_configurable", frozenset({"mode"}))
    monkeypatch.setitem(provider_registry._EMITS, "zz_configurable", ("ZZ_TEST_VALUE",))
    ok = make_layout([text()], providers=[{"name": "zz_configurable", "params": {"mode": "x"}}])
    check_layout(ok)
    typo = make_layout([text()], providers=[{"name": "zz_configurable", "params": {"mdoe": "x"}}])
    reject_layout(typo, "mdoe")


def test_a_referenced_providers_emits_resolve_as_known_fields() -> None:
    """A `{FIELD}` naming a value this layout's own `field_providers:` derives must resolve.

    `known_fields` is widened by the provider's emits.
    """
    body = [text("{POS_TIME}")]
    reject_layout(make_layout(body), "POS_TIME")
    check_layout(make_layout(body, providers=[{"name": "receipt_pos"}]))


def test_field_providers_declaring_overlapping_emits_is_rejected_at_validate_time() -> None:
    """Two providers on one layout declaring the same emits name fail here, before any provider is called.

    The check runs from the `emits=` declarations alone, so a receipt layout combining providers cannot ship
    a page where one provider's value overwrites another's with no error. `apply_field_providers` keeps a
    second, defensive check at merge time (see tests/layout_dsl/test_field_providers.py).
    """
    layout = make_layout([text()], providers=[{"name": "receipt_pos"}, {"name": "receipt_pos"}])
    reject_layout(layout, "collide", "POS_TIME", "field_providers[1]")


def test_an_unreferenced_providers_emits_do_not_resolve_as_known_fields() -> None:
    """A provider's emits widen `known_fields` only for a layout that declares that provider.

    Widening them globally would let a typo that coincidentally matches another layout's derived field name
    silently pass.
    """
    layout = make_layout([text("{SUBTOTAL_AMOUNT}")], providers=[{"name": "receipt_pos"}])
    reject_layout(layout, "SUBTOTAL_AMOUNT")


# --- pair currency and column prefix ----------------------------------------------------------------------


def test_pair_accepts_the_two_currency_styles() -> None:
    """A pair's `currency:` validates as 'symbol' and as 'plain'."""
    check_body([pair(currency="symbol")])
    check_body([pair(currency="plain")])


def test_pair_rejects_an_unknown_currency_style() -> None:
    """An unknown pair `currency:` is rejected, listing the allowed styles."""
    reject_body([pair(currency="euro")], "euro", "symbol", "plain")


def prefixed_column(**extra) -> dict:
    """Return a left-aligned, budgeted column carrying both prefix keys."""
    column = {
        "key": "desc",
        "label": "D",
        "align": "left",
        "x": 0,
        "budget": "DESC",
        "prefix_key": "quantity_prefix",
        "prefix_field": "LINE_ITEM_QUANTITIES",
    }
    column.update(extra)
    return column


def test_prefix_keys_are_accepted_on_a_left_budgeted_column() -> None:
    """`prefix_key` and `prefix_field` together validate on a left-aligned budgeted column."""
    check_body([table(columns=[prefixed_column()])])


def test_prefix_key_without_prefix_field_is_rejected() -> None:
    """`prefix_key` alone is rejected."""
    column = prefixed_column()
    del column["prefix_field"]
    reject_body([table(columns=[column])], "prefix_key", "prefix_field")


def test_prefix_field_without_prefix_key_is_rejected() -> None:
    """`prefix_field` alone is rejected."""
    column = prefixed_column()
    del column["prefix_key"]
    reject_body([table(columns=[column])], "prefix_field", "prefix_key")


def test_prefix_keys_are_rejected_on_an_unbudgeted_column() -> None:
    """Only `draw_fitted_left` measures a prefix sub-box.

    An unbudgeted cell draws through `draw_text_left`, which would silently record nothing.
    """
    column = prefixed_column()
    del column["budget"]
    reject_body([table(columns=[column])], "unbudgeted")


def test_prefix_keys_are_rejected_on_a_right_aligned_column() -> None:
    """A right-aligned column has no left-budgeted draw path, so the prefix keys are rejected."""
    reject_body([table(columns=[prefixed_column(align="right")])], "right-aligned")


def test_prefix_field_must_name_a_known_field() -> None:
    """A `prefix_field` naming no known field is rejected."""
    reject_body([table(columns=[prefixed_column(prefix_field="NO_SUCH_FIELD")])], "NO_SUCH_FIELD")


# --- unit sub-line spans and params_from_layout ---------------------------------------------------------


def unit_table(sub_line: dict | None = None, **extra) -> dict:
    """Return a receipt line-items table whose description carries a span-recording sub-line."""
    spec = {
        "key": "unit_line",
        "budget": "UNIT",
        "span_fields": {
            "key": "unit_line_spans",
            # TOTAL_AMOUNT stands in for the price field: this module's known set is a small fixed one.
            "fields": {"quantity": "LINE_ITEM_QUANTITIES", "price": "TOTAL_AMOUNT"},
        },
    }
    if sub_line is not None:
        spec = sub_line
    block = table(
        rows="receipt_line_items",
        params={"fields": {"description": "LINE_ITEM_DESCRIPTIONS", "quantity": "LINE_ITEM_QUANTITIES"}},
        params_from_layout={"unit_line_format": "unit_line_format"},
        columns=[
            {
                "key": "description",
                "label": "D",
                "align": "left",
                "x": 0,
                "budget": "DESC",
                "sub_line": spec,
            },
            {"key": "total", "label": "A", "align": "right", "x_right": 0},
        ],
    )
    block.update(extra)
    return block


def unit_layout(block: dict, *, unit: dict | None = None, **extra) -> dict:
    """A layout carrying the unit table, its budgets and the unit-line wording."""
    return make_layout(
        [block],
        budgets={"DESC": budget(200), "UNIT": unit if unit is not None else budget(200)},
        unit_line_format="  {quantity} @ ${price}",
        **extra,
    )


def test_a_span_recording_sub_line_validates() -> None:
    """The receipt unit-line table, with its budget and layout-read format, validates."""
    check_layout(unit_layout(unit_table()))


def test_span_fields_without_a_budget_is_rejected() -> None:
    """Spans are measured on one fitted line, so a span sub-line must be budgeted."""
    spec = unit_table()["columns"][0]["sub_line"]
    del spec["budget"]
    reject_body([unit_table(spec)], "without a budget")


@pytest.mark.parametrize("spans", [{"key": "s"}, {"fields": {"q": "LINE_ITEM_QUANTITIES"}}, "s"])
def test_malformed_span_fields_is_rejected(spans: object) -> None:
    """span_fields needs both a row key and a non-empty field mapping."""
    reject_body([unit_table({"key": "unit_line", "budget": "UNIT", "span_fields": spans})], "span_fields")


def test_span_field_must_name_a_known_field() -> None:
    """A span bound to no known field is rejected."""
    spec = {"key": "unit_line", "budget": "UNIT", "span_fields": {"key": "s", "fields": {"q": "NO_SUCH"}}}
    reject_body([unit_table(spec)], "NO_SUCH")


def test_a_multi_line_sub_line_budget_is_rejected() -> None:
    """A sub-line budget must be a single-line shrink fit."""
    unit = {"width": 200, "fit": "wrap", "min_font": 10, "max_lines": 2}
    reject_layout(unit_layout(unit_table(), unit=unit), "UNIT", "max_lines: 1")


def test_a_sub_line_budget_wider_than_its_room_is_rejected() -> None:
    """A sub-line budget wider than the room before the next column is rejected."""
    reject_layout(unit_layout(unit_table(), unit=budget(5000)), "UNIT", "5000")


def test_an_undefined_sub_line_budget_is_rejected() -> None:
    """A sub-line naming a budget the layout lacks is rejected."""
    layout = unit_layout(unit_table())
    del layout["field_budgets"]["UNIT"]
    reject_layout(layout, "UNIT", "does not define")


def test_params_from_layout_naming_a_missing_key_is_rejected() -> None:
    """A param read off a layout key the layout lacks is rejected."""
    layout = unit_layout(unit_table(params_from_layout={"unit_line_format": "no_such_key"}))
    reject_layout(layout, "no_such_key")


def test_params_from_layout_must_name_a_provider_param() -> None:
    """A param the provider does not accept is rejected, wherever it is set."""
    reject_body([unit_table(params_from_layout={"bogus": "unit_line_format"})], "bogus")


def test_a_param_set_twice_is_rejected() -> None:
    """A param in both params and params_from_layout is rejected."""
    block = unit_table()
    block["params"]["unit_line_format"] = "{quantity} @ {price}"
    reject_body([block], "unit_line_format", "both")


def test_a_layout_key_read_only_through_params_from_layout_is_known() -> None:
    """unit_line_format is a known layout key because the table reads it; unread, it is refused."""
    check_layout(unit_layout(unit_table()))
    reject_layout(make_layout([text()], unit_line_format="x"), "unit_line_format")


# --- layout keys nothing reads ----------------------------------------------------------------------------


def test_layout_carries_no_key_the_engine_never_reads() -> None:
    """No shipped layout carries a top-level key that nothing reads.

    The invoice YAML once shipped six-column table specs, `table_start_y` and per-section font sizes that no
    code path read. A layout key that does nothing is worse than no key: it tells an operator the document is
    configured a way it is not.
    """
    checked = 0
    for name in ("bank_statements", "receipts", "invoices"):
        for layout_id, layout in load_layout_registry(LAYOUT_DIR / f"{name}.yml").items():
            if "body" not in layout:
                continue
            checked += 1
            assert set(layout) <= known_layout_keys(layout), (
                f"{name}.yml {layout_id}: {sorted(set(layout) - known_layout_keys(layout))}"
            )
    assert checked > 0


def test_known_layout_keys_admits_a_from_layout_target() -> None:
    """`from_layout:` names an arbitrary layout key, so the permitted set grows with the body."""
    layout = make_layout([{"type": "text", "from_layout": "tagline_text"}])
    assert "tagline_text" in known_layout_keys(layout)
    assert "tagline_text" not in known_layout_keys(make_layout([text()]))


def test_known_layout_keys_admits_a_suppress_if_equals_target() -> None:
    """A `suppress_if_equals:` target is admitted too."""
    layout = make_layout(
        [{"type": "text", "from_layout": "tagline_text", "suppress_if_equals": "blank_marker"}]
    )
    assert {"tagline_text", "blank_marker"} <= known_layout_keys(layout)


def test_known_layout_keys_reaches_into_panel_and_split_children() -> None:
    """A `from_layout:` buried in a nested panel or split column is not invisible to the walk."""
    in_panel = [{"type": "panel", "children": [{"type": "text", "from_layout": "panel_key"}]}]
    assert "panel_key" in known_layout_keys(make_layout(in_panel))
    in_split = [{"type": "split", "children": [[text()], [{"type": "text", "from_layout": "split_key"}]]}]
    assert "split_key" in known_layout_keys(make_layout(in_split))


def test_validate_layout_rejects_a_key_nothing_reads() -> None:
    """A top-level layout key that no code path reads is rejected, and named."""
    layout = make_layout([text()], minimum_amount=10000)
    reject_layout(layout, "minimum_amount", "no code path reads")


def test_validate_layout_accepts_a_key_the_body_names_via_from_layout() -> None:
    """A top-level key the body names through `from_layout:` is read, so it is accepted."""
    layout = make_layout([{"type": "text", "from_layout": "tagline_text"}], tagline_text="Hello")
    check_layout(copy.deepcopy(layout))
