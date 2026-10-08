"""The overlay verifier: padded protected boxes, the pixel clearance check, page inset and contrast."""

import pytest
from PIL import Image, ImageChops, ImageDraw

from generators.common import load_font
from generators.distractors.config import ContrastSettings, load_distractor_config
from generators.exporters.geometry import InkRecord
from generators.overlay.verify import (
    OverlayError,
    check_clear,
    check_contrast,
    check_inset,
    contrast_ratio,
    protected_boxes,
)
from tests.conftest import assert_diagnostic_error
from tests.overlay.overlay_helpers import site

SIZE = (400, 300)
SCORED = InkRecord("scored", (100, 100, 200, 120), False, "x", 10, False)
TEXT = InkRecord("text", (100, 160, 200, 180), False, "x", 10, False)
CODE = InkRecord("distractor", (250, 100, 330, 140), True, "", 0, False)
DISTRACTOR = InkRecord("distractor", (250, 200, 330, 220), False, "x", 10, False)
INK = [SCORED, TEXT, CODE, DISTRACTOR]
CLEARANCE = load_distractor_config().overlay.clearance_px


def _alpha(*pixels: tuple[int, int]) -> Image.Image:
    alpha = Image.new("L", SIZE, 0)
    for xy in pixels:
        alpha.putpixel(xy, 255)
    return alpha


def _check(alpha: Image.Image, policy: str) -> None:
    check_clear(
        alpha,
        protected_boxes(INK, policy, CLEARANCE),
        site=site(),
        mark=alpha.getbbox() or (0, 0, 0, 0),
        clearance=CLEARANCE,
        policy=policy,
    )


def _refused(alpha: Image.Image, policy: str, *tokens: str) -> OverlayError:
    with pytest.raises(OverlayError) as err:
        _check(alpha, policy)
    assert_diagnostic_error(err.value)
    for token in ("CASE999", "synthetic_layout", "synthetic_layout.overlay[0]", *tokens):
        assert token in str(err.value), f"{token!r} missing from: {err.value}"
    return err.value


def test_protected_boxes_pad_by_the_clearance() -> None:
    (padded,) = protected_boxes([SCORED], "scored_and_marks", 6)
    assert padded.box == (94, 94, 206, 126)
    assert padded.ink == SCORED


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("scored_and_marks", [SCORED, CODE]), ("all_ink", INK)],
)
def test_each_policy_protects_what_it_names(policy: str, expected: list[InkRecord]) -> None:
    assert [entry.ink for entry in protected_boxes(INK, policy, CLEARANCE)] == expected


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="scored_and_marks or all_ink"):
        protected_boxes(INK, "most_ink", CLEARANCE)


def test_a_mark_inside_a_padded_scored_box_fails() -> None:
    err = _refused(_alpha((150, 110)), "scored_and_marks", "scored ink (100, 100, 200, 120)")
    assert "1 protected box" in str(err)


def test_a_mark_inside_the_padding_but_outside_the_box_fails() -> None:
    _refused(_alpha((200 + CLEARANCE - 1, 110)), "scored_and_marks", "scored ink")


def test_a_mark_one_pixel_outside_the_padding_passes() -> None:
    _check(
        _alpha((200 + CLEARANCE, 110), (150, 120 + CLEARANCE), (100 - CLEARANCE - 1, 110)),
        "scored_and_marks",
    )


def test_a_mark_in_the_descender_band_below_a_recorded_box_fails() -> None:
    # Descenders hang about 3px below a recorded line box; the padding covers them.
    _refused(_alpha((150, 123)), "scored_and_marks", "scored ink")


def test_a_text_box_is_protected_under_all_ink_only() -> None:
    mark = _alpha((150, 170))
    _check(mark, "scored_and_marks")
    _refused(mark, "all_ink", "text ink (100, 160, 200, 180)")


@pytest.mark.parametrize("policy", ["scored_and_marks", "all_ink"])
def test_a_mark_over_a_code_mark_fails_under_both(policy: str) -> None:
    _refused(_alpha((290, 120)), policy, "code mark (250, 100, 330, 140)")


def test_every_box_a_mark_meets_is_named() -> None:
    # One stroke across the scored box and the code mark.
    alpha = Image.new("L", SIZE, 0)
    ImageDraw.Draw(alpha).line((120, 110, 300, 110), fill=255, width=2)
    err = _refused(alpha, "scored_and_marks", "scored ink", "code mark", "2 protected box")
    assert f"its ink at {alpha.getbbox()}" in str(err)


@pytest.mark.parametrize("policy", ["no_overlay", "none"])
def test_a_policy_that_protects_nothing_is_refused(policy: str) -> None:
    """A type with no overlay has nothing to protect; asking for its boxes is a fault, not an empty list."""
    with pytest.raises(ValueError, match=f"'{policy}' protects nothing"):
        protected_boxes(INK, policy, CLEARANCE)


def test_a_mark_outside_the_page_inset_fails() -> None:
    inset = 30
    check_inset((inset, inset, SIZE[0] - inset, SIZE[1] - inset), SIZE, inset, site=site())
    with pytest.raises(OverlayError) as err:
        check_inset((inset - 1, 100, 60, 120), SIZE, inset, site=site())
    assert_diagnostic_error(err.value)
    assert "page_inset_px" in str(err.value) and "synthetic_layout.overlay[0]" in str(err.value)


# --- contrast ---------------------------------------------------------------------------------------------

SETTINGS = load_distractor_config().overlay.contrast
LIGHT = (255, 241, 118)
DARK = (90, 90, 90)


def _page(colour: str) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """A white page with one line of `colour` text, and the line's ink box."""
    page = Image.new("RGB", SIZE, "white")
    font = load_font(28, family="carlito", bold=False)
    draw = ImageDraw.Draw(page)
    draw.text((100, 100), "1,234.56 Payment", font=font, fill=colour)
    left, top, right, bottom = draw.textbbox((100, 100), "1,234.56 Payment", font=font)
    return page, (int(left), int(top), int(right), int(bottom))


def _tinted(
    page: Image.Image, band: tuple[int, int, int, int], colour: tuple[int, int, int]
) -> Image.Image:
    mask = Image.new("L", page.size, 0)
    ImageDraw.Draw(mask).rectangle((band[0], band[1], band[2] - 1, band[3] - 1), fill=255)
    return Image.composite(ImageChops.multiply(page, Image.new("RGB", page.size, colour)), page, mask)


def _band(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (box[0] - 10, box[1] - 6, box[2] + 10, box[3] + 6)


def test_contrast_ratio_reads_black_on_white_as_high_and_blank_paper_as_one() -> None:
    page, box = _page("black")
    assert contrast_ratio(page, box, SETTINGS) > 15
    assert contrast_ratio(Image.new("RGB", SIZE, "white"), box, SETTINGS) == pytest.approx(1.0)


def test_contrast_ratio_refuses_an_empty_box() -> None:
    with pytest.raises(ValueError, match="no pixels"):
        contrast_ratio(Image.new("RGB", SIZE, "white"), (10, 10, 10, 20), SETTINGS)


def test_black_text_under_a_light_tint_passes() -> None:
    page, box = _page("black")
    after = _tinted(page, _band(box), LIGHT)
    assert contrast_ratio(after, box, SETTINGS) >= SETTINGS.min_ratio
    check_contrast(
        page, after, [_band(box)], [InkRecord("scored", box, False, "x", 10, False)], SETTINGS, site=site()
    )


def test_black_text_under_a_dark_tint_fails_with_the_diagnostic() -> None:
    page, box = _page("black")
    after = _tinted(page, _band(box), DARK)
    with pytest.raises(OverlayError) as err:
        check_contrast(
            page,
            after,
            [_band(box)],
            [InkRecord("text", box, False, "x", 10, False)],
            SETTINGS,
            site=site(),
        )
    assert_diagnostic_error(err.value)
    for token in ("CASE999", "synthetic_layout.overlay[0]", "min_ratio", f"text ink {box}", "before"):
        assert token in str(err.value), f"{token!r} missing from: {err.value}"


def test_grey_text_already_below_the_ratio_is_exempt_unless_scored() -> None:
    page, box = _page("#9A9A9A")
    assert contrast_ratio(page, box, SETTINGS) < SETTINGS.min_ratio
    after = _tinted(page, _band(box), LIGHT)
    check_contrast(
        page, after, [_band(box)], [InkRecord("text", box, False, "x", 10, False)], SETTINGS, site=site()
    )
    with pytest.raises(OverlayError) as err:
        check_contrast(
            page,
            after,
            [_band(box)],
            [InkRecord("scored", box, False, "x", 10, False)],
            SETTINGS,
            site=site(),
        )
    assert_diagnostic_error(err.value)
    assert f"scored ink {box}" in str(err.value)


def test_a_scored_box_below_the_ratio_fails() -> None:
    page, box = _page("black")
    after = _tinted(page, _band(box), DARK)
    with pytest.raises(OverlayError, match="scored ink"):
        check_contrast(
            page,
            after,
            [_band(box)],
            [InkRecord("scored", box, False, "x", 10, False)],
            SETTINGS,
            site=site(),
        )


def test_a_box_no_band_meets_is_not_checked() -> None:
    # Grey scored text is below the ratio before and after; it is not under the band, so it is not checked.
    page, box = _page("#9A9A9A")
    elsewhere = (10, 250, 60, 290)
    after = _tinted(page, elsewhere, LIGHT)
    check_contrast(
        page, after, [elsewhere], [InkRecord("scored", box, False, "x", 10, False)], SETTINGS, site=site()
    )


def test_the_threshold_comes_from_the_settings() -> None:
    page, box = _page("black")
    after = _tinted(page, _band(box), LIGHT)
    strict = ContrastSettings(min_ratio=20.5, dark_percentile=5, light_percentile=95)
    with pytest.raises(OverlayError, match="20.5"):
        check_contrast(
            page,
            after,
            [_band(box)],
            [InkRecord("scored", box, False, "x", 10, False)],
            strict,
            site=site(),
        )
