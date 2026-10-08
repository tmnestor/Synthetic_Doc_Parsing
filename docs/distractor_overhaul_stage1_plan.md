# Distractor Overhaul, Stage 1: Code and Logo Primitives

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the layout DSL three new inline primitives, `barcode`, `qrcode` and `logo`, plus a vendored handwriting face, so later stages can place realistic unscored marks on receipts, invoices and bank statements.

**Architecture:** One new module, `generators/layout_dsl/primitives_codes.py`, holds the three drawers. They paint modules and shapes directly through the existing `ImageDraw` surface (no image pasting), using `python-barcode` and `segno` only to compute module patterns. Each primitive is registered in `PRIMITIVE_DRAWERS` (engine) and `PRIMITIVES` (schema), validated at startup like every other block, and gated at render time by the existing `when:` mechanism. No shipped layout uses them yet, so every existing page must render pixel-identically.

**Tech Stack:** Python 3.12, Pillow 12.2.0, `segno`, `python-barcode`, `zxing-cpp` (test-only decoder), pytest, conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md`, sections 6 and 7 (primitives) and 12 (testing). Parent plan: `docs/distractor_overhaul_plan.md` (roadmap row 1, Global Constraints, Stage 0 outcome).

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- Pillow stays pinned at `12.2.0`.
- Every new dependency is pinned exactly (`name==X.Y.Z`) in `environment.yml` first, then installed with `conda env update -f environment.yml --prune`; never `pip install` ad hoc.
- Line length 108; Python 3.12 type hints (`X | Y`); `pathlib.Path` for every path; Google-style docstrings.
- YAML is the single source of truth: no Python defaults for configuration. Every parameter of the new primitives is either a required block key or resolved through the existing layout `defaults:` (`align`, `color`, `role`, `family`).
- Every fail-fast error names What, Where (file and key path), Expected and Recover, on lines starting with those labels; tests assert this with `assert_diagnostic_error`.
- Renderers never clip: a mark wider than its region fails fast.
- In `except` blocks always `raise ... from err` or `from None`.
- The plain pixel snapshot must not change: `conda run -n synthetic pytest tests/test_pixel_snapshot.py -q` passes with no re-bless after every task.
- `tests/` is gitignored and local-only; tracked changes are committed on `main` after the quality gate: `pytest tests/` (must collect tests), `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`, `mypy . --ignore-missing-imports`.
- Tracked files contain no tooling references, no real business names and no value copied from `ground_truth/`; invented example values are checked absent with `command grep -rnF "<value>" ground_truth/`.
- Prose uses British spelling and no em-dashes.

## Review Focus

1. **Content absent for an entry** (the template resolves empty or to `NOT_FOUND`): a four-part diagnostic, never a blank or garbage mark. Pinned in Tasks 2, 3 and 4.
2. **A mark wider than its region**: fails fast naming the widths, never clipped. Pinned in Tasks 2 and 3.
3. **Content outside the symbology** (non-ASCII for Code 128, not 12 or 13 digits for EAN-13): refused before drawing, both statically (literal content, at validate time) and at render time (templated content). Pinned in Task 2.
4. **QR data that does not fit any QR version at the chosen error level**: a diagnostic, not a library traceback. Pinned in Task 3.
5. **Determinism**: the same block and entry render identical pixels on every run. Pinned in Tasks 2, 3 and 4.

---

### Task 1: Dependencies and the handwriting face

**Files:**
- Modify: `environment.yml` (pip section)
- Create: `fonts/Kalam-Regular.ttf`, `fonts/Kalam-Bold.ttf`, `fonts/LICENSE-Kalam.txt`
- Modify: `generators/common.py:55-59` (`FONT_FAMILIES`)
- Test: `tests/test_environment_pins.py`, `tests/test_font_guard.py`

**Interfaces:**
- Produces: importable `segno`, `barcode` (python-barcode) and `zxingcpp`; `FONT_FAMILIES["kalam"] == ("Kalam-Regular.ttf", "Kalam-Bold.ttf")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_environment_pins.py`:

```python
import re
from importlib.metadata import version

import pytest

from tests.conftest import REPO_ROOT

ENV = (REPO_ROOT / "environment.yml").read_text()


@pytest.mark.parametrize("dist", ["segno", "python-barcode", "zxing-cpp"])
def test_code_libraries_are_pinned_and_installed_at_the_pin(dist: str) -> None:
    """Barcode and QR module patterns must not drift between machines."""
    pins = re.findall(rf"^\s*-\s*{re.escape(dist)}==([\w.]+)\s*$", ENV, re.MULTILINE)
    assert len(pins) == 1, f"environment.yml must pin {dist} exactly once with =="
    assert version(dist) == pins[0]
```

Append to `tests/test_font_guard.py`:

```python
from PIL import Image, ImageDraw

from generators.common import FONT_FAMILIES, font_path, load_font
from tests.conftest import REPO_ROOT


def test_kalam_is_vendored_with_its_licence() -> None:
    """The handwriting face ships in fonts/ with its OFL licence, both weights."""
    assert FONT_FAMILIES["kalam"] == ("Kalam-Regular.ttf", "Kalam-Bold.ttf")
    for bold in (False, True):
        path = font_path("kalam", bold=bold)
        assert path.parent == REPO_ROOT / "fonts" and path.is_file()
    assert "SIL Open Font License" in (REPO_ROOT / "fonts" / "LICENSE-Kalam.txt").read_text()


def test_kalam_draws_ink() -> None:
    """The face loads through the vendored-font path and renders visible text."""
    image = Image.new("RGB", (200, 60), "white")
    ImageDraw.Draw(image).text((5, 5), "WRE 50% work", font=load_font(24, family="kalam"), fill="black")
    assert image.convert("L").getextrema()[0] < 128
```

Before relying on them, read `generators/common.py` around lines 55-140 and use the real names and signatures of `font_path` and the font loader (the loader may be named differently or take the family positionally); adjust the two font tests to match the code, not the reverse.

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n synthetic pytest tests/test_environment_pins.py tests/test_font_guard.py -q`
Expected: FAIL (`PackageNotFoundError` for the libraries; `KeyError: 'kalam'`).

- [ ] **Step 3: Pin the libraries**

In `environment.yml`'s pip list, after the degradation block, add a section (choose the current release of each that installs on Python 3.12, and pin it exactly):

```yaml
      # --- Inline code marks (generators/layout_dsl/primitives_codes.py) ----
      # Pure Python. Used only to compute module patterns; the DSL draws the
      # modules itself, so no image writer or extra dependency is involved.
      - segno==<version>
      - python-barcode==<version>

      # --- Test-only ---------------------------------------------------------
      # Decodes rendered barcodes and QR codes in the local suite, proving the
      # marks are real, scannable symbols. Not imported by the generator.
      - zxing-cpp==<version>
```

Replace each `<version>` with the exact version you install, then run `conda run -n synthetic conda env update -f environment.yml --prune` (or the equivalent `conda env update -n synthetic -f environment.yml --prune`). If `--prune` would remove the separately installed dev tools (pytest, ruff, mypy), reinstall them afterwards as the file's dev-tooling note describes, and say so in your report.

- [ ] **Step 4: Vendor Kalam**

Download the two static faces and the licence from the Google Fonts repository (`https://github.com/google/fonts/raw/main/ofl/kalam/Kalam-Regular.ttf`, `.../Kalam-Bold.ttf`, `.../OFL.txt`), save them as `fonts/Kalam-Regular.ttf`, `fonts/Kalam-Bold.ttf` and `fonts/LICENSE-Kalam.txt`, and record each file's SHA-256 in your report. Then add to `FONT_FAMILIES` in `generators/common.py`:

```python
    "kalam": ("Kalam-Regular.ttf", "Kalam-Bold.ttf"),
```

- [ ] **Step 5: Run the tests and the snapshot**

Run: `conda run -n synthetic pytest tests/test_environment_pins.py tests/test_font_guard.py tests/test_pixel_snapshot.py -q`
Expected: all pass; the pixel snapshot is unchanged.

- [ ] **Step 6: Quality gate and commit**

Run the full gate (Global Constraints). Commit `environment.yml`, `fonts/Kalam-Regular.ttf`, `fonts/Kalam-Bold.ttf`, `fonts/LICENSE-Kalam.txt`, `generators/common.py` with the message `➕ deps: pin segno and python-barcode, vendor the Kalam handwriting face`.

### Task 2: `barcode` primitive

**Files:**
- Create: `generators/layout_dsl/primitives_codes.py`
- Modify: `generators/layout_dsl/engine.py` (imports, `PRIMITIVE_DRAWERS`, `_DSL_ERRORS`)
- Modify: `generators/layout_dsl/schema.py` (`PRIMITIVES`, per-kind checks in `_validate_block`)
- Test: `tests/layout_dsl/test_primitives_codes.py`, `tests/layout_dsl/test_codes_schema.py`

**Interfaces:**
- Consumes: `interpolate(template, fields) -> str` (binding.py), `resolve_param(block, layout, key, *, layout_id, layout_path)` (defaults.py), `resolve_role(layout, role) -> int`, `font_for(layout, block, size, *, bold, layout_id, layout_path)`, `line_advance(layout, block, *, layout_id, layout_path) -> int` (primitives_text.py), `RenderContext` with `.draw`, `.entry`, `.layout`, `.layout_id`, `.layout_path`, `.region` (`Region` with `x`, `width`, `right`).
- Produces: `CodeBlockError(RuntimeError)`; `draw_barcode(block: dict, ctx: RenderContext, y: int) -> int`; helpers `_fail`, `_resolve_content`, `_x_for` reused by Tasks 3 and 4; `SYMBOLOGIES = ("code128", "ean13")`.

Block keys. Required: `content` (template), `symbology` (`code128` | `ean13`), `height` (px, > 0), `module_width` (px per module, > 0), `quiet_zone` (modules each side, >= 0), `human_readable` (bool). Optional, falling back to `defaults:`: `align`, `color`, `role` (caption size), `family`.

- [ ] **Step 1: Confirm the library APIs**

In a throwaway `conda run -n synthetic python -c` session, confirm: `barcode.get_barcode_class("code128")("TX-2031", writer=None).build()` returns a list whose first item is a string of `0`/`1`; `get_fullcode()` on an EAN-13 built from 12 digits returns 13 digits; `zxingcpp.read_barcodes(pil_image)` returns results with `.text` and `.format`. If any name differs in the pinned versions, use the real one in the code below.

- [ ] **Step 2: Write the failing tests**

`tests/layout_dsl/test_primitives_codes.py` (read `tests/layout_dsl/dsl_helpers.py` first and build the drawing context with its `make_drawing_ctx`, adapting the helper below to its real signature):

```python
"""Inline code marks: real, scannable, deterministic, never clipped."""

import pytest
import zxingcpp
from PIL import Image

from generators.layout_dsl.primitives_codes import CodeBlockError, draw_barcode
from tests.conftest import assert_diagnostic_error
from tests.layout_dsl.dsl_helpers import make_drawing_ctx

# Invented values, absent from ground_truth/.
FIELDS = {"DX_TRANSACTION_ID": "TX-2031-904417", "DX_EAN": "931234500001", "EMPTY": "NOT_FOUND"}


def barcode_block(**overrides: object) -> dict:
    block = {
        "type": "barcode",
        "content": "{DX_TRANSACTION_ID}",
        "symbology": "code128",
        "height": 60,
        "module_width": 2,
        "quiet_zone": 10,
        "human_readable": True,
    }
    block.update(overrides)
    return block


def render(block: dict, width: int = 600) -> tuple[Image.Image, int]:
    image, ctx = make_drawing_ctx(fields=FIELDS, width=width)
    advance = draw_barcode(block, ctx, 0)
    return image, advance


def decoded(image: Image.Image) -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(image)]


def test_code128_round_trips_through_a_decoder() -> None:
    image, _ = render(barcode_block(human_readable=False))
    assert decoded(image) == ["TX-2031-904417"]


def test_ean13_round_trips_with_its_check_digit() -> None:
    image, _ = render(barcode_block(content="{DX_EAN}", symbology="ean13"))
    (text,) = decoded(image)
    assert len(text) == 13 and text.startswith("931234500001")


def test_same_block_renders_identical_pixels() -> None:
    first, _ = render(barcode_block())
    second, _ = render(barcode_block())
    assert first.tobytes() == second.tobytes()


def test_advance_is_the_bar_height_without_a_caption() -> None:
    _, advance = render(barcode_block(human_readable=False))
    assert advance == 60


def test_caption_adds_one_line_advance() -> None:
    _, bare = render(barcode_block(human_readable=False))
    _, captioned = render(barcode_block())
    assert captioned > bare


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_places_the_quiet_zone_edge(align: str) -> None:
    image, _ = render(barcode_block(human_readable=False, align=align))
    left, _, right, _ = image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    if align == "left":
        assert left == 0 + 10 * 2  # region x is 0 in the helper canvas; adjust if it is not
    elif align == "right":
        assert image.width - right >= 10 * 2
    else:
        assert abs(left - (image.width - right)) <= 2


def test_too_wide_for_its_region_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(module_width=8), width=300)
    assert_diagnostic_error(err.value)
    assert "300" in str(err.value)


def test_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)


def test_code128_refuses_non_ascii() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="Café-2031"))
    assert_diagnostic_error(err.value)


def test_ean13_refuses_the_wrong_length() -> None:
    with pytest.raises(CodeBlockError) as err:
        render(barcode_block(content="12345", symbology="ean13"))
    assert_diagnostic_error(err.value)
```

`tests/layout_dsl/test_codes_schema.py` (read `tests/layout_dsl/test_schema.py` for its `make_layout` builder and `rejected` helper and reuse them, importing rather than copying):

```python
"""Startup validation of the code and logo primitives."""

import pytest

from tests.layout_dsl.test_schema import make_layout, rejected  # adapt names to the real helpers

GOOD_BARCODE = {
    "type": "barcode", "content": "TX-2031", "symbology": "code128",
    "height": 60, "module_width": 2, "quiet_zone": 10, "human_readable": True,
}


def test_a_valid_barcode_block_passes() -> None:
    make_layout(body=[GOOD_BARCODE])  # validates without raising


@pytest.mark.parametrize("key", ["content", "symbology", "height", "module_width", "quiet_zone", "human_readable"])
def test_each_required_barcode_key_is_enforced(key: str) -> None:
    block = {k: v for k, v in GOOD_BARCODE.items() if k != key}
    rejected(body=[block], token=key)


@pytest.mark.parametrize(
    ("override", "token"),
    [
        ({"symbology": "qr"}, "symbology"),
        ({"height": 0}, "height"),
        ({"module_width": -1}, "module_width"),
        ({"quiet_zone": -1}, "quiet_zone"),
        ({"human_readable": "yes"}, "human_readable"),
        ({"symbology": "ean13", "content": "12345"}, "content"),
        ({"content": "{NOT_A_FIELD}"}, "NOT_A_FIELD"),
    ],
)
def test_invalid_barcode_values_are_rejected(override: dict, token: str) -> None:
    rejected(body=[{**GOOD_BARCODE, **override}], token=token)
```

Check the invented values: `command grep -rnF "TX-2031" ground_truth/; command grep -rnF "931234500001" ground_truth/` must print nothing.

- [ ] **Step 3: Run them to verify they fail**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_primitives_codes.py tests/layout_dsl/test_codes_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: generators.layout_dsl.primitives_codes` and schema rejections of the unknown `barcode` type.

- [ ] **Step 4: Write `generators/layout_dsl/primitives_codes.py`**

```python
"""Inline code marks: barcodes, QR codes and generated logos.

These primitives draw realistic marks that carry no scored field. Libraries
compute module patterns only; every module is painted through the page's own
`ImageDraw`, so output is deterministic and nothing is pasted or resampled. A
mark never clips: one wider than its region fails fast.
"""

import barcode

from generators.layout_dsl.binding import interpolate
from generators.layout_dsl.context import RenderContext
from generators.layout_dsl.defaults import resolve_param
from generators.layout_dsl.primitives_text import font_for, line_advance, resolve_role

SYMBOLOGIES = ("code128", "ean13")


class CodeBlockError(RuntimeError):
    """Raised when a code or logo block cannot be drawn as configured."""


def _fail(ctx: RenderContext, kind: str, what: str, expected: str, recover: str) -> CodeBlockError:
    return CodeBlockError(
        f"Cannot draw '{kind}' block.\n"
        f"  What:     {what}\n"
        f"  Where:    {ctx.layout_path} -> {ctx.layout_id}.body\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _resolve_content(block: dict, ctx: RenderContext, kind: str, key: str = "content") -> str:
    text = interpolate(str(block[key]), ctx.entry["fields"]).strip()
    if not text or "NOT_FOUND" in text:
        raise _fail(
            ctx,
            kind,
            f"'{key}' {block[key]!r} resolved to {text!r} for this entry.",
            "a template whose fields are present for every entry that draws this block.",
            f"gate the block with when: <FIELD>, or point '{key}' at a field this entry carries.",
        )
    return text


def _param(block: dict, ctx: RenderContext, key: str) -> object:
    return resolve_param(block, ctx.layout, key, layout_id=ctx.layout_id, layout_path=ctx.layout_path)


def _x_for(width: int, block: dict, ctx: RenderContext, kind: str) -> int:
    region = ctx.region
    if width > region.width:
        raise _fail(
            ctx,
            kind,
            f"the {kind} is {width}px wide but its region is {region.width}px.",
            "a mark no wider than its region; renderers never clip.",
            "reduce the module size or quiet zone, shorten the content, or widen the region.",
        )
    align = str(_param(block, ctx, "align"))
    if align == "left":
        return region.x
    if align == "right":
        return region.right - width
    return region.x + (region.width - width) // 2


def _check_symbology(content: str, symbology: str, ctx: RenderContext) -> None:
    if symbology == "code128" and not all(32 <= ord(ch) < 127 for ch in content):
        raise _fail(
            ctx,
            "barcode",
            f"code128 content {content!r} has characters outside printable ASCII.",
            "content made of ASCII characters 32 to 126.",
            "transliterate the content, or use a qrcode block for arbitrary text.",
        )
    if symbology == "ean13" and not (content.isdigit() and len(content) in (12, 13)):
        raise _fail(
            ctx,
            "barcode",
            f"ean13 content {content!r} is not 12 or 13 digits.",
            "12 digits (the check digit is computed) or 13 digits including it.",
            "point content at a 12-digit field, or use symbology: code128.",
        )


def draw_barcode(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a Code 128 or EAN-13 barcode, optionally with its digits beneath.

    Args:
        block: The `barcode` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the content is absent, outside the symbology, or the
            mark is wider than its region.
    """
    content = _resolve_content(block, ctx, "barcode")
    symbology = str(block["symbology"])
    _check_symbology(content, symbology, ctx)
    code = barcode.get_barcode_class(symbology)(content, writer=None)
    modules = code.build()[0]
    unit = int(block["module_width"])
    quiet = int(block["quiet_zone"]) * unit
    height = int(block["height"])
    left = _x_for(len(modules) * unit + 2 * quiet, block, ctx, "barcode") + quiet
    color = _param(block, ctx, "color")
    for index, bit in enumerate(modules):
        if bit == "1":
            x = left + index * unit
            ctx.draw.rectangle((x, y, x + unit - 1, y + height - 1), fill=color)
    y += height
    if block["human_readable"]:
        text = code.get_fullcode()
        size = resolve_role(ctx.layout, str(_param(block, ctx, "role")))
        font = font_for(ctx.layout, block, size, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
        text_x = left + (len(modules) * unit - int(ctx.draw.textlength(text, font=font))) // 2
        ctx.draw.text((text_x, y), text, font=font, fill=color)
        y += line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    return y
```

Confirm `Region.right` is a property (not a method) in `generators/layout_dsl/context.py`; call it accordingly.

- [ ] **Step 5: Register the primitive**

In `generators/layout_dsl/engine.py`: import `CodeBlockError, draw_barcode` from `generators.layout_dsl.primitives_codes`, add `"barcode": draw_barcode` to `PRIMITIVE_DRAWERS`, and add `CodeBlockError` to `_DSL_ERRORS` so failures carry the block path.

In `generators/layout_dsl/schema.py`: add to `PRIMITIVES`

```python
    "barcode": (
        ("content", "symbology", "height", "module_width", "quiet_zone", "human_readable"),
        ("align", "color", "role", "family"),
    ),
```

and in `_validate_block`, a `kind == "barcode"` branch using the module's existing `_err(what, *, layout_path, key_path, expected, recover)` builder that rejects: `symbology` not in `("code128", "ean13")`; `height` or `module_width` not a positive `int` (and not a `bool`); `quiet_zone` not an `int` >= 0; `human_readable` not a `bool`; a literal `content` (no `{`) for `ean13` that is not 12 or 13 digits, or for `code128` with characters outside ASCII 32 to 126. Reuse whatever check the module already applies to `text` content templates so that `{FIELD}` references are validated against `known_fields` for `content` too (read how `text` does it and call the same helper). Keep `align`, `color`, `role` and `family` checks consistent with the existing primitives that carry them.

- [ ] **Step 6: Run the tests**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_primitives_codes.py tests/layout_dsl/test_codes_schema.py tests/layout_dsl tests/test_pixel_snapshot.py -q`
Expected: all pass; the pixel snapshot is unchanged. If `test_alignment_places_the_quiet_zone_edge` depends on a helper canvas whose region does not start at x = 0, assert against the region's real `x` instead of 0.

- [ ] **Step 7: Quality gate and commit**

Run the full gate. Commit `generators/layout_dsl/primitives_codes.py`, `generators/layout_dsl/engine.py`, `generators/layout_dsl/schema.py` with `✨ feat: add a barcode primitive to the layout DSL`.

### Task 3: `qrcode` primitive

**Files:**
- Modify: `generators/layout_dsl/primitives_codes.py`, `generators/layout_dsl/engine.py`, `generators/layout_dsl/schema.py`
- Test: `tests/layout_dsl/test_primitives_codes.py`, `tests/layout_dsl/test_codes_schema.py`

**Interfaces:**
- Consumes: `_fail`, `_resolve_content`, `_param`, `_x_for`, `CodeBlockError` from Task 2.
- Produces: `draw_qrcode(block: dict, ctx: RenderContext, y: int) -> int`; `ERROR_LEVELS = ("l", "m", "q", "h")`.

Block keys. Required: `content` (template), `module_px` (> 0), `quiet_zone` (modules, >= 0), `error_level` (`l` | `m` | `q` | `h`). Optional: `caption` (template, drawn centred beneath), `align`, `color`, `role`, `family`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/layout_dsl/test_primitives_codes.py`:

```python
from generators.layout_dsl.primitives_codes import draw_qrcode

QR_FIELDS = {**FIELDS, "DX_SURVEY_URL": "https://survey.example.test/r/2031-7Q4K"}


def qr_block(**overrides: object) -> dict:
    block = {"type": "qrcode", "content": "{DX_SURVEY_URL}", "module_px": 4, "quiet_zone": 4, "error_level": "m"}
    block.update(overrides)
    return block


def render_qr(block: dict, width: int = 600) -> tuple[Image.Image, int]:
    image, ctx = make_drawing_ctx(fields=QR_FIELDS, width=width)
    return image, draw_qrcode(block, ctx, 0)


def test_qr_round_trips_through_a_decoder() -> None:
    image, _ = render_qr(qr_block())
    assert decoded(image) == ["https://survey.example.test/r/2031-7Q4K"]


def test_higher_error_level_draws_a_larger_symbol() -> None:
    _, low = render_qr(qr_block(error_level="l"))
    _, high = render_qr(qr_block(error_level="h"))
    assert high > low


def test_qr_is_deterministic() -> None:
    first, _ = render_qr(qr_block())
    second, _ = render_qr(qr_block())
    assert first.tobytes() == second.tobytes()


def test_caption_adds_one_line_advance_and_ink() -> None:
    _, bare = render_qr(qr_block())
    image, captioned = render_qr(qr_block(caption="Scan to rate your visit"))
    assert captioned > bare
    assert image.crop((0, bare, image.width, captioned)).convert("L").getextrema()[0] < 128


def test_qr_too_wide_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(module_px=20), width=200)
    assert_diagnostic_error(err.value)


def test_qr_data_overflow_is_a_diagnostic() -> None:
    QR_FIELDS["DX_HUGE"] = "x" * 8000
    try:
        with pytest.raises(CodeBlockError) as err:
            render_qr(qr_block(content="{DX_HUGE}", error_level="h"))
        assert_diagnostic_error(err.value)
    finally:
        del QR_FIELDS["DX_HUGE"]


def test_qr_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_qr(qr_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)
```

Append to `tests/layout_dsl/test_codes_schema.py`:

```python
GOOD_QR = {"type": "qrcode", "content": "https://survey.example.test", "module_px": 4, "quiet_zone": 4, "error_level": "m"}


@pytest.mark.parametrize("key", ["content", "module_px", "quiet_zone", "error_level"])
def test_each_required_qr_key_is_enforced(key: str) -> None:
    rejected(body=[{k: v for k, v in GOOD_QR.items() if k != key}], token=key)


@pytest.mark.parametrize(
    ("override", "token"),
    [({"error_level": "x"}, "error_level"), ({"module_px": 0}, "module_px"), ({"caption": "{NOT_A_FIELD}"}, "NOT_A_FIELD")],
)
def test_invalid_qr_values_are_rejected(override: dict, token: str) -> None:
    rejected(body=[{**GOOD_QR, **override}], token=token)
```

`example.test` is a reserved test domain, so it cannot name a real business.

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_primitives_codes.py tests/layout_dsl/test_codes_schema.py -q`
Expected: the new tests FAIL (`ImportError: draw_qrcode`; unknown `qrcode` type).

- [ ] **Step 3: Implement `draw_qrcode`**

Confirm first in a throwaway session that `segno.make(text, error="m", micro=False).matrix` is a sequence of rows whose truthy values are dark modules, and the name of segno's overflow exception (`segno.DataOverflowError` in current releases). Then add to `primitives_codes.py` (with `import segno` at the top):

```python
ERROR_LEVELS = ("l", "m", "q", "h")


def draw_qrcode(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a QR code, optionally with a caption beneath.

    Args:
        block: The `qrcode` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the content is absent, does not fit any QR version at
            the error level, or the symbol is wider than its region.
    """
    content = _resolve_content(block, ctx, "qrcode")
    level = str(block["error_level"])
    try:
        matrix = segno.make(content, error=level, micro=False).matrix
    except segno.DataOverflowError as err:
        raise _fail(
            ctx,
            "qrcode",
            f"{len(content)} characters do not fit any QR version at error level {level!r}.",
            "content short enough for a version-40 symbol at this error level.",
            "shorten the content or lower error_level.",
        ) from err
    unit = int(block["module_px"])
    quiet = int(block["quiet_zone"]) * unit
    side = len(matrix) * unit
    left = _x_for(side + 2 * quiet, block, ctx, "qrcode") + quiet
    top = y + quiet
    color = _param(block, ctx, "color")
    for row_index, row in enumerate(matrix):
        for col_index, dark in enumerate(row):
            if dark:
                x, cell_y = left + col_index * unit, top + row_index * unit
                ctx.draw.rectangle((x, cell_y, x + unit - 1, cell_y + unit - 1), fill=color)
    y = top + side + quiet
    if "caption" in block:
        text = _resolve_content(block, ctx, "qrcode", key="caption")
        size = resolve_role(ctx.layout, str(_param(block, ctx, "role")))
        font = font_for(ctx.layout, block, size, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
        text_x = left + (side - int(ctx.draw.textlength(text, font=font))) // 2
        ctx.draw.text((text_x, y), text, font=font, fill=color)
        y += line_advance(ctx.layout, block, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    return y
```

If the caption is wider than the symbol, centre it on the symbol anyway but fail fast (through `_x_for`-style width check against the region) when it would overflow the region; add a test for that if you add the check.

- [ ] **Step 4: Register and validate**

Engine: `"qrcode": draw_qrcode`. Schema `PRIMITIVES`:

```python
    "qrcode": (
        ("content", "module_px", "quiet_zone", "error_level"),
        ("caption", "align", "color", "role", "family"),
    ),
```

and a `kind == "qrcode"` branch rejecting `error_level` outside `("l", "m", "q", "h")`, `module_px` not a positive `int`, `quiet_zone` not an `int` >= 0, and validating `{FIELD}` references in `content` and `caption` against `known_fields` with the same helper as Task 2.

- [ ] **Step 5: Run the tests**

Run: `conda run -n synthetic pytest tests/layout_dsl tests/test_pixel_snapshot.py -q`
Expected: all pass; pixel snapshot unchanged.

- [ ] **Step 6: Quality gate and commit**

Commit the three tracked files with `✨ feat: add a qrcode primitive to the layout DSL`.

### Task 4: `logo` primitive and documentation

**Files:**
- Modify: `generators/layout_dsl/primitives_codes.py`, `generators/layout_dsl/engine.py`, `generators/layout_dsl/schema.py`
- Modify: `docs/generator_capability_overview.md` (primitive table near line 370), `docs/ten_minute_tour.md` (primitive list near line 32)
- Test: `tests/layout_dsl/test_primitives_codes.py`, `tests/layout_dsl/test_codes_schema.py`

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces: `draw_logo(block: dict, ctx: RenderContext, y: int) -> int`; `LOGO_STYLES = ("circle", "square", "chevron")`; `initials(name: str) -> str`.

A logo is a generated mark, never a stored image, so no real brand can appear and it always agrees with the name it is built from: a filled shape `size` pixels square with up to two initials centred inside it. Block keys. Required: `content` (template, normally `{SUPPLIER_NAME}`), `style` (`circle` | `square` | `chevron`), `size` (> 0), `text_color` (initials colour). Optional: `align`, `color` (shape fill), `family`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/layout_dsl/test_primitives_codes.py`:

```python
from generators.layout_dsl.primitives_codes import draw_logo, initials

LOGO_FIELDS = {**FIELDS, "DX_NAME": "Mallee Quillfeather Supplies", "DX_DIGITS": "2031 4417"}


def logo_block(**overrides: object) -> dict:
    block = {"type": "logo", "content": "{DX_NAME}", "style": "circle", "size": 80, "text_color": "white"}
    block.update(overrides)
    return block


def render_logo(block: dict, width: int = 600) -> tuple[Image.Image, int]:
    image, ctx = make_drawing_ctx(fields=LOGO_FIELDS, width=width)
    return image, draw_logo(block, ctx, 0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Mallee Quillfeather Supplies", "MQ"), ("quillfeather", "Q"), ("& 2031 Marlowe", "M")],
)
def test_initials_take_the_first_letters_of_up_to_two_lettered_words(name: str, expected: str) -> None:
    assert initials(name) == expected


def test_logo_advance_is_its_size() -> None:
    _, advance = render_logo(logo_block())
    assert advance == 80


def test_each_style_draws_a_different_shape() -> None:
    images = [render_logo(logo_block(style=style))[0].tobytes() for style in ("circle", "square", "chevron")]
    assert len(set(images)) == 3


def test_initials_are_drawn_inside_the_shape_in_their_own_colour() -> None:
    image, _ = render_logo(logo_block(style="square", color="black", text_color="white", align="left"))
    inner = image.crop((10, 10, 70, 70)).convert("L")
    assert inner.getextrema() == (0, 255)  # black fill and white initials both present


def test_logo_is_deterministic() -> None:
    assert render_logo(logo_block())[0].tobytes() == render_logo(logo_block())[0].tobytes()


def test_a_name_without_letters_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{DX_DIGITS}"))
    assert_diagnostic_error(err.value)


def test_logo_absent_content_fails_fast() -> None:
    with pytest.raises(CodeBlockError) as err:
        render_logo(logo_block(content="{EMPTY}"))
    assert_diagnostic_error(err.value)
```

Append to `tests/layout_dsl/test_codes_schema.py`:

```python
GOOD_LOGO = {"type": "logo", "content": "{SUPPLIER_NAME}", "style": "circle", "size": 80, "text_color": "white"}


@pytest.mark.parametrize("key", ["content", "style", "size", "text_color"])
def test_each_required_logo_key_is_enforced(key: str) -> None:
    rejected(body=[{k: v for k, v in GOOD_LOGO.items() if k != key}], token=key)


@pytest.mark.parametrize(("override", "token"), [({"style": "star"}, "style"), ({"size": 0}, "size")])
def test_invalid_logo_values_are_rejected(override: dict, token: str) -> None:
    rejected(body=[{**GOOD_LOGO, **override}], token=token)
```

Check `command grep -rnF "Quillfeather" ground_truth/` prints nothing, and that "Mallee Quillfeather Supplies" is not on the real-name blocklist in `config/data_pools.yml`.

- [ ] **Step 2: Run them to verify they fail**

Run: `conda run -n synthetic pytest tests/layout_dsl/test_primitives_codes.py tests/layout_dsl/test_codes_schema.py -q`
Expected: the new tests FAIL (`ImportError: draw_logo`).

- [ ] **Step 3: Implement**

```python
LOGO_STYLES = ("circle", "square", "chevron")


def initials(name: str) -> str:
    """Return up to two initials: the first letter of each of the first two words containing one."""
    letters = [next(ch for ch in word if ch.isalpha()) for word in name.split() if any(c.isalpha() for c in word)]
    return "".join(letters[:2]).upper()


def draw_logo(block: dict, ctx: RenderContext, y: int) -> int:
    """Draw a generated logo: a filled shape with the name's initials inside.

    Args:
        block: The `logo` block.
        ctx: Render context.
        y: Current y-cursor.

    Returns:
        The advanced y-cursor.

    Raises:
        CodeBlockError: If the name is absent or has no letters, or the mark is
            wider than its region.
    """
    name = _resolve_content(block, ctx, "logo")
    letters = initials(name)
    if not letters:
        raise _fail(
            ctx,
            "logo",
            f"name {name!r} has no letters to take initials from.",
            "a name with at least one word containing a letter.",
            "point content at a business-name field.",
        )
    size = int(block["size"])
    x = _x_for(size, block, ctx, "logo")
    fill = _param(block, ctx, "color")
    style = str(block["style"])
    box = (x, y, x + size - 1, y + size - 1)
    if style == "circle":
        ctx.draw.ellipse(box, fill=fill)
    elif style == "square":
        ctx.draw.rectangle(box, fill=fill)
    else:
        third = size // 3
        ctx.draw.polygon(
            [(x, y), (x + 2 * third, y), (x + size - 1, y + size // 2),
             (x + 2 * third, y + size - 1), (x, y + size - 1), (x + third, y + size // 2)],
            fill=fill,
        )
    font = font_for(ctx.layout, block, size // 2, bold=True, layout_id=ctx.layout_id, layout_path=ctx.layout_path)
    left, top, right, bottom = ctx.draw.textbbox((0, 0), letters, font=font)
    text_x = x + (size - (right - left)) // 2 - left
    text_y = y + (size - (bottom - top)) // 2 - top
    ctx.draw.text((text_x, text_y), letters, font=font, fill=str(block["text_color"]))
    return y + size
```

Register `"logo": draw_logo` in the engine. Schema `PRIMITIVES`:

```python
    "logo": (("content", "style", "size", "text_color"), ("align", "color", "family")),
```

and a `kind == "logo"` branch rejecting `style` outside `LOGO_STYLES`, `size` not a positive `int`, `text_color` not a non-empty string, and validating `{FIELD}` references in `content`.

- [ ] **Step 4: Documentation**

Add `barcode`, `qrcode` and `logo` to the primitive table in `docs/generator_capability_overview.md` (near line 370) and the primitive list in `docs/ten_minute_tour.md` (near line 32), one short clause each on what they draw and that they carry no scored field. Leave `docs/pipeline_backend_comparison.md` as it is: it is a dated comparison of the DSL as it stood. British spelling, no em-dashes in new text.

- [ ] **Step 5: Run everything**

Run: `conda run -n synthetic pytest tests/ -q`
Expected: the whole suite passes (Stage 0 left 1042 passed and 11 xfailed; this stage adds tests and no new xfails); the pixel and derived snapshots are unchanged.

- [ ] **Step 6: Quality gate and commit**

Commit `generators/layout_dsl/primitives_codes.py`, `generators/layout_dsl/engine.py`, `generators/layout_dsl/schema.py`, `docs/generator_capability_overview.md`, `docs/ten_minute_tour.md` with `✨ feat: add a logo primitive and document the code primitives`.

**Stage 1 is complete when** the three primitives validate at startup, render real scannable symbols and deterministic logos, fail fast on every Review Focus input, and the plain pixel and derived snapshots are unchanged.

## Stage 1 outcome (6 October 2026)

Complete. Commits 62cc105 to 1cc2708. The local suite holds 1202 tests: 1191 pass and the 11 strict expected failures are unchanged from Stage 0. The plain pixel and derived snapshots are unchanged. Pins: `segno==1.6.6`, `python-barcode==0.16.1`, `zxing-cpp==3.1.1` (test-only); `kalam` font family vendored.

### How the primitives differ from this plan's text

Later stages must use the shipped behaviour, not the code samples above.

- The barcode key is `module_px`, not `module_width`, matching `qrcode`.
- `logo` requires `min_text_size`: initials start at `size // 2` and step down until they lie wholly inside the shape, failing fast below that floor.
- Content binding is strict: a mark fails fast if ANY field its `content` or `caption` references is absent, not only when the whole result is empty. Literal content is checked at validate time (empty, no letters for a logo, too long for a QR code at its error level, wrong EAN-13 check digit).
- A caption is measured before drawing; the mark's span is the wider of symbol and caption, and a span wider than the region fails fast.
- QR codes are drawn at exactly the configured error level (`boost_error=False`).
- Colours (`color`, logo `text_color`) are validated at startup, and a logo whose initials would match its fill is refused.

### Constraints carried into later stages

**Stage 2 (configuration and providers)**
- Gate each mark with `when:` on the same `DX_*` field its `content:` binds. `when:` takes a single field, so `DX_*` values should be emitted only when distractors are on.
- Provider values bound to marks must be unpadded: content is stripped before encoding.
- Barcode content should be an even-length, all-digit ID of at most 16 digits where a mark must fit a 294 px thermal page at 2 px per module (measured: a 14-character mixed ID needs 374 px at 2 px per module and 187 px at 1 px).
- The validator should compute the worst-case mark width per layout at validate time, so a too-wide mark is not discovered partway through `generate`.

**Stage 3 (receipt distractors)**
- Done in 389ad3d: Augraphy's dependency stack (numba, llvmlite, scikit-image, scipy and the rest) is pinned exactly, `python-dateutil`, `six` and `typing_extensions` are listed so a prune cannot remove them, and the known-good block is refreshed. A local test checks every pin against the installed version.

**Stage 5 (overlay)**
- Code marks record no geometry. When `BoxRecorder` gains a distractor role, the three drawers should record their exact rectangles so overlap and density checks can see them.

**Later**
- No startup check validates the colour keys under a layout's `defaults:`; an invalid default colour is caught only at render time (now with a four-part diagnostic for all three code marks, but not for older primitives).
