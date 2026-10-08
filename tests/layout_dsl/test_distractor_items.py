"""A layout lists exactly the distractor items its blocks draw, beside its document type's provider."""

from typing import Any

import pytest

from generators.distractors.config import load_distractor_config
from generators.loader import load_layout_registry
from tests.conftest import REPO_ROOT
from tests.layout_dsl.test_schema import KNOWN, check_layout, make_layout, reject_layout, rejected, table

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

DX_KNOWN = KNOWN | {"DX_STORE_NUMBER", "DX_DUE_DATE"}
PROVIDER = [{"name": "receipt_distractors"}]
# Enough further receipt items, each drawn by a gated block, to meet selection.receipt.count[0].
FLOOR = ["rc_merchant_id", "rc_acn", "rc_phone"]
FLOOR_KEYS = ["DX_MERCHANT_ID", "DX_ACN", "DX_PHONE"]


def _gated(key: str = "DX_STORE_NUMBER") -> dict:
    return {"type": "text", "content": f"{{{key}}}", "when": key}


def dx_layout(body: list, items: list[str], *, providers: list | None = None) -> dict:
    """A receipt layout with its distractor provider, the floor items listed and drawn, plus `items`."""
    return make_layout(
        [*body, *(_gated(k) for k in FLOOR_KEYS)],
        providers=PROVIDER if providers is None else providers,
        distractor_items=[*FLOOR, *items],
    )


def test_the_floor_meets_the_receipt_minimum() -> None:
    assert len(FLOOR) >= load_distractor_config().selection["receipt"][0]


def test_every_shipped_layout_declares_its_items() -> None:
    for stem in ("receipts", "invoices", "bank_statements"):
        registry = load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml")
        for layout_id, layout in registry.items():
            # Every layout lists the items its blocks draw (tests/test_receipt_distractors.py,
            # tests/test_invoice_distractors.py, tests/test_bank_distractors.py check each type's list).
            assert layout["distractor_items"], layout_id


def test_a_layout_without_distractor_items_is_refused() -> None:
    layout = make_layout([])
    del layout["distractor_items"]
    reject_layout(layout, "distractor_items")


def test_a_block_binding_an_unlisted_dx_field_is_refused() -> None:
    layout = make_layout([_gated()], distractor_items=[])
    rejected(lambda: check_layout(layout, DX_KNOWN), "DX_STORE_NUMBER")


def test_a_listed_item_that_no_block_draws_is_refused() -> None:
    reject_layout(dx_layout([], ["rc_store_number"]), "rc_store_number", "no block draws")


def test_a_listed_item_for_another_document_type_is_refused() -> None:
    layout = dx_layout([_gated("DX_DUE_DATE")], ["in_due_date"])
    rejected(lambda: check_layout(layout, DX_KNOWN), "in_due_date")


def test_an_unknown_item_id_is_refused() -> None:
    reject_layout(dx_layout([], ["rc_nonexistent"]), "rc_nonexistent")


def test_a_block_drawing_a_dx_field_without_gating_on_it_is_refused() -> None:
    body = [{"type": "text", "content": "Store {DX_STORE_NUMBER}"}]
    reject_layout(dx_layout(body, ["rc_store_number"]), "when")


def test_a_block_gated_on_a_different_key_is_refused() -> None:
    body = [{"type": "text", "content": "Store {DX_STORE_NUMBER}", "when": "PAYER_NAME"}]
    reject_layout(dx_layout(body, ["rc_store_number"]), "when")


def test_a_correctly_listed_and_gated_item_passes() -> None:
    check_layout(dx_layout([_gated()], ["rc_store_number"]))


def test_a_dx_key_in_lines_that_no_item_emits_is_refused() -> None:
    body = [{"type": "block", "lines": ["Store {DX_STORE_NUMBER}"], "when": "DX_STORE_NUMBER"}]
    reject_layout(dx_layout(body, []), "DX_STORE_NUMBER")


def test_an_ungated_dx_key_in_lines_is_refused() -> None:
    body = [{"type": "block", "lines": ["Store {DX_STORE_NUMBER}"]}]
    reject_layout(dx_layout(body, ["rc_store_number"]), "when")


def test_an_ungated_dx_key_in_a_heading_is_refused() -> None:
    body = [{"type": "block", "heading": "{DX_STORE_NUMBER}", "lines": ["x"]}]
    reject_layout(dx_layout(body, ["rc_store_number"]), "when")


def test_a_gated_dx_key_in_lines_passes() -> None:
    body = [{"type": "block", "lines": ["Store {DX_STORE_NUMBER}"], "when": "DX_STORE_NUMBER"}]
    check_layout(dx_layout(body, ["rc_store_number"]))


def test_a_dx_key_in_a_table_column_label_is_refused() -> None:
    column = {"key": "desc", "label": "{DX_STORE_NUMBER}", "align": "left", "x": 0}
    body = [table(columns=[column]), _gated()]
    reject_layout(dx_layout(body, ["rc_store_number"]), "interpolates")


# --- the list and the provider arrive together ----------------------------------------------------


def test_listed_items_without_the_types_distractor_provider_are_refused() -> None:
    layout = dx_layout([_gated()], ["rc_store_number"], providers=[])
    err = rejected(lambda: check_layout(layout, DX_KNOWN | set(FLOOR_KEYS)), "receipt_distractors")
    assert "field_providers" in str(err)


def test_a_distractor_provider_for_another_document_type_is_refused() -> None:
    layout = dx_layout(
        [_gated()], ["rc_store_number"], providers=[*PROVIDER, {"name": "invoice_distractors"}]
    )
    reject_layout(layout, "invoice_distractors", "receipt")


def test_another_types_provider_alone_is_refused() -> None:
    layout = make_layout([], providers=[{"name": "bank_distractors"}], distractor_items=[])
    reject_layout(layout, "bank_distractors", "bank_statement")


@pytest.mark.parametrize("count", [0, 1, 2])
def test_a_provider_with_fewer_items_than_the_minimum_is_refused(count: int) -> None:
    low = load_distractor_config().selection["receipt"][0]
    assert count < low
    layout = make_layout(
        [_gated(k) for k in FLOOR_KEYS[:count]], providers=PROVIDER, distractor_items=FLOOR[:count]
    )
    reject_layout(layout, "selection.receipt.count", f"at least {low}")


def test_a_provider_with_exactly_the_minimum_passes() -> None:
    check_layout(dx_layout([], []))


def test_no_items_and_no_provider_stays_valid() -> None:
    check_layout(make_layout([], distractor_items=[]))


# Enough invoice items to meet selection.invoice.count[0], with every payment-state group item listed (a
# layout drawing invoice distractors must list them all) and the invoice number they require.
INVOICE_FLOOR = [
    "in_acn",
    "in_phone",
    "in_email",
    "in_po_number",
    "in_tagline",
    "in_job_reference",
    "in_customer_number",
    "in_terms",
    "in_thank_you",
    "in_invoice_number",
    "in_amount_paid",
    "in_balance_due",
    "in_paid_stamp",
    "in_paid_date",
    "in_remittance_total",
    "in_remit_barcode",
    "in_account_summary",
    "in_previous_balance",
]
INVOICE_FLOOR_KEYS = [
    "DX_ACN",
    "DX_PHONE",
    "DX_EMAIL",
    "DX_PO_NUMBER",
    "DX_TAGLINE",
    "DX_JOB_REFERENCE",
    "DX_CUSTOMER_NUMBER",
    "DX_TERMS",
    "DX_THANK_YOU",
    "DX_INVOICE_NUMBER",
    "DX_AMOUNT_PAID",
    "DX_BALANCE_DUE",
    "DX_PAID_STAMP",
    "DX_PAID_DATE",
    "DX_REMITTANCE_TOTAL",
    "DX_REMIT_BARCODE",
    "DX_ACCOUNT_TOTAL",
    "DX_PREVIOUS_BALANCE",
]


@pytest.mark.parametrize(
    ("stem", "doc_type", "provider", "items", "keys"),
    [
        (
            "invoices",
            "invoices",
            "invoice_distractors",
            INVOICE_FLOOR,
            INVOICE_FLOOR_KEYS,
        ),
    ],
)
def test_the_provider_check_resolves_the_layouts_own_type(
    stem: str, doc_type: str, provider: str, items: list[str], keys: list[str]
) -> None:
    """An invoice layout needs invoice_distractors, and receipt_distractors is refused on it."""
    body = [_gated(k) for k in keys]
    # An invoice layout declares the service categories its content may draw.
    extra: dict[str, Any] = {"service_categories": ["accounting"]} if doc_type == "invoices" else {}
    good = make_layout(body, providers=[{"name": provider}], distractor_items=items, **extra)
    check_layout(good, doc_type=doc_type)
    bad = make_layout(body, providers=[{"name": "receipt_distractors"}], distractor_items=items, **extra)
    known = KNOWN | set(keys)
    rejected(lambda: check_layout(bad, known, doc_type=doc_type), "receipt_distractors", "invoice")


# --- a DX key with no provider, and DX geometry ----------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"type": "text", "content": "{DX_STORE_NUMBER}", "when": "DX_STORE_NUMBER"},
        {"type": "text", "content": "Store", "when": "DX_STORE_NUMBER"},
        {"type": "block", "lines": ["{DX_STORE_NUMBER}"], "when": "DX_STORE_NUMBER"},
    ],
)
def test_an_unknown_dx_key_points_at_distractor_items_and_the_provider(block: dict) -> None:
    err = reject_layout(make_layout([block], distractor_items=[]), "DX_STORE_NUMBER", "distractor_items")
    message = str(err)
    assert "receipt_distractors" in message and "rc_store_number" in message
    assert "field_definitions.yml" not in message


def test_an_unknown_ordinary_key_keeps_the_field_definitions_advice() -> None:
    err = reject_layout(make_layout([{"type": "text", "content": "{NOT_A_FIELD}"}], distractor_items=[]))
    assert "field_definitions.yml" in str(err)


@pytest.mark.parametrize("known", [False, True])
def test_a_field_naming_a_dx_key_is_refused(known: bool) -> None:
    block = {
        "type": "text",
        "content": "{DX_STORE_NUMBER}",
        "when": "DX_STORE_NUMBER",
        "field": "DX_STORE_NUMBER",
    }
    layout = dx_layout([block], ["rc_store_number"]) if known else make_layout([block], distractor_items=[])
    err = reject_layout(layout, "DX_STORE_NUMBER", "overlay stage")
    assert ".field" in str(err)


@pytest.mark.parametrize("field_key", ["field", "last_row_field", "prefix_field"])
def test_a_table_column_recording_a_dx_key_is_refused(field_key: str) -> None:
    column = {"key": "desc", "label": "Description", "align": "left", "x": 0, field_key: "DX_STORE_NUMBER"}
    layout = dx_layout([table(columns=[column])], [])
    reject_layout(layout, "DX_STORE_NUMBER", "overlay stage", f"columns[0].{field_key}")


# --- two-key gating: one gate covers the keys its item requires (Ruling 7) ---------------------------

SURVEY = ["rc_survey_url", "rc_survey_code"]


def _survey_qr(gate: str, extra: str = "") -> dict:
    return {
        "type": "qrcode",
        "content": "{DX_SURVEY_URL}",
        "caption": "{DX_SURVEY_CODE}" + extra,
        "module_px": 2,
        "quiet_zone": 4,
        "error_level": "m",
        "when": gate,
    }


def test_a_qr_gated_on_the_url_whose_item_requires_the_code_passes() -> None:
    check_layout(dx_layout([_survey_qr("DX_SURVEY_URL")], SURVEY))


def test_the_reverse_gate_is_refused_naming_the_uncovered_key() -> None:
    reject_layout(
        dx_layout([_survey_qr("DX_SURVEY_CODE")], SURVEY), "['DX_SURVEY_URL']", "rc_survey_code", "body[0]"
    )


def test_only_the_uncovered_keys_are_named() -> None:
    block = _survey_qr("DX_SURVEY_URL", extra=" {DX_STORE_NUMBER}")
    err = reject_layout(dx_layout([block], [*SURVEY, "rc_store_number"]), "['DX_STORE_NUMBER']")
    assert "['DX_SURVEY_CODE'" not in str(err)


def test_two_dx_keys_gated_on_an_ordinary_field_are_both_uncovered() -> None:
    block = {**_survey_qr("PAYER_NAME")}
    reject_layout(dx_layout([block], SURVEY), "['DX_SURVEY_CODE', 'DX_SURVEY_URL']")


def test_a_nested_block_names_its_own_path() -> None:
    panel = {"type": "panel", "children": [_survey_qr("DX_SURVEY_CODE")]}
    reject_layout(dx_layout([panel], SURVEY), "body[0].children[0]")


BPAY_KEYS = {"DX_BPAY_BILLER", "DX_BPAY_REFERENCE"}


def _bpay_layout(gate: str) -> dict:
    block = {"type": "text", "content": "Biller {DX_BPAY_BILLER} Ref {DX_BPAY_REFERENCE}", "when": gate}
    return make_layout(
        [block, *(_gated(k) for k in INVOICE_FLOOR_KEYS)],
        providers=[{"name": "invoice_distractors"}],
        distractor_items=[*INVOICE_FLOOR, "in_bpay"],
        service_categories=["accounting"],
    )


@pytest.mark.parametrize("gate", ["DX_BPAY_BILLER", "DX_BPAY_REFERENCE"])
def test_one_item_emitting_both_bpay_keys_covers_either_gate(gate: str) -> None:
    """The biller and reference are one item's two keys, so a block gated on either draws both."""
    check_layout(_bpay_layout(gate), KNOWN | set(INVOICE_FLOOR_KEYS) | BPAY_KEYS, doc_type="invoices")


def test_bpay_keys_gated_on_another_items_key_are_refused() -> None:
    known = KNOWN | set(INVOICE_FLOOR_KEYS) | BPAY_KEYS
    rejected(
        lambda: check_layout(_bpay_layout("DX_PO_NUMBER"), known, doc_type="invoices"),
        "['DX_BPAY_BILLER', 'DX_BPAY_REFERENCE']",
        "in_po_number",
    )


@pytest.mark.parametrize(
    "dropped", ["in_balance_due", "in_paid_stamp", "in_previous_balance", "in_account_summary"]
)
def test_an_invoice_layout_omitting_a_payment_state_item_is_refused(dropped: str) -> None:
    items = [i for i in INVOICE_FLOOR if i != dropped]
    key = INVOICE_FLOOR_KEYS[INVOICE_FLOOR.index(dropped)]
    keys = [k for k in INVOICE_FLOOR_KEYS if k != key]
    layout = make_layout(
        [_gated(k) for k in keys], providers=[{"name": "invoice_distractors"}], distractor_items=items
    )
    rejected(
        lambda: check_layout(layout, KNOWN | set(INVOICE_FLOOR_KEYS), doc_type="invoices"),
        dropped,
        "states.invoice.payment.items",
        "distractor_items",
    )


# --- anchor: foot -----------------------------------------------------------------------------------


def _footer(**extra: object) -> dict:
    return {"type": "stack", "anchor": "foot", "children": [_gated("DX_STORE_NUMBER")], **extra}


def _anchored(body: list) -> dict:
    """A receipt layout drawing the floor items, then `body`."""
    return make_layout(
        [*(_gated(k) for k in FLOOR_KEYS), *body],
        providers=PROVIDER,
        distractor_items=[*FLOOR, "rc_store_number"],
    )


def test_a_foot_anchored_stack_closing_the_body_passes() -> None:
    check_layout(_anchored([_footer()]))


@pytest.mark.parametrize(
    ("body", "tokens"),
    [
        ([_footer(anchor="head")], ("anchor", "foot")),
        ([_footer(), _gated("DX_STORE_NUMBER")], ("not the last", "body[3].anchor")),
        ([{"type": "panel", "children": [_footer()]}], ("not the last", "body[3].children[0].anchor")),
    ],
)
def test_a_misplaced_or_unknown_anchor_is_refused(body: list, tokens: tuple[str, ...]) -> None:
    reject_layout(_anchored(body), *tokens)


# --- a barcode bound to a DX key: an unpadded even-length digit string that fits -----------------------


def _dx_barcode(key: str = "DX_TRANSACTION_ID", **extra: object) -> dict:
    return {
        "type": "barcode",
        "content": f"{{{key}}}",
        "symbology": "code128",
        "height": 40,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": True,
        "caption_gap": 4,
        "when": key,
        **extra,
    }


def _barcode_layout(block: dict, items: list[str], **extra: object) -> dict:
    layout = dx_layout([block], items)
    layout.update(extra)
    return layout


def _with_rule(monkeypatch: pytest.MonkeyPatch, item_id: str, **rule: object) -> None:
    """Swap one catalogue item's rule mapping for this test, in the schema's view of the catalogue."""
    import dataclasses

    from generators.layout_dsl import schema

    config = load_distractor_config()
    items = dict(config.items)
    items[item_id] = dataclasses.replace(items[item_id], rule={**items[item_id].rule, **rule})
    patched = dataclasses.replace(config, items=items)
    monkeypatch.setattr(schema, "load_distractor_config", lambda: patched)


@pytest.mark.parametrize(
    ("key", "item"), [("DX_TRANSACTION_ID", "rc_transaction_id"), ("DX_SURVEY_CODE", "rc_survey_code")]
)
def test_a_barcode_bound_to_a_qualifying_item_passes(key: str, item: str) -> None:
    check_layout(_barcode_layout(_dx_barcode(key), [item]))


def test_a_barcode_bound_to_a_prefixed_rule_is_refused() -> None:
    err = reject_layout(
        _barcode_layout(_dx_barcode("DX_STORE_NUMBER"), ["rc_store_number"]), "rc_store_number", "prefix"
    )
    assert "layout_under_test" in str(err)


def test_a_barcode_bound_to_a_non_digits_rule_is_refused() -> None:
    reject_layout(_barcode_layout(_dx_barcode("DX_PHONE"), []), "rc_phone", "digits")


@pytest.mark.parametrize(("length", "token"), [(15, "even"), (18, "at most 16"), (0, "length")])
def test_a_barcode_bound_to_a_bad_length_is_refused(
    monkeypatch: pytest.MonkeyPatch, length: int, token: str
) -> None:
    _with_rule(monkeypatch, "rc_transaction_id", length=length)
    reject_layout(_barcode_layout(_dx_barcode(), ["rc_transaction_id"]), "rc_transaction_id", token)


def test_a_barcode_too_wide_for_the_layout_is_refused_naming_both_widths() -> None:
    from PIL import Image, ImageDraw

    from generators.layout_dsl.schema import _widest_digit_barcode

    block = _dx_barcode(module_px=3)
    layout = _barcode_layout(block, ["rc_transaction_id"], content_width=300)
    _, worst = _widest_digit_barcode(
        16,
        block,
        layout,
        ImageDraw.Draw(Image.new("RGB", (1, 1))),
        layout_id="layout_under_test",
        layout_path="x",
    )
    expected = worst.span
    # With the in-repo encoder every even run of digits has the same bars; the width is the bars here.
    assert expected == _span("9" * 16, block, layout) and expected > 300
    reject_layout(layout, "rc_transaction_id", "layout_under_test", f"{expected}px", "300px")


def _span(content: str, block: dict, layout: dict) -> int:
    from PIL import Image, ImageDraw

    from generators.layout_dsl.primitives_codes import barcode_geometry

    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return barcode_geometry(
        content, block, layout, draw, layout_id="layout_under_test", layout_path="x"
    ).span


def test_a_proportional_caption_is_measured_at_its_widest_digits() -> None:
    """Kalam's digits differ in width: a region fitting sixteen nines but not sixteen eights is refused."""
    block = _dx_barcode(family="kalam", module_px=1, quiet_zone=0)
    layout = _barcode_layout(block, ["rc_transaction_id"])
    layout["font_sizes"] = {**layout["font_sizes"], "body": 28}
    nines, eights = _span("9" * 16, block, layout), _span("8" * 16, block, layout)
    assert nines < eights, (nines, eights)
    layout["content_width"] = nines
    reject_layout(layout, "rc_transaction_id", f"{nines}px")
    layout["content_width"] = max(_span(str(d) * 16, block, layout) for d in range(10)) + 20
    check_layout(layout)


def test_the_caption_counts_towards_the_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bars that fit with a caption that does not are still refused."""
    from generators.layout_dsl import primitives_codes

    real = primitives_codes.measure_caption

    def wide(*args: object, **kwargs: object) -> object:
        caption = real(*args, **kwargs)  # type: ignore[arg-type]
        return caption._replace(width=10_000)

    monkeypatch.setattr(primitives_codes, "measure_caption", wide)
    reject_layout(_barcode_layout(_dx_barcode(), ["rc_transaction_id"]), "10000px")
    check_layout(_barcode_layout(_dx_barcode(human_readable=False), ["rc_transaction_id"]))


def test_a_barcode_binding_a_dx_key_with_other_text_is_refused() -> None:
    block = _dx_barcode(content="TX{DX_TRANSACTION_ID}")
    reject_layout(_barcode_layout(block, ["rc_transaction_id"]), "single", "DX_TRANSACTION_ID")


def test_a_barcode_binding_two_dx_keys_is_refused() -> None:
    block = _dx_barcode("DX_POINTS_BALANCE", content="{DX_POINTS_BALANCE}{DX_MEMBER_NUMBER}")
    layout = _barcode_layout(block, ["rc_points_balance", "rc_member_number"])
    reject_layout(layout, "single", "DX_MEMBER_NUMBER")


def test_an_ean13_barcode_bound_to_a_dx_key_is_refused() -> None:
    block = _dx_barcode(symbology="ean13")
    reject_layout(_barcode_layout(block, ["rc_transaction_id"]), "code128", "ean13")
