"""Every layout declares `overlay:`, and validation refuses an overlay block it could not draw."""

import copy
import dataclasses

import pytest

from generators.distractors.config import NO_OVERLAY, load_distractor_config
from generators.layout_dsl import schema
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import load_layout_registry
from generators.schema import field_names_for
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.test_schema import check_layout, make_layout
from tests.overlay.overlay_helpers import TEST_TYPE

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

INVOICES = REPO_ROOT / "config" / "layouts" / "invoices.yml"
RECEIPTS = REPO_ROOT / "config" / "layouts" / "receipts.yml"
INVOICE_ID = "tax_invoice_standard"


def _invoice(overlay: object) -> dict:
    """The standard invoice layout with `overlay` as its overlay; a list keeps the shipped stamp after it.

    The stamp's items are drawn only by the stamp block, so a layout without it is refused for listing an
    item no block draws; the block under test stays at `overlay[0]`.
    """
    layout = copy.deepcopy(load_layout_registry(INVOICES)[INVOICE_ID])
    layout["overlay"] = [*overlay, *layout["overlay"]] if isinstance(overlay, list) else overlay
    return layout


def _validate_invoice(layout: dict) -> None:
    validate_layout(
        layout,
        layout_id=INVOICE_ID,
        layout_path="config/layouts/invoices.yml",
        known_fields=set(field_names_for("invoices")),
        doc_type="invoices",
    )


def _refused(call, *tokens: str) -> str:
    with pytest.raises(LayoutSchemaError) as err:
        call()
    assert_diagnostic_error(err.value)
    message = str(err.value)
    for token in tokens:
        assert token in message, f"{token!r} missing from: {message}"
    return message


def _without_gate(blocks: object, key: str) -> object:
    """Return a body with every block gated on `key` removed, at any depth."""
    if isinstance(blocks, list):
        return [
            _without_gate(block, key)
            for block in blocks
            if not (isinstance(block, dict) and block.get("when") == key)
        ]
    if isinstance(blocks, dict):
        return {name: _without_gate(value, key) for name, value in blocks.items()}
    return blocks


def test_every_shipped_layout_declares_its_overlay() -> None:
    """Receipts declare an empty overlay; every invoice layout one PAID stamp; every bank layout the
    taxpayer's four annotation blocks (tests/test_bank_annotations.py checks them in full)."""
    count = 0
    for stem in ("receipts", "invoices", "bank_statements"):
        for layout_id, layout in load_layout_registry(
            REPO_ROOT / "config" / "layouts" / f"{stem}.yml"
        ).items():
            if stem == "invoices":
                assert [block["type"] for block in layout["overlay"]] == ["stamp"], layout_id
                assert layout["overlay"][0]["when"] == "DX_PAID_STAMP", layout_id
                assert layout["overlay"][0]["date_key"] == "DX_PAID_DATE", layout_id
            elif stem == "bank_statements":
                assert [block["type"] for block in layout["overlay"]] == [
                    "highlight",
                    "ink_marks",
                    "handwriting",
                    "handwriting",
                ], layout_id
            else:
                assert layout["overlay"] == [], layout_id
            count += 1
    assert count == 22


def test_a_layout_without_overlay_is_refused() -> None:
    layout = make_layout([])
    del layout["overlay"]
    message = _refused(
        lambda: check_layout(layout),
        f"{REPO_ROOT / 'config' / 'layouts' / 'receipts.yml'} -> layout_under_test.overlay",
        "overlay: []",
        "add overlay: [] to the layout",
    )
    assert "has no 'overlay' key" in message


def test_an_overlay_that_is_not_a_list_is_refused() -> None:
    _refused(
        lambda: _validate_invoice(_invoice({"type": TEST_TYPE})),
        f"{INVOICES} -> {INVOICE_ID}.overlay\n",
        "not a list",
    )


def test_a_receipt_overlay_must_be_empty(box_drawer: str) -> None:
    layout = make_layout(
        [], overlay=[{"type": box_drawer, "when": "DX_STORE_NUMBER", "box": [40, 40, 60, 60]}]
    )
    _refused(
        lambda: check_layout(layout),
        "layout_under_test.overlay",
        "overlay: [] on every receipt layout",
        "overlay.protect.receipt is no_overlay",
        "distractors.yml",
    )


def _protect(monkeypatch: pytest.MonkeyPatch, **policies: str) -> None:
    """Replace the shipped overlay.protect policies for one test, as if the YAML said so."""
    config = load_distractor_config()
    overlay = dataclasses.replace(config.overlay, protect={**config.overlay.protect, **policies})
    patched = dataclasses.replace(config, overlay=overlay)
    monkeypatch.setattr(schema, "load_distractor_config", lambda *args: patched)


def test_which_types_carry_no_overlay_is_read_from_the_yaml(
    box_drawer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Minor 4: 'receipts carry no overlay' is stated once, in overlay.protect, not in Python."""
    block = {"type": box_drawer, "when": "DX_STORE_NUMBER", "box": [40, 40, 60, 60]}
    receipt = copy.deepcopy(next(iter(load_layout_registry(RECEIPTS).values())))
    receipt["overlay"] = [block]
    known = set(field_names_for("receipts"))
    _refused(lambda: check_layout(receipt, known), "overlay.protect.receipt is no_overlay")
    _protect(monkeypatch, receipt="scored_and_marks")
    check_layout(receipt, known)
    _protect(monkeypatch, invoice=NO_OVERLAY)
    _refused(lambda: _validate_invoice(_invoice([])), "overlay: [] on every invoice layout")


def test_an_unknown_block_type_lists_the_registered_ones(box_drawer: str) -> None:
    layout = _invoice([{"type": "hologram", "when": "DX_ACN"}])
    _refused(
        lambda: _validate_invoice(layout),
        f"{INVOICE_ID}.overlay[0].type",
        "'hologram'",
        "['handwriting', 'highlight', 'ink_marks', 'stamp', 'test_box']",
    )


def test_an_unknown_block_type_is_refused_with_only_the_shipped_types_registered() -> None:
    _refused(
        lambda: _validate_invoice(_invoice([{"type": TEST_TYPE, "when": "DX_ACN"}])),
        "'test_box'",
        "['handwriting', 'highlight', 'ink_marks', 'stamp']",
    )


@pytest.mark.parametrize("when", ["DX_NOT_EMITTED", "TOTAL_AMOUNT", None, "DX_STORE_NUMBER"])
def test_a_when_key_no_listed_item_emits_is_refused(box_drawer: str, when: object) -> None:
    block = {"type": box_drawer, "box": [40, 40, 60, 60], **({"when": when} if when else {})}
    _refused(lambda: _validate_invoice(_invoice([block])), f"{INVOICE_ID}.overlay[0].when")


def test_a_valid_overlay_block_is_accepted(box_drawer: str) -> None:
    _validate_invoice(_invoice([{"type": box_drawer, "when": "DX_ACN", "box": [40, 40, 60, 60]}]))


def test_a_key_covered_by_the_gate_is_accepted(box_drawer: str) -> None:
    # in_balance_due requires in_amount_paid, so a block gated on the balance may name the amount paid.
    block = {
        "type": box_drawer,
        "when": "DX_BALANCE_DUE",
        "content": "{DX_AMOUNT_PAID}",
        "rows": "DX_AMOUNT_PAID",
    }
    _validate_invoice(_invoice([block]))


@pytest.mark.parametrize(
    "extra",
    [
        {"content": "Paid {DX_BALANCE_DUE}"},
        {"rows": "DX_BALANCE_DUE"},
        {"lanes": [{"key": "DX_BALANCE_DUE"}]},
    ],
)
def test_a_key_the_gate_does_not_cover_is_refused(box_drawer: str, extra: dict) -> None:
    block = {"type": box_drawer, "when": "DX_AMOUNT_PAID", **extra}
    _refused(
        lambda: _validate_invoice(_invoice([block])),
        f"{INVOICE_ID}.overlay[0]",
        "['DX_BALANCE_DUE']",
        "gated on 'DX_AMOUNT_PAID'",
    )


def test_a_key_no_listed_item_emits_is_refused_in_a_parameter(box_drawer: str) -> None:
    block = {"type": box_drawer, "when": "DX_ACN", "rows": "DX_NOT_EMITTED"}
    _refused(lambda: _validate_invoice(_invoice([block])), "['DX_NOT_EMITTED']", f"{INVOICE_ID}.overlay[0]")


def test_an_item_drawn_only_by_the_overlay_counts_as_drawn(box_drawer: str) -> None:
    layout = _invoice([])
    layout["body"] = _without_gate(layout["body"], "DX_ACN")
    _refused(lambda: _validate_invoice(layout), "'in_acn'", "no block draws")
    layout["overlay"] = [
        {"type": box_drawer, "when": "DX_ACN", "box": [40, 40, 60, 60]},
        *layout["overlay"],
    ]
    _validate_invoice(layout)


# --- optional keys (Decision 10) ------------------------------------------------------------------------


def test_an_optional_key_need_not_be_covered_by_the_gate(box_drawer: str) -> None:
    """in_paid_date requires in_paid_stamp, not the reverse: the date may be absent when the stamp draws."""
    config = load_distractor_config()
    assert "in_paid_date" not in schema.required_items(config, "in_paid_stamp")
    block = {"type": box_drawer, "when": "DX_PAID_STAMP", "date_key": "DX_PAID_DATE"}
    _validate_invoice(_invoice([block]))
    # Named anywhere else, the same key is uncovered and refused (Ruling 7).
    _refused(
        lambda: _validate_invoice(_invoice([{**block, "rows": "DX_PAID_DATE"}])),
        "['DX_PAID_DATE']",
        "gated on 'DX_PAID_STAMP'",
    )


def test_an_optional_key_whose_item_does_not_require_the_gate_is_refused(box_drawer: str) -> None:
    # in_due_date requires nothing, so its date could print on a page whose stamp never draws.
    block = {"type": box_drawer, "when": "DX_PAID_STAMP", "date_key": "DX_DUE_DATE"}
    _refused(
        lambda: _validate_invoice(_invoice([block])),
        f"{INVOICE_ID}.overlay[0].date_key",
        "['DX_DUE_DATE']",
        "'in_paid_stamp'",
        "requires: [in_paid_stamp]",
    )


def test_an_optional_key_no_listed_item_emits_is_refused(box_drawer: str) -> None:
    block = {"type": box_drawer, "when": "DX_PAID_STAMP", "date_key": "DX_NOT_EMITTED"}
    _refused(lambda: _validate_invoice(_invoice([block])), "['DX_NOT_EMITTED']")


# --- the stamp block ------------------------------------------------------------------------------------


def _stamp(**changes: object) -> dict:
    block = copy.deepcopy(load_layout_registry(INVOICES)[INVOICE_ID]["overlay"][0])
    block.update(changes)
    return {k: v for k, v in block.items() if v is not _DROP}


_DROP = object()


def _stamp_refused(block: dict, *tokens: str) -> str:
    layout = copy.deepcopy(load_layout_registry(INVOICES)[INVOICE_ID])
    layout["overlay"] = [block]
    return _refused(lambda: _validate_invoice(layout), f"{INVOICES} -> {INVOICE_ID}.overlay[0]", *tokens)


def test_every_shipped_stamp_block_validates() -> None:
    for layout_id, layout in load_layout_registry(INVOICES).items():
        validate_layout(
            layout,
            layout_id=layout_id,
            layout_path="config/layouts/invoices.yml",
            known_fields=set(field_names_for("invoices")),
            doc_type="invoices",
        )


@pytest.mark.parametrize("key", ["content", "date_key", "candidates"])
def test_a_stamp_block_missing_a_key_is_refused(key: str) -> None:
    _stamp_refused(_stamp(**{key: _DROP}), f"missing ['{key}']")


def test_a_stamp_block_with_an_unknown_key_is_refused() -> None:
    _stamp_refused(_stamp(colour="#FF0000"), "unknown ['colour']")


@pytest.mark.parametrize(("content", "token"), [("", "content is ''"), ("PAID", "content is 'PAID'")])
def test_a_stamp_content_not_drawn_by_a_pick_rule_is_refused(content: str, token: str) -> None:
    _stamp_refused(_stamp(content=content), ".content", token)


def test_a_stamp_whose_word_is_not_a_pick_rules_is_refused() -> None:
    # The nil balance is a paid-group item drawn by a fixed rule: its size is not proven by any list.
    block = _stamp(when="DX_BALANCE_DUE", content="{DX_BALANCE_DUE}", date_key="none")
    _stamp_refused(block, ".content", "['DX_BALANCE_DUE'] are drawn by other rules")


@pytest.mark.parametrize("date_key", ["PAID_DATE", "", 7, "DX_paid"])
def test_a_stamp_date_key_that_is_neither_a_key_nor_none_is_refused(date_key: object) -> None:
    _stamp_refused(_stamp(date_key=date_key), ".date_key", "neither a DX_ key nor none")


def test_a_stamp_without_a_date_line_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    layout = copy.deepcopy(load_layout_registry(INVOICES)[INVOICE_ID])
    layout["overlay"] = [_stamp(date_key="none")]
    # With date_key: none no block draws the date, so a layout still listing its item is refused ...
    _refused(lambda: _validate_invoice(layout), "'in_paid_date'", "no block draws")
    # ... and one that drops it is accepted once the paid group no longer asks for it.
    config = load_distractor_config()
    payment = config.states["invoice"]["payment"]
    paid = tuple(i for i in payment.groups["paid"] if i != "in_paid_date")
    groups = {**payment.groups, "paid": paid}
    states = {**config.states, "invoice": {"payment": dataclasses.replace(payment, groups=groups)}}
    monkeypatch.setattr(
        schema, "load_distractor_config", lambda *a: dataclasses.replace(config, states=states)
    )
    layout["distractor_items"] = [i for i in layout["distractor_items"] if i != "in_paid_date"]
    _validate_invoice(layout)


@pytest.mark.parametrize(
    ("candidates", "token"),
    [
        ([{"x": 1.2, "y": 0.5}], "outside [0, 1]"),
        ([{"x": 0.5, "y": -0.1}], "outside [0, 1]"),
        ([], "candidates is []"),
        ([{"x": 0.5}], "each candidate is the stamp's centre"),
        ([{"x": "0.5", "y": 0.5}], "each candidate is the stamp's centre"),
        ([{"x": 0.5, "y": 0.5, "r": 3}], "each candidate is the stamp's centre"),
        ({"x": 0.5, "y": 0.5}, "each candidate is the stamp's centre"),
    ],
)
def test_a_stamp_candidate_off_the_page_or_malformed_is_refused(candidates: object, token: str) -> None:
    _stamp_refused(_stamp(candidates=candidates), ".candidates", token)


@pytest.mark.parametrize("candidate", [{"x": 0.02, "y": 0.5}, {"x": 0.5, "y": 0.99}, {"x": 0.9, "y": 0.5}])
def test_a_candidate_whose_largest_footprint_leaves_the_page_inset_is_refused(candidate: dict) -> None:
    message = _stamp_refused(
        _stamp(candidates=[{"x": 0.5, "y": 0.5}, candidate]),
        ".candidates[1]",
        "largest footprint",
        "page inset",
        "overlay.stamp",
    )
    assert ".candidates[0]" not in message.split("Where:")[1].split("\n")[0]


def test_a_candidate_just_inside_the_inset_is_accepted() -> None:
    """The footprint check is the largest footprint's half-extent from the inset edge, no more."""
    from generators.overlay.stamp import DATE_SAMPLES, rotated_extent, tile_size

    stamp = load_distractor_config().overlay.stamp
    widest = max(
        rotated_extent(tile_size(stamp, word, date), stamp.rotation_deg)[0]
        for word in ("PAID", "PAID IN FULL", "RECEIVED")
        for date in DATE_SAMPLES
    )
    inset = load_distractor_config().overlay.page_inset_px
    x = (inset + widest / 2 + 1) / 1900
    layout = copy.deepcopy(load_layout_registry(INVOICES)[INVOICE_ID])
    layout["overlay"] = [_stamp(candidates=[{"x": x, "y": 0.5}])]
    _validate_invoice(layout)
    layout["overlay"] = [_stamp(candidates=[{"x": x - 2 / 1900, "y": 0.5}])]
    _refused(lambda: _validate_invoice(layout), "largest footprint")


def test_a_stamp_outside_the_paid_group_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_distractor_config()
    payment = config.states["invoice"]["payment"]
    groups = {"paid": ("in_amount_paid", "in_balance_due"), "unpaid": payment.groups["unpaid"]}
    states = {**config.states, "invoice": {"payment": dataclasses.replace(payment, groups=groups)}}
    monkeypatch.setattr(
        schema, "load_distractor_config", lambda *a: dataclasses.replace(config, states=states)
    )
    _stamp_refused(_stamp(), ".when", "states.invoice.payment.items.paid", "remittance slip")
