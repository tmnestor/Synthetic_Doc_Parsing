"""Generate transaction linking ground truth from existing ground truth entries.

Reads receipts.yml, invoices.yml, and bank_statements.yml, then creates
matching pairs in transaction_links.yml. Produces a denormalized format keyed
by image filenames for direct consumption by the LMM evaluation pipeline.

Usage:
    python scripts/seed_transaction_links.py
"""

import random
from datetime import datetime
from pathlib import Path

import yaml

# Bank description shorthands matching real Australian bank statement conventions.
# Derived from existing TRANSACTION_DESCRIPTIONS in bank_statements.yml and
# the transaction_patterns section of config/data_pools.yml.
_RECEIPT_SHORTHANDS: dict[str, str] = {
    "Bunnings Warehouse": "BUNNINGS W/HOUSE",
    "Woolworths": "WOOLWORTHS",
    "Coles": "COLES",
    "Officeworks": "OFFICEWORKS",
    "JB Hi-Fi": "JB HI-FI",
    "Harvey Norman": "HARVEY NORMAN",
    "Kmart Australia": "KMART",
    "Big W": "Big W",
    "Chemist Warehouse": "CHEMIST WHSE",
    "Dan Murphy's": "DAN MURPHYS",
    "BP Australia": "BP",
    "Shell Australia": "SHELL",
    "Ampol Limited": "AMPOL",
    "7-Eleven Australia": "7-Eleven Aus",
    "Myer": "Myer",
    "David Jones": "David Jones",
    "Target Australia": "TARGET",
    "Spotlight": "SPOTLIGHT",
    "Supercheap Auto": "SUPERCHEAP AUTO",
    "Repco": "REPCO",
}

_INVOICE_SHORTHANDS: dict[str, str] = {
    "Smith & Associates Accounting": "SMITH ASSOCIATES",
    "Johnson Legal": "JOHNSON LEGAL",
    "Brisbane IT Solutions": "BRISBANE IT SOLUTIONS",
    "Adelaide Business Consulting": "ADELAIDE BUS CONSULTING",
    "Perth Marketing Group": "PERTH MARKETING GROUP",
}

# EFTPOS vs VISA DEBIT prefixes for retail transactions.
_RETAIL_PREFIXES = [
    ("EFTPOS", "AUS"),
    ("VISA DEBIT PURCHASE", "AU"),
]

# Payment prefixes for professional service invoices.
_INVOICE_PREFIXES = ["BPAY", "DIRECT DEBIT", "EFT PAYMENT"]


def _load_gt(name: str) -> dict:
    path = Path(f"ground_truth/{name}.yml")
    return yaml.safe_load(path.read_text())


def _parse_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        return None


def _extract_suburb(bank_entry: dict) -> str:
    """Extract the suburb used in a bank statement's transaction descriptions.

    All transactions for a given bank statement use the same suburb (the
    account holder's local area). Every description that names a place ends
    with it: a card purchase or refund before its "AU"/"AUS" suffix, an ATM
    withdrawal after its prefix. The suburb is the run of title-case words there,
    after the upper-case merchant name.

    Raises:
        ValueError: No description of the statement names its suburb, so a
            linked row would have to invent one.
    """
    descriptions = bank_entry["fields"].get("TRANSACTION_DESCRIPTIONS", "").split("|")
    for desc in descriptions:
        desc = desc.strip()
        if desc.startswith("ATM WITHDRAWAL "):
            words = desc[len("ATM WITHDRAWAL ") :].split()
        elif desc.endswith((" AU", " AUS")):
            words = desc.rsplit(" ", 1)[0].split()
        else:
            continue
        # Walk backwards: title-case words are the suburb, uppercase words
        # are the merchant name.
        suburb_words: list[str] = []
        for word in reversed(words):
            if word[0].isupper() and not word.isupper():
                suburb_words.insert(0, word)
            else:
                break
        if suburb_words:
            return " ".join(suburb_words)
    msg = (
        "Statement names no suburb.\n"
        f"  What:     no TRANSACTION_DESCRIPTIONS entry of the {bank_entry.get('layout')} statement ends "
        "with the holder's suburb (a card purchase, refund or ATM withdrawal), so a linked row's suburb "
        "would be invented.\n"
        "  Where:    ground_truth/bank_statements.yml, as written by scripts/seed_ground_truth.py.\n"
        "  Expected: at least one row such as 'EFTPOS ALPHA Town AUS' or 'ATM WITHDRAWAL Town'.\n"
        "  Recover:  regenerate with scripts/seed_ground_truth.py; if it recurs, widen the location-bearing "
        "kinds under bank_descriptions in config/data_pools.yml."
    )
    raise ValueError(msg)


def _abbrev(supplier: str) -> str:
    """A cryptic abbreviation of a merchant name: recognisable but not the full
    name (the 'medium' difficulty signal). First word, upper, capped at 10 chars."""
    first = supplier.split()[0] if supplier.split() else supplier
    return first.upper()[:10]


def _link_description(
    supplier: str,
    suburb: str,
    is_invoice: bool,
    difficulty: str,
    rng: random.Random,
) -> str:
    """Bank-statement description for a linked transaction, keyed to difficulty.

    Difficulty is defined by how recognisable the merchant is (the date is always
    an exact match):
      - easy:   the full merchant name appears verbatim.
      - medium: only a cryptic abbreviation appears (processor-style reference).
      - hard:   no merchant reference at all, a generic card/payment line.
    """
    if is_invoice:
        if difficulty == "easy":
            shorthand = _INVOICE_SHORTHANDS.get(supplier, supplier.upper())
            prefix = rng.choice(_INVOICE_PREFIXES)
            if prefix == "BPAY":
                return f"BPAY {shorthand} CRN {rng.randint(100_000_000, 999_999_999)}"
            return f"{prefix} {shorthand}"
        if difficulty == "medium":
            return f"{rng.choice(_INVOICE_PREFIXES)} {_abbrev(supplier)} REF{rng.randint(10_000, 99_999)}"
        return f"DIRECT DEBIT REF{rng.randint(100_000, 999_999)}"

    if difficulty == "easy":
        shorthand = _RECEIPT_SHORTHANDS.get(supplier, supplier.upper())
        prefix, suffix = rng.choice(_RETAIL_PREFIXES)
        return f"{prefix} {shorthand} {suburb} {suffix}"
    if difficulty == "medium":
        prefix, suffix = rng.choice(_RETAIL_PREFIXES)
        return f"{prefix} SQ *{_abbrev(supplier)} {suburb} {suffix}"
    prefix, suffix = rng.choice(_RETAIL_PREFIXES)
    return f"{prefix} CARD {rng.randint(1000, 9999)} {suburb} {suffix}"


def _statement_month(bank_fields: dict) -> tuple[int, int]:
    """The statement's (year, month), taken as the modal month of its transaction
    dates. The ~30+ in-period base transactions dominate any linked outliers, so
    the mode is robust even before the outliers are pulled back into the period."""
    from collections import Counter

    ym: Counter[tuple[int, int]] = Counter()
    for d in bank_fields.get("TRANSACTION_DATES", "").split("|"):
        parsed = _parse_date(d)
        if parsed:
            ym[(parsed.year, parsed.month)] += 1
    (year, month), _ = ym.most_common(1)[0]
    return year, month


def _month_length(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def _sort_transactions(bank_fields: dict) -> None:
    """Sort the four parallel transaction arrays chronologically (ascending),
    keeping rows aligned. Stable, so same-date rows keep their relative order."""
    keys = [
        "TRANSACTION_DATES",
        "TRANSACTION_DESCRIPTIONS",
        "TRANSACTION_AMOUNTS_PAID",
        "TRANSACTION_AMOUNTS_RECEIVED",
    ]
    cols = [bank_fields.get(k, "").split("|") for k in keys]
    rows = list(zip(*cols))
    rows.sort(key=lambda r: _parse_date(r[0]) or datetime.min)
    for k, col in zip(keys, zip(*rows)):
        bank_fields[k] = "|".join(col)


def _recompute_range(bank_fields: dict) -> None:
    """STATEMENT_DATE_RANGE = earliest - latest transaction date."""
    dates = [d for d in (_parse_date(x) for x in bank_fields["TRANSACTION_DATES"].split("|")) if d]
    lo, hi = min(dates), max(dates)
    bank_fields["STATEMENT_DATE_RANGE"] = f"{lo.strftime('%d/%m/%Y')} - {hi.strftime('%d/%m/%Y')}"


def _position_label(matched_idx: int, total_txns: int) -> str:
    if matched_idx < total_txns / 3:
        return "Early row"
    if matched_idx < 2 * total_txns / 3:
        return "Mid row"
    return "Late row"


def _build_notes(
    matched_idx: int,
    total_txns: int,
    bank_layout: str,
    difficulty: str,
) -> str:
    position = _position_label(matched_idx, total_txns)
    layout_label = bank_layout.replace("_", " ")

    # Date and amount always match exactly (the common real-world case); the
    # difficulty is carried entirely by how recognisable the merchant is.
    if difficulty == "easy":
        characteristic = "exact date and amount, full merchant name"
    elif difficulty == "medium":
        characteristic = "exact date and amount, abbreviated merchant reference"
    else:
        characteristic = "exact date and amount, no merchant reference in description"

    return f"{position} on {layout_label}: {characteristic}"


def link_corpus(receipts: dict, invoices: dict, bank_stmts: dict) -> dict[str, list[dict]]:
    """Link every case's receipt and invoice to its bank statement, editing the entries in place.

    The one statement of the linking rule: each source gets one shared in-period date (its INVOICE_DATE
    and its bank transaction's date), the linked bank row becomes a debit for the source total with a
    description keyed to the drawn difficulty, and each statement is then sorted and its period
    recomputed. `main` applies it to the committed ground truth; the ground-truth seed's dry run applies
    it to copies of the entries it generates, so both build exactly the same links.

    Args:
        receipts: Case id -> receipt entry; INVOICE_DATE is rewritten.
        invoices: Case id -> invoice entry; INVOICE_DATE is rewritten.
        bank_stmts: Case id -> bank statement entry; linked rows are injected, rows sorted and the
            period recomputed.

    Returns:
        Image filename -> its links, as ground_truth/transaction_links.yml maps them.
    """
    rng = random.Random(42)
    links: dict[str, list[dict]] = {}

    # Fixed difficulty budget across the 110 links (the documented 52 easy /
    # 36 medium / 22 hard split), shuffled deterministically and drawn in
    # processing order so the exact distribution is preserved.
    difficulty_pool = ["easy"] * 52 + ["medium"] * 36 + ["hard"] * 22
    rng.shuffle(difficulty_pool)
    difficulty_iter = iter(difficulty_pool)

    # Each case pairs its receipt and invoice with its bank statement
    # (deterministic within-case pairing via shared CASE### prefix)
    for case_num in range(1, 56):
        case_id = f"CASE{case_num:03d}"
        bank_entry = bank_stmts[case_id]
        bank_fields = bank_entry["fields"]
        suburb = _extract_suburb(bank_entry)

        # The statement's period is its modal transaction month. Every linked
        # date is pulled back into this month so the statement stays a realistic
        # ~1-month document instead of spanning the receipt's unrelated date.
        year, month = _statement_month(bank_fields)
        month_len = _month_length(year, month)
        used_slots: set[int] = set()

        for src_entry, is_invoice in [
            (receipts[case_id], False),
            (invoices[case_id], True),
        ]:
            src_fields = src_entry["fields"]
            src_total = src_fields.get("TOTAL_AMOUNT", "")
            supplier = src_fields.get("SUPPLIER_NAME", "")
            if not src_total:
                continue

            # Difficulty is carried by description quality; the date is always an
            # exact match. Drawn from the fixed 52/36/22 budget above.
            difficulty = next(difficulty_iter)

            # One shared in-period date for the source doc AND its bank
            # transaction: the common real-world case where a purchase appears on
            # the statement on the day it was made.
            shared_date = datetime(year, month, rng.randint(1, month_len)).strftime("%d/%m/%Y")
            src_fields["INVOICE_DATE"] = shared_date

            bank_amounts = bank_fields["TRANSACTION_AMOUNTS_PAID"].split("|")
            bank_dates = bank_fields["TRANSACTION_DATES"].split("|")
            bank_descriptions = bank_fields["TRANSACTION_DESCRIPTIONS"].split("|")
            bank_credits = bank_fields["TRANSACTION_AMOUNTS_RECEIVED"].split("|")

            # Locate the transaction to carry this link: an existing slot already
            # holding the source total (idempotent re-runs), else a fresh debit
            # slot. `used_slots` stops a case's receipt and invoice colliding when
            # they share a total.
            matched_idx = next(
                (i for i, a in enumerate(bank_amounts) if a.strip() == src_total and i not in used_slots),
                None,
            )
            if matched_idx is None:
                candidates = [
                    i
                    for i, a in enumerate(bank_amounts)
                    if a.strip() not in ("", "NOT_FOUND") and i not in used_slots
                ]
                matched_idx = candidates[rng.randrange(len(candidates))] if candidates else 0
            used_slots.add(matched_idx)

            # A linked transaction is a debit for the source total, on the shared
            # date, described according to difficulty.
            bank_amounts[matched_idx] = src_total
            bank_credits[matched_idx] = "NOT_FOUND"
            bank_dates[matched_idx] = shared_date
            description = _link_description(supplier, suburb, is_invoice, difficulty, rng)
            bank_descriptions[matched_idx] = description

            bank_fields["TRANSACTION_AMOUNTS_PAID"] = "|".join(bank_amounts)
            bank_fields["TRANSACTION_AMOUNTS_RECEIVED"] = "|".join(bank_credits)
            bank_fields["TRANSACTION_DATES"] = "|".join(bank_dates)
            bank_fields["TRANSACTION_DESCRIPTIONS"] = "|".join(bank_descriptions)

            links[f"{case_id}_{src_entry['layout']}.png"] = [
                {
                    "bank_statement": f"{case_id}_{bank_entry['layout']}.png",
                    "supplier": supplier,
                    "receipt_date": shared_date,
                    "receipt_total": src_total,
                    "bank_date": shared_date,
                    "bank_description": description,
                    "bank_amount": src_total,
                    "match_status": "FOUND",
                    "match_difficulty": difficulty,
                }
            ]

        # After both sources are injected, sort the statement chronologically and
        # derive its period from the (now in-period) date column.
        _sort_transactions(bank_fields)
        _recompute_range(bank_fields)

        # Notes carry the post-sort row position, so build them now.
        sorted_dates = bank_fields["TRANSACTION_DATES"].split("|")
        sorted_amounts = bank_fields["TRANSACTION_AMOUNTS_PAID"].split("|")
        total_txns = len(sorted_dates)
        for src_file, records in links.items():
            if not src_file.startswith(f"{case_id}_"):
                continue
            for rec in records:
                final_idx = next(
                    (
                        i
                        for i, (d, a) in enumerate(zip(sorted_dates, sorted_amounts))
                        if d.strip() == rec["bank_date"] and a.strip() == rec["bank_amount"]
                    ),
                    0,
                )
                rec["notes"] = _build_notes(
                    final_idx, total_txns, bank_entry["layout"], rec["match_difficulty"]
                )
    return links


def main() -> None:
    receipts = _load_gt("receipts")
    invoices = _load_gt("invoices")
    bank_stmts = _load_gt("bank_statements")
    links = link_corpus(receipts, invoices, bank_stmts)

    # Write updated ground truth: bank statements (injected + sorted), the
    # re-dated receipts/invoices, and the links themselves.
    #
    # allow_unicode=True matches scripts/seed_ground_truth.py. Without it PyYAML
    # escapes every non-ASCII character (a supplier or street name with an accent,
    # say) as a backslash-u escape, which forces double-quoted style and mid-word
    # line wrapping with trailing backslashes. The value round-trips either way, but
    # the escaped form is unreadable in review and moves the wrap point on any
    # edit, turning a one-word change into a multi-line diff.
    for path, payload in (
        ("ground_truth/bank_statements.yml", dict(bank_stmts)),
        ("ground_truth/receipts.yml", dict(receipts)),
        ("ground_truth/invoices.yml", dict(invoices)),
        ("ground_truth/transaction_links.yml", links),
    ):
        Path(path).write_text(
            yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True)
        )

    # Stats
    difficulties: dict[str, int] = {}
    for entry_list in links.values():
        for entry in entry_list:
            d = entry["match_difficulty"]
            difficulties[d] = difficulties.get(d, 0) + 1
    print(f"Generated {len(links)} transaction links: {difficulties}")


if __name__ == "__main__":
    main()
