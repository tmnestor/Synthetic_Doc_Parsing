"""Inline code marks: barcodes, QR codes and generated logos.

These primitives draw realistic marks that carry no scored field. Libraries
compute module patterns only; every module is painted through the page's own
`ImageDraw`, so output is deterministic and nothing is pasted or resampled. A
mark never clips: one wider than its region fails fast.

Every mark records its exact drawn rectangle (symbol plus caption) as distractor
ink flagged as a mark, and a caption also records its own line of distractor text,
so the density metrics can count a mark's area apart from text.
"""

import math
from typing import Any, NamedTuple

import barcode
import segno
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter
from PIL.ImageFont import FreeTypeFont, ImageFont

from generators.common import record_text_ink, text_ink_box
from generators.layout_dsl.binding import interpolate, is_present, referenced_fields
from generators.layout_dsl.code128 import encode_code128
from generators.layout_dsl.context import RenderContext
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.primitives_text import font_for, line_advance, resolve_role

SYMBOLOGIES = ("code128", "ean13")
ERROR_LEVELS = ("l", "m", "q", "h")
LOGO_STYLES = ("circle", "square", "chevron")


class CodeBlockError(RuntimeError):
    """Raised when a code or logo block cannot be drawn as configured."""


def _fail(ctx: RenderContext, kind: str, what: str, expected: str, recover: str) -> CodeBlockError:
    return CodeBlockError(
        f"Cannot draw '{kind}' block.\n"
        f"  What:     {what}\n"
        f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _resolve_content(block: dict, ctx: RenderContext, kind: str, key: str = "content") -> str:
    template = str(block[key])
    absent = [name for name in referenced_fields(template) if not is_present(ctx.entry["fields"], name)]
    if absent:
        raise _fail(
            ctx,
            kind,
            f"'{key}' {block[key]!r} references {', '.join(absent)}, which this entry does not carry.",
            "every field the template references present for each entry that draws this block.",
            f"gate the block with when: <FIELD>, or point '{key}' at fields this entry carries.",
        )
    text = interpolate(template, ctx.entry["fields"]).strip()
    if not text:
        raise _fail(
            ctx,
            kind,
            f"'{key}' {block[key]!r} resolved to {text!r} for this entry.",
            f"a '{key}' that resolves to non-empty text for every entry that draws this block.",
            f"gate the block with when: <FIELD>, or give '{key}' some text or a present field.",
        )
    return text


def _param(block: dict, ctx: RenderContext, key: str) -> Any:
    """Resolve a layout-default parameter: the block's own key, then `defaults:`, else fail fast.

    Each key is spelled as a literal at its own `resolve_param` call because the
    defaults-coverage guard audits those literals against `PARAMETER_DEFAULTS`.

    Raises:
        ValueError: If `key` is not one of the parameters these primitives resolve.
    """
    where = {"layout_id": ctx.layout_id, "layout_path": ctx.layout_path}
    if key == "align":
        return resolve_param(block, ctx.layout, "align", **where)
    if key == "color":
        return resolve_param(block, ctx.layout, "color", **where)
    if key == "role":
        return resolve_param(block, ctx.layout, "role", **where)
    raise ValueError(
        f"_param does not resolve {key!r}; add a literal branch for it in primitives_codes.py."
    )


def _rgb(value: Any, ctx: RenderContext, key: str, kind: str) -> tuple[int, ...]:
    """Resolve a colour to RGB(A), so equal colours compare equal however spelled."""
    try:
        return ImageColor.getrgb(str(value))
    except ValueError as err:
        raise _fail(
            ctx,
            kind,
            f"'{key}' {value!r} is not a colour.",
            "a colour name or hex string such as white or #ffffff.",
            "correct the colour in the layout YAML.",
        ) from err


def _x_for(width: int, block: dict, ctx: RenderContext, kind: str) -> int:
    region = ctx.region
    if width > region.width:
        raise _fail(
            ctx,
            kind,
            f"the {kind} is {width}px wide but its region is {region.width}px.",
            "a mark no wider than its region; renderers never clip.",
            "reduce the module size or quiet zone, shorten the content, or widen the region.",
        )
    align = str(_param(block, ctx, "align"))
    if align == "left":
        return region.x
    if align == "right":
        return region.right - width
    return region.x + (region.width - width) // 2


def ean13_check_digit(digits: str) -> str:
    """Return the EAN-13 check digit for the first 12 digits of `digits`."""
    total = sum(int(ch) * (3 if index % 2 else 1) for index, ch in enumerate(digits[:12]))
    return str((10 - total % 10) % 10)


def _check_symbology(content: str, symbology: str, ctx: RenderContext) -> None:
    if symbology == "code128" and not all(32 <= ord(ch) < 127 for ch in content):
        raise _fail(
            ctx,
            "barcode",
            f"code128 content {content!r} has characters outside printable ASCII.",
            "content made of ASCII characters 32 to 126.",
            "transliterate the content, or use a qrcode block for arbitrary text.",
        )
    if symbology == "ean13" and not (content.isdigit() and len(content) in (12, 13)):
        raise _fail(
            ctx,
            "barcode",
            f"ean13 content {content!r} is not 12 or 13 digits.",
            "12 digits (the check digit is computed) or 13 digits including it.",
            "point content at a 12-digit field, or use symbology: code128.",
        )
    if symbology == "ean13" and len(content) == 13 and content[12] != ean13_check_digit(content):
        raise _fail(
            ctx,
            "barcode",
            f"ean13 content {content!r} has check digit {content[12]}, expected "
            f"{ean13_check_digit(content)}.",
            "13 digits whose last digit is the EAN-13 check digit, or 12 digits so it is computed.",
            "drop the 13th digit to let the check digit be computed, or correct it.",
        )


class Caption(NamedTuple):
    """A measured caption: its text, font, ink width and left overhang."""

    text: str
    font: FreeTypeFont | ImageFont
    width: int
    overhang: int


def measure_caption(
    text: str, block: dict, layout: dict, draw: ImageDraw.ImageDraw, *, layout_id: str, layout_path: str
) -> Caption:
    """Measure a caption's true ink extent at the block's role size, before drawing.

    Width is the larger of the advance and the ink right edge, plus any left
    bearing that pokes left of the draw origin (`overhang`). Shared by the
    renderer and by validate's barcode width check, so the two cannot disagree.

    Args:
        text: The caption text.
        block: The code block, whose `role` (or the layout default) sets the size.
        layout: The resolved layout dict.
        draw: Any drawing surface; measurement draws nothing.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        The measured caption.
    """
    role = resolve_param(block, layout, "role", layout_id=layout_id, layout_path=layout_path)
    size = resolve_role(layout, str(role))
    font = font_for(layout, block, size, layout_id=layout_id, layout_path=layout_path)
    ink = draw.textbbox((0, 0), text, font=font)
    overhang = int(max(0, -ink[0]))
    width = math.ceil(max(draw.textlength(text, font=font), ink[2])) + overhang
    return Caption(text, font, width, overhang)


def _measure_caption(text: str, block: dict, ctx: RenderContext) -> Caption:
    return measure_caption(
        text, block, ctx.layout, ctx.draw, layout_id=ctx.layout_id, layout_path=ctx.layout_path
    )


class BarcodeGeometry(NamedTuple):
    """A barcode's module pattern and horizontal extent, computed before anything is drawn."""

    modules: str
    bars_span: int  # bars plus both quiet zones, in pixels
    caption: Caption | None  # the human-readable digits, when the block prints them

    @property
    def span(self) -> int:
        """The mark's full width: the wider of the bars and the caption."""
        return max(self.bars_span, self.caption.width if self.caption is not None else 0)


def barcode_geometry(
    content: str,
    block: dict,
    layout: dict,
    draw: ImageDraw.ImageDraw,
    *,
    layout_id: str,
    layout_path: str,
) -> BarcodeGeometry:
    """Compute a barcode's modules and width for `content`, as `draw_barcode` draws it.

    Args:
        content: The resolved content, already checked against the block's symbology.
        block: The `barcode` block (`symbology`, `module_px`, `quiet_zone`, `human_readable`).
        layout: The resolved layout dict.
        draw: Any drawing surface, for measuring the caption.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        The module pattern, the bars' span with quiet zones, and the measured caption if printed.
    """
    if str(block["symbology"]) == "code128":
        # In-repo: python-barcode drops a leading "99" digit pair (see code128.py).
        modules, caption_text = encode_code128(content), content
    else:
        code = barcode.get_barcode_class(str(block["symbology"]))(content, writer=None)
        modules, caption_text = code.build()[0], code.get_fullcode()
    unit = int(block["module_px"])
    bars_span = len(modules) * unit + 2 * int(block["quiet_zone"]) * unit
    caption = None
    if block["human_readable"]:
        caption = measure_caption(
            caption_text, block, layout, draw, layout_id=layout_id, layout_path=layout_path
        )
    return BarcodeGeometry(modules, bars_span, caption)


Box = tuple[int, int, int, int]


def _draw_caption(caption: Caption, span_x: int, span: int, y: int, color: Any, ctx: RenderContext) -> Box:
    """Draw a measured caption centred within the span that starts at `span_x`; return its ink box."""
    x = span_x + (span - caption.width) // 2 + caption.overhang
    ctx.draw.text((x, y), caption.text, font=caption.font, fill=color)
    record_text_ink(ctx.ink("distractor"), caption.font, caption.text, x, y)
    return text_ink_box(caption.font, caption.text, x, y)


def _record_mark(ctx: RenderContext, symbol: Box, payload: str, caption: Box | None = None) -> None:
    """Record a code mark's drawn rectangle, its symbol united with its caption's ink, as distractor.

    The record carries the mark's payload as its text (a barcode's digits, a QR code's content, a logo's
    initials), so a number a mark encodes counts as printed wherever the page is read back.
    """
    box = symbol
    if caption is not None and caption[2] > caption[0]:
        box = (
            min(symbol[0], caption[0]),
            min(symbol[1], caption[1]),
            max(symbol[2], caption[2]),
            max(symbol[3], caption[3]),
        )
    if ctx.ink_recorder is not None:
        ctx.ink_recorder.record_ink(box, "distractor", text=payload, size=0, bold=False, mark=True)


def draw_barcode(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a Code 128 or EAN-13 barcode, optionally with its digits `caption_gap` px beneath.

    Args:
        block: The `barcode` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the content is absent, outside the symbology, or the
            mark is wider than its region.
    """
    content = _resolve_content(block, ctx, "barcode")
    _check_symbology(content, str(block["symbology"]), ctx)
    geometry = barcode_geometry(
        content, block, ctx.layout, ctx.draw, layout_id=ctx.layout_id, layout_path=ctx.layout_path
    )
    modules, bars_span, caption = geometry
    span = geometry.span
    unit = int(block["module_px"])
    quiet = int(block["quiet_zone"]) * unit
    height = int(block["height"])
    color = _rgb(_param(block, ctx, "color"), ctx, "color", "barcode")
    span_x = _x_for(span, block, ctx, "barcode")
    left = span_x + (span - bars_span) // 2 + quiet
    for index, bit in enumerate(modules):
        if bit == "1":
            x = left + index * unit
            ctx.draw.rectangle((x, y, x + unit - 1, y + height - 1), fill=color)
    # Every symbology's pattern starts and ends with a bar, so the bars' extent is the pattern's.
    first, last = modules.index("1"), modules.rindex("1")
    bars: Box = (left + first * unit, y, left + (last + 1) * unit, y + height)
    y += height
    caption_box = None
    if caption is not None:
        # The digits sit `caption_gap` px below the bars, as a printer leaves white between them.
        y += int(block["caption_gap"])
        caption_box = _draw_caption(caption, span_x, span, y, color, ctx)
        y += line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    _record_mark(ctx, bars, content, caption_box)
    return y


def draw_qrcode(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a QR code, optionally with a caption beneath.

    Args:
        block: The `qrcode` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the content is absent, does not fit any QR version at
            the error level, or the symbol or caption is wider than its region.
    """
    content = _resolve_content(block, ctx, "qrcode")
    level = str(block["error_level"])
    try:
        matrix = segno.make(content, error=level, micro=False, boost_error=False).matrix
    except segno.DataOverflowError as err:
        raise _fail(
            ctx,
            "qrcode",
            f"{len(content)} characters do not fit any QR version at error level {level!r}.",
            "content short enough for a version-40 symbol at this error level.",
            "shorten the content or lower error_level.",
        ) from err
    unit = int(block["module_px"])
    quiet = int(block["quiet_zone"]) * unit
    side = len(matrix) * unit
    symbol_span = side + 2 * quiet
    caption = None
    span = symbol_span
    if "caption" in block:
        caption = _measure_caption(_resolve_content(block, ctx, "qrcode", key="caption"), block, ctx)
        span = max(symbol_span, caption.width)
    span_x = _x_for(span, block, ctx, "qrcode")
    left = span_x + (span - symbol_span) // 2 + quiet
    top = y + quiet
    color = _rgb(_param(block, ctx, "color"), ctx, "color", "qrcode")
    for row_index, row in enumerate(matrix):
        for col_index, dark in enumerate(row):
            if dark:
                x, cell_y = left + col_index * unit, top + row_index * unit
                ctx.draw.rectangle((x, cell_y, x + unit - 1, cell_y + unit - 1), fill=color)
    symbol: Box = (left, top, left + side, top + side)
    y = top + side + quiet
    caption_box = None
    if caption is not None:
        caption_box = _draw_caption(caption, span_x, span, y, color, ctx)
        y += line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    _record_mark(ctx, symbol, content, caption_box)
    return y


def initials(name: str) -> str:
    """Return up to two initials: the first letter of each of the first two words containing one."""
    letters = [
        next(ch for ch in word if ch.isalpha()) for word in name.split() if any(c.isalpha() for c in word)
    ]
    return "".join(letters[:2]).upper()


def _chevron_depth(size: int) -> int:
    """Return how far a chevron's point and notch reach in from its edges."""
    return size // 6


def _draw_shape(draw: ImageDraw.ImageDraw, style: str, x: int, y: int, size: int, fill: Any) -> None:
    """Paint a logo shape that stays within the `size` x `size` box at (x, y)."""
    box = (x, y, x + size - 1, y + size - 1)
    if style == "circle":
        draw.ellipse(box, fill=fill)
    elif style == "square":
        draw.rectangle(box, fill=fill)
    else:
        depth = _chevron_depth(size)
        draw.polygon(
            [
                (x, y),
                (x + size - 1 - depth, y),
                (x + size - 1, y + size // 2),
                (x + size - 1 - depth, y + size - 1),
                (x, y + size - 1),
                (x + depth, y + size // 2),
            ],
            fill=fill,
        )


def draw_logo(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a generated logo: a filled shape with the name's initials inside.

    Args:
        block: The `logo` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the name is absent or has no letters, the initials
            would spill outside the shape, or the mark is wider than its region.
    """
    name = _resolve_content(block, ctx, "logo")
    letters = initials(name)
    if not letters:
        raise _fail(
            ctx,
            "logo",
            f"name {name!r} has no letters to take initials from.",
            "a name with at least one word containing a letter.",
            "point content at a business-name field.",
        )
    size = int(block["size"])
    style = str(block["style"])
    x = _x_for(size, block, ctx, "logo")
    fill = _param(block, ctx, "color")
    text_color = str(block["text_color"])
    if _rgb(fill, ctx, "color", "logo") == _rgb(text_color, ctx, "text_color", "logo"):
        raise _fail(
            ctx,
            "logo",
            f"text_color {text_color!r} is the same colour as the shape fill {fill!r}, so the initials "
            "would be invisible.",
            "a text_color that differs from the shape colour.",
            "change text_color, or the block's color (or the layout default colour).",
        )
    fitted = _fit_initials(letters, block, ctx, style, size)
    if fitted is None:
        floor = int(block["min_text_size"])
        raise _fail(
            ctx,
            "logo",
            f"the initials {letters!r} do not fit inside a {size}px {style}, {block['inset']}px clear of "
            f"its rim, at any bold size from {size // 2}px down to min_text_size {floor}px.",
            "initials whose ink lies wholly inside the shape, inset from its rim, at or above "
            "min_text_size.",
            "enlarge size, lower min_text_size or inset, choose style: square, or use a narrower face "
            "with 'family:'.",
        )
    font, (text_x, text_y) = fitted
    _draw_shape(ctx.draw, style, x, y, size, fill)
    ctx.draw.text((x + text_x, y + text_y), letters, font=font, fill=text_color)
    # The initials lie wholly inside the shape (checked above), so the shape's box is the mark's.
    _record_mark(ctx, (x, y, x + size, y + size), letters)
    return y + size


def _fit_initials(
    letters: str, block: dict, ctx: RenderContext, style: str, size: int
) -> tuple[FreeTypeFont | ImageFont, tuple[float, float]] | None:
    """Find the largest bold size, from `size // 2` down to `min_text_size`, whose initials fit the shape.

    Returns:
        The font and the text origin relative to the shape, or None if even the floor spills.
    """
    for text_size in range(size // 2, int(block["min_text_size"]) - 1, -1):
        font = font_for(
            ctx.layout, block, text_size, bold=True, layout_id=ctx.layout_id, layout_path=ctx.layout_path
        )
        left, top, right, bottom = ctx.draw.textbbox((0, 0), letters, font=font)
        text_x = (size - (right - left)) // 2 - left
        text_y = (size - (bottom - top)) // 2 - top
        if style == "chevron":
            # The notch bites into the left edge, so centre in the part that is solid.
            text_x += _chevron_depth(size) // 2
        if not _initials_spill(letters, font, style, size, (text_x, text_y), int(block["inset"])):
            return font, (text_x, text_y)
    return None


def _initials_spill(
    letters: str,
    font: FreeTypeFont | ImageFont,
    style: str,
    size: int,
    origin: tuple[float, float],
    inset: int,
) -> bool:
    """Report whether any pixel of the initials falls outside the shape inset by `inset` px.

    Both are rasterised; the shape is eroded by `inset` px on every side (a minimum filter), so the
    initials keep that clear gap from the rim. An inset of 0 tests the shape itself. The filter's window
    is a square, not a disc, so the gap is at least `inset` px measured straight across the rim and up to
    about 1.4 times that (the square's half-diagonal) where the rim runs diagonally, as on a circle at 45
    degrees or a chevron's notch.
    """
    pad = size
    canvas = (size + 2 * pad, size + 2 * pad)
    shape = Image.new("L", canvas, 0)
    _draw_shape(ImageDraw.Draw(shape), style, pad, pad, size, 255)
    if inset:
        shape = shape.filter(ImageFilter.MinFilter(2 * inset + 1))
    ink = Image.new("L", canvas, 0)
    ImageDraw.Draw(ink).text((pad + origin[0], pad + origin[1]), letters, font=font, fill=255)
    return ImageChops.subtract(ink, shape).getbbox() is not None
