"""Local replacement for LMM_POC's ``common.field_schema``, scoped to this repo.

``scripts/relabel_evaluation_set.py`` used to import ``FieldSchema`` /
``get_field_schema`` from the external LMM_POC checkout to answer five
questions about the evaluation schema: which fields a document type is
scored on (in what order), which of those fields are monetary or boolean,
and how to resolve a document-type name written in one of this repo's several
conventions to the schema's canonical key. This module answers the same five
questions from ``config/extraction_schema.yml`` alone, so the export no
longer needs a second repository on the path.

Field order is load-bearing: the projected JSONL's key order must match
``document_fields[type].fields`` exactly (see
``docs/eval_export_plan.md``), so every accessor here iterates that list in
file order and never sorts or otherwise reorders it.

One deliberate divergence from the LMM_POC contract: LMM_POC's
``get_extraction_fields`` falls back to a ``universal`` superset of fields
for an unresolved document type. ``config/extraction_schema.yml`` has no
such superset -- this repo has exactly three extraction document types, no
more -- so an unresolved type fails fast here instead of silently returning
the wrong field list (or an empty one). Per the project's fail-fast pattern, a
document type this schema does not know is a configuration error, not a
degraded-but-working case.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "extraction_schema.yml"

# Ground-truth field name -> canonical extraction-schema field name, only where the two differ: the
# generation contract (config/field_definitions.yml) and the extraction contract name a bank statement's
# descriptions differently. scripts/generate_extraction_gt.py keeps its own copy, since it runs with the
# standard library and PyYAML alone; a test keeps the two in step.
FIELD_ALIASES: dict[str, str] = {
    "TRANSACTION_DESCRIPTIONS": "LINE_ITEM_DESCRIPTIONS",  # bank statements
}

# Concrete YAML snippets cited in diagnostics, keyed by what the message is about.
EXAMPLES: dict[str, str] = {
    "document_fields": (
        "document_fields:\n"
        "  invoice:\n"
        "    fields: [DOCUMENT_TYPE, BUSINESS_ABN, ...]\n"
        "  receipt:\n"
        "    fields: [DOCUMENT_TYPE, BUSINESS_ABN, ...]\n"
        "  bank_statement:\n"
        "    fields: [DOCUMENT_TYPE, STATEMENT_DATE_RANGE, ...]"
    ),
    "fields": ("  invoice:\n    fields: [DOCUMENT_TYPE, BUSINESS_ABN, ...]"),
    "evaluation": (
        "evaluation:\n  field_types:\n    monetary: [TOTAL_AMOUNT, ...]\n    boolean: [IS_GST_INCLUDED]"
    ),
    "field_types": (
        "evaluation:\n  field_types:\n    monetary: [TOTAL_AMOUNT, ...]\n    boolean: [IS_GST_INCLUDED]"
    ),
    "monetary": "evaluation:\n  field_types:\n    monetary: [GST_AMOUNT, TOTAL_AMOUNT, ...]",
    "boolean": "evaluation:\n  field_types:\n    boolean: [IS_GST_INCLUDED]",
}


def _msg(what: str, example_key: str, remediation: str) -> str:
    """Assemble a WHAT / Example / Remediation diagnostic (WHERE lives in `what`)."""
    return f"{what} Example:\n{EXAMPLES[example_key]}\nRemediation: {remediation}"


@dataclass(frozen=True)
class ExtractionSchema:
    """Immutable projection of ``config/extraction_schema.yml``.

    Attributes:
        document_fields: Canonical document-type key (``invoice``,
            ``receipt``, ``bank_statement``) -> its extraction field list,
            in the file's declared order.
        monetary_fields: Field names formatted with a ``$`` prefix.
        boolean_fields: Field names formatted as lowercase ``true``/``false``.
        source_path: The YAML file this instance was loaded from, kept for
            diagnostics raised by the query methods below.
    """

    document_fields: dict[str, tuple[str, ...]]
    monetary_fields: frozenset[str]
    boolean_fields: frozenset[str]
    source_path: Path

    def resolve_doc_type(self, raw_type: str) -> str:
        """Normalise a document-type name to its canonical `document_fields` key.

        Handles every naming convention this repo actually uses for the same
        type: upper-snake-case ground-truth values (``BANK_STATEMENT``,
        ``INVOICE``, ``RECEIPT``), the plural lower-snake-case pipeline keys
        in ``config/generation_config.yml`` (``bank_statements``,
        ``invoices``, ``receipts``), and the singular lower-snake-case keys
        this schema itself uses (``bank_statement``, ``invoice``,
        ``receipt``). Case, hyphens and spaces are normalised first; a
        trailing plural ``s`` is stripped only if doing so yields a known
        canonical type.

        Mirrors LMM_POC's ``FieldSchema.resolve_doc_type``: an unresolved
        name is returned lowercased/normalised, unchanged, rather than
        raising -- callers that need a hard failure on an unknown type
        should check membership in `document_fields` themselves, or call
        `get_extraction_fields`, which does raise.

        Args:
            raw_type: A document-type name in any of the conventions above.

        Returns:
            The canonical `document_fields` key, or the normalised input
            unchanged if no canonical type matches.
        """
        normalized = raw_type.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in self.document_fields:
            return normalized

        singular = normalized.removesuffix("s")
        if singular != normalized and singular in self.document_fields:
            return singular

        return normalized

    def get_extraction_fields(self, document_type: str) -> list[str]:
        """Return one document type's extraction fields, in schema order.

        Resolves `document_type` through `resolve_doc_type` first, so any
        convention `resolve_doc_type` understands works here too.

        Args:
            document_type: A document-type name in any convention this repo
                uses (see `resolve_doc_type`).

        Returns:
            A new list of field names, in `config/extraction_schema.yml`
            declaration order.

        Raises:
            ValueError: `document_type` does not resolve to a document type
                declared under `document_fields`.
        """
        canonical = self.resolve_doc_type(document_type)
        fields = self.document_fields.get(canonical)
        if fields is None:
            raise ValueError(
                _msg(
                    f"Unknown document type {document_type!r} (resolved to {canonical!r}) has no entry "
                    f"under 'document_fields' in {self.source_path.resolve()}. "
                    f"Known document types: {sorted(self.document_fields)}.",
                    "fields",
                    f"correct the document type, or add '{canonical}:' under 'document_fields' in "
                    f"{self.source_path.resolve()}.",
                )
            )
        return list(fields)

    def get_all_doc_type_fields(self) -> dict[str, list[str]]:
        """All document types -> field lists.

        Returns:
            A new dict each call (callers can mutate freely), preserving
            each type's declared field order.
        """
        return {doc_type: list(fields) for doc_type, fields in self.document_fields.items()}


@lru_cache(maxsize=None)
def load_extraction_schema(path: Path = _SCHEMA_PATH) -> ExtractionSchema:
    """Load and validate `config/extraction_schema.yml` into an `ExtractionSchema`.

    Every key this module reads is required; nothing is defaulted in Python.

    Args:
        path: Path to `extraction_schema.yml`.

    Returns:
        The validated, immutable `ExtractionSchema`.

    Raises:
        FileNotFoundError: `path` does not exist.
        ValueError: the file is unparsable YAML, or a required
            section/key is missing, empty, or the wrong type.
    """
    if not path.exists():
        raise FileNotFoundError(
            _msg(
                f"Extraction schema not found: {path.resolve()}.",
                "document_fields",
                f"restore {path} from git, or create it with a 'document_fields' section and an "
                f"'evaluation.field_types' section.",
            )
        )

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(
            _msg(
                f"Failed to parse YAML in {path.resolve()}: {exc}.",
                "document_fields",
                "fix the syntax error at the reported line.",
            )
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            _msg(
                f"Expected a top-level mapping in {path.resolve()}, got {type(data).__name__}.",
                "document_fields",
                "replace the file contents with a key/value mapping.",
            )
        )

    doc_fields_raw = data.get("document_fields")
    if not isinstance(doc_fields_raw, dict) or not doc_fields_raw:
        raise ValueError(
            _msg(
                f"Missing or empty 'document_fields' section in {path.resolve()}.",
                "document_fields",
                "add a 'document_fields:' section with one entry per document type.",
            )
        )

    document_fields: dict[str, tuple[str, ...]] = {}
    for doc_type, type_cfg in doc_fields_raw.items():
        fields = type_cfg.get("fields") if isinstance(type_cfg, dict) else None
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields):
            raise ValueError(
                _msg(
                    f"'document_fields.{doc_type}.fields' is missing, empty, or not a list of strings "
                    f"in {path.resolve()}.",
                    "fields",
                    f"add a non-empty 'fields:' list under 'document_fields.{doc_type}'.",
                )
            )
        document_fields[doc_type] = tuple(fields)

    evaluation = data.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError(
            _msg(
                f"Missing or invalid 'evaluation' section in {path.resolve()}.",
                "evaluation",
                "add an 'evaluation:' section containing 'field_types'.",
            )
        )

    field_types = evaluation.get("field_types")
    if not isinstance(field_types, dict):
        raise ValueError(
            _msg(
                f"Missing or invalid 'evaluation.field_types' section in {path.resolve()}.",
                "field_types",
                "add a 'field_types:' mapping under 'evaluation' with 'monetary' and 'boolean' lists.",
            )
        )

    classified: dict[str, list[str]] = {}
    for kind in ("monetary", "boolean"):
        names = field_types.get(kind)
        if not isinstance(names, list) or not all(isinstance(f, str) for f in names):
            raise ValueError(
                _msg(
                    f"'evaluation.field_types.{kind}' is missing or not a list of strings "
                    f"in {path.resolve()}.",
                    kind,
                    f"add a '{kind}:' list under 'evaluation.field_types'.",
                )
            )
        classified[kind] = names

    return ExtractionSchema(
        document_fields=document_fields,
        monetary_fields=frozenset(classified["monetary"]),
        boolean_fields=frozenset(classified["boolean"]),
        source_path=path,
    )
