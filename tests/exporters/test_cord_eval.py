"""Tests for the vendored, apted-backed CORD evaluator (generators.exporters.cord_eval).

This is the real deliverable of Task 6: a vendored tree-edit-distance metric that
silently always returned 1.0 would make every later self-scoring gate worthless, so
these tests exist to prove the metric actually discriminates between correct and
incorrect predictions, not just that it round-trips identity.
"""

import copy

import pytest
import yaml

from generators.exporters.cord import to_cord
from generators.exporters.cord_eval import cal_acc, cal_f1
from tests.conftest import REPO_ROOT, assert_diagnostic_error


def _case001_tree() -> dict:
    """The gt_parse tree of the first receipt in the live ground truth."""
    data = yaml.safe_load((REPO_ROOT / "ground_truth" / "receipts.yml").read_text())
    return to_cord(data["CASE001"]["fields"], "spaced")


def test_case001_cord_self_score_is_perfect() -> None:
    """A perfect prediction must score exactly 1.0 against itself."""
    tree = _case001_tree()
    assert cal_acc(copy.deepcopy(tree), tree) == 1.0


def test_case001_tree_is_not_empty() -> None:
    """Guard against a no-op mapper: the CASE001 tree must actually be populated.

    cal_acc(x, x) == 1.0 is trivially true for x == {} too, so the identity test
    would pass even if to_cord emitted nothing. This checks real content.
    """
    tree = _case001_tree()
    assert tree["menu"]
    assert tree["total"]["total_price"]
    assert tree["extension"]["supplier_name"]


def test_cal_acc_on_two_empty_dicts_fails_fast_instead_of_lying() -> None:
    """cal_acc({}, {}) must not silently report a perfect score.

    The upstream evaluator raises a bare ZeroDivisionError for this input; the
    vendored version raises a diagnostic ValueError instead. A degenerate
    empty-vs-empty comparison carries no information and must never be read
    as 'perfect'.
    """
    with pytest.raises(ValueError) as err:
        cal_acc({}, {})
    assert_diagnostic_error(err.value)


def test_one_field_wrong_scores_strictly_between_zero_and_one() -> None:
    """One wrong leaf value drags the score below 1.0 but leaves it well above 0."""
    gold = _case001_tree()
    pred = copy.deepcopy(gold)
    pred["total"]["total_price"] = "0.01"
    score = cal_acc(pred, gold)
    assert 0.0 < score < 1.0
    assert score > 0.5


def test_everything_different_scores_near_zero() -> None:
    """A prediction sharing no values with the reference scores close to zero."""
    gold = _case001_tree()
    pred = {
        "menu": [{"nm": "ZZZZ", "cnt": "9", "unitprice": "Q", "price": "W"}],
        "total": {"total_price": "XXXXXXXX"},
        "extension": {"supplier_name": "YYYYYYYYYYYYYYYYYYYY"},
    }
    assert cal_acc(pred, gold) < 0.2


def test_two_fields_wrong_does_not_score_higher_than_one_field_wrong() -> None:
    """Corrupting a second field never raises the score above that of one corruption."""
    gold = _case001_tree()
    one = copy.deepcopy(gold)
    one["total"]["total_price"] = "0.01"
    two = copy.deepcopy(one)
    two["sub_total"]["tax_price"] = "0.01"
    assert cal_acc(two, gold) <= cal_acc(one, gold)
    assert cal_acc(two, gold) < 1.0


def test_cal_f1_perfect_predictions_score_one() -> None:
    """Identical prediction and reference lists score an F1 of exactly 1.0."""
    gold = [_case001_tree(), {"total": {"total_price": "5.00"}}]
    assert cal_f1(copy.deepcopy(gold), gold) == 1.0


def test_cal_f1_one_wrong_document_scores_strictly_less_than_one() -> None:
    """One wrong document in a list pulls the micro F1 below 1.0, but not to 0."""
    gold = [_case001_tree(), {"total": {"total_price": "5.00"}}]
    pred = copy.deepcopy(gold)
    pred[1]["total"]["total_price"] = "6.00"
    score = cal_f1(pred, gold)
    assert 0.0 < score < 1.0


def test_cal_f1_rejects_mismatched_list_lengths() -> None:
    """Mismatched list lengths raise rather than silently truncating via zip().

    Silent truncation is exactly the kind of failure the project's fail-fast
    convention forbids.
    """
    gold = [{"total": {"total_price": "5.00"}}] * 2
    with pytest.raises(ValueError) as err:
        cal_f1(gold[:1], gold)
    assert_diagnostic_error(err.value)


def test_missing_menu_item_reduces_score() -> None:
    """A prediction that omits one menu row scores below a perfect 1.0."""
    gold = _case001_tree()
    assert len(gold["menu"]) > 1
    pred = copy.deepcopy(gold)
    pred["menu"].pop()
    assert cal_acc(pred, gold) < 1.0
    assert cal_f1([pred], [gold]) < 1.0


def test_extra_menu_item_reduces_score() -> None:
    """A prediction with a spurious extra menu row scores below a perfect 1.0."""
    gold = _case001_tree()
    pred = copy.deepcopy(gold)
    pred["menu"].append({"nm": "Phantom Item", "cnt": "1", "unitprice": "1.00", "price": "1.00"})
    assert cal_acc(pred, gold) < 1.0
    assert cal_f1([pred], [gold]) < 1.0
