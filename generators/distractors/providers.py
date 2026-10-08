"""The receipt, invoice and bank-statement distractor field providers.

Each provider is handed the layout's `distractor_items` by `apply_field_providers` (the `items`
keyword; the layout's list is the only copy, and the providers take no params). It selects items within
the catalogue's `selection` count with a `random.Random` seeded from `sha256("{case_id}:distractor:
{doc_type}")` (a domain independent of every other derived value), then draws each selected item's value
with its rule from `generators/distractors/values.py`, on that item's own `random.Random` seeded from
`sha256("{case_id}:distractor:{doc_type}:{item_id}")`, and screens it with `collision` from
`generators/distractors/validator.py`. An item's value therefore depends on its case, type and id
alone (and on the values of the items it requires), never on which other items were selected: the
all-items draw `pipeline validate` makes produces exactly the values any real selection draws, and a
change to a layout's list leaves every other item's value unchanged.

How a collision is answered is declared per rule beside `RULES` in values.py. A `REDRAWABLE` value is
redrawn on the item's own generator up to `max_redraws` times, then the render fails. A `DESELECTABLE`
value (a fixed value, a count, a sum) ignores the generator: when one collides, the item and every item
requiring it are deselected for that case only, the one permitted deselection, recorded in a debug log
line. A `FAIL_AT_ONCE` value fails on the first collision: a `repeat` copies its field verbatim, so a
mismatch is a code fault, and a website, handle or survey address is fixed by the supplier slug. Any
slug rule (`SLUG_RULES`, email included) whose collision comes from the slug itself fails at once
naming SUPPLIER_NAME, since no redraw can change the slug.

Before selecting, a document's states (an invoice is paid or unpaid) are drawn, each on its own seed
`sha256("{case_id}:distractor:{doc_type}:state:{name}")`, and the items grouped under every other value
of a state, with every item requiring one of them, are removed from the eligible list (`draw_states`,
`excluded_items`). An item may emit several keys when its rule draws several values (a BSB and its
account number), so values that belong together always print together.

Every tunable (catalogue, counts, states, retry budget, validator settings) lives in
`config/distractors.yml`.
"""

import hashlib
import logging
import random
from functools import lru_cache

from generators.content_engine import ContentEngine, build_engine
from generators.distractors.config import DistractorConfig, Item, load_distractor_config, required_items
from generators.distractors.paths import DISTRACTORS_PATH, ground_truth_file, layout_file
from generators.distractors.validator import check_value, collision, scored_values
from generators.distractors.values import (
    DESELECTABLE,
    REDRAWABLE,
    draw_values,
    SLUG_RULES,
    RuleContext,
    ValueRuleError,
    parse_bank_rows,
    supplier_slug,
)
from generators.layout_dsl.provider_registry import distractor_provider_names, field_provider

logger = logging.getLogger(__name__)


def _provider_name(doc_type: str) -> str:
    """The distractor provider registered for `doc_type`, named in diagnostics."""
    names = distractor_provider_names(doc_type)
    return names[0] if names else f"<{doc_type} distractor provider>"


class DistractorProviderError(ValueError):
    """Raised when a distractor provider cannot draw a document's distractor values."""


def _error(what: str, where: str, expected: str, recover: str) -> DistractorProviderError:
    return DistractorProviderError(
        "Cannot draw distractor values.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def case_seed(case_id: str, doc_type: str) -> int:
    """Return the seed for one document's distractor selection.

    Only which items are drawn comes from this seed; each item's value comes from `item_seed`.

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `invoice`.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:distractor:{doc_type}")` as a big-endian integer.
    """
    return _seed(f"{case_id}:distractor:{doc_type}")


def item_seed(case_id: str, doc_type: str, item_id: str) -> int:
    """Return the seed for one item's value on one document, its redraws included.

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `invoice`.
        item_id: The catalogue item, e.g. `in_due_date`.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:distractor:{doc_type}:{item_id}")` as a big-endian
        integer.
    """
    return _seed(f"{case_id}:distractor:{doc_type}:{item_id}")


def state_seed(case_id: str, doc_type: str, state: str) -> int:
    """Return the seed for one document's value of one state (its payment state, say).

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `invoice`.
        state: The state's name under `states.<doc_type>` in config/distractors.yml, e.g. `payment`.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:distractor:{doc_type}:state:{state}")` as a big-endian
        integer: a domain of its own, so the state is independent of the selection and of every value.
    """
    return _seed(f"{case_id}:distractor:{doc_type}:state:{state}")


def draw_states(config: DistractorConfig, case_id: str, doc_type: str) -> dict[str, str]:
    """Draw a document's value of each state its type declares.

    Args:
        config: The catalogue.
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type.

    Returns:
        State name -> the choice drawn on its own `state_seed`; {} for a type with no states.
    """
    return {
        name: random.Random(state_seed(case_id, doc_type, name)).choice(spec.choices)
        for name, spec in config.states[doc_type].items()
    }


def excluded_items(config: DistractorConfig, doc_type: str, states: dict[str, str]) -> set[str]:
    """Return the items a document in `states` may not select.

    The items of every group other than the drawn choice, and every item requiring one of them
    (transitively), since a selected item brings its requirements with it.

    Args:
        config: The catalogue.
        doc_type: The extraction-schema document type.
        states: State name -> drawn choice, as `draw_states` returns it.

    Returns:
        The excluded item ids; empty for a type with no states.
    """
    excluded: set[str] = set()
    for name, choice in states.items():
        for group, ids in config.states[doc_type][name].groups.items():
            if group != choice:
                excluded.update(ids)
    if not excluded:
        return excluded
    return excluded | {
        item_id
        for item_id, item in config.items.items()
        if doc_type in item.doc_types and required_items(config, item_id) & excluded
    }


def exclusive_seed(case_id: str, doc_type: str, members: tuple[str, ...]) -> int:
    """Return the seed for which member of one exclusive set a document keeps.

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type.
        members: The set's item ids, as `exclusive.<doc_type>` lists them.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:distractor:{doc_type}:exclusive:{'+'.join(members)}")`
        as a big-endian integer.
    """
    return _seed(f"{case_id}:distractor:{doc_type}:exclusive:{'+'.join(members)}")


def exclusive_excluded(
    config: DistractorConfig, case_id: str, doc_type: str, candidates: list[str]
) -> set[str]:
    """Return the items a document drops so it prints at most one member of each exclusive set.

    Of a set's members among `candidates` (the layout's items its states leave), one is kept, drawn on
    `exclusive_seed`; the others are dropped, with every item requiring one (transitively). A set with
    at most one member among the candidates drops nothing and draws nothing.

    Args:
        config: The catalogue.
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type.
        candidates: The item ids still eligible.

    Returns:
        The dropped item ids.
    """
    dropped: set[str] = set()
    for members in config.exclusive[doc_type]:
        present = [item_id for item_id in members if item_id in candidates]
        if len(present) < 2:
            continue
        keep = random.Random(exclusive_seed(case_id, doc_type, members)).choice(present)
        dropped.update(item_id for item_id in present if item_id != keep)
    if not dropped:
        return dropped
    return dropped | {
        item_id
        for item_id, item in config.items.items()
        if doc_type in item.doc_types and required_items(config, item_id) & dropped
    }


def effective_rule(item: Item) -> dict:
    """Return the rule mapping an item's value is drawn with.

    A `repeat` rule copies a scored column, which the catalogue declares on the item (`field:`) rather
    than inside its `rule:` mapping; the rule reads it as `rule["field"]`, so for a repeat item this
    returns `{**item.rule, "field": item.field}`. Every other item's rule is returned unchanged.

    Args:
        item: A catalogue item.

    Returns:
        The rule mapping to pass to the item's rule function. Never the item's own dict mutated.
    """
    if item.kind == "repeat":
        return {**item.rule, "field": item.field}
    return item.rule


def _check_items(config: DistractorConfig, doc_type: str, items: object, where: str) -> list[str]:
    """Check a list of item ids is a list of distinct catalogue items for `doc_type`."""
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise _error(
            f"the distractor items are {items!r}, not a list of item ids.",
            where,
            "a list of catalogue item ids, e.g. items: [rc_store_number, rc_acn, rc_phone].",
            f"list the layout's distractor_items, ids from {DISTRACTORS_PATH} -> items.",
        )
    duplicates = sorted({i for i in items if items.count(i) > 1})
    if duplicates:
        raise _error(
            f"the distractor items list {duplicates} more than once.",
            where,
            "each item id at most once.",
            f"remove the repeated {duplicates}.",
        )
    for item_id in items:
        item = config.items.get(item_id)
        if item is None:
            raise _error(
                f"unknown distractor item {item_id!r}.",
                where,
                f"ids declared in {DISTRACTORS_PATH} -> items.",
                f"correct or remove {item_id!r}, or declare it under items: in {DISTRACTORS_PATH}.",
            )
        if doc_type not in item.doc_types:
            raise _error(
                f"item {item_id!r} is for {sorted(item.doc_types)}, not {doc_type!r}.",
                f"{where}; {DISTRACTORS_PATH} -> items.{item_id}.doc_types",
                f"items whose doc_types include {doc_type!r}.",
                f"remove {item_id!r} from the {doc_type} layout, or add {doc_type} to its doc_types.",
            )
    return items


def draw_order(config: DistractorConfig, ids: list[str]) -> list[str]:
    """Order item ids so every requirement comes before its dependants.

    Args:
        config: The catalogue.
        ids: Item ids; requirements absent from `ids` are skipped.

    Returns:
        `ids` in catalogue order, each item's requirements moved before it.
    """
    wanted = set(ids)
    position = {item_id: n for n, item_id in enumerate(config.items)}
    ordered: list[str] = []

    def visit(item_id: str) -> None:
        if item_id in ordered or item_id not in wanted:
            return
        for required in sorted(config.items[item_id].requires, key=position.__getitem__):
            visit(required)
        ordered.append(item_id)

    for item_id in sorted(wanted, key=position.__getitem__):
        visit(item_id)
    return ordered


def select_items(
    config: DistractorConfig, doc_type: str, eligible: list[str], rng: random.Random, *, where: str
) -> list[str]:
    """Select the items drawn on one document.

    Draws `k` uniformly from `[min, min(max, len(eligible))]` (the catalogue's `selection.<type>.count`),
    samples `k` eligible items, and adds each sampled item's requirements, transitively. A sampled item
    with a requirement that is not eligible is dropped, so the result can hold fewer than `k` items or,
    with requirements added, more.

    Args:
        config: The catalogue.
        doc_type: The extraction-schema document type.
        eligible: The layout's `distractor_items`.
        rng: The document's seeded generator.
        where: The layout file and id the eligible items come from, named in a diagnostic.

    Returns:
        The selected item ids in draw order (see `draw_order`).

    Raises:
        DistractorProviderError: An id is unknown, for another document type, or repeated; or there are
            fewer eligible items than the configured minimum.
    """
    _check_items(config, doc_type, eligible, where)
    low, high = config.selection[doc_type]
    if len(eligible) < low:
        raise _error(
            f"{len(eligible)} eligible {doc_type} items, fewer than the minimum of {low} to select.",
            f"{DISTRACTORS_PATH} -> selection.{doc_type}.count; {where}",
            f"at least {low} eligible items, or a lower minimum, e.g. count: [{len(eligible)}, {high}].",
            f"list more items in the layout's distractor_items, or lower selection.{doc_type}.count.",
        )
    k = rng.randint(low, min(high, len(eligible)))
    allowed = set(eligible)
    chosen: set[str] = set()
    for item_id in rng.sample(eligible, k):
        needed = required_items(config, item_id)
        if needed <= allowed:
            chosen |= needed | {item_id}
    return draw_order(config, sorted(chosen))


@lru_cache(maxsize=1)
def _engine() -> ContentEngine:
    return build_engine()


def _wrap(err: ValueRuleError, item: Item, case_id: str, doc_type: str) -> DistractorProviderError:
    where = f"{DISTRACTORS_PATH} -> items.{item.id}.rule"
    if err.field is not None:
        where += f"; {ground_truth_file(doc_type)} -> {case_id}.fields.{err.field}"
    return _error(
        f"item {item.id!r} could not draw a value for {case_id}: {err.what}",
        f"{where} (rule detail: {err.where})",
        err.expected,
        err.recover,
    )


def _draw(
    item: Item, ctx: RuleContext, scored: dict[str, list[str]], config: DistractorConfig, doc_type: str
) -> tuple[str, ...] | None:
    """Draw one item's values, redrawing on a collision; None when a deselectable value collides.

    Returns:
        One value per key the item emits, in order; each is screened, and a collision of any redraws
        them all.

    Raises:
        DistractorValueError: A repeat differs from its field (never redrawn, never deselected).
        DistractorProviderError: A rule fails, every redraw collides, or a value that no redraw can
            change (a slug collision, or any `FAIL_AT_ONCE` rule) collides on the first draw.
    """
    rule = effective_rule(item)
    rule_type = rule["type"]
    value, hit = "", ""
    for _ in range(config.max_redraws + 1):
        try:
            values = draw_values(ctx, rule)
        except ValueRuleError as err:
            raise _wrap(err, item, ctx.case_id, doc_type) from err
        if len(values) != len(item.emits):
            raise _error(
                f"rule type {rule_type!r} drew {len(values)} value(s) for {item.id!r}, which emits "
                f"{list(item.emits)}.",
                f"{DISTRACTORS_PATH} -> items.{item.id}.emits; MULTI_VALUE_COUNTS in "
                "generators/distractors/values.py",
                "one drawn value per emitted key.",
                f"declare the rule's value count in MULTI_VALUE_COUNTS, or fix {item.id}.emits.",
            )
        found, value = None, values[0]
        for candidate in values:
            found = collision(item, candidate, ctx.fields, scored)
            if found is not None:
                value = candidate
                break
        if found is None:
            return values
        if item.kind == "repeat":
            check_value(item, item.emits[0], value, ctx.fields, scored, doc_type, case_id=ctx.case_id)
        hit = found
        if rule_type in SLUG_RULES:
            slug = supplier_slug(ctx)
            if collision(item, slug, ctx.fields, scored) is not None:
                raise _error(
                    f"item {item.id!r} on {ctx.case_id} drew {value!r}, whose supplier slug {slug!r} "
                    f"already contains the scored field {hit}; no redraw can change the slug.",
                    f"{ground_truth_file(doc_type)} -> {ctx.case_id}.fields.SUPPLIER_NAME; "
                    f"{DISTRACTORS_PATH} -> items.{item.id}.rule",
                    "a supplier name whose run-together slug contains no scored value (a one-word name "
                    "is its own slug, so it always does).",
                    f"rename SUPPLIER_NAME of {ctx.case_id} in the ground truth (a name of two or more "
                    f"words), or remove {item.id!r} from the layout's distractor_items.",
                )
        if rule_type not in REDRAWABLE and rule_type not in DESELECTABLE:
            raise _error(
                f"item {item.id!r} on {ctx.case_id} drew {value!r}, which collides with the scored field "
                f"{hit}, and rule type {rule_type!r} ignores the generator, so a redraw would repeat it.",
                f"{DISTRACTORS_PATH} -> items.{item.id}.rule; {ground_truth_file(doc_type)} -> "
                f"{ctx.case_id}.fields.{hit}",
                f"a {item.kind} value that contains no scored value.",
                f"change the value {item.id!r} depends on (its required items or {hit} in the ground "
                f"truth of {ctx.case_id}), or remove {item.id!r} from the layout's distractor_items.",
            )
        if rule_type in DESELECTABLE:
            logger.debug(
                "deselected %s for %s: value %r collides with the scored field %s, and rule type %r "
                "cannot be redrawn",
                item.id,
                ctx.case_id,
                value,
                hit,
                rule_type,
            )
            return None
    raise _error(
        f"item {item.id!r} on {ctx.case_id} drew {value!r}, which collides with the scored field {hit}, "
        f"on the first draw and on every one of {config.max_redraws} redraws.",
        f"{DISTRACTORS_PATH} -> items.{item.id}.rule; {ground_truth_file(doc_type)} -> "
        f"{ctx.case_id}.fields.{hit}",
        f"a {item.kind} value that neither equals its target nor contains any scored value "
        "(a repeat equals its field exactly).",
        f"widen items.{item.id}.rule or raise max_redraws in {DISTRACTORS_PATH}, or remove "
        f"{item.id!r} from the layout's distractor_items.",
    )


def _provide(doc_type: str, entry: dict, listed: object, *, select_all: bool) -> dict[str, str]:
    """Draw the distractor values for one document of `doc_type`.

    With `select_all` every listed item is drawn; otherwise `select_items` samples them.
    """
    config = load_distractor_config()
    name = _provider_name(doc_type)
    layout = entry.get("layout", "<layout>")
    where = f"{layout_file(doc_type)} -> {layout}.distractor_items"
    items = _check_items(config, doc_type, listed, where)
    case_id = entry.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise _error(
            f"the entry has no case_id (got {case_id!r}), so its distractor seed cannot be derived.",
            f"{ground_truth_file(doc_type)} -> <CASE>; the entry passed to the {name} provider",
            "an entry dict carrying case_id, e.g. {'case_id': 'CASE001', 'layout': ..., 'fields': {...}}.",
            "add case_id to the entry before applying field providers.",
        )

    selection_rng = random.Random(case_seed(case_id, doc_type))
    # The document's states are drawn whatever is selected: a state_text rule's wording reads them.
    states = draw_states(config, case_id, doc_type)
    if select_all:
        chosen = list(items)
    else:
        # The states' excluded items are removed before selection, so a paid invoice never samples an
        # unpaid item; then of each exclusive set one member is kept. The count is drawn over what is left.
        excluded = excluded_items(config, doc_type, states)
        excluded |= exclusive_excluded(
            config, case_id, doc_type, [item_id for item_id in items if item_id not in excluded]
        )
        eligible = [item_id for item_id in items if item_id not in excluded]
        if excluded & set(items):
            where = f"{where} (states {states}: {sorted(excluded & set(items))} excluded)"
        chosen = select_items(config, doc_type, eligible, selection_rng, where=where)
    order = draw_order(config, chosen)
    fields = entry["fields"]
    scored = scored_values(fields, doc_type, config.validator)
    engine = _engine()
    try:
        rows = parse_bank_rows(fields) if doc_type == "bank_statement" else None
    except ValueRuleError as err:
        raise _error(
            f"{case_id}: {err.what}",
            f"{ground_truth_file(doc_type)} -> {case_id}.fields.TRANSACTION_*",
            err.expected,
            err.recover,
        ) from err
    selected: dict[str, str] = {}

    dropped: set[str] = set()
    for item_id in order:
        item = config.items[item_id]
        lost = sorted(set(item.requires) & dropped)
        if lost:
            logger.debug("deselected %s for %s: it requires the deselected %s", item_id, case_id, lost)
            dropped.add(item_id)
            continue
        # Each item draws, and redraws, on its own generator, so its value is the same whichever
        # other items are selected alongside it.
        ctx = RuleContext(
            rng=random.Random(item_seed(case_id, doc_type, item_id)),
            fields=fields,
            engine=engine,
            pools=engine.pools,
            case_id=case_id,
            rows=rows,
            selected=selected,
            states=states,
            service_category=entry.get("service_category"),
        )
        values = _draw(item, ctx, scored, config, doc_type)
        if values is None:
            dropped.add(item_id)
            continue
        selected.update(zip(item.emits, values, strict=True))
    return dict(selected)


def draw_all_eligible(entry: dict, doc_type: str) -> dict[str, str]:
    """Draw every catalogue item eligible for a document type on one entry, none sampled away.

    What `pipeline validate` checks: each item's rule runs on each case with that item's own seed
    (`item_seed`), so every value drawn here is exactly the value any real selection containing the
    item draws, and a catalogue or ground-truth edit that lets any item reproduce a scored value fails
    at validate time.

    Args:
        entry: The ground-truth entry; reads `case_id`, `layout` and `fields`.
        doc_type: The extraction-schema document type.

    Returns:
        The `DX_` keys and values of every item not deselected.

    Raises:
        DistractorProviderError: A rule fails, or an item's every redraw collides.
        DistractorValueError: A repeat differs from its field.
    """
    items = [i.id for i in load_distractor_config().items.values() if doc_type in i.doc_types]
    return _provide(doc_type, entry, items, select_all=True)


def _emits(doc_type: str) -> tuple[str, ...]:
    """Every key the catalogue's items for `doc_type` emit, in catalogue order."""
    items = load_distractor_config().items.values()
    return tuple(dict.fromkeys(key for item in items if doc_type in item.doc_types for key in item.emits))


# The providers read no params: the eligible items are the layout's own `distractor_items`, handed in by
# `apply_field_providers` as the `items` keyword.
_PARAMS: frozenset[str] = frozenset()


@field_provider("receipt_distractors", params=_PARAMS, emits=_emits("receipt"), distractor="receipt")
def receipt_distractors(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    """Draw a receipt's distractor values.

    Args:
        entry: The ground-truth entry; reads `case_id`, `layout` and `fields`.
        params: Unused; these providers take no params.
        items: The layout's `distractor_items`, the items eligible for selection.

    Returns:
        The selected items' `DX_` keys and values.
    """
    return _provide("receipt", entry, items, select_all=False)


@field_provider("invoice_distractors", params=_PARAMS, emits=_emits("invoice"), distractor="invoice")
def invoice_distractors(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    """Draw an invoice's distractor values.

    Args:
        entry: The ground-truth entry; reads `case_id`, `layout` and `fields`.
        params: Unused; these providers take no params.
        items: The layout's `distractor_items`, the items eligible for selection.

    Returns:
        The selected items' `DX_` keys and values.
    """
    return _provide("invoice", entry, items, select_all=False)


@field_provider(
    "bank_distractors", params=_PARAMS, emits=_emits("bank_statement"), distractor="bank_statement"
)
def bank_distractors(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]:
    """Draw a bank statement's distractor values, annotated rows and handwritten total included.

    Args:
        entry: The ground-truth entry; reads `case_id`, `layout` and `fields`.
        params: Unused; these providers take no params.
        items: The layout's `distractor_items`, the items eligible for selection.

    Returns:
        The selected items' `DX_` keys and values.
    """
    return _provide("bank_statement", entry, items, select_all=False)
