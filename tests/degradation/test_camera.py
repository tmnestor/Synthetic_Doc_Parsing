"""The warp produces a photo-shaped frame, and the pipeline is deterministic per
(case seed, tier index).

Provenance reporting (reported rotations, clean composition) no longer exists in
the code, so the tests here pin the behaviour that does: the frame geometry, the
YAML ranges actually reaching the transform, and byte-level determinism.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from generators.degradation import Tier, degrade_receipt, load_tiers, tier_seed
from generators.degradation import camera
from generators.degradation.camera import apply_photometrics, warp_to_photo
from tests.conftest import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "generation_config.yml"
TIERS = load_tiers(CONFIG)


def _page() -> Image.Image:
    """A small synthetic receipt-like page: white with a few dark bars."""
    img = Image.new("RGB", (120, 180), "white")
    draw = ImageDraw.Draw(img)
    for y in (20, 60, 100, 140):
        draw.rectangle([15, y, 105, y + 10], fill=(20, 20, 20))
    return img


def _rng(seed: int = 5) -> np.random.Generator:
    return np.random.default_rng(seed)


def test_warp_enlarges_the_frame_by_the_margin() -> None:
    """The document occupies a sub-region surrounded by background, sized by the YAML margin."""
    page = _page()
    w, h = page.size
    for tier in TIERS:
        lo, hi = tier.warp["margin"]
        frame = warp_to_photo(page, tier.warp, _rng())
        fw, fh = frame.size
        assert fw > w and fh > h
        # frame = size + 2 * int(size * m): invert, allowing for int truncation.
        assert lo - 1 / w <= (fw - w) / (2 * w) <= hi
        assert lo - 1 / h <= (fh - h) / (2 * h) <= hi


def test_warp_leaves_background_in_the_corners() -> None:
    """A corner pixel is desk, not paper: if the page filled the frame, no warp happened."""
    page = Image.new("RGB", (120, 180), "white")
    for tier in TIERS:
        frame = np.asarray(warp_to_photo(page, tier.warp, _rng()), dtype=int)
        h, w, _ = frame.shape
        for y, x in ((2, 2), (2, w - 3), (h - 3, 2), (h - 3, w - 3)):
            assert frame[y, x].min() < 235, f"{tier.name}: corner ({y},{x}) is paper-white"
        # The centre, by contrast, is the white page.
        assert frame[h // 2, w // 2].min() > 235


def test_photometrics_preserve_dimensions() -> None:
    frame = warp_to_photo(_page(), TIERS[0].warp, _rng())
    for tier in TIERS:
        out = apply_photometrics(frame, tier.camera, _rng())
        assert out.size == frame.size
        assert out.mode == "RGB"


def test_warp_reports_values_inside_the_declared_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam between what a tier permits and what an image got.

    (adjusted: the warp no longer reports provenance, so the drawn rotation is
    observed at the point it is applied.)
    """
    drawn: list[float] = []
    real_rot = camera._rot

    def spy(point: list[float], cx: float, cy: float, degrees: float) -> list[float]:
        drawn.append(degrees)
        return real_rot(point, cx, cy, degrees)

    monkeypatch.setattr(camera, "_rot", spy)
    for tier in TIERS:
        lo, hi = tier.warp["rotation_deg"]
        for seed in range(6):
            drawn.clear()
            warp_to_photo(_page(), tier.warp, _rng(seed))
            assert len(drawn) == 4 and len(set(drawn)) == 1  # one angle for all four corners
            assert lo <= drawn[0] <= hi


def test_photometrics_report_values_inside_the_declared_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blur radius and JPEG quality actually applied lie inside the tier's YAML ranges.

    (adjusted: observed at the PIL call rather than read back from provenance.)
    """
    blurs: list[float] = []
    qualities: list[int] = []
    real_blur = camera.ImageFilter.GaussianBlur
    real_save = Image.Image.save

    def blur_spy(radius: float):
        blurs.append(radius)
        return real_blur(radius)

    def save_spy(self, fp, format=None, **params):  # noqa: A002
        qualities.append(params["quality"])
        return real_save(self, fp, format, **params)

    monkeypatch.setattr(camera.ImageFilter, "GaussianBlur", blur_spy)
    monkeypatch.setattr(Image.Image, "save", save_spy)
    frame = Image.new("RGB", (60, 60), "white")
    for tier in TIERS:
        blurs.clear()
        qualities.clear()
        for seed in range(8):
            apply_photometrics(frame, tier.camera, _rng(seed))
        b_lo, b_hi = tier.camera["blur"]
        j_lo, j_hi = tier.camera["jpeg"]
        assert len(blurs) == len(qualities) == 8
        assert all(b_lo <= b <= b_hi for b in blurs)
        assert all(j_lo <= q <= j_hi for q in qualities)


def test_rotation_range_takes_effect() -> None:
    """Rotation drawn from the YAML range changes the frame: zero rotation differs from the maximum."""
    page = _page()
    still = {"foreshorten": [0.0, 0.0], "rotation_deg": [0, 0], "margin": [0.1, 0.1]}
    spun = {**still, "rotation_deg": [14, 14]}
    a = warp_to_photo(page, still, _rng())
    b = warp_to_photo(page, spun, _rng())
    assert a.size == b.size
    assert a.tobytes() != b.tobytes()
    # The signed angle matters too: +14 and -14 give different frames.
    c = warp_to_photo(page, {**still, "rotation_deg": [-14, -14]}, _rng())
    assert b.tobytes() != c.tobytes()


def test_heavier_tiers_degrade_the_camera_more() -> None:
    """Tier parameters take effect: heavier tiers lose more to JPEG (measured on the pipeline output)."""
    frame = warp_to_photo(_page(), TIERS[0].warp, _rng())
    reference = np.asarray(frame, dtype=float)

    def mean_error(tier_index: int) -> float:
        errs = []
        for seed in range(4):
            out = apply_photometrics(frame, TIERS[tier_index].camera, _rng(seed))
            errs.append(np.abs(np.asarray(out, dtype=float) - reference).mean())
        return float(np.mean(errs))

    assert mean_error(0) < mean_error(len(TIERS) - 1)


def test_tier_seed_is_stable_and_distinct_per_tier() -> None:
    assert tier_seed(123, 1) == tier_seed(123, 1)
    seeds = {tier_seed(base, index) for base in (0, 1, 2, 123, 99_999) for index in range(len(TIERS))}
    assert len(seeds) == 5 * len(TIERS)
    assert tier_seed(7, 0) != tier_seed(8, 0)


def _folds(tier: Tier) -> bool:
    return any(spec["augmentation"] == "Folding" for spec in [*tier.ink, *tier.paper])


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: Augraphy Folding is non-deterministic under a fixed seed (pipeline already seeds "
    "random, numpy and cv2); probable cause numba parallel prange RNG in augraphy lib.warp_fold, "
    "which Python-level seeding cannot reach. Heavy is the only tier with Folding.",
)
def test_degrade_document_is_deterministic_bug() -> None:
    """The folding tier is byte-identical for the same (seed, tier)."""
    page = _page()
    for index, tier in enumerate(TIERS):
        if not _folds(tier):
            continue
        seed = tier_seed(31, index)
        runs = {degrade_receipt(page, tier, seed).tobytes() for _ in range(4)}
        assert len(runs) == 1, f"{tier.name}: {len(runs)} distinct outputs from one seed"


def test_degrade_document_is_deterministic() -> None:
    """Same (seed, tier) is byte-identical; a different seed differs.

    Byte-identity is asserted for tiers without a fold; the folding tier is in the
    sibling `_bug` test. Different seeds are likewise checked only for tiers
    without a fold: a non-deterministic tier would differ trivially.
    """
    page = _page()
    for index, tier in enumerate(TIERS):
        seed = tier_seed(31, index)
        first = degrade_receipt(page, tier, seed)
        second = degrade_receipt(page, tier, seed)
        if not _folds(tier):
            assert first.tobytes() == second.tobytes()
        if not _folds(tier):
            other = degrade_receipt(page, tier, tier_seed(32, index))
            assert first.tobytes() != other.tobytes()


def test_tiers_differ_from_each_other() -> None:
    page = _page()
    outputs = [degrade_receipt(page, tier, tier_seed(31, i)).tobytes() for i, tier in enumerate(TIERS)]
    assert len(set(outputs)) == len(TIERS)


def test_degrade_document_returns_rgb() -> None:
    page = _page().convert("L")  # a greyscale input must still come out as an RGB frame
    out = degrade_receipt(page, TIERS[0], tier_seed(1, 0))
    assert out.mode == "RGB"
    assert out.size[0] > page.size[0] and out.size[1] > page.size[1]
