"""Arithmetic invariants `pipeline validate` enforces on ground truth.

Two rules that a hand-edited entry can violate while still rendering and
exporting happily, because nothing downstream recomputes them:

* parallel pipe-delimited fields must carry the same number of items;
* GST_AMOUNT must equal TOTAL_AMOUNT / 11 when the total is GST-inclusive.

Both rules are declared in config/field_definitions.yml, so these tests also
cover the fail-fast path when that declaration is missing.
"""

import copy
from collections.abc import Callable

import pytest
import yaml

from generators import schema
from tests.conftest import REPO_ROOT, assert_diagnostic_error

REAL_FIELD_DEFS = REPO_ROOT / "config" / "field_definitions.yml"
GROUND_TRUTH = REPO_ROOT / "ground_truth"


@pytest.fixture(autouse=True)
def _reset_field_defs_cache(monkeypatch):
    """Clear the module-level field-defs cache around each test, and fix the path absolutely."""
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)
    monkeypatch.setattr(schema, "_FIELD_DEFS_PATH", REAL_FIELD_DEFS)
    yield
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)


def _defs_at(monkeypatch, tmp_path, mutate: Callable[[dict], None]) -> None:
    """Point the schema module at a copy of the real definitions after `mutate` has edited it."""
    defs = copy.deepcopy(yaml.safe_load(REAL_FIELD_DEFS.read_text()))
    mutate(defs)
    path = tmp_path / "field_definitions.yml"
    path.write_text(yaml.safe_dump(defs))
    monkeypatch.setattr(schema, "_FIELD_DEFS", None)
    monkeypatch.setattr(schema, "_FIELD_DEFS_PATH", path)


def _entry(**fields: str) -> dict:
    """A RECEIPT entry carrying only DOCUMENT_TYPE plus the given fields."""
    return {
        "layout": "some_layout",
        "degradation_seed": 1,
        "fields": {"DOCUMENT_TYPE": "RECEIPT", **fields},
    }


def _gst_entry(included: str, total: str, gst: str) -> dict:
    return _entry(IS_GST_INCLUDED=included, TOTAL_AMOUNT=total, GST_AMOUNT=gst)


def _gst_errors(errors: list[str]) -> list[str]:
    """The errors that are about the GST rule, ignoring missing-required-field noise."""
    return [e for e in errors if "GST must be" in e]


def _parallel_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "pipe-delimited field count mismatch" in e]


def test_gst_inconsistent_with_total_is_reported():
    """GST 21.69 against a 216.89 inclusive total (expected 19.72) is reported."""
    errors = _gst_errors(schema.validate_entry("CASE001", _gst_entry("true", "216.89", "21.69")))
    assert len(errors) == 1
    assert "CASE001" in errors[0]
    assert "21.69" in errors[0] and "19.72" in errors[0]


def test_gst_consistent_with_total_passes():
    """GST 19.72 against a 216.89 inclusive total is accepted."""
    assert _gst_errors(schema.validate_entry("CASE001", _gst_entry("true", "216.89", "19.72"))) == []


def test_gst_rounds_half_up_like_the_seeder():
    """100.00 / 11 is 9.0909..., which must round to 9.09 rather than truncate."""
    assert _gst_errors(schema.validate_entry("CASE001", _gst_entry("true", "100.00", "9.09"))) == []
    assert len(_gst_errors(schema.validate_entry("CASE001", _gst_entry("true", "100.00", "9.10")))) == 1


def test_gst_not_checked_when_total_excludes_gst():
    """IS_GST_INCLUDED false means the total is not the base for the fraction."""
    assert _gst_errors(schema.validate_entry("CASE001", _gst_entry("false", "216.89", "21.69"))) == []


def test_missing_gst_consistency_block_fails_with_a_diagnostic(monkeypatch, tmp_path):
    """Without a gst_consistency block the rule cannot run, so validate fails loudly."""
    _defs_at(monkeypatch, tmp_path, lambda d: d.pop("gst_consistency"))
    with pytest.raises(schema.SchemaError) as exc_info:
        schema.validate_entry("CASE001", _entry(IS_GST_INCLUDED="true", TOTAL_AMOUNT="1.00"))
    assert "gst_consistency" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_incomplete_gst_consistency_block_names_the_missing_keys(monkeypatch, tmp_path):
    """A gst_consistency block lacking keys lists exactly which are missing."""
    _defs_at(
        monkeypatch,
        tmp_path,
        lambda d: d.update(gst_consistency={"divisor": 11}),
    )
    with pytest.raises(schema.SchemaError) as exc_info:
        schema.validate_entry("CASE001", _entry(IS_GST_INCLUDED="true", TOTAL_AMOUNT="1.00"))
    message = str(exc_info.value)
    missing_part = message.split("is missing ")[1].split(".\n")[0]
    assert missing_part == "decimals, inclusive_field, gst_field, total_field"
    assert_diagnostic_error(exc_info.value)


def test_mismatched_parallel_list_lengths_are_reported():
    """Three descriptions against two prices is a count mismatch naming both fields."""
    errors = _parallel_errors(
        schema.validate_entry(
            "CASE001", _entry(LINE_ITEM_DESCRIPTIONS="a|b|c", LINE_ITEM_PRICES="1.00|2.00")
        )
    )
    assert len(errors) == 1
    assert "LINE_ITEM_DESCRIPTIONS=3" in errors[0]
    assert "LINE_ITEM_PRICES=2" in errors[0]


def test_equal_parallel_list_lengths_pass():
    """Equal item counts raise no mismatch error."""
    errors = schema.validate_entry(
        "CASE001", _entry(LINE_ITEM_DESCRIPTIONS="a|b", LINE_ITEM_PRICES="1.00|2.00")
    )
    assert _parallel_errors(errors) == []


def test_missing_parallel_field_groups_fails_with_a_diagnostic(monkeypatch, tmp_path):
    """Without a parallel_field_groups block the rule cannot run, so validate fails loudly."""
    _defs_at(monkeypatch, tmp_path, lambda d: d.pop("parallel_field_groups"))
    with pytest.raises(schema.SchemaError) as exc_info:
        schema.validate_entry("CASE001", _entry(LINE_ITEM_DESCRIPTIONS="a"))
    assert "parallel_field_groups" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_real_corpus_satisfies_both_invariants():
    """The shipped ground truth must pass what validate now enforces."""
    for name in ("receipts", "invoices", "bank_statements"):
        path = GROUND_TRUTH / f"{name}.yml"
        assert schema.validate_ground_truth_file(path) == [], name
