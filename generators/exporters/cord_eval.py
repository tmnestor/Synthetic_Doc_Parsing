"""Vendored, apted-backed port of Donut's CORD ``JSONParseEvaluator``.

ATTRIBUTION / PROVENANCE
-------------------------
Original source:
    https://github.com/clovaai/donut/blob/master/donut/util.py
    (class ``JSONParseEvaluator``, commit as fetched 2026-07-23)

Original licence (reproduced below in full, per its terms):

    Donut
    Copyright (c) 2022-present NAVER Corp.

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to
    the following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
    TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
    SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

This module is a DERIVED WORK: it re-implements ``JSONParseEvaluator``'s
public behaviour (``cal_acc``, ``cal_f1``) and every normalisation step it
performs before comparing trees (``flatten``, ``normalize_dict``,
``construct_tree_from_dict``), ported line-for-line where the algorithm
does not depend on the tree-edit-distance backend.

WHAT WAS CHANGED, AND WHY
--------------------------
The original evaluator is only importable via the ``donut-python`` PyPI
package, whose unbounded ``datasets[vision]`` dependency backtracks to
``datasets==1.8.0`` -> ``pyarrow==3.0.0`` -> ``numpy==1.19.4``, none of which
have Python 3.12 / arm64 wheels (see the project's Task 6 investigation).
Rather than drag in that (unusable, multi-GB, ML-training-oriented)
dependency tree for a ~200-line scoring utility, this module is vendored
directly into the repository, per
``docs/GroundTruth_Export_Spec.md``'s own explicit sanctioning of
"pip install donut-python, or copy (deps zss + nltk)".

Within that vendoring, one further substitution was made, also per the
spec: the original's tree-edit-distance backend, ``zss``
(https://pypi.org/project/zss/), carries a non-SPDX / historically-GPL
licence questionable for vendoring into this codebase. This port uses
``apted`` (MIT, https://pypi.org/project/apted/) instead. ``apted`` and
``zss`` both compute the *exact* (not approximate) tree edit distance for
ordered labelled trees under insert/delete/rename operations -- ``zss``
implements the classic Zhang-Shasha algorithm and ``apted`` implements the
newer, faster APTED algorithm for the same well-defined problem -- so for
an identical cost model the two backends must produce numerically
identical distances. The substitution is therefore in *backend*, not in
*metric*: this module translates ``zss``'s three cost callbacks
(``update_cost``, ``insert_and_remove_cost`` used for both insert and
remove) into ``apted``'s ``Config`` interface (``rename``, ``delete``,
``insert``, ``children``) with the same per-node-pair cost values.

The original's leaf-label string-edit-distance uses ``nltk.edit_distance``
(plain Levenshtein, unit costs, no transpositions). This port uses
``rapidfuzz.distance.Levenshtein.distance`` instead (MIT-licensed;
``nltk`` is GPL-side-effect-free but drags in a large corpus-download
ecosystem this project has no other use for). ``rapidfuzz``'s default
weights (1, 1, 1) reproduce standard Levenshtein distance identically to
``nltk.edit_distance``'s defaults -- verified directly against ``nltk`` for
this port (see Task 6 report) rather than assumed.

ONE DELIBERATE, DOCUMENTED BEHAVIOUR CHANGE
--------------------------------------------
The original ``cal_acc`` divides by
``zss.distance(<empty tree>, answer_tree, ...)``. When ``answer``
normalises to nothing (e.g. ``answer == {}``), that denominator is exactly
0, and the original evaluator raises a bare, undiagnosed
``ZeroDivisionError`` (confirmed by direct testing of the unmodified
upstream code against this exact input as part of this port -- it does
NOT return 1.0, despite that being a natural-seeming assumption). Per this
project's fail-fast convention, this port instead raises a diagnostic
``ValueError`` for that degenerate case rather than silently returning a
number OR crashing with an unlabelled exception. This is a genuine,
intentional divergence from upstream behaviour, called out here so nobody
mistakes this module for a byte-for-byte port at every edge case.
``cal_f1`` has the equivalent guard for its own 0/0 case (both field lists
empty), for the same reason.

Public interface (matches the original's shape):
    cal_acc(pred: dict, answer: dict) -> float
    cal_f1(preds: list[dict], answers: list[dict]) -> float
"""

from typing import Any

from apted import APTED, Config
from apted.helpers import Tree
from rapidfuzz.distance import Levenshtein

LEAF_MARKER = "<leaf>"
ROOT_LABEL = "<root>"
SUBTREE_LABEL = "<subtree>"


def flatten(data: dict | list) -> list[tuple[str, Any]]:
    """Convert a nested gt_parse tree into a flat list of (dotted_key, value) pairs.

    Ported from ``JSONParseEvaluator.flatten``. Dict keys are joined with '.'; list
    items are flattened under their parent's key without adding a path segment (so
    repeated menu rows all flatten under "menu.nm", "menu.cnt", etc).

    Args:
        data: A (typically already-normalised) dict or list.

    Returns:
        A flat list of (dotted_key, leaf_value) tuples, in traversal order.
    """
    flat: list[tuple[str, Any]] = []

    def _flatten(value: Any, key: str = "") -> None:
        if type(value) is dict:
            for child_key, child_value in value.items():
                _flatten(child_value, f"{key}.{child_key}" if key else child_key)
        elif type(value) is list:
            for item in value:
                _flatten(item, key)
        else:
            flat.append((key, value))

    _flatten(data)
    return flat


def _normalize_dict(data: dict | list | Any) -> dict | list:
    """Sort dict keys and stringify leaves, matching ``JSONParseEvaluator.normalize_dict``.

    This is part of the metric, not incidental formatting: keys are sorted by
    (length, alphabetical) so that dict key order never affects the tree-edit
    distance, and every scalar value is coerced to a stripped string so that "1" and
    1 (int) compare equal, and falsy/blank values are dropped.

    Args:
        data: Any value appearing in a gt_parse tree: dict, list, or scalar.

    Returns:
        A dict (for dict input) or list (for list/scalar input), recursively
        normalised. Falsy input normalises to {}.
    """
    if not data:
        return {}

    if isinstance(data, dict):
        new_dict: dict[str, Any] = {}
        for key in sorted(data.keys(), key=lambda k: (len(k), k)):
            value = _normalize_dict(data[key])
            if value:
                if not isinstance(value, list):
                    value = [value]
                new_dict[key] = value
        return new_dict

    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            new_list: list[Any] = []
            for item in data:
                normalised_item = _normalize_dict(item)
                if normalised_item:
                    new_list.append(normalised_item)
            return new_list
        return [str(item).strip() for item in data if type(item) in {str, int, float} and str(item).strip()]

    return [str(data).strip()]


def _construct_tree(data: dict | list, node_name: str = ROOT_LABEL) -> Tree:
    """Build an apted ``Tree`` from a normalised gt_parse dict/list.

    Ported from ``JSONParseEvaluator.construct_tree_from_dict``, retargeted from
    ``zss.Node`` to ``apted.helpers.Tree`` (both are plain (label, children) nodes;
    only the tree-edit-distance backend that walks them differs).

    Args:
        data: The output of ``_normalize_dict`` — always a dict or a list.
        node_name: This node's label. Defaults to the root sentinel "<root>".

    Returns:
        The root ``Tree`` node of the constructed tree.

    Raises:
        ValueError: If `data` is neither a dict nor a list (should not happen for
            the output of ``_normalize_dict``, which never returns a bare scalar).
    """
    node = Tree(node_name)

    if isinstance(data, dict):
        for key, value in data.items():
            node.children.append(_construct_tree(value, key))
    elif isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            for item in data:
                node.children.append(_construct_tree(item, SUBTREE_LABEL))
        else:
            for item in data:
                node.children.append(Tree(f"{LEAF_MARKER}{item}"))
    else:
        msg = (
            f"What: _construct_tree received a bare scalar ({data!r}) under node "
            f"'{node_name}', but _normalize_dict should only ever produce dict or "
            f"list values.\n"
            f"Where: generators/exporters/cord_eval.py, _construct_tree().\n"
            f"Expected: `data` to be a dict or a list.\n"
            f"Recover: This indicates _normalize_dict was bypassed or modified to "
            f"return a scalar; always route data through _normalize_dict() first."
        )
        raise ValueError(msg) from None

    return node


def _update_cost(label1: str, label2: str) -> int:
    """Rename cost for one node label to another (ports ``update_cost``).

    Both leaves: fuzzy string-edit distance between their unwrapped text (this is
    the one spot where the original used ``nltk.edit_distance``; here it is
    ``rapidfuzz.distance.Levenshtein.distance``, verified to agree with ``nltk`` on
    unit-cost Levenshtein distance).
    One leaf, one internal node: 1 + length of the leaf's text (renaming a leaf into
    a subtree, or vice versa, is expensive and scales with the leaf's content).
    Neither is a leaf: 0 if the labels match exactly, else 1.

    Args:
        label1: The first node's label (may contain the "<leaf>" marker).
        label2: The second node's label (may contain the "<leaf>" marker).

    Returns:
        The non-negative rename cost.
    """
    label1_is_leaf = LEAF_MARKER in label1
    label2_is_leaf = LEAF_MARKER in label2

    if label1_is_leaf and label2_is_leaf:
        return int(Levenshtein.distance(label1.replace(LEAF_MARKER, ""), label2.replace(LEAF_MARKER, "")))
    if not label1_is_leaf and label2_is_leaf:
        return 1 + len(label2.replace(LEAF_MARKER, ""))
    if label1_is_leaf and not label2_is_leaf:
        return 1 + len(label1.replace(LEAF_MARKER, ""))
    return int(label1 != label2)


def _insert_or_remove_cost(label: str) -> int:
    """Insert/delete cost for one node (ports ``insert_and_remove_cost``).

    A leaf costs the length of its unwrapped text (dropping a leaf value into or
    out of the tree is as costly as its content); an internal node costs a flat 1.

    Args:
        label: The node's label (may contain the "<leaf>" marker).

    Returns:
        The non-negative insert/delete cost.
    """
    if LEAF_MARKER in label:
        return len(label.replace(LEAF_MARKER, ""))
    return 1


class _CordTreeEditConfig(Config):  # type: ignore[misc]
    """apted ``Config`` translating the zss cost model above to apted's interface.

    apted's ``Config`` separates delete/insert/rename into three methods rather
    than zss's ``insert_cost``/``remove_cost``/``update_cost`` keyword arguments,
    but the shape of the cost model (per-node insert/delete cost, per-node-pair
    rename cost) is identical, so the translation is direct.
    """

    def children(self, node: Tree) -> list[Tree]:
        """Return a node's children, in order."""
        return node.children  # type: ignore[no-any-return]

    def rename(self, node1: Tree, node2: Tree) -> int:
        """Cost of relabelling `node1` to `node2`'s label."""
        return _update_cost(node1.name, node2.name)

    def delete(self, node: Tree) -> int:
        """Cost of deleting `node` from the source tree."""
        return _insert_or_remove_cost(node.name)

    def insert(self, node: Tree) -> int:
        """Cost of inserting `node` into the destination tree."""
        return _insert_or_remove_cost(node.name)


def _tree_edit_distance(tree1: Tree, tree2: Tree) -> int:
    """Compute the exact tree edit distance between two trees under the CORD cost model.

    Args:
        tree1: The source tree.
        tree2: The destination tree.

    Returns:
        The minimum total cost, over insert/delete/rename operations, to transform
        `tree1` into `tree2`.
    """
    return int(APTED(tree1, tree2, _CordTreeEditConfig()).compute_edit_distance())


def cal_acc(pred: dict, answer: dict) -> float:
    """Calculate normalised-tree-edit-distance (nTED) based accuracy.

    Ports ``JSONParseEvaluator.cal_acc``: build a tree from each dict, compute
    apted's tree edit distance between them, normalise by the edit distance
    between an empty tree and the answer tree, then convert to an accuracy in
    [0, 1] (``max(0, 1 - nTED)``).

    Args:
        pred: The predicted (or candidate) gt_parse tree.
        answer: The reference ("ground truth") gt_parse tree.

    Returns:
        1.0 for an exact match, decreasing towards 0.0 as `pred` diverges
        structurally or in value from `answer`; never negative.

    Raises:
        ValueError: If `answer` normalises to nothing (so the nTED denominator
            would be zero). Diverges intentionally from the original, which
            raises a bare ZeroDivisionError for this case — see the module
            docstring's "ONE DELIBERATE, DOCUMENTED BEHAVIOUR CHANGE" section.
    """
    pred_tree = _construct_tree(_normalize_dict(pred))
    answer_tree = _construct_tree(_normalize_dict(answer))
    empty_tree = _construct_tree(_normalize_dict({}))

    numerator = _tree_edit_distance(pred_tree, answer_tree)
    denominator = _tree_edit_distance(empty_tree, answer_tree)

    if denominator == 0:
        msg = (
            "What: cal_acc's normaliser is degenerate: `answer` normalises to an "
            "empty tree, so the normalised tree edit distance (numerator / "
            "denominator) would divide by zero.\n"
            "Where: generators/exporters/cord_eval.py, cal_acc(), the `answer` "
            "argument.\n"
            "Expected: A non-empty ground-truth gt_parse dict, e.g. "
            "answer={'total': {'total_price': '13.60'}}.\n"
            "Recover: Confirm generators.exporters.cord.to_cord actually emitted "
            "fields for this document before scoring it — an empty tree usually "
            "means the ground-truth record has no present fields."
        )
        raise ValueError(msg) from None

    return max(0.0, 1.0 - (numerator / denominator))


def cal_f1(preds: list[dict], answers: list[dict]) -> float:
    """Calculate micro-averaged, field-level F1 across a list of documents.

    Ports ``JSONParseEvaluator.cal_f1``: flattens each (pred, answer) pair to
    (dotted_key, value) tuples and counts true positives / false positives /
    false negatives by multiset membership (each answer field is consumed at
    most once per pred field it matches).

    Args:
        preds: One gt_parse dict per predicted document.
        answers: One gt_parse dict per reference document, index-aligned with
            `preds`.

    Returns:
        The micro-averaged F1 score across all documents and fields, in [0, 1].

    Raises:
        ValueError: If `preds` and `answers` have different lengths (the
            original silently truncates to the shorter list via `zip`; this is
            exactly the kind of silent-mismatch failure this project's
            fail-fast convention forbids, so it is rejected explicitly here
            instead), or if every document's field list is empty (the F1
            denominator would be zero).
    """
    if len(preds) != len(answers):
        msg = (
            f"What: cal_f1 received {len(preds)} predictions but {len(answers)} "
            f"answers — mismatched lengths.\n"
            f"Where: generators/exporters/cord_eval.py, cal_f1(), the `preds` and "
            f"`answers` arguments.\n"
            f"Expected: `preds` and `answers` to have equal length, index-aligned "
            f"one prediction per reference document.\n"
            f"Recover: Pass one gt_parse dict per document in both lists, in the "
            f"same document order."
        )
        raise ValueError(msg) from None

    total_tp = 0
    total_fn_or_fp = 0
    for pred, answer in zip(preds, answers, strict=True):
        pred_fields = flatten(_normalize_dict(pred))
        answer_fields = flatten(_normalize_dict(answer))
        for field in pred_fields:
            if field in answer_fields:
                total_tp += 1
                answer_fields.remove(field)
            else:
                total_fn_or_fp += 1
        total_fn_or_fp += len(answer_fields)

    denominator = total_tp + total_fn_or_fp / 2
    if denominator == 0:
        msg = (
            "What: cal_f1's denominator (total_tp + total_fn_or_fp / 2) is zero: "
            "every prediction and every answer normalised to no fields at all.\n"
            "Where: generators/exporters/cord_eval.py, cal_f1(), the `preds` and "
            "`answers` arguments.\n"
            "Expected: At least one non-empty gt_parse dict across `preds` or "
            "`answers`.\n"
            "Recover: Confirm generators.exporters.cord.to_cord actually emitted "
            "fields for these documents before scoring them."
        )
        raise ValueError(msg) from None

    return total_tp / denominator
