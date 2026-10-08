"""Load and validate the `receipt_degradation:` tier declarations.

The tier list *is* the variant count -- three tiers produce three degraded
variants per receipt. There is deliberately no separate count key, so the
configuration cannot contradict itself.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

_BLOCK = "receipt_degradation"
_TIER_KEYS = ("name", "suffix", "ink", "paper", "warp", "camera")

_EXAMPLE = """              receipt_degradation:
                tiers:
                  - name: light
                    suffix: v1
                    ink:    [{augmentation: InkBleed, intensity: [0.05, 0.15], kernel: 3}]
                    paper:  [{augmentation: LightingGradient, max_brightness: 255, direction: 90}]
                    warp:   {foreshorten: [0.01, 0.03], rotation_deg: [-3, 3], margin: [0.05, 0.10]}
                    camera: {blur: [0.2, 0.5], noise_sigma: [1, 3], jpeg: [85, 95]}"""


class TierConfigError(RuntimeError):
    """Raised when the receipt_degradation block is missing or malformed."""


@dataclass(frozen=True)
class Tier:
    """One declared severity level.

    Attributes:
        name: Human-readable severity label, e.g. "light".
        suffix: Filename suffix distinguishing this tier's variant, e.g. "v1".
        ink: Augraphy ink-phase augmentation specs, each with an
            `augmentation:` key naming a registered class.
        paper: Augraphy paper-phase augmentation specs, same shape as `ink`.
        warp: Perspective-warp parameters consumed by camera.warp_to_photo.
        camera: Photometric parameters consumed by camera.apply_photometrics.
    """

    name: str
    suffix: str
    ink: list[dict]
    paper: list[dict]
    warp: dict
    camera: dict


def _err(what: str, *, config_path: Path, key_path: str, expected: str, recover: str) -> TierConfigError:
    """Build a four-element fail-fast diagnostic."""
    return TierConfigError(
        f"Invalid receipt degradation config.\n"
        f"  What:     {what}\n"
        f"  Where:    {config_path.resolve()} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def load_tiers(config_path: Path) -> list[Tier]:
    """Load every declared severity tier, in YAML order.

    Args:
        config_path: Path to generation_config.yml.

    Returns:
        The declared tiers, in the order they appear in the YAML. That order
        fixes each tier's seed offset, so reordering the list changes output.

    Raises:
        TierConfigError: The block is absent, empty, malformed, or declares a
            tier missing any required key or reusing a suffix.
    """
    data = yaml.safe_load(config_path.read_text()) or {}

    block = data.get(_BLOCK)
    if not isinstance(block, dict):
        raise _err(
            f"the top-level '{_BLOCK}:' block is missing, so no degraded receipt can be produced.",
            config_path=config_path,
            key_path=_BLOCK,
            expected=f"a mapping with a 'tiers:' list, e.g.\n{_EXAMPLE}",
            recover=f"add a '{_BLOCK}:' block to {config_path.name}.",
        )

    raw_tiers = block.get("tiers")
    if not isinstance(raw_tiers, list) or not raw_tiers:
        raise _err(
            f"'{_BLOCK}.tiers' is missing or empty, so there is no severity level to render.",
            config_path=config_path,
            key_path=f"{_BLOCK}.tiers",
            expected=f"a non-empty list of tier mappings, e.g.\n{_EXAMPLE}",
            recover=f"declare at least one tier under {_BLOCK}.tiers.",
        )

    tiers: list[Tier] = []
    seen_suffixes: dict[str, str] = {}
    for index, raw in enumerate(raw_tiers):
        if not isinstance(raw, dict):
            raise _err(
                f"tier at index {index} is a {type(raw).__name__}, not a mapping.",
                config_path=config_path,
                key_path=f"{_BLOCK}.tiers[{index}]",
                expected=f"a mapping carrying {list(_TIER_KEYS)}, e.g.\n{_EXAMPLE}",
                recover=f"replace {_BLOCK}.tiers[{index}] with a mapping.",
            )

        missing = [key for key in _TIER_KEYS if key not in raw]
        if missing:
            raise _err(
                f"tier at index {index} is missing required key(s): {missing}.",
                config_path=config_path,
                key_path=f"{_BLOCK}.tiers[{index}]",
                expected=f"every one of {list(_TIER_KEYS)}, e.g.\n{_EXAMPLE}",
                recover=f"add {missing} to {_BLOCK}.tiers[{index}].",
            )

        suffix = str(raw["suffix"])
        if suffix in seen_suffixes:
            raise _err(
                f"tiers '{seen_suffixes[suffix]}' and '{raw['name']}' both declare "
                f"suffix '{suffix}', so their images would overwrite each other.",
                config_path=config_path,
                key_path=f"{_BLOCK}.tiers[{index}].suffix",
                expected="a suffix unique across every tier, e.g. v1 / v2 / v3.",
                recover=f"give {_BLOCK}.tiers[{index}] a suffix no other tier uses.",
            )
        seen_suffixes[suffix] = str(raw["name"])

        tiers.append(
            Tier(
                name=str(raw["name"]),
                suffix=suffix,
                ink=list(raw["ink"]),
                paper=list(raw["paper"]),
                warp=dict(raw["warp"]),
                camera=dict(raw["camera"]),
            )
        )

    return tiers
