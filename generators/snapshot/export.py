"""Export a dated snapshot of four paired halves, rendered in one run from a recorded tree.

`export_snapshot` writes one directory, `<eval_set.output_root>/<snapshot_dir_prefix>_<YYYYMMDD>/`::

    plain_<YYYYMMDD>/            every case of every type, rendered without distractors
    distractor_<YYYYMMDD>/       the same pages with their distractors, under the same filenames
    labelled_varied_<YYYYMMDD>/  the distractor pages with their ABN, GST and total labels varied,
                                 plus label_styles.jsonl (one row per image, sorted by image_file)
    degraded_<YYYYMMDD>/         each distractor receipt photographed at every declared tier
    geometry/                    plain.jsonl, distractor.jsonl, labelled_varied.jsonl: scored boxes
    tools/                       verify_snapshot.py, check_extractions_match_gt.py, score_extractions.ipynb
    README.md                    generated from the manifest (generators/snapshot/readme.py)
    MANIFEST.json, SHA256SUMS    what the snapshot holds and every file's SHA-256 (snapshot/manifest.py)

(directory names from `eval_set.half_prefixes` and `eval_set.geometry_dir`). The three clean halves
share filenames and byte-identical ground truth, so pairing them is a direct join; degraded filenames
carry their tier suffix and their own receipt-only ground truth.

The snapshot is published as is, so nothing in it says how it was produced. That is written beside it,
in `<snapshot dir>.<eval_set.provenance_suffix>/`, which is never uploaded::

    PROVENANCE.json              the commit, input hashes, library versions, density metrics, the
                                 manifest's own SHA-256 and the earlier snapshot this one follows
    working_tree.patch           only when the tree was modified: what differed from the commit

The order is fixed, and each step refuses before the next begins:

1. The configuration and the README's field descriptions are checked and the output root resolved
   (Decisions 3 and 13).
2. The repository state is read: the commit, every changed or untracked path, and the patch. A modified
   tree is not refused (Decision 4, revised 9 October 2026); it is recorded.
3. The cheap checks run on every entry and layout before anything is drawn: the entry schema, the bank
   ledger, the distractor values, and each layout's DSL body (Decision 8). Every failure is collected and
   refused as one; the output root is untouched.
4. Every case is rendered plain, with distractors and labelled varied (Decision 5: nothing is read from
   `generate`'s output), and each distractor receipt degraded once per tier (Decision 7), all into a
   staging directory `<root>/.<snapshot_dir_prefix>_<YYYYMMDD>.partial`. The density bounds are checked
   on every distractor and labelled varied render, the drawn-field guard on every plain and labelled
   varied render; overlay and unambiguity refusals are raised inside the renderers. Plain renders are
   measured, never bounded. Every failure is collected, and any refuses the export as one.
5. Ground truth, sidecar, geometry and tools are written; then the README, from the manifest without
   its `files`; then every file is hashed and MANIFEST.json and SHA256SUMS written last.
6. Every file name and non-image file in the staging directory is scanned for `eval_set.forbidden_terms`;
   one hit refuses the export. The provenance record (and the patch, when the tree was modified) is
   written into its own staging directory beside the snapshot's.
7. The repository state is read again. Any difference from the first read (a moved HEAD, a path changed,
   reverted or edited again) means the tree changed during the export, so the snapshot would mix two
   states; the staging directories are discarded and the export fails.
8. An earlier snapshot of the same name is replaced (the project owner's ruling): it is renamed aside,
   the staging directory renamed into place, and only then the earlier one deleted, so a failed swap
   restores it. Its provenance directory is replaced the same way, after it. A leftover staging
   directory from a failed run is cleared at the start of the next.

Any failure after a staging directory exists removes it, so a refused or interrupted export leaves no
partial snapshot behind.
"""

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

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
from generators.labels.render import LabelRefusalError, label_sites, render_labelled_varied
from generators.labels.sidecar import sidecar_row, write_sidecar
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import REPO_ROOT, load_ground_truth, load_layout_registry
from generators.overflow_check import LABELLED_VARIED_MODE, render_modes
from generators.overlay.verify import OverlayError
from generators.schema import field_names_for, validate_entry
from generators.snapshot.config import HALVES, SnapshotConfig, load_snapshot_config
from generators.snapshot.manifest import (
    MANIFEST_NAME,
    README_NAME,
    TOOLS_DIR,
    build_manifest,
    describe_provenance,
    describe_snapshot,
    sha256_file,
    write_checksums,
    write_manifest,
    write_provenance,
)
from generators.snapshot.publication import find_forbidden, refusal
from generators.snapshot.readme import find_previous, load_field_descriptions, render_readme
from generators.snapshot.repo_state import RepoState, RepoStateError, TreeChange, read_repo_state
from generators.snapshot.summary import SnapshotSummary

# The three halves rendered directly (the fourth, degraded, is made from the distractor render).
CLEAN_HALVES = HALVES[:3]
PLAIN, DISTRACTOR, LABELLED_VARIED, DEGRADED = HALVES

# The suffix of the staging directory beside the destination; it is a contract with the spec.
_STAGING_SUFFIX = ".partial"
# The suffix an earlier snapshot of the same name is renamed to while the new one is swapped in.
_ASIDE_SUFFIX = ".old"

# The tools every snapshot ships (Decision 12): two scripts copied byte for byte, and the scoring
# notebook from the repository root with its outputs cleared.
SHIPPED_SCRIPTS = ("verify_snapshot.py", "check_extractions_match_gt.py")
SHIPPED_NOTEBOOK = "score_extractions.ipynb"


class SnapshotError(RuntimeError):
    """The export refused: a check failed, or the repository changed while it ran."""


# The most changed paths a mid-export refusal names; the total is always given.
_LISTED_PATHS = 10


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


def _changed_paths(start: RepoState, end: RepoState) -> list[str]:
    """The paths whose recorded change differs between two reads, sorted."""
    before: dict[str, TreeChange] = {change.path: change for change in start.changes}
    after: dict[str, TreeChange] = {change.path: change for change in end.changes}
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def _state_changed(start: RepoState, end: RepoState) -> SnapshotError:
    """Build the refusal for a repository that changed between the start and the publish."""
    changes = []
    if end.commit != start.commit:
        changes.append(f"HEAD moved from {start.commit} to {end.commit}")
    paths = _changed_paths(start, end)
    if paths:
        listed = ", ".join(paths[:_LISTED_PATHS])
        more = f" (and {len(paths) - _LISTED_PATHS} more)" if len(paths) > _LISTED_PATHS else ""
        changes.append(f"{len(paths)} path(s) changed during the export: {listed}{more}")
    if not changes:
        changes.append("the working-tree patch changed during the export")
    return SnapshotError(
        "The repository changed while the snapshot was rendered; nothing was published.\n"
        f"  What:     {'; '.join(changes)}. The snapshot would mix two states of the tree, so neither "
        "its commit nor its working_tree.patch would describe what produced it.\n"
        f"  Where:    {REPO_ROOT}\n"
        "  Expected: the same HEAD and the same git status --porcelain --untracked-files=all (with the "
        "same file contents) from the start of the export to its publish; a modified tree is fine, a "
        "tree modified during the export is not.\n"
        "  Recover:  leave the repository untouched while the export runs (no edit, commit, stash or "
        "checkout), then export again."
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


def _attempt[T](failures: list[str], where: str, draw: Callable[[], T]) -> T | None:
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
        varied_image, outcome = varied

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
            image.save(half_dirs[half] / filename)
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
            degrade_receipt(distractor, tier, tier_seed(seed, index)).save(half_dirs[DEGRADED] / variant)
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


def _write_tools(staging: Path) -> None:
    """Copy the three tools into `tools/`: two scripts byte for byte, the notebook with outputs cleared.

    The notebook ships with every output removed and every `execution_count` null (Decision 12), so no
    earlier run's numbers read as this snapshot's.
    """
    tools = staging / TOOLS_DIR
    tools.mkdir()
    for name in SHIPPED_SCRIPTS:
        shutil.copyfile(REPO_ROOT / "scripts" / name, tools / name)
    notebook = json.loads((REPO_ROOT / SHIPPED_NOTEBOOK).read_text())
    for cell in notebook["cells"]:
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
    (tools / SHIPPED_NOTEBOOK).write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n")


def _clear_staging(staging: Path) -> None:
    """Clear a staging directory a killed run left behind; refuse anything else at that path."""
    if staging.is_dir() and not staging.is_symlink():
        shutil.rmtree(staging)
    elif staging.exists() or staging.is_symlink():
        raise SnapshotError(
            "The snapshot's staging path is occupied by something other than a directory.\n"
            f"  What:     {staging} exists and is not a directory, so the export cannot build there.\n"
            f"  Where:    {staging}\n"
            "  Expected: nothing at that path, or a staging directory left by an interrupted export.\n"
            f"  Recover:  move or delete {staging}, then export again."
        )


def _publish(staging: Path, final: Path) -> None:
    """Swap the staging directory into place, keeping any earlier snapshot until the new one is there.

    An earlier snapshot of the same name is renamed aside, the staging directory renamed into place, and
    only then the earlier one deleted; if the second rename fails, the earlier one is restored.
    """
    aside = final.parent / f".{final.name}{_ASIDE_SUFFIX}"
    if aside.exists():
        shutil.rmtree(aside)
    if final.exists():
        final.rename(aside)
    try:
        staging.rename(final)
    except BaseException:
        if aside.exists() and not final.exists():
            aside.rename(final)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def export_snapshot(
    config_path: Path,
    *,
    today: date | None = None,
    renderers: dict | None = None,
    repo_state: Callable[[], RepoState] | None = None,
) -> SnapshotSummary:
    """Render and publish one dated snapshot of the four paired halves, documented and checksummed.

    Args:
        config_path: Path to generation_config.yml.
        today: The date to stamp; None means the real clock.
        renderers: Document type -> renderer; None means the pipeline's registry.
        repo_state: Reads the repository state; None means the real repository at the generator's root.
            Injectable so tests run from a working tree under development.

    Returns:
        The summary of what was published, with the published MANIFEST.json's own SHA-256.

    Raises:
        FileNotFoundError: The generation config or a ground-truth file is missing.
        ValueError: The configuration, a field description, a layout reference, a seed or a projection
            is invalid.
        RepoStateError: Git or the repository is missing, or its state cannot be read.
        SnapshotError: A check failed, the staging path is occupied, or the repository changed during
            the export.
        TierConfigError: The receipt_degradation block is missing or malformed.
    """
    config = load_snapshot_config(config_path)
    schema = load_extraction_schema()
    load_field_descriptions(schema)  # refused now, before anything is drawn, not at the README
    if renderers is None:
        from generators.pipeline import _RENDERERS

        renderers = _RENDERERS
    read_state = (
        repo_state
        if repo_state is not None
        else lambda: read_repo_state(REPO_ROOT, exclude=(config.output_root,))
    )
    start = read_state()

    stamp = (today or date.today()).strftime(_DATE_FORMAT)
    name = f"{config.snapshot_dir_prefix}_{stamp}"
    final = config.output_root / name
    staging = config.output_root / f".{name}{_STAGING_SUFFIX}"
    provenance_name = f"{name}.{config.provenance_suffix}"
    provenance_final = config.output_root / provenance_name
    provenance_staging = config.output_root / f".{provenance_name}{_STAGING_SUFFIX}"

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
    ready = [plan for plan in plans if plan is not None]

    _clear_staging(staging)
    _clear_staging(provenance_staging)
    staging.mkdir()
    try:
        half_dirs = {half: staging / f"{config.half_prefixes[half]}_{stamp}" for half in HALVES}
        for directory in half_dirs.values():
            directory.mkdir()
        out = _Rendered()
        for plan in ready:
            _render_type(plan, half_dirs=half_dirs, tiers=tiers, label_config=label_config, out=out)
            out.failures.extend(plan.plain_drawn.errors + plan.density.errors)
            out.failures.extend(plan.varied_density.errors + plan.varied_drawn.errors)
        if out.failures:
            raise _checks_failed(out.failures, stage="while rendering", config_path=config.config_path)

        _write_ground_truth(config, half_dirs, out)
        rows = sorted(out.sidecar_rows, key=lambda row: row["image_file"])
        write_sidecar(half_dirs[LABELLED_VARIED], rows)
        _write_geometry(config, staging, out)
        _write_tools(staging)

        staged = SnapshotSummary(
            path=final,  # the name it is published under; its files are still in staging
            stamp=stamp,
            repo_state=start,
            half_dirs=half_dirs,
            counts=out.counts,
            sidecar_rows=tuple(rows),
            density=out.density,
            tiers=tuple((tier.name, tier.suffix) for tier in tiers),
            geometry_dir=config.geometry_dir,
            csv_name=config.csv_name,
            jsonl_name=config.jsonl_name,
        )
        # The README is generated from the manifest without its files, then every file is hashed.
        description = describe_snapshot(staged)
        (staging / README_NAME).write_text(render_readme(description, schema=schema))
        manifest = build_manifest(staged, snapshot_root=staging)
        write_manifest(manifest, staging)
        write_checksums(manifest, staging)
        manifest_sha256 = sha256_file(staging / MANIFEST_NAME)

        hits = find_forbidden(staging, config.forbidden_terms)
        if hits:
            raise SnapshotError(refusal(hits, root=staging, config_path=config.config_path))

        previous = find_previous(
            config.output_root,
            prefix=config.snapshot_dir_prefix,
            suffix=config.provenance_suffix,
            current_dir=name,
            head=start.commit,
            repo_root=REPO_ROOT,
        )
        record = describe_provenance(
            staged,
            repo_state=start,
            repo_root=REPO_ROOT,
            manifest_sha256=manifest_sha256,
            previous=previous.as_record() if previous is not None else None,
        )
        provenance_staging.mkdir()
        write_provenance(record, provenance_staging, patch=start.patch if start.dirty else None)

        end = read_state()
        if end != start:
            raise _state_changed(start, end)
        _publish(staging, final)
        _publish(provenance_staging, provenance_final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(provenance_staging, ignore_errors=True)
        raise

    return replace(
        staged,
        half_dirs={half: final / directory.name for half, directory in half_dirs.items()},
        manifest_sha256=manifest_sha256,
        provenance_dir=provenance_final,
    )


__all__ = ["RepoStateError", "SnapshotError", "SnapshotSummary", "export_snapshot"]
