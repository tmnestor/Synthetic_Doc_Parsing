"""Tests for score_cord (generators.exporters.cord_score).

Wires the previously-unconsumed ``cord_extension_scoring`` config key into
real behaviour: the headline CORD F1 must stay comparable to public CORD
leaderboards (no ``extension`` slot exists there), while the extension
subtree (supplier/ABN/address/date/payer) is either folded back in, scored
separately, or dropped, per the configured mode.
"""

import copy

import pytest

from generators.exporters.cord import to_cord
from generators.exporters.cord_score import score_cord
from tests.conftest import assert_diagnostic_error


def _tree() -> dict:
    """An invented invoice tree with a menu row whose unit price differs from its total."""
    return to_cord(
        {
            "DOCUMENT_TYPE": "INVOICE",
            "SUPPLIER_NAME": "Marlowe Trade Services",
            "BUSINESS_ABN": "55 666 777 888",
            "BUSINESS_ADDRESS": "3 Fixture Rd, Sampleton QLD 4999",
            "INVOICE_DATE": "09/03/2031",
            "GST_AMOUNT": "6.00",
            "TOTAL_AMOUNT": "66.00",
            "LINE_ITEM_DESCRIPTIONS": "Pipe Fitting|Sealant Tube",
            "LINE_ITEM_QUANTITIES": "3|1",
            "LINE_ITEM_PRICES": "12.00|24.00",
            "LINE_ITEM_TOTAL_PRICES": "36.00|24.00",
            "PAYER_NAME": "Wren Aldous",
            "PAYER_ADDRESS": "21 Pretend Pl, Mockford TAS 7999",
        },
        "spaced",
    )


def _cfg(mode: str) -> dict:
    return {"cord_extension_scoring": mode}


def _golds() -> list[dict]:
    return [_tree(), _tree()]


def _corrupt_extension(trees: list[dict]) -> list[dict]:
    out = copy.deepcopy(trees)
    for tree in out:
        tree["extension"]["supplier_name"] = "Wrong Supplier Pty"
        tree["extension"]["invoice_date"] = "01/01/2099"
    return out


def _corrupt_total_price(trees: list[dict]) -> list[dict]:
    out = copy.deepcopy(trees)
    for tree in out:
        tree["menu"][0]["price"] = "999.99"
    return out


def test_in_tree_mode_scores_the_full_tree_and_reports_no_extension_figure() -> None:
    """In in_tree mode a perfect prediction scores 1.0 headline with no extension figure."""
    result = score_cord(copy.deepcopy(_golds()), _golds(), _cfg("in_tree"))
    assert result == {"headline_f1": 1.0, "extension_f1": None, "mode": "in_tree"}


def test_in_tree_mode_is_sensitive_to_an_extension_only_corruption() -> None:
    """Unlike the excluded modes, in_tree is dragged down by an extension-only mutation.

    This proves the mode genuinely changes behaviour rather than the extension
    key being ignored everywhere.
    """
    result = score_cord(_corrupt_extension(_golds()), _golds(), _cfg("in_tree"))
    assert result["headline_f1"] < 1.0


def test_excluded_unscored_mode_drops_extension_from_headline_and_reports_none() -> None:
    """excluded_unscored scores the tree without extension and reports no extension figure."""
    result = score_cord(copy.deepcopy(_golds()), _golds(), _cfg("excluded_unscored"))
    assert result == {"headline_f1": 1.0, "extension_f1": None, "mode": "excluded_unscored"}


def test_excluded_unscored_mode_ignores_an_extension_only_corruption() -> None:
    """Under excluded_unscored an extension-only corruption leaves the headline at 1.0."""
    result = score_cord(_corrupt_extension(_golds()), _golds(), _cfg("excluded_unscored"))
    assert result["headline_f1"] == 1.0
    assert result["extension_f1"] is None


def test_excluded_scored_separately_mode_reports_both_figures_perfectly() -> None:
    """excluded_scored_separately reports a perfect headline and a perfect extension figure."""
    result = score_cord(copy.deepcopy(_golds()), _golds(), _cfg("excluded_scored_separately"))
    assert result == {
        "headline_f1": 1.0,
        "extension_f1": 1.0,
        "mode": "excluded_scored_separately",
    }


def test_excluded_scored_separately_mode_isolates_an_extension_only_corruption() -> None:
    """Corrupting ONLY the extension subtree leaves headline_f1 untouched while extension_f1 drops."""
    result = score_cord(_corrupt_extension(_golds()), _golds(), _cfg("excluded_scored_separately"))
    assert result["headline_f1"] == 1.0
    assert result["extension_f1"] < 1.0


def test_excluded_scored_separately_mode_still_catches_a_non_extension_corruption() -> None:
    """Corrupting a non-extension field (total_price) still drags the headline score down.

    Exclusion removes only `extension`, not sensitivity in general.
    """
    result = score_cord(_corrupt_total_price(_golds()), _golds(), _cfg("excluded_scored_separately"))
    assert result["headline_f1"] < 1.0
    assert result["extension_f1"] == 1.0


def test_inputs_are_never_mutated() -> None:
    """score_cord leaves both the predictions and the references exactly as given."""
    preds = _corrupt_extension(_golds())
    golds = _golds()
    preds_before, golds_before = copy.deepcopy(preds), copy.deepcopy(golds)
    for mode in ("in_tree", "excluded_unscored", "excluded_scored_separately"):
        score_cord(preds, golds, _cfg(mode))
    assert preds == preds_before
    assert golds == golds_before
    assert "extension" in preds[0]


def test_unknown_mode_raises_a_diagnostic_error() -> None:
    """An unrecognised cord_extension_scoring mode fails fast with the four-element diagnostic."""
    with pytest.raises(ValueError) as err:
        score_cord(_golds(), _golds(), _cfg("sideways"))
    assert_diagnostic_error(err.value)
    assert "sideways" in str(err.value)
