"""The receipt, invoice and bank distractor field providers."""

import copy
import dataclasses
import hashlib
import logging
import random
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from functools import lru_cache

import pytest
import yaml

from generators.distractors import providers, values
from generators.distractors.config import load_distractor_config
from generators.distractors.paths import DISTRACTORS_PATH, layout_file
from generators.distractors.providers import (
    DistractorProviderError,
    case_seed,
    item_seed,
    draw_all_eligible,
    effective_rule,
    select_items,
)
from generators.distractors.validator import (
    DistractorValueError,
    check_distractor_values,
    collision,
    scored_values,
)
from generators.distractors.values import RULES, RuleContext, ValueRuleError, parse_bank_rows
from generators.layout_dsl.field_providers import (
    apply_field_providers,
    field_provider_emits,
    field_provider_param_keys,
    get_distractor_provider,
    is_distractor_provider,
)
from tests.conftest import REPO_ROOT, assert_diagnostic_error

GT = REPO_ROOT / "ground_truth"
TYPES = {
    "receipt": ("receipt_distractors", "receipts.yml"),
    "invoice": ("invoice_distractors", "invoices.yml"),
    "bank_statement": ("bank_distractors", "bank_statements.yml"),
}
LOGGER = "generators.distractors.providers"


@lru_cache(maxsize=None)
def _gt(name: str) -> dict:
    return yaml.safe_load((GT / name).read_text())


def entry(doc_type: str, case: str = "CASE001") -> dict:
    return {"case_id": case, **copy.deepcopy(_gt(TYPES[doc_type][1])[case])}


def eligible(doc_type: str) -> list[str]:
    return [i.id for i in load_distractor_config().items.values() if doc_type in i.doc_types]


def state_eligible(doc_type: str, case: str) -> list[str]:
    """The eligible items left once the case's drawn states exclude the other groups, and its exclusive
    sets drop all but one member each."""
    config = load_distractor_config()
    excluded = providers.excluded_items(config, doc_type, providers.draw_states(config, case, doc_type))
    left = [i for i in eligible(doc_type) if i not in excluded]
    excluded |= providers.exclusive_excluded(config, case, doc_type, left)
    return [i for i in eligible(doc_type) if i not in excluded]


def run(
    doc_type: str, case: str = "CASE001", items: list[str] | None = None, e: dict | None = None
) -> dict:
    provider = get_distractor_provider(TYPES[doc_type][0])
    return provider(e or entry(doc_type, case), {}, items=eligible(doc_type) if items is None else items)


def force(monkeypatch: pytest.MonkeyPatch, ids: list[str] | None = None) -> None:
    """Make the provider select every eligible item (or exactly `ids`)."""
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, chosen, rng, **kw: list(ids or chosen)
    )


def deselected(caplog: pytest.LogCaptureFixture) -> list[str]:
    found = []
    for record in caplog.records:
        if record.name == LOGGER and str(record.msg).startswith("deselected"):
            assert isinstance(record.args, tuple)
            found.append(str(record.args[0]))
    return found


# --- registration -------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_providers_are_registered_as_distractor_providers(doc_type: str) -> None:
    name = TYPES[doc_type][0]
    assert is_distractor_provider(name)
    assert field_provider_param_keys(name) == frozenset()
    union = {k for i in load_distractor_config().items.values() if doc_type in i.doc_types for k in i.emits}
    assert set(field_provider_emits(name)) == union
    assert all(key.startswith("DX_") for key in field_provider_emits(name))


def test_importing_the_field_provider_registry_registers_the_distractor_providers() -> None:
    code = (
        "import generators.layout_dsl.field_providers as f; "
        "names = f.field_provider_names(); "
        "assert {'receipt_distractors', 'invoice_distractors', 'bank_distractors'} <= set(names), names"
    )
    subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, check=True)


# --- seeding and selection ----------------------------------------------------------------------


def test_case_seed_is_the_sha256_of_case_and_type() -> None:
    digest = hashlib.sha256(b"CASE007:distractor:invoice").digest()
    assert case_seed("CASE007", "invoice") == int.from_bytes(digest[:8], "big")
    assert case_seed("CASE007", "invoice") != case_seed("CASE007", "receipt")
    assert case_seed("CASE007", "invoice") != case_seed("CASE008", "invoice")


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_the_same_case_gives_the_same_output(doc_type: str) -> None:
    assert run(doc_type, "CASE005") == run(doc_type, "CASE005")


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_different_cases_differ(doc_type: str) -> None:
    outputs = [run(doc_type, f"CASE{n:03d}") for n in range(1, 11)]
    assert len({tuple(sorted(o.items())) for o in outputs}) == len(outputs)


def _independent(doc_type: str) -> list[str]:
    """Eligible items that neither require nor are required by another item."""
    items = load_distractor_config().items
    required = {r for i in items.values() for r in i.requires}
    return [i for i in eligible(doc_type) if not items[i].requires and i not in required]


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_k_stays_within_the_configured_range(doc_type: str) -> None:
    config = load_distractor_config()
    lo, hi = config.selection[doc_type]
    pool = _independent(doc_type)
    seen: Counter[int] = Counter()
    for seed in range(300):
        chosen = select_items(config, doc_type, pool, random.Random(seed), where="W")
        assert lo <= len(chosen) <= min(hi, len(pool))
        assert len(set(chosen)) == len(chosen)
        seen[len(chosen)] += 1
    assert set(seen) == set(range(lo, min(hi, len(pool)) + 1))


def test_k_stays_within_the_eligible_count() -> None:
    config = load_distractor_config()
    lo, hi = config.selection["invoice"]
    pool = _independent("invoice")[: lo + 1]
    assert len(pool) < hi
    lengths = {len(select_items(config, "invoice", pool, random.Random(s), where="W")) for s in range(200)}
    assert lengths == {lo, lo + 1}


def test_fewer_eligible_items_than_the_minimum_is_a_diagnostic() -> None:
    config = load_distractor_config()
    lo, _ = config.selection["receipt"]
    with pytest.raises(DistractorProviderError) as err:
        select_items(config, "receipt", _independent("receipt")[: lo - 1], random.Random(1), where="W")
    assert_diagnostic_error(err.value)
    assert "selection.receipt.count" in str(err.value)


def test_too_few_eligible_items_names_the_layout_file_and_id() -> None:
    lo, _ = load_distractor_config().selection["receipt"]
    e = entry("receipt", "CASE002")
    with pytest.raises(DistractorProviderError) as err:
        run("receipt", items=_independent("receipt")[: lo - 1], e=e)
    assert_diagnostic_error(err.value)
    where = next(line for line in str(err.value).splitlines() if line.strip().startswith("Where:"))
    assert f"{layout_file('receipt')} -> {e['layout']}.distractor_items" in where
    assert "selection.receipt.count" in where


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_requirements_come_with_their_dependants_and_before_them(doc_type: str) -> None:
    config = load_distractor_config()
    with_requires = 0
    for seed in range(200):
        chosen = select_items(config, doc_type, eligible(doc_type), random.Random(seed), where="W")
        for position, item_id in enumerate(chosen):
            for required in config.items[item_id].requires:
                assert required in chosen[:position], (item_id, required, chosen)
                with_requires += 1
    assert with_requires


def test_a_dependant_whose_requirement_is_not_eligible_is_never_selected() -> None:
    config = load_distractor_config()
    pool = [i for i in eligible("receipt") if i != "rc_member_number"]
    for seed in range(200):
        chosen = select_items(config, "receipt", pool, random.Random(seed), where="W")
        assert "rc_points_balance" not in chosen and "rc_member_number" not in chosen


# --- per-item value seeds -----------------------------------------------------------------------


def test_item_seed_is_the_sha256_of_case_type_and_item() -> None:
    digest = hashlib.sha256(b"CASE007:distractor:invoice:in_due_date").digest()
    assert item_seed("CASE007", "invoice", "in_due_date") == int.from_bytes(digest[:8], "big")
    assert item_seed("CASE007", "invoice", "in_due_date") != item_seed("CASE007", "invoice", "in_tagline")
    assert item_seed("CASE007", "invoice", "in_due_date") != case_seed("CASE007", "invoice")


def _with_requirements(item_id: str) -> list[str]:
    config = load_distractor_config()
    found = {item_id}
    stack = list(config.items[item_id].requires)
    while stack:
        current = stack.pop()
        if current not in found:
            found.add(current)
            stack.extend(config.items[current].requires)
    return sorted(found)


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_an_items_value_is_the_same_alone_sampled_or_all_at_once(
    doc_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 5: `validate`'s all-items draw sees exactly the values a real selection draws."""
    config = load_distractor_config()
    for n in (1, 2, 3, 17, 42):
        case = f"CASE{n:03d}"
        everything = draw_all_eligible(entry(doc_type, case), doc_type)
        sampled = run(doc_type, case)
        assert sampled and set(sampled) < set(everything)
        assert {k: everything[k] for k in sampled} == sampled
        with monkeypatch.context() as patch:
            for item_id in eligible(doc_type):
                key = config.items[item_id].emits[0]
                force(patch, _with_requirements(item_id))
                alone = run(doc_type, case)
                if key in everything:
                    assert alone[key] == everything[key], (case, item_id)


def test_a_change_to_the_layout_list_leaves_other_values_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    force(monkeypatch)
    full = run("invoice", "CASE004")
    shorter = run("invoice", "CASE004", items=[i for i in eligible("invoice") if i != "in_tagline"])
    assert "DX_TAGLINE" not in shorter
    assert shorter == {k: v for k, v in full.items() if k != "DX_TAGLINE"}


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_the_selection_depends_only_on_the_case_seed(
    doc_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selection is drawn from `case_seed` alone: changing every value seed leaves it as it was."""
    config = load_distractor_config()
    for n in range(1, 11):
        case = f"CASE{n:03d}"
        expected = select_items(
            config,
            doc_type,
            state_eligible(doc_type, case),
            random.Random(case_seed(case, doc_type)),
            where="W",
        )
        before = set(run(doc_type, case))
        with monkeypatch.context() as patch:
            patch.setattr(providers, "item_seed", lambda *args: 20310201)
            after = set(run(doc_type, case))
        keys = {k for i in expected for k in config.items[i].emits}
        assert before <= keys and after <= keys
        assert before == after


# --- the layout's list is the one copy ----------------------------------------------------------


def test_apply_field_providers_hands_the_layout_list_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from generators.layout_dsl import provider_registry

    seen: list[object] = []
    real = provider_registry.get_distractor_provider("receipt_distractors")

    def spy(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
        seen.append((params, items))
        return real(entry, params, items=items)

    monkeypatch.setitem(provider_registry._REGISTRY, "receipt_distractors", spy)
    listed = ["rc_store_number", "rc_acn", "rc_phone", "rc_website"]
    layout = {"field_providers": [{"name": "receipt_distractors"}], "distractor_items": listed}
    e = entry("receipt", "CASE006")
    merged = apply_field_providers(layout, e, distractors=True)
    assert seen == [({}, listed)]
    drawn = {k: v for k, v in merged["fields"].items() if k.startswith("DX_")}
    assert drawn == get_distractor_provider("receipt_distractors")(e, {}, items=listed)
    allowed = {k for i in listed for k in load_distractor_config().items[i].emits}
    assert drawn and set(drawn) <= allowed


def test_a_distractor_provider_on_a_layout_without_distractor_items_fails_fast() -> None:
    from generators.layout_dsl.field_providers import FieldProviderError

    with pytest.raises(FieldProviderError) as err:
        apply_field_providers(
            {"field_providers": [{"name": "receipt_distractors"}]}, entry("receipt"), distractors=True
        )
    assert_diagnostic_error(err.value)
    assert "distractor_items" in str(err.value) and "receipt_distractors" in str(err.value)


# --- emitted keys -------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_emitted_keys_are_exactly_the_selected_items_emits(
    doc_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    config = load_distractor_config()
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    for n in range(1, 11):
        case = f"CASE{n:03d}"
        caplog.clear()
        out = run(doc_type, case)
        chosen = select_items(
            config,
            doc_type,
            state_eligible(doc_type, case),
            random.Random(case_seed(case, doc_type)),
            where="W",
        )
        dropped = set(deselected(caplog))
        expected = {k for i in chosen if i not in dropped for k in config.items[i].emits}
        assert set(out) == expected
        assert all(isinstance(v, str) and v for v in out.values())


def test_apply_field_providers_merges_only_when_distractors_are_asked_for() -> None:
    layout = {"field_providers": [{"name": "receipt_distractors"}], "distractor_items": eligible("receipt")}
    e = entry("receipt")
    plain = apply_field_providers(layout, e, distractors=False)
    loaded = apply_field_providers(layout, e, distractors=True)
    assert not any(k.startswith("DX_") for k in plain["fields"])
    assert any(k.startswith("DX_") for k in loaded["fields"])


# --- bank ---------------------------------------------------------------------------------------


def test_annotation_total_is_the_sum_of_the_annotated_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    force(monkeypatch)
    checked = 0
    for case in _gt("bank_statements.yml"):
        out = run("bank_statement", case)
        if "DX_ANNOTATION_TOTAL" not in out:
            continue
        rows = parse_bank_rows(_gt("bank_statements.yml")[case]["fields"])
        total = sum(Decimal(rows[int(i)]["paid"]) for i in out["DX_ANNOTATED_ROWS"].split("|"))
        assert out["DX_ANNOTATION_TOTAL"] == f"{total:.2f}"
        checked += 1
    assert checked >= 40


# --- collisions ---------------------------------------------------------------------------------


def test_a_forced_collision_fails_after_exactly_max_redraws(monkeypatch: pytest.MonkeyPatch) -> None:
    e = entry("invoice", "CASE002")
    total = e["fields"]["TOTAL_AMOUNT"]
    calls: Counter[str] = Counter()

    def colliding(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return f"PO-{total}"

    monkeypatch.setitem(RULES, "digits", colliding)
    force(monkeypatch, ["in_po_number"])
    with pytest.raises(DistractorProviderError) as err:
        run("invoice", e=e)
    assert calls["n"] == load_distractor_config().max_redraws + 1
    message = str(err.value)
    assert_diagnostic_error(err.value)
    for token in ("CASE002", "in_po_number", f"PO-{total}", "TOTAL_AMOUNT", str(DISTRACTORS_PATH)):
        assert token in message, token


def test_a_collision_cleared_by_a_redraw_is_not_written(monkeypatch: pytest.MonkeyPatch) -> None:
    e = entry("invoice", "CASE002")
    total = e["fields"]["TOTAL_AMOUNT"]
    calls: Counter[str] = Counter()

    def first_collides(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return f"PO-{total}" if calls["n"] == 1 else f"PO-{ctx.rng.randint(100000, 999999)}"

    monkeypatch.setitem(RULES, "digits", first_collides)
    force(monkeypatch, ["in_po_number"])
    out = run("invoice", e=e)
    assert calls["n"] == 2
    assert total not in out["DX_PO_NUMBER"]


def test_a_fixed_value_that_collides_is_deselected_not_redrawn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The only permitted deselection: a 0.00 balance on an invoice whose GST is 0.00."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    e = entry("invoice", "CASE001")
    e["fields"]["GST_AMOUNT"] = "0.00"
    calls: Counter[str] = Counter()
    fixed = RULES["fixed"]

    def counted(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return fixed(ctx, rule)

    monkeypatch.setitem(RULES, "fixed", counted)
    force(monkeypatch, ["in_amount_paid", "in_balance_due", "in_po_number"])
    out = run("invoice", e=e)
    assert calls["n"] == 1
    assert "DX_BALANCE_DUE" not in out
    assert out["DX_AMOUNT_PAID"] == e["fields"]["TOTAL_AMOUNT"]
    assert "DX_PO_NUMBER" in out
    assert deselected(caplog) == ["in_balance_due"]
    assert any("CASE001" in r.getMessage() and "GST_AMOUNT" in r.getMessage() for r in caplog.records)


def test_a_deselected_item_takes_its_dependants_with_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A fixed-rule item that collides is deselected, and so is every item requiring it.

    No shipped item requires a non-redrawable one, so the catalogue is amended for this test only:
    in_invoice_number becomes a fixed value equal to the invoice total, and in_remittance_total
    requires it.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    config = load_distractor_config()
    total = entry("invoice", "CASE001")["fields"]["TOTAL_AMOUNT"]
    items = dict(config.items)
    items["in_invoice_number"] = dataclasses.replace(
        items["in_invoice_number"], rule={"type": "fixed", "value": f"INV {total}"}
    )
    amended = dataclasses.replace(config, items=items)
    monkeypatch.setattr(providers, "load_distractor_config", lambda: amended)
    force(monkeypatch, ["in_invoice_number", "in_amount_paid", "in_remittance_total", "in_po_number"])
    out = run("invoice", "CASE001")
    assert set(out) == {"DX_AMOUNT_PAID", "DX_PO_NUMBER"}
    assert sorted(deselected(caplog)) == ["in_invoice_number", "in_remittance_total"]


def test_a_drifted_repeat_fails_fast_and_is_not_redrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ruling 3: a repeat that differs from its raw field is a code fault, never a deselection."""
    calls: Counter[str] = Counter()

    def drifted(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return "9046.01"

    monkeypatch.setitem(RULES, "repeat", drifted)
    force(monkeypatch, ["in_amount_paid"])
    with pytest.raises(DistractorValueError) as err:
        run("invoice", "CASE001")
    assert calls["n"] == 1
    assert_diagnostic_error(err.value)
    for token in ("CASE001", "in_amount_paid", "DX_AMOUNT_PAID", "9046.01", "TOTAL_AMOUNT"):
        assert token in str(err.value), token


def test_repeat_is_not_deselectable() -> None:
    assert "repeat" not in values.DESELECTABLE and "repeat" in values.FAIL_AT_ONCE
    assert values.DESELECTABLE == frozenset({"fixed", "item_count", "annotation_total"})


def test_redrawability_is_declared_for_every_rule_beside_the_rules() -> None:
    sets = (values.REDRAWABLE, values.DESELECTABLE, values.FAIL_AT_ONCE)
    for name in values.RULE_TYPES:
        assert sum(name in s for s in sets) == 1, name
    assert values.FAIL_AT_ONCE >= {"website", "social", "survey_url"}
    assert values.SLUG_RULES == frozenset({"email", "website", "social", "survey_url"})
    assert not hasattr(providers, "NON_REDRAWABLE")


def test_an_undeclared_rule_is_refused_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(RULES, "zz_rule", lambda ctx, rule: "x")
    with pytest.raises(ValueRuleError) as err:
        values._check_declarations()
    assert_diagnostic_error(err.value)
    assert "zz_rule" in str(err.value)


ONE_WORD = "Quenbrook"


@pytest.mark.parametrize(
    ("doc_type", "item_id"),
    [
        ("receipt", "rc_website"),
        ("invoice", "in_social"),
        ("invoice", "in_email"),
        ("receipt", "rc_survey_url"),
    ],
)
def test_a_one_word_supplier_slug_collision_fails_at_once_naming_supplier_name(
    doc_type: str, item_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slug of a one-word supplier name is the name itself: no redraw can clear it."""
    e = entry(doc_type, "CASE005")
    e["fields"]["SUPPLIER_NAME"] = ONE_WORD
    rule_type = load_distractor_config().items[item_id].rule["type"]
    calls: Counter[str] = Counter()
    real = RULES[rule_type]

    def counted(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return real(ctx, rule)

    monkeypatch.setitem(RULES, rule_type, counted)
    force(monkeypatch, _with_requirements(item_id))
    with pytest.raises(DistractorProviderError) as err:
        run(doc_type, e=e)
    assert calls["n"] == 1
    assert_diagnostic_error(err.value)
    recover = next(line for line in str(err.value).splitlines() if line.strip().startswith("Recover:"))
    assert "SUPPLIER_NAME" in recover and item_id in recover
    assert "max_redraws" not in str(err.value)


def test_a_one_word_supplier_is_absent_from_the_ground_truth() -> None:
    for name in ("receipts.yml", "invoices.yml", "bank_statements.yml"):
        assert ONE_WORD not in (GT / name).read_text()


def test_a_fail_at_once_collision_not_caused_by_the_slug_is_not_redrawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A survey address fixed by an already-drawn code that collides fails on the first draw."""
    e = entry("receipt", "CASE005")
    total = e["fields"]["TOTAL_AMOUNT"]
    calls: Counter[str] = Counter()

    def colliding(ctx: RuleContext, rule: dict) -> str:
        calls["n"] += 1
        return f"https://survey.example.com.au/{total}"

    monkeypatch.setitem(RULES, "survey_url", colliding)
    force(monkeypatch, ["rc_survey_code", "rc_survey_url"])
    with pytest.raises(DistractorProviderError) as err:
        run("receipt", e=e)
    assert calls["n"] == 1
    assert_diagnostic_error(err.value)
    assert "rc_survey_url" in str(err.value) and "TOTAL_AMOUNT" in str(err.value)
    assert "ignores the generator" in str(err.value)


def test_a_bank_redraw_failure_names_the_ground_truth_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collision with a bank description names TRANSACTION_DESCRIPTIONS, the key the entry carries."""
    e = entry("bank_statement", "CASE001")
    description = e["fields"]["TRANSACTION_DESCRIPTIONS"].split("|")[0]
    monkeypatch.setitem(RULES, "pool_text", lambda ctx, rule: f"Ask about {description}")
    force(monkeypatch, ["bk_promo_message"])
    with pytest.raises(DistractorProviderError) as err:
        run("bank_statement", e=e)
    assert_diagnostic_error(err.value)
    where = next(line for line in str(err.value).splitlines() if line.strip().startswith("Where:"))
    assert "CASE001.fields.TRANSACTION_DESCRIPTIONS" in where
    assert "LINE_ITEM_DESCRIPTIONS" not in str(err.value)


def test_a_redrawable_rule_is_never_deselected(monkeypatch: pytest.MonkeyPatch) -> None:
    e = entry("receipt", "CASE001")
    monkeypatch.setitem(RULES, "amount_fraction", lambda ctx, rule: e["fields"]["TOTAL_AMOUNT"])
    force(monkeypatch, ["rc_you_saved"])
    with pytest.raises(DistractorProviderError):
        run("receipt", e=e)


# --- fail fast ----------------------------------------------------------------------------------


def test_an_item_for_another_document_type_fails_fast() -> None:
    with pytest.raises(DistractorProviderError) as err:
        run("receipt", items=[*eligible("receipt"), "in_acn"])
    assert_diagnostic_error(err.value)
    assert "in_acn" in str(err.value) and "receipt" in str(err.value)


def test_an_unknown_item_fails_fast() -> None:
    with pytest.raises(DistractorProviderError) as err:
        run("invoice", items=[*eligible("invoice"), "in_no_such_item"])
    assert_diagnostic_error(err.value)
    assert "in_no_such_item" in str(err.value)


def test_the_providers_take_the_items_as_a_keyword_not_a_param() -> None:
    with pytest.raises(TypeError):
        provider = get_distractor_provider("receipt_distractors")
        provider(entry("receipt"), {"items": eligible("receipt")})  # type: ignore[call-arg]


def test_an_entry_without_a_case_id_fails_fast() -> None:
    e = entry("receipt")
    del e["case_id"]
    with pytest.raises(DistractorProviderError) as err:
        run("receipt", e=e)
    assert_diagnostic_error(err.value)


def test_a_rule_failure_names_the_item_and_ground_truth_key(monkeypatch: pytest.MonkeyPatch) -> None:
    e = entry("receipt", "CASE003")
    del e["fields"]["SUPPLIER_NAME"]
    force(monkeypatch, ["rc_website"])
    with pytest.raises(DistractorProviderError) as err:
        run("receipt", e=e)
    assert isinstance(err.value.__cause__, ValueRuleError)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    where = next(line for line in message.splitlines() if line.strip().startswith("Where:"))
    assert f"{DISTRACTORS_PATH} -> items.rc_website.rule" in where
    assert f"{GT / 'receipts.yml'} -> CASE003.fields.SUPPLIER_NAME" in where


def test_providers_never_use_the_global_random(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("global random used")

    for name in ("random", "randint", "choice", "sample", "shuffle", "randrange", "uniform", "choices"):
        monkeypatch.setattr(random, name, boom)
    for doc_type in TYPES:
        assert run(doc_type, "CASE004")


# --- effective rule -----------------------------------------------------------------------------


def test_effective_rule_adds_the_field_for_repeat_items_only() -> None:
    items = load_distractor_config().items
    paid = effective_rule(items["in_amount_paid"])
    assert paid == {**items["in_amount_paid"].rule, "field": "TOTAL_AMOUNT"}
    assert "field" not in items["in_amount_paid"].rule
    assert effective_rule(items["rc_acn"]) == items["rc_acn"].rule


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_effective_rule_runs_for_every_catalogue_item(doc_type: str) -> None:
    from generators.content_engine import build_engine

    config = load_distractor_config()
    e = entry(doc_type, "CASE001")
    engine = build_engine()
    ctx = RuleContext(
        rng=random.Random(1),
        fields=e["fields"],
        engine=engine,
        pools=engine.pools,
        case_id="CASE001",
        rows=parse_bank_rows(e["fields"]) if doc_type == "bank_statement" else None,
        selected={},
        states=providers.draw_states(config, "CASE001", doc_type),
        service_category=e.get("service_category"),
    )
    scored = scored_values(e["fields"], doc_type, config.validator)
    order = providers.draw_order(config, eligible(doc_type))
    assert sorted(order) == sorted(eligible(doc_type))
    for item_id in order:
        item = config.items[item_id]
        drawn = values.draw_values(ctx, effective_rule(item))
        assert len(drawn) == len(item.emits) and all(drawn)
        if item.kind == "repeat":
            assert collision(item, drawn[0], e["fields"], scored) is None
        ctx.selected.update(zip(item.emits, drawn, strict=True))


# --- corpus-wide --------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_every_case_draws_with_all_eligible_items(doc_type: str) -> None:
    """The corpus-wide proof the catalogue and rules are compatible: no failure, no collision."""
    config = load_distractor_config()
    for case in _gt(TYPES[doc_type][1]):
        out = run(doc_type, case)
        check_distractor_values(
            out, _gt(TYPES[doc_type][1])[case]["fields"], doc_type, config, case_id=case
        )


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_every_case_draws_every_item_at_once(
    doc_type: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every item selected on every case: no failure, and deselections only for fixed-value rules."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    config = load_distractor_config()
    force(monkeypatch)
    for case in _gt(TYPES[doc_type][1]):
        out = run(doc_type, case)
        check_distractor_values(
            out, _gt(TYPES[doc_type][1])[case]["fields"], doc_type, config, case_id=case
        )
    for item_id in deselected(caplog):
        rule_type = config.items[item_id].rule["type"]
        requires = config.items[item_id].requires
        assert rule_type in values.DESELECTABLE or requires, item_id


@pytest.mark.parametrize("doc_type", sorted(TYPES))
def test_draw_all_eligible_draws_every_item_and_passes_the_check(
    doc_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    """What `pipeline validate` runs: every catalogue item for the type, none sampled away."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    config = load_distractor_config()
    for case in list(_gt(TYPES[doc_type][1]))[:10]:
        caplog.clear()
        out = draw_all_eligible(entry(doc_type, case), doc_type)
        dropped = set(deselected(caplog))
        expected = {k for i in eligible(doc_type) if i not in dropped for k in config.items[i].emits}
        assert set(out) == expected
        check_distractor_values(
            out, _gt(TYPES[doc_type][1])[case]["fields"], doc_type, config, case_id=case
        )
