"""Field providers derive off-ground-truth values deterministically per case."""

from decimal import Decimal

import pytest

from generators import payment_block
from generators.layout_dsl import provider_registry as registry
from generators.layout_dsl.field_providers import (
    FieldProviderError,
    apply_field_providers,
    computed_totals,
    field_provider,
    field_provider_emits,
    field_provider_names,
    field_provider_param_keys,
    get_field_provider,
    receipt_payment,
    receipt_pos,
)
from generators.layout_dsl.schema import LayoutSchemaError, _validate_field_providers
from generators.loader import load_ground_truth
from generators.payment_block import derive_payment, load_link_index, load_pos_pools, load_terminal_pools
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import real_layout, render

# Invented values: CASE999 and this date appear nowhere in ground_truth/.
ENTRY = {"case_id": "CASE999", "fields": {"INVOICE_DATE": "01/02/2031"}}

PAYMENT_ENTRY = {
    "case_id": "CASE999",
    "layout": "receipt_thermal_80mm",
    "fields": {"INVOICE_DATE": "01/02/2031", "TOTAL_AMOUNT": "40.00", "GST_AMOUNT": "3.64"},
}


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give a test private copies of the provider registries, so registrations do not leak."""
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_PARAM_KEYS", dict(registry._PARAM_KEYS))
    monkeypatch.setattr(registry, "_EMITS", dict(registry._EMITS))
    monkeypatch.setattr(registry, "_DISTRACTOR", dict(registry._DISTRACTOR))


def test_receipt_pos_is_deterministic_per_case() -> None:
    assert receipt_pos(ENTRY, {}) == receipt_pos(ENTRY, {})


def test_receipt_pos_varies_with_the_case() -> None:
    other = {**ENTRY, "case_id": "CASE998"}
    assert receipt_pos(ENTRY, {}) != receipt_pos(other, {})


def test_receipt_pos_emits_only_declared_keys() -> None:
    assert set(receipt_pos(ENTRY, {})) <= set(field_provider_emits("receipt_pos"))


def test_unknown_provider_is_a_four_part_diagnostic() -> None:
    with pytest.raises(FieldProviderError) as exc_info:
        get_field_provider("no_such_provider")
    assert_diagnostic_error(exc_info.value)


def test_provider_output_is_merged_into_entry_fields() -> None:
    """Provider output lands in `fields` beside the ground-truth values, without mutating the input."""
    layout = {"field_providers": [{"name": "receipt_pos", "params": {}}]}
    merged = apply_field_providers(layout, ENTRY, distractors=False)
    assert merged["fields"]["INVOICE_DATE"] == "01/02/2031"
    assert merged["fields"]["POS_STAFF"] == receipt_pos(ENTRY, {})["POS_STAFF"]
    assert merged["case_id"] == "CASE999"
    assert "POS_STAFF" not in ENTRY["fields"]


def test_provider_emitting_an_undeclared_key_fails(scratch_registry: None) -> None:
    """`emits` is the contract validate checks placeholders against; a stray key makes that a lie."""

    @field_provider("rogue_test_provider", emits=("ROGUE_DECLARED",))
    def rogue(entry: dict, params: dict) -> dict[str, str]:
        return {"ROGUE_DECLARED": "a", "ROGUE_UNDECLARED": "b"}

    layout = {"field_providers": [{"name": "rogue_test_provider"}]}
    with pytest.raises(FieldProviderError) as exc_info:
        apply_field_providers(layout, ENTRY, distractors=False)
    assert_diagnostic_error(exc_info.value)
    assert "ROGUE_UNDECLARED" in str(exc_info.value)


def test_emit_colliding_with_a_scored_column_is_rejected(scratch_registry: None) -> None:
    """A derived presentation value must never be mistaken for extraction ground truth."""
    with pytest.raises(FieldProviderError) as exc_info:

        @field_provider("collider_test_provider", emits=("TOTAL_AMOUNT",))
        def collider(entry: dict, params: dict) -> dict[str, str]:
            return {}

    assert_diagnostic_error(exc_info.value)
    assert "TOTAL_AMOUNT" in str(exc_info.value)
    assert "collider_test_provider" not in field_provider_names()


def test_apply_field_providers_merges_multiple_providers() -> None:
    """Two providers with disjoint emits on one layout both land."""
    layout = {"field_providers": [{"name": "receipt_pos"}, {"name": "computed_totals"}]}
    merged = apply_field_providers(layout, PAYMENT_ENTRY, distractors=False)["fields"]
    assert merged["POS_TIME"] == receipt_pos(PAYMENT_ENTRY, {})["POS_TIME"]
    assert merged["SUBTOTAL_AMOUNT"] == "36.36"


def test_apply_field_providers_rejects_two_providers_emitting_the_same_key(
    scratch_registry: None,
) -> None:
    """A silent dict.update() overwrite would make one value vanish; the merge-time check refuses it."""

    @field_provider("dup_test_provider", emits=("POS_TIME",))
    def dup(entry: dict, params: dict) -> dict[str, str]:
        return {"POS_TIME": "00:00"}

    layout = {"field_providers": [{"name": "receipt_pos"}, {"name": "dup_test_provider"}]}
    with pytest.raises(FieldProviderError) as exc_info:
        apply_field_providers(layout, ENTRY, distractors=False)
    assert_diagnostic_error(exc_info.value)
    assert "POS_TIME" in str(exc_info.value)
    assert "receipt_pos" in str(exc_info.value)


def test_apply_field_providers_with_no_providers_returns_an_unchanged_copy() -> None:
    """With nothing declared the result equals the entry but is a distinct object."""
    merged = apply_field_providers({"field_providers": []}, ENTRY, distractors=False)
    assert merged == ENTRY
    assert merged is not ENTRY
    assert merged["fields"] is not ENTRY["fields"]


def test_apply_field_providers_rejects_an_unregistered_provider_name() -> None:
    with pytest.raises(FieldProviderError) as exc_info:
        apply_field_providers(
            {"field_providers": [{"name": "nope_not_registered"}]}, ENTRY, distractors=False
        )
    assert_diagnostic_error(exc_info.value)
    assert "nope_not_registered" in str(exc_info.value)


def test_get_field_provider_rejects_unknown_name_and_lists_known() -> None:
    with pytest.raises(FieldProviderError) as exc_info:
        get_field_provider("no_such_provider")
    message = str(exc_info.value)
    assert "no_such_provider" in message
    for known in field_provider_names():
        assert known in message


def test_field_provider_names_includes_registered() -> None:
    names = field_provider_names()
    assert {"receipt_pos", "receipt_payment", "computed_totals"} <= set(names)
    assert names == sorted(names)


def test_field_provider_rejects_duplicate_registration(scratch_registry: None) -> None:
    with pytest.raises(FieldProviderError) as exc_info:

        @field_provider("receipt_pos", emits=("ANOTHER_KEY",))
        def again(entry: dict, params: dict) -> dict[str, str]:
            return {}

    assert_diagnostic_error(exc_info.value)
    assert "already registered" in str(exc_info.value)
    assert get_field_provider("receipt_pos") is receipt_pos


def test_field_provider_emits_and_param_keys_return_declared_values(scratch_registry: None) -> None:
    @field_provider("declared_test_provider", params=frozenset({"alpha"}), emits=("ZZ_ONE", "ZZ_TWO"))
    def declared(entry: dict, params: dict) -> dict[str, str]:
        return {}

    assert field_provider_emits("declared_test_provider") == ("ZZ_ONE", "ZZ_TWO")
    assert field_provider_param_keys("declared_test_provider") == frozenset({"alpha"})
    assert field_provider_emits("computed_totals") == ("SUBTOTAL_AMOUNT",)


def test_field_provider_emits_and_param_keys_are_empty_for_unknown_name() -> None:
    """Unknown names degrade to empty rather than raising; schema.py reports them itself."""
    assert field_provider_emits("no_such_provider") == ()
    assert field_provider_param_keys("no_such_provider") == frozenset()


def test_when_admits_a_block_whose_field_is_a_provider_emitted_value() -> None:
    """POS_STAFF exists nowhere in `entry["fields"]`; the block is admitted because providers ran first."""
    assert "POS_STAFF" not in PAYMENT_ENTRY["fields"]
    body = [{"type": "spacer", "height": 7, "when": "POS_STAFF"}]
    layout = real_layout(body, [{"name": "receipt_pos", "params": {}}])
    assert render(layout, PAYMENT_ENTRY, y=10) == 17


def test_when_suppresses_a_block_whose_field_absent_even_with_a_provider_declared() -> None:
    """Declaring a provider does not make every field present, only the keys it emits."""
    body = [{"type": "spacer", "height": 7, "when": "NOT_A_PROVIDER_KEY"}]
    layout = real_layout(body, [{"name": "receipt_pos", "params": {}}])
    assert render(layout, PAYMENT_ENTRY, y=10) == 10


def test_receipt_pos_matches_the_pinned_derivation() -> None:
    """Golden values for the invented entry: the derivation is part of the corpus contract."""
    assert receipt_pos(ENTRY, {}) == {
        "POS_TIME": "14:08",
        "POS_REGISTER": "05",
        "POS_STAFF": "Isla",
        "RECEIPT_NUMBER": "R-2DA506",
    }


def test_receipt_pos_stays_within_its_declared_pools() -> None:
    """Every derived value lands inside the ranges `pos_terminal` declares."""
    pools = load_pos_pools()
    for index in range(60):
        out = receipt_pos({"case_id": f"CASE9{index:02d}", "fields": {"INVOICE_DATE": "01/02/2031"}}, {})
        hour, minute = (int(part) for part in out["POS_TIME"].split(":"))
        assert pools["hour_min"] <= hour < pools["hour_min"] + pools["hour_span"]
        assert 0 <= minute < 60
        register = int(out["POS_REGISTER"])
        assert pools["register_min"] <= register < pools["register_min"] + pools["register_span"]
        assert out["POS_STAFF"] in pools["staff_names"]
        assert out["RECEIPT_NUMBER"].startswith(pools["receipt_number_prefix"])
        digits = out["RECEIPT_NUMBER"].removeprefix(pools["receipt_number_prefix"])
        assert len(digits) == pools["receipt_number_digest_length"]


def test_receipt_pos_is_registered_with_its_declared_emits() -> None:
    assert get_field_provider("receipt_pos") is receipt_pos
    assert field_provider_emits("receipt_pos") == (
        "POS_TIME",
        "POS_REGISTER",
        "POS_STAFF",
        "RECEIPT_NUMBER",
    )
    assert field_provider_param_keys("receipt_pos") == frozenset()


def test_a_pools_key_param_is_now_rejected_rather_than_documented() -> None:
    """Neither provider declares `pools_key`, so the schema rejects it if it is ever re-added."""
    for name in ("receipt_pos", "receipt_payment"):
        layout = {"field_providers": [{"name": name, "params": {"pools_key": "pos_terminal"}}]}
        with pytest.raises(LayoutSchemaError) as exc_info:
            _validate_field_providers(layout, layout_id="demo", layout_path="demo.yml")
        assert_diagnostic_error(exc_info.value)
        assert "pools_key" in str(exc_info.value)


def test_receipt_pos_runs_through_apply_field_providers() -> None:
    """The layout-facing path, not just direct provider invocation."""
    layout = {"field_providers": [{"name": "receipt_pos", "params": {}}]}
    merged = apply_field_providers(layout, ENTRY, distractors=False)
    assert {k: merged["fields"][k] for k in field_provider_emits("receipt_pos")} == receipt_pos(ENTRY, {})


def test_computed_totals_matches_the_legacy_subtraction() -> None:
    """Matches the legacy `str(Decimal(total) - Decimal(gst))`."""
    for total, gst in (("40.00", "3.64"), ("137.73", "12.52"), ("10", "0.91")):
        entry = {"fields": {"TOTAL_AMOUNT": total, "GST_AMOUNT": gst}}
        assert computed_totals(entry, {}) == {"SUBTOTAL_AMOUNT": str(Decimal(total) - Decimal(gst))}


def test_computed_totals_emits_nothing_when_a_required_field_is_absent_or_not_found() -> None:
    cases = [
        {"TOTAL_AMOUNT": "40.00"},
        {"GST_AMOUNT": "3.64"},
        {"TOTAL_AMOUNT": "40.00", "GST_AMOUNT": "NOT_FOUND"},
        {"TOTAL_AMOUNT": "NOT_FOUND", "GST_AMOUNT": "3.64"},
        {"TOTAL_AMOUNT": "", "GST_AMOUNT": "3.64"},
    ]
    for fields in cases:
        assert computed_totals({"fields": fields}, {}) == {}


def test_computed_totals_is_registered_with_its_declared_emits() -> None:
    assert get_field_provider("computed_totals") is computed_totals
    assert field_provider_emits("computed_totals") == ("SUBTOTAL_AMOUNT",)
    assert field_provider_param_keys("computed_totals") == frozenset()


def test_computed_totals_absent_case_does_not_violate_the_undeclared_emit_check() -> None:
    """`emits` is an upper bound: a NOT_FOUND GST merges cleanly with no SUBTOTAL_AMOUNT key."""
    entry = {"case_id": "CASE999", "fields": {"TOTAL_AMOUNT": "40.00", "GST_AMOUNT": "NOT_FOUND"}}
    merged = apply_field_providers(
        {"field_providers": [{"name": "computed_totals"}]}, entry, distractors=False
    )
    assert "SUBTOTAL_AMOUNT" not in merged["fields"]
    assert merged["fields"]["GST_AMOUNT"] == "NOT_FOUND"


def _receipt_entries() -> list[dict]:
    raw = load_ground_truth(REPO_ROOT / "ground_truth" / "receipts.yml")
    return [{**entry, "case_id": str(case_id)} for case_id, entry in raw.items()]


def test_receipt_payment_matches_derive_payment() -> None:
    """Phase A parity: the provider reproduces derive_payment exactly for every corpus entry."""
    links = load_link_index()
    entries = _receipt_entries()
    assert len(entries) == 55
    for entry in entries:
        fields = entry["fields"]
        time_str = receipt_pos(entry, {})["POS_TIME"]
        details = derive_payment(
            entry["case_id"],
            fields["INVOICE_DATE"],
            fields["TOTAL_AMOUNT"],
            time_str,
            bank_description=links.get(f"{entry['case_id']}_{entry['layout']}"),
        )
        out = receipt_payment(entry, {})
        assert out["PAYMENT_KIND"] == details.kind
        assert out["PAYMENT_METHOD"] == details.method
        assert out["PAYMENT_TIMESTAMP"] == details.timestamp
        assert out["PAYMENT_ACQUIRER"] == details.acquirer
        assert out["PAYMENT_SCHEME_DISPLAY"] == (details.scheme_display or "NOT_FOUND")
        assert out["PAYMENT_MASKED_PAN"] == (details.masked_pan or "NOT_FOUND")
        assert out["PAYMENT_AID"] == (details.aid or "NOT_FOUND")
        assert out["PAYMENT_TERMINAL_ID"] == (details.terminal_id or "NOT_FOUND")
        assert out["PAYMENT_TRANSACTION_REF"] == (details.transaction_ref or "NOT_FOUND")
        assert out["PAYMENT_WALLET_LABEL"] == (details.wallet_label or "NOT_FOUND")


def test_receipt_payment_never_emits_a_purchase_total() -> None:
    """The slip binds {TOTAL_AMOUNT} directly; a second copy of a scored value could drift."""
    assert "PAYMENT_PURCHASE_TOTAL" not in field_provider_emits("receipt_payment")
    out = receipt_payment(PAYMENT_ENTRY, {})
    assert "PAYMENT_PURCHASE_TOTAL" not in out
    assert not any("TOTAL" in key.replace("TERMINAL", "") for key in out)


def test_receipt_payment_exercises_at_least_one_linked_receipt() -> None:
    """The corpus is not accidentally empty of links, so the parity test covers the linked path."""
    links = load_link_index()
    linked = [e for e in _receipt_entries() if f"{e['case_id']}_{e['layout']}" in links]
    assert linked


def _patched_weights(monkeypatch: pytest.MonkeyPatch, weights: dict[str, int]) -> None:
    pools = {**load_terminal_pools(), "receipt_method_weights": weights}
    monkeypatch.setattr(payment_block, "load_terminal_pools", lambda *a, **k: pools)


def test_cash_receipts_suppress_the_card_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cash payment emits NOT_FOUND for every card key. A fabricated case id is absent from the link
    index, so a cash-weighted pool decides the method."""
    _patched_weights(monkeypatch, {"Cash": 1})
    assert f"{PAYMENT_ENTRY['case_id']}_{PAYMENT_ENTRY['layout']}" not in load_link_index()
    out = receipt_payment(PAYMENT_ENTRY, {})
    assert out["PAYMENT_KIND"] == "cash"
    for key in (
        "PAYMENT_SCHEME_DISPLAY",
        "PAYMENT_ACCOUNT_TYPE",
        "PAYMENT_AID",
        "PAYMENT_MASKED_PAN",
        "PAYMENT_ENTRY_MODE",
        "PAYMENT_PSN",
        "PAYMENT_ATC",
        "PAYMENT_TERMINAL_ID",
        "PAYMENT_TRANSACTION_REF",
        "PAYMENT_WALLET_LABEL",
    ):
        assert out[key] == "NOT_FOUND", key
    assert out["PAYMENT_TENDERED"] != "NOT_FOUND"
    assert out["PAYMENT_CHANGE"] != "NOT_FOUND"


def test_card_receipts_suppress_the_cash_and_wallet_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched_weights(monkeypatch, {"Visa": 1})
    out = receipt_payment(PAYMENT_ENTRY, {})
    assert out["PAYMENT_KIND"] == "card"
    assert out["PAYMENT_WALLET_LABEL"] == "NOT_FOUND"
    assert out["PAYMENT_TENDERED"] == "NOT_FOUND"
    assert out["PAYMENT_CHANGE"] == "NOT_FOUND"
    assert out["PAYMENT_MASKED_PAN"] != "NOT_FOUND"


def test_receipt_payment_is_registered_with_its_declared_emits() -> None:
    assert get_field_provider("receipt_payment") is receipt_payment
    emits = field_provider_emits("receipt_payment")
    assert len(emits) == 16
    assert all(key.startswith("PAYMENT_") for key in emits)
    assert set(receipt_payment(PAYMENT_ENTRY, {})) == set(emits)
    assert field_provider_param_keys("receipt_payment") == frozenset()


def test_receipt_payment_runs_through_apply_field_providers() -> None:
    """The layout-facing path, not just direct provider invocation."""
    layout = {"field_providers": [{"name": "receipt_payment", "params": {}}]}
    merged = apply_field_providers(layout, PAYMENT_ENTRY, distractors=False)
    assert {k: merged["fields"][k] for k in field_provider_emits("receipt_payment")} == receipt_payment(
        PAYMENT_ENTRY, {}
    )
