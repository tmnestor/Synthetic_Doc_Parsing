"""The reseeded corpus is coherent, brand-free, and keeps its service category out of every scored view.

Covers the reseed's gate (Review Focus 3 and 5): every invoice bills only its own category's catalogue
lines, on a layout that declares that category, from a supplier named for it; every receipt sells only
its merchant category's goods; no catalogue line or pool text names a real brand; no ground-truth value
names a blocklisted business or brand; GST is a true eleventh; and the invoice's `service_category`,
stored as non-scored metadata, reaches no derived CSV/JSONL/CORD/DocILE/native file, no extraction
ground truth and no eval-set export, while `validate` refuses an invoice without a valid one. Every check
is shown able to fail on a doctored input inside its own test.
"""

import copy
import csv
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
import yaml

from generators import derive_outputs
from generators.content_engine import ContentEngine, load_pools
from generators.exporters.config import load_export_config
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.schema import field_names_for, validate_entry
from generators.snapshot.export import SnapshotSummary
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.name_category import supplier_name_category
from tests.renderer_helpers import layouts_for

GT = REPO_ROOT / "ground_truth"
POOLS = load_pools()
INVOICES = yaml.safe_load((GT / "invoices.yml").read_text())
RECEIPTS = yaml.safe_load((GT / "receipts.yml").read_text())
GT_FILES = [GT / "bank_statements.yml", GT / "receipts.yml", GT / "invoices.yml"]


def _whole_word(name: str, text: str) -> bool:
    """The business-name screen's match: `name` as a whole word of `text`, ignoring case."""
    return re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", text.lower()) is not None


# --- brands --------------------------------------------------------------------------------------------


def _catalogue_and_pool_text(pools: dict) -> list[str]:
    """Every product and service description, every name noun and every distractor pool sentence."""
    texts = [line["description"] for lines in pools["product_catalog"].values() for line in lines]
    texts += [line["description"] for lines in pools["category_catalog"].values() for line in lines]
    texts += [noun for nouns in pools["business_name_parts"]["category_nouns"].values() for noun in nouns]
    for pool in pools["distractor_text"].values():
        texts += [t for sub in pool.values() for t in sub] if isinstance(pool, dict) else list(pool)
    return texts


def _brands_named(texts: list[str], brands: list[str]) -> list[tuple[str, str]]:
    return [(text, brand) for text in texts for brand in brands if _whole_word(brand, text)]


def test_no_catalogue_line_or_pool_text_names_a_real_brand() -> None:
    brands = POOLS["real_brand_blocklist"]
    assert len(brands) >= 50 and "Panadol" in brands
    texts = _catalogue_and_pool_text(POOLS)
    assert len(texts) > 300
    assert not _brands_named(texts, brands)
    # The screen can fail: a brand inserted into the receipt catalogue is caught, whatever its case.
    doctored = copy.deepcopy(POOLS)
    doctored["product_catalog"]["pharmacy"][0]["description"] = "PANADOL Tablets 24pk"
    assert _brands_named(_catalogue_and_pool_text(doctored), brands) == [
        ("PANADOL Tablets 24pk", "Panadol")
    ]
    # A brand only as part of a longer word is not a hit (whole-word match, as the name screen).
    assert not _brands_named(["Cokernut ice"], ["Coke"])


def test_the_render_time_screen_includes_the_brands() -> None:
    blocklist = ContentEngine(POOLS).blocklist
    assert {"panadol", "band-aid", "woolworths"} <= blocklist


def test_no_ground_truth_value_names_a_blocklisted_business_or_brand() -> None:
    blocked = sorted(ContentEngine(POOLS).blocklist)
    values = [
        str(value)
        for path in GT_FILES
        for entry in yaml.safe_load(path.read_text()).values()
        for value in entry["fields"].values()
    ]
    assert len(values) > 1500
    hits = [(value, name) for value in values for name in blocked if _whole_word(name, value)]
    assert not hits, hits[:5]
    assert [n for n in blocked if _whole_word(n, "2 x Panadol 24pk")] == ["panadol"]


# --- coherence -----------------------------------------------------------------------------------------


def _invoice_problems(case_id: str, entry: dict, pools: dict) -> list[str]:
    """Why an invoice's content contradicts its layout or category; empty when coherent."""
    category = entry.get("service_category")
    layout = layouts_for("invoice")[entry["layout"]]
    fields = entry["fields"]
    problems = []
    if category not in layout["service_categories"]:
        problems.append(f"{case_id}: category {category!r} is not one of {entry['layout']}'s")
        return problems
    if supplier_name_category(fields["SUPPLIER_NAME"], pools) != category:
        problems.append(f"{case_id}: supplier {fields['SUPPLIER_NAME']!r} is not named for {category}")
    catalogue = [line["description"] for line in pools["category_catalog"][category]]
    lines = fields["LINE_ITEM_DESCRIPTIONS"].split("|")
    quantities = fields["LINE_ITEM_QUANTITIES"].split("|")
    for desc, qty in zip(lines, quantities, strict=True):
        if desc not in catalogue:
            problems.append(f"{case_id}: line {desc!r} is not in category_catalog.{category}")
            continue
        low, high = pools["category_catalog"][category][catalogue.index(desc)]["quantity"]
        if not low <= int(qty) <= high:
            problems.append(f"{case_id}: {desc!r} billed {qty}, outside [{low}, {high}]")
    if len(set(lines)) != len(lines):
        problems.append(f"{case_id}: a catalogue line is billed twice")
    return problems


def test_every_invoice_bills_only_its_layouts_category_lines() -> None:
    """Review Focus 3, on every corpus invoice: layout, category, supplier and lines agree."""
    problems = [p for case_id, entry in INVOICES.items() for p in _invoice_problems(case_id, entry, POOLS)]
    assert not problems, problems
    assert {entry["layout"] for entry in INVOICES.values()} == set(layouts_for("invoice"))
    # The check can fail: a plumber billing legal advice, and a trade layout carrying a legal category.
    case_id, entry = next((c, e) for c, e in INVOICES.items() if e["service_category"] == "electrical")
    doctored = copy.deepcopy(entry)
    doctored["fields"]["LINE_ITEM_DESCRIPTIONS"] = "|".join(
        ["Contract review and drafting"] + doctored["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|")[1:]
    )
    assert any(
        "not in category_catalog.electrical" in p for p in _invoice_problems(case_id, doctored, POOLS)
    )
    doctored = copy.deepcopy(entry)
    doctored["service_category"] = "legal"
    assert any("is not one of" in p for p in _invoice_problems(case_id, doctored, POOLS))


def _receipt_problems(case_id: str, entry: dict, pools: dict) -> list[str]:
    """Why a receipt's merchant, layout or goods disagree; empty when coherent."""
    category = entry.get("merchant_category")
    if supplier_name_category(entry["fields"]["SUPPLIER_NAME"], pools, "receipt_categories") != category:
        return [f"{case_id}: supplier {entry['fields']['SUPPLIER_NAME']!r} is not named for {category!r}"]
    if category not in layouts_for("receipt")[entry["layout"]]["merchant_categories"]:
        return [f"{case_id}: a {category} merchant on {entry['layout']}"]
    catalogue = {line["description"]: line for line in pools["product_catalog"][category]}
    problems = []
    for desc, qty in zip(
        entry["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|"),
        entry["fields"]["LINE_ITEM_QUANTITIES"].split("|"),
        strict=True,
    ):
        if desc not in catalogue:
            problems.append(f"{case_id}: a {category} merchant sells {desc!r}")
        elif not catalogue[desc]["quantity"][0] <= int(qty) <= catalogue[desc]["quantity"][1]:
            problems.append(f"{case_id}: {desc!r} sold {qty}")
    return problems


def test_every_receipt_sells_only_its_merchant_categorys_goods() -> None:
    problems = [p for case_id, entry in RECEIPTS.items() for p in _receipt_problems(case_id, entry, POOLS)]
    assert not problems, problems
    # The check can fail: a cellar ringing up yoghurt.
    case_id, entry = next((c, e) for c, e in RECEIPTS.items() if e["merchant_category"] == "liquor")
    doctored = copy.deepcopy(entry)
    doctored["fields"]["LINE_ITEM_DESCRIPTIONS"] = "|".join(
        ["Yoghurt Tub 1kg"] + doctored["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|")[1:]
    )
    assert _receipt_problems(case_id, doctored, POOLS) == [
        f"{case_id}: a liquor merchant sells 'Yoghurt Tub 1kg'"
    ]


def test_every_catalogue_covers_its_categories() -> None:
    assert set(POOLS["product_catalog"]) == set(POOLS["receipt_categories"])
    assert set(POOLS["category_catalog"]) == set(POOLS["service_categories"])
    assert "service_catalog" not in POOLS


def test_gst_is_one_eleventh_of_every_gst_inclusive_total() -> None:
    checked = 0
    for path in (GT / "receipts.yml", GT / "invoices.yml"):
        for case_id, entry in yaml.safe_load(path.read_text()).items():
            fields = entry["fields"]
            if fields["IS_GST_INCLUDED"] == "true":
                expected = (Decimal(fields["TOTAL_AMOUNT"]) / 11).quantize(Decimal("0.01"), ROUND_HALF_UP)
                assert Decimal(fields["GST_AMOUNT"]) == expected, (path.name, case_id)
                checked += 1
    assert checked == 110


# --- the stored service category ----------------------------------------------------------------------


def test_every_invoice_stores_its_category_and_validate_accepts_it() -> None:
    assert all(isinstance(entry.get("service_category"), str) for entry in INVOICES.values())
    assert not [e for c, e in INVOICES.items() if validate_entry(c, e)]
    assert "service_category" not in field_names_for("invoices")
    assert not any("service_category" in entry for entry in RECEIPTS.values())
    assert all(isinstance(entry.get("merchant_category"), str) for entry in RECEIPTS.values())
    assert not [e for c, e in RECEIPTS.items() if validate_entry(c, e)]


@pytest.mark.parametrize(
    ("edit", "token"),
    [
        (lambda e: e.pop("service_category"), "has no 'service_category'"),
        (lambda e: e.update(service_category="astrology"), "service_category: 'astrology'"),
        (
            lambda e: e.update(service_category="legal", layout="tax_invoice_trade"),
            "service_category: 'legal'",
        ),
    ],
)
def test_validate_refuses_an_invoice_without_a_valid_category(edit, token: str) -> None:  # noqa: ANN001
    case_id, entry = next(iter(INVOICES.items()))
    doctored = copy.deepcopy(entry)
    edit(doctored)
    errors = validate_entry(case_id, doctored)
    assert len(errors) == 1, errors
    assert_diagnostic_error(errors[0])
    assert token in errors[0] and "service_categories" in errors[0] and case_id in errors[0]


def _category_leaks(text: str, categories: set[str]) -> list[str]:
    """Where `service_category`, or a category as a whole value, appears in an exported file's text."""
    leaks = ["service_category"] if "service_category" in text else []
    leaks += [c for c in sorted(categories) if re.search(rf'(^|[",]){re.escape(c)}($|[",\n])', text, re.M)]
    return leaks


def test_the_category_reaches_no_derived_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    categories = {e["service_category"] for e in INVOICES.values()} | {
        e["merchant_category"] for e in RECEIPTS.values()
    }
    export_config = load_export_config(REPO_ROOT / "config" / "export_config.yml")
    written = [
        derive_outputs.derive_csv(
            GT_FILES, REPO_ROOT / "config" / "field_definitions.yml", tmp_path / "gt.csv"
        ),
        derive_outputs.derive_jsonl(GT_FILES, tmp_path / "gt.jsonl"),
        derive_outputs.derive_cord(GT_FILES, export_config, tmp_path / "cord.jsonl"),
        derive_outputs.derive_native(GT_FILES, tmp_path / "native.jsonl"),
    ]
    # DocILE needs captured boxes; give every invoice an empty record and see what reaches the exporter.
    seen: list[dict] = []

    def spy(fields: dict, boxes: dict, types: dict) -> dict:
        seen.append(fields)
        return {}

    monkeypatch.setattr(derive_outputs, "to_docile", spy)
    geometry = tmp_path / "geometry.jsonl"
    geometry.write_text(
        "".join(
            json.dumps({"image_file": f"{c}_{e['layout']}.png", "boxes": {}}) + "\n"
            for c, e in INVOICES.items()
        )
    )
    written.append(
        derive_outputs.derive_docile(GT_FILES, geometry, export_config, tmp_path / "docile.jsonl")
    )
    assert len(seen) == 55 and not any("service_category" in fields for fields in seen)
    for path in written:
        assert not _category_leaks(path.read_text(), categories), path.name
    header = next(csv.reader((tmp_path / "gt.csv").open()))
    assert "service_category" not in header
    # The screen can fail: a file carrying the key, or a category as a whole value, is caught.
    assert _category_leaks('{"service_category": "plumbing"}', categories)
    assert _category_leaks('CASE001,"x",legal\n', {"legal"}) == ["legal"]
    assert not _category_leaks("CASE001,Legal document preparation\n", {"legal"})


def test_the_category_reaches_no_extraction_ground_truth(tmp_path: Path) -> None:
    from tests.test_generate_extraction_gt import SCHEMA_PATH, gt

    out = tmp_path / "ground_truth_extraction.csv"
    gt.generate(GT, out, None, gt._load_schema(SCHEMA_PATH))
    categories = {e["service_category"] for e in INVOICES.values()} | {
        e["merchant_category"] for e in RECEIPTS.values()
    }
    assert not _category_leaks(out.read_text(), categories)


def test_the_category_reaches_no_eval_set_export(export: SnapshotSummary) -> None:
    categories = {e["service_category"] for e in INVOICES.values()} | {
        e["merchant_category"] for e in RECEIPTS.values()
    }
    files = [p for p in export.path.rglob("*") if p.suffix in {".csv", ".jsonl", ".json", ".yml"}]
    assert files
    for path in files:
        assert not _category_leaks(path.read_text(), categories), path


# --- catalogue coverage --------------------------------------------------------------------------------


def test_validate_refuses_an_invoice_layout_whose_category_has_no_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from generators.layout_dsl import schema as layout_schema

    layout = layouts_for("invoice")["tax_invoice_trade"]
    known = set(field_names_for("invoices"))
    path = str(REPO_ROOT / "config" / "layouts" / "invoices.yml")
    monkeypatch.setattr(layout_schema, "_catalogued_categories", lambda: frozenset({"plumbing"}))
    with pytest.raises(LayoutSchemaError) as err:
        validate_layout(
            layout, layout_id="tax_invoice_trade", layout_path=path, known_fields=known, doc_type="invoices"
        )
    assert_diagnostic_error(err.value)
    assert "category_catalog.electrical" in str(err.value)


def test_the_seed_refuses_a_category_with_no_catalogue_lines() -> None:
    from scripts import seed_ground_truth as seed

    pools = copy.deepcopy(POOLS)
    del pools["category_catalog"]["plumbing"]
    with pytest.raises(ValueError) as err:
        seed._catalogue_lines(ContentEngine(pools), "category_catalog", "plumbing")
    assert_diagnostic_error(err.value)
    assert "category_catalog.plumbing" in str(err.value)


def test_the_loader_refuses_a_receipt_category_with_no_products(tmp_path: Path) -> None:
    data = yaml.safe_load((REPO_ROOT / "config" / "data_pools.yml").read_text())
    del data["product_catalog"]["liquor"]
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError) as err:
        load_pools(path)
    assert_diagnostic_error(err.value)
    assert "product_catalog.liquor" in str(err.value)


# --- headings with nothing under them ------------------------------------------------------------------


def _is_heading(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("content"), str)
        and re.fullmatch(r"[A-Za-z ]+:", block["content"]) is not None
    )


def _dx_gated(block: dict) -> bool:
    return str(block.get("when", "")).startswith("DX_")


def _bare_headings(blocks: object, gated: bool = False) -> list[str]:
    """Label-only headings ('Payment Terms:') that print in a plain render with nothing under them.

    A heading is bare when it is not DX-gated itself (nor inside a gated container) and the next block
    after it that is not a spacer is DX-gated, a foot-anchored band, or missing: the heading prints, the
    content it announces does not.
    """
    found: list[str] = []
    if not isinstance(blocks, list):
        return found
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        inside = gated or _dx_gated(block)
        if _is_heading(block) and not inside:
            following = [
                b for b in blocks[index + 1 :] if isinstance(b, dict) and b.get("type") != "spacer"
            ]
            under = following[0] if following else None
            if under is None or _dx_gated(under) or under.get("anchor") == "foot":
                found.append(block["content"])
        children = block.get("children") or []
        columns = children if all(isinstance(c, list) for c in children) else [children]
        for column in columns:
            found += _bare_headings(column, inside)
    return found


def test_no_invoice_heading_prints_with_nothing_under_it() -> None:
    """'Payment Terms:' prints only with the terms, and high value's 'Delivery:' now says something."""
    for layout_id, layout in layouts_for("invoice").items():
        assert not _bare_headings(layout["body"]), layout_id
    # The check can fail: the headings as they were before the reseed.
    old = [
        {"type": "text", "content": "Bill To:"},
        {"type": "text", "content": "{PAYER_NAME}"},
        {"type": "text", "content": "Payment Terms:"},
        {"type": "text", "content": "{DX_TERMS}", "when": "DX_TERMS"},
        {"type": "text", "content": "Delivery:"},
        {"type": "stack", "anchor": "foot", "children": []},
    ]
    assert _bare_headings(old) == ["Payment Terms:", "Delivery:"]


def test_the_professional_layout_prints_long_terms_of_engagement() -> None:
    """Its terms come from the multi-sentence engagement_terms pool, never the one-line terms."""
    items = layouts_for("invoice")["tax_invoice_professional"]["distractor_items"]
    assert "in_engagement_terms" in items and "in_terms" not in items
    pool = POOLS["distractor_text"]["engagement_terms"]
    assert len(pool) >= 5
    for terms in pool:
        assert len(re.findall(r"[.] ", terms)) >= 2 and len(terms) >= 180, terms
    # The shape check can fail: a one-line term is too short.
    assert len(re.findall(r"[.] ", "Payment is due within 14 days of the invoice date.")) < 2


def test_every_subscription_upgrade_offer_costs_more_than_anything_it_bills() -> None:
    """The premium plan is priced on the dearest unit price billed, at 1.2 to 2 times it, so it is never
    cheaper than the plan or add-on it would replace, whichever line the invoice prints first."""
    from generators.distractors.providers import draw_all_eligible

    cases = {c: e for c, e in INVOICES.items() if e["layout"] == "tax_invoice_subscription"}
    assert len(cases) >= 5
    for case_id, entry in cases.items():
        dearest = max(Decimal(p) for p in entry["fields"]["LINE_ITEM_PRICES"].split("|"))
        offer = Decimal(draw_all_eligible({**entry, "case_id": case_id}, "invoice")["DX_UPGRADE_PRICE"])
        assert dearest * Decimal("1.2") - Decimal("0.01") <= offer <= dearest * 2, (case_id, dearest, offer)
    assert "in_promo_price" not in layouts_for("invoice")["tax_invoice_subscription"]["distractor_items"]
    # The check can fail: an offer priced on a cheaper first line (an add-on) falls below the dearest.
    assert not Decimal("60.00") * Decimal("1.2") <= Decimal("9.00") * 2


# --- the lead line is drawn, not fixed by the category ----------------------------------------------------

# Measured on the reseed of 7 October 2026: 34 distinct first lines over the 55 invoices, and no category of
# three or more cases leading every one with the same line. The floor sits below the measurement.
MIN_DISTINCT_FIRST_LINES = 30


def _lead_problems(invoices: dict) -> list[str]:
    """Why the first lines are fixed by category; empty when they vary."""
    leads: dict[str, list[str]] = {}
    for entry in invoices.values():
        leads.setdefault(entry["service_category"], []).append(
            entry["fields"]["LINE_ITEM_DESCRIPTIONS"].split("|")[0]
        )
    problems = [
        f"every one of the {len(lines)} {category} invoices starts {lines[0]!r}"
        for category, lines in sorted(leads.items())
        if len(lines) >= 3 and len(set(lines)) == 1
    ]
    distinct = {line for lines in leads.values() for line in lines}
    if len(distinct) < MIN_DISTINCT_FIRST_LINES:
        problems.append(f"only {len(distinct)} distinct first lines, below {MIN_DISTINCT_FIRST_LINES}")
    return problems


def test_no_category_decides_an_invoices_first_line() -> None:
    """Final review, Important 1: a model must not read LINE_ITEM_DESCRIPTIONS[0] off the supplier name."""
    assert not _lead_problems(INVOICES), _lead_problems(INVOICES)
    # The check can fail: the old seed, which always billed the catalogue's first line and printed it first.
    doctored = copy.deepcopy(INVOICES)
    for entry in doctored.values():
        fields = entry["fields"]
        fields["LINE_ITEM_DESCRIPTIONS"] = "|".join(
            [POOLS["category_catalog"][entry["service_category"]][0]["description"]]
            + fields["LINE_ITEM_DESCRIPTIONS"].split("|")[1:]
        )
    problems = _lead_problems(doctored)
    assert any("every one of the" in p for p in problems) and any(
        "distinct first lines" in p for p in problems
    )


def _pools_file(tmp_path: Path, edit) -> Path:  # noqa: ANN001
    data = yaml.safe_load((REPO_ROOT / "config" / "data_pools.yml").read_text())
    edit(data)
    path = tmp_path / "data_pools.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


@pytest.mark.parametrize(
    ("edit", "tokens"),
    [
        (lambda d: d["product_catalog"]["pharmacy"][0].update(description="Panadol 24pk"), ("Panadol",)),
        (
            lambda d: d["category_catalog"]["plumbing"][0].update(description="Labour: Holden fleet"),
            ("Holden",),
        ),
        (lambda d: d["product_catalog"]["grocery"][0].update(description="MILO 1kg"), ("Milo",)),
        (
            lambda d: d["distractor_text"]["thank_you"].append("Thanks from all of us at Samsung"),
            ("Samsung",),
        ),
        (
            lambda d: d["locations"].append(
                {"suburb": "Dulux Heights", "postcode": "2000", "state": "NSW"}
            ),
            ("Dulux",),
        ),
        (lambda d: d["brand_screen"]["screened"].remove("locations"), ("locations", "brand_screen")),
        (lambda d: d["brand_screen"]["exempt"].append("locations"), ("locations", "brand_screen")),
        (lambda d: d.update(new_pool=["x"]), ("new_pool",)),
        (lambda d: d.pop("catalogue_only_brands"), ("catalogue_only_brands",)),
    ],
)
def test_the_loader_refuses_a_brand_or_an_unsorted_pool(tmp_path: Path, edit, tokens: tuple) -> None:  # noqa: ANN001
    with pytest.raises(ValueError) as err:
        load_pools(_pools_file(tmp_path, edit))
    assert_diagnostic_error(err.value)
    for token in tokens:
        assert token in str(err.value), (token, str(err.value))


def test_the_payment_chrome_is_the_declared_exemption() -> None:
    """Wallet and acquirer names a real terminal prints are exempt by declaration, not by omission."""
    screen = POOLS["brand_screen"]
    assert set(screen["screened"]) | set(screen["exempt"]) == set(POOLS)
    assert {"payment_terminal", "banks"} <= set(screen["exempt"])
    wallets = " ".join(POOLS["payment_terminal"]["wallets"].values())
    assert _whole_word("Apple", wallets) and "Apple" in POOLS["real_brand_blocklist"]
    # Common-name brands are screened on catalogue lines only, so a person or street may carry the name.
    assert {"Stanley", "Ford", "Holden", "Milo"} <= set(POOLS["catalogue_only_brands"])
    assert "ford" not in ContentEngine(POOLS).blocklist


def test_every_receipt_prints_on_a_layout_that_serves_its_merchant() -> None:
    """A fuel layout's "Drive safely" is printed only for fuel and automotive merchants."""
    layouts = layouts_for("receipt")
    served = {c for layout in layouts.values() for c in layout["merchant_categories"]}
    assert served == set(POOLS["receipt_categories"])
    assert layouts["receipt_fuel"]["footer_text"] == "Drive safely"
    for case_id, entry in RECEIPTS.items():
        if entry["layout"] == "receipt_fuel":
            assert entry["merchant_category"] in ("fuel", "automotive"), case_id
    assert {entry["layout"] for entry in RECEIPTS.values()} == set(layouts)
    # The check can fail: a pharmacy moved onto the fuel layout.
    case_id, entry = next((c, e) for c, e in RECEIPTS.items() if e["merchant_category"] == "pharmacy")
    doctored = {**copy.deepcopy(entry), "layout": "receipt_fuel"}
    assert _receipt_problems(case_id, doctored, POOLS) == [
        f"{case_id}: a pharmacy merchant on receipt_fuel"
    ]
    errors = validate_entry(case_id, doctored)
    assert len(errors) == 1 and "merchant_categories" in errors[0]
    assert_diagnostic_error(errors[0])


def _receipt_layout_refused(edit, token: str) -> None:  # noqa: ANN001
    layout = copy.deepcopy(layouts_for("receipt")["receipt_fuel"])
    edit(layout)
    with pytest.raises(LayoutSchemaError) as err:
        validate_layout(
            layout,
            layout_id="receipt_fuel",
            layout_path=str(REPO_ROOT / "config" / "layouts" / "receipts.yml"),
            known_fields=set(field_names_for("receipts")),
            doc_type="receipts",
        )
    assert_diagnostic_error(err.value)
    assert token in str(err.value)


@pytest.mark.parametrize(
    ("edit", "token"),
    [
        (lambda lay: lay.pop("merchant_categories"), "has no merchant_categories key"),
        (lambda lay: lay.update(merchant_categories=[]), "merchant_categories is []"),
        (lambda lay: lay.update(merchant_categories=["fuel", "fuel"]), "receipt_fuel.merchant_categories"),
        (lambda lay: lay.update(merchant_categories=["bakery"]), "['bakery'] are not receipt categories"),
    ],
)
def test_validate_refuses_a_malformed_merchant_category_list(edit, token: str) -> None:  # noqa: ANN001
    _receipt_layout_refused(edit, token)


def test_an_invoice_layout_carrying_merchant_categories_is_refused() -> None:
    layout = {**copy.deepcopy(layouts_for("invoice")["tax_invoice_trade"]), "merchant_categories": ["fuel"]}
    with pytest.raises(LayoutSchemaError) as err:
        validate_layout(
            layout,
            layout_id="tax_invoice_trade",
            layout_path=str(REPO_ROOT / "config" / "layouts" / "invoices.yml"),
            known_fields=set(field_names_for("invoices")),
            doc_type="invoices",
        )
    assert_diagnostic_error(err.value)
    assert "tax_invoice_trade.merchant_categories" in str(err.value)


def test_the_seed_refuses_a_category_no_receipt_layout_serves() -> None:
    from scripts import seed_ground_truth as seed

    # The hospitality layout, the only one serving cafes, is left out (an empty list is refused itself).
    layouts = {
        k: {**v, "merchant_categories": [c for c in v["merchant_categories"] if c != "cafe"]}
        for k, v in layouts_for("receipt").items()
        if k != "receipt_hospitality"
    }
    assert seed._layouts_serving(layouts, "fuel") == ["receipt_fuel"]
    with pytest.raises(ValueError) as err:
        seed._layouts_serving(layouts, "cafe")
    assert_diagnostic_error(err.value)
    assert "'cafe'" in str(err.value) and "merchant_categories" in str(err.value)


@pytest.mark.parametrize("value", ["absent", [], None])
def test_the_seed_refuses_a_receipt_layout_without_merchant_categories(value: object) -> None:
    """A layout missing the key is named as the cause, not read as serving nothing."""
    from scripts import seed_ground_truth as seed

    layouts = copy.deepcopy(layouts_for("receipt"))
    if value == "absent":
        layouts["receipt_hospitality"].pop("merchant_categories")
    else:
        layouts["receipt_hospitality"]["merchant_categories"] = value
    with pytest.raises(ValueError) as err:
        seed._layouts_serving(layouts, "grocery")
    assert_diagnostic_error(err.value)
    assert "receipt_hospitality.merchant_categories" in str(err.value)
