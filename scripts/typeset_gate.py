"""The typesetting feasibility gate: the first case of each gate layout, typeset as a native PDF.

For each of the three gate layouts (`GATE_LAYOUTS`), plain and with distractors, the gate translates the
layout's first case to Typst, compiles it twice (the byte check) and once more with ligatures switched on
(the ligature probe), reads the page back as stage one's records, and runs every check:

- the box query: every translated block has exactly one record on the compiled page;
- stage one's six invariants (`check_page`) and the layout checks (`layout_errors`);
- the byte check: two compiles of one source give the same PDF;
- the evaluator: the page's ground truth, written as markdown, scored as its own prediction.

Overlays (handwriting, ink marks, highlights, the paid stamp) are placed only by a later task, so a
distractor page is typeset without them; each page's report says so, with the number of overlay sites
stage one draws for the case. Files go under `<output_root>/typeset_gate/`: per page its PDF, PNG,
Typst source and the stage-one PNG it pairs with, plus `gate.json` (one OmniDocBench page each) and
`gate_report.json`. Nothing is written inside the repository.

Usage:
    python -m scripts.typeset_gate
"""

import json
import math
import re
import shutil
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pypdfium2

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.loader import load_ground_truth, load_layout_registry  # noqa: E402
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config  # noqa: E402
from generators.parsing.omnidocbench import (  # noqa: E402
    OVERLAY_PREFIX,
    PageContext,
    build_page,
    check_page,
    page_where,
)
from generators.pipeline import _RENDERERS  # noqa: E402
from generators.snapshot.config import load_snapshot_config  # noqa: E402
from generators.typst.compile import TypstError, compile_page, rasterise  # noqa: E402
from generators.typst.config import (  # noqa: E402
    TYPESET_CONFIG_PATH,
    TypesetConfig,
    layout_page,
    load_typeset_config,
)
from generators.typst.readback import ReadbackError, layout_errors, page_geometry  # noqa: E402
from generators.typst.translate import GATE_LAYOUTS, Translation, translate_page  # noqa: E402
from scripts.omnidocbench_eval import ensure_checkout, result_path, run_end2end  # noqa: E402
from tests.parsing.gt_to_markdown import gt_to_markdown  # noqa: E402

GATE_DIR = "typeset_gate"
GATE_JSON = "gate.json"
REPORT_JSON = "gate_report.json"
# The gate layouts' file stems, with the document type each draws.
_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}
_MODES = ("plain", "distractor")
# The line of a page's source after which the ligature probe switches ligatures on.
_SHOW_LINE = "#show: typeset"
_LIGATURES_ON = "#set text(ligatures: true)"
_CANDIDATE = re.compile(r"ffi|ffl|ff|fi|fl")


class GateError(RuntimeError):
    """Raised when the gate cannot run: a missing output root, or a source it cannot probe."""


@dataclass
class PageReport:
    """Every check on one typeset page.

    Attributes:
        case_id: The case typeset.
        layout_id: Its layout.
        mode: `plain` or `distractor`.
        pdf: The PDF's file name under the gate directory.
        png: The PNG's file name (also the page's `image_path`).
        stage_one_png: The file name of stage one's PNG of the same case and mode.
        typst: The Typst source's file name.
        page_count: The PDF's page count.
        blocks: The blocks queried back from the PDF.
        cells: The table cells queried back.
        box_query_ok: Whether the page compiled and each translated block has its one record.
        box_query_error: The refusal when it did not.
        invariant_errors: Stage one's `check_page` diagnostics.
        layout_errors: `layout_errors` diagnostics.
        bytes_identical: Whether two compiles of one source gave the same PDF.
        ligature_invariant_1_holds: Whether, compiled with ligatures on, every word still has an owner.
        ligature_other_errors: How many other invariant diagnostics the ligature compile raised.
        ligatures_in_text_layer: The ligature characters the ligature compile's text layer holds.
        ligature_candidates: How many ff, fi, fl, ffi or ffl sequences the page's text holds, the places
            a ligature could form.
        ligature_pdf_differs: Whether the ligature compile's PDF differs from the plain compile's, which
            shows the switch took effect.
        overlays_placed: Whether the page carries its overlays (always False until they are placed).
        overlay_sites_in_stage_one: The overlay sites stage one draws for this case and mode.
        scores: The evaluator's scores for the page's ground truth as its own prediction.
        reading_order_gt: The evaluator's ground-truth reading order.
        reading_order_pred: The evaluator's predicted reading order.
    """

    case_id: str
    layout_id: str
    mode: str
    pdf: str
    png: str
    stage_one_png: str
    typst: str
    page_count: int = 0
    blocks: int = 0
    cells: int = 0
    box_query_ok: bool = False
    box_query_error: str | None = None
    invariant_errors: list[str] = field(default_factory=list)
    layout_errors: list[str] = field(default_factory=list)
    bytes_identical: bool = False
    ligature_invariant_1_holds: bool | None = None
    ligature_other_errors: int | None = None
    ligatures_in_text_layer: list[str] = field(default_factory=list)
    ligature_candidates: int = 0
    ligature_pdf_differs: bool | None = None
    overlays_placed: bool = False
    overlay_sites_in_stage_one: int = 0
    scores: dict[str, float | None] | None = None
    reading_order_gt: list[int] | None = None
    reading_order_pred: list[int] | None = None

    @property
    def passed(self) -> bool:
        """Whether the box query, invariants, layout checks and byte check all passed."""
        return (
            self.box_query_ok
            and self.page_count == 1
            and not self.invariant_errors
            and not self.layout_errors
            and self.bytes_identical
        )


@dataclass
class GateReport:
    """The gate's outcome.

    Attributes:
        directory: `<output_root>/typeset_gate/`.
        pages: One report per page, plain then distractor for each gate layout.
    """

    directory: Path
    pages: list[PageReport]

    @property
    def passed(self) -> bool:
        """Whether every page passed every check."""
        return all(page.passed for page in self.pages)

    def render(self) -> str:
        """Return the report as text for the project owner."""
        lines = [f"Typesetting feasibility gate: {self.directory}", ""]
        for page in self.pages:
            lines.extend(_render_page(page))
            lines.append("")
        lines.append(f"Gate verdict: {'every page passes every check' if self.passed else 'FAILURES'}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return the report as JSON-ready data (NaN scores as null)."""
        return {
            "directory": str(self.directory),
            "passed": self.passed,
            "pages": [asdict(page) | {"passed": page.passed} for page in self.pages],
        }


def _render_page(page: PageReport) -> list[str]:
    """Write one page's report as text lines."""
    verdict = "PASS" if page.passed else "FAIL"
    scores = page.scores or {}
    shown = ", ".join(f"{key}={value}" for key, value in scores.items()) or "not scored"
    return [
        f"{page.case_id} / {page.layout_id} ({page.mode}): {verdict}",
        f"  files: {page.pdf}, {page.png} (stage one: {page.stage_one_png})",
        f"  page count: {page.page_count}; blocks queried: {page.blocks}; cells: {page.cells}",
        f"  box query: {'ok' if page.box_query_ok else 'FAILED ' + str(page.box_query_error)}",
        f"  six invariants: {'ok' if not page.invariant_errors else str(len(page.invariant_errors))} "
        f"{'' if not page.invariant_errors else 'FAILED'}".rstrip(),
        f"  layout checks: {'ok' if not page.layout_errors else str(len(page.layout_errors)) + ' FAILED'}",
        f"  byte check (two compiles identical): {'ok' if page.bytes_identical else 'FAILED'}",
        f"  ligature probe: invariant 1 holds={page.ligature_invariant_1_holds}; "
        f"other invariant errors={page.ligature_other_errors}; "
        f"ligatures in text layer={page.ligatures_in_text_layer}; candidate sequences={page.ligature_candidates}; "
        f"ligature PDF differs from plain={page.ligature_pdf_differs}",
        f"  overlays placed: {page.overlays_placed} (stage one draws {page.overlay_sites_in_stage_one} "
        "overlay site(s) for this case and mode; not yet covered)",
        f"  evaluator on the perfect prediction: {shown}",
        f"  reading order gt={page.reading_order_gt} pred={page.reading_order_pred}",
    ]


def _refusal(title: str, *, what: str, where: str, expected: str, recover: str) -> GateError:
    """Build a four-part refusal."""
    return GateError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _first_case(layout_file: str, layout_id: str) -> tuple[str, dict]:
    """Return the layout's first case by case id, with its entry."""
    stem = Path(layout_file).stem
    ground_truth = REPO_ROOT / "ground_truth" / f"{stem}.yml"
    entries = load_ground_truth(ground_truth)
    case_id = next((str(c) for c in sorted(entries, key=str) if entries[c]["layout"] == layout_id), None)
    if case_id is None:
        raise _refusal(
            "A gate layout has no case.",
            what=f"no entry of {ground_truth} uses the layout {layout_id!r}.",
            where=f"{ground_truth}, entries' layout key",
            expected="at least one entry naming each gate layout, e.g. layout: " + layout_id,
            recover="add a case for the layout, or change GATE_LAYOUTS in generators/typst/translate.py.",
        )
    return case_id, {**entries[case_id], "case_id": case_id}


def _ligatures(texts: list[str]) -> list[str]:
    """Return the distinct ligature characters among `texts`, sorted."""
    found = {c for text in texts for c in text if "LIGATURE" in unicodedata.name(c, "")}
    return sorted(found)


def _ligature_probe(
    source: str,
    plain_pdf: bytes,
    translation: Translation,
    report: PageReport,
    *,
    work: Path,
    config: TypesetConfig,
) -> None:
    """Compile the page once more with ligatures on and record what that does."""
    if _SHOW_LINE not in source.splitlines():
        raise _refusal(
            "The ligature probe cannot find where to switch ligatures on.",
            what=f"the page's source has no line {_SHOW_LINE!r}.",
            where=f"the source translate_page returned for {report.case_id} / {report.layout_id}",
            expected=f"a line {_SHOW_LINE!r}, after which the probe appends {_LIGATURES_ON!r}.",
            recover="update _SHOW_LINE in scripts/typeset_gate.py to the line translate_page now writes.",
        )
    probed = source.replace(_SHOW_LINE, f"{_SHOW_LINE}\n{_LIGATURES_ON}", 1)
    compiled = compile_page(probed, workdir=work, config=config)
    geometry = page_geometry(compiled, dpi=config.raster_dpi, translation=translation)
    owned = all(record.block is not None for record in geometry["ink"])
    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    context = PageContext(report.png, "n/a", report.layout_id, "typeset_ligatures", "none", True)
    page = build_page(geometry, context=context, config=parsing)
    errors = check_page(page, geometry, where="ligature probe", config=parsing)
    owner_errors = [e for e in errors if e.startswith("A line of ink belongs to no block.")]
    report.ligature_invariant_1_holds = owned and not owner_errors
    report.ligature_other_errors = len(errors) - len(owner_errors)
    texts = [record.text for record in geometry["ink"]]
    report.ligatures_in_text_layer = _ligatures(texts)
    report.ligature_candidates = sum(len(_CANDIDATE.findall(text)) for text in texts)
    report.ligature_pdf_differs = compiled.pdf != plain_pdf


def _typeset_page(
    layout_file: str, layout_id: str, mode: str, *, directory: Path, config: TypesetConfig
) -> tuple[PageReport, dict | None]:
    """Typeset, read back and check one page; return its report and its OmniDocBench page."""
    doc_type = _DOC_TYPES[Path(layout_file).stem]
    case_id, entry = _first_case(layout_file, layout_id)
    stem = f"{case_id}_{doc_type}_{mode}"
    report = PageReport(
        case_id=case_id,
        layout_id=layout_id,
        mode=mode,
        pdf=f"{stem}.pdf",
        png=f"{stem}.png",
        stage_one_png=f"{stem}_stage1.png",
        typst=f"{stem}.typ",
    )
    layout_path = REPO_ROOT / layout_file
    layout = load_layout_registry(layout_path)[layout_id]
    distractors = mode == "distractor"

    stage_one: dict = {}
    image = _RENDERERS[Path(layout_file).stem](
        entry, layout, distractors=distractors, geometry_out=stage_one
    )
    image.save(directory / report.stage_one_png)
    report.overlay_sites_in_stage_one = sum(
        1 for block in stage_one["blocks"] if block.kind.startswith(OVERLAY_PREFIX)
    )

    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        page=layout_page(layout, layout_path=str(layout_path), layout_id=layout_id),
        distractors=distractors,
        config=config,
        overlay_dir=directory / "overlays" / stem,
    )
    (directory / report.typst).write_text(translation.source)
    work = directory / "work" / stem
    try:
        first = compile_page(translation.source, workdir=work / "first", config=config)
        second = compile_page(translation.source, workdir=work / "second", config=config)
        geometry = page_geometry(first, dpi=config.raster_dpi, translation=translation)
    except (TypstError, ReadbackError) as err:
        report.box_query_error = str(err)
        return report, None
    report.box_query_ok = True
    report.blocks, report.cells = len(first.blocks), len(first.cells)
    report.bytes_identical = first.pdf == second.pdf
    document = pypdfium2.PdfDocument(first.pdf)
    try:
        report.page_count = len(document)
    finally:
        document.close()
    (directory / report.pdf).write_bytes(first.pdf)
    rasterise(first.pdf, dpi=config.raster_dpi).save(directory / report.png)

    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    where = page_where(case_id, layout_id, f"typeset {mode}")
    context = PageContext(report.png, doc_type, layout_id, f"typeset_{mode}", "none", True)
    page = build_page(geometry, context=context, config=parsing)
    report.invariant_errors = check_page(page, geometry, where=where, config=parsing)
    report.layout_errors = layout_errors(geometry, where=where)
    _ligature_probe(
        translation.source, first.pdf, translation, report, work=work / "ligatures", config=config
    )
    return report, page


def _evaluate(report: PageReport, page: dict, *, directory: Path, checkout: Path) -> None:
    """Score a page's ground truth, written as markdown, as its own prediction."""
    stem = Path(report.png).stem
    scores_dir = directory / "scores" / stem
    predictions = scores_dir / "predictions"
    predictions.mkdir(parents=True)
    ground_truth = scores_dir / "gt.json"
    ground_truth.write_text(json.dumps([page], indent=2))
    (predictions / f"{stem}.md").write_text(gt_to_markdown(page))
    scores = run_end2end(ground_truth, predictions, checkout=checkout, work_dir=scores_dir)
    report.scores = {key: None if math.isnan(value) else value for key, value in scores.items()}
    reading = result_path(predictions, work_dir=scores_dir).with_name(
        f"{predictions.name}_quick_match_reading_order_result.json"
    )
    for sample in json.loads(reading.read_text()):
        report.reading_order_gt = sample["gt"]
        report.reading_order_pred = sample["pred"]


def run_gate(*, output_root: Path) -> GateReport:
    """Typeset the gate layouts' first cases, run every check, and write the files.

    Args:
        output_root: The directory that receives `typeset_gate/` (`eval_set.output_root`); it must exist.

    Returns:
        The report: per page the box query, six invariants, layout checks, byte check, ligature probe,
        overlay status, page count and the evaluator's scores.

    Raises:
        GateError: `output_root` does not exist, a gate layout has no case, or a source cannot be probed.
        EvaluatorError: The evaluator's env or checkout is missing, or its run failed.
    """
    if not output_root.is_dir():
        raise _refusal(
            "The gate's output root does not exist.",
            what=f"{output_root.resolve()} is not a directory.",
            where="the output_root argument (eval_set.output_root in config/generation_config.yml)",
            expected="an existing directory outside the repository, e.g. ../evaluation_data",
            recover=f"create {output_root.resolve()}, or point eval_set.output_root at a directory that exists.",
        )
    config = load_typeset_config(TYPESET_CONFIG_PATH)
    checkout_root = (
        load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml").output_root / "tools"
    )
    checkout = ensure_checkout(load_parsing_config(PARSING_CONFIG_PATH), root=checkout_root)
    directory = output_root / GATE_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir()

    reports: list[PageReport] = []
    pages: list[dict] = []
    for layout_file, layout_id in GATE_LAYOUTS:
        for mode in _MODES:
            report, page = _typeset_page(layout_file, layout_id, mode, directory=directory, config=config)
            if page is not None:
                pages.append(page)
                _evaluate(report, page, directory=directory, checkout=checkout)
            reports.append(report)
    (directory / GATE_JSON).write_text(json.dumps(pages, indent=2, ensure_ascii=False))
    gate = GateReport(directory=directory, pages=reports)
    (directory / REPORT_JSON).write_text(json.dumps(gate.to_dict(), indent=2, ensure_ascii=False))
    return gate


def main() -> int:
    """Run the gate over the configured output root and print its report."""
    snapshot = load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml")
    gate = run_gate(output_root=snapshot.output_root)
    print(gate.render())
    return 0 if gate.passed else 1


if __name__ == "__main__":
    sys.exit(main())
