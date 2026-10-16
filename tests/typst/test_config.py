"""Startup validation of config/typeset_config.yml.

Every refusal is built from a copy of the shipped YAML with one key removed or changed, so each test
isolates one rule and asserts the four-part diagnostic names the copy's absolute path and dotted key.
"""

import copy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from generators.typst import config as typst_config
from generators.typst.config import (
    TYPESET_CONFIG_PATH,
    TypesetConfig,
    TypesetConfigError,
    load_typeset_config,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SHIPPED = REPO_ROOT / "config" / "typeset_config.yml"
KEYS = (
    "typst_version",
    "font_dir",
    "creation_timestamp",
    "canvas_dpi",
    "raster_dpi",
    "template",
    "validate_workers",
)

Mutation = Callable[[dict[str, Any]], None]


def write_variant(tmp_path: Path, mutate: Mutation) -> Path:
    """Write a copy of the shipped YAML with `mutate` applied, and return its path."""
    data = copy.deepcopy(yaml.safe_load(SHIPPED.read_text()))
    mutate(data)
    path = tmp_path / "config" / "typeset_config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def refused(tmp_path: Path, mutate: Mutation, key: str, *tokens: str) -> TypesetConfigError:
    """Assert the mutated config is refused with a four-part diagnostic naming path, key and tokens."""
    path = write_variant(tmp_path, mutate)
    with pytest.raises(TypesetConfigError) as exc_info:
        load_typeset_config(path)
    err = exc_info.value
    assert_diagnostic_error(err)
    message = str(err)
    assert f"{path.resolve()} -> {key}" in message, f"Where line should name {key!r}: {message}"
    for token in tokens:
        assert token in message, f"{token!r} missing from: {message}"
    return err


def test_the_shipped_config_loads() -> None:
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    assert isinstance(config, TypesetConfig)
    assert TYPESET_CONFIG_PATH == SHIPPED
    assert config.typst_version == "0.15.1"
    assert config.font_dir == "fonts"
    assert config.creation_timestamp == 1767225600
    assert config.canvas_dpi == 300
    assert config.raster_dpi == 300
    assert config.template == "generators/typst/template.typ"
    assert config.validate_workers == 6


def test_the_pinned_version_matches_the_environment_file() -> None:
    env = (REPO_ROOT / "environment.yml").read_text()
    assert f"  - typst={load_typeset_config(SHIPPED).typst_version}\n" in env


def test_the_config_is_frozen() -> None:
    config = load_typeset_config(SHIPPED)
    with pytest.raises(AttributeError):
        config.raster_dpi = 72  # type: ignore[misc]


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nowhere" / "typeset_config.yml"
    with pytest.raises(TypesetConfigError) as exc_info:
        load_typeset_config(path)
    assert_diagnostic_error(exc_info.value)
    assert str(path.resolve()) in str(exc_info.value)


def test_unparseable_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "typeset_config.yml"
    path.write_text("typst_version: [unclosed\n")
    with pytest.raises(TypesetConfigError) as exc_info:
        load_typeset_config(path)
    assert_diagnostic_error(exc_info.value)


def test_a_top_level_list_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "typeset_config.yml"
    path.write_text("- a\n- b\n")
    with pytest.raises(TypesetConfigError) as exc_info:
        load_typeset_config(path)
    assert_diagnostic_error(exc_info.value)
    assert "mapping" in str(exc_info.value)


@pytest.mark.parametrize("key", KEYS)
def test_each_missing_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda data: data.pop(key), key, "missing")


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, lambda data: data.update(dpi=300), "dpi", "unknown")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("typst_version", 0.15),
        ("typst_version", ""),
        ("font_dir", 3),
        ("font_dir", ""),
        ("template", None),
        ("template", ""),
        ("creation_timestamp", "1767225600"),
        ("creation_timestamp", True),
        ("creation_timestamp", 1.5),
        ("creation_timestamp", -1),
        ("raster_dpi", "300"),
        ("raster_dpi", 300.0),
        ("raster_dpi", True),
        ("canvas_dpi", "300"),
        ("canvas_dpi", 300.0),
        ("canvas_dpi", True),
    ],
)
def test_each_mistyped_key_is_refused(tmp_path: Path, key: str, value: object) -> None:
    refused(tmp_path, lambda data: data.__setitem__(key, value), key, repr(value))


@pytest.mark.parametrize("workers", [0, -2, 1.5, "6", True, None])
def test_validate_workers_that_is_not_a_positive_integer_is_refused(
    tmp_path: Path, workers: object
) -> None:
    refused(
        tmp_path,
        lambda data: data.__setitem__("validate_workers", workers),
        "validate_workers",
        repr(workers),
        "positive integer",
    )


@pytest.mark.parametrize("key", ["raster_dpi", "canvas_dpi"])
@pytest.mark.parametrize("dpi", [0, -300])
def test_a_dpi_that_is_not_positive_is_refused(tmp_path: Path, key: str, dpi: int) -> None:
    refused(tmp_path, lambda data: data.__setitem__(key, dpi), key, "positive integer")


def test_a_typst_version_differing_from_the_installed_one_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda data: data.__setitem__("typst_version", "0.1.0"),
        "typst_version",
        "0.1.0",
        "0.15.1",
        "typst --version",
    )


def test_a_missing_typst_binary_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("typst")

    monkeypatch.setattr(typst_config.subprocess, "run", absent)
    refused(tmp_path, lambda data: None, "typst_version", "not found", "environment.yml")


def test_a_typst_version_that_cannot_be_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def garbled(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="what is this\n", stderr="")

    monkeypatch.setattr(typst_config.subprocess, "run", garbled)
    refused(tmp_path, lambda data: None, "typst_version", "what is this")


def test_a_font_dir_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path, lambda data: data.__setitem__("font_dir", "no_such_fonts"), "font_dir", "no_such_fonts"
    )


def test_a_font_dir_without_a_ttf_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "empty_fonts").mkdir()
    monkeypatch.setattr(typst_config, "REPO_ROOT", tmp_path)
    path = write_variant(tmp_path, lambda data: data.__setitem__("font_dir", "empty_fonts"))
    with pytest.raises(TypesetConfigError) as exc_info:
        load_typeset_config(path)
    assert_diagnostic_error(exc_info.value)
    message = str(exc_info.value)
    assert f"{path.resolve()} -> font_dir" in message
    assert ".ttf" in message
