"""Pipeline CLI — generate, derive, validate synthetic documents.

Usage:
    python -m generators.pipeline generate --config config/generation_config.yml
    python -m generators.pipeline derive --config config/generation_config.yml
    python -m generators.pipeline validate --config config/generation_config.yml
"""

import json
from pathlib import Path

import typer
from rich import print as rprint

from generators.bank_ledger import BankLedgerError, check_statement
from generators.bank_statement import render_bank_statement
from generators.common import FitError
from generators.content_engine import load_pools, reachable_blocked_names
from generators.derive_outputs import (
    derive_cord,
    derive_csv,
    derive_docile,
    derive_jsonl,
    derive_links,
    derive_native,
)
from generators.distractors.config import load_distractor_config
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH
from generators.distractors.providers import DistractorProviderError, draw_all_eligible
from generators.distractors.validator import DistractorValueError, check_distractor_values
from generators.drawn_fields import DrawnFieldsError
from generators.drawn_fields import build_check as build_drawn_check
from generators.drawn_values import DrawnValuePolicyError
from generators.exporters.config import load_export_config
from generators.exporters.eval_projection import load_extraction_schema
from generators.invoice import render_invoice
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import (
    CLEAN_SUBDIR,
    load_generation_config,
    load_ground_truth,
    load_layout_registry,
)
from generators.labels.config import load_label_config
from generators.labels.keys import LABEL_STYLES
from generators.labels.render import LabelRefusalError, render_labelled_varied
from generators.labels.sidecar import SIDECAR_NAME, sidecar_row, summarise, write_sidecar
from generators.overflow_check import (
    LABELLED_VARIED_MODE,
    LabelledVaried,
    build_overflow_error,
    check_overflow,
)
from generators.payment_block import (
    load_link_index,
    load_terminal_pools,
    method_from_bank_description,
)
from generators.parsing.config import (
    PARSING_CONFIG_PATH,
    ParsingConfig,
    ParsingConfigError,
    layout_parse_errors,
    load_parsing_config,
)
from generators.parsing.omnidocbench import PageCheck
from generators.receipt import render_receipt
from generators.schema import field_names_for, validate_entry
from generators.typst.config import (
    TYPESET_CONFIG_PATH,
    TypesetConfig,
    TypesetConfigError,
    load_typeset_config,
)
from generators.typst.validate import StageOnePages, typeset_errors

app = typer.Typer(help="Synthetic Australian business document generator.")

_RENDERERS = {
    "bank_statements": render_bank_statement,
    "receipts": render_receipt,
    "invoices": render_invoice,
}

_DEFAULT_CONFIG = Path("config/generation_config.yml")


@app.command()
def validate(
    config: Path = typer.Option(_DEFAULT_CONFIG, help="Path to generation_config.yml"),
) -> None:
    """Check every ground-truth entry before anything is rendered.

    Covers required fields per document type, ABN checksums, date and amount
    formats, equal item counts across parallel pipe-delimited fields, GST as one
    eleventh of a GST-inclusive total, that each `layout:` names a layout the
    registry actually holds, that the DSL bodies are well formed, that no text
    overflows its budget (rendered plain, and with distractors for a layout that
    carries a distractor provider), that every scored ground-truth value is drawn by its layout (the
    plain render records a box for it, one per list member, holding a string that reads as the ground
    truth under `drawn_value_policy`; see `geometry_exemptions` in config/field_definitions.yml),
    that every such distractor render meets its
    document type's density bounds in config/distractors.yml (plain renders are
    the control and are never bounded), that every receipt and invoice also renders labelled varied
    (its ABN, GST and total labels drawn per case as labelled, variant or unlabelled, every choice a
    careful reader could misread refused and redrawn under label_variation.refusal_policy) with every
    scored value drawn and within the same density bounds, that every
    entry's distractor values (drawn with every catalogue item eligible for its
    document type) pass the value validator, that every bank statement row sits in the column its
    description says and its running balance stays at or above its account's floor (config/data_pools.yml
    bank_descriptions and bank_balance), that the OmniDocBench page each plain and distractor render writes
    keeps the parsing invariants (generators/parsing/omnidocbench.py, config/parsing_config.yml), that
    every case typeset as a native PDF, plain and with distractors (config/typeset_config.yml), keeps the
    same invariants and the layout checks, says block for block what stage one's page says, and with
    distractors meets the density bounds and places every overlay legally on its own geometry
    (generators/typst/validate.py), and that the business-name grammar cannot emit a real company.

    Errors are collected rather than raised one at a time, so a single run
    reports every problem in the corpus.
    """
    cfg = load_generation_config(config)
    all_errors: list[str] = []
    distractor_config = load_distractor_config()
    schema = load_extraction_schema()
    pools = load_pools()
    parsing_config: ParsingConfig | None = None
    try:
        parsing_config = load_parsing_config(PARSING_CONFIG_PATH)
    except ParsingConfigError as exc:
        all_errors.append(str(exc))
    typeset_config: TypesetConfig | None = None
    try:
        typeset_config = load_typeset_config(TYPESET_CONFIG_PATH)
    except TypesetConfigError as exc:
        all_errors.append(str(exc))

    for doc_type, doc_cfg in cfg.get("document_types", {}).items():
        gt_path = Path(doc_cfg["ground_truth"])
        if not gt_path.exists():
            all_errors.append(f"{doc_type}: ground truth not found at {gt_path}")
            continue

        gt_data = load_ground_truth(gt_path)

        layouts: dict = {}
        if "layouts" in doc_cfg:
            layout_path = Path(doc_cfg["layouts"])
            if layout_path.exists():
                layouts = load_layout_registry(layout_path)

        canonical = schema.resolve_doc_type(doc_type)
        for case_id, entry in gt_data.items():
            errors = validate_entry(str(case_id), entry)
            all_errors.extend(errors)

            # Every statement row sits in the column its description says, and the running balance stays
            # at or above its account's floor (generators/bank_ledger.py, rules in config/data_pools.yml).
            if doc_type == "bank_statements" and not errors:
                try:
                    all_errors.extend(
                        check_statement(str(case_id), entry["fields"], pools, source=gt_path.resolve())
                    )
                except BankLedgerError as exc:
                    all_errors.append(str(exc))

            # Every catalogue item eligible for the type is drawn on every entry and the values are
            # checked, so a catalogue or ground-truth edit that lets a distractor reproduce a scored
            # value fails here rather than mid-render. Skipped for an entry already in error, whose
            # malformed fields would only produce a second, less useful diagnostic.
            if not errors:
                try:
                    values = draw_all_eligible({**entry, "case_id": str(case_id)}, canonical)
                    check_distractor_values(
                        values, entry["fields"], canonical, distractor_config, case_id=str(case_id)
                    )
                except (DistractorProviderError, DistractorValueError) as exc:
                    all_errors.append(str(exc))

            layout_ref = entry.get("layout", "")
            if layouts and layout_ref not in layouts:
                all_errors.append(
                    f"{case_id}: layout '{layout_ref}' not found in "
                    f"{doc_cfg.get('layouts')}. "
                    f"Available layouts: {sorted(layouts.keys())}"
                )

        # Every layout body is structurally validated before any rendering, so a
        # malformed primitive or unknown field reference fails here rather than
        # part-way through a 330-image generate run.
        layout_errors: list[str] = []
        if layouts:
            known = set(field_names_for(doc_type))
            for layout_id, layout in layouts.items():
                if "body" not in layout:
                    continue
                try:
                    validate_layout(
                        layout,
                        layout_id=layout_id,
                        layout_path=str(doc_cfg["layouts"]),
                        known_fields=known,
                        doc_type=doc_type,
                    )
                except LayoutSchemaError as exc:
                    layout_errors.append(str(exc))
            if parsing_config is not None:
                layout_errors.extend(
                    layout_parse_errors(layouts, layout_path=str(doc_cfg["layouts"]), config=parsing_config)
                )
        all_errors.extend(layout_errors)

        # Overflow backstop: render each entry plain, and with distractors where its layout
        # carries a distractor provider, and surface any content that cannot fit its box even
        # after lossless wrap/shrink (a real design error); a distractor value only widens a
        # block when distractors are drawn.
        # Skipped when layout validation already found a structural problem —
        # rendering against a known-broken body raises an unrelated exception
        # (e.g. an unbound field reference) instead of a useful fit diagnostic.
        renderer = _RENDERERS.get(doc_type)
        # Density is measured on the overflow pass's own distractor renders, so each page is drawn
        # once per mode.
        if renderer and layouts and not layout_errors:
            density = DensityCheck(
                distractor_config.density[canonical], doc_type=canonical, config_path=DISTRACTORS_PATH
            )
            # The plain render is also the one a scored value must be drawn on (a recorded box).
            try:
                drawn = build_drawn_check(
                    doc_type, gt_path, Path(doc_cfg["layouts"]), {str(k): v for k, v in gt_data.items()}
                )
            except (DrawnFieldsError, DrawnValuePolicyError) as exc:
                all_errors.append(str(exc))
                drawn = None
            # The labelled varied render (a distractor render whose ABN, GST and total labels vary per
            # case) is checked as the distractor render is, by the drawn-field guard and the density
            # bounds; its unambiguity rules run inside the render, and a refusal under redraw is no error.
            # A layout without label sites (a bank statement) is not rendered a third time.
            varied_density = DensityCheck(
                distractor_config.density[canonical],
                doc_type=canonical,
                config_path=DISTRACTORS_PATH,
                render=LABELLED_VARIED_MODE,
            )
            varied_drawn = None
            if drawn is not None:
                varied_drawn = build_drawn_check(
                    doc_type,
                    gt_path,
                    Path(doc_cfg["layouts"]),
                    {str(k): v for k, v in gt_data.items()},
                    render=LABELLED_VARIED_MODE,
                )
            varied = LabelledVaried(
                doc_type=canonical,
                config=load_label_config(),
                measures=(varied_density,) + ((varied_drawn,) if varied_drawn is not None else ()),
            )
            # Every plain and distractor render also writes its OmniDocBench page, which must keep the
            # parsing invariants; each page check hands the render on to the measure it wraps first.
            plain_page = distractor_page = None
            stage_one: dict[tuple[str, bool], dict] = {}
            if parsing_config is not None:
                plain_page = PageCheck(parsing_config, doc_type=canonical, mode="plain", then=drawn)
                distractor_page = PageCheck(
                    parsing_config, doc_type=canonical, mode="distractor", then=density
                )
            all_errors.extend(
                check_overflow(
                    gt_data,
                    layouts,
                    renderer,
                    # Each render is also kept, as the page its typeset twin is compared with below.
                    measure=StageOnePages(stage_one, distractors=True, then=distractor_page or density),
                    plain_measure=StageOnePages(stage_one, distractors=False, then=plain_page or drawn),
                    labelled_varied=varied,
                )
            )
            for page_check in (plain_page, distractor_page):
                if page_check is not None:
                    all_errors.extend(page_check.errors)
            all_errors.extend(density.errors)
            all_errors.extend(varied_density.errors)
            if drawn is not None:
                all_errors.extend(drawn.errors)
            if varied_drawn is not None:
                all_errors.extend(varied_drawn.errors)
            # Every case is also typeset as a native PDF, plain and with distractors, and each page checked
            # as stage one's is (generators/typst/validate.py).
            if parsing_config is not None and typeset_config is not None:
                all_errors.extend(
                    typeset_errors(
                        gt_data,
                        layouts,
                        stage_one,
                        doc_type=canonical,
                        layout_path=Path(doc_cfg["layouts"]).resolve(),
                        parsing=parsing_config,
                        distractor_config=distractor_config,
                        config=typeset_config,
                    )
                )

    # Every linked receipt's bank description must resolve to a card scheme, so a
    # reseed introducing a new description shape fails here rather than silently
    # rendering a receipt that contradicts its bank statement.
    terminal_cfg = load_terminal_pools()
    for stem, description in sorted(load_link_index().items()):
        try:
            method_from_bank_description(description, terminal_cfg)
        except ValueError:
            all_errors.append(
                f"{stem}: bank description '{description}' maps to no card scheme. "
                f"Add a prefix to payment_terminal.bank_description_methods in "
                f"config/data_pools.yml. Known prefixes: "
                f"{sorted(terminal_cfg['bank_description_methods'])}."
            )

    # A pool edit must not make a real business name emittable. The runtime
    # blocklist in fictional_business_name() would still catch it, but only as a
    # retry during seeding; checked here it is a configuration error raised at
    # the moment someone widens business_name_parts.
    for name in reachable_blocked_names(pools):
        all_errors.append(
            f"the business-name grammar can now emit '{name}', a real business on the "
            f"blocklist. A prefix in business_name_parts.surnames or .suburb_prefixes "
            f"combined with a noun in .category_nouns produces it exactly. Rename or "
            f"remove the offending part in config/data_pools.yml so no real name is "
            f"reachable."
        )

    if all_errors:
        rprint(f"[red]Validation failed with {len(all_errors)} error(s):[/red]")
        for err in all_errors:
            rprint(f"  [red]- {err}[/red]")
        raise typer.Exit(1) from None

    rprint("[green]Validation passed.[/green]")


@app.command()
def derive(
    config: Path = typer.Option(_DEFAULT_CONFIG, help="Path to generation_config.yml"),
) -> None:
    """Re-project the ground truth into every derived view.

    Writes `ground_truth.csv` and `.jsonl`, then each benchmark schema listed in
    `export_targets` in config/export_config.yml — CORD, DocILE, native and
    doc_refs. A target absent from that list is skipped, which is how one is
    shipped as a no-op without deleting its key.

    DocILE consumes the per-field bounding boxes `generate` captures into
    `geometry.jsonl` in the configured `derived_dir`, so on a fresh tree `generate` has to run first.
    """
    cfg = load_generation_config(config)
    derived_dir = Path(cfg["derived_dir"])
    derived_dir.mkdir(parents=True, exist_ok=True)

    gt_files: list[Path] = []
    for doc_cfg in cfg.get("document_types", {}).values():
        gt_path = Path(doc_cfg["ground_truth"])
        if gt_path.exists():
            gt_files.append(gt_path)

    formats = cfg.get("derived_formats", {})
    field_defs_path = Path("config/field_definitions.yml")

    if formats.get("csv"):
        csv_path = derive_csv(gt_files, field_defs_path, derived_dir / "ground_truth.csv")
        rprint(f"[green]CSV written: {csv_path}[/green]")

    if formats.get("jsonl"):
        jsonl_path = derive_jsonl(gt_files, derived_dir / "ground_truth.jsonl")
        rprint(f"[green]JSONL written: {jsonl_path}[/green]")

    export_cfg = load_export_config(Path("config/export_config.yml"))
    if "cord" in export_cfg["export_targets"]:
        cord_path = derive_cord(gt_files, export_cfg, derived_dir / "cord.jsonl")
        rprint(f"[green]CORD JSONL written: {cord_path}[/green]")

    if "doc_refs" in export_cfg["export_targets"]:
        gt_dir = Path(cfg["ground_truth_dir"])
        links_path = derive_links(
            gt_dir / "transaction_links.yml",
            export_cfg,
            derived_dir / "doc_refs.jsonl",
        )
        rprint(f"[green]doc_refs JSONL written: {links_path}[/green]")

    if "docile" in export_cfg["export_targets"]:
        docile_path = derive_docile(
            gt_files,
            derived_dir / "geometry.jsonl",
            export_cfg,
            derived_dir / "docile.jsonl",
        )
        rprint(f"[green]DocILE JSONL written: {docile_path}[/green]")

    if "native" in export_cfg["export_targets"]:
        native_path = derive_native(gt_files, derived_dir / "native.jsonl")
        rprint(f"[green]Native JSONL written: {native_path}[/green]")


@app.command()
def generate(
    config: Path = typer.Option(_DEFAULT_CONFIG, help="Path to generation_config.yml"),
    doc_type: str | None = typer.Option(None, "--type", help="Generate only this document type"),
    distractors: bool = typer.Option(
        False,
        "--distractors",
        help="Render with distractors into <output_dir>/<distractor_output_subdir>/<type>/ instead; "
        "no geometry is written",
    ),
    label_variation: bool = typer.Option(
        False,
        "--label-variation",
        help="With --distractors: vary each page's ABN, GST and total labels, rendering into "
        "<output_dir>/<labelled_varied_output_subdir>/<type>/ with a label_styles.jsonl sidecar",
    ),
) -> None:
    """Generate clean synthetic document images from ground truth YAML.

    Degradation is not part of this command. It applies to receipts only, at
    three severity tiers, and is produced by the `eval-set` export -- see
    generators/degradation/.

    With `--distractors`, every document is rendered with its layout's distractors drawn and saved to
    `<output_dir>/<distractor_output_subdir>/<type>/` for visual review, under the same filename as
    its plain render. Nothing else is written: not the plain images, and not `geometry.jsonl`, whose
    scored boxes come from the plain renders.

    With `--distractors --label-variation`, every document is rendered through
    `render_labelled_varied` (its distractor page with the case's ABN, GST and total label styles, any
    choice a reader could misread refused and redrawn) into
    `<output_dir>/<labelled_varied_output_subdir>/<type>/`, under the same filenames, and each type
    directory gets its `label_styles.jsonl` sidecar. The run prints, per type, how often each style was
    drawn per field and how many choices each rule refused. Nothing else is written.
    """
    cfg = load_generation_config(config)
    if label_variation and not distractors:
        # Echoed plain rather than through rich, which would rewrap the four lines at the terminal width.
        typer.echo(
            "Label variation is layered on a distractor render.\n"
            "  What:     --label-variation was given without --distractors; a labelled varied page is its "
            "distractor page with its labels varied, so there is nothing to vary on a plain render.\n"
            f"  Where:    the generate command line (output: {Path(cfg['output_dir'])}/"
            f"{cfg['labelled_varied_output_subdir']}/; config: {config.resolve()} -> "
            "labelled_varied_output_subdir)\n"
            "  Expected: python -m generators.pipeline generate --distractors --label-variation\n"
            "  Recover:  add --distractors, or drop --label-variation for a plain render.",
            err=True,
        )
        raise typer.Exit(1) from None

    output_dir = Path(cfg["output_dir"])
    derived_dir = Path(cfg["derived_dir"])
    # The plain run writes clean images and geometry; the distractor and labelled varied runs write only
    # their own images (and the latter its sidecars).
    if label_variation:
        image_root = output_dir / cfg["labelled_varied_output_subdir"]
    elif distractors:
        image_root = output_dir / cfg["distractor_output_subdir"]
    else:
        image_root = output_dir / CLEAN_SUBDIR
    label_config = load_label_config() if label_variation else None
    schema = load_extraction_schema() if label_variation else None

    doc_types = cfg.get("document_types", {})
    if doc_type:
        if doc_type not in doc_types:
            rprint(f"[red]Unknown document type '{doc_type}'. Available: {sorted(doc_types.keys())}[/red]")
            raise typer.Exit(1) from None
        doc_types = {doc_type: doc_types[doc_type]}

    geometry_records: list[dict] = []

    for dtype, doc_cfg in doc_types.items():
        renderer = _RENDERERS.get(dtype)
        if not renderer:
            rprint(f"[yellow]No renderer for '{dtype}', skipping.[/yellow]")
            continue

        gt_data = load_ground_truth(Path(doc_cfg["ground_truth"]))
        layouts = load_layout_registry(Path(doc_cfg["layouts"]))
        subdir = doc_cfg.get("output_subdir", dtype)

        # Structurally validate every DSL layout before rendering anything, on
        # the same terms `validate` does (skip layouts with no `body:` — a
        # defensive guard for any future non-DSL type; every surviving layout
        # is DSL-driven today). The DSL's guarantees — required defaults,
        # line_advance role coverage, budget geometry, provider params — are
        # enforced at validate time, so without this a missing key surfaces as
        # a raw traceback part-way through a 165-image run instead of a
        # diagnostic before the first file is written.
        known = set(field_names_for(dtype))
        for layout_id, layout in layouts.items():
            if "body" not in layout:
                continue
            try:
                validate_layout(
                    layout,
                    layout_id=layout_id,
                    layout_path=str(doc_cfg["layouts"]),
                    known_fields=known,
                    doc_type=dtype,
                )
            except LayoutSchemaError as exc:
                rprint(f"[red]{dtype}: layout '{layout_id}' is invalid.[/red]")
                rprint(f"[red]{exc}[/red]")
                raise typer.Exit(1) from None

        image_dir = image_root / subdir
        image_dir.mkdir(parents=True, exist_ok=True)
        if label_config is not None:
            # The directory's images and its sidecar describe one run: nothing earlier survives.
            _clear_labelled_directory(image_dir)

        # `generate_clean` gates the plain images only; a distractor run exists to write its images.
        save_images = distractors or doc_cfg["generate_clean"]

        count = 0
        sidecar_rows: list[dict] = []
        canonical = schema.resolve_doc_type(dtype) if schema is not None else dtype
        for case_id, entry in gt_data.items():
            layout_ref = entry.get("layout", "")
            layout = layouts.get(layout_ref, {})
            if not layout:
                rprint(f"[yellow]Skipping {case_id}: layout '{layout_ref}' not found.[/yellow]")
                continue

            entry["case_id"] = str(case_id)
            geometry_out: dict = {}
            filename = f"{case_id}_{layout_ref}.png"
            try:
                if label_config is not None:
                    varied_geometry: dict = {}
                    img, outcome = render_labelled_varied(
                        renderer,
                        entry,
                        layout,
                        doc_type=canonical,
                        config=label_config,
                        geometry_out=varied_geometry,
                    )
                    sidecar_rows.append(
                        sidecar_row(
                            image_file=filename,
                            case_id=str(case_id),
                            doc_type=canonical,
                            layout_id=str(layout_ref),
                            outcome=outcome,
                            geometry=varied_geometry,
                        )
                    )
                elif distractors:
                    img = renderer(entry, layout, distractors=True)
                else:
                    img = renderer(entry, layout, distractors=False, geometry_out=geometry_out)
            except FitError as exc:
                if label_config is not None:
                    _clear_labelled_directory(image_dir)
                mode = (
                    " (labelled varied)"
                    if label_variation
                    else " (with distractors)"
                    if distractors
                    else ""
                )
                raise build_overflow_error(
                    [f"{case_id} / {layout_ref}{mode}: {str(exc).splitlines()[0]}"]
                ) from None
            except LabelRefusalError as exc:
                # A refused run leaves neither images nor a sidecar, so none disagrees with the other.
                _clear_labelled_directory(image_dir)
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from None

            if save_images:
                img.save(image_dir / filename)

            if geometry_out:
                geometry_records.append(
                    {
                        "case_id": str(case_id),
                        "image_file": filename,
                        "width": geometry_out["width"],
                        "height": geometry_out["height"],
                        "boxes": geometry_out["boxes"],
                    }
                )

            count += 1

        if label_config is not None:
            sidecar = write_sidecar(image_dir, sidecar_rows)
            rprint(f"[green]{dtype}: generated {count} labelled varied documents in:[/green]")
            print(image_dir)
            _print_label_summary(dtype, canonical, sidecar_rows, label_config.doc_types[canonical])
            typer.echo(f"{dtype}: label styles written: {sidecar}")
        elif distractors:
            rprint(f"[green]{dtype}: generated {count} documents with distractors in:[/green]")
            print(image_dir)
        else:
            rprint(f"[green]{dtype}: generated {count} documents.[/green]")

    if geometry_records:
        derived_dir.mkdir(parents=True, exist_ok=True)
        geometry_path = derived_dir / "geometry.jsonl"
        with geometry_path.open("w") as f:
            for record in geometry_records:
                f.write(json.dumps(record) + "\n")
        rprint(f"[green]Geometry written: {geometry_path} ({len(geometry_records)} documents)[/green]")


def _clear_labelled_directory(directory: Path) -> None:
    """Remove a labelled varied type directory's PNGs and its sidecar, so a run leaves one coherent set."""
    for stale in [*directory.glob("*.png"), directory / SIDECAR_NAME]:
        stale.unlink(missing_ok=True)


def _print_label_summary(dtype: str, canonical: str, rows: list[dict], fields: tuple[str, ...]) -> None:
    """Print a type's realised label styles per field and its refusals per rule, one plain line each."""
    styles, rules = summarise(rows)
    if not fields:
        typer.echo(f"  {dtype}: no label varies on a {canonical}.")
    for field in fields:
        counts = ", ".join(f"{style} {styles[(field, style)]}" for style in LABEL_STYLES)
        typer.echo(f"  {dtype}: {field}: {counts}")
    refused = ", ".join(f"{rule} {rules[rule]}" for rule in sorted(rules)) or "none"
    typer.echo(f"  {dtype}: refusals: {refused} ({sum(rules.values())} in all)")


# Registered under both spellings: `eval-set` matches Typer's own convention and
# the other commands, `eval_set` matches how the plan and the module are named.
@app.command("eval-set")
@app.command("eval_set", hidden=True)
def eval_set(
    config: Path = typer.Option(_DEFAULT_CONFIG, help="Path to generation_config.yml"),
) -> None:
    """Export the deliverable: one dated snapshot of four paired halves and two typeset sets.

    Writes `<eval_set.output_root>/<snapshot_dir_prefix>_<date>/`, holding the plain, distractor and
    labelled varied halves (every case of every type under the same generic filenames, with
    byte-identical ground truth), the degraded half (each distractor receipt at every tier, named
    `CASE001_receipt_v1.png` and up, with its own ground truth), the two typeset sets (every case
    typeset plain and with distractors as a native PDF beside the PNG rasterised from it, in
    `config/typeset_config.yml`'s `export_workers` processes, each page checked as `validate` checks
    it, every PDF's Producer `eval_set.pdf_producer`), the labelled varied half's
    label_styles.jsonl, the scored-field geometry of the three clean halves, every half's OmniDocBench
    parsing ground truth, the tools, a README generated from the manifest, MANIFEST.json and SHA256SUMS.
    The snapshot says nothing about how it was produced and is refused if it names a term under
    `eval_set.forbidden_terms`; that is written beside it, in `<snapshot dir>.<provenance_suffix>/`
    (PROVENANCE.json, and working_tree.patch when the tree was modified, which is recorded rather than
    refused). A tree changed during the export is refused. Every render is checked before anything is
    published; a snapshot of the same name is replaced. Prints MANIFEST.json's own SHA-256, which the manifest cannot record about itself.
    """
    from generators.degradation.tiers import TierConfigError
    from generators.drawn_fields import DrawnFieldsError
    from generators.parsing.config import ParsingConfigError
    from generators.snapshot.config import HALVES, TYPESET_SETS
    from generators.snapshot.export import SnapshotError, export_snapshot
    from generators.snapshot.repo_state import RepoStateError

    refusals = (
        ValueError,
        FileNotFoundError,
        RepoStateError,
        SnapshotError,
        TierConfigError,
        DrawnFieldsError,
        ParsingConfigError,
        TypesetConfigError,
    )
    try:
        summary = export_snapshot(config)
    except refusals as exc:
        # Echoed plain rather than through rich, which would rewrap the four-part lines.
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None

    for half in HALVES:
        per_type = ", ".join(
            f"{count} {doc_type}" for doc_type, count in sorted(summary.counts[half].items())
        )
        typer.echo(f"{half}: {summary.total(half)} images ({per_type}) in {summary.half_dirs[half].name}")
    for name in TYPESET_SETS:
        per_type = ", ".join(
            f"{count} {doc_type}" for doc_type, count in sorted(summary.counts[name].items())
        )
        directory = summary.typeset_dirs[name].name
        typer.echo(f"{name}: {summary.total(name)} PDFs and images ({per_type}) in {directory}")
    typer.echo(f"Snapshot: {summary.path}")
    state = summary.repo_state
    if state.dirty:
        typer.echo(
            f"Working tree: modified, {len(state.changes)} path(s) differ from {state.commit[:12]} "
            f"(recorded in {summary.provenance_dir})"
        )
    else:
        typer.echo(f"Working tree: clean at {state.commit[:12]}")
    typer.echo(f"Provenance (keep, never upload): {summary.provenance_dir}")
    typer.echo(f"MANIFEST.json SHA-256: {summary.manifest_sha256} (also in the provenance record)")


@app.command("parsing-probe")
def parsing_probe(
    config: Path = typer.Option(_DEFAULT_CONFIG, help="Path to generation_config.yml"),
) -> None:
    """Export the parsing probe: the pages Decision 12 chooses, with their OmniDocBench ground truth.

    Writes `<eval_set.output_root>/parsing_probe/`: each probe page's image and one `probe.json`, a list
    of OmniDocBench pages, replacing an earlier probe. Every page is checked against the parsing
    invariants first; a page that breaks one publishes nothing.
    """
    from generators.degradation import AugmentationError, TierConfigError
    from generators.parsing.probe import PROBE_JSON, ProbeError, export_probe

    refusals = (
        ProbeError,
        ParsingConfigError,
        ValueError,
        FileNotFoundError,
        FitError,
        TierConfigError,
        AugmentationError,
    )
    try:
        directory = export_probe(config, load_parsing_config(PARSING_CONFIG_PATH))
    except refusals as exc:
        # Echoed plain rather than through rich, which would rewrap the four-part lines.
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    pages = json.loads((directory / PROBE_JSON).read_text())
    typer.echo(f"Probe: {directory}")
    typer.echo(f"{len(pages)} pages")


if __name__ == "__main__":
    app()
