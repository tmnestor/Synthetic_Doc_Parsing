"""Export the probe set: a handful of pages, chosen by rule, with their OmniDocBench ground truth.

The probe is what the matcher check (the design's section 5.3) runs the parsers on before the whole
snapshot is scored. Decision 12 chooses it: for each document type of `eval_set.document_types`, in that
order, the first case (by case id) of each of its first two layouts (by layout id, among the layouts some
case uses). That is six clean pages, and the first receipt among them is also photographed at every
degradation tier (light, moderate and heavy): nine pages.

Each clean page is the case's distractor render (the render the degraded half photographs), saved under
the snapshot's own file name, `<case id>_<document type>.png`, and written as one OmniDocBench page by
`build_page` from that very render's draw-time records. Each degraded page is that render degraded as the
snapshot degrades it (the case's `degradation_seed`, through `tier_seed`), saved as the snapshot names its
variant, `<case id>_<document type>_<tier suffix>.png`, with every `poly` carried onto the photographed
frame (`degraded_page`). Every page must keep the invariants `check_page` checks, or nothing is published. `export_probe` writes `<eval_set.output_root>/parsing_probe/`: the images
and `probe.json`, a list of pages sorted by image file name. An earlier probe is replaced whole.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from generators.degradation import load_tiers, tier_seed
from generators.exporters.eval_projection import load_extraction_schema
from generators.loader import REPO_ROOT, load_ground_truth, load_layout_registry
from generators.parsing.config import ParsingConfig
from generators.parsing.degraded import DEGRADED_HALF, degraded_page
from generators.parsing.omnidocbench import PageContext, build_page, check_page, page_where
from generators.snapshot.config import load_snapshot_config

# The probe's directory under the output root, and its ground-truth file (the plan names both).
PROBE_DIR = "parsing_probe"
PROBE_JSON = "probe.json"
# The half every clean probe page is drawn as: the distractor render, which the degraded half photographs.
PROBE_HALF = "distractor"
# Decision 12: how many layouts of each type the probe draws.
LAYOUTS_PER_TYPE = 2
# The extraction-schema document type whose first probe page is also photographed at every tier.
DEGRADED_DOC_TYPE = "receipt"
# The suffixes of the staging directory, and of an earlier probe while the new one is swapped in.
_STAGING_SUFFIX = ".partial"
_ASIDE_SUFFIX = ".old"


class ProbeError(RuntimeError):
    """The probe could not be chosen, or a probe page breaks an invariant."""


@dataclass(frozen=True)
class ProbeCase:
    """One clean probe page.

    Attributes:
        dtype: The `document_types` key it is drawn from (`receipts`, ...).
        canonical: Its extraction-schema document type (`receipt`, ...).
        case_id: The case drawn.
        layout_id: The case's layout.
        image_name: The page's file name, `<case id>_<document type>.png`, as the snapshot names it.
        ground_truth: The ground-truth file the case comes from.
        layouts: The layout registry file its layout comes from.
    """

    dtype: str
    canonical: str
    case_id: str
    layout_id: str
    image_name: str
    ground_truth: Path
    layouts: Path


def _repo_path(value: str) -> Path:
    """Resolve a config path against the repository root (an absolute path stays as it is)."""
    return (REPO_ROOT / value).resolve()


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> ProbeError:
    """Build a four-part refusal, each part on a labelled line."""
    return ProbeError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def select_probe(config_path: Path) -> list[ProbeCase]:
    """Choose the clean probe pages by Decision 12.

    Args:
        config_path: Path to generation_config.yml.

    Returns:
        The pages, by document type in `eval_set.document_types` order, then by layout id.

    Raises:
        ProbeError: A document type has fewer than two layouts that some case uses.
        ValueError: The `eval_set` block is invalid.
    """
    snapshot = load_snapshot_config(config_path)
    data = yaml.safe_load(snapshot.config_path.read_text())
    schema = load_extraction_schema()
    chosen: list[ProbeCase] = []
    for dtype in snapshot.document_types:
        doc_cfg = data["document_types"][dtype]
        gt_path = _repo_path(doc_cfg["ground_truth"])
        layout_path = _repo_path(doc_cfg["layouts"])
        entries = {str(case_id): entry for case_id, entry in load_ground_truth(gt_path).items()}
        registry = load_layout_registry(layout_path)
        by_layout: dict[str, list[str]] = {}
        for case_id, entry in entries.items():
            layout_id = str(entry.get("layout", ""))
            if layout_id in registry:
                by_layout.setdefault(layout_id, []).append(case_id)
        if len(by_layout) < LAYOUTS_PER_TYPE:
            raise _diagnostic(
                "The probe cannot be chosen.",
                what=f"document type {dtype!r} has {len(by_layout)} layout(s) that some case uses; the "
                f"probe draws the first case of each of its first {LAYOUTS_PER_TYPE}.",
                where=f"{gt_path}; {snapshot.config_path} -> eval_set.document_types",
                expected=f"at least {LAYOUTS_PER_TYPE} registered layouts each named by some case's "
                "'layout:', e.g. two cases on two different layouts.",
                recover=f"add cases on a second layout to {gt_path.name}, or remove {dtype!r} from "
                "eval_set.document_types.",
            )
        canonical = schema.resolve_doc_type(dtype)
        for layout_id in sorted(by_layout)[:LAYOUTS_PER_TYPE]:
            case_id = sorted(by_layout[layout_id])[0]
            fields = entries[case_id].get("fields", {}) or {}
            image_name = f"{case_id}_{schema.resolve_doc_type(str(fields.get('DOCUMENT_TYPE', dtype)))}.png"
            chosen.append(ProbeCase(dtype, canonical, case_id, layout_id, image_name, gt_path, layout_path))
    return chosen


def export_probe(config_path: Path, parsing_config: ParsingConfig) -> Path:
    """Render the probe pages and write their images and `probe.json` under the output root.

    Args:
        config_path: Path to generation_config.yml; its `eval_set.output_root` receives the probe.
        parsing_config: The validated parsing configuration.

    Returns:
        The probe directory, `<eval_set.output_root>/parsing_probe/`.

    Raises:
        ProbeError: The probe cannot be chosen, or a page breaks an invariant (nothing is published).
        ValueError: The `eval_set` block is invalid, or a page has a block no category covers.
        TierConfigError: The `receipt_degradation` block is invalid.
        AugmentationError: A tier names an unregistered augmentation.
    """
    from generators.pipeline import _RENDERERS

    snapshot = load_snapshot_config(config_path)
    cases = select_probe(config_path)
    tiers = load_tiers(snapshot.config_path)
    degraded_case = next((case for case in cases if case.canonical == DEGRADED_DOC_TYPE), None)
    final = snapshot.output_root / PROBE_DIR
    staging = snapshot.output_root / f".{PROBE_DIR}{_STAGING_SUFFIX}"
    aside = snapshot.output_root / f".{PROBE_DIR}{_ASIDE_SUFFIX}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(aside, ignore_errors=True)
    staging.mkdir()
    try:
        pages = []
        errors: list[str] = []
        for case in cases:
            entry = {**load_ground_truth(case.ground_truth)[case.case_id], "case_id": case.case_id}
            layout = load_layout_registry(case.layouts)[case.layout_id]
            geometry: dict = {}
            image = _RENDERERS[case.dtype](entry, layout, distractors=True, geometry_out=geometry)
            image.save(staging / case.image_name)
            context = PageContext(case.image_name, case.canonical, case.layout_id, PROBE_HALF, "none", True)
            page = build_page(geometry, context=context, config=parsing_config)
            where = page_where(case.case_id, case.layout_id, PROBE_HALF)
            errors.extend(check_page(page, geometry, where=where, config=parsing_config))
            pages.append(page)
            if case is not degraded_case:
                continue
            seed = int(entry["degradation_seed"])
            for index, tier in enumerate(tiers):
                variant = f"{Path(case.image_name).stem}_{tier.suffix}.png"
                frame, degraded = degraded_page(
                    image,
                    geometry,
                    tier=tier,
                    seed=tier_seed(seed, index),
                    image_path=variant,
                    doc_type=case.canonical,
                    layout_id=case.layout_id,
                    config=parsing_config,
                )
                frame.save(staging / variant)
                where = page_where(case.case_id, case.layout_id, f"{DEGRADED_HALF} {tier.name}")
                errors.extend(check_page(degraded, geometry, where=where, config=parsing_config))
                pages.append(degraded)
        if errors:
            listing = "\n".join("      " + line for error in errors for line in error.splitlines())
            raise _diagnostic(
                "The probe breaks the parsing invariants; nothing was published.",
                what=f"{len(errors)} invariant failure(s), each with its own diagnostic:\n{listing}",
                where=f"{snapshot.config_path} -> eval_set, and the files each diagnostic above names",
                expected="every probe page keeps the invariants, as `python -m generators.pipeline "
                "validate` reports, e.g. 'Validation passed.'",
                recover="fix each failure listed (validate reports them in full), then export again.",
            )
        pages.sort(key=lambda page: page["page_info"]["image_path"])
        (staging / PROBE_JSON).write_text(json.dumps(pages, indent=1, ensure_ascii=False) + "\n")
        if final.exists():
            final.rename(aside)
        staging.rename(final)
        shutil.rmtree(aside, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if aside.exists() and not final.exists():
            aside.rename(final)
        raise
    return final
