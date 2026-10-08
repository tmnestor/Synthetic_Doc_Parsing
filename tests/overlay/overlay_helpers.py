"""A test-only overlay primitive and builders, since no shipped layout draws an overlay yet.

`test_box` draws one filled rectangle, either on an opaque layer, as a multiply tint, or both. Its box is
given in page pixels (`box: [l, t, r, b]`), or relative to a recorded ink box (`near: {role, index, dx,
dy, size}`), so a test can put a mark exactly where a scored value, a code mark or a text line was drawn.
`ink: text | mark | none` says what it records.
"""

import copy
import random
from pathlib import Path

from PIL import Image, ImageDraw

from generators.distractors.config import load_distractor_config
from generators.exporters.geometry import BoxRecorder, InkRecord
from generators.overlay.registry import OverlayContext, OverlayDrawing, OverlayMark, TextInk, Tint
from generators.overlay.verify import Box, OverlaySite, protected_boxes
from tests.renderer_helpers import corpus_entries, layouts_for

TEST_TYPE = "test_box"
TEST_KEY = "DX_OVERLAY_TEST"


def _target(block: dict, ctx: OverlayContext) -> Box:
    if "box" in block:
        left, top, right, bottom = block["box"]
        return (left, top, right, bottom)
    near = block["near"]
    if near["role"] == "mark":
        records = [r for r in ctx.ink if r.mark]
    else:
        records = [r for r in ctx.ink if r.role == near["role"] and not r.mark]
    left, top, right, bottom = records[near.get("index", 0)].box
    x = (left + right) // 2 + near.get("dx", 0)
    y = (top + bottom) // 2 + near.get("dy", 0)
    size = near.get("size", 3)
    return (x, y, x + size, y + size)


def draw_test_box(block: dict, ctx: OverlayContext) -> OverlayDrawing:
    """Draw one rectangle as the block asks; see the module docstring."""
    box = _target(block, ctx)
    mode = block.get("mode", "opaque")
    colour = tuple(block.get("colour", [200, 0, 0]))
    if block.get("jitter"):
        # How a block draws comes from its overlay seed: a seeded shift of the red channel stands in.
        colour = (colour[0] - ctx.rng.randrange(0, 100), colour[1], colour[2])
    layer = None
    tint = None
    if mode in ("opaque", "both"):
        layer = Image.new("RGBA", ctx.page.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(*colour, 255))
    if mode in ("tint", "both"):
        mask = Image.new("L", ctx.page.size, 0)
        ImageDraw.Draw(mask).rectangle(
            (box[0], box[1], box[2] - 1, box[3] - 1), fill=block.get("alpha", 255)
        )
        tint = Tint(mask=mask, colour=(colour[0], colour[1], colour[2]), bands=(box,))
    ink = block.get("ink", "text")
    return OverlayDrawing(
        marks=(OverlayMark(key=block["when"], kind=mode, box=box, opaque=layer is not None),),
        layer=layer,
        tint=tint,
        text_ink=(TextInk(box, "note", 30, False),) if ink == "text" and layer is not None else (),
        mark_ink=(box,) if ink == "mark" and layer is not None else (),
    )


def site(doc_type: str = "invoice") -> OverlaySite:
    """A site for direct verifier calls, naming an invented case and layout."""
    return OverlaySite(
        case_id="CASE999",
        layout_id="synthetic_layout",
        layout_path=Path("/abs/config/layouts/synthetic.yml"),
        block_path="synthetic_layout.overlay[0]",
        block_type=TEST_TYPE,
        doc_type=doc_type,
    )


def context(page: Image.Image, ink: list[InkRecord], *, doc_type: str = "invoice") -> OverlayContext:
    """An `OverlayContext` over a synthetic page, for calling a drawer directly."""
    settings = load_distractor_config().overlay
    policy = settings.protect[doc_type]
    return OverlayContext(
        page=page,
        entry={"case_id": "CASE999", "fields": {}},
        layout={},
        site=site(doc_type),
        settings=settings,
        policy=policy,
        protected=tuple(protected_boxes(ink, policy, settings.clearance_px)),
        ink=tuple(ink),
        rng=random.Random(0),
    )


def recorder_with(ink: list[InkRecord], size: tuple[int, int]) -> BoxRecorder:
    """A recorder already holding `ink`, as a page's recorder does when the overlay runs."""
    recorder = BoxRecorder(*size)
    for record in ink:
        recorder.record_ink(
            record.box, record.role, text=record.text, size=record.size, bold=record.bold, mark=record.mark
        )
    return recorder


def overlaid(doc_type: str, case_id: str, blocks: list) -> tuple[dict, dict]:
    """Return (entry, layout) for a corpus case on its own layout, the layout carrying `blocks`.

    The entry carries one extra field, `TEST_KEY`, which no body block draws: a block gated on it is
    drawn on every distractor render, whatever the case selects (bank statements select nothing yet).
    """
    entry = corpus_entries(doc_type)[case_id]
    entry = {**entry, "fields": {**entry["fields"], TEST_KEY: "overlay test"}}
    layout = copy.deepcopy(layouts_for(doc_type)[entry["layout"]])
    layout["overlay"] = blocks
    return entry, layout
