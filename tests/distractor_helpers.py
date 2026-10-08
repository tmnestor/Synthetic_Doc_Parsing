"""Helpers shared by the per-document-type distractor tests (receipts, invoices).

The two fixtures built on these, `drawn_keys` and `every_item_forced`, live in `tests/conftest.py`.
"""

from collections.abc import Iterator

import numpy as np
import pytest
import zxingcpp
from PIL import Image

from generators.distractors import providers
from generators.overlay.engine import OverlayResult
from generators.layout_dsl import engine
from generators.layout_dsl.binding import referenced_fields
from generators.layout_dsl.field_providers import apply_field_providers

_TEMPLATE_KEYS = ("content", "label", "value", "heading", "caption")


def printed_dx_names(block: dict) -> set[str]:
    """Every DX_ key a block's own templates print; a key it is only gated on does not count."""
    names: set[str] = set()
    templates = [block[k] for k in _TEMPLATE_KEYS if isinstance(block.get(k), str)]
    templates += [line for line in block.get("lines", []) or [] if isinstance(line, str)]
    for template in templates:
        names.update(n for n in referenced_fields(template) if n.startswith("DX_"))
    return names


def dx_names(block: dict) -> set[str]:
    """Every DX_ key a block names, in its `when:` or in a template it draws."""
    names = printed_dx_names(block)
    when = block.get("when")
    if isinstance(when, str) and when.startswith("DX_"):
        names.add(when)
    return names


def budgets(blocks: list, gated: bool = False) -> tuple[set[str], set[str]]:
    """The (scored, distractor) budgets a body uses, recursing into split and panel children.

    A block nested inside a DX-gated container is distractor text, as the renderer records its ink.
    """
    scored: set[str] = set()
    distractor: set[str] = set()
    for item in blocks:
        if isinstance(item, list):
            inner = budgets(item, gated)
            scored |= inner[0]
            distractor |= inner[1]
            continue
        dx = gated or bool(dx_names(item))
        if "budget" in item:
            (distractor if dx else scored).add(item["budget"])
        for column in item.get("columns", []):
            if "budget" in column:
                scored.add(column["budget"])
        inner = budgets(item.get("children", []) or [], dx)
        scored |= inner[0]
        distractor |= inner[1]
    return scored, distractor


def walk_blocks(blocks: list) -> Iterator[dict]:
    """Yield every block of a body, descending into split columns and panel children."""
    for item in blocks:
        if isinstance(item, list):
            yield from walk_blocks(item)
            continue
        yield item
        yield from walk_blocks(item.get("children", []) or [])


def selected(entry: dict, layout: dict) -> dict[str, str]:
    """The DX_ keys and values a distractor render of `entry` on `layout` draws."""
    fields = apply_field_providers(layout, entry, distractors=True)["fields"]
    return {key: value for key, value in fields.items() if key.startswith("DX_")}


def decoded(image: Image.Image) -> list[str]:
    """Every barcode and QR code payload zxing-cpp reads from the page, sorted."""
    return sorted(result.text for result in zxingcpp.read_barcodes(image))


def record_drawn_keys(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Wrap every primitive drawer to collect the DX_ keys printed by blocks that drew distractor ink.

    Only keys a block's templates reference count: a block merely gated on a key (a logo, a spacer, a
    heading) draws ink without printing the value, and must not mask a missing line.
    """
    drawn: set[str] = set()
    for kind, real in list(engine.PRIMITIVE_DRAWERS.items()):

        def drawer(block: dict, ctx: engine.RenderContext, y: int, real: engine.Drawer = real) -> int:
            before = len(ctx.ink_recorder.ink_records()) if ctx.ink_recorder is not None else 0
            end = real(block, ctx, y)
            if ctx.ink_recorder is not None and any(
                record.role == "distractor" for record in ctx.ink_recorder.ink_records()[before:]
            ):
                drawn.update(printed_dx_names(block))
            return end

        monkeypatch.setitem(engine.PRIMITIVE_DRAWERS, kind, drawer)

    # Overlay blocks (the PAID stamp) print keys too: the content's keys when the drawing records text,
    # and an optional `date_key` when it records a second text line under the word.
    import generators.overlay.engine  # noqa: F401  registers the overlay primitives
    from generators.overlay import registry

    for kind, real_overlay in list(registry.OVERLAY_DRAWERS.items()):

        def overlay_drawer(
            block: dict, ctx: registry.OverlayContext, real: registry.OverlayDrawer = real_overlay
        ) -> registry.OverlayDrawing:
            drawing = real(block, ctx)
            if drawing.marks:
                # A block draws its gate's key whenever it draws a mark: a highlight band or a pen
                # stroke prints no text, but it is the drawing of that key.
                drawn.add(str(block.get("when")))
            if drawing.text_ink:
                drawn.update(
                    n for n in referenced_fields(str(block.get("content", ""))) if n.startswith("DX_")
                )
                date_key = block.get("date_key")
                if isinstance(date_key, str) and date_key.startswith("DX_") and len(drawing.text_ink) > 1:
                    drawn.add(date_key)
            return drawing

        monkeypatch.setitem(registry.OVERLAY_DRAWERS, kind, overlay_drawer)
    return drawn


def force_every_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select every listed item on every document, instead of the seeded selection."""
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: list(eligible)
    )


class OverlayRun:
    """One bank overlay run, captured: the ink recorded before it and what it drew."""

    def __init__(self, before: int, result: OverlayResult, image: Image.Image | None) -> None:
        self.before = before
        self.result = result
        self.image = image


def capture_bank_overlay(monkeypatch: pytest.MonkeyPatch, *, keep_layers: bool = False) -> list[OverlayRun]:
    """Wrap the bank renderer's overlay call, recording each run's pre-overlay ink count and result.

    With `keep_layers`, each run also keeps every opaque layer the engine composited (alpha only), so a
    test can check the drawn pixels against the scored boxes independently of the engine's own check.
    """
    import generators.bank_statement as bank
    from generators.overlay import engine as overlay_engine

    runs: list[OverlayRun] = []
    layers: list[Image.Image] = []
    real = bank.apply_overlay
    real_composite = overlay_engine._composite

    def composite(image, drawing, ctx):  # type: ignore[no-untyped-def]
        if keep_layers and drawing.layer is not None:
            layers.append(drawing.layer.getchannel("A"))
        return real_composite(image, drawing, ctx)

    def wrapper(image, layout, entry, **kwargs):  # type: ignore[no-untyped-def]
        before = len(kwargs["recorder"].ink_records())
        layers.clear()
        result = real(image, layout, entry, **kwargs)
        alpha = None
        for layer in layers:
            alpha = (
                layer
                if alpha is None
                else Image.fromarray(np.maximum(np.asarray(alpha), np.asarray(layer)))
            )
        runs.append(OverlayRun(before, result, alpha))
        return result

    monkeypatch.setattr(overlay_engine, "_composite", composite)
    monkeypatch.setattr(bank, "apply_overlay", wrapper)
    return runs
