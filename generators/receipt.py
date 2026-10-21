"""Receipt renderer — a thin adapter over the declarative layout engine.

Visual DNA per receipt format now lives in config/layouts/receipts.yml as a
`body:` tree of layout primitives; this module only sets up the page, delegates
to the DSL, and crops the result. The values a receipt draws that exist nowhere
in ground truth (POS time/register/staff, the receipt number and the sixteen
EFTPOS terminal-slip values) come from the `receipt_pos` and `receipt_payment`
field providers
(generators/layout_dsl/field_providers.py), which each layout declares under
`field_providers:`; the line items come from the `receipt_line_items` row
provider (generators/layout_dsl/providers.py).
"""

from PIL import Image, ImageDraw

from generators.exporters.geometry import BoxRecorder, InkRecord, records_ink, rescale_vertical
from generators.labels.keys import LabelChoice
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.overlay.engine import apply_overlay

_LAYOUT_PATH = "config/layouts/receipts.yml"


def render_receipt(
    entry: dict,
    layout: dict,
    *,
    distractors: bool,
    labels: LabelChoice | None = None,
    geometry_out: dict | None = None,
    ink_out: list[InkRecord] | None = None,
) -> Image.Image:
    """Render a receipt image from ground truth entry and layout config.

    Receipts are variable-height: the body is drawn onto a canvas
    `canvas_ceiling` tall, then cropped to the y the engine returns plus the
    bottom margin, and the recorder's boxes are rescaled to that final height.
    `render_body` is canvas-agnostic, so this needs no engine support.

    Args:
        entry: Ground truth YAML entry with 'fields', 'case_id' and 'layout'.
        layout: Layout registry entry carrying a `body:` tree, `width`,
            `margin`, `content_width` and `canvas_ceiling`.
        distractors: Whether this render draws distractors.
        labels: The render's label choice (`generators.labels.keys.LabelChoice`), or None for a plain or
            distractor render, whose label sites print their own wording. A choice requires
            `distractors=True`.
        geometry_out: Optional dict (opt-in); when given, populated in place
            with {"width", "height", "boxes", "texts", "ink", "blocks", "labels", "regions"}:
            each captured field's normalised bounding box on the rendered page, and every drawn
            line's and mark's pixel ink with its role (internal; not exported).
        ink_out: Optional list (opt-in); when given, extended in place with
            the same ink records, for a caller that needs the ink without the
            scored geometry. A distractor render records ink whether or not
            either is given; nothing is written anywhere.

    Returns:
        PIL Image of the rendered receipt, cropped to its content.
    """
    layout_id = str(entry.get("layout", ""))
    width = int(layout["width"])
    ceiling = int(layout["canvas_ceiling"])
    margin = int(layout["margin"])

    image = Image.new("RGB", (width, ceiling), "white")
    draw = ImageDraw.Draw(image)
    recorder = BoxRecorder(width, ceiling) if records_ink(distractors, geometry_out, ink_out) else None
    enriched: dict = {}

    end_y = render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=_LAYOUT_PATH,
        draw=draw,
        region=Region(x=margin, width=int(layout["content_width"])),
        y=margin,
        distractors=distractors,
        recorder=recorder,
        entry_out=enriched,
        labels=labels,
    )

    # The overlay runs on the flat page before the crop, as on every distractor render. Whether a receipt
    # may carry one is config/distractors.yml's to say (overlay.protect); a refused overlay fails here.
    if distractors and recorder is not None:
        apply_overlay(
            image,
            layout,
            enriched,
            layout_id=layout_id,
            layout_path=_LAYOUT_PATH,
            doc_type="receipt",
            recorder=recorder,
        )

    height = min(end_y + margin, ceiling)
    image = image.crop((0, 0, width, height))

    if recorder is not None and geometry_out is not None:
        geometry_out["width"] = width
        geometry_out["height"] = height
        geometry_out["boxes"] = rescale_vertical(recorder.as_dict(), old_height=ceiling, new_height=height)
        geometry_out["texts"] = recorder.texts()
        # Labels and regions stay in pixels, as ink does; like ink they are internal, never exported.
        geometry_out["labels"] = recorder.labels()
        geometry_out["regions"] = recorder.regions()
        # Ink stays in pixels: cropping the bottom moves no drawn line.
        geometry_out["ink"] = recorder.ink_records()
        geometry_out["blocks"] = recorder.block_records()
    if recorder is not None and ink_out is not None:
        ink_out.extend(recorder.ink_records())
    return image
