"""Tests for the in-house extraction-schema projection.

Covers the five accessors `scripts/relabel_evaluation_set.py` needs
(`monetary_fields`, `boolean_fields`, `resolve_doc_type`,
`get_all_doc_type_fields`, `get_extraction_fields`), sourced from
`config/extraction_schema.yml` instead of LMM_POC's `common.field_schema`.
"""

import copy
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from generators.exporters.eval_projection import ExtractionSchema, load_extraction_schema
from tests.conftest import REPO_ROOT, assert_diagnostic_error

REAL_PATH = REPO_ROOT / "config" / "extraction_schema.yml"

VALID: dict = {
    "document_fields": {
        "invoice": {"fields": ["DOCUMENT_TYPE", "ZULU_FIELD", "ALPHA_FIELD", "TOTAL_AMOUNT"]},
        "receipt": {"fields": ["DOCUMENT_TYPE", "TOTAL_AMOUNT", "IS_GST_INCLUDED"]},
        "bank_statement": {"fields": ["DOCUMENT_TYPE", "STATEMENT_DATE_RANGE"]},
    },
    "evaluation": {"field_types": {"monetary": ["TOTAL_AMOUNT"], "boolean": ["IS_GST_INCLUDED"]}},
}


def _write(tmp_path: Path, data: object | None = None, *, text: str | None = None) -> Path:
    path = tmp_path / "extraction_schema.yml"
    body = text if text is not None else yaml.safe_dump(VALID if data is None else data, sort_keys=False)
    path.write_text(body)
    return path


def _mutated(mutate: Callable[[dict], object]) -> dict:
    data = copy.deepcopy(VALID)
    mutate(data)
    return data


@pytest.fixture(scope="module")
def real() -> ExtractionSchema:
    return load_extraction_schema(REAL_PATH)


def test_loads_real_repo_config(real: ExtractionSchema) -> None:
    """The actual config/extraction_schema.yml loads and exposes all three types."""
    assert set(real.document_fields) == {"invoice", "receipt", "bank_statement"}
    assert real.source_path == REAL_PATH
    assert all(real.document_fields[t] for t in real.document_fields)
    assert "TOTAL_AMOUNT" in real.monetary_fields
    assert real.boolean_fields == frozenset({"IS_GST_INCLUDED"})


def test_extraction_fields_preserve_declared_order(real: ExtractionSchema, tmp_path: Path) -> None:
    """Fields come back in the order the YAML declares them, never sorted."""
    raw = yaml.safe_load(REAL_PATH.read_text())
    for doc_type in ("invoice", "receipt", "bank_statement"):
        assert real.get_extraction_fields(doc_type) == raw["document_fields"][doc_type]["fields"]
    # The invented schema is deliberately not alphabetical: ZULU_FIELD precedes ALPHA_FIELD.
    invented = load_extraction_schema(_write(tmp_path))
    assert invented.get_extraction_fields("invoice") == [
        "DOCUMENT_TYPE",
        "ZULU_FIELD",
        "ALPHA_FIELD",
        "TOTAL_AMOUNT",
    ]


def test_extraction_fields_returns_new_list(real: ExtractionSchema) -> None:
    """Callers can mutate the returned list without corrupting the schema."""
    first = real.get_extraction_fields("invoice")
    first.append("INJECTED")
    first.clear()
    again = real.get_extraction_fields("invoice")
    assert again
    assert "INJECTED" not in again
    assert again is not first


def test_get_all_doc_type_fields_matches_yaml_order(tmp_path: Path) -> None:
    """The mapping iterates types, and each type's fields, in the file's declared order."""
    schema = load_extraction_schema(_write(tmp_path))
    result = schema.get_all_doc_type_fields()
    assert list(result) == ["invoice", "receipt", "bank_statement"]
    assert result["invoice"] == ["DOCUMENT_TYPE", "ZULU_FIELD", "ALPHA_FIELD", "TOTAL_AMOUNT"]
    assert result["bank_statement"] == ["DOCUMENT_TYPE", "STATEMENT_DATE_RANGE"]


def test_get_all_doc_type_fields_returns_new_dict(real: ExtractionSchema) -> None:
    """Mutating the returned dict or its lists leaves the schema, and the next call, intact."""
    result = real.get_all_doc_type_fields()
    result["invoice"].append("INJECTED")
    del result["receipt"]
    fresh = real.get_all_doc_type_fields()
    assert "INJECTED" not in fresh["invoice"]
    assert "receipt" in fresh


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BANK_STATEMENT", "bank_statement"),
        ("INVOICE", "invoice"),
        ("RECEIPT", "receipt"),
        ("bank_statements", "bank_statement"),
        ("invoices", "invoice"),
        ("receipts", "receipt"),
        ("bank_statement", "bank_statement"),
        ("Bank-Statement", "bank_statement"),
        ("bank statement", "bank_statement"),
    ],
)
def test_resolve_doc_type_across_conventions(real: ExtractionSchema, raw: str, expected: str) -> None:
    """Upper-snake ground-truth values, plural pipeline keys and singular schema keys resolve alike."""
    assert real.resolve_doc_type(raw) == expected


def test_resolve_doc_type_strips_surrounding_whitespace(real: ExtractionSchema) -> None:
    """More lenient than LMM_POC by design: padding only ever helps resolution."""
    assert real.resolve_doc_type("  INVOICE\t") == "invoice"
    assert real.resolve_doc_type(" receipts ") == "receipt"


def test_resolve_doc_type_unresolved_returns_normalised_unchanged(real: ExtractionSchema) -> None:
    """Mirrors LMM_POC: no match means normalised-but-unchanged, not a raise."""
    assert real.resolve_doc_type("Tax Return") == "tax_return"
    assert real.resolve_doc_type("PAYSLIPS") == "payslips"


def test_monetary_and_boolean_fields(tmp_path: Path) -> None:
    """The evaluation field-type lists are exposed as frozensets of the declared names."""
    schema = load_extraction_schema(_write(tmp_path))
    assert schema.monetary_fields == frozenset({"TOTAL_AMOUNT"})
    assert schema.boolean_fields == frozenset({"IS_GST_INCLUDED"})
    assert isinstance(schema.monetary_fields, frozenset)
    assert isinstance(schema.boolean_fields, frozenset)


def test_unknown_document_type_raises(tmp_path: Path) -> None:
    """get_extraction_fields fails fast on a type the schema does not declare, naming the known types."""
    path = _write(tmp_path)
    schema = load_extraction_schema(path)
    with pytest.raises(ValueError) as err:
        schema.get_extraction_fields("payslip")
    assert_diagnostic_error(err.value, path, "payslip")
    assert "bank_statement" in str(err.value)


def test_get_all_doc_type_fields_excludes_unknown_type(real: ExtractionSchema) -> None:
    """The full mapping holds exactly the declared types and nothing for an unknown one."""
    assert "payslip" not in real.get_all_doc_type_fields()
    assert set(real.get_all_doc_type_fields()) == set(real.document_fields)


def test_missing_file_is_diagnostic(tmp_path: Path) -> None:
    """A missing schema file raises FileNotFoundError naming the resolved path."""
    path = tmp_path / "absent.yml"
    with pytest.raises(FileNotFoundError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "Extraction schema not found")


def test_malformed_yaml_is_diagnostic(tmp_path: Path) -> None:
    """Unparsable YAML raises a ValueError naming the file."""
    path = _write(tmp_path, text="document_fields: [unclosed\n  - : :")
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "Failed to parse YAML")


def test_non_dict_top_level_is_diagnostic(tmp_path: Path) -> None:
    """A top-level list is rejected as not a mapping."""
    path = _write(tmp_path, ["document_fields"])
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "top-level mapping")


@pytest.mark.parametrize("bad", [None, {}, ["invoice"]])
def test_missing_document_fields_is_diagnostic(tmp_path: Path, bad: object) -> None:
    """An absent, empty or non-mapping 'document_fields' section is rejected."""

    def mutate(data: dict) -> None:
        if bad is None:
            del data["document_fields"]
        else:
            data["document_fields"] = bad

    path = _write(tmp_path, _mutated(mutate))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "document_fields")


def test_doc_type_missing_fields_list_is_diagnostic(tmp_path: Path) -> None:
    """A document type with no 'fields' list is rejected, naming the type."""
    path = _write(tmp_path, _mutated(lambda d: d["document_fields"]["receipt"].pop("fields")))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "document_fields.receipt.fields")


def test_empty_fields_list_is_diagnostic(tmp_path: Path) -> None:
    """An empty 'fields' list is rejected as strictly as an absent one."""
    path = _write(tmp_path, _mutated(lambda d: d["document_fields"]["invoice"].update(fields=[])))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "document_fields.invoice.fields")


def test_missing_evaluation_is_diagnostic(tmp_path: Path) -> None:
    """A schema with no 'evaluation' section is rejected."""
    path = _write(tmp_path, _mutated(lambda d: d.pop("evaluation")))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "'evaluation'")


def test_missing_field_types_is_diagnostic(tmp_path: Path) -> None:
    """An 'evaluation' section with no 'field_types' mapping is rejected."""
    path = _write(tmp_path, _mutated(lambda d: d["evaluation"].pop("field_types")))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "evaluation.field_types")


def test_missing_monetary_is_diagnostic(tmp_path: Path) -> None:
    """A field_types block with no 'monetary' list is rejected."""
    path = _write(tmp_path, _mutated(lambda d: d["evaluation"]["field_types"].pop("monetary")))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "evaluation.field_types.monetary")


def test_missing_boolean_is_diagnostic(tmp_path: Path) -> None:
    """A field_types block with no 'boolean' list is rejected."""
    path = _write(tmp_path, _mutated(lambda d: d["evaluation"]["field_types"].pop("boolean")))
    with pytest.raises(ValueError) as err:
        load_extraction_schema(path)
    assert_diagnostic_error(err.value, path, "evaluation.field_types.boolean")
