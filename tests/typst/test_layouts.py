"""Every layout typeset: each one's first case, plain and with distractors, against stage one's page.

For each of the 22 layouts the first case in `ground_truth/` (by case id) is translated, compiled and read
back. The page must compile with no Typst warning onto one page (an A4 sheet, or one roll), pass stage
one's six invariants (`check_page`) and the layout checks (`layout_errors`), and its blocks' texts and
tables must equal stage one's `build_page` for the same case and mode, block by block (whitespace
collapsed, overlay blocks excluded). Compiling 44 pages takes a while, so these tests are slow.
"""

import functools
from dataclasses import dataclass
from pathlib import Path

import pypdfium2
import pytest

from generators.loader import load_ground_truth, load_layout_registry
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.omnidocbench import PageContext, build_page, check_page, page_where
from generators.pipeline import _RENDERERS
from generators.typst.compare import stage_one_differences
from generators.typst.compile import compile_page
from generators.typst.config import TYPESET_CONFIG_PATH, PageSpec, layout_page, load_typeset_config
from generators.typst.readback import OVERSHOOT_TOLERANCE_PT, layout_errors, page_geometry
from generators.typst.translate import GATE_LAYOUTS, Translation, page_scale, translate_page
from tests.conftest import REPO_ROOT, assert_diagnostic_error

_LAYOUT_FILES = sorted((REPO_ROOT / "config" / "layouts").glob("*.yml"))
_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}
_MODES = ("plain", "distractor")


def _layouts() -> list[tuple[Path, str]]:
    """Every layout, with the file that holds it, read from the registry."""
    return [(path, layout_id) for path in _LAYOUT_FILES for layout_id in load_layout_registry(path)]


@dataclass(frozen=True)
class Typeset:
    """One typeset page and every check on it."""

    case_id: str
    translation: Translation
    geometry: dict
    page: dict
    stage_one: dict
    spec: PageSpec
    page_count: int
    page_mm: tuple[float, float]
    invariant_errors: list[str]
    layout_errors: list[str]
    differences: list[str]


def first_case(layout_path: Path, layout_id: str) -> tuple[str, dict]:
    """Return a layout's first case by case id, with its entry."""
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / layout_path.name)
    case_id = next(str(c) for c in sorted(entries, key=str) if entries[c]["layout"] == layout_id)
    return case_id, {**entries[case_id], "case_id": case_id}


@functools.cache
def typeset(layout_path: Path, layout_id: str, mode: str, workdir: Path) -> Typeset:
    """Typeset a layout's first case in `mode`, read it back and run every check on it."""
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    case_id, entry = first_case(layout_path, layout_id)
    layout = load_layout_registry(layout_path)[layout_id]
    spec = layout_page(layout, layout_path=str(layout_path), layout_id=layout_id)
    distractors = mode == "distractor"
    stage_one: dict = {}
    _RENDERERS[layout_path.stem](entry, layout, distractors=distractors, geometry_out=stage_one)
    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        page=spec,
        distractors=distractors,
        config=config,
        overlay_dir=workdir / "overlays",
    )
    compiled = compile_page(translation.source, workdir=workdir, config=config)
    geometry = page_geometry(compiled, dpi=config.raster_dpi, translation=translation)
    where = page_where(case_id, layout_id, f"typeset {mode}")
    doc_type = _DOC_TYPES[layout_path.stem]
    context = PageContext(f"{case_id}.png", doc_type, layout_id, f"typeset_{mode}", "none", True)
    page = build_page(geometry, context=context, config=parsing)
    document = pypdfium2.PdfDocument(compiled.pdf)
    try:
        page_count = len(document)
    finally:
        document.close()
    return Typeset(
        case_id=case_id,
        translation=translation,
        geometry=geometry,
        page=page,
        stage_one=stage_one,
        spec=spec,
        page_count=page_count,
        page_mm=(compiled.width_pt * 25.4 / 72, compiled.height_pt * 25.4 / 72),
        invariant_errors=check_page(page, geometry, where=where, config=parsing),
        layout_errors=layout_errors(geometry, where=where),
        differences=stage_one_differences(page, stage_one, context=context),
    )


@pytest.fixture(scope="module")
def workroot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("layouts")


def test_every_layout_is_covered() -> None:
    assert len(_layouts()) == 22


@pytest.mark.slow
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    ("layout_path", "layout_id"), _layouts(), ids=lambda value: getattr(value, "stem", value)
)
def test_every_layout_typesets_as_stage_one_draws_it(
    layout_path: Path, layout_id: str, mode: str, workroot: Path
) -> None:
    result = typeset(layout_path, layout_id, mode, workroot / f"{layout_id}_{mode}")
    assert result.page_count == 1
    # One A4 sheet, or one roll of its own width.
    assert result.page_mm[0] == pytest.approx(result.spec.width_mm, abs=0.01)
    if result.spec.height_mm is not None:
        assert result.page_mm[1] == pytest.approx(result.spec.height_mm, abs=0.01)
    assert result.invariant_errors == []
    assert result.layout_errors == []
    assert result.differences == []


@pytest.mark.slow
def test_the_gate_receipts_staff_line_is_one_line_at_stage_ones_size(workroot: Path) -> None:
    # The gate once wrapped this half-column line onto two lines; Typst now sets each line stage one drew
    # as one line, at the size stage one drew it.
    layout_file, layout_id = next(gate for gate in GATE_LAYOUTS if "receipts" in gate[0])
    layout_path = REPO_ROOT / layout_file
    result = typeset(layout_path, layout_id, "plain", workroot / f"{layout_id}_plain")
    (drawn,) = [record for record in result.stage_one["ink"] if "Staff:" in record.text]
    lines = [record for record in result.geometry["ink"] if record.block == drawn.block and not record.mark]
    # Read-back joins a line's words by one space.
    assert [line.text for line in lines] == [" ".join(drawn.text.split())]
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    layout = load_layout_registry(layout_path)[layout_id]
    points = drawn.size * page_scale(layout, result.spec, canvas_dpi=config.canvas_dpi)
    assert lines[0].size == round(points * config.raster_dpi / 72)


# --- Ruling P9: a block may run out of its column only as far as stage one's own line did -----------------


def _overshooting(workroot: Path) -> tuple[Typeset, int]:
    """The 57 mm receipt's first case, plain, and the one block whose stage-one line runs out of its column."""
    layout_id = next(
        layout_id
        for path, layout_id in _layouts()
        if path.stem == "receipts" and "57" in str(load_layout_registry(path)[layout_id]["page"])
    )
    result = typeset(
        REPO_ROOT / "config" / "layouts" / "receipts.yml",
        layout_id,
        "plain",
        workroot / f"{layout_id}_plain",
    )
    (block_id,) = result.translation.overshoot
    return result, block_id


def _pushed(geometry: dict, block_id: int, *, right: float) -> dict:
    """Return the geometry with one block's right edge moved to `right` pixels."""
    measured = geometry["measured"]
    left, top, _, bottom = measured.boxes[block_id]
    boxes = {**measured.boxes, block_id: (left, top, right, bottom)}
    return {**geometry, "measured": measured._replace(boxes=boxes)}


@pytest.mark.slow
def test_only_a_block_stage_one_drew_past_its_column_gets_an_allowance(workroot: Path) -> None:
    result, block_id = _overshooting(workroot)
    (drawn,) = [record for record in result.stage_one["ink"] if record.block == block_id]
    assert "Staff:" in drawn.text
    left, right = result.translation.overshoot[block_id]
    assert left == 0 and right > 0
    allowances = result.geometry["measured"].allowances
    assert set(allowances) == {block_id}
    assert allowances[block_id][0] == 0
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    assert allowances[block_id][1] == pytest.approx(
        (right + OVERSHOOT_TOLERANCE_PT) * config.raster_dpi / 72
    )
    assert result.layout_errors == []


@pytest.mark.slow
def test_a_block_pushed_further_than_stage_ones_overshoot_is_refused(workroot: Path) -> None:
    result, block_id = _overshooting(workroot)
    measured = result.geometry["measured"]
    column_right = measured.columns[block_id][1]
    two_points = 2 * load_typeset_config(TYPESET_CONFIG_PATH).raster_dpi / 72
    limit = column_right + measured.allowances[block_id][1]
    where = "P9 check"
    assert layout_errors(_pushed(result.geometry, block_id, right=limit), where=where) == []
    (error,) = layout_errors(_pushed(result.geometry, block_id, right=limit + two_points), where=where)
    assert_diagnostic_error(error)
    assert "out of its column" in error


@pytest.mark.slow
def test_a_block_stage_one_kept_inside_its_column_may_not_overrun_it(workroot: Path) -> None:
    result, overshooting = _overshooting(workroot)
    measured = result.geometry["measured"]
    inside = next(
        block.id
        for block in result.translation.blocks
        if block.id != overshooting and block.id in measured.columns and block.kind == "text"
    )
    two_points = 2 * load_typeset_config(TYPESET_CONFIG_PATH).raster_dpi / 72
    pushed = _pushed(result.geometry, inside, right=measured.columns[inside][1] + two_points)
    errors = [e for e in layout_errors(pushed, where="P9 check") if "out of its column" in e]
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
