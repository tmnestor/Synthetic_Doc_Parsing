"""The field-provider registry: registration, lookup and the declared emits and params.

A leaf module: from `generators` it imports only the leaves `generators.distractors.keys` and
`generators.labels.keys`, so every module that registers a provider (`generators/layout_dsl/
field_providers.py`, `generators/distractors/providers.py`, `generators/labels/provider.py`) and every
module that looks one up (`field_providers.apply_field_providers`, `schema.py`) can import it without an
import cycle. The providers themselves register at import; `generators/layout_dsl/__init__.py` imports
every registering module, so importing any part of the DSL registers them all.

Three kinds register here: an ordinary provider, a distractor provider (`distractor=<document type>`,
handed the layout's items, run only on a distractor render) and a label provider (`labels=True`, handed
the render's label choice, run on every render).
"""

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, cast, overload

import yaml

from generators.distractors.keys import DX_PREFIX
from generators.labels.keys import LABEL_PREFIX, LabelChoice


class FieldProvider(Protocol):
    """An ordinary provider, called as `provider(entry, params)`."""

    def __call__(self, entry: dict, params: dict, /) -> dict[str, str]:
        """Derive values from a ground-truth entry and the layout's `params:`."""
        ...


class DistractorProvider(Protocol):
    """A distractor provider, also handed the layout's `distractor_items` as the keyword `items`."""

    def __call__(self, entry: dict, params: dict, /, *, items: list[str]) -> dict[str, str]:
        """Draw distractor values for an entry from the layout's eligible `items`."""
        ...


class LabelProvider(Protocol):
    """A label provider, also handed the render's label choice as the keyword `choice`.

    It emits only `LABEL_` keys, runs on every render, and with `choice=None` emits the wording each site
    prints today, so plain and distractor renders are unchanged by it.
    """

    def __call__(self, entry: dict, params: dict, /, *, choice: LabelChoice | None) -> dict[str, str]:
        """Map the render's label choice onto one `LABEL_` key per site."""
        ...


# What the registry holds. Which kind a name maps to is recorded in `_DISTRACTOR` and `_LABELS` and
# checked against the function's signature at registration, so the typed lookups below can narrow safely.
AnyProvider = FieldProvider | DistractorProvider | LabelProvider

_REGISTRY: dict[str, AnyProvider] = {}
# Mirrors providers.py's _PARAM_KEYS: the top-level `params:` keys a provider
# reads, declared at registration time so schema.py can reject a typo'd
# params key (e.g. `terminl_id`) at validate time.
_PARAM_KEYS: dict[str, frozenset[str]] = {}
# The one addition over providers.py's registry: every `fields` key a
# provider may return. schema.py unions this into `known_fields` (per
# provider actually referenced by a layout) so a `{FIELD}` naming a derived
# value resolves at validate time, and `field_providers.apply_field_providers` uses it
# to reject a provider that returns a key it never declared.
_EMITS: dict[str, tuple[str, ...]] = {}
# For a provider that emits distractor (DX_) values only, the extraction-schema
# document type it draws for (its output is screened against that type's scored
# columns); None for an ordinary provider. A distractor provider runs only when a
# render asks for distractors.
_DISTRACTOR: dict[str, str | None] = {}
# True for a label provider (`labels=True`): it emits `LABEL_` keys only and is called with the keyword
# `choice`. No other provider may emit a `LABEL_` key.
_LABELS: dict[str, bool] = {}

_FIELD_DEFINITIONS_PATH = Path("config/field_definitions.yml")
_EXTRACTION_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "config" / "extraction_schema.yml"

# Loaded once, on first registration (see _scored_columns()).
_SCORED_COLUMNS: set[str] | None = None


def _site(func: object) -> str:
    """Where a provider function is defined: `<absolute file>:<line>`, or its module if it has no code."""
    code = getattr(func, "__code__", None)
    if code is not None:
        return f"{Path(code.co_filename).resolve()}:{code.co_firstlineno}"
    return str(getattr(func, "__module__", "<unknown module>"))


class FieldProviderError(RuntimeError):
    """Raised when a field provider is unknown, misregistered, or misbehaves."""


def _scored_columns() -> set[str]:
    """Return every column name `config/field_definitions.yml` scores.

    Loaded once and cached: registration happens a handful of times, all at
    import time, so there is no render-time cost to worry about; this just
    avoids re-reading and re-parsing the YAML on every `@field_provider` use.

    Returns:
        Every name in field_definitions.yml's `all_columns` list: the same
        schema `derive_outputs.py` uses as the CSV header, i.e. every column
        this corpus scores as extraction ground truth.

    Raises:
        FieldProviderError: If the file is missing or has no `all_columns` list.
    """
    global _SCORED_COLUMNS  # noqa: PLW0603
    if _SCORED_COLUMNS is not None:
        return _SCORED_COLUMNS

    path = _FIELD_DEFINITIONS_PATH
    if not path.exists():
        msg = (
            "Cannot check a field provider's emits against the scored-column schema.\n"
            f"  What:     field definitions file not found.\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: config/field_definitions.yml with an 'all_columns:' list.\n"
            "  Recover:  restore config/field_definitions.yml, or fix the path in "
            "generators/layout_dsl/provider_registry.py's _FIELD_DEFINITIONS_PATH."
        )
        raise FieldProviderError(msg)

    data = yaml.safe_load(path.read_text())
    columns = data.get("all_columns") if isinstance(data, dict) else None
    if not isinstance(columns, list):
        msg = (
            "Cannot check a field provider's emits against the scored-column schema.\n"
            f"  What:     {path} has no 'all_columns:' list.\n"
            f"  Where:    {path.resolve()} -> all_columns\n"
            "  Expected: all_columns:\n              - DOCUMENT_TYPE\n              - ...\n"
            f"  Recover:  add an 'all_columns:' list to {path}."
        )
        raise FieldProviderError(msg)

    _SCORED_COLUMNS = set(columns)
    return _SCORED_COLUMNS


def _schema_doc_types() -> list[str]:
    """Return the document types `config/extraction_schema.yml` declares under `document_fields`.

    Read with PyYAML directly, not through `generators.exporters`, so this module stays a leaf.
    """
    data = yaml.safe_load(_EXTRACTION_SCHEMA_PATH.read_text()) if _EXTRACTION_SCHEMA_PATH.exists() else None
    types = data.get("document_fields") if isinstance(data, dict) else None
    return sorted(types) if isinstance(types, dict) else []


def _check_label_signature(name: str, func: object, labels: bool, site: str) -> None:
    """Refuse a function whose `choice` parameter does not match whether it registers as a label provider.

    A label provider is called with the keyword `choice` and no other provider ever is, so a mismatch
    would otherwise surface as a `TypeError` on the first render that reaches it.
    """
    choice = inspect.signature(cast(Callable[..., object], func)).parameters.get("choice")
    keyword_only = choice is not None and choice.kind is inspect.Parameter.KEYWORD_ONLY
    if labels and not keyword_only:
        declared = (
            "no 'choice' parameter"
            if choice is None
            else f"'choice' as a {choice.kind.description} parameter"
        )
        raise FieldProviderError(
            "Cannot register field provider.\n"
            f"  What:     label provider '{name}' declares {declared}, but apply_field_providers hands a "
            "label provider the render's label choice as the keyword 'choice'.\n"
            f"  Where:    {site}, the signature of the function under @field_provider('{name}', "
            "labels=True)\n"
            "  Expected: def provider(entry: dict, params: dict, *, choice: LabelChoice | None) "
            "-> dict[str, str]\n"
            f"  Recover:  add a keyword-only '*, choice: LabelChoice | None' parameter to '{name}', "
            "or omit "
            "labels=True."
        )
    if not labels and choice is not None:
        raise FieldProviderError(
            "Cannot register field provider.\n"
            f"  What:     provider '{name}' declares a 'choice' parameter, which only a label provider is "
            "handed.\n"
            f"  Where:    {site}, the signature of the function under @field_provider('{name}', ...)\n"
            "  Expected: def provider(entry: dict, params: dict) -> dict[str, str]\n"
            f"  Recover:  remove 'choice' from '{name}', or register it with labels=True if it emits label "
            "keys."
        )


def _check_signature(name: str, func: object, distractor: str | None, site: str) -> None:
    """Refuse a function whose `items` parameter does not match the kind it registers as.

    A distractor provider is called with the keyword `items` and an ordinary one never is, so a
    mismatch would otherwise surface as a `TypeError` on the first render that reaches it.
    """
    items = inspect.signature(cast(Callable[..., object], func)).parameters.get("items")
    keyword_only = items is not None and items.kind is inspect.Parameter.KEYWORD_ONLY
    if distractor is not None and not keyword_only:
        declared = (
            "no 'items' parameter" if items is None else f"'items' as a {items.kind.description} parameter"
        )
        raise FieldProviderError(
            "Cannot register field provider.\n"
            f"  What:     distractor provider '{name}' declares {declared}, but apply_field_providers "
            "hands a distractor provider the layout's distractor_items as the keyword 'items'.\n"
            f"  Where:    {site}, the signature of the function under @field_provider('{name}', "
            "distractor=...)\n"
            "  Expected: def provider(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]\n"
            f"  Recover:  add a keyword-only '*, items: list[str]' parameter to '{name}', or omit "
            "distractor= to register it as an ordinary provider."
        )
    if distractor is None and items is not None:
        raise FieldProviderError(
            "Cannot register field provider.\n"
            f"  What:     ordinary provider '{name}' declares an 'items' parameter, which only a "
            "distractor provider is handed.\n"
            f"  Where:    {site}, the signature of the function under @field_provider('{name}', ...)\n"
            "  Expected: def provider(entry: dict, params: dict) -> dict[str, str]\n"
            f"  Recover:  remove 'items' from '{name}', or register it with distractor=<document type> "
            "if it draws distractor values."
        )


@overload
def field_provider(
    name: str,
    *,
    params: frozenset[str] = ...,
    emits: tuple[str, ...],
    distractor: None = None,
    labels: Literal[False] = False,
) -> Callable[[FieldProvider], FieldProvider]: ...


@overload
def field_provider(
    name: str,
    *,
    params: frozenset[str] = ...,
    emits: tuple[str, ...],
    distractor: str,
    labels: Literal[False] = False,
) -> Callable[[DistractorProvider], DistractorProvider]: ...


@overload
def field_provider(
    name: str,
    *,
    params: frozenset[str] = ...,
    emits: tuple[str, ...],
    distractor: None = None,
    labels: Literal[True],
) -> Callable[[LabelProvider], LabelProvider]: ...


def field_provider(
    name: str,
    *,
    params: frozenset[str] = frozenset(),
    emits: tuple[str, ...],
    distractor: str | None = None,
    labels: bool = False,
) -> (
    Callable[[FieldProvider], FieldProvider]
    | Callable[[DistractorProvider], DistractorProvider]
    | Callable[[LabelProvider], LabelProvider]
):
    """Register a field provider under `name`.

    Args:
        name: The name a layout's `field_providers:` entry uses.
        params: The top-level `params:` keys this provider reads (see
            `row_provider`'s identical rationale in `providers.py`).
        emits: Every `fields` key this provider may return. Mandatory:
            without it, `validate` could not resolve a `{FIELD}` naming a
            derived value, and `apply_field_providers` could not catch a
            provider returning a key it never declared.
        distractor: For a distractor provider, the extraction-schema document
            type it draws for (`receipt`, `invoice`, `bank_statement`); None
            for an ordinary provider. A code-level registration attribute,
            not configuration. A distractor provider emits only `DX_` keys,
            runs only when a render asks for distractors, and has its output
            screened against that document type's scored columns; no other
            provider may emit a `DX_` key.
        labels: True for a label provider: it emits only `LABEL_` keys, runs on every render and is
            called with the render's label choice as the keyword `choice`. No other provider may emit
            a `LABEL_` key. A code-level registration attribute, not configuration.

    Returns:
        A decorator that registers and returns the function unchanged: typed for a `FieldProvider`
        when `distractor` is absent and for a `DistractorProvider` when it names a document type, so
        mypy checks each kind's call shape.

    Raises:
        FieldProviderError: If `name` is already registered, an emitted
            name collides with a scored column `config/field_definitions.yml` owns,
            the `DX_` prefix and the `distractor` document type disagree,
            `distractor` names no extraction-schema document type, a distractor
            provider has no keyword-only `items` parameter, or an ordinary
            provider declares one.
    """

    def decorate[F: AnyProvider](func: F) -> F:
        site = _site(func)
        if distractor is not None:
            known_types = _schema_doc_types()
            if not isinstance(distractor, str) or distractor not in known_types:
                msg = (
                    "Cannot register field provider.\n"
                    f"  What:     distractor provider '{name}' gives distractor={distractor!r}, not a "
                    "document type of the extraction schema.\n"
                    f"  Where:    {site}, @field_provider('{name}', distractor=...); document types "
                    f"from {_EXTRACTION_SCHEMA_PATH} -> document_fields\n"
                    f'  Expected: one of {known_types}, e.g. distractor="receipt".\n'
                    f"  Recover:  pass the singular document type '{name}' draws for, or omit "
                    "distractor= for an ordinary provider."
                )
                raise FieldProviderError(msg)
        if name in _REGISTRY:
            msg = (
                "Cannot register field provider.\n"
                f"  What:     field provider '{name}' is already registered.\n"
                f"  Where:    {site}, @field_provider('{name}', ...); first registered at "
                f"{_site(_REGISTRY[name])}\n"
                "  Expected: a distinct provider name.\n"
                "  Recover:  pick a distinct provider name."
            )
            raise FieldProviderError(msg)

        collisions = sorted(set(emits) & _scored_columns())
        if collisions:
            msg = (
                "Cannot register field provider.\n"
                f"  What:     field provider '{name}' declares emits {collisions} that collide "
                "with scored column name(s) config/field_definitions.yml owns.\n"
                f"  Where:    {site}, @field_provider('{name}', emits=...)\n"
                "  Expected: emits names distinct from every column in "
                "config/field_definitions.yml's all_columns list.\n"
                f"  Recover:  rename {collisions} in '{name}''s emits= to a name that is not "
                "a scored column, e.g. prefix it POS_ or TERMINAL_."
            )
            raise FieldProviderError(msg)

        dx_keys = [key for key in emits if key.startswith(DX_PREFIX)]
        if distractor and len(dx_keys) != len(emits):
            offending = sorted(set(emits) - set(dx_keys))
            msg = (
                "Cannot register field provider.\n"
                f"  What:     distractor provider '{name}' emits non-DX keys {offending}.\n"
                f"  Where:    {site}, @field_provider('{name}', distractor=..., emits=...)\n"
                "  Expected: every emitted key starts with DX_, so a plain render can drop them all.\n"
                f"  Recover:  rename {offending} with a DX_ prefix."
            )
            raise FieldProviderError(msg)
        if not distractor and dx_keys:
            msg = (
                "Cannot register field provider.\n"
                f"  What:     ordinary provider '{name}' emits DX_ keys {sorted(dx_keys)}.\n"
                f"  Where:    {site}, @field_provider('{name}', emits=...)\n"
                "  Expected: DX_ keys only from providers registered with distractor=<document type>.\n"
                f"  Recover:  register '{name}' with distractor=<the document type it draws for>, "
                "or rename the keys."
            )
            raise FieldProviderError(msg)

        label_keys = [key for key in emits if key.startswith(LABEL_PREFIX)]
        if labels and (distractor is not None or len(label_keys) != len(emits)):
            offending = sorted(set(emits) - set(label_keys))
            raise FieldProviderError(
                "Cannot register field provider.\n"
                f"  What:     label provider '{name}' "
                + (
                    f"is also registered as a {distractor} distractor provider"
                    if distractor is not None
                    else f"emits non-{LABEL_PREFIX} keys {offending}"
                )
                + ".\n"
                f"  Where:    {site}, @field_provider('{name}', labels=True, emits=...)\n"
                f"  Expected: every emitted key starts with {LABEL_PREFIX}, and no distractor=, e.g. "
                "emits=('LABEL_TOTAL_AMOUNT',).\n"
                f"  Recover:  rename {offending} with a {LABEL_PREFIX} prefix, or drop distractor=."
            )
        if not labels and label_keys:
            raise FieldProviderError(
                "Cannot register field provider.\n"
                f"  What:     provider '{name}' emits {LABEL_PREFIX} keys {sorted(label_keys)} without "
                "labels=True.\n"
                f"  Where:    {site}, @field_provider('{name}', emits=...)\n"
                f"  Expected: {LABEL_PREFIX} keys only from a provider registered with labels=True.\n"
                f"  Recover:  register '{name}' with labels=True, or rename the keys."
            )

        _check_signature(name, func, distractor, site)
        _check_label_signature(name, func, labels, site)

        _REGISTRY[name] = func
        _PARAM_KEYS[name] = params
        _EMITS[name] = emits
        _DISTRACTOR[name] = distractor
        _LABELS[name] = labels
        return func

    return decorate


def get_field_provider(name: str) -> AnyProvider:
    """Look up a registered field provider of either kind.

    Call it through `get_ordinary_provider` or `get_distractor_provider`, which narrow it to one call
    shape.

    Args:
        name: Provider name from a layout's `field_providers:` list.

    Returns:
        The registered provider.

    Raises:
        FieldProviderError: If no provider is registered under `name`.
    """
    if name not in _REGISTRY:
        msg = (
            "Unknown field provider.\n"
            f"  What:     no field provider named '{name}' is registered.\n"
            "  Where:    a layout's 'field_providers:' list.\n"
            f"  Expected: one of {sorted(_REGISTRY)}.\n"
            "  Recover:  set the entry's name: to a registered field provider, or register a "
            "new one with @field_provider (generators/layout_dsl/provider_registry.py) in the "
            "module that defines it."
        )
        raise FieldProviderError(msg)
    return _REGISTRY[name]


def _wrong_kind(name: str, kind: str, wanted: str, expected: str) -> FieldProviderError:
    return FieldProviderError(
        f"Field provider is not {wanted}.\n"
        f"  What:     field provider '{name}' is registered as {kind}, so it cannot be called as "
        f"{wanted}.\n"
        f"  Where:    {registration_site(name)}, @field_provider('{name}', ...)\n"
        f"  Expected: {expected}\n"
        "  Recover:  branch on is_distractor_provider(name) and look the provider up with "
        "get_distractor_provider or get_ordinary_provider to match."
    )


def get_ordinary_provider(name: str) -> FieldProvider:
    """Look up a registered ordinary provider, typed for its `(entry, params)` call.

    Args:
        name: Provider name from a layout's `field_providers:` list.

    Returns:
        The registered provider.

    Raises:
        FieldProviderError: `name` is unregistered, or registered as a distractor provider.
    """
    provider = get_field_provider(name)
    if is_distractor_provider(name) or is_label_provider(name):
        kind = (
            f"a {distractor_doc_type(name)} distractor provider"
            if is_distractor_provider(name)
            else "a label provider"
        )
        raise _wrong_kind(
            name,
            kind,
            "an ordinary provider",
            "a provider registered without distractor= or labels=, called as provider(entry, params).",
        )
    # Registration checked the signature against the kind (`_check_signature`).
    return cast(FieldProvider, provider)


def get_label_provider(name: str) -> LabelProvider:
    """Look up a registered label provider, typed for its `(entry, params, *, choice)` call.

    Args:
        name: Provider name from a layout's `field_providers:` list.

    Returns:
        The registered provider.

    Raises:
        FieldProviderError: `name` is unregistered, or not registered with labels=True.
    """
    provider = get_field_provider(name)
    if not is_label_provider(name):
        raise _wrong_kind(
            name,
            "a distractor provider" if is_distractor_provider(name) else "an ordinary provider",
            "a label provider",
            "a provider registered with labels=True, called as provider(entry, params, choice=...).",
        )
    # Registration checked the signature against the kind (`_check_label_signature`).
    return cast(LabelProvider, provider)


def is_label_provider(name: str) -> bool:
    """Return whether a registered provider emits label (`LABEL_`) keys and takes the render's choice."""
    return _LABELS.get(name, False)


def label_provider_names() -> list[str]:
    """Return the registered label providers, sorted."""
    return sorted(name for name, labels in _LABELS.items() if labels)


def get_distractor_provider(name: str) -> DistractorProvider:
    """Look up a registered distractor provider, typed for its `(entry, params, *, items)` call.

    Args:
        name: Provider name from a layout's `field_providers:` list.

    Returns:
        The registered provider.

    Raises:
        FieldProviderError: `name` is unregistered, or registered as an ordinary provider.
    """
    provider = get_field_provider(name)
    if not is_distractor_provider(name):
        raise _wrong_kind(
            name,
            "an ordinary provider",
            "a distractor provider",
            "a provider registered with distractor=<document type>, called as "
            "provider(entry, params, items=[...]).",
        )
    # Registration checked the signature against the kind (`_check_signature`).
    return cast(DistractorProvider, provider)


def registration_site(name: str) -> str:
    """Return where a registered field provider is defined, for diagnostics.

    Args:
        name: A registered field provider name.

    Returns:
        `<absolute file>:<line>` of the decorated function, e.g. generators/distractors/providers.py for
        a distractor provider.
    """
    return _site(_REGISTRY[name])


def field_provider_names() -> list[str]:
    """Return the names of all registered field providers, sorted."""
    return sorted(_REGISTRY)


def field_provider_emits(name: str) -> tuple[str, ...]:
    """Return the `fields` keys a registered field provider may emit.

    Args:
        name: A registered field provider name (validate the name itself with
            `field_provider_names()`/`get_field_provider()` first; this
            returns an empty tuple for an unknown name rather than raising,
            since schema.py already reports the unknown-provider case with
            its own diagnostic before ever reaching an emits check).

    Returns:
        The tuple passed to this provider's `@field_provider(..., emits=...)`.
    """
    return _EMITS.get(name, ())


def is_distractor_provider(name: str) -> bool:
    """Return whether a registered provider emits distractor values only."""
    return _DISTRACTOR.get(name) is not None


def distractor_provider_names(doc_type: str | None = None) -> list[str]:
    """Return the registered distractor providers, sorted.

    Args:
        doc_type: When given, only the providers drawing for this extraction-schema document type.

    Returns:
        Provider names.
    """
    return sorted(
        name for name, drawn in _DISTRACTOR.items() if drawn is not None and doc_type in (None, drawn)
    )


def distractor_doc_type(name: str) -> str:
    """Return the document type a distractor field provider draws for.

    Args:
        name: A registered field provider name.

    Returns:
        The extraction-schema document type given as its `distractor=`, e.g. `receipt`.

    Raises:
        FieldProviderError: `name` is unregistered or an ordinary provider, which draws for no
            document type.
    """
    doc_type = _DISTRACTOR.get(name)
    if doc_type is None:
        msg = (
            "Not a distractor field provider.\n"
            f"  What:     field provider '{name}' is unregistered or ordinary, so it draws distractors "
            "for no document type.\n"
            f"  Where:    the @field_provider('{name}', ...) registration; distractor providers: "
            f"{distractor_provider_names()}\n"
            "  Expected: a provider registered with distractor=<document type>, e.g. "
            "@field_provider('receipt_distractors', emits=(...), distractor=\"receipt\").\n"
            "  Recover:  ask only a distractor provider for its document type (check "
            "is_distractor_provider first), or register it with distractor=<document type>."
        )
        raise FieldProviderError(msg)
    return doc_type


def field_provider_param_keys(name: str) -> frozenset[str]:
    """Return the top-level `params:` keys a registered field provider accepts.

    Args:
        name: A registered field provider name (see `field_provider_emits`'s
            identical note on validating the name first).

    Returns:
        The frozenset passed to this provider's `@field_provider(..., params=...)`.
    """
    return _PARAM_KEYS.get(name, frozenset())
