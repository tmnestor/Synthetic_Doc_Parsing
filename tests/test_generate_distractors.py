"""`generate --distractors` writes distractor renders to their own directory; plain `generate` is unchanged.

The distractor directory is a required key of config/generation_config.yml (`distractor_output_subdir`),
resolved under the configured `output_dir`, so the renders land beside the clean ones and outside the
repository. No geometry is written for them: scored geometry comes from the plain renders.
"""

import hashlib
import itertools
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from generators import pipeline
from generators.loader import load_generation_config
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SHIPPED = REPO_ROOT / "config" / "generation_config.yml"
CASES = 3


def _config(tmp_path: Path, **overrides: object) -> Path:
    """A receipts-only config over the first few real receipts, writing under `tmp_path`."""
    gt = yaml.safe_load((REPO_ROOT / "ground_truth" / "receipts.yml").read_text())
    trimmed = dict(itertools.islice(gt.items(), CASES))
    gt_path = tmp_path / "receipts.yml"
    gt_path.write_text(yaml.safe_dump(trimmed, sort_keys=False))
    data = yaml.safe_load(SHIPPED.read_text())
    data["output_dir"] = str(tmp_path / "output")
    data["derived_dir"] = str(tmp_path / "derived")
    data["document_types"] = {
        "receipts": {**data["document_types"]["receipts"], "ground_truth": str(gt_path)},
    }
    data["document_types"]["receipts"]["layouts"] = str(REPO_ROOT / "config" / "layouts" / "receipts.yml")
    data.update(overrides)
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _digests(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.png"))}


# --- the config key -------------------------------------------------------------------------------


def test_the_shipped_config_names_the_distractor_subdirectory() -> None:
    assert load_generation_config(SHIPPED)["distractor_output_subdir"] == "distractor"


def test_a_config_without_the_key_is_refused(tmp_path: Path) -> None:
    data = yaml.safe_load(SHIPPED.read_text())
    del data["distractor_output_subdir"]
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "distractor_output_subdir" in message and str(path.resolve()) in message
    assert "distractor_output_subdir: distractor" in message


@pytest.mark.parametrize("bad", ["", "a/b", "..", ".", "/abs", None, 3, "clean", "Clean", "CLEAN"])
def test_a_bad_subdirectory_is_refused(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError) as err:
        load_generation_config(_config(tmp_path, distractor_output_subdir=bad))
    assert_diagnostic_error(err.value)
    assert "distractor_output_subdir" in str(err.value)


# --- the switch -----------------------------------------------------------------------------------


def test_generate_with_distractors_writes_only_distractor_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    monkeypatch.chdir(REPO_ROOT)
    modes: list[bool] = []
    real = pipeline._RENDERERS["receipts"]

    def spy(entry: dict, layout: dict, **kwargs: object) -> object:
        modes.append(bool(kwargs["distractors"]))
        assert "geometry_out" not in kwargs
        return real(entry, layout, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setitem(pipeline._RENDERERS, "receipts", spy)
    result = CliRunner().invoke(app, ["generate", "--config", str(config), "--distractors"])

    assert result.exit_code == 0, result.output
    assert modes == [True] * CASES
    out = tmp_path / "output" / "distractor" / "receipts"
    assert len(_digests(out)) == CASES
    assert not (tmp_path / "output" / "clean").exists()
    assert not (tmp_path / "derived" / "geometry.jsonl").exists()
    assert str(out) in result.output


def test_distractor_renders_are_byte_identical_across_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    runs = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        result = CliRunner().invoke(app, ["generate", "--config", str(_config(root)), "--distractors"])
        assert result.exit_code == 0, result.output
        runs.append(_digests(root / "output" / "distractor" / "receipts"))
    assert runs[0] == runs[1] and len(runs[0]) == CASES


def test_plain_generate_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.chdir(REPO_ROOT)
    modes: list[bool] = []
    real = pipeline._RENDERERS["receipts"]

    def spy(entry: dict, layout: dict, **kwargs: object) -> object:
        modes.append(bool(kwargs["distractors"]))
        return real(entry, layout, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setitem(pipeline._RENDERERS, "receipts", spy)
    result = CliRunner().invoke(app, ["generate", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert modes == [False] * CASES
    assert len(_digests(tmp_path / "output" / "clean" / "receipts")) == CASES
    assert (tmp_path / "derived" / "geometry.jsonl").exists()
    assert not (tmp_path / "output" / "distractor").exists()


def test_geometry_records_carry_scored_geometry_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ink roles are internal: each geometry.jsonl record holds exactly the scored-geometry keys."""
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["generate", "--config", str(_config(tmp_path))])
    assert result.exit_code == 0, result.output
    lines = (tmp_path / "derived" / "geometry.jsonl").read_text().splitlines()
    assert len(lines) == CASES
    for line in lines:
        assert set(json.loads(line)) == {"case_id", "image_file", "width", "height", "boxes"}


def test_the_readme_documents_the_switch() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    assert "generate --distractors" in readme and "distractor_output_subdir" in readme


def test_the_clean_directory_name_has_one_source() -> None:
    from generators import loader

    assert loader.CLEAN_SUBDIR == "clean"
    assert '"clean"' not in (REPO_ROOT / "generators" / "pipeline.py").read_text()


def test_a_document_type_without_generate_clean_is_refused(tmp_path: Path) -> None:
    path = _config(tmp_path)
    data = yaml.safe_load(path.read_text())
    del data["document_types"]["receipts"]["generate_clean"]
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "document_types.receipts.generate_clean" in message and str(path.resolve()) in message
    assert "generate_clean: true" in message


@pytest.mark.parametrize("bad", [["receipts"], "receipts", None, 3])
def test_a_non_mapping_document_types_is_refused(tmp_path: Path, bad: object) -> None:
    path = _config(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["document_types"] = bad
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert "document_types" in message and str(path.resolve()) in message
    assert "generate_clean: true" in message


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_a_non_boolean_generate_clean_is_refused(tmp_path: Path, bad: object) -> None:
    path = _config(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["document_types"]["receipts"]["generate_clean"] = bad
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    assert "generate_clean" in str(err.value)
