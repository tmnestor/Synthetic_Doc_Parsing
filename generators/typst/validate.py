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

import multiprocessing
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Job:
    """One page of the pass: a case in one mode, with stage one's render of it.

    Attributes:
        case_id: The case.
        entry: Its entry, carrying `case_id`.
        layout_id: Its layout.
        layout: The resolved layout.
        distractors: Whether the page draws distractors.
        stage_one: Stage one's render of the same case and mode.
    """

    case_id: str
    entry: dict
    layout_id: str
    layout: dict
    distractors: bool
    stage_one: dict


@dataclass(frozen=True)
class _Shared:
    """What every page of one document type's pass is checked with."""

    doc_type: str
    layout_path: Path
    parsing: ParsingConfig
    distractor_config: DistractorConfig
    config: TypesetConfig


def _mode(distractors: bool) -> str:
    return "distractor" if distractors else "plain"


def _check(job: _Job, shared: _Shared) -> tuple[list[str], list[str]]:
    """Typeset one page and run every check on it.

    Returns:
        (the page's failures, its density refusals), each four-part.
    """
    mode = _mode(job.distractors)
    density = DensityCheck(
        shared.distractor_config.density[shared.doc_type],
        doc_type=shared.doc_type,
        config_path=DISTRACTORS_PATH,
        render=TYPESET_DISTRACTOR_RENDER,
    )
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="typeset_validate_") as scratch:
        workdir = Path(scratch) / f"{job.case_id}_{mode}"
        try:
            result = typeset_case(
                job.entry,
                job.layout,
                layout_id=job.layout_id,
                layout_path=shared.layout_path,
                distractors=job.distractors,
                config=shared.config,
                workdir=workdir,
            )
        except (TranslateError, TypstError, ReadbackError, OverlayError) as err:
            return [str(err)], []
        geometry = result.geometry
        where = page_where(job.case_id, job.layout_id, f"typeset {mode}")
        context = PageContext(
            f"{job.case_id}.png", shared.doc_type, job.layout_id, f"typeset_{mode}", "none", True
        )
        try:
            page = build_page(geometry, context=context, config=shared.parsing)
        except ValueError as err:
            return [str(err)], []
        errors.extend(check_page(page, geometry, where=where, config=shared.parsing))
        errors.extend(layout_errors(geometry, where=where))
        differences = stage_one_differences(page, job.stage_one, context=context)
        if differences:
            errors.append(
                _differences(
                    differences, where=where, layout_path=shared.layout_path, layout_id=job.layout_id
                )
            )
        if job.distractors:
            density(job.case_id, job.layout_id, geometry)
            errors.extend(
                overlay_errors(
                    result,
                    case_id=job.case_id,
                    layout_id=job.layout_id,
                    layout_path=shared.layout_path,
                    doc_type=shared.doc_type,
                    config=shared.config,
                    workdir=workdir / "bare",
                )
            )
    return errors, density.errors


def _crashed(job: _Job, shared: _Shared, err: BaseException) -> str:
    """Word a page whose check raised (or whose worker died) as a four-part failure naming the case."""
    where = page_where(job.case_id, job.layout_id, f"typeset {_mode(job.distractors)}")
    return (
        "A typeset page could not be checked.\n"
        f"  What:     {where}: checking the page raised {type(err).__name__}: {err}\n"
        f"  Where:    {shared.layout_path.resolve()} -> {job.layout_id}; the case's entry in its ground "
        "truth; generators/typst/validate.py (the typeset pass)\n"
        "  Expected: every page typesets and is checked, each failure reported four-part, e.g. a page that "
        "differs from stage one named with its blocks.\n"
        "  Recover:  typeset the case alone with generators.typst.typeset.typeset_case to see the full "
        "traceback, then fix the cause it names."
    )


def _guarded(job: _Job, shared: _Shared) -> tuple[list[str], list[str]]:
    """`_check`, with any exception it raises turned into one four-part failure naming the case."""
    try:
        return _check(job, shared)
    except Exception as err:  # noqa: BLE001  every page must be reported, never lost
        return [_crashed(job, shared, err)], []


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

    Pages are independent, so they are typeset in `config.validate_workers` processes at once (one runs
    them in this process). The result does not depend on the count: each page's failures are reported in
    case order and, within a case, plain before distractor, then every density refusal in the same order.
    A page whose check raises, or whose worker process dies, is reported as a four-part failure naming
    its case; no page is lost and the pass never waits on a dead worker. Workers are spawned, on every
    platform alike, so a script that calls the pass must guard its own entry point (`if __name__ ==
    "__main__"`), as `python -m generators.pipeline` does.

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
        config: The typesetting configuration, its `validate_workers` included.

    Returns:
        Every failure, four-part.
    """
    shared = _Shared(doc_type, layout_path, parsing, distractor_config, config)
    jobs: list[_Job] = []
    for case_id, raw in gt_data.items():
        layout_id = str(raw.get("layout", ""))
        layout = layouts.get(layout_id)
        if layout is None:
            continue
        for _, distractors in render_modes(layout):
            page_one = stage_one.get((str(case_id), distractors))
            if page_one is None:
                continue  # stage one's own pass reports a page it cannot render
            entry = {**raw, "case_id": str(case_id)}
            jobs.append(_Job(str(case_id), entry, layout_id, layout, distractors, page_one))
    if config.validate_workers == 1 or len(jobs) <= 1:
        results = [_guarded(job, shared) for job in jobs]
    else:
        results = []
        with ProcessPoolExecutor(
            max_workers=min(config.validate_workers, len(jobs)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            futures = [pool.submit(_guarded, job, shared) for job in jobs]
            for job, future in zip(jobs, futures, strict=True):
                try:
                    results.append(future.result())
                except Exception as err:  # noqa: BLE001  a dead worker loses no page
                    results.append(([_crashed(job, shared, err)], []))
    return [error for errors, _ in results for error in errors] + [
        error for _, refusals in results for error in refusals
    ]
