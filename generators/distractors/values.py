"""Deterministic value rules for distractor catalogue items.

Each rule is a small pure function of `(ctx, rule)`: `ctx` carries the seeded `random.Random`, the
document's scored fields and the values already drawn for it; `rule` is the item's `rule` mapping from
`config/distractors.yml`. Every parameter comes from that mapping and nothing is defaulted here. Every
draw comes from `ctx.rng`, never the module-global `random`.

A `repeat` rule needs the scored column to copy; the caller passes it as `rule["field"]`, taken from the
item's `field`.
"""

import math
import random
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import yaml

from generators import abn_register
from generators.common import validate_abn
from generators.content_engine import ContentEngine
from generators.distractors.paths import DATA_POOLS_PATH, DISTRACTORS_PATH, LINKS_PATH, layout_file
from generators.exporters.eval_projection import load_extraction_schema

DATE_FORMAT = "%d/%m/%Y"
_COMPANY_WORDS = frozenset({"pty", "ltd", "co"})
_STATES = ("NSW", "ACT", "VIC", "TAS", "QLD", "SA", "WA", "NT")
_STATE_RE = re.compile(r"\b(" + "|".join(_STATES) + r")\b")


class ValueRuleError(ValueError):
    """Raised when a rule cannot produce a value from its parameters or document context.

    Carries the four diagnostic parts separately so a caller (the distractor provider) can re-raise
    with the item id and the absolute ground-truth location added.

    Attributes:
        what: What is wrong.
        where: Where the rule found it.
        expected: What a valid value or parameter looks like.
        recover: How to recover.
        field: The ground-truth field the failure is about, or None when it is not about one.
    """

    def __init__(
        self, what: str, where: str, expected: str, recover: str, field: str | None = None
    ) -> None:
        super().__init__(
            "Distractor value rule failed.\n"
            f"  What:     {what}\n"
            f"  Where:    {where}\n"
            f"  Expected: {expected}\n"
            f"  Recover:  {recover}"
        )
        self.what = what
        self.where = where
        self.expected = expected
        self.recover = recover
        self.field = field


@dataclass(frozen=True)
class RuleContext:
    """Everything a value rule may read.

    Attributes:
        rng: The seeded generator every draw must come from.
        fields: The document's scored fields.
        engine: The content engine, for addresses, locations, people and the real-name screen.
        pools: The loaded `config/data_pools.yml`.
        case_id: The case the document belongs to, e.g. `CASE001`.
        rows: Parsed bank transaction rows (dicts with `date`, `description`, `paid`, `received`; the
            amounts are strings or None) for a bank statement, else None.
        selected: `DX_` values already drawn for this document, for dependent rules.
        states: State name -> the value drawn for this document (its payment state, say), for a rule
            whose wording depends on it; empty where no state was drawn, which such a rule refuses.
        service_category: An invoice's stored service category (its ground truth entry's
            `service_category`, non-scored metadata), which picks the industry of a keyed text pool; None
            for a document that carries none, which such a pool refuses.
    """

    rng: random.Random
    fields: dict[str, str]
    engine: ContentEngine
    pools: dict
    case_id: str
    rows: list[dict] | None
    selected: dict[str, str]
    states: dict[str, str] = field(default_factory=dict)
    service_category: str | None = None


def _fail(where: str, what: str, expected: str, recover: str, field: str | None = None) -> ValueRuleError:
    return ValueRuleError(what, where, expected, recover, field)


def _rule_where(rule: dict, key: str) -> str:
    return f"{DISTRACTORS_PATH} -> items.<id>.rule.{key} (rule type {rule.get('type')!r})"


def _param(rule: dict, key: str, example: str) -> object:
    if key not in rule:
        raise _fail(
            _rule_where(rule, key),
            f"rule type {rule.get('type')!r} has no '{key}' parameter.",
            f"a '{key}' entry in the rule mapping, e.g. {example}.",
            f"add '{key}' to the rule in {DISTRACTORS_PATH}.",
        )
    return rule[key]


def _field(ctx: RuleContext, name: str, rule: dict) -> str:
    value = ctx.fields.get(name)
    if not isinstance(value, str) or not value:
        raise _fail(
            f"{ctx.case_id} fields.{name} (needed by rule type {rule.get('type')!r})",
            f"the document has no usable '{name}' value.",
            f"a non-empty '{name}' among the document's scored fields.",
            f"use the rule only on document types that carry '{name}', or fix the ground truth entry.",
            field=name,
        )
    return value


def _selected(ctx: RuleContext, key: str, rule: dict) -> str:
    value = ctx.selected.get(key)
    if value is None:
        raise _fail(
            f"{ctx.case_id} selected.{key} (needed by rule type {rule.get('type')!r})",
            f"'{key}' has not been drawn for this document.",
            f"'{key}' drawn before this item, via the item's `requires:` list.",
            f"add the providing item to `requires:` in {DISTRACTORS_PATH}.",
        )
    return value


def _normalise(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    while words and words[-1] in _COMPANY_WORDS:
        words.pop()
    return "".join(words)


def _screen_text(ctx: RuleContext, text: str, where: str) -> str:
    """Refuse a sentence, name or address that names a real business from the blocklist."""
    low = text.lower()
    for name in sorted(ctx.engine.blocklist):
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", low):
            raise _fail(
                where,
                f"the text {text!r} names the real business {name!r}.",
                "invented text that names no real organisation, brand or product.",
                f"rewrite or remove that entry in {DATA_POOLS_PATH}.",
            )
    return text


def _slug(ctx: RuleContext, rule: dict) -> str:
    slug = _normalise(_field(ctx, "SUPPLIER_NAME", rule))
    if not slug:
        raise _fail(
            f"{ctx.case_id} fields.SUPPLIER_NAME",
            "the supplier name has no alphanumeric characters to build a slug from.",
            "a supplier name containing letters or digits.",
            "fix the ground truth entry.",
            field="SUPPLIER_NAME",
        )
    blocked = {_normalise(name) for name in ctx.engine.blocklist}
    if slug in blocked:
        raise _fail(
            f"{ctx.case_id} fields.SUPPLIER_NAME",
            f"the slug {slug!r} matches a real business name on the blocklist.",
            "an invented supplier name whose slug is not a real business.",
            f"rename the supplier in the ground truth, or check the blocklist in {DATA_POOLS_PATH}.",
            field="SUPPLIER_NAME",
        )
    return slug


def _service_category_names(pools: dict, case_id: str) -> list[str]:
    """`service_categories`, failing four-part when it is missing or not a list of names."""
    categories = pools.get("service_categories")
    if not isinstance(categories, list) or not all(isinstance(c, str) and c for c in categories):
        raise _fail(
            f"{DATA_POOLS_PATH} -> service_categories",
            f"service_categories is {categories!r}, not a list of category names, so the industry of "
            f"{case_id}'s supplier cannot be told.",
            "a list of the invoice categories, each a key of business_name_parts.category_nouns, e.g. "
            "service_categories: [accounting, legal, plumbing].",
            f"restore service_categories: [...] at the top level of {DATA_POOLS_PATH}.",
        )
    return categories


def case_category(service_category: object, pools: dict, case_id: str) -> str:
    """Return an invoice's service category: the `service_category` stored in its ground truth entry.

    The seed stores the category it drew the supplier, its lines and its clutter for beside the entry's
    `layout`, as non-scored metadata (config/field_definitions.yml -> entry_metadata), so it is read, never
    inferred from the supplier's name.

    Args:
        service_category: The entry's `service_category` value (None when the entry carries none).
        pools: The loaded `config/data_pools.yml`.
        case_id: The case, named in a diagnostic.

    Returns:
        The service category, e.g. `legal`.

    Raises:
        ValueRuleError: `service_categories` is missing or malformed, or the entry's category is absent
            or not one of them.
    """
    categories = _service_category_names(pools, case_id)
    if not isinstance(service_category, str) or service_category not in categories:
        state = "has no service_category" if service_category is None else f"has {service_category!r}"
        raise _fail(
            f"{case_id}.service_category in the invoice ground truth (ground_truth/invoices.yml); "
            f"{DATA_POOLS_PATH} -> service_categories",
            f"invoice {case_id} {state}, so the industry whose distractor text it prints cannot be told.",
            f"service_category: <one of {categories}> beside the entry's layout, e.g. "
            "service_category: plumbing.",
            "re-run scripts/seed_ground_truth.py then scripts/seed_transaction_links.py, which store each "
            "invoice's category, or add the key to the entry.",
        )
    return service_category


def case_industry(service_category: object, pools: dict, case_id: str) -> str:
    """Return the industry whose distractor text a document prints: its category's `category_industries`.

    Args:
        service_category: The entry's stored `service_category` (None when it carries none).
        pools: The loaded `config/data_pools.yml`.
        case_id: The case, named in a diagnostic.

    Returns:
        The industry, e.g. `legal_services`.

    Raises:
        ValueRuleError: The category is absent or unknown, or mapped to no industry.
    """
    category = case_category(service_category, pools, case_id)
    mapping = pools.get("category_industries")
    industry = mapping.get(category) if isinstance(mapping, dict) else None
    if not isinstance(industry, str) or not industry:
        raise _fail(
            f"{DATA_POOLS_PATH} -> category_industries.{category}",
            f"the service category {category!r} of {case_id} is mapped to no industry.",
            f"category_industries: {{{category}: <industry>, ...}}, every service category mapped.",
            f"add {category}: <industry> under category_industries in {DATA_POOLS_PATH}.",
        )
    return industry


def _pool(ctx: RuleContext, name: str, rule: dict) -> list[str]:
    """A text pool's entries; for a pool keyed by industry, the sub-pool of the document's industry."""
    texts = ctx.pools.get("distractor_text")
    pool = texts.get(name) if isinstance(texts, dict) else None
    where = f"{DATA_POOLS_PATH} -> distractor_text.{name}"
    if isinstance(pool, dict):
        industry = case_industry(ctx.service_category, ctx.pools, ctx.case_id)
        pool = pool.get(industry)
        if not isinstance(pool, list) or not pool:
            raise _fail(
                f"{where}.{industry}",
                f"the text pool '{name}' is keyed by industry and has no sub-pool for {industry!r} "
                f"(the industry of {ctx.case_id}).",
                f"a non-empty list of invented strings under distractor_text.{name}.{industry}.",
                f"add the '{industry}' sub-pool to distractor_text.{name} in {DATA_POOLS_PATH}.",
            )
        return pool
    if not isinstance(pool, list) or not pool:
        raise _fail(
            where,
            f"the text pool '{name}' is missing or empty.",
            f"a non-empty list of invented strings under distractor_text.{name}, or a mapping of "
            "industry to such lists.",
            f"add the '{name}' pool to {DATA_POOLS_PATH}.",
        )
    return pool


def _digits(rng: random.Random, length: int) -> str:
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(length - 1))


def _int_param(rule: dict, key: str, example: str) -> int:
    value = _param(rule, key, example)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _fail(
            _rule_where(rule, key),
            f"'{key}' is {value!r}.",
            f"a positive integer, e.g. {example}.",
            f"fix '{key}' in {DISTRACTORS_PATH}.",
        )
    return value


def _digits_rule(ctx: RuleContext, rule: dict) -> str:
    length = _int_param(rule, "length", "length: 8")
    prefix = _param(rule, "prefix", 'prefix: "INV-"')
    return f"{prefix}{_digits(ctx.rng, length)}"


_ACN_ATTEMPTS = 100
_registers: dict[tuple[object, str], object] = {}


def _abn_register(pools: dict) -> object:
    """The public ABN register, loaded once per loader (the suite swaps the loader for a stand-in)."""
    loader = abn_register.load_abn_register
    key = (loader, str(pools["abn_register"]["path"]))
    if key not in _registers:
        _registers[key] = loader(pools, DATA_POOLS_PATH)
    return _registers[key]


def _checksum_acn(rng: random.Random) -> str:
    digits = [int(ch) for ch in _digits(rng, 8)]
    weighted = sum(d * w for d, w in zip(digits, range(8, 0, -1), strict=True))
    digits.append((10 - weighted % 10) % 10)
    return "".join(map(str, digits))


def _company_registered(acn: str, register: object) -> bool:
    """Whether a registered ABN ends in this ACN, as a company's ABN does (two digits, then its ACN)."""
    return any(validate_abn(f"{p}{acn}") and f"{p}{acn}" in register for p in range(10, 100))  # type: ignore[operator]


def _acn(ctx: RuleContext, rule: dict) -> str:
    """A checksum-valid ACN that no registered company holds.

    A draw that is the tail of a registered ABN is redrawn from a stream seeded by the rejected ACN, as
    `generate_abn` does, so screening moves only that value and every later draw is unchanged.
    """
    register = _abn_register(ctx.pools)
    text = _checksum_acn(ctx.rng)
    redraw = random.Random(int(text))
    for _ in range(_ACN_ATTEMPTS):
        if not _company_registered(text, register):
            return f"{text[:3]} {text[3:6]} {text[6:]}"
        text = _checksum_acn(redraw)
    raise _fail(
        f"the register at 'abn_register.path' in {DATA_POOLS_PATH}",
        f"{_ACN_ATTEMPTS} checksum-valid ACN draws were all the tail of a registered ABN.",
        "a register of issued ABNs, a small fraction of the checksum-valid space.",
        "rebuild the register with scripts/build_abn_register.py from the data.gov.au extract; a register "
        "holding nearly every ABN is a build fault.",
    )


def _phone(ctx: RuleContext, rule: dict) -> str:
    kind = _param(rule, "kind", "kind: landline")
    rng = ctx.rng
    if kind == "mobile":
        return f"04{rng.randint(0, 99):02d} {rng.randint(0, 999):03d} {rng.randint(0, 999):03d}"
    if kind == "freecall":
        return f"1300 {_digits(rng, 3)} {rng.randint(0, 999):03d}"
    if kind != "landline":
        raise _fail(
            _rule_where(rule, "kind"),
            f"phone kind {kind!r} is unknown.",
            "one of landline, mobile, freecall.",
            f"fix 'kind' in {DISTRACTORS_PATH}.",
        )
    address = _field(ctx, "BUSINESS_ADDRESS", rule)
    states = _STATE_RE.findall(address)
    codes = ctx.pools.get("phone_area_codes")
    if not states or not isinstance(codes, dict) or states[-1] not in codes:
        raise _fail(
            f"{DATA_POOLS_PATH} -> phone_area_codes (address {address!r})",
            "no area code for the state in the business address.",
            "an address ending in a state and a phone_area_codes entry for it, e.g. NSW: '02'.",
            f"add the state to phone_area_codes in {DATA_POOLS_PATH}, or fix the address.",
            field="BUSINESS_ADDRESS",
        )
    return f"({codes[states[-1]]}) {_digits(rng, 4)} {_digits(rng, 4)}"


def _email(ctx: RuleContext, rule: dict) -> str:
    pool = _param(rule, "pool", "pool: email_local")
    local = ctx.rng.choice(_pool(ctx, str(pool), rule))
    where = f"{DATA_POOLS_PATH} -> distractor_text.{pool}"
    return f"{_screen_text(ctx, str(local), where)}@{_slug(ctx, rule)}.com.au"


def _website(ctx: RuleContext, rule: dict) -> str:
    return f"www.{_slug(ctx, rule)}.com.au"


def _social(ctx: RuleContext, rule: dict) -> str:
    return f"@{_slug(ctx, rule)}"


def _survey_url(ctx: RuleContext, rule: dict) -> str:
    code = _selected(ctx, "DX_SURVEY_CODE", rule)
    return f"https://survey.{_slug(ctx, rule)}.com.au/{code}"


_CENT = Decimal("0.01")


def _as_bound(raw: object) -> Decimal | None:
    """An amount bound as a decimal, or None unless it is finite, non-negative, at most two places."""
    try:
        value = Decimal(str(raw)) if not isinstance(raw, bool) else None
    except InvalidOperation:
        return None
    exponent = value.as_tuple().exponent if value is not None else None
    if (
        value is None
        or not value.is_finite()
        or value < 0
        or not isinstance(exponent, int)
        or exponent < -2
    ):
        return None
    return value


def _bound(rule: dict, key: str, example: str) -> Decimal:
    """Read an amount bound: a finite, non-negative decimal with at most two places."""
    raw = _param(rule, key, example)
    value = _as_bound(raw)
    if value is None:
        raise _fail(
            _rule_where(rule, key),
            f"'{key}' is {raw!r}.",
            f"a non-negative decimal string with at most two places, e.g. {example}.",
            f"fix '{key}' in {DISTRACTORS_PATH}.",
        )
    return value


def _amount(ctx: RuleContext, rule: dict) -> str:
    low = _bound(rule, "min", 'min: "1.00"')
    high = _bound(rule, "max", 'max: "25.00"')
    lo_c, hi_c = int(low * 100), int(high * 100)
    if lo_c > hi_c:
        raise _fail(
            _rule_where(rule, "min"),
            f"min {low} exceeds max {high}.",
            "min <= max, both decimal strings.",
            f"fix min and max in {DISTRACTORS_PATH}.",
        )
    # Both bounds have at most two places, so lo_c and hi_c are exact and the draw stays inside them.
    return str((Decimal(ctx.rng.randint(lo_c, hi_c)) * _CENT).quantize(_CENT))


def _as_fraction(raw: object) -> Decimal | None:
    """A fraction as a decimal, or None unless it is a decimal string strictly between 0 and 1."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and 0 < value < 1 else None


def _fraction(rule: dict, key: str, example: str) -> Decimal:
    raw = _param(rule, key, example)
    value = _as_fraction(raw)
    if value is None:
        raise _fail(
            _rule_where(rule, key),
            f"'{key}' is {raw!r}.",
            f"a decimal string strictly between 0 and 1, e.g. {example}.",
            f"fix '{key}' in {DISTRACTORS_PATH}.",
        )
    return value


def _amount_fraction(ctx: RuleContext, rule: dict) -> str:
    """A two-decimal amount between two fractions of a scored amount, always strictly below it.

    The draw is in whole cents from `ceil(total * min_fraction)` to `floor(total * max_fraction)`,
    never below 0.01; on a total too small for that range the one cent left below the total is drawn.
    """
    source = str(_param(rule, "of", "of: TOTAL_AMOUNT"))
    low = _fraction(rule, "min_fraction", 'min_fraction: "0.03"')
    high = _fraction(rule, "max_fraction", 'max_fraction: "0.25"')
    text = _field(ctx, source, rule)
    try:
        total = Decimal(text.strip())
    except InvalidOperation:
        total = Decimal("NaN")
    total_cents = int(total * 100) if total.is_finite() else 0
    if total_cents < 2:
        raise _fail(
            f"{ctx.case_id} fields.{source} (needed by rule type 'amount_fraction')",
            f"{source} is {text!r}, which leaves no whole cent of at least 0.01 strictly below it.",
            f"a decimal amount of at least 0.02 in {source}, e.g. 18.22.",
            f"use the rule only on documents whose {source} is a positive amount, or fix the ground truth "
            "entry.",
            field=source,
        )
    lo_c = max(1, math.ceil(total * low * 100))
    hi_c = min(total_cents - 1, max(lo_c, math.floor(total * high * 100)))
    lo_c = min(lo_c, hi_c)
    return str((Decimal(ctx.rng.randint(lo_c, hi_c)) * _CENT).quantize(_CENT))


def _as_ratio(raw: object) -> Decimal | None:
    """A ratio as a decimal, or None unless it is a positive, finite decimal string."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _ratio(rule: dict, key: str, example: str) -> Decimal:
    raw = _param(rule, key, example)
    value = _as_ratio(raw)
    if value is None:
        raise _fail(
            _rule_where(rule, key),
            f"'{key}' is {raw!r}.",
            f"a positive decimal string, e.g. {example}.",
            f"fix '{key}' in {DISTRACTORS_PATH}.",
        )
    return value


# The `amount_ratio` rule's `cap` value for no cap, and its `element` values.
NO_CAP = "none"
ELEMENTS = ("whole", "max")


def _cap(rule: dict) -> tuple[Decimal, Decimal] | None:
    """An `amount_ratio` cap: None for `cap: none`, else its (min, max) amounts."""
    raw = _param(rule, "cap", f'cap: {{min: "500.00", max: "999.00"}}, or cap: {NO_CAP}')
    if raw == NO_CAP:
        return None
    bounds = _as_cap(raw)
    if bounds is None:
        raise _fail(
            _rule_where(rule, "cap"),
            f"'cap' is {raw!r}.",
            f'{NO_CAP}, or {{min: "500.00", max: "999.00"}}: two non-negative decimal strings with at most '
            "two places, min not above max.",
            f"fix 'cap' in {DISTRACTORS_PATH}.",
        )
    return bounds


def _as_cap(raw: object) -> tuple[Decimal, Decimal] | None:
    if not isinstance(raw, dict) or sorted(raw) != ["max", "min"]:
        return None
    low, high = _as_bound(raw["min"]), _as_bound(raw["max"])
    if low is None or high is None or low > high or high <= 0:
        return None
    return low, high


def _amount_ratio(ctx: RuleContext, rule: dict) -> str:
    """A two-decimal amount between two multiples of a scored amount, so it is scaled to the document.

    `of` names the scored column and `element` which of its values: `whole` (a total) or `max` (the
    largest of a pipe list, the dearest unit price billed). The draw is in whole cents from
    `ceil(amount * min_ratio)` to `floor(amount * max_ratio)`, never below 0.01. A `cap: {min, max}`
    bounds a figure printed inside running text, where it gets no thousands separator: when the scaled
    range runs past `max`, the draw is made from `min(scaled floor, cap min)` to `max` instead, so a large
    invoice still varies its figure rather than printing the cap itself. `cap: none` leaves it unbounded.
    Unlike `amount_fraction` the range may run past the scored amount (a previous balance larger than this
    invoice), so the value validator's redraw keeps it distinct from every scored value.
    """
    source = str(_param(rule, "of", "of: TOTAL_AMOUNT"))
    element = _param(rule, "element", "element: whole")
    if element not in ELEMENTS:
        raise _fail(
            _rule_where(rule, "element"),
            f"'element' is {element!r}.",
            f"one of {list(ELEMENTS)}, e.g. element: max.",
            f"fix 'element' in {DISTRACTORS_PATH}.",
        )
    low = _ratio(rule, "min_ratio", 'min_ratio: "0.40"')
    high = _ratio(rule, "max_ratio", 'max_ratio: "1.50"')
    cap = _cap(rule)
    text = _field(ctx, source, rule)
    try:
        if element == "max":
            total = max(Decimal(part.strip()) for part in text.split("|"))
        else:
            total = Decimal(text.strip())
    except InvalidOperation:
        total = Decimal("NaN")
    if not total.is_finite() or total <= 0:
        raise _fail(
            f"{ctx.case_id} fields.{source} (needed by rule type 'amount_ratio', element {element})",
            f"{source} is {text!r}, not a positive amount to scale.",
            f"a positive decimal amount in {source}, e.g. 18.22.",
            f"use the rule only on documents whose {source} is a positive amount, or fix the ground truth "
            "entry.",
            field=source,
        )
    lo_c = max(1, math.ceil(total * low * 100))
    hi_c = max(lo_c, math.floor(total * high * 100))
    if cap is not None:
        cap_lo, cap_hi = (int(bound * 100) for bound in cap)
        if hi_c > cap_hi:
            hi_c = cap_hi
            lo_c = max(1, min(lo_c, cap_lo))
    return str((Decimal(ctx.rng.randint(lo_c, hi_c)) * _CENT).quantize(_CENT))


def _fixed(ctx: RuleContext, rule: dict) -> str:
    return str(_param(rule, "value", 'value: "0.00"'))


def _repeat(ctx: RuleContext, rule: dict) -> str:
    return _field(ctx, str(_param(rule, "field", "field: TOTAL_AMOUNT")), rule)


def _item_count(ctx: RuleContext, rule: dict) -> str:
    return str(len(_field(ctx, "LINE_ITEM_DESCRIPTIONS", rule).split("|")))


def _parse_date(text: str, where: str, field: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT)
    except ValueError:
        raise _fail(
            where,
            f"{text!r} is not a DD/MM/YYYY date.",
            "a date such as 20/09/2024.",
            "fix the ground truth entry.",
            field=field,
        ) from None


def _offset_days(rule: dict) -> list[int]:
    days = _param(rule, "days", "days: [7, 14, 30]")
    if not isinstance(days, list) or not days or not all(isinstance(d, int) for d in days):
        raise _fail(
            _rule_where(rule, "days"),
            f"'days' is {days!r}.",
            "a non-empty list of integers, e.g. [7, 14, 30].",
            f"fix 'days' in {DISTRACTORS_PATH}.",
        )
    return days


def _date_offset(ctx: RuleContext, rule: dict) -> str:
    source = str(_param(rule, "from", "from: INVOICE_DATE"))
    base = _parse_date(_field(ctx, source, rule), f"{ctx.case_id} fields.{source}", source)
    return (base + timedelta(days=ctx.rng.choice(_offset_days(rule)))).strftime(DATE_FORMAT)


def _month_period(ctx: RuleContext, rule: dict) -> str:
    """A whole calendar month a number of months from a scored date's month, as 'first to last'.

    The offset is drawn from `months`, none of them 0, so the period never contains the scored date.
    """
    source = str(_param(rule, "from", "from: INVOICE_DATE"))
    months = _param(rule, "months", "months: [-1, 1]")
    if not isinstance(months, list) or not _month_list(months, rule, ctx.pools):
        raise _fail(
            _rule_where(rule, "months"),
            f"'months' is {months!r}.",
            "a non-empty list of non-zero whole months, e.g. months: [-1, 1].",
            f"fix 'months' in {DISTRACTORS_PATH}.",
        )
    base = _parse_date(_field(ctx, source, rule), f"{ctx.case_id} fields.{source}", source)
    index = base.year * 12 + base.month - 1 + int(ctx.rng.choice(months))
    first = datetime(index // 12, index % 12 + 1, 1)
    following = datetime((index + 1) // 12, (index + 1) % 12 + 1, 1)
    last = following - timedelta(days=1)
    return f"{first.strftime(DATE_FORMAT)} to {last.strftime(DATE_FORMAT)}"


def _range_end_offset(ctx: RuleContext, rule: dict) -> str:
    text = _field(ctx, "STATEMENT_DATE_RANGE", rule)
    parts = text.split(" - ")
    if len(parts) != 2:
        raise _fail(
            f"{ctx.case_id} fields.STATEMENT_DATE_RANGE",
            f"{text!r} is not 'DD/MM/YYYY - DD/MM/YYYY'.",
            "a range such as '01/09/2023 - 23/09/2023'.",
            "fix the ground truth entry.",
            field="STATEMENT_DATE_RANGE",
        )
    end = _parse_date(parts[1], f"{ctx.case_id} fields.STATEMENT_DATE_RANGE", "STATEMENT_DATE_RANGE")
    return (end + timedelta(days=ctx.rng.choice(_offset_days(rule)))).strftime(DATE_FORMAT)


_FAKER_WHERE = f"{DATA_POOLS_PATH} -> faker_config (Faker en_AU names) and locations"


# The `address` rule's `state_of` value that leaves the state free.
ANY_STATE = "any"


def _source_state(ctx: RuleContext, rule: dict) -> tuple[str, str | None]:
    """The rule's `state_of` source and the state to draw in: that address's state, or None for `any`."""
    rule_type = rule.get("type")
    source = str(_param(rule, "state_of", f"state_of: PAYER_ADDRESS, or state_of: {ANY_STATE}"))
    if source == ANY_STATE:
        return source, None
    address = _field(ctx, source, rule)
    states = _STATE_RE.findall(address)
    if not states:
        raise _fail(
            f"{ctx.case_id} fields.{source} (needed by rule type {rule_type!r}, state_of: {source})",
            f"{source} is {address!r}, which names no Australian state.",
            "an address ending in a state and postcode, e.g. '12 Ashby St, Unley SA 5061'; "
            f"states {_STATES}.",
            "fix the ground truth entry, or set state_of: any on the rule.",
            field=source,
        )
    return source, states[-1]


def _no_location(err: ValueError, source: str, state: str | None) -> ValueRuleError:
    return _fail(
        f"{DATA_POOLS_PATH} -> locations (state {state})",
        f"no location to draw from in {state}: {str(err).splitlines()[0]}",
        f"at least one entry in locations for every state a {source} can name.",
        f"add a location in {state} to {DATA_POOLS_PATH}.",
    )


def _address(ctx: RuleContext, rule: dict) -> str:
    """An invented address; with `state_of` naming a scored address, in that address's state.

    A site attended or a delivery address sits near the customer it is billed to, so a near-miss for
    PAYER_ADDRESS is drawn in the payer's state rather than across the country.
    """
    source, state = _source_state(ctx, rule)
    try:
        text = ctx.engine.address(ctx.rng, state)
    except ValueError as err:
        raise _no_location(err, source, state) from err
    return _screen_text(ctx, text, _FAKER_WHERE)


def _po_box(ctx: RuleContext, rule: dict) -> str:
    """An invented PO Box; with `state_of` naming a scored address, in that address's state.

    A remittance PO Box is the supplier's own postal address, so it is drawn in the state of
    BUSINESS_ADDRESS, never across the country from the office it serves.
    """
    source, state = _source_state(ctx, rule)
    try:
        loc = ctx.engine.location(ctx.rng, state)
    except ValueError as err:
        raise _no_location(err, source, state) from err
    text = f"PO Box {_digits(ctx.rng, 4)}, {loc['suburb']} {loc['state']} {loc['postcode']}"
    return _screen_text(ctx, text, f"{DATA_POOLS_PATH} -> locations")


def _person(ctx: RuleContext, rule: dict) -> str:
    return _screen_text(ctx, ctx.engine.person(ctx.rng)["full_name"], _FAKER_WHERE)


def _pool_text(ctx: RuleContext, rule: dict) -> str:
    pool = str(_param(rule, "pool", "pool: tagline"))
    text = str(ctx.rng.choice(_pool(ctx, pool, rule)))
    return _screen_text(ctx, text, f"{DATA_POOLS_PATH} -> distractor_text.{pool}")


def _state_text(ctx: RuleContext, rule: dict) -> str:
    """A sentence from the text pool of the document's drawn state (a paid invoice's past tense)."""
    name = str(_param(rule, "state", "state: payment"))
    pools = _param(rule, "pools", "pools: {paid: direct_debit_paid, unpaid: direct_debit_notice}")
    choice = ctx.states.get(name)
    if choice is None or not isinstance(pools, dict) or choice not in pools:
        raise _fail(
            f"{ctx.case_id} states.{name}; {_rule_where(rule, 'pools')}",
            f"the document's {name!r} state is {choice!r}, and the rule has no text pool for it.",
            f"a drawn {name!r} state and a pool for each of its choices, e.g. "
            "pools: {paid: ..., unpaid: ...}.",
            "draw the document's states before its values, or add the pool to the rule in "
            f"{DISTRACTORS_PATH}.",
        )
    pool = str(pools[choice])
    text = str(ctx.rng.choice(_pool(ctx, pool, rule)))
    return _screen_text(ctx, text, f"{DATA_POOLS_PATH} -> distractor_text.{pool}")


def _bsb(ctx: RuleContext, rule: dict) -> str:
    return f"{_digits(ctx.rng, 3)}-{ctx.rng.randint(0, 999):03d}"


def _bank_account(ctx: RuleContext, rule: dict) -> tuple[str, str]:
    """A BSB and the account number it belongs with: one bank account, printed as two lines."""
    length = _int_param(rule, "account_length", "account_length: 8")
    return _bsb(ctx, rule), _digits(ctx.rng, length)


def _bpay(ctx: RuleContext, rule: dict) -> tuple[str, str]:
    """A BPAY biller code and the customer reference paid against it."""
    biller = _digits(ctx.rng, _int_param(rule, "biller_length", "biller_length: 5"))
    return biller, _digits(ctx.rng, _int_param(rule, "reference_length", "reference_length: 10"))


@lru_cache(maxsize=None)
def _load_links(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise _fail(
            str(path),
            "the transaction links file is not a mapping.",
            "a mapping of image filename to a list of links.",
            f"restore {path} from git.",
        )
    return data


def _pick(ctx: RuleContext, rule: dict) -> str:
    """One of a fixed list of words, drawn on the item's seed (a stamp's PAID, PAID IN FULL or RECEIVED)."""
    values = _param(rule, "values", "values: [PAID, PAID IN FULL, RECEIVED]")
    if not isinstance(values, list) or not _word_list(values, rule, ctx.pools):
        raise _fail(
            _rule_where(rule, "values"),
            f"'values' is {values!r}.",
            "a non-empty list of non-empty strings, e.g. values: [PAID, PAID IN FULL, RECEIVED].",
            f"fix 'values' in {DISTRACTORS_PATH}.",
        )
    return str(ctx.rng.choice(values))


@lru_cache(maxsize=1)
def _invoice_layout_ids() -> frozenset[str]:
    """Every invoice layout id, so the case's invoice image can be told from its receipt image."""
    path = layout_file("invoice")
    data = yaml.safe_load(path.read_text())
    layouts = data.get("layouts") if isinstance(data, dict) else None
    if not isinstance(layouts, dict) or not layouts:
        # Read as no layouts, a malformed file would report every paid invoice as never paid.
        raise _fail(
            f"{path.resolve()} -> layouts",
            f"the invoice layout file holds {type(layouts).__name__ if layouts is not None else 'no'} "
            f"'layouts' ({layouts!r:.80}), not a non-empty mapping of layout ids, so the case's invoice "
            "image in the links file cannot be told from its receipt image.",
            "a top-level mapping 'layouts:' of invoice layout id -> layout, e.g. layouts: "
            "{tax_invoice_standard: {...}}.",
            f"restore {path.resolve()} from git (`git checkout -- config/layouts/invoices.yml`), then "
            "run `python -m generators.pipeline validate`.",
        )
    return frozenset(layouts)


def _link_date(ctx: RuleContext, rule: dict) -> str:
    """The bank date of the transaction that paid the case's invoice (`ground_truth/transaction_links.yml`).

    The case's invoice image is the links-file key `{case_id}_{layout}.png` whose layout is an invoice
    layout; it must carry exactly one link. Every invoice link's bank date equals its invoice date, so a
    `repeat` item drawing this rule passes the repeat check; a reseed that pays invoices later fails there.
    """
    prefix = f"{ctx.case_id}_"
    links = _links()
    images = [
        image
        for image in links
        if str(image).startswith(prefix)
        and str(image).endswith(".png")
        and str(image)[len(prefix) : -len(".png")] in _invoice_layout_ids()
    ]
    image = images[0] if len(images) == 1 else f"{prefix}<invoice layout>.png"
    found = links[images[0]] if len(images) == 1 else []
    if len(images) == 1 and isinstance(found, list) and len(found) == 1 and "bank_date" in found[0]:
        return str(found[0]["bank_date"])
    if len(images) > 1:
        what = f"{ctx.case_id} has {len(images)} invoice images in the links file: {sorted(images)}."
    elif not images:
        what = f"{ctx.case_id} has no invoice image in the links file, so its invoice was never paid."
    else:
        count = len(found) if isinstance(found, list) else 0
        what = f"the invoice image {image} carries {count} links, not exactly one with a bank_date."
    raise _fail(
        f"{LINKS_PATH} -> {image}",
        what,
        f"exactly one link under {image}, e.g. - {{bank_statement: {prefix}<bank layout>.png, "
        "bank_date: DD/MM/YYYY, ...}.",
        "bring ground_truth/invoices.yml and transaction_links.yml back in step (re-run the seed scripts), "
        "or remove the item drawing this rule from the layout's distractor_items.",
    )


# Links a caller has handed in for the duration of `links_in_memory`, read instead of LINKS_PATH.
_IN_MEMORY_LINKS: list[dict] = []


@contextmanager
def links_in_memory(links: dict) -> Iterator[None]:
    """Read transaction links from `links` instead of `ground_truth/transaction_links.yml` while open.

    For a caller holding ground truth that is not on disk yet: the ground-truth seed's dry run renders
    entries whose links the link seeder writes afterwards, so the committed links file describes another
    corpus. Every rule that reads links (`link_date`, `annotated_rows`) reads these instead.

    Args:
        links: Image filename -> list of links, as the links file maps them.

    Yields:
        Nothing; the links file is read again once the block exits.
    """
    _IN_MEMORY_LINKS.append(links)
    try:
        yield
    finally:
        _IN_MEMORY_LINKS.pop()


def _links() -> dict:
    return _IN_MEMORY_LINKS[-1] if _IN_MEMORY_LINKS else _load_links(LINKS_PATH)


def _rows(ctx: RuleContext, rule: dict) -> list[dict]:
    if ctx.rows is None:
        raise _fail(
            f"{ctx.case_id} RuleContext.rows (rule type {rule.get('type')!r})",
            "the document has no bank transaction rows.",
            "a bank statement with parsed rows.",
            "use the rule only on bank statements.",
        )
    return ctx.rows


def _squash(text: str) -> str:
    return " ".join(str(text).split())


def _same_amount(paid: object, linked: object) -> bool:
    """Whether a row's paid amount equals a link's amount as decimals, so `61.2` matches `61.20`.

    An unquoted YAML amount loads as a float, whose text can drop a trailing zero; comparing strings
    would then report a matching row as missing.
    """
    if paid is None or linked is None or isinstance(linked, bool):
        return False
    try:
        return Decimal(str(paid)) == Decimal(str(linked))
    except InvalidOperation:
        return False


def _linked_indices(ctx: RuleContext, rows: list[dict]) -> list[int]:
    prefix = f"{ctx.case_id}_"
    claimed: list[int] = []
    for image, links in _links().items():
        for link in links:
            if not str(link["bank_statement"]).startswith(prefix):
                continue
            index = next(
                (
                    i
                    for i, row in enumerate(rows)
                    if i not in claimed
                    and row["date"] == link["bank_date"]
                    and _same_amount(row["paid"], link["bank_amount"])
                    and _squash(row["description"]) == _squash(link["bank_description"])
                ),
                None,
            )
            if index is None:
                raise _fail(
                    f"{LINKS_PATH} -> {image} (case {ctx.case_id}, statement {link['bank_statement']})",
                    f"no statement row matches the link: date {link['bank_date']}, amount "
                    f"{link['bank_amount']}, description {link['bank_description']!r}.",
                    "every link to this statement to match a debit row in the bank ground truth.",
                    "bring ground_truth/bank_statements.yml and transaction_links.yml back in step "
                    "(re-run the seed scripts), then retry.",
                )
            claimed.append(index)
    return claimed


def _annotated_rows(ctx: RuleContext, rule: dict) -> str:
    rows = _rows(ctx, rule)
    extra = _param(rule, "extra", "extra: [1, 3]")
    if not isinstance(extra, list) or len(extra) != 2 or extra[0] > extra[1] or extra[0] < 0:
        raise _fail(
            _rule_where(rule, "extra"),
            f"'extra' is {extra!r}.",
            "[min, max] with 0 <= min <= max, e.g. [1, 3].",
            f"fix 'extra' in {DISTRACTORS_PATH}.",
        )
    linked = _linked_indices(ctx, rows)
    others = [i for i, row in enumerate(rows) if i not in linked and row["paid"] is not None]
    count = ctx.rng.randint(extra[0], extra[1])
    if len(others) < extra[0]:
        raise _fail(
            f"{ctx.case_id} bank statement rows",
            f"only {len(others)} unlinked debit rows, fewer than the minimum {extra[0]} extra rows.",
            "enough unlinked debit rows to annotate.",
            f"lower 'extra' in {DISTRACTORS_PATH} or regenerate the statement.",
        )
    chosen = ctx.rng.sample(others, min(count, len(others)))
    return "|".join(str(i) for i in sorted([*linked, *chosen]))


def _annotation_total(ctx: RuleContext, rule: dict) -> str:
    rows = _rows(ctx, rule)
    indices = [int(i) for i in _selected(ctx, "DX_ANNOTATED_ROWS", rule).split("|") if i]
    total = Decimal("0")
    for i in indices:
        paid = rows[i]["paid"] if 0 <= i < len(rows) else None
        if paid is None:
            raise _fail(
                f"{ctx.case_id} DX_ANNOTATED_ROWS index {i}",
                f"annotated row {i} is not a debit row of the statement.",
                "only debit rows in DX_ANNOTATED_ROWS.",
                "check the annotated_rows rule drew from debit rows.",
            )
        total += Decimal(paid)
    return f"{total:.2f}"


def _row_marks(ctx: RuleContext, rule: dict) -> str:
    """One pen mark per annotated row, aligned with `DX_ANNOTATED_ROWS`, e.g. `tick|circle|tick`.

    The kinds are drawn from `marks` on the item's seed; the overlay's `ink_marks` block draws exactly
    the kind each row is given, never another (a layout's block must be able to draw every kind listed).
    """
    marks = _param(rule, "marks", "marks: [tick, circle, underline]")
    if not isinstance(marks, list) or not _distinct_words(marks, rule, ctx.pools):
        raise _fail(
            _rule_where(rule, "marks"),
            f"'marks' is {marks!r}.",
            "a non-empty list of distinct mark kinds, e.g. marks: [tick, circle, underline].",
            f"fix 'marks' in {DISTRACTORS_PATH}.",
        )
    rows = [i for i in _selected(ctx, "DX_ANNOTATED_ROWS", rule).split("|") if i]
    return "|".join(str(ctx.rng.choice(marks)) for _ in rows)


RULES: dict[str, Callable[[RuleContext, dict], str]] = {
    "digits": _digits_rule,
    "acn": _acn,
    "phone": _phone,
    "email": _email,
    "website": _website,
    "social": _social,
    "survey_url": _survey_url,
    "amount": _amount,
    "amount_fraction": _amount_fraction,
    "amount_ratio": _amount_ratio,
    "fixed": _fixed,
    "repeat": _repeat,
    "item_count": _item_count,
    "date_offset": _date_offset,
    "month_period": _month_period,
    "range_end_offset": _range_end_offset,
    "address": _address,
    "po_box": _po_box,
    "person": _person,
    "pool_text": _pool_text,
    "state_text": _state_text,
    "annotated_rows": _annotated_rows,
    "annotation_total": _annotation_total,
    "row_marks": _row_marks,
    "bsb": _bsb,
    "pick": _pick,
    "link_date": _link_date,
}

# The rules that draw more than one value: each value is emitted under one of the item's keys, in order
# (`emits: [DX_BSB, DX_ACCOUNT_NUMBER]`), so values that belong together (a BSB and its account number)
# always print together. Every rule in `RULES` draws exactly one value.
MULTI_VALUE_RULES: dict[str, Callable[[RuleContext, dict], tuple[str, ...]]] = {
    "bank_account": _bank_account,
    "bpay": _bpay,
}
# How many values each multi-value rule draws; `load_distractor_config` refuses an item whose emits differ
# in number. A property of the rule code above, not a tunable.
MULTI_VALUE_COUNTS: dict[str, int] = {"bank_account": 2, "bpay": 2}

RULE_TYPES: frozenset[str] = frozenset(RULES) | frozenset(MULTI_VALUE_RULES)


def rule_arity(rule_type: str) -> int:
    """Return how many values a rule draws, so how many keys an item using it emits.

    Args:
        rule_type: A rule type, a key of `RULES` or `MULTI_VALUE_RULES`.

    Returns:
        The rule's `MULTI_VALUE_COUNTS` entry, or 1 for a rule of `RULES`.
    """
    return MULTI_VALUE_COUNTS.get(rule_type, 1)


def draw_values(ctx: RuleContext, rule: dict) -> tuple[str, ...]:
    """Draw a rule's values: one for a rule of `RULES`, `rule_arity` of them for a multi-value rule.

    Args:
        ctx: The document's rule context.
        rule: The item's rule mapping, with its `type`.

    Returns:
        The drawn values, in the order of the item's emitted keys.
    """
    multi = MULTI_VALUE_RULES.get(rule["type"])
    return multi(ctx, rule) if multi is not None else (RULES[rule["type"]](ctx, rule),)


# How a rule's value answers a collision with a scored value, a property of the rule code above and not
# a tunable; every rule sits in exactly one of the three sets (`_check_declarations` enforces it).
#
# REDRAWABLE: the value is drawn from `ctx.rng`, so a redraw can clear a collision.
REDRAWABLE: frozenset[str] = frozenset(
    {
        "digits",
        "acn",
        "phone",
        "email",
        "amount",
        "amount_fraction",
        "amount_ratio",
        "date_offset",
        "month_period",
        "range_end_offset",
        "address",
        "po_box",
        "person",
        "pool_text",
        "state_text",
        "annotated_rows",
        "row_marks",
        "bsb",
        "bank_account",
        "bpay",
        "pick",
    }
)
# DESELECTABLE: the value ignores the generator (a fixed value, a count, a sum), so a redraw would
# repeat it; on a collision the item, and every item requiring it, is dropped for that case. The one
# permitted deselection.
DESELECTABLE: frozenset[str] = frozenset({"fixed", "item_count", "annotation_total"})
# FAIL_AT_ONCE: the value ignores the generator and may not be dropped: a `repeat` copies its field
# verbatim (a mismatch is a code fault), `link_date` copies the paying transaction's bank date from the
# links file (drawn by a repeat item, whose check pins it to the invoice date), and `website`, `social`
# and `survey_url` are fixed by the supplier slug and the code already drawn. A collision fails the render
# on the first draw.
FAIL_AT_ONCE: frozenset[str] = frozenset({"repeat", "link_date", "website", "social", "survey_url"})
# Rules whose value embeds the supplier slug (`_slug`), whatever else they draw. A collision caused by
# the slug cannot be cleared by any redraw, `email` included, so it fails at once naming SUPPLIER_NAME.
SLUG_RULES: frozenset[str] = frozenset({"email", "website", "social", "survey_url"})


@dataclass(frozen=True)
class Param:
    """One required parameter of a rule's mapping in `config/distractors.yml`.

    Attributes:
        expected: What a valid value looks like, with an example, for a diagnostic.
        valid: `(value, rule, pools) -> bool`; `rule` is the whole rule mapping, for a check across
            parameters, and `pools` the loaded `config/data_pools.yml`.
    """

    expected: str
    valid: Callable[[object, dict, dict], bool]


def _positive_int(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _text(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, str)


def _non_empty_text(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, str) and bool(value)


def _text_pool(value: object, rule: dict, pools: dict) -> bool:
    texts = pools.get("distractor_text")
    pool = texts.get(value) if isinstance(texts, dict) and isinstance(value, str) else None
    if isinstance(pool, dict):
        return bool(pool) and all(isinstance(sub, list) and bool(sub) for sub in pool.values())
    return isinstance(pool, list) and bool(pool)


def _flat_pool(value: object, pools: dict) -> bool:
    texts = pools.get("distractor_text")
    pool = texts.get(value) if isinstance(texts, dict) and isinstance(value, str) else None
    return isinstance(pool, list) and bool(pool)


def _state_pools(value: object, rule: dict, pools: dict) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(k, str) and _flat_pool(v, pools) for k, v in value.items())
    )


def _phone_kind(value: object, rule: dict, pools: dict) -> bool:
    return value in PHONE_KINDS


def _min_bound(value: object, rule: dict, pools: dict) -> bool:
    return _as_bound(value) is not None


def _max_bound(value: object, rule: dict, pools: dict) -> bool:
    high, low = _as_bound(value), _as_bound(rule.get("min"))
    return high is not None and (low is None or low <= high)


@lru_cache(maxsize=1)
def _scored_columns() -> frozenset[str]:
    """Every column the extraction schema scores, on any document type."""
    fields = load_extraction_schema().get_all_doc_type_fields()
    return frozenset(column for columns in fields.values() for column in columns)


def _scored_column(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, str) and value in _scored_columns()


def _min_fraction(value: object, rule: dict, pools: dict) -> bool:
    return _as_fraction(value) is not None


def _max_fraction(value: object, rule: dict, pools: dict) -> bool:
    high, low = _as_fraction(value), _as_fraction(rule.get("min_fraction"))
    return high is not None and (low is None or low <= high)


def _min_ratio(value: object, rule: dict, pools: dict) -> bool:
    return _as_ratio(value) is not None


def _max_ratio(value: object, rule: dict, pools: dict) -> bool:
    high, low = _as_ratio(value), _as_ratio(rule.get("min_ratio"))
    return high is not None and (low is None or low <= high)


def _element(value: object, rule: dict, pools: dict) -> bool:
    return value in ELEMENTS


def _cap_param(value: object, rule: dict, pools: dict) -> bool:
    return value == NO_CAP or _as_cap(value) is not None


def _state_source(value: object, rule: dict, pools: dict) -> bool:
    return value == ANY_STATE or _scored_column(value, rule, pools)


def _day_list(value: object, rule: dict, pools: dict) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(d, int) and not isinstance(d, bool) for d in value)
    )


def _month_list(value: object, rule: dict, pools: dict) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(m, int) and not isinstance(m, bool) and m != 0 for m in value)
    )


def _extra_range(value: object, rule: dict, pools: dict) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) for n in value)
        and 0 <= value[0] <= value[1]
    )


def _word_list(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and bool(v) for v in value)


def _distinct_words(value: object, rule: dict, pools: dict) -> bool:
    return isinstance(value, list) and _word_list(value, rule, pools) and len(set(value)) == len(value)


PHONE_KINDS: tuple[str, ...] = ("landline", "mobile", "freecall")

POOL_PARAM = Param(
    "the name of a non-empty list under distractor_text in config/data_pools.yml, or of a mapping of "
    "industry to non-empty lists",
    _text_pool,
)
_DAYS = Param("a non-empty list of whole days, e.g. days: [7, 14, 30]", _day_list)

# Every parameter each rule reads from its mapping, beside `RULES`; `load_distractor_config` refuses a
# rule with a parameter missing, mistyped or not declared here. A `repeat` reads its field from the
# item's own `field:`, so its mapping carries no parameters.
RULE_PARAMS: dict[str, dict[str, Param]] = {
    "digits": {
        "length": Param("a positive whole number of digits, e.g. length: 8", _positive_int),
        "prefix": Param('a string printed before the digits, "" for none, e.g. prefix: "INV-"', _text),
    },
    "acn": {},
    "phone": {"kind": Param(f"one of {list(PHONE_KINDS)}, e.g. kind: landline", _phone_kind)},
    "email": {"pool": POOL_PARAM},
    "website": {},
    "social": {},
    "survey_url": {},
    "amount": {
        "min": Param('a non-negative decimal string, at most two places, e.g. min: "1.00"', _min_bound),
        "max": Param(
            'a non-negative decimal string, at most two places and not below min, e.g. max: "25.00"',
            _max_bound,
        ),
    },
    "amount_fraction": {
        "of": Param("a scored column holding an amount, e.g. of: TOTAL_AMOUNT", _scored_column),
        "min_fraction": Param(
            'a decimal string strictly between 0 and 1, e.g. min_fraction: "0.03"', _min_fraction
        ),
        "max_fraction": Param(
            "a decimal string strictly between 0 and 1 and not below min_fraction, "
            'e.g. max_fraction: "0.25"',
            _max_fraction,
        ),
    },
    "amount_ratio": {
        "of": Param("a scored column holding an amount, e.g. of: TOTAL_AMOUNT", _scored_column),
        "min_ratio": Param('a positive decimal string, e.g. min_ratio: "0.40"', _min_ratio),
        "max_ratio": Param(
            'a positive decimal string not below min_ratio, e.g. max_ratio: "1.50"', _max_ratio
        ),
        "element": Param(
            f"which value of the column, one of {list(ELEMENTS)}, e.g. element: whole", _element
        ),
        "cap": Param(
            f'{NO_CAP} for no cap, or the range a capped figure is drawn in, e.g. cap: {{min: "500.00", '
            'max: "999.00"}',
            _cap_param,
        ),
    },
    "fixed": {"value": Param('the string to print, e.g. value: "0.00"', _non_empty_text)},
    "repeat": {},
    "item_count": {},
    "date_offset": {
        "from": Param("the scored date column to offset, e.g. from: INVOICE_DATE", _non_empty_text),
        "days": _DAYS,
    },
    "month_period": {
        "from": Param(
            "the scored date column the period is counted from, e.g. from: INVOICE_DATE", _non_empty_text
        ),
        "months": Param("a non-empty list of non-zero whole months, e.g. months: [-1, 1]", _month_list),
    },
    "range_end_offset": {"days": _DAYS},
    "address": {
        "state_of": Param(
            f"a scored address column whose state the address is drawn in, or {ANY_STATE!r}, e.g. "
            "state_of: PAYER_ADDRESS",
            _state_source,
        )
    },
    "po_box": {
        "state_of": Param(
            f"a scored address column whose state the PO Box is drawn in, or {ANY_STATE!r}, e.g. "
            "state_of: BUSINESS_ADDRESS",
            _state_source,
        )
    },
    "person": {},
    "pool_text": {"pool": POOL_PARAM},
    "state_text": {
        "state": Param("the name of a state under states.<type>, e.g. state: payment", _non_empty_text),
        "pools": Param(
            "a mapping of each of the state's choices to the name of a non-empty list under "
            "distractor_text, "
            "e.g. pools: {paid: direct_debit_paid, unpaid: direct_debit_notice}",
            _state_pools,
        ),
    },
    "annotated_rows": {
        "extra": Param("[min, max] whole numbers with 0 <= min <= max, e.g. [1, 3]", _extra_range)
    },
    "annotation_total": {},
    "row_marks": {
        "marks": Param(
            "a non-empty list of distinct mark kinds the layout's ink_marks block draws, e.g. marks: "
            "[tick, circle, underline]",
            _distinct_words,
        )
    },
    "bsb": {},
    "pick": {
        "values": Param(
            "a non-empty list of non-empty strings, one drawn per document, e.g. values: [PAID, "
            "PAID IN FULL, RECEIVED]",
            _word_list,
        )
    },
    "link_date": {},
    "bank_account": {
        "account_length": Param("a positive whole number of digits, e.g. account_length: 8", _positive_int)
    },
    "bpay": {
        "biller_length": Param("a positive whole number of digits, e.g. biller_length: 5", _positive_int),
        "reference_length": Param(
            "a positive whole number of digits, e.g. reference_length: 10", _positive_int
        ),
    },
}


def _check_declarations() -> None:
    """Refuse, at import, a rule missing from the redraw sets or the parameter table.

    Raises:
        ValueRuleError: A rule is in no redraw set or in more than one, or has no `RULE_PARAMS` entry;
            a table names a rule that does not exist; or a multi-value rule lacks its count.
    """
    here = str(Path(__file__).resolve())
    sets = {"REDRAWABLE": REDRAWABLE, "DESELECTABLE": DESELECTABLE, "FAIL_AT_ONCE": FAIL_AT_ONCE}
    rules = set(RULES) | set(MULTI_VALUE_RULES)
    for name in sorted(rules):
        homes = [label for label, members in sets.items() if name in members]
        if len(homes) != 1 or name not in RULE_PARAMS:
            raise _fail(
                f"{here} -> RULES[{name!r}]",
                f"rule {name!r} is in {homes or 'no'} redraw set(s) and "
                f"{'has' if name in RULE_PARAMS else 'lacks'} a RULE_PARAMS entry.",
                "every rule in exactly one of REDRAWABLE, DESELECTABLE, FAIL_AT_ONCE, and in RULE_PARAMS.",
                f"declare {name!r} beside RULES in {here}.",
            )
    declared = (
        REDRAWABLE | DESELECTABLE | FAIL_AT_ONCE | SLUG_RULES | set(RULE_PARAMS) | set(MULTI_VALUE_COUNTS)
    )
    stray = sorted(declared - rules)
    mismatched = sorted(
        (set(MULTI_VALUE_COUNTS) ^ set(MULTI_VALUE_RULES)) | (set(RULES) & set(MULTI_VALUE_RULES))
    )
    if mismatched:
        raise _fail(
            f"{here} -> MULTI_VALUE_RULES",
            f"{mismatched} are not each a multi-value rule with a count, and in RULES too or not at all.",
            "every multi-value rule in MULTI_VALUE_RULES and MULTI_VALUE_COUNTS, and none in RULES.",
            f"declare {mismatched} consistently beside MULTI_VALUE_RULES in {here}.",
        )
    if stray:
        raise _fail(
            f"{here} -> RULES",
            f"{stray} are declared but are not rules.",
            f"names from {sorted(RULES)}.",
            f"remove {stray} from the declarations in {here}.",
        )


_check_declarations()


def supplier_slug(ctx: RuleContext) -> str:
    """Return the supplier slug the slug rules (`SLUG_RULES`) build their value from.

    Args:
        ctx: The document's rule context.

    Returns:
        SUPPLIER_NAME lowercased, its trailing company words dropped and its words run together.

    Raises:
        ValueRuleError: The supplier name is missing, has no letters or digits, or is a real business.
    """
    return _slug(ctx, {"type": "slug"})


def parse_bank_rows(fields: dict[str, str]) -> list[dict]:
    """Parse a bank statement's pipe-joined transaction columns into row dicts.

    Args:
        fields: The bank statement's scored fields.

    Returns:
        One dict per row with `date`, `description`, `paid` and `received`; an absent amount is None.

    Raises:
        ValueRuleError: The columns are missing or their counts differ.
    """
    names = (
        "TRANSACTION_DATES",
        "TRANSACTION_DESCRIPTIONS",
        "TRANSACTION_AMOUNTS_PAID",
        "TRANSACTION_AMOUNTS_RECEIVED",
    )
    columns = [str(fields.get(name, "")).split("|") for name in names]
    if len({len(c) for c in columns}) != 1 or columns[0] == [""]:
        raise _fail(
            "bank statement TRANSACTION_* fields",
            "the transaction columns are missing or have different lengths.",
            "four pipe-joined columns with the same number of entries.",
            "fix the ground truth entry.",
        )
    return [
        {
            "date": d,
            "description": desc,
            "paid": None if paid == "NOT_FOUND" else paid,
            "received": None if recv == "NOT_FOUND" else recv,
        }
        for d, desc, paid, recv in zip(*columns, strict=True)
    ]
