"""Synthetic invoice entries for the four industry layouts, beyond the corpus's own cases.

The four industry layouts (trade, professional, wholesale, subscription) carry about seven corpus cases
each; their tests also render entries built here, with line counts the seed never draws: a supplier of each of the layout's own
`service_categories`, named `{prefix} {noun}` as the seed names one, with line items drawn from that
category's `category_catalog` in config/data_pools.yml. The construction mirrors the seed's arithmetic
(GST-exclusive prices, GST at 10% of the subtotal, TOTAL_AMOUNT the GST-inclusive sum), so every entry
is one the seed could write. Deterministic: every draw comes from a generator seeded by the layout id.
"""

import functools
import random
from decimal import ROUND_HALF_UP, Decimal

from generators.abn_register import AbnRegister
from generators.common import generate_abn
from generators.content_engine import ContentEngine, load_pools
from tests.renderer_helpers import layouts_for

# The industry layouts this stage adds; the general four draw the corpus's own cases.
INDUSTRY_LAYOUTS = (
    "tax_invoice_trade",
    "tax_invoice_professional",
    "tax_invoice_wholesale",
    "tax_invoice_subscription",
)
# Line counts the entries of each category cycle through: one line, a typical few, and more than the
# seed has ever drawn (it draws one to four), so a page's fit is proven with headroom.
LINE_COUNTS = (1, 3, 6)
# The seed's own range, one to four lines, twice over: the sample density is measured on.
SEED_LINE_COUNTS = (1, 2, 3, 4, 1, 2, 3, 4)
_CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):.2f}"


def _abn(rng: random.Random) -> str:
    """A checksum-valid ABN drawn without disturbing the module-global generator's state."""
    state = random.getstate()
    random.seed(rng.random())
    try:
        return generate_abn(registered=AbnRegister.from_abns([]))
    finally:
        random.setstate(state)


def _lines(rng: random.Random, catalog: list[dict], count: int) -> list[dict]:
    """`count` of the catalogue's lines, as the seed draws them: every line equally likely, in drawn order."""
    return rng.sample(catalog, min(count, len(catalog)))


@functools.cache
def synthetic_entries(layout_id: str, line_counts: tuple[int, ...] = LINE_COUNTS) -> dict[str, dict]:
    """Entries for every category of an invoice layout, one per line count per category.

    Args:
        layout_id: An invoice layout id whose categories all have a `category_catalog` entry.
        line_counts: The number of lines of each category's successive entries.

    Returns:
        Case id -> entry (with `case_id`, `layout`, `service_category` and `fields`), ready to render on
        `layout_id`.
    """
    pools = load_pools()
    engine = ContentEngine(pools)
    parts = pools["business_name_parts"]
    prefixes = [*parts["surnames"], *parts["suburb_prefixes"]]
    rng = random.Random(f"{layout_id}:{line_counts}")
    # The set's tag keeps every set's case ids distinct, so two sets merge without overwriting.
    tag = (
        "FIT"
        if line_counts == LINE_COUNTS
        else "SEED"
        if line_counts == SEED_LINE_COUNTS
        else "L" + "".join(map(str, line_counts))
    )
    entries: dict[str, dict] = {}
    for category in layouts_for("invoice")[layout_id]["service_categories"]:
        nouns = parts["category_nouns"][category]
        catalog = pools["category_catalog"][category]
        for index, count in enumerate(line_counts):
            case_id = (
                f"SYN_{layout_id.removeprefix('tax_invoice_').upper()}_{tag}_{category.upper()}_{index}"
            )
            descs, qtys, prices, totals = [], [], [], []
            subtotal = Decimal("0")
            for line in _lines(rng, catalog, count):
                qty = rng.randint(*line["quantity"])
                price = Decimal(str(rng.uniform(line["price_low"], line["price_high"]))).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                total = (price * qty).quantize(_CENT, rounding=ROUND_HALF_UP)
                descs.append(line["description"])
                qtys.append(str(qty))
                prices.append(_money(price))
                totals.append(_money(total))
                subtotal += total
            gst = (subtotal * Decimal("0.10")).quantize(_CENT, rounding=ROUND_HALF_UP)
            day, month, year = rng.randint(1, 28), rng.randint(1, 12), rng.randint(2023, 2024)
            entries[case_id] = {
                "case_id": case_id,
                "layout": layout_id,
                "service_category": category,
                "fields": {
                    "DOCUMENT_TYPE": "INVOICE",
                    "SUPPLIER_NAME": f"{rng.choice(prefixes)} {nouns[index % len(nouns)]}",
                    "BUSINESS_ABN": _abn(rng),
                    "BUSINESS_ADDRESS": engine.address(rng),
                    "INVOICE_DATE": f"{day:02d}/{month:02d}/{year}",
                    "IS_GST_INCLUDED": "true",
                    "GST_AMOUNT": _money(gst),
                    "TOTAL_AMOUNT": _money(subtotal + gst),
                    "LINE_ITEM_DESCRIPTIONS": "|".join(descs),
                    "LINE_ITEM_QUANTITIES": "|".join(qtys),
                    "LINE_ITEM_PRICES": "|".join(prices),
                    "LINE_ITEM_TOTAL_PRICES": "|".join(totals),
                    "PAYER_NAME": engine.person(rng)["full_name"],
                    "PAYER_ADDRESS": engine.address(rng),
                },
            }
    return entries


@functools.cache
def synthetic_links() -> dict[str, list[dict]]:
    """A transaction link for every synthetic entry, as the seed writes one for every corpus invoice.

    The PAID stamp's date is the paying transaction's bank date, read from the links file
    (`link_date`), and a corpus invoice's link always carries its invoice date there; an invoice with no
    link fails fast. Each synthetic entry is paid on its invoice date by an invented statement.

    Returns:
        Links-file entries keyed `{case_id}_{layout}.png`, for every entry of every line-count set.
    """
    links: dict[str, list[dict]] = {}
    for layout_id in INDUSTRY_LAYOUTS:
        for counts in (LINE_COUNTS, SEED_LINE_COUNTS):
            for case_id, entry in synthetic_entries(layout_id, counts).items():
                fields = entry["fields"]
                links[f"{case_id}_{layout_id}.png"] = [
                    {
                        "bank_statement": f"{case_id}_synthetic_statement.png",
                        "supplier": fields["SUPPLIER_NAME"],
                        "receipt_date": fields["INVOICE_DATE"],
                        "receipt_total": fields["TOTAL_AMOUNT"],
                        "bank_date": fields["INVOICE_DATE"],
                        "bank_description": f"DIRECT DEBIT {fields['SUPPLIER_NAME'].upper()}",
                        "bank_amount": fields["TOTAL_AMOUNT"],
                        "match_status": "FOUND",
                        "match_difficulty": "easy",
                        "notes": "synthetic test entry",
                    }
                ]
    return links
