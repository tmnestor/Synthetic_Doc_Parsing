"""Pins every file `derive` writes, so a change to any derived view is deliberate."""

import json
from pathlib import Path

import pytest

from tests.derived_tools import DERIVED_SNAPSHOT_PATH, derive_into


@pytest.fixture(scope="module")
def derived(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return derive_into(tmp_path_factory.mktemp("first"))


def test_derived_outputs_match_the_snapshot(derived: dict[str, str]) -> None:
    pinned = json.loads(DERIVED_SNAPSHOT_PATH.read_text())
    assert derived == pinned


def test_derived_outputs_do_not_depend_on_where_they_are_written(
    derived: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    assert derive_into(tmp_path_factory.mktemp("second")) == derived


def test_the_snapshot_covers_the_core_artefacts() -> None:
    names = set(json.loads(DERIVED_SNAPSHOT_PATH.read_text()))
    assert {"ground_truth.csv", "ground_truth.jsonl", "geometry.jsonl"} <= {Path(n).name for n in names}


def test_no_raw_derived_file_embeds_a_machine_specific_path(tmp_path: Path) -> None:
    """Before normalisation, no derived file contains the scratch root or the repository root.

    Normalising `str(root)` away in the hashes would otherwise hide a leaked
    absolute path that makes the outputs depend on where they were written.
    """
    from tests.derived_tools import REPO_ROOT

    root = tmp_path / "raw_check"
    root.mkdir()
    derive_into(root)
    leaks = []
    for path in sorted((root / "derived").rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        leaks += [
            f"{path.name}: {needle}" for needle in (str(root), str(REPO_ROOT)) if needle.encode() in data
        ]
    assert not leaks, leaks
