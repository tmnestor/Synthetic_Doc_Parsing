"""Derive flat CSV and JSONL from ground truth YAML files.

YAML is the single source of truth. This module regenerates derived
formats (CSV, JSONL) from YAML on every invocation.
"""

import csv
import json
from pathlib import Path

import yaml

from generators.exporters.cord import to_cord
from generators.exporters.docile import to_docile
from generators.exporters.links import transaction_links_to_doc_refs
from generators.exporters.native import to_native


def derive_csv(
    gt_files: list[Path],
    field_defs_path: Path,
    output_path: Path,
) -> Path:
    """Derive a flat CSV from one or more ground truth YAML files.

    Omitted fields are filled with 'NOT_FOUND'. An 'image_file' column
    is prepended using the filename convention: {CASEID}_{layout}.png.

    Args:
        gt_files: Paths to ground truth YAML files.
        field_defs_path: Path to field_definitions.yml.
        output_path: Where to write the CSV.

    Returns:
        Path to the written CSV file.
    """
    field_defs = yaml.safe_load(field_defs_path.read_text())
    all_columns = field_defs["all_columns"]

    rows: list[dict[str, str]] = []
    for gt_path in gt_files:
        data = yaml.safe_load(gt_path.read_text())
        if not isinstance(data, dict):
            continue
        for case_id, entry in data.items():
            fields = entry.get("fields", {})
            layout = entry.get("layout", "unknown")
            image_file = f"{case_id}_{layout}.png"
            row: dict[str, str] = {"image_file": image_file}
            for col in all_columns:
                row[col] = str(fields.get(col, "NOT_FOUND"))
            rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = ["image_file", *all_columns]
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def derive_jsonl(
    gt_files: list[Path],
    output_path: Path,
) -> Path:
    """Derive JSONL from one or more ground truth YAML files.

    Each line is a JSON object with case_id, layout, degradation_seed,
    image_file, and all field values.

    Args:
        gt_files: Paths to ground truth YAML files.
        output_path: Where to write the JSONL.

    Returns:
        Path to the written JSONL file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for gt_path in gt_files:
            data = yaml.safe_load(gt_path.read_text())
            if not isinstance(data, dict):
                continue
            for case_id, entry in data.items():
                fields = entry.get("fields", {})
                layout = entry.get("layout", "unknown")
                record = {
                    "case_id": str(case_id),
                    "layout": layout,
                    "degradation_seed": entry.get("degradation_seed"),
                    "image_file": f"{case_id}_{layout}.png",
                    **{k: str(v) for k, v in fields.items()},
                }
                f.write(json.dumps(record) + "\n")

    return output_path


CORD_DOCUMENT_TYPES: frozenset[str] = frozenset({"RECEIPT", "INVOICE"})


def derive_cord(
    gt_files: list[Path],
    export_config: dict,
    output_path: Path,
) -> Path:
    """Derive CORD gt_parse JSONL from ground truth YAML files.

    Only receipts and invoices are emitted; other document types have no CORD
    equivalent (spec section 7).

    Args:
        gt_files: Paths to ground truth YAML files.
        export_config: The validated export config mapping.
        output_path: Where to write the JSONL.

    Returns:
        Path to the written JSONL file.
    """
    identifier_form = export_config["abn_tfn_canonical_form"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for gt_path in gt_files:
            data = yaml.safe_load(gt_path.read_text())
            if not isinstance(data, dict):
                continue
            for case_id, entry in data.items():
                fields = entry.get("fields", {})
                if fields.get("DOCUMENT_TYPE") not in CORD_DOCUMENT_TYPES:
                    continue
                layout = entry.get("layout", "unknown")
                record = {
                    "case_id": str(case_id),
                    "image_file": f"{case_id}_{layout}.png",
                    "gt_parse": to_cord(fields, identifier_form),
                }
                f.write(json.dumps(record) + "\n")

    return output_path


def derive_links(
    transactions_path: Path,
    export_config: dict,
    output_path: Path,
) -> Path:
    """Derive doc_refs JSONL from the receipt/invoice-to-bank link ground truth.

    Args:
        transactions_path: Path to transaction_links.yml.
        export_config: The validated export config mapping.
        output_path: Where to write the JSONL.

    Returns:
        Path to the written JSONL file.
    """
    identifier_form = export_config["abn_tfn_canonical_form"]
    transactions = yaml.safe_load(transactions_path.read_text())
    records = transaction_links_to_doc_refs(transactions, identifier_form)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    return output_path


# DocILE is invoices only, by design: a receipt prints a quantity and unit price
# only on a line of more than one unit, so its quantity-1 members have no captured
# bounding box, and DocILE is a localisation benchmark where every annotation must
# be findable on the page
# (see generators/exporters/docile.py module docstring; spec section 8.3).
DOCILE_DOCUMENT_TYPES: frozenset[str] = frozenset({"INVOICE"})


def derive_docile(
    gt_files: list[Path],
    geometry_path: Path,
    export_config: dict,
    output_path: Path,
) -> Path:
    """Derive DocILE KILE/LIR JSONL from ground truth plus captured geometry.

    Only invoices are emitted; DOCILE_DOCUMENT_TYPES documents the scope
    restriction (spec section 8.3, invoice-only).

    Args:
        gt_files: Paths to ground truth YAML files.
        geometry_path: Path to the geometry.jsonl in the configured derived_dir.
        export_config: The validated export config mapping.
        output_path: Where to write the JSONL.

    Returns:
        Path to the written JSONL file.

    Raises:
        FileNotFoundError: If the geometry file is absent.
        KeyError: If an invoice has no geometry record in geometry.jsonl.
    """
    if not geometry_path.exists():
        msg = (
            f"What: DocILE export requires draw-time bounding boxes, but the geometry "
            f"file does not exist.\n"
            f"Where: {geometry_path.resolve()}\n"
            f"Expected: A JSONL file with one record per document, each carrying "
            f"'image_file', 'width', 'height' and 'boxes' keys.\n"
            f"Recover: Run 'python -m generators.pipeline generate' to produce "
            f"the geometry file, then re-run 'python -m generators.pipeline derive'."
        )
        raise FileNotFoundError(msg)

    geometry = {
        record["image_file"]: record["boxes"]
        for record in (json.loads(line) for line in geometry_path.read_text().strip().split("\n") if line)
    }
    fieldtypes = export_config["docile_fieldtypes"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for gt_path in gt_files:
            data = yaml.safe_load(gt_path.read_text())
            if not isinstance(data, dict):
                continue
            for case_id, entry in data.items():
                fields = entry.get("fields", {})
                if fields.get("DOCUMENT_TYPE") not in DOCILE_DOCUMENT_TYPES:
                    continue
                image_file = f"{case_id}_{entry.get('layout', 'unknown')}.png"
                boxes = geometry.get(image_file)
                if boxes is None:
                    msg = (
                        f"What: No geometry record found for invoice '{image_file}' in "
                        f"the geometry file.\n"
                        f"Where: {geometry_path.resolve()}\n"
                        f"Expected: Every invoice has a captured geometry record — "
                        f"DocILE is invoice-only and every DocILE field on an invoice "
                        f"has draw-time geometry, so an absent record is a real capture "
                        f"bug, never a structural absence.\n"
                        f"Recover: Re-run 'python -m generators.pipeline generate' so "
                        f"every document in ground_truth/ has a geometry record."
                    )
                    raise KeyError(msg)
                record = {
                    "case_id": str(case_id),
                    "image_file": image_file,
                    **to_docile(fields, boxes, fieldtypes),
                }
                f.write(json.dumps(record) + "\n")

    return output_path


# Bank statements have no CORD or DocILE equivalent (spec section 7) and are
# emitted in a project-defined schema instead. Together with
# CORD_DOCUMENT_TYPES this partitions the whole corpus: every document type
# lands in exactly one of the two sets.
NATIVE_DOCUMENT_TYPES: frozenset[str] = frozenset({"BANK_STATEMENT"})


def derive_native(gt_files: list[Path], output_path: Path) -> Path:
    """Derive the native JSONL for document types with no public schema.

    Args:
        gt_files: Paths to ground truth YAML files.
        output_path: Where to write the JSONL.

    Returns:
        Path to the written JSONL file.

    Raises:
        ValueError: If a statement's transaction register columns have
            mismatched counts (propagated from to_native).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for gt_path in gt_files:
            data = yaml.safe_load(gt_path.read_text())
            if not isinstance(data, dict):
                continue
            for case_id, entry in data.items():
                fields = entry.get("fields", {})
                if fields.get("DOCUMENT_TYPE") not in NATIVE_DOCUMENT_TYPES:
                    continue
                layout = entry.get("layout", "unknown")
                record = {
                    "case_id": str(case_id),
                    "image_file": f"{case_id}_{layout}.png",
                    **to_native(fields),
                }
                f.write(json.dumps(record) + "\n")

    return output_path
