"""The doc_refs export, scored against its own ground truth, must be perfect.

``linking.link_validator.validate_links`` already computes precision/recall/F1
overall and per difficulty (spec section 8.6, the ``LinkScore``/``DifficultyScore``
dataclasses); this file proves the doc_refs export round-trips through that
*existing* validator rather than standing up a second scorer.

The validator's interface::

    validate_links(ground_truth: dict, predictions: dict[str, dict]) -> LinkScore

- ``ground_truth`` is keyed by receipt filename (``source_doc`` in doc_refs
  terms); each value is a *list* of link records (only index ``[0]`` is read
  by the validator), each with ``bank_statement``, ``bank_amount`` and
  ``match_difficulty`` keys.
- ``predictions`` is keyed the same way, but each value is a single dict
  ``{"bank_statement": ..., "bank_amount": ...}``, with no list wrapper.

``_ground_truth_and_predictions`` reshapes the exported doc_refs records
(which carry ``target_doc``, ``match_keys.amount`` and ``difficulty``) into
that exact shape.

A self-score alone (``score(x, x) == 1.0``) is near-tautological: it proves
the scorer ingests our shape but not that it can catch a wrong mapping. Every
perfect-score assertion below is therefore paired with a mutation test that
proves the corresponding gate can actually fail.

The export is the session-shared `derived` fixture, read-only: every mutation
works on a deep copy.
"""

import copy
import json
from pathlib import Path

import pytest

from linking.link_validator import LinkScore, validate_links

DIFFICULTIES = ("easy", "medium", "hard")


@pytest.fixture(scope="module")
def records(derived: Path) -> list[dict]:
    return [json.loads(line) for line in (derived / "doc_refs.jsonl").read_text().splitlines()]


def _ground_truth_and_predictions(records: list[dict]) -> tuple[dict, dict[str, dict]]:
    """Reshape doc_refs records into the validator's ground-truth and prediction mappings."""
    ground_truth: dict[str, list[dict]] = {}
    predictions: dict[str, dict] = {}
    for record in records:
        source = record["source_doc"]
        link = {
            "bank_statement": record["target_doc"],
            "bank_amount": record["match_keys"]["amount"],
            "match_difficulty": record["difficulty"],
        }
        ground_truth.setdefault(source, []).append(link)
        predictions[source] = {"bank_statement": link["bank_statement"], "bank_amount": link["bank_amount"]}
    return ground_truth, predictions


def _first_source_in_band(ground_truth: dict, difficulty: str) -> str:
    return next(s for s, links in ground_truth.items() if links[0]["match_difficulty"] == difficulty)


def _assert_perfect(score: LinkScore) -> None:
    assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)
    assert score.false_positives == score.false_negatives == 0


def test_every_difficulty_band_is_populated(records: list[dict]) -> None:
    """A band with no links would self-score perfectly while testing nothing."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    score = validate_links(ground_truth, predictions)
    assert set(score.by_difficulty) == set(DIFFICULTIES)
    for band in DIFFICULTIES:
        assert score.by_difficulty[band].true_positives > 0, band
    assert (
        sum(b.true_positives for b in score.by_difficulty.values()) == score.true_positives == len(records)
    )


def test_transaction_links_self_score_is_perfect(records: list[dict]) -> None:
    """Feeding the export back to itself as predictions must score a perfect 1.0 overall and in every
    per-difficulty band."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    score = validate_links(ground_truth, predictions)
    _assert_perfect(score)
    for band in DIFFICULTIES:
        band_score = score.by_difficulty[band]
        assert (band_score.precision, band_score.recall, band_score.f1) == (1.0, 1.0, 1.0), band


def test_dropping_one_link_degrades_recall_only(records: list[dict]) -> None:
    """Removing one prediction must strictly lower recall (a real link is no longer predicted) while leaving
    precision at 1.0 (no spurious link was added), proving the perfect-score gate can actually fail on a
    missing link, not just pass on a tautological self-comparison."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    predictions = copy.deepcopy(predictions)
    del predictions[next(iter(predictions))]
    score = validate_links(ground_truth, predictions)
    assert score.recall < 1.0
    assert score.precision == 1.0
    assert (score.true_positives, score.false_negatives, score.false_positives) == (len(records) - 1, 1, 0)
    assert score.recall == pytest.approx((len(records) - 1) / len(records))


def test_adding_a_spurious_link_degrades_precision_only(records: list[dict]) -> None:
    """Adding one prediction absent from ground truth must strictly lower precision while recall (every real
    link is still predicted) stays 1.0."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    predictions = copy.deepcopy(predictions)
    predictions["CASE999_receipt_invented.png"] = {
        "bank_statement": "CASE999_bank.png",
        "bank_amount": "1.00",
    }
    score = validate_links(ground_truth, predictions)
    assert score.precision < 1.0
    assert score.recall == 1.0
    assert (score.true_positives, score.false_positives, score.false_negatives) == (len(records), 1, 0)
    assert score.precision == pytest.approx(len(records) / (len(records) + 1))


def test_corrupting_a_target_doc_degrades_the_score(records: list[dict]) -> None:
    """Changing one prediction's bank_statement to a wrong value (rather than deleting the prediction
    outright) must turn that true positive into a false negative, proving the equality check on
    bank_statement is actually exercised rather than the validator merely checking key presence."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    victim = next(iter(predictions))
    wrong = copy.deepcopy(predictions)
    wrong[victim]["bank_statement"] = "CASE999_bank_wrong.png"
    score = validate_links(ground_truth, wrong)
    assert score.recall < 1.0
    assert score.true_positives == len(records) - 1
    assert score.false_negatives == 1
    # The validator counts a wrong target only as a miss (the source is known to ground truth), so
    # precision is unchanged; the score still falls through recall and therefore through f1.
    assert score.false_positives == 0
    assert score.f1 < 1.0
    # A wrong amount on the right statement is likewise a miss.
    wrong_amount = copy.deepcopy(predictions)
    wrong_amount[victim]["bank_amount"] = "0.01"
    assert validate_links(ground_truth, wrong_amount).false_negatives == 1


def test_removing_only_hard_links_degrades_the_hard_band_alone(records: list[dict]) -> None:
    """Dropping a single hard-difficulty prediction must degrade only the hard per-difficulty band, proving
    by_difficulty is wired through to real per-link difficulty data rather than merely reported alongside
    the overall score."""
    ground_truth, predictions = _ground_truth_and_predictions(records)
    predictions = copy.deepcopy(predictions)
    del predictions[_first_source_in_band(ground_truth, "hard")]
    score = validate_links(ground_truth, predictions)
    assert score.by_difficulty["hard"].recall < 1.0
    assert score.by_difficulty["hard"].false_negatives == 1
    for band in ("easy", "medium"):
        assert score.by_difficulty[band].recall == 1.0, band
        assert score.by_difficulty[band].false_negatives == 0, band
    assert score.recall < 1.0
