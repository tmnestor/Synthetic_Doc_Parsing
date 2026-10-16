"""Startup validation of config/parsing_config.yml.

Every refusal is built from a copy of the shipped YAML with one key removed or changed, so each test
isolates one rule and asserts the four-part diagnostic names the copy's absolute path and dotted key.
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from generators.overlay.registry import OVERLAY_DRAWERS
from generators.parsing.config import (
    BLOCK_PRIMITIVES,
    SCORED_METRICS,
    EvaluatorPin,
    ParsingConfig,
    ParsingConfigError,
    ToolSpec,
    load_parsing_config,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SHIPPED = REPO_ROOT / "config" / "parsing_config.yml"

Mutation = Callable[[dict[str, Any]], None]


def shipped_data() -> dict[str, Any]:
    """Return a fresh parse of the shipped YAML."""
    return copy.deepcopy(yaml.safe_load(SHIPPED.read_text()))


def write_variant(tmp_path: Path, mutate: Mutation) -> Path:
    """Write a copy of the shipped YAML with `mutate` applied, and return its path."""
    data = shipped_data()
    mutate(data)
    path = tmp_path / "config" / "parsing_config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def refused(tmp_path: Path, mutate: Mutation, key: str, *tokens: str) -> ParsingConfigError:
    """Assert the mutated config is refused with a four-part diagnostic naming path, key and tokens."""
    path = write_variant(tmp_path, mutate)
    with pytest.raises(ParsingConfigError) as exc_info:
        load_parsing_config(path)
    err = exc_info.value
    assert_diagnostic_error(err)
    message = str(err)
    assert f"{path.resolve()} -> {key}" in message, f"Where line should name {key!r}: {message}"
    for token in tokens:
        assert token in message, f"{token!r} missing from: {message}"
    return err


def test_the_shipped_config_loads() -> None:
    config = load_parsing_config(SHIPPED)
    assert isinstance(config, ParsingConfig)
    assert isinstance(config.evaluator, EvaluatorPin)
    assert len(config.evaluator.commit) == 40
    assert config.evaluator.formula_metrics is False
    assert config.textless_categories == ("figure", "abandon")
    assert config.fold_displacement_max_px == 3
    assert config.layout_scored_tiers == ("light", "moderate")
    assert [tool.name for tool in config.tools] == [
        "mineru_pipeline",
        "mineru_vlm",
        "docling",
        "marker",
        "text_layer",
    ]
    assert all(isinstance(tool, ToolSpec) for tool in config.tools)
    # Every parser runs locally on Apple silicon, from the env file that builds it; the text-layer
    # baseline runs in the generator's own env, on the CPU.
    assert {tool.name: (tool.runner, tool.device) for tool in config.tools} == {
        "mineru_pipeline": ("local", "mps"),
        "mineru_vlm": ("local", "mps"),
        "docling": ("local", "mps"),
        "marker": ("local", "mps"),
        "text_layer": ("builtin", "cpu"),
    }
    assert [tool.conda_env_file for tool in config.tools] == [
        "envs/mineru.yml",
        "envs/mineru.yml",
        "envs/docling.yml",
        "envs/marker.yml",
        None,
    ]
    assert [tool.backend for tool in config.tools] == [
        "pipeline",
        "vlm-engine",
        "default",
        "default",
        "default",
    ]
    # Marker is scored on images only, as stage one ran it; the baseline reads only a PDF's text layer.
    assert {tool.name: tool.inputs for tool in config.tools} == {
        "mineru_pipeline": ("png", "pdf"),
        "mineru_vlm": ("png", "pdf"),
        "docling": ("png", "pdf"),
        "marker": ("png",),
        "text_layer": ("pdf",),
    }
    assert config.anchors == {"foot": "footer"}
    # The evaluator's contract at the pinned commit (match_gt2pred_quick in src/core/matching/match_quick.py).
    assert config.ignored_categories == (
        "figure_caption",
        "figure_footnote",
        "table_caption",
        "table_footnote",
        "code_algorithm",
        "code_algorithm_caption",
        "header",
        "footer",
        "page_footnote",
        "page_number",
        "equation_caption",
    )
    assert config.ignored_match_max_distance == 0.25


def test_the_shipped_maps_cover_exactly_the_primitives_and_overlay_drawers() -> None:
    import generators.overlay.engine  # noqa: F401  registers every overlay drawer

    config = load_parsing_config(SHIPPED)
    assert set(config.primitives) == BLOCK_PRIMITIVES
    assert len(BLOCK_PRIMITIVES) == 11
    assert set(config.overlays) == set(OVERLAY_DRAWERS)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config" / "parsing_config.yml"
    with pytest.raises(ParsingConfigError) as exc_info:
        load_parsing_config(path)
    assert_diagnostic_error(exc_info.value)
    assert str(path.resolve()) in str(exc_info.value)


def test_unparseable_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "parsing_config.yml"
    path.write_text("evaluator: [unclosed")
    with pytest.raises(ParsingConfigError) as exc_info:
        load_parsing_config(path)
    assert_diagnostic_error(exc_info.value)


def test_a_non_mapping_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "parsing_config.yml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ParsingConfigError) as exc_info:
        load_parsing_config(path)
    assert_diagnostic_error(exc_info.value)


TOP_LEVEL_KEYS = [
    "evaluator",
    "category_vocabulary",
    "textless_categories",
    "category_map",
    "ignored_categories",
    "ignored_match_max_distance",
    "fold_displacement_max_px",
    "layout_scored_tiers",
    "tools",
    "native_pdf_control",
]


@pytest.mark.parametrize("key", TOP_LEVEL_KEYS)
def test_a_missing_top_level_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d.pop(key), key, "missing")


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("evaluator", ["not", "a", "mapping"]),
        ("category_vocabulary", "title"),
        ("category_vocabulary", []),
        ("textless_categories", "figure"),
        ("category_map", ["x"]),
        ("ignored_categories", "footer"),
        ("ignored_categories", []),
        ("ignored_categories", ["footer", 3]),
        ("ignored_match_max_distance", "0.25"),
        ("ignored_match_max_distance", True),
        ("ignored_match_max_distance", 0),
        ("ignored_match_max_distance", 1.5),
        ("fold_displacement_max_px", "3"),
        ("fold_displacement_max_px", True),
        ("fold_displacement_max_px", -1),
        ("layout_scored_tiers", "light"),
        ("layout_scored_tiers", []),
        ("tools", {"name": "x"}),
        ("tools", []),
    ],
)
def test_a_mistyped_top_level_key_is_refused(tmp_path: Path, key: str, bad: Any) -> None:
    refused(tmp_path, lambda d: d.__setitem__(key, bad), key)


@pytest.mark.parametrize("key", ["repo", "commit", "conda_env_file", "formula_metrics"])
def test_a_missing_evaluator_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["evaluator"].pop(key), f"evaluator.{key}", "missing")


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("repo", 3),
        ("repo", ""),
        ("conda_env_file", None),
        ("conda_env_file", ""),
        ("formula_metrics", "no"),
    ],
)
def test_a_mistyped_evaluator_key_is_refused(tmp_path: Path, key: str, bad: Any) -> None:
    refused(tmp_path, lambda d: d["evaluator"].__setitem__(key, bad), f"evaluator.{key}")


@pytest.mark.parametrize(
    "commit", ["v1.7", "50d69e3", "g" * 40, "50D69E3EA1DC1866F49E8B5369F4155DFDAC" + "3" * 4]
)
def test_a_commit_that_is_not_forty_hex_characters_is_refused(tmp_path: Path, commit: str) -> None:
    refused(tmp_path, lambda d: d["evaluator"].__setitem__("commit", commit), "evaluator.commit", "40")


def test_formula_metrics_true_is_refused_in_stage_one(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["evaluator"].__setitem__("formula_metrics", True),
        "evaluator.formula_metrics",
        "formula_metrics: false",
        "formulas arrive with stage three",
    )


@pytest.mark.parametrize("key", ["primitives", "overlays", "anchors"])
def test_a_missing_category_map_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["category_map"].pop(key), f"category_map.{key}", "missing")


@pytest.mark.parametrize("key", ["primitives", "overlays", "anchors"])
def test_a_mistyped_category_map_section_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["category_map"].__setitem__(key, ["x"]), f"category_map.{key}")


@pytest.mark.parametrize("section", ["primitives", "overlays", "anchors"])
def test_a_category_map_value_outside_the_vocabulary_is_refused(tmp_path: Path, section: str) -> None:
    def mutate(data: dict[str, Any]) -> None:
        mapping = data["category_map"][section]
        mapping[next(iter(mapping))] = "paragraph"

    key = next(iter(shipped_data()["category_map"][section]))
    refused(tmp_path, mutate, f"category_map.{section}.{key}", "paragraph", "category_vocabulary")


def test_an_anchor_the_dsl_does_not_know_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["category_map"]["anchors"].__setitem__("fot", "footer"),
        "category_map.anchors",
        "fot",
        "extra",
        "foot",
    )


def test_a_primitive_missing_from_the_map_is_refused_naming_both_sets(tmp_path: Path) -> None:
    err = refused(
        tmp_path,
        lambda d: d["category_map"]["primitives"].pop("barcode"),
        "category_map.primitives",
        "barcode",
        "missing",
    )
    assert "stack" in str(err), "What should list the expected set as well as the actual one"


def test_an_extra_primitive_in_the_map_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["category_map"]["primitives"].__setitem__("hologram", "figure"),
        "category_map.primitives",
        "hologram",
        "extra",
    )


def test_an_overlay_missing_from_the_map_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["category_map"]["overlays"].pop("stamp"),
        "category_map.overlays",
        "stamp",
        "missing",
    )


def test_an_extra_overlay_in_the_map_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["category_map"]["overlays"].__setitem__("sparkle", "figure"),
        "category_map.overlays",
        "sparkle",
        "extra",
    )


def test_a_duplicated_vocabulary_value_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["category_vocabulary"].append("table"),
        "category_vocabulary",
        "table",
        "duplicate",
    )


def test_a_duplicated_ignored_category_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["ignored_categories"].append("footer"),
        "ignored_categories",
        "footer",
        "duplicate",
    )


def test_an_ignored_distance_out_of_range_names_the_evaluator_threshold(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d.__setitem__("ignored_match_max_distance", -0.25),
        "ignored_match_max_distance",
        "(0, 1]",
        "ignored_match_max_distance: 0.25",
        "match_gt2pred_quick",
    )


def test_a_non_string_vocabulary_value_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, lambda d: d["category_vocabulary"].append(7), "category_vocabulary")


def test_a_textless_category_outside_the_vocabulary_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["textless_categories"].append("paragraph"),
        "textless_categories",
        "paragraph",
    )


def test_a_scored_tier_absent_from_the_generation_config_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["layout_scored_tiers"].append("catastrophic"),
        "layout_scored_tiers",
        "catastrophic",
        "light",
        "generation_config.yml",
    )


@pytest.mark.parametrize(
    "key", ["name", "backend", "runner", "device", "conda_env_file", "inputs", "credits"]
)
def test_a_missing_tool_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["tools"][0].pop(key), "tools[0]", key, "missing")


@pytest.mark.parametrize(
    ("key", "bad"), [("name", ""), ("backend", 3), ("runner", None), ("device", ""), ("device", 0)]
)
def test_a_mistyped_tool_key_is_refused(tmp_path: Path, key: str, bad: Any) -> None:
    refused(tmp_path, lambda d: d["tools"][0].__setitem__(key, bad), f"tools[0].{key}")


def test_a_non_mapping_tool_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, lambda d: d["tools"].__setitem__(1, "docling"), "tools[1]")


def test_a_duplicated_tool_name_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][1].__setitem__("name", d["tools"][0]["name"]),
        "tools[1].name",
        "mineru_pipeline",
        "duplicate",
    )


def test_a_bad_runner_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][0].__setitem__("runner", "cloud"),
        "tools[0].runner",
        "cloud",
        "local",
        "remote",
        "builtin",
    )


def test_a_local_tool_without_an_env_file_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][2].__setitem__("conda_env_file", None),
        "tools[2].conda_env_file",
        "docling",
        "local",
    )


def test_a_remote_tool_without_an_env_file_loads(tmp_path: Path) -> None:
    def remote(data: dict[str, Any]) -> None:
        data["tools"][0].update(runner="remote", conda_env_file=None)

    tool = load_parsing_config(write_variant(tmp_path, remote)).tools[0]
    assert (tool.name, tool.runner, tool.conda_env_file) == ("mineru_pipeline", "remote", None)


def test_a_remote_tool_with_an_env_file_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][0].__setitem__("runner", "remote"),
        "tools[0].conda_env_file",
        "mineru_pipeline",
        "remote",
        "null",
    )


@pytest.mark.parametrize("bad", [[], "pdf", ["tiff"], ["png", "png"], [3]])
def test_bad_inputs_are_refused(tmp_path: Path, bad: Any) -> None:
    refused(tmp_path, lambda d: d["tools"][0].__setitem__("inputs", bad), "tools[0].inputs", "png", "pdf")


def test_a_builtin_tool_with_an_env_file_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][4].__setitem__("conda_env_file", "envs/text_layer.yml"),
        "tools[4].conda_env_file",
        "text_layer",
        "builtin",
        "null",
    )


def test_a_builtin_tool_on_an_accelerator_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][4].__setitem__("device", "mps"),
        "tools[4].device",
        "text_layer",
        "builtin",
        "cpu",
    )


def test_every_shipped_tool_carries_its_credits() -> None:
    config = load_parsing_config(SHIPPED)
    assert {tool.name: tool.credits.project for tool in config.tools} == {
        "mineru_pipeline": "MinerU",
        "mineru_vlm": "MinerU",
        "docling": "Docling",
        "marker": "Marker",
        "text_layer": "pypdfium2",
    }
    for tool in config.tools:
        credits = tool.credits
        assert credits.url.startswith("https://"), tool.name
        assert "http" in credits.code_licence and "http" in credits.weights_licence, tool.name


@pytest.mark.parametrize("key", ["project", "url", "code_licence", "weights_licence"])
def test_a_missing_credits_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["tools"][3]["credits"].pop(key), f"tools[3].credits.{key}", "missing")


@pytest.mark.parametrize("key", ["project", "url", "code_licence", "weights_licence"])
def test_a_mistyped_credits_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["tools"][3]["credits"].__setitem__(key, ""), f"tools[3].credits.{key}")


def test_credits_that_are_not_a_mapping_are_refused(tmp_path: Path) -> None:
    refused(tmp_path, lambda d: d["tools"][3].__setitem__("credits", "MIT"), "tools[3].credits")


def test_an_unknown_credits_key_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        lambda d: d["tools"][3]["credits"].__setitem__("copyright", "x"),
        "tools[3].credits",
        "copyright",
    )


def test_the_shipped_control_threshold_checks_text_and_teds() -> None:
    control = load_parsing_config(SHIPPED).native_pdf_control
    assert control.max_difference == 0.02
    assert control.metrics == ("text_block_edit_dist", "table_teds")


def test_the_control_metrics_are_the_headline_scores_the_leaderboard_reports() -> None:
    from scripts.score_parsing import METRICS

    assert SCORED_METRICS == tuple(name for name, _, _ in METRICS)


@pytest.mark.parametrize("key", ["max_difference", "metrics"])
def test_a_missing_control_key_is_refused(tmp_path: Path, key: str) -> None:
    refused(tmp_path, lambda d: d["native_pdf_control"].pop(key), f"native_pdf_control.{key}", "missing")


@pytest.mark.parametrize("bad", ["0.02", True, None, 0, 1, 1.5, -0.02])
def test_a_control_threshold_outside_the_unit_interval_is_refused(tmp_path: Path, bad: Any) -> None:
    refused(
        tmp_path,
        lambda d: d["native_pdf_control"].__setitem__("max_difference", bad),
        "native_pdf_control.max_difference",
        "(0, 1)",
        "max_difference: 0.02",
    )


@pytest.mark.parametrize("bad", [[], "table_teds", ["teds"], ["table_teds", "table_teds"], [3]])
def test_bad_control_metrics_are_refused(tmp_path: Path, bad: Any) -> None:
    refused(
        tmp_path,
        lambda d: d["native_pdf_control"].__setitem__("metrics", bad),
        "native_pdf_control.metrics",
        "text_block_edit_dist",
    )


def test_a_control_that_is_not_a_mapping_or_has_a_stray_key_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, lambda d: d.__setitem__("native_pdf_control", 0.02), "native_pdf_control")
    refused(
        tmp_path,
        lambda d: d["native_pdf_control"].__setitem__("relative", True),
        "native_pdf_control",
        "relative",
    )
