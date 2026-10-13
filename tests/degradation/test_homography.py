"""The camera warp's homography, returned beside the frame, carries a box onto the photographed page.

`warp_to_photo_mapped` and `degrade_receipt_mapped` return the very frame `warp_to_photo` and
`degrade_receipt` return, plus the 3x3 homography the warp applied. The pinned digests below were taken
from those two functions before they gained the mapped variants, so a change in the draws (an extra
`rng` call, a reordered one) fails here rather than silently re-rendering every degraded receipt. The
heavy tier's fold is not bit-reproducible under a fixed seed (see `test_camera.py`), so its degraded
output is compared by size only; its warp alone is reproducible and is pinned.

Review Focus 5: a white page with one black square, warped by each tier, has the square's ink centred
inside the square's mapped `poly`, whose corners stay clockwise from top-left.
"""

import hashlib

import numpy as np
from PIL import Image, ImageDraw

from generators.degradation import degrade_receipt, degrade_receipt_mapped, load_tiers, tier_seed
from generators.degradation.camera import homography_transform, warp_to_photo, warp_to_photo_mapped
from tests.conftest import REPO_ROOT

TIERS = load_tiers(REPO_ROOT / "config" / "generation_config.yml")

# sha256 of `tobytes()` and the frame size, taken from the functions before the mapped variants existed.
_WARP_PINS = {
    "light": ((140, 212), "ca4a79c15d9aaa07f3bd44e4368cce999fd3d4b0d2c4dfc4bf423496f660d1fe"),
    "moderate": ((150, 224), "10dfcb38cdb09a6d3ed1941a474c944f4dbcf5c58be65f6d08bc4adc9e0e2440"),
    "heavy": ((158, 238), "9500ddcd8b71f8107577e975c409f216c2bbac18d714fcb193441d9a7eb824d1"),
}
_DEGRADE_PINS = {
    "light": ((136, 212), "f9d02ad53c48cce60760a781e9db9b4106cf0542ab849031d743c5eb84da35d5"),
    "moderate": ((148, 220), "c8f4465322c6703da22a9edc2202dfe6a2e03719535f704f1d9a46309aae2d3b"),
    "heavy": ((154, 240), None),
}
# The square of the Review Focus 5 page, (left, top, right, bottom), on a page of _SQUARE_PAGE.
_SQUARE_PAGE = (300, 420)
_SQUARE = (110, 160, 150, 200)


def _page() -> Image.Image:
    """The small receipt-like page the pins were taken on: white with four dark bars."""
    img = Image.new("RGB", (120, 180), "white")
    draw = ImageDraw.Draw(img)
    for y in (20, 60, 100, 140):
        draw.rectangle([15, y, 105, y + 10], fill=(20, 20, 20))
    return img


def _digest(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_warp_to_photo_is_unchanged_for_every_tier() -> None:
    for tier in TIERS:
        size, digest = _WARP_PINS[tier.name]
        frame = warp_to_photo(_page(), tier.warp, np.random.default_rng(5))
        assert (frame.size, _digest(frame)) == (size, digest), tier.name


def test_degrade_receipt_is_unchanged_light_and_moderate_by_bytes_heavy_by_size() -> None:
    for index, tier in enumerate(TIERS):
        size, digest = _DEGRADE_PINS[tier.name]
        out = degrade_receipt(_page(), tier, tier_seed(31, index))
        assert out.size == size, tier.name
        if digest is not None:
            assert _digest(out) == digest, tier.name


def test_the_mapped_warp_returns_the_same_frame_and_its_homography() -> None:
    for tier in TIERS:
        frame, m = warp_to_photo_mapped(_page(), tier.warp, np.random.default_rng(5))
        assert (frame.size, _digest(frame)) == _WARP_PINS[tier.name], tier.name
        assert m.shape == (3, 3)


def test_the_mapped_degradation_returns_the_same_image_and_its_homography() -> None:
    for index, tier in enumerate(TIERS):
        size, digest = _DEGRADE_PINS[tier.name]
        image, m = degrade_receipt_mapped(_page(), tier, tier_seed(31, index))
        assert image.size == size, tier.name
        if digest is not None:
            assert _digest(image) == digest, tier.name
        assert m.shape == (3, 3)
        # The homography does not depend on the fold: the warp's draws come from the seed alone.
        _, again = degrade_receipt_mapped(_page(), tier, tier_seed(31, index))
        assert np.array_equal(m, again), tier.name


def test_the_identity_homography_leaves_a_poly_as_it_is() -> None:
    poly = [10.0, 20.0, 110.0, 20.0, 110.0, 40.0, 10.0, 40.0]
    assert homography_transform(np.eye(3))(poly) == poly


def test_a_translation_moves_every_corner_and_keeps_their_order() -> None:
    shift = np.array([[1.0, 0.0, 7.0], [0.0, 1.0, -3.0], [0.0, 0.0, 1.0]])
    mapped = homography_transform(shift)([10, 20, 110, 20, 110, 40, 10, 40])
    assert mapped == [17.0, 17.0, 117.0, 17.0, 117.0, 37.0, 17.0, 37.0]


def _square_page(with_square: bool) -> Image.Image:
    page = Image.new("RGB", _SQUARE_PAGE, "white")
    if with_square:
        ImageDraw.Draw(page).rectangle(_SQUARE, fill="black")
    return page


def _square_poly() -> list[float]:
    left, top, right, bottom = _SQUARE
    return [left, top, right, top, right, bottom, left, bottom]


def _ink_centroid(with_square: Image.Image, without: Image.Image) -> tuple[float, float]:
    """The centroid of what the square alone changed: the two frames differ only where its ink went."""
    diff = np.abs(np.asarray(with_square, dtype=float) - np.asarray(without, dtype=float)).max(axis=2)
    ys, xs = np.nonzero(diff > 64)
    assert len(xs), "the square left no ink on the frame"
    return float(xs.mean()), float(ys.mean())


def _inside(point: tuple[float, float], poly: list[float]) -> bool:
    """Whether `point` lies inside the convex quadrilateral `poly`, its corners clockwise on screen."""
    corners = list(zip(poly[0::2], poly[1::2], strict=True))
    x, y = point
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True):
        if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) < 0:
            return False
    return True


def _clockwise_from_top_left(poly: list[float]) -> bool:
    """Clockwise on screen (y down) is a positive shoelace sum; top-left is the corner of least x + y."""
    corners = list(zip(poly[0::2], poly[1::2], strict=True))
    turning = sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True)
    )
    first = min(range(4), key=lambda i: corners[i][0] + corners[i][1])
    return turning > 0 and first == 0


def test_the_square_lands_inside_its_mapped_poly_for_every_tier_warp() -> None:
    unmapped_misses = 0
    for seed in range(3):
        for tier in TIERS:
            frame, m = warp_to_photo_mapped(_square_page(True), tier.warp, np.random.default_rng(seed))
            blank = warp_to_photo(_square_page(False), tier.warp, np.random.default_rng(seed))
            poly = homography_transform(m)(_square_poly())
            centroid = _ink_centroid(frame, blank)
            assert _clockwise_from_top_left(poly), (tier.name, seed, poly)
            assert _inside(centroid, poly), (tier.name, seed, poly)
            unmapped_misses += not _inside(centroid, _square_poly())
    # The check can fail: the flat page's own poly, ignoring the desk padding, misses the ink.
    assert unmapped_misses > 0


def test_the_square_lands_inside_its_mapped_poly_through_the_whole_degradation() -> None:
    """The homography is the one applied to the augmented page, not to a crop or the padded desk.

    A folding tier differs between the two runs, so only the reproducible tiers are compared.
    """
    for index, tier in enumerate(TIERS):
        if any(spec["augmentation"] == "Folding" for spec in [*tier.ink, *tier.paper]):
            continue
        seed = tier_seed(17, index)
        frame, m = degrade_receipt_mapped(_square_page(True), tier, seed)
        blank = degrade_receipt(_square_page(False), tier, seed)
        poly = homography_transform(m)(_square_poly())
        assert _clockwise_from_top_left(poly), (tier.name, poly)
        assert _inside(_ink_centroid(frame, blank), poly), (tier.name, poly)
