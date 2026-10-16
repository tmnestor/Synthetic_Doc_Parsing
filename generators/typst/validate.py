"""`validate`'s typeset pass: every case typeset, plain and with distractors, and checked as stage one's is.

For each case of a document type, in each mode stage one's own pass renders it (plain, and with
distractors where the layout carries a distractor provider, as every shipped layout does),
`typeset_errors` typesets the page (`typeset_case`) and runs every check a typeset page must pass,
collecting each failure rather than stopping at the first:

- stage one's six invariants (`check_page`) on the page built from the typeset geometry;
- the layout checks (`layout_errors`): no block off its page or out of its column, no two overlapping;
- the stage-one comparison (`stage_one_differences`): every block's category, text or table html, overlay
  blocks included, equal to stage one's page of the same case and mode;
- with distractors: the density bounds of config/distractors.yml (`DensityCheck`) on the typeset geometry;
- with distractors: the overlay placement checks (`overlay_errors`).

Stage one's page for each case and mode is the very render its own pass made, kept by a `StageOnePages`
measure handed to `check_overflow`, so no page is drawn twice. A case stage one cannot render (its own
pass reports why) is not typeset.
"""

import tempfile
from pathlib import Path

from generators.distractors.config import DistractorConfig
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH
from generators.overflow_check import Measure, render_modes
from generators.overlay.verify import OverlayError
from generators.parsing.config import ParsingConfig
from generators.parsing.omnidocbench import PageContext, build_page, check_page, page_where
from generators.typst.checks import overlay_errors
from generators.typst.compare import stage_one_differences
from generators.typst.compile import TypstError
from generators.typst.config import TypesetConfig
from generators.typst.readback import ReadbackError, layout_errors
from generators.typst.translate import TranslateError
from generators.typst.typeset import typeset_case

# The render the typeset density refusals name.
TYPESET_DISTRACTOR_RENDER = "typeset with distractors"


class StageOnePages:
    """A `check_overflow` measure that keeps each render's geometry, then hands it on.

    Attributes:
        pages: Each kept render's geometry, by (case id, whether it drew distractors).
    """

    def __init__(
        self, pages: dict[tuple[str, bool], dict], *, distractors: bool, then: Measure | None
    ) -> None:
        """Keep renders of one mode in `pages`.

        Args:
            pages: Where each geometry is kept; shared by the plain and the distractor measure.
            distractors: Whether the renders this measure is handed drew distractors.
            then: The measure the render is handed on to (the page check), or None.
        """
        self.pages = pages
        self.distractors = distractors
        self.then = then

    def __call__(self, case_id: str, layout_id: str, geometry: dict) -> None:
        """Keep one render's geometry, then hand it to `then`."""
        self.pages[(case_id, self.distractors)] = geometry
        if self.then is not None:
            self.then(case_id, layout_id, geometry)


def _differences(differences: list[str], *, where: str, layout_path: Path, layout_id: str) -> str:
    """Word a page's differences from stage one as one four-part diagnostic."""
    listing = "\n".join(f"              - {line}" for line in differences)
    return (
        "A typeset page differs from stage one's page.\n"
        f"  What:     {where}: {len(differences)} block(s) differ from stage one's page of the same case "
        f"and mode:\n{listing}\n"
        f"  Where:    {layout_path.resolve()} -> {layout_id}; the page as generators/typst/translate.py "
        "writes it\n"
        "  Expected: every block's category, and its text or table html with whitespace collapsed, "
        "equal to stage one's, e.g. block 3: text 'Total $10.00' on both pages.\n"
        "  Recover:  typeset the case with generators.typst.typeset.typeset_case and compare its blocks "
        "with stage one's build_page; fix the translator (or template) where the two part."
    )


def typeset_errors(
    gt_data: dict,
    layouts: dict,
    stage_one: dict[tuple[str, bool], dict],
    *,
    doc_type: str,
    layout_path: Path,
    parsing: ParsingConfig,
    distractor_config: DistractorConfig,
    config: TypesetConfig,
) -> list[str]:
    """Typeset every case of one document type in each mode stage one rendered it, and check each page.

    Args:
        gt_data: Case id -> ground-truth entry.
        layouts: The type's layout registry.
        stage_one: Stage one's render of each case and mode, by (case id, drew distractors), as its own
            pass made it (`StageOnePages`); a case and mode absent here is not typeset.
        doc_type: The extraction-schema document type (`invoice`), as the density bounds and the overlay
            protection policy key it.
        layout_path: The type's layout YAML.
        parsing: The validated parsing configuration.
        distractor_config: The distractor catalogue (its density bounds).
        config: The typesetting configuration.

    Returns:
        Every failure, four-part, in case order and, within a case, plain before distractor.
    """
    errors: list[str] = []
    density = DensityCheck(
        distractor_config.density[doc_type],
        doc_type=doc_type,
        config_path=DISTRACTORS_PATH,
        render=TYPESET_DISTRACTOR_RENDER,
    )
    with tempfile.TemporaryDirectory(prefix="typeset_validate_") as scratch:
        for case_id, raw in gt_data.items():
            layout_id = str(raw.get("layout", ""))
            layout = layouts.get(layout_id)
            if layout is None:
                continue
            entry = {**raw, "case_id": str(case_id)}
            for _, distractors in render_modes(layout):
                mode = "distractor" if distractors else "plain"
                page_one = stage_one.get((str(case_id), distractors))
                if page_one is None:
                    continue  # stage one's own pass reports a page it cannot render
                workdir = Path(scratch) / f"{case_id}_{mode}"
                try:
                    result = typeset_case(
                        entry,
                        layout,
                        layout_id=layout_id,
                        layout_path=layout_path,
                        distractors=distractors,
                        config=config,
                        workdir=workdir,
                    )
                except (TranslateError, TypstError, ReadbackError, OverlayError) as err:
                    errors.append(str(err))
                    continue
                geometry = result.geometry
                where = page_where(str(case_id), layout_id, f"typeset {mode}")
                context = PageContext(
                    f"{case_id}.png", doc_type, layout_id, f"typeset_{mode}", "none", True
                )
                try:
                    page = build_page(geometry, context=context, config=parsing)
                except ValueError as err:
                    errors.append(str(err))
                    continue
                errors.extend(check_page(page, geometry, where=where, config=parsing))
                errors.extend(layout_errors(geometry, where=where))
                differences = stage_one_differences(page, page_one, context=context)
                if differences:
                    errors.append(
                        _differences(differences, where=where, layout_path=layout_path, layout_id=layout_id)
                    )
                if distractors:
                    density(str(case_id), layout_id, geometry)
                    errors.extend(
                        overlay_errors(
                            result,
                            case_id=str(case_id),
                            layout_id=layout_id,
                            layout_path=layout_path,
                            doc_type=doc_type,
                            config=config,
                            workdir=workdir / "bare",
                        )
                    )
    return errors + density.errors
