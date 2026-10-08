"""Coherence of a bank statement's rows: each row's direction agrees with its description, and the
running balance of the account stays at or above its floor.

A statement row is money out (a purchase, a bill, a withdrawal, a transfer out, a fee) or money in (a
salary, a refund, a transfer in, a direct credit), and its description says which. A salary printed in
the debit column, or a card purchase in the credit column, is a page no bank prints; a taxpayer's
annotations then claim a salary as an expense. And an everyday personal account does not sit thousands of
dollars overdrawn for a month. Both rules are declared in config/data_pools.yml:

- `bank_descriptions.direction_prefixes` says how a printed description's direction is told (the seed's
  own templates and the link seeder's linked-row descriptions alike): money in when it starts with a
  `credit` prefix, money out when it starts with a `debit` prefix.
- `bank_balance` names the corpus's account kind and the floor its running balance may not go below.

The seed draws rows by direction and the opening balance so that both hold, and fails fast if they do
not (scripts/seed_ground_truth.py); `pipeline validate` checks every row of every committed statement.
The running balance is the one the statement prints: the opening balance is implied by the closing
balance (ACCOUNT_BALANCE) and the rows, and each row moves it in the order the ground truth lists them.
"""

from decimal import Decimal
from pathlib import Path

from generators.content_engine import _DATA_POOLS_PATH

NOT_FOUND = "NOT_FOUND"
DIRECTIONS = ("debit", "credit")


class BankLedgerError(ValueError):
    """The bank-statement coherence rules in config/data_pools.yml are missing or malformed."""


def _pool_error(key: str, what: str, expected: str) -> BankLedgerError:
    return BankLedgerError(
        "the bank statement row rules are missing or malformed.\n"
        f"  What:     {what}\n"
        f"  Where:    {_DATA_POOLS_PATH} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  correct `{key}` in {_DATA_POOLS_PATH}."
    )


def direction_prefixes(pools: dict) -> dict[str, tuple[str, ...]]:
    """Return `bank_descriptions.direction_prefixes`, checked.

    Args:
        pools: The loaded config/data_pools.yml.

    Returns:
        `debit` and `credit` -> the description prefixes that mark that direction.

    Raises:
        BankLedgerError: The block is missing, a direction is missing or empty, a prefix is not a string,
            or one prefix of each direction could both match one description.
    """
    key = "bank_descriptions.direction_prefixes"
    example = 'direction_prefixes: {debit: ["EFTPOS ", "BPAY "], credit: ["SALARY ", "REFUND "]}'
    block = pools.get("bank_descriptions", {}).get("direction_prefixes")
    if not isinstance(block, dict) or sorted(block) != sorted(DIRECTIONS):
        raise _pool_error(key, f"`{key}` is {block!r}, not a mapping of debit and credit.", example)
    prefixes: dict[str, tuple[str, ...]] = {}
    for direction in DIRECTIONS:
        values = block[direction]
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            raise _pool_error(
                f"{key}.{direction}", f"`{key}.{direction}` is {values!r}, not a list of prefixes.", example
            )
        prefixes[direction] = tuple(values)
    clashes = [
        (d, c) for d in prefixes["debit"] for c in prefixes["credit"] if d.startswith(c) or c.startswith(d)
    ]
    if clashes:
        raise _pool_error(
            key,
            f"debit and credit prefixes overlap, so one description could read as both: {clashes}.",
            f"no debit prefix starting with a credit prefix or the reverse, e.g. {example}",
        )
    return prefixes


def classify(description: str, prefixes: dict[str, tuple[str, ...]]) -> str | None:
    """Return the direction a description's wording gives, `debit` or `credit`, or None for neither."""
    for direction in DIRECTIONS:
        if any(description.startswith(prefix) for prefix in prefixes[direction]):
            return direction
    return None


def balance_floor(pools: dict) -> Decimal:
    """Return the floor of the corpus's account kind, from `bank_balance`.

    Raises:
        BankLedgerError: `bank_balance` is missing or malformed, or names a kind with no floor.
    """
    key = "bank_balance"
    example = (
        "bank_balance: {account_kind: personal, floors: {personal: 0.00}, lowest_above_floor: [150, 2500]}"
    )
    block = pools.get(key)
    if not isinstance(block, dict) or sorted(block) != ["account_kind", "floors", "lowest_above_floor"]:
        raise _pool_error(key, f"`{key}` is {block!r}.", f"exactly the keys of {example}")
    floors = block["floors"]
    kind = block["account_kind"]
    if not isinstance(floors, dict) or kind not in floors or not isinstance(floors[kind], int | float):
        raise _pool_error(
            f"{key}.floors.{kind}", f"account kind {kind!r} has no numeric floor in {floors!r}.", example
        )
    return Decimal(str(floors[kind])).quantize(Decimal("0.01"))


def lowest_above_floor(pools: dict) -> tuple[float, float]:
    """Return `bank_balance.lowest_above_floor`: how far above the floor a statement's lowest balance sits.

    Raises:
        BankLedgerError: The range is not two non-negative numbers, min <= max.
    """
    balance_floor(pools)
    value = pools["bank_balance"]["lowest_above_floor"]
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
        or not 0 <= value[0] <= value[1]
    ):
        raise _pool_error(
            "bank_balance.lowest_above_floor",
            f"`bank_balance.lowest_above_floor` is {value!r}.",
            "[min, max], two numbers with 0 <= min <= max, e.g. lowest_above_floor: [150, 2500].",
        )
    return float(value[0]), float(value[1])


def _amount(value: str) -> Decimal | None:
    value = value.strip()
    return None if value in ("", NOT_FOUND) else Decimal(value)


def rows(fields: dict) -> list[tuple[str, Decimal | None, Decimal | None]]:
    """Return each row's (description, paid, received), in ground-truth order; an absent amount is None."""
    descriptions = str(fields.get("TRANSACTION_DESCRIPTIONS", "")).split("|")
    paid = str(fields.get("TRANSACTION_AMOUNTS_PAID", "")).split("|")
    received = str(fields.get("TRANSACTION_AMOUNTS_RECEIVED", "")).split("|")
    return [
        (descriptions[i].strip(), _amount(paid[i]), _amount(received[i]) if i < len(received) else None)
        for i in range(len(descriptions))
    ]


def opening_balance(fields: dict) -> Decimal:
    """The balance before the first row: the closing balance less what came in, plus what went out."""
    closing = Decimal(str(fields["ACCOUNT_BALANCE"]))
    return closing + sum(
        ((paid or Decimal("0")) - (received or Decimal("0")) for _, paid, received in rows(fields)),
        Decimal("0"),
    )


def running_balances(fields: dict) -> list[Decimal]:
    """The balance before the first row and after each row, as the statement prints it."""
    balance = opening_balance(fields)
    out = [balance]
    for _, paid, received in rows(fields):
        balance = balance - (paid or Decimal("0")) + (received or Decimal("0"))
        out.append(balance)
    return out


def check_statement(case_id: str, fields: dict, pools: dict, *, source: Path) -> list[str]:
    """Refuse every row printed against its description and a running balance below the floor.

    Args:
        case_id: The statement's case.
        fields: Its scored fields.
        pools: The loaded config/data_pools.yml.
        source: The ground truth (or the seed) the statement comes from, named in each refusal.

    Returns:
        One four-part refusal per kind of breach (empty when the statement is coherent).

    Raises:
        BankLedgerError: The rules themselves are missing or malformed.
    """
    prefixes = direction_prefixes(pools)
    floor = balance_floor(pools)
    errors = []
    wrong = []
    for index, (description, paid, received) in enumerate(rows(fields)):
        column = "debit" if paid is not None else "credit" if received is not None else None
        reads = classify(description, prefixes)
        if column is None or reads != column:
            wrong.append(
                f"row {index} {description!r} in the {column or 'no'} column reads as {reads or 'neither'}"
            )
    if wrong:
        listing = "\n".join(f"              - {item}" for item in wrong[:12])
        more = f"\n              (and {len(wrong) - 12} more)" if len(wrong) > 12 else ""
        errors.append(
            f"{case_id}: bank statement rows are printed against their descriptions\n"
            f"  What:     {len(wrong)} row(s) of {case_id} sit in the column their wording contradicts (money "
            f"in printed as money out, or the reverse):\n{listing}{more}\n"
            f"  Where:    {source} -> {case_id}.fields.TRANSACTION_DESCRIPTIONS / TRANSACTION_AMOUNTS_PAID / "
            f"TRANSACTION_AMOUNTS_RECEIVED; rule {_DATA_POOLS_PATH} -> bank_descriptions.direction_prefixes\n"
            "  Expected: a debit row described as money out (e.g. 'EFTPOS ...', 'BPAY ...') and a credit row "
            f"as money in (e.g. 'SALARY ...', 'REFUND ...'), per the prefixes {dict(prefixes)}.\n"
            "  Recover:  regenerate the corpus with scripts/seed_ground_truth.py then "
            "scripts/seed_transaction_links.py, which draw each row's description from its direction's pool."
        )
    balances = running_balances(fields)
    lowest = min(balances)
    if lowest < floor:
        at = balances.index(lowest)
        where = "before the first row" if at == 0 else f"after row {at - 1}"
        errors.append(
            f"{case_id}: the running balance falls below the account's floor\n"
            f"  What:     {case_id}'s running balance reaches {lowest} {where} (opening {balances[0]}, closing "
            f"{balances[-1]}), below the floor {floor} of its account kind.\n"
            f"  Where:    {source} -> {case_id}.fields.ACCOUNT_BALANCE and the TRANSACTION_AMOUNTS_* rows; "
            f"rule {_DATA_POOLS_PATH} -> bank_balance\n"
            f"  Expected: every running balance at or above {floor}, e.g. bank_balance: {{account_kind: personal, "
            "floors: {personal: 0.00}, lowest_above_floor: [150, 2500]}.\n"
            "  Recover:  regenerate the corpus with scripts/seed_ground_truth.py then "
            "scripts/seed_transaction_links.py, which draw the opening balance so the floor holds."
        )
    return errors


__all__ = [
    "BankLedgerError",
    "balance_floor",
    "check_statement",
    "classify",
    "direction_prefixes",
    "lowest_above_floor",
    "opening_balance",
    "running_balances",
]
