"""An unpaid invoice's previous balance prints in an account summary that adds up, never in the totals.

Final review, Important 3: a previous balance printed above Subtotal and never added to the Total or the
amount due is a sum a careful reader sees does not work, and teaches a model that the first figure of the
totals column is never the answer. The balance now prints only in a panel of its own, beside the payment
that cleared it, above this invoice and the amount due (both the invoice total).
"""

import copy
from decimal import Decimal

import pytest

from generators.distractors.config import load_distractor_config
from generators.invoice import render_invoice
from tests.distractor_helpers import printed_dx_names, selected, walk_blocks
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.test_invoice_distractors import force_state

DOC = "invoice"
CONFIG = load_distractor_config()
LAYOUTS = layouts_for(DOC)
SUMMARY_KEYS = {"DX_PREVIOUS_BALANCE", "DX_ACCOUNT_TOTAL"}


def _summary_panels(body: list) -> list[dict]:
    return [
        b for b in walk_blocks(body) if b.get("type") == "panel" and b.get("when") == "DX_ACCOUNT_TOTAL"
    ]


def _summary_lines(layout: dict) -> list[tuple[str, str]]:
    """(label, value template) of each pair in the layout's account summary panel."""
    (panel,) = _summary_panels(layout["body"])
    return [(b["label"], b["value"]) for b in walk_blocks(panel["children"]) if b.get("type") == "pair"]


def _misplaced(body: list) -> list[str]:
    """Every block printing a summary key outside the account summary panel."""
    inside = {id(b) for panel in _summary_panels(body) for b in walk_blocks(panel["children"])}
    return [
        str(b.get("label") or b.get("content"))
        for b in walk_blocks(body)
        if printed_dx_names(b) & SUMMARY_KEYS and id(b) not in inside
    ]


def test_the_catalogue_ties_the_balance_to_the_summary_in_the_unpaid_state() -> None:
    summary, balance = CONFIG.items["in_account_summary"], CONFIG.items["in_previous_balance"]
    assert summary.kind == "repeat" and summary.field == "TOTAL_AMOUNT"
    assert balance.kind == "near_miss" and balance.requires == ("in_account_summary",)
    unpaid = CONFIG.states[DOC]["payment"].groups["unpaid"]
    assert {"in_account_summary", "in_previous_balance"} <= set(unpaid)


@pytest.mark.parametrize("layout_id", list(LAYOUTS))
def test_every_layout_prints_the_balance_only_in_a_summary_that_adds_up(layout_id: str) -> None:
    layout = LAYOUTS[layout_id]
    assert {"in_account_summary", "in_previous_balance"} <= set(layout["distractor_items"])
    assert _summary_lines(layout) == [
        ("Previous balance", "{DX_PREVIOUS_BALANCE}"),
        ("Payment received, thank you", "{DX_PREVIOUS_BALANCE}"),
        ("This invoice", "{DX_ACCOUNT_TOTAL}"),
        ("Amount due", "{DX_ACCOUNT_TOTAL}"),
    ]
    assert not _misplaced(layout["body"]), _misplaced(layout["body"])


def test_the_placement_check_can_fail() -> None:
    """The old design: a previous balance in the totals column, above Subtotal."""
    body = copy.deepcopy(LAYOUTS["tax_invoice_standard"]["body"])
    totals = next(b for b in walk_blocks(body) if b.get("type") == "pair" and b.get("label") == "Subtotal")
    # The totals column is the totals region's stack; the old design put the balance at its head.
    column = next(
        b["children"] for b in walk_blocks(body) if b.get("region") == "totals" and totals in b["children"]
    )
    column.insert(0, {"type": "pair", "label": "Previous balance", "value": "{DX_PREVIOUS_BALANCE}"})
    assert _misplaced(body) == ["Previous balance"]


def test_every_unpaid_summary_adds_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case, unpaid with every item on: the balance is cleared by an equal payment, and this invoice
    and the amount due are both the invoice total, so the amount due is what the summary sums to."""
    force_state(monkeypatch, "unpaid")
    monkeypatch.setattr(
        "generators.distractors.providers.select_items",
        lambda config, doc_type, eligible, rng, *, where: list(eligible),
    )
    for case_id, entry in corpus_entries(DOC).items():
        values = selected(entry, LAYOUTS[entry["layout"]])
        total = Decimal(entry["fields"]["TOTAL_AMOUNT"])
        assert Decimal(values["DX_ACCOUNT_TOTAL"]) == total, case_id
        balance = Decimal(values["DX_PREVIOUS_BALANCE"])
        assert balance != total, case_id
        # Previous balance - payment received + this invoice = amount due.
        assert balance - balance + Decimal(values["DX_ACCOUNT_TOTAL"]) == total, case_id


def test_a_paid_invoice_prints_no_summary(monkeypatch: pytest.MonkeyPatch, drawn_keys: set[str]) -> None:
    force_state(monkeypatch, "paid")
    monkeypatch.setattr(
        "generators.distractors.providers.select_items",
        lambda config, doc_type, eligible, rng, *, where: list(eligible),
    )
    for case_id, entry in list(corpus_entries(DOC).items())[:8]:
        drawn_keys.clear()
        render_invoice(entry, LAYOUTS[entry["layout"]], distractors=True)
        assert not drawn_keys & SUMMARY_KEYS, case_id
        assert "DX_AMOUNT_PAID" in drawn_keys, case_id


def test_an_unpaid_summary_draws_all_four_lines(
    monkeypatch: pytest.MonkeyPatch, drawn_keys: set[str]
) -> None:
    force_state(monkeypatch, "unpaid")
    monkeypatch.setattr(
        "generators.distractors.providers.select_items",
        lambda config, doc_type, eligible, rng, *, where: list(eligible),
    )
    seen = set()
    for entry in corpus_entries(DOC).values():
        if entry["layout"] in seen:
            continue
        seen.add(entry["layout"])
        drawn_keys.clear()
        render_invoice(entry, LAYOUTS[entry["layout"]], distractors=True)
        assert SUMMARY_KEYS <= drawn_keys, entry["layout"]
    assert seen == set(LAYOUTS)
