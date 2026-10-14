#!/usr/bin/env python3
"""Run the pinned OmniDocBench evaluator on a set of ground-truth pages and their markdown predictions.

    python scripts/omnidocbench_eval.py checkout [--config config/generation_config.yml]
    python scripts/omnidocbench_eval.py end2end --gt probe.json --pred predictions/ --work-dir scores/

The evaluator is pinned, not vendored (Decision 10): `config/parsing_config.yml` names its repository and
commit, `ensure_checkout` clones it once into `<eval_set.output_root>/tools/OmniDocBench` at that commit,
and refuses a checkout at any other. It runs from its own conda env (`envs/omnidocbench.yml`, with the
checkout installed by `pip install -e`), so none of its packages enter the generator's env.

`run_end2end` writes an end-to-end config into a work directory (text edit distance, table TEDS and edit
distance, reading-order edit distance; formula metrics off; quick matching) and runs the checkout's
`pdf_validation.py` with that directory as its working directory. The evaluator writes every result under
`./result/`, named `<prediction directory name>_quick_match_*`, so each run keeps its own files and the
checkout is never written to. The scores are read from `<name>_quick_match_metric_result.json`: per
element, `all` holds the headline numbers. Hugging Face access is switched off for the run, so the
evaluator cannot fetch anything; the metrics used are pure computation and load no model.

The evaluator pairs a ground-truth page `<stem>.png` with the prediction `<stem>.md` (Decision 13).
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import (  # noqa: E402
    PARSING_CONFIG_PATH,
    ParsingConfig,
    ParsingConfigError,
)

# The conda env `envs/omnidocbench.yml` builds (its file stem), and the checkout's directory name.
EVALUATOR_ENV = "omnidocbench"
CHECKOUT_NAME = "OmniDocBench"
# The evaluator's own names: its task, dataset and matcher, and the directory it writes results to.
_TASK = "end2end_eval"
_DATASET = "end2end_dataset"
_MATCH_METHOD = "quick_match"
_RESULT_DIR = "result"
_METRICS = {
    "text_block": {"metric": ["Edit_dist"]},
    "table": {"metric": ["TEDS", "Edit_dist"]},
    "reading_order": {"metric": ["Edit_dist"]},
}
# Each headline score: its name here, then its path inside the result file (element, `all`, metric, key).
_SCORES = {
    "text_block_edit_dist": ("text_block", "Edit_dist", "ALL_page_avg"),
    "table_teds": ("table", "TEDS", "all"),
    "table_teds_structure_only": ("table", "TEDS_structure_only", "all"),
    "table_edit_dist": ("table", "Edit_dist", "ALL_page_avg"),
    "reading_order_edit_dist": ("reading_order", "Edit_dist", "ALL_page_avg"),
}
# The environment switches that keep the evaluator from fetching anything from the Hugging Face hub.
_OFFLINE = {"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
# How much of the evaluator's output a refusal quotes.
_TAIL_LINES = 30


class EvaluatorError(RuntimeError):
    """The evaluator checkout is wrong, or a run of it failed or wrote no readable result."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> EvaluatorError:
    """Build a four-part refusal, each part on a labelled line."""
    return EvaluatorError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run git in `cwd`, capturing its output; never raises on a non-zero exit."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _tail(text: str) -> str:
    """The last lines of a process's output, indented under a diagnostic's What line."""
    lines = text.strip().splitlines()[-_TAIL_LINES:]
    return "\n".join("      " + line for line in lines) or "      (no output)"


def _head_commit(checkout: Path) -> str | None:
    """The commit `checkout` has out, or None when it is not the top of a git checkout."""
    if not (checkout / ".git").exists():
        return None
    top = _git("rev-parse", "--show-toplevel", cwd=checkout)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != checkout.resolve():
        return None
    head = _git("rev-parse", "HEAD", cwd=checkout)
    return head.stdout.strip() if head.returncode == 0 else None


def ensure_checkout(config: ParsingConfig, *, root: Path) -> Path:
    """Return the evaluator checkout under `root`, cloning it at the pinned commit when it is absent.

    Args:
        config: The validated parsing configuration, naming the repository and commit.
        root: The directory that holds the checkout (`<eval_set.output_root>/tools`).

    Returns:
        `root / "OmniDocBench"`, checked out at `config.evaluator.commit`.

    Raises:
        EvaluatorError: The directory exists but is not a checkout, or is at another commit; or the clone
            failed, or the pinned commit is not in the repository. A failed clone leaves nothing behind.
    """
    pin = config.evaluator
    checkout = root / CHECKOUT_NAME
    if checkout.exists():
        head = _head_commit(checkout)
        if head is None:
            raise _diagnostic(
                "The evaluator directory is not a git checkout.",
                what=f"{checkout} exists but is not the top of a git checkout, so its commit cannot be "
                "checked against the pin.",
                where=f"{checkout}; {PARSING_CONFIG_PATH} -> evaluator.repo",
                expected=f"a clone of {pin.repo} at commit {pin.commit}.",
                recover=f"move {checkout} aside, then run `python scripts/omnidocbench_eval.py checkout` "
                "to clone it afresh.",
            )
        if head != pin.commit:
            raise _diagnostic(
                "The evaluator checkout is not at the pinned commit.",
                what=f"{checkout} is at commit {head}, but evaluator.commit pins {pin.commit}; scores from "
                "another commit are not comparable.",
                where=f"{checkout}; {PARSING_CONFIG_PATH} -> evaluator.commit",
                expected=f"the checkout at the pinned commit, e.g. `git -C {checkout} rev-parse HEAD` "
                f"printing {pin.commit}.",
                recover=f"run `git -C {checkout} checkout --detach {pin.commit}`, or change "
                "evaluator.commit deliberately and re-run the perfect-score test.",
            )
        dirty = _git("status", "--porcelain", "--untracked-files=no", cwd=checkout)
        if dirty.returncode != 0 or dirty.stdout.strip():
            raise _diagnostic(
                "The evaluator checkout has local modifications.",
                what=f"{checkout} is at the pinned commit {pin.commit} but tracked files differ from it:\n"
                f"{_tail(dirty.stdout or dirty.stderr)}\n"
                "scores from a modified evaluator are not comparable.",
                where=f"{checkout}; {PARSING_CONFIG_PATH} -> evaluator.commit",
                expected=f"a clean checkout, e.g. `git -C {checkout} status --porcelain "
                "--untracked-files=no` printing nothing.",
                recover=f"run `git -C {checkout} checkout -- .` to discard the changes, or move {checkout} "
                "aside and run `python scripts/omnidocbench_eval.py checkout` to clone it afresh.",
            )
        return checkout

    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{CHECKOUT_NAME}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    clone = _git("clone", "--quiet", "--no-checkout", pin.repo, str(staging), cwd=root)
    if clone.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise _diagnostic(
            "The evaluator repository could not be cloned.",
            what=f"`git clone {pin.repo}` failed:\n{_tail(clone.stderr)}",
            where=f"{PARSING_CONFIG_PATH} -> evaluator.repo",
            expected="a reachable git URL, e.g. repo: https://github.com/opendatalab/OmniDocBench.git",
            recover="check the network and evaluator.repo, then run the command again.",
        )
    switch = _git("checkout", "--quiet", "--detach", pin.commit, cwd=staging)
    if switch.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        raise _diagnostic(
            "The pinned evaluator commit is not in its repository.",
            what=f"`git checkout {pin.commit}` failed in a fresh clone of {pin.repo}:\n"
            f"{_tail(switch.stderr)}",
            where=f"{PARSING_CONFIG_PATH} -> evaluator.commit",
            expected="a full 40-character SHA the repository holds, e.g. one `git ls-remote <repo>` lists.",
            recover="correct evaluator.commit to a commit of evaluator.repo.",
        )
    staging.rename(checkout)
    return checkout


def end2end_config(gt_json: Path, pred_dir: Path) -> dict:
    """The evaluator's end-to-end config for one set of pages and predictions.

    Args:
        gt_json: The OmniDocBench ground truth, a JSON list of pages.
        pred_dir: The directory of `<image stem>.md` predictions.

    Returns:
        The config, as the evaluator's YAML structure.
    """
    return {
        _TASK: {
            "metrics": _METRICS,
            "dataset": {
                "dataset_name": _DATASET,
                "ground_truth": {"data_path": str(gt_json)},
                "prediction": {"data_path": str(pred_dir)},
                "match_method": _MATCH_METHOD,
            },
        }
    }


def result_path(pred_dir: Path, *, work_dir: Path) -> Path:
    """Where the evaluator, run in `work_dir`, writes the metric result for `pred_dir`."""
    return work_dir / _RESULT_DIR / f"{pred_dir.name}_{_MATCH_METHOD}_metric_result.json"


def _score(value: object) -> float:
    """A score as a float; the evaluator writes 'NaN' for an element it scored nothing in."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TypeError(f"{value!r} is not a number")
    return float(value)


def parse_result(result: dict, *, source: Path) -> dict[str, float]:
    """Read the headline scores from the evaluator's metric result.

    Args:
        result: The parsed `<name>_quick_match_metric_result.json`.
        source: The file it was read from, named in a refusal.

    Returns:
        `text_block_edit_dist`, `table_teds`, `table_teds_structure_only`, `table_edit_dist` and
        `reading_order_edit_dist`; an element the evaluator scored nothing in reads as NaN.

    Raises:
        EvaluatorError: A score is missing or not a number.
    """
    scores: dict[str, float] = {}
    for name, (element, metric, key) in _SCORES.items():
        try:
            scores[name] = _score(result[element]["all"][metric][key])
        except (KeyError, TypeError, ValueError) as err:
            raise _diagnostic(
                "The evaluator's result does not hold a score where the pinned commit writes it.",
                what=f"{element}.all.{metric}.{key} is missing or not a number ({err!r}).",
                where=f"{source}",
                expected=f'a number at {element}.all.{metric}.{key}, e.g. {{"{element}": {{"all": '
                f'{{"{metric}": {{"{key}": 0.0}}}}}}}}.',
                recover="check the evaluator checkout is at evaluator.commit, and that the run scored "
                f"{element} (the evaluator's log shows each element it scored).",
            ) from err
    return scores


def run_end2end(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
    """Score `pred_dir` against `gt_json` with the pinned evaluator, in its own env.

    Args:
        gt_json: The OmniDocBench ground truth, a JSON list of pages.
        pred_dir: The directory of `<image stem>.md` predictions.
        checkout: The evaluator checkout `ensure_checkout` returned.
        work_dir: Where the config and the evaluator's `result/` directory are written.

    Returns:
        The headline scores `parse_result` reads.

    Raises:
        EvaluatorError: The evaluator exited non-zero, or wrote no readable result.
    """
    # The evaluator runs in `work_dir`, so every path it is handed is absolute.
    gt_json, pred_dir, checkout, work_dir = (p.resolve() for p in (gt_json, pred_dir, checkout, work_dir))
    work_dir.mkdir(parents=True, exist_ok=True)
    config_file = work_dir / "end2end.yaml"
    config_file.write_text(yaml.safe_dump(end2end_config(gt_json, pred_dir), sort_keys=False))
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda") or "conda"
    command = [
        conda,
        "run",
        "-n",
        EVALUATOR_ENV,
        "python",
        str(checkout / "pdf_validation.py"),
        "--config",
        str(config_file),
    ]
    done = subprocess.run(
        command, cwd=work_dir, env={**os.environ, **_OFFLINE}, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise _diagnostic(
            "The evaluator failed.",
            what=f"`{' '.join(command)}` exited {done.returncode}:\n{_tail(done.stderr or done.stdout)}",
            where=f"{config_file} (run in {work_dir})",
            expected=f"exit 0 and a result at {result_path(pred_dir, work_dir=work_dir)}.",
            recover=f"build the env (conda env create -n {EVALUATOR_ENV} -f envs/omnidocbench.yml, then "
            f"conda run -n {EVALUATOR_ENV} pip install -e {checkout}) and run the command again.",
        )
    source = result_path(pred_dir, work_dir=work_dir)
    if not source.is_file():
        raise _diagnostic(
            "The evaluator wrote no result.",
            what=f"the run exited 0 but {source} does not exist:\n{_tail(done.stdout)}",
            where=f"{config_file} (run in {work_dir})",
            expected=f"the evaluator's metric result at {source}.",
            recover="check the checkout is at evaluator.commit; a different commit may write elsewhere.",
        )
    return parse_result(json.loads(source.read_text()), source=source)


def _refuse_work_dir_in_repository(work_dir: Path) -> None:
    """Refuse a work directory inside the repository: generated output is never written there."""
    resolved = work_dir.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise _diagnostic(
            "The work directory lies inside the repository.",
            what=f"--work-dir {resolved} is under {REPO_ROOT}, and the evaluator would write its config "
            "and results there.",
            where="the --work-dir argument",
            expected="a directory outside the repository, e.g. --work-dir ../evaluation_data/scores/run1",
            recover="pass a --work-dir under eval_set.output_root (../evaluation_data) or another "
            "directory outside the repository.",
        )


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: `checkout` or `end2end`."""
    from generators.parsing.config import load_parsing_config
    from generators.snapshot.config import load_snapshot_config

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    checkout_cmd = commands.add_parser("checkout", help="Clone or verify the pinned evaluator checkout.")
    end2end_cmd = commands.add_parser("end2end", help="Score markdown predictions against ground truth.")
    for command in (checkout_cmd, end2end_cmd):
        command.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    end2end_cmd.add_argument("--gt", type=Path, required=True, help="OmniDocBench ground-truth JSON")
    end2end_cmd.add_argument("--pred", type=Path, required=True, help="Directory of <image stem>.md")
    end2end_cmd.add_argument("--work-dir", type=Path, required=True, help="Where the result is written")
    args = parser.parse_args(argv)

    try:
        if args.command == "end2end":
            _refuse_work_dir_in_repository(args.work_dir)
        root = load_snapshot_config(args.config).output_root / "tools"
        checkout = ensure_checkout(load_parsing_config(PARSING_CONFIG_PATH), root=root)
        if args.command == "checkout":
            print(f"Evaluator checkout: {checkout}")
            return 0
        scores = run_end2end(
            args.gt.resolve(), args.pred.resolve(), checkout=checkout, work_dir=args.work_dir
        )
    except (EvaluatorError, ParsingConfigError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps({key: None if math.isnan(value) else value for key, value in scores.items()}, indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
