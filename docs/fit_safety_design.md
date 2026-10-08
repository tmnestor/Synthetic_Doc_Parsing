# Phase 0 — Fit Safety: Design Spec

> Status: approved design. Precursor to the content-variety
> programme. This spec covers **fit safety only** — the guardrail that makes later
> content widening (Phases 1–3) safe. It changes no content and is designed to
> leave the current 220 documents byte-identical.

Precursor to the content-variety programme, whose options paper has since been
retired; it remains in git history.

---

## 1. Goal & the contract it protects

Guarantee that every rendered image shows the **exact full string** stored in
ground truth — no horizontal clipping, no column collision, no silent truncation
— so the pixel-perfect image ↔ ground-truth contract holds **by construction**,
for both today's 220 frozen documents and future widened content.

Overflow becomes *structurally impossible* for anything a layout can actually
hold. The only failure path is genuinely-impossible geometry (a string that
cannot fit even at the font floor across the reserved line band), which fails
loud with a diagnostic — never silently.

### Why this must ship before content widening

The renderers place text into fixed positions with no wrapping, truncation, or
fit-to-width check (`generators/common.py`: `draw_text_center`,
`draw_text_right`, `draw_line_item` use `font.getbbox()` for alignment only).
Widening content produces longer, variable-length strings that would silently
clip or overlap — corrupting the benchmark, because the GT YAML stores the full
string while the image shows a clipped one. An LLM is then scored against text it
cannot see. Fit safety and content variety must not be decoupled; this is the
prerequisite.

---

## 2. YAML schema — budgets are the single source of truth

Each layout in `config/layouts/*.yml` gains a `field_budgets` block. Every
variable field a renderer draws gets an explicit entry. **All keys are required**
— per the project's YAML-is-source-of-truth and fail-fast rules, there are no
silent Python defaults. A missing field entry, or a missing key within one, is a
startup validation error.

```yaml
field_budgets:
  NAME_ADDRESS:
    width: 380              # px — the field's horizontal box
    fit: shrink_then_wrap   # one of: shrink | wrap | shrink_then_wrap
    min_font: 12            # font-size floor; below this -> fail, do not shrink further
    max_lines: 2            # vertical band reserved for this field (>=1)
  AMOUNT:
    width: 120
    fit: shrink
    min_font: 14
    max_lines: 1            # non-wrapping fields declare max_lines: 1 explicitly
```

### Key semantics

| Key | Meaning | Constraints |
|-----|---------|-------------|
| `width` | Horizontal box in px the string must fit within | Positive int. **Derived from current renderer geometry** (see §5) so day-one output is unchanged. |
| `fit` | Allowed lossless fit strategy for this field | Enum: `shrink`, `wrap`, `shrink_then_wrap`. |
| `min_font` | Smallest font size fitting may shrink to | Positive int ≤ the field's nominal size. |
| `max_lines` | Lines the field may occupy (reserved vertical band) | Int ≥ 1. Non-wrapping fields (amounts, table columns) use `1`. |

### Fit-strategy guidance per field class

- **Amounts, table-row columns** — cannot wrap without breaking the row grid →
  `fit: shrink`, `max_lines: 1`.
- **Header blocks** (business name, address) where the layout can reserve
  vertical space → `fit: wrap` or `fit: shrink_then_wrap`, `max_lines: 2+`.

Operator intent is fully visible in the YAML: reading `field_budgets` alone
answers "how is this field allowed to fit?" without consulting Python.

---

## 3. Fit mechanism — `fit_text()` in `common.py`

A shared helper that renderers call instead of drawing text raw. It is the
mechanism that enforces the contract; validation (§4) is only a backstop.

**Inputs:** the full string, the field's budget (`width`, `fit`, `min_font`,
`max_lines`), the draw origin, and the resolved bundled font.

**Algorithm:**

1. Measure the string with `font.getbbox()` at the field's nominal size.
2. **If it fits** the `width` at nominal size → draw as-is. This is the day-one
   path for all 220 current documents (widths are derived from existing
   geometry), so there is **no visual change** to the passing corpus.
3. **If it does not fit**, apply the field's `fit` strategy losslessly:
   - `shrink` — reduce font size toward `min_font` until the string fits on one
     line.
   - `wrap` — break the string across up to `max_lines`, each line within
     `width`, at the nominal size.
   - `shrink_then_wrap`: try each size from the nominal size down to
     `min_font`; at each size, wrap the string within `max_lines` and take the
     first size at which the wrapped lines fit. Wrapping at the nominal size is
     therefore preferred over shrinking, and the string shrinks only when
     wrapping at the current size cannot fit. If nothing fits at `min_font`,
     it raises the error in step 4. The name is historical; the order is
     wrap-first, deliberately.
4. **If the full string cannot fit even at `min_font` across `max_lines`** →
   raise a four-element diagnostic error (§6). The helper **never truncates or
   ellipsizes** — the only outcomes are "fits losslessly" or "fails loud."

The full string that renders is always exactly the string GT stores; the image ↔
GT contract is preserved regardless of length.

---

## 4. Validation backstop — `pipeline validate`

`python -m generators.pipeline validate` runs the same measurement across every
ground-truth entry against its layout's budgets. It **collects all** genuinely-
unfittable violations into a single report, then exits non-zero (fail on the
batch, not on the first offender — so a reseed surfaces every problem in one
pass).

This gate fires **only** for impossible geometry — content that cannot fit even
with lossless shrink+wrap. Routine length variation is handled at render time by
`fit_text()` and does **not** trip validation. When it does fire, the offending
field represents a real layout design error a human must resolve (widen the box,
raise `max_lines`, or lower `min_font` in the YAML).

The overflow check also runs inside `generate` before rendering, so a clipped
image can never be produced even if `validate` is skipped.

---

## 5. Deriving budgets from current renderer geometry

Today the layouts carry no per-field boxes; geometry is hardcoded in the
renderers (e.g. `draw_line_item` right-aligns the amount at `content_width`, so
the description box is `left_margin → amount_x − gap`). Phase 0 does **not**
migrate all geometry into YAML (that is out of scope, §8). Instead:

1. Read each variable field's effective horizontal box off what the renderer
   already draws.
2. Commit those widths into the layout `field_budgets` as the authoritative
   source of truth.
3. Renderers call `fit_text(field, ...)` which looks up the budget; they keep
   their existing x/y drawing. Only the *fit constraint* is new.

Because the derived widths match current reality, all 220 documents render
unchanged on day one.

---

## 6. Determinism preconditions

Pixel measurement is only safe if the font is identical on the local Mac and on
PROD Linux.

1. **Bundled fonts (verified).** All four DejaVu faces
   (`fonts/DejaVuSans*.ttf`, `DejaVuSansMono*.ttf`) are committed, git-tracked,
   and `load_font()` searches the bundled directory first; system fonts are
   fallbacks only. So both platforms load the same TTF and `getbbox()` agrees.
2. **Pin Pillow.** `environment.yml` currently has a bare `- pillow`, while
   `faker` and `opencv-python-headless` are pinned for byte-stable reproducibility.
   Pillow's FreeType version drives `getbbox()` widths → every shrink/wrap
   decision → the rendered pixels. Pin it (`pillow==<current>`, version captured
   from the maintainer's live env at implementation time) with a comment
   mirroring the faker/opencv rationale.
3. **Fail-loud font-source guard.** `load_font()` silently falls back to system
   fonts when a bundled file is missing. `fit_text()` must measure against the
   *bundled* face; add a diagnostic hard-fail if the loaded font is not the
   bundled one, so a missing bundle can never silently degrade to Helvetica on
   Mac and diverge from PROD.

### Diagnostic error shape (fail-fast, four elements)

Both the render-time impossible-fit error and the validation backstop use the
project's four-element diagnostic (**what / where / what-it-should-look-like /
how-to-recover**), e.g.:

```
✗ Content overflow: field NAME_ADDRESS cannot fit even at the font floor.
  Entry:    CASE037  (receipts / receipt_thermal_57mm)
  Field:    NAME_ADDRESS
  Measured: 412px at min_font 12 across max_lines 2  ·  Budget width: 380px
  String:   "Nguyen & Associates Chartered Accountants Pty Ltd"

  Where:    config/layouts/receipt.yml
            -> receipt_thermal_57mm.field_budgets.NAME_ADDRESS
  Expected: width >= measured, or increase max_lines, or lower min_font.
  Recover:  raise `width` to >=412 (or `max_lines` to 3) under that key.
            Budgets are the single source of truth — never silently truncate.
```

---

## 7. Testing (TDD, local `tests/`)

Tests are local-only (`tests/` is gitignored). Minimum 80% coverage.

- **`fit_text` core:** returns text that fits within the box; **never** returns a
  truncated/ellipsized string; output is deterministic across runs.
- **Per strategy:** `shrink` floors at `min_font`; `wrap` respects `max_lines`
  and `width`; `shrink_then_wrap` shrinks before wrapping.
- **Impossible fit:** raises the four-element diagnostic (shared
  `assert_diagnostic_error` helper asserts all four elements present).
- **Validation backstop:** collects *all* violations into one report and exits
  non-zero; does not trip on content that fits via lossless fit.
- **Font-source guard:** raises a diagnostic when the bundled font is absent.
- **Regression:** all 220 current documents render byte-identically to
  pre-change output (day-one derived widths cause no visual change).

---

## 8. Scope / non-goals

**In scope:**
- `field_budgets` schema in `config/layouts/*.yml` (all keys required).
- `fit_text()` in `generators/common.py` and renderer wiring.
- Overflow validation backstop in `pipeline validate` (and `generate`).
- Pillow pin in `environment.yml`.
- Bundled-font fail-loud guard.
- Tests + all-four-doc-type regression.

**Out of scope (later work):**
- Content widening — bigger pools, procedural composition, edge-case matrix
  (Phases 1–3 of the content-variety programme).
- Generation-time content regeneration/shortening in `seed_ground_truth.py`.
- Full geometry-to-YAML migration of renderer coordinates.

---

## 9. Key files

- `config/layouts/*.yml` — gains `field_budgets` per layout.
- `generators/common.py` — `fit_text()`, font-source guard.
- `generators/pipeline.py` — validation backstop wiring (`validate`, `generate`).
- `generators/{bank_statement,receipt,invoice,cc_statement}.py` — call `fit_text`.
- `environment.yml` — Pillow pin.
