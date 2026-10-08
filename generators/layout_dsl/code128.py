"""Code 128 encoding, in-repo.

Implements the symbology of ISO/IEC 15417 (Code 128 bar code symbology specification) for the two code
sets this corpus prints: set C, one symbol per pair of digits, for all-digit content of even length;
and set B, one symbol per character, for any other printable ASCII content (32 to 126).

python-barcode 0.16.1 is not used for Code 128: it drops a leading "99" digit pair (its set C encoding
of "9912" decodes as "12"), so a drawn distractor value could print a mark that does not match its
caption. EAN-13 is still drawn with python-barcode, which encodes it correctly.

Each symbol is six alternating elements (bar, space, bar, space, bar, space) of one to four modules,
eleven modules in all; the stop symbol adds a final two-module bar, thirteen modules. The check symbol
is (start value + sum of position x value) mod 103, positions counted from 1.
"""

# Element widths (bar, space, bar, space, bar, space) for symbol values 0 to 106, from the standard.
_WIDTHS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()


def _modules(widths: str) -> str:
    return "".join(("1" if index % 2 == 0 else "0") * int(w) for index, w in enumerate(widths))


# Module strings ('1' bar, '0' space) for values 0 to 106.
PATTERNS: tuple[str, ...] = tuple(_modules(w) for w in _WIDTHS)

START_B = 104
START_C = 105
STOP = 106
_CHECK_MODULUS = 103
_SET_B_OFFSET = 32  # set B value = ASCII code - 32


def code128_values(content: str) -> list[int]:
    """Return the symbol values for `content`: start, data and check, without the stop.

    Args:
        content: All-digit text of even length (set C), or printable ASCII 32 to 126 (set B).

    Returns:
        The start value, one value per data symbol, and the check value.

    Raises:
        ValueError: `content` is empty or holds a character outside printable ASCII.
    """
    if not content:
        raise ValueError("Code 128 content is empty.")
    if content.isascii() and content.isdigit() and len(content) % 2 == 0:
        start = START_C
        data = [int(content[i : i + 2]) for i in range(0, len(content), 2)]
    else:
        bad = sorted({ch for ch in content if not 32 <= ord(ch) <= 126})
        if bad:
            raise ValueError(f"Code 128 set B cannot encode {bad!r}; only printable ASCII 32 to 126.")
        start = START_B
        data = [ord(ch) - _SET_B_OFFSET for ch in content]
    check = (start + sum(position * value for position, value in enumerate(data, start=1))) % _CHECK_MODULUS
    return [start, *data, check]


def encode_code128(content: str) -> str:
    """Encode `content` as Code 128 modules, without the quiet zones.

    Args:
        content: All-digit text of even length (set C), or printable ASCII 32 to 126 (set B).

    Returns:
        The module string, '1' for a bar module and '0' for a space module, ending with the stop.

    Raises:
        ValueError: `content` is empty or holds a character outside printable ASCII.
    """
    return "".join(PATTERNS[value] for value in code128_values(content)) + PATTERNS[STOP]
