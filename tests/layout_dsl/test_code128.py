"""The in-repo Code 128 encoder: every drawn mark decodes to exactly its content.

python-barcode 0.16.1 drops a leading "99" digit pair ("9912" decodes as "12"), so Code 128 is encoded
in-repo. Each test draws the real mark through `draw_barcode` and decodes it with zxing-cpp.
"""

import random

import pytest
import zxingcpp
from PIL import Image

from generators.layout_dsl.code128 import PATTERNS, encode_code128
from generators.layout_dsl.primitives_codes import draw_barcode
from tests.layout_dsl.dsl_helpers import make_drawing_ctx, real_layout

# Invented values, absent from ground_truth/.
SET_B = ["TX-2031-904417", "99AB", "A", "Ref #7/2031 (x)", "~!@$%^&*_+={}[]|:;'<>,.?", "123", "12 34"]


def _block(symbology: str = "code128") -> dict:
    return {
        "type": "barcode",
        "content": "{DX_X}",
        "symbology": symbology,
        "height": 40,
        "module_px": 2,
        "quiet_zone": 10,
        "human_readable": False,
        "caption_gap": 4,
    }


def draw(content: str, symbology: str = "code128") -> Image.Image:
    entry = {"case_id": "CASE999", "layout": "receipt_thermal_80mm", "fields": {"DX_X": content}}
    block = _block(symbology)
    ctx, image = make_drawing_ctx(real_layout([block]), entry, page_width=900)
    draw_barcode(block, ctx, 0)
    return image


def decoded(content: str, symbology: str = "code128") -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(draw(content, symbology))]


def test_the_table_holds_the_107_standard_patterns() -> None:
    assert len(PATTERNS) == 107
    assert all(len(p) == 11 for p in PATTERNS[:106])
    assert len(PATTERNS[106]) == 13  # stop, with its final two-module bar
    assert len(set(PATTERNS)) == 107


@pytest.mark.parametrize("pair", [f"{n:02d}" for n in range(100)])
def test_every_leading_digit_pair_decodes_exactly(pair: str) -> None:
    assert decoded(pair + "1234") == [pair + "1234"]


def test_random_sixteen_digit_strings_decode_exactly() -> None:
    rng = random.Random(20311006)
    for _ in range(1000):
        value = "".join(rng.choice("0123456789") for _ in range(16))
        assert decoded(value) == [value], value


@pytest.mark.parametrize("value", SET_B)
def test_set_b_content_decodes_exactly(value: str) -> None:
    assert decoded(value) == [value]


@pytest.mark.parametrize("digits", [2, 4, 8, 10, 16])
def test_set_c_module_count_follows_the_standard(digits: int) -> None:
    assert len(encode_code128("9" * digits)) == 11 * (digits // 2 + 2) + 13


def test_set_c_starts_with_start_c_and_ends_with_stop() -> None:
    modules = encode_code128("9912")
    assert modules.startswith(PATTERNS[105]) and modules.endswith(PATTERNS[106])
    # start C, 99, 12, check, stop
    assert modules[11:22] == PATTERNS[99] and modules[22:33] == PATTERNS[12]
    assert modules[33:44] == PATTERNS[(105 + 1 * 99 + 2 * 12) % 103]


def test_odd_length_digits_use_set_b() -> None:
    assert encode_code128("123").startswith(PATTERNS[104])


@pytest.mark.parametrize("bad", ["", "Café", "tab\there", "\x7f"])
def test_empty_or_non_printable_content_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        encode_code128(bad)


@pytest.mark.parametrize("value", ["990000000001", "999999999999", "991234567890", "931234500001"])
def test_ean13_still_decodes_with_leading_nines(value: str) -> None:
    (text,) = decoded(value, "ean13")
    assert text[:12] == value[:12] and len(text) == 13
