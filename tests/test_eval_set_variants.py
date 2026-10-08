"""The degraded half holds the degraded type at each declared tier, with
extraction ground truth duplicated per variant.

Asserted on the small snapshot of the session-scoped `export` fixture (the first
three cases of each type); its `plain_` half is the clean source each variant pairs with.

Every expectation here is read from config/generation_config.yml (types,
tiers, ground-truth paths) or from the export itself rather than written as a
literal. Retuning the tier ladder in YAML therefore does not require editing this
file, and a type or tier silently vanishing still fails, because the counts stop
matching the images.

The quality ground truth, the combined directory, the collages and the defect
vocabulary that the original suite also covered do not exist in the exporter any
more (see tests/RECOVERED_INVENTORY.md); those tests are dropped there.
"""

import re
from pathlib import Path

import pytest
import yaml

from generators.degradation import load_tiers
from generators.eval_set import _DEGRADED_TYPE
from generators.exporters.eval_projection import load_extraction_schema
from tests.conftest import REPO_ROOT
from generators.snapshot.export import SnapshotSummary
from tests.eval_export_tools import CONFIG_PATH, SMALL_CASES, read_csv, read_jsonl

SCHEMA = load_extraction_schema()
CONFIG = yaml.safe_load(CONFIG_PATH.read_text())
TIERS = load_tiers(CONFIG_PATH)
SUFFIXES = [tier.suffix for tier in TIERS]
DECLARED = CONFIG["eval_set"]["document_types"]


def _cases(dtype: str) -> list[str]:
    """Case ids of a document type the small snapshot exports: the first SMALL_CASES of its ground truth."""
    path = REPO_ROOT / CONFIG["document_types"][dtype]["ground_truth"]
    return list(yaml.safe_load(path.read_text()))[:SMALL_CASES]


def _split(name: str) -> tuple[str, str, str | None]:
    """Split `CASE001_receipt_v2.png` into (case, type, tier suffix or None)."""
    match = re.fullmatch(r"(CASE\d{3})_([a-z_]+?)(?:_(v\d+))?\.png", name)
    assert match, name
    return match.group(1), match.group(2), match.group(3)


@pytest.fixture(scope="module")
def clean(export: SnapshotSummary) -> Path:
    return export.half_dirs["plain"]


@pytest.fixture(scope="module")
def degraded(export: SnapshotSummary) -> Path:
    return export.half_dirs["degraded"]


def test_clean_dir_holds_every_declared_type_once_per_case(clean: Path) -> None:
    """The clean half holds one image per ground-truth case for every declared type."""
    expected = {
        f"{case}_{SCHEMA.resolve_doc_type(dtype)}.png" for dtype in DECLARED for case in _cases(dtype)
    }
    assert {p.name for p in clean.glob("*.png")} == expected


def test_degraded_dir_holds_exactly_the_types_declared_for_degradation(degraded: Path) -> None:
    """The degraded half holds only the degraded type, and that type is one the corpus declares.

    (adjusted: there is no `degrade_types` config key any more. The exporter degrades
    one type, receipts, named by the `receipt_degradation:` block and held in
    `generators.eval_set._DEGRADED_TYPE`; the check is against that constant.)
    """
    types = {_split(p.name)[1] for p in degraded.glob("*.png")}
    assert types == {_DEGRADED_TYPE}
    assert _DEGRADED_TYPE in {SCHEMA.resolve_doc_type(d) for d in DECLARED}


def test_types_not_declared_for_degradation_have_no_variants(clean: Path, degraded: Path) -> None:
    """A clean-only type must appear in the clean half and nowhere else."""
    clean_only = {SCHEMA.resolve_doc_type(d) for d in DECLARED} - {_DEGRADED_TYPE}
    assert clean_only
    clean_types = {_split(p.name)[1] for p in clean.glob("*.png")}
    degraded_types = {_split(p.name)[1] for p in degraded.glob("*.png")}
    assert clean_only <= clean_types
    assert clean_only.isdisjoint(degraded_types)
    for record in read_jsonl(degraded / "ground_truth.jsonl"):
        assert SCHEMA.resolve_doc_type(record["DOCUMENT_TYPE"]) not in clean_only


def test_every_degraded_document_has_one_image_per_tier(degraded: Path) -> None:
    """Each degraded case carries exactly one image per declared tier suffix."""
    tiers_by_case: dict[str, list[str]] = {}
    for image in sorted(degraded.glob("*.png")):
        case, _, suffix = _split(image.name)
        assert suffix is not None
        tiers_by_case.setdefault(case, []).append(suffix)
    assert tiers_by_case
    for case, found in tiers_by_case.items():
        assert found == SUFFIXES, f"{case}: {found}"


def test_degraded_count_is_cases_times_types_times_tiers(export: SnapshotSummary, degraded: Path) -> None:
    """Degraded images = cases of the degraded type x tiers declared (one type degrades)."""
    cases = _cases("receipts")
    assert len(list(degraded.glob("*.png"))) == len(cases) * 1 * len(TIERS)
    assert export.total("degraded") == len(cases) * len(TIERS)


def test_degraded_ground_truth_has_one_row_per_variant(degraded: Path) -> None:
    """The degraded ground truth has exactly one record and one CSV row per variant image."""
    images = sorted(p.name for p in degraded.glob("*.png"))
    assert [r["filename"] for r in read_jsonl(degraded / "ground_truth.jsonl")] == images
    assert [r["image_file"] for r in read_csv(degraded / "ground_truth.csv")[1]] == images


def test_variants_of_a_case_share_identical_field_values(degraded: Path) -> None:
    """The value-F1 contract: distortion never changes the answer."""
    by_case: dict[str, list[dict]] = {}
    for record in read_jsonl(degraded / "ground_truth.jsonl"):
        by_case.setdefault(_split(record["filename"])[0], []).append(
            {k: v for k, v in record.items() if k != "filename"}
        )
    assert by_case
    for case, rows in by_case.items():
        assert len(rows) == len(TIERS)
        assert all(row == rows[0] for row in rows), case


def test_degraded_jsonl_matches_the_csv(degraded: Path) -> None:
    """The JSONL keys the image as `filename`; only the CSV renames it to `image_file`."""
    records = read_jsonl(degraded / "ground_truth.jsonl")
    header, rows = read_csv(degraded / "ground_truth.csv")
    assert "filename" not in header and header[0] == "image_file"
    assert all("image_file" not in r for r in records)
    assert len(records) == len(rows)
    for record, row in zip(records, rows):
        assert row["image_file"] == record["filename"]
        assert {k: v for k, v in row.items() if k != "image_file"} == {
            k: v for k, v in record.items() if k != "filename"
        }


def test_variant_values_match_the_clean_source_row(clean: Path, degraded: Path) -> None:
    """A variant carries its source document's answers, not merely values consistent across tiers."""
    source = {r["filename"]: r for r in read_jsonl(clean / "ground_truth.jsonl")}
    for record in read_jsonl(degraded / "ground_truth.jsonl"):
        case, dtype, _ = _split(record["filename"])
        origin = source[f"{case}_{dtype}.png"]
        assert {k: v for k, v in record.items() if k != "filename"} == {
            k: v for k, v in origin.items() if k != "filename"
        }


def test_degraded_header_carries_no_column_for_an_absent_type(degraded: Path) -> None:
    """No column that would be NOT_FOUND in every row.

    A type excluded from the degraded half must not leave its columns behind: the
    header is the receipt fields and nothing else, so the bank-statement-only
    columns are absent.
    """
    header, rows = read_csv(degraded / "ground_truth.csv")
    assert header == ["image_file", *SCHEMA.get_extraction_fields(_DEGRADED_TYPE)]
    bank_only = set(SCHEMA.get_extraction_fields("bank_statement")) - set(
        SCHEMA.get_extraction_fields(_DEGRADED_TYPE)
    )
    assert bank_only and bank_only.isdisjoint(header)
    # The receipt fields themselves stay: receipts legitimately print no payer, and
    # that is a property of the type, not a column left behind by an absent one.
    assert rows


def test_case_ids_still_resolve_for_linking(clean: Path, degraded: Path) -> None:
    """Case ids are unsuffixed, so transaction_links.yml keeps resolving.

    Every linked receipt's case id among the exported cases has a clean receipt and a full set of
    degraded variants.
    """
    links = yaml.safe_load((REPO_ROOT / "ground_truth" / "transaction_links.yml").read_text())
    linked_cases = {re.match(r"(CASE\d{3})_", key).group(1) for key in links if "receipt" in key}  # type: ignore[union-attr]
    linked_cases &= set(_cases("receipts"))
    assert linked_cases
    clean_receipts = {_split(p.name)[0] for p in clean.glob("*_receipt.png")}
    degraded_cases = {_split(p.name)[0] for p in degraded.glob("*.png")}
    assert linked_cases <= clean_receipts
    assert linked_cases <= degraded_cases
    assert all(re.fullmatch(r"CASE\d{3}", c) for c in degraded_cases)


def test_summary_reports_both_counts(export: SnapshotSummary) -> None:
    """The summary's counts equal the images actually written to each half, per document type."""
    for half, directory in export.half_dirs.items():
        assert export.total(half) == len(list(directory.glob("*.png")))
        for doc_type, count in export.counts[half].items():
            assert count == len(list(directory.glob(f"CASE*_{doc_type}*.png"))), (half, doc_type)


def test_every_degraded_row_names_a_declared_tier(degraded: Path) -> None:
    """Every degraded ground-truth row names a tier declared in the config.

    (adjusted: the quality ground truth this was asserted against no longer exists; the
    same property is checked on the extraction ground truth's filenames.)
    """
    for record in read_jsonl(degraded / "ground_truth.jsonl"):
        assert _split(record["filename"])[2] in SUFFIXES


def test_a_case_carries_identical_extraction_values_across_conditions(clean: Path, degraded: Path) -> None:
    """Distortion never changes the answer: a case's clean row and all its variants agree.

    (adjusted: checked across the two halves directly, since there is no combined directory.)
    """
    rows: dict[str, list[dict]] = {}
    for directory in (clean, degraded):
        for record in read_jsonl(directory / "ground_truth.jsonl"):
            case, dtype, _ = _split(record["filename"])
            if dtype == _DEGRADED_TYPE:
                rows.setdefault(case, []).append({k: v for k, v in record.items() if k != "filename"})
    assert rows
    for case, group in rows.items():
        assert len(group) == 1 + len(TIERS), case
        assert all(row == group[0] for row in group), case
