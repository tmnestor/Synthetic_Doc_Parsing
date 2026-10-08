"""Tests for scripts/generate_extraction_gt.py.

The script has no package, so it is loaded by path. Its `--schema` argument must be
the extraction contract; this repo's own config/field_definitions.yml is the
generation contract and would silently produce a wrong-shaped CSV, so the loader
rejects it.

The script is standalone (standard library plus PyYAML) and words its fourth
diagnostic element 'How to fix:' where the shared helper requires 'Recover:'. The
passing tests assert the raise and what the message names; each has a strict `_bug`
sibling asserting the full four-element form through `assert_diagnostic_error`.
"""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from generators.exporters.eval_projection import load_extraction_schema
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SCRIPT = REPO_ROOT / "scripts" / "generate_extraction_gt.py"
SCHEMA_PATH = REPO_ROOT / "config" / "extraction_schema.yml"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_extraction_gt", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gt = _load_script()


def _write_schema(path: Path, *, bank_extra: list[str] | None = None) -> Path:
    """A small extraction contract with invented fields."""
    bank = ["DOCUMENT_TYPE", "STATEMENT_DATE_RANGE", "LINE_ITEM_DESCRIPTIONS", *(bank_extra or [])]
    path.write_text(
        yaml.safe_dump(
            {
                "document_fields": {
                    "invoice": {"fields": ["DOCUMENT_TYPE", "SUPPLIER_NAME", "TOTAL_AMOUNT", "FLAG"]},
                    "receipt": {"fields": ["DOCUMENT_TYPE", "SUPPLIER_NAME", "TOTAL_AMOUNT", "FLAG"]},
                    "bank_statement": {"fields": bank},
                },
                "evaluation": {
                    "field_types": {
                        "monetary": ["TOTAL_AMOUNT", "ACCOUNT_BALANCE", "TRANSACTION_AMOUNTS_RECEIVED"],
                        "boolean": ["FLAG"],
                    }
                },
            }
        )
    )
    return path


def test_script_lives_in_this_repo() -> None:
    """The script is a file in this repository's scripts/ directory and loads."""
    assert SCRIPT.is_file()
    assert SCRIPT.resolve().is_relative_to(REPO_ROOT)
    assert callable(gt.main) and callable(gt.generate)


def test_this_repos_generation_schema_is_rejected() -> None:
    """The generation contract must not be accepted as the extraction contract."""
    path = REPO_ROOT / "config" / "field_definitions.yml"
    with pytest.raises(ValueError) as err:
        gt._load_schema(path)
    assert str(path) in str(err.value)
    assert "field_types" in str(err.value)
    assert "How to fix:" in str(err.value)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: scripts/generate_extraction_gt.py:61 labels the fourth element 'How to fix:' not 'Recover:'",
)
def test_this_repos_generation_schema_is_rejected_bug() -> None:
    """The rejection carries the shared four-element diagnostic form."""
    with pytest.raises(ValueError) as err:
        gt._load_schema(REPO_ROOT / "config" / "field_definitions.yml")
    assert_diagnostic_error(err.value)


def test_missing_schema_file_is_rejected(tmp_path: Path) -> None:
    """A schema path that does not exist is a FileNotFoundError naming the path."""
    path = tmp_path / "absent.yml"
    with pytest.raises(FileNotFoundError) as err:
        gt._load_schema(path)
    assert str(path) in str(err.value)
    assert "How to fix:" in str(err.value)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: scripts/generate_extraction_gt.py:61 labels the fourth element 'How to fix:' not 'Recover:'",
)
def test_missing_schema_file_is_rejected_bug(tmp_path: Path) -> None:
    """The missing-file error carries the shared four-element diagnostic form."""
    with pytest.raises(FileNotFoundError) as err:
        gt._load_schema(tmp_path / "absent.yml")
    assert_diagnostic_error(err.value)


def test_schema_without_evaluation_key_is_rejected(tmp_path: Path) -> None:
    """A YAML with document_fields but no evaluation block must not slip through."""
    path = tmp_path / "schema.yml"
    path.write_text(yaml.safe_dump({"document_fields": {"invoice": {"fields": ["DOCUMENT_TYPE"]}}}))
    with pytest.raises(ValueError) as err:
        gt._load_schema(path)
    assert str(path) in str(err.value)
    assert "How to fix:" in str(err.value)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: scripts/generate_extraction_gt.py:61 labels the fourth element 'How to fix:' not 'Recover:'",
)
def test_schema_without_evaluation_key_is_rejected_bug(tmp_path: Path) -> None:
    """The rejection carries the shared four-element diagnostic form."""
    path = tmp_path / "schema.yml"
    path.write_text(yaml.safe_dump({"document_fields": {"invoice": {"fields": ["DOCUMENT_TYPE"]}}}))
    with pytest.raises(ValueError) as err:
        gt._load_schema(path)
    assert_diagnostic_error(err.value)


def test_valid_schema_yields_field_lists_and_type_sets(tmp_path: Path) -> None:
    """A valid contract yields the invoice and bank field lists and the monetary and boolean sets."""
    schema = gt._load_schema(_write_schema(tmp_path / "schema.yml"))
    assert schema["invoice_fields"] == ["DOCUMENT_TYPE", "SUPPLIER_NAME", "TOTAL_AMOUNT", "FLAG"]
    assert schema["bank_fields"] == ["DOCUMENT_TYPE", "STATEMENT_DATE_RANGE", "LINE_ITEM_DESCRIPTIONS"]
    assert schema["monetary"] == frozenset(
        {"TOTAL_AMOUNT", "ACCOUNT_BALANCE", "TRANSACTION_AMOUNTS_RECEIVED"}
    )
    assert schema["boolean"] == frozenset({"FLAG"})


def test_columns_put_image_file_first_and_append_bank_only_fields(tmp_path: Path) -> None:
    """Columns are image_file, then the invoice fields, then bank-only fields not already present."""
    schema = gt._load_schema(_write_schema(tmp_path / "schema.yml", bank_extra=["TRANSACTION_DATES"]))
    assert gt._build_columns(schema) == [
        "image_file",
        "DOCUMENT_TYPE",
        "SUPPLIER_NAME",
        "TOTAL_AMOUNT",
        "FLAG",
        "STATEMENT_DATE_RANGE",
        "LINE_ITEM_DESCRIPTIONS",
        "TRANSACTION_DATES",
    ]


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *args])
    return gt.main()


def test_yaml_dir_defaults_to_this_repos_ground_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default must be repo-relative, not a path on the author's machine.

    Run from an unrelated working directory: the CSV still holds one row per case of
    this repository's ground truth, named {CASE}_{layout}.png.
    """
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "gt.csv"
    assert _run_main(monkeypatch, "--output", str(output)) == 0
    expected: set[str] = set()
    for stem in ("bank_statements", "invoices", "receipts"):
        entries = yaml.safe_load((REPO_ROOT / "ground_truth" / f"{stem}.yml").read_text())
        expected |= {f"{case}_{entry['layout']}.png" for case, entry in entries.items()}
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {r["image_file"] for r in rows} == expected
    assert len(rows) == len(expected)
    assert [r["image_file"] for r in rows] == sorted(r["image_file"] for r in rows)
    assert f"Wrote {len(expected)} rows" in capsys.readouterr().out


def test_schema_defaults_to_the_repo_local_extraction_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generator is self-contained: with no --schema, the header comes from config/extraction_schema.yml."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "gt.csv"
    _run_main(monkeypatch, "--output", str(output))
    expected = gt._build_columns(gt._load_schema(SCHEMA_PATH))
    assert output.read_text().splitlines()[0] == ",".join(expected)
    # The generation contract has different fields, so the header must not come from it.
    assert "ACCOUNT_BALANCE" not in expected


def test_shipped_extraction_contract_loads_and_matches_the_prompts() -> None:
    """The vendored contract is the extraction field set, and agrees with the in-package loader."""
    schema = gt._load_schema(SCHEMA_PATH)
    package = load_extraction_schema()
    assert schema["invoice_fields"] == package.get_extraction_fields("invoice")
    assert schema["monetary"] == package.monetary_fields
    assert schema["boolean"] == package.boolean_fields
    assert {"TOTAL_AMOUNT", "GST_AMOUNT"} <= schema["monetary"]
    assert "IS_GST_INCLUDED" in schema["boolean"]
    generation = yaml.safe_load((REPO_ROOT / "config" / "field_definitions.yml").read_text())
    assert "evaluation" not in generation


def test_validation_only_fields_are_not_csv_columns(tmp_path: Path) -> None:
    """ACCOUNT_BALANCE etc. have formatting declared but must add no columns."""
    path = _write_schema(
        tmp_path / "schema.yml", bank_extra=["ACCOUNT_BALANCE", "TRANSACTION_AMOUNTS_RECEIVED"]
    )
    schema = gt._load_schema(path)
    columns = gt._build_columns(schema)
    for name in ("ACCOUNT_BALANCE", "TRANSACTION_AMOUNTS_RECEIVED"):
        assert name not in columns
        assert name in schema["monetary"], "formatting stays declared"
