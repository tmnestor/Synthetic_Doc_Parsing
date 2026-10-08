"""Fail-fast overflow backstop.

Renders each ground-truth entry and collects any FitError raised by fit_text
(a string that cannot fit its box even after lossless wrap/shrink), and any
OverlayError from a distractor render's overlay (a mark with no legal place),
the latter with its whole four-part message. Routine
length variation is handled at render time and never appears here — this gate
fires only for genuinely-impossible geometry, a real layout design error.

Rendering (rather than re-deriving each field's string) means the backstop
tests exactly the same code path that `generate` runs, with no duplication of
per-renderer string construction.
"""

from collections.abc import Callable
from dataclasses import dataclass

from generators.common import FitError
from generators.labels.config import LabelConfig
from generators.labels.render import LabelRefusalError, label_sites, render_labelled_varied
from generators.layout_dsl.provider_registry import get_field_provider, is_distractor_provider
from generators.overlay.verify import OverlayError


class OverflowError_(RuntimeError):
    """Aggregated render-overflow report across a ground-truth file."""


# Both render modes, each with the label a violation line names it by. A distractor value can widen a
# block only when distractors are drawn, so a plain-only check would miss it.
RENDER_MODES: tuple[tuple[str, bool], ...] = (("plain", False), ("with distractors", True))


def render_modes(layout: dict) -> tuple[tuple[str, bool], ...]:
    """Return the render modes worth checking for a layout.

    Only a layout whose `field_providers` include a distractor provider draws anything different with
    distractors on; for any other layout the distractor render would repeat the plain one.

    Args:
        layout: A layout dict carrying `field_providers`.

    Returns:
        `RENDER_MODES` for a layout with a distractor provider, else the plain mode alone.

    Raises:
        FieldProviderError: A `field_providers` entry names no registered provider.
    """
    names = [spec.get("name") for spec in layout.get("field_providers", []) if isinstance(spec, dict)]
    for name in names:
        # An unknown name fails here, rather than reading as "no distractor provider" (plain only).
        get_field_provider(str(name))
    if any(is_distractor_provider(str(name)) for name in names):
        return RENDER_MODES
    return RENDER_MODES[:1]


# Called with (case id, layout id, geometry) after each distractor render that fits, so a caller can
# measure the render the overflow pass already made rather than drawing the page a second time.
Measure = Callable[[str, str, dict], None]

# The label a violation line names the third render by.
LABELLED_VARIED_MODE = "labelled varied"


@dataclass(frozen=True)
class LabelledVaried:
    """What the third render mode needs: the labels to vary and what to measure the accepted page with.

    Attributes:
        doc_type: The extraction-schema document type the renderer draws.
        config: The label configuration (orders, rules' words, refusal policy).
        measures: Each called with (case id, layout id, geometry) of the accepted page, as `measure` is:
            the drawn-field guard and the density bounds.
    """

    doc_type: str
    config: LabelConfig
    measures: tuple[Measure, ...]


def check_overflow(
    gt_data: dict,
    layouts: dict,
    renderer: Callable[..., object],
    *,
    measure: Measure | None = None,
    plain_measure: Measure | None = None,
    labelled_varied: LabelledVaried | None = None,
) -> list[str]:
    """Render each entry plain, and with distractors where its layout draws any; collect every refusal.

    A `FitError` (content that cannot fit its box) is reported by its first line, under the caller's
    field-budget banner. An `OverlayError` (an overlay mark with no legal placement) is reported whole,
    its own four-part message naming the mark, the boxes that blocked it and the fix, and labelled as an
    overlay refusal, never as a field budget.

    Args:
        gt_data: Mapping of case id -> entry dict.
        layouts: Layout registry (layout id -> layout dict).
        renderer: The doc type's renderer, called as renderer(entry, layout, distractors=...) once per
            mode `render_modes` returns for the entry's layout (with `geometry_out` as well for a
            distractor render when `measure` is given).
        measure: Optional; handed each distractor render's geometry (`width`, `height`, `boxes`,
            `ink`) after it renders without a FitError. `validate` measures density this way.
        plain_measure: Optional; handed each plain render's geometry the same way. `validate` checks
            every scored value is drawn (has a recorded box) this way.
        labelled_varied: Optional; when given, a layout carrying label sites is also rendered a third
            time through `render_labelled_varied` (its unambiguity rules run inside the render, a refusal
            under `redraw` being no error and one under `fail` a violation), and the accepted page is
            handed to each of its measures. A layout without label sites (a bank statement) is not.

    Returns:
        A list of human-readable violation lines (empty if everything fits), each naming the case,
        the layout and the render mode that overflowed.
        Never raises for a fitting document; collects all problems in one pass.
    """
    violations: list[str] = []
    for case_id, entry in gt_data.items():
        layout_ref = entry.get("layout", "")
        layout = layouts.get(layout_ref)
        if not layout:
            continue
        for mode, distractors in render_modes(layout):
            e = dict(entry)
            e["case_id"] = str(case_id)
            wanted = measure if distractors else plain_measure
            geometry: dict | None = {} if wanted is not None else None
            try:
                if geometry is None:
                    renderer(e, layout, distractors=distractors)
                else:
                    renderer(e, layout, distractors=distractors, geometry_out=geometry)
            except FitError as exc:
                first = str(exc).splitlines()[0]
                violations.append(f"{case_id} / {layout_ref} ({mode}): {first}")
                continue
            except OverlayError as exc:
                # Not a field budget: the overlay's own four-part message says which mark, which boxes
                # blocked it and what to change, so it is kept whole.
                violations.append(f"{case_id} / {layout_ref} ({mode}): overlay refused:\n{exc}")
                continue
            if geometry is not None and wanted is not None:
                wanted(str(case_id), str(layout_ref), geometry)
        if labelled_varied is not None and label_sites(layout):
            violations.extend(_check_labelled_varied(case_id, entry, layout, renderer, labelled_varied))
    return violations


def _check_labelled_varied(
    case_id: object, entry: dict, layout: dict, renderer: Callable[..., object], mode: LabelledVaried
) -> list[str]:
    """Render one entry labelled varied and measure the accepted page; return its violations."""
    layout_ref = entry.get("layout", "")
    e = {**entry, "case_id": str(case_id)}
    geometry: dict = {}
    try:
        render_labelled_varied(
            renderer,  # type: ignore[arg-type]
            e,
            layout,
            doc_type=mode.doc_type,
            config=mode.config,
            geometry_out=geometry,
        )
    except FitError as exc:
        return [f"{case_id} / {layout_ref} ({LABELLED_VARIED_MODE}): {str(exc).splitlines()[0]}"]
    except OverlayError as exc:
        return [f"{case_id} / {layout_ref} ({LABELLED_VARIED_MODE}): overlay refused:\n{exc}"]
    except LabelRefusalError as exc:
        return [f"{case_id} / {layout_ref} ({LABELLED_VARIED_MODE}): label refused:\n{exc}"]
    for measure in mode.measures:
        measure(str(case_id), str(layout_ref), geometry)
    return []


def build_overflow_error(violations: list[str], *, layout_dir: str = "config/layouts/") -> OverflowError_:
    """Build a four-element fail-fast diagnostic from collected violations."""
    listing = "\n    ".join(violations)
    return OverflowError_(
        "Content overflow: fields cannot fit their boxes losslessly.\n"
        f"  What:     {len(violations)} field(s) overflow even after wrap/shrink:\n"
        f"    {listing}\n"
        f"  Where:    the field_budgets entries in {layout_dir}*.yml\n"
        "  Expected: each listed field's content <= its budget width, or a larger\n"
        "            max_lines / lower min_font so it fits losslessly.\n"
        "  Recover:  widen `width` (or raise `max_lines`) for the listed fields in\n"
        "            the layout YAML, or shorten the source strings; never truncate."
    )
