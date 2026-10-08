"""The distractor catalogue loads only when every key is present and coherent."""

from pathlib import Path

import pytest
import yaml

from generators.distractors.config import DISTRACTORS_PATH, DistractorConfigError, load_distractor_config
from tests.conftest import assert_diagnostic_error


def _shipped() -> dict:
    return yaml.safe_load(DISTRACTORS_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_the_shipped_catalogue_loads() -> None:
    config = load_distractor_config()
    assert {"receipt", "invoice", "bank_statement"} == set(config.selection)
    assert all(
        item.emits and all(key.startswith("DX_") for key in item.emits) for item in config.items.values()
    )


@pytest.mark.parametrize("key", ["selection", "max_redraws", "validator", "items", "density"])
def test_a_missing_top_level_key_is_a_diagnostic(tmp_path: Path, key: str) -> None:
    data = {k: v for k, v in _shipped().items() if k != key}
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert key in str(err.value)


@pytest.mark.parametrize(
    ("item_patch", "token"),
    [
        ({"kind": "decoy"}, "kind"),
        ({"kind": "near_miss", "target": None}, "target"),
        ({"kind": "near_miss", "target": "NOT_A_FIELD"}, "NOT_A_FIELD"),
        ({"emits": ["STORE"]}, "DX_"),
        ({"requires": ["rc_missing"]}, "rc_missing"),
        ({"rule": {"type": "telepathy"}}, "telepathy"),
        ({"doc_types": ["passport"]}, "passport"),
    ],
)
def test_an_incoherent_item_is_a_diagnostic(tmp_path: Path, item_patch: dict, token: str) -> None:
    data = _shipped()
    data["items"]["rc_store_number"].update(item_patch)
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert token in str(err.value)


def test_a_selection_range_must_be_ordered(tmp_path: Path) -> None:
    data = _shipped()
    data["selection"]["receipt"]["count"] = [6, 3]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)


def test_two_items_may_not_emit_the_same_key_for_one_document_type(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_merchant_id"]["emits"] = ["DX_STORE_NUMBER"]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)


def test_a_requirement_must_share_a_document_type(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_store_number"]["requires"] = ["in_po_number"]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert "in_po_number" in str(err.value)


def test_a_validator_ignore_field_must_be_a_scored_column(tmp_path: Path) -> None:
    data = _shipped()
    data["validator"]["ignore_fields"] = ["NOT_A_COLUMN"]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert "NOT_A_COLUMN" in str(err.value)


def test_a_repeat_item_needs_a_field(tmp_path: Path) -> None:
    data = _shipped()
    del data["items"]["in_amount_paid"]["field"]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert "field" in str(err.value)


def test_a_non_positive_redraw_budget_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["max_redraws"] = 0
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert "max_redraws" in str(err.value)


def _refused(tmp_path: Path, data: dict, token: str) -> None:
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    assert token in str(err.value)


def test_an_unknown_item_key_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_store_number"]["colour"] = "red"
    _refused(tmp_path, data, "colour")


def test_a_target_on_a_clutter_item_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_store_number"]["target"] = "TOTAL_AMOUNT"
    _refused(tmp_path, data, "target")


def test_a_field_on_a_non_repeat_item_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_acn"]["field"] = "TOTAL_AMOUNT"
    _refused(tmp_path, data, "field")


def test_an_item_requiring_itself_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_store_number"]["requires"] = ["rc_store_number"]
    _refused(tmp_path, data, "requires itself")


def test_a_transitive_requires_cycle_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_member_number"]["requires"] = ["rc_points_balance"]
    _refused(tmp_path, data, "requires itself")


def test_the_shipped_catalogue_is_parsed_once() -> None:
    assert load_distractor_config() is load_distractor_config()


# --- rule parameters, checked at load time ------------------------------------------------------


_FRACTION = {
    "type": "amount_fraction",
    "of": "TOTAL_AMOUNT",
    "min_fraction": "0.03",
    "max_fraction": "0.25",
}


@pytest.mark.parametrize(
    ("item_id", "rule", "key", "token"),
    [
        ("rc_store_number", {"type": "digits", "prefix": "Store "}, "length", "missing"),
        (
            "rc_store_number",
            {"type": "digits", "length": "4", "prefix": "Store "},
            "length",
            "positive whole",
        ),
        (
            "rc_store_number",
            {"type": "digits", "length": 0, "prefix": "Store "},
            "length",
            "positive whole",
        ),
        ("rc_store_number", {"type": "digits", "length": 4}, "prefix", "missing"),
        ("rc_store_number", {"type": "digits", "length": 4, "prefix": 7}, "prefix", "string"),
        ("rc_phone", {"type": "phone", "kind": "satellite"}, "kind", "freecall"),
        ("rc_phone", {"type": "phone"}, "kind", "missing"),
        ("rc_returns_policy", {"type": "pool_text", "pool": "no_such_pool"}, "pool", "distractor_text"),
        ("in_email", {"type": "email", "pool": "no_such_pool"}, "pool", "distractor_text"),
        ("in_promo_price", {"type": "amount", "min": "25.00", "max": "1.00"}, "max", "not below min"),
        ("in_promo_price", {"type": "amount", "min": "1.005", "max": "25.00"}, "min", "two places"),
        ("in_promo_price", {"type": "amount", "min": "-1.00", "max": "25.00"}, "min", "non-negative"),
        ("in_promo_price", {"type": "amount", "min": "1.00"}, "max", "missing"),
        ("rc_you_saved", {**_FRACTION, "of": "TOTAL"}, "of", "scored column"),
        ("rc_you_saved", {**_FRACTION, "of": 7}, "of", "scored column"),
        ("rc_you_saved", {k: v for k, v in _FRACTION.items() if k != "of"}, "of", "missing"),
        ("rc_you_saved", {**_FRACTION, "min_fraction": "0"}, "min_fraction", "strictly between 0 and 1"),
        ("rc_you_saved", {**_FRACTION, "min_fraction": 0.03}, "min_fraction", "decimal string"),
        ("rc_you_saved", {**_FRACTION, "max_fraction": "1"}, "max_fraction", "strictly between 0 and 1"),
        ("rc_you_saved", {**_FRACTION, "max_fraction": "1.25"}, "max_fraction", "strictly between 0 and 1"),
        ("rc_you_saved", {**_FRACTION, "max_fraction": "0.02"}, "max_fraction", "not below min_fraction"),
        ("rc_you_saved", {**_FRACTION, "min_fraction": "abc"}, "min_fraction", "decimal string"),
        ("rc_returns_by", {"type": "date_offset", "from": "INVOICE_DATE", "days": []}, "days", "non-empty"),
        ("rc_returns_by", {"type": "date_offset", "from": "INVOICE_DATE", "days": 14}, "days", "non-empty"),
        (
            "rc_returns_by",
            {"type": "date_offset", "from": "INVOICE_DATE", "days": [7, "x"]},
            "days",
            "whole",
        ),
        ("rc_returns_by", {"type": "date_offset", "days": [7]}, "from", "missing"),
        ("bk_issued_date", {"type": "range_end_offset", "days": [True]}, "days", "whole"),
        ("bk_annotated_rows", {"type": "annotated_rows", "extra": [3, 1]}, "extra", "min <= max"),
        ("in_balance_due", {"type": "fixed", "value": ""}, "value", "string to print"),
    ],
)
def test_a_missing_or_mistyped_rule_parameter_is_refused(
    tmp_path: Path, item_id: str, rule: dict, key: str, token: str
) -> None:
    data = _shipped()
    data["items"][item_id]["rule"] = rule
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert f"items.{item_id}.rule.{key}" in message and token in message, message


def test_a_rule_parameter_nothing_reads_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["rc_website"]["rule"] = {"type": "website", "domain": "com.au"}
    _refused(tmp_path, data, "domain")


def test_a_bad_pool_names_the_data_pools_file(tmp_path: Path) -> None:
    from generators.distractors.paths import DATA_POOLS_PATH

    data = _shipped()
    data["items"]["in_tagline"]["rule"] = {"type": "pool_text", "pool": "taglines"}
    _refused(tmp_path, data, str(DATA_POOLS_PATH))


def test_every_shipped_rule_declares_its_parameters() -> None:
    from generators.distractors.values import RULE_PARAMS, RULE_TYPES

    assert set(RULE_PARAMS) == RULE_TYPES
    for item in load_distractor_config().items.values():
        assert set(item.rule) - {"type"} == set(RULE_PARAMS[item.rule["type"]]), item.id


def _chained() -> object:
    """The real catalogue with rc_survey_code made to require rc_member_number: url -> code -> member."""
    import dataclasses

    config = load_distractor_config()
    items = dict(config.items)
    items["rc_survey_code"] = dataclasses.replace(items["rc_survey_code"], requires=("rc_member_number",))
    return dataclasses.replace(config, items=items)


def test_required_items_is_transitive() -> None:
    from generators.distractors.config import required_items

    config = _chained()
    needed = required_items(config, "rc_survey_url")  # type: ignore[arg-type]
    assert needed == {"rc_survey_code", "rc_member_number"}
    assert required_items(load_distractor_config(), "rc_survey_url") == {"rc_survey_code"}
    assert required_items(load_distractor_config(), "rc_store_number") == set()


@pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")
def test_a_gate_covers_keys_its_item_requires_transitively(monkeypatch: pytest.MonkeyPatch) -> None:
    """A survey QR gated on the URL may also draw the member number once the code requires it."""
    from generators.layout_dsl import schema
    from tests.layout_dsl.test_distractor_items import SURVEY, _survey_qr, dx_layout
    from tests.layout_dsl.test_schema import check_layout

    block = _survey_qr("DX_SURVEY_URL", extra=" {DX_MEMBER_NUMBER}")
    layout = dx_layout([block], [*SURVEY, "rc_member_number"])
    with pytest.raises(schema.LayoutSchemaError):
        check_layout(layout)
    monkeypatch.setattr(schema, "load_distractor_config", _chained)
    check_layout(layout)


# --- density bounds -------------------------------------------------------------


def test_the_shipped_density_bounds_load_for_every_document_type() -> None:
    density = load_distractor_config().density
    assert set(density) == {"receipt", "invoice", "bank_statement"}
    # Final invoice bounds, measured with 14 to 22 items over all eight layouts (7 October 2026);
    # config/distractors.yml records the measurement and the spec's starting 0.25 and 0.75 beside them.
    assert density["invoice"].scored_ink_ratio_max == 0.48
    assert density["invoice"].distractor_share_min == 0.40
    assert density["invoice"].page_fill_min == 0.33
    assert density["receipt"].page_fill_min is None
    for doc_type in ("invoice", "bank_statement"):
        fill = density[doc_type].page_fill_min
        assert fill is not None and 0.0 <= fill <= 1.0, doc_type
    for bounds in density.values():
        assert 0.0 <= bounds.scored_ink_ratio_max <= 1.0
        assert 0.0 <= bounds.distractor_share_min <= 1.0


@pytest.mark.parametrize(
    ("patch", "token"),
    [
        ({"scored_ink_ratio_max": 1.5}, "density.receipt.scored_ink_ratio_max"),
        ({"scored_ink_ratio_max": -0.1}, "density.receipt.scored_ink_ratio_max"),
        ({"scored_ink_ratio_max": None}, "density.receipt.scored_ink_ratio_max"),
        ({"distractor_share_min": 1.5}, "density.receipt.distractor_share_min"),
        ({"distractor_share_min": "some"}, "density.receipt.distractor_share_min"),
        ({"distractor_share_min": None}, "density.receipt.distractor_share_min"),
        ({"page_fill_min": "high"}, "density.receipt.page_fill_min"),
        ({"page_fill_min": True}, "density.receipt.page_fill_min"),
        ({"page_fill_min": 1.2}, "density.receipt.page_fill_min"),
        ({"crowding": 0.5}, "crowding"),
    ],
)
def test_an_invalid_density_bound_is_refused(tmp_path: Path, patch: dict, token: str) -> None:
    data = _shipped()
    data["density"]["receipt"].update(patch)
    _refused(tmp_path, data, token)


def test_an_explicit_null_page_fill_means_unbounded(tmp_path: Path) -> None:
    data = _shipped()
    data["density"]["invoice"]["page_fill_min"] = None
    assert load_distractor_config(_write(tmp_path, data)).density["invoice"].page_fill_min is None


@pytest.mark.parametrize("doc_type", ["invoice", "receipt"])
@pytest.mark.parametrize("key", ["scored_ink_ratio_max", "distractor_share_min", "page_fill_min"])
def test_a_missing_density_bound_is_refused(tmp_path: Path, key: str, doc_type: str) -> None:
    """A missing key fails even where an explicit null is allowed (the receipt's page_fill_min)."""
    data = _shipped()
    del data["density"][doc_type][key]
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(_write(tmp_path, data))
    assert_diagnostic_error(err.value)
    message = str(err.value)
    assert f"density.{doc_type}.{key}" in message and "missing" in message, message


def test_a_document_type_without_density_bounds_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data["density"]["bank_statement"]
    _refused(tmp_path, data, "bank_statement")


def test_density_bounds_for_an_unknown_document_type_are_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["density"]["passport"] = {
        "scored_ink_ratio_max": 0.5,
        "distractor_share_min": 0.1,
        "page_fill_min": 0.5,
    }
    _refused(tmp_path, data, "density.passport")


def test_density_must_be_a_mapping(tmp_path: Path) -> None:
    data = _shipped()
    data["density"] = [0.25, 0.75]
    _refused(tmp_path, data, "density")
