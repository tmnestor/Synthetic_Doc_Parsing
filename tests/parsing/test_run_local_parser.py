"""The local parser runner: it runs local tools from the env their file names, and builtin tools with its
own interpreter, on a set's PNGs or PDFs, and records the run.

The parsers themselves never run here: `conda` is replaced by a script that records its arguments and
writes one markdown file per input page, so each test checks what the runner asks for and what it keeps.
The builtin text-layer baseline does run, on PDFs compiled from invented text.
"""

import dataclasses
import importlib.metadata
import json
import sys
from pathlib import Path

import pytest
import yaml

from generators.parsing.config import PARSING_CONFIG_PATH, ParsingConfig, ToolSpec, load_parsing_config
from scripts import run_local_parser
from scripts.run_local_parser import (
    CONTRACTS,
    MINERU_CLI,
    RUN_LOG,
    RUN_RECORD,
    ParserRunError,
    env_name,
    half_images,
    local_tool,
    run_parser,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.eval_export_tools import scratch_config
from tests.parsing.parser_runs import (
    FAKE_VERSION,
    INVENTED_LINES,
    SNAPSHOT_CONFIG,
    STEMS,
    calls,
    fake_conda,
    invented_pdf,
    make_snapshot,
)


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


def _local_tools() -> list[ToolSpec]:
    return [tool for tool in _shipped().tools if tool.runner == "local"]


def test_each_shipped_env_file_names_the_env_the_runner_uses() -> None:
    names = {tool.name: env_name(tool) for tool in _local_tools()}
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
    for tool in _local_tools():
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
        "text_layer": (),
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
            input_kind="png",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
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
    # The refusal names every runner value, so the operator sees what the script does run.
    assert all(runner in message for runner in ("local", "builtin", "remote"))
    assert f"{path.resolve()} -> tools[0].runner" in message


def test_a_remote_tool_is_refused_from_the_command_line(
    tmp_path: Path, conda: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _scratch_parsing_config(tmp_path, runner="remote", conda_env_file=None)
    snapshot = make_snapshot(tmp_path)
    argv = ["--tool", "mineru_pipeline", "--snapshot", str(snapshot), "--half", "plain", "--input", "png"]
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
        "text_layer": "text_layer_baseline",
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
        input_kind="png",
        run_name="plain",
        output_root=tmp_path / "out",
        snapshot_config=SNAPSHOT_CONFIG,
        config_path=PARSING_CONFIG_PATH,
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
        assert argv[argv.index("--from") + 1] == "image"
    if name == "marker":
        assert tool_call["env"]["TORCH_DEVICE"] == tool.device
    assert tool_call["env"]["HF_HUB_OFFLINE"] == "1"

    record = json.loads((out / RUN_RECORD).read_text())
    keys = ("tool", "backend", "half", "input", "run_name", "device", "env")
    assert {key: record[key] for key in keys} == {
        "tool": name,
        "backend": tool.backend,
        "half": "plain",
        "input": "png",
        "run_name": "plain",
        "device": tool.device,
        "env": env_name(tool),
    }
    assert record["tool_version"] == FAKE_VERSION
    assert record["page_count"] == len(STEMS)
    assert record["pages"] == [f"{stem}.png" for stem in STEMS]
    assert record["input_files"] == [f"{stem}.png" for stem in STEMS]
    assert record["snapshot"] == str(snapshot.resolve())
    assert record["seconds"] >= 0 and record["started"] <= record["finished"]
    assert "parsed 2 pages" in (out / "run.log").read_text()


def test_a_run_name_other_than_the_half_names_the_output(tmp_path: Path, conda: Path) -> None:
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    out = run_parser(
        tool,
        snapshot=make_snapshot(tmp_path),
        half="plain",
        input_kind="png",
        run_name="probe_smoke",
        output_root=tmp_path / "out",
        snapshot_config=SNAPSHOT_CONFIG,
        config_path=PARSING_CONFIG_PATH,
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
            input_kind="png",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
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
            input_kind="png",
            run_name="plain",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
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
            input_kind="png",
            run_name="plain",
            output_root=REPO_ROOT / "build",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
        )
    assert_diagnostic_error(caught.value)


def _main(tmp_path: Path, snapshot: Path, tool: str, half: str, *extra: str) -> int:
    """Run the command line on `snapshot` with a scratch generation config (output under tmp_path/out)."""
    argv = ["--tool", tool, "--snapshot", str(snapshot), "--half", half, *extra]
    return run_local_parser.main([*argv, "--config", str(scratch_config(tmp_path))])


def test_a_run_without_an_input_kind_is_refused(
    tmp_path: Path, conda: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, make_snapshot(tmp_path), "docling", "plain") == 1
    message = capsys.readouterr().err
    assert_diagnostic_error(message)
    assert "--input" in message and "png" in message and "pdf" in message
    assert not conda.exists(), "a refused run must not reach conda"


def test_an_unknown_input_kind_is_refused(
    tmp_path: Path, conda: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, make_snapshot(tmp_path), "docling", "plain", "--input", "tiff") == 1
    message = capsys.readouterr().err
    assert_diagnostic_error(message)
    assert "'tiff'" in message
    assert not conda.exists()


def test_an_input_kind_the_tool_does_not_take_is_refused(tmp_path: Path, conda: Path) -> None:
    # Marker is scored on images only.
    tool = local_tool(_shipped(), "marker", config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path, "typeset_plain"),
            half="typeset_plain",
            input_kind="pdf",
            run_name="typeset_plain_pdf",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
        )
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert "marker" in message and "['png']" in message
    assert f"{PARSING_CONFIG_PATH} -> tools[3].inputs" in message
    assert not conda.exists()
    assert not (tmp_path / "out" / "raw_predictions" / "marker").exists()


@pytest.mark.parametrize("half", ["plain", "distractor", "labelled_varied", "degraded"])
def test_a_pdf_run_on_a_stage_one_half_is_refused(tmp_path: Path, conda: Path, half: str) -> None:
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=make_snapshot(tmp_path, half),
            half=half,
            input_kind="pdf",
            run_name=half,
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
        )
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert half in message and "--input png" in message and "typeset_plain" in message
    assert not conda.exists()


@pytest.mark.parametrize(
    ("half", "input_kind", "run_name", "suffix"),
    [
        ("typeset_plain", "pdf", "typeset_plain_pdf", ".pdf"),
        ("typeset_plain", "png", "typeset_plain_png", ".png"),
        ("typeset_distractor", "pdf", "typeset_distractor_pdf", ".pdf"),
        ("plain", "png", "plain", ".png"),
    ],
)
def test_the_output_directories_follow_the_set_and_input(
    tmp_path: Path, conda: Path, half: str, input_kind: str, run_name: str, suffix: str
) -> None:
    snapshot = make_snapshot(tmp_path, half)
    assert _main(tmp_path, snapshot, "docling", half, "--input", input_kind) == 0
    out = tmp_path / "out" / "raw_predictions" / "docling" / run_name
    record = json.loads((out / RUN_RECORD).read_text())
    assert (record["half"], record["input"], record["run_name"]) == (half, input_kind, run_name)
    # The tool is given the input kind's files; the record pairs each with its ground-truth PNG.
    assert record["input_files"] == [f"{stem}{suffix}" for stem in STEMS]
    assert record["pages"] == [f"{stem}.png" for stem in STEMS]
    argv = calls(conda)[-1]["argv"]
    assert argv[argv.index("--from") + 1] == {"pdf": "pdf", "png": "image"}[input_kind]
    assert sorted(path.name for path in out.rglob("*.md")) == sorted(f"{stem}.md" for stem in STEMS)


def test_the_mineru_arguments_are_the_same_for_either_input(tmp_path: Path, conda: Path) -> None:
    snapshot = make_snapshot(tmp_path, "typeset_plain")
    assert _main(tmp_path / "a", snapshot, "mineru_pipeline", "typeset_plain", "--input", "png") == 0
    assert _main(tmp_path / "b", snapshot, "mineru_pipeline", "typeset_plain", "--input", "pdf") == 0
    tool_calls = [call["argv"] for call in calls(conda) if "-b" in call["argv"]]

    def masked(argv: list[str]) -> list[str]:
        # The staging and output directories differ between runs; every other argument must not.
        places = {argv.index("-p") + 1, argv.index("-o") + 1}
        return ["<dir>" if index in places else arg for index, arg in enumerate(argv)]

    png_call, pdf_call = tool_calls
    assert masked(png_call) == masked(pdf_call)
    # MinerU's own default method (auto) decides between the text layer and OCR.
    assert pdf_call[pdf_call.index("-b") + 1] == "pipeline" and "-m" not in pdf_call


def test_a_typeset_page_whose_pdf_is_missing_is_refused(tmp_path: Path, conda: Path) -> None:
    snapshot = make_snapshot(tmp_path, "typeset_plain")
    next(snapshot.rglob(f"{STEMS[1]}.pdf")).unlink()
    tool = local_tool(_shipped(), "docling", config_path=PARSING_CONFIG_PATH)
    with pytest.raises(ParserRunError) as caught:
        run_parser(
            tool,
            snapshot=snapshot,
            half="typeset_plain",
            input_kind="pdf",
            run_name="typeset_plain_pdf",
            output_root=tmp_path / "out",
            snapshot_config=SNAPSHOT_CONFIG,
            config_path=PARSING_CONFIG_PATH,
        )
    assert_diagnostic_error(caught.value)
    assert f"{STEMS[1]}.pdf" in str(caught.value)
    assert not conda.exists()


def test_the_text_layer_baseline_runs_through_the_runner_without_conda(tmp_path: Path, conda: Path) -> None:
    pdfs = {stem: invented_pdf(tmp_path / "typst" / stem) for stem in STEMS}
    snapshot = make_snapshot(tmp_path, "typeset_plain", pdfs=pdfs)
    assert _main(tmp_path, snapshot, "text_layer", "typeset_plain", "--input", "pdf") == 0
    out = tmp_path / "out" / "raw_predictions" / "text_layer" / "typeset_plain_pdf"
    for stem in STEMS:
        assert (out / f"{stem}.md").read_text() == "\n\n".join(INVENTED_LINES) + "\n"
    record = json.loads((out / RUN_RECORD).read_text())
    assert {key: record[key] for key in ("tool", "backend", "half", "input", "device")} == {
        "tool": "text_layer",
        "backend": "default",
        "half": "typeset_plain",
        "input": "pdf",
        "device": "cpu",
    }
    # It runs in the generator's own env: no conda call, and the pypdfium2 version recorded.
    assert record["env"] == Path(sys.prefix).name
    assert record["tool_version"] == importlib.metadata.version("pypdfium2")
    assert record["pages"] == [f"{stem}.png" for stem in STEMS]
    assert record["command"][0] == sys.executable
    assert not conda.exists()
    assert "2 pages" in (out / RUN_LOG).read_text()


def test_the_text_layer_baseline_is_refused_on_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = make_snapshot(tmp_path, "typeset_plain")
    assert _main(tmp_path, snapshot, "text_layer", "typeset_plain", "--input", "png") == 1
    message = capsys.readouterr().err
    assert_diagnostic_error(message)
    assert "text_layer" in message and "['pdf']" in message
