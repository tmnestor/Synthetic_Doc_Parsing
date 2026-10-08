"""Bank statement renderer — a thin adapter over the declarative layout engine.

Visual DNA per bank now lives in config/layouts/bank_statements.yml as a `body:`
tree of layout primitives; this module only sets up the page and delegates to
the DSL. `_parse_transactions` survives as a small standalone utility for
callers (tests/test_bank_fit.py) that need to iterate transaction fields
independently of rendering — the renderer's own equivalent is the
`bank_transactions` row provider in generators/layout_dsl/providers.py.
"""

from pathlib import Path

from PIL import Image, ImageDraw

from generators.common import PageOverflowError
from generators.exporters.geometry import BoxRecorder, InkRecord, records_ink
from generators.labels.keys import LabelChoice
from generators.layout_dsl.context import Region
from generators.layout_dsl.engine import render_body
from generators.overlay.engine import apply_overlay

_LAYOUT_PATH = "config/layouts/bank_statements.yml"
_LAYOUT_FILE = Path(__file__).resolve().parent.parent / _LAYOUT_PATH


def _parse_transactions(fields: dict) -> list[dict]:
    """Parse pipe-delimited transaction fields into a list of transaction dicts."""
    dates = fields.get("TRANSACTION_DATES", "").split("|")
    descs = fields.get("TRANSACTION_DESCRIPTIONS", "").split("|")
    debits = fields.get("TRANSACTION_AMOUNTS_PAID", "").split("|")
    credits = fields.get("TRANSACTION_AMOUNTS_RECEIVED", "").split("|")

    txns = []
    for i in range(len(dates)):
        txn = {
            "date": dates[i].strip() if i < len(dates) else "",
            "description": descs[i].strip() if i < len(descs) else "",
            "debit": debits[i].strip() if i < len(debits) else "NOT_FOUND",
            "credit": credits[i].strip() if i < len(credits) else "NOT_FOUND",
        }
        txns.append(txn)
    return txns


def render_via_dsl(
    entry: dict,
    layout: dict,
    layout_id: str,
    *,
    distractors: bool,
    labels: LabelChoice | None = None,
    geometry_out: dict | None = None,
    ink_out: list[InkRecord] | None = None,
) -> Image.Image:
    """Render a bank statement through the declarative layout DSL.

    Args:
        entry: Ground truth YAML entry with 'fields' dict.
        layout: Layout config carrying a `body:` tree, 'page_dimensions', etc.
        layout_id: The layout's registry id, used in diagnostics.
        distractors: Whether this render draws distractors.
        labels: The render's label choice (`generators.labels.keys.LabelChoice`), or None for a plain or
            distractor render, whose label sites print their own wording. A choice requires
            `distractors=True`.
        geometry_out: Optional dict (opt-in); when given, populated in place
            with {"width", "height", "boxes", "texts", "ink", "labels", "regions"}: each captured field's
            normalised bounding box on the rendered page, and every drawn
            line's and mark's pixel ink with its role, overlay marks included (internal; not exported).
        ink_out: Optional list (opt-in); when given, extended in place with
            the same ink records, for a caller that needs the ink without the
            scored geometry. A distractor render records ink whether or not
            either is given; nothing is written anywhere.

    Returns:
        PIL Image of the rendered bank statement.

    Raises:
        PageOverflowError: The body ends below the page's bottom margin. A statement is one fixed-height
            page, so a body that does not fit would be clipped; it fails instead, naming the case and layout.
        OverlayError: A distractor render's overlay mark cannot be drawn legally (it would meet a
            protected box, leave the page inset or leave highlighted text illegible).
    """
    dims = layout["page_dimensions"]
    width, height = dims["width"], dims["height"]
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    recorder = BoxRecorder(width, height) if records_ink(distractors, geometry_out, ink_out) else None
    enriched: dict = {}

    end = render_body(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=_LAYOUT_PATH,
        draw=draw,
        region=Region(x=layout["margin"], width=layout["content_width"]),
        y=layout["margin"],
        distractors=distractors,
        recorder=recorder,
        entry_out=enriched,
        labels=labels,
    )
    _check_fits_the_page(end, layout, entry, layout_id, distractors=distractors)
    # The overlay draws on the finished page, and records its ink before geometry is handed back, so
    # density is measured on the composited page. A plain render never draws one.
    if distractors and recorder is not None:
        apply_overlay(
            image,
            layout,
            enriched,
            layout_id=layout_id,
            layout_path=_LAYOUT_FILE,
            doc_type="bank_statement",
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
    if recorder is not None and ink_out is not None:
        ink_out.extend(recorder.ink_records())
    return image


def _check_fits_the_page(end: int, layout: dict, entry: dict, layout_id: str, *, distractors: bool) -> None:
    """Refuse a body whose cursor ends below the bottom margin of the fixed-height page."""
    height = int(layout["page_dimensions"]["height"])
    margin = int(layout["margin"])
    limit = height - margin
    if end <= limit:
        return
    mode = "with distractors" if distractors else "plain"
    raise PageOverflowError(
        f"Bank statement body runs past the page's bottom margin (ends at y={end}, limit y={limit}).\n"
        f"  What:     {entry.get('case_id', '<case>')} on layout '{layout_id}' ({mode}) ends at y={end}, "
        f"{end - limit}px below the bottom margin at y={limit} (page height {height}, margin {margin}); "
        "a bank statement is one fixed-height page, so drawing on would clip.\n"
        f"  Where:    {_LAYOUT_FILE} -> {layout_id}.body\n"
        f"  Expected: a body whose cursor ends at or above y = page_dimensions.height - margin "
        f"(here {limit}), e.g. an inline item in a split column beside an existing line rather than "
        "stacked under it.\n"
        f"  Recover:  lay the blocks of {layout_id}.body out in split columns, tighten the DISTRACTOR_* "
        f"budgets' max_lines, or drop items from {layout_id}.distractor_items; a statement with more "
        f"transactions than {layout_id}.transaction_count allows belongs on a longer layout."
    )


def render_bank_statement(
    entry: dict,
    layout: dict,
    *,
    distractors: bool,
    labels: LabelChoice | None = None,
    geometry_out: dict | None = None,
    ink_out: list[InkRecord] | None = None,
) -> Image.Image:
    """Render a bank statement image from ground truth entry and layout config.

    Args:
        entry: Ground truth YAML entry with 'fields' dict and 'layout' id.
        layout: Layout registry entry carrying 'body', 'page_dimensions',
            'margin', and 'content_width'.
        distractors: Whether this render draws distractors.
        labels: The render's label choice (`generators.labels.keys.LabelChoice`), or None for a plain or
            distractor render, whose label sites print their own wording. A choice requires
            `distractors=True`.
        geometry_out: Optional dict (opt-in); when given, populated in place
            with {"width", "height", "boxes", "texts", "ink", "labels", "regions"}: each captured field's
            normalised bounding box on the rendered page, and every drawn
            line's and mark's pixel ink with its role, overlay marks included (internal; not exported).
        ink_out: Optional list (opt-in); when given, extended in place with
            the same ink records, for a caller that needs the ink without the
            scored geometry. A distractor render records ink whether or not
            either is given; nothing is written anywhere.

    Returns:
        PIL Image of the rendered bank statement.
    """
    return render_via_dsl(
        entry,
        layout,
        str(entry.get("layout", "")),
        distractors=distractors,
        labels=labels,
        geometry_out=geometry_out,
        ink_out=ink_out,
    )
