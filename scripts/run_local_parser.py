#!/usr/bin/env python3
"""Run one configured parser over one half or typeset set of a snapshot, on this machine, and record it.

    python scripts/run_local_parser.py --tool docling --snapshot ../evaluation_data/snapshot_20261013 \\
        --half typeset_plain --input pdf

The tool comes from `tools` in `config/parsing_config.yml`; a `remote` tool is refused. A `local` tool runs
through `conda run -n <env>`, the env named by the `name:` key of the env file the tool's `conda_env_file`
names; a `builtin` tool (the text-layer baseline) runs with this script's own interpreter. `--input` is
required and names the page files the tool is given: `png`, the images the set's parsing file lists
(`<snapshot>/<parsing_dir>/<prefix>.json`, each page's `image_path` relative to the snapshot), or `pdf`,
the native PDF beside each of them, which only a typeset set holds. It must be one of the tool's `inputs`,
and a stage-one half takes only `png`. Raw output goes to
`<eval_set.output_root>/raw_predictions/<tool>/<run name>/`, which must be absent or empty; the run name
is the half for a stage-one half and `<set>_<input>` for a typeset set (e.g. `typeset_plain_pdf`), unless
`--run-name` gives another, such as a smoke run. Beside the output lie the tool's log (`run.log`) and
`run.json`: tool, back-end, half (or set), input, device, env, tool version, start and end times, seconds,
the ground-truth pages (always the PNG names) and the files the tool was given. `collect_predictions.py`
then turns the raw output into one markdown file per page, named by the PNG's stem for either input.

`CONTRACTS` holds how each tool is invoked: its executable, its arguments, how it is told the device, and
the package whose version is recorded. That is the tool's own interface, so it lives here; what an
operator chooses (back-end, device, env) lives in the YAML. Every tool runs with the Hugging Face hub
offline, so a run uses only weights already downloaded (the runbook downloads each tool's weights once), and
a tool whose contract names weight variables is refused unless its env sets each one.
"""

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import (  # noqa: E402
    INPUT_KINDS,
    PARSING_CONFIG_PATH,
    RUNNERS,
    ParsingConfig,
    ParsingConfigError,
    ToolSpec,
    load_parsing_config,
)
from generators.snapshot.config import (  # noqa: E402
    HALVES,
    TYPESET_SETS,
    SnapshotConfig,
    load_snapshot_config,
)

# The record each run leaves beside its raw output, and the log of the tool's own output.
RUN_RECORD = "run.json"
RUN_LOG = "run.log"
RAW_DIR = "raw_predictions"
# MinerU's command, run so that a stalled local API cannot drop a status poll.
MINERU_CLI = Path(__file__).resolve().parent / "mineru_cli.py"
# The no-model baseline's command: a PDF's own text layer as markdown.
TEXT_LAYER_BASELINE = Path(__file__).resolve().parent / "text_layer_baseline.py"
# The runner values this script runs: a local tool through its conda env, a builtin one with this
# interpreter.
LOCAL_RUNNERS = ("local", "builtin")
# Every tool reads only weights already on disk.
_OFFLINE = {"HF_HUB_OFFLINE": "1"}


class ParserRunError(RuntimeError):
    """A tool cannot be run as configured, or its run failed."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> ParserRunError:
    """Build a four-part refusal, each part on a labelled line."""
    return ParserRunError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


@dataclass(frozen=True)
class ToolContract:
    """How one tool is invoked.

    Attributes:
        command: The tool's own command, as messages name it (`executable` may be a wrapper that runs it).
        executable: The command the tool's env provides.
        package: The distribution whose version the run records.
        backends: The `backend` values the tool accepts.
        arguments: The arguments after the executable, given the back-end, the input and output
            directories, the device and the input kind (`png` or `pdf`).
        environment: The environment variables that tell the tool its device.
        weight_variables: The environment variables that tell the tool where its weights are. Their values
            are machine paths, so they live in the env (`conda env config vars set`), never in a tracked
            file; each must be set in the env before a run.
    """

    command: str
    executable: str
    package: str
    backends: tuple[str, ...]
    arguments: Callable[[str, Path, Path, str, str], list[str]]
    environment: Callable[[str], dict[str, str]]
    weight_variables: tuple[str, ...]


def _mineru_arguments(backend: str, pages: Path, out: Path, device: str, input_kind: str) -> list[str]:
    # `mineru` through a wrapper that polls its local API on fresh connections (see the wrapper's
    # docstring); isolated mode, so nothing outside the MinerU env is imported. Images and PDFs alike: with
    # no -m, MinerU's own default (auto) reads a PDF's text layer where it classifies the PDF as text.
    return ["-I", str(MINERU_CLI), "-p", str(pages), "-o", str(out), "-b", backend]


def _docling_arguments(backend: str, pages: Path, out: Path, device: str, input_kind: str) -> list[str]:
    # The standard pipeline: markdown to score, json kept for the layout adapters; pictures as placeholders.
    # A PDF is read with Docling's default PDF backend, which takes the text layer's cells and OCRs only
    # regions that have none.
    return [
        "convert",
        str(pages),
        "--from",
        {"png": "image", "pdf": "pdf"}[input_kind],
        "--to",
        "md",
        "--to",
        "json",
        "--image-export-mode",
        "placeholder",
        "--device",
        device,
        "--output",
        str(out),
    ]


def _marker_arguments(backend: str, pages: Path, out: Path, device: str, input_kind: str) -> list[str]:
    # One worker: each worker loads every model and starts its own OCR server. Images only (its inputs).
    return [str(pages), "--output_dir", str(out), "--workers", "1"]


def _text_layer_arguments(backend: str, pages: Path, out: Path, device: str, input_kind: str) -> list[str]:
    # Isolated mode: the script needs only pypdfium2, from the env the runner itself runs in.
    return ["-I", str(TEXT_LAYER_BASELINE), str(pages), str(out)]


_MINERU = ToolContract(
    command="mineru",
    executable="python",
    package="mineru",
    backends=("pipeline", "vlm-engine", "hybrid-engine"),
    arguments=_mineru_arguments,
    environment=lambda device: {"MINERU_DEVICE_MODE": device},
    weight_variables=("MINERU_TOOLS_CONFIG_JSON",),
)
CONTRACTS: dict[str, ToolContract] = {
    "mineru_pipeline": _MINERU,
    "mineru_vlm": _MINERU,
    "docling": ToolContract(
        command="docling",
        executable="docling",
        package="docling",
        backends=("default",),
        arguments=_docling_arguments,
        environment=lambda device: {},
        weight_variables=("DOCLING_ARTIFACTS_PATH",),
    ),
    "marker": ToolContract(
        command="marker",
        executable="marker",
        package="marker-pdf",
        backends=("default",),
        arguments=_marker_arguments,
        environment=lambda device: {"TORCH_DEVICE": device},
        weight_variables=(),
    ),
    # Builtin: run with the runner's own interpreter, not through conda.
    "text_layer": ToolContract(
        command="text_layer_baseline",
        executable="python",
        package="pypdfium2",
        backends=("default",),
        arguments=_text_layer_arguments,
        environment=lambda device: {},
        weight_variables=(),
    ),
}


def _conda() -> str:
    return os.environ.get("CONDA_EXE") or shutil.which("conda") or "conda"


def local_tool(config: ParsingConfig, name: str, *, config_path: Path) -> ToolSpec:
    """Return the configured tool `name`, refusing one that is unknown, remote or has no contract.

    Args:
        config: The validated parsing configuration.
        name: The tool's name.
        config_path: The file `config` was read from, named in a refusal.

    Returns:
        The tool's configuration.

    Raises:
        ParserRunError: The tool is not configured, its runner is neither `local` nor `builtin` (the
            third runner value, `remote`, is run elsewhere), or this script has no command contract for it
            or its back-end.
    """
    names = [tool.name for tool in config.tools]
    if name not in names:
        raise _diagnostic(
            "The tool is not configured.",
            what=f"--tool {name!r} is not one of the configured tools {names}.",
            where=f"{config_path.resolve()} -> tools",
            expected=f"a configured tool name, e.g. --tool {names[0]}",
            recover="pass a name from tools in the parsing config, or add the tool there.",
        )
    index = names.index(name)
    tool = config.tools[index]
    if tool.runner not in LOCAL_RUNNERS:
        raise _diagnostic(
            "The tool is not run on this machine.",
            what=f"tool {name!r} has runner {tool.runner!r}; of the runner values {list(RUNNERS)}, this "
            f"script runs only {' and '.join(LOCAL_RUNNERS)} tools.",
            where=f"{config_path.resolve()} -> tools[{index}].runner",
            expected=f"runner: local with an env file, e.g. - {{name: {name}, backend: {tool.backend}, "
            f"runner: local, device: mps, conda_env_file: envs/{name}.yml}}, or runner: builtin with "
            "conda_env_file: null and device: cpu for a tool run in the generator's own env.",
            recover="run the tool where it is configured to run and copy its markdown back, or set it to "
            "local with its env file.",
        )
    contract = CONTRACTS.get(name)
    if contract is None:
        raise _diagnostic(
            "The tool has no command contract.",
            what=f"tool {name!r} is configured, but this script does not know how to invoke it.",
            where=f"{Path(__file__).resolve()} -> CONTRACTS",
            expected=f"an entry for {name!r} beside {sorted(CONTRACTS)}.",
            recover=f"add {name!r} to CONTRACTS with its executable, arguments and device setting.",
        )
    if tool.backend not in contract.backends:
        raise _diagnostic(
            "The tool's back-end is not one its command contract knows.",
            what=f"tool {name!r} has backend {tool.backend!r}; {contract.command} is invoked here with "
            f"one of {list(contract.backends)}.",
            where=f"{config_path.resolve()} -> tools[{index}].backend",
            expected=f"one of {list(contract.backends)}, e.g. backend: {contract.backends[0]}",
            recover=f"set tools[{index}].backend to a listed value, or extend CONTRACTS in this script.",
        )
    return tool


def env_name(tool: ToolSpec) -> str:
    """The conda env the tool runs in: the `name:` key of its env file.

    Raises:
        ParserRunError: The tool has no env file, or the file is missing or names no env.
    """
    if tool.conda_env_file is None:
        raise _diagnostic(
            "The tool has no env file.",
            what=f"tool {tool.name!r} names no conda_env_file, so there is no env to run it in.",
            where=f"{PARSING_CONFIG_PATH} -> tools ({tool.name}).conda_env_file",
            expected="conda_env_file: envs/<tool>.yml",
            recover="name the env file that builds the tool.",
        )
    path = REPO_ROOT / tool.conda_env_file
    spec = yaml.safe_load(path.read_text()) if path.is_file() else None
    name = spec.get("name") if isinstance(spec, dict) else None
    if not isinstance(name, str) or not name:
        raise _diagnostic(
            "The tool's env file names no env.",
            what=f"{path} {'has no name: key' if path.is_file() else 'does not exist'}, so the env to run "
            f"{tool.name!r} in is unknown.",
            where=f"{path} -> name",
            expected="a top-level name, e.g. name: docparse-docling",
            recover=f"add the env's name to {path.name} (the name `conda env create -f` gives the env).",
        )
    return name


def _set_prefix(half: str, *, snapshot_config: SnapshotConfig) -> str:
    """The directory prefix of a half or typeset set: its parsing file is `<parsing_dir>/<prefix>.json`."""
    if half in HALVES:
        return snapshot_config.half_prefixes[half]
    if half in TYPESET_SETS:
        return snapshot_config.typeset_prefixes[half]
    raise _diagnostic(
        "The set is neither a half nor a typeset set.",
        what=f"--half {half!r} is not one of the halves {list(HALVES)} or the typeset sets "
        f"{list(TYPESET_SETS)}.",
        where="the --half argument",
        expected=f"a half or typeset set, e.g. --half {TYPESET_SETS[0]}",
        recover="pass one of the listed names.",
    )


def default_run_name(half: str, input_kind: str) -> str:
    """The raw output directory's name for a run (Decision 10 of the stage-two plan).

    A stage-one half keeps its own name (it is only ever run on its PNGs); a typeset set's run is named
    after the set and the input, e.g. `typeset_plain_pdf`.
    """
    return half if half in HALVES else f"{half}_{input_kind}"


def check_input(tool: ToolSpec, half: str, input_kind: str | None, *, config_path: Path) -> str:
    """Refuse an input kind that is missing, unknown, not one the tool takes, or not in the half.

    Args:
        tool: The tool to run.
        half: The half or typeset set to run it on.
        input_kind: The `--input` value, `None` when it was not given.
        config_path: The parsing config the tool was read from, named in a refusal.

    Returns:
        The input kind, `png` or `pdf`.

    Raises:
        ParserRunError: The kind is missing or unknown, is not in the tool's `inputs`, or is `pdf` on a
            stage-one half (which holds only PNGs).
    """
    if input_kind not in INPUT_KINDS:
        given = "is missing" if input_kind is None else f"is {input_kind!r}"
        raise _diagnostic(
            "The run's input kind is missing or unknown.",
            what=f"--input {given}; every run names the page files it hands the tool, one of "
            f"{list(INPUT_KINDS)}.",
            where="the --input argument",
            expected=f"--input png (the page images) or --input pdf (the native PDFs of a typeset set), "
            f"e.g. --tool {tool.name} --half {half} --input {tool.inputs[0]}",
            recover=f"add --input with one of {list(INPUT_KINDS)} to the command.",
        )
    if input_kind not in tool.inputs:
        index = _tool_index(tool, config_path=config_path)
        raise _diagnostic(
            "The tool is not scored on this input.",
            what=f"--input {input_kind} was given, but tool {tool.name!r} takes only {list(tool.inputs)}.",
            where=f"{config_path.resolve()} -> tools[{index}].inputs",
            expected=f"an input the tool takes, e.g. --input {tool.inputs[0]}; or inputs: "
            f"{sorted({*tool.inputs, input_kind})} in its YAML entry, if it is to be scored on both",
            recover=f"pass --input {tool.inputs[0]}, or add {input_kind} to tools[{index}].inputs.",
        )
    if half in HALVES and input_kind != "png":
        raise _diagnostic(
            "A stage-one half has no PDFs.",
            what=f"--input {input_kind} was given for the half {half!r}, whose pages are PNG images only.",
            where="the --half and --input arguments",
            expected=f"--half {half} --input png, or a typeset set with its PDFs, e.g. --half "
            f"{TYPESET_SETS[0]} --input pdf (the typeset sets are {list(TYPESET_SETS)})",
            recover=f"run the half with --input png, or pass a typeset set for --input {input_kind}.",
        )
    return input_kind


def _tool_index(tool: ToolSpec, *, config_path: Path) -> int:
    """The tool's position under `tools` in the parsing config, for a refusal's Where line."""
    names = [configured.name for configured in load_parsing_config(config_path).tools]
    return names.index(tool.name)


def half_images(snapshot: Path, half: str, *, snapshot_config: SnapshotConfig) -> list[Path]:
    """The page images a half's (or typeset set's) parsing file lists, in its order.

    Raises:
        ParserRunError: The parsing file is missing or unreadable, or a page's image is missing.
    """
    gt = (
        snapshot
        / snapshot_config.parsing_dir
        / f"{_set_prefix(half, snapshot_config=snapshot_config)}.json"
    )
    try:
        pages = json.loads(gt.read_text())
        images = [snapshot / page["page_info"]["image_path"] for page in pages]
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise _diagnostic(
            "The half's parsing file cannot be read.",
            what=f"{gt} could not be read as OmniDocBench pages ({err!r}).",
            where=f"{gt}",
            expected="a JSON list of pages, each with page_info.image_path, as `eval-set` writes it.",
            recover="pass --snapshot a snapshot `python -m generators.pipeline eval-set` wrote.",
        ) from err
    missing = [str(image) for image in images if not image.is_file()]
    if missing:
        raise _diagnostic(
            "A page's image is missing.",
            what=f"{len(missing)} image(s) {gt.name} lists do not exist: {missing[:5]}",
            where=f"{gt} -> page_info.image_path",
            expected="every listed image beside the parsing file, under the snapshot.",
            recover="re-export the snapshot, or pass the snapshot the parsing file belongs to.",
        )
    return images


def page_inputs(images: list[Path], input_kind: str) -> list[Path]:
    """The files the tool is given for each page: the image itself, or the PDF beside it.

    A typeset set holds each page as `<stem>.pdf` beside `<stem>.png`, so a PDF run's outputs carry the
    image's stem and are paired with the same ground truth as a PNG run's.

    Raises:
        ParserRunError: A page's PDF is missing.
    """
    if input_kind == "png":
        return images
    pdfs = [image.with_suffix(".pdf") for image in images]
    missing = [str(pdf) for pdf in pdfs if not pdf.is_file()]
    if missing:
        raise _diagnostic(
            "A page's PDF is missing.",
            what=f"{len(missing)} PDF(s) beside the set's images do not exist: {missing[:5]}",
            where=f"{images[0].parent}",
            expected="every page's <stem>.pdf beside its <stem>.png, as the typeset export writes them.",
            recover="re-export the snapshot, or run the set with --input png.",
        )
    return pdfs


def _check_weight_variables(conda: str, env: str, contract: ToolContract) -> None:
    """Refuse a run whose env does not say where the tool's weights are."""
    for variable in contract.weight_variables:
        done = subprocess.run(
            [conda, "run", "-n", env, "printenv", variable], capture_output=True, text=True, check=False
        )
        if done.returncode != 0 or not done.stdout.strip():
            raise _diagnostic(
                "The tool's environment does not say where its weights are.",
                what=f"{variable} is not set in the environment the conda env {env!r} runs in, so "
                f"{contract.command} cannot find its weights (runs are offline, so nothing is downloaded).",
                where=f"the conda env {env!r} -> {variable}",
                expected=f"`conda run -n {env} printenv {variable}` printing the path, e.g. "
                f"{variable}=<weights dir>",
                recover=f"add `export {variable}=<path to the weights>` to the shell profile, or run "
                f"`conda env config vars set -n {env} {variable}=<path to the weights>`, then run again.",
            )


def _tool_version(conda: str, env: str, package: str) -> str:
    probe = f"import importlib.metadata as m; print(m.version({package!r}))"
    done = subprocess.run(
        [conda, "run", "-n", env, "python", "-c", probe], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise _diagnostic(
            "The tool's env cannot be run.",
            what=f"reading {package}'s version in env {env!r} failed:\n"
            f"{(done.stderr or done.stdout)[-2000:]}",
            where=f"the conda env {env!r}",
            expected=f'the env built, e.g. `conda run -n {env} python -c "{probe}"` printing a version.',
            recover="build it with `conda env create -f envs/<tool>.yml` (the runbook lists each env).",
        )
    return done.stdout.strip().splitlines()[-1]


def run_parser(
    tool: ToolSpec,
    *,
    snapshot: Path,
    half: str,
    input_kind: str | None,
    run_name: str,
    output_root: Path,
    snapshot_config: SnapshotConfig,
    config_path: Path,
) -> Path:
    """Run `tool` over one half's (or typeset set's) pages and record the run.

    Args:
        tool: A local or builtin tool `local_tool` returned.
        snapshot: The snapshot directory.
        half: One of the snapshot's halves or typeset sets.
        input_kind: The page files the tool is given, `png` or `pdf` (`None` is refused).
        run_name: The raw output directory's name under `raw_predictions/<tool>/`.
        output_root: The directory raw output is written under.
        snapshot_config: The snapshot configuration, naming the parsing directory and set prefixes.
        config_path: The parsing config `tool` was read from, named in a refusal.

    Returns:
        The raw output directory, holding the tool's output, `run.log` and `run.json`.

    Raises:
        ParserRunError: The input kind is refused (`check_input`), the output directory is inside the
            repository or already holds files, a page's file is missing, the env cannot be run, or the
            tool exited non-zero.
    """
    kind = check_input(tool, half, input_kind, config_path=config_path)
    snapshot = snapshot.resolve()
    out = output_root / RAW_DIR / tool.name / run_name
    resolved = out.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise _diagnostic(
            "The output directory lies inside the repository.",
            what=f"{resolved} is under {REPO_ROOT}; nothing generated is written inside the repository.",
            where="eval_set.output_root in the generation config",
            expected="an output root beside the repository, e.g. output_root: ../evaluation_data",
            recover="set eval_set.output_root to a directory outside the repository.",
        )
    if out.exists() and any(out.iterdir()):
        raise _diagnostic(
            "The output directory already holds a run.",
            what=f"{out} is not empty; a second run into it would leave two outputs for some pages.",
            where=f"{out}",
            expected="an absent or empty raw output directory.",
            recover=f"move or remove {out}, or pass another --run-name.",
        )
    images = half_images(snapshot, half, snapshot_config=snapshot_config)
    files = page_inputs(images, kind)
    contract = CONTRACTS[tool.name]
    if tool.runner == "builtin":
        # The generator's own env: this interpreter, with no conda env to check or enter.
        env = Path(sys.prefix).name
        version = importlib.metadata.version(contract.package)
        prefix = [sys.executable]
    else:
        env = env_name(tool)
        conda = _conda()
        _check_weight_variables(conda, env, contract)
        version = _tool_version(conda, env, contract.package)
        prefix = [conda, "run", "--no-capture-output", "-n", env, contract.executable]
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{tool.name}_{run_name}_") as staging:
        # The tool reads a directory: give it one holding exactly the set's files of this input kind.
        inputs = Path(staging)
        for file in files:
            (inputs / file.name).symlink_to(file)
        command = [*prefix, *contract.arguments(tool.backend, inputs, out, tool.device, kind)]
        started = datetime.now(UTC)
        clock = time.monotonic()
        with (out / RUN_LOG).open("w") as log:
            done = subprocess.run(
                command,
                env={**os.environ, **_OFFLINE, **contract.environment(tool.device)},
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        seconds = time.monotonic() - clock
        finished = datetime.now(UTC)
    if done.returncode != 0:
        tail = "\n".join((out / RUN_LOG).read_text().splitlines()[-30:])
        raise _diagnostic(
            "The tool failed.",
            what=f"`{' '.join(command)}` exited {done.returncode}:\n{tail}",
            where=f"{out / RUN_LOG}",
            expected="exit 0, with one markdown file per page under the output directory.",
            recover=f"read {out / RUN_LOG}, fix the cause, remove {out} and run again.",
        )
    record = {
        "tool": tool.name,
        "backend": tool.backend,
        "half": half,
        "input": kind,
        "run_name": run_name,
        "device": tool.device,
        "env": env,
        "tool_version": version,
        "command": command,
        "snapshot": str(snapshot),
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "seconds": round(seconds, 3),
        "page_count": len(images),
        # The ground-truth page each output pairs with (always the PNG), and the file the tool was given.
        "pages": [image.name for image in images],
        "input_files": [file.name for file in files],
    }
    (out / RUN_RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", required=True, help="A tool name from tools in the parsing config")
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory")
    parser.add_argument(
        "--half", required=True, choices=(*HALVES, *TYPESET_SETS), help="A half or typeset set"
    )
    # Required, but checked by `check_input` so a missing value is refused four-part like any other.
    parser.add_argument("--input", help=f"The page files the tool is given: one of {list(INPUT_KINDS)}")
    parser.add_argument("--run-name", help="The raw output directory's name (the default follows the set)")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    parser.add_argument("--parsing-config", type=Path, default=PARSING_CONFIG_PATH)
    args = parser.parse_args(argv)
    try:
        config = load_parsing_config(args.parsing_config)
        tool = local_tool(config, args.tool, config_path=args.parsing_config)
        kind = check_input(tool, args.half, args.input, config_path=args.parsing_config)
        snapshot_config = load_snapshot_config(args.config)
        out = run_parser(
            tool,
            snapshot=args.snapshot,
            half=args.half,
            input_kind=kind,
            run_name=args.run_name or default_run_name(args.half, kind),
            output_root=snapshot_config.output_root,
            snapshot_config=snapshot_config,
            config_path=args.parsing_config,
        )
    except (ParserRunError, ParsingConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    record = json.loads((out / RUN_RECORD).read_text())
    print(f"{record['tool']} on {record['page_count']} pages in {record['seconds']:.1f} s: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
