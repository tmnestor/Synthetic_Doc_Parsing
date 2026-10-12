"""Shared utilities for synthetic document generation.

Font loading, text drawing helpers, and ABN/GST validation.

Image degradation lives in `generators/degradation/`, not here: it applies to
receipts only and is a pipeline of its own rather than a shared helper.
"""

import random
from collections.abc import Callable, Container
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Protocol

from PIL import ImageDraw, ImageFont

from generators.exporters.geometry import BoxRecorder

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


class InkSink(Protocol):
    """Receives each drawn line: its pixel ink box (left, top, right, bottom), its string, its font size
    in points and its weight. The layout engine binds the line's role (see RenderContext.ink). Recording
    ink never changes what is drawn."""

    def __call__(self, box: tuple[int, int, int, int], *, text: str, size: int, bold: bool) -> None:
        """Record one drawn line."""
        ...


_FONT_CACHE: dict[tuple[int, str, bool], Font] = {}

# Maps id(font) -> Path it was loaded from, so fit measurement can assert it is
# using a vendored face rather than something the caller built itself.
_FONT_SOURCE: dict[int, Path] = {}
# Maps id(font) -> (size, bold) it was loaded at, so a drawn line's ink can say what it was drawn in.
_FONT_SPEC: dict[int, tuple[int, bool]] = {}


class FontSourceError(RuntimeError):
    """Raised when a font used for measurement is not a vendored face."""


class FontFamilyError(RuntimeError):
    """Raised when a layout names a font family that is not vendored."""


# Bundled fonts directory (committed to repo for cross-platform consistency)
_BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# The vendored font families, as {family: (regular, bold)} filenames.
#
# There is deliberately NO system-font fallback. A fallback cannot be a
# reproducibility-neutral convenience here: a missing face would silently swap
# glyph metrics, which re-runs every `fit_text()` decision and diverges the
# rendered pixels Mac<->PROD without erroring. Since these files are vendored
# and tracked in git, a missing one means a broken checkout, and the only safe
# response is to fail loudly. This is the single-path guarantee `fit_text` and
# `geometry.jsonl` in the configured `derived_dir` both rest on.
#
# All three families are metric-compatible substitutes for the proprietary
# fonts real Australian business documents actually use, so they are both
# redistributable (OFL) and closer to the target distribution than a generic
# face would be:
#   carlito         -> Calibri  (the modern invoice/statement register)
#   liberation_sans -> Arial    (the older, more conservative register)
#   liberation_mono -> Courier  (thermal/dot-matrix receipts)
#
# The handwriting faces (kalam, caveat, indie_flower, patrick_hand; OFL, provenance in
# fonts/SOURCES-handwriting.txt) draw a taxpayer's notes and sums on a bank statement. A face that
# ships one weight has None for its bold file: asking for that weight fails fast, never falling back.
FONT_FAMILIES: dict[str, tuple[str, str | None]] = {
    "carlito": ("Carlito-Regular.ttf", "Carlito-Bold.ttf"),
    "liberation_sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    "liberation_mono": ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
    "kalam": ("Kalam-Regular.ttf", "Kalam-Bold.ttf"),
    "caveat": ("Caveat-Regular.ttf", "Caveat-Bold.ttf"),
    "indie_flower": ("IndieFlower-Regular.ttf", None),
    "patrick_hand": ("PatrickHand-Regular.ttf", None),
}


def vendored_weights(family: str) -> tuple[bool, ...]:
    """Return the weights a vendored family ships, as `bold` flags: (False,) or (False, True).

    Args:
        family: A key of `FONT_FAMILIES`.

    Returns:
        False (regular) always, then True when the family ships a bold file.

    Raises:
        FontFamilyError: `family` is not a vendored family.
    """
    font_path(family)
    return (False, True) if FONT_FAMILIES[family][1] is not None else (False,)


def font_path(family: str, *, bold: bool = False) -> Path:
    """Resolve a family+weight to its single vendored file path.

    Args:
        family: A key of `FONT_FAMILIES`.
        bold: Select the bold face rather than the regular one.

    Returns:
        Path to the vendored TTF. The path is not checked for existence here;
        `load_font` reports a missing file with its own diagnostic.

    Raises:
        FontFamilyError: `family` is not a vendored family, or `bold` asks for a weight it does not ship.
    """
    faces = FONT_FAMILIES.get(family)
    if faces is not None and bold and faces[1] is None:
        raise FontFamilyError(
            "Font weight not vendored.\n"
            f"  What:     family '{family}' ships no bold face (only {faces[0]}).\n"
            f"  Where:    the `bold:` key naming '{family}', e.g. overlay.handwriting.hands in "
            f"config/distractors.yml, or a block's `bold:` in config/layouts/*.yml\n"
            f"  Expected: bold: false for '{family}', or a family that ships a bold file, e.g.\n"
            f"              {{family: kalam, bold: true}}\n"
            f"  Recover:  set bold: false for '{family}', or vendor its bold file in fonts/ with its "
            "licence and name it in FONT_FAMILIES in generators/common.py."
        )
    if faces is None:
        raise FontFamilyError(
            "Unknown font family.\n"
            f"  What:     '{family}' is not a vendored font family.\n"
            f"  Where:    the `family:` key on a block, or `defaults.family` in "
            f"config/layouts/*.yml\n"
            f"  Expected: one of {sorted(FONT_FAMILIES)}, e.g.\n"
            f"              defaults:\n"
            f"                family: carlito\n"
            f"  Recover:  set `family:` to a vendored family, or vendor the new "
            f"face in fonts/ and register it in FONT_FAMILIES in generators/common.py."
        )
    regular, bold_file = faces
    return _BUNDLED_FONTS_DIR / (bold_file if bold and bold_file is not None else regular)


def load_font(size: int, *, family: str, bold: bool = False) -> Font:
    """Load a vendored font face, cached.

    Resolves to exactly one file per (family, weight) — see `FONT_FAMILIES` for
    why there is no system fallback.

    Args:
        size: Font size in points.
        family: A key of `FONT_FAMILIES`, e.g. "carlito".
        bold: Use bold weight.

    Returns:
        Loaded PIL font object.

    Raises:
        FontFamilyError: `family` is not a vendored family, or ships no bold face and `bold` is set.
        FileNotFoundError: The vendored file for this family/weight is missing
            or unreadable.
    """
    key = (size, family, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    path = font_path(family, bold=bold)
    try:
        font: Font = ImageFont.truetype(str(path), size)
    except OSError as err:
        raise FileNotFoundError(
            "Vendored font file is missing or unreadable.\n"
            f"  What:     cannot load {path.name} for family '{family}' "
            f"(bold={bold}): {err}.\n"
            f"  Where:    {path}\n"
            f"  Expected: the vendored face present in {_BUNDLED_FONTS_DIR}, "
            f"tracked in git alongside its OFL license file.\n"
            "  Recover:  restore the fonts/ directory from the repo "
            "(`git checkout -- fonts/`); never substitute a system font, which "
            "would silently change fit decisions and rendered pixels."
        ) from None

    _FONT_SOURCE[id(font)] = path
    _FONT_SPEC[id(font)] = (size, bold)
    _FONT_CACHE[key] = font
    return font


def font_source_path(font: Font) -> Path | None:
    """Return the file a font was loaded from, or None if unknown."""
    return _FONT_SOURCE.get(id(font))


def font_spec(font: Font) -> tuple[int, bool]:
    """Return the size in points and the weight (True for bold) a font was loaded at by `load_font`.

    Args:
        font: A font `load_font` returned.

    Returns:
        (size, bold).

    Raises:
        FontSourceError: The font did not come from `load_font`, so what it draws cannot be described.
    """
    spec = _FONT_SPEC.get(id(font))
    if spec is None:
        raise FontSourceError(
            "A drawn line's font is not a vendored face loaded by load_font.\n"
            f"  What:     cannot say what size and weight {font!r} draws in, so its ink cannot be "
            "recorded with them.\n"
            f"  Where:    bundled fonts directory {_BUNDLED_FONTS_DIR}; the drawing call that built "
            "the font\n"
            "  Expected: every font that draws a recorded line comes from "
            "generators.common.load_font, e.g. "
            "load_font(18, family='carlito', bold=False).\n"
            "  Recover:  load the font through load_font instead of constructing an ImageFont directly."
        )
    return spec


def assert_bundled_font(font: Font) -> None:
    """Fail loud if `font` did not come from the vendored fonts/ directory.

    `load_font` can no longer return a system font, so this now guards the
    remaining way an unvendored face could reach measurement: a caller that
    builds an `ImageFont` itself and passes it in. Measuring against such a
    font would diverge Mac<->PROD and silently corrupt fit decisions.

    Raises:
        FontSourceError: the font is not a vendored face.
    """
    src = font_source_path(font)
    if src is not None and _BUNDLED_FONTS_DIR in src.parents:
        return
    raise FontSourceError(
        "Font used for measurement is not a bundled font.\n"
        f"  What:     fit measurement requires a vendored face; got {src}.\n"
        f"  Where:    bundled fonts directory {_BUNDLED_FONTS_DIR}\n"
        f"  Expected: a face loaded via load_font(family=...) from one of "
        f"{sorted(FONT_FAMILIES)}, e.g. fonts/Carlito-Regular.ttf.\n"
        "  Recover:  load the font with load_font(size, family=..., bold=...) "
        "instead of constructing an ImageFont directly."
    )


FitStrategy = str  # one of: "shrink", "wrap", "shrink_then_wrap"
_FIT_STRATEGIES = ("shrink", "wrap", "shrink_then_wrap")


@dataclass(frozen=True)
class FitResult:
    """Lossless render plan for a field: the full string laid out to fit its box."""

    lines: list[str]
    size: int
    line_height: int


class FitError(RuntimeError):
    """Raised when a string cannot fit its box even at the font floor / max lines."""


class PageOverflowError(FitError):
    """Raised when a fixed-height page's body runs past its bottom margin.

    A subclass of FitError so the overflow backstop (`generators/overflow_check.py`), which collects
    every FitError, reports a page that cannot hold its body alongside a field that cannot fit its box.
    """


def _text_width(text: str, size: int, *, family: str, bold: bool) -> int:
    """Pixel width of `text` at `size`, measured against the bundled font."""
    font = load_font(size, family=family, bold=bold)
    assert_bundled_font(font)
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])


def _fit_error_message(text: str, *, width: int, min_font: int, max_lines: int, fit: str) -> str:
    """Four-element diagnostic body (caller prepends entry/field context)."""
    return (
        "string cannot fit its box losslessly.\n"
        f"  What:     {text!r} exceeds width {width}px at min_font {min_font} "
        f"across max_lines {max_lines} (fit={fit}).\n"
        "  Where:    the field's `field_budgets` entry in its config/layouts/*.yml.\n"
        "  Expected: width >= measured, or larger max_lines, or lower min_font; "
        "fit one of shrink|wrap|shrink_then_wrap.\n"
        "  Recover:  raise `width` (or `max_lines`) for this field in the layout YAML; "
        "never truncate the string."
    )


def _wrap_to_width(text: str, *, width: int, size: int, family: str, bold: bool) -> list[str] | None:
    """Greedy word-wrap at `size`.

    Returns lines each within `width`, or None if a single word cannot fit
    (caller treats None as unfittable — never splits a word / truncates).
    """
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if _text_width(word, size, family=family, bold=bold) > width:
            return None  # unbreakable word wider than the box
        candidate = word if not current else f"{current} {word}"
        if _text_width(candidate, size, family=family, bold=bold) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_text(
    text: str,
    *,
    width: int,
    fit: FitStrategy,
    min_font: int,
    max_lines: int,
    nominal_size: int,
    family: str,
    bold: bool = False,
) -> FitResult:
    """Compute a lossless layout of `text` fitting within `width` px.

    Never truncates. Applies the field's `fit` strategy and raises FitError if
    the full string cannot fit even at the font floor across max_lines.

    Args:
        text: The full string to lay out (rendered verbatim).
        width: Horizontal box in pixels the string must fit within.
        fit: Strategy — "shrink", "wrap", or "shrink_then_wrap".
        min_font: Smallest font size shrinking may reach.
        max_lines: Lines the field may occupy.
        nominal_size: The field's default font size.
        family: Vendored font family to measure against, a key of
            `FONT_FAMILIES`. Required — there is no Python-side default,
            because the family is a layout decision that must be visible in
            the layout YAML.
        bold: Measure with the bold weight.

    Returns:
        FitResult with the laid-out lines, chosen size, and line height.

    Raises:
        FitError: the string cannot fit losslessly.
        ValueError: unknown fit strategy.
    """
    if fit not in _FIT_STRATEGIES:
        raise ValueError(f"unknown fit strategy {fit!r}; allowed: {_FIT_STRATEGIES}")

    def line_height(size: int) -> int:
        fnt = load_font(size, family=family, bold=bold)
        return int(fnt.size) if isinstance(fnt, ImageFont.FreeTypeFont) else size

    # Fits as-is at nominal size on one line -> unchanged (day-one path).
    if _text_width(text, nominal_size, family=family, bold=bold) <= width:
        return FitResult(lines=[text], size=nominal_size, line_height=line_height(nominal_size))

    if fit == "shrink":
        for size in range(nominal_size - 1, min_font - 1, -1):
            if _text_width(text, size, family=family, bold=bold) <= width:
                return FitResult(lines=[text], size=size, line_height=line_height(size))
        raise FitError(
            _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
        )

    if fit == "wrap":
        lines = _wrap_to_width(text, width=width, size=nominal_size, family=family, bold=bold)
        if lines is None or len(lines) > max_lines:
            raise FitError(
                _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
            )
        return FitResult(lines=lines, size=nominal_size, line_height=line_height(nominal_size))

    if fit == "shrink_then_wrap":
        for size in range(nominal_size, min_font - 1, -1):
            if _text_width(text, size, family=family, bold=bold) <= width:
                return FitResult(lines=[text], size=size, line_height=line_height(size))
            wrapped = _wrap_to_width(text, width=width, size=size, family=family, bold=bold)
            if wrapped is not None and len(wrapped) <= max_lines:
                return FitResult(lines=wrapped, size=size, line_height=line_height(size))
        raise FitError(
            _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
        )

    raise ValueError(f"unhandled fit strategy {fit!r}")


def _fit_from_budget(text: str, budget: dict, nominal_size: int, *, family: str, bold: bool) -> FitResult:
    """Run fit_text using a field's budget dict (width/fit/min_font/max_lines)."""
    return fit_text(
        text,
        width=budget["width"],
        fit=budget["fit"],
        min_font=budget["min_font"],
        max_lines=budget["max_lines"],
        nominal_size=nominal_size,
        family=family,
        bold=bold,
    )


def text_ink_box(font: Font, text: str, x: int, y: int) -> tuple[int, int, int, int]:
    """Return the ink box of `text` drawn at (x, y) with its default left-ascender anchor.

    Args:
        font: The font the text is drawn with.
        text: The drawn text.
        x: The x the text is drawn at.
        y: The y the text is drawn at.

    Returns:
        Pixel coordinates (left, top, right, bottom) of the glyphs' bounding box.
    """
    left, top, right, bottom = font.getbbox(text)
    return (x + int(left), y + int(top), x + int(right), y + int(bottom))


def left_draw_x(font: Font, text: str, x: int) -> int:
    """Return the x to draw left-aligned `text` at so that its ink starts at `x`, never left of it.

    A glyph can ink left of its origin: a negative left side bearing (bold Carlito "Y", a "j" or "w" in
    most faces) reaches a pixel or more past the pen position, so text drawn at a margin would cross it.
    `font.getbbox` reports that overhang as a negative left edge; drawing that far to the right puts the
    first ink pixel on `x` exactly. For every other string the left edge is 0 and nothing moves.

    Args:
        font: The font the text is drawn with.
        text: The line to draw.
        x: The left edge the ink must not cross.

    Returns:
        The x to pass to `ImageDraw.text`.
    """
    return x - min(0, int(font.getbbox(text)[0]))


def right_draw_x(font: Font, text: str, x_right: int) -> int:
    """Return the x to draw right-aligned `text` at so that its ink ends at `x_right`, never past it.

    The mirror of `left_draw_x`: `font.getbbox`'s right edge is the last ink pixel, wherever the last
    glyph's advance ends, so drawing at `x_right` minus that edge ends the ink on the margin whatever the
    string. The width a caller records, `right - left` of the same box, is unchanged.

    Args:
        font: The font the text is drawn with.
        text: The line to draw.
        x_right: The right edge the ink must not cross.

    Returns:
        The x to pass to `ImageDraw.text`.
    """
    return x_right - int(font.getbbox(text)[2])


def record_text_ink(ink: InkSink | None, font: Font, text: str, x: int, y: int) -> None:
    """Pass one drawn line's ink box, string, size and weight to `ink`, unless capture is off or the line
    has no glyphs.

    The size and weight are the ones `font` was loaded at (`font_spec`), so the record says what the page
    shows rather than what a caller believes it drew.

    Args:
        ink: The sink, or None when ink is not captured.
        font: The font the line is drawn with.
        text: The drawn line.
        x: The x the line is drawn at.
        y: The y the line is drawn at.

    Raises:
        FontSourceError: The font did not come from `load_font`.
    """
    if ink is not None and text.strip():
        size, bold = font_spec(font)
        ink(text_ink_box(font, text, x, y), text=text, size=size, bold=bold)


def _label_offset(draw: ImageDraw.ImageDraw, font: Font, label: str, lines: list[str], field: str) -> int:
    """Return how far a bound value starts right of its line's left edge, past a label drawn before it.

    A block such as "ABN: {BUSINESS_ABN}" draws the label and the value as one line, so the shaping
    matches the page, but the field's box must hold the value alone (as `pair` records it). The label
    width is measured with the font the fit chose. A wrapped line would put the value's box on two lines,
    so a labelled value must fit on one.

    Args:
        draw: The page's draw handle (for `textlength`).
        font: The fitted font.
        label: The literal text drawn before the value on the same line, or "" for none.
        lines: The fitted lines.
        field: The bound field, named in the diagnostic.

    Returns:
        The label's advance width in pixels (0 without a label).

    Raises:
        FitError: The fitted text does not start with the label on a single line.
    """
    if not label:
        return 0
    if len(lines) != 1 or not lines[0].startswith(label):
        raise FitError(
            f"a labelled value bound to {field} did not fit on one line.\n"
            f"  What:     the line {lines!r} should be the label {label!r} followed by the {field} value on "
            "one line, so the value's own box can be measured where it lands.\n"
            "  Where:    the text block bound to that field (its `content` and `budget`) in the layout YAML.\n"
            "  Expected: a budget that fits label and value on one line, e.g. {fit: shrink, max_lines: 1}.\n"
            "  Recover:  set the block's budget to shrink on one line, or draw label and value as a `pair`."
        )
    return int(draw.textlength(label, font=font))


def _record_site_label(
    recorder: "BoxRecorder | None", field: str | None, font: Font, label: str, x: int, y: int, *, site: bool
) -> None:
    """Record the label a label site drew before its bound value (see `BoxRecorder.record_label`).

    Args:
        recorder: The page's recorder, or None when nothing is captured.
        field: The bound field, or None.
        font: The fitted font the line was drawn in.
        label: The label text the line starts with ("" when the site draws none).
        x: The x the line was drawn at.
        y: The y the line was drawn at.
        site: Whether the block is a label site; nothing is recorded for any other block.
    """
    if not site or recorder is None or field is None:
        return
    recorder.record_label(field, label, text_ink_box(font, label, x, y) if label.strip() else None)


def draw_fitted_left(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
    label: str = "",
    prefix: str | None = None,
    prefix_field: str | None = None,
    ink: InkSink | None = None,
    label_site: bool = False,
) -> int:
    """Left-align `text` at x, fitting it to its budget. Returns the advanced y.

    `line_spacing` overrides the per-line vertical advance (e.g. the layout's
    line_height); when None the font's own height is used. Advancing by a
    caller-supplied line_spacing keeps the single-line case pixel-identical to
    the pre-fit renderer while multi-line wrap pushes following content down.

    `recorder`/`field` are optional draw-time bounding-box capture (opt-in):
    when both are given, the drawn extent is recorded against `field`. `ink`,
    when given, receives each drawn line's own ink box. `label`, when not empty,
    is literal text `text` starts with: the field's box and recorded string then
    cover only what follows it (see `_label_offset`). `label_site` says the block
    is a label site: the label it drew ("" when it drew none) is then recorded
    against `field` with `BoxRecorder.record_label`.

    `prefix`/`prefix_field` are an additional, independent opt-in capture: when
    `text` begins with the literal `prefix` (e.g. a "2x " quantity marker
    concatenated onto a line-item description before fitting), the prefix's
    own sub-box -- measured on the first rendered line, at the font size the
    fit actually chose -- is recorded against `prefix_field`. This never draws
    anything extra; it only measures where the already-drawn prefix landed.
    Silently skipped (no record, no error) if the first rendered line does not
    start with `prefix` -- this should not happen for current callers, since
    word-wrap never splits an unspaced prefix away from its own first word,
    but a future caller passing a prefix containing a space could hit it.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    top = y
    max_width = 0
    first_x = x
    for index, line in enumerate(r.lines):
        line_x = left_draw_x(font, line, x)
        first_x = line_x if index == 0 else first_x
        draw.text((line_x, y), line, font=font, fill=fill)
        record_text_ink(ink, font, line, line_x, y)
        bbox = font.getbbox(line)
        max_width = max(max_width, int(bbox[2] - bbox[0]))
        y += spacing
    if recorder is not None and field is not None:
        offset = _label_offset(draw, font, label, r.lines, field)
        recorder.record(field, (x + offset, top, x + max_width, y), text="\n".join(r.lines)[len(label) :])
        _record_site_label(recorder, field, font, label, first_x, top, site=label_site)
    if recorder is not None and prefix_field is not None and prefix and r.lines:
        if r.lines[0].startswith(prefix):
            prefix_bbox = font.getbbox(prefix)
            prefix_w = int(prefix_bbox[2] - prefix_bbox[0])
            prefix_h = int(prefix_bbox[3] - prefix_bbox[1])
            recorder.record(prefix_field, (x, top, x + prefix_w, top + prefix_h), text=prefix)
    return y


def draw_fitted_center(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    canvas_width: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
    label: str = "",
    ink: InkSink | None = None,
    label_site: bool = False,
) -> int:
    """Center `text` within canvas_width, fitting it to its budget. Returns advanced y.

    See draw_fitted_left for `line_spacing` semantics and the optional
    `recorder`/`field` and `ink` capture.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    top = y
    left = canvas_width
    right = 0
    first_x = 0
    for index, line in enumerate(r.lines):
        bbox = font.getbbox(line)
        w = int(bbox[2] - bbox[0])
        x = (canvas_width - w) // 2
        first_x = x if index == 0 else first_x
        draw.text((x, y), line, font=font, fill=fill)
        record_text_ink(ink, font, line, x, y)
        left = min(left, x)
        right = max(right, x + w)
        y += spacing
    if recorder is not None and field is not None:
        offset = _label_offset(draw, font, label, r.lines, field)
        recorder.record(field, (left + offset, top, right, y), text="\n".join(r.lines)[len(label) :])
        _record_site_label(recorder, field, font, label, first_x, top, site=label_site)
    return y


def draw_fitted_right(
    draw: ImageDraw.ImageDraw,
    text: str,
    x_right: int,
    y: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
    label: str = "",
    ink: InkSink | None = None,
    label_site: bool = False,
) -> int:
    """Right-align `text` to x_right, fitting it to its budget. Returns advanced y.

    See draw_fitted_left for `line_spacing` semantics and the optional
    `recorder`/`field` and `ink` capture.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    top = y
    left = x_right
    first_x = x_right
    for index, line in enumerate(r.lines):
        bbox = font.getbbox(line)
        w = int(bbox[2] - bbox[0])
        line_x = right_draw_x(font, line, x_right)
        first_x = line_x if index == 0 else first_x
        draw.text((line_x, y), line, font=font, fill=fill)
        record_text_ink(ink, font, line, line_x, y)
        left = min(left, x_right - w)
        y += spacing
    if recorder is not None and field is not None:
        offset = _label_offset(draw, font, label, r.lines, field)
        recorder.record(field, (left + offset, top, x_right, y), text="\n".join(r.lines)[len(label) :])
        _record_site_label(recorder, field, font, label, first_x, top, site=label_site)
    return y


def draw_text_left(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: Font,
    fill: str = "black",
    *,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
    ink: InkSink | None = None,
) -> None:
    """Draw text left-aligned at (x, y).

    `recorder`/`field` are optional draw-time bounding-box capture (opt-in):
    when both are given, the drawn extent is recorded against `field`. `ink`,
    when given, receives the line's ink box.
    """
    draw_x = left_draw_x(font, text, x)
    draw.text((draw_x, y), text, font=font, fill=fill)
    record_text_ink(ink, font, text, draw_x, y)
    if recorder is not None and field is not None and text:
        bbox = font.getbbox(text)
        text_width = int(bbox[2] - bbox[0])
        text_height = int(bbox[3] - bbox[1])
        recorder.record(field, (x, y, x + text_width, y + text_height), text=text)


def capture_label_prefixed_value(
    draw: ImageDraw.ImageDraw,
    label: str,
    value: str,
    x: int,
    y: int,
    font: Font,
    *,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
) -> None:
    """Record the box of `value` as drawn immediately after `label` at (x, y).

    Draws nothing itself -- the caller must already have drawn the combined
    `f"{label}{value}"` string as a single `draw.text` call (so glyph shaping
    matches exactly what is on the page; this function never changes pixels).
    It only measures where the `value` substring landed, so a label+value
    string like "Date: 02/03/2023" can carry a ground-truth box for the value
    alone ("02/03/2023"), excluding the label ("Date: ").

    `recorder`/`field` are optional (opt-in): when both are given and `value`
    is non-empty, the value's extent is recorded against `field`.

    Args:
        draw: PIL ImageDraw object (used only for `textlength` measurement).
        label: The literal label text preceding `value` (e.g. "Date: ").
        value: The value substring whose box should be recorded.
        x: The x position the combined `f"{label}{value}"` string was drawn at.
        y: The y position the combined string was drawn at.
        font: The font the combined string was drawn with.
    """
    if recorder is None or field is None or not value:
        return
    label_width = draw.textlength(label, font=font)
    bbox = font.getbbox(value)
    value_width = int(bbox[2] - bbox[0])
    value_height = int(bbox[3] - bbox[1])
    left = int(x + label_width)
    recorder.record(field, (left, y, left + value_width, y + value_height), text=value)


def draw_text_right(
    draw: ImageDraw.ImageDraw,
    text: str,
    x_right: int,
    y: int,
    font: Font,
    fill: str = "black",
    *,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
    ink: InkSink | None = None,
) -> None:
    """Draw text right-aligned to x_right.

    `recorder`/`field` are optional draw-time bounding-box capture (opt-in):
    when both are given, the drawn extent is recorded against `field`. `ink`,
    when given, receives the line's ink box.
    """
    bbox = font.getbbox(text)
    text_width = int(bbox[2] - bbox[0])
    text_height = int(bbox[3] - bbox[1])
    left = x_right - text_width
    draw_x = right_draw_x(font, text, x_right)
    draw.text((draw_x, y), text, font=font, fill=fill)
    record_text_ink(ink, font, text, draw_x, y)
    if recorder is not None and field is not None:
        recorder.record(field, (left, y, x_right, y + text_height), text=text)


def draw_text_center(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    width: int,
    font: Font,
    fill: str = "black",
    *,
    recorder: "BoxRecorder | None" = None,
    field: str | None = None,
) -> None:
    """Draw text centered within given width.

    `recorder`/`field` are optional draw-time bounding-box capture (opt-in):
    when both are given, the drawn extent is recorded against `field`.
    """
    bbox = font.getbbox(text)
    text_width = int(bbox[2] - bbox[0])
    text_height = int(bbox[3] - bbox[1])
    x = (width - text_width) // 2
    draw.text((x, y), text, font=font, fill=fill)
    if recorder is not None and field is not None:
        recorder.record(field, (x, y, x + text_width, y + text_height), text=text)


def draw_separator(
    draw: ImageDraw.ImageDraw,
    y: int,
    width: int,
    margin: int,
    font: Font,
    fill: str = "black",
    char: str = "-",
) -> None:
    """Draw a separator line made of a repeated glyph (a dash, by default)."""
    dash_bbox = font.getbbox(char)
    dash_width = int(dash_bbox[2] - dash_bbox[0])
    count = (width - 2 * margin) // dash_width
    dash = char * count
    draw.text((margin, y), dash, font=font, fill=fill)


def draw_separator_line(
    draw: ImageDraw.ImageDraw,
    x1: int,
    x2: int,
    y: int,
    color: str = "black",
    width: int = 1,
) -> None:
    """Draw a thin horizontal rule from x1 to x2 at vertical position y.

    Args:
        draw: PIL ImageDraw object.
        x1: Left x coordinate.
        x2: Right x coordinate.
        y: Vertical position.
        color: Line color (hex or name).
        width: Line width in pixels.
    """
    draw.line([(x1, y), (x2, y)], fill=color, width=width)


def draw_line_item(
    draw: ImageDraw.ImageDraw,
    desc: str,
    amount: str,
    y: int,
    font: Font,
    margin: int,
    width: int,
    fill: str = "black",
    *,
    recorder: "BoxRecorder | None" = None,
    amount_field: str | None = None,
) -> None:
    """Draw a receipt line item: left-aligned description, right-aligned amount.

    `desc` is always a static label at every current call site (e.g. "SUBTOTAL",
    "GST", "TOTAL"), never a ground-truth field, so only the amount cell is
    capturable. `recorder`/`amount_field` are optional (opt-in): when both are
    given, the amount's drawn extent is recorded against `amount_field`.
    """
    draw.text((left_draw_x(font, desc, margin), y), desc, font=font, fill=fill)
    draw_text_right(
        draw,
        amount,
        x_right=width - margin,
        y=y,
        font=font,
        fill=fill,
        recorder=recorder,
        field=amount_field,
    )


def fmt_amount(amount: Decimal | float | int) -> str:
    """Format a numeric amount as $X,XXX.XX."""
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${d:,.2f}"


# --- ABN Validation (Australian Business Number checksum) ---

_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]


def validate_abn(abn: str) -> bool:
    """Validate an Australian Business Number using the official checksum algorithm.

    Args:
        abn: ABN string, with or without spaces (e.g. "62 495 580 190" or "62495580190").

    Returns:
        True if checksum is valid.
    """
    digits_str = abn.replace(" ", "")
    if len(digits_str) != 11 or not digits_str.isdigit():
        return False
    digits = [int(d) for d in digits_str]
    digits[0] -= 1  # Subtract 1 from first digit, per the published algorithm
    total = sum(d * w for d, w in zip(digits, _ABN_WEIGHTS, strict=True))
    return total % 89 == 0


def _checksum_abn(randint: Callable[[int, int], int]) -> str:
    """Draw nine digits with `randint` and prefix the check digits that make them a valid ABN."""
    base = [randint(0, 9) for _ in range(9)]
    for d0 in range(1, 10):
        for d1 in range(0, 10):
            digits = [d0, d1, *base]
            test = digits.copy()
            test[0] -= 1
            total = sum(d * w for d, w in zip(test, _ABN_WEIGHTS, strict=True))
            if total % 89 == 0:
                s = "".join(str(d) for d in digits)
                return f"{s[:2]} {s[2:5]} {s[5:8]} {s[8:11]}"
    msg = "Failed to generate valid ABN"
    raise RuntimeError(msg)


def generate_abn(*, registered: Container[str]) -> str:
    """Generate a valid 11-digit ABN with correct checksum that no real business holds.

    The first draw comes from the module-global RNG. A draw found in `registered` (the public ABN
    register, see generators/abn_register.py) is redrawn, so an invented business never prints a
    real ABN. Redraws come from a stream seeded by the rejected ABN, not the global RNG, so
    screening moves only the ABNs that were registered: every other draw, earlier or later, is
    the one an unscreened run makes.

    Args:
        registered: The ABNs to avoid, tested with `in` on the spaced form.

    Returns:
        ABN formatted as "XX XXX XXX XXX".

    Raises:
        RuntimeError: every draw within the retry budget was registered.
    """
    max_attempts = 100
    abn = _checksum_abn(random.randint)
    redraw = random.Random(int(abn.replace(" ", "")))
    for _ in range(max_attempts):
        if abn not in registered:
            return abn
        abn = _checksum_abn(redraw.randint)
    raise RuntimeError(
        "generate_abn: exhausted retry budget without an unregistered ABN.\n"
        f"  What:     {max_attempts} checksum-valid draws were all found in the ABN register.\n"
        "  Where:    the register at 'abn_register.path' in config/data_pools.yml.\n"
        "  Expected: a register of issued ABNs, a small fraction of the checksum-valid space.\n"
        "  Recover:  rebuild the register with scripts/build_abn_register.py from the data.gov.au "
        "extract; a register holding nearly every ABN is a build fault."
    )


# --- GST Calculation ---


def calculate_gst_inclusive(total: Decimal) -> tuple[Decimal, Decimal]:
    """Calculate GST from a GST-inclusive total.

    GST = total / 11 (rounded to 2dp).
    Ex-GST = total - GST.

    Args:
        total: GST-inclusive total amount.

    Returns:
        (gst_amount, ex_gst_amount) both rounded to 2 decimal places.
    """
    gst = (total / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ex_gst = total - gst
    return gst, ex_gst


def calculate_gst_exclusive(subtotal: Decimal) -> tuple[Decimal, Decimal]:
    """Calculate GST from an ex-GST subtotal.

    GST = subtotal * 0.10.
    Total = subtotal + GST.

    Args:
        subtotal: Ex-GST subtotal.

    Returns:
        (gst_amount, gst_inclusive_total) both rounded to 2 decimal places.
    """
    gst = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = subtotal + gst
    return gst, total
