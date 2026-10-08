"""Permanent regression guard: every page renders exactly as pinned.

Re-bless only after an intended rendering change:

    conda run -n synthetic python tests/regenerate_pixel_snapshot.py --confirm
"""

import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from tests import snapshot_tools
from tests.snapshot_tools import (
    DOC_TYPES,
    EXPECTED_PER_TYPE,
    REPO_ROOT,
    SNAPSHOT_PATH,
    compare,
    digest,
    load_snapshot,
    render_corpus,
)


@pytest.fixture(scope="module")
def current() -> dict[str, dict]:
    return render_corpus()


def test_snapshot_pins_the_whole_corpus() -> None:
    counts = Counter(record["type"] for record in load_snapshot().values())
    assert counts == {doc_type: EXPECTED_PER_TYPE for doc_type in DOC_TYPES}


def test_every_page_matches_the_snapshot(current: dict[str, dict]) -> None:
    problems = compare(load_snapshot(), current)
    assert not problems, "\n".join(problems)


def test_one_changed_pixel_changes_the_digest() -> None:
    image = Image.new("RGB", (4, 4), "white")
    before = digest(image)
    image.putpixel((2, 2), (254, 255, 255))
    assert digest(image) != before


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda s: s["A"].update(hash="0"), "A: hash differs"),
        (lambda s: s["A"].update(size=[1, 1]), "A: size differs"),
        (lambda s: s["A"].update(boxes={}), "A: boxes differs"),
        (lambda s: s.pop("A"), "A: rendered but not pinned"),
        (lambda s: s.update(B=dict(s["A"])), "B: pinned but no longer rendered"),
    ],
)
def test_compare_reports_each_kind_of_difference(mutate, expected: str) -> None:
    current = {"A": {"type": "receipt", "hash": "h", "size": [2, 2], "boxes": {"X": [0, 0, 1, 1]}}}
    pinned = {key: dict(value) for key, value in current.items()}
    mutate(pinned)
    assert expected in compare(pinned, current)


def test_rendering_after_import_does_not_depend_on_the_working_directory(
    current: dict[str, dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renders made after `generators` is imported are unaffected by a later chdir.

    This proves only that render-time paths are absolute. Paths read at import
    time are out of its reach (the modules are already imported); the fresh
    process test below covers those.
    """
    monkeypatch.chdir(tmp_path)
    elsewhere = render_corpus(("receipt",))
    assert not compare({k: v for k, v in current.items() if v["type"] == "receipt"}, elsewhere)


_FRESH_PROCESS_SCRIPT = """
import sys
from pathlib import Path
from generators.bank_statement import render_bank_statement
from generators.invoice import render_invoice
from generators.loader import load_ground_truth, load_layout_registry
from generators.receipt import render_receipt

root = Path(sys.argv[1])
for stem, render in (
    ("receipts", render_receipt),
    ("invoices", render_invoice),
    ("bank_statements", render_bank_statement),
):
    entries = load_ground_truth(root / "ground_truth" / f"{stem}.yml")
    layouts = load_layout_registry(root / "config" / "layouts" / f"{stem}.yml")
    case_id, raw = next(iter(entries.items()))
    entry = {**raw, "case_id": str(case_id)}
    render(entry, layouts[entry["layout"]], distractors=False, geometry_out={})
"""


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG: generators/layout_dsl/provider_registry.py (_FIELD_DEFINITIONS_PATH) reads config/field_definitions.yml "
        "relative to the working directory; also scripts/seed_ground_truth.py, "
        "generators/schema.py, generators/pipeline.py:185-190 (see ledger)"
    ),
)
def test_a_fresh_process_renders_from_outside_the_repository_bug(tmp_path: Path) -> None:
    """A fresh interpreter started outside the repository renders one page per type."""
    result = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_SCRIPT, str(REPO_ROOT)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"fresh process failed (rc={result.returncode}):\n{result.stderr}"


def test_a_ground_truth_entry_naming_a_missing_layout_raises_keyerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`render_corpus` raises KeyError, not a skip, for an entry whose layout is not registered."""
    real = snapshot_tools.load_ground_truth

    def with_bad_layout(path: Path) -> dict:
        entries = real(path)
        first = next(iter(entries))
        entries[first] = {**entries[first], "layout": "no_such_layout_9xx"}
        return entries

    monkeypatch.setattr(snapshot_tools, "load_ground_truth", with_bad_layout)
    with pytest.raises(KeyError, match="no_such_layout_9xx"):
        render_corpus(("receipt",))


def test_regenerate_without_confirm_writes_nothing() -> None:
    before = SNAPSHOT_PATH.stat().st_mtime_ns
    result = subprocess.run(
        [sys.executable, "tests/regenerate_pixel_snapshot.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout.lower()
    assert SNAPSHOT_PATH.stat().st_mtime_ns == before


def test_regenerate_refuses_an_unpinned_pillow() -> None:
    result = subprocess.run(
        [sys.executable, "tests/regenerate_pixel_snapshot.py", "--confirm", "--pretend-pillow", "0.0.0"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "pillow" in (result.stdout + result.stderr).lower()
