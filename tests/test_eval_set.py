"""Eval-set export: config validation, projection, CSV/JSONL shape.

The export is exercised once, in the session-scoped `export` fixture (tests/conftest.py): a small
snapshot of the first three cases of each type, because rendering every case three times and
degrading each receipt is the expensive part; every property of its `plain_` and `degraded_` halves is
asserted against that one export, which tests must treat as read-only. Totals over the whole corpus
are asserted by the slow full-corpus export (tests/snapshot/test_full_export.py).

Counts are derived from config/generation_config.yml and from the export itself, never written as
literals, except where a test is about the documented export shape (one clean image per case per
type).
"""

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from generators.degradation import load_tiers
from generators.eval_set import (
    FIELD_ALIASES,
    NOT_FOUND,
    csv_from_jsonl,
    format_value,
    load_eval_set_config,
    project_fields,
    write_jsonl,
)
from generators.exporters.eval_projection import FIELD_ALIASES as SHARED_FIELD_ALIASES
from generators.exporters.eval_projection import load_extraction_schema
from generators.pipeline import _RENDERERS, app
from generators.snapshot import export as export_module
from generators.snapshot.config import HALVES, load_snapshot_config
from generators.snapshot.export import SnapshotSummary
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.eval_export_tools import (
    CONFIG_PATH,
    STAMP,
    assert_eval_export_matches_baseline,
    clean_state,
    read_csv,
    read_jsonl,
    scratch_config,
    single_case_config,
    small_baseline,
)

SCHEMA = load_extraction_schema()
EVAL_CFG = yaml.safe_load(CONFIG_PATH.read_text())["eval_set"]
REQUIRED_KEYS = [
    "output_root",
    "snapshot_dir_prefix",
    "half_prefixes",
    "geometry_dir",
    "document_types",
    "csv_name",
    "jsonl_name",
    "provenance_suffix",
    "forbidden_terms",
]


@pytest.fixture(scope="module")
def clean(export: SnapshotSummary) -> Path:
    return export.half_dirs["plain"]


@pytest.fixture(scope="module")
def degraded(export: SnapshotSummary) -> Path:
    return export.half_dirs["degraded"]


def _config_without(tmp_path: Path, drop: str | None = None, *, block: bool = True) -> Path:
    """Scratch config with one `eval_set` key (or the whole block) removed."""

    def mutate(config: dict) -> None:
        if not block:
            del config["eval_set"]
        elif drop is not None:
            del config["eval_set"][drop]

    return scratch_config(tmp_path, mutate=mutate)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_loads_real_config() -> None:
    """The real config is coherent: every declared type is renderable and every degraded type is produced.

    Which types the corpus carries is an operator decision, so no list is pinned. The snapshot's own
    keys load too, the output root resolving to an existing directory outside the repository.
    """
    cfg = load_eval_set_config(CONFIG_PATH)
    top_level = yaml.safe_load(CONFIG_PATH.read_text())["document_types"]
    assert cfg["document_types"]
    for dtype in cfg["document_types"]:
        assert dtype in top_level
        assert dtype in _RENDERERS, f"{dtype} is declared for export but has no renderer"
        assert SCHEMA.resolve_doc_type(dtype) in SCHEMA.document_fields
    # The degraded half is receipts only, so receipts must be a declared type
    # and at least one tier must be declared for them.
    assert "receipts" in cfg["document_types"]
    assert load_tiers(CONFIG_PATH)
    snapshot = load_snapshot_config(CONFIG_PATH)
    assert snapshot.output_root.is_dir() and not snapshot.output_root.is_relative_to(REPO_ROOT)
    assert sorted(snapshot.half_prefixes) == sorted(HALVES)


def test_relabel_keys_are_gone_from_the_config() -> None:
    """The export owns its projection; no path into another repo may survive.

    The output root is the one path the block holds, and it points beside the repository.
    """
    text = CONFIG_PATH.read_text()
    cfg = yaml.safe_load(text)["eval_set"]
    assert sorted(cfg) == sorted(REQUIRED_KEYS)
    # The forbidden terms name other repositories in order to keep them out of the snapshot.
    for term in cfg["forbidden_terms"]:
        text = text.replace(term, "")
    assert not re.search(r"relabel|lmm_poc|field_schema", text, re.IGNORECASE)
    for key, value in cfg.items():
        if key in ("output_root", "forbidden_terms"):
            continue
        assert not (isinstance(value, str) and ("/" in value or ".." in value)), value


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_missing_key_is_four_element_diagnostic(tmp_path: Path, key: str) -> None:
    """A missing eval_set key names what, where (file and dotted key), expected and recovery."""
    path = _config_without(tmp_path, key)
    with pytest.raises(ValueError) as err:
        load_snapshot_config(path)
    assert_diagnostic_error(err.value)
    assert f"eval_set.{key}" in str(err.value)
    assert str(path.resolve()) in str(err.value)


def test_missing_eval_set_block_is_diagnostic(tmp_path: Path) -> None:
    """A config without the eval_set block fails with a four-element diagnostic."""
    path = _config_without(tmp_path, block=False)
    with pytest.raises(ValueError) as err:
        load_eval_set_config(path)
    assert_diagnostic_error(err.value)
    assert "eval_set" in str(err.value)
    assert str(path) in str(err.value)


def test_missing_config_file_is_diagnostic(tmp_path: Path) -> None:
    """A config path that does not exist is a FileNotFoundError carrying the same four elements."""
    with pytest.raises(FileNotFoundError) as err:
        load_eval_set_config(tmp_path / "absent.yml")
    assert_diagnostic_error(err.value)


def test_unknown_document_type_is_diagnostic(tmp_path: Path) -> None:
    """An eval_set document type absent from the top-level block names the type and the valid keys."""

    def mutate(config: dict) -> None:
        config["eval_set"]["document_types"] = ["receipts", "invented_type"]

    path = scratch_config(tmp_path, mutate=mutate)
    with pytest.raises(ValueError) as err:
        load_eval_set_config(path)
    assert_diagnostic_error(err.value)
    assert "invented_type" in str(err.value)
    assert "eval_set.document_types" in str(err.value)
    for known in ("bank_statements", "invoices", "receipts"):
        assert known in str(err.value)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_format_value_applies_monetary_boolean_and_pipe_conventions() -> None:
    """Monetary items gain a $ each, booleans are lowercased, pipes re-join with ' | ', blanks are NOT_FOUND."""
    assert format_value("TOTAL_AMOUNT", "12.50", SCHEMA) == "$12.50"
    assert format_value("LINE_ITEM_PRICES", "1.5|NOT_FOUND|$3.00", SCHEMA) == "$1.5 | NOT_FOUND | $3.00"
    assert format_value("IS_GST_INCLUDED", "TRUE", SCHEMA) == "true"
    assert format_value("LINE_ITEM_DESCRIPTIONS", "Widget A|Widget B", SCHEMA) == "Widget A | Widget B"
    assert format_value("SUPPLIER_NAME", "  Acme  ", SCHEMA) == "Acme"
    assert format_value("TOTAL_AMOUNT", "   ", SCHEMA) == NOT_FOUND
    assert format_value("TOTAL_AMOUNT", 7, SCHEMA) == "$7"


def test_project_fields_uses_schema_order_and_fills_gaps() -> None:
    """Projection emits exactly the schema's fields in schema order, NOT_FOUND where absent, aliases applied."""
    raw = {"TOTAL_AMOUNT": "10.00", "SUPPLIER_NAME": "Example Traders", "ACCOUNT_BALANCE": "99.00"}
    projected = project_fields("CASE900", raw, "INVOICE", SCHEMA, Path("invoices.yml"))
    assert list(projected) == SCHEMA.get_extraction_fields("invoice")
    assert projected["TOTAL_AMOUNT"] == "$10.00"
    assert projected["SUPPLIER_NAME"] == "Example Traders"
    assert projected["BUSINESS_ABN"] == NOT_FOUND
    assert "ACCOUNT_BALANCE" not in projected

    bank = project_fields(
        "CASE900",
        {"DOCUMENT_TYPE": "BANK_STATEMENT", "TRANSACTION_DESCRIPTIONS": "Coffee | Fuel"},
        "BANK_STATEMENT",
        SCHEMA,
        Path("bank_statements.yml"),
    )
    assert FIELD_ALIASES is SHARED_FIELD_ALIASES
    assert bank["LINE_ITEM_DESCRIPTIONS"] == "Coffee | Fuel"
    assert list(bank) == SCHEMA.get_extraction_fields("bank_statement")


def test_project_fields_unknown_document_type_is_diagnostic() -> None:
    """An unknown DOCUMENT_TYPE names the case, the ground-truth file and the schema key to add."""
    source = Path("ground_truth/invented.yml")
    with pytest.raises(ValueError) as err:
        project_fields("CASE900", {}, "PAYSLIP", SCHEMA, source)
    assert_diagnostic_error(err.value)
    assert "CASE900" in str(err.value)
    assert str(source) in str(err.value)
    assert "extraction_schema.yml" in str(err.value)


# ---------------------------------------------------------------------------
# CSV / JSONL writers
# ---------------------------------------------------------------------------


def test_csv_from_jsonl_matches_records(tmp_path: Path) -> None:
    """CSV columns are the first-seen union of record keys, filename becomes image_file, gaps are NOT_FOUND."""
    docs = [
        {"filename": "CASE900_a.png", "fields": {"X": "1", "Y": "2"}},
        {"filename": "CASE900_b.png", "fields": {"X": "3", "Z": "4"}},
    ]
    jsonl = write_jsonl(docs, tmp_path / "gt.jsonl")
    csv_path = csv_from_jsonl(jsonl, tmp_path / "gt.csv")
    header, rows = read_csv(csv_path)
    assert header == ["image_file", "X", "Y", "Z"]
    assert rows == [
        {"image_file": "CASE900_a.png", "X": "1", "Y": "2", "Z": NOT_FOUND},
        {"image_file": "CASE900_b.png", "X": "3", "Y": NOT_FOUND, "Z": "4"},
    ]


def test_write_jsonl_preserves_field_order(tmp_path: Path) -> None:
    """Each line is filename then the fields in the order given, never re-sorted."""
    docs = [{"filename": "CASE900_a.png", "fields": {"Z": "z", "A": "a", "M": "m"}}]
    path = write_jsonl(docs, tmp_path / "gt.jsonl")
    assert list(read_jsonl(path)[0]) == ["filename", "Z", "A", "M"]
    assert path.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# Export products
# ---------------------------------------------------------------------------


def test_export_creates_dated_half_directories(export: SnapshotSummary) -> None:
    """One directory per declared half prefix, plus the geometry and tools directories and the README,
    manifest and checksum files, inside the dated snapshot.

    Expected names are derived from the configured prefixes, so renaming a half does not require editing
    a list here.
    """
    expected = {f"{EVAL_CFG['half_prefixes'][half]}_{STAMP}" for half in HALVES} | {
        EVAL_CFG["geometry_dir"],
        "tools",
        "README.md",
        "MANIFEST.json",
        "SHA256SUMS",
    }
    assert export.path.name == f"{EVAL_CFG['snapshot_dir_prefix']}_{STAMP}"
    assert {p.name for p in export.path.iterdir()} == expected
    assert {directory.parent for directory in export.half_dirs.values()} == {export.path}


def test_every_half_shares_the_snapshot_date_stamp(export: SnapshotSummary) -> None:
    """A mismatched stamp would let a run score new images against an older answer key."""
    stamps = {directory.name.rsplit("_", 1)[1] for directory in export.half_dirs.values()}
    assert stamps == {STAMP} == {export.stamp}


def test_export_matches_pinned_format(clean: Path) -> None:
    """The pinned format describes the plain half: every document type once, under CASE###_<type>.png.

    Checked here against the baseline's columns, key order and pattern with the small snapshot's counts;
    the pinned counts themselves hold on the full-corpus export. The degraded half deliberately has
    another shape (tier suffixes), asserted in tests/test_eval_set_variants.py.
    """
    assert_eval_export_matches_baseline(clean, small_baseline())
    types = {p.stem.split("_", 1)[1] for p in clean.glob("*.png")}
    assert types == set(SCHEMA.document_fields)


def test_the_two_directories_no_longer_mirror_each_other(clean: Path, degraded: Path) -> None:
    """Every degraded name carries a declared tier suffix, so the halves join by stripping it.

    (adjusted: the original also asserted different image counts. With three types
    clean and three tiers degraded the counts coincide, so equality would be a
    coincidence either way and is not asserted. The contents are what differ.)
    """
    suffixes = {tier.suffix for tier in load_tiers(CONFIG_PATH)}
    clean_names = {p.name for p in clean.glob("*.png")}
    degraded_names = {p.name for p in degraded.glob("*.png")}
    assert clean_names.isdisjoint(degraded_names)
    for name in degraded_names:
        match = re.fullmatch(r"(CASE\d{3}_[a-z_]+?)_(v\d+)\.png", name)
        assert match, name
        assert match.group(2) in suffixes
        assert f"{match.group(1)}.png" in clean_names, f"{name} has no clean source"


def test_export_filenames_carry_no_layout_variant(export: SnapshotSummary) -> None:
    """A leaked layout name would let a model infer the template for free, in any half."""
    layout_ids: set[str] = set()
    for doc_cfg in yaml.safe_load(CONFIG_PATH.read_text())["document_types"].values():
        layout_ids |= set(yaml.safe_load((REPO_ROOT / doc_cfg["layouts"]).read_text()))
    types = "|".join(SCHEMA.document_fields)
    pattern = re.compile(rf"CASE\d{{3}}_({types})(_v\d+)?\.png")
    for directory in export.half_dirs.values():
        for image in directory.glob("*.png"):
            assert pattern.fullmatch(image.name), image.name
            body = image.stem.split("_", 1)[1]
            assert not any(layout_id in body for layout_id in layout_ids if layout_id not in types)


def test_each_directory_carries_its_own_ground_truth(
    export: SnapshotSummary, clean: Path, degraded: Path
) -> None:
    """The degraded ground truth is written, not copied.

    It describes different rows (receipt variants), so a copy of the clean file would
    name images that do not exist in that directory.
    """
    for directory in export.half_dirs.values():
        names = {p.name for p in directory.glob("*.png")}
        assert {r["filename"] for r in read_jsonl(directory / "ground_truth.jsonl")} == names
        assert {r["image_file"] for r in read_csv(directory / "ground_truth.csv")[1]} == names
    assert (clean / "ground_truth.csv").read_bytes() != (degraded / "ground_truth.csv").read_bytes()
    assert (clean / "ground_truth.jsonl").read_bytes() != (degraded / "ground_truth.jsonl").read_bytes()


def test_every_csv_image_file_exists_in_its_own_directory(export: SnapshotSummary) -> None:
    """Every image_file in a half's CSV is a file in that same half."""
    for directory in export.half_dirs.values():
        for row in read_csv(directory / "ground_truth.csv")[1]:
            assert (directory / row["image_file"]).is_file(), row["image_file"]


def test_jsonl_and_csv_agree_row_for_row(export: SnapshotSummary) -> None:
    """CSV is a transposition of the JSONL: same order, same values."""
    for directory in export.half_dirs.values():
        records = read_jsonl(directory / "ground_truth.jsonl")
        header, rows = read_csv(directory / "ground_truth.csv")
        assert len(records) == len(rows)
        for record, row in zip(records, rows):
            assert row["image_file"] == record["filename"]
            for column in header[1:]:
                assert row[column] == record.get(column, NOT_FOUND)


def _run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, int, int]:
    """Run `eval-set` on a receipt plus an invoice case; return (output, clean, degraded) counts.

    One receipt and one invoice give 2 images in each clean half and one degraded image per tier,
    so the counts differ and a swapped number is visible. The repository state is the fake clean one.
    """
    config = single_case_config(tmp_path, ["receipts", "invoices"])
    monkeypatch.setattr(export_module, "read_repo_state", lambda repo_root, **kwargs: clean_state())
    result = CliRunner().invoke(app, ["eval-set", "--config", str(config)])
    assert result.exit_code == 0, result.output
    return result.output, 2, len(load_tiers(config))


def test_cli_eval_set_prints_both_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `eval-set` command runs and reports a line per half and the snapshot path."""
    output, clean_count, degraded_count = _run_cli(tmp_path, monkeypatch)
    assert clean_count != degraded_count
    for half in HALVES:
        assert re.search(rf"^{half}: \d+ images", output, re.MULTILINE), output
    assert re.search(rf"^Snapshot: .*{EVAL_CFG['snapshot_dir_prefix']}_\d{{8}}$", output, re.MULTILINE)


def test_cli_eval_set_prints_each_half_its_own_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each half's line shows that half's count: the degraded line no longer repeats the clean count."""
    output, clean_count, degraded_count = _run_cli(tmp_path, monkeypatch)
    for half in ("plain", "distractor", "labelled_varied"):
        assert re.search(rf"^{half}: {clean_count} images", output, re.MULTILINE), output
    assert re.search(rf"^degraded: {degraded_count} images", output, re.MULTILINE), output


def test_cli_eval_set_prints_the_manifest_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, _, _ = _run_cli(tmp_path, monkeypatch)
    assert re.search(r"^MANIFEST.json SHA-256: [0-9a-f]{64} ", output, re.MULTILINE), output


def test_cli_eval_set_reports_a_malformed_tier_block_four_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TierConfigError (a RuntimeError) prints its four-part message and exits 1, with no traceback."""
    config = single_case_config(tmp_path, ["receipts"])
    data = yaml.safe_load(config.read_text())
    del data["receipt_degradation"]["tiers"][0]["suffix"]
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(export_module, "read_repo_state", lambda repo_root, **kwargs: clean_state())
    result = CliRunner().invoke(app, ["eval-set", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert_diagnostic_error(result.output)
    assert "Traceback" not in result.output and not isinstance(result.exception, RuntimeError)
