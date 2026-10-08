"""Row providers turn ground-truth fields into table rows, computing what ground truth omits."""

from decimal import Decimal
from typing import cast

import pytest

from generators.layout_dsl import providers
from generators.layout_dsl.providers import (
    ProviderError,
    bank_transaction_totals,
    bank_transactions,
    get_provider,
    pipe_fields,
    provider_names,
    receipt_line_items,
    row_provider,
)
from tests.conftest import assert_diagnostic_error

# Invented values: CASE999 and the 2031 dates appear nowhere in ground_truth/.
BANK_ENTRY: dict[str, str | dict[str, str]] = {
    "case_id": "CASE999",
    "fields": {
        "TRANSACTION_DATES": "01/01/2031|02/01/2031|03/01/2031",
        "TRANSACTION_DESCRIPTIONS": "Alpha Widgets|Beta Gadgets|Gamma Gizmos",
        "TRANSACTION_AMOUNTS_PAID": "10.00|NOT_FOUND|5.50",
        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|20.00|NOT_FOUND",
        "ACCOUNT_BALANCE": "100.00",
    },
}

RECEIPT_ENTRY: dict[str, str | dict[str, str]] = {
    "case_id": "CASE999",
    "fields": {
        "LINE_ITEM_DESCRIPTIONS": "Zeta Bolts|Eta Nuts|Theta Washers",
        "LINE_ITEM_QUANTITIES": "1|3|2",
        "LINE_ITEM_PRICES": "9.5|2.00|NOT_FOUND",
        "LINE_ITEM_TOTAL_PRICES": "9.5|6.00|",
    },
}
RECEIPT_PARAMS = {
    "fields": {
        "description": "LINE_ITEM_DESCRIPTIONS",
        "quantity": "LINE_ITEM_QUANTITIES",
        "price": "LINE_ITEM_PRICES",
        "total": "LINE_ITEM_TOTAL_PRICES",
    },
    "unit_line_format": "  {quantity} @ ${price} ea",
}

LEADING_LABELS = {"brought_forward": True, "brought_forward_label": "Carry In"}


def _bank(params: dict | None = None, entry: dict | None = None) -> list[dict]:
    return bank_transactions(entry or BANK_ENTRY, params or {})


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give a test private copies of the provider registries, so registrations do not leak."""
    monkeypatch.setattr(providers, "_REGISTRY", dict(providers._REGISTRY))
    monkeypatch.setattr(providers, "_PARAM_KEYS", dict(providers._PARAM_KEYS))


# --- pipe_fields and the registry -------------------------------------------------------------


def test_pipe_fields_zips_list_fields_into_rows() -> None:
    """Each pipe-delimited source field becomes one key, one row per list position."""
    rows = pipe_fields(
        BANK_ENTRY,
        {"fields": {"when": "TRANSACTION_DATES", "what": "TRANSACTION_DESCRIPTIONS"}},
    )
    assert rows == [
        {"when": "01/01/2031", "what": "Alpha Widgets"},
        {"when": "02/01/2031", "what": "Beta Gadgets"},
        {"when": "03/01/2031", "what": "Gamma Gizmos"},
    ]


def test_pipe_fields_rejects_ragged_lists() -> None:
    """Source lists of differing length fail with a four-part diagnostic naming the lengths."""
    entry = {"fields": {"A": "1|2|3", "B": "1|2"}}
    with pytest.raises(ProviderError) as exc_info:
        pipe_fields(entry, {"fields": {"a": "A", "b": "B"}})
    assert "differ in length" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_pipe_fields_requires_fields_mapping() -> None:
    """A missing or empty `fields` mapping is a diagnostic, not an empty table."""
    for params in cast(list[dict[str, object]], ({}, {"fields": {}}, {"fields": ["A"]})):
        with pytest.raises(ProviderError) as exc_info:
            pipe_fields(BANK_ENTRY, params)
        assert "'fields' mapping" in str(exc_info.value)
        assert_diagnostic_error(exc_info.value)


def test_get_provider_rejects_unknown_name_and_lists_known() -> None:
    """An unknown provider name raises, naming the offender and listing the registered names."""
    with pytest.raises(ProviderError) as exc_info:
        get_provider("no_such_provider")
    message = str(exc_info.value)
    assert "no_such_provider" in message
    for name in provider_names():
        assert name in message


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: get_provider's unknown-name diagnostic labels its fix line 'Remediation:' instead of 'Recover:' (providers.py:80-86), so it lacks the four-part form",
)
def test_get_provider_rejects_unknown_name_and_lists_known_bug() -> None:
    """The unknown-provider error should carry the four-part diagnostic labels."""
    with pytest.raises(ProviderError) as exc_info:
        get_provider("no_such_provider")
    assert_diagnostic_error(exc_info.value)


def test_provider_names_includes_registered() -> None:
    """`provider_names` is sorted and lists every built-in provider."""
    names = provider_names()
    assert names == sorted(names)
    assert {"pipe_fields", "bank_transactions", "bank_transaction_totals", "receipt_line_items"} <= set(
        names
    )


def test_row_provider_rejects_duplicate_registration(scratch_registry: None) -> None:
    """Registering a second provider under an existing name raises rather than overwriting."""
    original = get_provider("pipe_fields")
    with pytest.raises(ProviderError) as exc_info:
        row_provider("pipe_fields")(lambda entry, params: [])
    assert "already registered" in str(exc_info.value)
    assert get_provider("pipe_fields") is original


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: the duplicate-registration ProviderError carries only a 'Remediation:' line, no What/Where/Expected/Recover labels (providers.py:53-58)",
)
def test_row_provider_rejects_duplicate_registration_bug(scratch_registry: None) -> None:
    """The duplicate-registration error should carry the four-part diagnostic labels."""
    with pytest.raises(ProviderError) as exc_info:
        row_provider("pipe_fields")(lambda entry, params: [])
    assert_diagnostic_error(exc_info.value)


# --- bank_transactions ------------------------------------------------------------------------


def test_bank_transactions_computes_running_balances_backwards() -> None:
    """The last row carries ACCOUNT_BALANCE; earlier rows undo each later movement."""
    rows = _bank()
    assert [r["balance"] for r in rows] == [Decimal("85.5"), Decimal("105.5"), Decimal("100.00")]
    assert rows[0]["debit"] == Decimal("10.00")
    assert rows[0]["credit"] == "NOT_FOUND"
    assert rows[1]["credit"] == Decimal("20.00")


def test_bank_transactions_prepends_opening_row_when_requested() -> None:
    """`opening_balance` inserts a synthetic first row holding the balance before the first transaction."""
    rows = _bank({"opening_balance": True, "opening_balance_label": "Opening Balance"})
    assert len(rows) == 4
    first = rows[0]
    assert first["description"] == "Opening Balance"
    assert first["synthetic"] is True
    assert first["balance"] == Decimal("95.5")
    assert first["debit"] == first["credit"] == "NOT_FOUND"
    assert first["date"] == ""
    # The opening balance, less the first debit, is the first real row's balance.
    assert first["balance"] - rows[1]["debit"] == rows[1]["balance"]


def test_bank_transactions_brought_forward_uses_its_own_label() -> None:
    """`brought_forward` prepends the same computed row, printing its own label."""
    rows = _bank({"brought_forward": True, "brought_forward_label": "Carry In"})
    assert rows[0]["description"] == "Carry In"
    assert rows[0]["balance"] == Decimal("95.5")
    assert rows[0]["synthetic"] is True


def test_bank_transactions_rejects_both_synthetic_rows() -> None:
    """`opening_balance` and `brought_forward` are mutually exclusive leading rows."""
    with pytest.raises(ProviderError) as exc_info:
        _bank({"opening_balance": True, "brought_forward": True})
    assert "mutually exclusive" in str(exc_info.value)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BUG: the mutually-exclusive-leading-rows ProviderError carries only a 'Remediation:' line, no What/Where/Expected/Recover labels (providers.py:331-335)",
)
def test_bank_transactions_rejects_both_synthetic_rows_bug() -> None:
    """The mutual-exclusion error should carry the four-part diagnostic labels."""
    with pytest.raises(ProviderError) as exc_info:
        _bank({"opening_balance": True, "brought_forward": True})
    assert_diagnostic_error(exc_info.value)


def test_bank_transactions_without_synthetic_row_marks_all_real() -> None:
    """With no synthetic option every row is flagged real and none is bold."""
    rows = _bank()
    assert len(rows) == 3
    assert all(r["synthetic"] is False for r in rows)
    assert all("bold" not in r for r in rows)


def test_malformed_amount_fails_loudly_rather_than_becoming_zero() -> None:
    """A non-numeric transaction amount raises a four-part diagnostic instead of reading as zero."""
    entry = {
        "fields": {
            **cast(dict[str, str], BANK_ENTRY["fields"]),
            "TRANSACTION_AMOUNTS_PAID": "10.00|NOT_FOUND|abc",
        }
    }
    with pytest.raises(ProviderError) as exc_info:
        _bank(entry=entry)
    assert "'abc'" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_absent_value_sentinels_still_read_as_zero() -> None:
    """`NOT_FOUND` and the empty string are absent values and contribute nothing to balances."""
    fields = {**cast(dict[str, str], BANK_ENTRY["fields"]), "TRANSACTION_AMOUNTS_PAID": "10.00||5.50"}
    rows = _bank(entry={"fields": fields})
    assert rows[1]["balance"] == Decimal("105.5")
    # A missing closing balance is zero, so the last row's balance is zero.
    no_balance = {"fields": {**cast(dict[str, str], BANK_ENTRY["fields"]), "ACCOUNT_BALANCE": "NOT_FOUND"}}
    assert _bank(entry=no_balance)[-1]["balance"] == Decimal("0")


def test_carried_forward_appends_a_trailing_synthetic_row() -> None:
    """`carried_forward` appends a synthetic row repeating the closing balance."""
    rows = _bank({"carried_forward": True, "carried_forward_label": "Carry Out"})
    assert len(rows) == 4
    last = rows[-1]
    assert last["description"] == "Carry Out"
    assert last["synthetic"] is True
    assert last["balance"] == Decimal("100.00")
    assert last["debit"] == last["credit"] == "NOT_FOUND"


def test_carried_forward_row_is_bold_unlike_leading_synthetic_rows() -> None:
    """Legacy draws Carried forward in font_body_bold; Brought forward/Opening Balance stay regular."""
    rows = _bank({**LEADING_LABELS, "carried_forward": True, "carried_forward_label": "Carry Out"})
    assert rows[-1]["bold"] is True
    assert "bold" not in rows[0]


def test_carried_forward_combines_with_a_leading_synthetic_row() -> None:
    """NAB shows both: Brought-forward leading, Carried-forward trailing."""
    rows = _bank({**LEADING_LABELS, "carried_forward": True, "carried_forward_label": "Carry Out"})
    assert len(rows) == 5
    assert rows[0]["description"] == "Carry In"
    assert rows[-1]["description"] == "Carry Out"
    assert [r["synthetic"] for r in rows] == [True, False, False, False, True]
    # The trailing row repeats the real closing balance, not the shifted leading row's.
    assert rows[-1]["balance"] == Decimal("100.00")


def test_carried_forward_without_transactions_appends_nothing() -> None:
    """With no transactions there is no closing row to carry, so nothing is appended."""
    entry = {"fields": {"ACCOUNT_BALANCE": "100.00"}}
    assert _bank({"carried_forward": True, "carried_forward_label": "Carry Out"}, entry) == []


REFERENCE_PARAMS: dict[str, bool | str | int] = {
    "references": True,
    "reference_prefix": "Ref: ",
    "reference_pad_char": ".",
    "reference_pad_width": 4,
}


def test_references_adds_a_reference_key_to_every_real_row() -> None:
    """`references` adds prefix, a zero-padded 10-digit hash and the leader run to each real row."""
    rows = _bank(REFERENCE_PARAMS)
    for row in rows:
        ref = row["reference"]
        assert ref.startswith("Ref: ")
        assert ref.endswith("....")
        digits = ref[len("Ref: ") : -4]
        assert len(digits) == 10 and digits.isdigit()
    # Deterministic per description, distinct across descriptions.
    assert [r["reference"] for r in rows] == [r["reference"] for r in _bank(REFERENCE_PARAMS)]
    assert len({r["reference"] for r in rows}) == 3


def test_references_absent_by_default() -> None:
    """Without `references` no row carries a `reference` key."""
    assert all("reference" not in r for r in _bank())


def test_references_not_added_to_synthetic_rows() -> None:
    """Synthetic leading and trailing rows get no reference."""
    rows = _bank(
        {**REFERENCE_PARAMS, **LEADING_LABELS, "carried_forward": True, "carried_forward_label": "Out"}
    )
    for row in rows:
        assert ("reference" in row) is (not row["synthetic"])


def test_brought_forward_label_is_printed_verbatim() -> None:
    """The layout's `brought_forward_label` text is used exactly, including case."""
    rows = _bank({"brought_forward": True, "brought_forward_label": "BALANCE BROUGHT FORWARD"})
    assert rows[0]["description"] == "BALANCE BROUGHT FORWARD"


def test_opening_balance_label_is_printed_verbatim() -> None:
    """The layout's `opening_balance_label` text is used exactly, including case."""
    rows = _bank({"opening_balance": True, "opening_balance_label": "oPeNiNg bAlAnCe"})
    assert rows[0]["description"] == "oPeNiNg bAlAnCe"


def test_differing_labels_leave_the_computed_balance_untouched() -> None:
    """Labels decide only the printed text; the leading balance is identical whatever the label."""
    a = _bank({"opening_balance": True, "opening_balance_label": "One"})[0]
    b = _bank({"brought_forward": True, "brought_forward_label": "Two"})[0]
    assert a["balance"] == b["balance"] == Decimal("95.5")


def test_printed_text_params_are_required_not_defaulted() -> None:
    """Every string this provider prints comes from the layout, or it fails fast.

    Each of these six params decides text on a rendered bank statement page. A Python fallback would
    put the decision back in this module, so the provider refuses to guess.
    """
    cases = cast(
        list[tuple[str, dict[str, object]]],
        [
            ("opening_balance_label", {"opening_balance": True}),
            ("brought_forward_label", {"brought_forward": True}),
            ("carried_forward_label", {"carried_forward": True}),
            ("reference_prefix", {k: v for k, v in REFERENCE_PARAMS.items() if k != "reference_prefix"}),
            (
                "reference_pad_char",
                {k: v for k, v in REFERENCE_PARAMS.items() if k != "reference_pad_char"},
            ),
            (
                "reference_pad_width",
                {k: v for k, v in REFERENCE_PARAMS.items() if k != "reference_pad_width"},
            ),
        ],
    )
    for missing_key, params in cases:
        with pytest.raises(ProviderError) as exc_info:
            _bank(params)
        assert f"'{missing_key}'" in str(exc_info.value), missing_key
        assert_diagnostic_error(exc_info.value)


SUFFIX = {"debit": "DR", "credit": "CR"}


def test_balance_suffix_formats_a_positive_balance_with_the_credit_suffix() -> None:
    """A non-negative balance prints as the `$`-formatted amount plus the credit suffix."""
    rows = _bank({"balance_suffix": SUFFIX})
    assert rows[-1]["balance"] == "$100.00 CR"
    assert rows[1]["balance"] == "$105.50 CR"


def test_balance_suffix_formats_a_negative_balance_with_the_debit_suffix_and_abs_value() -> None:
    """A negative balance prints its absolute value plus the debit suffix."""
    entry = {"fields": {**cast(dict[str, str], BANK_ENTRY["fields"]), "ACCOUNT_BALANCE": "-40.00"}}
    rows = _bank({"balance_suffix": SUFFIX}, entry)
    assert rows[-1]["balance"] == "$40.00 DR"
    assert rows[1]["balance"] == "$34.50 DR"
    assert rows[0]["balance"] == "$54.50 DR"


def test_balance_suffix_applies_to_a_leading_synthetic_row_too() -> None:
    """The suffix is applied after synthetic rows are built, so the leading row is formatted as well."""
    rows = _bank({**LEADING_LABELS, "balance_suffix": SUFFIX})
    assert rows[0]["synthetic"] is True
    assert rows[0]["balance"] == "$95.50 CR"


def test_balance_suffix_absent_by_default_leaves_balance_as_decimal() -> None:
    """Without `balance_suffix` every balance stays a Decimal."""
    assert all(isinstance(r["balance"], Decimal) for r in _bank(LEADING_LABELS))


# --- bank_transaction_totals ------------------------------------------------------------------


def test_bank_transaction_totals_sums_debits_and_credits() -> None:
    """The single row's debit and credit are the column sums, as Decimals."""
    (row,) = bank_transaction_totals(BANK_ENTRY, {"label": "Totals"})
    assert row["debit"] == Decimal("15.50")
    assert row["credit"] == Decimal("20.00")


def test_bank_transaction_totals_row_is_synthetic_bold_and_ruled_above() -> None:
    """The totals row is never recorded, is drawn bold, and is ruled above."""
    (row,) = bank_transaction_totals(BANK_ENTRY, {"label": "Totals"})
    assert row["synthetic"] is True
    assert row["bold"] is True
    assert row["rule_above"] is True
    assert row["date"] == ""
    assert "balance" not in row


def test_bank_transaction_totals_label_overrides_the_default() -> None:
    """The description is exactly the label the layout supplies."""
    (row,) = bank_transaction_totals(BANK_ENTRY, {"label": "Grand Sum 2031"})
    assert row["description"] == "Grand Sum 2031"


def test_bank_transaction_totals_ignores_not_found_amounts() -> None:
    """Absent amounts contribute zero rather than failing the sum."""
    entry = {
        "fields": {
            "TRANSACTION_AMOUNTS_PAID": "NOT_FOUND|NOT_FOUND",
            "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND|3.25",
        }
    }
    (row,) = bank_transaction_totals(entry, {"label": "Totals"})
    assert row["debit"] == Decimal("0")
    assert row["credit"] == Decimal("3.25")


def test_empty_transaction_list_with_synthetic_row_requested_returns_no_rows() -> None:
    """With no transactions, a requested leading row has no first row to derive from, so none appears."""
    entry = {"fields": {"ACCOUNT_BALANCE": "100.00"}}
    assert _bank({"opening_balance": True, "opening_balance_label": "Opening"}, entry) == []


def test_bank_transaction_totals_label_must_come_from_yaml() -> None:
    """A missing `label` is a four-part diagnostic; there is no Python default text."""
    with pytest.raises(ProviderError) as exc_info:
        bank_transaction_totals(BANK_ENTRY, {})
    assert "'label'" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


# --- receipt_line_items -----------------------------------------------------------------------


def test_unit_line_is_built_only_above_one() -> None:
    """A quantity of 1 gets no unit line; above 1, the line holds both ground-truth strings verbatim."""
    entry = {
        "fields": {**cast(dict[str, str], RECEIPT_ENTRY["fields"]), "LINE_ITEM_PRICES": "9.50|2.00|3.10"}
    }
    rows = receipt_line_items(entry, RECEIPT_PARAMS)
    assert [r["unit_line"] for r in rows] == ["", "  3 @ $2.00 ea", "  2 @ $3.10 ea"]
    assert [r["description"] for r in rows] == ["Zeta Bolts", "Eta Nuts", "Theta Washers"]


def test_unit_line_spans_locate_each_value() -> None:
    """Each span slices exactly its ground-truth value out of the unit line."""
    rows = receipt_line_items(RECEIPT_ENTRY, RECEIPT_PARAMS)
    line, spans = rows[1]["unit_line"], rows[1]["unit_line_spans"]
    assert line[slice(*spans["quantity"])] == "3"
    assert line[slice(*spans["price"])] == "2.00"
    assert rows[0]["unit_line_spans"] == {}


def test_unit_line_is_omitted_without_a_price() -> None:
    """An absent unit price prints no unit line (the drawn-field guard then refuses the unboxed quantity)."""
    rows = receipt_line_items(RECEIPT_ENTRY, RECEIPT_PARAMS)
    assert rows[2]["unit_line"] == ""


@pytest.mark.parametrize("price", ["2.5", "2.005"])
def test_a_unit_price_not_in_printed_form_is_refused(price: str) -> None:
    """A price whose printed form does not read back as its ground-truth string fails four-part."""
    entry = {
        "case_id": "CASE999",
        "fields": {
            **cast(dict[str, str], RECEIPT_ENTRY["fields"]),
            "LINE_ITEM_PRICES": f"9.50|{price}|3.10",
        },
    }
    with pytest.raises(ProviderError) as exc_info:
        receipt_line_items(entry, RECEIPT_PARAMS)
    assert repr(price) in str(exc_info.value)
    assert "CASE999.fields.LINE_ITEM_PRICES" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_a_unit_price_of_a_thousand_or_more_prints_grouped() -> None:
    """Grouping is allowed by drawn_value_policy, so 1234.50 prints as every receipt amount does,
    `1,234.50`, and its span slices exactly that printed form."""
    entry = {
        "case_id": "CASE999",
        "fields": {
            **cast(dict[str, str], RECEIPT_ENTRY["fields"]),
            "LINE_ITEM_PRICES": "9.50|1234.50|3.10",
        },
    }
    rows = receipt_line_items(entry, RECEIPT_PARAMS)
    line, spans = rows[1]["unit_line"], rows[1]["unit_line_spans"]
    assert line[slice(*spans["price"])] == "1,234.50"
    assert "$1,234.50" in line


@pytest.mark.parametrize(
    "template",
    ["{quantity} @", "{quantity} @ {price} {price}", "{quantity} @ {price:>6}", "{qty} @ {price}"],
)
def test_a_malformed_unit_line_format_is_refused(template: str) -> None:
    """The format must name {quantity} and {price} once each, undecorated."""
    with pytest.raises(ProviderError) as exc_info:
        receipt_line_items(RECEIPT_ENTRY, {**RECEIPT_PARAMS, "unit_line_format": template})
    assert "unit_line_format" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_amount_columns_are_coerced_to_decimal() -> None:
    """The legacy renderer printed f"{Decimal(total):,.2f}", not the raw string, so a one-decimal-place
    amount must not render as `9.5`."""
    rows = receipt_line_items(RECEIPT_ENTRY, RECEIPT_PARAMS)
    assert rows[0]["price"] == Decimal("9.5")
    assert isinstance(rows[0]["price"], Decimal)
    assert rows[1]["total"] == Decimal("6.00")
    assert isinstance(rows[1]["total"], Decimal)


def test_absent_amounts_are_left_as_sentinels() -> None:
    """`_cell_text` maps both to a cell that draws nothing; coercing them to Decimal("0") would print a
    fabricated $0.00."""
    rows = receipt_line_items(RECEIPT_ENTRY, RECEIPT_PARAMS)
    assert rows[2]["price"] == "NOT_FOUND"
    assert rows[2]["total"] == ""


def test_malformed_amount_is_a_diagnostic_not_a_silent_zero() -> None:
    """A malformed price names a receipt line item and raises a four-part diagnostic."""
    entry = {
        "fields": {
            **cast(dict[str, str], RECEIPT_ENTRY["fields"]),
            "LINE_ITEM_PRICES": "9.5|oops|NOT_FOUND",
        }
    }
    with pytest.raises(ProviderError) as exc_info:
        receipt_line_items(entry, RECEIPT_PARAMS)
    assert "a receipt line item" in str(exc_info.value)
    assert "'oops'" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_missing_unit_line_format_fails_diagnostically() -> None:
    """`unit_line_format` has no Python default: omitting it is a four-part diagnostic."""
    params = {"fields": RECEIPT_PARAMS["fields"]}
    with pytest.raises(ProviderError) as exc_info:
        receipt_line_items(RECEIPT_ENTRY, params)
    assert "unit_line_format" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_missing_fields_delegates_validation_to_pipe_fields() -> None:
    """Verify that missing 'fields' delegates to pipe_fields for diagnostic error."""
    with pytest.raises(ProviderError) as exc_info:
        receipt_line_items(RECEIPT_ENTRY, {"unit_line_format": "{quantity} @ {price}"})
    assert "'fields' mapping" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


# --- pipe_fields decimal_keys -----------------------------------------------------------------

DECIMAL_ENTRY = {"fields": {"NAMES": "Iota|Kappa|Lambda", "AMOUNTS": "9.5|NOT_FOUND|"}}
DECIMAL_FIELDS = {"name": "NAMES", "amount": "AMOUNTS"}


def test_decimal_keys_coerce_named_columns_to_decimal() -> None:
    """A key listed in `decimal_keys` becomes a Decimal, so the table formats it as currency."""
    rows = pipe_fields(DECIMAL_ENTRY, {"fields": DECIMAL_FIELDS, "decimal_keys": ["amount"]})
    assert rows[0]["amount"] == Decimal("9.5")
    assert isinstance(rows[0]["amount"], Decimal)


def test_decimal_keys_leave_unnamed_columns_and_sentinels_alone() -> None:
    """Columns not named stay strings, and the absent sentinel and empty string are not coerced."""
    rows = pipe_fields(DECIMAL_ENTRY, {"fields": DECIMAL_FIELDS, "decimal_keys": ["amount"]})
    assert [r["name"] for r in rows] == ["Iota", "Kappa", "Lambda"]
    assert rows[1]["amount"] == "NOT_FOUND"
    assert rows[2]["amount"] == ""


def test_absent_decimal_keys_changes_nothing() -> None:
    """Without `decimal_keys` every value stays the raw ground-truth string."""
    rows = pipe_fields(DECIMAL_ENTRY, {"fields": DECIMAL_FIELDS})
    assert rows[0]["amount"] == "9.5"
    assert all(isinstance(v, str) for r in rows for v in r.values())


def test_decimal_keys_naming_an_unmapped_row_key_fails_diagnostically() -> None:
    """A typo here would otherwise silently coerce nothing, indistinguishable from the amounts having
    been drawn correctly all along."""
    with pytest.raises(ProviderError) as exc_info:
        pipe_fields(DECIMAL_ENTRY, {"fields": DECIMAL_FIELDS, "decimal_keys": ["amuont"]})
    assert "amuont" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)


def test_malformed_decimal_key_amount_names_its_source_field() -> None:
    """A bad amount under a decimal key names the ground-truth list it came from."""
    entry = {"fields": {"NAMES": "Iota|Kappa", "AMOUNTS": "9.5|junk"}}
    with pytest.raises(ProviderError) as exc_info:
        pipe_fields(entry, {"fields": DECIMAL_FIELDS, "decimal_keys": ["amount"]})
    assert "AMOUNTS" in str(exc_info.value)
    assert "'junk'" in str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
