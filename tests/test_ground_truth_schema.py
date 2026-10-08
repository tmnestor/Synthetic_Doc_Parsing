"""Tests for generators/schema.py's ground-truth fail-fast diagnostics (local-only; tests/ is gitignored).

Named test_ground_truth_schema.py, not test_schema.py, to avoid a pytest
basename collision with tests/layout_dsl/test_schema.py (this test tree has
no __init__.py files in its subdirectories, so module basenames must be unique
across it).

Covers the two config-driven lookups that read config/field_definitions.yml
with bare dict subscripts: _valid_doc_types() (the 'document_type_values' key)
and _field_type_group() (the 'field_types' key). Both must raise SchemaError
with a four-element diagnostic on a missing key rather than an unhandled
KeyError, since generators/pipeline.py's `validate` command does not catch
KeyError around generators.schema.validate_entry.
"""

from pathlib import Path

import pytest
import yaml

from generators import schema
from tests.conftest import REPO_ROOT, assert_diagnostic_error

REAL_FIELD_DEFS = REPO_ROOT / "config" / "field_definitions.yml"


@pytest.fixture(autouse=True)
def _reset_field_defs_cache(monkeypatch):
    """Clear generators.schema's module-level field-defs cache around each test.

    _load_field_defs() memoises its result into the module-level _FIELD_DEFS
    global the first time it is called. Tests that point _FIELD_DEFS_PATH at a
    temp file must clear this cache first, or they would silently see whatever
    an earlier test (or an earlier call against the real config) already
    cached; clearing afterwards keeps the cache from leaking into tests that
    run after this module. The path is also made absolute so the tests do not
    depend on the working directory.
    """
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)
    monkeypatch.setattr(schema, "_FIELD_DEFS_PATH", REAL_FIELD_DEFS)
    yield
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)


def _point_at(monkeypatch, tmp_path: Path, defs: dict) -> Path:
    """Write `defs` as a field_definitions.yml and point the schema module at it."""
    path = tmp_path / "field_definitions.yml"
    path.write_text(yaml.safe_dump(defs))
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)
    monkeypatch.setattr(schema, "_FIELD_DEFS_PATH", path)
    return path


def _entry(doc_type: str = "RECEIPT") -> dict:
    return {"layout": "some_layout", "degradation_seed": 1, "fields": {"DOCUMENT_TYPE": doc_type}}


def test_valid_doc_types_raises_schema_error_when_document_type_values_missing(monkeypatch, tmp_path):
    """A field_definitions.yml without document_type_values raises SchemaError, not KeyError."""
    path = _point_at(monkeypatch, tmp_path, {"document_fields": {"receipt": ["DOCUMENT_TYPE"]}})
    with pytest.raises(schema.SchemaError) as exc_info:
        schema._valid_doc_types()
    assert "document_type_values" in str(exc_info.value)
    assert str(path.resolve()) in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_valid_doc_types_returns_correct_values_against_real_config():
    """The real config allows exactly the three corpus document types."""
    assert schema._valid_doc_types() == {"BANK_STATEMENT", "RECEIPT", "INVOICE"}


def test_field_type_group_raises_schema_error_when_field_types_key_missing(monkeypatch, tmp_path):
    """A field_definitions.yml without field_types raises SchemaError, not KeyError."""
    path = _point_at(monkeypatch, tmp_path, {"document_type_values": ["RECEIPT"]})
    with pytest.raises(schema.SchemaError) as exc_info:
        schema._field_type_group("date")
    assert "field_types" in str(exc_info.value)
    assert str(path.resolve()) in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_field_type_group_returns_empty_set_when_requested_group_absent(monkeypatch, tmp_path):
    """field_types present but with no 'abn' key is not a schema error.

    This is deliberate existing behaviour (not every field type applies to
    every document type) and must not change.
    """
    _point_at(monkeypatch, tmp_path, {"field_types": {"date": ["INVOICE_DATE"]}})
    assert schema._field_type_group("abn") == set()
    assert schema._field_type_group("date") == {"INVOICE_DATE"}


def test_field_type_group_returns_correct_values_against_real_config():
    """The real config lists INVOICE_DATE under date, and BUSINESS_ABN under abn."""
    assert schema._field_type_group("date") == {"INVOICE_DATE"}
    assert schema._field_type_group("abn") == {"BUSINESS_ABN"}


def test_validate_entry_surfaces_missing_document_type_values_as_error_string(monkeypatch, tmp_path):
    """pipeline.py calls validate_entry() without a try/except around it.

    So a SchemaError raised deep inside must be caught and turned into an
    error string here rather than propagating as a raw exception.
    """
    _point_at(monkeypatch, tmp_path, {"document_fields": {"receipt": ["DOCUMENT_TYPE"]}})
    errors = schema.validate_entry("CASE001", _entry())
    assert len(errors) == 1
    assert isinstance(errors[0], str)
    assert "document_type_values" in errors[0]
    assert_diagnostic_error(errors[0])


def test_validate_entry_surfaces_missing_field_types_as_error_string(monkeypatch, tmp_path):
    """A missing field_types key is likewise returned as an error string."""
    _point_at(
        monkeypatch,
        tmp_path,
        {"document_type_values": ["RECEIPT"], "document_fields": {"receipt": ["DOCUMENT_TYPE"]}},
    )
    errors = schema.validate_entry("CASE001", _entry())
    assert len(errors) == 1
    assert isinstance(errors[0], str)
    assert "field_types" in errors[0]
    assert_diagnostic_error(errors[0])
