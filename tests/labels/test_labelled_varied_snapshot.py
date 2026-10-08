"""The labelled varied renders are pinned: hash, size, scored boxes and each page's label choices.

Stage 6, Task 3. `tests/fixtures/labelled_varied_pixel_snapshot.json` holds all 165 pages, re-captured only
by `tests/regenerate_labelled_varied_pixel_snapshot.py --confirm` under the pinned Pillow. Bank statements
vary no label, so their pins are the distractor snapshot's.
"""

import subprocess
import sys
from collections import Counter

import pytest

from generators.labels.config import load_label_config
from generators.labels.keys import LABEL_STYLES
from tests.conftest import REPO_ROOT
from tests.snapshot_tools import (
    DISTRACTOR_DOC_TYPES,
    DISTRACTOR_SNAPSHOT_PATH,
    EXPECTED_PER_TYPE,
    LABELLED_VARIED_SNAPSHOT_PATH,
    compare,
    load_snapshot,
    render_corpus,
)

TYPES = [
    pytest.param("receipt"),
    pytest.param("invoice", marks=pytest.mark.slow),
    pytest.param("bank_statement", marks=pytest.mark.slow),
]


def _pinned(doc_type: str) -> dict[str, dict]:
    return {k: v for k, v in load_snapshot(LABELLED_VARIED_SNAPSHOT_PATH).items() if v["type"] == doc_type}


def test_the_snapshot_pins_every_page_of_every_type() -> None:
    pinned = load_snapshot(LABELLED_VARIED_SNAPSHOT_PATH)
    assert Counter(record["type"] for record in pinned.values()) == dict.fromkeys(
        DISTRACTOR_DOC_TYPES, EXPECTED_PER_TYPE
    )
    assert set(pinned) == set(load_snapshot(DISTRACTOR_SNAPSHOT_PATH))
    for key, record in pinned.items():
        assert set(record) == {"type", "hash", "size", "boxes", "choices"}, key
        assert set(record["choices"]) == {"styles", "refusals"}, key


def test_bank_pins_are_the_distractor_pins() -> None:
    distractor = load_snapshot(DISTRACTOR_SNAPSHOT_PATH)
    for key, record in _pinned("bank_statement").items():
        assert {k: v for k, v in record.items() if k != "choices"} == distractor[key], key
        assert record["choices"] == {"styles": {}, "refusals": []}, key


def test_every_style_of_every_varied_field_is_pinned_in_receipts_and_invoices() -> None:
    config = load_label_config()
    for doc_type in ("receipt", "invoice"):
        seen = Counter(
            (field, pick["style"])
            for record in _pinned(doc_type).values()
            for field, pick in record["choices"]["styles"].items()
        )
        for field in config.doc_types[doc_type]:
            for style in LABEL_STYLES:
                assert seen[(field, style)] > 0, (doc_type, field, style)


def test_the_receipt_and_invoice_pages_differ_from_their_distractor_pins_somewhere() -> None:
    """Label variation reaches the pixels: most pages print some label differently."""
    distractor = load_snapshot(DISTRACTOR_SNAPSHOT_PATH)
    for doc_type in ("receipt", "invoice"):
        pinned = _pinned(doc_type)
        moved = [key for key, record in pinned.items() if record["hash"] != distractor[key]["hash"]]
        varied = [
            key
            for key, record in pinned.items()
            if any(pick["style"] != "labelled" for pick in record["choices"]["styles"].values())
        ]
        assert sorted(moved) == sorted(varied), doc_type


@pytest.mark.parametrize("doc_type", TYPES)
def test_every_render_matches_its_pin_and_repeats_exactly(doc_type: str) -> None:
    current = render_corpus((doc_type,), distractors=True, labels=True)
    problems = compare(_pinned(doc_type), current)
    assert not problems, "\n".join(problems)
    assert not compare(current, render_corpus((doc_type,), distractors=True, labels=True))


def test_a_labels_corpus_without_distractors_is_refused() -> None:
    with pytest.raises(ValueError, match="distractor"):
        render_corpus(("receipt",), labels=True)


@pytest.mark.slow
def test_regenerate_without_confirm_writes_nothing() -> None:
    before = LABELLED_VARIED_SNAPSHOT_PATH.stat().st_mtime_ns
    result = subprocess.run(
        [sys.executable, "tests/regenerate_labelled_varied_pixel_snapshot.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout.lower() and "0 differences" in result.stdout
    assert LABELLED_VARIED_SNAPSHOT_PATH.stat().st_mtime_ns == before


def test_regenerate_refuses_an_unpinned_pillow() -> None:
    before = LABELLED_VARIED_SNAPSHOT_PATH.stat().st_mtime_ns
    result = subprocess.run(
        [
            sys.executable,
            "tests/regenerate_labelled_varied_pixel_snapshot.py",
            "--confirm",
            "--pretend-pillow",
            "0.0.0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "pillow" in (result.stdout + result.stderr).lower()
    assert LABELLED_VARIED_SNAPSHOT_PATH.stat().st_mtime_ns == before
