"""The drawn-value policy and the stricter drawn-field guard: the string each box draws must read as its
ground truth, every list member needs a box of its own, and a letterhead exemption is checked."""

import copy
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from generators import drawn_fields, drawn_values
from generators.common import FitError
from generators.drawn_fields import DrawnFieldsCheck, DrawnFieldsError
from generators.drawn_values import DrawnValuePolicyError, load_policy
from generators.schema import validate_entry
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, layout_path_for, layouts_for, renderer_for

DOC_TYPE_KEYS = {"bank_statement": "bank_statements", "receipt": "receipts", "invoice": "invoices"}
LETTERHEAD_LAYOUTS = (
    "anz_modern",
    "anz_standard",
    "cba_date_grouped",
    "cba_standard",
    "westpac_premium",
    "westpac_standard",
)


@pytest.fixture(autouse=True)
def repo_cwd(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The guard and policy read config/field_definitions.yml relative to the repository root."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr("generators.schema._FIELD_DEFS", None)
    load_policy.cache_clear()
    yield
    load_policy.cache_clear()


def _defs() -> dict:
    return yaml.safe_load((REPO_ROOT / "config/field_definitions.yml").read_text())


def _check(doc_type: str, gt: dict) -> DrawnFieldsCheck:
    stem = DOC_TYPE_KEYS[doc_type]
    return DrawnFieldsCheck(
        gt,
        doc_type=stem,
        layout_path=REPO_ROOT / layout_path_for(doc_type),
        ground_truth_path=REPO_ROOT / "ground_truth" / f"{stem}.yml",
    )


def _render(doc_type: str, entry: dict, layouts: dict | None = None) -> dict:
    geometry: dict = {}
    registry = layouts if layouts is not None else layouts_for(doc_type)
    renderer_for(doc_type)(entry, registry[entry["layout"]], distractors=False, geometry_out=geometry)
    return geometry


# --- the policy --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "drawn", "expected"),
    [
        ("TOTAL_AMOUNT", "$1,234.50", "1234.50"),
        ("ACCOUNT_BALANCE", "$5,010.20 CR", "5010.20"),
        ("ACCOUNT_BALANCE", "$12.40 DR", "-12.40"),
        ("ACCOUNT_BALANCE", "3,456.70", "3456.70"),
        ("LINE_ITEM_PRICES", "9.50", "9.50"),
        ("TRANSACTION_DESCRIPTIONS", "EFTPOS  ALPHA\nTOWN AUS", "EFTPOS ALPHA TOWN AUS"),
        ("INVOICE_DATE", "04/05/2031", "04/05/2031"),
    ],
)
def test_the_policy_reads_a_drawn_string_into_ground_truth_form(
    field: str, drawn: str, expected: str
) -> None:
    """Sign, grouping, markers and whitespace are read back; decimals and dates are left as drawn."""
    assert load_policy().read(field, "any_layout", drawn) == expected


def test_decimals_are_never_normalised() -> None:
    """'9.50' does not read as '9.5': a ground truth in another form is refused, not excused."""
    assert load_policy().read("LINE_ITEM_PRICES", "any", "9.50") != "9.5"


def test_grouping_refused_leaves_a_grouped_amount_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    """With grouping refused, '1,234.50' no longer reads as 1234.50, so the grouped page fails."""
    defs = _defs()
    defs["drawn_value_policy"]["amounts"]["grouping"] = "refused"
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    load_policy.cache_clear()
    assert load_policy().read("TOTAL_AMOUNT", "any", "$1,234.50") != "1234.50"


def test_a_layout_date_format_is_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A layout declared to print '%d %b %Y' has its drawn dates parsed in that format, ranges included."""
    defs = _defs()
    defs["drawn_value_policy"]["dates"]["layout_formats"] = {"some_layout": "%d %b %Y"}
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    load_policy.cache_clear()
    policy = load_policy()
    assert policy.read("INVOICE_DATE", "some_layout", "4 May 2031") == "04/05/2031"
    assert policy.read("STATEMENT_DATE_RANGE", "some_layout", "1 May 2031 - 31 May 2031") == (
        "01/05/2031 - 31/05/2031"
    )
    assert policy.read("INVOICE_DATE", "other_layout", "4 May 2031") == "4 May 2031"


@pytest.mark.parametrize(
    "path",
    [
        ("drawn_value_policy",),
        ("drawn_value_policy", "whitespace"),
        ("drawn_value_policy", "amounts", "grouping"),
        ("drawn_value_policy", "amounts", "credit_markers"),
        ("drawn_value_policy", "dates", "layout_formats"),
    ],
)
def test_a_missing_policy_key_fails_fast(path: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """Every policy key is required; a missing one is a four-part refusal naming it."""
    defs = _defs()
    node = defs
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    load_policy.cache_clear()
    with pytest.raises(DrawnValuePolicyError) as caught:
        load_policy()
    assert path[-1] in str(caught.value)
    assert_diagnostic_error(caught.value)


@pytest.mark.parametrize(("key", "value"), [("whitespace", "squash"), ("grouping", "sometimes")])
def test_an_enum_outside_its_values_fails_fast(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`whitespace` and `grouping` accept only their listed values."""
    defs = _defs()
    target = defs["drawn_value_policy"] if key == "whitespace" else defs["drawn_value_policy"]["amounts"]
    target[key] = value
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    load_policy.cache_clear()
    with pytest.raises(DrawnValuePolicyError) as caught:
        load_policy()
    assert_diagnostic_error(caught.value)


# --- the guard compares the drawn string -----------------------------------------------------------


@pytest.mark.parametrize("doc_type", list(DOC_TYPE_KEYS))
def test_corpus_draws_every_value_as_its_ground_truth(doc_type: str) -> None:
    """Every scored value of the shipped corpus is drawn and reads as its ground truth (or is exempt)."""
    gt = corpus_entries(doc_type)
    check = _check(doc_type, gt)
    for case_id, entry in gt.items():
        check(case_id, entry["layout"], _render(doc_type, entry))
    assert check.errors == []


def test_a_wrong_value_in_a_bound_box_is_refused() -> None:
    """A block bound to GST_AMOUNT but drawing TOTAL_AMOUNT records a box, and is still refused."""
    layouts = copy.deepcopy(layouts_for("invoice"))
    layout_id = "tax_invoice_standard"
    swapped = _rebind_value(layouts[layout_id], "GST_AMOUNT", "{TOTAL_AMOUNT}")
    assert swapped >= 1
    gt = {cid: e for cid, e in corpus_entries("invoice").items() if e["layout"] == layout_id}
    check = _check("invoice", gt)
    for case_id, entry in gt.items():
        geometry = _render("invoice", entry, layouts)
        assert "GST_AMOUNT" in geometry["boxes"]
        check(case_id, layout_id, geometry)
    assert len(check.errors) == len(gt)
    for error in check.errors:
        assert "scored field GST_AMOUNT is drawn as a string that is not its ground truth" in error
        assert "drawn_value_policy" in error
        assert_diagnostic_error(error)


def test_a_drifted_balance_is_refused() -> None:
    """Westpac's last running balance is bound to ACCOUNT_BALANCE; if it drifts from the ground truth
    (here the ground truth is moved, which the page then contradicts), the guard refuses it."""
    entry = next(e for e in corpus_entries("bank_statement").values() if e["layout"] == "westpac_standard")
    geometry = _render("bank_statement", entry)
    moved = copy.deepcopy(entry)
    moved["fields"]["ACCOUNT_BALANCE"] = f"{float(entry['fields']['ACCOUNT_BALANCE']) + 1:.2f}"
    check = _check("bank_statement", {entry["case_id"]: moved})
    check(entry["case_id"], entry["layout"], geometry)
    assert len(check.errors) == 1 and "ACCOUNT_BALANCE" in check.errors[0]
    assert_diagnostic_error(check.errors[0])


def test_a_list_member_without_its_own_box_is_refused() -> None:
    """One drawn member no longer stands for the list: each member with a value needs its own box."""
    entry = next(iter(corpus_entries("bank_statement").values()))
    geometry = _render("bank_statement", entry)
    paid = [k for k in geometry["texts"] if k.startswith("TRANSACTION_AMOUNTS_PAID[")]
    assert len(paid) > 1
    dropped = paid[1]
    geometry["texts"] = {k: v for k, v in geometry["texts"].items() if k != dropped}
    check = _check("bank_statement", {entry["case_id"]: entry})
    check(entry["case_id"], entry["layout"], geometry)
    assert len(check.errors) == 1
    assert dropped in check.errors[0] and "does not draw" in check.errors[0]
    assert_diagnostic_error(check.errors[0])


def test_a_labelled_value_records_the_value_alone() -> None:
    """'ABN: {BUSINESS_ABN}' draws as one line, but its box and string hold the ABN only."""
    for doc_type in ("invoice", "receipt"):
        for entry in list(corpus_entries(doc_type).values())[:8]:
            geometry = _render(doc_type, entry)
            assert geometry["texts"]["BUSINESS_ABN"] == entry["fields"]["BUSINESS_ABN"]


def test_a_labelled_value_that_wraps_is_refused() -> None:
    """A label and value that do not fit one line cannot be measured apart, so the render refuses."""
    from PIL import Image, ImageDraw

    from generators.common import draw_fitted_left
    from generators.exporters.geometry import BoxRecorder

    draw = ImageDraw.Draw(Image.new("RGB", (400, 200), "white"))
    budget = {"width": 120, "fit": "wrap", "min_font": 20, "max_lines": 3}
    with pytest.raises(FitError) as caught:
        draw_fitted_left(
            draw,
            "ABN: 11 222 333 444",
            0,
            0,
            budget=budget,
            nominal_size=24,
            family="carlito",
            recorder=BoxRecorder(400, 200),
            field="BUSINESS_ABN",
            label="ABN: ",
        )
    assert_diagnostic_error(caught.value)


# --- exemptions --------------------------------------------------------------------------------------


def test_the_letterhead_exemption_is_checked() -> None:
    """Each letterhead layout's logo_text equals its statements' SUPPLIER_NAME; a changed one is refused."""
    gt = {
        cid: e for cid, e in corpus_entries("bank_statement").items() if e["layout"] in LETTERHEAD_LAYOUTS
    }
    assert {e["layout"] for e in gt.values()} == set(LETTERHEAD_LAYOUTS)
    check = _check("bank_statement", gt)
    for case_id, entry in gt.items():
        check(case_id, entry["layout"], _render("bank_statement", entry))
    assert check.errors == []

    altered = copy.deepcopy(layouts_for("bank_statement"))
    altered["cba_standard"]["logo_text"] = "Southern Cross Savings"
    check = _check("bank_statement", gt)
    check.__dict__["layouts"] = altered
    for case_id, entry in gt.items():
        check(case_id, entry["layout"], _render("bank_statement", entry))
    cba = [cid for cid, e in gt.items() if e["layout"] == "cba_standard"]
    assert len(check.errors) == len(cba) > 0
    for error in check.errors:
        assert "logo_text" in error and "SUPPLIER_NAME" in error
        assert_diagnostic_error(error)


@pytest.mark.parametrize("key", ["members_where", "printed_as", "reason", "layouts"])
def test_every_exemption_key_is_required(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exemption missing any of its four keys is refused; `none` must be written out."""
    defs = _defs()
    del defs["geometry_exemptions"]["INVOICE"]["DOCUMENT_TYPE"][key]
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    with pytest.raises(DrawnFieldsError) as caught:
        drawn_fields.load_exemptions("invoices")
    assert key in str(caught.value)
    assert_diagnostic_error(caught.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("members_where", {"LINE_ITEM_QUANTITY": "1"}),
        ("printed_as", {"layout": "logo_text"}),
        ("printed_as", "logo_text"),
    ],
)
def test_a_malformed_exemption_value_is_refused(
    key: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A members_where naming no field of the type, or a printed_as not {layout_key: KEY}, is refused."""
    defs = _defs()
    defs["geometry_exemptions"]["RECEIPT"]["LINE_ITEM_PRICES"][key] = value
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    with pytest.raises(DrawnFieldsError) as caught:
        drawn_fields.load_exemptions("receipts")
    assert key in str(caught.value)
    assert_diagnostic_error(caught.value)


# --- quantities --------------------------------------------------------------------------------------


@pytest.mark.parametrize("quantity", ["1.0", "01", "2.5"])
def test_a_quantity_not_written_as_a_whole_number_is_refused(quantity: str) -> None:
    """'1.0' is neither treated as one nor silently boxed: validate refuses it, four-part."""
    entry = copy.deepcopy(next(iter(corpus_entries("receipt").values())))
    items = entry["fields"]["LINE_ITEM_QUANTITIES"].split("|")
    items[0] = quantity
    entry["fields"]["LINE_ITEM_QUANTITIES"] = "|".join(items)
    errors = [e for e in validate_entry(entry["case_id"], entry) if "LINE_ITEM_QUANTITIES" in e]
    assert len(errors) == 1 and repr(quantity) in errors[0]
    assert_diagnostic_error(errors[0])


def test_the_corpus_quantities_are_whole_numbers() -> None:
    """No corpus quantity is refused by the whole-number rule."""
    for doc_type in ("receipt", "invoice"):
        for case_id, entry in corpus_entries(doc_type).items():
            assert not [e for e in validate_entry(case_id, entry) if "whole numbers" in e]


def test_the_policy_path_is_the_field_definitions() -> None:
    """Diagnostics name the absolute path the policy is declared in."""
    assert drawn_values.policy_path() == (REPO_ROOT / "config/field_definitions.yml").resolve()


def _rebind_value(node: object, field: str, template: str) -> int:
    """Point every block bound to `field` at another template, leaving its binding; return the count."""
    count = 0
    if isinstance(node, dict):
        if node.get("field") == field:
            key = "value" if "value" in node else "content"
            node[key] = template
            count += 1
        for child in node.values():
            count += _rebind_value(child, field, template)
    elif isinstance(node, list):
        for child in node:
            count += _rebind_value(child, field, template)
    return count


def test_layouts_are_untouched_on_disk() -> None:
    """The tests above work on deep copies; the tracked layout file keeps its bindings."""
    text = Path(REPO_ROOT / "config/layouts/invoices.yml").read_text()
    assert 'value: "{GST_AMOUNT}"' in text
