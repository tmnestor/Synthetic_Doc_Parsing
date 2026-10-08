"""Refuse a scored ground-truth value its layout does not draw, or draws as something else.

A ground-truth value the page never prints is a field the benchmark scores without showing the model the
answer, and a value printed wrongly is worse. The layout draws a scored field when a block or column binds
it with `field:`, so the plain render records a geometry box for it (`FIELD`, or `FIELD[i]` for a list
member) together with the string drawn there. This check reads both off the plain render `check_overflow`
already makes. A scored field with a real value is refused when it has no box, when a list member with a
value has no box of its own, or when the drawn string, read under `drawn_value_policy` in
config/field_definitions.yml (currency sign, thousands grouping, credit markers, layout date formats,
whitespace), is not the ground-truth string.

A field the layout deliberately leaves unboxed (a classification label, a boolean, a value printed in a
letterhead) is declared under `geometry_exemptions` in config/field_definitions.yml with a reason, so the
exception is visible in YAML and a new omission cannot hide behind it. Each exemption states
`members_where` (`none` to exempt every member, or `{FIELD: value}` to exempt only the list members at
whose position each named list holds that value) and `printed_as` (`none`, or `{layout_key: KEY}` for a
value printed from the layout's own KEY, which is then checked against the ground truth).
"""

import functools
from pathlib import Path

from generators.drawn_values import DrawnValuePolicy, load_policy, policy_path
from generators.loader import load_ground_truth, load_layout_registry
from generators.schema import _load_field_defs, field_names_for

NOT_FOUND = "NOT_FOUND"
EXEMPTIONS_KEY = "geometry_exemptions"
MEMBERS_WHERE_KEY = "members_where"
PRINTED_AS_KEY = "printed_as"
EXPLICIT_NONE = "none"
FIELD_DEFS_PATH = Path("config/field_definitions.yml")
# Every exemption carries exactly these keys.
EXEMPTION_KEYS = ("layouts", MEMBERS_WHERE_KEY, PRINTED_AS_KEY, "reason")


class DrawnFieldsError(RuntimeError):
    """A scored field is exempt-listed wrongly, or a layout leaves a scored value undrawn."""


def has_value(value: object) -> bool:
    """Report whether a ground-truth value carries anything but NOT_FOUND.

    Args:
        value: A field's ground-truth value (a pipe-delimited list for a list field).

    Returns:
        True unless the value is absent, empty, NOT_FOUND, or a list whose every item is NOT_FOUND.
    """
    if value is None:
        return False
    items = [item.strip() for item in str(value).split("|")]
    return any(item not in ("", NOT_FOUND) for item in items)


def list_items(value: object) -> list[str]:
    """Split a ground-truth value into its stripped pipe-delimited items (one item for a scalar)."""
    return [item.strip() for item in str(value).split("|")]


def list_fields() -> frozenset[str]:
    """Fields held as pipe-delimited lists: every `field_types` group whose name ends in `_list`."""
    types = _load_field_defs().get("field_types", {})
    return frozenset(field for group, names in types.items() if group.endswith("_list") for field in names)


def _exemption_error(key: str, what: str, example: str) -> DrawnFieldsError:
    path = FIELD_DEFS_PATH.resolve()
    return DrawnFieldsError(
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: each exemption carries exactly {list(EXEMPTION_KEYS)}: `layouts` is `all` or a\n"
        f"            non-empty list of layout ids, `{MEMBERS_WHERE_KEY}` is `{EXPLICIT_NONE}` or a {{FIELD: "
        f'"value"}} mapping\n'
        f"            naming fields of the type, `{PRINTED_AS_KEY}` is `{EXPLICIT_NONE}` or {{layout_key: KEY}}, "
        f"and `reason` is\n"
        f"            non-empty text, e.g.\n{example}\n"
        f"  Recover:  fix that entry in {path}."
    )


def load_exemptions(doc_type: str) -> dict[str, dict]:
    """Return a document type's geometry exemptions from config/field_definitions.yml.

    Args:
        doc_type: A generation_config.yml `document_types` key, e.g. `bank_statements`.

    Returns:
        Field name -> {`layouts`, `members_where`, `printed_as`, `reason`} as declared.

    Raises:
        DrawnFieldsError: The `geometry_exemptions` block, or the type's entry, is missing or malformed.
    """
    path = FIELD_DEFS_PATH.resolve()
    key = (doc_type[:-1] if doc_type.endswith("s") else doc_type).upper()
    defs = _load_field_defs()
    block = defs.get(EXEMPTIONS_KEY)
    example = (
        f"            {EXEMPTIONS_KEY}:\n"
        f"              {key}:\n"
        "                DOCUMENT_TYPE: {layouts: all, members_where: none, printed_as: none,\n"
        "                                reason: a classification label, never printed}"
    )
    if not isinstance(block, dict) or not isinstance(block.get(key), dict):
        raise DrawnFieldsError(
            f"  What:     no `{EXEMPTIONS_KEY}.{key}` mapping for the drawn-field check.\n"
            f"  Where:    {path} -> {EXEMPTIONS_KEY}.{key}\n"
            f"  Expected: a mapping of field -> {{layouts, members_where, printed_as, reason}}; use {{}} "
            f"for none, e.g.\n{example}\n"
            f"  Recover:  add the `{key}:` mapping under `{EXEMPTIONS_KEY}:` in {path}."
        )
    known = field_names_for(doc_type)
    exemptions: dict[str, dict] = block[key]
    for field, spec in exemptions.items():
        where_key = f"{EXEMPTIONS_KEY}.{key}.{field}"
        if not isinstance(spec, dict) or sorted(spec) != sorted(EXEMPTION_KEYS):
            keys = sorted(spec) if isinstance(spec, dict) else spec
            raise _exemption_error(where_key, f"`{where_key}` has the keys {keys!r}.", example)
        layouts = spec["layouts"]
        if not (layouts == "all" or (isinstance(layouts, list) and layouts)) or not spec["reason"]:
            raise _exemption_error(where_key, f"`{where_key}` is not well formed: {spec!r}.", example)
        where = spec[MEMBERS_WHERE_KEY]
        valid_where = where == EXPLICIT_NONE or (
            isinstance(where, dict)
            and where
            and all(isinstance(name, str) and isinstance(value, str) for name, value in where.items())
        )
        if not valid_where:
            raise _exemption_error(
                f"{where_key}.{MEMBERS_WHERE_KEY}",
                f"`{where_key}.{MEMBERS_WHERE_KEY}` is {where!r}, not `{EXPLICIT_NONE}` or a mapping of list "
                'field to string value (quote the value: "1").',
                example,
            )
        unknown = sorted(name for name in where if name not in known) if isinstance(where, dict) else []
        if unknown:
            raise _exemption_error(
                f"{where_key}.{MEMBERS_WHERE_KEY}",
                f"`{where_key}.{MEMBERS_WHERE_KEY}` names {unknown}, which are not {key} fields, so the "
                f"exemption could match no member.",
                example,
            )
        printed = spec[PRINTED_AS_KEY]
        valid_printed = printed == EXPLICIT_NONE or (
            isinstance(printed, dict)
            and sorted(printed) == ["layout_key"]
            and isinstance(printed["layout_key"], str)
            and printed["layout_key"]
        )
        if not valid_printed:
            raise _exemption_error(
                f"{where_key}.{PRINTED_AS_KEY}",
                f"`{where_key}.{PRINTED_AS_KEY}` is {printed!r}, not `{EXPLICIT_NONE}` or {{layout_key: KEY}}.",
                example,
            )
    return exemptions


class DrawnFieldsCheck:
    """Collect a refusal for each scored value a plain render does not draw, or draws wrongly.

    An instance is the `plain_measure` callback `check_overflow` hands each plain render's geometry to.

    Attributes:
        errors: One four-part refusal per (case, field) with a value that is undrawn, drawn without a box
            of its own for every list member, or drawn as a string that is not its ground truth.
    """

    def __init__(
        self,
        gt_data: dict,
        *,
        doc_type: str,
        layout_path: Path,
        ground_truth_path: Path,
        render: str = "plain",
    ) -> None:
        """Hold what a refusal must name.

        Args:
            gt_data: Mapping of case id -> entry dict for the document type.
            doc_type: A generation_config.yml `document_types` key, e.g. `bank_statements`.
            layout_path: The layout registry YAML, named in each refusal and read for `printed_as` keys.
            ground_truth_path: The ground-truth YAML, named in each refusal.
            render: Which render the refusals name: `plain`, or `labelled varied` for the third render
                `validate` checks the same way.

        Raises:
            DrawnFieldsError: The type's `geometry_exemptions` are missing or malformed.
            DrawnValuePolicyError: `drawn_value_policy` is missing or malformed.
            SchemaError: field_definitions.yml has no entry for the type.
        """
        self.gt_data = gt_data
        self.render = render
        self.layout_path = layout_path.resolve()
        self.ground_truth_path = ground_truth_path.resolve()
        self.scored = sorted(field_names_for(doc_type))
        self.exemptions = load_exemptions(doc_type)
        self.policy: DrawnValuePolicy = load_policy()
        self.lists = list_fields()
        self.errors: list[str] = []

    @functools.cached_property
    def layouts(self) -> dict:
        """The layout registry, read once for the `printed_as` checks."""
        return load_layout_registry(self.layout_path)

    def exemption(self, field: str, layout_id: str) -> dict | None:
        """Return the exemption covering a field on a layout, or None."""
        spec = self.exemptions.get(field)
        if spec is not None and (spec["layouts"] == "all" or layout_id in spec["layouts"]):
            return spec
        return None

    def __call__(self, case_id: str, layout_id: str, geometry: dict) -> None:
        """Record a refusal for each scored value the render did not draw, or drew as another string.

        Args:
            case_id: The case rendered.
            layout_id: Its layout.
            geometry: The render's `geometry_out`, carrying `boxes` and `texts`.
        """
        fields = self.gt_data[case_id].get("fields", {})
        texts: dict[str, str] = geometry["texts"]
        for field in self.scored:
            value = fields.get(field)
            if not has_value(value):
                continue
            spec = self.exemption(field, layout_id)
            if spec is not None and spec[MEMBERS_WHERE_KEY] == EXPLICIT_NONE:
                self._check_printed_as(case_id, layout_id, field, str(value), spec)
                continue
            if field in self.lists:
                where = spec[MEMBERS_WHERE_KEY] if spec is not None else {}
                members = self._members(field, fields, where)
            else:
                members = [(field, self.policy.normalise_text(str(value)))]
            missing = [key for key, _ in members if key not in texts]
            wrong = [
                (key, texts[key], expected)
                for key, expected in members
                if key in texts and self.policy.read(field, layout_id, texts[key]) != expected
            ]
            if missing:
                self.errors.append(self._missing_refusal(case_id, layout_id, field, missing, spec))
            if wrong:
                self.errors.append(self._wrong_refusal(case_id, layout_id, field, wrong))

    def _members(self, field: str, fields: dict, where: dict) -> list[tuple[str, str]]:
        """Return (box key, expected string) for each list member with a value that no exemption covers."""
        conditions = {name: list_items(fields.get(name, "")) for name in where}
        members = []
        for index, item in enumerate(list_items(fields.get(field))):
            if item in ("", NOT_FOUND):
                continue
            if where and all(
                index < len(items) and items[index] == where[name] for name, items in conditions.items()
            ):
                continue
            members.append((f"{field}[{index}]", self.policy.normalise_text(item)))
        return members

    def _check_printed_as(self, case_id: str, layout_id: str, field: str, value: str, spec: dict) -> None:
        """Refuse a value exempted as printed from a layout key when that key does not hold it."""
        printed = spec[PRINTED_AS_KEY]
        if printed == EXPLICIT_NONE:
            return
        layout_key = printed["layout_key"]
        drawn = self.layouts.get(layout_id, {}).get(layout_key)
        if drawn is not None and self.policy.normalise_text(str(drawn)) == self.policy.normalise_text(
            value
        ):
            return
        self.errors.append(
            f"{case_id}: scored field {field} is exempt as printed from layout key `{layout_key}`, which "
            f"does not hold its value\n"
            f"  What:     {case_id} / {field} / {layout_id}: layouts.{layout_id}.{layout_key} is {drawn!r} "
            f"but the ground truth is {value!r}, so the page prints something other than the scored value.\n"
            f"  Where:    layout {self.layout_path} -> layouts.{layout_id}.{layout_key}; ground truth "
            f"{self.ground_truth_path} -> {case_id}.fields.{field}; exemption "
            f"{FIELD_DEFS_PATH.resolve()} -> {EXEMPTIONS_KEY}\n"
            f"  Expected: {layout_key}: {value} on that layout, equal to the ground truth.\n"
            f"  Recover:  correct layouts.{layout_id}.{layout_key} in {self.layout_path}, or the ground "
            "truth, so the two agree."
        )

    def _missing_refusal(
        self, case_id: str, layout_id: str, field: str, missing: list[str], spec: dict | None
    ) -> str:
        """Word the refusal for a field, or list members of it, with a value and no box."""
        scope = (
            f" (exempt only where {spec[MEMBERS_WHERE_KEY]})"
            if spec is not None and spec[MEMBERS_WHERE_KEY] != EXPLICIT_NONE
            else ""
        )
        return (
            f"{case_id}: scored field {field} has a ground-truth value that layout '{layout_id}' "
            f"does not draw\n"
            f"  What:     {case_id} / {field} / {layout_id}: the {self.render} render recorded no box for "
            f"{', '.join(missing)}{scope}, so the page does not print the value the benchmark scores.\n"
            f"  Where:    layout {self.layout_path} -> layouts.{layout_id}.body; ground truth "
            f"{self.ground_truth_path} -> {case_id}.fields.{field}; exemptions "
            f"{FIELD_DEFS_PATH.resolve()} -> {EXEMPTIONS_KEY}\n"
            f"  Expected: a block or column in that layout bound to the field, every list member in a box "
            f"of its own, e.g.\n"
            f'            {{type: pair, label: "Label", value: "{{{field}}}", field: {field}}}\n'
            f"            (or, for a field a layout leaves unboxed by design, an entry under "
            f"`{EXEMPTIONS_KEY}` with a reason).\n"
            f"  Recover:  add the `field: {field}` binding to layouts.{layout_id} in "
            f"{self.layout_path}, or correct the ground truth."
        )

    def _wrong_refusal(
        self, case_id: str, layout_id: str, field: str, wrong: list[tuple[str, str, str]]
    ) -> str:
        """Word the refusal for boxes whose drawn string is not the ground truth under the policy."""
        listing = "; ".join(
            f"{key} drawn {drawn!r} reads as {self.policy.read(field, layout_id, drawn)!r}, ground truth "
            f"{expected!r}"
            for key, drawn, expected in wrong[:5]
        )
        more = f" (and {len(wrong) - 5} more)" if len(wrong) > 5 else ""
        return (
            f"{case_id}: scored field {field} is drawn as a string that is not its ground truth on layout "
            f"'{layout_id}'\n"
            f"  What:     {case_id} / {field} / {layout_id} ({self.render} render): {listing}{more}.\n"
            f"  Where:    layout {self.layout_path} -> layouts.{layout_id}.body (the block bound to "
            f"{field}); ground truth {self.ground_truth_path} -> {case_id}.fields.{field}; policy "
            f"{policy_path()} -> drawn_value_policy\n"
            f"  Expected: the bound block draws the ground-truth value itself, varied only as "
            f"drawn_value_policy allows (currency sign, thousands grouping, credit or debit marker, the "
            f"layout's date format, whitespace), e.g. ground truth '1234.50' drawn '$1,234.50'.\n"
            f"  Recover:  bind the block to the right field, draw the value without other text in its box "
            f"(a label belongs outside the bound value), or correct the ground truth."
        )


def build_check(
    doc_type: str,
    gt_path: Path,
    layout_path: Path,
    gt_data: dict | None = None,
    *,
    render: str = "plain",
) -> DrawnFieldsCheck:
    """Build the check for a document type, loading its ground truth unless given.

    Args:
        doc_type: A generation_config.yml `document_types` key.
        gt_path: The ground-truth YAML.
        layout_path: The layout registry YAML.
        gt_data: Already-loaded ground truth, to spare a second load.
        render: Which render the refusals name (see `DrawnFieldsCheck`).

    Returns:
        A `DrawnFieldsCheck` ready to be passed to `check_overflow` as `plain_measure`.
    """
    data = gt_data if gt_data is not None else load_ground_truth(gt_path)
    return DrawnFieldsCheck(
        {str(k): v for k, v in data.items()},
        doc_type=doc_type,
        layout_path=layout_path,
        ground_truth_path=gt_path,
        render=render,
    )


__all__ = [
    "DrawnFieldsCheck",
    "DrawnFieldsError",
    "build_check",
    "has_value",
    "list_fields",
    "list_items",
    "load_exemptions",
]
