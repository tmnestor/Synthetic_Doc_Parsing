"""Page density of a render, measured from its recorded ink (spec section 9).

Three metrics, all computed from the ink `BoxRecorder.record_ink` collects while a page is drawn:

- *scored-ink ratio*: scored ink area over all text ink area (scored, distractor and other text). A
  code mark's rectangle is not text, so it is left out of that denominator and reported separately as
  `mark_area`; a mark's caption is still a line of distractor text. Rules (a row of dashes or a thin
  line) are not recorded as ink at all, so they count in neither metric.
- *distractor share*: distractor text area over all text ink area, marks excluded as above, so a
  render that meets a floor on it is known to carry distractor text (a plain render has none).
- *page fill*: the lowest ink y over the page height, so a page whose content stops a third of the
  way down reads as sparse. A page cropped to its content (a receipt) fills itself by construction,
  so its bound may be null in the catalogue, and the check is then skipped.

Areas are summed per line, not unioned: printed lines do not overlap, and a summed area is what the
bounds in `config/distractors.yml` were measured with.

`validate` enforces the bounds only on the distractor render of a layout that carries a distractor
provider. A plain render is the control and is measured (`measure_render`) but never enforced.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from generators.common import FitError
from generators.distractors.config import DensityBounds
from generators.distractors.paths import DISTRACTORS_PATH, layout_file
from generators.exporters.geometry import InkRecord
from generators.overflow_check import render_modes


class DensityReport(NamedTuple):
    """The density metrics for one render, with the areas behind them.

    Attributes:
        scored_ink_ratio: Scored ink area over all text ink area.
        page_fill: Lowest ink y over page height.
        distractor_share: Distractor text ink area over all text ink area.
        scored_area: Pixel area of scored text ink.
        distractor_area: Pixel area of distractor text ink, code marks excluded.
        text_area: Pixel area of other text ink.
        mark_area: Pixel area of code-mark rectangles.
    """

    scored_ink_ratio: float
    page_fill: float
    distractor_share: float
    scored_area: int
    distractor_area: int
    text_area: int
    mark_area: int


def _area(box: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = box
    return max(0, right - left) * max(0, bottom - top)


def _areas(boxes: Sequence[InkRecord]) -> dict[str, int]:
    areas = {"scored": 0, "distractor": 0, "text": 0, "mark": 0}
    for record in boxes:
        areas["mark" if record.mark else record.role] += _area(record.box)
    return areas


def _text_total(areas: dict[str, int], metric: str) -> int:
    total = areas["scored"] + areas["distractor"] + areas["text"]
    if total == 0:
        msg = (
            f"The render recorded no text ink, so its {metric} is undefined. "
            "Remediation: render with distractors, geometry_out or ink_out so the recorder captures ink, "
            "and check the layout draws text."
        )
        raise ValueError(msg)
    return total


def distractor_share(boxes: Sequence[InkRecord]) -> float:
    """Return distractor text ink area over all text ink area, code marks excluded.

    Args:
        boxes: The render's ink records.

    Returns:
        A fraction from 0 to 1.

    Raises:
        ValueError: If the render recorded no text ink, so the share is undefined.
    """
    areas = _areas(boxes)
    return areas["distractor"] / _text_total(areas, "distractor share")


def scored_ink_ratio(boxes: Sequence[InkRecord]) -> float:
    """Return scored ink area over all text ink area, code marks excluded.

    Args:
        boxes: The render's ink records.

    Returns:
        A fraction from 0 to 1.

    Raises:
        ValueError: If the render recorded no text ink, so the ratio is undefined.
    """
    areas = _areas(boxes)
    return areas["scored"] / _text_total(areas, "scored-ink ratio")


def page_fill(boxes: Sequence[InkRecord], page_height: int) -> float:
    """Return the lowest ink y over the page height.

    Args:
        boxes: The render's ink records, code marks included.
        page_height: The rendered page's height in pixels.

    Returns:
        The fraction of the page above the lowest ink.

    Raises:
        ValueError: If `page_height` is not positive, or the render recorded no ink.
    """
    if page_height <= 0:
        msg = f"page_height must be positive, got {page_height}. Remediation: pass the rendered image's height."
        raise ValueError(msg)
    if not boxes:
        msg = "The render recorded no ink, so its page fill is undefined. Remediation: render with geometry_out."
        raise ValueError(msg)
    return max(record.box[3] for record in boxes) / page_height


def density_report(boxes: Sequence[InkRecord], page_height: int) -> DensityReport:
    """Return the three density metrics and the areas behind them.

    Args:
        boxes: The render's ink records.
        page_height: The rendered page's height in pixels.

    Returns:
        The report.
    """
    areas = _areas(boxes)
    return DensityReport(
        scored_ink_ratio=scored_ink_ratio(boxes),
        page_fill=page_fill(boxes, page_height),
        distractor_share=distractor_share(boxes),
        scored_area=areas["scored"],
        distractor_area=areas["distractor"],
        text_area=areas["text"],
        mark_area=areas["mark"],
    )


def measure_render(
    renderer: Callable[..., object], entry: dict, layout: dict, distractors: bool
) -> DensityReport:
    """Render one entry and measure its density.

    Args:
        renderer: The document type's renderer, called as renderer(entry, layout, distractors=...,
            geometry_out=...).
        entry: The ground-truth entry, carrying `case_id`.
        layout: The entry's layout.
        distractors: Whether to render with distractors.

    Returns:
        The render's density report.
    """
    geometry: dict = {}
    renderer(entry, layout, distractors=distractors, geometry_out=geometry)
    return density_report(geometry["ink"], int(geometry["height"]))


def _refusal(
    *,
    case_id: str,
    layout_id: str,
    metric: str,
    value: float,
    bound_key: str,
    bound: float,
    comparison: str,
    doc_type: str,
    config_path: Path,
    render: str,
) -> str:
    return (
        "Distractor density out of bounds.\n"
        f"  What:     {case_id} / {layout_id} ({render}): {metric} {value:.4f} is "
        f"{'above' if comparison == '<=' else 'below'} {bound_key} {bound}.\n"
        f"  Where:    {config_path.resolve()} -> density.{doc_type}.{bound_key}\n"
        f"  Expected: {metric} {comparison} {bound} for every distractor render of a {doc_type}, e.g.\n"
        f"              density: {{{doc_type}: {{{bound_key}: {bound}}}}}\n"
        f"  Recover:  change which distractors layout '{layout_id}' draws (its blocks or "
        f"distractor_items) so the page meets the bound, or re-measure and revise "
        f"density.{doc_type}.{bound_key}."
    )


def _no_distractor_ink(*, case_id: str, layout_id: str, doc_type: str, render: str) -> str:
    path = layout_file(doc_type)
    return (
        "Distractor render carries no distractor.\n"
        f"  What:     {case_id} / {layout_id} ({render}) recorded no distractor ink and no "
        "distractor mark, so it cannot be told from the plain render of the same page.\n"
        f"  Where:    {path} -> {layout_id}.distractor_items; {DISTRACTORS_PATH} -> "
        f"selection.{doc_type}.count\n"
        f"  Expected: every distractor render draws at least one selected item, a line of distractor text "
        f"or a mark, e.g. selection: {{{doc_type}: {{count: [2, 4]}}}} with every listed item drawn by a "
        "gated block or overlay.\n"
        f"  Recover:  check {layout_id}'s blocks draw each item its distractor_items lists (run "
        "`python -m generators.pipeline validate`), or raise the selection count's minimum."
    )


class DensityCheck:
    """Check each measured distractor render against a document type's bounds, collecting refusals.

    An instance is the `measure` callback `check_overflow` hands each distractor render's geometry to,
    so `validate` bounds the very render the overflow pass made instead of drawing every page again.

    Attributes:
        errors: One four-part refusal per metric outside its bound, in the order measured: the scored-ink
            ratio, the distractor share, then the page fill unless its bound is null.
    """

    def __init__(
        self, bounds: DensityBounds, *, doc_type: str, config_path: Path, render: str = "with distractors"
    ) -> None:
        """Hold the bounds a render must meet.

        Args:
            bounds: The document type's density bounds.
            doc_type: The extraction-schema document type, as `density:` keys it.
            config_path: The distractor catalogue the bounds came from, named in each refusal.
            render: Which render the refusals name: the distractor render, or the labelled varied one
                (Decision 20 of the label-variation plan holds it to the same bounds).
        """
        self.render = render
        self.bounds = bounds
        self.doc_type = doc_type
        self.config_path = config_path
        self.errors: list[str] = []

    def __call__(self, case_id: str, layout_id: str, geometry: dict) -> None:
        """Measure one distractor render's geometry and record a refusal per metric out of bounds.

        Args:
            case_id: The case rendered.
            layout_id: Its layout.
            geometry: The render's `geometry_out`, carrying `ink` and `height`.
        """
        if not any(record.role == "distractor" for record in geometry["ink"]):
            # Decision 14: a bank statement's share floor may be 0 (a barcode-only page has no distractor
            # text), so the bounds alone cannot tell a distractor render that drew nothing from a plain one.
            self.errors.append(
                _no_distractor_ink(
                    case_id=case_id, layout_id=layout_id, doc_type=self.doc_type, render=self.render
                )
            )
        report = density_report(geometry["ink"], int(geometry["height"]))
        bounds = self.bounds
        checks: list[tuple[str, float, str, float, str]] = [
            (
                "scored_ink_ratio",
                report.scored_ink_ratio,
                "scored_ink_ratio_max",
                bounds.scored_ink_ratio_max,
                "<=",
            ),
            (
                "distractor_share",
                report.distractor_share,
                "distractor_share_min",
                bounds.distractor_share_min,
                ">=",
            ),
        ]
        # A null page_fill_min (a page cropped to its content) bounds nothing, so it is not checked.
        if bounds.page_fill_min is not None:
            checks.append(("page_fill", report.page_fill, "page_fill_min", bounds.page_fill_min, ">="))
        for metric, value, bound_key, bound, comparison in checks:
            within = value <= bound if comparison == "<=" else value >= bound
            if not within:
                self.errors.append(
                    _refusal(
                        case_id=case_id,
                        layout_id=layout_id,
                        metric=metric,
                        value=value,
                        bound_key=bound_key,
                        bound=bound,
                        comparison=comparison,
                        doc_type=self.doc_type,
                        config_path=self.config_path,
                        render=self.render,
                    )
                )


def check_density(
    gt_data: dict,
    layouts: dict,
    renderer: Callable[..., object],
    bounds: DensityBounds,
    *,
    doc_type: str,
    config_path: Path,
) -> list[str]:
    """Render each entry with distractors, where its layout carries a provider, and check its density.

    A standalone pass for callers without an overflow pass to share; `validate` instead hands a
    `DensityCheck` to `check_overflow`, which measures the renders it already makes. A layout without a
    distractor provider has only its plain render, which is never enforced. An entry that does not
    render (a FitError) is left to the overflow check, which reports it.

    Args:
        gt_data: Mapping of case id -> entry dict.
        layouts: Layout registry (layout id -> layout dict).
        renderer: The document type's renderer.
        bounds: The document type's density bounds.
        doc_type: The extraction-schema document type, as `density:` keys it.
        config_path: The distractor catalogue the bounds came from, named in each refusal.

    Returns:
        One four-part refusal per metric outside its bound; empty when every render passes.
    """
    check = DensityCheck(bounds, doc_type=doc_type, config_path=config_path)
    for case_id, entry in gt_data.items():
        layout_id = str(entry.get("layout", ""))
        layout = layouts.get(layout_id)
        if not layout or not any(distractors for _, distractors in render_modes(layout)):
            continue
        geometry: dict = {}
        try:
            renderer({**entry, "case_id": str(case_id)}, layout, distractors=True, geometry_out=geometry)
        except FitError:
            continue
        check(str(case_id), layout_id, geometry)
    return check.errors
