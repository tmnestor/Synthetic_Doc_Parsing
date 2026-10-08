"""The layout schema refuses a malformed label site or totals region.

Stage 6, Task 1, Decisions 3, 9, 16 and 17.
"""

import copy
from collections.abc import Iterator

import pytest

from generators.distractors.paths import REPO_ROOT
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.pipeline import field_names_for
from tests.conftest import assert_diagnostic_error
from tests.renderer_helpers import layout_path_for, layouts_for

RECEIPT = "receipt_thermal_57mm"
INVOICE = "tax_invoice_standard"
INCLUSIVE = "tax_invoice_gst_inclusive"
BANK = "cba_standard"
PIPELINE_TYPE = {"receipt": "receipts", "invoice": "invoices", "bank_statement": "bank_statements"}


def _layout(doc_type: str, layout_id: str) -> dict:
    return copy.deepcopy(layouts_for(doc_type)[layout_id])


def _validate(doc_type: str, layout_id: str, layout: dict) -> None:
    validate_layout(
        layout,
        layout_id=layout_id,
        layout_path=layout_path_for(doc_type),
        known_fields=set(field_names_for(PIPELINE_TYPE[doc_type])),
        doc_type=PIPELINE_TYPE[doc_type],
    )


def _refused(doc_type: str, layout_id: str, layout: dict, where: str, *tokens: str) -> str:
    with pytest.raises(LayoutSchemaError) as err:
        _validate(doc_type, layout_id, layout)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    absolute = REPO_ROOT / layout_path_for(doc_type)
    assert f"{absolute} -> {where}" in message, message
    for token in tokens:
        assert token in message, (token, message)
    return message


def _blocks(node: object) -> Iterator[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _blocks(item)
    elif isinstance(node, dict):
        yield node
        yield from _blocks(node.get("children"))


def _block(layout: dict, **match: object) -> dict:
    found = [b for b in _blocks(layout["body"]) if all(b.get(k) == v for k, v in match.items())]
    assert len(found) == 1, (match, len(found))
    return found[0]


def _sites(layout: dict) -> dict:
    return next(s for s in layout["field_providers"] if s["name"] == "label_styles")["params"]["sites"]


@pytest.mark.parametrize(
    ("doc_type", "layout_id"),
    [("receipt", lid) for lid in layouts_for("receipt")]
    + [("invoice", lid) for lid in layouts_for("invoice")]
    + [("bank_statement", lid) for lid in layouts_for("bank_statement")],
)
def test_every_shipped_layout_passes(doc_type: str, layout_id: str) -> None:
    _validate(doc_type, layout_id, _layout(doc_type, layout_id))


def test_a_receipt_layout_without_label_styles_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    layout["field_providers"] = [s for s in layout["field_providers"] if s["name"] != "label_styles"]
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.field_providers", "label_styles")


def test_an_invoice_layout_with_two_label_styles_is_refused() -> None:
    layout = _layout("invoice", INVOICE)
    spec = next(s for s in layout["field_providers"] if s["name"] == "label_styles")
    layout["field_providers"].append(copy.deepcopy(spec))
    # The registry's own emit-collision check refuses the second entry first.
    with pytest.raises(LayoutSchemaError) as err:
        _validate("invoice", INVOICE, layout)
    assert_diagnostic_error(err.value)
    assert "LABEL_TOTAL_AMOUNT" in str(err.value) and f"{INVOICE}.field_providers[3]" in str(err.value)


def test_a_bank_layout_with_label_styles_is_refused() -> None:
    layout = _layout("bank_statement", BANK)
    layout["field_providers"].append({"name": "label_styles", "params": {"sites": {}}})
    _refused("bank_statement", BANK, layout, f"{BANK}.field_providers", "label_styles", "bank_statement")


def test_sites_not_matching_the_varied_fields_are_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    del _sites(layout)["GST_AMOUNT"]
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.field_providers[4].params.sites", "GST_AMOUNT")


def test_a_placeholder_with_no_site_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    layout["body"][0]["children"].insert(0, {"type": "text", "content": "{LABEL_PAYER_NAME}{PAYER_NAME}"})
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body[0].children[0].content", "LABEL_PAYER_NAME")


def test_a_site_with_no_placeholder_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _block(layout, field="GST_AMOUNT")["label"] = "GST"
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "GST_AMOUNT", "never be drawn")


def test_a_placeholder_in_a_block_bound_to_another_field_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _block(layout, field="GST_AMOUNT")["label"] = "{LABEL_TOTAL_AMOUNT}"
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "not bound field: TOTAL_AMOUNT")


def test_a_placeholder_in_a_value_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _block(layout, field="GST_AMOUNT")["value"] = "{LABEL_GST_AMOUNT}{GST_AMOUNT}"
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "in 'value'")


def test_a_second_block_for_one_site_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    stack = _block(layout, type="stack", region="totals")
    stack["children"].append(copy.deepcopy(_block(layout, field="TOTAL_AMOUNT")))
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "second block")


def test_an_unknown_pool_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _sites(layout)["TOTAL_AMOUNT"]["pool"] = "total_shouted"
    _refused(
        "receipt",
        RECEIPT,
        layout,
        f"{RECEIPT}.field_providers[4].params.sites.TOTAL_AMOUNT.pool",
        "total_shouted",
    )


def test_a_pool_holding_the_sites_own_wording_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _sites(layout)["GST_AMOUNT"]["labelled"] = "Tax"
    _refused(
        "receipt",
        RECEIPT,
        layout,
        f"{RECEIPT}.field_providers[4].params.sites.GST_AMOUNT.pool",
        "'TAX'",
        "'tax'",
    )


@pytest.mark.parametrize("spec", [{"labelled": "", "pool": "gst_receipt"}, {"labelled": "GST"}, "GST"])
def test_a_malformed_site_is_refused(spec: object) -> None:
    layout = _layout("receipt", RECEIPT)
    _sites(layout)["GST_AMOUNT"] = spec
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.field_providers[4].params.sites.GST_AMOUNT")


@pytest.mark.parametrize(("key", "value"), [("value_align", "left"), ("separator", ":")])
def test_a_pair_site_must_right_align_with_an_empty_separator(key: str, value: str) -> None:
    layout = _layout("invoice", INVOICE)
    _block(layout, field="TOTAL_AMOUNT")[key] = value
    _refused("invoice", INVOICE, layout, f"{INVOICE}.body", "TOTAL_AMOUNT", repr(value))


def test_a_pair_site_inheriting_a_separator_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    del _block(layout, field="GST_AMOUNT")["separator"]  # the receipt default is ": "
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "': '")


def test_a_text_site_wording_not_ending_in_whitespace_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _sites(layout)["BUSINESS_ABN"]["labelled"] = "ABN:"
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "'ABN:'", "whitespace")


def test_a_text_site_wording_that_would_shrink_its_value_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    wording = "AUSTRALIAN BUSINESS NUMBER "
    _sites(layout)["BUSINESS_ABN"]["labelled"] = wording
    message = _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", repr(wording), "px wide", "ABN_LINE")
    budget = layout["field_budgets"]["ABN_LINE"]["width"]
    assert f"ABN_LINE of {budget}px" in message


def test_a_text_site_without_a_budget_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    del _block(layout, field="BUSINESS_ABN")["budget"]
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "budget")


def test_a_region_on_anything_but_a_stack_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    _block(layout, field="TOTAL_AMOUNT")["region"] = "totals"
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "only a stack")


def test_an_unknown_region_name_is_refused() -> None:
    layout = _layout("invoice", INVOICE)
    _block(layout, type="stack", region="totals")["region"] = "subtotals"
    _refused("invoice", INVOICE, layout, f"{INVOICE}.body", "'subtotals'")


def test_two_totals_regions_are_refused() -> None:
    layout = _layout("invoice", INVOICE)
    layout["body"].insert(0, {"type": "stack", "region": "totals", "children": []})
    _refused("invoice", INVOICE, layout, f"{INVOICE}.body", "declared twice", f"{INVOICE}.body[0]")


def test_a_layout_with_sites_and_no_totals_region_is_refused() -> None:
    layout = _layout("invoice", INCLUSIVE)
    del _block(layout, type="stack", region="totals")["region"]
    _refused("invoice", INCLUSIVE, layout, f"{INCLUSIVE}.body", "no totals region")


def test_a_gst_site_outside_the_totals_region_is_refused() -> None:
    layout = _layout("receipt", RECEIPT)
    stack = _block(layout, type="stack", region="totals")
    gst = _block(layout, field="GST_AMOUNT")
    stack["children"].remove(gst)
    layout["body"][0]["children"].insert(0, gst)
    _refused("receipt", RECEIPT, layout, f"{RECEIPT}.body", "['GST_AMOUNT']", "outside")
