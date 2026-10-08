"""Every CORD-exported document must score 1.0 against itself, and the gate that says so must be able to fail.

Uses the vendored, apted-backed evaluator at generators/exporters/cord_eval.py
(cal_acc/cal_f1) rather than the unavailable ``donut.util.JSONParseEvaluator``;
see that module's docstring for provenance.

The self-score (``test_whole_corpus_self_scores_perfectly``) proves the
evaluator ACCEPTS our schema: it normalises, flattens and tree-edits our trees
without choking. It cannot prove the evaluator would NOTICE a wrong field
mapping, because scoring any non-degenerate corpus against itself is
guaranteed to return 1.0. A gate that cannot fail manufactures false confidence.

The mutation tests supply the missing half: they take the real CORD corpus,
perturb copies of it, and assert the score genuinely degrades, for a single
wrong value, a dropped menu row, a dropped top-level key, monotonically with
the amount of wrongness, and for a unitprice/price field swap.

Every mutation operates on a ``copy.deepcopy`` of the pristine trees. If a
mutation leaked into the baseline, these tests would compare wrong against
wrong and pass spuriously, so ``test_mutation_helpers_never_touch_the_pristine_corpus``
asserts the isolation directly.

The corpus is derived directly from the ground truth into a scratch directory
(no rendering needed: CORD trees are values only).
"""

import copy
import json

import pytest
import yaml

from generators.derive_outputs import derive_cord
from generators.exporters.config import load_export_config
from generators.exporters.cord_eval import cal_f1
from generators.exporters.cord_score import score_cord
from tests.conftest import REPO_ROOT

GT_DIR = REPO_ROOT / "ground_truth"
EXPORT_CONFIG = REPO_ROOT / "config" / "export_config.yml"


@pytest.fixture(scope="module")
def records(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    """The CORD records derived from the real receipts and invoices."""
    out = tmp_path_factory.mktemp("cord_corpus") / "cord.jsonl"
    config = load_export_config(EXPORT_CONFIG)
    derive_cord([GT_DIR / "receipts.yml", GT_DIR / "invoices.yml"], config, out)
    return [json.loads(line) for line in out.read_text().splitlines()]


@pytest.fixture
def trees(records: list[dict]) -> list[dict]:
    """A fresh deep copy of every gt_parse tree, safe for a test to mutate."""
    return [copy.deepcopy(r["gt_parse"]) for r in records]


@pytest.fixture(scope="module")
def pristine(records: list[dict]) -> list[dict]:
    return [copy.deepcopy(r["gt_parse"]) for r in records]


def test_corpus_is_populated_and_non_trivial(records: list[dict]) -> None:
    """Guard for the self-score: a perfect score over a trivial corpus would be meaningless.

    One record per receipt and invoice, and every record carries menu, total and sub_total
    somewhere, with the corpus as a whole carrying all three.
    """
    expected = sum(len(yaml.safe_load((GT_DIR / n).read_text())) for n in ("receipts.yml", "invoices.yml"))
    assert len(records) == expected
    assert expected > 0
    trees = [r["gt_parse"] for r in records]
    assert all("menu" in t and "total" in t for t in trees)
    assert any("sub_total" in t for t in trees)


def test_whole_corpus_self_scores_perfectly(pristine: list[dict]) -> None:
    """The evaluator accepts every tree: the whole corpus scored against a copy of itself is 1.0."""
    assert cal_f1(copy.deepcopy(pristine), pristine) == 1.0


def test_whole_corpus_self_scores_perfectly_via_score_cord(pristine: list[dict]) -> None:
    """The production path: score through score_cord with the REAL export config.

    cord_extension_scoring is 'excluded_scored_separately', so this must report a
    perfect headline_f1 (extension excluded) AND a perfect extension_f1, proving
    the key is consumed by production code, not merely validated.
    """
    config = load_export_config(EXPORT_CONFIG)
    assert config["cord_extension_scoring"] == "excluded_scored_separately"
    result = score_cord(copy.deepcopy(pristine), pristine, config)
    assert result["headline_f1"] == 1.0
    assert result["extension_f1"] == 1.0


def test_every_record_has_a_populated_tree(records: list[dict]) -> None:
    """No record carries an empty gt_parse, and each names its case and image."""
    for record in records:
        assert record["gt_parse"], record["case_id"]
        assert record["case_id"]
        assert record["image_file"].endswith(".png")


def test_mutation_helpers_never_touch_the_pristine_corpus(
    trees: list[dict], pristine: list[dict], records: list[dict]
) -> None:
    """Mutating the working copy leaves the baseline untouched.

    If a mutation leaked into the baseline, every assertion below would pass for
    the wrong reason.
    """
    trees[0]["total"]["total_price"] = "MUTATED"
    trees[0]["menu"].pop()
    del trees[1]["total"]
    assert pristine[0]["total"]["total_price"] != "MUTATED"
    assert pristine == [r["gt_parse"] for r in records]
    assert pristine != trees


def test_single_value_mutation_degrades_the_score(trees: list[dict], pristine: list[dict]) -> None:
    """Corrupting ONE leaf value in ONE document scores strictly below 1.0, yet well above 0.

    Nearly every other field of every other document is still correct.
    """
    trees[0]["total"]["total_price"] = "0.00"
    score = cal_f1(trees, pristine)
    assert 0.9 < score < 1.0


def test_dropping_one_menu_item_degrades_the_score(trees: list[dict], pristine: list[dict]) -> None:
    """Deleting one menu row from one document (structural, not just a wrong value) scores below 1.0."""
    victim = next(t for t in trees if len(t["menu"]) > 1)
    victim["menu"].pop()
    assert cal_f1(trees, pristine) < 1.0


def test_dropping_a_top_level_key_degrades_the_score(trees: list[dict], pristine: list[dict]) -> None:
    """Deleting an entire top-level subtree ('total') from one document scores below 1.0."""
    del trees[0]["total"]
    assert cal_f1(trees, pristine) < 1.0


def test_score_degrades_monotonically_with_the_number_of_wrong_documents(
    trees: list[dict], pristine: list[dict]
) -> None:
    """The gate is sensitive to HOW MUCH is wrong, not merely that something is.

    Corrupting the same field in ten documents scores strictly lower than in one.
    """
    one = copy.deepcopy(pristine)
    one[0]["total"]["total_price"] = "0.00"
    ten = copy.deepcopy(pristine)
    for tree in ten[:10]:
        tree["total"]["total_price"] = "0.00"
    assert cal_f1(ten, pristine) < cal_f1(one, pristine) < 1.0


def test_swapping_unitprice_and_price_degrades_the_score(trees: list[dict], pristine: list[dict]) -> None:
    """A unitprice/price field swap, the mapping bug the self-score cannot catch, scores below 1.0.

    Only rows where the two values genuinely differ (quantity > 1) are swapped:
    with quantity 1 the swap is a no-op no value-based metric could see. The
    test asserts such rows exist in the corpus AND that swapping them is
    detected.

    This proves the METRIC is sensitive to a swap. It cannot detect a real swap
    inside generators/exporters/cord.py, since both sides of a self-score come
    from that code path. The independent oracle is
    test_derive_cord_maps_unit_price_and_total_price_distinctly in
    tests/test_derive_cord.py; the two are complements, not substitutes.
    """
    swapped = 0
    for tree in trees:
        for row in tree["menu"]:
            if row["unitprice"] != row["price"]:
                row["unitprice"], row["price"] = row["price"], row["unitprice"]
                swapped += 1
    assert swapped > 0, "no row with quantity > 1 in the corpus; the swap would be invisible"
    assert cal_f1(trees, pristine) < 1.0


def test_extension_only_corruption_leaves_headline_perfect_but_drops_extension_score(
    trees: list[dict], pristine: list[dict]
) -> None:
    """Corrupting ONLY the extension subtree across the corpus leaves headline_f1 at 1.0.

    The headline is computed over the tree with 'extension' removed, while
    extension_f1 drops strictly below 1.0, proving the corruption was visible
    to the evaluator. Without this, cord_extension_scoring could be silently
    ignored and every other test here would still pass.
    """
    for tree in trees:
        tree["extension"]["supplier_name"] = "Corrupted Supplier"
    result = score_cord(trees, pristine, load_export_config(EXPORT_CONFIG))
    assert result["headline_f1"] == 1.0
    assert result["extension_f1"] < 1.0


def test_non_extension_corruption_still_drops_the_headline_score(
    trees: list[dict], pristine: list[dict]
) -> None:
    """Corrupting a NON-extension value (a total_price) still drags headline_f1 down.

    Exclusion applies only to 'extension', not to sensitivity in general.
    """
    trees[0]["total"]["total_price"] = "0.00"
    result = score_cord(trees, pristine, load_export_config(EXPORT_CONFIG))
    assert result["headline_f1"] < 1.0
    assert result["extension_f1"] == 1.0
