"""Shared content-generation engine for scripts/seed_ground_truth.py.

Loads config/data_pools.yml once, owns a seeded Faker("en_AU"), and exposes
the primitives scripts/seed_ground_truth.py calls in place of in-script
constants and `pool[i % len(pool)]` cycling: person/location/address (Faker +
curated locations), fictional_business (invented AU entities screened
against a real-name blocklist), and sample / NonRepeatingSampler (seeded pool
draws). Every primitive is driven by an injected `random.Random`, so a
reseed is reproducible and diffable run-to-run.
"""

import random
import re
from collections.abc import Callable, Container
from pathlib import Path

import yaml
from faker import Faker

from generators.common import generate_abn

_DATA_POOLS_PATH = Path(__file__).resolve().parent.parent / "config" / "data_pools.yml"

# Top-level keys load_pools() requires; each maps to the dotted sub-keys (if
# any) that must also be present, so a missing nested key fails fast too.
_REQUIRED_KEYS: dict[str, list[str]] = {
    "faker_config": ["locale", "seed_base"],
    "locations": [],
    "street_types": [],
    "business_name_parts": ["surnames", "suburb_prefixes", "category_nouns"],
    "product_catalog": [],
    "banks": [],
    "bank_descriptions": ["debit_share", "debit", "credit", "direction_prefixes"],
    "bank_balance": ["account_kind", "floors", "lowest_above_floor"],
    "retailers": [],
    "professional_services": [],
    "real_name_blocklist_extra": [],
    "real_brand_blocklist": [],
    "catalogue_only_brands": [],
    "brand_screen": ["screened", "exempt"],
    "receipt_categories": [],
    "service_categories": [],
    "category_catalog": [],
    "abn_register": ["path"],
}

# The keys of one `category_catalog` or `product_catalog` line; every one is required.
_CATALOG_ENTRY_KEYS = ("description", "unit", "price_low", "price_high", "quantity")
# Each line catalogue, and the category list its keys come from.
_CATALOGUE_OWNERS = {"category_catalog": "service_categories", "product_catalog": "receipt_categories"}
_CATALOG_EXAMPLES = {
    "category_catalog": (
        'plumbing: [{description: "Labour: licensed plumber", unit: hrs, price_low: 95, price_high: 140, '
        "quantity: [1, 6]}]"
    ),
    "product_catalog": (
        'grocery: [{description: "Milk 2L", unit: ea, price_low: 2.50, price_high: 5.50, quantity: [1, 3]}]'
    ),
}


def _missing_key_error(path: Path, dotted_key: str, subkeys: list[str]) -> str:
    """Build a four-element fail-fast diagnostic for a missing pool key."""
    example = f"a mapping with keys {subkeys}" if subkeys else "a non-empty value"
    return (
        "content pool is missing a required key.\n"
        f"  What:     '{dotted_key}' not found in {path}.\n"
        f"  Where:    {path} -> '{dotted_key}'.\n"
        f"  Expected: {example}.\n"
        f"  Recover:  add the missing key to {path} under '{dotted_key}'."
    )


def _not_mapping_error(path: Path, dotted_key: str) -> str:
    """Four-element diagnostic for a key that is present but not a mapping."""
    return (
        "content pool key is present but not a mapping.\n"
        f"  What:     '{dotted_key}' in {path} is not a YAML mapping (dict).\n"
        f"  Where:    {path} -> '{dotted_key}'.\n"
        "  Expected: a mapping, e.g. 'faker_config:\\n  locale: en_AU\\n  seed_base: 42'.\n"
        f"  Recover:  make '{dotted_key}' a mapping with the required sub-keys in {path}."
    )


def load_pools(path: Path = _DATA_POOLS_PATH) -> dict:
    """Load and validate config/data_pools.yml, failing fast on any missing key.

    Args:
        path: Path to the pools YAML file.

    Returns:
        The parsed pools dict.

    Raises:
        FileNotFoundError: `path` does not exist.
        ValueError: a required top-level or nested key is missing.
    """
    if not path.exists():
        raise FileNotFoundError(
            "content pool file not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path}\n"
            "  Expected: a YAML file with the required top-level pool keys "
            "(see generators/content_engine.py _REQUIRED_KEYS).\n"
            f"  Recover:  create {path} (see config/data_pools.yml in the repo for the canonical shape)."
        )

    data = yaml.safe_load(path.read_text())

    if not isinstance(data, dict):
        raise ValueError(
            "content pool file is empty or not a mapping.\n"
            f"  What:     {path} did not parse to a YAML mapping (got {type(data).__name__}).\n"
            f"  Where:    {path}\n"
            "  Expected: a non-empty YAML file whose top level is a mapping of pool keys.\n"
            f"  Recover:  populate {path} with the required top-level pool keys "
            "(see generators/content_engine.py _REQUIRED_KEYS)."
        )

    for key, subkeys in _REQUIRED_KEYS.items():
        if key not in data:
            raise ValueError(_missing_key_error(path, key, subkeys))
        if subkeys:
            if not isinstance(data[key], dict):
                raise ValueError(_not_mapping_error(path, key))
            for sub in subkeys:
                if sub not in data[key]:
                    raise ValueError(_missing_key_error(path, f"{key}.{sub}", []))

    _check_category_catalog(data, path)
    _check_product_catalog(data, path)
    _check_brands(data, path)
    return data


def _strings(value: object) -> list[str]:
    """Every string in a pool, its mapping keys included, depth first."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in (*_strings(key), *_strings(item))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def names_brand(text: str, brand: str) -> bool:
    """Whether `brand` is a whole word of `text`, ignoring case (the business-name screen's match)."""
    return re.search(rf"(?<![a-z0-9]){re.escape(brand.lower())}(?![a-z0-9])", text.lower()) is not None


def _brand_error(path: Path, key: str, what: str, expected: str, recover: str) -> ValueError:
    return ValueError(
        "content pools name a real brand, or the brand screen is misdeclared.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> '{key}'.\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _check_brands(data: dict, path: Path) -> None:
    """Refuse a brand in a screened pool, a common-name brand in a catalogue line, or a pool left unsorted.

    `brand_screen` sorts every top-level key into `screened` or `exempt`; no string anywhere in a screened
    pool may name a `real_brand_blocklist` brand, and no product or service description may name one of
    `catalogue_only_brands` either. Matching is whole-word and case-insensitive.

    Raises:
        ValueError: A key is in neither list or both, a list names an unknown key, or a brand is named.
    """
    screen = data["brand_screen"]
    screened, exempt = screen["screened"], screen["exempt"]
    example = "brand_screen: {screened: [product_catalog, ...], exempt: [payment_terminal, ...]}"
    if not isinstance(screened, list) or not isinstance(exempt, list):
        raise _brand_error(
            path,
            "brand_screen",
            f"brand_screen is {screen!r}, not two lists of pool names.",
            example + ".",
            f"give brand_screen a screened and an exempt list in {path}.",
        )
    keys = list(data)
    unsorted = [k for k in keys if (k in screened) == (k in exempt)]
    unknown = [k for k in (*screened, *exempt) if k not in data]
    if unsorted or unknown:
        raise _brand_error(
            path,
            "brand_screen",
            f"pools in neither list or in both: {unsorted}; names that are not pools: {unknown}.",
            f"every top-level key of {path.name} in exactly one of the two lists, e.g. {example}.",
            f"list each pool once under brand_screen.screened (or .exempt, with the reason) in {path}.",
        )
    brands = [str(b) for b in data["real_brand_blocklist"]]
    catalogue_brands = brands + [str(b) for b in data["catalogue_only_brands"]]
    checks = [(pool, text, brands) for pool in screened for text in _strings(data[pool])]
    checks += [
        (f"{name}.{category}", line["description"], catalogue_brands)
        for name in ("product_catalog", "category_catalog")
        for category, lines in data[name].items()
        for line in lines
    ]
    for where, text, listed in checks:
        hit = next((b for b in listed if names_brand(text, b)), None)
        if hit is not None:
            raise _brand_error(
                path,
                where,
                f"{text!r} names the brand {hit!r}.",
                "generic wording with no brand, e.g. 'Paracetamol Tablets 24pk', not 'Panadol 24pk'.",
                f"rewrite the entry under '{where}' in {path}, or move the pool to brand_screen.exempt "
                "with a comment saying why its brands are deliberate.",
            )


def _catalog_error(
    path: Path, key: str, what: str, recover: str, *, catalogue: str = "category_catalog"
) -> ValueError:
    owners = _CATALOGUE_OWNERS[catalogue]
    return ValueError(
        f"{catalogue} is invalid.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> '{key}'.\n"
        f"  Expected: {catalogue}: {{{_CATALOG_EXAMPLES[catalogue]}}}, each key one of {owners} and "
        f"each line carrying exactly {list(_CATALOG_ENTRY_KEYS)}, prices positive with price_low <= price_high, "
        "quantity [min, max] whole numbers with 1 <= min <= max.\n"
        f"  Recover:  {recover}"
    )


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_price(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _check_category_catalog(data: dict, path: Path) -> None:
    """Refuse a malformed `category_catalog`: the invoice lines each service category may print.

    Coverage of every category an invoice layout declares is checked with the layouts, by `pipeline
    validate` (generators/layout_dsl/schema.py), and the seed refuses a category with no entry.

    Raises:
        ValueError: The catalogue is not a mapping, names a category outside `service_categories`, has
            an empty category, or a line missing a key, carrying an unknown one, or out of range.
    """
    _check_catalog(data, path, "category_catalog", require_every_category=False)


def _check_product_catalog(data: dict, path: Path) -> None:
    """Refuse a malformed `product_catalog`: the receipt lines each merchant category may sell.

    Every receipt category must have lines, since the seed draws a receipt's lines from its merchant's
    own category.

    Raises:
        ValueError: As `_check_category_catalog`, or a receipt category has no entry.
    """
    _check_catalog(data, path, "product_catalog", require_every_category=True)


def _check_catalog(data: dict, path: Path, name: str, *, require_every_category: bool) -> None:
    """Refuse a malformed line catalogue keyed by category; see the two callers."""
    catalogue = data[name]
    owner = _CATALOGUE_OWNERS[name]
    categories = data[owner]
    if not isinstance(catalogue, dict):
        raise _catalog_error(
            path,
            name,
            f"{name} is {catalogue!r}, not a mapping of category to its lines.",
            f"rewrite {name} in {path} as a mapping of category to a list of lines.",
            catalogue=name,
        )
    if require_every_category and isinstance(categories, list):
        missing = [c for c in categories if c not in catalogue]
        if missing:
            raise _catalog_error(
                path,
                f"{name}.{missing[0]}",
                f"the {owner} {missing} have no lines in {name}.",
                f"add a {missing[0]}: [...] entry under {name} in {path}.",
                catalogue=name,
            )
    for category, lines in catalogue.items():
        here = f"{name}.{category}"
        if not isinstance(categories, list) or category not in categories:
            raise _catalog_error(
                path,
                here,
                f"{category!r} is not one of {owner} {categories}.",
                f"add {category!r} to {owner}, or correct the key {here}.",
                catalogue=name,
            )
        if not isinstance(lines, list) or not lines:
            raise _catalog_error(
                path,
                here,
                f"{here} is {lines!r}, not a non-empty list of lines.",
                f"list lines under {here}.",
                catalogue=name,
            )
        for index, line in enumerate(lines):
            where = f"{here}[{index}]"
            if not isinstance(line, dict) or sorted(line) != sorted(_CATALOG_ENTRY_KEYS):
                keys = sorted(line) if isinstance(line, dict) else line
                raise _catalog_error(
                    path,
                    where,
                    f"the line has the keys {keys!r}, not exactly {list(_CATALOG_ENTRY_KEYS)}.",
                    f"give {where} exactly the keys {list(_CATALOG_ENTRY_KEYS)}.",
                    catalogue=name,
                )
            problem = _catalog_line_problem(line)
            if problem is not None:
                key, check = problem
                raise _catalog_error(
                    path,
                    f"{where}.{key}",
                    f"{key} is {line[key]!r}, which {check}.",
                    f"correct {where}.{key} in {path}.",
                    catalogue=name,
                )


def _catalog_line_problem(line: dict) -> tuple[str, str] | None:
    """Return the first failing key of a catalogue line and the check it fails, or None.

    The checks run in order and each runs only once the ones before it pass, so a comparison never meets
    a value of the wrong type.
    """
    quantity = line["quantity"]
    checks: tuple[tuple[str, Callable[[], bool], str], ...] = (
        (
            "description",
            lambda: isinstance(line["description"], str) and bool(line["description"].strip()),
            "is not a non-empty string",
        ),
        (
            "unit",
            lambda: isinstance(line["unit"], str) and bool(line["unit"].strip()),
            "is not a non-empty string",
        ),
        ("price_low", lambda: _is_price(line["price_low"]), "is not a positive number"),
        ("price_high", lambda: _is_price(line["price_high"]), "is not a positive number"),
        (
            "price_low",
            lambda: line["price_low"] <= line["price_high"],
            f"exceeds price_high {line['price_high']!r}",
        ),
        (
            "quantity",
            lambda: (
                isinstance(quantity, list) and len(quantity) == 2 and all(_is_count(q) for q in quantity)
            ),
            "is not two whole numbers of at least 1",
        ),
        ("quantity", lambda: quantity[0] <= quantity[1], "has its minimum above its maximum"),
    )
    for key, ok, check in checks:
        if not ok():
            return key, check
    return None


class ContentEngine:
    """Seeded generator for fictional AU business/person/address content."""

    def __init__(self, pools: dict) -> None:
        self.pools = pools
        self._faker = Faker(pools["faker_config"]["locale"])
        # Defined baseline state before any per-call reseed in _seed_faker();
        # keeps a freshly-constructed engine deterministic even if a caller
        # (e.g. a future primitive) drew from self._faker before seeding rng.
        self._faker.seed_instance(pools["faker_config"]["seed_base"])
        self._blocklist = frozenset(
            name.lower()
            for name in (
                [r["name"] for r in pools["retailers"]]
                + [p["name"] for p in pools["professional_services"]]
                + pools["real_name_blocklist_extra"]
                + pools["real_brand_blocklist"]
            )
        )

    @property
    def blocklist(self) -> frozenset[str]:
        """The lower-cased blocklist: retailers, professional_services, the extras and the brands."""
        return self._blocklist

    def _seed_faker(self, rng: random.Random) -> None:
        """Reseed the engine's Faker instance from the injected rng stream."""
        self._faker.seed_instance(rng.randint(0, 2**32 - 1))

    def person(self, rng: random.Random) -> dict:
        """Return a seeded en_AU person: {first_name, last_name, full_name}."""
        self._seed_faker(rng)
        first = self._faker.first_name()
        last = self._faker.last_name()
        return {"first_name": first, "last_name": last, "full_name": f"{first} {last}"}

    def location(self, rng: random.Random, state: str | None = None) -> dict:
        """Return a seeded {suburb, postcode, state} dict from the locations pool.

        Args:
            rng: The seeded generator.
            state: Draw from this state's locations only; None draws from every location.

        Raises:
            ValueError: `state` has no entry in `locations`.
        """
        if state is None:
            return sample(rng, self.pools["locations"])
        return sample(rng, self._locations_in(state))

    def address(self, rng: random.Random, state: str | None = None) -> str:
        """Return a seeded AU-style address: "N Street St, Suburb ST PPPP".

        Args:
            rng: The seeded generator.
            state: Draw the suburb from this state's locations only (a site near the payer, say); None
                draws from every location.

        Raises:
            ValueError: `state` has no entry in `locations`.
        """
        self._seed_faker(rng)
        street_num = self._faker.random_int(min=1, max=400)
        street_name = self._faker.last_name()
        street_type = sample(rng, self.pools["street_types"])
        loc = self.location(rng, state)
        return f"{street_num} {street_name} {street_type}, {loc['suburb']} {loc['state']} {loc['postcode']}"

    def _locations_in(self, state: str) -> list[dict]:
        """The `locations` of one state, failing fast when it has none."""
        found = [loc for loc in self.pools["locations"] if loc["state"] == state]
        if not found:
            raise ValueError(
                "content_engine.address: no location in the requested state.\n"
                f"  What:     state {state!r} has no entry under 'locations'.\n"
                f"  Where:    {_DATA_POOLS_PATH} -> 'locations'.\n"
                "  Expected: at least one {suburb, postcode, state} entry for every state an address is "
                "drawn in, e.g. - {suburb: Darwin, postcode: '0800', state: NT}.\n"
                f"  Recover:  add a location in {state} to 'locations' in {_DATA_POOLS_PATH}."
            )
        return found

    def fictional_business_name(self, rng: random.Random, category: str) -> str:
        """Invented AU business NAME only (blocklist-screened); no address/ABN.

        Raises:
            ValueError: `category` has no entry in business_name_parts.category_nouns.
            RuntimeError: the retry budget was exhausted without a clean name.
        """
        parts = self.pools["business_name_parts"]
        nouns = parts["category_nouns"].get(category)
        if not nouns:
            raise ValueError(
                "content_engine.fictional_business_name: unknown category.\n"
                f"  What:     category {category!r} has no entry under "
                "'business_name_parts.category_nouns'.\n"
                f"  Where:    {_DATA_POOLS_PATH} -> "
                f"'business_name_parts.category_nouns.{category}'.\n"
                "  Expected: a list of nouns, e.g. "
                '\'hardware: ["Hardware", "Trade Supplies"]\'.\n'
                f"  Recover:  add a '{category}:' entry under "
                f"'business_name_parts.category_nouns' in {_DATA_POOLS_PATH}."
            )
        max_attempts = 20
        for _ in range(max_attempts):
            noun = sample(rng, nouns)
            if rng.random() < 0.5:
                name = f"{sample(rng, parts['surnames'])} {noun}"
            else:
                name = f"{sample(rng, parts['suburb_prefixes'])} {noun}"
            if name.lower() not in self._blocklist:
                return name
        raise RuntimeError(
            "content_engine.fictional_business_name: exhausted retry budget without a clean name.\n"
            f"  What:     {max_attempts} draws for category {category!r} all collided with "
            "the real-name blocklist.\n"
            f"  Where:    {_DATA_POOLS_PATH} -> 'business_name_parts' (category {category!r}) "
            "and 'real_name_blocklist_extra'.\n"
            "  Expected: enough surname/noun combinations for the category to clear the "
            f"blocklist within {max_attempts} attempts.\n"
            "  Recover:  widen 'business_name_parts.surnames', 'suburb_prefixes', or "
            f"'category_nouns.{category}' in {_DATA_POOLS_PATH}."
        )

    def fictional_business(self, rng: random.Random, category: str, registered: Container[str]) -> dict:
        """Invented AU business (blocklist-screened) + generate_abn() + address.

        Args:
            rng: The seeded stream the name and address are drawn from.
            category: A key of business_name_parts.category_nouns.
            registered: The public ABN register (generators/abn_register.py); the ABN avoids it.

        Returns:
            {name, address, abn, category}.

        Raises:
            ValueError: `category` has no entry in business_name_parts.category_nouns.
            RuntimeError: the retry budget was exhausted without a clean name.
        """
        name = self.fictional_business_name(rng, category)
        return {
            "name": name,
            "address": self.address(rng),
            "abn": generate_abn(registered=registered),
            "category": category,
        }


def reachable_blocked_names(pools: dict) -> list[str]:
    """Blocklisted real business names the name grammar could actually emit.

    `fictional_business_name` composes exactly `{surname | suburb_prefix} {noun}`
    and rejects any candidate on the blocklist, retrying up to a budget. That
    runtime guard is the last line of defence; this is the first one, and it is
    the one that fails at a useful moment.

    The blocklist currently cannot fire: none of its names is expressible in the
    grammar, because the nouns are trade phrases ("business supplies",
    "& partners lawyers") and real brands are not. It is insurance against a
    future edit to `business_name_parts` — adding "Origin" to suburb_prefixes and
    "Energy" to a category, say — which would silently make a real company
    emittable. Checking reachability at startup turns that from a runtime retry
    into a configuration error raised at the moment the pool is edited.

    Args:
        pools: The loaded config/data_pools.yml mapping.

    Returns:
        Blocked names the grammar can produce, sorted. Empty is the healthy case.
    """
    parts = pools["business_name_parts"]
    prefixes = {p.lower() for p in (*parts["surnames"], *parts["suburb_prefixes"])}
    nouns = {n.lower() for group in parts["category_nouns"].values() for n in group}
    reachable = {f"{prefix} {noun}" for prefix in prefixes for noun in nouns}

    blocked = (
        [r["name"] for r in pools["retailers"]]
        + [p["name"] for p in pools["professional_services"]]
        + list(pools["real_name_blocklist_extra"])
    )
    return sorted({name for name in blocked if name.lower() in reachable})


def sample(rng: random.Random, pool: list):
    """Seeded single draw from a non-empty pool."""
    if not pool:
        raise ValueError(
            "content_engine.sample: pool is empty; cannot draw.\n"
            "  What:     sample() was called with an empty pool.\n"
            "  Where:    caller of generators.content_engine.sample.\n"
            "  Expected: a non-empty list.\n"
            "  Recover:  widen the source pool in config/data_pools.yml before sampling from it."
        )
    return rng.choice(pool)


class NonRepeatingSampler:
    """Cycles a shuffled copy of `pool`, reshuffling on exhaustion.

    Replaces `pool[i % len(pool)]`: draws are a random permutation of the
    pool each pass (not the same fixed order every cycle), so entity
    selection varies and de-correlates across doc types even when two
    samplers share the same underlying pool.
    """

    def __init__(self, rng: random.Random, pool: list) -> None:
        if not pool:
            raise ValueError(
                "content_engine.NonRepeatingSampler: pool is empty; cannot draw.\n"
                "  What:     NonRepeatingSampler was constructed with an empty pool.\n"
                "  Where:    caller of generators.content_engine.NonRepeatingSampler.\n"
                "  Expected: a non-empty list.\n"
                "  Recover:  widen the source pool in config/data_pools.yml before sampling from it."
            )
        self._rng = rng
        self._pool = list(pool)
        self._order: list = []
        self._i = 0

    def draw(self):
        """Return the next item; reshuffles a fresh permutation on exhaustion."""
        if self._i >= len(self._order):
            self._order = list(self._pool)
            self._rng.shuffle(self._order)
            self._i = 0
        item = self._order[self._i]
        self._i += 1
        return item


def build_engine(path: Path = _DATA_POOLS_PATH) -> "ContentEngine":
    """Load pools from `path` and construct a ContentEngine."""
    return ContentEngine(load_pools(path))
