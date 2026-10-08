"""Receipt unit lines and the Westpac Balance column: the scored values they print, and the guard over them."""

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw, ImageStat

from generators import drawn_fields
from generators.common import fmt_amount
from generators.drawn_fields import DrawnFieldsCheck, DrawnFieldsError
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, layout_path_for, layouts_for, renderer_for

WESTPAC_LAYOUTS = ("westpac_premium", "westpac_standard")
DOC_TYPE_KEYS = {"bank_statement": "bank_statements", "receipt": "receipts"}


@pytest.fixture(autouse=True)
def repo_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard reads config/field_definitions.yml relative to the repository root."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr("generators.schema._FIELD_DEFS", None)


def _check(doc_type: str, gt: dict) -> DrawnFieldsCheck:
    stem = DOC_TYPE_KEYS[doc_type]
    return DrawnFieldsCheck(
        gt,
        doc_type=stem,
        layout_path=REPO_ROOT / layout_path_for(doc_type),
        ground_truth_path=REPO_ROOT / "ground_truth" / f"{stem}.yml",
    )


def _run(check: DrawnFieldsCheck, doc_type: str, layouts: dict, gt: dict) -> None:
    for case_id, entry in gt.items():
        geometry: dict = {}
        renderer_for(doc_type)(
            {**entry, "case_id": case_id},
            layouts[entry["layout"]],
            distractors=False,
            geometry_out=geometry,
        )
        check(case_id, entry["layout"], geometry)


def _quantities(entry: dict) -> list[str]:
    return [q.strip() for q in entry["fields"]["LINE_ITEM_QUANTITIES"].split("|")]


def _multi_unit_receipts() -> dict:
    return {cid: e for cid, e in corpus_entries("receipt").items() if any(q != "1" for q in _quantities(e))}


def _westpac() -> dict:
    return {cid: e for cid, e in corpus_entries("bank_statement").items() if e["layout"] in WESTPAC_LAYOUTS}


def _pixel_box(geometry: dict, key: str) -> tuple[int, int, int, int]:
    left, top, right, bottom = geometry["boxes"][key]
    w, h = geometry["width"], geometry["height"]
    return (int(left * w), int(top * h), int(right * w) + 1, int(bottom * h) + 1)


def _has_ink(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    return ImageStat.Stat(image.convert("L").crop(box)).extrema[0][0] < 160


def _render_spied(
    doc_type: str, entry: dict, monkeypatch: pytest.MonkeyPatch
) -> tuple[Image.Image, dict, list]:
    """Render plain, recording every string drawn."""
    drawn: list[str] = []
    original = ImageDraw.ImageDraw.text

    def spy(self: ImageDraw.ImageDraw, xy: object, text: str, *args: object, **kwargs: object) -> object:
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    geometry: dict = {}
    image = renderer_for(doc_type)(
        entry, layouts_for(doc_type)[entry["layout"]], distractors=False, geometry_out=geometry
    )
    return image, geometry, drawn


def _strip_span(node: object, span: str) -> int:
    """Remove the `span` entry from every sub-line `span_fields.fields` below `node`; return how many."""
    removed = 0
    if isinstance(node, dict):
        fields = (
            node.get("span_fields", {}).get("fields") if isinstance(node.get("span_fields"), dict) else None
        )
        if isinstance(fields, dict) and span in fields:
            del fields[span]
            removed += 1
        for child in node.values():
            removed += _strip_span(child, span)
    elif isinstance(node, list):
        for child in node:
            removed += _strip_span(child, span)
    return removed


def _strip_last_row_field(node: object, field: str) -> int:
    removed = 0
    if isinstance(node, dict):
        if node.get("last_row_field") == field:
            del node["last_row_field"]
            removed += 1
        for child in node.values():
            removed += _strip_last_row_field(child, field)
    elif isinstance(node, list):
        for child in node:
            removed += _strip_last_row_field(child, field)
    return removed


# --- scope -----------------------------------------------------------------------------------------


def test_scope_of_the_two_defects() -> None:
    """46 receipts have a line of more than one unit; 14 statements are on a Westpac layout."""
    assert len(_multi_unit_receipts()) == 46
    assert len(_westpac()) == 14


def test_the_two_exemptions_are_gone() -> None:
    """Westpac's ACCOUNT_BALANCE is not exempt; receipt prices and quantities are exempt only at quantity 1."""
    bank = drawn_fields.load_exemptions("bank_statements")
    assert "ACCOUNT_BALANCE" not in bank
    receipt = drawn_fields.load_exemptions("receipts")
    for field in ("LINE_ITEM_PRICES", "LINE_ITEM_QUANTITIES"):
        assert receipt[field]["members_where"] == {"LINE_ITEM_QUANTITIES": "1"}


# --- the guard -------------------------------------------------------------------------------------


def test_guard_fails_on_receipts_without_the_unit_price_binding() -> None:
    """With the price span unbound on every receipt layout, each multi-unit receipt is refused, four-part."""
    layouts = copy.deepcopy(layouts_for("receipt"))
    assert sum(_strip_span(layouts[lid]["body"], "price") for lid in layouts) >= 1
    gt = _multi_unit_receipts()
    check = _check("receipt", gt)
    _run(check, "receipt", layouts, gt)
    refused = [e for e in check.errors if "scored field LINE_ITEM_PRICES" in e]
    assert len(refused) == 46
    assert len(check.errors) == 46
    for error in refused:
        assert_diagnostic_error(error)
        assert str((REPO_ROOT / "config/layouts/receipts.yml").resolve()) in error
        assert str((REPO_ROOT / "ground_truth/receipts.yml").resolve()) in error
        assert "LINE_ITEM_PRICES[" in error


def test_guard_names_only_the_members_above_one() -> None:
    """A refusal names exactly the members whose quantity exceeds 1."""
    layouts = copy.deepcopy(layouts_for("receipt"))
    for lid in layouts:
        _strip_span(layouts[lid]["body"], "quantity")
    gt = _multi_unit_receipts()
    check = _check("receipt", gt)
    _run(check, "receipt", layouts, gt)
    by_case = {e.split(":", 1)[0]: e for e in check.errors}
    assert len(by_case) == 46
    for case_id, error in by_case.items():
        for i, qty in enumerate(_quantities(gt[case_id])):
            assert (f"LINE_ITEM_QUANTITIES[{i}]" in error) == (qty != "1"), (case_id, i)


def test_guard_fails_on_westpac_without_the_balance_binding() -> None:
    """With the Balance column unbound, all 14 Westpac statements are refused, four-part."""
    layouts = copy.deepcopy(layouts_for("bank_statement"))
    assert (
        sum(_strip_last_row_field(layouts[lid]["body"], "ACCOUNT_BALANCE") for lid in WESTPAC_LAYOUTS) >= 1
    )
    gt = _westpac()
    check = _check("bank_statement", gt)
    _run(check, "bank_statement", layouts, gt)
    assert len(check.errors) == 14
    for error in check.errors:
        assert_diagnostic_error(error)
        assert "ACCOUNT_BALANCE" in error
        assert "westpac_" in error
        assert str((REPO_ROOT / "config/layouts/bank_statements.yml").resolve()) in error


def test_guard_passes_both_corpora() -> None:
    """With the bindings in place, neither corpus has a refusal."""
    for doc_type in DOC_TYPE_KEYS:
        gt = corpus_entries(doc_type)
        check = _check(doc_type, gt)
        _run(check, doc_type, layouts_for(doc_type), gt)
        assert check.errors == [], doc_type


def test_member_scoping_on_a_hand_built_geometry() -> None:
    """Quantity-1 members may go unboxed; any other member with a value may not (invented values)."""
    gt = {
        "CASE999": {
            "layout": "receipt_thermal_57mm",
            "fields": {"LINE_ITEM_QUANTITIES": "1|2|3", "LINE_ITEM_PRICES": "4.00|2.10|NOT_FOUND"},
        }
    }
    check = _check("receipt", gt)
    boxes = {"LINE_ITEM_QUANTITIES[1]": [0, 0, 1, 1], "LINE_ITEM_QUANTITIES[2]": [0, 0, 1, 1]}
    texts = {"LINE_ITEM_QUANTITIES[1]": "2", "LINE_ITEM_QUANTITIES[2]": "3"}
    check("CASE999", "receipt_thermal_57mm", {"boxes": boxes, "texts": texts})
    prices = [e for e in check.errors if "LINE_ITEM_PRICES" in e]
    assert len(prices) == 1 and "LINE_ITEM_PRICES[1]" in prices[0]
    assert "LINE_ITEM_PRICES[0]" not in prices[0] and "LINE_ITEM_PRICES[2]" not in prices[0]
    assert not [e for e in check.errors if "scored field LINE_ITEM_QUANTITIES" in e]
    assert_diagnostic_error(prices[0])


@pytest.mark.parametrize("where", [{}, {"LINE_ITEM_QUANTITIES": 1}, "LINE_ITEM_QUANTITIES"])
def test_malformed_members_where_is_refused(where: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """`members_where` must be a non-empty mapping of field to string value."""
    defs = yaml.safe_load((REPO_ROOT / "config/field_definitions.yml").read_text())
    defs["geometry_exemptions"]["RECEIPT"]["LINE_ITEM_PRICES"]["members_where"] = where
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    with pytest.raises(DrawnFieldsError) as caught:
        drawn_fields.load_exemptions("receipts")
    assert "members_where" in str(caught.value)
    assert_diagnostic_error(caught.value)


# --- what is printed -------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(_multi_unit_receipts()))
def test_every_unit_line_prints_the_ground_truth(case_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each line of more than one unit draws a unit line holding the ground-truth quantity and unit price
    verbatim, and both recorded boxes contain ink."""
    entry = corpus_entries("receipt")[case_id]
    layout = layouts_for("receipt")[entry["layout"]]
    image, geometry, drawn = _render_spied("receipt", {**entry, "case_id": case_id}, monkeypatch)
    prices = [p.strip() for p in entry["fields"]["LINE_ITEM_PRICES"].split("|")]
    for i, qty in enumerate(_quantities(entry)):
        if qty == "1":
            assert f"LINE_ITEM_PRICES[{i}]" not in geometry["boxes"]
            continue
        expected = layout["unit_line_format"].format(quantity=qty, price=prices[i])
        assert expected in drawn, (case_id, i, expected)
        assert f" {qty} " in expected and f"${prices[i]}" in expected
        for key in (f"LINE_ITEM_PRICES[{i}]", f"LINE_ITEM_QUANTITIES[{i}]"):
            box = _pixel_box(geometry, key)
            assert _has_ink(image, box), (case_id, key, box)
            assert 0 <= box[0] < box[2] <= image.width


@pytest.mark.parametrize("case_id", sorted(_westpac()))
def test_every_westpac_balance_prints_the_ground_truth(
    case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The closing balance cell draws the ground-truth balance in the statement's plain amount format,
    its box contains ink, and it sits in the Balance column at the right of the table."""
    entry = corpus_entries("bank_statement")[case_id]
    image, geometry, drawn = _render_spied("bank_statement", {**entry, "case_id": case_id}, monkeypatch)
    truth = entry["fields"]["ACCOUNT_BALANCE"]
    printed = fmt_amount(Decimal(truth)).lstrip("$")
    assert printed.replace(",", "") == truth
    assert printed in drawn
    box = _pixel_box(geometry, "ACCOUNT_BALANCE")
    assert _has_ink(image, box), (case_id, box)
    # The column's content ends 8px inside the table's right edge (margin 80 + content width 1600).
    assert abs(box[2] - 1 - (80 + 1600 - 8)) <= 1
    assert box[0] > 80 + 1600 - 160


def test_balance_column_header_and_amounts_fit_their_columns() -> None:
    """Every Westpac amount cell stays inside its column's dividers, so no column overruns its neighbour."""
    for case_id, entry in _westpac().items():
        geometry: dict = {}
        renderer_for("bank_statement")(
            {**entry, "case_id": case_id},
            layouts_for("bank_statement")[entry["layout"]],
            distractors=False,
            geometry_out=geometry,
        )
        x0 = 80
        columns = {
            "TRANSACTION_AMOUNTS_PAID": (x0 + 1136, x0 + 1288),
            "TRANSACTION_AMOUNTS_RECEIVED": (x0 + 1288, x0 + 1440),
            "TRANSACTION_DESCRIPTIONS": (x0 + 176, x0 + 1136),
        }
        for key in geometry["boxes"]:
            name = key.split("[", 1)[0]
            if name in columns:
                left, right = columns[name]
                box = _pixel_box(geometry, key)
                assert left < box[0] and box[2] - 1 <= right, (case_id, key, box)
        balance = _pixel_box(geometry, "ACCOUNT_BALANCE")
        assert x0 + 1440 < balance[0], (case_id, balance)


def test_layouts_are_untouched_on_disk() -> None:
    """The guard tests above work on deep copies; the tracked layout files keep their bindings."""
    text = Path(REPO_ROOT / "config/layouts/receipts.yml").read_text()
    assert "price: LINE_ITEM_PRICES" in text
    banks = Path(REPO_ROOT / "config/layouts/bank_statements.yml").read_text()
    assert "currency: plain, last_row_field: ACCOUNT_BALANCE" in banks
