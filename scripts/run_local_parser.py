#!/usr/bin/env python3
"""Run one configured parser over one half of a snapshot, on this machine, and record the run.

    python scripts/run_local_parser.py --tool docling --snapshot ../evaluation_data/snapshot_20261013 \\
        --half plain

The tool comes from `tools` in `config/parsing_config.yml`; a tool whose runner is not `local` is refused.
It runs through `conda run -n <env>`, the env named by the `name:` key of the env file the tool's
`conda_env_file` names, on exactly the images the half's parsing file lists (`<snapshot>/<parsing_dir>/
<half prefix>.json`, each page's `image_path` relative to the snapshot). Raw output goes to
`<eval_set.output_root>/raw_predictions/<tool>/<run name>/` (the run name is the half unless
`--run-name` gives another, such as a smoke run), which must be absent or empty, beside the tool's log
(`run.log`) and `run.json`: tool, back-end, half, device, env, tool version, start and end times, seconds
and the pages run. `collect_predictions.py` then turns the raw output into one markdown file per page.

`CONTRACTS` holds how each tool is invoked: its executable, its arguments, how it is told the device, and
the package whose version is recorded. That is the tool's own interface, so it lives here; what an
operator chooses (back-end, device, env) lives in the YAML. Every tool runs with the Hugging Face hub
offline, so a run uses only weights already downloaded (the runbook downloads each tool's weights once), and
a tool whose contract names weight variables is refused unless its env sets each one.
"""

import argparse
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
    PARSING_CONFIG_PATH,
    ParsingConfig,
    ParsingConfigError,
    ToolSpec,
    load_parsing_config,
)
from generators.snapshot.config import HALVES, SnapshotConfig, load_snapshot_config  # noqa: E402

# The record each run leaves beside its raw output, and the log of the tool's own output.
RUN_RECORD = "run.json"
RUN_LOG = "run.log"
RAW_DIR = "raw_predictions"
# MinerU's command, run so that a stalled local API cannot drop a status poll.
MINERU_CLI = Path(__file__).resolve().parent / "mineru_cli.py"
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
        executable: The command the tool's env provides.
        package: The distribution whose version the run records.
        backends: The `backend` values the tool accepts.
        arguments: The arguments after the executable, given the back-end, input and output directories
            and the device.
        environment: The environment variables that tell the tool its device.
        weight_variables: The environment variables that tell the tool where its weights are. Their values
            are machine paths, so they live in the env (`conda env config vars set`), never in a tracked
            file; each must be set in the env before a run.
    """

    executable: str
    package: str
    backends: tuple[str, ...]
    arguments: Callable[[str, Path, Path, str], list[str]]
    environment: Callable[[str], dict[str, str]]
    weight_variables: tuple[str, ...]


def _mineru_arguments(backend: str, images: Path, out: Path, device: str) -> list[str]:
    # `mineru` through a wrapper that polls its local API on fresh connections (see the wrapper's
    # docstring); isolated mode, so nothing outside the MinerU env is imported.
    return ["-I", str(MINERU_CLI), "-p", str(images), "-o", str(out), "-b", backend]


def _docling_arguments(backend: str, images: Path, out: Path, device: str) -> list[str]:
    # The standard pipeline: markdown to score, json kept for the layout adapters; pictures as placeholders.
    return [
        "convert",
        str(images),
        "--from",
        "image",
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


def _marker_arguments(backend: str, images: Path, out: Path, device: str) -> list[str]:
    # One worker: each worker loads every model and starts its own OCR server.
    return [str(images), "--output_dir", str(out), "--workers", "1"]


_MINERU = ToolContract(
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
        executable="docling",
        package="docling",
        backends=("default",),
        arguments=_docling_arguments,
        environment=lambda device: {},
        weight_variables=("DOCLING_ARTIFACTS_PATH",),
    ),
    "marker": ToolContract(
        executable="marker",
        package="marker-pdf",
        backends=("default",),
        arguments=_marker_arguments,
        environment=lambda device: {"TORCH_DEVICE": device},
        weight_variables=(),
    ),
}


def _conda() -> str:
    return os.environ.get("CONDA_EXE") or shutil.which("conda") or "conda"


def local_tool(config: ParsingConfig, name: str, *, config_path: Path) -> ToolSpec:
    """Return the configured tool `name`, refusing one that is unknown, not local or has no contract.

    Args:
        config: The validated parsing configuration.
        name: The tool's name.
        config_path: The file `config` was read from, named in a refusal.

    Returns:
        The tool's configuration.

    Raises:
        ParserRunError: The tool is not configured, its runner is not `local`, or this script has no
            command contract for it or its back-end.
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
    if tool.runner != "local":
        raise _diagnostic(
            "The tool is not run locally.",
            what=f"tool {name!r} has runner {tool.runner!r}; this script runs only local tools.",
            where=f"{config_path.resolve()} -> tools[{index}].runner",
            expected=f"runner: local with an env file, e.g. - {{name: {name}, backend: {tool.backend}, "
            f"runner: local, device: mps, conda_env_file: envs/{name}.yml}}",
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
            what=f"tool {name!r} has backend {tool.backend!r}; {contract.executable} is invoked here with "
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


def half_images(snapshot: Path, half: str, *, snapshot_config: SnapshotConfig) -> list[Path]:
    """The images a half's parsing file lists, in its order.

    Raises:
        ParserRunError: The parsing file is missing or unreadable, or a page's image is missing.
    """
    gt = snapshot / snapshot_config.parsing_dir / f"{snapshot_config.half_prefixes[half]}.json"
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
                f"{contract.executable} cannot find its weights (runs are offline, so nothing is downloaded).",
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
            what=f"reading {package}'s version in env {env!r} failed:\n{(done.stderr or done.stdout)[-2000:]}",
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
    run_name: str,
    output_root: Path,
    snapshot_config: SnapshotConfig,
) -> Path:
    """Run `tool` over one half's images and record the run.

    Args:
        tool: A local tool `local_tool` returned.
        snapshot: The snapshot directory.
        half: One of the snapshot's halves.
        run_name: The raw output directory's name under `raw_predictions/<tool>/`.
        output_root: The directory raw output is written under.
        snapshot_config: The snapshot configuration, naming the parsing directory and half prefixes.

    Returns:
        The raw output directory, holding the tool's output, `run.log` and `run.json`.

    Raises:
        ParserRunError: The output directory is inside the repository or already holds files, the env
            cannot be run, or the tool exited non-zero.
    """
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
    contract = CONTRACTS[tool.name]
    env = env_name(tool)
    conda = _conda()
    _check_weight_variables(conda, env, contract)
    version = _tool_version(conda, env, contract.package)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{tool.name}_{run_name}_") as staging:
        # The tool reads a directory: give it one holding exactly the half's images.
        inputs = Path(staging)
        for image in images:
            (inputs / image.name).symlink_to(image)
        command = [
            conda,
            "run",
            "--no-capture-output",
            "-n",
            env,
            contract.executable,
            *contract.arguments(tool.backend, inputs, out, tool.device),
        ]
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
        "pages": [image.name for image in images],
    }
    (out / RUN_RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", required=True, help="A tool name from tools in the parsing config")
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory")
    parser.add_argument("--half", required=True, choices=HALVES)
    parser.add_argument("--run-name", help="The raw output directory's name (the half when omitted)")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    parser.add_argument("--parsing-config", type=Path, default=PARSING_CONFIG_PATH)
    args = parser.parse_args(argv)
    try:
        config = load_parsing_config(args.parsing_config)
        tool = local_tool(config, args.tool, config_path=args.parsing_config)
        snapshot_config = load_snapshot_config(args.config)
        out = run_parser(
            tool,
            snapshot=args.snapshot,
            half=args.half,
            run_name=args.run_name or args.half,
            output_root=snapshot_config.output_root,
            snapshot_config=snapshot_config,
        )
    except (ParserRunError, ParsingConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    record = json.loads((out / RUN_RECORD).read_text())
    print(f"{record['tool']} on {record['page_count']} pages in {record['seconds']:.1f} s: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
