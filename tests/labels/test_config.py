"""`label_variation:` loads only when every key is present and coherent (Stage 6, Task 1)."""

from pathlib import Path

import pytest
import yaml

from generators.distractors.config import DistractorConfigError, load_distractor_config
from generators.distractors.paths import DISTRACTORS_PATH
from generators.labels.config import (
    REFUSAL_POLICIES,
    LabelConfigError,
    load_label_config,
    normalise_wording,
)
from generators.labels.keys import LABEL_STYLES
from tests.conftest import assert_diagnostic_error

ROOT = "label_variation"


def _shipped() -> dict:
    return yaml.safe_load(DISTRACTORS_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _refused(tmp_path: Path, data: dict, *tokens: str) -> str:
    path = _write(tmp_path, data)
    with pytest.raises(LabelConfigError) as err:
        load_label_config(path)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    assert f"{path.resolve()} -> {ROOT}" in message, message
    for token in tokens:
        assert token in message, (token, message)
    return message


def test_the_shipped_block_loads_with_the_declared_values() -> None:
    config = load_label_config()
    assert config.doc_types == {
        "receipt": ("BUSINESS_ABN", "GST_AMOUNT", "TOTAL_AMOUNT"),
        "invoice": ("BUSINESS_ABN", "GST_AMOUNT", "TOTAL_AMOUNT"),
        "bank_statement": (),
    }
    assert set(config.weights) == {"BUSINESS_ABN", "GST_AMOUNT", "TOTAL_AMOUNT"}
    assert all(weights.of(style) > 0 for weights in config.weights.values() for style in LABEL_STYLES)
    assert set(config.pools) == {
        "abn_receipt",
        "abn_invoice",
        "gst_receipt",
        "gst_invoice_added",
        "gst_invoice_included",
        "total_receipt",
        "total_invoice",
    }
    assert config.refusal_policy == "redraw"
    assert config.ignore_words == {
        "BUSINESS_ABN": frozenset({"of", "the", "inc", "incl", "no", "number"}),
        "GST_AMOUNT": frozenset({"of", "the", "inc", "incl"}),
        "TOTAL_AMOUNT": frozenset({"of", "the", "inc", "incl"}),
    }
    assert config.withheld == {
        "receipt": {},
        "invoice": {"payment": {"paid": frozenset({"Balance:"})}},
        "bank_statement": {},
    }
    assert config.totals_region_fields == ("GST_AMOUNT", "TOTAL_AMOUNT")


def test_the_shipped_block_is_parsed_once() -> None:
    assert load_label_config() is load_label_config()


def test_the_distractor_catalogue_requires_the_block(tmp_path: Path) -> None:
    data = {k: v for k, v in _shipped().items() if k != ROOT}
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert ROOT in str(err.value)


def test_a_missing_block_is_refused(tmp_path: Path) -> None:
    data = {k: v for k, v in _shipped().items() if k != ROOT}
    _refused(tmp_path, data, "missing", "Recover:  add the")


@pytest.mark.parametrize(
    "key",
    [
        "doc_types",
        "fields",
        "pools",
        "withheld_by_state",
        "collision",
        "refusal_policy",
        "totals_region_fields",
    ],
)
def test_a_missing_sub_key_is_refused(tmp_path: Path, key: str) -> None:
    data = _shipped()
    del data[ROOT][key]
    message = _refused(tmp_path, data, f"{ROOT}.{key}", "missing")
    assert f"add the key under {ROOT}:" in message


def test_an_unknown_sub_key_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["weighting"] = {}
    _refused(tmp_path, data, "weighting")


@pytest.mark.parametrize("value", ["sometimes", None, 1])
def test_a_mistyped_refusal_policy_names_the_allowed_values(tmp_path: Path, value: object) -> None:
    data = _shipped()
    data[ROOT]["refusal_policy"] = value
    message = _refused(tmp_path, data, f"{ROOT}.refusal_policy")
    assert all(policy in message for policy in REFUSAL_POLICIES)


def test_fail_is_an_accepted_policy(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["refusal_policy"] = "fail"
    assert load_label_config(_write(tmp_path, data)).refusal_policy == "fail"


@pytest.mark.parametrize("value", [0, -1, "1", True, None])
def test_a_weight_not_above_zero_is_refused(tmp_path: Path, value: object) -> None:
    data = _shipped()
    data[ROOT]["fields"]["TOTAL_AMOUNT"]["weights"]["unlabelled"] = value
    _refused(tmp_path, data, f"{ROOT}.fields.TOTAL_AMOUNT.weights.unlabelled", "above zero")


def test_a_missing_style_weight_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data[ROOT]["fields"]["GST_AMOUNT"]["weights"]["variant"]
    _refused(tmp_path, data, f"{ROOT}.fields.GST_AMOUNT.weights.variant")


def test_a_varied_field_without_weights_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data[ROOT]["fields"]["BUSINESS_ABN"]
    _refused(tmp_path, data, f"{ROOT}.fields.BUSINESS_ABN")


def test_weights_for_a_field_no_type_varies_are_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["fields"]["PAYER_NAME"] = {"weights": {"labelled": 1, "variant": 1, "unlabelled": 1}}
    _refused(tmp_path, data, "PAYER_NAME")


def test_a_field_not_scored_for_its_type_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["doc_types"]["receipt"] = ["BUSINESS_ABN", "STATEMENT_DATE_RANGE"]
    _refused(tmp_path, data, f"{ROOT}.doc_types.receipt", "STATEMENT_DATE_RANGE")


def test_a_missing_document_type_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data[ROOT]["doc_types"]["bank_statement"]
    _refused(tmp_path, data, f"{ROOT}.doc_types.bank_statement", "missing")


def test_an_unknown_document_type_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["doc_types"]["passport"] = []
    _refused(tmp_path, data, "passport")


@pytest.mark.parametrize("value", [[], None, "AMOUNT"])
def test_an_empty_or_mistyped_pool_is_refused(tmp_path: Path, value: object) -> None:
    data = _shipped()
    data[ROOT]["pools"]["total_receipt"] = value
    _refused(tmp_path, data, f"{ROOT}.pools.total_receipt")


@pytest.mark.parametrize(
    "pair", [["Amount due:", "AMOUNT DUE"], ["GST incl.", "gst  incl"], ["Tax", "tax!"]]
)
def test_a_pool_holding_duplicates_after_normalisation_is_refused(tmp_path: Path, pair: list[str]) -> None:
    data = _shipped()
    data[ROOT]["pools"]["total_invoice"] = pair
    _refused(tmp_path, data, f"{ROOT}.pools.total_invoice", "read alike")


@pytest.mark.parametrize("wording", ["", "  ", "...", 7])
def test_a_wordless_pool_entry_is_refused(tmp_path: Path, wording: object) -> None:
    data = _shipped()
    data[ROOT]["pools"]["total_invoice"] = ["Amount:", wording]
    _refused(tmp_path, data, f"{ROOT}.pools.total_invoice")


@pytest.mark.parametrize("value", [None, "of", ["of"]])
def test_mistyped_ignore_words_are_refused(tmp_path: Path, value: object) -> None:
    data = _shipped()
    data[ROOT]["collision"]["ignore_words"] = value
    _refused(tmp_path, data, f"{ROOT}.collision.ignore_words", "BUSINESS_ABN")


@pytest.mark.parametrize("value", [None, "of", [1], ["..."]])
def test_a_mistyped_field_ignore_list_is_refused(tmp_path: Path, value: object) -> None:
    data = _shipped()
    data[ROOT]["collision"]["ignore_words"]["GST_AMOUNT"] = value
    _refused(tmp_path, data, f"{ROOT}.collision.ignore_words.GST_AMOUNT", "not a list of words")


def test_a_varied_field_without_ignore_words_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data[ROOT]["collision"]["ignore_words"]["TOTAL_AMOUNT"]
    _refused(tmp_path, data, f"{ROOT}.collision.ignore_words.TOTAL_AMOUNT", "missing")


def test_ignore_words_for_a_field_no_type_varies_are_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["collision"]["ignore_words"]["PAYER_NAME"] = ["of"]
    _refused(tmp_path, data, f"{ROOT}.collision.ignore_words", "PAYER_NAME")


def test_an_explicit_empty_ignore_list_is_accepted(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["collision"]["ignore_words"]["GST_AMOUNT"] = []
    assert load_label_config(_write(tmp_path, data)).ignore_words["GST_AMOUNT"] == frozenset()


def test_a_missing_ignore_words_key_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["collision"] = {}
    _refused(tmp_path, data, f"{ROOT}.collision.ignore_words")


@pytest.mark.parametrize(
    ("text", "normal"),
    [
        ("ABN No.", "abn no"),
        ("  Amount   Due: ", "amount due"),
        ("GST (10%):", "gst 10"),
        ("A.B.N.", "abn"),
    ],
)
def test_normalisation_folds_case_punctuation_and_spacing(text: str, normal: str) -> None:
    assert normalise_wording(text) == normal


@pytest.mark.parametrize(
    ("value", "token"),
    [
        ("GST_AMOUNT", "not a list"),
        (["GST_AMOUNT", 3], "not a list"),
        (["GST_AMOUNT", "GST_AMOUNT"], "more than once"),
        (["SUPPLIER_NAME"], "no document type"),
    ],
)
def test_malformed_totals_region_fields_are_refused(tmp_path: Path, value: object, token: str) -> None:
    data = _shipped()
    data[ROOT]["totals_region_fields"] = value
    _refused(tmp_path, data, f"{ROOT}.totals_region_fields", token)


def test_an_empty_totals_region_field_list_is_accepted(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["totals_region_fields"] = []
    assert load_label_config(_write(tmp_path, data)).totals_region_fields == ()


# --- withheld_by_state (a paid invoice never prints 'Balance:' over its total) ----------------------


def test_withheld_wordings_follow_the_drawn_state() -> None:
    config = load_label_config()
    assert config.withheld_for("invoice", {"payment": "paid"}) == frozenset({"Balance:"})
    assert config.withheld_for("invoice", {"payment": "unpaid"}) == frozenset()
    assert config.withheld_for("receipt", {}) == frozenset()


def test_an_explicit_empty_withholding_is_accepted(tmp_path: Path) -> None:
    data = _shipped()
    data[ROOT]["withheld_by_state"]["invoice"] = {}
    assert load_label_config(_write(tmp_path, data)).withheld["invoice"] == {}


@pytest.mark.parametrize(
    ("value", "key", "token"),
    [
        (["Balance:"], "withheld_by_state", "not a mapping"),
        ({"receipt": {}, "invoice": {}}, "withheld_by_state.bank_statement", "missing"),
        (
            {"receipt": {}, "invoice": {}, "bank_statement": {}, "passport": {}},
            "withheld_by_state",
            "passport",
        ),
        (
            {"receipt": [], "invoice": {}, "bank_statement": {}},
            "withheld_by_state.receipt",
            "not a mapping",
        ),
        (
            {"receipt": {}, "invoice": {"delivery": {"paid": ["Balance:"]}}, "bank_statement": {}},
            "withheld_by_state.invoice.delivery",
            "not declared",
        ),
        (
            {"receipt": {"payment": {"paid": ["BALANCE"]}}, "invoice": {}, "bank_statement": {}},
            "withheld_by_state.receipt.payment",
            "not declared",
        ),
        (
            {"receipt": {}, "invoice": {"payment": ["Balance:"]}, "bank_statement": {}},
            "withheld_by_state.invoice.payment",
            "not a mapping",
        ),
        (
            {"receipt": {}, "invoice": {"payment": {"overdue": ["Balance:"]}}, "bank_statement": {}},
            "withheld_by_state.invoice.payment.overdue",
            "not a choice",
        ),
        (
            {"receipt": {}, "invoice": {"payment": {"paid": []}}, "bank_statement": {}},
            "withheld_by_state.invoice.payment.paid",
            "non-empty list",
        ),
        (
            {"receipt": {}, "invoice": {"payment": {"paid": ["Balance"]}}, "bank_statement": {}},
            "withheld_by_state.invoice.payment.paid",
            "no pool",
        ),
        # A receipt pool's wording, withheld on invoices: it is a wording of a pool, but no invoice
        # layout names that pool, so no invoice page would ever print it.
        (
            {"receipt": {}, "invoice": {"payment": {"paid": ["BALANCE"]}}, "bank_statement": {}},
            "withheld_by_state.invoice.payment.paid",
            "no pool a invoice layout names",
        ),
    ],
)
def test_a_malformed_withholding_is_refused(tmp_path: Path, value: object, key: str, token: str) -> None:
    data = _shipped()
    data[ROOT]["withheld_by_state"] = value
    _refused(tmp_path, data, f"{ROOT}.{key}", token)


def test_no_receipt_wording_says_gst_is_included() -> None:
    """A receipt's SUBTOTAL is the total less GST, so GST is an added line there, never an included one."""
    assert not [w for w in load_label_config().pools["gst_receipt"] if "INCL" in w.upper()]
