"""The required `page:` layout key: the physical page a layout is typeset onto.

Every one of the 22 shipped layouts carries it; a layout without it, or with a malformed one, is refused
by `validate_layout` and by `layout_page` with a four-part diagnostic.
"""

import copy

import pytest

from generators.layout_dsl.schema import LayoutSchemaError
from generators.loader import load_layout_registry
from generators.typst.config import PageSpec, layout_page
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import RECEIPTS_PATH
from tests.layout_dsl.test_schema import check_layout, make_layout, text

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

LAYOUT_DIR = REPO_ROOT / "config" / "layouts"
DOC_TYPES = {
    "receipts": "receipts.yml",
    "invoices": "invoices.yml",
    "bank_statements": "bank_statements.yml",
}
ROLL_WIDTHS_MM = {
    "receipt_thermal_80mm": 80,
    "receipt_thermal_57mm": 57,
    "receipt_retail_tax": 80,
    "receipt_fuel": 80,
    "receipt_professional": 80,
    "receipt_hospitality": 80,
}


def all_layouts() -> list[tuple[str, str, dict]]:
    """Return (file name, layout id, layout) for every shipped layout."""
    return [
        (file_name, layout_id, layout)
        for file_name in DOC_TYPES.values()
        for layout_id, layout in load_layout_registry(LAYOUT_DIR / file_name).items()
    ]


def refused(layout: dict, *tokens: str) -> LayoutSchemaError:
    """Assert `layout_page` refuses `layout` with a four-part diagnostic naming every token."""
    with pytest.raises(LayoutSchemaError) as exc_info:
        layout_page(layout, layout_path="/x/layouts.yml", layout_id="demo")
    assert_diagnostic_error(exc_info.value)
    message = str(exc_info.value)
    assert "/x/layouts.yml" in message
    assert "demo.page" in message
    for token in tokens:
        assert token in message, f"{token!r} missing from: {message}"
    return exc_info.value


def test_there_are_twenty_two_shipped_layouts() -> None:
    assert len(all_layouts()) == 22


@pytest.mark.parametrize(("file_name", "layout_id", "layout"), all_layouts())
def test_every_shipped_layout_declares_a_page(file_name: str, layout_id: str, layout: dict) -> None:
    spec = layout_page(layout, layout_path=str(LAYOUT_DIR / file_name), layout_id=layout_id)
    assert isinstance(spec, PageSpec)
    if file_name == "receipts.yml":
        assert spec.width_mm == ROLL_WIDTHS_MM[layout_id]
        assert spec.height_mm is None
    else:
        assert (spec.width_mm, spec.height_mm) == (210.0, 297.0)


def test_the_page_key_is_accepted_by_validate_layout() -> None:
    layout = make_layout([text()])
    assert layout["page"] == {"width_mm": 80}
    check_layout(layout)


def test_a_layout_without_a_page_is_refused_by_validate_layout() -> None:
    layout = make_layout([text()])
    del layout["page"]
    with pytest.raises(LayoutSchemaError) as exc_info:
        check_layout(layout)
    assert_diagnostic_error(exc_info.value)
    assert "page" in str(exc_info.value)


def test_validate_layout_refuses_a_malformed_page() -> None:
    layout = make_layout([text()], page={"size": "a4", "width_mm": 80})
    with pytest.raises(LayoutSchemaError) as exc_info:
        check_layout(layout)
    assert_diagnostic_error(exc_info.value)
    assert "layout_under_test.page" in str(exc_info.value)


def test_a_missing_page_is_refused() -> None:
    refused({"width": 420}, "no 'page' key", "page: {size: a4}", "width_mm")


@pytest.mark.parametrize("page", [None, "a4", ["a4"], 80])
def test_a_page_that_is_not_a_mapping_is_refused(page: object) -> None:
    refused({"page": page}, "not a mapping")


def test_an_empty_page_is_refused() -> None:
    refused({"page": {}}, "neither", "size", "width_mm")


def test_a_page_with_both_size_and_width_is_refused() -> None:
    refused({"page": {"size": "a4", "width_mm": 80}}, "both", "size", "width_mm")


def test_a_page_with_an_unknown_key_is_refused() -> None:
    refused({"page": {"size": "a4", "height_mm": 297}}, "height_mm")


@pytest.mark.parametrize("size", ["letter", "A4", "a3", 4, None, ""])
def test_a_size_other_than_a4_is_refused(size: object) -> None:
    err = refused({"page": {"size": size}}, "a4")
    assert "page.size" in str(err)


@pytest.mark.parametrize("width", [0, -80, "80", None, True, False, float("nan"), float("inf")])
def test_a_width_that_is_not_a_positive_number_is_refused(width: object) -> None:
    err = refused({"page": {"width_mm": width}}, "positive")
    assert "page.width_mm" in str(err)


def test_a_fractional_width_is_accepted() -> None:
    spec = layout_page({"page": {"width_mm": 57.5}}, layout_path="/x/l.yml", layout_id="demo")
    assert spec == PageSpec(width_mm=57.5, height_mm=None)


def test_the_page_key_is_not_consulted_by_the_pil_renderer() -> None:
    """Removing it from a copy changes nothing the renderer reads: `width` still sets the roll."""
    layout = load_layout_registry(RECEIPTS_PATH)["receipt_thermal_80mm"]
    stripped = copy.deepcopy(layout)
    del stripped["page"]
    assert stripped["width"] == layout["width"] == 420
