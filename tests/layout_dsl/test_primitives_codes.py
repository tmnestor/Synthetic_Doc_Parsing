"""Inline code marks: real, scannable, deterministic, never clipped."""

import pytest
import zxingcpp
from PIL import Image, ImageDraw, ImageFilter

from generators.layout_dsl import primitives_codes as codes
from generators.layout_dsl.primitives_codes import (
    CodeBlockError,
    draw_barcode,
    draw_logo,
    _draw_shape,
    draw_qrcode,
    initials,
)
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import MARGIN, PAGE_WIDTH, make_drawing_ctx, real_layout

# Invented values, absent from ground_truth/.
FIELDS = {
    "BLANK": "",
    "BAD_EAN": "9312345000019",
    "DX_TRANSACTION_ID": "TX-2031-904417",
    "DX_EAN": "931234500001",
    "EMPTY": "NOT_FOUND",
    "DX_SURVEY_URL": "https://survey.example.test/r/2031-7Q4K",
    "SHORT": "hello",
}
ENTRY = {"case_id": "CASE999", "layout": "receipt_thermal_80mm", "fields": FIELDS}


def barcode_block(**overrides: object) -> dict:
    block = {
        "type": "barcode",
        "content": "{DX_TRANSACTION_ID}",
        "symbology": "code128",
        "height": 60,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": True,
        "caption_gap": 4,
    }
    block.update(overrides)
    return block


def render(block: dict, width: int = 600) -> tuple[Image.Image, int]:
    ctx, image = make_drawing_ctx(real_layout([block]), ENTRY, page_width=width)
    advance = draw_barcode(block, ctx, 0)
    return image, advance


def decoded(image: Image.Image) -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(image)]


def test_code128_round_trips_through_a_decoder() -> None:
    image, _ = render(barcode_block(human_readable=False))
    assert decoded(image) == ["TX-2031-904417"]


def test_ean13_round_trips_with_its_check_digit() -> None:
    image, _ = render(barcode_block(content="{DX_EAN}", symbology="ean13"))
    (text,) = decoded(image)
    assert text == "9312345000012"


def test_same_block_renders_identical_pixels() -> None:
    first, _ = render(barcode_block())
    second, _ = render(barcode_block())
    assert first.tobytes() == second.tobytes()


def test_advance_is_the_bar_height_without_a_caption() -> None:
    _, advance = render(barcode_block(human_readable=False))
    assert advance == 60


def test_caption_adds_one_line_advance() -> None:
    _, bare = render(barcode_block(human_readable=False))
    _, captioned = render(barcode_block())
    assert captioned > bare


def test_the_caption_sits_caption_gap_below_the_bars() -> None:
    """The digits clear the bars by exactly the declared gap; without a caption the gap is unused."""
    image, flush = render(barcode_block(caption_gap=0))
    spaced_image, spaced = render(barcode_block(caption_gap=6))
    assert spaced == flush + 6
    assert render(barcode_block(human_readable=False, caption_gap=6))[1] == 60

    def first_caption_row(img: Image.Image) -> int:
        dark = img.convert("L").point(lambda v: 255 if v < 128 else 0)
        box = dark.crop((0, 60, img.width, img.height)).getbbox()
        assert box is not None
        return 60 + box[1]

    assert first_caption_row(spaced_image) == first_caption_row(image) + 6


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_places_the_quiet_zone_edge(align: str) -> None:
    image, _ = render(barcode_block(human_readable=False, align=align))
    box = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    assert box is not None
    left, _, right, _ = box
    if align == "left":
        assert left == MARGIN + 10 * 2
    elif align == "right":
        assert image.width - right == MARGIN + 10 * 2
    else:
        assert abs(left - (image.width - right)) <= 2


def test_too_wide_for_its_region_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(module_px=8), width=300)
    assert_diagnostic_error(err.value)
    assert str(300 - 2 * MARGIN) in str(err.value)


def test_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)


def test_code128_refuses_non_ascii() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="Café-2031"))
    assert_diagnostic_error(err.value)


def test_ean13_refuses_the_wrong_length() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="12345", symbology="ean13"))
    assert_diagnostic_error(err.value)


def test_page_width_constant_is_the_default_canvas() -> None:
    image, _ = render(barcode_block(human_readable=False, module_px=1, quiet_zone=5), width=PAGE_WIDTH)
    assert image.width == PAGE_WIDTH


def test_blank_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="{BLANK}"))
    assert_diagnostic_error(err.value)


def test_ean13_with_a_wrong_check_digit_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="{BAD_EAN}", symbology="ean13"))
    assert_diagnostic_error(err.value)
    assert "check digit 9, expected 2" in str(err.value)


def test_a_caption_wider_than_the_region_fails_fast() -> None:
    block = barcode_block(content="{DX_EAN}", symbology="ean13", module_px=1, quiet_zone=0)
    with pytest.raises(CodeBlockError) as err:
        render(block, width=119)
    assert_diagnostic_error(err.value)
    assert "95px" in str(err.value)


def test_a_caption_wider_than_the_bars_but_within_the_region_stays_inside_it() -> None:
    block = barcode_block(content="{DX_EAN}", symbology="ean13", module_px=1, quiet_zone=0)
    image, _ = render(block, width=200)
    box = image.convert("L").point(lambda v: 255 if v < 200 else 0).getbbox()
    assert box is not None
    assert box[0] >= MARGIN and box[2] <= image.width - MARGIN


def qr_block(**overrides: object) -> dict:
    block = {
        "type": "qrcode",
        "content": "{DX_SURVEY_URL}",
        "module_px": 4,
        "quiet_zone": 4,
        "error_level": "m",
    }
    block.update(overrides)
    return block


def render_qr(block: dict, width: int = 600, fields: dict | None = None) -> tuple[Image.Image, int]:
    entry = {**ENTRY, "fields": fields or FIELDS}
    ctx, image = make_drawing_ctx(real_layout([block]), entry, page_width=width)
    return image, draw_qrcode(block, ctx, 0)


def test_qr_round_trips_through_a_decoder() -> None:
    image, _ = render_qr(qr_block())
    assert decoded(image) == ["https://survey.example.test/r/2031-7Q4K"]


def test_higher_error_level_draws_a_larger_symbol() -> None:
    _, low = render_qr(qr_block(error_level="l"))
    _, high = render_qr(qr_block(error_level="h"))
    assert high > low


def test_qr_is_deterministic() -> None:
    first, _ = render_qr(qr_block())
    second, _ = render_qr(qr_block(caption="Scan to rate your visit"))
    third, _ = render_qr(qr_block(caption="Scan to rate your visit"))
    assert second.tobytes() == third.tobytes()
    assert first.tobytes() == render_qr(qr_block())[0].tobytes()


def test_qr_caption_adds_one_line_advance_and_ink() -> None:
    _, bare = render_qr(qr_block())
    image, captioned = render_qr(qr_block(caption="Scan to rate"))
    assert captioned > bare
    strip = image.crop((0, bare, image.width, captioned)).convert("L")
    assert strip.point(lambda v: 255 if v < 128 else 0).getbbox() is not None


def test_qr_too_wide_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(module_px=20), width=200)
    assert_diagnostic_error(err.value)


def test_qr_data_overflow_is_a_diagnostic() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(content="{DX_HUGE}", error_level="h"), fields={**FIELDS, "DX_HUGE": "x" * 8000})
    assert_diagnostic_error(err.value)
    assert "do not fit" in str(err.value)


def test_qr_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)


def test_a_qr_caption_wider_than_the_region_fails_fast() -> None:
    block = qr_block(module_px=1, quiet_zone=0, caption="Scan to rate your visit today for a prize")
    with pytest.raises(CodeBlockError) as err:
        render_qr(block, width=120)
    assert_diagnostic_error(err.value)


def test_a_qr_caption_wider_than_the_symbol_but_within_the_region_stays_inside_it() -> None:
    block = qr_block(module_px=1, quiet_zone=0, caption="Scan to rate your visit today")
    image, _ = render_qr(block, width=400)
    box = image.convert("L").point(lambda v: 255 if v < 200 else 0).getbbox()
    assert box is not None
    assert box[0] >= MARGIN and box[2] <= image.width - MARGIN


def test_the_configured_error_level_is_honoured_for_short_content() -> None:
    low, low_adv = render_qr(qr_block(content="{SHORT}", error_level="l"))
    high, high_adv = render_qr(qr_block(content="{SHORT}", error_level="h"))
    assert low.tobytes() != high.tobytes()
    assert low_adv <= high_adv


@pytest.mark.parametrize("level", ["l", "m", "q", "h"])
def test_the_decoded_error_level_equals_the_configured_one(level: str) -> None:
    image, _ = render_qr(qr_block(content="{SHORT}", error_level=level))
    (result,) = zxingcpp.read_barcodes(image)
    if not hasattr(result, "ec_level"):
        pytest.skip("zxing-cpp exposes no ec_level")
    assert result.ec_level == level.upper()


def test_the_quiet_zone_and_advance_are_exact() -> None:
    block = qr_block(content="{SHORT}", module_px=3, quiet_zone=5, align="left")
    image, advance = render_qr(block)
    box = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    assert box is not None
    side = advance - 2 * 15
    assert side % 3 == 0
    assert (box[0] - MARGIN, box[1], box[2] - box[0], box[3] - box[1]) == (15, 15, side, side)


def test_an_absent_caption_fails_fast() -> None:
    for field in ("{EMPTY}", "{BLANK}"):
        with pytest.raises(CodeBlockError) as err:
            render_qr(qr_block(caption=field))
        assert_diagnostic_error(err.value)
        assert "caption" in str(err.value)


def test_a_negative_left_bearing_keeps_the_caption_ink_inside_the_span(monkeypatch) -> None:
    block = qr_block(content="{SHORT}", module_px=1, quiet_zone=0, caption="Scan me please", align="left")
    ctx, image = make_drawing_ctx(real_layout([block]), {**ENTRY, "fields": FIELDS}, page_width=400)
    real = ctx.draw.textbbox

    def shifted(xy, text, *args, **kwargs):
        left, top, right, bottom = real(xy, text, *args, **kwargs)
        return (left - 6, top, right, bottom)

    monkeypatch.setattr(ctx.draw, "textbbox", shifted)
    draw_qrcode(block, ctx, 0)
    ink = image.convert("L").point(lambda v: 255 if v < 128 else 0).crop((0, 30, 400, 600)).getbbox()
    assert ink is not None
    assert ink[0] >= MARGIN


LOGO_FIELDS = {
    **FIELDS,
    "DX_NAME": "Mallee Quillfeather Supplies",
    "DX_DIGITS": "2031 4417",
    "DX_WIDE": "Wmwm Wmwm",
    "DX_MM": "Marlowe Mill",
    "DX_WM": "Wombat Mart",
    "DX_WW": "Wren Wix",
}
LOGO_ENTRY = {**ENTRY, "fields": LOGO_FIELDS}


def logo_block(**overrides: object) -> dict:
    block = {
        "type": "logo",
        "content": "{DX_NAME}",
        "style": "circle",
        "size": 80,
        "min_text_size": 12,
        "inset": 0,
        "text_color": "white",
    }
    block.update(overrides)
    return block


def render_logo(block: dict, width: int = 600) -> tuple[Image.Image, int]:
    ctx, image = make_drawing_ctx(real_layout([block]), LOGO_ENTRY, page_width=width)
    return image, draw_logo(block, ctx, 0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Mallee Quillfeather Supplies", "MQ"), ("quillfeather", "Q"), ("& 2031 Marlowe", "M")],
)
def test_initials_take_the_first_letters_of_up_to_two_lettered_words(name: str, expected: str) -> None:
    assert initials(name) == expected


def test_logo_advance_is_its_size() -> None:
    _, advance = render_logo(logo_block())
    assert advance == 80


def test_each_style_draws_a_different_shape() -> None:
    images = [render_logo(logo_block(style=s))[0].tobytes() for s in ("circle", "square", "chevron")]
    assert len(set(images)) == 3


def test_initials_are_drawn_inside_the_shape_in_their_own_colour() -> None:
    image, _ = render_logo(logo_block(style="square", color="black", text_color="white", align="left"))
    inner = image.crop((MARGIN, 0, MARGIN + 80, 80)).convert("L")
    assert inner.getextrema() == (0, 255)  # black fill and white initials both present


def _is_red(pixel: object) -> bool:
    return isinstance(pixel, tuple) and pixel[0] > pixel[1] and pixel[1] == pixel[2]


def stray_ink(style: str, **overrides: object) -> list[tuple[int, int]]:
    """Return non-white pixels of a red-on-black logo that lie outside an independently drawn shape."""
    block = logo_block(style=style, color="black", text_color="red", align="left", **overrides)
    image, _ = render_logo(block)
    mask = Image.new("L", image.size, 0)
    _draw_shape(ImageDraw.Draw(mask), style, MARGIN, 0, int(block["size"]), 255)
    return [
        (x, y)
        for x in range(image.width)
        for y in range(image.height)
        if image.getpixel((x, y)) != (255, 255, 255) and mask.getpixel((x, y)) == 0
    ]


@pytest.mark.parametrize("style", ["circle", "square", "chevron"])
def test_every_initial_pixel_lies_inside_the_shape(style: str) -> None:
    image, _ = render_logo(logo_block(style=style, color="black", text_color="red", align="left"))
    reddish = [
        (x, y) for x in range(image.width) for y in range(image.height) if _is_red(image.getpixel((x, y)))
    ]
    assert reddish  # the initials were drawn
    assert stray_ink(style) == []


def red_outside_the_inset(style: str, rendered_inset: int, checked_inset: int) -> list[tuple[int, int]]:
    """Red initial pixels closer than `checked_inset` px to the rim of an independently drawn shape."""
    block = logo_block(
        style=style,
        color="black",
        text_color="red",
        align="left",
        content="{DX_WIDE}",
        size=48,
        inset=rendered_inset,
    )
    image, _ = render_logo(block)
    mask = Image.new("L", image.size, 0)
    _draw_shape(ImageDraw.Draw(mask), style, MARGIN, 0, int(block["size"]), 255)
    mask = mask.filter(ImageFilter.MinFilter(2 * checked_inset + 1))
    return [
        (x, y)
        for x in range(image.width)
        for y in range(image.height)
        if _is_red(image.getpixel((x, y))) and mask.getpixel((x, y)) == 0
    ]


# A square never binds: initials are capped at half its size, so they stay clear of its rim anyway.
@pytest.mark.parametrize("style", ["circle", "chevron"])
def test_the_initials_keep_their_inset_from_the_rim(style: str) -> None:
    """Initials hug a curved or notched rim at inset 0; with an inset they keep that clear gap from it."""
    assert red_outside_the_inset(style, rendered_inset=0, checked_inset=6) != []
    assert red_outside_the_inset(style, rendered_inset=6, checked_inset=6) == []


@pytest.mark.parametrize("style", ["circle", "square", "chevron"])
def test_the_stray_ink_check_detects_initials_outside_the_shape(
    style: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codes, "_initials_spill", lambda *args: False)
    real_font_for = codes.font_for
    monkeypatch.setattr(
        codes, "font_for", lambda layout, block, size, **kw: real_font_for(layout, block, size * 2, **kw)
    )
    assert stray_ink(style) != []


def test_an_unparseable_layout_default_colour_fails_fast_at_render_time() -> None:
    layout = real_layout([logo_block()])
    layout["defaults"]["color"] = "not-a-colour"
    ctx, _ = make_drawing_ctx(layout, LOGO_ENTRY)
    with pytest.raises(CodeBlockError) as err:
        draw_logo(logo_block(), ctx, 0)
    assert_diagnostic_error(err.value)
    assert "not-a-colour" in str(err.value)


@pytest.mark.parametrize("style", ["circle", "square", "chevron"])
def test_the_shape_stays_within_its_size_box(style: str) -> None:
    image, _ = render_logo(logo_block(style=style, color="black", align="left"))
    box = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    assert box is not None
    left, top, right, bottom = box
    assert (left, top) >= (MARGIN, 0)
    assert right <= MARGIN + 80
    assert bottom <= 80


def test_logo_is_deterministic() -> None:
    assert render_logo(logo_block())[0].tobytes() == render_logo(logo_block())[0].tobytes()


def test_a_name_without_letters_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{DX_DIGITS}"))
    assert_diagnostic_error(err.value)


def test_logo_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)


@pytest.mark.parametrize("style", ["circle", "square", "chevron"])
def test_initials_that_would_spill_out_of_the_shape_fail_fast(
    style: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stand in for a very wide face that no size down to the floor can fit.
    real_font_for = codes.font_for
    monkeypatch.setattr(
        codes, "font_for", lambda layout, block, size, **kw: real_font_for(layout, block, size * 4, **kw)
    )
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{DX_WIDE}", size=40, style=style))
    assert_diagnostic_error(err.value)
    assert "initials" in str(err.value)


def test_a_logo_wider_than_its_region_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(size=300), width=300)
    assert_diagnostic_error(err.value)


def test_text_colour_equal_to_the_explicit_fill_fails_fast_however_spelled() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(color="white", text_color="#ffffff"))
    assert_diagnostic_error(err.value)
    assert "invisible" in str(err.value)


def test_text_colour_equal_to_the_layout_default_fill_fails_fast() -> None:
    layout = real_layout([logo_block(text_color="#FFF")])
    layout["defaults"]["color"] = "white"
    block = logo_block(text_color="#FFF")
    ctx, _ = make_drawing_ctx(layout, LOGO_ENTRY)
    with pytest.raises(CodeBlockError) as err:
        draw_logo(block, ctx, 0)
    assert_diagnostic_error(err.value)


@pytest.mark.parametrize("family", ["carlito", "liberation_sans"])
@pytest.mark.parametrize("size", [48, 80])
@pytest.mark.parametrize(
    ("style", "field"), [("chevron", "DX_MM"), ("chevron", "DX_WM"), ("circle", "DX_WW")]
)
def test_wide_initials_are_fitted_inside_the_shape_in_proportional_faces(
    style: str, field: str, size: int, family: str
) -> None:
    overrides = {"content": "{" + field + "}", "size": size, "family": family, "min_text_size": 8}
    image, _ = render_logo(
        logo_block(style=style, color="black", text_color="red", align="left", **overrides)
    )
    assert any(_is_red(image.getpixel((x, y))) for x in range(image.width) for y in range(size))
    assert stray_ink(style, **overrides) == []


def test_the_fitted_logo_is_deterministic() -> None:
    block = logo_block(style="chevron", content="{DX_WM}", family="liberation_sans", min_text_size=8)
    assert render_logo(block)[0].tobytes() == render_logo(block)[0].tobytes()


def test_the_min_text_size_floor_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    real_font_for = codes.font_for
    monkeypatch.setattr(
        codes, "font_for", lambda layout, block, size, **kw: real_font_for(layout, block, size * 4, **kw)
    )
    render_logo(logo_block(content="{DX_WIDE}", size=80, min_text_size=1))  # shrinks until it fits
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{DX_WIDE}", size=80, min_text_size=30))
    assert_diagnostic_error(err.value)
    assert "min_text_size 30px" in str(err.value)


def test_a_barcode_with_one_absent_field_among_literals_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="TX{EMPTY}"))
    assert_diagnostic_error(err.value)
    assert "EMPTY" in str(err.value)


def test_a_qr_caption_with_one_absent_field_among_literals_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(caption="Survey code: {EMPTY}"))
    assert_diagnostic_error(err.value)
    assert "EMPTY" in str(err.value)
    assert "caption" in str(err.value)


@pytest.mark.parametrize("kind", ["barcode", "qrcode"])
def test_an_unparseable_default_colour_is_a_diagnostic_for_barcode_and_qrcode(kind: str) -> None:
    block = barcode_block() if kind == "barcode" else qr_block()
    layout = real_layout([block])
    layout["defaults"]["color"] = "not-a-colour"
    ctx, _ = make_drawing_ctx(layout, ENTRY)
    draw = draw_barcode if kind == "barcode" else draw_qrcode
    with pytest.raises(CodeBlockError) as err:
        draw(block, ctx, 0)
    assert_diagnostic_error(err.value)
    assert "not-a-colour" in str(err.value)
    assert f"'{kind}'" in str(err.value)
