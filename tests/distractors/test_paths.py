"""One source for the distractor modules' paths, and the per-type files read from the pipeline config."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from generators.distractors import paths
from generators.distractors.paths import (
    DistractorPathError,
    document_type_files,
    ground_truth_file,
    layout_file,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

MODULES = ("config.py", "values.py", "validator.py", "providers.py")


@pytest.fixture
def fresh() -> Iterator[None]:
    """Clear the cached document-type table around a test that points it at another file."""
    document_type_files.cache_clear()
    yield
    document_type_files.cache_clear()


def _point_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: object) -> Path:
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setattr(paths, "GENERATION_CONFIG_PATH", path)
    return path


def test_the_files_come_from_the_generation_config() -> None:
    config = yaml.safe_load((REPO_ROOT / "config" / "generation_config.yml").read_text())
    declared = config["document_types"]
    assert ground_truth_file("receipt") == REPO_ROOT / declared["receipts"]["ground_truth"]
    assert layout_file("invoice") == REPO_ROOT / declared["invoices"]["layouts"]
    assert ground_truth_file("bank_statement") == REPO_ROOT / declared["bank_statements"]["ground_truth"]
    assert set(document_type_files()) == {"receipt", "invoice", "bank_statement"}
    assert all(
        p.is_absolute() and p.exists() for f in document_type_files().values() for p in vars(f).values()
    )


def test_a_renamed_file_in_the_config_is_followed(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(
        monkeypatch,
        tmp_path,
        {"document_types": {"receipts": {"ground_truth": "gt/r2.yml", "layouts": "lay/r2.yml"}}},
    )
    assert ground_truth_file("receipt") == REPO_ROOT / "gt" / "r2.yml"
    assert layout_file("receipt") == REPO_ROOT / "lay" / "r2.yml"


def test_a_missing_layouts_path_is_a_diagnostic(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _point_at(monkeypatch, tmp_path, {"document_types": {"receipts": {"ground_truth": "x.yml"}}})
    with pytest.raises(DistractorPathError) as err:
        layout_file("receipt")
    assert_diagnostic_error(err.value)
    assert str(path) in str(err.value) and "document_types.receipts.layouts" in str(err.value)


def test_an_unknown_document_type_key_is_a_diagnostic(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(
        monkeypatch, tmp_path, {"document_types": {"payslips": {"ground_truth": "a", "layouts": "b"}}}
    )
    with pytest.raises(DistractorPathError) as err:
        document_type_files()
    assert_diagnostic_error(err.value)
    assert "payslips" in str(err.value)


def test_a_type_with_no_entry_is_a_diagnostic(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(
        monkeypatch, tmp_path, {"document_types": {"receipts": {"ground_truth": "a", "layouts": "b"}}}
    )
    with pytest.raises(DistractorPathError) as err:
        ground_truth_file("invoice")
    assert_diagnostic_error(err.value)


def test_no_document_types_section_is_a_diagnostic(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _point_at(monkeypatch, tmp_path, {"output_dir": "x"})
    with pytest.raises(DistractorPathError) as err:
        document_type_files()
    assert_diagnostic_error(err.value)


def test_a_missing_generation_config_is_a_diagnostic(
    fresh: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "GENERATION_CONFIG_PATH", tmp_path / "absent.yml")
    with pytest.raises(DistractorPathError) as err:
        document_type_files()
    assert_diagnostic_error(err.value)


@pytest.mark.parametrize("module", MODULES)
def test_no_distractor_module_builds_its_own_repository_path(module: str) -> None:
    """Every repository path is imported from paths.py; none is rebuilt from a module path or literal."""
    source = (REPO_ROOT / "generators" / "distractors" / module).read_text()
    assert "parents[" not in source
    tree = ast.parse(source)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in {"config", "ground_truth", "distractors.yml", "transaction_links.yml"}
    ]
    assert not literals, literals


@pytest.mark.parametrize("module", MODULES)
def test_no_distractor_module_declares_a_per_type_file_table(module: str) -> None:
    source = (REPO_ROOT / "generators" / "distractors" / module).read_text()
    for name in ("GROUND_TRUTH_FILES", "LAYOUT_FILES", "PROVIDER_DOC_TYPES", "REPO_ROOT ="):
        assert name not in source, name
