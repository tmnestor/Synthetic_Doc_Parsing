"""Per-document states (an invoice is paid or unpaid), multi-key items and industry-keyed text pools."""

import copy
import dataclasses
import hashlib
from pathlib import Path

import pytest
import yaml

from generators.distractors import config as config_module
from generators.distractors import providers
from generators.distractors.config import (
    DISTRACTORS_PATH,
    DistractorConfigError,
    StateSpec,
    load_distractor_config,
)
from generators.distractors.paths import DATA_POOLS_PATH
from generators.layout_dsl.field_providers import get_distractor_provider
from tests.conftest import REPO_ROOT, assert_diagnostic_error

CONFIG = load_distractor_config()
PAID = set(CONFIG.states["invoice"]["payment"].groups["paid"])
UNPAID = set(CONFIG.states["invoice"]["payment"].groups["unpaid"])
GT = yaml.safe_load((REPO_ROOT / "ground_truth" / "invoices.yml").read_text())
LAYOUTS = yaml.safe_load((REPO_ROOT / "config" / "layouts" / "invoices.yml").read_text())["layouts"]


def _shipped() -> dict:
    return yaml.safe_load(DISTRACTORS_PATH.read_text())


def _refused(tmp_path: Path, data: dict, *tokens: str) -> str:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(path)
    assert_diagnostic_error(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))
    return str(err.value)


def _keys(ids: set[str]) -> set[str]:
    return {key for item_id in ids for key in CONFIG.items[item_id].emits}


def _drawn(case: str, layout_id: str | None = None) -> dict[str, str]:
    entry = {**GT[case], "case_id": case}
    layout = LAYOUTS[layout_id or entry["layout"]]
    return get_distractor_provider("invoice_distractors")(entry, {}, items=layout["distractor_items"])


# --- the shipped catalogue ----------------------------------------------------------------------------


def test_the_shipped_states_are_the_payment_state_of_an_invoice_alone() -> None:
    assert CONFIG.states["receipt"] == {} and CONFIG.states["bank_statement"] == {}
    payment = CONFIG.states["invoice"]["payment"]
    assert payment.choices == ("paid", "unpaid")
    assert PAID == {"in_amount_paid", "in_balance_due", "in_paid_stamp", "in_paid_date"}
    assert UNPAID == {
        "in_remittance_total",
        "in_remit_barcode",
        "in_account_summary",
        "in_previous_balance",
    }


def test_the_state_seed_is_its_own_sha256_domain() -> None:
    digest = hashlib.sha256(b"CASE007:distractor:invoice:state:payment").digest()
    assert providers.state_seed("CASE007", "invoice", "payment") == int.from_bytes(digest[:8], "big")
    assert providers.state_seed("CASE007", "invoice", "payment") != providers.case_seed(
        "CASE007", "invoice"
    )


def test_every_invoice_draws_one_payment_state_and_both_occur() -> None:
    drawn = {case: providers.draw_states(CONFIG, case, "invoice") for case in GT}
    assert all(set(states) == {"payment"} for states in drawn.values())
    assert {states["payment"] for states in drawn.values()} == {"paid", "unpaid"}
    assert drawn == {case: providers.draw_states(CONFIG, case, "invoice") for case in GT}
    assert providers.draw_states(CONFIG, "CASE001", "receipt") == {}


def test_no_invoice_selects_items_from_both_payment_groups() -> None:
    """Review: a page both settled and owing is resolved by its labels alone."""
    seen = {"paid": 0, "unpaid": 0}
    for case in GT:
        state = providers.draw_states(CONFIG, case, "invoice")["payment"]
        keys = set(_drawn(case))
        other = _keys(UNPAID if state == "paid" else PAID)
        assert not keys & other, (case, state, sorted(keys & other))
        seen[state] += bool(keys & _keys(PAID | UNPAID))
    assert seen["paid"] and seen["unpaid"], seen


def test_without_the_state_filter_some_invoice_mixes_the_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check above can fail: with the exclusion switched off, a case prints both states."""
    monkeypatch.setattr(providers, "excluded_items", lambda config, doc_type, states: set())
    mixed = [case for case in GT if set(_drawn(case)) & _keys(PAID) and set(_drawn(case)) & _keys(UNPAID)]
    assert mixed


def test_an_item_requiring_an_excluded_item_is_excluded_with_it() -> None:
    """The remittance barcode requires the amount due; were it ungrouped, a paid invoice still drops it."""
    state = StateSpec(
        choices=("paid", "unpaid"),
        groups={"paid": ("in_amount_paid",), "unpaid": ("in_remittance_total",)},
    )
    narrowed = dataclasses.replace(CONFIG, states={**CONFIG.states, "invoice": {"payment": state}})
    excluded = providers.excluded_items(narrowed, "invoice", {"payment": "paid"})
    assert {"in_remittance_total", "in_remit_barcode"} <= excluded
    assert "in_invoice_number" not in excluded
    unpaid = providers.excluded_items(narrowed, "invoice", {"payment": "unpaid"})
    assert unpaid == {"in_amount_paid", "in_balance_due"}


def test_a_state_leaving_too_few_items_fails_fast_naming_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = sorted(PAID | UNPAID | {"in_invoice_number"})
    entry = {**GT["CASE001"], "case_id": "CASE001"}
    with pytest.raises(providers.DistractorProviderError) as err:
        get_distractor_provider("invoice_distractors")(entry, {}, items=listed)
    assert_diagnostic_error(err.value)
    assert "states" in str(err.value) and "payment" in str(err.value)


# --- multi-key items ------------------------------------------------------------------------------


def test_bsb_and_account_number_and_the_bpay_pair_always_print_together() -> None:
    together = (("DX_BSB", "DX_ACCOUNT_NUMBER"), ("DX_BPAY_BILLER", "DX_BPAY_REFERENCE"))
    hits = 0
    for case in GT:
        keys = set(_drawn(case))
        for pair in together:
            assert len(keys & set(pair)) in (0, 2), (case, pair)
            hits += set(pair) <= keys
    assert hits
    assert CONFIG.items["in_bank_account"].emits == ("DX_BSB", "DX_ACCOUNT_NUMBER")
    assert CONFIG.items["in_bpay"].emits == ("DX_BPAY_BILLER", "DX_BPAY_REFERENCE")


def test_an_item_emitting_more_keys_than_its_rule_draws_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    data["items"]["in_po_number"]["emits"] = ["DX_PO_NUMBER", "DX_PO_EXTRA"]
    _refused(tmp_path, data, "items.in_po_number.emits", "draws 1 value")
    data = _shipped()
    data["items"]["in_bank_account"]["emits"] = ["DX_BSB"]
    _refused(tmp_path, data, "items.in_bank_account.emits", "draws 2 value")


def test_a_multi_value_rule_without_a_count_is_refused_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    from generators.distractors import values

    monkeypatch.setitem(values.MULTI_VALUE_RULES, "bsb", lambda ctx, rule: ("1", "2"))
    with pytest.raises(values.ValueRuleError) as err:
        values._check_declarations()
    assert_diagnostic_error(err.value)
    assert "bsb" in str(err.value) and "MULTI_VALUE_RULES" in str(err.value)


# --- fail-fast catalogue paths ----------------------------------------------------------------------


def test_a_missing_states_key_is_refused(tmp_path: Path) -> None:
    data = {k: v for k, v in _shipped().items() if k != "states"}
    _refused(tmp_path, data, "states")


def test_a_document_type_without_an_explicit_states_entry_is_refused(tmp_path: Path) -> None:
    data = _shipped()
    del data["states"]["bank_statement"]
    _refused(tmp_path, data, "bank_statement", "explicitly")


@pytest.mark.parametrize(
    ("patch", "token"),
    [
        ({"choices": ["paid"], "items": {"paid": []}}, "choices"),
        ({"choices": ["paid", "paid"], "items": {"paid": []}}, "choices"),
        ({"choices": ["paid", "unpaid"], "items": {"paid": []}}, "one per choice"),
        ({"choices": ["paid", "unpaid"]}, "exactly"),
        ({"choices": ["paid", "unpaid"], "items": {"paid": ["in_nope"], "unpaid": []}}, "in_nope"),
        ({"choices": ["paid", "unpaid"], "items": {"paid": ["rc_acn"], "unpaid": []}}, "rc_acn"),
        (
            {"choices": ["paid", "unpaid"], "items": {"paid": ["in_acn"], "unpaid": ["in_acn"]}},
            "again here",
        ),
        (
            {
                "choices": ["paid", "unpaid"],
                "items": {"paid": ["in_remit_barcode"], "unpaid": ["in_remittance_total"]},
            },
            "requires",
        ),
    ],
)
def test_an_incoherent_state_is_refused(tmp_path: Path, patch: dict, token: str) -> None:
    data = _shipped()
    data["states"]["invoice"]["payment"] = patch
    message = _refused(tmp_path, data, token)
    assert "states.invoice.payment" in message


# --- industry-keyed pools -----------------------------------------------------------------------------


def _pools_refused(monkeypatch: pytest.MonkeyPatch, edit, *tokens: str) -> None:  # noqa: ANN001
    real = config_module._load_pools()
    pools = copy.deepcopy(real)
    edit(pools)
    monkeypatch.setattr(config_module, "_load_pools", lambda: pools)
    with pytest.raises(DistractorConfigError) as err:
        config_module._load(DISTRACTORS_PATH)
    assert_diagnostic_error(err.value)
    assert str(DATA_POOLS_PATH.resolve()) in str(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))


def test_an_unmapped_service_category_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _pools_refused(monkeypatch, lambda p: p["category_industries"].pop("marketing"), "marketing")


def test_a_missing_industry_map_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _pools_refused(monkeypatch, lambda p: p.pop("category_industries"), "category_industries")


def test_a_keyed_pool_missing_an_industry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _pools_refused(
        monkeypatch,
        lambda p: p["distractor_text"]["services"].pop("technology"),
        "distractor_text.services",
        "technology",
    )


def test_a_keyed_pool_with_a_stray_industry_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _pools_refused(
        monkeypatch,
        lambda p: p["distractor_text"]["tagline"].update({"astrology": ["Written in the stars"]}),
        "astrology",
    )


def test_a_noun_shared_by_two_service_categories_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _pools_refused(
        monkeypatch,
        lambda p: p["business_name_parts"]["category_nouns"]["legal"].append("Advisory Partners"),
        "Advisory Partners",
    )


def test_the_clutter_pools_are_keyed_by_every_industry() -> None:
    pools = config_module._load_pools()
    industries = set(pools["category_industries"].values())
    for name in ("tagline", "accreditation", "services", "warranty", "terms"):
        assert set(pools["distractor_text"][name]) == industries, name


def test_every_invoice_cases_clutter_text_comes_from_its_industrys_sub_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every keyed pool, drawn on every invoice (every item forced on), stays within its industry."""
    from generators.distractors.values import case_industry

    pools = config_module._load_pools()
    texts = pools["distractor_text"]
    keyed = {
        item.emits[0]: item.rule["pool"]
        for item in CONFIG.items.values()
        if "invoice" in item.doc_types and isinstance(texts.get(item.rule.get("pool")), dict)
    }
    assert set(keyed) >= {"DX_TAGLINE", "DX_ACCREDITATION", "DX_SERVICES", "DX_TERMS"}
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: eligible
    )
    for case in GT:
        entry = {**GT[case], "case_id": case}
        industry = case_industry(entry["service_category"], pools, case)
        drawn = providers.draw_all_eligible(entry, "invoice")
        for key, pool in keyed.items():
            assert drawn[key] in texts[pool][industry], (case, industry, key, drawn[key])
            others = {t for i, sub in texts[pool].items() if i != industry for t in sub}
            assert drawn[key] not in others or drawn[key] in texts[pool][industry]
