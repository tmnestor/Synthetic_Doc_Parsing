"""`generate --distractors --label-variation` writes the labelled varied renders and their sidecars.

Stage 6, Task 3. The renders land in `<output_dir>/<labelled_varied_output_subdir>/<type>/` under the
filenames the plain and distractor renders use, each type directory with its `label_styles.jsonl`: one row
per image, saying what the page printed for each varied label and what was refused on the way. The flag
is layered on `--distractors` and refused without it. A small run (three cases per type) runs with the
fast suite; the whole corpus is the slow test.
"""

import collections
import itertools
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from generators import pipeline
from generators.distractors.config import load_distractor_config
from generators.distractors.providers import draw_states
from generators.labels.keys import LABEL_STYLES
from generators.labels.sidecar import REFUSAL_KEYS, ROW_KEYS, SIDECAR_NAME, read_sidecar, summarise
from generators.loader import load_generation_config
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.snapshot_tools import (
    DISTRACTOR_SNAPSHOT_PATH,
    LABELLED_VARIED_SNAPSHOT_PATH,
    digest,
    load_snapshot,
)

SHIPPED = REPO_ROOT / "config" / "generation_config.yml"
CASES = 3
TYPES = {"bank_statements": "bank_statement", "receipts": "receipt", "invoices": "invoice"}


def _config(root: Path, cases: int | None) -> Path:
    """The shipped config over the first `cases` entries of each type (all when None), writing in `root`."""
    data = yaml.safe_load(SHIPPED.read_text())
    data["output_dir"] = str(root / "output")
    data["derived_dir"] = str(root / "derived")
    for stem, doc_cfg in data["document_types"].items():
        gt = REPO_ROOT / doc_cfg["ground_truth"]
        if cases is not None:
            entries = yaml.safe_load(gt.read_text())
            gt = root / f"{stem}.yml"
            gt.write_text(yaml.safe_dump(dict(itertools.islice(entries.items(), cases)), sort_keys=False))
        doc_cfg["ground_truth"] = str(gt)
        doc_cfg["layouts"] = str(REPO_ROOT / doc_cfg["layouts"])
    path = root / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _run(root: Path, cases: int | None, *extra: str) -> tuple[str, dict[str, dict]]:
    """Run the labelled varied generate; return its output and, per image file, the labels drawn."""
    config = _config(root, cases)
    drawn: dict[str, dict] = {}
    real = pipeline.render_labelled_varied

    def spy(renderer, entry, layout, **kwargs):  # type: ignore[no-untyped-def]
        image, outcome = real(renderer, entry, layout, **kwargs)
        drawn[f"{entry['case_id']}_{entry['layout']}.png"] = dict(kwargs["geometry_out"]["labels"])
        return image, outcome

    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(REPO_ROOT)
        mp.setattr(pipeline, "render_labelled_varied", spy)
        result = CliRunner().invoke(
            app, ["generate", "--config", str(config), "--distractors", "--label-variation", *extra]
        )
    assert result.exit_code == 0, result.output
    return result.output, drawn


def _summary(output: str, stem: str) -> tuple[collections.Counter, collections.Counter]:
    """Read one type's printed style and refusal counts back."""
    styles: collections.Counter = collections.Counter()
    rules: collections.Counter = collections.Counter()
    for line in output.splitlines():
        found = re.fullmatch(rf"  {stem}: ([A-Z_]+): (.*)", line)
        if found:
            for part in found.group(2).split(", "):
                style, count = part.split(" ")
                styles[(found.group(1), style)] = int(count)
        found = re.fullmatch(rf"  {stem}: refusals: (.*) \((\d+) in all\)", line)
        if found:
            if found.group(1) != "none":
                for part in found.group(1).split(", "):
                    rule, count = part.split(" ")
                    rules[rule] = int(count)
            assert sum(rules.values()) == int(found.group(2))
    return +styles, rules


def _type_dirs(root: Path) -> Iterator[tuple[str, Path]]:
    for stem in TYPES:
        yield stem, root / "output" / "labelled_varied" / stem


def _check_run(root: Path, output: str, drawn: dict[str, dict], per_type: int) -> None:
    """Every assertion a labelled varied run must meet, whatever its size."""
    varied_pins = load_snapshot(LABELLED_VARIED_SNAPSHOT_PATH)
    distractor_pins = load_snapshot(DISTRACTOR_SNAPSHOT_PATH)
    for stem, directory in _type_dirs(root):
        images = sorted(path.name for path in directory.glob("*.png"))
        assert len(images) == per_type, stem
        # The filenames the distractor run writes (its snapshot is keyed by them).
        assert {name.removesuffix(".png") for name in images} <= set(distractor_pins)
        rows = read_sidecar(directory)
        assert sorted(row["image_file"] for row in rows) == images, stem
        lines = (directory / SIDECAR_NAME).read_text().splitlines()
        for row, line in zip(rows, lines, strict=True):
            assert tuple(row) == ROW_KEYS and list(row) == sorted(row), line
            assert row["doc_type"] == TYPES[stem]
            assert f"{row['case_id']}_{row['layout']}.png" == row["image_file"]
            assert all(tuple(r) == REFUSAL_KEYS for r in row["refusals"])
            labels = drawn[row["image_file"]]
            if stem == "bank_statements":
                assert row["styles"] == {} and row["refusals"] == []
            # The row says what each site recorded at draw time (Review Focus 5).
            assert {f: s["wording"] for f, s in row["styles"].items()} == {
                f: text.strip() for f, (text, _) in labels.items()
            }
            for pick in row["styles"].values():
                assert pick["style"] in LABEL_STYLES
                assert (pick["wording"] == "") == (pick["style"] == "unlabelled")
            key = row["image_file"].removesuffix(".png")
            pixels = digest(Image.open(directory / row["image_file"]))
            assert pixels == varied_pins[key]["hash"], key
            assert {"styles": row["styles"], "refusals": row["refusals"]} == varied_pins[key]["choices"]
            if stem == "bank_statements":
                assert pixels == distractor_pins[key]["hash"], key
        styles, rules = summarise(rows)
        assert _summary(output, stem) == (+styles, rules), stem
    assert not (root / "output" / "clean").exists() and not (root / "output" / "distractor").exists()
    assert not (root / "derived").exists()


@pytest.fixture(scope="module")
def small(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, dict[str, dict]]:
    root = tmp_path_factory.mktemp("small")
    output, drawn = _run(root, CASES)
    return root, output, drawn


# --- the config key -------------------------------------------------------------------------------


def test_the_shipped_config_names_the_labelled_varied_subdirectory() -> None:
    assert load_generation_config(SHIPPED)["labelled_varied_output_subdir"] == "labelled_varied"


def test_a_config_without_the_key_is_refused(tmp_path: Path) -> None:
    data = yaml.safe_load(SHIPPED.read_text())
    del data["labelled_varied_output_subdir"]
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert f"{path.resolve()} -> labelled_varied_output_subdir" in message
    assert "labelled_varied_output_subdir: labelled_varied" in message


@pytest.mark.parametrize(
    "bad", ["", "a/b", "..", ".", "/abs", None, 3, "clean", "Clean", "distractor", "DISTRACTOR"]
)
def test_a_bad_subdirectory_is_refused(tmp_path: Path, bad: object) -> None:
    data = yaml.safe_load(_config(tmp_path, 1).read_text())
    data["labelled_varied_output_subdir"] = bad
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    assert f"{path.resolve()} -> labelled_varied_output_subdir" in str(err.value)


def test_the_directory_may_not_be_the_distractor_one_whatever_it_is_called(tmp_path: Path) -> None:
    data = yaml.safe_load(_config(tmp_path, 1).read_text())
    data["distractor_output_subdir"] = "noisy"
    data["labelled_varied_output_subdir"] = "Noisy"
    path = tmp_path / "generation_config.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_generation_config(path)
    assert_diagnostic_error(err.value)
    assert "'noisy'" in str(err.value)


# --- the switch -----------------------------------------------------------------------------------


def test_label_variation_without_distractors_is_refused_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, 1)
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["generate", "--config", str(config), "--label-variation"])
    assert result.exit_code != 0
    message = result.output
    assert_diagnostic_error(message)
    assert "--distractors" in message and f"{config.resolve()} -> labelled_varied_output_subdir" in message
    assert not (tmp_path / "output").exists() and not (tmp_path / "derived").exists()


def test_a_small_run_writes_renders_and_sidecars_that_match_the_pages(
    small: tuple[Path, str, dict[str, dict]],
) -> None:
    root, output, drawn = small
    _check_run(root, output, drawn, CASES)
    for stem, directory in _type_dirs(root):
        assert str(directory) in output, stem


def test_a_type_run_leaves_the_other_types_sidecars_untouched(
    small: tuple[Path, str, dict[str, dict]],
) -> None:
    """Also determinism: re-running receipts reproduces their images and sidecar byte for byte."""
    root, _, _ = small
    before = {
        stem: {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
        for stem, directory in _type_dirs(root)
    }
    output, _ = _run(root, CASES, "--type", "receipts")
    assert "invoices:" not in output and "bank_statements:" not in output
    after = {
        stem: {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
        for stem, directory in _type_dirs(root)
    }
    for stem in ("invoices", "bank_statements"):
        assert after[stem] == before[stem], stem
    assert {n: b for n, (b, _) in after["receipts"].items()} == {
        n: b for n, (b, _) in before["receipts"].items()
    }


def test_a_rerun_removes_the_images_an_earlier_run_left(tmp_path: Path) -> None:
    """A type directory holds the last run's images and nothing else: no stale PNG beside the sidecar."""
    stale = tmp_path / "output" / "labelled_varied" / "receipts" / "CASE999_receipt_old_layout.png"
    stale.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4)).save(stale)
    output, drawn = _run(tmp_path, 1)
    assert not stale.exists()
    _check_run(tmp_path, output, drawn, 1)


def test_a_refusal_under_fail_leaves_no_images_and_no_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first refusal stops the run; the earlier run's images and sidecar go with the partial one."""
    _run(tmp_path, 2)
    receipts = tmp_path / "output" / "labelled_varied" / "receipts"
    assert list(receipts.glob("*.png")) and (receipts / SIDECAR_NAME).exists()
    real = pipeline.render_labelled_varied
    calls: list[str] = []

    def refuse_second(renderer, entry, layout, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(entry["case_id"])
        if len(calls) == 2:
            raise pipeline.LabelRefusalError("refused for the test")
        return real(renderer, entry, layout, **kwargs)

    config = _config(tmp_path, 2)
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(pipeline, "render_labelled_varied", refuse_second)
    result = CliRunner().invoke(
        app,
        ["generate", "--config", str(config), "--distractors", "--label-variation", "--type", "receipts"],
    )
    assert result.exit_code == 1 and len(calls) == 2, result.output
    assert not list(receipts.glob("*.png")) and not (receipts / SIDECAR_NAME).exists()


def test_the_sidecar_is_read_by_nothing_that_reaches_the_ground_truth() -> None:
    """derive, eval-set, the exporters and the extraction ground-truth script never name the sidecar."""
    sources = [
        REPO_ROOT / "generators" / "derive_outputs.py",
        REPO_ROOT / "generators" / "eval_set.py",
        REPO_ROOT / "scripts" / "generate_extraction_gt.py",
        *sorted((REPO_ROOT / "generators" / "exporters").rglob("*.py")),
    ]
    for source in sources:
        text = source.read_text()
        for token in ("label_styles", "sidecar", "generators.labels", "LABEL_"):
            assert token not in text, (source, token)


_LEAKS = re.compile(r'LABEL_|label_styles|labelled_varied|unlabelled|"(styles|refusals|style|wording)"\s*:')
_STYLE_CELL = re.compile(r'(^|[,"])(labelled|variant|unlabelled)([,"]|$)')


def test_no_derived_export_or_extraction_file_carries_a_label_style(tmp_path: Path) -> None:
    """With a labelled varied run's sidecars on disk, derive, eval-set and the extraction ground-truth
    script write nothing naming a label key, a style or a sidecar column.

    The snapshot's own labelled varied sidecar is metadata beside the ground truth, and its README,
    MANIFEST.json and the PROVENANCE.json beside it document the snapshot, naming the labelled varied
    half (its style counts, or its density metrics) by design; none of the four is scanned. Every other
    file the export writes is."""
    from generators.snapshot.export import export_snapshot
    from tests.eval_export_tools import clean_state

    _run(tmp_path, 1)
    config = tmp_path / "generation_config.yml"
    data = yaml.safe_load(config.read_text())
    data["eval_set"]["output_root"] = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(REPO_ROOT)
        for command in (["generate"], ["derive"]):
            result = CliRunner().invoke(app, [*command, "--config", str(config)])
            assert result.exit_code == 0, result.output
        export_snapshot(config, repo_state=clean_state)
    extraction = tmp_path / "extraction.csv"
    subprocess.run(
        [sys.executable, "scripts/generate_extraction_gt.py", "--output", str(extraction)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    files = [extraction, *(tmp_path / "derived").rglob("*"), *(tmp_path / "out").rglob("*")]
    scanned = [
        f
        for f in files
        if f.is_file()
        and f.suffix in {".csv", ".jsonl", ".json", ".txt", ".md"}
        and f.name not in {SIDECAR_NAME, "README.md", "MANIFEST.json", "PROVENANCE.json"}
    ]
    assert len(scanned) >= 5 and any(f.parent.name == "derived" for f in scanned), scanned
    for path in scanned:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert not _LEAKS.search(line), (path, number, line[:200])
            assert not _STYLE_CELL.search(line), (path, number, line[:200])


# --- the whole corpus -------------------------------------------------------------------------------


@pytest.mark.slow
def test_the_whole_corpus_run(tmp_path: Path) -> None:
    """165 images under the distractor run's filenames, each sidecar row matching its page and its pin;
    the summary equals the sidecars; no paid invoice prints 'Balance:' over its total (Ruling 1)."""
    output, drawn = _run(tmp_path, None)
    _check_run(tmp_path, output, drawn, 55)
    names = {p.name.removesuffix(".png") for _, d in _type_dirs(tmp_path) for p in d.glob("*.png")}
    assert names == set(load_snapshot(DISTRACTOR_SNAPSHOT_PATH))
    for row in read_sidecar(tmp_path / "output" / "labelled_varied" / "invoices"):
        state = draw_states(load_distractor_config(), row["case_id"], "invoice")["payment"]
        if state == "paid":
            assert row["styles"]["TOTAL_AMOUNT"]["wording"] != "Balance:", row["case_id"]
