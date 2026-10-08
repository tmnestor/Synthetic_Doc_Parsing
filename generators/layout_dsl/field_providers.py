"""Field providers -- the DSL's other sanctioned escape hatch.

Some values a receipt or invoice draws exist nowhere in ground truth: a
receipt number, POS time, register and staff name, and the sixteen EFTPOS
terminal-slip values -- today derived by SHA-256 inside `generators/receipt.py`
and `generators/payment_block.py`. A field provider is a registered Python
function returning a flat `dict[str, str]` that is merged into `entry["fields"]`
before a layout's body renders, so `{FIELD}` interpolation (`binding.py`) and
`when:` suppression (`engine.py`) reach these derived values with no change to
either.

Mirrors `providers.py`'s row-provider registry shape deliberately -- a reader
who knows one recognises the other. The one addition is `emits`, mandatory:
without it, `validate` could not resolve a `{FIELD}` naming a derived value,
and every placeholder check would degrade from a startup failure to a
render-time one.

The registry itself (`field_provider`, lookup, declared emits and params) lives in the leaf module
`provider_registry.py` and is re-exported here; this module holds the ordinary providers and
`apply_field_providers`.
"""

import hashlib
from collections.abc import Iterable
from decimal import Decimal

from generators.common import fmt_amount
from generators.distractors.config import load_distractor_config
from generators.distractors.validator import check_distractor_values
from generators.layout_dsl.provider_registry import (
    AnyProvider,
    DistractorProvider,
    FieldProvider,
    FieldProviderError,
    distractor_doc_type,
    distractor_provider_names,
    field_provider,
    field_provider_emits,
    field_provider_names,
    field_provider_param_keys,
    get_distractor_provider,
    get_field_provider,
    get_label_provider,
    get_ordinary_provider,
    is_distractor_provider,
    is_label_provider,
    registration_site,
)
from generators.labels.keys import LabelChoice
from generators.payment_block import derive_payment, load_link_index, load_pos_pools

# Re-exported: the registry moved to provider_registry.py; callers keep importing these from here.
__all__ = [
    "AnyProvider",
    "DistractorProvider",
    "FieldProvider",
    "FieldProviderError",
    "apply_field_providers",
    "collect_emit_collisions",
    "computed_totals",
    "distractor_doc_type",
    "distractor_provider_names",
    "field_provider",
    "field_provider_emits",
    "field_provider_names",
    "field_provider_param_keys",
    "get_distractor_provider",
    "get_field_provider",
    "get_label_provider",
    "get_ordinary_provider",
    "is_distractor_provider",
    "is_label_provider",
    "receipt_payment",
    "receipt_pos",
    "refuse_labels_without_distractors",
    "registration_site",
]


def collect_emit_collisions(
    emitted_by: dict[str, str], name: str, keys: Iterable[str]
) -> tuple[str, str, str] | None:
    """Check `keys` against every key an earlier provider on this layout already claimed.

    Shared by two callers so their diagnostics cannot drift apart the way an
    earlier round of this task found them doing when the same
    build-the-map/set-intersect/format-the-detail logic was hand-copied into
    both places:

    - `generators/layout_dsl/schema.py`'s `_validate_field_providers`, the
      primary, validate-time check -- called with each provider's *declared*
      `emits`, so a whole layout is checked statically, before any provider
      ever runs.
    - `apply_field_providers` below, a defensive, merge-time check for a
      caller that builds a layout dict by hand and never calls
      `validate_layout` first -- called with a provider's *actual* returned
      keys, always a subset of its declared emits by the point this runs
      (the undeclared-emit check above already enforced that).

    Args:
        emitted_by: Accumulated key -> provider-name map for providers
            already checked on this layout. Not mutated -- the caller
            updates it once it has decided whether to raise, so a call about
            to raise leaves the map exactly as it was.
        name: The provider currently being checked.
        keys: The keys to check `name` against -- declared `emits` or actual
            output, per caller (see above).

    Returns:
        `None` if `keys` is disjoint from `emitted_by` -- no collision.
        Otherwise a `(what, expected, recover)` string triple: the three
        diagnostic elements common to both callers' four-element errors.
        Deliberately excludes WHERE: `_validate_field_providers` knows the
        offending layout's id and a dotted YAML key path;
        `apply_field_providers` is given neither (its own signature carries
        only `layout` and `entry`, no id or path), so each caller supplies
        its own WHERE around this shared text rather than being forced to
        fabricate one it does not have.
    """
    collisions = sorted(set(keys) & set(emitted_by))
    if not collisions:
        return None

    detail = ", ".join(f"'{key}' (already emitted by '{emitted_by[key]}')" for key in collisions)
    other_names = sorted({emitted_by[key] for key in collisions})
    what = (
        f"field provider '{name}' emits key(s) that collide with another provider on this layout: {detail}."
    )
    expected = (
        "every field_providers: entry on one layout to emit keys disjoint from every other "
        "provider on that same layout."
    )
    recover = (
        f"rename the colliding key(s) in one provider's emits=, or remove one of '{name}' / "
        f"{other_names} from this layout's field_providers:."
    )
    return what, expected, recover


def refuse_labels_without_distractors(
    labels: LabelChoice | None, *, distractors: bool, case_id: object
) -> None:
    """Refuse a label choice on a render that draws no distractors.

    Label variation is layered on the distractor render (the `labelled_varied` render is the distractor
    render with its labels varied), so a plain render never takes a choice.

    Args:
        labels: The render's label choice, or None.
        distractors: Whether the render draws distractors.
        case_id: The entry's case id, for the diagnostic.

    Raises:
        ValueError: `labels` is not None and `distractors` is false.
    """
    if labels is None or distractors:
        return
    raise ValueError(
        "A label choice was given to a render without distractors.\n"
        f"  What:     {case_id}: the renderer was called with labels={labels!r} and distractors=False; "
        "label variation is drawn only on a distractor render.\n"
        "  Where:    the renderer call (render_receipt / render_invoice / render_bank_statement, "
        "render_body, apply_field_providers): its labels= and distractors= arguments\n"
        "  Expected: labels=None on a plain render, or distractors=True with a LabelChoice, e.g. "
        "render_receipt(entry, layout, distractors=True, labels=choice).\n"
        "  Recover:  pass distractors=True, or drop labels= for a plain render."
    )


def apply_field_providers(
    layout: dict, entry: dict, *, distractors: bool, labels: LabelChoice | None = None
) -> dict:
    """Merge every field provider a layout declares into a copy of `entry`.

    Args:
        layout: The resolved layout dict, carrying `field_providers` -- a
            required key (see `validate_layout` in `generators/layout_dsl/
            schema.py`), so a layout deriving nothing declares
            `field_providers: []` explicitly rather than omitting the key.
        entry: The ground-truth entry.
        distractors: Whether this render draws distractors. When false, every
            provider registered with a `distractor=` document type is skipped, so no `DX_`
            key reaches the entry and the value validator never runs. When
            true, each distractor provider is called with the layout's
            `distractor_items` as the keyword `items` (ordinary providers keep
            the two-argument call), and its output is checked with
            `check_distractor_values` against the entry's raw ground truth
            before it is merged.
        labels: The render's label choice, handed to every label provider (`label_styles`) as the
            keyword `choice`; None for a plain or distractor render, whose label providers then emit
            each site's own wording. An API default, not configuration: every existing caller passes
            nothing and draws exactly as before.

    Returns:
        A new entry dict -- never a mutation of `entry` -- whose `fields` is
        `entry["fields"]` merged with every provider's derived output.
        `pipeline.generate` reuses entries across the clean and degraded
        passes, so mutating the caller's entry would leak derived values
        between the two.

    Raises:
        FieldProviderError: If a `field_providers:` entry names an unknown
            provider, a provider returns a key it did not declare in its own
            `emits`, two providers on this layout emit the same key, or a
            distractor provider runs on a layout with no `distractor_items`.
        DistractorValueError: A distractor value collides with a scored value,
            or a repeat differs from its field.
        ValueError: `labels` is given on a render without distractors, or names a field the layout has
            no label site for.
    """
    refuse_labels_without_distractors(labels, distractors=distractors, case_id=entry.get("case_id"))
    derived: dict[str, str] = {}
    # Tracks which provider already emitted each key, so a second provider
    # colliding on it is caught here even when validate_layout's static
    # (emits-declaration-only) check in schema.py was skipped or bypassed --
    # e.g. a caller that builds a layout dict by hand and calls this function
    # directly, as several tests in this module do. A silent dict.update()
    # overwrite here would make one provider's value vanish from the page
    # with no error, which is worse than either provider failing loudly.
    emitted_by: dict[str, str] = {}
    for spec in layout["field_providers"]:
        name = spec["name"]
        get_field_provider(name)  # an unknown name fails here, before any typed lookup
        if is_label_provider(name):
            result = get_label_provider(name)(entry, spec.get("params", {}), choice=labels)
        elif not is_distractor_provider(name):
            result = get_ordinary_provider(name)(entry, spec.get("params", {}))
        elif not distractors:
            continue
        else:
            # The layout's own list is the one copy of a distractor provider's eligible items.
            if "distractor_items" not in layout:
                msg = (
                    "Distractor provider has no items to draw from.\n"
                    f"  What:     the layout runs distractor provider '{name}' but carries no "
                    "'distractor_items' key.\n"
                    "  Where:    this layout's 'distractor_items:' (the layout dict passed to "
                    "apply_field_providers).\n"
                    "  Expected: distractor_items: [<catalogue item ids from config/distractors.yml>], "
                    "e.g. [rc_store_number, rc_acn, rc_phone].\n"
                    f"  Recover:  add distractor_items: to the layout, or remove '{name}' from its "
                    "field_providers:."
                )
                raise FieldProviderError(msg)
            result = get_distractor_provider(name)(
                entry, spec.get("params", {}), items=layout["distractor_items"]
            )

        declared = set(field_provider_emits(name))
        undeclared = sorted(set(result) - declared)
        if undeclared:
            msg = (
                "Field provider emitted an undeclared key.\n"
                f"  What:     field provider '{name}' returned key(s) {undeclared} not listed "
                "in its own emits=.\n"
                f"  Where:    {registration_site(name)}, @field_provider('{name}', emits=...)\n"
                f"  Expected: emits including {undeclared}.\n"
                f"  Recover:  add {undeclared} to '{name}''s emits= tuple, or stop returning "
                "them from the provider."
            )
            raise FieldProviderError(msg)

        collision = collect_emit_collisions(emitted_by, name, result)
        if collision is not None:
            what, expected, recover = collision
            msg = (
                "Two field providers on one layout emit the same key.\n"
                f"  What:     {what}\n"
                "  Where:    this layout's 'field_providers:' list.\n"
                f"  Expected: {expected}\n"
                f"  Recover:  {recover}"
            )
            raise FieldProviderError(msg)

        if is_distractor_provider(name):
            check_distractor_values(
                result,
                entry["fields"],
                distractor_doc_type(name),
                load_distractor_config(),
                case_id=str(entry.get("case_id")),
            )

        derived.update(result)
        emitted_by.update(dict.fromkeys(result, name))

    if labels and not any(is_label_provider(spec["name"]) for spec in layout["field_providers"]):
        raise ValueError(
            "A label choice was given to a layout with no label sites.\n"
            f"  What:     {entry.get('case_id')} on layout '{entry.get('layout')}': the choice picks "
            f"{sorted(labels)}, but the layout declares no label_styles provider, so nothing would draw "
            "the picks.\n"
            "  Where:    the layout's field_providers (config/layouts/<type>.yml -> "
            "<layout>.field_providers); "
            "the LabelChoice handed to the renderer\n"
            "  Expected: an empty LabelChoice({}) for a layout without label sites (every bank statement "
            "layout), e.g. render_bank_statement(entry, layout, distractors=True, "
            "labels=LabelChoice({})).\n"
            "  Recover:  pass an empty choice, or add a label_styles provider and its sites to the layout."
        )
    return {**entry, "fields": {**entry["fields"], **derived}}


@field_provider(
    "receipt_pos",
    params=frozenset(),
    emits=("POS_TIME", "POS_REGISTER", "POS_STAFF", "RECEIPT_NUMBER"),
)
def receipt_pos(entry: dict, params: dict) -> dict[str, str]:
    """Derive a receipt's POS time, register, staff name, and receipt number.

    Moved the arithmetic here verbatim from the receipt renderer's since-deleted
    `_derive_receipt_details` / `_derive_receipt_number` -- same two digests,
    same hex slices -- reading its pools from
    `generators.payment_block.load_pos_pools()` instead of the module-level
    staff-name list and inline hour/register ranges those functions used.
    `payment_block.derive_payment` consumes hex chars 10-40 of the same
    `pos:` digest this reads 0-8 of; the two must never collide.

    Args:
        entry: The ground-truth entry. Reads `entry["case_id"]` and
            `entry["fields"]["INVOICE_DATE"]`.
        params: Unused. This provider accepts no params: it always loads the
            `pos_terminal` pool, so a `pools_key` naming it would read as a
            switch and be none -- and a layout key no code path reads is worse
            than no key at all, since it tells an operator the document is
            configured a way it is not.

    Returns:
        `{"POS_TIME": ..., "POS_REGISTER": ..., "POS_STAFF": ..., "RECEIPT_NUMBER": ...}`.
    """
    pools = load_pos_pools()
    case_id = entry.get("case_id", "")
    invoice_date = entry["fields"].get("INVOICE_DATE", "")

    pos_digest = hashlib.sha256(f"{case_id}:pos:{invoice_date}".encode()).hexdigest()
    hour = pools["hour_min"] + int(pos_digest[0:2], 16) % pools["hour_span"]
    minute = int(pos_digest[2:4], 16) % 60
    register = pools["register_min"] + int(pos_digest[4:6], 16) % pools["register_span"]
    staff_names = pools["staff_names"]
    staff = staff_names[int(pos_digest[6:8], 16) % len(staff_names)]

    number_digest = hashlib.sha256(f"{case_id}:{invoice_date}".encode()).hexdigest()
    digest_length = pools["receipt_number_digest_length"]
    receipt_number = f"{pools['receipt_number_prefix']}{number_digest[:digest_length].upper()}"

    return {
        "POS_TIME": f"{hour:02d}:{minute:02d}",
        "POS_REGISTER": f"{register:02d}",
        "POS_STAFF": staff,
        "RECEIPT_NUMBER": receipt_number,
    }


def _or_not_found(value: str) -> str:
    """Convert an empty PaymentDetails string into the `NOT_FOUND` sentinel.

    `is_present` (`generators/layout_dsl/binding.py`) already treats
    `NOT_FOUND` and empty string as absent -- that's the mechanism a future
    receipt body uses to pick its card/wallet/cash variant via `when:` -- but
    `derive_payment` itself returns `""` for a field that does not apply to a
    given `PaymentDetails.kind` (e.g. `aid` for cash), never the sentinel. This
    normalises every such gap to the one spelling `when:` checks for.
    """
    return value if value else "NOT_FOUND"


def _amount_or_not_found(value: Decimal | None) -> str:
    """Format a cash Decimal as the slip prints it, or `NOT_FOUND`.

    Already carries `fmt_amount`'s `$` and thousands separators, so the cash
    `pair` blocks in config/layouts/receipts.yml deliberately declare no
    `currency:` of their own -- formatting an already-formatted string twice
    would raise `CurrencyError`.

    `tendered`/`change` are `None` unless `PaymentDetails.kind == "cash"`.
    """
    return fmt_amount(value) if value is not None else "NOT_FOUND"


@field_provider(
    "receipt_payment",
    params=frozenset(),
    emits=(
        "PAYMENT_KIND",
        "PAYMENT_METHOD",
        "PAYMENT_SCHEME_DISPLAY",
        "PAYMENT_ACCOUNT_TYPE",
        "PAYMENT_ACQUIRER",
        "PAYMENT_AID",
        "PAYMENT_MASKED_PAN",
        "PAYMENT_ENTRY_MODE",
        "PAYMENT_PSN",
        "PAYMENT_ATC",
        "PAYMENT_TERMINAL_ID",
        "PAYMENT_TRANSACTION_REF",
        "PAYMENT_TIMESTAMP",
        "PAYMENT_WALLET_LABEL",
        "PAYMENT_TENDERED",
        "PAYMENT_CHANGE",
    ),
)
def receipt_payment(entry: dict, params: dict) -> dict[str, str]:
    """Derive a receipt's EFTPOS terminal-slip values.

    Wraps `generators.payment_block.derive_payment` exactly as the receipt
    renderer's since-deleted `payment` section did -- same POS time (reusing
    `receipt_pos`'s derivation, hex chars 0-8 of the
    `{case_id}:pos:{invoice_date}` digest, so this never duplicates that
    arithmetic) and the same linked-receipt lookup keyed
    `f"{case_id}_{layout_id}"` into `load_link_index()`, so a linked receipt's
    scheme still comes from its bank row rather than the weighted pool.

    Deliberately never emits a purchase total: the slip's `Purchase   AUD`
    line binds `{TOTAL_AMOUNT}` directly, so a second, provider-derived copy
    of a scored value could silently drift from it. `PaymentDetails` carries
    no purchase total either, for the same reason -- see its docstring in
    payment_block.py.

    The three slip variants -- card, wallet, cash -- are selected by the
    receipt body's `when:` on these emitted keys, not by a branch here: every value
    `PaymentDetails` leaves as `""` or `None` for the current `kind` becomes
    the literal `"NOT_FOUND"`, which `is_present` already treats as absent.

    Args:
        entry: The ground-truth entry. Reads `entry["case_id"]`,
            `entry["layout"]`, `entry["fields"]["INVOICE_DATE"]`, and
            `entry["fields"]["TOTAL_AMOUNT"]`.
        params: Unused, for the same reason as `receipt_pos` above:
            `derive_payment` always loads the `payment_terminal` pool, so
            nothing here could branch on a `pools_key`.

    Returns:
        The sixteen `PAYMENT_*` fields listed in this provider's `emits`.
    """
    case_id = entry.get("case_id", "")
    layout_id = entry.get("layout", "")
    fields = entry["fields"]
    invoice_date = fields.get("INVOICE_DATE", "")
    time_str = receipt_pos(entry, {})["POS_TIME"]

    details = derive_payment(
        case_id,
        invoice_date,
        fields.get("TOTAL_AMOUNT", "0"),
        time_str,
        bank_description=load_link_index().get(f"{case_id}_{layout_id}"),
    )

    return {
        "PAYMENT_KIND": details.kind,
        "PAYMENT_METHOD": details.method,
        "PAYMENT_SCHEME_DISPLAY": _or_not_found(details.scheme_display),
        "PAYMENT_ACCOUNT_TYPE": _or_not_found(details.account_type),
        "PAYMENT_ACQUIRER": _or_not_found(details.acquirer),
        "PAYMENT_AID": _or_not_found(details.aid),
        "PAYMENT_MASKED_PAN": _or_not_found(details.masked_pan),
        "PAYMENT_ENTRY_MODE": _or_not_found(details.entry_mode),
        "PAYMENT_PSN": _or_not_found(details.psn),
        "PAYMENT_ATC": _or_not_found(details.atc),
        "PAYMENT_TERMINAL_ID": _or_not_found(details.terminal_id),
        "PAYMENT_TRANSACTION_REF": _or_not_found(details.transaction_ref),
        "PAYMENT_TIMESTAMP": _or_not_found(details.timestamp),
        "PAYMENT_WALLET_LABEL": _or_not_found(details.wallet_label),
        "PAYMENT_TENDERED": _amount_or_not_found(details.tendered),
        "PAYMENT_CHANGE": _amount_or_not_found(details.change),
    }


@field_provider("computed_totals", params=frozenset(), emits=("SUBTOTAL_AMOUNT",))
def computed_totals(entry: dict, params: dict) -> dict[str, str]:
    """Derive `SUBTOTAL_AMOUNT` as `TOTAL_AMOUNT` minus `GST_AMOUNT`.

    Matches the receipt renderer's since-deleted totals section, which computed
    `str(Decimal(total) - Decimal(gst))` and printed it with `,.2f` (the body's
    SUBTOTAL pair now declares `currency: plain` for that half).
    Emits nothing -- not even a zero -- when either input is absent
    or `NOT_FOUND`, so a `{SUBTOTAL_AMOUNT}` placeholder is suppressed by
    `when:` rather than rendering a fabricated value; `emits` is an upper
    bound on what a provider may return, not a promise every call returns it
    (see `apply_field_providers`'s undeclared-key check above, which only
    rejects extra keys, never absent ones).

    Args:
        entry: The ground-truth entry. Reads `entry["fields"]["TOTAL_AMOUNT"]`
            and `entry["fields"]["GST_AMOUNT"]`.
        params: Unused; this provider derives purely from `entry["fields"]`.

    Returns:
        `{"SUBTOTAL_AMOUNT": ...}`, or `{}` when either input is missing.
    """
    fields = entry["fields"]
    total = fields.get("TOTAL_AMOUNT")
    gst = fields.get("GST_AMOUNT")
    if not total or not gst or total == "NOT_FOUND" or gst == "NOT_FOUND":
        return {}
    return {"SUBTOTAL_AMOUNT": str(Decimal(total) - Decimal(gst))}
