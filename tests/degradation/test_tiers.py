"""Severity tiers come only from YAML, and a missing block fails with a diagnostic."""

from pathlib import Path

import pytest
import yaml

from generators.degradation import TierConfigError, load_tiers
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG = REPO_ROOT / "config" / "generation_config.yml"
TIER_KEYS = ("name", "suffix", "ink", "paper", "warp", "camera")


def _write_config(tmp_path: Path, mutate) -> Path:
    """Write a copy of the shipped config after applying `mutate` to its parsed form."""
    config = yaml.safe_load(CONFIG.read_text())
    mutate(config)
    broken = tmp_path / "generation_config.yml"
    broken.write_text(yaml.safe_dump(config))
    return broken


def test_the_shipped_config_declares_three_tiers() -> None:
    assert [tier.name for tier in load_tiers(CONFIG)] == ["light", "moderate", "heavy"]


def test_a_missing_degradation_block_is_a_four_part_diagnostic(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text())
    del config["receipt_degradation"]
    broken = tmp_path / "generation_config.yml"
    broken.write_text(yaml.safe_dump(config))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)


def test_loads_the_real_project_config() -> None:
    """The ladder is well-formed: non-empty, complete tiers, unique names and suffixes."""
    tiers = load_tiers(CONFIG)
    raw = yaml.safe_load(CONFIG.read_text())["receipt_degradation"]["tiers"]
    assert len(tiers) == len(raw) >= 1
    assert [t.name for t in tiers] == [r["name"] for r in raw]
    assert [t.suffix for t in tiers] == [r["suffix"] for r in raw]
    assert len({t.name for t in tiers}) == len(tiers)
    assert len({t.suffix for t in tiers}) == len(tiers)
    for tier in tiers:
        assert isinstance(tier.ink, list) and isinstance(tier.paper, list)
        assert {"foreshorten", "rotation_deg", "margin"} <= set(tier.warp)
        assert {"blur", "noise_sigma", "jpeg"} <= set(tier.camera)


def test_missing_block_is_a_diagnostic(tmp_path: Path) -> None:
    """A config whose degradation block is absent names the block and the file."""
    broken = _write_config(tmp_path, lambda c: c.pop("receipt_degradation"))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)
    assert "receipt_degradation" in str(exc_info.value)
    assert str(broken.resolve()) in str(exc_info.value)


def test_empty_tier_mapping_is_a_diagnostic(tmp_path: Path) -> None:
    """(adjusted: tiers are one flat list now, so this is an empty or absent `tiers` key.)"""
    for mutate in (
        lambda c: c["receipt_degradation"].update(tiers=[]),
        lambda c: c["receipt_degradation"].pop("tiers"),
        lambda c: c.update(receipt_degradation={}),
    ):
        broken = _write_config(tmp_path, mutate)
        with pytest.raises(TierConfigError) as exc_info:
            load_tiers(broken)
        assert_diagnostic_error(exc_info.value)


@pytest.mark.parametrize("key", TIER_KEYS)
def test_missing_tier_key_is_a_diagnostic(tmp_path: Path, key: str) -> None:
    """Every required tier key is enforced, and the message names the missing key."""
    broken = _write_config(tmp_path, lambda c: c["receipt_degradation"]["tiers"][1].pop(key))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)
    assert key in str(exc_info.value)
    assert "tiers[1]" in str(exc_info.value)


def test_duplicate_suffix_within_a_type_is_a_diagnostic(tmp_path: Path) -> None:
    """Two tiers sharing a suffix would overwrite each other's images (adjusted: no per-type ladders)."""
    broken = _write_config(tmp_path, lambda c: c["receipt_degradation"]["tiers"][2].update(suffix="v1"))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)
    assert "v1" in str(exc_info.value)
    assert "suffix" in str(exc_info.value)


def test_tier_that_is_not_a_mapping_is_a_diagnostic(tmp_path: Path) -> None:
    """A tier written as a bare string is rejected, naming its index."""
    broken = _write_config(tmp_path, lambda c: c["receipt_degradation"]["tiers"].__setitem__(0, "light"))
    with pytest.raises(TierConfigError) as exc_info:
        load_tiers(broken)
    assert_diagnostic_error(exc_info.value)
    assert "tiers[0]" in str(exc_info.value)


def test_tier_order_is_yaml_order(tmp_path: Path) -> None:
    """Order fixes each tier's seed offset, so reversing the YAML reverses the loaded list."""
    names = [t.name for t in load_tiers(CONFIG)]
    broken = _write_config(tmp_path, lambda c: c["receipt_degradation"]["tiers"].reverse())
    assert [t.name for t in load_tiers(broken)] == names[::-1]
