# Distractor Overhaul, Stage 5: Overlay

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw the overlay layer on distractor renders: a PAID stamp on paid invoices, and a taxpayer's annotations on bank statements (highlighter over the rows claimed as work-related expenses, ink ticks, circles and underlines beside them, handwritten notes such as 'WRE' and a handwritten running total of the claimed rows), with every scored value still printed verbatim and legible. Exit criterion: across all 165 cases no ink or stamp pixel meets a scored box; highlighter, the one deliberate exception, overlays rows and keeps their text above a contrast threshold.

**Architecture:** Task 1 builds the overlay layer with no shipped consumer: a required `overlay:` list on every layout, an `overlay:` settings block in `config/distractors.yml`, a renderer hook that runs the overlay on the flat page after `render_body`, and a verifier that checks every composited page (ink pixels against padded protected boxes, highlighted text against a contrast threshold) and fails fast. Task 2 adds the `stamp` primitive and puts the PAID stamp in the paid group of the invoice payment state. Task 3 wires the bank distractor provider into the eight bank layouts with the bank's inline items, a page-fit check for the fixed-height statement and measured bank density bounds. Task 4 adds the annotation primitives (highlight, ink marks, handwriting in the vendored Kalam face), draws them on every bank layout, re-measures bank density after compositing and pins bank statements in the distractor snapshot.

**Tech Stack:** Python 3.12, Pillow 12.2.0 (`Image.alpha_composite`, `ImageChops.multiply`, `ImageDraw`), the layout DSL, the distractor package, vendored OFL handwriting faces (Kalam, plus Caveat, Indie Flower and Patrick Hand added in Task 4), pytest, zxing-cpp (tests), conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md` sections 3 (PAID stamp overlap, bank annotation overlap, annotated rows), 4 (overlay layer), 5 (invoice overlay row, bank statement table and the handwritten-total note), 7 (overlay primitives, geometry), 8 (overlay settings), 9 (geometry and density checks), 12 (overlay tests). The outcome sections of `docs/distractor_overhaul_plan.md` (Stage 0), `docs/distractor_overhaul_stage3_plan.md` and `docs/distractor_overhaul_stage4_plan.md` are binding; their constraints carried into Stage 5 drive Tasks 1, 2 and 4. Where the spec is silent or disagrees with the code, the Decisions below rule.

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- YAML is the single source of truth: overlay colours, opacities, rotation, stroke and glyph jitter, handwriting sizes, clearance, contrast threshold, protection policy, stamp candidates and every new rule parameter are required YAML keys; Python holds no defaults and no colour or size constants.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected (with a YAML example) and Recover, on lines starting with those labels; tests assert this with `assert_diagnostic_error`.
- Scored values are printed verbatim and stay consistent with the layout. No reseed in this stage: ground truth, links and transaction counts are unchanged.
- The overlay runs only on distractor renders, inside the document renderer, on the flat page before anything else touches it (degradation, export). Plain renders do not change: `tests/test_pixel_snapshot.py` and `tests/test_derived_snapshot.py` pass with no re-bless after every task. Geometry exports carry scored boxes only; overlay marks are internal ink.
- Pixel changes are deliberate: the distractor snapshot is re-blessed for invoices in Task 2 and gains bank statements in Task 3 (re-blessed for them in Task 4); receipt entries in it never change.
- Renderers never clip and never skip a mark silently: a mark with no legal placement fails fast naming the case, layout, block and the boxes that blocked it.
- Determinism: a distractor render, overlay included, is byte-identical across runs for the same case.
- Adding a distractor provider to a layout switches density enforcement on at once (Ruling 9): land a layout's distractor blocks and its density bounds in the same commit.
- No real business or brand names in any pool, layout, test or prose; invented example values are checked absent from `ground_truth/`.
- Line length 108; Python 3.12 types; `pathlib.Path`; Google-style docstrings; `raise ... from err` in `except` blocks.
- `tests/` is gitignored and local-only. Per-task gate (decided by the project owner): `conda run -n synthetic pytest tests/ -m "not slow"` plus the slow tests named in the task, `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`, `mypy . --ignore-missing-imports`, `python -m generators.pipeline validate`. The full suite runs once, at the end of Task 4. One review per task; review fixes are applied without a second review. Commit messages carry no trailers.
- Prose uses British spelling and no em-dashes; no tooling references in tracked files.

## Decisions

Numbered so reviews can cite them. Each records where the spec is silent or disagrees with the code, and why.

1. **The bank's inline items are drawn in this stage.** The spec's bank catalogue has inline items (issued date, mailing barcode, promotional box) that no stage schedules, and wiring `bank_distractors` into a layout makes validation require every listed item to be drawn and switches density on. Task 3 draws them, so the bank bounds are set once over every bank distractor.
2. **The dated PAID stamp is a `repeat` of INVOICE_DATE, not a near-miss.** The spec dates the stamp with the linked bank transaction's date and lists INVOICE_DATE as its near-miss target, but in the corpus every invoice link's bank date equals its invoice date (55 of 55). A stamp date that differed would contradict the statement the invoice is linked to. The date item is therefore a repeat drawn by a `link_date` rule from `ground_truth/transaction_links.yml`; the validator's repeat check (equal to the raw field, else fail) pins the coherence, and a future reseed that pays invoices later fails fast there, prompting a change of kind.
3. **Several handwriting faces, one hand per statement.** One face across every annotated statement is itself a pattern a model can learn, so Task 4 vendors three more OFL handwriting faces beside Kalam: Caveat, Indie Flower and Patrick Hand (static TTFs with their licence files, from the upstream Google Fonts sources; where upstream ships only a variable font, a static instance is cut with fontTools and the command recorded beside the licence). Each statement draws one face and weight on the overlay seed, and every note and sum on that statement uses it (one taxpayer, one hand); glyph jitter varies the strokes within it. No new runtime dependency.
4. **What opaque ink may not touch is declared per document type.** Bank statements protect every recorded ink box of every role (scored, distractor, text) and every code mark: TRANSACTION_DATES is a scored extraction field, but no bank layout records a box for it (the date column carries no `field:`, and date-grouped layouts print one date per group), so protecting scored boxes alone would let a tick land on a scored date. Invoices protect scored boxes and code marks: the spec lets the stamp cover labels and unscored text, and protecting marks keeps every barcode decodable. The policy is a required key, `overlay.protect`, so the YAML says what each type protects.
5. **Overlap is verified on ink pixels, not bounding boxes.** A circle's bounding box necessarily encloses the amount it rings, so a box test would forbid circles outright. The spec's 'ink box' is read as the drawn stroke: after compositing, no pixel of an opaque overlay layer (alpha above zero) lies inside any padded protected box. Stamp placement is stricter still: its whole rotated footprint, border included, must clear every padded protected box, and the pixel check then confirms it.
6. **Contrast is checked on every box a highlighter band meets, relative to its starting legibility.** A scored box under a band must meet `overlay.contrast.min_ratio` after compositing. Any other ink box under a band that met the ratio before compositing must still meet it after (this protects the unboxed scored dates). Text already fainter than the ratio before compositing (grey reference sub-lines) is exempt, because the overlay did not make it illegible. Contrast is the WCAG ratio between a dark and a light luminance percentile inside the box, both percentiles declared in YAML.
7. **Density counts what a reader sees as writing.** Stamp text and handwriting record distractor text ink; ticks, circles and underlines record distractor marks (`mark=True`, outside the text denominators, as code marks are); highlighter records nothing, being a tint over text already counted, as rules are not ink. The overlay records before the renderer fills `geometry_out`, so `validate` and every density test measure the composited page.
8. **Content comes from the catalogue; drawing comes from the overlay seed.** What is drawn (the annotated rows, each row's mark kind, the stamp word, the note, the total) is drawn by catalogue rules on the existing distractor seeds and screened by the value validator. How it is drawn (placement order, colour choice, rotation, stroke and glyph jitter) comes from a new seed domain, `sha256("{case_id}:overlay:{doc_type}:{block_index}")`, so no existing value or selection moves and adding a block does not shift another block's draws.
9. **The highlighter is the drawing of `bk_annotated_rows`.** The highlight block is gated on `DX_ANNOTATED_ROWS`, so an annotated statement always carries highlighter and no selected key draws nothing. Ticks, circles and underlines are a separate item, `bk_row_marks`, whose `row_marks` rule assigns one mark kind per annotated row; no mark kind is replaced by another at render time, and a layout's ink-marks block must be able to draw every kind the rule can choose (validated).
10. **The stamp's date is an optional key of the stamp block.** The stamp block is gated on `DX_PAID_STAMP` and names `date_key: DX_PAID_DATE` (or explicit `none`). This is the converse of Ruling 7: the optional key's item must require the gate's item, which validation checks; the date line prints only when that key is present.
11. **The bank statement gains a page-fit check, and is not reseeded.** A statement is a fixed-height page, like an invoice, but its renderer has no overflow check today; the fullest corpus statement ends about 216 px above the bottom margin. Task 3 adds the invoice's four-part check to the bank renderer, places inline items beside existing lines or foot-anchored rather than stacked, and leaves each layout's `transaction_count` alone.
12. **`overlay:` is required on every layout; receipts must declare `[]`.** The spec makes receipts inline-only, and a receipt is cropped to its content after drawing, which an overlay would have to survive. Validation refuses a non-empty receipt overlay.
13. **Clearance is a YAML key pinned by a test.** `overlay.clearance_px` pads every protected box. Stage 0 measured descender ink 3 px below recorded line-item boxes and set the geometry tests' allowance at 4 px; the shipped value is at least that, and a test compares it with the geometry tests' `DESCENDER_ALLOWANCE` rather than the loader holding a Python minimum.
14. **The bank share floor is set from measurement and may be zero.** A statement's selected distractors can be entirely marks and tint (mailing barcode, highlighter, ticks), whose text share is zero. If the measured minimum is zero the floor stays an explicit `0.0` with its reason in the YAML comment, and a test carries the separation instead: every bank distractor render records at least one distractor ink box or mark, and no plain render does.
15. **Adding the stamp items reshuffles invoice selections.** Selection samples from the layout's eligible list, so two new items change which items most invoices draw (values stay fixed: they are seeded per item). Task 2 re-measures invoice density; the shipped bounds stay if every render meets them, otherwise they are re-set by the Stage 4 rule (at least 10% relative headroom over the corpus and the synthetic entries) with a dated comment, never moved without the measurement.
16. **Stamp candidates are realistic positions, not the blank band.** Stage 4 records that short invoices keep up to about 1000 px blank above the footer and that the overlay must not be relied on to fill it. Each invoice layout's stamp candidates sit where a clerk stamps: beside the title and invoice details, over the totals panel, or across the upper body.

## Review Focus

1. **Ink or stamp touching a scored value**, including the unboxed scored dates (Decision 4) and descenders below a recorded box (Decision 13). The verifier checks every distractor render's pixels and fails fast; a fixture forcing a stamp candidate onto TOTAL_AMOUNT and one forcing a tick onto a date cell must both fail with the four-part diagnostic; the per-case tests in Tasks 2 and 4 prove the check passes on all 165 cases. Tasks 1, 2 and 4.
2. **An annotation on the wrong row.** Row indices in `DX_ANNOTATED_ROWS` are ground-truth row positions, while the table may draw an opening-balance row, group dates on their own rows, or interleave credits. For every bank case: each highlighted band contains the recorded `TRANSACTION_AMOUNTS_PAID[i]` box of each annotated index and no other row's paid box; each linked row (matched by date, amount and description from the links file) is highlighted; and the handwritten total equals the sum of the ground-truth amounts of exactly the highlighted rows. Task 4.
3. **Highlighter that makes text illegible**: a dark colour, two adjacent bands multiplied twice where they overlap, or faint text pushed below the threshold. Bands are unioned into one mask before a single multiply; fixtures with a dark colour and with two adjacent annotated rows prove the contrast check can fail and that overlaps never darken twice. Tasks 1 and 4.
4. **Density measured without the overlay.** If `geometry_out` is filled before the overlay records its ink, or highlighter is counted as text, the bank bounds are set from the wrong numbers. A test asserts that a distractor render's `geometry["ink"]` holds the overlay's records and no highlighter record, and that `validate`'s density refusal fires for a composited page pushed past a bound. Tasks 1 and 4.
5. **A bank statement pushed off its fixed page** by inline distractors or a running total below the table. The new page-fit check fails fast; the forced test renders every bank case on every bank layout with every bank item on and asserts each fits, and the running total's placement stays above the bottom margin. Tasks 3 and 4.

---

### Task 1: The overlay layer and its verifier

**Files:** `generators/overlay/__init__.py`, `generators/overlay/engine.py` and `generators/overlay/verify.py` (new); `generators/distractors/config.py` (load `overlay:`); `config/distractors.yml` (`overlay:` block, first part); `generators/layout_dsl/engine.py` (`entry_out`); `generators/layout_dsl/schema.py` (`overlay:` key on every layout); `generators/invoice.py`, `generators/bank_statement.py`, `generators/receipt.py` (call the overlay); `config/layouts/receipts.yml`, `invoices.yml`, `bank_statements.yml` (`overlay: []` on every layout); `README.md`; local tests.

Interfaces:
- `render_body(..., entry_out: dict | None = None)`: when given, filled in place with the provider-enriched entry (ground truth plus every selected `DX_` value), as `geometry_out` is. Return value unchanged.
- `apply_overlay(image, layout, entry, *, layout_id, layout_path, doc_type, recorder) -> OverlayResult`: draws each `overlay:` block whose `when:` key is present, in order, onto the RGB page in place; records each mark's ink on `recorder`; then verifies the composited page. Never called on a plain render. `OverlayResult` lists `OverlayMark(key, kind, box, opaque)` per drawn mark, so tests can ask what each key drew.
- `generators/overlay/verify.py`: `protected_boxes(ink, policy, clearance)` (padded pixel boxes per Decision 4, from ink records rather than normalised geometry, so they are pixel-exact); `check_clear(alpha, protected, ...)` (Decision 5); `contrast_ratio(image, box, settings)` and `check_contrast(before, after, bands, ink, settings, ...)` (Decision 6). Each check raises `OverlayError` naming case, layout, block, the mark and every protected box it met.
- An overlay block primitive registry (`OVERLAY_DRAWERS`), empty in this task, so Tasks 2 and 4 register `stamp`, `highlight`, `ink_marks` and `handwriting`.
- Each renderer runs the overlay after `render_body` and before filling `geometry_out` and `ink_out` (Decision 7). The receipt renderer only asserts its overlay is empty, before cropping.

Required YAML (`config/distractors.yml`, new top-level `overlay:`; `_TOP_LEVEL` gains it). This task adds the shared keys; Tasks 2 and 4 add `stamp:`, `highlighter:`, `ink:` and `handwriting:` and the loader requires each from then on:
- `overlay.clearance_px` (int, at least 1; Decision 13).
- `overlay.page_inset_px` (int): no mark nearer a page edge.
- `overlay.protect: {receipt: none, invoice: scored_and_marks, bank_statement: all_ink}` (enum `none`, `scored_and_marks`, `all_ink`; every document type present).
- `overlay.contrast: {min_ratio, dark_percentile, light_percentile}` (ratio above 1; percentiles in [0, 100], dark below light).
- Every layout: `overlay: []` (list of blocks). Each block carries `type`, `when` (a `DX_` key the layout's items emit) and its type's own keys.

Fail-fast diagnostics (four-part, each tested):
- Missing or mistyped `overlay` key, or any sub-key: Where `<abs>/config/distractors.yml -> overlay.<key>`; Expected the key with an example (`overlay: {clearance_px: 6, ...}`) and allowed values for enums; Recover 'add the key under overlay:'.
- A layout without `overlay:`: Where `<abs>/config/layouts/<type>.yml -> <layout>.overlay`; Expected `overlay: []` or a list of blocks; Recover 'add overlay: [] to the layout'.
- A non-empty receipt overlay (Decision 12); an unknown block type (listing the registered ones); a `when:` key no listed item emits; a key referenced by a block but not covered by its gate (Ruling 7).
- At render: a mark meeting a protected box, a highlighted box below contrast, a mark outside the page inset, each naming case, layout, block path `<layout>.overlay[n]`, the mark's box and the blocking boxes; Recover names the block's candidates or the overlay settings to change.

Tests (RED first; synthetic layouts and a test-only overlay drawer registered by a fixture, since no shipped layout has an overlay yet):
- Plain renders of every type never call the overlay; every shipped layout declares `overlay: []`; plain and derived snapshots unchanged; the distractor snapshot unchanged (no shipped overlay).
- `entry_out` carries the selected `DX_` values; omitting it renders identically.
- Verifier: a test mark drawn inside a padded scored box fails; one drawn 1 px outside the padding passes; one in the descender band below a recorded box (inside the padding) fails; under `all_ink` a mark over a text-role box fails while under `scored_and_marks` it passes; a mark over a code mark fails under both.
- Contrast: black text under a light tint passes; under a dark tint fails with the diagnostic; grey text already below the ratio is exempt unless it is scored; a scored box below the ratio fails.
- Ink and density: overlay ink appears in `geometry["ink"]` and `ink_out`; a tint records nothing; a mark records `mark=True`; `measure_render` on a synthetic page includes the overlay's text ink.
- Each loader and schema refusal above, through `assert_diagnostic_error`; `overlay.clearance_px` in the shipped YAML is at least the geometry tests' `DESCENDER_ALLOWANCE`.

Visual check: none needed beyond confirming one invoice and one bank distractor render are byte-identical to before (no shipped overlay).

Commit: `✨ feat: add the overlay layer with geometry and contrast checks`.

### Task 2: The PAID stamp on paid invoices

**Files:** `generators/overlay/stamp.py` (new); `generators/distractors/values.py` (`pick` and `link_date` rules, with their parameters and redraw behaviour declared beside the other rules); `config/distractors.yml` (two items, the paid group, `overlay.stamp`, invoice density re-measured); `config/layouts/invoices.yml` (stamp block and candidates on all eight layouts; both items in the shared invoice items anchor); `generators/layout_dsl/schema.py` (stamp block validation); `tests/snapshot_tools.py` and the distractor snapshot fixture; `README.md`; local tests.

Catalogue additions:
- `in_paid_stamp`: clutter, emits `DX_PAID_STAMP`, rule `{type: pick, values: [PAID, PAID IN FULL, RECEIVED]}` (spec section 8 puts stamp words in this file; the value screen checks them like any clutter). `pick` requires a non-empty list of non-empty strings and draws one on the item's seed; a collision redraws.
- `in_paid_date`: repeat of INVOICE_DATE, emits `DX_PAID_DATE`, requires `in_paid_stamp`, rule `{type: link_date}` (Decision 2). The rule finds the case's invoice image in the links file and returns its `bank_date`; it fails at once (four-part, Where the links file and the image key) when the invoice has no link or more than one.
- `states.invoice.payment.items.paid` gains both items, so the stamp never appears with the remittance slip or the account summary (Stage 4 carried constraint).

Required YAML, `overlay.stamp`: `colours` (list of hex), `opacity: [min, max]`, `rotation_deg: [min, max]` (about plus or minus 15), `font: {family, bold, size}`, `date_size`, `border_px`, `padding_px`, `speckle: [min, max]` (fraction of stamp pixels knocked back, for ink variation). Each invoice layout's stamp block: `{type: stamp, when: DX_PAID_STAMP, content: "{DX_PAID_STAMP}", date_key: DX_PAID_DATE, candidates: [{x, y}, ...]}` with candidate centres as page fractions (Decision 16).

Behaviour: the stamp is a bordered word (and date line when `DX_PAID_DATE` is present) drawn on its own RGBA layer in a colour, opacity and rotation drawn on the overlay seed, with seeded speckle, then alpha-composited. Candidates are tried in an order shuffled on the overlay seed; the first whose rotated footprint, border included, clears every padded protected box (scored boxes and code marks, Decision 4) and the page inset is used. With none legal, `OverlayError` names the layout, each candidate and the boxes that blocked it. Stamp text records distractor text ink (Decision 7).

Validation (four-part): a candidate outside [0, 1]; a candidate whose footprint at the largest rotation and font cannot fit the page; `date_key` whose item does not require the gate's item (Decision 10); a stamp block on a layout whose payment state does not group `in_paid_stamp` as paid.

Tests:
- Rules: `pick` deterministic per case and within its list; `link_date` equals the link's bank date for every corpus invoice and equals INVOICE_DATE (the repeat check passes); missing and duplicate links fail four-part.
- State: no invoice case selects the stamp in the unpaid state, or with any unpaid-group item; with every paid item forced, every case's stamp appears.
- Per case (slow): every one of the 55 invoices on its own layout with its selection, and every invoice case on each of the eight layouts with every paid-group item forced, renders, places its stamp, passes the verifier, and decodes every barcode on the page; a stamped page is byte-identical across two renders.
- Failing fixtures: a layout whose only candidate centres on TOTAL_AMOUNT fails naming TOTAL_AMOUNT; a candidate over a barcode fails.
- Density: re-measure all 55 invoice distractor renders and the synthetic entries; record the measurement in the density comment with its date (Decision 15).
- Snapshot: re-bless the distractor snapshot for invoices only, report how many invoice entries changed and confirm receipt entries are unchanged; plain and derived snapshots unchanged.

Visual check: run `python -m generators.pipeline generate --type invoices --distractors`, open one stamped invoice per layout, and describe the stamp's position, rotation, colour and legibility, and that no scored value is covered; fix what looks wrong.

Slow tests in the gate: the invoice stamp per-case and forced tests, and the existing invoice forced-render tests.

Commit: `✨ feat: stamp paid invoices`.

### Task 3: Bank statement distractors, inline

**Files:** `config/layouts/bank_statements.yml` (provider, items anchor, inline blocks, `DISTRACTOR_*` budgets); `generators/bank_statement.py` (page-fit check); `config/distractors.yml` (bank selection count and density bounds); `tests/snapshot_tools.py` (`DISTRACTOR_DOC_TYPES` gains bank statements) and the distractor snapshot fixture; local tests.

- Every bank layout gains `{name: bank_distractors, params: {}}` in `field_providers` and lists the three inline items through a shared anchor: `bk_issued_date`, `bk_mail_barcode`, `bk_promo_message` (Decision 1).
- Blocks where a real statement prints them, each gated on its own key and laid out so the page does not grow (Decision 11): 'Statement issued' with its date on the period line or beside it (a split, labelled so it is not read as the period); the mailing barcode beside or under the account-holder lines in the address area (Code 128, meeting Stage 3's validate-time width check); the promotional message in a bordered panel, foot-anchored above the existing footer text.
- `generators/bank_statement.py` refuses a body ending below `page_dimensions.height - margin`, four-part: What names case, layout, mode and the overrun; Where `<abs>/config/layouts/bank_statements.yml -> <layout>.body`; Expected a body ending at or above the limit; Recover names split columns, the `DISTRACTOR_*` budgets or the layout's `distractor_items`.
- Selection: `selection.bank_statement.count` suits three items in this task (for example `[1, 3]`); Task 4 revises it.
- Density: measure all 55 bank distractor renders and every case forced with all three items; set `density.bank_statement` from measurement with at least 10% relative headroom, with a dated comment (Decision 14 governs the share floor).

Tests:
- End to end: every selected bank `DX_` key drew distractor ink; every mailing barcode decodes to its value; plain renders byte-identical.
- Forced (slow): every bank case on every one of the eight bank layouts with all three items renders on one page; the page-fit refusal fires on a synthetic statement made too long, through `assert_diagnostic_error`.
- Snapshot: add bank statements to the distractor snapshot (55 new entries); invoice and receipt entries unchanged.

Visual check: `python -m generators.pipeline generate --type bank_statements --distractors`; open one statement per layout and describe where each inline item sits and whether any label could be mistaken for the statement period.

Slow tests in the gate: the bank forced-render test.

Commit: `✨ feat: draw inline distractors on every bank statement layout`.

### Task 4: Bank annotations

**Files:** `generators/overlay/annotation.py` and `generators/overlay/handwriting.py` (new); `generators/distractors/values.py` (`row_marks` rule); `config/distractors.yml` (`bk_row_marks`, bank selection count, `overlay.highlighter`, `overlay.ink`, `overlay.handwriting`, bank density re-measured); `config/layouts/bank_statements.yml` (overlay blocks on all eight layouts; the four annotation items added to the bank items anchor); `generators/layout_dsl/schema.py` (annotation block validation); `docs/distractor_overhaul_design.md` (a dated revision recording Decisions 2, 3, 5, 6 and 7); `README.md`; the distractor snapshot fixture; local tests.

Catalogue: `bk_annotated_rows`, `bk_annotation_total` and `bk_margin_note` already exist. Add `bk_row_marks`: clutter, emits `DX_ROW_MARKS`, requires `bk_annotated_rows`, rule `{type: row_marks, marks: [tick, circle, underline]}`, returning one mark kind per annotated row as a pipe list aligned with `DX_ANNOTATED_ROWS`. Raise `selection.bank_statement.count` so most statements carry annotations, and report the measured share of annotated statements in the YAML comment.

Required YAML:
- `overlay.highlighter: {colours: {name: hex, ...}, opacity: [min, max], pad_px, end_jitter_px}`.
- `overlay.ink: {colours: [hex, ...], stroke_px: [min, max], jitter_px, tick: {size_px}, circle: {gap_px}, underline: {gap_px}}`.
- `overlay.handwriting: {hands: [{family, bold}, ...], size_px: [min, max], glyph_jitter: {size_px, slant_deg, baseline_px}}`, listing every vendored handwriting face and weight (Kalam regular and bold, Caveat, Indie Flower, Patrick Hand, with bold only where the face ships one); every family must be a key of the vendored font registry, and a listed weight with no vendored file fails fast. The hand is drawn once per statement on the overlay seed. A test renders every hand over the full note and sum character set (digits, `$`, `.`, `,`, `=`, `+`, letters used by the note pool) and fails on a missing glyph (tofu), and a corpus test shows every hand is used at least once.
- Vendoring: the font files sit in `fonts/` beside the existing faces with `LICENSE-<Face>.txt`; a test checks each vendored face has its licence file and loads at every size in `size_px`.
- Bank overlay blocks, in this order so ink lies over the tint: `{type: highlight, when: DX_ANNOTATED_ROWS, rows: DX_ANNOTATED_ROWS, row_fields: [TRANSACTION_DESCRIPTIONS, TRANSACTION_AMOUNTS_PAID]}`; `{type: ink_marks, when: DX_ROW_MARKS, rows: DX_ANNOTATED_ROWS, marks: DX_ROW_MARKS, target_field: TRANSACTION_AMOUNTS_PAID, kinds: [tick, circle, underline], tick_lane: left_margin}`; `{type: handwriting, when: DX_MARGIN_NOTE, content: "{DX_MARGIN_NOTE}", rows: DX_ANNOTATED_ROWS, lanes: [...]}`; `{type: handwriting, when: DX_ANNOTATION_TOTAL, content: "= {DX_ANNOTATION_TOTAL}", rows: DX_ANNOTATED_ROWS, lanes: [...]}`. Lanes are an enum: `right_margin`, `after_description` (the gap between a row's description ink and its next column), `left_margin`, `below_column` (under the paid column, below the table).

Behaviour:
- Rows are located by recorded scored boxes: row `i` is the union of the `row_fields` boxes indexed `[i]`, widened to every ink box in its vertical band. A row with no recorded paid box fails fast naming the case and index (Review Focus 2).
- Highlight: one translucent band per annotated row, slightly wavy ends drawn on the overlay seed, all bands unioned into a single mask, then one multiply blend at the drawn opacity, so text stays dark and overlaps never darken twice; then the contrast check (Decision 6). No ink is recorded.
- Ink marks: per annotated row, its kind from `DX_ROW_MARKS`: a tick in the left-margin lane level with the row, a jittered loop around the paid amount clearing its padded box and every neighbour's, or an underline just below the paid amount's padded box. Strokes are jittered polylines in an ink colour drawn once per page on the overlay seed; each records a distractor mark. A kind that cannot be placed fails fast; it is never swapped for another (Decision 9).
- Handwriting: the statement's hand, glyphs drawn one by one with jittered size, slant and baseline on an RGBA layer, then composited. Lanes are tried, row by row, in an order shuffled on the overlay seed; the first box clear of every padded protected box and the page inset is used; none legal fails fast naming every lane tried. The text records distractor text ink.
- Every opaque layer is verified against the `all_ink` protection after compositing (Decisions 4 and 5).

Validation (four-part): a `kinds:` list that misses a kind the `row_marks` rule can choose; a `rows:` or `marks:` key that is not a `DX_` key of an item the gate requires; an unknown lane; a `target_field` or `row_fields` entry that is not recorded by the layout's table.

Tests:
- Rules: `row_marks` aligns one kind per annotated row, deterministic per case.
- Rows (Review Focus 2), all 55 bank cases: each band contains the paid boxes of exactly its annotated indices; every linked row is highlighted; the handwritten total equals the sum of the ground-truth paid amounts of the highlighted rows.
- Per case (slow): every bank case on its own layout with its selection, and every bank case on each of the eight bank layouts with every bank item forced, renders on one page, passes the verifier, draws a mark for every selected overlay key (from `OverlayResult`), decodes its mailing barcode, and is byte-identical across two renders.
- Across all 165 cases (slow; the roadmap exit criterion): no opaque overlay pixel lies in any padded scored box (receipts have no overlay and their distractor renders are unchanged); every highlighted scored box meets the contrast ratio. Report the smallest clearance and smallest contrast found.
- Failing fixtures: a tick lane forced onto the date column fails naming the date cell; a dark highlighter colour fails contrast; two adjacent annotated rows produce a single multiply where their bands meet.
- Density (Review Focus 4): measure all 55 bank distractor renders and every case forced after compositing; set `density.bank_statement` from measurement with at least 10% relative headroom and a dated comment; a test asserts every bank distractor render records at least one distractor ink box or mark and no plain render does (Decision 14).
- Snapshot: re-bless the distractor snapshot for bank statements; receipt and invoice entries unchanged; plain and derived snapshots unchanged.

Visual check: generate bank statements with `--distractors`, open one per layout, and describe whether the page reads like a statement a taxpayer has marked up: highlighter colour and coverage, tick and loop shapes, how handwritten the notes and total look, and that every scored value stays readable. Fix what looks mechanical (identical strokes, perfectly straight bands, notes floating far from their rows).

Gate: the stage-end gate. The full suite `conda run -n synthetic pytest tests/` must pass, slow tests included; then ruff, format, mypy and `python -m generators.pipeline validate`.

Commit: `✨ feat: annotate bank statements with highlighter, ink marks and handwriting`.

**Stage 5 is complete when** paid invoices may carry a PAID stamp that never covers a scored value, bank statements carry their inline distractors and a taxpayer's annotations on the rows they claim, no opaque overlay pixel meets a padded scored box across all 165 cases, every highlighted value meets the contrast threshold, plain renders are unchanged, and the bank density bounds are set from composited measurements.

## Stage 5 outcome (8 October 2026)

Complete. Commits dfebfec to 9cbbeaf. The local suite holds 2886 tests at stage end: 2875 pass and the 11 strict expected failures are unchanged. `pipeline validate` and `scripts/check_seed_reproducibility.py` pass. Links: 110, every difficulty level present (52 / 36 / 22), oracle F1 1.0. Across all 55 bank statements and 55 invoices, no opaque overlay pixel lands in a padded scored box (closest approach 7 px against a clearance of 6), and the lowest contrast on a highlighted scored value is 14.8 against a threshold of 4.5.

### How the shipped code differs from this plan's text

- **Plain renders changed, deliberately, for validity fixes** (the exit criterion's "plain renders are unchanged" does not hold). A guard added in this stage (`generators/drawn_fields.py`, run by `pipeline validate`) refuses any scored ground-truth value its layout does not draw, comparing every member's drawn string with the ground truth under a required `drawn_value_policy` (amount grouping and currency sign, per-layout date formats). It exposed and the stage fixed: NAB layouts printed neither the statement period nor bound the account holder (14 statements); receipts printed no unit price on lines with quantity above 1 (46 receipts; the export had blanked those prices in the answer key, which is now removed, so receipt unit-price scores are not comparable with earlier runs); Westpac layouts had no balance column for the scored closing balance (14 statements); the ABN box included its label. Remaining exemptions are declared in `geometry_exemptions` with a reason, a member condition and how the value is printed.
- **Four handwriting hands.** Caveat, Indie Flower and Patrick Hand are vendored beside Kalam with their licences and recorded sources (`fonts/SOURCES-handwriting.txt`); each annotated statement draws one hand, sized by x-height.
- **Bank ground truth reseeded.** Transaction descriptions are drawn by direction (credits from income and refund pools, debits from purchase, bill and withdrawal pools) and a personal account's running balance stays above a YAML floor; `generators/bank_ledger.py` holds both checks, run by the seed and by validate. Rows printed against their description fell from 287 to 0; the lowest running balance rose from -17,627.35 to 166.98. Receipts and invoices are byte-identical across the reseed. The link seeder no longer falls back silently to a default suburb.
- **Annotations.** Bank selection draws 2 to 4 items (51 of 55 statements annotated); validate requires every distractor render to record distractor ink or a mark. Notes may fall back to a lane under the paid column where a layout's description column is narrow (ANZ). Highlighter bands wander and vary in ink; handwritten sums sit slightly off the column edge; every shape range is a YAML key.
- **PAID stamp.** A paid-group item with an optional dated line repeating the linked payment date; clumped speckle and continuous placement jitter around clerk-style candidate positions.
- **Density.** Bank bounds from composited measurement: `scored_ink_ratio_max` 0.76, `distractor_share_min` 0.0 (Decision 14), `page_fill_min` 0.56. Receipt ceiling re-measured to 0.48 after unit lines were added.

### Constraints carried into later stages

**Stage 6 (labels)**
- The drawn-field guard must pass on the labelled_varied render: label removal may never take a scored value with it.
- Scored-field type sizes (Stage 4 carry) are reported, not changed.

**Stage 7 (export)**
- Bank, receipt unit-price and Westpac balance scores are not comparable with runs before this stage; the snapshot README says so.
- The heavy degradation tier's nondeterminism and the eval-set count message (carried from earlier stages) are still open.

**Later passes**
- A highlighted claim can be an ATM withdrawal; employer names on salary rows are cut to 12 characters; the highlighter does not cover NAB's grey reference sub-line; the stamp's jitter fallback has no breaker test; Westpac amount columns have little spare width for larger future amounts.

