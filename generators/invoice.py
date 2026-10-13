"""Invoice renderer — a thin adapter over the declarative layout engine.

Renders A4-format Australian tax invoices carrying the mandatory fields:
1. "Tax Invoice" header  2. Seller identity  3. ABN
4. Issue date  5. Item descriptions with qty/price
6. GST amount  7. Taxable sale extent

The visual DNA per invoice format now lives in config/layouts/invoices.yml as a
`body:` tree of layout primitives; this module only sets up the page and
delegates to the DSL. The one value an invoice draws that exists nowhere in
ground truth — the ex-GST subtotal — comes from the `computed_totals` field
provider (generators/layout_dsl/field_providers.py), which each layout declares
under `field_providers:`; the line items come from the `pipe_fields` row
provider (generators/layout_dsl/providers.py).
"""

from pathlib import Path

from PIL import Image, ImageDraw

from generators.common import PageOverflowError
from generators.exporters.geometry import BoxRecorder, InkRecord, records_ink
from generators.labels.keys import LabelChoice
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.overlay.engine import apply_overlay

_LAYOUT_PATH = "config/layouts/invoices.yml"
_LAYOUT_FILE = Path(__file__).resolve().parent.parent / _LAYOUT_PATH


def render_invoice(
    entry: dict,
    layout: dict,
    *,
    distractors: bool,
    labels: LabelChoice | None = None,
    geometry_out: dict | None = None,
    ink_out: list[InkRecord] | None = None,
) -> Image.Image:
    """Render a compliant Australian tax invoice from ground truth and layout config.

    Invoices are fixed-page, so this is page setup and nothing else: no crop and
    no vertical rescale (contrast generators/receipt.py, which is
    variable-height).

    Args:
        entry: Ground truth YAML entry with 'fields' dict and 'layout' id.
        layout: Layout registry entry carrying a `body:` tree,
            'page_dimensions', 'margin' and 'content_width'.
        distractors: Whether this render draws distractors.
        labels: The render's label choice (`generators.labels.keys.LabelChoice`), or None for a plain or
            distractor render, whose label sites print their own wording. A choice requires
            `distractors=True`.
        geometry_out: Optional dict (opt-in); when given, populated in place
            with {"width", "height", "boxes", "texts", "ink", "blocks", "labels", "regions"}:
            each captured field's normalised bounding box on the rendered page, and every drawn
            line's and mark's pixel ink with its role, overlay marks included (internal; not exported).
        ink_out: Optional list (opt-in); when given, extended in place with
            the same ink records, for a caller that needs the ink without the
            scored geometry. A distractor render records ink whether or not
            either is given; nothing is written anywhere.

    Returns:
        PIL Image of the rendered invoice.

    Raises:
        PageOverflowError: The body ends below the page's bottom margin. The page is a fixed A4 sheet,
            so a body that does not fit would be clipped; it fails instead, naming the layout.
        OverlayError: A distractor render's overlay mark cannot be drawn legally (it would meet a
            protected box, leave the page inset or leave highlighted text illegible).
    """
    dims = layout["page_dimensions"]
    width, height = int(dims["width"]), int(dims["height"])
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    recorder = BoxRecorder(width, height) if records_ink(distractors, geometry_out, ink_out) else None
    layout_id = str(entry.get("layout", ""))
    enriched: dict = {}

    end = render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=_LAYOUT_PATH,
        draw=draw,
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        distractors=distractors,
        recorder=recorder,
        entry_out=enriched,
        labels=labels,
    )
    _check_fits_the_page(end, layout, entry, distractors=distractors)
    # The overlay draws on the finished page, and records its ink before geometry is handed back, so
    # density is measured on the composited page. A plain render never draws one.
    if distractors and recorder is not None:
        apply_overlay(
            image,
            layout,
            enriched,
            layout_id=layout_id,
            layout_path=_LAYOUT_FILE,
            doc_type="invoice",
            recorder=recorder,
        )

    if recorder is not None and geometry_out is not None:
        geometry_out["width"] = width
        geometry_out["height"] = height
        geometry_out["boxes"] = recorder.as_dict()
        geometry_out["texts"] = recorder.texts()
        # Labels and regions stay in pixels, as ink does; like ink they are internal, never exported.
        geometry_out["labels"] = recorder.labels()
        geometry_out["regions"] = recorder.regions()
        geometry_out["ink"] = recorder.ink_records()
        geometry_out["blocks"] = recorder.block_records()
    if recorder is not None and ink_out is not None:
        ink_out.extend(recorder.ink_records())
    return image


def _check_fits_the_page(end: int, layout: dict, entry: dict, *, distractors: bool) -> None:
    """Refuse a body whose cursor ends below the bottom margin of the fixed-height page."""
    height = int(layout["page_dimensions"]["height"])
    margin = int(layout["margin"])
    limit = height - margin
    if end <= limit:
        return
    layout_id = str(entry.get("layout", "<layout>"))
    mode = "with distractors" if distractors else "plain"
    raise PageOverflowError(
        f"Invoice body runs past the page's bottom margin (ends at y={end}, limit y={limit}).\n"
        f"  What:     {entry.get('case_id', '<case>')} on layout '{layout_id}' ({mode}) ends at y={end}, "
        f"{end - limit}px below the bottom margin at y={limit} (page height {height}, margin {margin}); "
        "an invoice is one fixed-height page, so drawing on would clip.\n"
        f"  Where:    {_LAYOUT_FILE} -> {layout_id}.page_dimensions.height; {layout_id}.body\n"
        f"  Expected: a body whose cursor ends at or above y = page_dimensions.height - margin "
        f"(here {limit}), e.g. side-by-side split columns instead of stacked blocks.\n"
        f"  Recover:  lay the longest blocks of {layout_id}.body out in split columns, tighten their "
        "line_advance or the DISTRACTOR_* budgets' max_lines, or drop items from its distractor_items."
    )
