"""The walker: dispatch each block in a layout body to its primitive drawer."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace

from PIL import ImageDraw

from generators.common import FitError
from generators.distractors.keys import DX_PREFIX
from generators.exporters.geometry import BoxRecorder
from generators.labels.keys import LabelChoice
from generators.layout_budgets import LayoutBudgetError
from generators.layout_dsl.binding import BindingError, is_present
from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.defaults import DefaultsError
from generators.layout_dsl.field_providers import apply_field_providers
from generators.layout_dsl.primitives_codes import CodeBlockError, draw_barcode, draw_logo, draw_qrcode
from generators.layout_dsl.primitives_container import (
    ContainerError,
    draw_panel,
    draw_split,
    draw_stack,
    foot_origin,
)
from generators.layout_dsl.primitives_table import TableError, draw_table
from generators.layout_dsl.primitives_text import (
    CurrencyError,
    RoleError,
    draw_banner,
    draw_block,
    draw_pair,
    draw_rule,
    draw_spacer,
    draw_text_block,
)

Drawer = Callable[[dict, RenderContext, int], int]

PRIMITIVE_DRAWERS: dict[str, Drawer] = {
    "text": draw_text_block,
    "pair": draw_pair,
    "block": draw_block,
    "rule": draw_rule,
    "spacer": draw_spacer,
    "panel": draw_panel,
    "split": draw_split,
    "stack": draw_stack,
    "table": draw_table,
    "banner": draw_banner,
    "barcode": draw_barcode,
    "qrcode": draw_qrcode,
    "logo": draw_logo,
}

# The primitives a parser finds as blocks (the parsing configuration's `category_map.primitives` keys). Each
# leaf opens a block scope around its own drawing; a container opens one only when it declares
# `parse_block: true`, so its children's ink joins one block. `rule` and `spacer` draw no block.
SCOPED_LEAVES = frozenset({"text", "pair", "block", "table", "banner", "logo", "barcode", "qrcode"})
SCOPED_CONTAINERS = frozenset({"panel", "split", "stack"})


class EngineError(RuntimeError):
    """Raised when a block cannot be dispatched at render time."""


# Every runtime error a primitive (or a helper it calls) can raise while drawing.
# render_blocks tags each with the failing block's path as it unwinds through
# nested containers, so render_body can report exactly which block failed rather
# than just which layout.
#
# `DefaultsError` belongs here as much as any of the others: every primitive
# parameter resolves through `resolve_param`, so a missing default is the
# likeliest render-time DSL failure there is, and its own message cannot name
# the block that asked -- `resolve_param` sees a block dict, not its position
# in the body. Without it, the failure most likely to reach an author was the
# one escaping without the `At: <layout_id>.body[3].children[1]` suffix.
#
# `FieldProviderError` deliberately is *not* here. `apply_field_providers`
# runs once in `render_body` before the walk begins, outside its try, so no
# field-provider failure ever passes through `render_blocks` to be tagged.
# Listing it would promise a block path that cannot exist; its own diagnostic
# names the provider and the layout's `field_providers:` entry instead.
_DSL_ERRORS: tuple[type[RuntimeError], ...] = (
    EngineError,
    ContainerError,
    TableError,
    RoleError,
    CurrencyError,
    BindingError,
    LayoutBudgetError,
    FitError,
    DefaultsError,
    CodeBlockError,
)


def _tag_path(err: RuntimeError, segment: str) -> None:
    """Prepend a path segment to an exception's accumulating `dsl_path`.

    Uses `setattr`/`getattr` rather than attribute syntax so mypy does not
    need every DSL exception class to declare `dsl_path` — the attribute is
    walker bookkeeping, not part of any exception class's own contract.

    Args:
        err: The exception propagating out of a block's drawer.
        segment: This nesting level's own path segment, e.g. `[1](panel)`.
    """
    existing = getattr(err, "dsl_path", [])
    setattr(err, "dsl_path", [segment, *existing])


def render_blocks(blocks: list, ctx: RenderContext, y: int) -> int:
    """Render a list of blocks in order, threading the y-cursor.

    Args:
        blocks: Block dicts to render.
        ctx: Render context, already scoped to the right region.
        y: Starting y-cursor.

    Returns:
        The y-cursor after the last block.

    Raises:
        EngineError: If a block names a primitive with no registered drawer.
        ContainerError | TableError | RoleError | CurrencyError | BindingError |
        LayoutBudgetError | FitError | DefaultsError: Propagated from a
            primitive, tagged with the failing block's path so render_body can
            report exactly where it happened.
    """
    for position, block in enumerate(blocks):
        when = block.get("when")
        if when is not None and not is_present(ctx.entry["fields"], when):
            continue

        kind = block.get("type")
        drawer = PRIMITIVE_DRAWERS.get(str(kind))
        if drawer is None:
            unknown = EngineError(
                "Cannot render layout block.\n"
                f"  What:     no drawer registered for primitive '{kind}'.\n"
                f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
                f"  Expected: one of {sorted(PRIMITIVE_DRAWERS)}.\n"
                f"  Recover:  use a supported primitive, or register a drawer in "
                f"PRIMITIVE_DRAWERS in generators/layout_dsl/engine.py."
            )
            _tag_path(unknown, f"[{position}]({kind})")
            raise unknown

        # A block gated on a DX key draws distractor ink, and so does everything nested inside it
        # (containers hand their context down through `within`); see RenderContext.ink_role.
        block_ctx = ctx
        if when is not None and not ctx.dx_gated and str(when).startswith(DX_PREFIX):
            block_ctx = replace(ctx, dx_gated=True)
        here = f"{ctx.block_path}[{position}]"
        # A block without its own anchor records the anchor of its nearest enclosing container that declares
        # one; its own anchor wins.
        own_anchor = block.get("anchor")
        anchor = ctx.block_anchor if own_anchor is None else str(own_anchor)
        if kind in SCOPED_CONTAINERS:
            block_ctx = replace(block_ctx, block_path=f"{here}.children", block_anchor=anchor)

        try:
            if own_anchor == "foot":
                # Drawn upward from the bottom margin; fails fast if it would meet the body above.
                y = foot_origin(block, block_ctx, y, drawer, at=f"{ctx.layout_id}.body[{position}]")
            with _block_scope(block, str(kind), ctx, path=here, anchor=anchor):
                y = drawer(block, block_ctx, y)
        except _DSL_ERRORS as err:
            # Each nesting level prepends its own segment as the error unwinds, so
            # the final path reads outermost-first: body[2].children[0].
            _tag_path(err, f"[{position}]({kind})")
            raise
    return y


def _block_scope(
    block: dict, kind: str, ctx: RenderContext, *, path: str, anchor: str | None
) -> AbstractContextManager[None]:
    """Return the block scope a block draws inside, or a no-op when it opens none.

    A leaf always opens one, a container only with `parse_block: true`; with no ink recorder there is
    nothing to attribute. A scope opened inside another adds no block (see `BoxRecorder.block_scope`).

    Args:
        block: The block about to be drawn.
        kind: Its primitive name.
        ctx: The walker's context, carrying the ink recorder.
        path: The block's path, e.g. `body[3].children[1]`.
        anchor: The block's own `anchor:`, else the nearest enclosing container's, else None.

    Returns:
        A context manager to draw the block inside.
    """
    recorder = ctx.ink_recorder
    if recorder is None or not (
        kind in SCOPED_LEAVES or (kind in SCOPED_CONTAINERS and block.get("parse_block") is True)
    ):
        return nullcontext()
    category = block.get("parse_category")
    return recorder.block_scope(
        kind,
        path=path,
        anchor=anchor,
        parse_category=None if category is None else str(category),
        parse_order=int(block.get("parse_order", 0)),
        join=block.get("parse_join"),
    )


def render_body(
    layout: dict,
    entry: dict,
    *,
    layout_id: str,
    layout_path: str,
    draw: ImageDraw.ImageDraw,
    region: Region,
    y: int,
    distractors: bool,
    recorder: BoxRecorder | None = None,
    entry_out: dict | None = None,
    labels: LabelChoice | None = None,
) -> int:
    """Render a layout's whole body onto a drawing surface.

    Args:
        layout: The resolved layout dict, carrying `body`.
        entry: The ground-truth entry.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        draw: The PIL drawing surface.
        region: The page's content region.
        y: Starting y-cursor, normally the layout's top margin.
        distractors: Whether distractor field providers run for this render.
        recorder: Optional draw-time bounding-box capture. When given, it
            also receives every drawn line's and mark's ink with its role.
        entry_out: Optional dict (opt-in); when given, filled in place with the provider-enriched
            entry the body is drawn from (the ground truth plus every value its field providers derive,
            each selected `DX_` value included), as a renderer's `geometry_out` is. The overlay layer
            draws from it.
        labels: The render's label choice, handed to the layout's label provider; None for a plain or
            distractor render, which draws every label site with its own wording (see
            `apply_field_providers`).

    Returns:
        The y-cursor after the last block.

    Raises:
        EngineError: If the layout has no `body` key.
        FieldProviderError: If a `field_providers:` entry names an unknown
            provider, or a provider returns a key it did not declare. Raised
            before the walk starts, so it carries no block path.
        ContainerError | TableError | RoleError | CurrencyError | BindingError |
        LayoutBudgetError | FitError | DefaultsError: Propagated from a
            primitive, re-raised with the failing block's path appended to the
            message so the author knows exactly where to look, not just which
            layout.
    """
    if "body" not in layout:
        raise EngineError(
            "Cannot render layout.\n"
            f"  What:     layout '{layout_id}' has no 'body' key.\n"
            f"  Where:    {layout_path} -> {layout_id}.body\n"
            "  Expected: body: a list of block mappings, each with a 'type' key.\n"
            f"  Recover:  add a 'body:' list to {layout_id}, or run "
            "`python -m generators.pipeline validate` to see the full diagnostic."
        )

    entry = apply_field_providers(layout, entry, distractors=distractors, labels=labels)
    if entry_out is not None:
        entry_out.update(entry)

    ctx = RenderContext(
        draw=draw,
        entry=entry,
        layout=layout,
        layout_id=layout_id,
        layout_path=layout_path,
        region=region,
        recorder=recorder,
        render_children=render_blocks,
        ink_recorder=recorder,
    )
    try:
        return render_blocks(layout["body"], ctx, y)
    except _DSL_ERRORS as err:
        path = getattr(err, "dsl_path", None)
        if not path:
            raise
        raise type(err)(f"{err}\n  At:       {layout_id}.body{''.join(path)}") from err
