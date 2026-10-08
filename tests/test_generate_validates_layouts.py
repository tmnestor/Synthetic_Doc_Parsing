"""`generate` validates every DSL layout before it renders anything.

Before the layout DSL, an incomplete layout degraded gracefully: a missing key
took a Python default and the run finished. It does not any more: every
primitive parameter resolves through `resolve_param`, so an omission is a
render-time exception. `validate` catching that is no use to an operator who ran
`generate` directly, which is why `generate` runs the same check itself, before
the first image is written.
"""

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from generators.pipeline import app
from tests.conftest import REPO_ROOT
from tests.renderer_helpers import layouts_for, write_single_type_config


def test_generate_rejects_an_incomplete_layout_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A layout missing a required default stops `generate` with a diagnostic and writes no image."""
    layouts = copy.deepcopy(layouts_for("receipt"))
    victim = next(iter(layouts))
    del layouts[victim]["defaults"]["family"]
    config = write_single_type_config(tmp_path, "receipts", layouts)
    monkeypatch.chdir(REPO_ROOT)

    result = CliRunner().invoke(app, ["generate", "--config", str(config)])

    assert result.exit_code == 1, result.output
    assert victim in result.output
    assert "family" in result.output
    assert "Invalid" in result.output
    assert not list((tmp_path / "output").rglob("*.png")), "an image was written before validation"
    assert not (tmp_path / "derived" / "geometry.jsonl").exists()


def test_generate_still_produces_documents_when_layouts_are_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not cost a valid run anything.

    One document type, so the test stays fast; the full three-type run is
    covered by the CLI itself.
    """
    config = write_single_type_config(tmp_path, "receipts", layouts_for("receipt"))
    monkeypatch.chdir(REPO_ROOT)

    result = CliRunner().invoke(app, ["generate", "--config", str(config)])

    assert result.exit_code == 0, result.output
    images = sorted((tmp_path / "output" / "clean" / "receipts").glob("*.png"))
    assert images
    records = [
        json.loads(line) for line in (tmp_path / "derived" / "geometry.jsonl").read_text().splitlines()
    ]
    assert len(records) == len(images)
    assert {r["image_file"] for r in records} == {p.name for p in images}
