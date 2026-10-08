"""Self-contained evaluation-set export, owned entirely by this repo.

`export_eval_set` produces two sibling directories under one output root::

    <out>/synthetic_<YYYYMMDD>/     <out>/degraded_<YYYYMMDD>/
      CASE001_bank_statement.png      CASE001_receipt_v1.png
      CASE001_invoice.png             CASE001_receipt_v2.png
      CASE001_receipt.png             CASE001_receipt_v3.png
      ...  165 images, 3 types        ...  165 images, receipts only
      ground_truth.csv                ground_truth.csv     <- describes THESE rows
      ground_truth.jsonl              ground_truth.jsonl   <- describes THESE rows

The two halves are NOT mirrors of each other, and the asymmetry is the whole
design. Four properties are load-bearing:

* **The degraded half holds receipts only, one image per severity tier.**
  Receipts are the only type users photograph -- bank statements and invoices
  arrive as clean PDFs or printouts -- so degrading the other two models
  nothing. 55 receipts x the 3 tiers declared under ``receipt_degradation:``
  in ``config/generation_config.yml`` is again 165 images, but they are 55
  documents at 3 severities, not 165 distinct documents.
* **A degraded filename carries its tier**: ``CASE001_receipt.png`` becomes
  ``CASE001_receipt_v1.png`` and up, taking the suffix from the tier's own
  ``suffix:`` key. Anything pairing the two halves must therefore join on the
  filename with that suffix removed, NOT on the filename itself.
* **Filenames are generic** -- ``CASE001_bank_statement.png``, never
  ``CASE001_cba_standard.png``. The layout variant must not leak, or a model
  could infer the template before reading a pixel. The type portion is the
  canonical document-type key from ``config/extraction_schema.yml``, so the
  schema is the single source of that name. Case ids stay unsuffixed so
  ``transaction_links.yml`` keeps resolving.
* **Each directory is self-contained**, carrying the ground truth for the
  images IT holds, so a model run points at one path and finds everything.
  The degraded ground truth is written rather than copied: each variant row
  repeats its source receipt's field values verbatim and differs only in
  ``image_file``, so a copy of the clean file would name images this
  directory does not contain. The two ground truths are consequently NOT
  byte-identical, and the degraded one carries only the receipt columns.

The schema projection this export needs -- which fields a document type is
scored on, in what order, and which are monetary or boolean -- comes from
`generators.exporters.eval_projection`, reading
``config/extraction_schema.yml``. Nothing here shells out to, or imports
from, another repository.

Record order is the CSV's column order: records are emitted sorted by
filename, so the first record is a bank statement and its five fields lead
the header, exactly as the pinned format requires. Nothing is sorted or
set-ified anywhere order is observable.
"""

import csv
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from generators.common import FitError
from generators.degradation import degrade_receipt, load_tiers, tier_seed

# FIELD_ALIASES (generator field name -> extraction-schema field name, where they differ) is the
# shared table; re-exported here for callers that import it from this module.
from generators.exporters.eval_projection import FIELD_ALIASES, ExtractionSchema, load_extraction_schema
from generators.loader import load_ground_truth, load_layout_registry
from generators.overflow_check import build_overflow_error

NOT_FOUND = "NOT_FOUND"

_ROOT_KEY = "eval_set"

# The one extraction document type users photograph, and so the only one the
# degraded half of the evaluation set contains. Bank statements and invoices
# arrive as clean PDFs or printouts, so degrading them models nothing.
_DEGRADED_TYPE = "receipt"

# Required sub-keys of eval_set, mapped to the expected shape used in diagnostics.
_REQUIRED_KEYS: dict[str, str] = {
    "document_types": "a non-empty list of keys from the top-level document_types block",
    "clean_dir_prefix": "the clean output directory's name before the date stamp, e.g. 'synthetic'",
    "degraded_dir_prefix": "the degraded output directory's name before the date stamp, e.g. 'degraded'",
    "csv_name": "the CSV filename to write into both directories, e.g. 'ground_truth.csv'",
    "jsonl_name": "the JSONL filename to write into both directories, e.g. 'ground_truth.jsonl'",
}

# Date stamp appended to both directory names. Not configurable: the format is
# part of the pinned export contract, not an operator choice.
_DATE_FORMAT = "%Y%m%d"


def _err(what: str, *, path: Path, key_path: str, expected: str, recover: str) -> ValueError:
    """Build a four-element fail-fast diagnostic (what / where / expected / recover)."""
    return ValueError(
        f"{what}\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> '{key_path}'.\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover} in {path}."
    )


def load_eval_set_config(config_path: Path) -> dict:
    """Load and validate the `eval_set` block of the generation config.

    Args:
        config_path: Path to generation_config.yml.

    Returns:
        The validated `eval_set` mapping, with `document_types` checked against
        the top-level `document_types` block.

    Raises:
        FileNotFoundError: `config_path` does not exist.
        ValueError: the block, a required key, or a document type is invalid.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            "generation config not found.\n"
            f"  What:     {config_path} does not exist.\n"
            f"  Where:    {config_path}\n"
            f"  Expected: a YAML file with a top-level '{_ROOT_KEY}' mapping.\n"
            f"  Recover:  pass --config with the path to generation_config.yml."
        )

    data = yaml.safe_load(config_path.read_text())
    cfg = data.get(_ROOT_KEY) if isinstance(data, dict) else None
    if not isinstance(cfg, dict):
        raise _err(
            f"'{_ROOT_KEY}' block is missing or not a mapping in {config_path}.",
            path=config_path,
            key_path=_ROOT_KEY,
            expected="a mapping with keys " + ", ".join(_REQUIRED_KEYS) + ".",
            recover=f"add an '{_ROOT_KEY}:' block",
        )

    for key, expected in _REQUIRED_KEYS.items():
        if key not in cfg or not cfg[key]:
            raise _err(
                f"'{_ROOT_KEY}.{key}' is missing or empty.",
                path=config_path,
                key_path=f"{_ROOT_KEY}.{key}",
                expected=expected + ".",
                recover=f"set '{key}' under {_ROOT_KEY}",
            )

    known = data.get("document_types", {})
    for dtype in cfg["document_types"]:
        if dtype not in known:
            raise _err(
                f"eval_set document type '{dtype}' is not a configured document type.",
                path=config_path,
                key_path=f"{_ROOT_KEY}.document_types",
                expected=f"keys of the top-level document_types block: {sorted(known)}.",
                recover=f"remove '{dtype}' or add it to document_types",
            )

    return cfg


def format_value(field_name: str, raw: Any, schema: ExtractionSchema) -> str:
    """Format one generator value to the model-output convention.

    Multi-value fields are re-joined with `` | ``, monetary fields get a ``$``
    per item (``NOT_FOUND`` items untouched), booleans are lowercased.

    Args:
        field_name: The extraction-schema field name being formatted.
        raw: The ground-truth value, of whatever type the YAML produced.
        schema: The loaded extraction schema, for the monetary/boolean sets.

    Returns:
        The formatted value, or NOT_FOUND when `raw` is blank.
    """
    text = str(raw).strip()
    if not text:
        return NOT_FOUND

    items = [item.strip() for item in text.split("|")]

    if field_name in schema.monetary_fields:
        items = [it if it.upper() == NOT_FOUND or it.startswith("$") else f"${it}" for it in items]
    elif field_name in schema.boolean_fields:
        items = [it.lower() for it in items]

    return " | ".join(items)


def project_fields(
    case_id: str,
    raw_fields: dict,
    doc_type: str,
    schema: ExtractionSchema,
    source_path: Path,
) -> dict[str, str]:
    """Cut one entry's fields down to its document type's extraction fields.

    Applies :data:`FIELD_ALIASES`, emits the schema's field order, fills absent
    schema fields with NOT_FOUND, and formats every value.

    The document type is checked for membership here rather than relying on
    `get_extraction_fields`'s own raise, because this is the only place that
    knows the offending case id and the ground-truth file it came from --
    which is where an operator would actually fix it.

    Args:
        case_id: The ground-truth case id, named in the diagnostic.
        raw_fields: The entry's `fields` mapping, in generation-contract names.
        doc_type: The entry's DOCUMENT_TYPE value.
        schema: The loaded extraction schema.
        source_path: The ground-truth YAML the entry came from.

    Returns:
        Field name -> formatted value, in extraction-schema declaration order.

    Raises:
        ValueError: `doc_type` is not a document type the schema declares.
    """
    canonical = schema.resolve_doc_type(doc_type)
    known_types = schema.get_all_doc_type_fields()
    if canonical not in known_types:
        raise _err(
            f"{case_id} has DOCUMENT_TYPE '{doc_type}', which is not an extraction document type.",
            path=source_path,
            key_path=f"{case_id}.fields.DOCUMENT_TYPE",
            expected=f"one of: {sorted(known_types)} (or a name resolving to one).",
            recover=(
                f"correct DOCUMENT_TYPE for {case_id}, or add '{canonical}:' under "
                f"'document_fields:' in config/extraction_schema.yml"
            ),
        )

    aliased = {FIELD_ALIASES.get(name, name): value for name, value in raw_fields.items()}

    projected: dict[str, str] = {}
    for name in schema.get_extraction_fields(canonical):
        value = aliased.get(name)
        projected[name] = (
            format_value(name, value, schema) if value is not None and str(value).strip() else NOT_FOUND
        )
    # The extraction answer key is debit-only: the 5-field bank contract has no
    # TRANSACTION_AMOUNTS_RECEIVED, so credit rows have no home in it.
    #
    # Every receipt unit price is on the page (a line of more than one unit
    # prints its "2 @ $3.50" unit line; a line of one unit prints its total,
    # which is its unit price), so LINE_ITEM_PRICES is projected whole.
    return _drop_credit_rows(projected)


_PAID_COLUMN = "TRANSACTION_AMOUNTS_PAID"
_STATEMENT_TYPE = "BANK_STATEMENT"


def _drop_credit_rows(projected: dict[str, str]) -> dict[str, str]:
    """Drop credit rows from a projected bank statement's register columns.

    The canonical register in `ground_truth/bank_statements.yml` is the FULL
    table: index-aligned TRANSACTION_* columns where a credit row carries
    NOT_FOUND in the paid column. That stays untouched -- it is correct for the
    register, for `derive_native`, and for transaction linking.

    The extraction contract is narrower. `config/extraction_schema.yml` gives
    bank_statement five fields and no TRANSACTION_AMOUNTS_RECEIVED, so a credit
    amount has nowhere to go. Projecting the full register into that answer key
    leaves placeholder rows no model is asked to produce; a consumer scoring
    position-by-position then finds the two lists offset by the credit count and
    marks a correct extraction wrong.

    Rows are identified by NOT_FOUND in the paid column, and every register
    column of matching length is filtered with the same indices so the columns
    stay aligned. Detection is by shape rather than by field name because the
    projection renames TRANSACTION_DESCRIPTIONS to LINE_ITEM_DESCRIPTIONS; a
    bank record's only multi-member fields are the register columns.

    Returns a new dict in the original key order (the JSONL key order is the
    contract), or the input unchanged when filtering does not apply or would
    not be safe.
    """
    if projected.get("DOCUMENT_TYPE") != _STATEMENT_TYPE:
        return projected

    paid_raw = str(projected.get(_PAID_COLUMN, NOT_FOUND))
    if "|" not in paid_raw:
        return projected

    paid_items = [item.strip() for item in paid_raw.split("|")]
    keep = [i for i, value in enumerate(paid_items) if value.upper() != NOT_FOUND]

    # Nothing to drop, or every row is a credit. Emitting an empty register
    # would be a silently wrong answer key, so leave the record alone.
    if len(keep) == len(paid_items) or not keep:
        return projected

    result = dict(projected)
    for name, value in projected.items():
        text = str(value)
        if "|" not in text:
            continue
        items = [item.strip() for item in text.split("|")]
        if len(items) != len(paid_items):
            # Not an aligned register column -- filtering it by these indices
            # would drop the wrong entries. Leave it whole.
            continue
        separator = " | " if " | " in text else "|"
        result[name] = separator.join(items[i] for i in keep)
    return result


def write_jsonl(documents: list[dict], jsonl_path: Path) -> Path:
    """Write the projected ground truth as one JSON object per line.

    Each record is `filename` followed by the document type's extraction
    fields in schema order -- the key order is the contract, so the fields
    dict is spread as built and never re-sorted.

    Args:
        documents: `{"filename": str, "fields": dict}` records, in output order.
        jsonl_path: Where to write the JSONL.

    Returns:
        The written path.
    """
    lines = [
        json.dumps({"filename": doc["filename"], **doc["fields"]}, ensure_ascii=False) for doc in documents
    ]
    jsonl_path.write_text("\n".join(lines) + "\n")
    return jsonl_path


def csv_from_jsonl(jsonl_path: Path, csv_path: Path) -> Path:
    """Transpose the projected JSONL ground truth into a CSV.

    Columns are the union of every record's keys, in first-seen order, with the
    JSONL's `filename` renamed to `image_file` to match this repo's convention.
    A field absent from a record is filled with NOT_FOUND, never left blank.

    Args:
        jsonl_path: The projected ground_truth.jsonl.
        csv_path: Where to write the CSV.

    Returns:
        The written path.
    """
    records = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]

    columns: list[str] = []
    for record in records:
        for key in record:
            if key != "filename" and key not in columns:
                columns.append(key)

    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image_file", *columns])
        writer.writeheader()
        for record in records:
            row = {"image_file": record["filename"]}
            for col in columns:
                row[col] = record.get(col, NOT_FOUND)
            writer.writerow(row)
    return csv_path


def _prepare_dir(out_dir: Path, *, force: bool) -> None:
    """Create `out_dir`, refusing to write into a non-empty one unless forced."""
    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            raise _err(
                f"output directory {out_dir} already exists and is not empty.",
                path=out_dir,
                key_path="--out",
                expected="an empty or non-existent directory, so an existing evaluation "
                "set a benchmark run points at is never overwritten.",
                recover="pass --force to replace it, or choose another --out",
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def _render_documents(
    config_path: Path,
    eval_cfg: dict,
    schema: ExtractionSchema,
    clean_dir: Path,
    degraded_dir: Path,
    renderers: dict,
) -> tuple[list[dict], list[dict]]:
    """Render the clean set once, and a tiered degraded set for receipts only.

    Receipts are the only type users photograph, so they are the only type
    degraded -- and each is degraded once per declared severity tier. Every
    variant's ground-truth record carries field values identical to its source
    receipt, differing only in `image_file`, which is the value-F1 contract:
    distortion never changes the answer.

    Args:
        config_path: Path to generation_config.yml, for the document-type and
            degradation-tier blocks.
        eval_cfg: The validated `eval_set` block.
        schema: The loaded extraction schema.
        clean_dir: Directory to save clean images into.
        degraded_dir: Directory to save degraded receipt variants into.
        renderers: Document type -> renderer callable.

    Returns:
        `(clean_documents, degraded_documents)`, each a list of
        `{"filename": str, "fields": dict}` sorted by filename. The two lists
        differ in both length and content -- the degraded one holds only
        receipt variants.

    Raises:
        ValueError: any missing renderer, layout, seed, document type, or
            duplicate output filename.
        TierConfigError: the receipt_degradation block is missing or malformed.
    """
    tiers = load_tiers(config_path)

    # Kept despite the degradation params moving out: the loop below reads
    # `doc_cfg = data["document_types"][dtype]` from this same load.
    data = yaml.safe_load(config_path.read_text())

    documents: list[dict] = []
    degraded_documents: list[dict] = []
    seen: dict[str, str] = {}

    for dtype in eval_cfg["document_types"]:
        doc_cfg = data["document_types"][dtype]
        renderer = renderers.get(dtype)
        if renderer is None:
            raise _err(
                f"no renderer is registered for document type '{dtype}'.",
                path=config_path,
                key_path=f"{_ROOT_KEY}.document_types",
                expected=f"a type with a renderer: {sorted(renderers)}.",
                recover=f"remove '{dtype}' from {_ROOT_KEY}.document_types",
            )

        gt_path = Path(doc_cfg["ground_truth"])
        gt_data = load_ground_truth(gt_path)
        layouts = load_layout_registry(Path(doc_cfg["layouts"]))

        for case_id, entry in gt_data.items():
            layout_ref = entry.get("layout", "")
            layout = layouts.get(layout_ref, {})
            if not layout:
                raise _err(
                    f"{case_id} references layout '{layout_ref}', which is not in the registry.",
                    path=Path(doc_cfg["layouts"]),
                    key_path=f"layouts.{layout_ref}",
                    expected="every ground-truth entry's layout to exist in its layout registry.",
                    recover=f"add '{layout_ref}' to the registry or fix {case_id}'s layout",
                )

            fields = entry.get("fields", {}) or {}
            doc_type = fields.get("DOCUMENT_TYPE", "")
            resolved_type = schema.resolve_doc_type(str(doc_type))
            projected = project_fields(str(case_id), fields, str(doc_type), schema, gt_path)
            filename = f"{case_id}_{resolved_type}.png"

            if filename in seen:
                raise _err(
                    f"two documents would both be exported as '{filename}' "
                    f"({seen[filename]} and {case_id} / {layout_ref}).",
                    path=gt_path,
                    key_path=f"{case_id}.fields.DOCUMENT_TYPE",
                    expected="exactly one document per case per extraction document type.",
                    recover=f"remove or re-type the duplicate entry for {case_id}",
                )
            seen[filename] = f"{case_id} / {layout_ref}"

            seed = entry.get("degradation_seed")
            if not isinstance(seed, int):
                raise _err(
                    f"{case_id} has no integer 'degradation_seed', so its degraded image "
                    f"would not be reproducible.",
                    path=gt_path,
                    key_path=f"{case_id}.degradation_seed",
                    expected="an integer, e.g. 'degradation_seed: 9821'.",
                    recover=f"add a 'degradation_seed:' to {case_id}",
                )

            entry["case_id"] = str(case_id)
            try:
                # Plain render: the distractor half arrives in Stage 7.
                img = renderer(entry, layout, distractors=False)
            except FitError as exc:
                raise build_overflow_error(
                    [f"{case_id} / {layout_ref}: {str(exc).splitlines()[0]}"]
                ) from None

            img.save(clean_dir / filename)
            documents.append({"filename": filename, "fields": projected})

            if resolved_type != _DEGRADED_TYPE:
                continue

            for index, tier in enumerate(tiers):
                variant_name = f"{case_id}_{resolved_type}_{tier.suffix}.png"
                degrade_receipt(img, tier, tier_seed(seed, index)).save(degraded_dir / variant_name)
                degraded_documents.append({"filename": variant_name, "fields": projected})

    documents.sort(key=lambda doc: doc["filename"])
    degraded_documents.sort(key=lambda doc: doc["filename"])
    return documents, degraded_documents


def export_eval_set(
    config_path: Path,
    out_dir: Path,
    *,
    force: bool = False,
    renderers: dict | None = None,
    today: date | None = None,
) -> dict:
    """Export a clean and a degraded evaluation set as sibling directories.

    The two are no longer parallel. The clean directory holds every document
    type once; the degraded directory holds receipts only, one image per
    declared severity tier, because receipts are the only type users
    photograph. Each therefore carries its own ground truth.

    Args:
        config_path: Path to generation_config.yml.
        out_dir: Parent directory the two dated directories are created under.
        force: Replace either target directory if it exists and is non-empty.
        renderers: Document type -> renderer callable; defaults to the
            pipeline's registry. Injectable so tests can render a subset.
        today: Date to stamp the directory names with; defaults to today.

    Returns:
        Summary dict with `images` (clean), `degraded_images` (receipt
        variants), `clean_dir`, `degraded_dir`, `csv` and `jsonl` -- the latter
        two naming the clean directory's copies.

    Raises:
        ValueError: any configuration, directory, layout or projection failure.
        FileNotFoundError: the generation config or a ground-truth file is missing.
        TierConfigError: the receipt_degradation block is missing or malformed.
    """
    eval_cfg = load_eval_set_config(config_path)
    schema = load_extraction_schema()

    if renderers is None:
        from generators.pipeline import _RENDERERS

        renderers = _RENDERERS

    stamp = (today or date.today()).strftime(_DATE_FORMAT)
    clean_dir = out_dir / f"{eval_cfg['clean_dir_prefix']}_{stamp}"
    degraded_dir = out_dir / f"{eval_cfg['degraded_dir_prefix']}_{stamp}"

    # Both directories are cleared before anything is rendered, so a refusal
    # never leaves half an export behind.
    _prepare_dir(clean_dir, force=force)
    _prepare_dir(degraded_dir, force=force)

    documents, degraded_documents = _render_documents(
        config_path, eval_cfg, schema, clean_dir, degraded_dir, renderers
    )

    jsonl_path = write_jsonl(documents, clean_dir / eval_cfg["jsonl_name"])
    csv_path = csv_from_jsonl(jsonl_path, clean_dir / eval_cfg["csv_name"])

    # Written, not copied: the degraded set holds different rows entirely
    # (receipt variants, one per tier), so it needs its own ground truth rather
    # than a copy of the clean one.
    degraded_jsonl = write_jsonl(degraded_documents, degraded_dir / eval_cfg["jsonl_name"])
    csv_from_jsonl(degraded_jsonl, degraded_dir / eval_cfg["csv_name"])

    return {
        "images": len(documents),
        "degraded_images": len(degraded_documents),
        "clean_dir": str(clean_dir),
        "degraded_dir": str(degraded_dir),
        "csv": str(csv_path),
        "jsonl": str(jsonl_path),
    }
