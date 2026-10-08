"""Ground truth seed generator for synthetic Australian business documents.

Generates 55 YAML entries per document type (bank statements, receipts, invoices,
CC statements) using deterministic seed=42, writing to ground_truth/*.yml.

Each case's shared entities (account holder, home location) are generated once
via content_engine and projected across that case's bank/cc/invoice entries, so
widened content never desyncs a PAYER_NAME across a case's linked documents.
"""

import copy
import functools
import hashlib
import random
from collections.abc import Container

# Ensure we can import from project root
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import typer
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.content_engine import (  # noqa: E402
    ContentEngine,
    NonRepeatingSampler,
    build_engine,
    sample,
)
from generators import abn_register  # noqa: E402
from generators.bank_ledger import (  # noqa: E402
    balance_floor,
    check_statement,
    lowest_above_floor,
)
from generators.bank_ledger import rows as ledger_rows  # noqa: E402
from generators.distractors.values import links_in_memory  # noqa: E402
from generators.loader import load_layout_registry  # noqa: E402
from generators.overflow_check import check_overflow  # noqa: E402
from generators.schema import validate_entry  # noqa: E402
from scripts.seed_transaction_links import link_corpus  # noqa: E402

_SEED = 42
_COUNT = 55
_GT_DIR = Path(__file__).parent.parent / "ground_truth"
_DATA_POOLS_PATH = Path(__file__).parent.parent / "config" / "data_pools.yml"

# The layout registries the seed draws each document type's layouts from, in their YAML order. Adding a
# layout to a registry is all it takes for the reseed to use it; no layout list is kept here.
_BANK_LAYOUT_PATH = Path("config/layouts/bank_statements.yml")
_RECEIPT_LAYOUT_PATH = Path("config/layouts/receipts.yml")
_INVOICE_LAYOUT_PATH = Path("config/layouts/invoices.yml")


def _layout_ids(path: Path) -> list[str]:
    """Return every layout id a registry declares, in YAML order.

    Args:
        path: A layout registry, e.g. config/layouts/invoices.yml.

    Returns:
        The registry's layout ids.

    Raises:
        ValueError: The registry declares no layout.
    """
    ids = list(load_layout_registry(path))
    if not ids:
        msg = (
            "Empty layout registry.\n"
            f"  What:     {path} declares no layouts, so the seed has none to draw.\n"
            f"  Where:    {path.resolve()} -> layouts\n"
            "  Expected: a layouts: mapping of at least one layout id to its spec.\n"
            f"  Recover:  restore the layouts: block of {path} from git."
        )
        raise ValueError(msg)
    return ids


def _fmt_date(day: int, month: int, year: int) -> str:
    """Format date as DD/MM/YYYY."""
    return f"{day:02d}/{month:02d}/{year}"


def _txn_count_range(layouts: dict, layout_id: str) -> tuple[int, int]:
    """Return the (min, max) transactions a bank layout can fit on one page.

    Capacity is a property of the layout's own geometry — row height, date-group
    rows, reference sub-lines — so it is declared alongside them in
    config/layouts/bank_statements.yml rather than assumed here. A statement
    longer than its page is content the benchmark hides from the model while
    still scoring it, so this is required, never defaulted.

    Args:
        layouts: The bank layout registry.
        layout_id: The layout this statement will be rendered with.

    Returns:
        The inclusive (min, max) transaction count for that layout.

    Raises:
        ValueError: If the layout omits `transaction_count` or it is malformed.
    """
    spec = layouts.get(layout_id, {}).get("transaction_count")
    path = "config/layouts/bank_statements.yml"
    if not isinstance(spec, dict) or "min" not in spec or "max" not in spec:
        msg = (
            "Missing transaction_count.\n"
            f"  What:     layout '{layout_id}' declares no usable transaction_count.\n"
            f"  Where:    {path} -> {layout_id}.transaction_count\n"
            "  Expected: transaction_count: {min: <int>, max: <int>} — the "
            "transactions this layout fits on one page.\n"
            f"  Recover:  add a transaction_count block under {layout_id}, sized "
            "by rendering the corpus and finding where the body leaves the page."
        )
        raise ValueError(msg)
    lo, hi = int(spec["min"]), int(spec["max"])
    if lo < 1 or hi < lo:
        msg = (
            "Invalid transaction_count.\n"
            f"  What:     layout '{layout_id}' has min={lo}, max={hi}.\n"
            f"  Where:    {path} -> {layout_id}.transaction_count\n"
            "  Expected: 1 <= min <= max.\n"
            f"  Recover:  correct the bounds under {layout_id}.transaction_count."
        )
        raise ValueError(msg)
    return lo, hi


def _layout_categories(layouts: dict, layout_id: str) -> list[str]:
    """Return the service categories an invoice layout's content may draw.

    Declared beside the layout's design (`service_categories:` in config/layouts/invoices.yml) and
    validated there by `pipeline validate`, so a trade layout is never seeded with a legal supplier.

    Args:
        layouts: The invoice layout registry.
        layout_id: The layout this invoice will be rendered with.

    Returns:
        The layout's categories, in their YAML order.

    Raises:
        ValueError: If the layout declares no non-empty `service_categories` list.
    """
    categories = layouts.get(layout_id, {}).get("service_categories")
    if not isinstance(categories, list) or not categories:
        msg = (
            "Missing service_categories.\n"
            f"  What:     invoice layout '{layout_id}' declares no usable service_categories "
            f"({categories!r}).\n"
            f"  Where:    {_INVOICE_LAYOUT_PATH.resolve()} -> {layout_id}.service_categories\n"
            "  Expected: service_categories: [accounting, legal], names from service_categories in "
            "config/data_pools.yml.\n"
            f"  Recover:  add the categories this layout's design fits under {layout_id}."
        )
        raise ValueError(msg)
    return [str(c) for c in categories]


def _layouts_serving(layouts: dict, category: str) -> list[str]:
    """Return the receipt layouts whose `merchant_categories` include a merchant's category, in YAML order.

    Args:
        layouts: The receipt layout registry.
        category: The merchant's receipt category.

    Returns:
        The layout ids that print that category's receipts.

    Raises:
        ValueError: A receipt layout declares no `merchant_categories` list, or none serves the category.
    """
    for layout_id, layout in layouts.items():
        declared = layout.get("merchant_categories")
        if not isinstance(declared, list) or not declared:
            msg = (
                "Missing merchant_categories.\n"
                f"  What:     receipt layout '{layout_id}' declares no usable merchant_categories "
                f"({declared!r}), so the merchants it serves cannot be told.\n"
                f"  Where:    {_RECEIPT_LAYOUT_PATH.resolve()} -> {layout_id}.merchant_categories\n"
                "  Expected: merchant_categories: [grocery, pharmacy], names from receipt_categories in "
                "config/data_pools.yml.\n"
                f"  Recover:  add the merchant categories this layout's chrome suits under {layout_id}, "
                "then run `python -m generators.pipeline validate`."
            )
            raise ValueError(msg)
    serving = [lid for lid, layout in layouts.items() if category in layout["merchant_categories"]]
    if not serving:
        msg = (
            "No receipt layout for a merchant category.\n"
            f"  What:     no receipt layout lists '{category}' under merchant_categories, so its receipts "
            "have no layout to print on.\n"
            f"  Where:    {_RECEIPT_LAYOUT_PATH.resolve()} -> <layout>.merchant_categories\n"
            f"  Expected: at least one layout with merchant_categories: [..., {category}].\n"
            f"  Recover:  add '{category}' to the merchant_categories of the layout whose chrome suits it."
        )
        raise ValueError(msg)
    return serving


def _catalogue_lines(engine: ContentEngine, catalogue: str, category: str) -> list[dict]:
    """Return the lines a category's documents may print, from its entry in a line catalogue.

    Args:
        engine: The content engine, holding config/data_pools.yml.
        catalogue: `category_catalog` (invoice lines) or `product_catalog` (receipt lines).
        category: The category the document's supplier was drawn for.

    Returns:
        The category's lines, in catalogue order.

    Raises:
        ValueError: The catalogue has no non-empty entry for the category.
    """
    lines = engine.pools[catalogue].get(category)
    if not isinstance(lines, list) or not lines:
        msg = (
            "Missing catalogue lines.\n"
            f"  What:     category '{category}' has no lines in {catalogue}, so the seed cannot bill it.\n"
            f"  Where:    {Path('config/data_pools.yml').resolve()} -> {catalogue}.{category}\n"
            f"  Expected: {catalogue}: {{{category}: [{{description: ..., unit: ea, price_low: 10, "
            "price_high: 20, quantity: [1, 2]}]}}.\n"
            f"  Recover:  add the lines this category's documents print under {catalogue}.{category}."
        )
        raise ValueError(msg)
    return lines


@functools.cache
def _seeded_invoice_categories() -> tuple[str, ...]:
    """Every category the seeded invoice layouts may draw, first appearance first, for bank merchants."""
    layouts = load_layout_registry(_INVOICE_LAYOUT_PATH)
    ordered: list[str] = []
    for layout_id in _layout_ids(_INVOICE_LAYOUT_PATH):
        ordered += [c for c in _layout_categories(layouts, layout_id) if c not in ordered]
    return tuple(ordered)


def _rand_date(rng: random.Random, year_start: int = 2023, year_end: int = 2024) -> tuple[int, int, int]:
    """Generate a random date tuple (day, month, year)."""
    year = rng.randint(year_start, year_end)
    month = rng.randint(1, 12)
    max_day = 28 if month == 2 else 30 if month in (4, 6, 9, 11) else 31
    day = rng.randint(1, max_day)
    return day, month, year


def _rand_amount(rng: random.Random, lo: float, hi: float) -> Decimal:
    """Generate a random Decimal amount in [lo, hi] rounded to 2dp."""
    raw = rng.uniform(lo, hi)
    return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt_decimal(d: Decimal) -> str:
    """Format Decimal as plain string with 2dp (no $ sign)."""
    return f"{d:.2f}"


def _generate_case_entities(engine: ContentEngine, rng: random.Random, count: int) -> list[dict]:
    """Generate each case's shared entities once, projected across bank/cc/invoice.

    Receipts have no payer/holder field, so they draw their own supplier
    independently and do not consume this bundle.
    """
    return [{"holder": engine.person(rng), "location": engine.location(rng)} for _ in range(count)]


def _statement_merchant(name: str) -> str:
    """A merchant as a statement prints it: upper case, cut to 12 characters, no trailing space or '&'."""
    return name.upper()[:12].rstrip(" &")


def _bank_pool(engine: ContentEngine, direction: str) -> dict:
    """Return the description templates of one direction, `bank_descriptions.<direction>`, checked.

    Raises:
        ValueError: The pool is missing, empty, or an entry lacks its template, amount range or step.
    """
    pool = engine.pools["bank_descriptions"].get(direction)
    key = f"bank_descriptions.{direction}"
    example = '{eftpos: {template: "EFTPOS {merchant} {location} AUS", amount: [6.50, 240.00], step: 0.01}}'
    entries_ok = (
        isinstance(pool, dict)
        and pool
        and all(
            isinstance(spec, dict)
            and sorted(spec) == ["amount", "step", "template"]
            and isinstance(spec["template"], str)
            and isinstance(spec["amount"], list)
            and len(spec["amount"]) == 2
            and 0 < spec["amount"][0] <= spec["amount"][1]
            and spec["step"] > 0
            for spec in pool.values()
        )
    )
    if not entries_ok:
        msg = (
            "Malformed bank description pool.\n"
            f"  What:     {key} is {pool!r}, not a mapping of kind to {{template, amount, step}}.\n"
            f"  Where:    {Path('config/data_pools.yml').resolve()} -> {key}\n"
            f"  Expected: {key}: {example}, amount [min, max] with 0 < min <= max and step > 0.\n"
            f"  Recover:  correct {key} in config/data_pools.yml."
        )
        raise ValueError(msg)
    return pool


def _draw_bank_row(
    engine: ContentEngine,
    rng: random.Random,
    direction: str,
    *,
    suburb: str,
    holder_first: str,
    savings: str,
) -> tuple[str, Decimal]:
    """Draw a row's description and amount from its direction's pool, `bank_descriptions.<direction>`.

    Money out (purchases, bills, withdrawals, transfers out, fees) is described only by a debit template,
    money in (salary, refunds, transfers in, direct credits) only by a credit one, each with its own
    amount range, rounded to its `step` (an ATM pays out in twenties). The merchant is upper-cased, as
    real AU statements print it, which also keeps `_extract_suburb()` in scripts/seed_transaction_links.py
    (an ALL-CAPS merchant token before a Title-Case suburb) parsing EFTPOS descriptions correctly.

    `savings` is the holder's own savings account number, fixed per statement (a transfer to or from it
    names the same account every time).

    The draws are made in a fixed order whatever the direction (template, category, merchant, the four
    reference numbers, the amount), so the rows of one statement never shift another's.
    """
    pool = _bank_pool(engine, direction)
    template_key = sample(rng, list(pool.keys()))
    spec = pool[template_key]
    all_categories = engine.pools["receipt_categories"] + list(_seeded_invoice_categories())
    category = sample(rng, all_categories)
    merchant = _statement_merchant(engine.fictional_business_name(rng, category))
    description = spec["template"].format(
        merchant=merchant,
        location=suburb,
        last4=f"{rng.randint(1000, 9999)}",
        biller=merchant,
        crn=rng.randint(100000000, 999999999),
        ref=f"REF{rng.randint(10000, 99999)}",
        mhf=rng.randint(1000, 9999),
        name=holder_first,
        savings=savings,
    )
    low, high = spec["amount"]
    step = Decimal(str(spec["step"]))
    raw = Decimal(str(rng.uniform(low, high)))
    amount = ((raw / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step).quantize(Decimal("0.01"))
    return description, amount


def _debit_share(engine: ContentEngine) -> float:
    """The share of a statement's rows that are money out, `bank_descriptions.debit_share`."""
    share = engine.pools["bank_descriptions"].get("debit_share")
    if not isinstance(share, int | float) or isinstance(share, bool) or not 0 < share < 1:
        msg = (
            "Malformed bank debit share.\n"
            f"  What:     bank_descriptions.debit_share is {share!r}, not a fraction between 0 and 1.\n"
            f"  Where:    {Path('config/data_pools.yml').resolve()} -> bank_descriptions.debit_share\n"
            "  Expected: bank_descriptions: {debit_share: 0.80, ...}\n"
            "  Recover:  set bank_descriptions.debit_share in config/data_pools.yml."
        )
        raise ValueError(msg)
    return float(share)


def _generate_bank_entries(
    engine: ContentEngine, rng: random.Random, case_entities: list[dict], count: int
) -> tuple[dict, dict[str, Decimal]]:
    """Generate bank statement ground truth entries, each row's description drawn by its direction.

    Returns:
        The entries, their ACCOUNT_BALANCE not yet set, and for each case how far above its account's
        floor its lowest running balance is to sit (`bank_balance.lowest_above_floor`); `_settle_balances`
        sets each closing balance from that once the link seeder's rows are known.
    """
    entries: dict = {}
    lowest: dict[str, Decimal] = {}
    layout_draw = NonRepeatingSampler(rng, _layout_ids(_BANK_LAYOUT_PATH))
    banks_by_code = {b["code"]: b for b in engine.pools["banks"]}
    bank_layouts = load_layout_registry(_BANK_LAYOUT_PATH)
    debit_share = _debit_share(engine)
    cushion = lowest_above_floor(engine.pools)

    for i in range(count):
        case_id = f"CASE{i + 1:03d}"
        layout = layout_draw.draw()

        # The bank is a function of the layout, never an independent draw: a CBA
        # layout must carry a CBA supplier, or the SUPPLIER_NAME the VLM is scored
        # against contradicts the letterhead. The layout-id prefix is the pool code.
        bank = banks_by_code[layout.split("_")[0]]
        holder = case_entities[i]["holder"]
        suburb = case_entities[i]["location"]["suburb"]

        _d, m, y = _rand_date(rng)
        period_start = _fmt_date(1, m, y)
        max_day = 28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31
        period_end = _fmt_date(max_day, m, y)
        statement_range = f"{period_start} - {period_end}"

        n_txns = rng.randint(*_txn_count_range(bank_layouts, layout))
        txn_days = sorted(rng.randint(1, max_day) for _ in range(n_txns))

        txn_dates, txn_descs, txn_debits, txn_credits = [], [], [], []
        lowest[case_id] = _rand_amount(rng, *cushion)
        # The holder's savings account, a function of the case so it costs the generator no draw.
        savings = f"{int(hashlib.sha256(f'{case_id}:savings'.encode()).hexdigest(), 16) % 9000 + 1000}"

        for txn_day in txn_days:
            txn_dates.append(_fmt_date(txn_day, m, y))
            direction = "debit" if rng.random() < debit_share else "credit"
            desc, amt = _draw_bank_row(
                engine, rng, direction, suburb=suburb, holder_first=holder["first_name"], savings=savings
            )
            txn_descs.append(desc)
            txn_debits.append(_fmt_decimal(amt) if direction == "debit" else "NOT_FOUND")
            txn_credits.append(_fmt_decimal(amt) if direction == "credit" else "NOT_FOUND")

        entries[case_id] = {
            "layout": layout,
            "degradation_seed": rng.randint(1000, 9999),
            "fields": {
                "DOCUMENT_TYPE": "BANK_STATEMENT",
                "SUPPLIER_NAME": bank["name"],
                "STATEMENT_DATE_RANGE": statement_range,
                "TRANSACTION_DATES": "|".join(txn_dates),
                "TRANSACTION_DESCRIPTIONS": "|".join(txn_descs),
                "TRANSACTION_AMOUNTS_PAID": "|".join(txn_debits),
                "TRANSACTION_AMOUNTS_RECEIVED": "|".join(txn_credits),
                "ACCOUNT_BALANCE": "NOT_FOUND",
                "PAYER_NAME": holder["full_name"],
            },
        }

    return entries, lowest


def _settle_balances(all_entries: dict[str, dict], lowest: dict[str, Decimal], pools: dict) -> None:
    """Set each statement's closing balance so its running balance stays at or above its floor.

    The link seeder runs after this seed and rewrites the statements (a linked purchase replaces a
    debit's amount, its row moves to the purchase's date), and the page prints the statement it leaves.
    So the rows are taken from a copy linked by the link seeder's own function, and the opening balance
    is drawn as the account's floor, plus the statement's drawn margin above it, plus the deepest the rows
    take the balance below where it opened. The closing balance follows: the opening, less what goes out,
    plus what comes in. Then the linked copy is checked as validate checks the corpus, and any refusal
    (a row against its description, a balance under the floor) stops the seed.

    Args:
        all_entries: Doc type (`bank_statements`, `receipts`, `invoices`) -> case id -> entry; each bank
            statement's ACCOUNT_BALANCE is set in place.
        lowest: Case id -> how far above the floor the lowest running balance sits.
        pools: The loaded config/data_pools.yml.

    Raises:
        RuntimeError: A linked statement breaks a bank_ledger rule.
    """
    floor = balance_floor(pools)
    linked, _links = _linked_copies({f"{doc}.yml": entries for doc, entries in all_entries.items()})
    errors: list[str] = []
    for case_id, entry in all_entries["bank_statements"].items():
        copy_fields = linked["bank_statements.yml"][case_id]["fields"]
        net = Decimal("0")
        deepest = Decimal("0")
        for _description, paid, received in ledger_rows(copy_fields):
            net += (received or Decimal("0")) - (paid or Decimal("0"))
            deepest = min(deepest, net)
        opening = floor + lowest[case_id] - deepest
        closing = _fmt_decimal(opening + net)
        entry["fields"]["ACCOUNT_BALANCE"] = closing
        copy_fields["ACCOUNT_BALANCE"] = closing
        errors += check_statement(
            case_id, copy_fields, pools, source=Path("scripts/seed_ground_truth.py").resolve()
        )
    if errors:
        listing = "\n".join(errors)
        msg = (
            "Generated bank statements break the statement row rules.\n"
            f"  What:     {len(errors)} refusal(s) on the statements as the link seeder will leave them:\n"
            f"{listing}\n"
            "  Where:    scripts/seed_ground_truth.py (_draw_bank_row, _settle_balances) and "
            "config/data_pools.yml -> bank_descriptions, bank_balance.\n"
            "  Expected: every row described by its direction's pool and every running balance at or above "
            "the floor.\n"
            "  Recover:  correct the pools or the seed logic named above, then rerun the seed."
        )
        raise RuntimeError(msg)


def _generate_receipt_entries(
    engine: ContentEngine,
    rng: random.Random,
    case_entities: list[dict],
    count: int,
    registered: Container[str],
) -> dict:
    """Generate receipt ground truth entries with GST-inclusive pricing."""
    entries: dict = {}
    receipt_layouts = load_layout_registry(_RECEIPT_LAYOUT_PATH)

    for i in range(count):
        case_id = f"CASE{i + 1:03d}"
        # The merchant first, then a layout whose chrome suits it: a fuel layout's "Drive safely" is
        # printed only by the merchants it declares.
        category = sample(rng, engine.pools["receipt_categories"])
        layout = sample(rng, _layouts_serving(receipt_layouts, category))
        retailer = engine.fictional_business(rng, category, registered)

        d, m, y = _rand_date(rng)
        invoice_date = _fmt_date(d, m, y)

        # A merchant sells its own category's goods: a cellar never rings up yoghurt.
        item_pool = _catalogue_lines(engine, "product_catalog", category)
        n_items = rng.randint(1, 6)
        items = rng.sample(item_pool, min(n_items, len(item_pool)))

        item_descs, item_qtys, item_prices, item_totals = [], [], [], []
        gst_inclusive_total = Decimal("0")

        for item in items:
            qty = rng.randint(*item["quantity"])
            unit_price = _rand_amount(rng, item["price_low"], item["price_high"])
            line_total = (unit_price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item_descs.append(item["description"])
            item_qtys.append(str(qty))
            item_prices.append(_fmt_decimal(unit_price))
            item_totals.append(_fmt_decimal(line_total))
            gst_inclusive_total += line_total

        gst_amount = (gst_inclusive_total / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        entries[case_id] = {
            "layout": layout,
            # Non-scored metadata (config/field_definitions.yml -> entry_metadata): the merchant's category.
            "merchant_category": category,
            "degradation_seed": rng.randint(1000, 9999),
            "fields": {
                "DOCUMENT_TYPE": "RECEIPT",
                "SUPPLIER_NAME": retailer["name"],
                "BUSINESS_ABN": retailer["abn"],
                "BUSINESS_ADDRESS": retailer["address"],
                "INVOICE_DATE": invoice_date,
                "IS_GST_INCLUDED": "true",
                "GST_AMOUNT": _fmt_decimal(gst_amount),
                "TOTAL_AMOUNT": _fmt_decimal(gst_inclusive_total),
                "LINE_ITEM_DESCRIPTIONS": "|".join(item_descs),
                "LINE_ITEM_QUANTITIES": "|".join(item_qtys),
                "LINE_ITEM_PRICES": "|".join(item_prices),
                "LINE_ITEM_TOTAL_PRICES": "|".join(item_totals),
            },
        }

    return entries


def _generate_invoice_entries(
    engine: ContentEngine,
    rng: random.Random,
    case_entities: list[dict],
    count: int,
    registered: Container[str],
) -> dict:
    """Generate invoice ground truth entries with GST-exclusive pricing."""
    entries: dict = {}
    layout_draw = NonRepeatingSampler(rng, _layout_ids(_INVOICE_LAYOUT_PATH))
    invoice_layouts = load_layout_registry(_INVOICE_LAYOUT_PATH)

    for i in range(count):
        case_id = f"CASE{i + 1:03d}"
        layout = layout_draw.draw()

        category = sample(rng, _layout_categories(invoice_layouts, layout))
        provider = engine.fictional_business(rng, category, registered)

        d, m, y = _rand_date(rng)
        invoice_date = _fmt_date(d, m, y)
        due_day = min(d + 30, 28)
        due_m = m + 1 if due_day < d else m
        due_y = y + 1 if due_m > 12 else y
        due_m = due_m % 12 if due_m > 12 else due_m
        if due_m == 0:
            due_m = 12
        payment_due_date = _fmt_date(due_day, due_m, due_y)

        holder = case_entities[i]["holder"]
        payer_address = engine.address(rng)

        # Lines come only from the category's own catalogue: one to four of them, every line equally
        # likely to be billed and to be printed first, in the order drawn. No line leads by position,
        # so a category does not decide LINE_ITEM_DESCRIPTIONS[0].
        catalogue = _catalogue_lines(engine, "category_catalog", category)
        n_items = min(rng.randint(1, 4), len(catalogue))
        services = rng.sample(catalogue, n_items)

        item_descs, item_qtys, item_prices, item_totals = [], [], [], []
        subtotal_ex_gst = Decimal("0")

        for svc in services:
            qty = rng.randint(*svc["quantity"])
            unit_price = _rand_amount(rng, svc["price_low"], svc["price_high"])
            line_total = (unit_price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item_descs.append(svc["description"])
            item_qtys.append(str(qty))
            item_prices.append(_fmt_decimal(unit_price))
            item_totals.append(_fmt_decimal(line_total))
            subtotal_ex_gst += line_total

        gst_amount = (subtotal_ex_gst * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_incl_gst = subtotal_ex_gst + gst_amount

        entries[case_id] = {
            "layout": layout,
            # Non-scored metadata beside the layout (config/field_definitions.yml -> entry_metadata):
            # the category the supplier, its lines and its distractor text were drawn for. No derived
            # or exported view reads it.
            "service_category": category,
            "degradation_seed": rng.randint(1000, 9999),
            "fields": {
                "DOCUMENT_TYPE": "INVOICE",
                "SUPPLIER_NAME": provider["name"],
                "BUSINESS_ABN": provider["abn"],
                "BUSINESS_ADDRESS": provider["address"],
                "INVOICE_DATE": invoice_date,
                # Records whether GST is included in TOTAL_AMOUNT — the Tax Office
                # compliance question. TOTAL_AMOUNT is total_incl_gst, so this is
                # true even though the LINE ITEM prices are GST-exclusive. Do not
                # set it from how the prices are presented: that is a different
                # question, and docile.py keys TOTAL_AMOUNT_GROSS/NET off this flag.
                "IS_GST_INCLUDED": "true",
                "GST_AMOUNT": _fmt_decimal(gst_amount),
                "TOTAL_AMOUNT": _fmt_decimal(total_incl_gst),
                "LINE_ITEM_DESCRIPTIONS": "|".join(item_descs),
                "LINE_ITEM_QUANTITIES": "|".join(item_qtys),
                "LINE_ITEM_PRICES": "|".join(item_prices),
                "LINE_ITEM_TOTAL_PRICES": "|".join(item_totals),
                "PAYER_NAME": holder["full_name"],
                "PAYER_ADDRESS": payer_address,
            },
        }

    return entries


def _linked_copies(all_entries: dict[str, dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Copies of the generated entries as the link seeder will leave them, and the links it will write.

    scripts/seed_transaction_links.py runs after this seed and rewrites what it wrote: each case's receipt
    and invoice get one shared date with their bank transaction, the linked bank rows are injected and
    each statement is re-sorted. The dry run applies the link seeder's own `link_corpus` to deep copies,
    so the rule lives once, receipts are linked as well as invoices, and every distractor rule that reads
    links (the PAID stamp's date, the annotated statement rows) reads the corpus as it will be.
    """
    copies = copy.deepcopy(all_entries)
    links = link_corpus(copies["receipts.yml"], copies["invoices.yml"], copies["bank_statements.yml"])
    return copies, links


def _validate_dry_run(all_entries: dict[str, dict]) -> None:
    """Validate generated entries in-memory (schema + overflow) without writing YAML.

    The entries are validated and rendered as the link seeder will leave them (`_linked_copies`), since
    those, not the pre-link entries, are what the corpus will hold; `all_entries` is not changed.
    """
    from generators.bank_statement import render_bank_statement
    from generators.invoice import render_invoice
    from generators.receipt import render_receipt

    renderer_map = {
        "bank_statements.yml": (render_bank_statement, "config/layouts/bank_statements.yml"),
        "receipts.yml": (render_receipt, "config/layouts/receipts.yml"),
        "invoices.yml": (render_invoice, "config/layouts/invoices.yml"),
    }

    errors: list[str] = []
    # The committed links file describes the committed corpus, not these entries, whose links
    # scripts/seed_transaction_links.py writes afterwards. The distractor rules that read links read the
    # links its own function builds here, in memory, over the linked copies rather than the stale file.
    linked, links = _linked_copies(all_entries)
    with links_in_memory(links):
        for filename, entries in linked.items():
            renderer, layout_path = renderer_map[filename]
            layouts = load_layout_registry(Path(layout_path))
            for case_id, entry in entries.items():
                errors.extend(validate_entry(str(case_id), entry))
            errors.extend(check_overflow(entries, layouts, renderer))

    if errors:
        listing = "\n    ".join(errors)
        raise RuntimeError(
            "Dry run failed: generated content did not validate.\n"
            f"  What:     {len(errors)} error(s) across generated entries:\n"
            f"    {listing}\n"
            "  Where:    scripts/seed_ground_truth.py generator functions and the "
            "config/data_pools.yml content they draw from.\n"
            "  Expected: every generated entry passes schema validation and renders "
            "within its layout's field_budgets (no FitError).\n"
            "  Recover:  fix the failing generator logic or widen the offending pool/budget, "
            "then rerun `python scripts/seed_ground_truth.py --dry-run`."
        )


def build_all_entries() -> dict[str, dict]:
    """Generate all document-type entries in-memory, keyed by doc type (no I/O).

    Both `main()` (which appends `.yml` for the write/dry-run paths) and tests
    that need the generated entries without touching the filesystem call this.
    """
    rng = random.Random(_SEED)
    # generate_abn() draws from the module-global RNG; seed it too so ABN
    # digit fields are reproducible run-to-run, not just content selection.
    random.seed(_SEED)
    engine = build_engine()
    # Every invented ABN is screened against the public register, so none is a real business's.
    registered = abn_register.load_abn_register(engine.pools, _DATA_POOLS_PATH)
    case_entities = _generate_case_entities(engine, rng, _COUNT)

    all_entries: dict[str, dict] = {}
    all_entries["bank_statements"], lowest = _generate_bank_entries(engine, rng, case_entities, _COUNT)
    all_entries["receipts"] = _generate_receipt_entries(engine, rng, case_entities, _COUNT, registered)
    all_entries["invoices"] = _generate_invoice_entries(engine, rng, case_entities, _COUNT, registered)
    # The closing balances wait for every document: the link seeder's rows depend on the receipts and
    # invoices, and the floor is checked on the statements it will leave.
    _settle_balances(all_entries, lowest, engine.pools)
    return all_entries


def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate in-memory; do not write ground_truth/*.yml"
    ),
) -> None:
    """Generate all ground truth YAML files with deterministic seed=42."""
    all_entries = build_all_entries()

    if dry_run:
        _validate_dry_run({f"{doc_type}.yml": entries for doc_type, entries in all_entries.items()})
        print("Dry run: all entries validated in-memory; ground_truth/*.yml NOT written.")
        return

    _GT_DIR.mkdir(parents=True, exist_ok=True)
    for doc_type, entries in all_entries.items():
        out_path = _GT_DIR / f"{doc_type}.yml"
        with out_path.open("w", encoding="utf-8") as fh:
            yaml.dump(entries, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"Wrote {len(entries)} entries to {out_path}")


if __name__ == "__main__":
    typer.run(main)
