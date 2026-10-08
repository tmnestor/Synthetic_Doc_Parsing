"""Transaction link validator — scores predictions against ground truth.

Computes precision, recall, F1 at each difficulty level.

Ground truth format (transaction_links.yml):
    receipt_filename.png:
    - bank_statement: bank_filename.png
      bank_amount: "34.16"
      match_difficulty: easy
      ...

Prediction format:
    {receipt_filename: {"bank_statement": ..., "bank_amount": ...}}
"""

from dataclasses import dataclass, field


@dataclass
class DifficultyScore:
    """Scores for a single difficulty level."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class LinkScore:
    """Overall and per-difficulty linking scores."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    by_difficulty: dict[str, DifficultyScore] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def validate_links(
    ground_truth: dict,
    predictions: dict[str, dict],
) -> LinkScore:
    """Score linking predictions against ground truth.

    Args:
        ground_truth: Dict keyed by receipt filename from transaction_links.yml.
            Each value is a list of link records with 'bank_statement',
            'bank_amount', and 'match_difficulty' fields.
        predictions: Dict mapping receipt_filename -> {bank_statement, bank_amount}.

    Returns:
        LinkScore with overall and per-difficulty metrics.
    """
    score = LinkScore()

    # Build lookup: receipt_filename -> (bank_statement, bank_amount, difficulty)
    gt_lookup: dict[str, tuple[str, str, str]] = {}
    for receipt_filename, link_list in ground_truth.items():
        link = link_list[0]
        gt_lookup[receipt_filename] = (
            link["bank_statement"],
            str(link["bank_amount"]),
            link.get("match_difficulty", "unknown"),
        )

    for receipt_filename, (gt_bank, gt_amount, difficulty) in gt_lookup.items():
        if difficulty not in score.by_difficulty:
            score.by_difficulty[difficulty] = DifficultyScore()

        pred = predictions.get(receipt_filename)
        if pred and pred["bank_statement"] == gt_bank and str(pred["bank_amount"]) == gt_amount:
            score.true_positives += 1
            score.by_difficulty[difficulty].true_positives += 1
        else:
            score.false_negatives += 1
            score.by_difficulty[difficulty].false_negatives += 1

    for receipt_filename in predictions:
        if receipt_filename not in gt_lookup:
            score.false_positives += 1

    return score
