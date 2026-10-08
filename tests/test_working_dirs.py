"""Working directories resolve outside the repository, whatever the cwd."""

from pathlib import Path

import pytest
import yaml

from generators.loader import load_generation_config
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SHIPPED = REPO_ROOT / "config" / "generation_config.yml"
EVALUATION_DATA = (REPO_ROOT.parent / "evaluation_data").resolve()


def _config_with(tmp_path: Path, **overrides: str) -> Path:
    data = yaml.safe_load(SHIPPED.read_text())
    data.update(overrides)
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_shipped_config_puts_working_dirs_in_evaluation_data() -> None:
    cfg = load_generation_config(SHIPPED)
    assert Path(cfg["output_dir"]) == EVALUATION_DATA / "output"
    assert Path(cfg["derived_dir"]) == EVALUATION_DATA / "derived"


def test_resolution_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert Path(load_generation_config(SHIPPED)["output_dir"]) == EVALUATION_DATA / "output"


@pytest.mark.parametrize("key", ["output_dir", "derived_dir"])
def test_a_directory_inside_the_repository_is_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_generation_config(_config_with(tmp_path, **{key: "output"}))
    assert_diagnostic_error(exc_info.value)
    assert key in str(exc_info.value)


def test_a_directory_whose_parent_is_missing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_generation_config(_config_with(tmp_path, output_dir=str(tmp_path / "absent" / "output")))
    assert_diagnostic_error(exc_info.value)


@pytest.mark.parametrize("key", ["output_dir", "derived_dir"])
def test_a_non_string_directory_is_refused(tmp_path: Path, key: str) -> None:
    path = tmp_path / "generation_config.yml"
    data = yaml.safe_load(SHIPPED.read_text())
    data[key] = None
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as exc_info:
        load_generation_config(path)
    assert_diagnostic_error(exc_info.value)
    assert "path string" in str(exc_info.value)
