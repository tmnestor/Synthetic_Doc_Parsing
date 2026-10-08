"""Startup validation of the code and logo primitives."""

import pytest

from tests.layout_dsl.test_distractor_items import dx_layout
from tests.layout_dsl.test_schema import check_layout, reject_body, reject_layout

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

# A code mark always serves as a distractor, so a layout may draw one only under a `DX_` gate.
GATE = "DX_STORE_NUMBER"


def gated(body: list) -> dict:
    """A receipt layout drawing `body`, each block gated on GATE, with its item and provider listed."""
    return dx_layout([{**block, "when": GATE} for block in body], ["rc_store_number"])


GOOD_BARCODE = {
    "type": "barcode",
    "content": "TX-2031",
    "symbology": "code128",
    "height": 60,
    "module_px": 2,
    "quiet_zone": 10,
    "human_readable": True,
    "caption_gap": 4,
}


def test_a_valid_barcode_block_passes() -> None:
    check_layout(gated([GOOD_BARCODE]))


@pytest.mark.parametrize(
    "key", ["content", "symbology", "height", "module_px", "quiet_zone", "human_readable", "caption_gap"]
)
def test_each_required_barcode_key_is_enforced(key: str) -> None:
    block = {k: v for k, v in GOOD_BARCODE.items() if k != key}
    reject_body([block], key)


@pytest.mark.parametrize(
    ("override", "token"),
    [
        ({"symbology": "qr"}, "symbology"),
        ({"height": 0}, "height"),
        ({"height": True}, "height"),
        ({"module_px": -1}, "module_px"),
        ({"quiet_zone": -1}, "quiet_zone"),
        ({"human_readable": "yes"}, "human_readable"),
        ({"caption_gap": -1}, "caption_gap"),
        ({"caption_gap": 2.5}, "caption_gap"),
        ({"symbology": "ean13", "content": "12345"}, "content"),
        ({"content": "Café"}, "content"),
        ({"content": ""}, "empty literal"),
        ({"symbology": "ean13", "content": "9312345000019"}, "check digit"),
        ({"content": "{NOT_A_FIELD}"}, "NOT_A_FIELD"),
        ({"align": "middle"}, "align"),
        ({"family": "nope"}, "family"),
    ],
)
def test_invalid_barcode_values_are_rejected(override: dict, token: str) -> None:
    reject_body([{**GOOD_BARCODE, **override}], token)


def test_a_templated_ean13_is_left_to_render_time() -> None:
    check_layout(gated([{**GOOD_BARCODE, "symbology": "ean13", "content": "{PAYER_NAME}"}]))


def test_an_unknown_role_is_rejected_at_the_layout_level() -> None:
    reject_layout(gated([{**GOOD_BARCODE, "role": "no_such_role"}]), "no_such_role")


GOOD_QR = {
    "type": "qrcode",
    "content": "https://survey.example.test",
    "module_px": 4,
    "quiet_zone": 4,
    "error_level": "m",
}


def test_a_valid_qrcode_block_passes() -> None:
    check_layout(gated([{**GOOD_QR, "caption": "{PAYER_NAME}", "align": "right"}]))


@pytest.mark.parametrize("key", ["content", "module_px", "quiet_zone", "error_level"])
def test_each_required_qr_key_is_enforced(key: str) -> None:
    reject_body([{k: v for k, v in GOOD_QR.items() if k != key}], key)


@pytest.mark.parametrize(
    ("override", "token"),
    [
        ({"error_level": "x"}, "error_level"),
        ({"module_px": 0}, "module_px"),
        ({"module_px": True}, "module_px"),
        ({"quiet_zone": -1}, "quiet_zone"),
        ({"caption": "{NOT_A_FIELD}"}, "NOT_A_FIELD"),
        ({"content": "{NOT_A_FIELD}"}, "NOT_A_FIELD"),
        ({"caption": 5}, "caption"),
        ({"caption": ""}, "empty literal"),
        ({"content": ""}, "empty literal"),
        ({"content": "x" * 8000, "error_level": "h"}, "does not fit"),
        ({"align": "middle"}, "align"),
        ({"family": "nope"}, "family"),
    ],
)
def test_invalid_qr_values_are_rejected(override: dict, token: str) -> None:
    reject_body([{**GOOD_QR, **override}], token)


GOOD_LOGO = {
    "type": "logo",
    "content": "{PAYER_NAME}",
    "style": "circle",
    "size": 80,
    "min_text_size": 12,
    "inset": 0,
    "text_color": "white",
}


def test_a_valid_logo_block_passes() -> None:
    check_layout(gated([{**GOOD_LOGO, "align": "right", "color": "black"}]))


@pytest.mark.parametrize("key", ["content", "style", "size", "min_text_size", "text_color", "inset"])
def test_each_required_logo_key_is_enforced(key: str) -> None:
    reject_body([{k: v for k, v in GOOD_LOGO.items() if k != key}], key)


@pytest.mark.parametrize(
    ("override", "token"),
    [
        ({"style": "star"}, "style"),
        ({"size": 0}, "size"),
        ({"size": True}, "size"),
        ({"size": 8.5}, "size"),
        ({"min_text_size": 0}, "min_text_size"),
        ({"min_text_size": True}, "min_text_size"),
        ({"min_text_size": 41}, "min_text_size"),
        ({"min_text_size": 6.5}, "min_text_size"),
        ({"inset": -1}, "inset"),
        ({"inset": True}, "inset"),
        ({"inset": 21}, "inset"),
        ({"content": "  "}, "empty literal"),
        ({"content": "2031 4417"}, "no letters"),
        ({"text_color": ""}, "text_color"),
        ({"text_color": 5}, "text_color"),
        ({"content": "{NOT_A_FIELD}"}, "NOT_A_FIELD"),
        ({"content": 5}, "content"),
        ({"align": "middle"}, "align"),
        ({"family": "nope"}, "family"),
    ],
)
def test_invalid_logo_values_are_rejected(override: dict, token: str) -> None:
    reject_body([{**GOOD_LOGO, **override}], token)


def test_logo_text_colour_equal_to_explicit_colour_is_rejected() -> None:
    reject_body([{**GOOD_LOGO, "color": "white", "text_color": "#ffffff"}], "text_color", "same colour")


def test_an_invalid_logo_text_colour_is_rejected() -> None:
    reject_body([{**GOOD_LOGO, "text_color": "chartreuse-ish"}], "text_color")


@pytest.mark.parametrize("block", [GOOD_BARCODE, GOOD_QR, GOOD_LOGO])
def test_an_invalid_colour_is_rejected_on_each_code_primitive(block: dict) -> None:
    reject_body([{**block, "color": "not-a-colour"}], "color")


# --- a code mark always serves as a distractor, so it draws only under a DX_ gate ---------------------


@pytest.mark.parametrize("block", [GOOD_BARCODE, GOOD_QR, GOOD_LOGO], ids=["barcode", "qrcode", "logo"])
@pytest.mark.parametrize("when", [None, "PAYER_NAME"], ids=["ungated", "ordinary-gate"])
def test_a_code_mark_not_gated_on_a_dx_key_is_refused(block: dict, when: str | None) -> None:
    mark = {**block, **({"when": when} if when else {})}
    err = reject_layout(dx_layout([mark], []), f"'{block['type']}'", "when", "DX_", "body[0]")
    assert "not gated with when:" in str(err) if when is None else f"'{when}'" in str(err)


def test_a_code_mark_nested_in_an_ungated_container_names_its_own_path() -> None:
    panel = {"type": "panel", "children": [GOOD_LOGO]}
    reject_layout(dx_layout([panel], []), "body[0].children[0]", "'logo'")


def test_a_code_mark_inside_a_dx_gated_container_passes() -> None:
    panel = {"type": "panel", "when": GATE, "children": [GOOD_LOGO]}
    check_layout(dx_layout([panel], ["rc_store_number"]))


@pytest.mark.parametrize("block", [GOOD_BARCODE, GOOD_QR, GOOD_LOGO], ids=["barcode", "qrcode", "logo"])
def test_a_code_mark_gated_on_a_dx_key_passes(block: dict) -> None:
    check_layout(gated([block]))
