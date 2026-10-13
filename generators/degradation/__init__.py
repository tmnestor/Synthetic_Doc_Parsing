"""Receipt degradation: Augraphy paper damage, then a camera-scan warp.

Receipts are the only document type users photograph -- bank statements and
invoices arrive as clean PDFs or printouts -- so they are the only type this
package degrades.

Ordering is load-bearing. Augraphy's ink and paper phases run on the flat page,
before the warp, because a crease belongs to the paper and must be warped *with*
it; painting one flat across an already-tilted photo would read as a defect in
the image rather than in the document. Blur, sensor noise and JPEG blocking run
after, because they are artefacts of the camera and the file.
"""

import numpy as np
from PIL import Image

from generators.degradation.augment import AugmentationError, apply_augraphy
from generators.degradation.camera import apply_photometrics, warp_to_photo_mapped
from generators.degradation.tiers import Tier, TierConfigError, load_tiers

__all__ = [
    "AugmentationError",
    "Tier",
    "TierConfigError",
    "degrade_receipt",
    "degrade_receipt_mapped",
    "load_tiers",
    "tier_seed",
]

# Multiplier spacing each tier's seed far apart in the generator's sequence, so
# tier 0 and tier 1 of the same case share no draws.
_TIER_STRIDE = 100_003  # prime, to avoid collisions with round case seeds


def tier_seed(base_seed: int, tier_index: int) -> int:
    """Derive a tier's seed from the case seed and the tier's position.

    Args:
        base_seed: The ground-truth entry's `degradation_seed`.
        tier_index: The tier's index in the declared list.

    Returns:
        A seed unique to this (case, tier) pair and stable across runs.
    """
    return base_seed * _TIER_STRIDE + tier_index


def degrade_receipt(image: Image.Image, tier: Tier, seed: int) -> Image.Image:
    """Degrade one clean receipt render to one tier's severity.

    Args:
        image: The clean rendered receipt.
        tier: The severity tier to apply.
        seed: Seed for this (case, tier) pair -- see `tier_seed`.

    Returns:
        An RGB frame of the receipt as photographed on a desk.

    Raises:
        AugmentationError: The tier names an unregistered augmentation.
    """
    return degrade_receipt_mapped(image, tier, seed)[0]


def degrade_receipt_mapped(image: Image.Image, tier: Tier, seed: int) -> tuple[Image.Image, np.ndarray]:
    """Degrade one clean receipt render, and return the homography that placed it in the frame.

    The image is the one `degrade_receipt` returns for the same seed: the same
    draws, in the same order. Augraphy keeps the page's size and the
    photometrics keep the frame's, so the warp's homography maps a point of
    `image` to its point in the returned frame. The homography is drawn from
    the seed alone, so it is reproducible even where a fold is not.

    Args:
        image: The clean rendered receipt.
        tier: The severity tier to apply.
        seed: Seed for this (case, tier) pair; see `tier_seed`.

    Returns:
        The RGB frame, and the 3x3 homography from `image` pixels to frame pixels.

    Raises:
        AugmentationError: The tier names an unregistered augmentation.
    """
    augmented = apply_augraphy(image, tier, seed)
    rng = np.random.default_rng(seed)
    warped, m = warp_to_photo_mapped(augmented, tier.warp, rng)
    return apply_photometrics(warped, tier.camera, rng), m
