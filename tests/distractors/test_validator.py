"""Screening a drawn distractor value against the document's scored values."""

import re
import dataclasses
import importlib.util

import pytest

from generators.distractors.config import DISTRACTORS_PATH, Item, ValidatorSettings, load_distractor_config
from generators.distractors.paths import ground_truth_file
from generators.distractors.validator import (
    DistractorValueError,
    check_distractor_values,
    collision,
    scored_values,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

SETTINGS = ValidatorSettings(
    ignore_fields=frozenset({"DOCUMENT_TYPE", "IS_GST_INCLUDED", "LINE_ITEM_QUANTITIES"}),
    min_scored_length=4,
)

# Invented values, checked absent from ground_truth/ below.
FIELDS = {
    "DOCUMENT_TYPE": "INVOICE",
    "SUPPLIER_NAME": "Quillfeather Tiling Studio",
    "BUSINESS_ABN": "12 345 678 901",
    "BUSINESS_ADDRESS": "7 Larkspur Pde, Wombarra NSW 2515",
    "INVOICE_DATE": "14/03/2031",
    "IS_GST_INCLUDED": "true",
    "GST_AMOUNT": "NOT_FOUND",
    "TOTAL_AMOUNT": "1873.42",
    "LINE_ITEM_DESCRIPTIONS": "Grout sealing|Tap",
    "LINE_ITEM_QUANTITIES": "2|1",
    "LINE_ITEM_PRICES": "936.71|0.00",
    "LINE_ITEM_TOTAL_PRICES": "1873.42|0.00",
    "PAYER_NAME": "Odessa Pinchbeck",
    "PAYER_ADDRESS": "NOT_FOUND",
}
INVENTED = [
    "Quillfeather Tiling Studio",
    "12 345 678 901",
    "Larkspur Pde",
    "14/03/2031",
    "1873.42",
    "936.71",
    "Grout sealing",
    "Odessa Pinchbeck",
    "Wattlebird Banking Co",
    "8645.19",
    "0.01",
    "Thornquist",
    "28/03/2031",
    "1873.43",
    "Tiles done right",
    "9046",
]


def _item(kind: str, *, target: str | None = None, field: str | None = None) -> Item:
    return Item(
        id=f"zz_{kind}",
        doc_types=frozenset({"invoice"}),
        kind=kind,
        target=target,
        field=field,
        emits=("DX_ZZ",),
        rule={"type": "fixed", "value": "x"},
        requires=(),
    )


def test_invented_values_are_absent_from_ground_truth() -> None:
    text = "\n".join(p.read_text() for p in (REPO_ROOT / "ground_truth").glob("*.yml"))
    # Whole values only: an invented amount such as 0.01 may sit inside a real one (60.01).
    for value in INVENTED:
        assert not re.search(rf"(?<![\w.]){re.escape(value)}(?![\w])", text), value


def test_scored_values_split_and_filter() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert scored["LINE_ITEM_PRICES"] == ["936.71", "0.00"]
    # short element dropped, long kept
    assert scored["LINE_ITEM_DESCRIPTIONS"] == ["Grout sealing"]
    # ignored fields dropped
    for name in ("DOCUMENT_TYPE", "IS_GST_INCLUDED", "LINE_ITEM_QUANTITIES"):
        assert name not in scored
    # NOT_FOUND dropped
    assert "GST_AMOUNT" not in scored and "PAYER_ADDRESS" not in scored
    assert scored["BUSINESS_ABN"] == ["12 345 678 901"]


def test_scored_values_are_the_document_types_scored_columns_only() -> None:
    fields = {**FIELDS, "ACCOUNT_BALANCE": "4410.27"}
    assert "ACCOUNT_BALANCE" not in scored_values(fields, "invoice", SETTINGS)


def test_bank_descriptions_are_screened_through_their_ground_truth_name() -> None:
    fields = {
        "DOCUMENT_TYPE": "BANK_STATEMENT",
        "STATEMENT_DATE_RANGE": "01/03/2031 - 28/03/2031",
        "TRANSACTION_DATES": "02/03/2031|05/03/2031",
        "TRANSACTION_DESCRIPTIONS": "EFTPOS Wattlebird Banking Co|ATM",
        "TRANSACTION_AMOUNTS_PAID": "61.20|NOT_FOUND",
    }
    scored = scored_values(fields, "bank_statement", SETTINGS)
    assert scored["LINE_ITEM_DESCRIPTIONS"] == ["EFTPOS Wattlebird Banking Co"]
    assert scored["TRANSACTION_AMOUNTS_PAID"] == ["61.20"]


def test_the_bank_alias_agrees_with_the_extraction_ground_truth_script() -> None:
    spec = importlib.util.spec_from_file_location(
        "generate_extraction_gt", REPO_ROOT / "scripts" / "generate_extraction_gt.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from generators.distractors import validator
    from generators.exporters.eval_projection import FIELD_ALIASES

    # The script runs on the standard library and PyYAML alone, so it keeps its own copy.
    assert module._FIELD_ALIASES == FIELD_ALIASES
    assert {v: k for k, v in FIELD_ALIASES.items()} == validator.GROUND_TRUTH_ALIASES


def test_the_script_imports_nothing_from_generators() -> None:
    source = (REPO_ROOT / "scripts" / "generate_extraction_gt.py").read_text()
    assert "from generators" not in source and "import generators" not in source


def test_the_validator_takes_its_aliases_from_the_shared_table() -> None:
    import ast

    tree = ast.parse((REPO_ROOT / "generators" / "distractors" / "validator.py").read_text())
    literal_dicts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "LINE_ITEM_DESCRIPTIONS" for k in node.keys)
    ]
    assert not literal_dicts


def test_unknown_document_type_is_a_diagnostic() -> None:
    with pytest.raises(DistractorValueError) as err:
        scored_values(FIELDS, "payslip", SETTINGS)
    assert_diagnostic_error(err.value)
    assert "payslip" in str(err.value)


# --- collision ----------------------------------------------------------------------------------


def test_near_miss_equal_to_its_target_collides() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert collision(_item("near_miss", target="TOTAL_AMOUNT"), "1873.42", FIELDS, scored) == "TOTAL_AMOUNT"


def test_near_miss_equal_to_an_element_of_a_pipe_list_target_collides() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert (
        collision(_item("near_miss", target="LINE_ITEM_PRICES"), "936.71", FIELDS, scored)
        == "LINE_ITEM_PRICES"
    )


def test_near_miss_containing_another_fields_value_collides() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    item = _item("near_miss", target="TOTAL_AMOUNT")
    assert collision(item, "Paid in full on 14/03/2031", FIELDS, scored) == "INVOICE_DATE"


def test_near_miss_clear_of_every_scored_value_passes() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert collision(_item("near_miss", target="INVOICE_DATE"), "28/03/2031", FIELDS, scored) is None


def test_abn_like_values_compare_without_spaces() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    item = _item("near_miss", target="BUSINESS_ABN")
    assert collision(item, "12345678901", FIELDS, scored) == "BUSINESS_ABN"
    assert collision(_item("clutter"), "Ref 12345678901", FIELDS, scored) == "BUSINESS_ABN"


def test_comparison_ignores_case_and_surrounding_space() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    item = _item("near_miss", target="PAYER_NAME")
    assert collision(item, "  ODESSA pinchbeck ", FIELDS, scored) == "PAYER_NAME"
    assert collision(_item("clutter"), "Ask for odessa PINCHBECK", FIELDS, scored) == "PAYER_NAME"


def test_repeat_must_equal_its_field_exactly() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    item = _item("repeat", field="TOTAL_AMOUNT")
    assert collision(item, "1873.42", FIELDS, scored) is None
    assert collision(item, "1873.43", FIELDS, scored) == "TOTAL_AMOUNT"
    assert collision(item, " 1873.42", FIELDS, scored) == "TOTAL_AMOUNT"


def test_clutter_containing_a_scored_value_collides() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert (
        collision(_item("clutter"), "Thank you, Quillfeather Tiling Studio!", FIELDS, scored)
        == "SUPPLIER_NAME"
    )


def test_ignored_and_short_values_are_not_screened() -> None:
    """A quantity "2" or a "Tap" description inside clutter is allowed."""
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert collision(_item("clutter"), "Tap 2 for help, true story", FIELDS, scored) is None
    assert collision(_item("clutter"), "Store 2", FIELDS, scored) is None


def test_the_shipped_settings_load() -> None:
    settings = load_distractor_config().validator
    assert "LINE_ITEM_QUANTITIES" in settings.ignore_fields
    assert settings.min_scored_length > 0


# --- Ruling 3: a repeat is compared with the raw ground-truth value --------------------------------


def test_repeat_is_checked_against_the_raw_field_not_the_filtered_scored_values() -> None:
    """A repeat of a field the screen ignores (or drops as short) still equals its raw value."""
    settings = ValidatorSettings(ignore_fields=frozenset({"TOTAL_AMOUNT"}), min_scored_length=4)
    scored = scored_values(FIELDS, "invoice", settings)
    assert "TOTAL_AMOUNT" not in scored
    item = _item("repeat", field="TOTAL_AMOUNT")
    assert collision(item, "1873.42", FIELDS, scored) is None
    assert collision(item, "1873.43", FIELDS, scored) == "TOTAL_AMOUNT"


# --- fix 2: near-miss equality uses the unfiltered target -----------------------------------------


def test_near_miss_equal_to_a_short_target_element_collides() -> None:
    """min_scored_length bounds the contains-check only: "0.01" is too short to screen for, but a
    near-miss equal to it still imitates its target exactly."""
    fields = {**FIELDS, "LINE_ITEM_PRICES": "936.71|0.01"}
    settings = ValidatorSettings(ignore_fields=SETTINGS.ignore_fields, min_scored_length=5)
    scored = scored_values(fields, "invoice", settings)
    assert scored["LINE_ITEM_PRICES"] == ["936.71"]
    item = _item("near_miss", target="LINE_ITEM_PRICES")
    assert collision(item, "0.01", fields, scored) == "LINE_ITEM_PRICES"
    assert collision(_item("clutter"), "0.01", fields, scored) is None


def test_near_miss_equal_to_an_ignored_target_collides() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    item = _item("near_miss", target="LINE_ITEM_QUANTITIES")
    assert collision(item, "2", FIELDS, scored) == "LINE_ITEM_QUANTITIES"
    assert collision(item, "3", FIELDS, scored) is None


# --- fix 3: a number never matches inside a longer number -----------------------------------------

AMOUNTS = {**FIELDS, "TOTAL_AMOUNT": "8645.19", "GST_AMOUNT": "0.01"}


@pytest.mark.parametrize("drawn", ["Fee 10.01 applies", "Previous 18645.19", "8645.195", "0.012"])
def test_a_scored_number_inside_a_longer_number_is_not_a_collision(drawn: str) -> None:
    scored = scored_values(AMOUNTS, "invoice", ValidatorSettings(frozenset(), 4))
    assert collision(_item("clutter"), drawn, AMOUNTS, scored) is None


@pytest.mark.parametrize(
    ("drawn", "field"),
    [
        ("Pay 8645.19 now", "TOTAL_AMOUNT"),
        ("$8645.19.", "TOTAL_AMOUNT"),
        ("GST $0.01", "GST_AMOUNT"),
        ("0.01", "GST_AMOUNT"),
    ],
)
def test_a_scored_number_standing_alone_is_a_collision(drawn: str, field: str) -> None:
    scored = scored_values(AMOUNTS, "invoice", ValidatorSettings(frozenset(), 4))
    assert collision(_item("clutter"), drawn, AMOUNTS, scored) == field


# --- fix 4: the colliding field is named by its ground-truth key ----------------------------------

BANK = {
    "DOCUMENT_TYPE": "BANK_STATEMENT",
    "STATEMENT_DATE_RANGE": "01/03/2031 - 28/03/2031",
    "TRANSACTION_DATES": "02/03/2031|05/03/2031",
    "TRANSACTION_DESCRIPTIONS": "EFTPOS Wattlebird Banking Co|ATM",
    "TRANSACTION_AMOUNTS_PAID": "61.20|NOT_FOUND",
}


def test_a_bank_description_collision_names_the_ground_truth_key() -> None:
    scored = scored_values(BANK, "bank_statement", SETTINGS)
    found = collision(_item("clutter"), "Ask about eftpos wattlebird banking co", BANK, scored)
    assert found == "TRANSACTION_DESCRIPTIONS"


# --- check_distractor_values ----------------------------------------------------------------------


def _check(values: dict[str, str], fields: dict | None = None) -> None:
    check_distractor_values(
        values, FIELDS if fields is None else fields, "invoice", load_distractor_config(), case_id="CASE999"
    )


def _where(err: pytest.ExceptionInfo[DistractorValueError]) -> str:
    return next(line for line in str(err.value).splitlines() if line.strip().startswith("Where:"))


@pytest.mark.parametrize(
    ("key", "value", "item_id", "field"),
    [
        ("DX_DUE_DATE", "14/03/2031", "in_due_date", "INVOICE_DATE"),
        ("DX_PREVIOUS_BALANCE", "936.71", "in_previous_balance", "LINE_ITEM_PRICES"),
        ("DX_AMOUNT_PAID", "1873.43", "in_amount_paid", "TOTAL_AMOUNT"),
        ("DX_TAGLINE", "Proudly Quillfeather Tiling Studio", "in_tagline", "SUPPLIER_NAME"),
    ],
    ids=["near_miss_equal", "near_miss_contains", "repeat_drifted", "clutter_contains"],
)
def test_a_colliding_value_is_a_four_part_error(key: str, value: str, item_id: str, field: str) -> None:
    with pytest.raises(DistractorValueError) as err:
        _check({key: value})
    assert_diagnostic_error(err.value)
    message = str(err.value)
    for token in ("CASE999", item_id, key, value, field):
        assert token in message, token
    where = _where(err)
    assert f"{ground_truth_file('invoice')} -> CASE999.fields.{field}" in where
    assert f"{DISTRACTORS_PATH} -> items.{item_id}" in where


def test_clean_values_pass() -> None:
    _check({"DX_DUE_DATE": "28/03/2031", "DX_AMOUNT_PAID": "1873.42", "DX_TAGLINE": "Tiles done right"})


def test_a_bank_collision_names_the_ground_truth_key_in_where() -> None:
    with pytest.raises(DistractorValueError) as err:
        check_distractor_values(
            {"DX_PROMO_MESSAGE": "Thanks for using EFTPOS Wattlebird Banking Co"},
            BANK,
            "bank_statement",
            load_distractor_config(),
            case_id="CASE999",
        )
    assert_diagnostic_error(err.value)
    where = _where(err)
    assert "CASE999.fields.TRANSACTION_DESCRIPTIONS" in where
    assert "LINE_ITEM_DESCRIPTIONS" not in where


@pytest.mark.parametrize("key", ["DX_NOPE", "DX_STORE_NUMBER"])
def test_a_key_no_item_of_the_type_emits_is_a_four_part_error(key: str) -> None:
    with pytest.raises(DistractorValueError) as err:
        _check({key: "Thornquist"})
    assert_diagnostic_error(err.value)
    assert key in str(err.value) and "invoice" in str(err.value)


def test_the_check_uses_the_catalogue_validator_settings() -> None:
    """A config whose min_scored_length screens out "936.71" lets the contains-check pass it."""
    config = load_distractor_config()
    lenient = dataclasses.replace(
        config, validator=ValidatorSettings(config.validator.ignore_fields, min_scored_length=7)
    )
    check_distractor_values({"DX_TAGLINE": "Only 936.71"}, FIELDS, "invoice", lenient, case_id="CASE999")
    with pytest.raises(DistractorValueError):
        _check({"DX_TAGLINE": "Only 936.71"})


# --- fix round 1: boundaries at real token edges --------------------------------------------------

EDGES = {**BANK, "BUSINESS_ABN": "12 345 678 901"}
EDGE_SETTINGS = ValidatorSettings(frozenset({"DOCUMENT_TYPE"}), 4)


def _edge_hits(drawn: str) -> str | None:
    scored = scored_values(EDGES, "bank_statement", EDGE_SETTINGS)
    scored["BUSINESS_ABN"] = ["12 345 678 901"]
    return collision(_item("clutter"), drawn, EDGES, scored)


@pytest.mark.parametrize(
    ("drawn", "field"),
    [
        ("02/03/2031 61.20", "TRANSACTION_DATES"),
        ("Paid 61.20 2 items", "TRANSACTION_AMOUNTS_PAID"),
        ("Note 3 61.20", "TRANSACTION_AMOUNTS_PAID"),
        ("61.20 3", "TRANSACTION_AMOUNTS_PAID"),
        ("ABN 12 345 678 901 0", "BUSINESS_ABN"),
        ("ACN 9 12 345 678 901", "BUSINESS_ABN"),
        ("No.12 345 678 901", "BUSINESS_ABN"),
        ("Ref 12345678901", "BUSINESS_ABN"),
    ],
)
def test_a_number_beside_another_separate_number_is_still_caught(drawn: str, field: str) -> None:
    assert _edge_hits(drawn) == field


def test_both_numbers_in_a_date_amount_pair_are_caught() -> None:
    scored = scored_values(EDGES, "bank_statement", EDGE_SETTINGS)
    assert collision(_item("clutter"), "02/03/2031 61.20", EDGES, {"A": scored["TRANSACTION_AMOUNTS_PAID"]})
    assert collision(_item("clutter"), "02/03/2031 61.20", EDGES, {"D": scored["TRANSACTION_DATES"]})


@pytest.mark.parametrize("drawn", ["Ref 912345678901", "12.00", "245.50"])
def test_a_contiguous_longer_number_is_still_not_a_match(drawn: str) -> None:
    fields = {**EDGES, "TRANSACTION_AMOUNTS_PAID": "2.00|45.50"}
    scored = scored_values(fields, "bank_statement", EDGE_SETTINGS)
    scored["BUSINESS_ABN"] = ["12345678901"]
    assert collision(_item("clutter"), drawn, fields, scored) is None


def test_the_spaced_form_matches_an_unspaced_scored_value() -> None:
    scored = {"BUSINESS_ABN": ["12345678901"]}
    assert collision(_item("clutter"), "ABN 9 12 345 678 901", EDGES, scored) == "BUSINESS_ABN"


def test_letter_needles_are_unchanged() -> None:
    scored = scored_values(FIELDS, "invoice", SETTINGS)
    assert collision(_item("clutter"), "Ask for odessa  PINCHBECK", FIELDS, scored) == "PAYER_NAME"
    assert collision(_item("clutter"), "Odessa Pinch", FIELDS, scored) is None
