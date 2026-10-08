# Distractor Overhaul, Stage 3: Receipt Distractors

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw realistic distractors on all six receipt layouts in distractor renders, prove every one renders without clipping and with the values the providers chose, and measure page density, while plain renders stay byte-identical.

**Architecture:** Task 1 clears the groundwork carried out of Stage 2 (typed provider protocols, one alias table, two-key gating, validate-time barcode checks, a `--distractors` switch on `generate`). Task 2 gives `BoxRecorder` roles and records every text line and code mark, so a scored-ink ratio and page fill can be computed per render and bounded in YAML. Task 3 adds gated distractor blocks to the shared receipt body, wires the receipt distractor provider into every receipt layout, and pins the result with an end-to-end test and a distractor pixel snapshot.

**Tech Stack:** Python 3.12, Pillow 12.2.0, the layout DSL, segno, python-barcode, zxing-cpp (tests), pytest, conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md` sections 5 (receipt catalogue), 7 (geometry roles), 9 (density). Parent plans and their outcome sections: `docs/distractor_overhaul_plan.md`, `docs/distractor_overhaul_stage1_plan.md`, `docs/distractor_overhaul_stage2_plan.md`. The Stage 2 outcome's "How the shipped code differs" and "Constraints carried" sections are binding here.

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- YAML is the single source of truth: every new tunable (density bounds, distractor output directory, receipt field budgets for distractor text) is a required key; no Python defaults.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected and Recover, on lines starting with those labels; tests assert this with `assert_diagnostic_error`.
- Plain renders must not change: `tests/test_pixel_snapshot.py` and `tests/test_derived_snapshot.py` pass with no re-bless after every task.
- Renderers never clip; a distractor value that does not fit fails fast (budgets) and `validate` reports it (the overflow check renders with distractors on).
- Determinism: distractor renders are byte-identical across runs for the same case.
- Barcode content bound to a `DX_` key is an unpadded, even-length, all-digit string of at most 16 digits from a `digits` rule with an empty prefix.
- Geometry exports (`geometry.jsonl`, DocILE) carry scored boxes only; roles are internal.
- Line length 108; Python 3.12 types; `pathlib.Path`; Google-style docstrings; `raise ... from err` in `except` blocks.
- `tests/` is gitignored and local-only; tracked changes are committed on `main` after the gate: full `pytest tests/` (must collect), `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`, `mypy . --ignore-missing-imports`, and `python -m generators.pipeline validate`. Commit messages carry no trailers.
- Tracked files contain no tooling references and no real business names; prose uses British spelling and no em-dashes.

## Review Focus

1. **A distractor value too long for its line on the narrowest receipt** (57 mm, 294 px content): shrinks or wraps within its budget, or fails fast naming the value; never clipped. Pinned in Task 3 by rendering every case on every receipt layout with every receipt item forced on.
2. **A selected item whose block never draws** (gate key mismatch, or a block suppressed by an unrelated `when:`): the end-to-end test asserts each selected `DX_` key produced a recorded distractor box. Task 3.
3. **A barcode or QR code that does not decode** at the receipt's real size: decode every rendered mark in the end-to-end test. Task 3.
4. **Density metrics that count the wrong boxes** (scored boxes counted as distractor text, or code marks missing): unit tests on synthetic layouts with known areas. Task 2.
5. **A plain render that changes**, through a shared body edit: the plain snapshot after every task, plus a test that a plain render records no distractor-role box. Tasks 2 and 3.

---

### Task 1: Groundwork carried from Stage 2

**Files:** `generators/layout_dsl/provider_registry.py`, `generators/layout_dsl/field_providers.py`, `generators/distractors/providers.py`, `generators/eval_set.py`, `generators/overflow_check.py`, `generators/layout_dsl/schema.py`, `generators/pipeline.py`, `config/generation_config.yml`, `README.md`; local tests.

Deliver, each with tests (RED first):

1. **Typed providers.** Replace the widened `FieldProvider = Callable[..., ...]` with two Protocols: `FieldProvider.__call__(entry: dict, params: dict) -> dict[str, str]` and `DistractorProvider.__call__(entry: dict, params: dict, *, items: list[str]) -> dict[str, str]`. `field_provider` is overloaded on `distractor` (absent versus a document-type string). At registration, refuse (four-part) a distractor provider whose signature lacks a keyword-only `items` parameter (`inspect.signature`), and an ordinary provider that declares one. `apply_field_providers` narrows through `is_distractor_provider`. mypy must check both call shapes.
2. **One alias table.** `generators/eval_set.py` imports `FIELD_ALIASES` from `generators/exporters/eval_projection.py` instead of keeping its own copy; its test compares against the shared table.
3. **Overflow check scope.** Render the distractor mode only for layouts whose `field_providers` contain a distractor provider (today none, so `validate` returns to its Stage 1 timing until Task 3).
4. **Two-key gating (Ruling 7).** A block may reference several `DX_` keys when it is gated (`when:`) on one key whose catalogue item's transitive `requires` closure includes every other referenced key's item. Refuse, four-part, otherwise, naming the uncovered keys. Tests: QR content `{DX_SURVEY_URL}` with caption `{DX_SURVEY_CODE}` gated on `DX_SURVEY_URL` passes (rc_survey_url requires rc_survey_code); the reverse gate fails; BPAY biller plus reference gated on the reference passes.
5. **Barcode binding at validate time.** A `barcode` block whose `content` binds a single `DX_` key must map to a catalogue item whose rule is `digits` with `prefix: ""` and an even `length` of at most 16; and its worst-case width (build the Code 128 pattern for `"9" * length` with python-barcode, apply the block's `module_px` and `quiet_zone`, and the caption width at its role size) must fit the layout's content width. Refuse otherwise, four-part, naming the layout, item and both widths. Tests for each refusal and a passing case.
6. **`generate --distractors`.** Add a required YAML key `distractor_output_subdir` (value `distractor`) under the top level of `config/generation_config.yml`; `python -m generators.pipeline generate --distractors` writes distractor renders to `<output_dir>/<distractor_output_subdir>/<type>/` (still under `../evaluation_data/`), without geometry (scored geometry comes from plain renders). Plain `generate` is unchanged. README documents the switch.

Commit: `✨ feat: type distractor providers, gate multi-key blocks and add generate --distractors`.

### Task 2: Geometry roles and density

**Files:** `generators/exporters/geometry.py`, `generators/layout_dsl/primitives_text.py`, `primitives_table.py`, `primitives_container.py` (if it draws text), `primitives_codes.py`, `generators/distractors/density.py` (new), `config/distractors.yml` (new `density:` block), `generators/distractors/config.py` (load it), `generators/pipeline.py` (`validate` enforces bounds), `README.md`; local tests.

Deliver, each with tests:

1. **Roles.** `BoxRecorder` keeps `record(field, box)` (scored, exported as today) and gains `record_ink(box, role)` where `role` is `"scored"`, `"distractor"` or `"text"`, plus `ink_boxes() -> list[tuple[str, tuple[int, int, int, int]]]`. Every primitive that draws text records each drawn line's box with `record_ink`: role `scored` when the block or cell carries `field:`, `distractor` when the block references any `DX_` key, otherwise `text`. Code marks record their exact drawn rectangle (symbol plus caption span) as `distractor`. `as_dict()` and every export are unchanged.
2. **Metrics.** `density.py` provides `scored_ink_ratio(boxes) -> float` (scored area over all text-role area: scored plus distractor plus text, excluding code-mark rectangles from the denominator's text area but counting them in a separate `mark_area`), `page_fill(boxes, page_height) -> float` (lowest ink y over page height), and `density_report(...)` returning both.
3. **Bounds.** `config/distractors.yml` gains a required `density:` mapping per document type with `scored_ink_ratio_max` and `page_fill_min` (receipts: choose bounds from the measured Task 3 renders, recorded in a YAML comment with the measurement date; invoices: the spec's `0.25` and `0.75`; bank statements: measured). The loader validates them (floats in [0, 1]). `validate` computes density for every distractor render of a layout that carries a distractor provider and refuses a render outside its bounds (four-part, naming case, layout, metric, value and bound); plain renders are measured but never enforced, as the spec says. In this task no layout carries a provider yet, so set the receipt bounds after Task 3 measures them: commit Task 2 with receipt bounds that every current distractor render passes and a comment saying Task 3 measures the real ones.
4. **Tests:** synthetic layouts with known box areas give exact ratios; a scored box never counts as distractor; a plain render records no distractor-role box; code marks are recorded with their exact rectangle; bounds refusals are four-part.

Commit: `✨ feat: record ink roles and bound distractor density`.

### Task 3: Receipt distractor blocks

**Files:** `config/layouts/receipts.yml`, `config/distractors.yml` (receipt density bounds from measurement), local tests and a new fixture `tests/fixtures/distractor_pixel_snapshot.json` with its regenerate script (dry run unless `--confirm`, refusing an unpinned Pillow like the plain one).

Design:
- The receipt field-providers anchor gains `{name: receipt_distractors, params: {}}`; a new anchor `&receipt_distractor_items` lists all 14 receipt items, used by every receipt layout's `distractor_items` (the shared body draws them all; the provider picks 3 to 6 per case).
- The shared `_receipt_body` gains gated blocks in the places a real receipt carries them: header (a `logo` built from `{SUPPLIER_NAME}` and gated with `when: DX_STORE_NUMBER`, so it appears only in distractor renders and only alongside the store line, which Ruling 6 permits; store number and merchant ID line; ACN; phone; website), POS area (transaction ID text), body foot (member number and points balance; "You saved"; item count), footer (returns-by date; returns policy text; a `barcode` of `{DX_TRANSACTION_ID}`; a `qrcode` of `{DX_SURVEY_URL}` with caption `Survey code {DX_SURVEY_CODE}` gated on `DX_SURVEY_URL` per Ruling 7). Every distractor block carries `when:` on its own key; every label that prints a value carries the realistic wording (for example "Returns by", "Member", "You saved $"), written in YAML.
- Distractor text uses the layout's monospace body face and fits through field budgets: add per-layout budgets (for example `DX_LINE` shrink-then-wrap, `DX_PARAGRAPH` wrap up to the lines a real policy uses) to each receipt layout's `field_budgets`, sized to that layout's content width. No distractor text may use a scored field's budget.
- Barcode and QR sizes per layout satisfy Task 1's validate-time check on the 294 px layout (module 2 px for a 16-digit Code 128 fits 286 px with a 10-module quiet zone; QR at 3 px per module).

Tests (all local), RED first where the layouts are not yet changed:
- **End to end:** for every receipt case on its own layout, render with distractors on: every selected `DX_` key produced at least one `distractor` ink box; every barcode and QR decodes (zxing-cpp) to its `DX_` value; the plain render is byte-identical to the plain snapshot.
- **Forced:** every receipt case on every one of the six receipt layouts with all 14 items forced on renders without error (Review Focus 1), and `validate` passes.
- **Distractor snapshot:** pin every receipt's distractor render (hash, size, scored boxes) in `tests/fixtures/distractor_pixel_snapshot.json`; determinism across two runs.
- **Density:** measure the six layouts' distractor renders; set the receipt bounds in `config/distractors.yml` with a comment giving the measured range; a render outside them fails `validate`.
- **Visual check:** run `python -m generators.pipeline generate --type receipts --distractors`, open at least one image per layout with the Read tool, and describe in the report what you see (placement, legibility, anything that looks wrong).

Commit: `✨ feat: draw distractors on every receipt layout`.

**Stage 3 is complete when** every receipt renders its selected distractors legibly and scannably, plain renders are unchanged, density is measured and bounded, and `validate` checks all of it.

## Stage 3 outcome (7 October 2026)

Complete. Commits fa3f1ce to 85eaf86. The local suite holds 1888 tests: 1877 pass and the 11 strict expected failures are unchanged. Plain pixel and derived snapshots are unchanged; a distractor pixel snapshot pins all 55 receipt distractor renders. `pipeline validate` passes, rendering receipts in both modes and enforcing receipt density.

### How the shipped code differs from this plan's text

- **Code 128 is encoded in-repo** (`generators/layout_dsl/code128.py`): python-barcode 0.16.1 silently drops a leading `99` digit pair. python-barcode remains for EAN-13 only.
- **The survey code prints as its own line** gated on `DX_SURVEY_CODE`, not as the QR caption, because it can be selected without the URL.
- **Density bounds by page kind.** Every type's `density:` block has `scored_ink_ratio_max`, `distractor_share_min` and `page_fill_min`; `page_fill_min: null` (explicit) for content-cropped receipts. Receipts: 0.425 / 0.05 / null, measured on 6 October 2026. Invoices (0.25 / 0.30 / 0.75) and bank statements (0.80 / 0.0 / 0.60) are starting values that enforce nothing until those types carry distractor providers.
- **Ink** is recorded for every text line (role `scored`, `distractor` or `text`) and code mark in every distractor render; callers receive it through `geometry_out` or a new optional `ink_out` list; exports stay scored-only. Rules are not ink.
- **"You saved"** uses a new `amount_fraction` rule (3% to 25% of `TOTAL_AMOUNT`).
- **Barcodes** require a `caption_gap` key. Code marks must be gated on a `DX_` key. `DX_PREFIX` lives in `generators/distractors/keys.py`.

### Constraints carried into later stages

**Stage 4 (invoices)**
- Plain invoices measure scored-ink ratio 0.58 to 0.75 and page fill 0.25 to 0.31 against the spec targets 0.25 and 0.75: the expanded catalogue and the four industry layouts must add a great deal of text. Measure the distractor renders and set the invoice bounds from them (with headroom), as receipts did; record that spec section 9 calls these starting bounds.
- Adding a distractor provider to a layout switches enforcement on at once (Ruling 9): land a layout's distractor blocks and its bounds together.
- Sequence: the two rendering fixes (with their pixel-snapshot change notes), then the distractor blocks, then the four layouts, then the reseed and re-measurement.
- Content: the product catalogue in `config/data_pools.yml` contains real brand names (for example "Panadol 24pk"); the business-name blocklist does not cover products. Screen and replace them during the reseed. Merchant category and products can also mismatch (a cellar selling yoghurt).

**Stage 5 (overlay)**
- Use `ink_out` (every text line and code mark, with roles) for overlay avoidance, padding scored boxes by the descender allowance carried from Stage 0. The overlay runs on the flat page, before degradation. Overlay marks count in density: measure after compositing.

**Stage 7 (export)**
- `generate` and `eval-set` do not run the density check on every render (spec section 9 asks that checks run on every render); add it to the export.
