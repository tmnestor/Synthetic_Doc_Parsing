"""Tests for the fit-aware draw helpers in generators/common.py."""

from PIL import Image, ImageDraw

from generators.common import (
    _text_width,
    draw_fitted_center,
    draw_fitted_left,
    draw_fitted_right,
    fit_text,
)
from generators.exporters.geometry import BoxRecorder

FAMILY = "carlito"
SIZE = 24
CANVAS_W = 600
ROOMY = {"width": 560, "fit": "wrap", "min_font": 10, "max_lines": 3}
LONG_TEXT = "Industrial Pressure Washer Hose Fitting Kit with Spare Seals"


def make_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (CANVAS_W, 400), "white")
    return image, ImageDraw.Draw(image)


def narrow_budget() -> dict:
    """A wrap budget that forces LONG_TEXT onto more than one line."""
    return {**ROOMY, "width": _text_width(LONG_TEXT, SIZE, family=FAMILY, bold=False) // 2 + 40}


def test_fitted_left_returns_advanced_y() -> None:
    """A one-line draw advances y by the font's line height, or by an explicit line_spacing."""
    _, draw = make_canvas()
    plan = fit_text(
        "Hello", width=560, fit="wrap", min_font=10, max_lines=3, nominal_size=SIZE, family=FAMILY
    )
    y_end = draw_fitted_left(draw, "Hello", 10, 50, budget=ROOMY, nominal_size=SIZE, family=FAMILY)
    assert y_end == 50 + plan.line_height
    spaced = draw_fitted_left(
        draw, "Hello", 10, 50, budget=ROOMY, nominal_size=SIZE, family=FAMILY, line_spacing=40
    )
    assert spaced == 90


def test_fitted_center_wraps_and_advances_more_for_two_lines() -> None:
    """A wrapped string advances y once per line, so it moves following content further down."""
    _, draw = make_canvas()
    one = draw_fitted_center(draw, "Hello", 50, CANVAS_W, budget=ROOMY, nominal_size=SIZE, family=FAMILY)
    budget = narrow_budget()
    plan = fit_text(LONG_TEXT, nominal_size=SIZE, family=FAMILY, **budget)
    assert len(plan.lines) >= 2
    recorder = BoxRecorder(CANVAS_W, 400)
    many = draw_fitted_center(
        draw,
        LONG_TEXT,
        50,
        CANVAS_W,
        budget=budget,
        nominal_size=SIZE,
        family=FAMILY,
        recorder=recorder,
        field="SUPPLIER_NAME",
    )
    assert many - 50 == (one - 50) * len(plan.lines)
    left, _, right, _ = recorder.as_dict()["SUPPLIER_NAME"]
    assert abs((left + right) / 2 - 0.5) < 0.02, "wrapped block should stay centred"


def test_fitted_right_draws_within_canvas() -> None:
    """Right-aligned text ends at x_right, stays inside the canvas and records a box ending there."""
    image, draw = make_canvas()
    x_right = CANVAS_W - 20
    recorder = BoxRecorder(CANVAS_W, 400)
    y_end = draw_fitted_right(
        draw,
        LONG_TEXT,
        x_right,
        50,
        budget=narrow_budget(),
        nominal_size=SIZE,
        family=FAMILY,
        recorder=recorder,
        field="TOTAL_AMOUNT",
    )
    assert y_end > 50
    ink = image.convert("L").point(lambda v: 255 if v < 200 else 0).getbbox()
    assert ink is not None
    assert ink[0] >= 0
    assert ink[2] <= x_right + 1
    box = recorder.as_dict()["TOTAL_AMOUNT"]
    assert box[2] == round(x_right / CANVAS_W, 6)
    assert 0.0 <= box[0] < box[2]
