"""The four industry invoice layouts: trade, professional, wholesale and subscription.

No corpus case uses them until the reseed, so they are rendered here with synthetic entries built from
their own `service_categories` (tests/invoice_fixtures.py). Covers: the schema accepts them and refuses a
missing or unknown category list; each lists exactly the items its trade prints, every near-miss under its
own label; each renders plain and with distractors, every selected item drawn and every barcode decoded;
with every item of a payment state forced on, each fits one A4 page (slow); sampled renders meet the
invoice density bounds; the catalogue lines fit their layouts; the new rule, pools and the category
checks fail fast with four-part diagnostics.
"""

import copy
import random
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from generators.content_engine import ContentEngine, load_pools
from generators.distractors import config as config_module
from generators.distractors.config import DISTRACTORS_PATH, DistractorConfigError, load_distractor_config
from generators.distractors.density import measure_render
from generators.distractors.paths import DATA_POOLS_PATH
from generators.distractors.values import (
    RuleContext,
    ValueRuleError,
    case_category,
    draw_values,
)
from generators.invoice import render_invoice
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.schema import field_names_for
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.distractor_helpers import decoded, selected, walk_blocks
from tests.name_category import supplier_name_category
from tests.invoice_fixtures import INDUSTRY_LAYOUTS, LINE_COUNTS, SEED_LINE_COUNTS, synthetic_entries
from tests.renderer_helpers import layouts_for
from tests.test_invoice_distractors import (
    GENERAL_LAYOUTS,
    _ink_inside_the_page_box,
    _label_problems,
    _overlay_labels,
    _sites,
    expected_forced_keys,
    force_state,
)

# Synthetic entries have no transaction link in the links file; this gives each one, as the seed would,
# so a paid synthetic invoice's stamp date can be drawn.
pytestmark = pytest.mark.usefixtures("synthetic_invoice_links")

DOC = "invoice"
CONFIG = load_distractor_config()
POOLS = load_pools()
LAYOUT_PATH = "config/layouts/invoices.yml"
CATEGORIES = {
    "tax_invoice_trade": ["plumbing", "electrical"],
    "tax_invoice_professional": ["conveyancing", "patent_attorneys"],
    "tax_invoice_wholesale": ["food_wholesale", "cleaning_wholesale"],
    "tax_invoice_subscription": ["cloud_software", "security_monitoring"],
}
NEW_CATEGORIES = [c for cats in CATEGORIES.values() for c in cats]
# Each layout-specific item and the layouts that list it; no other layout may.
SPECIFIC = {
    "in_licence": {"tax_invoice_trade"},
    "in_site_address": {"tax_invoice_trade"},
    "in_warranty": {"tax_invoice_trade", "tax_invoice_professional"},
    "in_matter_reference": {"tax_invoice_professional"},
    "in_ship_to": {"tax_invoice_wholesale"},
    "in_delivery_docket": {"tax_invoice_wholesale"},
    "in_billing_period": {"tax_invoice_subscription"},
    "in_direct_debit": {"tax_invoice_subscription"},
    "in_engagement_terms": {"tax_invoice_professional"},
    "in_upgrade_price": {"tax_invoice_subscription"},
    "in_client_reference": {"tax_invoice_professional"},
}
NEW_INDUSTRIES = sorted({POOLS["category_industries"][c] for c in NEW_CATEGORIES} - {"legal_services"})
GT_TEXT = "\n".join(path.read_text() for path in sorted((REPO_ROOT / "ground_truth").glob("*.yml")))


def _validate(layout: dict, layout_id: str, doc_type: str = "invoices") -> None:
    validate_layout(
        layout,
        layout_id=layout_id,
        layout_path=LAYOUT_PATH,
        known_fields=set(field_names_for(doc_type)),
        doc_type=doc_type,
    )


# --- the schema and the item lists ---------------------------------------------------------------------


def test_the_registry_holds_the_four_general_and_the_four_industry_layouts() -> None:
    assert sorted(layouts_for(DOC)) == sorted([*GENERAL_LAYOUTS, *INDUSTRY_LAYOUTS])


@pytest.mark.parametrize("layout_id", [*GENERAL_LAYOUTS, *INDUSTRY_LAYOUTS])
def test_the_schema_accepts_every_invoice_layout(layout_id: str) -> None:
    _validate(layouts_for(DOC)[layout_id], layout_id)


def test_each_layout_declares_the_categories_its_design_fits() -> None:
    layouts = layouts_for(DOC)
    for layout_id in GENERAL_LAYOUTS:
        assert layouts[layout_id]["service_categories"] == [
            "accounting",
            "legal",
            "it_services",
            "consulting",
            "marketing",
        ]
    for layout_id, categories in CATEGORIES.items():
        assert layouts[layout_id]["service_categories"] == categories, layout_id
        for category in categories:
            assert POOLS["category_catalog"][category], category
            assert POOLS["business_name_parts"]["category_nouns"][category], category
            assert category in POOLS["category_industries"], category


def test_each_layout_specific_item_is_listed_only_where_its_trade_prints_it() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        for item_id, owners in SPECIFIC.items():
            assert (item_id in layout["distractor_items"]) == (layout_id in owners), (layout_id, item_id)
    assert set(SPECIFIC) <= set(CONFIG.items)


def test_a_professional_firm_quotes_its_clients_reference_not_a_purchase_order() -> None:
    """A law or advisory client raises no purchase order: the firm prints the client's own reference."""
    professional = layouts_for(DOC)["tax_invoice_professional"]
    assert "in_po_number" not in professional["distractor_items"]
    texts = [b.get("content", "") for b in walk_blocks(professional["body"])]
    assert "Your ref: {DX_CLIENT_REFERENCE}" in texts
    assert not [t for t in texts if "PO_NUMBER" in t or "Your reference" in t]
    rule = CONFIG.items["in_client_reference"].rule
    assert rule["type"] == "digits" and not rule["prefix"].startswith("PO")
    # The check can fail: the wholesale layout still prints a purchase order.
    wholesale = [
        b.get("content", "") for b in walk_blocks(layouts_for(DOC)["tax_invoice_wholesale"]["body"])
    ]
    assert any("PO_NUMBER" in t for t in wholesale)


@pytest.mark.parametrize("layout_id", INDUSTRY_LAYOUTS)
def test_every_industry_layout_draws_distractors_and_every_state_item(layout_id: str) -> None:
    layout = layouts_for(DOC)[layout_id]
    assert {"name": "invoice_distractors", "params": {}} in layout["field_providers"]
    for group in CONFIG.states[DOC]["payment"].groups.values():
        assert set(group) <= set(layout["distractor_items"]), layout_id
    assert len(layout["distractor_items"]) >= CONFIG.selection[DOC][1], layout_id


def _schema_refused(layout_id: str, edit, *tokens: str, doc_type: str = "invoices") -> None:  # noqa: ANN001
    layout = copy.deepcopy(layouts_for(DOC)[layout_id])
    edit(layout)
    with pytest.raises(LayoutSchemaError) as err:
        _validate(layout, layout_id, doc_type)
    assert_diagnostic_error(err.value)
    for token in (f"{layout_id}.service_categories", *tokens):
        assert token in str(err.value), (token, str(err.value))


def test_an_invoice_layout_without_service_categories_is_refused() -> None:
    _schema_refused(
        "tax_invoice_trade", lambda layout: layout.pop("service_categories"), "no service_categories"
    )


@pytest.mark.parametrize(
    ("value", "token"),
    [
        ([], "[]"),
        (["plumbing", "plumbing"], "plumbing"),
        (["astrology"], "astrology"),
        ("plumbing", "plumbing"),
    ],
)
def test_an_empty_repeated_unknown_or_scalar_category_list_is_refused(value: object, token: str) -> None:
    _schema_refused("tax_invoice_trade", lambda layout: layout.update(service_categories=value), token)


def test_a_receipt_layout_carrying_service_categories_is_refused() -> None:
    layout_id, layout = next(iter(layouts_for("receipt").items()))
    broken = {**copy.deepcopy(layout), "service_categories": ["plumbing"]}
    with pytest.raises(LayoutSchemaError) as err:
        validate_layout(
            broken,
            layout_id=layout_id,
            layout_path="config/layouts/receipts.yml",
            known_fields=set(field_names_for("receipts")),
            doc_type="receipts",
        )
    assert_diagnostic_error(err.value)
    assert f"{layout_id}.service_categories" in str(err.value)


# --- labels ------------------------------------------------------------------------------------------

LABELS = {
    "tax_invoice_trade": {
        "DX_SITE_ADDRESS": "site address",
        "DX_DUE_DATE": "due date",
        "DX_SUPPLY_DATE": "work completed",
        "DX_CONTACT_PERSON": "technician",
    },
    "tax_invoice_professional": {
        "DX_DUE_DATE": "due date",
        "DX_SUPPLY_DATE": "services to",
        "DX_CONTACT_PERSON": "principal",
        "DX_REMIT_PO_BOX": "postal address",
        "DX_BRANCH_ADDRESS": "also at",
    },
    "tax_invoice_wholesale": {
        "DX_SHIP_TO": "ship to",
        "DX_DUE_DATE": "due date",
        "DX_SUPPLY_DATE": "dispatched",
        "DX_CONTACT_PERSON": "sales representative",
        "DX_BRANCH_ADDRESS": "warehouse",
        "DX_REMIT_PO_BOX": "remittance address",
    },
    "tax_invoice_subscription": {"DX_BILLING_PERIOD": "billing period", "DX_DUE_DATE": "due date"},
}
TOTALS_LABELS = {
    "DX_AMOUNT_PAID": "amount paid",
    "DX_BALANCE_DUE": "balance due",
    "DX_PREVIOUS_BALANCE": "previous balance",
    "DX_ACCOUNT_TOTAL": "this invoice",
    "DX_REMITTANCE_TOTAL": "amount due",
}


@pytest.mark.parametrize("layout_id", INDUSTRY_LAYOUTS)
def test_every_near_miss_on_an_industry_layout_prints_under_its_own_label(layout_id: str) -> None:
    """Review focus 2: each near-miss carries a distinct, realistic label, never a scored block's."""
    layout = layouts_for(DOC)[layout_id]
    problems, labels = _label_problems(layout["body"], _sites(layout))
    assert not problems, problems
    labels.update(_overlay_labels(layouts_for(DOC)[layout_id]["overlay"]))
    listed = {k for i in layouts_for(DOC)[layout_id]["distractor_items"] for k in CONFIG.items[i].emits}
    near = {k for i in CONFIG.items.values() if i.kind in ("near_miss", "repeat") for k in i.emits}
    assert near & listed <= set(labels), sorted(near & listed - set(labels))
    for key, words in {**LABELS[layout_id], **TOTALS_LABELS}.items():
        assert labels[key] == words, (key, labels[key])


# --- rendering ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("layout_id", INDUSTRY_LAYOUTS)
def test_every_industry_layout_renders_its_own_categories_plain_and_with_distractors(
    layout_id: str, drawn_keys: set[str]
) -> None:
    layout = layouts_for(DOC)[layout_id]
    entries = synthetic_entries(layout_id)
    assert len(entries) == len(CATEGORIES[layout_id]) * len(LINE_COUNTS)
    for case_id, entry in entries.items():
        assert case_category(entry["service_category"], POOLS, case_id) in layout["service_categories"], (
            case_id
        )
        plain: dict = {}
        render_invoice(entry, layout, distractors=False, geometry_out=plain)
        lines = len(entry["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|"))
        scored = {k for k in entry["fields"] if k not in ("DOCUMENT_TYPE", "IS_GST_INCLUDED")}
        captured = {re.sub(r"\[\d+\]$", "", key) for key in plain["boxes"]}
        assert scored <= captured, (case_id, sorted(scored - captured))
        assert f"LINE_ITEM_DESCRIPTIONS[{lines - 1}]" in plain["boxes"], case_id
        assert not any(r.role == "distractor" for r in plain["ink"]), case_id

        chosen = selected(entry, layout)
        assert len(chosen) >= CONFIG.selection[DOC][0], case_id
        drawn_keys.clear()
        geometry: dict = {}
        image = render_invoice(entry, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, sorted(set(chosen) - drawn_keys))
        assert not _ink_inside_the_page_box(geometry, layout), case_id
        assert decoded(image) == sorted(chosen[k] for k in ("DX_REMIT_BARCODE",) if k in chosen), case_id


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", INDUSTRY_LAYOUTS)
@pytest.mark.parametrize("state", ["paid", "unpaid"])
def test_every_item_of_a_state_fits_one_page_of_each_industry_layout(
    every_item_forced: None,
    drawn_keys: set[str],
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    layout_id: str,
) -> None:
    """Review focus 1: every item of the state drawn on one page, unclipped, the other state absent.

    Run on every synthetic entry of the layout: the fit set (one, three and six lines, beyond the seed's
    one to four) and the seed-range set, merged without either overwriting the other.
    """
    force_state(monkeypatch, state)
    layout = layouts_for(DOC)[layout_id]
    fit, seed = synthetic_entries(layout_id), synthetic_entries(layout_id, SEED_LINE_COUNTS)
    entries = {**fit, **seed}
    assert len(entries) == len(fit) + len(seed)
    lines = [len(e["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|")) for e in entries.values()]
    assert max(lines) == max(min(6, len(POOLS["category_catalog"][c])) for c in CATEGORIES[layout_id])
    assert 6 in lines or layout_id == "tax_invoice_subscription", lines
    for case_id, entry in entries.items():
        chosen = selected(entry, layout)
        assert set(chosen) == expected_forced_keys(case_id, layout, state), case_id
        drawn_keys.clear()
        geometry: dict = {}
        image = render_invoice(entry, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, sorted(set(chosen) - drawn_keys))
        assert not _ink_inside_the_page_box(geometry, layout), case_id
        assert decoded(image) == ([chosen["DX_REMIT_BARCODE"]] if "DX_REMIT_BARCODE" in chosen else []), (
            case_id
        )
        assert not {"DX_DIRECT_DEBIT", "DX_REMITTANCE_TOTAL"} <= set(chosen), case_id


# --- density -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("layout_id", INDUSTRY_LAYOUTS)
def test_sampled_renders_of_each_industry_layout_meet_the_invoice_bounds(layout_id: str) -> None:
    """The layout's density commitment: its seeded-range entries meet the shared invoice bounds."""
    bounds = CONFIG.density[DOC]
    layout = layouts_for(DOC)[layout_id]
    for case_id, entry in synthetic_entries(layout_id, SEED_LINE_COUNTS).items():
        report = measure_render(render_invoice, entry, layout, True)
        assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, report)
        assert report.distractor_share >= bounds.distractor_share_min, (case_id, report)
        assert bounds.page_fill_min is not None and report.page_fill >= bounds.page_fill_min, (
            case_id,
            report,
        )
        plain = measure_render(render_invoice, entry, layout, False)
        assert plain.distractor_share < bounds.distractor_share_min, (case_id, plain)


# --- content that fits its layout ----------------------------------------------------------------------


def _lines(category: str) -> list[dict]:
    return POOLS["category_catalog"][category]


def test_trade_lines_split_labour_from_materials() -> None:
    for category in CATEGORIES["tax_invoice_trade"]:
        kinds = [line["description"].split(":")[0] for line in _lines(category)]
        assert _lines(category)[0]["description"].startswith("Labour: "), category
        assert {"Labour", "Materials"} <= set(kinds), category
        assert set(kinds) <= {"Labour", "Materials", "Call-out fee"}, (category, kinds)


def test_professional_lines_are_fees_and_disbursements() -> None:
    for category in CATEGORIES["tax_invoice_professional"]:
        for line in _lines(category):
            assert re.match(r"(Professional fees|Disbursement|Official fee): ", line["description"]), line


def test_wholesale_lines_carry_an_item_code_and_a_pack_size() -> None:
    pack = re.compile(r"\b(\d+(\.\d+)? (kg|L)|carton|crate|box|pack|bag|drum)\b")
    for category in CATEGORIES["tax_invoice_wholesale"]:
        codes = [line["description"].split()[0] for line in _lines(category)]
        assert len(set(codes)) == len(codes), category
        for line in _lines(category):
            assert re.match(r"[A-Z]{2}-\d{4} ", line["description"]), line
            assert pack.search(line["description"]), line


def test_a_subscription_plan_is_billed_once_per_period() -> None:
    for category in CATEGORIES["tax_invoice_subscription"]:
        assert "plan" in _lines(category)[0]["description"], category
        for line in _lines(category):
            if "plan" in line["description"]:
                assert line["quantity"] == [1, 1], line
            else:
                assert re.search(r"\b(per|licence|monthly|call-out)\b", line["description"]), line


def test_every_synthetic_entry_bills_only_its_categorys_lines_in_range() -> None:
    for layout_id in INDUSTRY_LAYOUTS:
        for case_id, entry in synthetic_entries(layout_id).items():
            category = case_category(entry["service_category"], POOLS, case_id)
            catalogue = {line["description"]: line for line in _lines(category)}
            fields = entry["fields"]
            for desc, qty in zip(
                fields["LINE_ITEM_DESCRIPTIONS"].split("|"),
                fields["LINE_ITEM_QUANTITIES"].split("|"),
                strict=True,
            ):
                low, high = catalogue[desc]["quantity"]
                assert low <= int(qty) <= high, (case_id, desc, qty)


def _new_text() -> list[str]:
    """Every invented string this task added: catalogue lines, nouns and the new industries' pool text."""
    texts = [line["description"] for c in NEW_CATEGORIES for line in _lines(c)]
    texts += [n for c in NEW_CATEGORIES for n in POOLS["business_name_parts"]["category_nouns"][c]]
    for pool in POOLS["distractor_text"].values():
        if isinstance(pool, dict):
            texts += [t for industry in NEW_INDUSTRIES for t in pool[industry]]
    texts += POOLS["distractor_text"]["direct_debit_notice"]
    return texts


def _named_businesses(texts: list[str], blocklist: frozenset[str]) -> list[tuple[str, str]]:
    return [
        (text, name)
        for text in texts
        for name in sorted(blocklist)
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text.lower())
    ]


def test_no_new_line_noun_or_pool_text_names_a_real_business() -> None:
    blocklist = ContentEngine(POOLS).blocklist
    assert not _named_businesses(_new_text(), blocklist)
    # The screen can fail: a line naming a blocklisted business is caught.
    assert _named_businesses(["FW-9999 Rice from Woolworths, 5 kg"], blocklist)


def test_the_invented_examples_appear_nowhere_in_the_ground_truth() -> None:
    """The new industries' pool text is printed only as distractors, never as ground truth.

    Their catalogue lines and nouns are corpus content since the reseed (LINE_ITEM_DESCRIPTIONS,
    SUPPLIER_NAME), so they are screened for real names above but not held absent here.
    """
    lines_and_nouns = {line["description"] for c in NEW_CATEGORIES for line in _lines(c)} | {
        n for c in NEW_CATEGORIES for n in POOLS["business_name_parts"]["category_nouns"][c]
    }
    texts = [text for text in _new_text() if text not in lines_and_nouns]
    assert len(texts) > 100
    assert not [text for text in texts if text in GT_TEXT]
    # The check can fail: a value the corpus does carry is found.
    assert (
        next(iter(yaml.safe_load((REPO_ROOT / "ground_truth" / "invoices.yml").read_text()).values()))[
            "fields"
        ]["SUPPLIER_NAME"]
        in GT_TEXT
    )


# --- the supplier's name reads as its stored category (a human sees the industry the page prints) -------------------------------------------------------


def _misresolved(pools: dict) -> list[tuple[str, str, str | None]]:
    """Every `{prefix} {noun}` name that does not resolve to the category whose noun it was built from."""
    parts = pools["business_name_parts"]
    wrong = []
    for prefix in [*parts["surnames"], *parts["suburb_prefixes"]]:
        for category in pools["service_categories"]:
            for noun in parts["category_nouns"][category]:
                name = f"{prefix} {noun}"
                got = supplier_name_category(name, pools)
                if got != category:
                    wrong.append((name, category, got))
    return wrong


def test_every_prefix_with_every_noun_resolves_to_its_own_category() -> None:
    assert not _misresolved(POOLS)
    # The enumeration can fail: a noun given to a second category resolves to only one of them.
    doctored = copy.deepcopy(POOLS)
    doctored["business_name_parts"]["category_nouns"]["electrical"].append("Plumbing")
    assert any(category == "electrical" for _, category, _ in _misresolved(doctored))


def _pools_refused(monkeypatch: pytest.MonkeyPatch, edit, *tokens: str) -> None:  # noqa: ANN001
    pools = copy.deepcopy(config_module._load_pools())
    edit(pools)
    monkeypatch.setattr(config_module, "_load_pools", lambda: pools)
    with pytest.raises(DistractorConfigError) as err:
        config_module._load(DISTRACTORS_PATH)
    assert_diagnostic_error(err.value)
    assert str(DATA_POOLS_PATH.resolve()) in str(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))


@pytest.mark.parametrize("value", [None, "accounting", [1, 2]])
def test_a_missing_service_category_list_is_refused_by_the_loader(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    def edit(pools: dict) -> None:
        if value is None:
            pools.pop("service_categories")
        else:
            pools["service_categories"] = value

    _pools_refused(monkeypatch, edit, "service_categories")


@pytest.mark.parametrize("value", [None, "accounting"])
def test_a_missing_service_category_list_is_refused_when_reading_a_supplier(value: object) -> None:
    pools = copy.deepcopy(POOLS)
    if value is None:
        pools.pop("service_categories")
    else:
        pools["service_categories"] = value
    with pytest.raises(ValueRuleError) as err:
        case_category("plumbing", pools, "CASE007")
    assert_diagnostic_error(err.value)
    assert "service_categories" in str(err.value) and "CASE007" in str(err.value)


# --- the billing period rule ---------------------------------------------------------------------------


def _period(date: str, months: list[int], seed: int = 0) -> str:
    ctx = RuleContext(
        rng=random.Random(seed),
        fields={"INVOICE_DATE": date},
        engine=ContentEngine(POOLS),
        pools=POOLS,
        case_id="CASE000",
        rows=None,
        selected={},
    )
    (value,) = draw_values(ctx, {"type": "month_period", "from": "INVOICE_DATE", "months": months})
    return value


@pytest.mark.parametrize(
    ("date", "months", "expected"),
    [
        ("15/12/2024", [1], "01/01/2025 to 31/01/2025"),
        ("10/03/2024", [-1], "01/02/2024 to 29/02/2024"),
        ("31/01/2023", [-1], "01/12/2022 to 31/12/2022"),
        ("05/06/2024", [2], "01/08/2024 to 31/08/2024"),
    ],
)
def test_a_billing_period_is_a_whole_calendar_month(date: str, months: list[int], expected: str) -> None:
    assert _period(date, months) == expected


def test_the_billing_period_never_contains_the_invoice_date() -> None:
    item = CONFIG.items["in_billing_period"]
    assert item.kind == "near_miss" and item.target == "INVOICE_DATE"
    for case_id, entry in synthetic_entries("tax_invoice_subscription", SEED_LINE_COUNTS).items():
        date = entry["fields"]["INVOICE_DATE"]
        for seed in range(6):
            first, last = _period(date, item.rule["months"], seed).split(" to ")
            day = datetime.strptime(date, "%d/%m/%Y")
            assert not (
                datetime.strptime(first, "%d/%m/%Y") <= day <= datetime.strptime(last, "%d/%m/%Y")
            ), (case_id, first, last)


def _catalogue_refused(tmp_path: Path, data: dict, *tokens: str) -> None:
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(DistractorConfigError) as err:
        load_distractor_config(path)
    assert_diagnostic_error(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))


@pytest.mark.parametrize("months", [[0], [], [1, 0], "1", [True]])
def test_a_billing_period_rule_with_bad_months_is_refused(tmp_path: Path, months: object) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["items"]["in_billing_period"]["rule"]["months"] = months
    _catalogue_refused(tmp_path, data, "items.in_billing_period.rule.months")


def test_a_billing_period_rule_without_months_is_refused(tmp_path: Path) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    del data["items"]["in_billing_period"]["rule"]["months"]
    _catalogue_refused(tmp_path, data, "items.in_billing_period.rule.months", "missing")


# --- the line catalogue's loader ----------------------------------------------------------------------


def _pools_file_refused(tmp_path: Path, edit, *tokens: str) -> None:  # noqa: ANN001
    data = yaml.safe_load(DATA_POOLS_PATH.read_text())
    edit(data)
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_pools(path)
    assert_diagnostic_error(err.value)
    assert str(path) in str(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))


def test_a_missing_line_catalogue_is_refused(tmp_path: Path) -> None:
    _pools_file_refused(tmp_path, lambda d: d.pop("category_catalog"), "category_catalog")


@pytest.mark.parametrize(
    ("edit", "token"),
    [
        (lambda d: d["category_catalog"].update(astrology=[]), "category_catalog.astrology"),
        (lambda d: d["category_catalog"].update(plumbing=[]), "category_catalog.plumbing"),
        (lambda d: d["category_catalog"]["plumbing"][0].pop("quantity"), "category_catalog.plumbing[0]"),
        (
            lambda d: d["category_catalog"]["plumbing"][1].update(colour="red"),
            "category_catalog.plumbing[1]",
        ),
        (
            lambda d: d["category_catalog"]["electrical"][2].update(quantity=[0, 1]),
            "category_catalog.electrical[2]",
        ),
        (
            lambda d: d["category_catalog"]["electrical"][3].update(quantity=[3, 2]),
            "category_catalog.electrical[3]",
        ),
        (lambda d: d["category_catalog"]["cloud_software"][0].update(price_low=500), "cloud_software[0]"),
        (lambda d: d["category_catalog"]["cloud_software"][1].update(description=" "), "cloud_software[1]"),
        (lambda d: d.update(category_catalog=["plumbing"]), "category_catalog"),
    ],
)
def test_a_malformed_line_catalogue_is_refused(tmp_path: Path, edit, token: str) -> None:  # noqa: ANN001
    _pools_file_refused(tmp_path, edit, token)


@pytest.mark.parametrize(
    ("edit", "tokens"),
    [
        (lambda d: d["category_catalog"]["electrical"][2].update(quantity=[0, 1]), ("quantity is [0, 1]",)),
        (lambda d: d["category_catalog"]["electrical"][3].update(quantity=[3, 2]), ("minimum above",)),
        (lambda d: d["category_catalog"]["cloud_software"][0].update(price_low=500), ("price_low is 500",)),
        (lambda d: d["category_catalog"]["cloud_software"][1].update(unit=""), ("unit is ''",)),
        (
            lambda d: d["category_catalog"]["plumbing"][0].update(price_high="lots"),
            ("price_high is 'lots'",),
        ),
    ],
)
def test_the_catalogue_diagnostic_names_the_failing_key_and_value(
    tmp_path: Path,
    edit,  # noqa: ANN001
    tokens: tuple[str, ...],
) -> None:
    _pools_file_refused(tmp_path, edit, *tokens)


# --- the seed reads each invoice's categories from its layout ------------------------------------------


def test_the_seed_draws_every_invoice_layouts_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    """Since the reseed the seed draws all eight layouts, so bank merchants come from all their categories."""
    from scripts import seed_ground_truth as seed

    monkeypatch.chdir(REPO_ROOT)
    expected: list[str] = []
    for categories in (layout["service_categories"] for layout in layouts_for(DOC).values()):
        expected += [c for c in categories if c not in expected]
    assert list(seed._seeded_invoice_categories()) == expected
    assert set(expected) == set(POOLS["service_categories"])
    layouts = layouts_for(DOC)
    for layout_id, categories in CATEGORIES.items():
        assert seed._layout_categories(layouts, layout_id) == categories
    with pytest.raises(ValueError) as err:
        seed._layout_categories({"tax_invoice_trade": {}}, "tax_invoice_trade")
    assert_diagnostic_error(err.value)
    assert "tax_invoice_trade.service_categories" in str(err.value)
