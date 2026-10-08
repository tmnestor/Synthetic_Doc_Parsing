"""Text-bearing primitives (text, pair, block, rule, spacer, banner) resolve their parameters from the layout.

Every test draws through the real primitives onto a real PIL canvas against a real receipt layout (its
`defaults:` and `field_budgets:` are the real ones; only the role table, line advances and a few budgets are
widened so each behaviour has something to discriminate). Pixel claims are proven by comparing the canvas with
one the test draws by hand, and recorded-box claims by reading the `BoxRecorder` back.
"""

import pytest
from PIL import Image, ImageChops, ImageDraw

from generators.common import Font, draw_separator, fit_text, font_path, load_font
from generators.exporters.geometry import BoxRecorder
from generators.layout_budgets import LayoutBudgetError
from generators.layout_dsl.context import Region
from generators.layout_dsl.defaults import DefaultsError
from generators.layout_dsl.primitives_text import (
    CurrencyError,
    RoleError,
    draw_banner,
    draw_block,
    draw_pair,
    draw_rule,
    draw_spacer,
    draw_text_block,
    font_for,
    format_currency,
    line_advance,
    resolve_role,
)
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import (
    ENTRY,
    LAYOUT_ID,
    MARGIN,
    PAGE_WIDTH,
    RECEIPTS_PATH,
    make_drawing_ctx,
    real_layout,
)

LAYOUT_PATH = str(RECEIPTS_PATH)
MONO = "liberation_mono"
PAGE_HEIGHT = 600
REGION = Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN)
# Invented values; none appear in ground_truth/.
NAME = "Jarrah Quill"
LONG_NAME = "Jarrah Quill Wholesale Hardware and Building Supplies"
VALUE_BUDGET: dict[str, int | str] = {"width": 260, "fit": "wrap", "min_font": 18, "max_lines": 3}
NARROW_BUDGET: dict[str, int | str] = {"width": 150, "fit": "shrink", "min_font": 10, "max_lines": 1}


def text_layout(**defaults) -> dict:
    """Return a real receipt layout widened with header/footer roles, extra budgets and `defaults` overrides."""
    layout = real_layout([])
    layout["font_sizes"] = {"body": 18, "header": 32, "footer": 12}
    layout["page_dimensions"] = {"width": PAGE_WIDTH, "height": PAGE_HEIGHT}
    layout["defaults"]["line_advance"] = {"body": 20, "header": 45, "footer": 16}
    layout["defaults"]["rule_fill_char"] = "none"
    layout["defaults"].update(defaults)
    layout["field_budgets"]["VALUE_WRAP"] = dict(VALUE_BUDGET)
    layout["field_budgets"]["NARROW"] = dict(NARROW_BUDGET)
    return layout


def entry_with(**fields: str) -> dict:
    """Return an entry carrying exactly `fields`."""
    return {"case_id": "CASE999", "layout": LAYOUT_ID, "fields": dict(fields)}


def mono(size: int = 18, *, bold: bool = False) -> Font:
    """Load the monospace face the real receipt layout declares."""
    return load_font(size, family=MONO, bold=bold)


def blank() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Return a blank page and a surface to draw the expected picture on."""
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    return image, ImageDraw.Draw(image)


def same(a: Image.Image, b: Image.Image) -> bool:
    """Report whether two images are pixel-identical."""
    return ImageChops.difference(a, b).getbbox() is None


def ink_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the bounding box of everything that is not white."""
    return ImageChops.invert(image.convert("L")).getbbox()


def text_width(text: str, font: Font) -> int:
    """Return a string's ink width the way the primitives measure it."""
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])


def box_px(
    recorder: BoxRecorder, field: str, width: int = PAGE_WIDTH, height: int = PAGE_HEIGHT
) -> list[int]:
    """Return a recorded box back in pixels."""
    left, top, right, bottom = recorder.as_dict()[field]
    return [round(left * width), round(top * height), round(right * width), round(bottom * height)]


def block_context(layout: dict, entry: dict | None = None, **kwargs):
    """Return a (context, image) pair for `layout` with the engine's walker injected."""
    return make_drawing_ctx(layout, entry, **kwargs)


# --- roles and line advance --------------------------------------------------------------------------------


def test_resolve_role_returns_font_size() -> None:
    """A role maps to the integer font size the layout declares."""
    assert resolve_role(text_layout(), "header") == 32


def test_resolve_role_fails_fast_on_unknown_role() -> None:
    """An undefined role raises a four-element diagnostic naming the available roles."""
    with pytest.raises(RoleError) as exc_info:
        resolve_role(text_layout(), "nonsense")
    assert_diagnostic_error(exc_info.value)
    assert "nonsense" in str(exc_info.value)
    assert "body" in str(exc_info.value)


def test_text_advances_y() -> None:
    """A text block advances the cursor by its role's line advance."""
    ctx, _ = block_context(text_layout())
    assert draw_text_block({"type": "text", "content": NAME}, ctx, 100) == 120


def test_line_advance_resolves_by_the_blocks_own_role() -> None:
    """A layout's line_advance is a role -> pixels mapping, not one flat number.

    A header-role block and a footer-role block need different advances, since each was historically
    int(that role's own font size * 1.4); a flat per-layout number cannot express both at once.
    """
    layout = text_layout()
    kwargs = {"layout_id": LAYOUT_ID, "layout_path": LAYOUT_PATH}
    assert line_advance(layout, {"role": "header"}, **kwargs) == 45
    assert line_advance(layout, {"role": "footer"}, **kwargs) == 16
    assert line_advance(layout, {}, **kwargs) == 20


def test_line_advance_missing_role_raises_a_four_element_diagnostic() -> None:
    """A role present in font_sizes but absent from defaults.line_advance fails fast.

    It must not silently fall back to some other role's advance.
    """
    layout = text_layout()
    layout["font_sizes"]["caption"] = 11
    with pytest.raises(DefaultsError) as exc_info:
        line_advance(layout, {"role": "caption"}, layout_id=LAYOUT_ID, layout_path=LAYOUT_PATH)
    assert_diagnostic_error(exc_info.value)
    assert "caption" in str(exc_info.value)


def test_line_advance_block_override_wins_over_the_layout_default() -> None:
    """A block's own bare-integer line_advance wins outright, without any role lookup.

    It is the escape hatch for the rare line that is not simply 'this role's usual advance': the role here
    is absent from the layout's mapping and still resolves.
    """
    layout = text_layout()
    layout["font_sizes"]["caption"] = 11
    kwargs = {"layout_id": LAYOUT_ID, "layout_path": LAYOUT_PATH}
    assert line_advance(layout, {"role": "caption", "line_advance": 7}, **kwargs) == 7
    assert line_advance(layout, {"role": "header", "line_advance": 7}, **kwargs) == 7


def test_line_advance_comes_from_the_layout_not_a_1_4_ratio() -> None:
    """Receipts declare `line_advance: {body: 20}` against an 18pt body font.

    That is a ratio of 1.11, not the 1.4 the old `line_height(size)` hardcoded. If draw_text_block silently
    reverted to that ratio, this would advance to 100 + int(18 * 1.4) == 125, not the declared 120.
    """
    ctx, _ = block_context(text_layout())
    end = draw_text_block({"type": "text", "content": NAME}, ctx, 100)
    assert end == 120
    assert end != 100 + int(18 * 1.4)


# --- fonts -------------------------------------------------------------------------------------------------


def test_font_for_resolves_mono_from_the_layout_default() -> None:
    """The layout's `defaults.family` picks the vendored face."""
    font = font_for(text_layout(), {}, 18, layout_id=LAYOUT_ID, layout_path=LAYOUT_PATH)
    assert font.path == str(font_path(MONO))  # type: ignore[union-attr]


def test_font_for_block_override_wins_over_the_layout_default() -> None:
    """A block's own `family` beats the layout default."""
    font = font_for(
        text_layout(), {"family": "carlito"}, 18, bold=True, layout_id=LAYOUT_ID, layout_path=LAYOUT_PATH
    )
    assert font.path == str(font_path("carlito", bold=True))  # type: ignore[union-attr]


def test_monospace_is_honoured_end_to_end() -> None:
    """A layout declaring a monospace family really renders the monospace face.

    Every load_font call in this module used to omit the face, so a layout declaring `mono: true` still
    silently rendered the sans face. Proven by an actual pixel difference, not just that a flag was threaded.
    """
    block = {"type": "text", "content": "Total Items 100"}
    ctx_mono, mono_image = block_context(text_layout())
    ctx_sans, sans_image = block_context(text_layout(family="carlito"))
    draw_text_block(block, ctx_mono, 10)
    draw_text_block(block, ctx_sans, 10)
    assert not same(mono_image, sans_image)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 10), "Total Items 100", font=mono(), fill="black")
    assert same(mono_image, expected)


# --- text --------------------------------------------------------------------------------------------------


def test_text_records_geometry_when_field_given() -> None:
    """A text block with `field:` records the ink extent of what it drew."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    draw_text_block({"type": "text", "content": NAME, "field": "PAYER_NAME"}, ctx, 50)
    left, top, right, bottom = box_px(recorder, "PAYER_NAME")
    font = mono()
    bbox = font.getbbox(NAME)
    assert (left, top) == (MARGIN, 50)
    assert right == MARGIN + text_width(NAME, font)
    assert bottom == 50 + int(bbox[3] - bbox[1])


def test_text_without_field_records_nothing() -> None:
    """A text block with no `field:` leaves the recorder empty."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    draw_text_block({"type": "text", "content": NAME}, ctx, 50)
    assert recorder.as_dict() == {}


def test_text_bold_uses_the_bold_font() -> None:
    """Westpac's brand header and section titles are bold in the legacy renderer.

    `bold` is opt-in and defaults to false, so it must not change advance or plain-text rendering for every
    other consumer of `text`.
    """
    layout = text_layout(family="carlito")
    ctx_bold, bold_image = block_context(layout)
    ctx_plain, plain_image = block_context(layout)
    bold_end = draw_text_block({"type": "text", "content": NAME, "bold": True}, ctx_bold, 10)
    plain_end = draw_text_block({"type": "text", "content": NAME}, ctx_plain, 10)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 10), NAME, font=load_font(18, family="carlito", bold=True), fill="black")
    assert same(bold_image, expected)
    assert not same(bold_image, plain_image)
    assert bold_end == plain_end == 30


# --- budgeted text -----------------------------------------------------------------------------------------


def test_budgeted_text_wraps_and_advances_by_the_wrapped_height() -> None:
    """shrink_then_wrap with max_lines 2 puts a long supplier name on two lines.

    The cursor must advance by both, or every block below it overlaps.
    """
    layout = text_layout()
    budget = layout["field_budgets"]["SUPPLIER_NAME"]
    fitted = fit_text(
        LONG_NAME,
        width=budget["width"],
        fit=budget["fit"],
        min_font=budget["min_font"],
        max_lines=budget["max_lines"],
        nominal_size=18,
        family=MONO,
    )
    assert len(fitted.lines) == 2
    ctx, image = block_context(layout)
    end = draw_text_block({"type": "text", "content": LONG_NAME, "budget": "SUPPLIER_NAME"}, ctx, 100)
    assert end == 100 + 2 * 20
    ink = ink_box(image)
    assert ink is not None
    assert ink[1] >= 100
    assert ink[3] > 100 + 20


def test_budget_names_an_undeclared_field_budget() -> None:
    """A `budget:` naming a budget the layout does not declare fails with a four-element diagnostic."""
    ctx, _ = block_context(text_layout())
    with pytest.raises(LayoutBudgetError) as exc_info:
        draw_text_block({"type": "text", "content": NAME, "budget": "NO_SUCH_BUDGET"}, ctx, 0)
    assert_diagnostic_error(exc_info.value)
    assert "NO_SUCH_BUDGET" in str(exc_info.value)


def test_budgeted_text_centers_within_the_full_page_width_not_the_region() -> None:
    """draw_fitted_center centres in a canvas width, not a region.

    Receipts centre within the full page width (the legacy `width` argument), not the margin-inset region.
    Here the region is x=100, width=1600 inside an 1800px page: a symmetric 100px margin each side.
    `region.x * 2 + region.width` reconstructs that 1800px page width. (100 + (1600 - w) // 2 equals
    (1800 - w) // 2 by coincidence of the symmetric margin; the left and right tests below are the ones that
    discriminate budgeted from unbudgeted rendering.)
    """
    layout = text_layout()
    layout["field_budgets"]["SUPPLIER_NAME"]["width"] = 1600
    recorder = BoxRecorder(1800, PAGE_HEIGHT)
    ctx, _ = make_drawing_ctx(layout, region=Region(x=100, width=1600), page_width=1800, recorder=recorder)
    block = {"type": "text", "content": NAME, "align": "center", "budget": "SUPPLIER_NAME", "field": "F"}
    draw_text_block(block, ctx, 20)
    left, _, right, _ = box_px(recorder, "F", width=1800)
    width = text_width(NAME, mono())
    assert left == (1800 - width) // 2
    assert right == left + width


def test_budgeted_text_left_shrinks_to_fit_and_stays_anchored_at_region_x() -> None:
    """Left alignment always anchors at region.x regardless of font size.

    Position alone cannot prove the budget was honoured, so the drawn extent must actually have shrunk to
    fit, not stayed at the too-wide nominal width ("Jarrah Quill" at 32pt).
    """
    from typing import cast

    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    nominal = text_width(NAME, mono(32))
    assert nominal > cast(int, NARROW_BUDGET["width"])
    block = {"type": "text", "content": NAME, "role": "header", "budget": "NARROW", "field": "F"}
    draw_text_block(block, ctx, 10)
    left, _, right, _ = box_px(recorder, "F")
    assert left == MARGIN
    assert right - left <= cast(int, NARROW_BUDGET["width"])
    assert right - left < nominal


def test_budgeted_text_right_shrinks_to_fit_and_anchors_at_region_right() -> None:
    """Right alignment anchors at region.right, and the extent has shrunk to fit the budget."""
    from typing import cast

    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    nominal = text_width(NAME, mono(32))
    block = {
        "type": "text",
        "content": NAME,
        "role": "header",
        "align": "right",
        "budget": "NARROW",
        "field": "F",
    }
    draw_text_block(block, ctx, 10)
    left, _, right, _ = box_px(recorder, "F")
    assert right == REGION.right
    assert right - left <= cast(int, NARROW_BUDGET["width"])
    assert right - left < nominal


def test_budgeted_text_records_the_full_wrapped_extent() -> None:
    """A wrapped, budgeted block records one box over every line it drew."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    block = {"type": "text", "content": LONG_NAME, "budget": "SUPPLIER_NAME", "field": "F"}
    end = draw_text_block(block, ctx, 100)
    left, top, right, bottom = box_px(recorder, "F")
    assert (left, top, bottom) == (MARGIN, 100, end)
    assert bottom - top == 2 * 20
    assert right <= REGION.right


def test_unbudgeted_text_is_unaffected_by_budget_support() -> None:
    """A block without `budget:` must render byte-identical to before.

    The engine gap this task closes must be inert unless a layout opts in: the same text drawn with and
    without the layout's budgets present is the plain one-line draw.
    """
    with_budgets = text_layout()
    without_budgets = text_layout()
    without_budgets["field_budgets"] = {}
    ctx_a, image_a = block_context(with_budgets)
    ctx_b, image_b = block_context(without_budgets)
    block = {"type": "text", "content": LONG_NAME}
    assert draw_text_block(block, ctx_a, 40) == draw_text_block(block, ctx_b, 40) == 60
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 40), LONG_NAME, font=mono(), fill="black")
    assert same(image_a, expected)
    assert same(image_b, expected)


# --- pair --------------------------------------------------------------------------------------------------


def test_pair_renders_label_and_value() -> None:
    """The default pair draws label, separator and value as one left-aligned string."""
    ctx, image = block_context(text_layout())
    end = draw_pair({"type": "pair", "label": "Date", "value": "{INVOICE_DATE}"}, ctx, 30)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 30), "Date: 01/02/2031", font=mono(), fill="black")
    assert same(image, expected)
    assert end == 50


def test_pair_records_only_the_value_not_the_label() -> None:
    """The recorded box starts after the label and separator and spans the value alone."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    draw_pair(
        {"type": "pair", "label": "Date", "value": "{INVOICE_DATE}", "field": "INVOICE_DATE"}, ctx, 30
    )
    left, top, right, _ = box_px(recorder, "INVOICE_DATE")
    font = mono()
    label_width = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength("Date: ", font=font))
    assert left == MARGIN + label_width
    assert top == 30
    assert right - left == text_width("01/02/2031", font)


def test_pair_records_nothing_without_a_field() -> None:
    """A pair with no `field:` leaves the recorder empty."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    draw_pair({"type": "pair", "label": "Date", "value": "{INVOICE_DATE}"}, ctx, 30)
    assert recorder.as_dict() == {}


def _wrapped_value_pair(**extra) -> dict:
    return {
        "type": "pair",
        "label": "Name",
        "value": "{PAYER_NAME}",
        "budget": "VALUE_WRAP",
        "field": "PAYER_NAME",
        **extra,
    }


def test_pair_budget_wraps_only_the_value_and_advances_by_the_wrapped_height() -> None:
    """Apply the fit budget to the pair's value only.

    The label always draws in full, unshrunk and unwrapped.
    """
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    entry = entry_with(PAYER_NAME=LONG_NAME)
    ctx, _ = block_context(text_layout(), entry, recorder=recorder)
    fitted = fit_text(LONG_NAME, **_fit_args(VALUE_BUDGET))
    assert len(fitted.lines) >= 2
    end = draw_pair(_wrapped_value_pair(), ctx, 100)
    assert end == 100 + len(fitted.lines) * 20
    _, top, _, bottom = box_px(recorder, "PAYER_NAME")
    assert (top, bottom) == (100, end)


def _fit_args(budget: dict) -> dict:
    return {
        "width": budget["width"],
        "fit": budget["fit"],
        "min_font": budget["min_font"],
        "max_lines": budget["max_lines"],
        "nominal_size": 18,
        "family": MONO,
    }


def test_pair_budget_positions_the_value_after_the_full_label() -> None:
    """The budgeted value starts where the unshrunk label and its separator end."""
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, image = block_context(text_layout(), entry_with(PAYER_NAME=LONG_NAME), recorder=recorder)
    draw_pair(_wrapped_value_pair(), ctx, 100)
    label_width = int(ctx.draw.textlength("Name: ", font=mono()))
    left, _, _, _ = box_px(recorder, "PAYER_NAME")
    assert left == MARGIN + label_width
    label_only, label_draw = blank()
    label_draw.text((MARGIN, 100), "Name: ", font=mono(), fill="black")
    crop = (0, 100, MARGIN + label_width, 120)
    assert same(image.crop(crop), label_only.crop(crop))


def test_pair_right_aligns_its_value_to_the_region() -> None:
    """value_align: right draws two separate strings, not the single joined string of the left style.

    The label (plus its separator, here "") sits at region.x and the value is right-aligned at region.right.
    """
    block = {
        "type": "pair",
        "label": "TOTAL",
        "value": "{TOTAL_AMOUNT}",
        "value_align": "right",
        "separator": "",
    }
    ctx, image = block_context(text_layout())
    end = draw_pair(block, ctx, 60)
    font = mono()
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 60), "TOTAL", font=font, fill="black")
    expected_draw.text((REGION.right - text_width("40.00", font), 60), "40.00", font=font, fill="black")
    assert same(image, expected)
    joined, joined_draw = blank()
    joined_draw.text((MARGIN, 60), "TOTAL40.00", font=font, fill="black")
    assert not same(image, joined)
    assert end == 80


def test_pair_separator_applies_on_the_right_aligned_path_too() -> None:
    """One label convention, not one per value_align.

    Before `pair_separator` existed the right-aligned path drew the label verbatim, so invoice layouts wrote
    their own colons into `label:` while left-aligned ones did not. The separator is now the layout's on both
    paths, and it is the drawn string that must carry it: an assertion on the label alone would pass even if
    the separator were dropped.
    """
    block = {"type": "pair", "label": "Total", "value": "{TOTAL_AMOUNT}", "value_align": "right"}
    ctx, image = block_context(text_layout(pair_separator=":"))
    draw_pair(block, ctx, 60)
    font = mono()
    with_separator, draw_with = blank()
    draw_with.text((MARGIN, 60), "Total:", font=font, fill="black")
    draw_with.text((REGION.right - text_width("40.00", font), 60), "40.00", font=font, fill="black")
    without_separator, draw_without = blank()
    draw_without.text((MARGIN, 60), "Total", font=font, fill="black")
    draw_without.text((REGION.right - text_width("40.00", font), 60), "40.00", font=font, fill="black")
    assert same(image, with_separator)
    assert not same(image, without_separator)


def test_pair_separator_is_required_not_defaulted() -> None:
    """A layout that declares no pair_separator fails fast.

    It must not print whichever punctuation this module happens to prefer.
    """
    layout = text_layout()
    del layout["defaults"]["pair_separator"]
    ctx, _ = block_context(layout)
    with pytest.raises(DefaultsError) as exc_info:
        draw_pair({"type": "pair", "label": "Date", "value": "{INVOICE_DATE}"}, ctx, 0)
    assert_diagnostic_error(exc_info.value)
    assert "pair_separator" in str(exc_info.value)


def _collision_region(label: str, value: str, *, x: int) -> Region:
    """Return a region deliberately narrower than label + value, derived from the measured strings.

    A fixed width is only narrow enough for the face it was tuned against; deriving it keeps the collision
    when the typeface changes.
    """
    font = mono()
    width = text_width(label, font) + text_width(value, font) - 10
    return Region(x=x, width=width)


def test_pair_min_gap_pushes_a_long_label_left() -> None:
    """A long label never merges with a right-aligned amount into one OCR token.

    Mirrors invoice.py:283-285: label_x is computed so `min_gap` px stay clear. The region is deliberately
    narrower than label + value so the collision-avoidance actually fires, proven by a sanity assertion
    rather than assumed, and the width is derived from the measured strings so a change of typeface cannot
    silently turn the collision into a comfortable fit.
    """
    label_text, value, min_gap = "Amount outstanding:", "40.00", 8
    region = _collision_region(label_text, value, x=60)
    font = mono()
    assert text_width(label_text, font) + text_width(value, font) > region.width
    ctx, image = make_drawing_ctx(text_layout(pair_separator=":"), region=region)
    block = {
        "type": "pair",
        "label": "Amount outstanding",
        "value": "{TOTAL_AMOUNT}",
        "value_align": "right",
        "min_gap": min_gap,
    }
    draw_pair(block, ctx, 40)
    value_w = text_width(value, font)
    label_x = region.right - value_w - text_width(label_text, font) - min_gap
    assert label_x < region.x
    expected, expected_draw = blank()
    expected_draw.text((label_x, 40), label_text, font=font, fill="black")
    expected_draw.text((region.right - value_w, 40), value, font=font, fill="black")
    assert same(image, expected)


def test_pair_min_gap_zero_never_moves_the_label() -> None:
    """`min_gap: 0` demands no gap and so never repositions the label.

    That is what both legacy renderers do: `draw_line_item` (receipts) and the invoice totals' "separate"
    branch each draw the label at a fixed x and let a long value run into it. Enforcing a zero gap instead
    would shift the label left of where legacy put it (real on one corpus invoice). As above, the region
    width is derived so the overlap this test depends on survives a change of typeface.
    """
    label_text, value = "Amount outstanding:", "40.00"
    region = _collision_region(label_text, value, x=60)
    font = mono()
    assert text_width(label_text, font) + text_width(value, font) > region.width
    ctx, image = make_drawing_ctx(text_layout(pair_separator=":"), region=region)
    block = {
        "type": "pair",
        "label": "Amount outstanding",
        "value": "{TOTAL_AMOUNT}",
        "value_align": "right",
        "min_gap": 0,
    }
    draw_pair(block, ctx, 40)
    expected, expected_draw = blank()
    expected_draw.text((region.x, 40), label_text, font=font, fill="black")
    expected_draw.text((region.right - text_width(value, font), 40), value, font=font, fill="black")
    assert same(image, expected)


def test_pair_value_align_right_records_the_values_own_box() -> None:
    """Both alignments must record the value's own extent, not the label's.

    This is the right-aligned counterpart to test_pair_records_only_the_value_not_the_label.
    """
    recorder = BoxRecorder(PAGE_WIDTH, PAGE_HEIGHT)
    ctx, _ = block_context(text_layout(), recorder=recorder)
    block = {
        "type": "pair",
        "label": "Total",
        "value": "{TOTAL_AMOUNT}",
        "value_align": "right",
        "field": "TOTAL_AMOUNT",
    }
    draw_pair(block, ctx, 60)
    font = mono()
    value_w = text_width("40.00", font)
    left, top, right, _ = box_px(recorder, "TOTAL_AMOUNT")
    assert right == REGION.right
    assert right - left == value_w
    assert top == 60


def test_pair_unaligned_left_is_unaffected_by_value_align_support() -> None:
    """Default (left) rendering must stay byte-identical.

    The engine gap this task closes must be inert unless a layout opts in: an explicit `value_align: left`
    and an absent one draw the same single joined string.
    """
    base = {"type": "pair", "label": "Date", "value": "{INVOICE_DATE}"}
    ctx_a, image_a = block_context(text_layout())
    ctx_b, image_b = block_context(text_layout())
    assert draw_pair(base, ctx_a, 30) == draw_pair({**base, "value_align": "left"}, ctx_b, 30) == 50
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 30), "Date: 01/02/2031", font=mono(), fill="black")
    assert same(image_a, expected)
    assert same(image_b, expected)


# --- block, spacer, rule -----------------------------------------------------------------------------------


def test_block_advances_once_per_line() -> None:
    """A heading-less block advances by one line advance per line."""
    ctx, _ = block_context(text_layout())
    block = {"type": "block", "lines": ["one", "two", "three"]}
    assert draw_block(block, ctx, 10) == 10 + 3 * 20


def test_spacer_advances_by_height() -> None:
    """A spacer advances by its own height, falling back to the layout's `spacer_height`."""
    ctx, image = block_context(text_layout())
    assert draw_spacer({"type": "spacer", "height": 17}, ctx, 10) == 27
    assert draw_spacer({"type": "spacer"}, ctx, 10) == 15
    assert ink_box(image) is None


def test_rule_advances_by_padding() -> None:
    """A rule advances by its pad above, its thickness and its pad below."""
    ctx, _ = block_context(text_layout())
    block = {"type": "rule", "pad_above": 4, "pad_below": 6}
    assert draw_rule(block, ctx, 10) == 10 + 4 + 1 + 6


def test_rule_advances_by_custom_thickness() -> None:
    """A drawn-line rule advances by the thickness it was given."""
    ctx, _ = block_context(text_layout())
    assert draw_rule({"type": "rule", "thickness": 3}, ctx, 10) == 13


def test_rule_default_fill_char_none_draws_a_line_not_glyphs() -> None:
    """With the layout's `rule_fill_char` at "none" (bank statements' own value), a thin line is drawn.

    This confirms the unaligned, undecorated path is unaffected.
    """
    ctx, image = block_context(text_layout())
    draw_rule({"type": "rule"}, ctx, 30)
    assert ink_box(image) == (MARGIN, 30, REGION.right + 1, 31)


def test_rule_fill_char_draws_glyphs_not_a_line() -> None:
    """fill_char dispatches to a row of repeated glyphs, not the drawn `line` the default style uses.

    It reuses common.py's draw_separator glyph-count arithmetic rather than reimplementing it.
    """
    ctx, image = block_context(text_layout())
    draw_rule({"type": "rule", "fill_char": "="}, ctx, 30)
    expected, expected_draw = blank()
    draw_separator(expected_draw, 30, PAGE_WIDTH, MARGIN, mono(), fill="black", char="=")
    assert same(image, expected)
    ink = ink_box(image)
    assert ink is not None
    assert ink[3] - ink[1] > 1


def test_rule_fill_char_advances_by_line_advance_not_thickness() -> None:
    """A character separator occupies a text line.

    Proven by a custom thickness making no difference to the advance when fill_char is set.
    """
    ctx, _ = block_context(text_layout())
    assert draw_rule({"type": "rule", "fill_char": "-", "thickness": 9}, ctx, 30) == 50
    assert draw_rule({"type": "rule", "fill_char": "-", "thickness": 1, "pad_below": 3}, ctx, 30) == 53


# --- banner ------------------------------------------------------------------------------------------------


def _banner(**extra) -> dict:
    return {"type": "banner", "height": 40, "color": "#112233", "content": "{PAYER_NAME}", **extra}


def test_banner_leaves_the_y_cursor_untouched() -> None:
    """Legacy jumps to a hardcoded y after its header bar rather than flowing from the bar's own height.

    A spacer after the banner covers that jump, so the banner returns `y` unchanged.
    """
    ctx, _ = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    assert draw_banner(_banner(), ctx, 123) == 123


def test_banner_paints_a_full_bleed_rectangle_at_the_page_top() -> None:
    """The bar spans the page edge to edge from (0, 0), ignoring the region's margin."""
    ctx, image = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    draw_banner(_banner(), ctx, 300)
    bar = (0x11, 0x22, 0x33)
    assert image.getpixel((0, 0)) == bar
    assert image.getpixel((PAGE_WIDTH - 1, 0)) == bar
    assert image.getpixel((0, 39)) == bar
    assert image.getpixel((PAGE_WIDTH - 1, 39)) == bar
    assert image.getpixel((5, 45)) == (255, 255, 255)
    assert image.getpixel((5, 300)) == (255, 255, 255)


def test_banner_draws_text_at_the_declared_text_y_and_region_x() -> None:
    """The text lands at the block's absolute `text_y` and the region's left edge."""
    ctx, image = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    draw_banner(_banner(text_y=12), ctx, 0)
    expected, expected_draw = blank()
    expected_draw.rectangle([(0, 0), (PAGE_WIDTH, 40)], fill="#112233")
    expected_draw.text((MARGIN, 12), NAME, font=mono(), fill="white")
    assert same(image, expected)


def test_banner_text_color_and_role_are_overridable() -> None:
    """A block's own `text_color` and `role` beat the layout's banner defaults."""
    ctx, image = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    draw_banner(_banner(text_color="yellow", role="header", text_y=2), ctx, 0)
    expected, expected_draw = blank()
    expected_draw.rectangle([(0, 0), (PAGE_WIDTH, 40)], fill="#112233")
    expected_draw.text((MARGIN, 2), NAME, font=mono(32), fill="yellow")
    assert same(image, expected)


def test_banner_content_is_interpolated() -> None:
    """`{FIELD}` placeholders in the banner's content come from the entry."""
    other = "Mallee Parts"
    ctx_a, image_a = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    ctx_b, image_b = block_context(text_layout(), entry_with(PAYER_NAME=other))
    draw_banner(_banner(), ctx_a, 0)
    draw_banner(_banner(), ctx_b, 0)
    assert not same(image_a, image_b)
    expected, expected_draw = blank()
    expected_draw.rectangle([(0, 0), (PAGE_WIDTH, 40)], fill="#112233")
    expected_draw.text((MARGIN, 0), other, font=mono(), fill="white")
    assert same(image_b, expected)


# --- currency ----------------------------------------------------------------------------------------------


def test_format_currency_symbol_keeps_the_dollar_sign() -> None:
    """`symbol` keeps fmt_amount's `$` and thousands separators."""
    assert format_currency("1234.5", "symbol", layout_id=LAYOUT_ID, layout_path=LAYOUT_PATH) == "$1,234.50"


def test_format_currency_plain_drops_the_dollar_sign() -> None:
    """`plain` formats the same amount without the `$`."""
    assert format_currency("1234.5", "plain", layout_id=LAYOUT_ID, layout_path=LAYOUT_PATH) == "1,234.50"


def test_pair_currency_formats_the_value_not_the_label() -> None:
    """Currency formatting touches the value only; the label is drawn as written."""
    block = {"type": "pair", "label": "TOTAL", "value": "{TOTAL_AMOUNT}", "currency": "symbol"}
    ctx, image = block_context(text_layout())
    draw_pair(block, ctx, 20)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 20), "TOTAL: $40.00", font=mono(), fill="black")
    assert same(image, expected)
    plain_ctx, plain_image = block_context(text_layout())
    draw_pair({**block, "currency": "plain"}, plain_ctx, 20)
    expected_plain, plain_draw = blank()
    plain_draw.text((MARGIN, 20), "TOTAL: 40.00", font=mono(), fill="black")
    assert same(plain_image, expected_plain)


def test_pair_without_currency_draws_the_value_verbatim() -> None:
    """A provider-formatted value must not be re-formatted.

    The receipt's cash lines bind an already-`fmt_amount`ed PAYMENT_TENDERED.
    """
    entry = entry_with(PAYMENT_TENDERED="$50.00")
    ctx, image = block_context(text_layout(), entry)
    draw_pair({"type": "pair", "label": "CASH", "value": "{PAYMENT_TENDERED}"}, ctx, 20)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 20), "CASH: $50.00", font=mono(), fill="black")
    assert same(image, expected)


def test_pair_currency_on_a_non_amount_is_a_four_element_diagnostic() -> None:
    """Asking for currency formatting of a non-amount value fails with a four-element diagnostic."""
    ctx, _ = block_context(text_layout(), entry_with(PAYER_NAME=NAME))
    block = {"type": "pair", "label": "Name", "value": "{PAYER_NAME}", "currency": "symbol"}
    with pytest.raises(CurrencyError) as exc_info:
        draw_pair(block, ctx, 0)
    assert_diagnostic_error(exc_info.value)
    assert NAME in str(exc_info.value)


def test_pair_currency_is_skipped_for_an_absent_value() -> None:
    """An empty interpolation (a NOT_FOUND field) must not raise.

    A `when:` guard, not the formatter, is what suppresses an absent value.
    """
    ctx, _ = block_context(text_layout(), entry_with(PAYMENT_TENDERED="NOT_FOUND"))
    block = {"type": "pair", "label": "CASH", "value": "{PAYMENT_TENDERED}", "currency": "symbol"}
    assert draw_pair(block, ctx, 20) == 40


# --- bold pair ---------------------------------------------------------------------------------------------


def test_pair_bold_draws_both_label_and_value_in_the_bold_face() -> None:
    """Legacy drew the receipt's TOTAL line's label and amount in font_bold."""
    layout = text_layout(family="carlito")
    block = {
        "type": "pair",
        "label": "TOTAL",
        "value": "{TOTAL_AMOUNT}",
        "value_align": "right",
        "separator": "",
        "bold": True,
    }
    ctx, image = block_context(layout)
    draw_pair(block, ctx, 20)
    bold = load_font(18, family="carlito", bold=True)
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 20), "TOTAL", font=bold, fill="black")
    expected_draw.text((REGION.right - text_width("40.00", bold), 20), "40.00", font=bold, fill="black")
    assert same(image, expected)


def test_pair_defaults_to_regular_weight() -> None:
    """Without `bold:`, a pair draws in the regular face."""
    from typing import cast

    layout = text_layout(family="carlito")
    ctx, image = block_context(layout)
    draw_pair({"type": "pair", "label": "Date", "value": "{INVOICE_DATE}"}, ctx, 20)
    regular = load_font(18, family="carlito")
    expected, expected_draw = blank()
    expected_draw.text((MARGIN, 20), "Date: 01/02/2031", font=regular, fill="black")
    assert same(image, expected)
    assert cast(dict[str, str], ENTRY["fields"])["INVOICE_DATE"] == "01/02/2031"
