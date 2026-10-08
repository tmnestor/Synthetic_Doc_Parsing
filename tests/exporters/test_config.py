"""Tests for export config loading and validation."""

from pathlib import Path

import pytest
import yaml

from generators.exporters.config import load_export_config
from tests.conftest import REPO_ROOT, assert_diagnostic_error

VALID: dict = {
    "abn_tfn_canonical_form": "spaced",
    "abn_tfn_equality_form": "digits_only",
    "cord_extension_scoring": "excluded_scored_separately",
    "export_targets": ["cord", "native"],
    "docile_fieldtypes": {"SUPPLIER_NAME": "vendor_name"},
}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "export_config.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_loads_valid_config(tmp_path: Path) -> None:
    """A complete config loads and is returned unchanged; the real config also loads."""
    assert load_export_config(_write(tmp_path, VALID)) == VALID
    real = load_export_config(REPO_ROOT / "config" / "export_config.yml")
    assert real["export_targets"]


@pytest.mark.parametrize("key", sorted(VALID))
def test_missing_key_is_diagnostic(tmp_path: Path, key: str) -> None:
    """Each required key, when absent, fails with the config-style diagnostic."""
    data = {k: v for k, v in VALID.items() if k != key}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, key)


def test_invalid_enum_value_is_diagnostic(tmp_path: Path) -> None:
    """An enum key with a value outside its allowed list is rejected and names the value."""
    path = _write(tmp_path, {**VALID, "abn_tfn_canonical_form": "dotted"})
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "dotted")
    assert "digits_only" in str(err.value)


def test_empty_export_targets_is_allowed(tmp_path: Path) -> None:
    """An explicit empty list ships every target as a no-op; it is not a missing key."""
    config = load_export_config(_write(tmp_path, {**VALID, "export_targets": []}))
    assert config["export_targets"] == []


def test_missing_file_is_diagnostic(tmp_path: Path) -> None:
    """A config path that does not exist raises FileNotFoundError with the diagnostic."""
    path = tmp_path / "absent.yml"
    with pytest.raises(FileNotFoundError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "Export config not found")


def test_malformed_yaml_is_diagnostic(tmp_path: Path) -> None:
    """An unparseable YAML file must hit the yaml.YAMLError branch, not crash raw."""
    path = tmp_path / "export_config.yml"
    path.write_text("export_targets: [cord\n  : : {{")
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "Failed to parse YAML")


def test_non_dict_top_level_is_diagnostic(tmp_path: Path) -> None:
    """A YAML file whose top level is a list (not a mapping) must be rejected."""
    path = _write(tmp_path, ["cord", "native"])
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "top-level mapping")


def test_export_targets_wrong_type_is_diagnostic(tmp_path: Path) -> None:
    """export_targets must be a list, not e.g. a bare scalar."""
    path = _write(tmp_path, {**VALID, "export_targets": "cord"})
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "export_targets")
    assert "must be a list" in str(err.value)


def test_export_targets_unknown_member_is_diagnostic(tmp_path: Path) -> None:
    """Every export_targets member must be one of the allowed targets."""
    path = _write(tmp_path, {**VALID, "export_targets": ["cord", "parquet"]})
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "parquet")


def test_docile_fieldtypes_wrong_type_is_diagnostic(tmp_path: Path) -> None:
    """docile_fieldtypes must be a mapping, not e.g. a list."""
    path = _write(tmp_path, {**VALID, "docile_fieldtypes": ["vendor_name"]})
    with pytest.raises(ValueError) as err:
        load_export_config(path)
    assert_diagnostic_error(err.value, path, "docile_fieldtypes")
    assert "must be a mapping" in str(err.value)
