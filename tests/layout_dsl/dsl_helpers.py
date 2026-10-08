"""Shared builders for the layout DSL tests: a real layout with a swapped body, and a canvas to draw on."""

import copy

from PIL import Image, ImageDraw

from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks, render_body
from generators.loader import load_layout_registry
from tests.conftest import REPO_ROOT

RECEIPTS_PATH = REPO_ROOT / "config" / "layouts" / "receipts.yml"
LAYOUT_ID = "receipt_thermal_80mm"
PAGE_WIDTH = 420
MARGIN = 12

# Invented values: CASE999 and this date appear nowhere in ground_truth/.
ENTRY: dict[str, str | dict[str, str]] = {
    "case_id": "CASE999",
    "layout": LAYOUT_ID,
    "fields": {"INVOICE_DATE": "01/02/2031", "TOTAL_AMOUNT": "40.00", "GST_AMOUNT": "3.64"},
}


def real_layout(body: list, field_providers: list | None = None) -> dict:
    """Return a deep copy of a real receipt layout with `body`, providers and distractor items replaced.

    The distractor items are emptied with the providers: the real layout's items belong to its own body.

    Args:
        body: The block list to render.
        field_providers: The `field_providers:` list; an empty list when omitted.

    Returns:
        A layout dict carrying the real defaults, font sizes and geometry.
    """
    layout = copy.deepcopy(load_layout_registry(RECEIPTS_PATH)[LAYOUT_ID])
    layout["body"] = body
    layout["field_providers"] = field_providers if field_providers is not None else []
    layout["distractor_items"] = []
    return layout


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Return a blank white page and a drawing surface over it."""
    image = Image.new("RGB", (PAGE_WIDTH, 600), "white")
    return image, ImageDraw.Draw(image)


def make_ctx(layout: dict, entry: dict | None = None, *, walker: bool = True) -> RenderContext:
    """Build a RenderContext over a fresh canvas.

    Args:
        layout: The layout dict.
        entry: The entry; defaults to `ENTRY`.
        walker: Inject the engine's walker as `render_children` when true.

    Returns:
        A context spanning the page's content region.
    """
    _, draw = canvas()
    return RenderContext(
        draw=draw,
        entry=entry if entry is not None else ENTRY,
        layout=layout,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        region=Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN),
        render_children=render_blocks if walker else None,
    )


def render(layout: dict, entry: dict | None = None, *, y: int = 0) -> int:
    """Render a layout's body through `render_body` and return the final y."""
    _, draw = canvas()
    return render_body(
        layout,
        entry if entry is not None else ENTRY,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        draw=draw,
        region=Region(x=MARGIN, width=PAGE_WIDTH - 2 * MARGIN),
        y=y,
        distractors=False,
    )


def make_drawing_ctx(
    layout: dict,
    entry: dict | None = None,
    *,
    region: Region | None = None,
    page_width: int = PAGE_WIDTH,
    recorder: BoxRecorder | None = None,
    walker: bool = True,
) -> tuple[RenderContext, Image.Image]:
    """Build a RenderContext and hand back the page image it draws on, for pixel assertions.

    Args:
        layout: The layout dict.
        entry: The entry; defaults to `ENTRY`.
        region: The drawing region; defaults to the page minus `MARGIN` each side.
        page_width: Width of the blank page in pixels.
        recorder: Optional draw-time box recorder.
        walker: Inject the engine's walker as `render_children` when true.

    Returns:
        The context and the white page image it draws onto.
    """
    image = Image.new("RGB", (page_width, 600), "white")
    ctx = RenderContext(
        draw=ImageDraw.Draw(image),
        entry=entry if entry is not None else ENTRY,
        layout=layout,
        layout_id=LAYOUT_ID,
        layout_path=str(RECEIPTS_PATH),
        region=region if region is not None else Region(x=MARGIN, width=page_width - 2 * MARGIN),
        recorder=recorder,
        render_children=render_blocks if walker else None,
    )
    return ctx, image
