"""The snapshot README: generated from the manifest, every section present, the field descriptions
required, nothing said about how the snapshot was produced, and the earlier snapshot the provenance
record names (Decisions 13 and 14)."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from generators.exporters.eval_projection import load_extraction_schema
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.snapshot import readme as readme_module
from generators.snapshot.export import SnapshotSummary
from generators.snapshot.config import load_snapshot_config
from generators.snapshot.manifest import MANIFEST_NAME, PROVENANCE_NAME
from generators.snapshot.publication import find_forbidden
from generators.snapshot.readme import (
    ANCESTOR,
    TEMPLATE_PATH,
    UNKNOWN,
    PreviousSnapshot,
    ReadmeError,
    find_previous,
    load_field_descriptions,
    render_readme,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SCHEMA = load_extraction_schema()
SCHEMA_PATH = REPO_ROOT / "config" / "extraction_schema.yml"
SECTIONS = (
    "## 1. What is here",
    "## 2. Fields and formats",
    "## 3. Distractors and annotations",
    "## 4. Label variation and the sidecar",
    "## 5. Running a model and scoring it",
    "## 6. Verifying what you downloaded",
    "## 7. Comparing scores",
    "## 8. Parsing ground truth",
)


def _manifest(export: SnapshotSummary) -> dict:
    return json.loads((export.path / MANIFEST_NAME).read_text())


def _readme(export: SnapshotSummary) -> str:
    return (export.path / "README.md").read_text()


def test_every_section_is_present_in_order(export: SnapshotSummary) -> None:
    text = _readme(export)
    positions = [text.index(heading) for heading in SECTIONS]
    assert positions == sorted(positions)


def test_the_shipped_readme_is_the_manifest_rendered(export: SnapshotSummary) -> None:
    assert _readme(export) == render_readme(_manifest(export), schema=SCHEMA)


def test_counts_and_dates_come_from_the_manifest(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    text = _readme(export)
    for half, directory in manifest["halves"].items():
        assert f"| `{directory}/` | {manifest['counts'][half]['total']} |" in text, half
    total = sum(count["total"] for count in manifest["counts"].values())
    assert f"holds {total} page images" in text
    assert manifest["snapshot_date"] in text
    for tier in manifest["degradation_tiers"]:
        assert f"`_{tier['suffix']}` ({tier['name']})" in text


def test_the_readme_says_nothing_about_how_the_snapshot_was_produced(export: SnapshotSummary) -> None:
    """No commit, input file, library or repository: the README is published to readers of a bucket."""
    text = _readme(export)
    assert export.provenance_dir is not None
    record = json.loads((export.provenance_dir / PROVENANCE_NAME).read_text())
    assert record["generator_commit"][:12] not in text
    for path in [*record["config_sha256"], *record["ground_truth_sha256"]]:
        assert path not in text, path
    terms = load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml").forbidden_terms
    assert terms, "no forbidden terms are declared, so this test proves nothing"
    assert find_forbidden(export.path, terms) == []


def _section(text: str, heading: str) -> str:
    return text[text.index(heading) :].split("\n## ", 1)[0]


def test_the_parsing_section_names_the_files_the_evaluator_and_the_scored_tiers(
    export: SnapshotSummary,
) -> None:
    """The section is generated from the manifest, which takes the evaluator and tiers from the parsing
    configuration."""
    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    manifest = _manifest(export)
    text = _readme(export)
    section = _section(text, "## 8. Parsing ground truth")
    for path in manifest["parsing"]["pages"].values():
        assert f"`{path}`" in section, path
    assert "| `parsing/` |" in text
    assert f"`{parsing.evaluator.commit}`" in section
    assert parsing.evaluator.repo.removesuffix(".git") in section
    for metric in ("text edit distance", "TEDS", "table edit distance", "reading-order edit distance"):
        assert metric in section, metric
    assert "overall" in section
    flat = " ".join(section.split())
    assert "stable within this snapshot only: they renumber when a layout is added" in flat
    scored = " and ".join(parsing.layout_scored_tiers)
    assert f"the {scored} tiers count for layout detection" in section
    tiers = [tier["name"] for tier in manifest["degradation_tiers"]]
    excluded = [name for name in tiers if name not in parsing.layout_scored_tiers]
    assert excluded == ["heavy"]
    assert (
        "The heavy tier does not: its fold moves ink by more than "
        f"{parsing.fold_displacement_max_px} pixels"
    ) in section


def test_the_parsing_section_follows_the_manifest(export: SnapshotSummary) -> None:
    """Every tier scored for layout: the section says so, and names no tier left out."""
    manifest = _manifest(export)
    names = [tier["name"] for tier in manifest["degradation_tiers"]]
    manifest["parsing"]["layout_scored_tiers"] = names
    manifest["parsing"]["evaluator"]["commit"] = "f" * 40
    section = _section(render_readme(manifest, schema=SCHEMA), "## 8. Parsing ground truth")
    assert f"`{'f' * 40}`" in section
    assert "every degradation tier counts for layout detection" in section
    assert "does not:" not in section


def test_the_readme_describes_the_prediction_format(export: SnapshotSummary) -> None:
    text = _readme(export)
    for key in ("`image_name`", "`document_type`", "`raw_response`"):
        assert key in text


def test_the_readme_gives_the_integrity_commands(export: SnapshotSummary) -> None:
    text = _readme(export)
    assert f"cd {export.path.name}\n" in text
    for command in (
        "shasum -a 256 -c SHA256SUMS",
        "sha256sum -c SHA256SUMS",
        "python tools/verify_snapshot.py",
    ):
        assert command in text


def test_every_field_is_described(export: SnapshotSummary) -> None:
    text = _readme(export)
    descriptions = load_field_descriptions(SCHEMA)
    fields = {f for fields in SCHEMA.document_fields.values() for f in fields}
    assert set(descriptions) == fields and len(fields) == 17
    for field, description in descriptions.items():
        assert f"| `{field}` |" in text and description.replace("|", "\\|") in text


def test_no_dash_used_as_punctuation(export: SnapshotSummary) -> None:
    text = _readme(export)
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert not re.search(r"\s--\s", text)  # a spaced double hyphen used as a dash; flags are fine


def test_no_layout_or_bank_name_in_the_readme(export: SnapshotSummary) -> None:
    """Filenames and prose never name a template, nor the real banks some bank layouts imitate."""
    text = _readme(export).lower()
    for name in ("bank_statements", "invoices", "receipts"):
        registry = yaml.safe_load((REPO_ROOT / "config" / "layouts" / f"{name}.yml").read_text())["layouts"]
        for layout_id in registry:
            assert layout_id not in text, layout_id
            if name == "bank_statements":
                assert not re.search(rf"\b{layout_id.split('_')[0]}\b", text), layout_id


def test_template_examples_are_invented() -> None:
    """Every amount and date the template shows is absent from the ground truth."""
    template = TEMPLATE_PATH.read_text()
    examples = re.findall(r"\d+\.\d{2}\b", template) + re.findall(r"\d{2}/\d{2}/\d{4}", template)
    assert examples, "the template shows no example value, so this test proves nothing"
    corpus = "".join(p.read_text() for p in (REPO_ROOT / "ground_truth").glob("*.yml"))
    for example in examples:
        assert example not in corpus, example


def test_an_unfilled_placeholder_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export: SnapshotSummary
) -> None:
    template = tmp_path / "template.md"
    template.write_text(TEMPLATE_PATH.read_text() + "\n$invented_placeholder\n")
    monkeypatch.setattr(readme_module, "TEMPLATE_PATH", template)
    with pytest.raises(ReadmeError) as err:
        render_readme(_manifest(export), schema=SCHEMA)
    assert_diagnostic_error(err.value)
    assert "invented_placeholder" in str(err.value)


# --- field descriptions ------------------------------------------------------------------------------


def _scratch_schema(tmp_path: Path, edit) -> Path:
    data = yaml.safe_load(SCHEMA_PATH.read_text())
    edit(data)
    path = tmp_path / "extraction_schema.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _drop_block(data: dict) -> None:
    del data["field_descriptions"]


def _drop_field(data: dict) -> None:
    del data["field_descriptions"]["GST_AMOUNT"]


def _stray(data: dict) -> None:
    data["field_descriptions"]["INVENTED_FIELD"] = "Names no field."


@pytest.mark.parametrize(
    ("edit", "key"),
    [
        (_drop_block, "field_descriptions"),
        (_drop_field, "field_descriptions.GST_AMOUNT"),
        (_stray, "field_descriptions.INVENTED_FIELD"),
    ],
    ids=["block_missing", "field_without_description", "description_naming_no_field"],
)
def test_field_descriptions_are_required(tmp_path: Path, edit, key: str) -> None:
    path = _scratch_schema(tmp_path, edit)
    with pytest.raises(ReadmeError) as err:
        load_field_descriptions(load_extraction_schema(path))
    assert_diagnostic_error(err.value)
    assert f"{path.resolve()} -> {key}" in str(err.value)


def test_the_projection_and_the_csv_script_ignore_field_descriptions() -> None:
    from scripts.generate_extraction_gt import _load_schema

    assert "field_descriptions" in yaml.safe_load(SCHEMA_PATH.read_text())
    assert set(SCHEMA.document_fields) == {"invoice", "receipt", "bank_statement"}
    assert set(_load_schema(SCHEMA_PATH)) == {"invoice_fields", "bank_fields", "monetary", "boolean"}


# --- the earlier snapshot the provenance record names (Decision 14) ---------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, list[str]]:
    """A repository of three commits; returns its root and their ids, oldest first."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tester@example.invalid")
    _git(root, "config", "user.name", "Tester")
    commits = []
    for subject in ("first invented change", "second invented change", "third invented change"):
        (root / "file.txt").write_text(subject)
        _git(root, "add", "file.txt")
        _git(root, "commit", "-q", "-m", subject)
        commits.append(_git(root, "rev-parse", "HEAD"))
    return root, commits


def _earlier(output: Path, name: str, commit: str) -> None:
    (output / f"{name}.provenance").mkdir(parents=True)
    record = {"generator_commit": commit, "snapshot_date": "2031-03-01"}
    (output / f"{name}.provenance" / PROVENANCE_NAME).write_text(json.dumps(record))


def test_an_ancestor_lists_the_commits_between(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    _earlier(output, "snapshot_20310101", commits[0])
    _earlier(output, "snapshot_20310201", commits[0])
    previous = find_previous(
        output,
        prefix="snapshot",
        suffix="provenance",
        current_dir="snapshot_20310309",
        head=commits[2],
        repo_root=root,
    )
    assert previous == PreviousSnapshot(
        "snapshot_20310201",
        "2031-03-01",
        commits[0],
        ANCESTOR,
        ("second invented change", "third invented change"),
    )


def test_a_later_or_same_name_snapshot_is_not_the_previous_one(
    history: tuple[Path, list[str]], tmp_path: Path
) -> None:
    root, commits = history
    output = tmp_path / "out"
    _earlier(output, "snapshot_20310309", commits[0])
    _earlier(output, "snapshot_20310401", commits[0])
    assert (
        find_previous(
            output,
            prefix="snapshot",
            suffix="provenance",
            current_dir="snapshot_20310309",
            head=commits[2],
            repo_root=root,
        )
        is None
    )


def test_an_unknown_commit_is_said_to_be_unknown(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    _earlier(output, "snapshot_20310101", "f" * 40)
    previous = find_previous(
        output,
        prefix="snapshot",
        suffix="provenance",
        current_dir="snapshot_20310309",
        head=commits[2],
        repo_root=root,
    )
    assert previous is not None and previous.relation == UNKNOWN and previous.subjects == ()


def test_no_earlier_manifest_means_none(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    (output / "snapshot_20310101").mkdir(parents=True)  # an earlier snapshot with no record
    (output / "snapshot_20310101" / MANIFEST_NAME).write_text(json.dumps({"generator_commit": "a" * 40}))
    assert (
        find_previous(
            output,
            prefix="snapshot",
            suffix="provenance",
            current_dir="snapshot_20310309",
            head=commits[2],
            repo_root=root,
        )
        is None
    )


def test_an_unreadable_earlier_manifest_is_refused(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    (output / "snapshot_20310101.provenance").mkdir(parents=True)
    (output / "snapshot_20310101.provenance" / PROVENANCE_NAME).write_text("{not json")
    with pytest.raises(ReadmeError) as err:
        find_previous(
            output,
            prefix="snapshot",
            suffix="provenance",
            current_dir="snapshot_20310309",
            head=commits[2],
            repo_root=root,
        )
    assert_diagnostic_error(err.value)


def test_the_previous_snapshot_is_recorded_in_full() -> None:
    previous = PreviousSnapshot(
        "snapshot_20310101", "2031-01-01", "a" * 40, ANCESTOR, ("invented subject",)
    )
    assert previous.as_record() == {
        "commit": "a" * 40,
        "commits_since": ["invented subject"],
        "directory": "snapshot_20310101",
        "relation": ANCESTOR,
        "snapshot_date": "2031-01-01",
    }
