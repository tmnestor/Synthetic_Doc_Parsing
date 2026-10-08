"""A distractor render always records its ink, geometry or not; a plain render is unchanged.

Stage 5 places its overlay away from every drawn line, so every distractor render, the ones `generate
--distractors` makes without `geometry_out` included, must carry its ink. The ink reaches a caller only
through `ink_out` or `geometry_out`, and is never written anywhere.
"""

import ast
from pathlib import Path

import pytest

from generators import bank_statement, invoice, receipt
from generators.distractors.keys import DX_PREFIX
from generators.exporters.geometry import InkRecord
from generators.layout_dsl import engine
from tests.conftest import REPO_ROOT
from tests.renderer_helpers import entry_per_layout, layouts_for, renderer_for

DOC_TYPES = ("receipt", "invoice", "bank_statement")
_RENDER_MODULES = {"receipt": receipt, "invoice": invoice, "bank_statement": bank_statement}


def _first(doc_type: str) -> tuple[dict, dict]:
    layout_id, entry = next(iter(entry_per_layout(doc_type).items()))
    return entry, layouts_for(doc_type)[layout_id]


@pytest.mark.parametrize("doc_type", DOC_TYPES)
@pytest.mark.parametrize(("distractors", "recorded"), [(True, True), (False, False)])
def test_a_render_without_geometry_records_ink_only_with_distractors(
    doc_type: str, distractors: bool, recorded: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []
    real = engine.render_body

    def spy(*args: object, **kwargs: object) -> int:
        seen.append(kwargs["recorder"])
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_RENDER_MODULES[doc_type], "render_body", spy)
    entry, layout = _first(doc_type)
    renderer_for(doc_type)(entry, layout, distractors=distractors)
    assert len(seen) == 1
    assert (seen[0] is not None) is recorded


def test_a_distractor_receipt_hands_its_ink_to_the_caller_without_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    entry, layout = _first("receipt")
    ink: list[InkRecord] = []
    image = renderer_for("receipt")(entry, layout, distractors=True, ink_out=ink)
    assert ink and any(record.role == "distractor" for record in ink)
    assert all(isinstance(record, InkRecord) for record in ink)

    geometry: dict = {}
    pinned = renderer_for("receipt")(entry, layout, distractors=True, geometry_out=geometry)
    assert ink == geometry["ink"]
    assert image.tobytes() == pinned.tobytes()
    assert not list(tmp_path.iterdir()), "a render wrote a file"


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_ink_out_on_a_plain_render_leaves_its_pixels_unchanged(doc_type: str) -> None:
    entry, layout = _first(doc_type)
    ink: list[InkRecord] = []
    with_ink = renderer_for(doc_type)(entry, layout, distractors=False, ink_out=ink)
    bare = renderer_for(doc_type)(entry, layout, distractors=False)
    assert ink
    assert not any(record.role == "distractor" for record in ink)
    assert with_ink.tobytes() == bare.tobytes()


# --- the one prefix ----------------------------------------------------------------------------------


def _prefix_literals(root: Path) -> list[str]:
    """Every string constant equal to the bare prefix, outside the module that defines it."""
    found: list[str] = []
    for path in sorted((root / "generators").rglob("*.py")):
        if path == root / "generators" / "distractors" / "keys.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and node.value == DX_PREFIX:
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


def test_the_distractor_prefix_is_spelled_once() -> None:
    assert _prefix_literals(REPO_ROOT) == []
