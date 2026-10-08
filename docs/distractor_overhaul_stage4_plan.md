# Distractor Overhaul, Stage 4: Invoices

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make invoices realistically dense: fix the two invoice rendering defects, draw the invoice distractor catalogue on every invoice layout, add four industry layouts whose content fits them, and reseed the corpus so cases are spread across all eight layouts with brand-free, category-coherent content.

**Architecture:** Task 1 fixes rendering defects in the plain invoice and re-blesses the plain baselines once, deliberately. Task 2 wires the invoice distractor provider and gated blocks into the four existing layouts and sets invoice density bounds from measurement. Task 3 adds four industry layouts (each with its own body and a declared set of service categories) and the content pools they need. Task 4 makes the seed scripts read layout lists and invoice categories from YAML, removes real brand names from the content pools, reseeds the corpus and re-captures every content-pinned baseline.

**Tech Stack:** Python 3.12, Pillow 12.2.0, the layout DSL, the distractor package, pytest, conda env `synthetic`.

**Spec:** `docs/distractor_overhaul_design.md` sections 5 (invoice catalogue and new layouts), 9 (density), 10 (invoice rendering fixes). Earlier stage outcome sections (Stages 0 to 3) are binding; Stage 3's carried constraints for Stage 4 drive Tasks 2 and 4.

## Global Constraints

- Conda env `synthetic`; run every tool as `conda run -n synthetic <command>` from the repository root.
- YAML is the single source of truth: layout lists, invoice service categories per layout, content pools, density bounds and every new tunable are required YAML keys; Python holds no layout lists and no defaults.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected and Recover; tests assert this with `assert_diagnostic_error`.
- Scored values are printed verbatim and stay consistent with the layout (the corpus invariant: a field's value must be one the layout's design would print).
- Renderers never clip; distractor text fits through dedicated `DISTRACTOR_*` budgets, never a scored field's budget.
- Adding a distractor provider to a layout switches density enforcement on at once (Ruling 9): land a layout's distractor blocks and its density bounds in the same commit.
- Pixel changes are deliberate: Task 1 re-blesses the plain snapshot for its two fixes only; Task 4's reseed re-captures every content-pinned baseline; no other task changes plain pixels.
- No real business or brand names in any pool, layout or test; invented example values are checked absent from `ground_truth/`.
- Line length 108; Python 3.12 types; `pathlib.Path`; Google-style docstrings; `raise ... from err` in `except` blocks.
- `tests/` is gitignored and local-only. Tracked changes are committed on `main` after the gate: full `pytest tests/` (must collect), `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`, `mypy . --ignore-missing-imports`, `python -m generators.pipeline validate`. Commit messages carry no trailers.
- Prose uses British spelling and no em-dashes; no tooling references in tracked files.

## Review Focus

1. **A dense invoice that runs off the A4 page**: with every invoice item forced on, every case on every invoice layout fits one page (invoices are fixed-height) or fails fast; never clipped. Tasks 2 and 3.
2. **A near-miss a careful reader cannot tell from its scored field** (an unlabelled second total, a ship-to address styled like Bill To): every near-miss carries a distinct, realistic label; reviewers check rendered pages by eye. Tasks 2 and 3.
3. **Content that contradicts its layout** (a trade invoice billing legal advice; a subscription invoice with quantity 7 of a monthly plan): the seed draws categories from the layout's declared list, and a test checks every invoice case. Tasks 3 and 4.
4. **A reseed that silently changes link difficulty or counts**: the reseed gate checks 110 links, F1 1.0 and every difficulty level present, without pinning the exact split. Task 4.
5. **A real brand left in a content pool**: a test screens every product and service description against a YAML brand blocklist, and proves the screen fails on an inserted brand. Task 4.

---

### Task 1: Invoice rendering fixes

Fix the two defects the spec records (section 10), found on `tax_invoice_standard` and present wherever the shared totals and line-item anchors are used:

- The bold "Total" label overlaps its amount (`Total$4,612.67`) in the totals panel.
- Line-total amounts overrun the right margin (the line-items table's total column is right-aligned at x 1750 on a 1700 px content width).

Fix both at their root in `config/layouts/invoices.yml` (or the primitive, if the defect is in the engine), with regression tests that measure the ink: the Total label's ink box and its amount's ink box do not intersect and keep a minimum gap declared in YAML; every line-total cell's ink lies within the content region. Then re-bless the plain pixel snapshot and the derived snapshot ONCE with their regenerate scripts, and record in the report exactly which keys changed (invoice pages and their geometry only; receipts and bank statements byte-identical). The distractor snapshot (receipts) must be unchanged.

Commit: `🐛 fix: stop invoice totals overlapping and line totals overrunning the margin`.

### Task 2: Invoice distractors on the four existing layouts

- Add `{name: invoice_distractors, params: {}}` to the invoice field-providers anchor and list the invoice items in every invoice layout's `distractor_items` (an anchor, as receipts do).
- Catalogue additions in `config/distractors.yml` (Stage 2 conventions; rules declared and checked): `in_remit_barcode` (`digits`, empty prefix, even length of at most 16, clutter) so the remittance slip carries a scannable Code 128 barcode; and `in_page_number` (`fixed`, value `Page 1 of 1`, clutter).
- Place gated blocks where a real Australian business invoice carries them, using the shared anchors so the four layouts stay consistent and variety comes from selection and from Task 3's layouts: letterhead (logo gated on a letterhead key, tagline, contact block: phone, mobile, 1300 number, email, website, social), identifiers (invoice number, PO number, customer number, ACN, trade licence, accreditation), parties (ship-to beside Bill To, remittance PO Box, branch address), dates (due date, supply date beside the invoice date), services panel and promotional price, notes (job reference, contact person), totals region (Amount paid, Balance due, Previous balance: each labelled so it cannot be mistaken for Total), payment block (BSB and account, BPAY biller and reference, PayID, surcharge note), terms (payment terms, late fee, warranty), a remittance slip at the foot (dashed tear line, "Remittance advice", invoice number, total repeat, remittance barcode), and a footer (contact band, thank you, page number). Every block is gated on its own key; multi-key blocks follow Ruling 7.
- Per-line table distractors (item code and per-line GST columns, a discount line) need row-level values, which providers do not emit; they are out of scope for this stage (Ruling 15).
- Distractor text uses dedicated `DISTRACTOR_*` budgets sized to the invoice content width; long terms wrap.
- Measure density across all 55 invoice distractor renders and set the invoice bounds from the measured ranges with headroom (`scored_ink_ratio_max`, `distractor_share_min`, `page_fill_min`), recording the spec targets (0.25 / 0.75) and the measured values in a dated comment. If the measured ratio is far above 0.25 even with every item selected, say so in the report rather than padding with nonsense: the spec calls these starting bounds.
- Tests: end-to-end (every selected key drew distractor ink; every barcode and QR decodes; plain renders byte-identical to the Task 1 baseline); forced (every invoice case on each of the four layouts with every invoice item renders on one page); extend the distractor snapshot to invoices.
- Visual check: generate invoices with `--distractors`, open one per layout, describe placement and legibility, fix what looks wrong.

Commit: `✨ feat: draw distractors on the existing invoice layouts`.

### Task 2b: Invoice coherence

Added after Task 2's review, which found two incoherences a reader resolves by the labels alone (defeating the near-miss) and one visibly empty page region. Task 3's layouts build on the result, so this runs first.

- **Payment state.** `config/distractors.yml` gains a required top-level `states:` key. Invoices declare `payment: {choices: [paid, unpaid], items: {paid: [...], unpaid: [...]}}`; receipts and bank statements declare `{}` explicitly. The state is drawn from its own seed, sha256 of `"{case}:distractor:{doc_type}:state:payment"`, and items of the other group are never selected. Paid group: Amount paid, Balance due 0.00. Unpaid group: the remittance slip (its gate moves to an unpaid item), Amount due, Previous balance. Every listed item exists in the catalogue, appears in at most one group, and an item named in a group but missing from a layout's `distractor_items` is an error; each fails fast with a four-part diagnostic. Stage 5's PAID stamp will join the paid group.
- **Footer at the foot.** A container option (`anchor: foot`) draws its block upward from `page_height - margin`, so footer bands sit at the bottom of the page whatever the body length; if the block would meet the body it fails fast (four-part diagnostic, naming the layout and block). Invoice footers and the remittance slip use it. `page_fill_min` stays required and is re-measured; the spec's section 9 records that page fill no longer discriminates on foot-anchored layouts and density is judged by `scored_ink_ratio_max` and `distractor_share_min`.
- **Industry-keyed clutter.** `config/data_pools.yml` gains a required category-to-industry map covering every invoice service category; the tagline, accreditation, services, warranty and terms pools become sub-pools keyed by industry, and an unmapped category or an industry with no sub-pool fails fast. Trade-only items (licence, warranty) and goods-only items (ship-to) are listed only on layouts whose categories warrant them.
- **Minors.** BSB and account number become one item emitting two keys; BPAY biller and reference likewise. The density comment's date reads 6 October 2026. The bare "Delivery:" heading on `tax_invoice_high_value` gains a proper label or goes. Logo initials keep a padding gap from the rim.
- Tests: forced rendering per payment state (all paid-group items, all unpaid-group items) on every invoice layout; no case selects items from both groups; every foot-anchored block's ink ends within the bottom margin band; every invoice case's clutter text comes from its industry's sub-pool; each new fail-fast path asserted with `assert_diagnostic_error`. Plain renders stay byte-identical; the distractor snapshot is re-blessed for invoices only.

Commit: `🐛 fix: make invoice distractors coherent with payment state and industry`.

### Task 3: Four industry invoice layouts

Add `tax_invoice_trade`, `tax_invoice_professional`, `tax_invoice_wholesale` and `tax_invoice_subscription` to `config/layouts/invoices.yml`, each designed around the catalogue rather than retrofitted, with its own body:

| Layout | Character |
| --- | --- |
| `tax_invoice_trade` | Tradie letterhead, licence number, job and site notes, labour and materials lines |
| `tax_invoice_professional` | Formal letterhead, matter reference, long terms, remittance advice |
| `tax_invoice_wholesale` | Item-coded goods, ship-to address, delivery docket number |
| `tax_invoice_subscription` | Account number, billing period, plan lines, direct-debit notice |

- Every invoice layout gains a required key `service_categories:` listing the categories its content may draw (the four existing layouts keep today's professional-services categories). New categories and service catalogue entries go in `config/data_pools.yml` (for example trade labour and materials, wholesale goods with units, subscription plans with sensible quantities), invented and brand-free.
- New layout-specific distractor items, if a layout needs them (delivery docket number, billing period, matter reference), are added to the catalogue with rules and listed only on their layouts.
- Each new layout carries the invoice distractor provider and its own density bounds commitment (shared invoice bounds re-measured over all eight layouts in Task 4).
- Every new category maps to an industry in `category_industries` (Task 2b), with keyed sub-pools for tagline, accreditation, services, warranty and terms. The loader also refuses a category noun that is a suffix of another category's noun when the two map to different industries (four-part diagnostic), and a test enumerates every name prefix with every noun to show each resolves to its own category.
- `in_licence` and `in_warranty` are listed on the trade layout and `in_ship_to` on the wholesale layout, using the anchors Task 2b kept (`_invoice_licence`, `_invoice_warranty`, `_invoice_ship_to_column`), so their rules and pools are exercised by renders.
- Carried from the Task 2b review: replace the `[]` fallbacks for `service_categories` in `generators/distractors/` with a four-part error for a missing or non-list key; wrap the 137-character comment in `config/layouts/invoices.yml` to 108; document in the logo docstring that the square erosion keeps at least `inset` px, up to about 1.4 times that at diagonals.
- Register a `slow` pytest marker in `tests/conftest.py` and mark the forced-render tests with it; the task gate runs `pytest tests/ -m "not slow"` plus the slow tests for the layouts this task adds.
- Tests: schema accepts the new layouts; every new layout renders plain and distractor modes for synthetic entries of its categories; forced rendering on one page in each payment state; content-coherence test (Review Focus 3) is added in Task 4 once cases are assigned.

Commit: `✨ feat: add trade, professional, wholesale and subscription invoice layouts`.

### Task 4: Reseed

- `scripts/seed_ground_truth.py` reads layout lists from the layout registries (YAML) instead of its hard-coded `_BANK_LAYOUTS`, `_RECEIPT_LAYOUTS`, `_INVOICE_LAYOUTS`; invoice categories are drawn from the assigned layout's `service_categories`; cases are spread across all eight invoice layouts.
- Brand scrub: add a required `real_brand_blocklist` to `config/data_pools.yml` (well-known product brands); replace every brand-named product or service description (for example "Panadol 24pk") with a generic description; a test screens every catalogue description against the blocklist (whole-word, case-insensitive, as the business-name screen does) and proves it fails on an inserted brand.
- Receipt category coherence: receipts draw line items from the merchant category's products (fix where they do not; for example a cellar selling yoghurt).
- Run the reseed in order (`scripts/seed_ground_truth.py`, then `scripts/seed_transaction_links.py`), confirm `scripts/check_seed_reproducibility.py` passes, then re-capture every content-pinned baseline with its regenerate script: plain pixel snapshot, derived snapshot, eval-format baseline, distractor snapshot. Update tests that pin corpus shape: layout counts (`tests/layout_dsl/test_defaults.py`) and the link difficulty split (`tests/test_transaction_dates.py`: assert 110 links, F1 1.0 and every level present, not a fixed split).
- The seed stores each invoice's service category in its ground truth entry as non-scored metadata (excluded from every derived and exported field set), and `values.case_category` reads it, failing fast with a four-part diagnostic when it is absent, instead of inferring it from SUPPLIER_NAME.
- Line content comes only from `category_catalog[category]`: move the five professional-services categories into it, retire `service_catalog` for invoices, have the seed fail fast (four-part) for a category with no catalogue entry, and have `pipeline validate` require catalogue coverage for every category of every invoice layout. Once the category is stored in ground truth, empty `category_noun_overlaps_allowed` (or rename the overlapping noun) so the allowlist carries no stale pair.
- Money near-misses (promotional price, previous balance, any other amount-valued distractor) are drawn as a fraction range of the case's TOTAL_AMOUNT declared in YAML, so a small invoice never carries an outsized second amount; the value validator still keeps them distinct from every scored amount.
- Site and ship-to addresses are drawn in the payer's state (a YAML rule option), so a near-miss address sits plausibly near the Bill To.
- The professional layout's terms draw from a multi-sentence professional terms sub-pool (long terms, as the layout's character requires).
- Root-cause fix for glyph overhang: the text primitive compensates a negative left side bearing on the first glyph of left-aligned text (and the mirror case on right-aligned text), so ink never crosses the margin whatever the string; a test sweeps every vendored face and size used by a layout over every capital and digit. Bold Carlito "Y" is the known case.
- Plain-pixel cleanups that wait for this re-capture: drop or label the bare "Delivery:" heading on `tax_invoice_high_value`, and gate the "Payment Terms:" heading on its content so no heading prints with nothing under it.
- Re-measure invoice density over all eight layouts and finalise the invoice bounds with a dated comment. Set `distractor_share_min` at least 15% (relative) below the sampled minimum; never raise a floor to make the corpus pass.
- Content-coherence test: every invoice case's line items come from its layout's categories (Review Focus 3).
- Gate: `pipeline validate` exit 0; 110 links, F1 1.0, every difficulty level present; GST equals total / 11 where included; no blocklisted name or brand in any field; full suite green.
- Update `docs/ten_minute_tour.md` / `README.md` corpus descriptions where they list the invoice layouts (four becomes eight).

Commit: `🗃️ data: reseed across eight invoice layouts with brand-free, coherent content` (and separate commits for the seed-script and pool changes if cleaner: `♻️ refactor: read seed layout lists from YAML`, `🐛 fix: remove real brand names from the content pools`).

**Stage 4 is complete when** invoices render densely and legibly on eight layouts, plain-mode invoices are free of the two rendering defects, content fits each layout, the corpus is reseeded with every baseline re-captured, and invoice density bounds are set from measurement.

## Stage 4 outcome (7 October 2026)

Complete. Commits a23b760 to c370836. The local suite holds 2368 tests at stage end: 2357 pass and the 11 strict expected failures are unchanged. Plain pixel, derived and distractor snapshots were re-captured deliberately (Task 1 for its two fixes, then once per reseed); the eval-format baseline is structural and unchanged. `pipeline validate` and `scripts/check_seed_reproducibility.py` pass. Links: 110, every difficulty level present (52 / 36 / 22), oracle F1 1.0, receipt-to-bank linking intact.

### How the shipped code differs from this plan's text

- **Task 2b was added** after Task 2's review: a page that is both paid and owing, clutter from the wrong industry, and an empty lower page are resolved by the reader through the labels, which defeats the near-miss.
- **Payment state.** `config/distractors.yml` has a required top-level `states:` (explicit `{}` for receipts and bank statements). Invoices draw `paid` or `unpaid` from their own seed; items of the other group are never selected, nor anything requiring them. A required top-level `exclusive:` keeps at most one of a set per page (the direct-debit notice or the remittance slip). The `state_text` rule picks wording by state.
- **Footer at the foot.** A `stack` container and `anchor: foot` (body's last block only) draw footers upward from the bottom margin, failing fast if they would meet the body. Plain renders are unaffected because every foot block is gated.
- **Categories are stored, not inferred.** Invoices carry a non-scored `service_category` and receipts a non-scored `merchant_category` in ground truth; neither reaches any derived file, export or the extraction ground truth. The supplier-name inference path was removed. Invoice and receipt layouts declare the categories they serve (`service_categories`, `merchant_categories`), the seed draws from them, and validate enforces them.
- **Industry-keyed clutter.** `category_industries` maps every category to an industry; tagline, accreditation, services, warranty and terms are sub-pools per industry. Line content comes only from `category_catalog`, with coverage required by validate.
- **Brand screens.** A shared blocklist screens every pool (with a declared exemption for payment-method names such as wallet brands), and a catalogue-only list screens product and service lines for brands that are also common names.
- **Money near-misses scale with the case.** `amount_ratio` draws relative to a scored amount, with a required `cap` (a number or explicit `none`); the upgrade offer is priced on the dearest unit price. Site, ship-to and remittance PO Box addresses are drawn in a declared party's state.
- **Previous balance** prints only in an account summary that adds up (previous balance, payment received, this invoice, amount due).
- **Glyph overhang.** The text primitive compensates a first glyph's negative left side bearing (and the right-aligned mirror), so ink never crosses its margin.
- **Density.** Invoices draw 14 to 22 distractor items. Bounds from measurement over all eight layouts (corpus and six-line synthetic entries): `scored_ink_ratio_max` 0.48, `distractor_share_min` 0.40, `page_fill_min` 0.33; corpus median scored-ink ratio 0.255, close to the spec's 0.25. Bank bounds loosened to 0.85 / 0.55 after the reseed shortened statements (they enforce nothing until Stage 5).
- **Not done (Ruling 15):** per-line table distractors (item code and per-line GST columns, discount lines) need row-level provider values; spec section 5 records the deferral.

### Constraints carried into later stages

**Stage 5 (overlay)**
- The PAID stamp joins the `paid` group of the invoice `payment` state, so it never appears on a page with a remittance slip or an account summary.
- Bank annotations use `ink_out` for avoidance and count in density: re-measure the bank bounds (0.85 / 0.55 are post-reseed placeholders) after compositing.
- Short invoices keep a blank band of up to about 1000 px between body and foot-anchored footer; no bound measures it. The overlay should not be relied on to fill it; if it reads as unrealistic, a later pass adds body-side content.

**Stage 6 (labels)**
- PAYER_NAME on all eight invoice layouts and SUPPLIER_NAME on four print at the small body size; review scored-field type sizes together with label variation.
- Trade invoices can list materials before labour, because lines print in the order drawn (this removed a first-line bias).

**Later passes**
- Receipt layouts are uneven after keying to merchant category (hospitality has 3 cases).
- Per-line table distractors (above).
- `docs/pipeline_backend_comparison.md` keeps its dated counts as a decision record.

