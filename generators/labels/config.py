"""Fail-fast loader for `label_variation:` in `config/distractors.yml`.

Which scored fields vary their label per document type, each field's style weights, the variant wording
pools, the words a collision ignores and the refusal policy all live in that block. Every key is required
and nothing is defaulted here. Each failure is a `LabelConfigError` carrying What / Where / Expected /
Recover lines, with the absolute file path and the dotted key.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from generators.distractors.paths import DISTRACTORS_PATH, document_type_files
from generators.exporters.eval_projection import load_extraction_schema
from generators.labels.keys import LABEL_STYLES, PROVIDER_NAME

ROOT_KEY = "label_variation"
_KEYS = (
    "doc_types",
    "fields",
    "pools",
    "withheld_by_state",
    "collision",
    "refusal_policy",
    "totals_region_fields",
)
_FIELD_KEYS = ("weights",)
_COLLISION_KEYS = ("ignore_words",)
# What a refused label choice does: draw the field's next candidate, or stop the render with a diagnostic.
REFUSAL_POLICIES = ("redraw", "fail")
_EXAMPLES = {
    "doc_types": "doc_types: {receipt: [BUSINESS_ABN, GST_AMOUNT, TOTAL_AMOUNT], invoice: [BUSINESS_ABN, "
    "GST_AMOUNT, TOTAL_AMOUNT], bank_statement: []}",
    "fields": "fields: {TOTAL_AMOUNT: {weights: {labelled: 1, variant: 1, unlabelled: 1}}}",
    "pools": 'pools: {total_receipt: ["AMOUNT", "TOTAL DUE"]}',
    "withheld_by_state": 'withheld_by_state: {receipt: {}, invoice: {payment: {paid: ["Balance:"]}}, '
    "bank_statement: {}}",
    "collision": "collision: {ignore_words: {BUSINESS_ABN: [of, the, 'no', number], GST_AMOUNT: [of, the], "
    "TOTAL_AMOUNT: [of, the]}}",
    "refusal_policy": "refusal_policy: redraw",
    "totals_region_fields": "totals_region_fields: [GST_AMOUNT, TOTAL_AMOUNT]",
}
_EXAMPLE = ("label_variation:\n" + "".join(f"                {_EXAMPLES[key]}\n" for key in _KEYS)).rstrip()
_PUNCTUATION = re.compile(r"[^\w\s]")


class LabelConfigError(ValueError):
    """Raised when `label_variation:` is missing, malformed or incoherent."""


@dataclass(frozen=True)
class StyleWeights:
    """One field's relative weight for each label style; every weight is above zero (Decision 19).

    Attributes:
        labelled: Weight of the layout's own wording.
        variant: Weight of a wording from the site's pool.
        unlabelled: Weight of drawing no label.
    """

    labelled: float
    variant: float
    unlabelled: float

    def of(self, style: str) -> float:
        """Return the weight of one style, named as in `LABEL_STYLES`."""
        return float(getattr(self, style))


@dataclass(frozen=True)
class LabelConfig:
    """The validated `label_variation:` block.

    Attributes:
        doc_types: Extraction-schema document type -> the scored fields whose label varies, in YAML order.
        weights: Varied field -> its style weights.
        pools: Pool name -> its variant wordings, in YAML order.
        withheld: Document type -> state name -> choice -> the pool wordings a page in that state may
            not print.
        ignore_words: Varied field -> the normalised words its variant may share with a distractor line
            without colliding.
        refusal_policy: One of `REFUSAL_POLICIES`.
        totals_region_fields: The varied fields whose label sites must lie inside the totals region, the
            block the unambiguity rules read around the GST and the total.
        path: The file the block was read from, absolute, for diagnostics.
    """

    doc_types: dict[str, tuple[str, ...]]
    weights: dict[str, StyleWeights]
    pools: dict[str, tuple[str, ...]]
    withheld: dict[str, dict[str, dict[str, frozenset[str]]]]
    ignore_words: dict[str, frozenset[str]]
    refusal_policy: str
    totals_region_fields: tuple[str, ...]
    path: Path

    def withheld_for(self, doc_type: str, states: dict[str, str]) -> frozenset[str]:
        """Return the pool wordings a page of `doc_type` in `states` may not print.

        Args:
            doc_type: The extraction-schema document type.
            states: State name -> the choice drawn for the page, as the distractor providers draw it.

        Returns:
            The withheld wordings; empty for a type or state that withholds none.
        """
        by_state = self.withheld[doc_type]
        return frozenset().union(
            *(by_state.get(name, {}).get(choice, frozenset()) for name, choice in states.items())
        )


def normalise_wording(text: str) -> str:
    """Return a wording case-folded, stripped of punctuation and with its whitespace collapsed.

    Two wordings that normalise alike read alike: `Amount Due:` and `amount due` are one wording.

    Args:
        text: A label wording.

    Returns:
        The normalised form, e.g. `abn no` for `ABN No.`.
    """
    return " ".join(_PUNCTUATION.sub("", text.casefold()).split())


def _fail(path: Path, key: str, what: str, expected: str, recover: str) -> LabelConfigError:
    return LabelConfigError(
        "Invalid label variation settings.\n"
        f"  What:     {what}\n"
        f"  Where:    {path.resolve()} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _missing(path: Path, key: str, example: str) -> LabelConfigError:
    return _fail(
        path,
        key,
        f"required key '{key}' is missing.",
        f"the key with a value, e.g. {example}.",
        f"add the key under {ROOT_KEY}: in {path.resolve()}.",
    )


def _mapping(path: Path, raw: object, key: str, keys: tuple[str, ...], example: str) -> dict:
    """Return `raw` as a mapping holding exactly `keys`, refusing any missing, extra or mistyped key."""
    if not isinstance(raw, dict):
        raise _fail(
            path,
            key,
            f"{key} is {raw!r}, not a mapping.",
            f"a mapping, e.g. {example}.",
            f"rewrite {key} as a mapping in {path.resolve()}.",
        )
    for name in keys:
        if name not in raw:
            raise _missing(path, f"{key}.{name}", example)
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise _fail(
            path,
            key,
            f"{key} carries unknown key(s) {unknown}.",
            f"exactly {list(keys)}, e.g. {example}.",
            f"remove {unknown} from {key}.",
        )
    return raw


@lru_cache(maxsize=None)
def _load_default() -> LabelConfig:
    return _load(DISTRACTORS_PATH)


def load_label_config(path: Path = DISTRACTORS_PATH) -> LabelConfig:
    """Load and validate `label_variation:`.

    The shipped file is parsed once per process; any other path is parsed on every call.

    Args:
        path: The distractor catalogue YAML carrying `label_variation:`.

    Returns:
        The validated, immutable settings.

    Raises:
        LabelConfigError: The file is absent or unparsable, or any key is missing or incoherent.
    """
    return _load_default() if path == DISTRACTORS_PATH else _load(path)


def _load(path: Path) -> LabelConfig:
    if not path.exists():
        raise _fail(
            path,
            "(file)",
            "the distractor catalogue does not exist.",
            f"a YAML mapping carrying a top-level {ROOT_KEY}: block, e.g.\n              {_EXAMPLE}",
            f"restore {path.resolve()} from git.",
        )
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as err:
        raise _fail(
            path, "(file)", f"the YAML does not parse: {err}.", "valid YAML.", "fix the syntax."
        ) from err
    if not isinstance(data, dict) or ROOT_KEY not in data:
        raise _fail(
            path,
            ROOT_KEY,
            f"required key '{ROOT_KEY}' is missing.",
            f"a top-level block, e.g.\n              {_EXAMPLE}",
            f"add the {ROOT_KEY}: block to {path.resolve()}.",
        )
    raw = _mapping(path, data[ROOT_KEY], ROOT_KEY, _KEYS, _EXAMPLE)
    schema = load_extraction_schema()
    doc_types = _load_doc_types(path, raw["doc_types"], schema.get_all_doc_type_fields())
    varied = list(dict.fromkeys(field for fields in doc_types.values() for field in fields))
    weights = _load_fields(path, raw["fields"], varied)
    pools = _load_pools(path, raw["pools"])
    withheld = _load_withheld(path, raw["withheld_by_state"], data.get("states"), list(doc_types), pools)
    collision = _mapping(
        path, raw["collision"], f"{ROOT_KEY}.collision", _COLLISION_KEYS, _EXAMPLES["collision"]
    )
    ignore_words = _load_ignore_words(path, collision["ignore_words"], varied)
    policy = raw["refusal_policy"]
    if policy not in REFUSAL_POLICIES:
        raise _fail(
            path,
            f"{ROOT_KEY}.refusal_policy",
            f"refusal_policy is {policy!r}.",
            f"one of {list(REFUSAL_POLICIES)}, e.g. {_EXAMPLES['refusal_policy']}.",
            f"set {ROOT_KEY}.refusal_policy to one of {list(REFUSAL_POLICIES)}.",
        )
    totals_region_fields = _load_totals_region_fields(path, raw["totals_region_fields"], varied)
    return LabelConfig(
        doc_types=doc_types,
        weights=weights,
        pools=pools,
        withheld=withheld,
        ignore_words=ignore_words,
        refusal_policy=str(policy),
        totals_region_fields=totals_region_fields,
        path=path.resolve(),
    )


def _load_doc_types(path: Path, raw: object, schema: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    key = f"{ROOT_KEY}.doc_types"
    example = _EXAMPLES["doc_types"]
    if not isinstance(raw, dict):
        raise _fail(
            path,
            key,
            f"doc_types is {raw!r}, not a mapping.",
            f"e.g. {example}.",
            "rewrite it as a mapping.",
        )
    for doc_type in schema:
        if doc_type not in raw:
            raise _fail(
                path,
                f"{key}.{doc_type}",
                f"document type '{doc_type}' is missing.",
                f"every extraction-schema document type {sorted(schema)} listed, [] for one whose labels "
                f"never vary, e.g. {example}.",
                f"add {doc_type}: [...] under {key}.",
            )
    unknown = sorted(set(raw) - set(schema))
    if unknown:
        raise _fail(
            path,
            key,
            f"doc_types names unknown document type(s) {unknown}.",
            f"only {sorted(schema)}, e.g. {example}.",
            f"remove {unknown} from {key}.",
        )
    doc_types: dict[str, tuple[str, ...]] = {}
    for doc_type, fields in raw.items():
        here = f"{key}.{doc_type}"
        if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
            raise _fail(
                path,
                here,
                f"{doc_type} is {fields!r}, not a list of field names.",
                f"e.g. {example}.",
                f"make {here} a list.",
            )
        if len(set(fields)) != len(fields):
            raise _fail(
                path,
                here,
                f"{doc_type} lists a field more than once: {fields}.",
                "each field once.",
                f"remove the repeat from {here}.",
            )
        unscored = [field for field in fields if field not in schema[doc_type]]
        if unscored:
            raise _fail(
                path,
                here,
                f"{doc_type} lists {unscored}, which are not scored columns of a {doc_type}.",
                f"fields from config/extraction_schema.yml -> document_fields.{doc_type}: "
                f"{schema[doc_type]}.",
                f"remove {unscored} from {here}.",
            )
        doc_types[doc_type] = tuple(fields)
    return doc_types


def _load_fields(path: Path, raw: object, varied: list[str]) -> dict[str, StyleWeights]:
    key = f"{ROOT_KEY}.fields"
    example = _EXAMPLES["fields"]
    if not isinstance(raw, dict):
        raise _fail(
            path, key, f"fields is {raw!r}, not a mapping.", f"e.g. {example}.", "rewrite it as a mapping."
        )
    for field in varied:
        if field not in raw:
            raise _missing(path, f"{key}.{field}", example)
    extra = sorted(set(raw) - set(varied))
    if extra:
        raise _fail(
            path,
            key,
            f"fields carries {extra}, which no document type in {ROOT_KEY}.doc_types varies.",
            f"exactly the varied fields {varied}, e.g. {example}.",
            f"remove {extra} from {key}, or list them under {ROOT_KEY}.doc_types.",
        )
    weights: dict[str, StyleWeights] = {}
    for field in varied:
        here = f"{key}.{field}"
        spec = _mapping(path, raw[field], here, _FIELD_KEYS, example)
        styles = _mapping(path, spec["weights"], f"{here}.weights", LABEL_STYLES, example)
        for style in LABEL_STYLES:
            value = styles[style]
            if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
                raise _fail(
                    path,
                    f"{here}.weights.{style}",
                    f"weight {value!r} is not a number above zero: every style keeps a positive weight.",
                    f"a number above zero for each of {list(LABEL_STYLES)}, e.g. {example}.",
                    f"set {here}.weights.{style} to a positive number.",
                )
        weights[field] = StyleWeights(**{style: float(styles[style]) for style in LABEL_STYLES})
    return weights


def _load_pools(path: Path, raw: object) -> dict[str, tuple[str, ...]]:
    key = f"{ROOT_KEY}.pools"
    example = _EXAMPLES["pools"]
    if not isinstance(raw, dict) or not raw:
        raise _fail(
            path,
            key,
            f"pools is {raw!r}, not a non-empty mapping.",
            f"e.g. {example}.",
            "declare the wording pools.",
        )
    pools: dict[str, tuple[str, ...]] = {}
    for name, wordings in raw.items():
        here = f"{key}.{name}"
        if not isinstance(wordings, list) or not wordings:
            raise _fail(
                path,
                here,
                f"pool '{name}' is {wordings!r}, not a non-empty list of wordings.",
                f"at least one wording, e.g. {example}.",
                f"add wordings to {here}, or remove the pool.",
            )
        seen: dict[str, str] = {}
        for wording in wordings:
            if not isinstance(wording, str) or not normalise_wording(wording):
                raise _fail(
                    path,
                    here,
                    f"pool '{name}' holds {wording!r}, which is not a wording with a word in it.",
                    f"non-empty strings, e.g. {example}.",
                    f"replace {wording!r} in {here} with a wording.",
                )
            normal = normalise_wording(wording)
            if normal in seen:
                raise _fail(
                    path,
                    here,
                    f"pool '{name}' holds {seen[normal]!r} and {wording!r}, which read alike once case, "
                    f"punctuation and spacing are set aside ('{normal}').",
                    "wordings unique after normalisation (case-folded, punctuation stripped, whitespace "
                    f"collapsed), e.g. {example}.",
                    f"remove one of {seen[normal]!r} and {wording!r} from {here}.",
                )
            seen[normal] = wording
        pools[str(name)] = tuple(wordings)
    return pools


def _load_ignore_words(path: Path, raw: object, varied: list[str]) -> dict[str, frozenset[str]]:
    """Load the words each varied field's variant may share with a distractor line, per field.

    Per field, so a generic noun ignored for one field's wordings ('number' beside 'ABN') cannot let
    another field's wording collide unseen.
    """
    key = f"{ROOT_KEY}.collision.ignore_words"
    example = _EXAMPLES["collision"]
    if not isinstance(raw, dict):
        raise _fail(
            path,
            key,
            f"ignore_words is {raw!r}, not a mapping of varied field to its ignored words.",
            f"one list per varied field {varied}, [] for none, e.g. {example}.",
            f"make {key} a mapping of each varied field to a list of words.",
        )
    for field in varied:
        if field not in raw:
            raise _missing(path, f"{key}.{field}", example)
    extra = sorted(set(raw) - set(varied))
    if extra:
        raise _fail(
            path,
            key,
            f"ignore_words carries {extra}, which no document type in {ROOT_KEY}.doc_types varies.",
            f"exactly the varied fields {varied}, e.g. {example}.",
            f"remove {extra} from {key}.",
        )
    words: dict[str, frozenset[str]] = {}
    for field in varied:
        listed = raw[field]
        if not isinstance(listed, list) or not all(
            isinstance(word, str) and normalise_wording(word) for word in listed
        ):
            raise _fail(
                path,
                f"{key}.{field}",
                f"ignore_words.{field} is {listed!r}, not a list of words.",
                f"a list of words, [] for none, e.g. {example}.",
                f"make {key}.{field} a list of words.",
            )
        words[field] = frozenset(normalise_wording(word) for word in listed)
    return words


def _load_totals_region_fields(path: Path, raw: object, varied: list[str]) -> tuple[str, ...]:
    key = f"{ROOT_KEY}.totals_region_fields"
    example = _EXAMPLES["totals_region_fields"]
    if not isinstance(raw, list) or not all(isinstance(field, str) for field in raw):
        raise _fail(
            path,
            key,
            f"totals_region_fields is {raw!r}, not a list of field names.",
            f"a list of varied fields, [] for none, e.g. {example}.",
            f"make {key} a list of field names.",
        )
    if len(set(raw)) != len(raw):
        raise _fail(
            path,
            key,
            f"totals_region_fields lists a field more than once: {raw}.",
            f"each field once, e.g. {example}.",
            f"remove the repeat from {key}.",
        )
    unknown = [field for field in raw if field not in varied]
    if unknown:
        raise _fail(
            path,
            key,
            f"totals_region_fields names {unknown}, which no document type in {ROOT_KEY}.doc_types varies.",
            f"fields from {varied}, e.g. {example}.",
            f"remove {unknown} from {key}, or list them under {ROOT_KEY}.doc_types.",
        )
    return tuple(raw)


def _pools_of_type(doc_type: str) -> set[str]:
    """Return the pool names the label sites of a document type's layouts name (empty for none)."""
    files = document_type_files()
    if doc_type not in files:
        return set()
    registry = yaml.safe_load(files[doc_type].layouts.read_text())
    names: set[str] = set()
    for layout in (registry.get("layouts") or {}).values():
        for spec in layout.get("field_providers") or []:
            if isinstance(spec, dict) and spec.get("name") == PROVIDER_NAME:
                sites = (spec.get("params") or {}).get("sites") or {}
                names.update(site["pool"] for site in sites.values())
    return names


def _load_withheld(
    path: Path, raw: object, states: object, doc_types: list[str], pools: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, dict[str, frozenset[str]]]]:
    """Load `withheld_by_state`: per document type, state and choice, the pool wordings never printed.

    A state and its choice must be declared under the catalogue's `states.<type>`, and a withheld wording
    must be a wording of a pool that one of the type's own layouts names, so the block cannot name a state
    no page is drawn in or a wording no page of that type would print.
    """
    key = f"{ROOT_KEY}.withheld_by_state"
    example = _EXAMPLES["withheld_by_state"]
    if not isinstance(raw, dict):
        raise _fail(
            path, key, f"withheld_by_state is {raw!r}, not a mapping.", f"e.g. {example}.", "rewrite it."
        )
    for doc_type in doc_types:
        if doc_type not in raw:
            raise _fail(
                path,
                f"{key}.{doc_type}",
                f"document type '{doc_type}' is missing.",
                f"every document type {doc_types} listed, {{}} for one that withholds nothing, e.g. "
                f"{example}.",
                f"add {doc_type}: {{}} under {key}.",
            )
    unknown = sorted(set(raw) - set(doc_types))
    if unknown:
        raise _fail(
            path,
            key,
            f"withheld_by_state names unknown document type(s) {unknown}.",
            f"only {doc_types}, e.g. {example}.",
            f"remove {unknown} from {key}.",
        )
    declared = states if isinstance(states, dict) else {}
    withheld: dict[str, dict[str, dict[str, frozenset[str]]]] = {}
    for doc_type in doc_types:
        here = f"{key}.{doc_type}"
        by_state = raw[doc_type]
        if not isinstance(by_state, dict):
            raise _fail(
                path,
                here,
                f"{doc_type} is {by_state!r}, not a mapping of state to choice to wordings.",
                f"e.g. {example}.",
                f"rewrite {here} as a mapping, or {{}} for none.",
            )
        known = {wording for name in _pools_of_type(doc_type) for wording in pools.get(name, ())}
        type_states = declared.get(doc_type) if isinstance(declared.get(doc_type), dict) else {}
        withheld[doc_type] = {}
        for state, by_choice in by_state.items():
            spec = type_states.get(state) if isinstance(type_states, dict) else None
            choices = spec.get("choices") if isinstance(spec, dict) else None
            if not isinstance(choices, list):
                raise _fail(
                    path,
                    f"{here}.{state}",
                    f"state '{state}' is not declared under states.{doc_type} in the catalogue.",
                    f"a state of states.{doc_type} ({sorted(type_states or {})}), e.g. {example}.",
                    f"name a declared state under {here}, or remove '{state}'.",
                )
            if not isinstance(by_choice, dict):
                raise _fail(
                    path,
                    f"{here}.{state}",
                    f"{state} is {by_choice!r}, not a mapping of choice to wordings.",
                    f"e.g. {example}.",
                    f"rewrite {here}.{state} as a mapping.",
                )
            withheld[doc_type][str(state)] = {}
            for choice, wordings in by_choice.items():
                at = f"{here}.{state}.{choice}"
                if choice not in choices:
                    raise _fail(
                        path,
                        at,
                        f"'{choice}' is not a choice of states.{doc_type}.{state}.",
                        f"one of {choices}, e.g. {example}.",
                        f"name one of {choices} under {here}.{state}.",
                    )
                if (
                    not isinstance(wordings, list)
                    or not wordings
                    or not all(isinstance(w, str) for w in wordings)
                ):
                    raise _fail(
                        path,
                        at,
                        f"{choice} is {wordings!r}, not a non-empty list of wordings.",
                        f"pool wordings, e.g. {example}.",
                        f"list wordings under {at}, or remove '{choice}'.",
                    )
                strays = [w for w in wordings if w not in known]
                if strays:
                    raise _fail(
                        path,
                        at,
                        f"{strays} are wordings of no pool a {doc_type} layout names, so withholding them "
                        "changes nothing.",
                        f"wordings exactly as a pool under {ROOT_KEY}.pools spells them, e.g. {example}.",
                        f"correct or remove {strays} in {at}.",
                    )
                withheld[doc_type][str(state)][str(choice)] = frozenset(wordings)
    return withheld
