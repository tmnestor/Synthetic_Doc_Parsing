"""The local parser runner: it runs only local tools, from the env their file names, and records the run.

The parsers themselves never run here: `conda` is replaced by a script that records its arguments and
writes one markdown file per input image, so each test checks what the runner asks for and what it keeps.
"""

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, load_parsing_config
from scripts import run_local_parser
from scripts.run_local_parser import (
    CONTRACTS,
    MINERU_CLI,
    RUN_RECORD,
    ParserRunError,
    env_name,
    half_images,
    local_tool,
    run_parser,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.eval_export_tools import scratch_config
from tests.parsing.parser_runs import FAKE_VERSION, SNAPSHOT_CONFIG, STEMS, calls, fake_conda, make_snapshot


def _shipped() -> ParsingConfig:
    return load_parsing_config(PARSING_CONFIG_PATH)


def _scratch_parsing_config(tmp_path: Path, **changes: object) -> Path:
    """A copy of the shipped parsing config with the first tool's keys changed."""
    data = yaml.safe_load(PARSING_CONFIG_PATH.read_text())
    data["tools"][0].update(changes)
    path = tmp_path / "parsing_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.fixture
def conda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put the fake `conda` in place; return the log of its calls."""
    script, log = fake_conda(tmp_path)
    monkeypatch.setenv("CONDA_EXE", str(script))
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    # Every weight variable a contract names is set, as `conda env config vars set` leaves it in the env.
    for variable in {name for contract in CONTRACTS.values() for name in contract.weight_variables}:
        monkeypatch.setenv(variable, str(tmp_path / "weights" / variable.lower()))
    return log


def test_every_configured_tool_has_a_command_contract() -> None:
    assert sorted(tool.name for tool in _shipped().tools) == sorted(CONTRACTS)


def test_each_shipped_env_file_names_the_env_the_runner_uses() -> None:
    names = {tool.name: env_name(tool) for tool in _shipped().tools}
    assert names == {
        "mineru_pipeline": "docparse-mineru",
        "mineru_vlm": "docparse-mineru",
        "docling": "docparse-docling",
        "marker": "marker",
    }


def _strings(node: object) -> list[str]:
    """Every string value in a parsed YAML document."""
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    if isinstance(node, list):
        return [text for value in node for text in _strings(value)]
    return [node] if isinstance(node, str) else []


def test_no_env_file_holds_a_machine_path() -> None:
    for tool in _shipped().tools:
        assert tool.conda_env_file is not None
        spec = yaml.safe_load((REPO_ROOT / tool.conda_env_file).read_text())
        assert "variables" not in spec, tool.conda_env_file
        paths = [text for text in _strings(spec) if text.startswith(("/", "~"))]
        assert paths == [], (tool.conda_env_file, paths)


def test_the_tools_that_read_weights_from_a_variable_name_it() -> None:
    assert {name: CONTRACTS[name].weight_variables for name in CONTRACTS} == {
        "mineru_pipeline": ("MINERU_TOOLS_CONFIG_JSON",),
        "mineru_vlm": ("MINERU_TOOLS_CONFIG_JSON",),
        "docling": ("DOCLING_ARTIFACTS_PATH",),
        "marker": (),
    }


@pytest.mark.parametrize(
    ("name", "variable"),
    [("docling", "DOCLING_ARTIFACTS_PATH"), ("mineru_vlm", "MINERU_TOOLS_CONFIG_JSON")],
)
def test_an_env_without_its_weight_variable_is_refused(
    tmp_path: Path, conda: Path, monkeypatch: pytest.MonkeyPatch, name: str, variable: str
) -> None:
    monkeypatch.delenv(variable)
    tool = local_tool(_shipped(), name, config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path),
            half="plain",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
        )
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    # The tool is named by its own command, not by a wrapper the runner invokes it through.
    assert f"{CONTRACTS[name].command} cannot find its weights" in message
    assert "python cannot" not in message
    assert f"export {variable}=" in message
    assert f"conda env config vars set -n {env_name(tool)} {variable}=" in message
    assert [call["argv"][-2:] for call in calls(conda)] == [["printenv", variable]]
    assert not (tmp_path / "out" / "raw_predictions" / name / "plain").exists()


def test_a_remote_tool_is_refused(tmp_path: Path) -> None:
    path = _scratch_parsing_config(tmp_path, runner="remote", conda_env_file=None)
    config = load_parsing_config(path)
    with pytest.raises(ParserRunError) as caught:
        local_tool(config, "mineru_pipeline", config_path=path)
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert "remote" in message and "mineru_pipeline" in message
    assert f"{path.resolve()} -> tools[0].runner" in message


def test_a_remote_tool_is_refused_from_the_command_line(
    tmp_path: Path, conda: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _scratch_parsing_config(tmp_path, runner="remote", conda_env_file=None)
    snapshot = make_snapshot(tmp_path)
    argv = ["--tool", "mineru_pipeline", "--snapshot", str(snapshot), "--half", "plain"]
    argv += ["--config", str(scratch_config(tmp_path))]
    assert run_local_parser.main([*argv, "--parsing-config", str(path)]) == 1
    assert_diagnostic_error(capsys.readouterr().err)
    assert not conda.exists(), "a refused tool must not reach conda"


def test_messages_name_each_tool_by_its_own_command() -> None:
    assert {name: CONTRACTS[name].command for name in CONTRACTS} == {
        "mineru_pipeline": "mineru",
        "mineru_vlm": "mineru",
        "docling": "docling",
        "marker": "marker",
    }


def test_a_backend_the_contract_does_not_know_is_refused_naming_the_tool(tmp_path: Path) -> None:
    path = _scratch_parsing_config(tmp_path, backend="no-such-backend")
    with pytest.raises(ParserRunError) as caught:
        local_tool(load_parsing_config(path), "mineru_pipeline", config_path=path)
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert "mineru is invoked here with one of" in message and "python" not in message
    assert f"{path.resolve()} -> tools[0].backend" in message


def test_an_unknown_tool_is_refused() -> None:
    with pytest.raises(ParserRunError) as caught:
        local_tool(_shipped(), "no_such_tool", config_path=PARSING_CONFIG_PATH)
    assert_diagnostic_error(caught.value)
    assert "no_such_tool" in str(caught.value) and "docling" in str(caught.value)


def test_an_env_file_without_a_name_is_refused(tmp_path: Path) -> None:
    env_file = tmp_path / "nameless.yml"
    env_file.write_text("channels: [conda-forge]\ndependencies: [python=3.12]\n")
    tool = dataclasses.replace(_shipped().tools[2], conda_env_file=str(env_file))
    with pytest.raises(ParserRunError) as caught:
        env_name(tool)
    assert_diagnostic_error(caught.value)
    assert str(env_file) in str(caught.value) and "name" in str(caught.value)


def test_the_half_images_are_read_from_the_parsing_file(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    images = half_images(snapshot, "plain", snapshot_config=SNAPSHOT_CONFIG)
    assert [image.stem for image in images] == list(STEMS)
    assert all(image.is_file() for image in images)


def test_a_snapshot_without_the_half_parsing_file_is_refused(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    with pytest.raises(ParserRunError) as caught:
        half_images(snapshot, "degraded", snapshot_config=SNAPSHOT_CONFIG)
    assert_diagnostic_error(caught.value)
    assert "degraded.json" in str(caught.value)


def test_a_page_whose_image_is_missing_is_refused(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    next(snapshot.rglob(f"{STEMS[1]}.png")).unlink()
    with pytest.raises(ParserRunError) as caught:
        half_images(snapshot, "plain", snapshot_config=SNAPSHOT_CONFIG)
    assert_diagnostic_error(caught.value)
    assert STEMS[1] in str(caught.value)


@pytest.mark.parametrize("name", ["mineru_pipeline", "mineru_vlm", "docling", "marker"])
def test_a_run_goes_through_the_tool_env_and_writes_its_record(
    tmp_path: Path, conda: Path, name: str
) -> None:
    tool = local_tool(_shipped(), name, config_path=PARSING_CONFIG_PATH)
    snapshot = make_snapshot(tmp_path)
    out = run_parser(
        tool,
        snapshot=snapshot,
        half="plain",
        run_name="plain",
        output_root=tmp_path / "out",
        snapshot_config=SNAPSHOT_CONFIG,
    )
    assert out == tmp_path / "out" / "raw_predictions" / name / "plain"
    assert sorted(path.name for path in out.rglob("*.md")) == sorted(f"{stem}.md" for stem in STEMS)

    contract = CONTRACTS[name]
    *printenv_calls, version_call, tool_call = calls(conda)
    assert [call["argv"][-2:] for call in printenv_calls] == [
        ["printenv", v] for v in contract.weight_variables
    ]
    assert version_call["argv"][:3] == ["run", "-n", env_name(tool)]
    assert contract.package in version_call["argv"][-1]
    argv = tool_call["argv"]
    assert argv[:4] == ["run", "--no-capture-output", "-n", env_name(tool)]
    assert argv[4] == contract.executable
    assert str(out) in argv
    if name.startswith("mineru"):
        # `mineru` runs through the wrapper that polls its local API on fresh connections.
        assert argv[5:7] == ["-I", str(MINERU_CLI)] and MINERU_CLI.is_file()
        assert argv[argv.index("-b") + 1] == tool.backend
        assert tool_call["env"]["MINERU_DEVICE_MODE"] == tool.device
    if name == "docling":
        assert argv[argv.index("--device") + 1] == tool.device
    if name == "marker":
        assert tool_call["env"]["TORCH_DEVICE"] == tool.device
    assert tool_call["env"]["HF_HUB_OFFLINE"] == "1"

    record = json.loads((out / RUN_RECORD).read_text())
    assert {key: record[key] for key in ("tool", "backend", "half", "run_name", "device", "env")} == {
        "tool": name,
        "backend": tool.backend,
        "half": "plain",
        "run_name": "plain",
        "device": tool.device,
        "env": env_name(tool),
    }
    assert record["tool_version"] == FAKE_VERSION
    assert record["page_count"] == len(STEMS)
    assert record["pages"] == [f"{stem}.png" for stem in STEMS]
    assert record["snapshot"] == str(snapshot.resolve())
    assert record["seconds"] >= 0 and record["started"] <= record["finished"]
    assert "parsed 2 pages" in (out / "run.log").read_text()


def test_a_run_name_other_than_the_half_names_the_output(tmp_path: Path, conda: Path) -> None:
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    out = run_parser(
        tool,
        snapshot=make_snapshot(tmp_path),
        half="plain",
        run_name="probe_smoke",
        output_root=tmp_path / "out",
        snapshot_config=SNAPSHOT_CONFIG,
    )
    assert out.name == "probe_smoke"
    assert json.loads((out / RUN_RECORD).read_text())["half"] == "plain"


def test_an_output_directory_that_already_holds_files_is_refused(tmp_path: Path, conda: Path) -> None:
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    stale = tmp_path / "out" / "raw_predictions" / "docling" / "plain"
    stale.mkdir(parents=True)
    (stale / "old.md").write_text("stale\n")
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path),
            half="plain",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
        )
    assert_diagnostic_error(caught.value)
    assert str(stale) in str(caught.value)
    assert not conda.exists()


def test_a_failed_run_is_refused_and_leaves_no_record(
    tmp_path: Path, conda: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CONDA_EXIT", "3")
    tool = local_tool(_shipped(), "marker", config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path),
            half="plain",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
        )
    assert_diagnostic_error(caught.value)
    assert "exited 3" in str(caught.value)
    assert not (tmp_path / "out" / "raw_predictions" / "marker" / "plain" / RUN_RECORD).exists()


def test_the_repository_is_never_an_output_root(tmp_path: Path, conda: Path) -> None:
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path),
            half="plain",
            run_name="plain",
            output_root=REPO_ROOT / "build",
            snapshot_config=SNAPSHOT_CONFIG,
        )
    assert_diagnostic_error(caught.value)
