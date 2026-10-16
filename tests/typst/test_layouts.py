"""Every layout typeset: each one's first case, plain and with distractors, against stage one's page.

For each of the 22 layouts the first case in `ground_truth/` (by case id) is typeset (`typeset_case`): with
distractors, overlays included. The page must compile with no Typst warning onto one page (an A4 sheet,
or one roll), pass stage one's six invariants (`check_page`) and the layout checks (`layout_errors`), and
its blocks' texts and tables must equal stage one's `build_page` for the same case and mode, block by
block (whitespace collapsed, overlay blocks included). Its lines carry stage one's roles, line for line;
with distractors it meets the density bounds and every overlay passes the placement checks, both on the
typeset geometry. Review Focus 3 and 4 run on every layout. Compiling 44 pages takes a while, so these
tests are slow.
"""

import dataclasses
import functools
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pypdfium2
import pytest
from PIL import Image, ImageFilter

from generators.distractors.config import load_distractor_config
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH
from generators.loader import load_ground_truth, load_layout_registry
from generators.overflow_check import render_modes
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.omnidocbench import (
    OVERLAY_PREFIX,
    PageContext,
    build_page,
    check_page,
    page_where,
)
from generators.pipeline import _RENDERERS
from generators.typst.checks import overlay_errors
from generators.typst.compare import stage_one_differences
from generators.typst.compile import compile_page, rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, PageSpec, layout_page, load_typeset_config
from generators.typst.readback import OVERSHOOT_TOLERANCE_PT, layout_errors
from generators.typst.translate import GATE_LAYOUTS, Translation, page_scale
from generators.typst.overlays import PlacedOverlay
from generators.typst.typeset import TypesetResult, typeset_case
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
    result: TypesetResult
    page: dict
    stage_one: dict
    spec: PageSpec
    page_count: int
    page_mm: tuple[float, float]
    page_pt: tuple[float, float]
    invariant_errors: list[str]
    layout_errors: list[str]
    differences: list[str]
    density_errors: list[str] | None
    overlay_errors: list[str]

    @property
    def translation(self) -> Translation:
        return self.result.translation

    @property
    def geometry(self) -> dict:
        return self.result.geometry


def first_case(layout_path: Path, layout_id: str) -> tuple[str, dict]:
    """Return a layout's first case by case id, with its entry."""
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / layout_path.name)
    case_id = next(str(c) for c in sorted(entries, key=str) if entries[c]["layout"] == layout_id)
    return case_id, {**entries[case_id], "case_id": case_id}


def typeset_entry(
    layout_path: Path, layout_id: str, case_id: str, entry: dict, mode: str, workdir: Path
) -> Typeset:
    """Typeset one entry in `mode`, read it back and run every check on it."""
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    layout = load_layout_registry(layout_path)[layout_id]
    spec = layout_page(layout, layout_path=str(layout_path), layout_id=layout_id)
    distractors = mode == "distractor"
    stage_one: dict = {}
    _RENDERERS[layout_path.stem](entry, layout, distractors=distractors, geometry_out=stage_one)
    doc_type = _DOC_TYPES[layout_path.stem]
    result = typeset_case(
        entry,
        layout,
        layout_id=layout_id,
        layout_path=layout_path,
        doc_type=doc_type,
        distractors=distractors,
        config=config,
        workdir=workdir,
    )
    geometry = result.geometry
    where = page_where(case_id, layout_id, f"typeset {mode}")
    context = PageContext(f"{case_id}.png", doc_type, layout_id, f"typeset_{mode}", "none", True)
    page = build_page(geometry, context=context, config=parsing)
    document = pypdfium2.PdfDocument(result.pdf)
    try:
        page_count = len(document)
        width_pt, height_pt = document[0].get_size()
    finally:
        document.close()
    density_errors = None
    if distractors and any(drawn for _, drawn in render_modes(layout)):
        density = DensityCheck(
            load_distractor_config().density[doc_type], doc_type=doc_type, config_path=DISTRACTORS_PATH
        )
        density(case_id, layout_id, geometry)
        density_errors = density.errors
    return Typeset(
        case_id=case_id,
        result=result,
        page=page,
        stage_one=stage_one,
        spec=spec,
        page_count=page_count,
        page_mm=(width_pt * 25.4 / 72, height_pt * 25.4 / 72),
        page_pt=(width_pt, height_pt),
        invariant_errors=check_page(page, geometry, where=where, config=parsing),
        layout_errors=layout_errors(geometry, where=where),
        differences=stage_one_differences(page, stage_one, context=context),
        density_errors=density_errors,
        overlay_errors=overlay_errors(
            result,
            case_id=case_id,
            layout_id=layout_id,
            layout_path=layout_path,
            doc_type=doc_type,
            config=config,
            workdir=workdir / "bare",
        ),
    )


@functools.cache
def typeset(layout_path: Path, layout_id: str, mode: str, workdir: Path) -> Typeset:
    """Typeset a layout's first case in `mode`, read it back and run every check on it."""
    case_id, entry = first_case(layout_path, layout_id)
    return typeset_entry(layout_path, layout_id, case_id, entry, mode, workdir)


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


_EVERY_LAYOUT = pytest.mark.parametrize(
    ("layout_path", "layout_id"), _layouts(), ids=lambda value: getattr(value, "stem", value)
)


# --- line roles: stage one's lines, role for role ---------------------------------------------------------


def _lines_by_owner(ink: list) -> dict[tuple, list[tuple[str, str]]]:
    """Each block's (and cell's) text lines in order: role and whitespace-collapsed text."""
    owners: dict[tuple, list[tuple[str, str]]] = {}
    for record in ink:
        if not record.mark:
            owners.setdefault((record.block, record.cell), []).append(
                (record.role, " ".join(record.text.split()))
            )
    return owners


def _with_typeset_initials(ink: list, blocks: list) -> list:
    """Stage one's ink, plus the line the typeset page's text layer holds for each logo's initials.

    Stage one records a logo as a code mark whose payload is its initials; the typeset page sets the
    initials as text, so the PDF holds them as a line of the logo's block, in the mark's role.
    """
    logos = {block.id for block in blocks if block.kind == "logo"}
    initials = [record._replace(mark=False) for record in ink if record.mark and record.block in logos]
    return [*ink, *initials]


@pytest.mark.slow
@pytest.mark.parametrize("mode", _MODES)
@_EVERY_LAYOUT
def test_every_page_has_stage_ones_lines_role_for_role(
    layout_path: Path, layout_id: str, mode: str, workroot: Path
) -> None:
    result = typeset(layout_path, layout_id, mode, workroot / f"{layout_id}_{mode}")
    typeset_ink = result.geometry["ink"]
    stage_one_ink = _with_typeset_initials(result.stage_one["ink"], result.stage_one["blocks"])
    # The density check counts roles: every role, and every mark, as often as stage one drew it ...
    assert Counter((r.role, r.mark) for r in typeset_ink) == Counter(
        (r.role, r.mark) for r in stage_one_ink
    )
    # ... because each line is the one stage one drew, with its role, in the same block and cell ...
    assert _lines_by_owner(typeset_ink) == _lines_by_owner(stage_one_ink)
    # ... and each mark is stage one's, in the same block.
    marks = [(r.block, r.text) for r in typeset_ink if r.mark]
    assert Counter(marks) == Counter((r.block, r.text) for r in stage_one_ink if r.mark)


# --- the distractor checks on Typst's geometry (Ruling P2) ------------------------------------------------


@pytest.mark.slow
@_EVERY_LAYOUT
def test_every_distractor_page_meets_its_density_bounds_on_the_typeset_geometry(
    layout_path: Path, layout_id: str, workroot: Path
) -> None:
    result = typeset(layout_path, layout_id, "distractor", workroot / f"{layout_id}_distractor")
    # Every layout carries a distractor provider, so every distractor page is bounded.
    assert result.density_errors == []


@pytest.mark.slow
@_EVERY_LAYOUT
def test_every_overlay_passes_the_placement_checks_on_the_typeset_geometry(
    layout_path: Path, layout_id: str, workroot: Path
) -> None:
    result = typeset(layout_path, layout_id, "distractor", workroot / f"{layout_id}_distractor")
    assert result.overlay_errors == []


@pytest.mark.slow
@pytest.mark.parametrize("mode", _MODES)
@_EVERY_LAYOUT
def test_every_overlay_stage_one_draws_is_placed(
    layout_path: Path, layout_id: str, mode: str, workroot: Path
) -> None:
    result = typeset(layout_path, layout_id, mode, workroot / f"{layout_id}_{mode}")
    # Stage one opens an overlay block only for a block it draws.
    drawn = {block.id for block in result.stage_one["blocks"] if block.kind.startswith(OVERLAY_PREFIX)}
    assert {overlay.block for overlay in result.translation.overlays} == drawn
    if mode == "plain":
        assert drawn == set()
    # Each placed site that records ink (a tint records none) is a block of the typeset page, its text
    # stage one's (the comparison holds it).
    inked = {record.block for record in result.stage_one["ink"] if record.block in drawn}
    dets = {det["anno_id"] for det in result.page["layout_dets"]}
    assert inked <= dets


def test_some_first_case_draws_every_overlay_type(workroot: Path) -> None:
    # The checks above run on real overlays: between them, the first cases draw a highlight, ink marks,
    # handwriting and a paid stamp (each type's own block kind).
    kinds = {
        block.kind.removeprefix(OVERLAY_PREFIX)
        for layout_path, layout_id in _layouts()
        for block in _stage_one_blocks(layout_path, layout_id)
        if block.kind.startswith(OVERLAY_PREFIX)
    }
    assert kinds == {"highlight", "ink_marks", "handwriting", "stamp"}


def _stage_one_blocks(layout_path: Path, layout_id: str) -> list:
    """The blocks stage one's distractor render of a layout's first case opened."""
    case_id, entry = first_case(layout_path, layout_id)
    layout = load_layout_registry(layout_path)[layout_id]
    geometry: dict = {}
    _RENDERERS[layout_path.stem](entry, layout, distractors=True, geometry_out=geometry)
    return list(geometry["blocks"])


def _whole_pixels(result: Typeset, overlay: PlacedOverlay) -> tuple[int, int]:
    """Where Typst placed an overlay's site on the PNG: on whole pixels, as every sheet's overlay lies."""
    left, top = result.geometry["measured"].boxes[overlay.block][:2]
    assert (left, top) == pytest.approx((round(left), round(top)), abs=1e-6)
    return round(left), round(top)


@pytest.mark.slow
def test_a_highlight_tints_the_paper_as_stage_one_does_and_lies_beneath_the_text(workroot: Path) -> None:
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    tinted = 0
    for layout_path, layout_id in _layouts():
        result = typeset(layout_path, layout_id, "distractor", workroot / f"{layout_id}_distractor")
        tints = [overlay for overlay in result.translation.overlays if overlay.under is not None]
        if not tints:
            continue
        bare = compile_page(
            result.translation.bare_source, workdir=workroot / f"{layout_id}_bare", config=config
        )
        before = np.asarray(rasterise(bare.pdf, dpi=config.raster_dpi), dtype=np.int32)
        after = np.asarray(result.result.image, dtype=np.int32)
        # Pixels near a pen stroke drawn over the tint are left out: the stroke's antialiasing reaches them.
        strokes = Image.new("L", result.result.image.size, 0)
        for overlay in result.translation.overlays:
            if overlay.over is not None:
                alpha = overlay.over.getchannel("A")
                strokes.paste(alpha, _whole_pixels(result, overlay), alpha)
        clear = np.asarray(strokes.filter(ImageFilter.MaxFilter(7))) == 0
        for overlay in tints:
            tinted += 1
            placed = Image.new("L", result.result.image.size, 0)
            assert overlay.under is not None
            placed.paste(overlay.under.getchannel("A"), _whole_pixels(result, overlay))
            mask = np.asarray(placed)
            # A band's soft edge is resampled as the PDF renderer places the image; its flat inside is not.
            flat = np.asarray(placed.filter(ImageFilter.MaxFilter(5))) == np.asarray(
                placed.filter(ImageFilter.MinFilter(5))
            )
            weight = mask[..., None] / 255
            colour = np.array(_tint_colour(overlay))
            paper = (before == 255).all(axis=2) & (mask > 0) & flat & clear
            assert paper.sum() > 0, layout_id
            # On white paper the tint is stage one's multiply exactly: colour * w + white * (1 - w).
            expected = colour * weight + 255 * (1 - weight)
            assert np.abs(after - expected)[paper].max() <= 1, layout_id
            # No shading painted over the tint hides it: every light pixel under the mask takes the tint.
            light = (before.sum(axis=2) > 3 * 128) & (mask > 0) & flat & clear
            assert (np.abs(after - before).sum(axis=2)[light] > 0).all(), layout_id
            # The highlighted text stays in the text layer: lines of the page lie under each band.
            for band in overlay.bands:
                dx = result.geometry["measured"].boxes[overlay.block][0] - overlay.site[0]
                dy = result.geometry["measured"].boxes[overlay.block][1] - overlay.site[1]
                inside = [
                    record
                    for record in result.geometry["ink"]
                    if not record.mark
                    and record.box[1] >= band[1] + dy - 2
                    and record.box[3] <= band[3] + dy + 2
                    and record.box[0] >= band[0] + dx - 2
                ]
                assert inside, (layout_id, band)
    assert tinted > 0


def _tint_colour(overlay) -> tuple[int, int, int]:  # noqa: ANN001
    """The colour a placed tint image carries (every pixel has it; only the alpha varies)."""
    colours = {pixel[:3] for pixel in overlay.under.getdata()}
    assert len(colours) == 1
    return colours.pop()


# --- Review Focus 3: every poly inside its page, and the PNG the page scaled, rounded up ---------------


@pytest.mark.slow
@pytest.mark.parametrize("mode", _MODES)
@_EVERY_LAYOUT
def test_every_poly_lies_inside_its_page_and_the_png_is_the_page_scaled(
    layout_path: Path, layout_id: str, mode: str, workroot: Path
) -> None:
    # Review Focus 3 asks it of every receipt layout, whose height is content-driven; every layout keeps it.
    result = typeset(layout_path, layout_id, mode, workroot / f"{layout_id}_{mode}")
    dpi = load_typeset_config(TYPESET_CONFIG_PATH).raster_dpi
    width, height = result.result.image.size
    assert (width, height) == tuple(math.ceil(side * dpi / 72) for side in result.page_pt)
    assert (result.geometry["width"], result.geometry["height"]) == (width, height)
    for det in result.page["layout_dets"]:
        xs, ys = det["poly"][0::2], det["poly"][1::2]
        assert 0 <= min(xs) and max(xs) <= width, det
        assert 0 <= min(ys) and max(ys) <= height, det


def test_review_focus_3_covers_every_receipt_layout() -> None:
    receipts = [layout_id for path, layout_id in _layouts() if path.stem == "receipts"]
    assert len(receipts) == 6


# --- Review Focus 4: one page per layout compiled in two separate processes is byte-identical ----------

# Typeset every layout's first case with distractors (overlays included) in a fresh process, writing each
# PDF to argv[1]/<layout_id>.pdf.
_TYPESET_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "from generators.loader import load_ground_truth, load_layout_registry\n"
    "from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config\n"
    "from generators.typst.typeset import typeset_case\n"
    "out = Path(sys.argv[1])\n"
    "config = load_typeset_config(TYPESET_CONFIG_PATH)\n"
    f"DOC_TYPES = {_DOC_TYPES!r}\n"
    "for path in sorted(Path('config/layouts').glob('*.yml')):\n"
    "    entries = load_ground_truth(Path('ground_truth') / path.name)\n"
    "    for layout_id, layout in load_layout_registry(path).items():\n"
    "        cases = [str(c) for c in sorted(entries, key=str) if entries[c]['layout'] == layout_id]\n"
    "        case_id = cases[0]\n"
    "        result = typeset_case({**entries[case_id], 'case_id': case_id}, layout, layout_id=layout_id,\n"
    "            layout_path=path.resolve(), doc_type=DOC_TYPES[path.stem], distractors=True, config=config,\n"
    "            workdir=out / layout_id)\n"
    "        (out / f'{layout_id}.pdf').write_bytes(result.pdf)\n"
)


def _typeset_in_new_process(out: Path) -> dict[str, bytes]:
    """Typeset one page per layout in a separate Python process; return each layout's PDF."""
    out.mkdir(parents=True)
    done = subprocess.run(
        [sys.executable, "-c", _TYPESET_SCRIPT, str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return {path.stem: path.read_bytes() for path in out.glob("*.pdf")}


@pytest.mark.slow
def test_one_page_per_layout_typesets_byte_identically_in_two_separate_processes(tmp_path: Path) -> None:
    first = _typeset_in_new_process(tmp_path / "first")
    second = _typeset_in_new_process(tmp_path / "second")
    assert sorted(first) == sorted(layout_id for _, layout_id in _layouts())
    assert [layout_id for layout_id in first if first[layout_id] != second[layout_id]] == []


# --- a cell stage one drew out of top-to-bottom order -----------------------------------------------------


def _out_of_order_cell() -> tuple[Path, str, str, dict]:
    """A statement case one of whose table cells stage one drew a line of below a line it drew later."""
    layout_path = REPO_ROOT / "config" / "layouts" / "bank_statements.yml"
    layouts = load_layout_registry(layout_path)
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / "bank_statements.yml")
    for case_id in sorted(entries, key=str):
        entry = {**entries[case_id], "case_id": str(case_id)}
        geometry: dict = {}
        _RENDERERS["bank_statements"](
            entry, layouts[entry["layout"]], distractors=False, geometry_out=geometry
        )
        cells: dict = {}
        for record in geometry["ink"]:
            if record.cell is not None and not record.mark:
                cells.setdefault((record.block, record.cell), []).append(record.box[1])
        if any(tops != sorted(tops) for tops in cells.values()):
            return layout_path, str(entry["layout"]), str(case_id), entry
    raise AssertionError("no statement case draws a cell's lines out of top-to-bottom order")


@pytest.mark.slow
def test_a_cell_whose_lines_stage_one_drew_out_of_order_typesets_as_stage_one_draws_it(
    tmp_path: Path,
) -> None:
    # A wrapped description whose last line stage one drew below the description's grey sub-line: the
    # typeset cell stacks the lines where stage one drew them, and its text keeps stage one's order.
    layout_path, layout_id, case_id, entry = _out_of_order_cell()
    result = typeset_entry(layout_path, layout_id, case_id, entry, "plain", tmp_path)
    assert result.invariant_errors == []
    assert result.layout_errors == []
    assert result.differences == []


# --- field boxes, built from the typeset lines ---------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("mode", _MODES)
def test_every_scored_field_has_a_typeset_box_where_stage_one_drew_it(mode: str, workroot: Path) -> None:
    layout_file, layout_id = next(gate for gate in GATE_LAYOUTS if "invoices" in gate[0])
    result = typeset(REPO_ROOT / layout_file, layout_id, mode, workroot / f"{layout_id}_{mode}")
    boxes, theirs = result.geometry["boxes"], result.stage_one["boxes"]
    assert sorted(boxes) == sorted(theirs)
    width, height = result.geometry["width"], result.geometry["height"]
    stage_width, stage_height = result.stage_one["width"], result.stage_one["height"]
    dpi = load_typeset_config(TYPESET_CONFIG_PATH).raster_dpi
    for field, box in boxes.items():
        left, top, right, bottom = theirs[field]
        area = (left * stage_width, top * stage_height, right * stage_width, bottom * stage_height)
        # Stage one's field box is the slot it drew in; the typeset one is the ink of the lines drawn in
        # it, so it is held to the union of stage one's scored ink boxes there, placed on the typeset page.
        inked = [
            record.box
            for record in result.translation.ink
            if record.role == "scored"
            and not record.mark
            and min(record.box[2], area[2]) > max(record.box[0], area[0])
            and min(record.box[3], area[3]) > max(record.box[1], area[1])
        ]
        assert inked, field
        union = (
            min(b[0] for b in inked),
            min(b[1] for b in inked),
            max(b[2] for b in inked),
            max(b[3] for b in inked),
        )
        placed = result.translation.to_points(union)
        mine = (box[0] * width, box[1] * height, box[2] * width, box[3] * height)
        # The typeset lines' boxes are the PDF's glyph boxes, a few pixels from stage one's ink boxes.
        assert max(abs(a - b * dpi / 72) for a, b in zip(mine, placed, strict=True)) <= 4, field


# --- the overlay checks refuse a misplaced overlay, four-part ---------------------------------------------


def _with_overlay_at(result: Typeset, block: int, origin: tuple[float, float]) -> TypesetResult:
    """The typeset page with one overlay's site moved to `origin` (its image with it)."""
    measured = result.geometry["measured"]
    left, top, right, bottom = measured.boxes[block]
    moved = (origin[0], origin[1], origin[0] + right - left, origin[1] + bottom - top)
    geometry = {**result.geometry, "measured": measured._replace(boxes={**measured.boxes, block: moved})}
    return dataclasses.replace(result.result, geometry=geometry)


def _marked_statement(workroot: Path) -> tuple[Typeset, Path, str]:
    """The first statement layout whose first case draws an opaque mark and a highlight (distractors)."""
    for layout_path, layout_id in _layouts():
        if layout_path.stem != "bank_statements":
            continue
        result = typeset(layout_path, layout_id, "distractor", workroot / f"{layout_id}_distractor")
        overlays = result.translation.overlays
        if any(o.over is not None for o in overlays) and any(o.under is not None for o in overlays):
            return result, layout_path, layout_id
    raise AssertionError("no statement's first case draws an opaque overlay")


def _ink_corner(overlay: PlacedOverlay) -> tuple[int, int]:
    """Where an overlay's opaque ink starts inside its site image."""
    assert overlay.over is not None
    extent = overlay.over.getchannel("A").getbbox()
    assert extent is not None
    return extent[0], extent[1]


def _overlay_errors(
    result: Typeset, moved: TypesetResult, layout_path: Path, layout_id: str, tmp: Path
) -> list[str]:
    return overlay_errors(
        moved,
        case_id=result.case_id,
        layout_id=layout_id,
        layout_path=layout_path,
        doc_type="bank_statement",
        config=load_typeset_config(TYPESET_CONFIG_PATH),
        workdir=tmp,
    )


@pytest.mark.slow
def test_an_overlay_moved_onto_printed_ink_is_refused_four_part(workroot: Path, tmp_path: Path) -> None:
    result, layout_path, layout_id = _marked_statement(workroot)
    overlay = next(o for o in result.translation.overlays if o.over is not None)
    ink_left, ink_top = _ink_corner(overlay)
    line = next(
        r
        for r in result.geometry["ink"]
        if not r.mark and r.role == "text" and r.block not in {o.block for o in result.translation.overlays}
    )
    moved = _with_overlay_at(result, overlay.block, (line.box[0] - ink_left + 2, line.box[1] - ink_top + 2))
    errors = _overlay_errors(result, moved, layout_path, layout_id, tmp_path)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "protected box" in errors[0] and overlay.path in errors[0]


@pytest.mark.slow
def test_an_overlay_moved_into_the_page_inset_is_refused_four_part(workroot: Path, tmp_path: Path) -> None:
    result, layout_path, layout_id = _marked_statement(workroot)
    overlay = next(o for o in result.translation.overlays if o.over is not None)
    ink_left, ink_top = _ink_corner(overlay)
    moved = _with_overlay_at(result, overlay.block, (1 - ink_left, 1 - ink_top))
    errors = _overlay_errors(result, moved, layout_path, layout_id, tmp_path)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "page_inset_px" in errors[0]


@pytest.mark.slow
def test_a_highlight_left_illegible_on_the_typeset_page_is_refused_four_part(
    workroot: Path, tmp_path: Path
) -> None:
    result, layout_path, layout_id = _marked_statement(workroot)
    overlay = next(o for o in result.translation.overlays if o.under is not None)
    # The page as an illegible tint would leave it: every band painted solid black over its text.
    page = result.result.image.copy()
    dx = result.geometry["measured"].boxes[overlay.block][0] - overlay.site[0]
    dy = result.geometry["measured"].boxes[overlay.block][1] - overlay.site[1]
    for band in overlay.bands:
        page.paste(
            (0, 0, 0), (round(band[0] + dx), round(band[1] + dy), round(band[2] + dx), round(band[3] + dy))
        )
    darkened = dataclasses.replace(result.result, image=page)
    errors = _overlay_errors(result, darkened, layout_path, layout_id, tmp_path)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "contrast" in errors[0] and overlay.path in errors[0]
