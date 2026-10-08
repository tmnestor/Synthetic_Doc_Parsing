"""Export a dated snapshot of four paired halves, rendered in one run from a committed tree.

`export_snapshot` writes one directory, `<eval_set.output_root>/<snapshot_dir_prefix>_<YYYYMMDD>/`::

    plain_<YYYYMMDD>/            every case of every type, rendered without distractors
    distractor_<YYYYMMDD>/       the same pages with their distractors, under the same filenames
    labelled_varied_<YYYYMMDD>/  the distractor pages with their ABN, GST and total labels varied,
                                 plus label_styles.jsonl (one row per image, sorted by image_file)
    degraded_<YYYYMMDD>/         each distractor receipt photographed at every declared tier
    geometry/                    plain.jsonl, distractor.jsonl, labelled_varied.jsonl: scored boxes

(directory names from `eval_set.half_prefixes` and `eval_set.geometry_dir`). The three clean halves
share filenames and byte-identical ground truth, so pairing them is a direct join; degraded filenames
carry their tier suffix and their own receipt-only ground truth.

The order is fixed, and each step refuses before the next begins:

1. The configuration is checked and the output root resolved (Decision 3).
2. The repository state is read; tracked changes are refused (Decision 4).
3. The cheap checks run on every entry and layout before anything is drawn: the entry schema, the bank
   ledger, the distractor values, and each layout's DSL body (Decision 8). Every failure is collected and
   refused as one; the output root is untouched.
4. Every case is rendered plain, with distractors and labelled varied (Decision 5: nothing is read from
   `generate`'s output), and each distractor receipt degraded once per tier (Decision 7), all into a
   staging directory `<root>/.<snapshot_dir_prefix>_<YYYYMMDD>.partial`. The density bounds are checked
   on every distractor and labelled varied render, the drawn-field guard on every plain and labelled
   varied render; overlay and unambiguity refusals are raised inside the renderers. Plain renders are
   measured, never bounded. Every failure is collected, and any refuses the export as one.
5. The repository state is read again; a moved HEAD or a newly dirty tree discards the staging
   directory, because a snapshot naming a commit that did not produce it is worse than none.
6. An earlier snapshot of the same name is replaced: removed, then the staging directory is renamed into
   place (the project owner's ruling; a leftover staging directory from a failed run is cleared at the
   start of the next).

Any failure after the staging directory exists removes it, so a refused or interrupted export leaves no
partial snapshot behind.
"""

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

import yaml
from PIL import Image

from generators.bank_ledger import BankLedgerError, check_statement
from generators.common import FitError
from generators.content_engine import load_pools
from generators.degradation import Tier, degrade_receipt, load_tiers, tier_seed
from generators.distractors.config import load_distractor_config
from generators.distractors.density import DensityCheck, DensityReport, density_report
from generators.distractors.paths import DISTRACTORS_PATH
from generators.distractors.providers import DistractorProviderError, draw_all_eligible
from generators.distractors.validator import DistractorValueError, check_distractor_values
from generators.drawn_fields import DrawnFieldsCheck, DrawnFieldsError, build_check
from generators.drawn_values import DrawnValuePolicyError
from generators.eval_set import (
    _DATE_FORMAT,
    _DEGRADED_TYPE,
    _err,
    csv_from_jsonl,
    project_fields,
    write_jsonl,
)
from generators.exporters.eval_projection import ExtractionSchema, load_extraction_schema
from generators.labels.config import LabelConfig, load_label_config
from generators.labels.render import LabelOutcome, LabelRefusalError, label_sites, render_labelled_varied
from generators.labels.sidecar import sidecar_row, write_sidecar
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import REPO_ROOT, load_ground_truth, load_layout_registry
from generators.overflow_check import LABELLED_VARIED_MODE, render_modes
from generators.overlay.verify import OverlayError
from generators.schema import field_names_for, validate_entry
from generators.snapshot.config import HALVES, SnapshotConfig, load_snapshot_config
from generators.snapshot.repo_state import RepoState, RepoStateError, read_repo_state, require_clean

# The three halves rendered directly (the fourth, degraded, is made from the distractor render).
CLEAN_HALVES = HALVES[:3]
PLAIN, DISTRACTOR, LABELLED_VARIED, DEGRADED = HALVES

# The suffix of the staging directory beside the destination; it is a contract with the spec.
_STAGING_SUFFIX = ".partial"


class SnapshotError(RuntimeError):
    """The export refused: a check failed, or the repository changed while it ran."""


@dataclass(frozen=True)
class SnapshotSummary:
    """What one export produced, for the command's report and the manifest.

    Attributes:
        path: The published snapshot directory.
        stamp: Its date stamp, `%Y%m%d`.
        repo_state: The repository state it was rendered from (clean, and unchanged at publish).
        half_dirs: Half -> its directory inside the snapshot.
        counts: Half -> extraction document type -> number of images (the degraded half names receipts only).
        sidecar_rows: The labelled varied half's sidecar rows, sorted by image_file.
        density: Clean half -> image_file -> the render's density report (plain renders measured only).
    """

    path: Path
    stamp: str
    repo_state: RepoState
    half_dirs: dict[str, Path]
    counts: dict[str, dict[str, int]]
    sidecar_rows: tuple[dict, ...]
    density: dict[str, dict[str, DensityReport]]

    def total(self, half: str) -> int:
        """Return a half's image count across document types."""
        return sum(self.counts[half].values())


@dataclass
class _TypePlan:
    """One document type, checked and ready to render."""

    dtype: str
    canonical: str
    renderer: Callable[..., Image.Image]
    layouts: dict
    entries: list[tuple[str, dict, str, dict]]  # (case id, entry, filename, projected fields)
    plain_drawn: DrawnFieldsCheck
    varied_drawn: DrawnFieldsCheck
    density: DensityCheck
    varied_density: DensityCheck


@dataclass
class _Rendered:
    """Everything the render pass collects."""

    documents: list[dict] = field(default_factory=list)
    degraded_documents: list[dict] = field(default_factory=list)
    geometry: dict[str, list[dict]] = field(default_factory=lambda: {h: [] for h in CLEAN_HALVES})
    sidecar_rows: list[dict] = field(default_factory=list)
    density: dict[str, dict[str, DensityReport]] = field(
        default_factory=lambda: {h: {} for h in CLEAN_HALVES}
    )
    counts: dict[str, dict[str, int]] = field(default_factory=lambda: {h: {} for h in HALVES})
    failures: list[str] = field(default_factory=list)


def _repo_path(value: str) -> Path:
    """Resolve a config path against the repository root (an absolute path stays as it is)."""
    return (REPO_ROOT / value).resolve()


def _checks_failed(failures: list[str], *, stage: str, config_path: Path) -> SnapshotError:
    """Build the one four-part refusal listing every collected check failure (Decision 8)."""
    listing = "\n".join("      " + line for failure in failures for line in failure.splitlines())
    return SnapshotError(
        "The snapshot export's checks failed; nothing was published.\n"
        f"  What:     {len(failures)} check failure(s) {stage}, each with its own diagnostic:\n"
        f"{listing}\n"
        f"  Where:    {config_path} -> eval_set, and the files each diagnostic above names\n"
        "  Expected: every entry, layout and render passes its checks, as "
        "`python -m generators.pipeline validate` reports, e.g. 'Validation passed.'\n"
        "  Recover:  fix each failure listed (run validate to see them in full), then export again."
    )


def _state_changed(start: RepoState, end: RepoState) -> SnapshotError:
    """Build the refusal for a repository that changed between the start and the publish."""
    changes = []
    if end.commit != start.commit:
        changes.append(f"HEAD moved from {start.commit} to {end.commit}")
    if end.dirty_paths:
        listed = ", ".join(end.dirty_paths[:10])
        more = f" (and {len(end.dirty_paths) - 10} more)" if len(end.dirty_paths) > 10 else ""
        changes.append(f"tracked paths changed: {listed}{more}")
    return SnapshotError(
        "The repository changed while the snapshot was rendered; nothing was published.\n"
        f"  What:     {'; '.join(changes)}. The images may not come from the commit the snapshot "
        "would name.\n"
        f"  Where:    {REPO_ROOT}\n"
        "  Expected: the same commit and git status --porcelain --untracked-files=no printing nothing, "
        "from the start of the export to its publish.\n"
        "  Recover:  leave the repository untouched while the export runs, then export again."
    )


def _plan_type(
    dtype: str,
    doc_cfg: dict,
    *,
    config: SnapshotConfig,
    schema: ExtractionSchema,
    renderers: dict,
    pools: dict,
    distractor_config: object,
    seen: dict[str, str],
    failures: list[str],
) -> _TypePlan | None:
    """Run the cheap checks on one document type, collecting failures; return its render plan."""
    renderer = renderers.get(dtype)
    if renderer is None:
        raise _err(
            f"no renderer is registered for document type '{dtype}'.",
            path=config.config_path,
            key_path="eval_set.document_types",
            expected=f"a type with a renderer: {sorted(renderers)}.",
            recover=f"remove '{dtype}' from eval_set.document_types",
        )
    gt_path = _repo_path(doc_cfg["ground_truth"])
    layout_path = _repo_path(doc_cfg["layouts"])
    gt_data = {str(k): v for k, v in load_ground_truth(gt_path).items()}
    layouts = load_layout_registry(layout_path)
    canonical = schema.resolve_doc_type(dtype)

    known = set(field_names_for(dtype))
    for layout_id, layout in layouts.items():
        if "body" not in layout:
            continue
        try:
            validate_layout(
                layout,
                layout_id=layout_id,
                layout_path=str(layout_path),
                known_fields=known,
                doc_type=dtype,
            )
        except LayoutSchemaError as exc:
            failures.append(str(exc))

    entries: list[tuple[str, dict, str, dict]] = []
    for case_id, entry in gt_data.items():
        layout_ref = entry.get("layout", "")
        if not layouts.get(layout_ref):
            raise _err(
                f"{case_id} references layout '{layout_ref}', which is not in the registry.",
                path=layout_path,
                key_path=f"layouts.{layout_ref}",
                expected="every ground-truth entry's layout to exist in its layout registry.",
                recover=f"add '{layout_ref}' to the registry or fix {case_id}'s layout",
            )
        if not isinstance(entry.get("degradation_seed"), int):
            raise _err(
                f"{case_id} has no integer 'degradation_seed', so its degraded image would not be "
                "reproducible.",
                path=gt_path,
                key_path=f"{case_id}.degradation_seed",
                expected="an integer, e.g. 'degradation_seed: 9821'.",
                recover=f"add a 'degradation_seed:' to {case_id}",
            )
        errors = validate_entry(case_id, entry)
        failures.extend(errors)
        if not errors:
            if dtype == "bank_statements":
                try:
                    failures.extend(check_statement(case_id, entry["fields"], pools, source=gt_path))
                except BankLedgerError as exc:
                    failures.append(str(exc))
            try:
                values = draw_all_eligible({**entry, "case_id": case_id}, canonical)
                check_distractor_values(
                    values,
                    entry["fields"],
                    canonical,
                    distractor_config,  # type: ignore[arg-type]
                    case_id=case_id,
                )
            except (DistractorProviderError, DistractorValueError) as exc:
                failures.append(str(exc))

        fields = entry.get("fields", {}) or {}
        doc_type = str(fields.get("DOCUMENT_TYPE", ""))
        projected = project_fields(case_id, fields, doc_type, schema, gt_path)
        filename = f"{case_id}_{schema.resolve_doc_type(doc_type)}.png"
        if filename in seen:
            raise _err(
                f"two documents would both be exported as '{filename}' ({seen[filename]} and "
                f"{case_id} / {layout_ref}).",
                path=gt_path,
                key_path=f"{case_id}.fields.DOCUMENT_TYPE",
                expected="exactly one document per case per extraction document type.",
                recover=f"remove or re-type the duplicate entry for {case_id}",
            )
        seen[filename] = f"{case_id} / {layout_ref}"
        entries.append((case_id, entry, filename, projected))

    try:
        plain_drawn = build_check(dtype, gt_path, layout_path, gt_data)
        varied_drawn = build_check(dtype, gt_path, layout_path, gt_data, render=LABELLED_VARIED_MODE)
    except (DrawnFieldsError, DrawnValuePolicyError) as exc:
        failures.append(str(exc))
        return None
    bounds = distractor_config.density[canonical]  # type: ignore[attr-defined]
    return _TypePlan(
        dtype=dtype,
        canonical=canonical,
        renderer=renderer,
        layouts=layouts,
        entries=entries,
        plain_drawn=plain_drawn,
        varied_drawn=varied_drawn,
        density=DensityCheck(bounds, doc_type=canonical, config_path=DISTRACTORS_PATH),
        varied_density=DensityCheck(
            bounds, doc_type=canonical, config_path=DISTRACTORS_PATH, render=LABELLED_VARIED_MODE
        ),
    )


def _attempt(failures: list[str], where: str, draw: Callable[[], object]) -> object | None:
    """Run one render, collecting a fit, overlay or label refusal as a failure instead of raising."""
    try:
        return draw()
    except FitError as exc:
        failures.append(f"{where}: {str(exc).splitlines()[0]}")
    except OverlayError as exc:
        failures.append(f"{where}: overlay refused:\n{exc}")
    except LabelRefusalError as exc:
        failures.append(f"{where}: label refused:\n{exc}")
    return None


def _geometry_record(case_id: str, filename: str, geometry: dict) -> dict:
    """The geometry row `generate` writes, under the generic filename: scored boxes only."""
    return {
        "case_id": case_id,
        "image_file": filename,
        "width": geometry["width"],
        "height": geometry["height"],
        "boxes": geometry["boxes"],
    }


def _render_type(
    plan: _TypePlan,
    *,
    half_dirs: dict[str, Path],
    tiers: list[Tier],
    label_config: LabelConfig,
    out: _Rendered,
) -> None:
    """Render one type's cases into the staging halves, checking every render and collecting failures."""
    for half in CLEAN_HALVES:
        out.counts[half].setdefault(plan.canonical, 0)
    for case_id, entry, filename, projected in plan.entries:
        layout_id = str(entry.get("layout", ""))
        layout = plan.layouts[layout_id]
        where = f"{case_id} / {layout_id}"

        def fresh() -> dict:
            return {**entry, "case_id": case_id}

        plain_geometry: dict = {}
        plain = _attempt(
            out.failures,
            f"{where} (plain)",
            lambda: plan.renderer(fresh(), layout, distractors=False, geometry_out=plain_geometry),
        )
        distractor_geometry: dict = {}
        distractor = _attempt(
            out.failures,
            f"{where} (with distractors)",
            lambda: plan.renderer(fresh(), layout, distractors=True, geometry_out=distractor_geometry),
        )
        varied_geometry: dict = {}
        varied = _attempt(
            out.failures,
            f"{where} ({LABELLED_VARIED_MODE})",
            lambda: render_labelled_varied(
                plan.renderer,
                fresh(),
                layout,
                doc_type=plan.canonical,
                config=label_config,
                geometry_out=varied_geometry,
            ),
        )
        if plain is None or distractor is None or varied is None:
            continue
        varied_image, outcome = cast(tuple[Image.Image, LabelOutcome], varied)

        # The checks `validate` runs, on the very renders that ship.
        plan.plain_drawn(case_id, layout_id, plain_geometry)
        if any(with_distractors for _, with_distractors in render_modes(layout)):
            plan.density(case_id, layout_id, distractor_geometry)
        if label_sites(layout):
            plan.varied_density(case_id, layout_id, varied_geometry)
            plan.varied_drawn(case_id, layout_id, varied_geometry)

        renders = {PLAIN: (plain, plain_geometry), DISTRACTOR: (distractor, distractor_geometry)}
        renders[LABELLED_VARIED] = (varied_image, varied_geometry)
        for half, (image, geometry) in renders.items():
            image.save(half_dirs[half] / filename)  # type: ignore[attr-defined]
            out.geometry[half].append(_geometry_record(case_id, filename, geometry))
            out.density[half][filename] = density_report(geometry["ink"], int(geometry["height"]))
            out.counts[half][plan.canonical] += 1
        out.documents.append({"filename": filename, "fields": projected})
        out.sidecar_rows.append(
            sidecar_row(
                image_file=filename,
                case_id=case_id,
                doc_type=plan.canonical,
                layout_id=layout_id,
                outcome=outcome,
                geometry=varied_geometry,
            )
        )

        if plan.canonical != _DEGRADED_TYPE:
            continue
        # Decision 7: the degraded half photographs the distractor receipt.
        seed = int(entry["degradation_seed"])
        for index, tier in enumerate(tiers):
            variant = f"{filename.removesuffix('.png')}_{tier.suffix}.png"
            degrade_receipt(distractor, tier, tier_seed(seed, index)).save(  # type: ignore[arg-type]
                half_dirs[DEGRADED] / variant
            )
            out.degraded_documents.append({"filename": variant, "fields": projected})
            out.counts[DEGRADED][plan.canonical] = out.counts[DEGRADED].get(plan.canonical, 0) + 1


def _write_ground_truth(config: SnapshotConfig, half_dirs: dict[str, Path], out: _Rendered) -> None:
    """Write each half's ground truth: one file for the clean halves, copied byte for byte."""
    documents = sorted(out.documents, key=lambda doc: doc["filename"])
    plain_dir = half_dirs[PLAIN]
    jsonl = write_jsonl(documents, plain_dir / config.jsonl_name)
    csv_path = csv_from_jsonl(jsonl, plain_dir / config.csv_name)
    for half in (DISTRACTOR, LABELLED_VARIED):
        shutil.copyfile(jsonl, half_dirs[half] / config.jsonl_name)
        shutil.copyfile(csv_path, half_dirs[half] / config.csv_name)
    degraded = sorted(out.degraded_documents, key=lambda doc: doc["filename"])
    degraded_jsonl = write_jsonl(degraded, half_dirs[DEGRADED] / config.jsonl_name)
    csv_from_jsonl(degraded_jsonl, half_dirs[DEGRADED] / config.csv_name)


def _write_geometry(config: SnapshotConfig, staging: Path, out: _Rendered) -> None:
    """Write one geometry file per clean half, rows sorted by image_file."""
    geometry_dir = staging / config.geometry_dir
    geometry_dir.mkdir()
    for half in CLEAN_HALVES:
        rows = sorted(out.geometry[half], key=lambda row: row["image_file"])
        text = "".join(json.dumps(row) + "\n" for row in rows)
        (geometry_dir / f"{config.half_prefixes[half]}.jsonl").write_text(text)


def export_snapshot(
    config_path: Path,
    *,
    today: date | None = None,
    renderers: dict | None = None,
    repo_state: Callable[[], RepoState] | None = None,
) -> SnapshotSummary:
    """Render and publish one dated snapshot of the four paired halves.

    Args:
        config_path: Path to generation_config.yml.
        today: The date to stamp; None means the real clock.
        renderers: Document type -> renderer; None means the pipeline's registry.
        repo_state: Reads the repository state; None means the real repository at the generator's root.
            Injectable so tests run from a working tree under development.

    Returns:
        The summary of what was published.

    Raises:
        FileNotFoundError: The generation config or a ground-truth file is missing.
        ValueError: The configuration, a layout reference, a seed or a projection is invalid.
        RepoStateError: Git or the repository is missing, or the tree carries tracked changes.
        SnapshotError: A check failed, or the repository changed during the export.
        TierConfigError: The receipt_degradation block is missing or malformed.
    """
    config = load_snapshot_config(config_path)
    schema = load_extraction_schema()
    if renderers is None:
        from generators.pipeline import _RENDERERS

        renderers = _RENDERERS
    read_state = repo_state if repo_state is not None else lambda: read_repo_state(REPO_ROOT)
    start = read_state()
    require_clean(start, repo_root=REPO_ROOT)

    stamp = (today or date.today()).strftime(_DATE_FORMAT)
    name = f"{config.snapshot_dir_prefix}_{stamp}"
    final = config.output_root / name
    staging = config.output_root / f".{name}{_STAGING_SUFFIX}"

    tiers = load_tiers(config.config_path)
    data = yaml.safe_load(config.config_path.read_text())
    pools = load_pools()
    distractor_config = load_distractor_config(DISTRACTORS_PATH)
    label_config = load_label_config()

    failures: list[str] = []
    seen: dict[str, str] = {}
    plans = [
        _plan_type(
            dtype,
            data["document_types"][dtype],
            config=config,
            schema=schema,
            renderers=renderers,
            pools=pools,
            distractor_config=distractor_config,
            seen=seen,
            failures=failures,
        )
        for dtype in config.document_types
    ]
    if failures:
        raise _checks_failed(failures, stage="before rendering", config_path=config.config_path)

    if staging.exists():
        # A failed run removes its own staging directory; one left by a killed process is cleared.
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        half_dirs = {half: staging / f"{config.half_prefixes[half]}_{stamp}" for half in HALVES}
        for directory in half_dirs.values():
            directory.mkdir()
        out = _Rendered()
        for plan in plans:
            assert plan is not None  # a None plan recorded a failure above
            _render_type(plan, half_dirs=half_dirs, tiers=tiers, label_config=label_config, out=out)
            out.failures.extend(plan.plain_drawn.errors + plan.density.errors)
            out.failures.extend(plan.varied_density.errors + plan.varied_drawn.errors)
        if out.failures:
            raise _checks_failed(out.failures, stage="while rendering", config_path=config.config_path)

        _write_ground_truth(config, half_dirs, out)
        rows = sorted(out.sidecar_rows, key=lambda row: row["image_file"])
        write_sidecar(half_dirs[LABELLED_VARIED], rows)
        _write_geometry(config, staging, out)

        end = read_state()
        if end != start:
            raise _state_changed(start, end)
        if final.exists():
            # The project owner's ruling: a same-day re-export replaces the earlier snapshot.
            shutil.rmtree(final)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return SnapshotSummary(
        path=final,
        stamp=stamp,
        repo_state=start,
        half_dirs={half: final / directory.name for half, directory in half_dirs.items()},
        counts=out.counts,
        sidecar_rows=tuple(rows),
        density=out.density,
    )


__all__ = ["RepoStateError", "SnapshotError", "SnapshotSummary", "export_snapshot"]
