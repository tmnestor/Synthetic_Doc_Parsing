"""The augmentation registry rejects unknown names at build time, and the
augmented output is deterministic for a given seed.
"""

import numpy as np
import pytest
import yaml
from PIL import Image, ImageDraw

from generators.degradation import AugmentationError, Tier, load_tiers
from generators.degradation.augment import AUGMENTATIONS, _build, apply_augraphy
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG = REPO_ROOT / "config" / "generation_config.yml"


def _page() -> Image.Image:
    """A small synthetic receipt-like page: white with a few dark bars."""
    img = Image.new("RGB", (120, 180), "white")
    draw = ImageDraw.Draw(img)
    for y in (20, 60, 100, 140):
        draw.rectangle([15, y, 105, y + 10], fill=(20, 20, 20))
    return img


def _tier(ink: list[dict], paper: list[dict]) -> Tier:
    return Tier(name="t", suffix="v9", ink=ink, paper=paper, warp={}, camera={})


def _heavy() -> Tier:
    return load_tiers(CONFIG)[-1]


def test_registry_exposes_the_tier_augmentations() -> None:
    """Every augmentation the shipped tiers name is registered."""
    named = {spec["augmentation"] for tier in load_tiers(CONFIG) for spec in [*tier.ink, *tier.paper]}
    assert named
    assert named <= set(AUGMENTATIONS)
    assert set(AUGMENTATIONS) == {"InkBleed", "LightingGradient", "ShadowCast", "Folding"}


def test_photocopier_augmentations_are_not_registered() -> None:
    """Excluded by design: they model a different damage story."""
    assert "DirtyRollers" not in AUGMENTATIONS
    assert "BadPhotoCopy" not in AUGMENTATIONS


def test_unknown_augmentation_is_a_diagnostic() -> None:
    with pytest.raises(AugmentationError) as exc_info:
        _build({"augmentation": "DirtyRollers"}, tier_name="light", phase="ink")
    assert_diagnostic_error(exc_info.value)
    assert "DirtyRollers" in str(exc_info.value)
    assert "InkBleed" in str(exc_info.value)  # lists what IS accepted


def test_spec_without_an_augmentation_key_is_a_diagnostic() -> None:
    with pytest.raises(AugmentationError) as exc_info:
        _build({"intensity": [0.1, 0.2]}, tier_name="light", phase="paper")
    assert_diagnostic_error(exc_info.value)
    assert "augmentation" in str(exc_info.value)


def test_unmapped_parameter_is_a_diagnostic_not_a_bare_keyerror() -> None:
    """An unmapped YAML parameter is rejected at build time, naming the offending key.

    The stricter requirement (a four-part diagnostic rather than a bare KeyError) is
    pinned by the sibling `_bug` test.
    """
    with pytest.raises(Exception) as exc_info:
        _build({"augmentation": "InkBleed", "bogus_knob": 1}, tier_name="light", phase="ink")
    assert "bogus_knob" in str(exc_info.value)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: augment._build indexes mapping[key] (augment.py:111), so an unmapped YAML "
    "parameter raises a bare KeyError naming only the key, not an AugmentationError diagnostic",
)
def test_unmapped_parameter_is_a_diagnostic_not_a_bare_keyerror_bug() -> None:
    """The unmapped-parameter error carries what, where, expected and recover."""
    with pytest.raises(Exception) as exc_info:
        _build({"augmentation": "InkBleed", "bogus_knob": 1}, tier_name="light", phase="ink")
    assert isinstance(exc_info.value, AugmentationError), type(exc_info.value).__name__
    assert_diagnostic_error(exc_info.value)
    assert "intensity" in str(exc_info.value)  # lists the keys that ARE accepted


def _has_folding(tier: Tier) -> bool:
    return any(spec["augmentation"] == "Folding" for spec in [*tier.ink, *tier.paper])


def test_same_seed_gives_identical_output() -> None:
    """Byte-identical for the same seed, for every tier that does not fold.

    The Folding tier is pinned by the sibling `_bug` test.
    """
    checked = 0
    for tier in load_tiers(CONFIG):
        if _has_folding(tier):
            continue
        first = apply_augraphy(_page(), tier, 4242)
        second = apply_augraphy(_page(), tier, 4242)
        assert first.tobytes() == second.tobytes(), tier.name
        checked += 1
    assert checked >= 1


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: Augraphy Folding is non-deterministic under a fixed seed (pipeline already seeds "
    "random, numpy and cv2); probable cause numba parallel prange RNG in augraphy lib.warp_fold, "
    "which Python-level seeding cannot reach. Heavy is the only tier with Folding.",
)
def test_same_seed_gives_identical_output_bug() -> None:
    """The tier that folds is byte-identical for a repeated seed."""
    folding = [tier for tier in load_tiers(CONFIG) if _has_folding(tier)]
    assert folding
    for tier in folding:
        runs = {apply_augraphy(_page(), tier, 4242).tobytes() for _ in range(4)}
        assert len(runs) == 1, f"{tier.name}: {len(runs)} distinct outputs from one seed"


def test_different_seeds_give_different_output() -> None:
    """Seeds matter, checked only on tiers without Folding.

    A tier that folds is non-deterministic even for one seed (see the `_bug`
    sibling above), so two of its outputs differing would prove nothing.
    """
    tiers = [tier for tier in load_tiers(CONFIG) if not _has_folding(tier)]
    assert tiers
    for tier in tiers:
        first = apply_augraphy(_page(), tier, 1).tobytes()
        assert first != apply_augraphy(_page(), tier, 2).tobytes(), tier.name


def test_augmentation_changes_the_image() -> None:
    page = _page()
    out = apply_augraphy(page, _heavy(), 7)
    assert out.tobytes() != page.tobytes()
    assert np.abs(np.asarray(out, dtype=int) - np.asarray(page, dtype=int)).mean() > 0.5


def test_empty_phases_pass_the_image_through_unchanged() -> None:
    page = _page()
    out = apply_augraphy(page, _tier([], []), 7)
    assert out.tobytes() == page.tobytes()
    assert out is not page  # a copy, so the caller's page cannot be mutated downstream


def test_dimensions_are_preserved() -> None:
    """The warp's source quad assumes the augmented page is the original size."""
    page = _page()
    for tier in load_tiers(CONFIG):
        assert apply_augraphy(page, tier, 11).size == page.size


def test_every_shipped_tier_spec_is_buildable() -> None:
    """Every spec in the shipped YAML maps cleanly onto Augraphy's constructors."""
    raw = yaml.safe_load(CONFIG.read_text())["receipt_degradation"]["tiers"]
    for tier in raw:
        for phase in ("ink", "paper"):
            for spec in tier[phase]:
                _build(spec, tier_name=tier["name"], phase=phase)
