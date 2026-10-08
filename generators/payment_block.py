"""EFTPOS terminal-slip data for synthetic receipts.

Owns the `payment_terminal` and `pos_terminal` config in config/data_pools.yml
and the deterministic derivation of per-case terminal values. It draws nothing:
the three slip variants (card, wallet, cash) are now a `body:` tree in
config/layouts/receipts.yml, selected by `when:` over the keys the
`receipt_payment` field provider emits from `derive_payment` below.

The split between this module's config and that body is *sampled value* versus
*fixed wording*. `payment_terminal` holds only what varies document to
document — the method weights, acquirers, card schemes, wallets, entry modes
and cash-tender arithmetic. The slip's constant text (CUSTOMER COPY, APPROVED
00, the AID:/Card:/Terminal ID: labels, the retain-copy footer) is page chrome
and lives in the layout body, as one copy, with the rest of the page's fixed
text.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

_DATA_POOLS_PATH = Path(__file__).resolve().parent.parent / "config" / "data_pools.yml"

_LINKS_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "transaction_links.yml"

_ROOT_KEY = "payment_terminal"

# Required sub-keys of payment_terminal, each mapped to a short description of
# the expected shape used in the fail-fast diagnostic.
#
# Only pools a receipt *samples* from live here. The slip's fixed wording
# (CUSTOMER COPY, APPROVED 00, the AID:/Card:/Terminal ID: labels, the cash
# tendered/change labels, the retain-copy footer) used to be validated here
# too, and was read by nothing: since the terminal slip became a `body:` tree,
# the strings the page prints are literals in config/layouts/receipts.yml.
# Two files declaring the same wording is a drift risk with no upside, and
# page chrome belongs with the page.
_REQUIRED_KEYS: dict[str, str] = {
    "receipt_method_weights": "a mapping of method name -> non-negative integer weight",
    "acquirers": "a non-empty list of acquirer display names",
    "schemes": "a mapping of scheme name -> {display, aid, pan_digits, account_types}",
    "wallets": "a mapping of wallet method name -> printed wallet label",
    "entry_modes": "a mapping with 'card' and 'wallet' entry-mode markers",
    "bank_description_methods": "a mapping of bank-description prefix -> schemes key",
    "wallet_presentation_weights": "a mapping of 'none' plus wallet names -> non-negative weights",
    "cash_tender_step": "the cash note denomination in dollars, as a positive integer, e.g. 5",
    "cash_extra_notes": "the number of extra-note variants, as a positive integer, e.g. 3",
}

_REQUIRED_SCHEME_KEYS = ("display", "aid", "pan_digits", "account_types")

_POSITIVE_INT_KEYS = ("cash_tender_step", "cash_extra_notes")


def _err(what: str, *, path: Path, key_path: str, expected: str, recover: str) -> ValueError:
    """Build a four-element fail-fast diagnostic (what / where / expected / recover)."""
    return ValueError(
        f"{what}\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> '{key_path}'.\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover} in {path}."
    )


@lru_cache(maxsize=None)
def load_terminal_pools(path: Path = _DATA_POOLS_PATH) -> dict:
    """Load and validate the `payment_terminal` block of the data pools file.

    Args:
        path: Path to the data pools YAML file.

    Returns:
        The validated `payment_terminal` mapping.

    Raises:
        FileNotFoundError: `path` does not exist.
        ValueError: the block or any required key is missing or malformed.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"data pools file not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path}\n"
            f"  Expected: a YAML file with a top-level '{_ROOT_KEY}' mapping.\n"
            f"  Recover:  create {path} (see config/data_pools.yml in the repo)."
        )

    data = yaml.safe_load(path.read_text())
    pools = data.get(_ROOT_KEY) if isinstance(data, dict) else None
    if not isinstance(pools, dict):
        raise _err(
            f"'{_ROOT_KEY}' block is missing or not a mapping in {path}.",
            path=path,
            key_path=_ROOT_KEY,
            expected="a mapping with keys " + ", ".join(_REQUIRED_KEYS) + ".",
            recover=f"add a '{_ROOT_KEY}:' block",
        )

    for key, expected in _REQUIRED_KEYS.items():
        if key not in pools:
            raise _err(
                f"'{_ROOT_KEY}.{key}' is missing.",
                path=path,
                key_path=f"{_ROOT_KEY}.{key}",
                expected=expected + ".",
                recover=f"add '{key}' under {_ROOT_KEY}",
            )
        if not pools[key]:
            raise _err(
                f"'{_ROOT_KEY}.{key}' is empty.",
                path=path,
                key_path=f"{_ROOT_KEY}.{key}",
                expected=expected + ".",
                recover=f"populate '{key}' under {_ROOT_KEY}",
            )

    for name, scheme in pools["schemes"].items():
        for sub in _REQUIRED_SCHEME_KEYS:
            if not isinstance(scheme, dict) or sub not in scheme or not scheme[sub]:
                raise _err(
                    f"scheme '{name}' is missing '{sub}'.",
                    path=path,
                    key_path=f"{_ROOT_KEY}.schemes.{name}.{sub}",
                    expected="display (str), aid (str), pan_digits (int), account_types (non-empty list).",
                    recover=f"add '{sub}' to scheme '{name}'",
                )

    for key in _POSITIVE_INT_KEYS:
        value = pools[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise _err(
                f"'{_ROOT_KEY}.{key}' is not a positive integer (got {value!r}).",
                path=path,
                key_path=f"{_ROOT_KEY}.{key}",
                expected=_REQUIRED_KEYS[key] + ".",
                recover=f"set '{key}' to a positive integer",
            )

    for mode in ("card", "wallet"):
        if mode not in pools["entry_modes"]:
            raise _err(
                f"entry_modes is missing '{mode}'.",
                path=path,
                key_path=f"{_ROOT_KEY}.entry_modes.{mode}",
                expected="a marker string, e.g. card: (c) and wallet: (t).",
                recover=f"add '{mode}' under {_ROOT_KEY}.entry_modes",
            )

    known = set(pools["schemes"]) | set(pools["wallets"]) | {"Cash"}
    for method, weight in pools["receipt_method_weights"].items():
        if method not in known:
            raise _err(
                f"weighted method '{method}' resolves to no scheme, wallet, or Cash.",
                path=path,
                key_path=f"{_ROOT_KEY}.receipt_method_weights.{method}",
                expected="a key of 'schemes', a key of 'wallets', or the literal 'Cash'. "
                f"Known: {sorted(known)}.",
                recover=f"remove '{method}' or add a matching scheme/wallet entry",
            )
        if not isinstance(weight, int) or weight < 0:
            raise _err(
                f"weight for '{method}' is not a non-negative integer (got {weight!r}).",
                path=path,
                key_path=f"{_ROOT_KEY}.receipt_method_weights.{method}",
                expected="a non-negative integer, e.g. 'EFTPOS: 30'. Use 0 to disable a "
                "method explicitly rather than deleting its key.",
                recover=f"set a non-negative integer weight for '{method}'",
            )

    if not any(pools["receipt_method_weights"].values()):
        raise _err(
            "every receipt_method_weights entry is zero, so no method can be picked.",
            path=path,
            key_path=f"{_ROOT_KEY}.receipt_method_weights",
            expected="at least one positive weight.",
            recover="give at least one method a positive weight",
        )

    for prefix, scheme_name in pools["bank_description_methods"].items():
        if scheme_name not in pools["schemes"]:
            raise _err(
                f"bank_description_methods['{prefix}'] names scheme '{scheme_name}', "
                "which is not configured.",
                path=path,
                key_path=f"{_ROOT_KEY}.bank_description_methods.{prefix}",
                expected=f"a key of 'schemes': {sorted(pools['schemes'])}.",
                recover=f"point '{prefix}' at a configured scheme",
            )

    for name, weight in pools["wallet_presentation_weights"].items():
        if name != "none" and name not in pools["wallets"]:
            raise _err(
                f"wallet_presentation_weights['{name}'] is not a configured wallet.",
                path=path,
                key_path=f"{_ROOT_KEY}.wallet_presentation_weights.{name}",
                expected=f"'none' or a key of 'wallets': {sorted(pools['wallets'])}.",
                recover=f"remove '{name}' or add it to {_ROOT_KEY}.wallets",
            )
        if not isinstance(weight, int) or weight < 0:
            raise _err(
                f"wallet presentation weight for '{name}' is not a non-negative integer (got {weight!r}).",
                path=path,
                key_path=f"{_ROOT_KEY}.wallet_presentation_weights.{name}",
                expected="a non-negative integer, e.g. 'none: 90'.",
                recover=f"set a non-negative integer weight for '{name}'",
            )

    return pools


_POS_ROOT_KEY = "pos_terminal"

# Required sub-keys of pos_terminal, each mapped to a short description of the
# expected shape used in the fail-fast diagnostic. Mirrors _REQUIRED_KEYS above.
_POS_REQUIRED_KEYS: dict[str, str] = {
    "staff_names": "a non-empty list of staff-name strings",
    "hour_min": "the earliest POS hour as a non-negative integer, e.g. 8",
    "hour_span": "the width of the POS hour window as a positive integer, e.g. 12",
    "register_min": "the lowest register number as a non-negative integer, e.g. 1",
    "register_span": "the width of the register-number range as a positive integer, e.g. 8",
    "receipt_number_prefix": "the printed receipt-number prefix, e.g. 'R-'",
    "receipt_number_digest_length": (
        "the number of hex digest characters consumed for the receipt number, as a positive integer, e.g. 6"
    ),
}

_POS_NON_NEGATIVE_INT_KEYS = ("hour_min", "register_min")
_POS_POSITIVE_INT_KEYS = ("hour_span", "register_span", "receipt_number_digest_length")


@lru_cache(maxsize=None)
def load_pos_pools(path: Path = _DATA_POOLS_PATH) -> dict:
    """Load and validate the `pos_terminal` block of the data pools file.

    Owns the staff-name pool, POS hour window, register-number range, and
    receipt-number prefix/digest-length that `receipt_pos`
    (`generators/layout_dsl/field_providers.py`) derives every receipt's POS
    detail fields from. Mirrors `load_terminal_pools` above -- same fail-fast,
    four-element-diagnostic shape, distinct root key.

    Args:
        path: Path to the data pools YAML file.

    Returns:
        The validated `pos_terminal` mapping.

    Raises:
        FileNotFoundError: `path` does not exist.
        ValueError: the block or any required key is missing or malformed.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"data pools file not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path}\n"
            f"  Expected: a YAML file with a top-level '{_POS_ROOT_KEY}' mapping.\n"
            f"  Recover:  create {path} (see config/data_pools.yml in the repo)."
        )

    data = yaml.safe_load(path.read_text())
    pools = data.get(_POS_ROOT_KEY) if isinstance(data, dict) else None
    if not isinstance(pools, dict):
        raise _err(
            f"'{_POS_ROOT_KEY}' block is missing or not a mapping in {path}.",
            path=path,
            key_path=_POS_ROOT_KEY,
            expected="a mapping with keys " + ", ".join(_POS_REQUIRED_KEYS) + ".",
            recover=f"add a '{_POS_ROOT_KEY}:' block",
        )

    for key, expected in _POS_REQUIRED_KEYS.items():
        if key not in pools:
            raise _err(
                f"'{_POS_ROOT_KEY}.{key}' is missing.",
                path=path,
                key_path=f"{_POS_ROOT_KEY}.{key}",
                expected=expected + ".",
                recover=f"add '{key}' under {_POS_ROOT_KEY}",
            )

    staff_names = pools["staff_names"]
    if (
        not isinstance(staff_names, list)
        or not staff_names
        or not all(isinstance(name, str) and name for name in staff_names)
    ):
        raise _err(
            f"'{_POS_ROOT_KEY}.staff_names' is not a non-empty list of strings.",
            path=path,
            key_path=f"{_POS_ROOT_KEY}.staff_names",
            expected=_POS_REQUIRED_KEYS["staff_names"] + ".",
            recover="set 'staff_names' to a non-empty list of name strings",
        )

    for key in _POS_NON_NEGATIVE_INT_KEYS:
        value = pools[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _err(
                f"'{_POS_ROOT_KEY}.{key}' is not a non-negative integer (got {value!r}).",
                path=path,
                key_path=f"{_POS_ROOT_KEY}.{key}",
                expected=_POS_REQUIRED_KEYS[key] + ".",
                recover=f"set '{key}' to a non-negative integer",
            )

    for key in _POS_POSITIVE_INT_KEYS:
        value = pools[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise _err(
                f"'{_POS_ROOT_KEY}.{key}' is not a positive integer (got {value!r}).",
                path=path,
                key_path=f"{_POS_ROOT_KEY}.{key}",
                expected=_POS_REQUIRED_KEYS[key] + ".",
                recover=f"set '{key}' to a positive integer",
            )

    prefix = pools["receipt_number_prefix"]
    if not isinstance(prefix, str) or not prefix:
        raise _err(
            f"'{_POS_ROOT_KEY}.receipt_number_prefix' is not a non-empty string.",
            path=path,
            key_path=f"{_POS_ROOT_KEY}.receipt_number_prefix",
            expected=_POS_REQUIRED_KEYS["receipt_number_prefix"] + ".",
            recover="set 'receipt_number_prefix' to a non-empty string",
        )

    return pools


def method_from_bank_description(description: str, cfg: dict) -> str:
    """Resolve a bank-statement description to the scheme the receipt must print.

    Longest matching prefix wins, so 'MASTERCARD DEBIT' beats 'MASTERCARD'.

    Args:
        description: The linked row's bank_description.
        cfg: The validated payment_terminal mapping.

    Returns:
        A key of cfg['schemes'].

    Raises:
        ValueError: no configured prefix matches the description.
    """
    mapping = cfg["bank_description_methods"]
    for prefix in sorted(mapping, key=len, reverse=True):
        if description.startswith(prefix):
            return mapping[prefix]

    raise _err(
        f"bank description '{description}' matches no configured prefix.",
        path=_DATA_POOLS_PATH,
        key_path=f"{_ROOT_KEY}.bank_description_methods",
        expected=f"a description starting with one of: {sorted(mapping)}.",
        recover="add a prefix mapping for this description shape",
    )


@lru_cache(maxsize=None)
def load_link_index(path: Path = _LINKS_PATH) -> dict[str, str]:
    """Map each linked receipt's image stem to its bank_description.

    Invoice links are skipped: invoices render no payment block. Where a stem
    carries several links the first is used; the seed script emits one per
    receipt.

    Args:
        path: Path to transaction_links.yml.

    Returns:
        {"CASE001_receipt_fuel": "VISA DEBIT PURCHASE ...", ...}

    Raises:
        FileNotFoundError: `path` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(
            "transaction links file not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path}\n"
            "  Expected: the receipt/invoice -> bank statement link ground truth.\n"
            f"  Recover:  restore {path} or run scripts/seed_transaction_links.py."
        )

    data = yaml.safe_load(path.read_text()) or {}
    index: dict[str, str] = {}
    for image_name, links in data.items():
        stem = str(image_name).removesuffix(".png")
        if "_receipt" not in stem or not links:
            continue
        index[stem] = links[0]["bank_description"]
    return index


@dataclass(frozen=True)
class PaymentDetails:
    """Deterministic terminal-slip values for one receipt.

    Attributes:
        method: The pool method name, e.g. "Visa", "Cash", "Apple Pay".
        kind: Block variant — "card", "wallet", or "cash".
        scheme_name: The `schemes` key backing this payment ("" for cash).
        scheme_display: Printed scheme text, e.g. "VISA CREDIT".
        account_type: Printed account suffix, e.g. "SAV" or "CR".
        acquirer: Printed acquirer line, e.g. "Tyro Payments EFTPOS".
        aid: Printed EMV application identifier.
        masked_pan: Masked card number, e.g. "xxxxxxxxxxxx3218".
        entry_mode: Entry-mode marker, "(c)" for card or "(t)" for wallet.
        psn: Two-digit PAN sequence number.
        atc: Four-hex-character application transaction counter.
        terminal_id: Printed terminal number.
        transaction_ref: Six-digit transaction reference.
        timestamp: "DD Mon YYYY at hh:mm AM/PM".
        wallet_label: Printed wallet name, "" unless kind == "wallet".
        tendered: Cash tendered, None unless kind == "cash".
        change: Cash change, None unless kind == "cash".

    There is deliberately no purchase total here. The slip's 'Purchase   AUD'
    line binds `{TOTAL_AMOUNT}` directly in the layout body, so the printed
    amount cannot drift from the field the benchmark scores; carrying a
    second, derived copy on this dataclass would only invite one to. The
    total does reach this module — `derive_payment` takes it, and the cash
    tender/change below are computed from it — it simply never becomes a
    slip value of its own. `receipt_payment` emits no
    PAYMENT_PURCHASE_TOTAL for the same reason.
    """

    method: str
    kind: str
    scheme_name: str
    scheme_display: str
    account_type: str
    acquirer: str
    aid: str
    masked_pan: str
    entry_mode: str
    psn: str
    atc: str
    terminal_id: str
    transaction_ref: str
    timestamp: str
    wallet_label: str
    tendered: Decimal | None
    change: Decimal | None


def _weighted_pool(weights: dict[str, int]) -> list[str]:
    """Expand a method -> weight mapping into a deterministic flat pool."""
    pool: list[str] = []
    for method in sorted(weights):
        pool.extend([method] * weights[method])
    return pool


def _format_timestamp(invoice_date: str, time_str: str) -> str:
    """Render 'DD/MM/YYYY' + 'HH:MM' as 'DD Mon YYYY at hh:mm AM/PM'."""
    stamp = datetime.strptime(f"{invoice_date} {time_str}", "%d/%m/%Y %H:%M")
    return stamp.strftime("%d %b %Y at %I:%M %p")


def _cash_tender(total: Decimal, extra_index: int, *, step: int) -> tuple[Decimal, Decimal]:
    """Return (tendered, change): next `step`-dollar note above `total`, plus 0/step/2*step.

    Tendered always strictly exceeds the total, so a total that is already a
    round note never renders 'CHANGE 0.00'.

    Args:
        total: The purchase total.
        extra_index: How many extra `step`-dollar notes to add, in
            `[0, cash_extra_notes)` -- see `payment_terminal.cash_extra_notes`.
        step: The note denomination in dollars -- `payment_terminal.cash_tender_step`.
    """
    step_dec = Decimal(step)
    base = (total // step_dec) * step_dec
    if base <= total:
        base += step_dec
    tendered = base + step_dec * extra_index
    return tendered, tendered - total


def derive_payment(
    case_id: str,
    invoice_date: str,
    total: str,
    time_str: str,
    *,
    pools: dict | None = None,
    bank_description: str | None = None,
) -> PaymentDetails:
    """Derive deterministic terminal-slip values for one receipt.

    Reuses the digest of f"{case_id}:pos:{invoice_date}" that the `receipt_pos`
    field provider (generators/layout_dsl/field_providers.py) computes for
    time/register/staff (hex chars 0-10); this function consumes hex chars
    10-40, so the two never collide.

    Args:
        case_id: Case identifier, e.g. "CASE001".
        invoice_date: Receipt date as DD/MM/YYYY.
        total: TOTAL_AMOUNT as a decimal string, e.g. "137.73".
        time_str: 24-hour HH:MM already printed by the receipt_meta header.
        pools: Validated payment_terminal mapping; loaded from YAML if omitted.
        bank_description: The linked bank row's description. When given, the
            scheme is taken from it (the bank statement is the source of truth
            for a linked receipt) and wallet_presentation_weights decides
            whether the payment is presented as a phone wallet over that same
            scheme. When None, the weighted pool picks the method.

    Returns:
        The derived PaymentDetails.
    """
    cfg = pools if pools is not None else load_terminal_pools()
    digest = hashlib.sha256(f"{case_id}:pos:{invoice_date}".encode()).hexdigest()

    if bank_description is None:
        method_pool = _weighted_pool(cfg["receipt_method_weights"])
        method = method_pool[int(digest[10:14], 16) % len(method_pool)]
        forced_scheme = None
    else:
        # A linked receipt's scheme is fixed by its bank row; the same hash slice
        # that would have picked a method now picks the wallet presentation.
        forced_scheme = method_from_bank_description(bank_description, cfg)
        presentation_pool = _weighted_pool(cfg["wallet_presentation_weights"])
        presentation = presentation_pool[int(digest[10:14], 16) % len(presentation_pool)]
        method = forced_scheme if presentation == "none" else presentation

    acquirers = cfg["acquirers"]
    acquirer = acquirers[int(digest[14:16], 16) % len(acquirers)]
    timestamp = _format_timestamp(invoice_date, time_str)
    total_dec = Decimal(total)

    if method == "Cash":
        tendered, change = _cash_tender(
            total_dec,
            int(digest[38:40], 16) % cfg["cash_extra_notes"],
            step=cfg["cash_tender_step"],
        )
        return PaymentDetails(
            method=method,
            kind="cash",
            scheme_name="",
            scheme_display="",
            account_type="",
            acquirer=acquirer,
            aid="",
            masked_pan="",
            entry_mode="",
            psn="",
            atc="",
            terminal_id="",
            transaction_ref="",
            timestamp=timestamp,
            wallet_label="",
            tendered=tendered,
            change=change,
        )

    wallets = cfg["wallets"]
    is_wallet = method in wallets
    if forced_scheme is not None:
        scheme_name = forced_scheme
    elif is_wallet:
        # A wallet transaction still runs over a card scheme; pick one by hash.
        scheme_names = sorted(cfg["schemes"])
        scheme_name = scheme_names[int(digest[16:18], 16) % len(scheme_names)]
    else:
        scheme_name = method
    scheme = cfg["schemes"][scheme_name]
    account_types = scheme["account_types"]

    last4 = f"{int(digest[18:22], 16) % 10000:04d}"
    masked_pan = "x" * (int(scheme["pan_digits"]) - 4) + last4

    return PaymentDetails(
        method=method,
        kind="wallet" if is_wallet else "card",
        scheme_name=scheme_name,
        scheme_display=scheme["display"],
        account_type=account_types[int(digest[16:18], 16) % len(account_types)],
        acquirer=acquirer,
        aid=scheme["aid"],
        masked_pan=masked_pan,
        entry_mode=cfg["entry_modes"]["wallet" if is_wallet else "card"],
        psn=f"{int(digest[22:24], 16) % 100:02d}",
        atc=digest[24:28].upper(),
        terminal_id=str(int(digest[28:32], 16) % 99 + 1),
        transaction_ref=f"{int(digest[32:38], 16) % 1000000:06d}",
        timestamp=timestamp,
        wallet_label=wallets[method] if is_wallet else "",
        tendered=None,
        change=None,
    )
