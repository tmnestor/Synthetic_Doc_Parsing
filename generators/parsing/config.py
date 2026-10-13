"""Load and validate config/parsing_config.yml.

Every key is required and the YAML is the single source of truth: this module holds no default for any of
them. A missing or invalid key fails fast with a four-part diagnostic (What, Where, Expected, Recover).
`layout_parse_errors` checks, for `validate`, that every `parse_category` a layout body declares is in the
configured vocabulary (the layout schema itself does not know the vocabulary), and that every table with
`parse_join: previous_table` has a table drawn before it in the same layout to join.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import generators.overlay.engine  # noqa: F401  registers every overlay drawer in OVERLAY_DRAWERS
from generators.degradation.tiers import load_tiers
from generators.distractors.paths import GENERATION_CONFIG_PATH, REPO_ROOT
from generators.layout_dsl.schema import ANCHORS, LayoutSchemaError, _err, _iter_blocks
from generators.overlay.registry import OVERLAY_DRAWERS

# The eight leaf primitives and three containers that draw a block a parser can find (Decision 1). `rule`
# and `spacer` draw no block of their own and take no category.
BLOCK_PRIMITIVES: frozenset[str] = frozenset(
    {"text", "pair", "block", "table", "banner", "logo", "barcode", "qrcode", "panel", "split", "stack"}
)

PARSING_CONFIG_PATH = REPO_ROOT / "config" / "parsing_config.yml"
RUNNERS = ("local", "remote")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TOP_LEVEL_KEYS = (
    "evaluator",
    "category_vocabulary",
    "textless_categories",
    "category_map",
    "fold_displacement_max_px",
    "layout_scored_tiers",
    "tools",
)
_EVALUATOR_KEYS = ("repo", "commit", "conda_env_file", "formula_metrics")
_MAP_SECTIONS = ("primitives", "overlays", "anchors")
_TOOL_KEYS = ("name", "backend", "runner", "conda_env_file")


class ParsingConfigError(RuntimeError):
    """Raised when config/parsing_config.yml is missing, unparseable or invalid."""


@dataclass(frozen=True)
class EvaluatorPin:
    """The pinned evaluator.

    Attributes:
        repo: Git URL of the evaluator repository.
        commit: Full 40-character lowercase commit SHA.
        conda_env_file: Conda environment YAML (relative to the repository root) that builds it.
        formula_metrics: Whether formula metrics run; refused in stage one.
    """

    repo: str
    commit: str
    conda_env_file: str
    formula_metrics: bool


@dataclass(frozen=True)
class ToolSpec:
    """One parser under test.

    Attributes:
        name: Unique tool name.
        backend: The tool's own backend selector.
        runner: `local` or `remote`.
        conda_env_file: Env file for a local tool; `None` for a remote tool.
    """

    name: str
    backend: str
    runner: str
    conda_env_file: str | None


@dataclass(frozen=True)
class ParsingConfig:
    """The validated parsing configuration.

    Attributes:
        evaluator: The pinned evaluator.
        category_vocabulary: The allowed OmniDocBench `category_type` values.
        textless_categories: Categories whose blocks carry no text.
        primitives: Default category of each block primitive.
        overlays: Default category of each overlay drawer.
        anchors: Default category of each container anchor.
        fold_displacement_max_px: Largest fold displacement tolerated, in pixels.
        layout_scored_tiers: Degradation tiers scored for layout.
        tools: The parsers scored.
    """

    evaluator: EvaluatorPin
    category_vocabulary: tuple[str, ...]
    textless_categories: tuple[str, ...]
    primitives: dict[str, str]
    overlays: dict[str, str]
    anchors: dict[str, str]
    fold_displacement_max_px: int
    layout_scored_tiers: tuple[str, ...]
    tools: tuple[ToolSpec, ...]


def _fail(
    what: str, *, path: Path, key: str, expected: str, recover: str, title: str = "Invalid parsing config."
) -> ParsingConfigError:
    """Build a four-part fail-fast diagnostic for the parsing config."""
    return ParsingConfigError(
        f"{title}\n"
        f"  What:     {what}\n"
        f"  Where:    {path.resolve()} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _require(data: dict, key: str, *, path: Path, prefix: str, example: str) -> Any:
    """Return `data[key]`, or raise naming the missing dotted key."""
    if key not in data:
        dotted = f"{prefix}{key}"
        raise _fail(
            what=f"required key '{dotted}' is missing.",
            path=path,
            key=dotted,
            expected=f"the key present, e.g.\n{example}",
            recover=f"add '{key}' to {path.name}"
            + (f" under '{prefix.rstrip('.')}'." if prefix else " at the top level."),
        )
    return data[key]


def _string_list(value: Any, *, path: Path, key: str, example: str, allow_empty: bool) -> tuple[str, ...]:
    """Check `value` is a list of non-empty strings, and return it as a tuple."""
    ok = (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item for item in value)
    )
    if not ok:
        raise _fail(
            what=f"'{key}' is {value!r}, not a {'' if allow_empty else 'non-empty '}list of non-empty strings.",
            path=path,
            key=key,
            expected=f"a YAML list of strings, e.g.\n{example}",
            recover=f"replace '{key}' in {path.name} with a list of strings.",
        )
    return tuple(value)


def _nonempty_string(value: Any, *, path: Path, key: str, example: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(
            what=f"'{key}' is {value!r}, not a non-empty string.",
            path=path,
            key=key,
            expected=f"a non-empty string, e.g.\n{example}",
            recover=f"set '{key}' in {path.name} to a non-empty string.",
        )
    return value


def _mapping(value: Any, *, path: Path, key: str, example: str) -> dict:
    if not isinstance(value, dict):
        raise _fail(
            what=f"'{key}' is {type(value).__name__}, not a mapping.",
            path=path,
            key=key,
            expected=f"a mapping, e.g.\n{example}",
            recover=f"replace '{key}' in {path.name} with a mapping.",
        )
    return value


def _load_evaluator(raw: Any, *, path: Path) -> EvaluatorPin:
    example = (
        "evaluator:\n"
        "  repo: https://github.com/opendatalab/OmniDocBench.git\n"
        "  commit: 0123456789abcdef0123456789abcdef01234567\n"
        "  conda_env_file: envs/omnidocbench.yml\n"
        "  formula_metrics: false"
    )
    block = _mapping(raw, path=path, key="evaluator", example=example)
    values = {
        key: _require(block, key, path=path, prefix="evaluator.", example=example)
        for key in _EVALUATOR_KEYS
    }
    repo = _nonempty_string(values["repo"], path=path, key="evaluator.repo", example=example)
    conda_env_file = _nonempty_string(
        values["conda_env_file"], path=path, key="evaluator.conda_env_file", example=example
    )
    commit = values["commit"]
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise _fail(
            what=f"'evaluator.commit' is {commit!r}, not a full 40-character lowercase hex commit SHA.",
            path=path,
            key="evaluator.commit",
            expected=f"40 hexadecimal characters (0-9, a-f), e.g.\n{example}",
            recover="look the commit up with `git ls-remote <repo> HEAD` and paste the full SHA.",
        )
    formula_metrics = values["formula_metrics"]
    if not isinstance(formula_metrics, bool):
        raise _fail(
            what=f"'evaluator.formula_metrics' is {formula_metrics!r}, not a boolean.",
            path=path,
            key="evaluator.formula_metrics",
            expected=f"formula_metrics: false (the only value stage one accepts), e.g.\n{example}",
            recover="set 'evaluator.formula_metrics' to false.",
        )
    if formula_metrics:
        raise _fail(
            what="'evaluator.formula_metrics' is true, but formula metrics are refused in stage one.",
            path=path,
            key="evaluator.formula_metrics",
            expected="formula_metrics: false",
            recover="set 'evaluator.formula_metrics' to false: formulas arrive with stage three.",
        )
    return EvaluatorPin(repo=repo, commit=commit, conda_env_file=conda_env_file, formula_metrics=False)


def _check_key_set(
    mapping: dict,
    expected: set[str],
    *,
    path: Path,
    key: str,
    noun: str,
    example: str,
    subset: bool = False,
) -> None:
    """Refuse a map whose keys differ from `expected`, or with `subset` stray outside it; What lists both."""
    missing = [] if subset else sorted(expected - set(mapping))
    extra = sorted(set(mapping) - expected)
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing {noun}(s) {missing}")
        if extra:
            problems.append(f"extra {noun}(s) {extra}")
        raise _fail(
            what=f"'{key}' has " + " and ".join(problems) + f"; the expected set is {sorted(expected)} "
            f"and the actual set is {sorted(mapping)}.",
            path=path,
            key=key,
            expected=(
                f"only keys from {sorted(expected)}, e.g.\n{example}"
                if subset
                else f"exactly the keys {sorted(expected)}, e.g.\n{example}"
            ),
            recover=(
                f"remove or correct the extra keys of '{key}' in {path.name}."
                if subset
                else f"add the missing and remove the extra keys of '{key}' in {path.name}."
            ),
        )


def _load_category_map(
    raw: Any, *, path: Path, vocabulary: tuple[str, ...]
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    example = (
        "category_map:\n"
        "  primitives: {text: text_block, table: table, ...}\n"
        "  overlays: {stamp: text_block, ...}\n"
        "  anchors: {foot: footer}"
    )
    block = _mapping(raw, path=path, key="category_map", example=example)
    sections: dict[str, dict[str, str]] = {}
    for section in _MAP_SECTIONS:
        value = _require(block, section, path=path, prefix="category_map.", example=example)
        sections[section] = _mapping(value, path=path, key=f"category_map.{section}", example=example)
    _check_key_set(
        sections["primitives"],
        set(BLOCK_PRIMITIVES),
        path=path,
        key="category_map.primitives",
        noun="primitive",
        example="primitives: {text: text_block, banner: title, ...}",
    )
    _check_key_set(
        sections["overlays"],
        set(OVERLAY_DRAWERS),
        path=path,
        key="category_map.overlays",
        noun="overlay",
        example="overlays: {stamp: text_block, ink_marks: abandon, ...}",
    )
    _check_key_set(
        sections["anchors"],
        set(ANCHORS),
        path=path,
        key="category_map.anchors",
        noun="anchor",
        example="anchors: {foot: footer}",
        subset=True,
    )
    for section, mapping in sections.items():
        for name, category in mapping.items():
            if category not in vocabulary:
                raise _fail(
                    what=f"'category_map.{section}.{name}' is {category!r}, which is not in "
                    "category_vocabulary.",
                    path=path,
                    key=f"category_map.{section}.{name}",
                    expected=f"one of {list(vocabulary)}.",
                    recover=f"set '{name}' under category_map.{section} to a category_vocabulary value.",
                )
    return sections["primitives"], sections["overlays"], sections["anchors"]


def _load_tiers(raw: Any, *, path: Path) -> tuple[str, ...]:
    example = "layout_scored_tiers: [light, moderate, heavy]"
    tiers = _string_list(raw, path=path, key="layout_scored_tiers", example=example, allow_empty=False)
    declared = [tier.name for tier in load_tiers(GENERATION_CONFIG_PATH)]
    for index, name in enumerate(tiers):
        if name not in declared:
            raise _fail(
                what=f"'layout_scored_tiers[{index}]' names tier {name!r}, which "
                f"{GENERATION_CONFIG_PATH} does not declare under receipt_degradation.tiers.",
                path=path,
                key=f"layout_scored_tiers[{index}]",
                expected=f"names drawn from {declared}, e.g.\nlayout_scored_tiers: {declared}",
                recover=f"use a tier name from receipt_degradation.tiers in {GENERATION_CONFIG_PATH.name}.",
            )
    return tiers


def _load_tools(raw: Any, *, path: Path) -> tuple[ToolSpec, ...]:
    example = (
        "tools:\n"
        "  - {name: mineru_pipeline, backend: pipeline, runner: remote, conda_env_file: null}\n"
        "  - {name: docling, backend: default, runner: local, conda_env_file: envs/docling.yml}"
    )
    if not isinstance(raw, list) or not raw:
        raise _fail(
            what=f"'tools' is {raw!r}, not a non-empty list of tool mappings.",
            path=path,
            key="tools",
            expected=f"a non-empty list, e.g.\n{example}",
            recover=f"declare at least one tool under 'tools' in {path.name}.",
        )
    tools: list[ToolSpec] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(raw):
        here = f"tools[{index}]"
        if not isinstance(item, dict):
            raise _fail(
                what=f"'{here}' is {type(item).__name__}, not a mapping.",
                path=path,
                key=here,
                expected=f"a mapping carrying {list(_TOOL_KEYS)}, e.g.\n{example}",
                recover=f"replace '{here}' in {path.name} with a tool mapping.",
            )
        values = {
            key: _require(item, key, path=path, prefix=f"{here}.", example=example) for key in _TOOL_KEYS
        }
        name = _nonempty_string(values["name"], path=path, key=f"{here}.name", example=example)
        backend = _nonempty_string(values["backend"], path=path, key=f"{here}.backend", example=example)
        if name in seen:
            raise _fail(
                what=f"'{here}.name' repeats the duplicate tool name {name!r} first used at tools[{seen[name]}].",
                path=path,
                key=f"{here}.name",
                expected="a name unique across every tool, e.g. mineru_pipeline, docling.",
                recover=f"rename tools[{index}] in {path.name}.",
            )
        seen[name] = index
        runner = values["runner"]
        if runner not in RUNNERS:
            raise _fail(
                what=f"'{here}.runner' is {runner!r}, not one of {list(RUNNERS)}.",
                path=path,
                key=f"{here}.runner",
                expected=f"one of {list(RUNNERS)}, e.g. runner: remote",
                recover=f"set '{here}.runner' to local or remote.",
            )
        env_file = values["conda_env_file"]
        if runner == "local" and (not isinstance(env_file, str) or not env_file):
            raise _fail(
                what=f"local tool {name!r} has no conda_env_file ({env_file!r}), so nothing builds its env.",
                path=path,
                key=f"{here}.conda_env_file",
                expected="a local tool names its env file, e.g. conda_env_file: envs/docling.yml",
                recover=f"set '{here}.conda_env_file' to the tool's envs/*.yml file.",
            )
        if runner == "remote" and env_file is not None:
            raise _fail(
                what=f"remote tool {name!r} names conda_env_file {env_file!r}; a remote tool builds no "
                "local env.",
                path=path,
                key=f"{here}.conda_env_file",
                expected="a remote tool gives null, e.g. conda_env_file: null",
                recover=f"set '{here}.conda_env_file' to null, or change the runner to local.",
            )
        tools.append(ToolSpec(name=name, backend=backend, runner=runner, conda_env_file=env_file))
    return tuple(tools)


def load_parsing_config(path: Path) -> ParsingConfig:
    """Load and validate the parsing config.

    Args:
        path: Path to parsing_config.yml.

    Returns:
        The validated configuration.

    Raises:
        ParsingConfigError: If the file is missing or unparseable, or any key is missing or invalid.
    """
    if not path.exists():
        raise _fail(
            what=f"the parsing config file does not exist at {path.resolve()}.",
            path=path,
            key="(file)",
            expected="config/parsing_config.yml carrying every required key, starting\n"
            "evaluator: {repo: ..., commit: ..., conda_env_file: ..., formula_metrics: false}",
            recover="create config/parsing_config.yml with every required key.",
        )
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as err:
        raise _fail(
            what=f"the YAML does not parse: {err}",
            path=path,
            key="(file)",
            expected="valid YAML, e.g.\nfold_displacement_max_px: 3",
            recover="fix the syntax error at the reported line.",
        ) from err
    if not isinstance(data, dict):
        raise _fail(
            what=f"the top level is {type(data).__name__}, not a mapping.",
            path=path,
            key="(top level)",
            expected="a key/value mapping, e.g.\nfold_displacement_max_px: 3",
            recover="replace the file contents with a mapping of the required keys.",
        )

    top = {key: _require(data, key, path=path, prefix="", example=f"{key}: ...") for key in _TOP_LEVEL_KEYS}
    evaluator = _load_evaluator(top["evaluator"], path=path)
    vocabulary = _string_list(
        top["category_vocabulary"],
        path=path,
        key="category_vocabulary",
        example="category_vocabulary: [title, text_block, figure, table]",
        allow_empty=False,
    )
    seen: set[str] = set()
    for value in vocabulary:
        if value in seen:
            raise _fail(
                what=f"'category_vocabulary' lists {value!r} more than once (a duplicate).",
                path=path,
                key="category_vocabulary",
                expected="each category once, e.g.\ncategory_vocabulary: [title, text_block, figure, table]",
                recover=f"remove the duplicate {value!r} from 'category_vocabulary'.",
            )
        seen.add(value)
    textless = _string_list(
        top["textless_categories"],
        path=path,
        key="textless_categories",
        example="textless_categories: [figure, abandon]",
        allow_empty=True,
    )
    for value in textless:
        if value not in vocabulary:
            raise _fail(
                what=f"'textless_categories' names {value!r}, which is not in category_vocabulary.",
                path=path,
                key="textless_categories",
                expected=f"values drawn from {list(vocabulary)}.",
                recover=f"remove {value!r} or add it to 'category_vocabulary'.",
            )
    primitives, overlays, anchors = _load_category_map(
        top["category_map"], path=path, vocabulary=vocabulary
    )
    fold = top["fold_displacement_max_px"]
    if isinstance(fold, bool) or not isinstance(fold, int) or fold < 0:
        raise _fail(
            what=f"'fold_displacement_max_px' is {fold!r}, not a non-negative integer.",
            path=path,
            key="fold_displacement_max_px",
            expected="a whole number of pixels, e.g.\nfold_displacement_max_px: 3",
            recover=f"set 'fold_displacement_max_px' in {path.name} to a non-negative integer.",
        )
    return ParsingConfig(
        evaluator=evaluator,
        category_vocabulary=vocabulary,
        textless_categories=textless,
        primitives=dict(primitives),
        overlays=dict(overlays),
        anchors=dict(anchors),
        fold_displacement_max_px=fold,
        layout_scored_tiers=_load_tiers(top["layout_scored_tiers"], path=path),
        tools=_load_tools(top["tools"], path=path),
    )


def layout_parse_errors(layouts: dict, *, layout_path: str, config: ParsingConfig) -> list[str]:
    """Check every `parse_category` a layout body declares, and every `parse_join`.

    A category must be in the configured vocabulary. A table joined to the previous table needs a table
    before it in draw order: the body is walked as the layout engine draws it, a split column by column.

    Args:
        layouts: Layout id to layout mapping, as `load_layout_registry` returns.
        layout_path: Path to the layout YAML, used in diagnostics.
        config: The validated parsing configuration.

    Returns:
        One four-part diagnostic per offending block; empty when every declared key is valid.
    """
    errors: list[str] = []
    for layout_id, layout in layouts.items():
        if not isinstance(layout, dict):
            continue
        table_seen = False
        for block, key_path in _iter_blocks(layout.get("body"), f"{layout_id}.body"):
            if block.get("type") == "table":
                if "parse_join" in block and not table_seen:
                    errors.append(_orphan_join(layout_path, key_path))
                table_seen = True
            category = block.get("parse_category")
            if category is None or category in config.category_vocabulary:
                continue
            error: LayoutSchemaError = _err(
                f"parse_category {category!r} is not in category_vocabulary.",
                layout_path=layout_path,
                key_path=f"{key_path}.parse_category",
                expected=f"one of {list(config.category_vocabulary)}.",
                recover="use a category_vocabulary value, or add the category to "
                "category_vocabulary in config/parsing_config.yml.",
            )
            errors.append(str(error))
    return errors


def _orphan_join(layout_path: str, key_path: str) -> str:
    """Word the refusal for a joined table with no table drawn before it."""
    error: LayoutSchemaError = _err(
        "parse_join: previous_table is set on a table with no table drawn before it in this layout, so its "
        "rows have no table to join.",
        layout_path=layout_path,
        key_path=f"{key_path}.parse_join",
        expected="the joined table drawn after another table, e.g.\n"
        "              - {type: table, rows: ..., columns: [...]}\n"
        "              - {type: table, rows: ..., columns: [...], parse_join: previous_table}",
        recover="remove parse_join from this table, or move it after the table its rows belong to.",
    )
    return str(error)
