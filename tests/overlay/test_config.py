"""The `overlay:` settings in config/distractors.yml load only when every key is present and valid."""

from pathlib import Path

import pytest
import yaml

from generators.common import FONT_FAMILIES
from generators.distractors.config import (
    OVERLAY_PROTECT_POLICIES,
    DistractorConfigError,
    load_distractor_config,
)
from generators.distractors.paths import DISTRACTORS_PATH
from tests.conftest import assert_diagnostic_error
from tests.exporters.test_geometry_pixel_anchor import DESCENDER_ALLOWANCE


def _shipped() -> dict:
    return yaml.safe_load(DISTRACTORS_PATH.read_text())


def _refused(tmp_path: Path, data: dict, *tokens: str) -> str:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(path)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    assert f"{path.resolve()} -> " in message
    for token in tokens:
        assert token in message, f"{token!r} missing from: {message}"
    return message


def test_the_shipped_overlay_settings_load() -> None:
    overlay = load_distractor_config().overlay
    assert overlay.protect == {
        "receipt": "no_overlay",
        "invoice": "scored_and_marks",
        "bank_statement": "all_ink",
    }
    assert overlay.clearance_px >= 1 and overlay.page_inset_px >= 0
    assert overlay.contrast.min_ratio > 1
    assert 0 <= overlay.contrast.dark_percentile < overlay.contrast.light_percentile <= 100


def test_the_clearance_covers_the_measured_descender_allowance() -> None:
    assert load_distractor_config().overlay.clearance_px >= DESCENDER_ALLOWANCE


def test_a_missing_overlay_block_is_refused(tmp_path: Path) -> None:
    data = {k: v for k, v in _shipped().items() if k != "overlay"}
    _refused(tmp_path, data, "-> overlay", "overlay: {clearance_px: 6")


@pytest.mark.parametrize("key", ["clearance_px", "page_inset_px", "protect", "contrast", "stamp"])
def test_a_missing_overlay_key_is_refused(tmp_path: Path, key: str) -> None:
    data = _shipped()
    del data["overlay"][key]
    message = _refused(tmp_path, data, f"-> overlay.{key}", "overlay: {clearance_px: 6")
    assert "add the key under overlay:" in message


@pytest.mark.parametrize("key", ["min_ratio", "dark_percentile", "light_percentile"])
def test_a_missing_contrast_key_is_refused(tmp_path: Path, key: str) -> None:
    data = _shipped()
    del data["overlay"]["contrast"][key]
    _refused(tmp_path, data, f"-> overlay.contrast.{key}", "add the key under overlay:")


@pytest.mark.parametrize(
    ("patch", "where"),
    [
        ({"clearance_px": 0}, "overlay.clearance_px"),
        ({"clearance_px": "6"}, "overlay.clearance_px"),
        ({"clearance_px": True}, "overlay.clearance_px"),
        ({"page_inset_px": -1}, "overlay.page_inset_px"),
        ({"page_inset_px": 2.5}, "overlay.page_inset_px"),
        ({"protect": "all_ink"}, "overlay.protect"),
        (
            {"protect": {"receipt": "no_overlay", "invoice": "scored_and_marks"}},
            "overlay.protect.bank_statement",
        ),
        (
            {
                "protect": {
                    "receipt": "no_overlay",
                    "invoice": "scored_and_marks",
                    "bank_statement": "all_ink",
                    "passport": "no_overlay",
                }
            },
            "overlay.protect.passport",
        ),
        ({"contrast": [4.5, 5, 95]}, "overlay.contrast"),
        (
            {"contrast": {"min_ratio": 1.0, "dark_percentile": 5, "light_percentile": 95}},
            "overlay.contrast.min_ratio",
        ),
        (
            {"contrast": {"min_ratio": "4.5", "dark_percentile": 5, "light_percentile": 95}},
            "overlay.contrast.min_ratio",
        ),
        (
            {"contrast": {"min_ratio": 4.5, "dark_percentile": -1, "light_percentile": 95}},
            "overlay.contrast.dark_percentile",
        ),
        (
            {"contrast": {"min_ratio": 4.5, "dark_percentile": 5, "light_percentile": 101}},
            "overlay.contrast.light_percentile",
        ),
        (
            {"contrast": {"min_ratio": 4.5, "dark_percentile": 95, "light_percentile": 95}},
            "overlay.contrast.dark_percentile",
        ),
        (
            {"contrast": {"min_ratio": 4.5, "dark_percentile": 5, "light_percentile": 95, "gamma": 2}},
            "overlay.contrast",
        ),
        ({"sparkle": True}, "overlay"),
    ],
)
def test_a_mistyped_overlay_key_is_refused(tmp_path: Path, patch: dict, where: str) -> None:
    data = _shipped()
    data["overlay"].update(patch)
    _refused(tmp_path, data, f"-> {where}\n", "add the key under overlay:")


def test_overlay_must_be_a_mapping(tmp_path: Path) -> None:
    data = _shipped()
    data["overlay"] = [6, 30]
    _refused(tmp_path, data, "-> overlay\n", "a mapping")


def test_an_unknown_protection_policy_lists_the_allowed_ones(tmp_path: Path) -> None:
    data = _shipped()
    data["overlay"]["protect"]["invoice"] = "most_ink"
    message = _refused(tmp_path, data, "-> overlay.protect.invoice", "'most_ink'")
    assert str(list(OVERLAY_PROTECT_POLICIES)) in message


def test_the_retired_none_policy_is_refused(tmp_path: Path) -> None:
    """Receipts say no_overlay; `none` (an overlay protecting nothing) is no longer a policy."""
    data = _shipped()
    data["overlay"]["protect"]["receipt"] = "none"
    _refused(tmp_path, data, "-> overlay.protect.receipt", "'none'", "no_overlay")


# --- overlay.stamp ------------------------------------------------------------------------------------


def test_the_shipped_stamp_settings_load() -> None:
    stamp = load_distractor_config().overlay.stamp
    assert stamp.colours and all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in stamp.colours)
    assert 0 < stamp.opacity[0] <= stamp.opacity[1] <= 1
    assert stamp.rotation_deg[0] < 0 < stamp.rotation_deg[1] and max(map(abs, stamp.rotation_deg)) <= 15
    assert stamp.font.family in FONT_FAMILIES and stamp.font.size > stamp.date_size > 0
    assert stamp.border_px >= 1 and stamp.padding_px >= 0
    assert 0 <= stamp.speckle[0] <= stamp.speckle[1] < 0.5


@pytest.mark.parametrize(
    "key", ["colours", "opacity", "rotation_deg", "font", "date_size", "border_px", "padding_px", "speckle"]
)
def test_a_missing_stamp_key_is_refused(tmp_path: Path, key: str) -> None:
    data = _shipped()
    del data["overlay"]["stamp"][key]
    _refused(tmp_path, data, f"-> overlay.stamp.{key}\n", "stamp: {colours:", "correct overlay.stamp")


@pytest.mark.parametrize(
    ("patch", "where"),
    [
        ({"colours": []}, "overlay.stamp.colours"),
        ({"colours": ["red"]}, "overlay.stamp.colours"),
        ({"colours": ["#B3261"]}, "overlay.stamp.colours"),
        ({"opacity": [0, 0.9]}, "overlay.stamp.opacity"),
        ({"opacity": [0.9, 0.7]}, "overlay.stamp.opacity"),
        ({"opacity": [0.7, 1.2]}, "overlay.stamp.opacity"),
        ({"opacity": 0.8}, "overlay.stamp.opacity"),
        ({"rotation_deg": [15, -15]}, "overlay.stamp.rotation_deg"),
        ({"rotation_deg": [-60, 15]}, "overlay.stamp.rotation_deg"),
        ({"rotation_deg": [-15, "15"]}, "overlay.stamp.rotation_deg"),
        ({"font": {"family": "comic", "bold": True, "size": 60}}, "overlay.stamp.font.family"),
        ({"font": {"family": "liberation_sans", "bold": "yes", "size": 60}}, "overlay.stamp.font.bold"),
        ({"font": {"family": "liberation_sans", "bold": True, "size": 0}}, "overlay.stamp.font.size"),
        ({"font": {"family": "liberation_sans", "bold": True}}, "overlay.stamp.font"),
        ({"date_size": 0}, "overlay.stamp.date_size"),
        ({"date_size": 30.5}, "overlay.stamp.date_size"),
        ({"border_px": 0}, "overlay.stamp.border_px"),
        ({"padding_px": -1}, "overlay.stamp.padding_px"),
        ({"speckle": [0.2, 0.1]}, "overlay.stamp.speckle"),
        ({"speckle": [0.1, 0.6]}, "overlay.stamp.speckle"),
        ({"ink": "red"}, "overlay.stamp"),
    ],
)
def test_a_mistyped_stamp_key_is_refused(tmp_path: Path, patch: dict, where: str) -> None:
    data = _shipped()
    data["overlay"]["stamp"].update(patch)
    _refused(tmp_path, data, f"-> {where}\n", "stamp: {colours:")


def test_the_stamp_must_be_a_mapping(tmp_path: Path) -> None:
    data = _shipped()
    data["overlay"]["stamp"] = ["#B3261E"]
    _refused(tmp_path, data, "-> overlay.stamp\n", "a mapping")
