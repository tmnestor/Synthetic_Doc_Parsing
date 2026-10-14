"""Ground-truth projection for the evaluation snapshot, owned entirely by this repository.

The snapshot export (`generators.snapshot.export`) renders the images; this module supplies what it
writes beside them: the `eval_set:` block's presence and document-type checks (`load_eval_set_config`;
each key's shape is checked in `generators.snapshot.config`), the projection of a
ground-truth entry onto its document type's extraction fields (`project_fields`, `format_value`), and
the two ground-truth files every half carries (`write_jsonl`, `csv_from_jsonl`).

Properties the export relies on:

* **Filenames are generic**: ``CASE001_bank_statement.png``, never one naming the layout, so a model
  cannot infer the template before reading a pixel. The type portion is the canonical document-type
  key from ``config/extraction_schema.yml``. Case ids stay unsuffixed so ``transaction_links.yml``
  keeps resolving.
* **The degraded half holds receipts only, one image per severity tier**, because receipts are the only
  type users photograph. A degraded filename carries its tier's ``suffix:`` (``CASE001_receipt_v1.png``),
  so pairing it with a clean half strips the suffix first. Its ground truth is written rather than
  copied: each variant row repeats its source receipt's values and differs only in the filename.
* **Each half is self-contained**, carrying the ground truth for the images it holds.

The schema projection (which fields a document type is scored on, in what order, and which are monetary
or boolean) comes from `generators.exporters.eval_projection`, reading ``config/extraction_schema.yml``.
Nothing here shells out to, or imports from, another repository.

Record order is the CSV's column order: records are emitted sorted by filename, so the first record is
a bank statement and its five fields lead the header, exactly as the pinned format requires. Nothing is
sorted or set-ified anywhere order is observable.
"""

import csv
import json
from pathlib import Path
from typing import Any

import yaml

# FIELD_ALIASES (generator field name -> extraction-schema field name, where they differ) is the
# shared table; re-exported here for callers that import it from this module.
from generators.exporters.eval_projection import FIELD_ALIASES, ExtractionSchema
from generators.parsing.degraded import DEGRADED_DOC_TYPE

__all__ = [
    "FIELD_ALIASES",
    "NOT_FOUND",
    "csv_from_jsonl",
    "format_value",
    "load_eval_set_config",
    "project_fields",
    "write_jsonl",
]

NOT_FOUND = "NOT_FOUND"

_ROOT_KEY = "eval_set"

# The one extraction document type users photograph, and so the only one the degraded half of the
# snapshot contains; held once, in the parsing package, beside the degraded half's name.
_DEGRADED_TYPE = DEGRADED_DOC_TYPE

# Required sub-keys of eval_set, mapped to the expected shape used in diagnostics. Each key's type and
# shape beyond presence is checked in generators/snapshot/config.py.
_REQUIRED_KEYS: dict[str, str] = {
    "output_root": "output_root: ../evaluation_data (a directory beside the repository, which must exist)",
    "snapshot_dir_prefix": "snapshot_dir_prefix: snapshot",
    "half_prefixes": (
        "half_prefixes: {plain: plain, distractor: distractor, labelled_varied: labelled_varied, "
        "degraded: degraded}"
    ),
    "geometry_dir": "geometry_dir: geometry",
    "parsing_dir": "parsing_dir: parsing",
    "document_types": "document_types: [bank_statements, invoices, receipts] (keys of the top-level "
    "document_types block)",
    "csv_name": "csv_name: ground_truth.csv",
    "jsonl_name": "jsonl_name: ground_truth.jsonl",
    "provenance_suffix": "provenance_suffix: provenance",
}

# Date stamp appended to the snapshot's and each half's directory name. Not configurable: the format is
# part of the pinned export contract, not an operator choice.
_DATE_FORMAT = "%Y%m%d"


def _err(what: str, *, path: Path, key_path: str, expected: str, recover: str) -> ValueError:
    """Build a four-element fail-fast diagnostic (what / where / expected / recover).

    The one helper for the `eval_set:` checks here and in `generators.snapshot.config`, and for the
    export's own refusals about a ground-truth entry.
    """
    return ValueError(
        "Invalid snapshot export input.\n"
        f"  What:     {what}\n"
        f"  Where:    {path.resolve()} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover.rstrip('.')} in {path.resolve()}."
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
            f"  What:     {config_path.resolve()} does not exist.\n"
            f"  Where:    {config_path.resolve()}\n"
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

    if not isinstance(cfg["document_types"], list):
        raise _err(
            f"'{_ROOT_KEY}.document_types' must be a list, got {cfg['document_types']!r}.",
            path=config_path,
            key_path=f"{_ROOT_KEY}.document_types",
            expected=_REQUIRED_KEYS["document_types"] + ".",
            recover=f"write {_ROOT_KEY}.document_types as a YAML list",
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
