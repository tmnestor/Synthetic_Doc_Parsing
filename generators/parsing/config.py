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
# local: run on this machine from its own conda env; remote: run elsewhere, its markdown copied back;
# builtin: run in the generator's own env, with no env file, on the CPU.
RUNNERS = ("local", "remote", "builtin")
# The page files a tool can be given: a typeset set holds both, a stage-one half only the PNG.
INPUT_KINDS = ("png", "pdf")
# The evaluator's headline scores, as the leaderboard names them (scripts/score_parsing.py METRICS): the
# names `native_pdf_control.metrics` may list.
SCORED_METRICS = (
    "text_block_edit_dist",
    "table_teds",
    "table_teds_structure_only",
    "table_edit_dist",
    "reading_order_edit_dist",
)
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TOP_LEVEL_KEYS = (
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
)
_CONTROL_KEYS = ("max_difference", "metrics")
_EVALUATOR_KEYS = ("repo", "commit", "conda_env_file", "formula_metrics")
_MAP_SECTIONS = ("primitives", "overlays", "anchors")
_TOOL_KEYS = ("name", "backend", "runner", "device", "conda_env_file", "inputs", "credits")
_CREDIT_KEYS = ("project", "url", "code_licence", "weights_licence")


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
class Credits:
    """What the leaderboard credits a tool's project with.

    Attributes:
        project: The project's name.
        url: The project's URL.
        code_licence: The licence of the code, with its URL.
        weights_licence: The licence of the model weights, with its URL.
    """

    project: str
    url: str
    code_licence: str
    weights_licence: str


@dataclass(frozen=True)
class ToolSpec:
    """One parser under test.

    Attributes:
        name: Unique tool name.
        backend: The tool's own backend selector.
        runner: `local`, `remote` or `builtin`.
        device: The accelerator the tool runs its models on, e.g. `mps`, as the tool spells it; `cpu`
            for a builtin tool.
        conda_env_file: Env file for a local tool; `None` for a remote or builtin tool.
        inputs: The page files the tool is scored on, drawn from `INPUT_KINDS`, e.g. `("png", "pdf")`.
        credits: The project, URL and licences the leaderboard prints for the tool.
    """

    name: str
    backend: str
    runner: str
    device: str
    conda_env_file: str | None
    inputs: tuple[str, ...]
    credits: Credits


@dataclass(frozen=True)
class NativePdfControl:
    """The check that a typeset PNG run reproduces the tool's stage one image score.

    Attributes:
        max_difference: The largest absolute difference, in (0, 1), at which a checked metric of the
            typeset PNG run still matches the tool's stage one image score.
        metrics: The headline metrics checked, drawn from `SCORED_METRICS`.
    """

    max_difference: float
    metrics: tuple[str, ...]


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
        ignored_categories: The evaluator's ignored categories, whose lines it matches first and drops.
        ignored_match_max_distance: The normalised edit distance below which the evaluator matches a
            prediction to an ignored line.
        fold_displacement_max_px: Largest fold displacement tolerated, in pixels.
        layout_scored_tiers: Degradation tiers scored for layout.
        tools: The parsers scored.
        native_pdf_control: The threshold and metrics of the typeset PNG control.
    """

    evaluator: EvaluatorPin
    category_vocabulary: tuple[str, ...]
    textless_categories: tuple[str, ...]
    primitives: dict[str, str]
    overlays: dict[str, str]
    anchors: dict[str, str]
    ignored_categories: tuple[str, ...]
    ignored_match_max_distance: float
    fold_displacement_max_px: int
    layout_scored_tiers: tuple[str, ...]
    tools: tuple[ToolSpec, ...]
    native_pdf_control: NativePdfControl


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
            what=f"'{key}' is {value!r}, not a {'' if allow_empty else 'non-empty '}list of "
            "non-empty strings.",
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
    """Refuse a map whose keys differ from `expected`, or with `subset` stray outside it.

    What lists both the missing and the stray keys.
    """
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


def _load_ignored_categories(raw: Any, *, path: Path) -> tuple[str, ...]:
    example = "ignored_categories: [figure_caption, header, footer, page_number]"
    categories = _string_list(raw, path=path, key="ignored_categories", example=example, allow_empty=False)
    for index, value in enumerate(categories):
        if value in categories[:index]:
            raise _fail(
                what=f"'ignored_categories' lists {value!r} more than once (a duplicate).",
                path=path,
                key="ignored_categories",
                expected=f"each category once, e.g.\n{example}",
                recover=f"remove the duplicate {value!r} from 'ignored_categories'.",
            )
    return categories


def _load_ignored_distance(raw: Any, *, path: Path) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not 0 < raw <= 1:
        raise _fail(
            what=f"'ignored_match_max_distance' is {raw!r}, not a number in (0, 1].",
            path=path,
            key="ignored_match_max_distance",
            expected="a normalised edit distance above 0 and at most 1, the evaluator's ignored-match "
            "threshold, e.g.\nignored_match_max_distance: 0.25",
            recover=f"set 'ignored_match_max_distance' in {path.name} to the threshold the pinned "
            "evaluator's match_gt2pred_quick compares ignore_matrix against.",
        )
    return float(raw)


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


def _load_credits(raw: Any, *, path: Path, here: str) -> Credits:
    example = (
        "credits:\n"
        "  project: Docling\n"
        "  url: https://github.com/docling-project/docling\n"
        "  code_licence: MIT, https://github.com/docling-project/docling/blob/main/LICENSE\n"
        "  weights_licence: see https://huggingface.co/docling-project/docling-models"
    )
    values = _mapping(raw, path=path, key=here, example=example)
    unknown = sorted(set(values) - set(_CREDIT_KEYS))
    if unknown:
        raise _fail(
            what=f"'{here}' has the unknown keys {unknown}.",
            path=path,
            key=here,
            expected=f"exactly the keys {list(_CREDIT_KEYS)}, e.g.\n{example}",
            recover=f"remove {unknown} from '{here}' in {path.name}.",
        )
    fields = {
        key: _nonempty_string(
            _require(values, key, path=path, prefix=f"{here}.", example=example),
            path=path,
            key=f"{here}.{key}",
            example=example,
        )
        for key in _CREDIT_KEYS
    }
    return Credits(**fields)


def _load_inputs(raw: Any, *, path: Path, here: str) -> tuple[str, ...]:
    """Check a tool's `inputs`: a non-empty list of distinct values from `INPUT_KINDS`."""
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(kind, str) and kind in INPUT_KINDS for kind in raw)
        or len(set(raw)) != len(raw)
    ):
        raise _fail(
            what=f"'{here}' is {raw!r}, not a non-empty list of distinct input kinds from "
            f"{list(INPUT_KINDS)}.",
            path=path,
            key=here,
            expected="the page files the tool is scored on, e.g. inputs: [png, pdf] (or [png], or [pdf])",
            recover=f"set '{here}' in {path.name} to a list drawn from {list(INPUT_KINDS)}.",
        )
    return tuple(raw)


def _load_tools(raw: Any, *, path: Path) -> tuple[ToolSpec, ...]:
    example = (
        "tools:\n"
        "  - {name: mineru_pipeline, backend: pipeline, runner: local, device: mps,\n"
        "     conda_env_file: envs/mineru.yml, inputs: [png, pdf], credits: {project: MinerU, url: ...,\n"
        "     code_licence: ..., weights_licence: ...}}\n"
        "  - {name: text_layer, backend: default, runner: builtin, device: cpu, conda_env_file: null,\n"
        "     inputs: [pdf], credits: {...}}"
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
        device = _nonempty_string(values["device"], path=path, key=f"{here}.device", example=example)
        if name in seen:
            raise _fail(
                what=f"'{here}.name' repeats the duplicate tool name {name!r} first used at "
                f"tools[{seen[name]}].",
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
                expected=f"one of {list(RUNNERS)}, e.g. runner: local",
                recover=f"set '{here}.runner' to local, remote or builtin.",
            )
        env_file = values["conda_env_file"]
        if runner == "local" and (not isinstance(env_file, str) or not env_file):
            raise _fail(
                what=f"local tool {name!r} has no conda_env_file ({env_file!r}), so nothing builds its "
                "env.",
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
        if runner == "builtin" and env_file is not None:
            raise _fail(
                what=f"builtin tool {name!r} names conda_env_file {env_file!r}; a builtin tool runs in the "
                "generator's own env, so no env file builds it.",
                path=path,
                key=f"{here}.conda_env_file",
                expected="a builtin tool gives null, e.g. conda_env_file: null",
                recover=f"set '{here}.conda_env_file' to null, or change the runner to local.",
            )
        if runner == "builtin" and device != "cpu":
            raise _fail(
                what=f"builtin tool {name!r} has device {device!r}; a builtin tool runs no model, so it "
                "runs on the CPU.",
                path=path,
                key=f"{here}.device",
                expected="device: cpu",
                recover=f"set '{here}.device' to cpu.",
            )
        inputs = _load_inputs(values["inputs"], path=path, here=f"{here}.inputs")
        credits = _load_credits(values["credits"], path=path, here=f"{here}.credits")
        tools.append(
            ToolSpec(
                name=name,
                backend=backend,
                runner=runner,
                device=device,
                conda_env_file=env_file,
                inputs=inputs,
                credits=credits,
            )
        )
    return tuple(tools)


def _load_control(raw: Any, *, path: Path) -> NativePdfControl:
    """Check `native_pdf_control`: a threshold in (0, 1) and a non-empty list of distinct metrics."""
    example = "native_pdf_control:\n  max_difference: 0.02\n  metrics: [text_block_edit_dist, table_teds]"
    block = _mapping(raw, path=path, key="native_pdf_control", example=example)
    values = {
        key: _require(block, key, path=path, prefix="native_pdf_control.", example=example)
        for key in _CONTROL_KEYS
    }
    _check_key_set(
        block, set(_CONTROL_KEYS), path=path, key="native_pdf_control", noun="key", example=example
    )
    limit = values["max_difference"]
    if isinstance(limit, bool) or not isinstance(limit, int | float) or not 0 < limit < 1:
        raise _fail(
            what=f"'native_pdf_control.max_difference' is {limit!r}, not a number in (0, 1).",
            path=path,
            key="native_pdf_control.max_difference",
            expected="the largest absolute difference between a typeset PNG run's score and the tool's "
            f"stage one image score that still counts as the same, above 0 and below 1, e.g.\n{example}",
            recover=f"set 'native_pdf_control.max_difference' in {path.name} to a number such as 0.02.",
        )
    key = "native_pdf_control.metrics"
    metrics = _string_list(values["metrics"], path=path, key=key, example=example, allow_empty=False)
    for index, name in enumerate(metrics):
        if name not in SCORED_METRICS or name in metrics[:index]:
            problem = "is listed twice" if name in metrics[:index] else "is not a headline metric"
            raise _fail(
                what=f"'{key}[{index}]' is {name!r}, which {problem}.",
                path=path,
                key=key,
                expected=f"distinct names from {list(SCORED_METRICS)}, e.g.\n{example}",
                recover=f"list each checked metric once under '{key}' in {path.name}.",
            )
    return NativePdfControl(max_difference=float(limit), metrics=metrics)


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
                expected="each category once, e.g.\n"
                "category_vocabulary: [title, text_block, figure, table]",
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
    ignored = _load_ignored_categories(top["ignored_categories"], path=path)
    distance = _load_ignored_distance(top["ignored_match_max_distance"], path=path)
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
        ignored_categories=ignored,
        ignored_match_max_distance=distance,
        fold_displacement_max_px=fold,
        layout_scored_tiers=_load_tiers(top["layout_scored_tiers"], path=path),
        tools=_load_tools(top["tools"], path=path),
        native_pdf_control=_load_control(top["native_pdf_control"], path=path),
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
