"""The probe set: the pages Decision 12 names, written as images and one OmniDocBench JSON that keeps the
invariants, under a scratch output root; and the `parsing-probe` command that writes it.

The expected pages are worked out here from the ground truth and layout registries directly, by the rule
as Decision 12 states it, rather than through `select_probe`, so the two derivations check each other.
"""

import json
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from generators.exporters.eval_projection import load_extraction_schema
from generators.loader import load_ground_truth, load_layout_registry
from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, load_parsing_config
from generators.parsing.omnidocbench import check_page
from generators.parsing.probe import (
    PROBE_DIR,
    PROBE_HALF,
    PROBE_JSON,
    ProbeError,
    export_probe,
    select_probe,
)
from generators.pipeline import app
from tests.conftest import assert_diagnostic_error
from tests.eval_export_tools import scratch_config
from tests.renderer_helpers import layouts_for, renderer_for


@pytest.fixture(scope="module")
def parsing_config() -> ParsingConfig:
    """The shipped parsing configuration."""
    return load_parsing_config(PARSING_CONFIG_PATH)


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory, parsing_config: ParsingConfig) -> tuple[Path, Path]:
    """One probe exported under a scratch output root; returns the config and the probe directory."""
    root = tmp_path_factory.mktemp("probe")
    config_path = scratch_config(root)
    return config_path, export_probe(config_path, parsing_config)


def _expected(config_path: Path) -> list[tuple[str, str, str]]:
    """Decision 12's clean pages, as (document type, layout id, case id), worked out directly."""
    config = yaml.safe_load(config_path.read_text())
    wanted = []
    for dtype in config["eval_set"]["document_types"]:
        doc_cfg = config["document_types"][dtype]
        entries = load_ground_truth(Path(doc_cfg["ground_truth"]))
        registry = load_layout_registry(Path(doc_cfg["layouts"]))
        by_layout: dict[str, list[str]] = {}
        for case_id, entry in entries.items():
            if entry["layout"] in registry:
                by_layout.setdefault(entry["layout"], []).append(str(case_id))
        for layout_id in sorted(by_layout)[:2]:
            wanted.append((dtype, layout_id, sorted(by_layout[layout_id])[0]))
    return wanted


def test_the_probe_holds_the_first_case_of_the_first_two_layouts_of_each_type(
    probe: tuple[Path, Path],
) -> None:
    config_path, _ = probe
    chosen = [(case.dtype, case.layout_id, case.case_id) for case in select_probe(config_path)]
    assert chosen == _expected(config_path)
    assert len(chosen) == 6


def test_the_probe_is_written_under_the_output_root_as_images_and_one_json(
    probe: tuple[Path, Path],
) -> None:
    config_path, directory = probe
    assert directory == config_path.parent / "out" / PROBE_DIR
    pages = json.loads((directory / PROBE_JSON).read_text())
    schema = load_extraction_schema()
    names = [
        f"{case_id}_{schema.resolve_doc_type(dtype)}.png" for dtype, _, case_id in _expected(config_path)
    ]
    assert [page["page_info"]["image_path"] for page in pages] == sorted(names)
    assert sorted(path.name for path in directory.iterdir()) == sorted([*names, PROBE_JSON])
    for page in pages:
        info = page["page_info"]
        with Image.open(directory / info["image_path"]) as image:
            assert image.size == (info["width"], info["height"])
        attributes = info["page_attribute"]
        assert attributes["half"] == PROBE_HALF
        assert attributes["tier"] == "none"
        assert attributes["layout_scored"] is True


def test_every_probe_page_keeps_the_invariants_of_the_render_it_was_written_from(
    probe: tuple[Path, Path], parsing_config: ParsingConfig
) -> None:
    config_path, directory = probe
    pages = {
        page["page_info"]["image_path"]: page for page in json.loads((directory / PROBE_JSON).read_text())
    }
    for case in select_probe(config_path):
        page = pages[case.image_name]
        assert page["page_info"]["page_attribute"]["layout_id"] == case.layout_id
        geometry: dict = {}
        canonical = page["page_info"]["page_attribute"]["doc_type"]
        entry = {**load_ground_truth(case.ground_truth)[case.case_id], "case_id": case.case_id}
        renderer_for(canonical)(
            entry, layouts_for(canonical)[case.layout_id], distractors=True, geometry_out=geometry
        )
        assert check_page(page, geometry, where=case.image_name, config=parsing_config) == []
        assert page["layout_dets"], case.image_name


def test_a_second_export_replaces_the_first(
    probe: tuple[Path, Path], parsing_config: ParsingConfig
) -> None:
    config_path, directory = probe
    stale = directory / "stale.png"
    stale.write_bytes(b"")
    assert export_probe(config_path, parsing_config) == directory
    assert not stale.exists()
    assert len(json.loads((directory / PROBE_JSON).read_text())) == 6
    assert sorted(path.name for path in directory.parent.iterdir() if "parsing_probe" in path.name) == [
        PROBE_DIR
    ]


def test_a_type_with_no_case_on_a_registered_layout_is_refused_four_part(
    tmp_path: Path, parsing_config: ParsingConfig
) -> None:
    def empty_receipts(config: dict) -> None:
        empty = tmp_path / "receipts.yml"
        empty.write_text("{}\n")
        config["document_types"]["receipts"]["ground_truth"] = str(empty)

    config_path = scratch_config(tmp_path, mutate=empty_receipts)
    with pytest.raises(ProbeError) as caught:
        select_probe(config_path)
    assert_diagnostic_error(caught.value)
    assert "receipts" in str(caught.value)


def test_the_cli_prints_the_probe_directory_and_page_count(probe: tuple[Path, Path]) -> None:
    config_path, directory = probe
    result = CliRunner().invoke(app, ["parsing-probe", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert f"Probe: {directory}" in result.output
    assert "6 pages" in result.output
