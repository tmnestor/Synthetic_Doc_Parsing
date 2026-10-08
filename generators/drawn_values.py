"""Read a drawn string in its ground-truth form, under the policy config/field_definitions.yml declares.

A layout prints some scored values in a form the ground truth does not hold: an amount with its currency
sign, its thousands grouped or a credit marker after it, or a date in the layout's own format. The page is
still correct when the drawn string, read back under one declared policy, equals the ground-truth string.
`drawn_value_policy` in config/field_definitions.yml is that policy, and this module is its only reader:
the drawn-field check in `pipeline validate` and the receipt's unit-price check both apply it, so what
the page may print is decided once.
"""

import functools
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from generators.schema import _FIELD_DEFS_PATH, _load_field_defs

POLICY_KEY = "drawn_value_policy"
WHITESPACE_MODES = ("collapse", "exact")
GROUPING_MODES = ("allowed", "refused")
AMOUNT_TYPES = ("amount", "amount_list")
DATE_TYPES = ("date",)
DATE_RANGE_TYPES = ("date_range",)

_EXAMPLE = (
    f"            {POLICY_KEY}:\n"
    "              whitespace: collapse\n"
    "              amounts:\n"
    '                currency_sign: "$"\n'
    "                grouping: allowed\n"
    '                thousands_separator: ","\n'
    "                credit_markers: [CR, Cr]\n"
    "                debit_markers: [DR, Dr]\n"
    "              dates:\n"
    '                ground_truth_format: "%d/%m/%Y"\n'
    '                range_separator: " - "\n'
    "                layout_formats: {}"
)


class DrawnValuePolicyError(ValueError):
    """The drawn-value policy is missing or malformed."""


def _policy_error(key: str, what: str) -> DrawnValuePolicyError:
    path = _FIELD_DEFS_PATH.resolve()
    return DrawnValuePolicyError(
        f"the drawn-value policy is missing or malformed.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: every key present, enums one of their listed values, e.g.\n{_EXAMPLE}\n"
        f"            (whitespace one of {list(WHITESPACE_MODES)}, grouping one of {list(GROUPING_MODES)}).\n"
        f"  Recover:  add or correct `{key}` in {path}."
    )


@dataclass(frozen=True)
class DrawnValuePolicy:
    """How a drawn string is read back into its ground-truth form.

    Attributes:
        whitespace: `collapse` (runs of whitespace read as one space) or `exact`.
        currency_sign: The sign an amount may carry before its digits.
        grouping: `allowed` or `refused`: whether an amount may group its thousands.
        thousands_separator: The grouping character.
        credit_markers: Words after an amount that mark it a credit (left positive).
        debit_markers: Words after an amount that mark it a debit (read as negative).
        ground_truth_format: The strftime format ground-truth dates are held in.
        range_separator: What joins the two dates of a ground-truth date range.
        layout_formats: Layout id -> the strftime format that layout prints dates in.
        amount_fields: Fields read as amounts (field_types `amount` and `amount_list`).
        date_fields: Fields read as one date (field_types `date`).
        date_range_fields: Fields read as a date range (field_types `date_range`).
    """

    whitespace: str
    currency_sign: str
    grouping: str
    thousands_separator: str
    credit_markers: tuple[str, ...]
    debit_markers: tuple[str, ...]
    ground_truth_format: str
    range_separator: str
    layout_formats: dict[str, str]
    amount_fields: frozenset[str]
    date_fields: frozenset[str]
    date_range_fields: frozenset[str]

    def read(self, field: str, layout_id: str, drawn: str) -> str:
        """Return a drawn string in the form the ground truth holds, or the string as drawn.

        A string that does not parse under the policy (an amount grouped when grouping is refused, a date
        not in the layout's format) is returned with only its whitespace normalised, so it then differs
        from the ground truth and is refused by the caller.

        Args:
            field: The scored field the string was drawn for (a list member's base name).
            layout_id: The layout that drew it.
            drawn: The drawn string, wrapped lines joined by a newline.

        Returns:
            The string the ground truth must equal for the page to be correct.
        """
        text = self.normalise_text(drawn)
        if field in self.amount_fields:
            return self._read_amount(text)
        if field in self.date_fields:
            return self._read_date(text, layout_id)
        if field in self.date_range_fields:
            parts = text.split(self.range_separator)
            if len(parts) == 2:
                return self.range_separator.join(self._read_date(part.strip(), layout_id) for part in parts)
        return text

    def money_in(self, text: str) -> list[Decimal]:
        """Return every money amount a drawn line prints, each read as the guard reads an amount.

        A whitespace-separated token is money when, stripped of surrounding brackets and trailing
        punctuation, it reads under the amount rule (currency sign, thousands grouping) as digits, and it
        either carries the currency sign or has exactly two decimals: `$6,210.61`, `564.60` and `$400` are
        money; `Store 1234`, `10%`, `20/09/2024` and a phone number are not.

        Args:
            text: A drawn line's text.

        Returns:
            The amounts in drawn order, as decimals (a debit-marked or minus-signed amount negative).
        """
        amounts: list[Decimal] = []
        words = self.normalise_text(text).split(" ")
        for index, word in enumerate(words):
            token = word.lstrip("(").rstrip(".,;:)")
            if not token:
                continue
            marker = words[index + 1] if index + 1 < len(words) else ""
            if marker in self.debit_markers or marker in self.credit_markers:
                token = f"{token} {marker}"
            signed = token.lstrip("-").startswith(self.currency_sign)
            read = self._read_amount(token)
            if not re.fullmatch(r"-?\d+(\.\d+)?", read):
                continue
            if signed or re.fullmatch(r"-?\d+\.\d{2}", read):
                amounts.append(Decimal(read))
        return amounts

    def normalise_text(self, text: str) -> str:
        """Apply the whitespace rule (to a drawn or a ground-truth string alike)."""
        if self.whitespace == "collapse":
            return " ".join(text.split())
        return text

    def _read_amount(self, text: str) -> str:
        negative = False
        words = text.split(" ")
        if len(words) == 2 and words[1] in self.debit_markers:
            negative, text = True, words[0]
        elif len(words) == 2 and words[1] in self.credit_markers:
            text = words[0]
        if text.startswith("-"):
            negative, text = not negative, text[1:]
        if self.currency_sign and text.startswith(self.currency_sign):
            text = text[len(self.currency_sign) :]
        separator = re.escape(self.thousands_separator)
        grouped = re.fullmatch(rf"\d{{1,3}}({separator}\d{{3}})+(\.\d+)?", text)
        if grouped and self.grouping == "allowed":
            text = text.replace(self.thousands_separator, "")
        if not re.fullmatch(r"\d+(\.\d+)?", text):
            return self.normalise_text(text)
        return f"-{text}" if negative else text

    def _read_date(self, text: str, layout_id: str) -> str:
        layout_format = self.layout_formats.get(layout_id)
        if layout_format is None:
            return text
        try:
            return datetime.strptime(text, layout_format).strftime(self.ground_truth_format)
        except ValueError:
            return text


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


@functools.cache
def load_policy() -> DrawnValuePolicy:
    """Load `drawn_value_policy` from config/field_definitions.yml, failing fast on any gap.

    Returns:
        The policy.

    Raises:
        DrawnValuePolicyError: A key is missing, has the wrong type, or an enum holds another value.
    """
    defs = _load_field_defs()
    block = defs.get(POLICY_KEY)
    if not isinstance(block, dict):
        raise _policy_error(POLICY_KEY, f"`{POLICY_KEY}` is {block!r}, not a mapping.")
    for key in ("whitespace", "amounts", "dates"):
        if key not in block:
            raise _policy_error(f"{POLICY_KEY}.{key}", f"`{POLICY_KEY}.{key}` is missing.")
    if block["whitespace"] not in WHITESPACE_MODES:
        raise _policy_error(f"{POLICY_KEY}.whitespace", f"whitespace is {block['whitespace']!r}.")
    amounts, dates = block["amounts"], block["dates"]
    for name, section, keys in (
        (
            "amounts",
            amounts,
            ("currency_sign", "grouping", "thousands_separator", "credit_markers", "debit_markers"),
        ),
        ("dates", dates, ("ground_truth_format", "range_separator", "layout_formats")),
    ):
        if not isinstance(section, dict):
            raise _policy_error(
                f"{POLICY_KEY}.{name}", f"`{POLICY_KEY}.{name}` is {section!r}, not a mapping."
            )
        for key in keys:
            if key not in section:
                raise _policy_error(
                    f"{POLICY_KEY}.{name}.{key}", f"`{POLICY_KEY}.{name}.{key}` is missing."
                )
    if amounts["grouping"] not in GROUPING_MODES:
        raise _policy_error(f"{POLICY_KEY}.amounts.grouping", f"grouping is {amounts['grouping']!r}.")
    for key in ("currency_sign", "thousands_separator"):
        if not isinstance(amounts[key], str) or not amounts[key]:
            raise _policy_error(f"{POLICY_KEY}.amounts.{key}", f"{key} is {amounts[key]!r}, not a string.")
    for key in ("credit_markers", "debit_markers"):
        if not _strings(amounts[key]):
            raise _policy_error(
                f"{POLICY_KEY}.amounts.{key}", f"{key} is {amounts[key]!r}, not a list of words."
            )
    for key in ("ground_truth_format", "range_separator"):
        if not isinstance(dates[key], str) or not dates[key]:
            raise _policy_error(f"{POLICY_KEY}.dates.{key}", f"{key} is {dates[key]!r}, not a string.")
    formats = dates["layout_formats"]
    if not isinstance(formats, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in formats.items()
    ):
        raise _policy_error(
            f"{POLICY_KEY}.dates.layout_formats",
            f"layout_formats is {formats!r}, not a mapping of layout id to strftime format ({{}} for none).",
        )
    types = defs.get("field_types", {})

    def group(names: tuple[str, ...]) -> frozenset[str]:
        return frozenset(field for name in names for field in types.get(name, []))

    return DrawnValuePolicy(
        whitespace=block["whitespace"],
        currency_sign=amounts["currency_sign"],
        grouping=amounts["grouping"],
        thousands_separator=amounts["thousands_separator"],
        credit_markers=tuple(amounts["credit_markers"]),
        debit_markers=tuple(amounts["debit_markers"]),
        ground_truth_format=dates["ground_truth_format"],
        range_separator=dates["range_separator"],
        layout_formats=dict(formats),
        amount_fields=group(AMOUNT_TYPES),
        date_fields=group(DATE_TYPES),
        date_range_fields=group(DATE_RANGE_TYPES),
    )


def policy_path() -> Path:
    """The file the policy is declared in, for diagnostics."""
    return _FIELD_DEFS_PATH.resolve()


__all__ = ["DrawnValuePolicy", "DrawnValuePolicyError", "load_policy", "policy_path"]
