"""Tests for the doc_refs link mapper (spec Mapping C).

Example links are invented (case ids CASE9xx, 2031 dates); the real-corpus
count test reads ground_truth/transaction_links.yml.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from generators.derive_outputs import derive_links
from generators.exporters.config import load_export_config
from generators.exporters.links import transaction_links_to_doc_refs
from tests.conftest import REPO_ROOT, assert_diagnostic_error

LINKS_PATH = REPO_ROOT / "ground_truth" / "transaction_links.yml"
CONFIG_PATH = REPO_ROOT / "config" / "export_config.yml"


def _link(bank: str = "CASE901_bank_a.png", amount: str = "41.20", difficulty: str = "easy") -> dict:
    return {
        "bank_statement": bank,
        "supplier": "Fixture Supplies Pty Ltd",
        "receipt_date": "03/05/2031",
        "receipt_total": amount,
        "bank_date": "04/05/2031",
        "bank_description": "CARD PURCHASE FIXTURE SUP",
        "bank_amount": amount,
        "match_status": "FOUND",
        "match_difficulty": difficulty,
        "notes": "invented link",
    }


def test_transaction_link_maps_to_doc_refs() -> None:
    """One link becomes one receipt_to_bank record carrying every source field in its place."""
    records = transaction_links_to_doc_refs({"CASE901_receipt_a.png": [_link()]}, "spaced")
    assert records == [
        {
            "link_type": "receipt_to_bank",
            "source_doc": "CASE901_receipt_a.png",
            "target_doc": "CASE901_bank_a.png",
            "match_keys": {"supplier": "Fixture Supplies Pty Ltd", "date": "03/05/2031", "amount": "41.20"},
            "target_evidence": {
                "date": "04/05/2031",
                "description": "CARD PURCHASE FIXTURE SUP",
                "amount": "41.20",
            },
            "label": "FOUND",
            "difficulty": "easy",
            "notes": "invented link",
        }
    ]


def test_notes_default_to_empty_when_the_link_has_none() -> None:
    """A link with no notes key still maps, with an empty notes string."""
    link = _link()
    del link["notes"]
    assert transaction_links_to_doc_refs({"CASE901_receipt_a.png": [link]}, "spaced")[0]["notes"] == ""


def test_one_source_with_several_targets_yields_several_records() -> None:
    """The YAML value is a list: one receipt may match several bank rows."""
    data = {
        "CASE901_receipt_a.png": [
            _link("CASE901_bank_a.png", "10.00"),
            _link("CASE901_bank_b.png", "12.00", "hard"),
        ],
        "CASE902_receipt_a.png": [_link("CASE902_bank_a.png", "5.00", "medium")],
    }
    records = transaction_links_to_doc_refs(data, "spaced")
    assert [(r["source_doc"], r["target_doc"], r["difficulty"]) for r in records] == [
        ("CASE901_receipt_a.png", "CASE901_bank_a.png", "easy"),
        ("CASE901_receipt_a.png", "CASE901_bank_b.png", "hard"),
        ("CASE902_receipt_a.png", "CASE902_bank_a.png", "medium"),
    ]


def test_transaction_links_rejects_unknown_identifier_form_diagnostic() -> None:
    """Transaction links carry no ABN or TFN, so identifier_form is otherwise unused on this path, but an
    invalid value (e.g. a typo'd abn_tfn_canonical_form in config/export_config.yml) must still fail fast
    here, rather than being silently accepted because this path never happens to call canonical_identifier
    itself."""
    with pytest.raises(ValueError, match="dotted") as err:
        transaction_links_to_doc_refs({"CASE901_receipt_a.png": [_link()]}, "dotted")
    assert_diagnostic_error(err.value)
    assert "abn_tfn_canonical_form" in str(err.value)


def test_derive_links_writes_transaction_jsonl(tmp_path: Path) -> None:
    """derive_links writes one JSON object per line, matching the mapper's records exactly."""
    source = tmp_path / "links.yml"
    data = {
        "CASE901_receipt_a.png": [_link()],
        "CASE902_receipt_a.png": [_link("CASE902_bank_a.png", "7.00")],
    }
    source.write_text(yaml.safe_dump(data))
    config = {"abn_tfn_canonical_form": "digits_only"}
    out = derive_links(source, config, tmp_path / "doc_refs.jsonl")
    lines = out.read_text().splitlines()
    assert [json.loads(line) for line in lines] == transaction_links_to_doc_refs(data, "digits_only")
    assert len(lines) == 2


def test_derive_links_creates_parent_directories(tmp_path: Path) -> None:
    """Mirrors derive_csv/derive_jsonl/derive_cord's mkdir(parents=True) behaviour."""
    source = tmp_path / "links.yml"
    source.write_text(yaml.safe_dump({"CASE901_receipt_a.png": [_link()]}))
    target = tmp_path / "a" / "b" / "doc_refs.jsonl"
    assert not target.parent.exists()
    out = derive_links(source, {"abn_tfn_canonical_form": "spaced"}, target)
    assert out == target
    assert target.exists()
    assert len(target.read_text().splitlines()) == 1


def test_real_derived_output_has_expected_counts(tmp_path: Path) -> None:
    """A silent record drop is the main failure mode of this task: run the real derivation over the actual
    repo ground truth and pin exact counts per difficulty band, per the Task 1 Step 1 figures."""
    config = load_export_config(CONFIG_PATH)
    out = derive_links(LINKS_PATH, config, tmp_path / "doc_refs.jsonl")
    records = [json.loads(line) for line in out.read_text().splitlines()]

    source = yaml.safe_load(LINKS_PATH.read_text())
    expected_bands = Counter(link["match_difficulty"] for links in source.values() for link in links)
    # One record per YAML link, none dropped, none invented.
    assert len(records) == sum(len(links) for links in source.values())
    assert Counter(r["difficulty"] for r in records) == expected_bands
    # The Task 1 Step 1 figures: 110 links, easy 52 / medium 36 / hard 22.
    assert len(records) == 110
    assert dict(expected_bands) == {"easy": 52, "medium": 36, "hard": 22}
    assert {r["link_type"] for r in records} == {"receipt_to_bank"}
