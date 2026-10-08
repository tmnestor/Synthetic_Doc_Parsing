"""Screen a drawn distractor value against the document's scored values.

A distractor must never hand a model a scored answer. The rules, from the design (section 9, item 2):

- `near_miss`: the value must not equal its target's value (or any element of a pipe-list target), and
  must not contain any scored value of any field;
- `repeat`: the value must equal its field's value exactly;
- `clutter`: the value must not contain any scored value.

The repeat and near-miss equality checks read the raw ground-truth value of the item's field or target,
so `validator.ignore_fields` and `validator.min_scored_length` (both in `config/distractors.yml`) bound
the contains-check only. The contains-check screens the scored values: the document type's columns in
`config/extraction_schema.yml`, less those ignored fields and values shorter than that length.

Comparisons other than the repeat check are made after normalising both sides: surrounding space
stripped, runs of whitespace collapsed, case folded, and the spaces inside a run of digit groups removed,
so an ABN-like `12 345 678 901` matches `12345678901`. The contains-check keeps the drawn text's
spaces and lets a scored number match its spaced or unspaced form, bounded at real token edges: a scored
value that starts or ends with a digit matches only where it is not part of a longer number, so `2.00`
is not found inside `12.00`, while two separate numbers side by side are still both screened.
"""

import re
from functools import lru_cache

from generators.distractors.config import DistractorConfig, Item, ValidatorSettings
from generators.distractors.paths import DISTRACTORS_PATH, DistractorPathError, ground_truth_file
from generators.exporters.eval_projection import FIELD_ALIASES, load_extraction_schema

# Extraction-schema column -> the ground-truth key that carries it, where the two differ: the inverse
# of the shared `FIELD_ALIASES`.
GROUND_TRUTH_ALIASES: dict[str, str] = {column: key for key, column in FIELD_ALIASES.items()}

_NOT_FOUND = "NOT_FOUND"
_DIGIT_GAP = re.compile(r"(?<=\d) (?=\d)")


class DistractorValueError(ValueError):
    """Raised when distractor values cannot be screened, or a value collides with a scored value."""


def _error(what: str, where: str, expected: str, recover: str) -> DistractorValueError:
    return DistractorValueError(
        "Distractor value check failed.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def normalise(text: str) -> str:
    """Normalise a value for comparison.

    Args:
        text: A scored or drawn value.

    Returns:
        The value stripped, with whitespace runs collapsed, case folded, and spaces between digits removed.
    """
    return _DIGIT_GAP.sub("", " ".join(str(text).split()).casefold())


def ground_truth_key(column: str, fields: dict) -> str:
    """Return the ground-truth key that carries an extraction-schema column in `fields`.

    Args:
        column: An extraction-schema column, e.g. `LINE_ITEM_DESCRIPTIONS`.
        fields: The document's ground-truth fields.

    Returns:
        `column` when `fields` holds it, else its ground-truth alias (`TRANSACTION_DESCRIPTIONS` for a
        bank statement's descriptions), else `column`.
    """
    if column in fields:
        return column
    return GROUND_TRUTH_ALIASES.get(column, column)


def _raw_values(fields: dict, column: str) -> list[str]:
    """A column's ground-truth values, unfiltered: pipe list split, stripped, `NOT_FOUND` dropped."""
    raw = fields.get(ground_truth_key(column, fields))
    if raw is None:
        return []
    return [part for part in (p.strip() for p in str(raw).split("|")) if part and part != _NOT_FOUND]


def scored_values(
    fields: dict[str, str], doc_type: str, settings: ValidatorSettings
) -> dict[str, list[str]]:
    """Collect the scored values a distractor value is screened against by the contains-check.

    Args:
        fields: The document's ground-truth fields.
        doc_type: The extraction-schema document type, e.g. `invoice`.
        settings: The catalogue's validator settings.

    Returns:
        Scored column -> its values, stripped: pipe lists split, `NOT_FOUND` and ignored columns dropped,
        values shorter than `min_scored_length` dropped, and columns left empty omitted.

    Raises:
        DistractorValueError: `doc_type` is not a document type of the extraction schema.
    """
    schema = load_extraction_schema().get_all_doc_type_fields()
    if doc_type not in schema:
        raise DistractorValueError(
            "Cannot screen distractor values.\n"
            f"  What:     unknown document type {doc_type!r}.\n"
            f"  Where:    the doc_types of the calling item in {DISTRACTORS_PATH} -> items.<id>.doc_types\n"
            f"  Expected: one of {sorted(schema)}.\n"
            "  Recover:  pass a document type declared in config/extraction_schema.yml."
        )
    scored: dict[str, list[str]] = {}
    for column in schema[doc_type]:
        if column in settings.ignore_fields:
            continue
        kept = [value for value in _raw_values(fields, column) if len(value) >= settings.min_scored_length]
        if kept:
            scored[column] = kept
    return scored


def _flatten(text: str) -> str:
    """Strip, collapse whitespace runs and case-fold, keeping every space (the contains-check's text)."""
    return " ".join(str(text).split()).casefold()


@lru_cache(maxsize=4096)
def _pattern(needle: str) -> re.Pattern[str]:
    """A search pattern for a scored value, bounded at real token edges.

    `needle` is the value normalised (digit gaps removed). One optional space is allowed between each
    pair of consecutive digits, so the scored `12345678901` or `12 345 678 901` matches either printed
    form. A needle that starts with a digit may not follow a digit, nor a digit and a decimal point (so
    `2.00` is not found inside `12.00`); one that ends with a digit may not be followed by a digit.
    The drawn text keeps its spaces, so two separate numbers never merge into one digit run.
    """
    parts: list[str] = []
    for index, char in enumerate(needle):
        if index and char.isdigit() and needle[index - 1].isdigit():
            parts.append(" ?")
        parts.append(re.escape(char))
    pattern = "".join(parts)
    if needle[0].isdigit():
        pattern = r"(?<!\d)(?<!\d\.)" + pattern
    if needle[-1].isdigit():
        pattern += r"(?!\d)"
    return re.compile(pattern)


def _contains(drawn: str, scored_value: str) -> bool:
    needle = normalise(scored_value)
    return bool(needle) and _pattern(needle).search(drawn) is not None


def _find(item: Item, value: str, fields: dict, scored: dict[str, list[str]]) -> tuple[str, str] | None:
    """The colliding ground-truth key and how the value collides with it, or None."""
    if item.kind == "repeat":
        # The loader guarantees a repeat item names its field.
        key = ground_truth_key(str(item.field), fields)
        raw = fields.get(key)
        if raw is None:
            return key, "copies a field the entry does not carry:"
        return None if value == str(raw) else (key, f"differs from the ground-truth value {str(raw)!r} of")
    if item.kind == "near_miss":
        target = str(item.target)
        if any(normalise(value) == normalise(raw) for raw in _raw_values(fields, target)):
            return ground_truth_key(target, fields), "equals its target, the scored field"
    drawn = _flatten(value)
    for column, values in scored.items():
        if any(_contains(drawn, scored_value) for scored_value in values):
            return ground_truth_key(column, fields), "contains the value of the scored field"
    return None


def collision(item: Item, value: str, fields: dict, scored: dict[str, list[str]]) -> str | None:
    """Return the ground-truth key a drawn value collides with, or None.

    Args:
        item: The catalogue item the value was drawn for.
        value: The drawn value.
        fields: The document's raw ground-truth fields, for the repeat and near-miss equality checks.
        scored: The output of `scored_values` for the document, for the contains-check.

    Returns:
        For a `repeat` item, its field unless the value equals that field's raw value exactly; for a
        `near_miss`, its target when the value equals the raw target (or an element of it), else the
        first scored column whose value the drawn value contains; for `clutter`, that first contained
        column; None when the value is clear. A column is named by its ground-truth key, e.g.
        `TRANSACTION_DESCRIPTIONS` for a bank statement's `LINE_ITEM_DESCRIPTIONS`.
    """
    found = _find(item, value, fields, scored)
    return None if found is None else found[0]


def _ground_truth_where(doc_type: str) -> str:
    """The ground-truth file of `doc_type` for a diagnostic, or a placeholder when none is declared."""
    try:
        return str(ground_truth_file(doc_type))
    except DistractorPathError:
        return "<ground truth>"


def check_value(
    item: Item,
    key: str,
    value: str,
    fields: dict,
    scored: dict[str, list[str]],
    doc_type: str,
    *,
    case_id: str,
) -> None:
    """Check one drawn value; on a collision raise a four-part error naming case, item and field.

    Args:
        item: The catalogue item the value was drawn for.
        key: The `DX_` key the value is emitted under.
        value: The drawn value.
        fields: The document's raw ground-truth fields.
        scored: The output of `scored_values` for the document.
        doc_type: The extraction-schema document type.
        case_id: The case the value was drawn for.

    Raises:
        DistractorValueError: The value collides with a scored value, or a repeat differs from its field.
    """
    found = _find(item, value, fields, scored)
    if found is None:
        return
    field, how = found
    what = f"{case_id}: {item.kind} item {item.id!r} emitted {key}={value!r}, which {how} {field}."
    where = (
        f"{_ground_truth_where(doc_type)} -> {case_id}.fields.{field}; "
        f"{DISTRACTORS_PATH} -> items.{item.id}"
    )
    if item.kind == "repeat":
        raise _error(
            what,
            where,
            f"a repeat value equal to the ground-truth {field} exactly, character for character.",
            "a repeat is copied verbatim, so a mismatch is a code fault: fix the rule or provider that "
            f"altered {key}, or restore {field} in the ground truth of {case_id}.",
        )
    if item.kind == "near_miss":
        expected = (
            f"a near_miss value that neither equals its target {item.target} nor contains a scored value."
        )
    else:
        expected = f"a {item.kind} value that contains no scored value."
    raise _error(
        what,
        where,
        expected,
        f"widen items.{item.id}.rule in {DISTRACTORS_PATH} so it cannot reproduce {field}, or remove "
        f"{item.id!r} from the layout's distractor_items.",
    )


def check_distractor_values(
    values: dict[str, str], fields: dict, doc_type: str, config: DistractorConfig, *, case_id: str
) -> None:
    """Check every distractor value of one document against its scored values.

    Args:
        values: `DX_` key -> drawn value, as a distractor provider returns them.
        fields: The document's raw ground-truth fields.
        doc_type: The extraction-schema document type, e.g. `invoice`.
        config: The distractor catalogue, for its items and validator settings.
        case_id: The case the values were drawn for, named in every diagnostic.

    Raises:
        DistractorValueError: A key is emitted by no catalogue item for `doc_type`, a value collides with
            a scored value, or a repeat differs from its field; the four-part message names the case,
            the item, the key, the value and the ground-truth field.
    """
    by_key = {
        key: item for item in config.items.values() if doc_type in item.doc_types for key in item.emits
    }
    scored = scored_values(fields, doc_type, config.validator)
    for key, value in values.items():
        item = by_key.get(key)
        if item is None:
            raise _error(
                f"{case_id}: no {doc_type} item in the catalogue emits {key} (value {value!r}), so it "
                "cannot be screened.",
                f"{DISTRACTORS_PATH} -> items (the items whose doc_types include {doc_type})",
                f"keys emitted by {doc_type} items: {sorted(by_key)}.",
                f"declare an item emitting {key} for {doc_type} in {DISTRACTORS_PATH}, or stop emitting it.",
            )
        check_value(item, key, value, fields, scored, doc_type, case_id=case_id)
