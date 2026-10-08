"""Nesting containers: panel and split.

These are the only primitives that create child regions, which is why all the
region arithmetic lives in `Region` rather than being duplicated here. Children
render through `ctx.render_children` — injected by the engine — so this module
never imports the engine, which imports it.
"""

from collections.abc import Callable
from dataclasses import replace

from PIL import Image, ImageDraw

from generators.common import PageOverflowError
from generators.layout_dsl.context import RenderContext
from generators.layout_dsl.defaults import resolve_param


class ContainerError(RuntimeError):
    """Raised when a container is asked to render without a walker."""


def _walker(ctx: RenderContext):
    """Return the injected child renderer, or fail with a diagnostic.

    Args:
        ctx: The render context.

    Returns:
        The injected `render_children` callable.

    Raises:
        ContainerError: If no walker was injected.
    """
    if ctx.render_children is None:
        raise ContainerError(
            "Container cannot render its children.\n"
            "  What:     RenderContext.render_children is None.\n"
            f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
            "  Expected: the engine injects render_children before rendering.\n"
            "  Recover:  render through generators.layout_dsl.engine.render_body, "
            "which sets it, rather than constructing a RenderContext by hand."
        )
    return ctx.render_children


def draw_panel(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a bordered container around a nested list of blocks.

    Args:
        block: The `panel` block, carrying `children` and optional `padding`,
            `border_color`, and a fixed `height`.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor: past the panel's border and padding.

    Raises:
        ContainerError: If a fixed height is given but children overflow it.
    """
    render_children = _walker(ctx)
    padding = int(
        resolve_param(
            block,
            ctx.layout,
            "panel_padding",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="padding",
        )
    )
    inner_ctx = ctx.within(ctx.region.indent(padding, padding))
    inner_end = render_children(block["children"], inner_ctx, y + padding)

    fixed = block.get("height")
    if fixed is not None:
        natural = inner_end + padding
        limit = y + int(fixed)
        if natural > limit:
            raise ContainerError(
                "Panel content overflows its fixed height.\n"
                f"  What:     children need {natural - y}px but the panel declares "
                f"height: {int(fixed)}.\n"
                f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body (a panel block)\n"
                f"  Expected: height >= {natural - y}, or fewer/smaller children.\n"
                f"  Recover:  raise the panel's height to at least {natural - y}, or "
                "reduce its children."
            )
        bottom = limit
    else:
        bottom = inner_end + padding

    ctx.draw.rectangle(
        [(ctx.region.x, y), (ctx.region.right, bottom)],
        outline=str(
            resolve_param(
                block,
                ctx.layout,
                "panel_border_color",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="border_color",
            )
        ),
    )
    return bottom


def draw_stack(block: dict, ctx: RenderContext, y: int) -> int:
    """Render a list of blocks one under another in this region, with no border or inset.

    A stack draws exactly what its children would draw in its place; it exists to group blocks under
    one container option, such as `anchor: foot` (see `foot_origin`) or `region: totals`, which records
    the stack's box (its region's x extent, from its start y to its end y) on the page's recorder, so the
    totals block is declared rather than inferred. Recording a region draws nothing. A stack whose children
    all draw nothing (every one gated off) records no region: a zero-height box would contain no line, and a
    page without the region is one on which no field can be read as lying in it.

    Args:
        block: The `stack` block, carrying `children` and optionally `region`.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The y-cursor after the last child.
    """
    end = _walker(ctx)(block["children"], ctx, y)
    region = block.get("region")
    if region is not None and ctx.recorder is not None and end > y:
        ctx.recorder.record_region(str(region), (ctx.region.x, y, ctx.region.right, end))
    return end


def foot_origin(
    block: dict,
    ctx: RenderContext,
    y: int,
    draw_block: Callable[[dict, RenderContext, int], int],
    *,
    at: str,
) -> int:
    """Return the y a foot-anchored container starts at, so that it ends on the bottom margin.

    The container is drawn once on a scratch page, with no recorders, to measure its height; it then
    starts at `page_dimensions.height - margin - height`, so a footer band sits at the foot of the page
    whatever the body's length. A container whose children draw nothing measures 0 and is left where
    the body ends, so it moves no cursor.

    Args:
        block: The container block, carrying `anchor: foot`.
        ctx: Render context.
        y: The body's cursor above the container.
        draw_block: The container's drawer, called for the measuring pass.
        at: The block's path in the body, e.g. `tax_invoice_standard.body[19]`, for a diagnostic.

    Returns:
        The y-cursor the container is drawn from.

    Raises:
        PageOverflowError: The container, drawn upward from the bottom margin, would start above `y`
            and so meet the body.
    """
    width = int(ctx.layout["page_dimensions"]["width"])
    height = int(ctx.layout["page_dimensions"]["height"])
    scratch = ImageDraw.Draw(Image.new("RGB", (width, height), "white"))
    measured = draw_block(block, replace(ctx, draw=scratch, recorder=None, ink_recorder=None), 0)
    if measured == 0:
        # Nothing to anchor (a plain render, whose footer blocks are all distractor-gated): the cursor
        # stays where the body left it, so the body's own end, and its page check, are unchanged.
        return y
    foot = height - int(ctx.layout["margin"])
    top = foot - measured
    if top < y:
        raise PageOverflowError(
            "A foot-anchored block, drawn up from the bottom margin, would meet the body above it.\n"
            f"  What:     {ctx.entry.get('case_id', '<case>')}: the block is {measured}px tall, so "
            f"drawn up from the bottom margin at y={foot} it starts at y={top}, {y - top}px above the body's end at "
            f"y={y}.\n"
            f"  Where:    {ctx.layout_path} -> {at} (anchor: foot); {ctx.layout_id}.body\n"
            f"  Expected: a body ending at or above y={top}, or a foot block at most {foot - y}px tall.\n"
            f"  Recover:  lay the body's longest blocks out in split columns, drop items from "
            f"{ctx.layout_id}.distractor_items, or move blocks out of the foot-anchored container."
        )
    return top


def draw_split(block: dict, ctx: RenderContext, y: int) -> int:
    """Render child block lists side by side in equal columns.

    Args:
        block: The `split` block, carrying `children` (a list of block lists,
            one per column), an optional `gap`, an optional `widths` (explicit
            per-column pixel widths, e.g. invoice totals' fixed 400px column
            at the right edge — equal division cannot express that), and an
            optional `divider` (draws a vertical rule down the middle of each
            gap, e.g. Westpac's rewards panel, which splits into a points
            summary and a message column separated by a ruled line —
            decorative only, so unlike column geometry it is never checked by
            the equivalence harness).
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor: the bottom of the tallest column.
    """
    render_children = _walker(ctx)
    columns = block["children"]
    gap = int(
        resolve_param(
            block,
            ctx.layout,
            "split_gap",
            layout_id=ctx.layout_id,
            layout_path=ctx.layout_path,
            block_key="gap",
        )
    )
    widths = block.get("widths")
    if widths is not None:
        regions = ctx.region.divide_widths([int(w) for w in widths], gap=gap)
    else:
        regions = ctx.region.divide(len(columns), gap=gap)
    ends = [
        render_children(child_blocks, ctx.within(region), y)
        for child_blocks, region in zip(columns, regions, strict=True)
    ]
    bottom = max(ends)
    if block.get("divider"):
        color = str(
            resolve_param(
                block,
                ctx.layout,
                "split_divider_color",
                layout_id=ctx.layout_id,
                layout_path=ctx.layout_path,
                block_key="divider_color",
            )
        )
        for left_region, right_region in zip(regions, regions[1:]):
            divider_x = (left_region.right + right_region.x) // 2
            ctx.draw.line([(divider_x, y), (divider_x, bottom)], fill=color)
    return bottom
