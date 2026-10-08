"""The snapshot README: generated from the manifest, every section present, the field descriptions
required, and the changes section's three cases (Decisions 13 and 14)."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from generators.exporters.eval_projection import load_extraction_schema
from generators.snapshot import readme as readme_module
from generators.snapshot.export import SnapshotSummary
from generators.snapshot.manifest import MANIFEST_NAME
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
    "## 1. Purpose and the four halves",
    "## 2. The role of MANIFEST.json",
    "## 3. Heavy-tier images are not bit-reproducible",
    "## 4. Fields and formats",
    "## 5. Distractor and overlay policy",
    "## 6. Label variation and the sidecar",
    "## 7. Pairing, joining, the preflight and the notebook",
    "## 8. Changes and comparability",
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
    """With no earlier snapshot under the root, the README is exactly what the manifest renders to."""
    assert _readme(export) == render_readme(_manifest(export), schema=SCHEMA, previous=None)
    assert "first snapshot to carry a manifest" in _readme(export)


def test_counts_dates_and_commit_come_from_the_manifest(export: SnapshotSummary) -> None:
    manifest = _manifest(export)
    text = _readme(export)
    for half, directory in manifest["halves"].items():
        assert f"| `{directory}/` | {manifest['counts'][half]['total']} |" in text, half
    total = sum(count["total"] for count in manifest["counts"].values())
    assert f"holds {total} images" in text
    assert manifest["snapshot_date"] in text and manifest["generator_commit"] in text
    for block in ("config_sha256", "ground_truth_sha256"):
        for path, digest in manifest[block].items():
            assert f"| `{path}` | `{digest}` |" in text


def _opening(text: str) -> str:
    """The README before its first numbered section."""
    return text[: text.index(SECTIONS[0])]


def test_a_clean_snapshot_says_so_near_the_top(export: SnapshotSummary) -> None:
    opening = _opening(_readme(export))
    assert "**Built from a clean commit.**" in opening and "`dirty: false`" in opening
    assert "working_tree.patch" not in opening and "modified working tree" not in opening


def test_a_dirty_snapshot_says_so_near_the_top_and_points_to_the_patch(
    dirty_export: tuple[SnapshotSummary, Path],
) -> None:
    summary, _ = dirty_export
    manifest = _manifest(summary)
    opening = _opening(_readme(summary))
    count = len(manifest["working_tree_changes"])
    assert "**Built from a modified working tree, not from a clean commit.**" in opening
    assert f"{count} path(s) differed from commit `{manifest['generator_commit'][:12]}`" in opening
    assert "`working_tree.patch`" in opening and "`working_tree_changes`" in opening
    assert _readme(summary) == render_readme(manifest, schema=SCHEMA, previous=None)


def test_the_readme_states_the_heavy_tier_is_not_reproducible(export: SnapshotSummary) -> None:
    heavy = _manifest(export)["degradation_tiers"][-1]["suffix"]
    text = _readme(export)
    assert "not bit-reproducible" in text and f"every `_{heavy}`" in text


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
        render_readme(_manifest(export), schema=SCHEMA, previous=None)
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


# --- changes since the previous snapshot (Decision 14) -----------------------------------------------


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
    (output / name).mkdir(parents=True)
    manifest = {"generator_commit": commit, "snapshot_date": "2031-03-01"}
    (output / name / MANIFEST_NAME).write_text(json.dumps(manifest))


def test_an_ancestor_lists_the_commits_between(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    _earlier(output, "snapshot_20310101", commits[0])
    _earlier(output, "snapshot_20310201", commits[0])
    previous = find_previous(
        output, prefix="snapshot", current_dir="snapshot_20310309", head=commits[2], repo_root=root
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
            output, prefix="snapshot", current_dir="snapshot_20310309", head=commits[2], repo_root=root
        )
        is None
    )


def test_an_unknown_commit_is_said_to_be_unknown(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    _earlier(output, "snapshot_20310101", "f" * 40)
    previous = find_previous(
        output, prefix="snapshot", current_dir="snapshot_20310309", head=commits[2], repo_root=root
    )
    assert previous is not None and previous.relation == UNKNOWN and previous.subjects == ()


def test_no_earlier_manifest_means_none(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    (output / "snapshot_20310101").mkdir(parents=True)  # an earlier export with no manifest
    assert (
        find_previous(
            output, prefix="snapshot", current_dir="snapshot_20310309", head=commits[2], repo_root=root
        )
        is None
    )


def test_an_unreadable_earlier_manifest_is_refused(history: tuple[Path, list[str]], tmp_path: Path) -> None:
    root, commits = history
    output = tmp_path / "out"
    (output / "snapshot_20310101").mkdir(parents=True)
    (output / "snapshot_20310101" / MANIFEST_NAME).write_text("{not json")
    with pytest.raises(ReadmeError) as err:
        find_previous(
            output, prefix="snapshot", current_dir="snapshot_20310309", head=commits[2], repo_root=root
        )
    assert_diagnostic_error(err.value)


@pytest.mark.parametrize(
    ("previous", "expected"),
    [
        (None, "This is the first snapshot to carry a manifest"),
        (
            PreviousSnapshot("snapshot_20310101", "2031-01-01", "a" * 40, ANCESTOR, ("invented subject",)),
            "The commits between the two, oldest first:\n\n- invented subject",
        ),
        (
            PreviousSnapshot("snapshot_20310101", "2031-01-01", "b" * 40, UNKNOWN),
            "Its commit is unknown to the generator's repository",
        ),
    ],
    ids=["none", "ancestor", "unknown"],
)
def test_the_changes_section_covers_each_case(
    export: SnapshotSummary, previous: PreviousSnapshot | None, expected: str
) -> None:
    text = render_readme(_manifest(export), schema=SCHEMA, previous=previous)
    changes = text[text.index("## 8. Changes and comparability") :]
    assert expected in changes
    assert "`synthetic_`" in changes and "`plain_`" in changes
    assert "No score from an export made before" in changes
    assert chr(0x2014) not in text
