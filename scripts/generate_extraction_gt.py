#!/usr/bin/env python3
"""Generate an extraction ground-truth CSV from the per-type synthetic YAMLs.

Merges ``bank_statements.yml`` + ``invoices.yml`` + ``receipts.yml`` (the
Synthetic_Doc_Generation ground truth) into a single CSV in the shape the
standard ``stages/evaluate.py`` stage expects: one row per image keyed by
``image_file``, columns = the union of the schema's per-doc-type extraction
fields, values formatted to match the model-output convention (monetary fields
carry ``$``, multi-value fields use `` | `` separators, missing fields are
``NOT_FOUND``).

Image filenames are reconstructed as ``{CASE}_{layout}.png`` from each YAML
entry's ``layout`` field, matching the synthetic_transaction_linking dataset.

Columns and field-type formatting come from ``config/extraction_schema.yml`` in this
repo -- not hardcoded -- so the CSV tracks the contract. Nothing outside this repo is
required: no other checkout, and no imports beyond the standard library and PyYAML.

``--schema`` defaults to ``config/extraction_schema.yml``, the EXTRACTION contract.
It is NOT ``config/field_definitions.yml``, which is the *generation* contract and
describes different fields (bank statements carry ACCOUNT_BALANCE and
TRANSACTION_DESCRIPTIONS there; receipts omit the payer fields). Passing the wrong
one would yield a CSV with the wrong columns and no monetary formatting, so the
loader rejects it rather than proceeding.

Usage:
    python3 scripts/generate_extraction_gt.py \
        --output   /path/to/evaluation_data/<dataset>/ground_truth_extraction.csv \
        --data-dir /path/to/evaluation_data/<dataset>
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import yaml

# Fields that exist for validation but are excluded from the evaluation CSV.
# The one piece of schema policy not expressible in extraction_schema.yml: these
# are declared under evaluation.field_types (so their formatting is defined) but
# must not become CSV columns. No extraction prompt asks for them.
_VALIDATION_ONLY: frozenset[str] = frozenset({"TRANSACTION_AMOUNTS_RECEIVED", "ACCOUNT_BALANCE"})

# Source YAML file -> the doc-type key whose schema fields it carries.
_SOURCE_FILES: dict[str, str] = {
    "bank_statements.yml": "bank_statement",
    "invoices.yml": "invoice",
    "receipts.yml": "receipt",
}

# YAML field name -> canonical schema field name (only where they differ).
_FIELD_ALIASES: dict[str, str] = {
    "TRANSACTION_DESCRIPTIONS": "LINE_ITEM_DESCRIPTIONS",  # bank statements
}


def _diagnostic(what: str, where: str, example: str, fix: str) -> str:
    """Assemble a 4-element diagnostic error message."""
    return f"What: {what}\nWhere: {where}\nExpected: {example}\nHow to fix: {fix}"


def _doc_type_fields(doc_fields: dict[str, Any], doc_type: str, schema_path: Path) -> list[str]:
    """Per-doc-type extraction field list from the schema YAML, validation-only excluded."""
    cfg = doc_fields.get(doc_type)
    if not isinstance(cfg, dict) or not cfg.get("fields"):
        raise ValueError(
            _diagnostic(
                what=f"document type '{doc_type}' is missing or has no non-empty 'fields' list.",
                where=f"{schema_path} -> document_fields.{doc_type}.fields",
                example="document_fields:\n  invoice:\n    fields: [SUPPLIER_NAME, TOTAL_AMOUNT, ...]",
                fix=f"ensure '{doc_type}' with a non-empty 'fields' list exists in the schema YAML.",
            )
        )
    return [f for f in cfg["fields"] if f not in _VALIDATION_ONLY]


def _load_schema(schema_path: Path) -> dict[str, Any]:
    """Read the extraction contract -- the column / field-type definition.

    Returns the invoice/bank field lists plus the monetary and boolean field sets,
    read from ``config/extraction_schema.yml`` rather than hardcoded, so the CSV
    tracks the contract.

    Args:
        schema_path: Path to the extraction schema YAML.

    Returns:
        Mapping with invoice_fields, bank_fields, monetary and boolean.

    Raises:
        FileNotFoundError: If the schema file does not exist.
        ValueError: If the file is not an extraction contract (no
            ``evaluation.field_types``) -- most likely the generation contract
            passed by mistake.
    """
    if not schema_path.is_file():
        raise FileNotFoundError(
            _diagnostic(
                what="the extraction schema YAML does not exist.",
                where=str(schema_path),
                example="config/extraction_schema.yml (the column / field-type contract).",
                fix="drop --schema to use config/extraction_schema.yml (the default), or "
                "point it at a file with the same document_fields / evaluation.field_types shape.",
            )
        )
    raw = yaml.safe_load(schema_path.read_text()) or {}

    # Guard against this repo's config/field_definitions.yml being passed by
    # mistake: it also has a `document_fields` key, so it would load without
    # error and silently emit the wrong columns with no monetary formatting.
    # `evaluation.field_types` exists only in the extraction contract.
    if "field_types" not in raw.get("evaluation", {}):
        raise ValueError(
            _diagnostic(
                what=(
                    "the schema YAML has no `evaluation.field_types` key, so it is not "
                    "the extraction contract. config/field_definitions.yml is the "
                    "GENERATION contract and describes different fields -- using it "
                    "produces a CSV with the wrong columns and no monetary formatting."
                ),
                where=str(schema_path),
                example="config/extraction_schema.yml, which contains:\n"
                "    evaluation:\n"
                "      field_types:\n"
                "        monetary: [GST_AMOUNT, TOTAL_AMOUNT, ...]\n"
                "        boolean:  [IS_GST_INCLUDED]",
                fix="drop --schema to use config/extraction_schema.yml (the default).",
            )
        )

    doc_fields = raw.get("document_fields", {})
    field_types = raw["evaluation"]["field_types"]
    return {
        "invoice_fields": _doc_type_fields(doc_fields, "invoice", schema_path),
        "bank_fields": _doc_type_fields(doc_fields, "bank_statement", schema_path),
        "monetary": frozenset(field_types.get("monetary", [])),
        "boolean": frozenset(field_types.get("boolean", [])),
    }


def _build_columns(schema: dict[str, Any]) -> list[str]:
    """Derive the CSV column order from the schema (image_file first).

    Union of the invoice/receipt and bank-statement extraction fields, ordered
    invoice-fields-first then any bank-only extras, with ``image_file`` prepended.
    """
    columns = ["image_file", *schema["invoice_fields"]]
    for field in schema["bank_fields"]:
        if field not in columns:
            columns.append(field)
    return columns


def _normalize_pipes(value: str) -> list[str]:
    """Split a pipe-delimited value into stripped items (no spacing assumed)."""
    return [item.strip() for item in value.split("|")]


def _format_value(field: str, raw: Any, monetary: frozenset[str], boolean: frozenset[str]) -> str:
    """Format one YAML field value to the evaluate-stage CSV convention.

    - Multi-value fields are re-joined with `` | `` (space-pipe-space).
    - Monetary fields get a ``$`` prefix per item (NOT_FOUND items untouched).
    - Boolean fields are lowercased.
    """
    text = str(raw).strip()
    if not text:
        return "NOT_FOUND"

    items = _normalize_pipes(text)

    if field in monetary:
        items = [it if it.upper() == "NOT_FOUND" or it.startswith("$") else f"${it}" for it in items]
    elif field in boolean:
        items = [it.lower() for it in items]

    return " | ".join(items)


def _row_for_entry(
    case_id: str,
    entry: dict[str, Any],
    columns: list[str],
    monetary: frozenset[str],
    boolean: frozenset[str],
) -> tuple[str, dict[str, str]]:
    """Build (image_file, row dict) for one CASE entry of a source YAML."""
    layout = entry.get("layout")
    if not layout:
        msg = _diagnostic(
            what=f"case {case_id} has no 'layout' field — cannot build the image filename.",
            where=f"source YAML entry {case_id}",
            example="each entry must carry layout: <name> (image = {CASE}_{layout}.png).",
            fix="regenerate the ground-truth YAML with a 'layout' per case.",
        )
        raise ValueError(msg)

    image_file = f"{case_id}_{layout}.png"
    fields = entry.get("fields", {}) or {}

    # Apply aliases (e.g. bank TRANSACTION_DESCRIPTIONS -> LINE_ITEM_DESCRIPTIONS).
    resolved: dict[str, Any] = {}
    for key, val in fields.items():
        resolved[_FIELD_ALIASES.get(key, key)] = val

    row: dict[str, str] = {"image_file": image_file}
    for col in columns:
        if col == "image_file":
            continue
        if col in resolved and str(resolved[col]).strip():
            row[col] = _format_value(col, resolved[col], monetary, boolean)
        else:
            row[col] = "NOT_FOUND"
    return image_file, row


def generate(yaml_dir: Path, output: Path, data_dir: Path | None, schema: dict[str, Any]) -> int:
    """Generate the extraction ground-truth CSV. Returns the row count."""
    if not yaml_dir.is_dir():
        msg = _diagnostic(
            what="the source YAML directory does not exist.",
            where=str(yaml_dir),
            example="a directory containing bank_statements.yml, invoices.yml, receipts.yml.",
            fix="pass --yaml-dir pointing at the Synthetic_Doc_Generation/ground_truth directory.",
        )
        raise FileNotFoundError(msg)

    monetary = schema["monetary"]
    boolean = schema["boolean"]
    columns = _build_columns(schema)

    rows: list[dict[str, str]] = []
    image_files: list[str] = []
    for filename, _doc_type in _SOURCE_FILES.items():
        path = yaml_dir / filename
        if not path.is_file():
            msg = _diagnostic(
                what=f"required source file '{filename}' is missing.",
                where=str(path),
                example=f"{yaml_dir}/{filename} (a mapping of CASE id -> {{layout, fields}}).",
                fix=f"ensure {filename} exists in --yaml-dir.",
            )
            raise FileNotFoundError(msg)

        data = yaml.safe_load(path.read_text()) or {}
        for case_id, entry in data.items():
            image_file, row = _row_for_entry(case_id, entry, columns, monetary, boolean)
            rows.append(row)
            image_files.append(image_file)

    rows.sort(key=lambda r: r["image_file"])

    # Optional: warn if generated filenames don't exist in the dataset.
    if data_dir is not None:
        missing = [name for name in image_files if not (data_dir / name).is_file()]
        if missing:
            print(
                f"WARNING: {len(missing)}/{len(image_files)} image filenames not found in "
                f"{data_dir} (first few: {missing[:5]})",
                file=sys.stderr,
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yaml-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "ground_truth",
        help="Directory with bank_statements.yml / invoices.yml / receipts.yml "
        "(default: this repo's ground_truth/).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "config" / "extraction_schema.yml",
        help="Extraction/evaluation contract defining the CSV columns and field types "
        "(default: this repo's config/extraction_schema.yml). NOT "
        "config/field_definitions.yml, which is the generation contract.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the extraction ground-truth CSV.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Optional image directory to validate generated filenames against.",
    )
    args = parser.parse_args()

    schema = _load_schema(args.schema)
    count = generate(args.yaml_dir, args.output, args.data_dir, schema)
    print(f"Wrote {count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
