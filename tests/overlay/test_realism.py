"""The overlay's realism settings (final review M1 to M4, M6, M7): pen shapes, highlighter and stamp read
from YAML, felt-tip bands, notes written roughly under a column, a clumped and jittered stamp, a legible
NAB bank-name line and a NAB highlighter that is never blue."""

import dataclasses
import random
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from generators.distractors.config import (
    DistractorConfigError,
    TickShape,
    load_distractor_config,
)
from generators.distractors.paths import DISTRACTORS_PATH
from generators.exporters.geometry import InkRecord
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.overlay.annotation import (
    LaneGeometry,
    Occupancy,
    Row,
    draw_highlight,
    highlight_band,
    place,
    tick_stroke,
)
from generators.overlay.stamp import draw_stamp, knock_out
from generators.overlay.verify import contrast_ratio
from generators.schema import field_names_for
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import capture_bank_overlay
from tests.overlay.overlay_helpers import context
from tests.renderer_helpers import corpus_entries, layouts_for, renderer_for

CONFIG = load_distractor_config()
NAB_LAYOUTS = ("nab_classic", "nab_dense")


def _catalogue(tmp_path: Path, path: tuple[str, ...]) -> Path:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    node = data["overlay"]
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    out = tmp_path / "distractors.yml"
    out.write_text(yaml.safe_dump(data, sort_keys=False))
    return out


# --- M1: every shape range is a required YAML key ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ("ink", "tick", "start"),
        ("ink", "tick", "knee"),
        ("ink", "tick", "tip"),
        ("ink", "tick", "bow"),
        ("ink", "tick", "taper"),
        ("ink", "tick", "spacing_px"),
        ("ink", "circle", "overrun"),
        ("ink", "circle", "taper"),
        ("ink", "underline", "lead_jitters"),
        ("ink", "underline", "spacing_px"),
        ("handwriting", "step_away"),
        ("handwriting", "below_column_dx_px"),
        ("highlighter", "edge_jitter_px"),
        ("highlighter", "density_wobble"),
        ("highlighter", "end_density"),
        ("highlighter", "end_length_px"),
        ("stamp", "jitter_px"),
        ("stamp", "speckle_blob_px"),
        ("stamp", "inking_gradient"),
    ],
)
def test_every_shape_key_is_required(tmp_path: Path, path: tuple[str, ...]) -> None:
    """Deleting any one of the shape keys is a four-part refusal naming it; Python holds no default."""
    catalogue = _catalogue(tmp_path, path)
    with pytest.raises(DistractorConfigError) as caught:
        load_distractor_config(catalogue)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert ".".join(("overlay", *path)) in message, message


def test_the_tick_is_drawn_from_its_yaml_ranges() -> None:
    """With every range collapsed to one value and no jitter, every seed draws the same tick; the shipped
    ranges draw different ones. So the geometry comes from the YAML, not from constants."""
    fixed = TickShape(
        size_px=34,
        start=((0.05, 0.05), (0.5, 0.5)),
        knee=((0.4, 0.4), (0.9, 0.9)),
        tip=((0.95, 0.95), (0.05, 0.05)),
        bow=(0.05, 0.05),
        taper=CONFIG.overlay.ink.tick.taper,
        spacing_px=3.0,
    )
    a = tick_stroke(fixed, 4.0, 0.0, random.Random(1)).alpha.tobytes()
    b = tick_stroke(fixed, 4.0, 0.0, random.Random(2)).alpha.tobytes()
    assert a == b
    shipped = CONFIG.overlay.ink.tick
    c = tick_stroke(shipped, 4.0, 0.0, random.Random(1)).alpha.tobytes()
    d = tick_stroke(shipped, 4.0, 0.0, random.Random(2)).alpha.tobytes()
    assert c != d


# --- M2: the highlighter as a felt tip leaves it -------------------------------------------------------


def test_a_band_wanders_is_antialiased_and_is_densest_at_its_ends() -> None:
    settings = CONFIG.overlay.highlighter
    weights, _ = highlight_band((200, 100, 1600, 130), settings, 0.6, random.Random(3))
    assert weights.max() <= 1.0
    # Antialiased: edge pixels carry partial weights, not just none or full.
    partial = (weights > 0.02) & (weights < 0.55)
    assert partial.sum() > 50
    # The top edge wanders: the first inked row differs along the band.
    columns = range(60, weights.shape[1] - 60, 40)
    tops = {int(np.argmax(weights[:, x] > 0.3)) for x in columns}
    assert len(tops) > 1
    # Denser where the pen starts and stops than mid-band, on a fully covered row.
    row = weights[weights.shape[0] // 2]
    inked = np.flatnonzero(row > 0.3)
    ends = max(row[inked[:10]].mean(), row[inked[-10:]].mean())
    middle = row[inked[len(inked) // 2 - 20 : len(inked) // 2 + 20]].mean()
    assert ends > middle * 1.1


def test_a_plain_band_is_even_and_straight() -> None:
    """With wander, wobble and the end ramp switched off, the band is one weight along its length: the
    realism above comes from those keys alone."""
    plain = dataclasses.replace(
        CONFIG.overlay.highlighter, edge_jitter_px=0, density_wobble=0.0, end_density=1.0, end_jitter_px=0
    )
    weights, _ = highlight_band((200, 100, 1600, 130), plain, 0.6, random.Random(3))
    row = weights[weights.shape[0] // 2]
    inked = row[row > 0.3]
    assert np.allclose(inked[2:-2], 0.6, atol=0.01)


# --- M7: NAB is never highlighted blue -----------------------------------------------------------------


def _band_ctx(seed: int):  # noqa: ANN202
    page = Image.new("RGB", (1800, 600), "white")
    description, paid = (200, 100, 700, 130), (1200, 100, 1300, 130)
    ink = [
        InkRecord("scored", description, False, "x", 10, False),
        InkRecord("scored", paid, False, "x", 10, False),
    ]
    ctx = context(page, ink, doc_type="bank_statement")
    return dataclasses.replace(
        ctx,
        entry={"case_id": "CASE999", "fields": {"DX_ANNOTATED_ROWS": "0"}},
        rng=random.Random(seed),
        fields={"TRANSACTION_DESCRIPTIONS[0]": description, "TRANSACTION_AMOUNTS_PAID[0]": paid},
    )


def _highlight_block(excluded: list[str]) -> dict:
    return {
        "type": "highlight",
        "when": "DX_ANNOTATED_ROWS",
        "rows": "DX_ANNOTATED_ROWS",
        "row_fields": ["TRANSACTION_DESCRIPTIONS", "TRANSACTION_AMOUNTS_PAID"],
        "exclude_colours": excluded,
    }


def _colour(excluded: list[str], seed: int) -> tuple[int, int, int]:
    tint = draw_highlight(_highlight_block(excluded), _band_ctx(seed)).tint
    assert tint is not None
    return tint.colour


def test_an_excluded_colour_is_never_drawn() -> None:
    blue = CONFIG.overlay.highlighter.colours["blue"]
    drawn = {_colour([], seed) for seed in range(60)}
    assert blue in drawn
    drawn = {_colour(["blue"], seed) for seed in range(60)}
    assert blue not in drawn and len(drawn) > 1


def test_the_nab_layouts_exclude_blue_and_no_other_layout_excludes_anything() -> None:
    for layout_id, layout in layouts_for("bank_statement").items():
        highlight = next(b for b in layout["overlay"] if b["type"] == "highlight")
        assert highlight["exclude_colours"] == (["blue"] if layout_id in NAB_LAYOUTS else []), layout_id


@pytest.mark.parametrize("excluded", [["purple"], ["yellow", "green", "pink", "orange", "blue"], "blue"])
def test_a_malformed_exclusion_is_refused(excluded: object) -> None:
    layout = dict(layouts_for("bank_statement")["nab_dense"])
    overlay = [dict(block) for block in layout["overlay"]]
    overlay[0]["exclude_colours"] = excluded
    layout["overlay"] = overlay
    with pytest.raises(LayoutSchemaError) as caught:
        validate_layout(
            layout,
            layout_id="nab_dense",
            layout_path="config/layouts/bank_statements.yml",
            known_fields=set(field_names_for("bank_statements")),
            doc_type="bank_statements",
        )
    assert "exclude_colours" in str(caught.value)
    assert_diagnostic_error(caught.value)


# --- M3: written roughly under the column, not typeset flush ---------------------------------------------


def test_below_column_places_the_right_edge_at_the_drawn_offset() -> None:
    ctx = _band_ctx(0)
    geometry = LaneGeometry(
        content_left=80, content_right=1720, description_field=None, column=(1100, 1300), table_bottom=200
    )
    row = Row(index=0, band=(200, 100, 1300, 130), boxes={})
    occupancy = Occupancy(ctx)
    for dx in (-40, 0, 12):
        box, why = place((120, 30), "below_column", row, geometry, occupancy, ctx, below_dx=dx)
        assert box is not None, why
        assert box[2] == 1300 + dx and box[1] >= 200


def test_corpus_sums_end_off_the_column_edge_within_the_yaml_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each handwritten sum's right edge sits below_column_dx_px from the paid column's right edge."""
    low, high = CONFIG.overlay.handwriting.below_column_dx_px
    runs = capture_bank_overlay(monkeypatch)
    offsets = []
    for case_id, entry in list(corpus_entries("bank_statement").items())[:16]:
        geometry: dict = {}
        renderer_for("bank_statement")(
            entry, layouts_for("bank_statement")[entry["layout"]], distractors=True, geometry_out=geometry
        )
        sums = runs[-1].result.drawn_for("DX_ANNOTATION_TOTAL")
        if not sums:
            continue
        paid = [
            box[2] * geometry["width"]
            for key, box in geometry["boxes"].items()
            if key.startswith("TRANSACTION_AMOUNTS_PAID[")
        ]
        offset = sums[0].box[2] - max(paid)
        assert low - 2 <= offset <= high + 2, (case_id, offset)
        offsets.append(round(offset))
    assert len(offsets) >= 5 and len(set(offsets)) > 1


# --- M4: a clumped, one-sided knock-out and a jittered placement -----------------------------------------


def test_the_knock_out_is_clumped_one_sided_and_exact() -> None:
    inked = np.zeros((200, 400), dtype=bool)
    inked[20:180, 20:380] = True
    knocked = knock_out(inked, 0.1, 9, 0.9, 0.0, seed=5)
    count = int(inked.sum())
    assert int(knocked.sum()) == round(0.1 * count)
    assert not (knocked & ~inked).any()
    # Clumped: most knocked pixels have a knocked right-hand neighbour (i.i.d. noise would give ~10%).
    neighbour = knocked[:, :-1] & knocked[:, 1:]
    assert neighbour.sum() / knocked[:, :-1].sum() > 0.6
    # One-sided: with the ramp along +x, the knock-out gathers on the right.
    xs = np.nonzero(knocked)[1]
    assert xs.mean() > 250


def test_a_jitter_that_leaves_the_page_falls_back_to_the_candidate() -> None:
    """With a jitter larger than the page every jittered centre is refused, and the stamp lands on the
    candidate itself, which validation proves legal."""
    ctx = context(Image.new("RGB", (1900, 3508), "white"), [])
    stamp = dataclasses.replace(CONFIG.overlay.stamp, jitter_px=5000)
    ctx = dataclasses.replace(
        ctx,
        settings=dataclasses.replace(ctx.settings, stamp=stamp),
        entry={
            "case_id": "CASE999",
            "fields": {"DX_PAID_STAMP": "PAID IN FULL", "DX_PAID_DATE": "15/03/2031"},
        },
    )
    block = {
        "type": "stamp",
        "when": "DX_PAID_STAMP",
        "content": "{DX_PAID_STAMP}",
        "date_key": "DX_PAID_DATE",
        "candidates": [{"x": 0.5, "y": 0.5}],
    }
    for seed in range(4):
        drawing = draw_stamp(block, dataclasses.replace(ctx, rng=random.Random(seed)))
        left, top, right, bottom = drawing.marks[0].box
        assert abs((left + right) / 2 - 950) <= 3 and abs((top + bottom) / 2 - 1754) <= 3


# --- M6: NAB's grey bank-name line is legible -------------------------------------------------------------


def test_the_nab_bank_name_line_meets_the_contrast_threshold() -> None:
    settings = CONFIG.overlay.contrast
    cases = [e for e in corpus_entries("bank_statement").values() if e["layout"] in NAB_LAYOUTS]
    assert cases
    for entry in cases:
        geometry: dict = {}
        image = renderer_for("bank_statement")(
            entry, layouts_for("bank_statement")[entry["layout"]], distractors=False, geometry_out=geometry
        )
        left, top, right, bottom = geometry["boxes"]["SUPPLIER_NAME"]
        w, h = geometry["width"], geometry["height"]
        box = (round(left * w), round(top * h), round(right * w) + 1, round(bottom * h) + 1)
        assert contrast_ratio(image, box, settings) >= settings.min_ratio, entry["case_id"]
