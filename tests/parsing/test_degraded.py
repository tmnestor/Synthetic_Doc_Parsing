"""Degraded receipts keep their boxes: every `poly` is carried through the camera warp onto the frame.

Review Focus 5 on a real receipt: the first probe receipt's distractor render, degraded by each tier, has
every mapped `poly` within the frame and keeps every invariant `check_page` checks; its blocks, text and
tables are the clean page's, with only the polys moved. Decision 11: `fold_displacement_px` measures how far
a tier's Augraphy phases move ink, and every tier `layout_scored_tiers` lists stays within
`fold_displacement_max_px` (slow).
"""

import dataclasses

import numpy as np
import pytest
from PIL import Image

from generators.degradation import Tier, degrade_receipt_mapped, load_tiers, tier_seed
from generators.degradation.camera import homography_transform
from generators.exporters.geometry import BlockRecord, InkRecord
from generators.loader import load_ground_truth
from generators.parsing import degraded as degraded_module
from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, load_parsing_config
from generators.parsing.degraded import (
    DEGRADED_HALF,
    FOLD_WINDOW_PX,
    degraded_page,
    fold_displacement_px,
)
from generators.parsing.omnidocbench import PageContext, build_page, check_page
from generators.parsing.probe import select_probe
from tests.conftest import REPO_ROOT
from tests.renderer_helpers import layouts_for, renderer_for

CONFIG = REPO_ROOT / "config" / "generation_config.yml"
TIERS = load_tiers(CONFIG)


@pytest.fixture(scope="module")
def parsing_config() -> ParsingConfig:
    """The shipped parsing configuration."""
    return load_parsing_config(PARSING_CONFIG_PATH)


@pytest.fixture(scope="module")
def receipt() -> tuple[str, str, int, Image.Image, dict]:
    """The first probe receipt's distractor render: case id, layout id, degradation seed, image, geometry."""
    case = next(case for case in select_probe(CONFIG) if case.canonical == "receipt")
    entry = {**load_ground_truth(case.ground_truth)[case.case_id], "case_id": case.case_id}
    geometry: dict = {}
    image = renderer_for("receipt")(
        entry, layouts_for("receipt")[case.layout_id], distractors=True, geometry_out=geometry
    )
    return case.case_id, case.layout_id, int(entry["degradation_seed"]), image, geometry


def _degrade(
    receipt: tuple[str, str, int, Image.Image, dict], index: int, config: ParsingConfig
) -> tuple[Image.Image, dict]:
    case_id, layout_id, seed, image, geometry = receipt
    tier = TIERS[index]
    return degraded_page(
        image,
        geometry,
        tier=tier,
        seed=tier_seed(seed, index),
        image_path=f"{case_id}_receipt_{tier.suffix}.png",
        doc_type="receipt",
        layout_id=layout_id,
        config=config,
    )


def test_every_tier_maps_every_poly_within_the_frame_and_keeps_the_invariants(
    receipt: tuple[str, str, int, Image.Image, dict], parsing_config: ParsingConfig
) -> None:
    case_id, layout_id, _, image, geometry = receipt
    for index, tier in enumerate(TIERS):
        frame, page = _degrade(receipt, index, parsing_config)
        info = page["page_info"]
        assert (info["width"], info["height"]) == frame.size
        assert frame.size[0] > image.size[0] and frame.size[1] > image.size[1]
        assert page["layout_dets"], tier.name
        for det in page["layout_dets"]:
            xs, ys = det["poly"][0::2], det["poly"][1::2]
            assert all(0 <= x <= frame.width for x in xs), (tier.name, det["poly"])
            assert all(0 <= y <= frame.height for y in ys), (tier.name, det["poly"])
        where = f"{case_id} / {layout_id} ({DEGRADED_HALF} {tier.name})"
        assert check_page(page, geometry, where=where, config=parsing_config) == [], tier.name


def test_a_degraded_page_is_the_clean_page_with_its_polys_mapped(
    receipt: tuple[str, str, int, Image.Image, dict], parsing_config: ParsingConfig
) -> None:
    case_id, layout_id, seed, image, geometry = receipt
    for index, tier in enumerate(TIERS):
        _, page = _degrade(receipt, index, parsing_config)
        _, m = degrade_receipt_mapped(image, tier, tier_seed(seed, index))
        context = PageContext("clean.png", "receipt", layout_id, "distractor", "none", True)
        clean = build_page(geometry, context=context, config=parsing_config)
        assert len(page["layout_dets"]) == len(clean["layout_dets"])
        to_frame = homography_transform(m)
        for got, want in zip(page["layout_dets"], clean["layout_dets"], strict=True):
            assert got["poly"] == to_frame(want["poly"])
            assert {**got, "poly": None} == {**want, "poly": None}
        attributes = page["page_info"]["page_attribute"]
        assert attributes["half"] == DEGRADED_HALF
        assert attributes["tier"] == tier.name
        assert attributes["layout_scored"] is (tier.name in parsing_config.layout_scored_tiers)
        assert attributes["doc_type"] == "receipt" and attributes["layout_id"] == layout_id
        assert page["page_info"]["image_path"] == f"{case_id}_receipt_{tier.suffix}.png"


def test_a_block_mapped_half_off_the_frame_is_clipped_and_ignored(
    monkeypatch: pytest.MonkeyPatch, parsing_config: ParsingConfig
) -> None:
    """A camera warp that pushes one block past the frame's right edge: that block's corners are clipped
    to the frame in their order and it is ignored, keeping its text; the block wholly inside is unchanged."""
    geometry = {
        "width": 100,
        "height": 80,
        "ink": [
            InkRecord("text", (10, 10, 60, 20), False, "Invented Half Line", 12, False, 0, None),
            InkRecord("text", (10, 40, 30, 50), False, "Whole", 12, False, 1, None),
        ],
        "blocks": [
            BlockRecord(0, "text", "body[0]", None, None, 0, None),
            BlockRecord(1, "text", "body[1]", None, None, 0, None),
        ],
        "texts": {},
    }
    shift = np.array([[1.0, 0.0, 150.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

    def warped(image: Image.Image, tier: Tier, seed: int) -> tuple[Image.Image, np.ndarray]:
        return Image.new("RGB", (200, 100), "white"), shift

    monkeypatch.setattr(degraded_module, "degrade_receipt_mapped", warped)
    _, page = degraded_page(
        Image.new("RGB", (100, 80), "white"),
        geometry,
        tier=TIERS[0],
        seed=0,
        image_path="invented_receipt_light.png",
        doc_type="receipt",
        layout_id="invented_layout",
        config=parsing_config,
    )
    cropped, whole = page["layout_dets"]
    assert cropped["poly"] == [160.0, 10.0, 200.0, 10.0, 200.0, 20.0, 160.0, 20.0]
    assert cropped["ignore"] is True and cropped["text"] == "Invented Half Line"
    assert whole["poly"] == [160.0, 40.0, 180.0, 40.0, 180.0, 50.0, 160.0, 50.0]
    assert whole["ignore"] is False and whole["text"] == "Whole"
    assert check_page(page, geometry, where="invented", config=parsing_config) == []


def test_a_real_receipt_cropped_by_the_camera_ignores_exactly_its_cropped_blocks(
    parsing_config: ParsingConfig,
) -> None:
    """The first receipt (by case id) whose heavy photograph crops blocks off the frame: exactly the
    blocks whose mapped poly leaves the frame are ignored, each clipped to it, and the page keeps every
    invariant."""
    case_id, index = "CASE008", 2
    entry = {**load_ground_truth(REPO_ROOT / "ground_truth" / "receipts.yml")[case_id], "case_id": case_id}
    layout_id = str(entry["layout"])
    geometry: dict = {}
    image = renderer_for("receipt")(
        entry, layouts_for("receipt")[layout_id], distractors=True, geometry_out=geometry
    )
    tier = TIERS[index]
    assert tier.name == "heavy"
    seed = tier_seed(int(entry["degradation_seed"]), index)
    frame, page = degraded_page(
        image,
        geometry,
        tier=tier,
        seed=seed,
        image_path=f"{case_id}_receipt_{tier.suffix}.png",
        doc_type="receipt",
        layout_id=layout_id,
        config=parsing_config,
    )
    _, m = degrade_receipt_mapped(image, tier, seed)
    context = PageContext("clean.png", "receipt", layout_id, "distractor", "none", True)
    clean = build_page(geometry, context=context, config=parsing_config)
    to_frame = homography_transform(m)
    off = set()
    for det in clean["layout_dets"]:
        mapped = to_frame(det["poly"])
        xs, ys = mapped[0::2], mapped[1::2]
        if not all(0 <= x <= frame.width for x in xs) or not all(0 <= y <= frame.height for y in ys):
            off.add(det["anno_id"])
    assert len(off) == 3, off
    assert {det["anno_id"] for det in page["layout_dets"] if det["ignore"]} == off
    where = f"{case_id} / {layout_id} ({DEGRADED_HALF} {tier.name})"
    assert check_page(page, geometry, where=where, config=parsing_config) == []


def test_layout_scored_follows_the_configured_list(
    receipt: tuple[str, str, int, Image.Image, dict], parsing_config: ParsingConfig
) -> None:
    only_light = dataclasses.replace(parsing_config, layout_scored_tiers=("light",))
    scored = [
        _degrade(receipt, index, only_light)[1]["page_info"]["page_attribute"]["layout_scored"]
        for index in range(len(TIERS))
    ]
    assert scored == [tier.name == "light" for tier in TIERS]


def _plain_tier() -> Tier:
    """A tier with no Augraphy phase at all: the page comes back untouched."""
    return dataclasses.replace(TIERS[0], name="flat", ink=[], paper=[])


def test_a_tier_with_no_augraphy_phase_moves_no_dot() -> None:
    assert fold_displacement_px(_plain_tier(), range(2), size=(200, 160)) == 0.0


def test_a_shifted_page_is_measured_as_its_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    def shifted(image: Image.Image, tier: Tier, seed: int) -> Image.Image:
        array = np.asarray(image)
        return Image.fromarray(np.roll(array, (3, 4), axis=(0, 1)))

    monkeypatch.setattr(degraded_module, "apply_augraphy", shifted)
    assert fold_displacement_px(_plain_tier(), range(1), size=(200, 160)) == pytest.approx(5.0)


def test_a_dot_lost_from_its_window_counts_as_half_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    def erased(image: Image.Image, tier: Tier, seed: int) -> Image.Image:
        return Image.new("RGB", image.size, "white")

    monkeypatch.setattr(degraded_module, "apply_augraphy", erased)
    assert fold_displacement_px(_plain_tier(), range(1), size=(200, 160)) == FOLD_WINDOW_PX / 2


@pytest.mark.slow
def test_every_layout_scored_tier_folds_within_the_configured_limit(
    receipt: tuple[str, str, int, Image.Image, dict], parsing_config: ParsingConfig
) -> None:
    """Decision 11, measured at the first probe receipt's size over seeds 0 to 4; every tier is reported."""
    size = receipt[3].size
    measured = {tier.name: fold_displacement_px(tier, range(5), size=size) for tier in TIERS}
    print(f"fold displacement (px) at {size}: {measured}; limit {parsing_config.fold_displacement_max_px}")
    for name in parsing_config.layout_scored_tiers:
        assert measured[name] <= parsing_config.fold_displacement_max_px, (name, measured)
