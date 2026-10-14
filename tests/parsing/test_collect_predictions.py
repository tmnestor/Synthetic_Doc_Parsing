"""Collecting predictions: each tool's raw output, however nested, becomes one `<image stem>.md` per page.

Every fixture tree is shaped like a tool's real output (the file names and nesting each tool writes for one
image page), built in `tmp_path` from invented markdown, with the `run.json` the runner leaves beside it.
"""

import json
from pathlib import Path

import pytest

from scripts import collect_predictions
from scripts.collect_predictions import CollectError, collect
from scripts.run_local_parser import RUN_RECORD
from tests.conftest import assert_diagnostic_error
from tests.eval_export_tools import scratch_config

STEMS = ("CASE901_invoice", "CASE902_receipt_v2")


def _mineru(raw: Path, stem: str, method: str) -> None:
    """MinerU: `<stem>/<method>/<stem>.md` beside its json, pdf and image by-products."""
    base = raw / stem / method
    (base / "images").mkdir(parents=True)
    (base / "images" / "0a1b2c.jpg").write_bytes(b"\xff\xd8")
    for suffix in ("_content_list.json", "_middle.json", "_model.json"):
        (base / f"{stem}{suffix}").write_text("{}")
    (base / f"{stem}_layout.pdf").write_bytes(b"%PDF")
    (base / f"{stem}.md").write_text(f"# Invented heading for {stem}\n\nOne line of text.\n")


def _docling(raw: Path, stem: str) -> None:
    """Docling: `<stem>.md` and `<stem>.json` side by side, flat."""
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"{stem}.json").write_text("{}")
    (raw / f"{stem}.md").write_text(f"## Invented heading for {stem}\n\n<!-- image -->\n")


def _marker(raw: Path, stem: str) -> None:
    """Marker: `<stem>/<stem>.md` beside `<stem>_meta.json` and the cropped pictures."""
    base = raw / stem
    base.mkdir(parents=True)
    (base / f"{stem}_meta.json").write_text("{}")
    (base / "_page_0_Picture_1.jpeg").write_bytes(b"\xff\xd8")
    (base / f"{stem}.md").write_text(
        f"**Invented heading for {stem}**\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )


SHAPES = {
    "mineru_pipeline": lambda raw, stem: _mineru(raw, stem, "auto"),
    "mineru_vlm": lambda raw, stem: _mineru(raw, stem, "vlm"),
    "docling": _docling,
    "marker": _marker,
}


def _raw_run(root: Path, tool: str, run_name: str = "plain", stems: tuple[str, ...] = STEMS) -> Path:
    """A raw output tree for `tool` holding every page in `stems`, with its run record."""
    raw = root / "raw_predictions" / tool / run_name
    raw.mkdir(parents=True)
    for stem in stems:
        SHAPES[tool](raw, stem)
    record = {"tool": tool, "run_name": run_name, "pages": [f"{stem}.png" for stem in STEMS]}
    (raw / RUN_RECORD).write_text(json.dumps(record))
    return raw


@pytest.mark.parametrize("tool", sorted(SHAPES))
def test_each_tool_shape_gives_one_markdown_file_per_page(tmp_path: Path, tool: str) -> None:
    raw = _raw_run(tmp_path, tool)
    out = collect(tool, "plain", output_root=tmp_path)
    assert out == tmp_path / "predictions" / tool / "plain"
    assert sorted(path.name for path in out.iterdir()) == sorted(f"{stem}.md" for stem in STEMS)
    for stem in STEMS:
        (source,) = raw.rglob(f"{stem}.md")
        assert (out / f"{stem}.md").read_text() == source.read_text()


def test_a_page_with_no_markdown_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    _raw_run(tmp_path, "marker", stems=STEMS[:1])
    with pytest.raises(CollectError) as caught:
        collect("marker", "plain", output_root=tmp_path)
    assert_diagnostic_error(caught.value)
    assert STEMS[1] in str(caught.value) and "no markdown" in str(caught.value)
    assert not (tmp_path / "predictions").exists()


def test_a_page_with_two_candidate_files_is_refused(tmp_path: Path) -> None:
    raw = _raw_run(tmp_path, "mineru_pipeline")
    _mineru(raw, STEMS[0], "ocr")
    with pytest.raises(CollectError) as caught:
        collect("mineru_pipeline", "plain", output_root=tmp_path)
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert str(raw / STEMS[0] / "auto" / f"{STEMS[0]}.md") in message
    assert str(raw / STEMS[0] / "ocr" / f"{STEMS[0]}.md") in message
    assert not (tmp_path / "predictions").exists()


def test_a_raw_run_without_its_record_is_refused(tmp_path: Path) -> None:
    raw = _raw_run(tmp_path, "docling")
    (raw / RUN_RECORD).unlink()
    with pytest.raises(CollectError) as caught:
        collect("docling", "plain", output_root=tmp_path)
    assert_diagnostic_error(caught.value)
    assert str(raw / RUN_RECORD) in str(caught.value)


def test_a_second_collection_replaces_the_first(tmp_path: Path) -> None:
    _raw_run(tmp_path, "docling")
    out = collect("docling", "plain", output_root=tmp_path)
    (out / "CASE999_invoice.md").write_text("left over\n")
    collect("docling", "plain", output_root=tmp_path)
    assert sorted(path.name for path in out.iterdir()) == sorted(f"{stem}.md" for stem in STEMS)


def test_the_command_line_reports_a_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = scratch_config(tmp_path)
    _raw_run(tmp_path / "out", "docling", stems=STEMS[:1])
    argv = ["--tool", "docling", "--half", "plain", "--config", str(config)]
    assert collect_predictions.main(argv) == 1
    assert_diagnostic_error(capsys.readouterr().err)
    _raw_run(tmp_path / "out", "docling", run_name="distractor")
    assert (
        collect_predictions.main(["--tool", "docling", "--half", "distractor", "--config", str(config)])
        == 0
    )
    assert (tmp_path / "out" / "predictions" / "docling" / "distractor" / f"{STEMS[1]}.md").is_file()


@pytest.mark.parametrize(("key", "value"), [("tool", "marker"), ("run_name", "distractor")])
def test_a_record_of_another_tool_or_run_is_refused(tmp_path: Path, key: str, value: str) -> None:
    raw = _raw_run(tmp_path, "docling")
    record = json.loads((raw / RUN_RECORD).read_text())
    record[key] = value
    (raw / RUN_RECORD).write_text(json.dumps(record))
    with pytest.raises(CollectError) as caught:
        collect("docling", "plain", output_root=tmp_path)
    assert_diagnostic_error(caught.value)
    assert f"{key} {value!r}" in str(caught.value)
    assert str(raw / RUN_RECORD) in str(caught.value)
    assert not (tmp_path / "predictions").exists()
