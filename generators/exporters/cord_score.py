"""Score CORD gt_parse trees per config/export_config.yml's cord_extension_scoring.

Implements spec section 4.3. CORD has no labelled slot for supplier, ABN,
address, invoice date or payer — this project carries those under an
``extension`` subtree (see generators/exporters/cord.py). Scoring the whole
tree, extension included, would make the headline number incomparable to
public CORD leaderboards, which have never seen that key. This module reads
the ``cord_extension_scoring`` config key and decides whether ``extension``
counts toward the headline score, is excluded but scored on the side, or is
excluded and dropped entirely.
"""

import copy
from typing import Any

from generators.exporters.cord_eval import cal_f1

CORD_EXTENSION_SCORING_MODES: tuple[str, ...] = (
    "in_tree",
    "excluded_scored_separately",
    "excluded_unscored",
)

EXTENSION_KEY = "extension"


def _validate_mode(mode: str) -> None:
    """Validate a cord_extension_scoring value against the three allowed modes.

    Args:
        mode: The value of export_config['cord_extension_scoring'].

    Raises:
        ValueError: If mode is not one of CORD_EXTENSION_SCORING_MODES.
    """
    if mode not in CORD_EXTENSION_SCORING_MODES:
        msg = (
            f"What: Unknown cord_extension_scoring mode '{mode}'.\n"
            f"Where: cord_extension_scoring in config/export_config.yml.\n"
            f"Expected: One of {list(CORD_EXTENSION_SCORING_MODES)}.\n"
            f"Recover: Set cord_extension_scoring to one of the allowed values "
            f"in config/export_config.yml, e.g. "
            f"'cord_extension_scoring: excluded_scored_separately'."
        )
        raise ValueError(msg) from None


def _without_extension(tree: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of a gt_parse tree with its extension subtree removed.

    Args:
        tree: A single document's gt_parse tree. Never mutated.

    Returns:
        A deep copy of tree with the top-level 'extension' key absent.
    """
    pruned = copy.deepcopy(tree)
    pruned.pop(EXTENSION_KEY, None)
    return pruned


def _extension_only(tree: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of just a gt_parse tree's extension subtree.

    The extension-only tree keeps the wrapping ``{"extension": ...}`` shape
    (rather than the bare inner dict) so it round-trips through
    ``cal_f1``/``flatten`` with dotted keys like ``extension.supplier_name``,
    exactly as they appear when scored in_tree.

    Args:
        tree: A single document's gt_parse tree. Never mutated.

    Returns:
        ``{"extension": <deep copy of tree["extension"]>}``, or ``{}`` if
        tree carries no extension subtree.
    """
    extension = tree.get(EXTENSION_KEY)
    if extension is None:
        return {}
    return {EXTENSION_KEY: copy.deepcopy(extension)}


def score_cord(preds: list[dict], golds: list[dict], export_config: dict) -> dict:
    """Score CORD gt_parse trees per the configured extension-scoring mode.

    Args:
        preds: One gt_parse dict per predicted document.
        golds: One gt_parse dict per reference document, index-aligned with
            `preds`.
        export_config: The validated export config mapping (as returned by
            generators.exporters.config.load_export_config). Reads
            'cord_extension_scoring'.

    Returns:
        A dict with:
            - 'headline_f1': the micro-averaged F1 that stays comparable to
              public CORD leaderboards. Computed over the full tree in
              'in_tree' mode; over the tree with 'extension' removed in the
              two 'excluded_*' modes.
            - 'extension_f1': the micro-averaged F1 over just the extension
              subtrees, populated only in 'excluded_scored_separately' mode
              (None otherwise).
            - 'mode': the cord_extension_scoring value that was applied.

    Raises:
        ValueError: If export_config['cord_extension_scoring'] is not one of
            'in_tree', 'excluded_scored_separately', 'excluded_unscored'.
    """
    mode = export_config["cord_extension_scoring"]
    _validate_mode(mode)

    if mode == "in_tree":
        headline_f1 = cal_f1(preds, golds)
        return {"headline_f1": headline_f1, "extension_f1": None, "mode": mode}

    pruned_preds = [_without_extension(tree) for tree in preds]
    pruned_golds = [_without_extension(tree) for tree in golds]
    headline_f1 = cal_f1(pruned_preds, pruned_golds)

    if mode == "excluded_unscored":
        return {"headline_f1": headline_f1, "extension_f1": None, "mode": mode}

    # mode == "excluded_scored_separately"
    extension_preds = [_extension_only(tree) for tree in preds]
    extension_golds = [_extension_only(tree) for tree in golds]
    extension_f1 = cal_f1(extension_preds, extension_golds)
    return {"headline_f1": headline_f1, "extension_f1": extension_f1, "mode": mode}
