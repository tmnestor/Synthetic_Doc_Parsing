"""Terminal-block config loading, derivation, and rendering (local-only).

The slip itself is no longer drawn by Python: `render_payment_block` was
deleted in the layout-DSL migration and the three variants (card, wallet,
cash) are now `when:`-guarded blocks in config/layouts/receipts.yml. The
rendering tests below therefore drive the DSL body directly, through
`generators.receipt.render_receipt` for the corpus cases and through a
provider-free layout with injected PAYMENT_* fields for the cash variant,
which the corpus itself can never reach (Cash carries weight 0).
"""

import ast
import copy
import dataclasses
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
import yaml
from typer.testing import CliRunner

from generators import payment_block
from generators.layout_budgets import field_budget
from generators.layout_dsl import field_providers as fp
from generators.layout_dsl.field_providers import receipt_payment, receipt_pos
from generators.payment_block import (
    _REQUIRED_KEYS,
    PaymentDetails,
    derive_payment,
    load_link_index,
    load_terminal_pools,
    method_from_bank_description,
)
from generators.pipeline import app
from generators.receipt import render_receipt
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import ENTRY as DSL_ENTRY
from tests.layout_dsl.dsl_helpers import real_layout, render
from tests.renderer_helpers import corpus_entries, entry_per_layout, layouts_for, pixels_equal

POOLS_PATH = REPO_ROOT / "config" / "data_pools.yml"
LINKS_PATH = REPO_ROOT / "ground_truth" / "transaction_links.yml"
RECEIPT_LAYOUT_PATH = "config/layouts/receipts.yml"

# Invented values: case ids in the CASE9xx range and years from 2031 appear nowhere in ground_truth/.
DATE = "05/03/2031"
TIME = "14:07"
TOTAL = "40.00"
SLIP_WHEN_KEYS = {"PAYMENT_AID", "PAYMENT_WALLET_LABEL"}
CASH_WHEN_KEYS = {"PAYMENT_TENDERED", "PAYMENT_CHANGE"}
FABRICATED_ENTRY_ID = "CASE999"


def case_ids(count: int, start: int = 900) -> list[str]:
    """Return `count` invented case ids, none of which is in the ground truth."""
    return [f"CASE{start + n:04d}" for n in range(count)]


def real_pools() -> dict:
    """Return a deep copy of the validated payment_terminal pools, safe to mutate."""
    return copy.deepcopy(load_terminal_pools())


def with_weights(weights: dict[str, int]) -> dict:
    """Return the real pools with `receipt_method_weights` replaced."""
    return {**real_pools(), "receipt_method_weights": weights}


def patch_pools(monkeypatch: pytest.MonkeyPatch, pools: dict) -> None:
    """Make `derive_payment` (hence the provider) draw from `pools` when none is passed."""
    monkeypatch.setattr(payment_block, "load_terminal_pools", lambda *a, **k: pools)


def write_pools(tmp_path: Path, mutate) -> Path:
    """Write a data-pools file whose payment_terminal block has been altered by `mutate(block)`."""
    block = real_pools()
    mutate(block)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump({"payment_terminal": block}))
    return path


def derive(case_id: str, *, total: str = TOTAL, pools: dict | None = None, bank: str | None = None):
    """Derive a payment for an invented receipt."""
    return derive_payment(case_id, DATE, total, TIME, pools=pools, bank_description=bank)


def cash_details(case_id: str, total: str = TOTAL) -> PaymentDetails:
    """Return a cash PaymentDetails by drawing from a cash-only weighted pool."""
    details = derive(case_id, total=total, pools=with_weights({"Cash": 1}))
    assert details.kind == "cash"
    return details


def flat_body(body: list[dict]) -> list[dict]:
    """A receipt body's blocks in drawing order: the body is three stacks, each drawing its children."""
    return [
        child for block in body for child in (block["children"] if block["type"] == "stack" else [block])
    ]


def without_blocks(body: list[dict], drop: set[str]) -> list[dict]:
    """The body with every block gated on one of `drop` removed, inside its stacks too."""
    out = []
    for block in body:
        if block["type"] == "stack":
            block = {**block, "children": [c for c in block["children"] if c.get("when") not in drop]}
        elif block.get("when") in drop:
            continue
        out.append(block)
    return out


def receipt_body() -> list[dict]:
    """Return the shared receipt body from the first receipt layout, its stacks flattened."""
    return flat_body(next(iter(layouts_for("receipt").values()))["body"])


def blocks_whose_when_is_in(keys: set[str]) -> list[dict]:
    """Return the body blocks guarded by `when:` on any of `keys`."""
    return [b for b in receipt_body() if b.get("when") in keys]


def injected_entry(details_source: dict) -> dict:
    """Build an entry whose fields already carry the PAYMENT_* values, for provider-free layouts."""
    fields = {**cast(dict[str, str], DSL_ENTRY["fields"]), **receipt_payment(details_source, {})}
    return {**DSL_ENTRY, "fields": fields}


def render_lines(blocks: list[dict], entry: dict) -> int:
    """Draw `blocks` onto a scratch canvas with the provider chain off; return the height used."""
    return render(real_layout(blocks), entry)


def unlinked_entry(layout_id: str, base: dict) -> dict:
    """Return `base` re-keyed to an invented case id so the link index cannot force a scheme."""
    return {**base, "case_id": FABRICATED_ENTRY_ID, "layout": layout_id}


# --------------------------------------------------------------------------- config loading


def test_loads_real_pools_file() -> None:
    """The shipped data pools file validates and carries every required key."""
    pools = load_terminal_pools()
    assert set(_REQUIRED_KEYS) <= set(pools)
    assert load_terminal_pools(POOLS_PATH) == pools


def test_every_scheme_has_required_subkeys() -> None:
    """Every card scheme declares display, aid, pan_digits and a non-empty account_types list."""
    for name, scheme in load_terminal_pools()["schemes"].items():
        for sub in ("display", "aid", "pan_digits", "account_types"):
            assert scheme.get(sub), f"{name}.{sub}"
        assert isinstance(scheme["pan_digits"], int)
        assert isinstance(scheme["account_types"], list)


def test_every_weighted_method_resolves() -> None:
    """Each method in the weighted pool names a scheme, a wallet, or Cash."""
    pools = load_terminal_pools()
    known = set(pools["schemes"]) | set(pools["wallets"]) | {"Cash"}
    assert set(pools["receipt_method_weights"]) <= known


def test_missing_required_key_is_four_element_diagnostic(tmp_path: Path) -> None:
    """Dropping a required key raises a diagnostic naming the key and the file."""
    path = write_pools(tmp_path, lambda block: block.pop("acquirers"))
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(path)
    assert_diagnostic_error(exc_info.value)
    assert "payment_terminal.acquirers" in str(exc_info.value)
    assert str(path) in str(exc_info.value)


def test_missing_payment_terminal_block_is_diagnostic(tmp_path: Path) -> None:
    """A pools file with no payment_terminal block fails with a diagnostic, not a KeyError."""
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump({"something_else": {"a": 1}}))
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(path)
    assert_diagnostic_error(exc_info.value)
    assert "payment_terminal" in str(exc_info.value)


def test_scheme_missing_subkey_is_diagnostic(tmp_path: Path) -> None:
    """A scheme lacking `aid` is rejected, naming the scheme and the sub-key."""
    path = write_pools(tmp_path, lambda block: block["schemes"]["Visa"].pop("aid"))
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(path)
    assert_diagnostic_error(exc_info.value)
    assert "payment_terminal.schemes.Visa.aid" in str(exc_info.value)


def test_unknown_weighted_method_is_diagnostic(tmp_path: Path) -> None:
    """A weighted method that is no scheme, wallet or Cash is rejected."""
    path = write_pools(tmp_path, lambda block: block["receipt_method_weights"].update({"Bitcoin": 5}))
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(path)
    assert_diagnostic_error(exc_info.value)
    assert "Bitcoin" in str(exc_info.value)


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """A nonexistent pools path raises FileNotFoundError carrying the four elements."""
    missing = tmp_path / "nope.yml"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_terminal_pools(missing)
    assert_diagnostic_error(exc_info.value)
    assert str(missing) in str(exc_info.value)


def test_the_receipt_body_prints_the_expected_slip_wording() -> None:
    """The fixed slip wording lives once, in the receipt body shared by every layout."""
    literals: list[str] = []
    for block in receipt_body():
        for key in ("content", "label"):
            if key in block:
                literals.append(block[key])
    printed = "\n".join(literals)
    for wording in (
        "CUSTOMER COPY",
        "CONTACTLESS - {PAYMENT_WALLET_LABEL}",
        "AID: {PAYMENT_AID}",
        "Card: {PAYMENT_MASKED_PAN} {PAYMENT_ENTRY_MODE}",
        "PSN: {PAYMENT_PSN}, ATC: {PAYMENT_ATC}",
        "Purchase   AUD",
        "APPROVED   00",
        "Terminal ID: {PAYMENT_TERMINAL_ID}",
        "Transaction Ref: {PAYMENT_TRANSACTION_REF}",
        "Retain copy for your records",
        "CASH TENDERED",
        "CHANGE",
    ):
        assert wording in printed, wording
    layouts = list(layouts_for("receipt").values())
    assert len(layouts) > 1
    # Every layout draws the same stacks; hospitality alone leaves out the returns stack (a cafe takes no
    # returns), which carries no slip wording.
    shared = [b for b in layouts[0]["body"]]
    for layout in layouts:
        assert all(block in shared for block in layout["body"])
        assert layout["body"][0] == shared[0]


def test_payment_terminal_declares_no_printed_slip_wording() -> None:
    """`payment_terminal` holds sampled values only; fixed wording is chrome.

    Re-adding a `customer_copy_text`/`slip_labels`-style key here would put a second copy of a
    printed string in a second file, which is what this sweep removed.
    """
    raw = yaml.safe_load(POOLS_PATH.read_text())["payment_terminal"]
    assert set(raw) == set(_REQUIRED_KEYS)
    for forbidden in ("customer_copy_text", "slip_labels", "approved_text", "footer_text", "labels"):
        assert forbidden not in raw
    dumped = yaml.safe_dump(raw)
    for wording in (
        "CUSTOMER COPY",
        "APPROVED",
        "Retain copy",
        "Terminal ID",
        "CASH TENDERED",
        "Transaction Ref",
    ):
        assert wording not in dumped, wording


def test_cash_step_and_extra_notes_reject_non_positive_ints(tmp_path: Path) -> None:
    """cash_tender_step and cash_extra_notes must be positive ints: 0, negatives, bools, floats fail."""
    for key in ("cash_tender_step", "cash_extra_notes"):
        for bad in (0, -5, True, 2.5, "5"):
            path = write_pools(tmp_path, lambda block, k=key, v=bad: block.update({k: v}))
            with pytest.raises(ValueError) as exc_info:
                load_terminal_pools(path)
            assert_diagnostic_error(exc_info.value)
            assert f"payment_terminal.{key}" in str(exc_info.value)
            path.unlink()


def test_cash_tender_step_drives_the_note_denomination() -> None:
    """A pools override with a larger step must change the derived tender."""
    base = with_weights({"Cash": 1})
    big = {**base, "cash_tender_step": 20, "cash_extra_notes": 1}
    small = {**base, "cash_tender_step": 5, "cash_extra_notes": 1}
    for case_id in case_ids(5):
        wide = derive(case_id, total="31.00", pools=big)
        narrow = derive(case_id, total="31.00", pools=small)
        assert wide.tendered == Decimal(40)
        assert narrow.tendered == Decimal(35)
        assert wide.change == Decimal(9)
        assert narrow.change == Decimal(4)


# --------------------------------------------------------------------------- derivation


def test_derivation_is_deterministic() -> None:
    """The same inputs always give the same PaymentDetails."""
    for case_id in case_ids(5):
        assert derive(case_id) == derive(case_id)
        assert derive(case_id, bank="VISA DEBIT PURCHASE X") == derive(
            case_id, bank="VISA DEBIT PURCHASE X"
        )


def test_different_cases_differ() -> None:
    """Different case ids produce different terminal values, not one shared slip."""
    slips = [derive(case_id) for case_id in case_ids(12)]
    assert len({s.transaction_ref for s in slips}) > 1
    assert len({s.atc for s in slips}) > 1
    assert len({s.masked_pan for s in slips}) > 1
    assert len(set(slips)) == len(slips)


def test_kind_matches_method() -> None:
    """Cash is kind 'cash', a wallet name is 'wallet', and everything else is 'card'."""
    pools = real_pools()
    pools["receipt_method_weights"] = {
        method: 1 for method in (*pools["schemes"], *pools["wallets"], "Cash")
    }
    seen: set[str] = set()
    for case_id in case_ids(200):
        details = derive(case_id, pools=pools)
        expected = (
            "cash"
            if details.method == "Cash"
            else "wallet"
            if details.method in pools["wallets"]
            else "card"
        )
        assert details.kind == expected, details.method
        seen.add(details.kind)
    assert seen == {"card", "wallet", "cash"}


def test_all_methods_appear_across_the_corpus() -> None:
    """The weighted pool must actually produce every configured method."""
    weights = load_terminal_pools()["receipt_method_weights"]
    expected = {method for method, weight in weights.items() if weight > 0}
    produced = {derive(case_id).method for case_id in case_ids(500)}
    assert produced == expected


def test_masked_pan_width_matches_scheme_pan_digits() -> None:
    """The masked PAN is pan_digits long: x-padding then four digits (15 for AMEX, 16 otherwise)."""
    pools = real_pools()
    for name, scheme in pools["schemes"].items():
        forced = with_weights({name: 1})
        details = derive(case_ids(1)[0], pools=forced)
        assert details.scheme_name == name
        assert len(details.masked_pan) == scheme["pan_digits"], name
        prefix, last4 = details.masked_pan[:-4], details.masked_pan[-4:]
        assert set(prefix) == {"x"}
        assert last4.isdigit()
    assert len(derive("CASE900", pools=with_weights({"AMEX": 1})).masked_pan) == 15


def test_account_type_comes_from_its_own_scheme() -> None:
    """The printed account type is one the slip's own scheme offers, never another scheme's."""
    pools = real_pools()
    for case_id in case_ids(120):
        details = derive(case_id, pools=pools)
        assert details.account_type in pools["schemes"][details.scheme_name]["account_types"]
    for name in ("Visa", "Mastercard", "AMEX"):
        assert {derive(c, pools=with_weights({name: 1})).account_type for c in case_ids(20)} == {"CR"}


def test_timestamp_is_twelve_hour_rendering_of_receipt_meta_time() -> None:
    """'DD/MM/YYYY' plus a 24-hour POS time renders as 'DD Mon YYYY at hh:mm AM/PM'."""
    assert derive_payment("CASE900", DATE, TOTAL, "14:07").timestamp == "05 Mar 2031 at 02:07 PM"
    assert derive_payment("CASE900", DATE, TOTAL, "00:05").timestamp == "05 Mar 2031 at 12:05 AM"
    assert derive_payment("CASE900", DATE, TOTAL, "12:30").timestamp == "05 Mar 2031 at 12:30 PM"
    entry = {"case_id": "CASE900", "layout": "x", "fields": {"INVOICE_DATE": DATE, "TOTAL_AMOUNT": TOTAL}}
    hour, minute = map(int, receipt_pos(entry, {})["POS_TIME"].split(":"))
    twelve = hour % 12 or 12
    suffix = "PM" if hour >= 12 else "AM"
    assert (
        receipt_payment(entry, {})["PAYMENT_TIMESTAMP"]
        == f"05 Mar 2031 at {twelve:02d}:{minute:02d} {suffix}"
    )


def test_cash_change_is_tendered_minus_total_and_tender_exceeds_total() -> None:
    """Cash change is tendered minus total, and the customer always tenders more than the bill."""
    for case_id in case_ids(15):
        for total in ("3.50", "37.35", "99.99", "137.73", "0.01"):
            details = cash_details(case_id, total)
            assert details.tendered is not None and details.change is not None
            assert details.tendered > Decimal(total)
            assert details.change == details.tendered - Decimal(total)
            assert details.change > 0


def test_exact_multiple_of_five_still_gives_change() -> None:
    """A total that is already a round note must never render CHANGE 0.00."""
    for case_id in case_ids(10):
        for total in ("5.00", "40.00", "100.00"):
            details = cash_details(case_id, total)
            assert details.change is not None and details.change >= Decimal(5)
            assert details.tendered is not None and details.tendered >= Decimal(total) + 5
            assert details.change != Decimal(0)


def test_card_details_have_no_cash_values() -> None:
    """Card and wallet slips carry no tender or change; the cash slip carries no card values."""
    pools = real_pools()
    pools["receipt_method_weights"] = {method: 1 for method in (*pools["schemes"], *pools["wallets"])}
    for case_id in case_ids(30):
        details = derive(case_id, pools=pools)
        assert details.kind in {"card", "wallet"}
        assert details.tendered is None and details.change is None
        assert details.masked_pan and details.aid
    cash = cash_details("CASE900")
    for value in (cash.scheme_name, cash.scheme_display, cash.aid, cash.masked_pan, cash.atc, cash.psn):
        assert value == ""


def test_derive_payment_carries_no_purchase_total_of_its_own() -> None:
    """The printed 'Purchase AUD' amount must never diverge from TOTAL_AMOUNT.

    The body binds {TOTAL_AMOUNT} directly, so PaymentDetails deliberately holds no derived copy
    for it to drift from: the structural version of the invariant the older
    `test_purchase_total_is_the_scored_total_verbatim` used to assert about a field nothing read.
    """
    names = {f.name for f in dataclasses.fields(PaymentDetails)}
    assert not {n for n in names if "total" in n or "purchase" in n or "amount" in n}
    pools = real_pools()
    pools["receipt_method_weights"] = {"Visa": 1}
    # A card slip is independent of the total, so no value on it can mirror the amount.
    assert derive("CASE900", total="12.34", pools=pools) == derive("CASE900", total="999.99", pools=pools)
    assert "PAYMENT_PURCHASE_TOTAL" not in receipt_payment(
        {"case_id": "CASE900", "layout": "x", "fields": {"INVOICE_DATE": DATE, "TOTAL_AMOUNT": TOTAL}}, {}
    )


# --------------------------------------------------------------------------- rendering


def test_card_block_is_taller_than_cash_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """The card/wallet slip draws many more lines than the two-line cash slip."""
    patch_pools(monkeypatch, with_weights({"Visa": 1}))
    card_entry = injected_entry({**DSL_ENTRY, "case_id": FABRICATED_ENTRY_ID})
    patch_pools(monkeypatch, with_weights({"Cash": 1}))
    cash_entry = injected_entry({**DSL_ENTRY, "case_id": FABRICATED_ENTRY_ID})
    assert card_entry["fields"]["PAYMENT_KIND"] == "card"
    assert cash_entry["fields"]["PAYMENT_KIND"] == "cash"
    slip = blocks_whose_when_is_in(SLIP_WHEN_KEYS)
    cash_blocks = blocks_whose_when_is_in(CASH_WHEN_KEYS)
    card_height = render_lines(slip + cash_blocks, card_entry)
    cash_height = render_lines(slip + cash_blocks, cash_entry)
    assert card_height > cash_height > 0
    assert card_height >= 5 * cash_height


def test_cash_block_draws_only_two_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cash advances exactly two line heights (tendered + change), nothing more."""
    patch_pools(monkeypatch, with_weights({"Cash": 1}))
    entry = injected_entry({**DSL_ENTRY, "case_id": FABRICATED_ENTRY_ID})
    layout = real_layout(blocks_whose_when_is_in(SLIP_WHEN_KEYS | CASH_WHEN_KEYS))
    line = layout["defaults"]["line_advance"]["body"]
    assert render(layout, entry) == 2 * line
    assert len(blocks_whose_when_is_in(CASH_WHEN_KEYS)) == 2


def test_a_whole_cash_receipt_page_renders_on_every_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cash slip's only whole-page oracle.

    `render_lines` draws the two cash blocks onto a scratch canvas with the provider chain
    switched off. What it cannot see is the page: a cash payment suppresses every
    `when: PAYMENT_AID` block, collapsing the slip from roughly twenty lines to two, and that
    collapse meets the surrounding rules and spacers and then `render_receipt`'s crop-to-content
    height. No corpus receipt reaches this, so this drives the real body through the real renderer
    with the pool patched, and asserts the two labels actually land on the cropped page rather
    than below its bottom edge.
    """
    patch_pools(monkeypatch, with_weights({"Cash": 1}))
    for layout_id, base in entry_per_layout("receipt").items():
        entry = unlinked_entry(layout_id, base)
        assert receipt_payment(entry, {})["PAYMENT_KIND"] == "cash"
        layout = layouts_for("receipt")[layout_id]
        geometry: dict = {}
        image = render_receipt(entry, layout, distractors=False, geometry_out=geometry)
        assert image.height == geometry["height"] < layout["canvas_ceiling"]
        # The footer rule and text follow the cash lines, so ink must reach the page's last rows,
        # and the cash block must not have been cropped away: the page is taller than the same
        # receipt with every payment block removed.
        stripped = copy.deepcopy(layout)
        stripped["body"] = without_blocks(stripped["body"], SLIP_WHEN_KEYS | CASH_WHEN_KEYS)
        without = render_receipt(entry, stripped, distractors=False)
        line = layout["defaults"]["line_advance"]["body"]
        assert image.height - without.height >= 2 * line, layout_id
        # Ink exists in the band just above the footer: the labels landed on the page.
        band = image.crop((0, without.height - line, image.width, image.height)).convert("L")
        assert cast(int, band.getextrema()[0]) < 128, layout_id


def test_a_cash_receipt_page_is_shorter_than_the_same_receipt_paid_by_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collapse is real, and it is the crop that shows it."""
    for layout_id, base in entry_per_layout("receipt").items():
        entry = unlinked_entry(layout_id, base)
        layout = layouts_for("receipt")[layout_id]
        patch_pools(monkeypatch, with_weights({"Cash": 1}))
        cash_page = render_receipt(entry, layout, distractors=False)
        patch_pools(monkeypatch, with_weights({"Visa": 1}))
        card_page = render_receipt(entry, layout, distractors=False)
        assert cash_page.height < card_page.height, layout_id


def test_block_renders_for_every_layout_and_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """No layout/method combination may raise (FitError included)."""
    pools = real_pools()
    methods = [*pools["schemes"], *pools["wallets"], "Cash"]
    for layout_id, base in entry_per_layout("receipt").items():
        entry = unlinked_entry(layout_id, base)
        layout = layouts_for("receipt")[layout_id]
        for method in methods:
            patch_pools(monkeypatch, with_weights({method: 1}))
            assert receipt_payment(entry, {})["PAYMENT_METHOD"] == method
            image = render_receipt(entry, layout, distractors=False)
            assert image.height > 0, (layout_id, method)


def test_every_corpus_receipt_renders_under_every_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broader fit guard than the per-block one above: whole pages, real data.

    The corpus assigns one layout per receipt, so a fit failure that only appears when a long
    supplier name lands on the narrowest layout would otherwise go unseen until a reseed happened
    to pair them.
    """
    layouts = layouts_for("receipt")
    receipts = corpus_entries("receipt")
    assert receipts
    for entry in receipts.values():
        for layout_id, layout in layouts.items():
            image = render_receipt({**entry, "layout": layout_id}, layout, distractors=False)
            assert image.width == layout["width"]
            assert 0 < image.height < layout["canvas_ceiling"], (entry["case_id"], layout_id)


def test_every_layout_has_payment_budgets() -> None:
    """Every receipt layout budgets the two payment text kinds the slip blocks name."""
    used = {
        b["budget"]
        for b in receipt_body()
        if "budget" in b and str(b.get("when", "")).startswith("PAYMENT")
    }
    assert used == {"PAYMENT_ACQUIRER", "PAYMENT_LINE"}
    for layout_id, layout in layouts_for("receipt").items():
        for name in used:
            budget = field_budget(layout, layout_id, name, layout_path=RECEIPT_LAYOUT_PATH)
            assert budget["width"] <= layout["width"]


def test_receipt_render_grows_by_the_block() -> None:
    """Rendering with the payment section must add the block's lines.

    Compares against the same entry rendered with the slip blocks stripped from its layout body,
    so the assertion measures the block itself rather than a fixed pixel threshold (receipt
    heights vary with line-item count). Only the card path is exercised through the corpus: every
    receipt is linked to a bank transaction, so no ground-truth receipt is cash. The cash block's
    height is covered directly by test_cash_block_draws_only_two_lines.
    """
    for layout_id, base in entry_per_layout("receipt").items():
        layout = layouts_for("receipt")[layout_id]
        stripped = copy.deepcopy(layout)
        stripped["body"] = without_blocks(stripped["body"], SLIP_WHEN_KEYS | CASH_WHEN_KEYS)
        assert len(flat_body(stripped["body"])) < len(flat_body(layout["body"]))
        full = render_receipt(base, layout, distractors=False)
        bare = render_receipt(base, stripped, distractors=False)
        slip_lines = len(blocks_whose_when_is_in(SLIP_WHEN_KEYS))
        assert (
            full.height - bare.height >= (slip_lines - 5) * layout["defaults"]["line_advance"]["body"] / 2
        )
        assert full.height > bare.height, layout_id


def test_receipt_module_has_no_hardcoded_methods() -> None:
    """generators/receipt.py names no payment method and does not derive payments itself."""
    source = (REPO_ROOT / "generators" / "receipt.py").read_text()
    tree = ast.parse(source)
    pools = load_terminal_pools()
    methods = {*pools["schemes"], *pools["wallets"], "Cash"}
    strings = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    docstrings = {
        ast.get_docstring(tree),
        ast.get_docstring(next(n for n in tree.body if isinstance(n, ast.FunctionDef))),
    }
    code_strings = strings - {d for d in docstrings if d}
    assert not code_strings & methods
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "derive_payment" not in imported
    assert "generators.payment_block" not in modules


# --------------------------------------------------------------------------- bank description mapping


def test_bank_description_maps_to_scheme() -> None:
    """A bank description resolves to the scheme named by its configured prefix."""
    cfg = load_terminal_pools()
    for prefix, scheme in cfg["bank_description_methods"].items():
        assert method_from_bank_description(f"{prefix} PURCHASE SQ *INVENTED Sydney AU", cfg) == scheme
    assert method_from_bank_description("VISA DEBIT PURCHASE X", cfg) == "Visa Debit"
    assert method_from_bank_description("EFTPOS PURCHASE X", cfg) == "EFTPOS"


def test_longest_prefix_wins() -> None:
    """A longer, more specific prefix must beat a shorter one."""
    cfg = real_pools()
    cfg["bank_description_methods"] = {
        "MASTERCARD": "Mastercard",
        "MASTERCARD DEBIT": "Mastercard Debit",
        "VISA": "Visa",
        "VISA DEBIT": "Visa Debit",
    }
    assert method_from_bank_description("MASTERCARD DEBIT PURCHASE", cfg) == "Mastercard Debit"
    assert method_from_bank_description("MASTERCARD PURCHASE", cfg) == "Mastercard"
    assert method_from_bank_description("VISA DEBIT PURCHASE", cfg) == "Visa Debit"
    assert method_from_bank_description("VISA PURCHASE", cfg) == "Visa"
    cfg["bank_description_methods"] = dict(reversed(list(cfg["bank_description_methods"].items())))
    assert method_from_bank_description("MASTERCARD DEBIT PURCHASE", cfg) == "Mastercard Debit"


def test_unmapped_description_is_four_element_diagnostic() -> None:
    """A description matching no prefix fails with a diagnostic listing the known prefixes."""
    cfg = load_terminal_pools()
    with pytest.raises(ValueError) as exc_info:
        method_from_bank_description("BPAY TO INVENTED PTY LTD", cfg)
    assert_diagnostic_error(exc_info.value)
    assert "BPAY TO INVENTED PTY LTD" in str(exc_info.value)
    assert "bank_description_methods" in str(exc_info.value)


def test_mapped_schemes_exist() -> None:
    """Every bank-description prefix points at a configured scheme."""
    cfg = load_terminal_pools()
    assert set(cfg["bank_description_methods"].values()) <= set(cfg["schemes"])


def test_debit_schemes_are_configured() -> None:
    """The debit schemes the bank rows name exist, with debit account types and debit wording."""
    schemes = load_terminal_pools()["schemes"]
    for name in ("Visa Debit", "Mastercard Debit"):
        assert name in schemes
        assert "DEBIT" in schemes[name]["display"]
        assert "CR" not in schemes[name]["account_types"]
    assert "EFTPOS" in schemes
    linked = {method_from_bank_description(d, load_terminal_pools()) for d in load_link_index().values()}
    assert linked <= set(schemes)


def test_zero_weight_is_allowed_but_all_zero_is_not(tmp_path: Path) -> None:
    """Cash: 0 must be accepted; a pool with no positive weight must not."""
    path = write_pools(tmp_path, lambda block: block["receipt_method_weights"].update({"Cash": 0}))
    assert load_terminal_pools(path)["receipt_method_weights"]["Cash"] == 0
    zeros_path = write_pools(
        tmp_path / "zeros",
        lambda block: block.update({"receipt_method_weights": {"Visa": 0, "Cash": 0}}),
    )
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(zeros_path)
    assert_diagnostic_error(exc_info.value)
    assert "receipt_method_weights" in str(exc_info.value)


def test_negative_weight_is_rejected(tmp_path: Path) -> None:
    """A negative method weight is a configuration error naming the method."""
    path = write_pools(tmp_path, lambda block: block["receipt_method_weights"].update({"Visa": -1}))
    with pytest.raises(ValueError) as exc_info:
        load_terminal_pools(path)
    assert_diagnostic_error(exc_info.value)
    assert "Visa" in str(exc_info.value)


# --------------------------------------------------------------------------- link index


def test_link_index_covers_every_receipt_stem() -> None:
    """Every ground-truth receipt has a link-index entry, keyed `{case_id}_{layout}`."""
    receipts = corpus_entries("receipt")
    index = load_link_index()
    stems = {f"{e['case_id']}_{e['layout']}" for e in receipts.values()}
    assert stems
    assert stems <= set(index)
    assert set(index) == stems


def test_link_index_excludes_invoices() -> None:
    """Invoice links are skipped: invoices render no payment block."""
    raw = yaml.safe_load(LINKS_PATH.read_text())
    assert any("_receipt" not in str(name) for name in raw), "fixture would make this vacuous"
    assert all("_receipt" in stem for stem in load_link_index())
    invoice_stems = {str(n).removesuffix(".png") for n in raw if "_receipt" not in str(n)}
    assert not invoice_stems & set(load_link_index())


def test_link_index_missing_file_raises(tmp_path: Path) -> None:
    """A missing links file raises FileNotFoundError with the four elements."""
    missing = tmp_path / "transaction_links.yml"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_link_index(missing)
    assert_diagnostic_error(exc_info.value)
    assert str(missing) in str(exc_info.value)


def test_bank_description_forces_the_scheme() -> None:
    """A linked receipt prints the scheme its bank row names, whatever the weighted pool says."""
    pools = real_pools()
    for case_id in case_ids(40):
        for description, scheme in (
            ("VISA DEBIT PURCHASE SQ *INVENTED Sydney AU", "Visa Debit"),
            ("MASTERCARD DEBIT PURCHASE INVENTED", "Mastercard Debit"),
            ("EFTPOS PURCHASE INVENTED", "EFTPOS"),
        ):
            details = derive(case_id, pools=pools, bank=description)
            assert details.scheme_name == scheme
            assert details.scheme_display == pools["schemes"][scheme]["display"]
            assert details.kind in {"card", "wallet"}


def test_forced_scheme_keeps_hash_derived_values() -> None:
    """Only the scheme changes; the rest of the slip stays hash-derived."""
    for case_id in case_ids(10):
        visa = derive(case_id, bank="VISA DEBIT PURCHASE INVENTED")
        eftpos = derive(case_id, bank="EFTPOS PURCHASE INVENTED")
        for attr in (
            "method",
            "kind",
            "acquirer",
            "psn",
            "atc",
            "terminal_id",
            "transaction_ref",
            "timestamp",
        ):
            if attr == "method":
                continue
            assert getattr(visa, attr) == getattr(eftpos, attr), attr
        assert visa.masked_pan[-4:] == eftpos.masked_pan[-4:]
        assert visa.scheme_name != eftpos.scheme_name


def test_account_type_belongs_to_the_forced_scheme() -> None:
    """With the scheme forced by the bank, the account type is still one of that scheme's own."""
    pools = real_pools()
    for case_id in case_ids(60):
        for description, scheme in (
            ("EFTPOS PURCHASE X", "EFTPOS"),
            ("VISA DEBIT PURCHASE X", "Visa Debit"),
        ):
            details = derive(case_id, pools=pools, bank=description)
            assert details.account_type in pools["schemes"][scheme]["account_types"]


def test_wallet_presentation_keeps_the_bank_scheme() -> None:
    """A wallet presentation must not change the scheme the bank row names."""
    pools = real_pools()
    pools["wallet_presentation_weights"] = {"none": 0, "Apple Pay": 1}
    for case_id in case_ids(10):
        details = derive(case_id, pools=pools, bank="VISA DEBIT PURCHASE INVENTED")
        assert details.kind == "wallet"
        assert details.method == "Apple Pay"
        assert details.wallet_label == pools["wallets"]["Apple Pay"]
        assert details.scheme_name == "Visa Debit"
        assert details.scheme_display == pools["schemes"]["Visa Debit"]["display"]
        assert details.entry_mode == pools["entry_modes"]["wallet"]
    plain = {**pools, "wallet_presentation_weights": {"none": 1}}
    card = derive("CASE900", pools=plain, bank="VISA DEBIT PURCHASE INVENTED")
    assert card.kind == "card" and card.scheme_name == "Visa Debit"


def test_unlinked_receipt_falls_back_to_the_weighted_pick() -> None:
    """Without a description the derivation is byte-identical to before.

    The oracle is the documented rule itself: expand the weights in sorted-name order and index the
    pool with hex chars 10-14 of the sha256 of `{case_id}:pos:{date}`.
    """
    pools = real_pools()
    pool = [
        m
        for name in sorted(pools["receipt_method_weights"])
        for m in [name] * pools["receipt_method_weights"][name]
    ]
    for case_id in case_ids(30):
        digest = hashlib.sha256(f"{case_id}:pos:{DATE}".encode()).hexdigest()
        details = derive(case_id)
        assert details.method == pool[int(digest[10:14], 16) % len(pool)]
        assert details.acquirer == pools["acquirers"][int(digest[14:16], 16) % len(pools["acquirers"])]
        assert details == derive_payment(case_id, DATE, TOTAL, TIME, bank_description=None)
        assert details == derive_payment(case_id, DATE, TOTAL, TIME, pools=pools)


def test_cash_never_appears_now_that_its_weight_is_zero() -> None:
    """With Cash weighted 0 no derivation, linked or not, yields cash."""
    assert load_terminal_pools()["receipt_method_weights"]["Cash"] == 0
    for case_id in case_ids(300):
        assert derive(case_id).kind != "cash"
        assert derive(case_id, bank="EFTPOS PURCHASE X").kind != "cash"


def test_every_linked_receipt_matches_its_bank_row() -> None:
    """The invariant this whole change exists to establish.

    The scheme printed on every receipt is the one its linked bank row names, read through the
    receipt_payment provider exactly as the renderer reads it.
    """
    cfg = load_terminal_pools()
    index = load_link_index()
    checked = 0
    for entry in corpus_entries("receipt").values():
        description = index[f"{entry['case_id']}_{entry['layout']}"]
        scheme = cfg["schemes"][method_from_bank_description(description, cfg)]
        out = receipt_payment(entry, {})
        assert out["PAYMENT_SCHEME_DISPLAY"] == scheme["display"], entry["case_id"]
        assert out["PAYMENT_AID"] == scheme["aid"], entry["case_id"]
        assert out["PAYMENT_ACCOUNT_TYPE"] in scheme["account_types"]
        checked += 1
    assert checked == len(index)


def test_no_linked_receipt_is_cash() -> None:
    """No corpus receipt is a cash payment: a bank row cannot be cash."""
    for entry in corpus_entries("receipt").values():
        out = receipt_payment(entry, {})
        assert out["PAYMENT_KIND"] != "cash", entry["case_id"]
        assert out["PAYMENT_TENDERED"] == "NOT_FOUND"
        assert out["PAYMENT_AID"] != "NOT_FOUND"


def test_renderer_actually_consults_the_link_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the bank scheme reaches the page, not just the dataclass.

    Renders a case whose linked scheme differs from the scheme it would pick unlinked, with the
    link index live and then stubbed empty. If the renderer ignored the links, both renders would
    be byte-identical.
    """
    cfg = load_terminal_pools()
    index = load_link_index()
    chosen = None
    for entry in corpus_entries("receipt").values():
        linked = method_from_bank_description(index[f"{entry['case_id']}_{entry['layout']}"], cfg)
        time_str = receipt_pos(entry, {})["POS_TIME"]
        unlinked = derive_payment(
            entry["case_id"], entry["fields"]["INVOICE_DATE"], entry["fields"]["TOTAL_AMOUNT"], time_str
        )
        if unlinked.scheme_name != linked:
            chosen = entry
            break
    assert chosen is not None, "no corpus receipt's linked scheme differs from its unlinked pick"
    layout = layouts_for("receipt")[chosen["layout"]]

    live = render_receipt(chosen, layout, distractors=False)
    live_display = receipt_payment(chosen, {})["PAYMENT_SCHEME_DISPLAY"]
    monkeypatch.setattr(fp, "load_link_index", lambda *a, **k: {})
    stubbed = render_receipt(chosen, layout, distractors=False)
    stub_display = receipt_payment(chosen, {})["PAYMENT_SCHEME_DISPLAY"]

    assert live_display != stub_display
    assert not pixels_equal(live, stubbed)


# --------------------------------------------------------------------------- validate


@pytest.mark.usefixtures("stub_typeset_pass")
def test_validate_passes_on_the_shipped_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """`validate` accepts the shipped corpus, including every linked bank description."""
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output


@pytest.mark.usefixtures("stub_typeset_pass")
def test_validate_reports_unmapped_bank_descriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A link the mapping cannot resolve must fail validation, not render wrongly."""
    import generators.pipeline as pipeline

    monkeypatch.chdir(REPO_ROOT)
    stem = "CASE999_receipt_invented"
    monkeypatch.setattr(pipeline, "load_link_index", lambda *a, **k: {stem: "BPAY TO INVENTED PTY LTD"})
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 1, result.output
    assert "Validation failed" in result.output
    assert stem in result.output
    assert "maps to no card scheme" in " ".join(result.output.split())
