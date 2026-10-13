"""The pinned evaluator: its checkout is held at the configured commit, and its result is read exactly.

`ensure_checkout` runs against a fake remote made with `git init` under `tmp_path`, so nothing touches the
network. `parse_result` reads `tests/fixtures/omnidocbench_result.json`, a result file the pinned evaluator
wrote for its own bundled demo data, so a change in where the evaluator keeps its numbers breaks here.
`run_end2end` is checked with the evaluator's process replaced by one that writes that same file.
"""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from generators.parsing.config import (
    PARSING_CONFIG_PATH,
    EvaluatorPin,
    ParsingConfig,
    ParsingConfigError,
    load_parsing_config,
)
from scripts import omnidocbench_eval
from scripts.omnidocbench_eval import (
    EVALUATOR_ENV,
    EvaluatorError,
    ensure_checkout,
    parse_result,
    result_path,
    run_end2end,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.eval_export_tools import scratch_config

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "omnidocbench_result.json"


def _git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and return its trimmed output."""
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[Path, str, str]:
    """A fake evaluator remote with two commits; returns its path, the first and the second SHA."""
    repo = tmp_path / "remote"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "probe@example.invalid", cwd=repo)
    _git("config", "user.name", "Probe", cwd=repo)
    (repo / "pdf_validation.py").write_text("print('first')\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "first", cwd=repo)
    first = _git("rev-parse", "HEAD", cwd=repo)
    (repo / "pdf_validation.py").write_text("print('second')\n")
    _git("commit", "-q", "-am", "second", cwd=repo)
    second = _git("rev-parse", "HEAD", cwd=repo)
    return repo, first, second


def _config(repo: Path, commit: str) -> ParsingConfig:
    """The shipped parsing config, its evaluator pointed at `repo` and pinned to `commit`."""
    shipped = load_parsing_config(PARSING_CONFIG_PATH)
    pin = EvaluatorPin(
        repo=str(repo), commit=commit, conda_env_file="envs/omnidocbench.yml", formula_metrics=False
    )
    return dataclasses.replace(shipped, evaluator=pin)


def test_the_evaluator_env_is_the_one_the_configured_env_file_builds() -> None:
    config = load_parsing_config(PARSING_CONFIG_PATH)
    env_file = REPO_ROOT / config.evaluator.conda_env_file
    assert env_file.is_file()
    assert Path(config.evaluator.conda_env_file).stem == EVALUATOR_ENV
    spec = yaml.safe_load(env_file.read_text())
    assert "python=3.10" in spec["dependencies"]


def test_a_missing_checkout_is_cloned_at_the_pinned_commit_not_the_remote_head(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, first, second = remote
    root = tmp_path / "tools"
    checkout = ensure_checkout(_config(repo, first), root=root)
    assert checkout == root / "OmniDocBench"
    assert _git("rev-parse", "HEAD", cwd=checkout) == first
    assert (checkout / "pdf_validation.py").read_text() == "print('first')\n"


def test_an_existing_checkout_at_the_pin_is_kept_without_reaching_the_remote(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, first, _ = remote
    root = tmp_path / "tools"
    ensure_checkout(_config(repo, first), root=root)
    gone = _config(tmp_path / "no_such_remote", first)
    assert ensure_checkout(gone, root=root) == root / "OmniDocBench"


def test_a_checkout_at_another_commit_is_refused_four_part(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, first, second = remote
    root = tmp_path / "tools"
    ensure_checkout(_config(repo, second), root=root)
    with pytest.raises(EvaluatorError) as caught:
        ensure_checkout(_config(repo, first), root=root)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert first in message and second in message
    assert str(PARSING_CONFIG_PATH) in message and "evaluator.commit" in message


def test_a_checkout_with_a_modified_tracked_file_is_refused_four_part(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, first, _ = remote
    root = tmp_path / "tools"
    checkout = ensure_checkout(_config(repo, first), root=root)
    (checkout / "pdf_validation.py").write_text("print('tampered')\n")
    with pytest.raises(EvaluatorError) as caught:
        ensure_checkout(_config(repo, first), root=root)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert "pdf_validation.py" in message and str(checkout) in message


def test_a_directory_that_is_not_a_checkout_is_refused_four_part(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, first, _ = remote
    root = tmp_path / "tools"
    (root / "OmniDocBench").mkdir(parents=True)
    with pytest.raises(EvaluatorError) as caught:
        ensure_checkout(_config(repo, first), root=root)
    assert_diagnostic_error(caught.value)
    assert str(root / "OmniDocBench") in str(caught.value)


def test_a_clone_that_fails_is_refused_four_part_and_leaves_nothing(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    config = _config(tmp_path / "no_such_remote", "0123456789abcdef0123456789abcdef01234567")
    with pytest.raises(EvaluatorError) as caught:
        ensure_checkout(config, root=root)
    assert_diagnostic_error(caught.value)
    assert "evaluator.repo" in str(caught.value)
    assert not (root / "OmniDocBench").exists()


def test_a_pinned_commit_the_remote_lacks_is_refused_four_part_and_leaves_nothing(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
    repo, _, _ = remote
    root = tmp_path / "tools"
    with pytest.raises(EvaluatorError) as caught:
        ensure_checkout(_config(repo, "0123456789abcdef0123456789abcdef01234567"), root=root)
    assert_diagnostic_error(caught.value)
    assert "evaluator.commit" in str(caught.value)
    assert not (root / "OmniDocBench").exists()


def test_the_captured_result_parses_to_the_headline_scores() -> None:
    scores = parse_result(json.loads(FIXTURE.read_text()), source=FIXTURE)
    assert set(scores) == {
        "text_block_edit_dist",
        "table_teds",
        "table_teds_structure_only",
        "table_edit_dist",
        "reading_order_edit_dist",
    }
    assert scores["text_block_edit_dist"] == pytest.approx(0.3356214754013134)
    assert scores["table_teds"] == pytest.approx(0.7957058028722995)
    assert scores["table_teds_structure_only"] == pytest.approx(0.9115886967813946)
    assert scores["table_edit_dist"] == pytest.approx(0.1905124455615901)
    assert scores["reading_order_edit_dist"] == pytest.approx(0.22430037417144588)


def test_an_element_the_evaluator_scored_nothing_in_reads_as_nan() -> None:
    result = json.loads(FIXTURE.read_text())
    result["table"]["all"] = {
        "TEDS": {"all": "NaN"},
        "TEDS_structure_only": {"all": "NaN"},
        "Edit_dist": {"ALL_page_avg": "NaN"},
    }
    scores = parse_result(result, source=FIXTURE)
    assert all(scores[key] != scores[key] for key in ("table_teds", "table_edit_dist"))


@pytest.mark.parametrize(
    ("element", "metric"), [("text_block", "Edit_dist"), ("table", "TEDS"), ("reading_order", "Edit_dist")]
)
def test_a_result_missing_a_score_is_refused_four_part(element: str, metric: str) -> None:
    result = json.loads(FIXTURE.read_text())
    del result[element]["all"][metric]
    with pytest.raises(EvaluatorError) as caught:
        parse_result(result, source=FIXTURE)
    assert_diagnostic_error(caught.value)
    assert f"{element}.all.{metric}" in str(caught.value)
    assert str(FIXTURE) in str(caught.value)


def _fake_evaluator(calls: list[dict], *, returncode: int = 0, write: bool = True):
    """A stand-in for `subprocess.run` that records the call and writes the captured result."""

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        cwd = Path(str(kwargs["cwd"]))
        config = yaml.safe_load(Path(command[command.index("--config") + 1]).read_text())
        calls.append({"command": command, "cwd": cwd, "env": kwargs.get("env"), "config": config})
        if write:
            pred_dir = Path(config["end2end_eval"]["dataset"]["prediction"]["data_path"])
            target = result_path(pred_dir, work_dir=cwd)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(FIXTURE.read_text())
        return subprocess.CompletedProcess(
            command, returncode, stdout="evaluator says hello", stderr="boom"
        )

    return run


def test_run_end2end_writes_its_config_runs_the_pinned_script_and_returns_its_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(omnidocbench_eval.subprocess, "run", _fake_evaluator(calls))
    checkout, work, preds = tmp_path / "OmniDocBench", tmp_path / "work", tmp_path / "preds"
    gt = tmp_path / "probe.json"
    scores = run_end2end(gt, preds, checkout=checkout, work_dir=work)

    assert scores["table_teds"] == pytest.approx(0.7957058028722995)
    (call,) = calls
    assert call["command"][-4:] == [
        "python",
        str(checkout / "pdf_validation.py"),
        "--config",
        call["command"][-1],
    ]
    assert call["command"][-6:-4] == ["-n", EVALUATOR_ENV]
    assert call["cwd"] == work
    assert call["env"]["HF_HUB_OFFLINE"] == "1"
    task = call["config"]["end2end_eval"]
    assert task["metrics"] == {
        "text_block": {"metric": ["Edit_dist"]},
        "table": {"metric": ["TEDS", "Edit_dist"]},
        "reading_order": {"metric": ["Edit_dist"]},
    }
    assert task["dataset"] == {
        "dataset_name": "end2end_dataset",
        "ground_truth": {"data_path": str(gt)},
        "prediction": {"data_path": str(preds)},
        "match_method": "quick_match",
    }


def test_run_end2end_refuses_a_failed_evaluator_four_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(omnidocbench_eval.subprocess, "run", _fake_evaluator([], returncode=1, write=False))
    with pytest.raises(EvaluatorError) as caught:
        run_end2end(tmp_path / "probe.json", tmp_path / "preds", checkout=tmp_path, work_dir=tmp_path / "w")
    assert_diagnostic_error(caught.value)
    assert "boom" in str(caught.value)


def test_run_end2end_refuses_a_missing_result_four_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(omnidocbench_eval.subprocess, "run", _fake_evaluator([], write=False))
    with pytest.raises(EvaluatorError) as caught:
        run_end2end(tmp_path / "probe.json", tmp_path / "preds", checkout=tmp_path, work_dir=tmp_path / "w")
    assert_diagnostic_error(caught.value)
    assert str(result_path(tmp_path / "preds", work_dir=tmp_path / "w")) in str(caught.value)


def test_the_cli_checks_out_under_the_output_root_and_prints_the_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict = {}

    def checkout(config: ParsingConfig, *, root: Path) -> Path:
        seen["root"] = root
        return root / "OmniDocBench"

    def end2end(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
        seen["run"] = (gt_json, pred_dir, checkout, work_dir)
        return {"text_block_edit_dist": 0.0, "table_teds": float("nan")}

    monkeypatch.setattr(omnidocbench_eval, "ensure_checkout", checkout)
    monkeypatch.setattr(omnidocbench_eval, "run_end2end", end2end)
    config = scratch_config(tmp_path)
    tools = tmp_path / "out" / "tools"
    assert omnidocbench_eval.main(["checkout", "--config", str(config)]) == 0
    assert seen["root"] == tools
    assert f"Evaluator checkout: {tools / 'OmniDocBench'}" in capsys.readouterr().out

    args = [
        "end2end",
        "--config",
        str(config),
        "--gt",
        str(tmp_path / "p.json"),
        "--pred",
        str(tmp_path / "md"),
    ]
    assert omnidocbench_eval.main([*args, "--work-dir", str(tmp_path / "w")]) == 0
    assert seen["run"] == (tmp_path / "p.json", tmp_path / "md", tools / "OmniDocBench", tmp_path / "w")
    assert json.loads(capsys.readouterr().out) == {"text_block_edit_dist": 0.0, "table_teds": None}


def test_the_cli_prints_a_refusal_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(config: ParsingConfig, *, root: Path) -> Path:
        raise EvaluatorError("refused\n  What:     x\n  Where:    y\n  Expected: z\n  Recover:  w")

    monkeypatch.setattr(omnidocbench_eval, "ensure_checkout", refuse)
    assert omnidocbench_eval.main(["checkout", "--config", str(scratch_config(tmp_path))]) == 1
    assert_diagnostic_error(capsys.readouterr().err)


def test_the_cli_prints_a_parsing_config_refusal_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(path: Path) -> ParsingConfig:
        raise ParsingConfigError(
            "Invalid parsing config.\n  What:     x\n  Where:    y\n  Expected: z\n  Recover:  w"
        )

    monkeypatch.setattr("generators.parsing.config.load_parsing_config", broken)
    assert omnidocbench_eval.main(["checkout", "--config", str(scratch_config(tmp_path))]) == 1
    assert_diagnostic_error(capsys.readouterr().err)


def test_the_cli_refuses_a_work_dir_inside_the_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["end2end", "--config", str(scratch_config(tmp_path)), "--gt", "p.json", "--pred", "md"]
    inside = REPO_ROOT / "scores_scratch"
    assert omnidocbench_eval.main([*args, "--work-dir", str(inside)]) == 1
    err = capsys.readouterr().err
    assert_diagnostic_error(err)
    assert "--work-dir" in err and str(REPO_ROOT) in err
    assert not inside.exists()
