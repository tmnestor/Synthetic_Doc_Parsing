# Synthetic Document Pipeline: PIL/YAML vs. Template-DSL Backends

> **Status (2026-08-06).** The backend decision is still **open** — no migration has been started. What *has* shipped are the two cheap, backend-independent realism levers this document identified: the OFL font swap with a per-layout `family:` selector, and the receipt-only tiered degradation rebuilt on Augraphy plus the camera-scan homography. Sections describing those now record what was delivered and what it cost, including the places this document's own predictions were wrong.
>
> The degradation design, its stakeholder rationale and its implementation plan have since been retired now that the work has shipped; all three remain in git history.

**Purpose.** Decide whether to invest a two-week sprint re-engineering the current PIL + YAML declarative synthetic-document pipeline onto a template-DSL render backend[^dsl] (HTML/CSS, Typst, or LaTeX). The benchmark's whole value is *credibly* measuring information-extraction (IE) accuracy of competing VLMs on labelled synthetic AU business documents, so the two things that actually matter are **realism** (does the synthetic distribution resemble real documents?) and **label fidelity** (are the ground-truth boxes and values exactly right?). Everything below is judged against those two axes plus the cost and risk of the migration itself.

[^dsl]: **DSL** = Domain-Specific Language: a small language built for one job, unlike a general-purpose language such as Python (SQL for data, regex for text patterns). Here the term spans the current pipeline's internal YAML layout DSL and the external Typst/LaTeX typesetting DSLs. See the Glossary for the fuller definition.

**Key correction (established during review).** The current pipeline already solves label fidelity and reproducibility: it captures per-field bounding boxes **at draw time** into `../evaluation_data/derived/geometry.jsonl`, and it pins `pillow==12.2.0` so that FreeType metrics (and therefore `fit_text()` decisions and rendered pixels) stay byte-stable across rebuilds and Mac↔PROD. So this is **not** a decision about fixing broken labels. The *only* thing a new backend buys is **realism**, and it must do so without regressing the label accuracy and byte-stability the PIL pipeline already delivers. Every claim below has been updated to reflect this.

---

## TL;DR / Recommendation

- **Do not treat this as PIL vs. Jinja2.** Jinja2 is a *templating layer*; it can emit `.html`, `.tex`, or `.typ`. The real decision is the **render backend**: raster canvas (PIL), HTML/CSS via a browser engine, LaTeX to PDF, or Typst to PDF.
- **The templating layer is already built here.** `generators/layout_dsl/` is a declarative nine-primitive DSL driving all 18 layouts from YAML `body:` trees, with the per-type renderers reduced to 65–93-line adapters. So the sprint cannot be justified as "templates instead of drawing code" — that is done.
- **The current pipeline's only ceiling is realism, and specifically typesetting.** Its labels (draw-time `geometry.jsonl`), reproducibility (pinned Pillow/FreeType) and authoring model (declarative YAML) are already solid. What it lacks is a typesetting engine — paragraph line-breaking, hyphenation, justification, kerning, row-height balancing — plus a theme layer and a wider typographic palette. The sprint buys typesetting and stylistic variety and nothing else, so the go/no-go turns on how much those are worth.
- **Both cheap realism levers are now done (2026-08-06), and neither was the sprint.** The font swap replaced DejaVu with metric-compatible Carlito/Liberation, removed the system-font fallback, and added a per-layout `family:` selector — buying typographic diversity as well as register accuracy. The degradation rebuild made degradation receipt-only and tiered on Augraphy plus the camera-scan homography. See *Production provisioning and font reproducibility* and *Camera-scan (mobile-phone) inputs*. **This changes what the sprint has left to buy** — see the next bullet.
- **The measured realism delta the sprint can still claim has narrowed.** With the typeface register corrected and the degraded corpus now genuinely photo-like, the remaining gap between this pipeline and a typeset one is paragraph line-breaking, hyphenation, justification, kerning and row-height balancing. Real, but smaller than when this document was first written, and largely invisible on the degraded half of the corpus. Price the sprint against *that* residue, not against the original gap.
- **Recommended target: Typst**, given a locked-down, reproducibility-strict production environment. Typst is a single permissively-licensed package with fully vendorable, self-contained fonts, and it is deterministic by construction. HTML/CSS + Playwright produces the highest realism but carries the heaviest production-provisioning burden (browser binary, root-level system libraries, system font configuration), which a locked-down PROD resists most. Reserve Playwright for the case where the environment can absorb that footprint. LaTeX ranks last for *this* use case despite its typographic quality.
- **Local feasibility is proven; PROD approval is assured but delayed.** Typst 0.15.1 installs cleanly from conda-forge (one 16.6 MB package), renders at 300 dpi, and its introspection query yields per-field boxes that overlay exactly on the rendered glyphs (see *Local feasibility check*). Getting `typst` into the internal Artifactory mirror is **not in doubt, only slow** (per the operator). Availability is therefore a lead-time item, not a go/no-go risk: the action is to **submit the mirror request now** so the delay runs in parallel with the Augraphy work.
- **Fonts are the shared reproducibility landmine.** Whatever backend you pick, vendor appropriately-licensed (OFL) fonts inside the repo and render against them explicitly. Never rely on whatever fonts happen to be installed in PROD.
- **Mobile-phone-scan inputs do not change the decision.** The camera-scan degradation is a post-render pixel step that consumes any clean raster identically (PIL or Typst), and because scoring is value-F1 the ground truth is invariant to the warp, so degradation is essentially free. It does, however, partly discount the realism payoff (blur/JPEG mask fine typography) and points to the degradation model itself as a possibly higher-ROI realism lever. See *Camera-scan (mobile-phone) inputs*.
- **Augraphy is shipped, and it corrected this document's own premise.** Rendering the options side by side established that **every Augraphy effect is a flat-page effect** — none produce perspective, background or framing, which is the dominant gap versus a real phone photo. Augraphy alone was never going to close it. The delivered design uses Augraphy for paper and ink damage on the flat page and the pre-existing camera-scan homography for geometry, in that order. See *Camera-scan (mobile-phone) inputs*.
- **Do not commit the full sprint blind.** The label mechanism is already de-risked locally; the remaining gate is confirming the dependency is obtainable in PROD and reproducing parity on one real document type.

---

## Glossary

- **DSL (Domain-Specific Language):** a small language built for one job, unlike a general-purpose language such as Python. SQL (data), regular expressions (text), and HTML/CSS (pages) are DSLs. This report involves two. The current pipeline's `generators/layout_dsl/` (nine primitives written as YAML) is an *internal* DSL, hosted in YAML and walked by Python. Typst and LaTeX are *external* DSLs, each with its own standalone syntax and engine.
- **Render backend:** the component that turns a document definition into pixels (a PIL canvas, a browser engine, Typst, or LaTeX). The central choice in this report.
- **Templating layer:** the component that injects data (field values, Faker output) into a document definition, separate from the render backend. Jinja2 is one option; the existing YAML layout DSL is another.
- **VLM (Vision-Language Model):** a model that reads an image and produces text; used here to extract fields from document images.
- **IE (Information Extraction):** pulling structured field values (totals, ABNs, dates) out of a document.
- **value-F1:** the benchmark metric. It scores extracted field *values* against ground truth (the harmonic mean of precision and recall), not where they sit on the page. Because it ignores position, geometric distortion of the image does not change the score.
- **Ground truth:** the known-correct answers for each synthetic document (field values and their boxes), recorded at generation time in `../evaluation_data/derived/geometry.jsonl`.
- **Bounding box / label:** the rectangle marking where a field's value sits on the page. "Label" here means that ground-truth annotation, not a caption.
- **Homography:** a perspective-warp transform mapping a flat rectangle to a tilted quadrilateral. `generators/degradation/camera.py` uses one to fake an off-axis phone photo; the rectifier approximately inverts it.
- **Rectification:** undoing that perspective warp offline to produce an upright, frontal document before the VLM sees it.
- **Determinism / byte-stable:** the same inputs always produce the identical output image, bit for bit, across machines. Required for a reproducible benchmark; achieved today by pinning Pillow/FreeType.
- **Introspection (Typst):** Typst's built-in ability to report where each element landed after layout, used to extract ground-truth boxes without a browser. The DOM's `getBoundingClientRect()` is the HTML equivalent (see Appendices A and B).
- **OFL (SIL Open Font License):** a permissive, redistributable font license. OFL fonts can be vendored in the repo and embedded, unlike proprietary Calibri/Arial/Times.
- **Metric-compatible font:** a free font with the same character widths as a proprietary one, so it substitutes without reflowing text. Carlito matches Calibri; Liberation matches Arial/Times/Courier.
- **Artifactory:** the internal package mirror PROD installs from. Public PyPI/conda are unreachable there, so every dependency must be mirrored internally first.
- **Augraphy:** an MIT-licensed library of document-degradation augmentations (ink bleed, shadows, folds, photocopy noise) that makes clean renders look like real scans or photos.
- **Typst / LaTeX / PIL:** Typst is a modern typesetting DSL shipped as a single binary; LaTeX is the classic typesetting DSL; PIL (Pillow) is the Python imaging library the current pipeline draws with.
- **pt / DPI (PPI):** a point (pt) is a typographic unit of 1/72 inch. DPI/PPI is pixels per inch at rasterization. Convert with `pixels = pt × ppi / 72`.

---

## The reframe: templating layer vs. render backend

The question "should we move to a Jinja2 DSL?" conflates two independent choices:

| Layer | Options | What it controls |
|---|---|---|
| Content/templating | Jinja2, Typst scripting, raw Python | How data (Faker output, field values) is injected into a document definition |
| Render backend | PIL canvas, HTML+CSS+browser, LaTeX→PDF, Typst→PDF | How the definition becomes pixels, and how much real typesetting you get |

You almost certainly keep a Jinja2-style content layer regardless. The genuine trade-off is the render backend, because that determines realism, label-extraction mechanics, determinism, and dependency weight. The rest of this document compares backends.

---

## Current pipeline: PIL + YAML (declarative)

**How it works.** YAML declares document content and layout; Python draws text, lines, and boxes onto a Pillow canvas, with `fit_text()` handling shrink/wrap. Crucially, the pipeline records each field's bounding box **at draw time** into `../evaluation_data/derived/geometry.jsonl`, so the label is the box actually drawn, not a pre-computed guess. `pillow==12.2.0` is pinned because its bundled FreeType drives `getbbox()` metrics and therefore every fit decision and rendered pixel; pinning keeps images and labels byte-stable across rebuilds and Mac↔PROD.

**Second correction: the pipeline is already a declarative layout DSL, not drawing code.** An earlier draft of this document described the current backend as "imperative and brittle — every new document type is new drawing code." That is no longer true, and the correction matters because it changes both the migration cost and what Typst actually adds. As built today:

- `generators/layout_dsl/` (~4,000 lines) implements a **layout DSL with nine primitives** — `text`, `pair`, `block`, `rule`, `spacer`, `panel`, `split`, `table`, `banner` — dispatched by a walker (`engine.py`) over a YAML `body:` tree. Blocks nest (`panel`, `split`, `table` recurse through `render_children`), carry conditional `when:` guards, and resolve every parameter through a required-defaults mechanism that fails fast.
- **All 18 layouts** (8 bank, 6 receipt, 4 invoice) are declarative `body:` sequences in `config/layouts/*.yml`, sharing page boxes, budgets and blocks through YAML anchors. Adding or altering a layout is a YAML edit, not a code change.
- The per-type "renderers" are now **thin adapters of 65–93 lines** (`invoice.py`, `receipt.py`, `bank_statement.py`) that create the canvas, set the content region, and delegate to `render_body()`.
- Content and layout are decoupled: values bind out of the ground-truth entry, computed values (e.g. the ex-GST subtotal) come from declared `field_providers:`, and repeated rows come from row providers such as `pipe_fields`.
- Authoring diagnostics are strong: primitive errors are tagged with the failing block's path as they unwind, so a bad block reports `At: tax_invoice_standard.body[2].children[1]`.

So the *templating-layer* half of the migration is already done, in-house, with a vocabulary tuned to these documents. What remains genuinely unmatched is the **render backend**: real typesetting.

### Strengths
- **Zero rendering dependencies** beyond Pillow (HPND license, permissive). No browser, no TeX distribution. Already approved in PROD.
- **Full pixel control.** You can put any glyph at any exact pixel.
- **Labels are accurate and reflow-safe already.** Draw-time capture to `geometry.jsonl` records the actual drawn box, so wrapping/shrinking does not desync the label from the pixels. This is a genuine strength, not a weakness.
- **Reproducibility already solved.** Pinned Pillow/FreeType gives byte-stable images and labels across rebuilds and Mac↔PROD. Any new backend has to match this bar, not merely approach it.
- **Deterministic.** Same YAML gives the same PNG.
- **Already built and understood.** No migration cost, no new failure modes, no new PROD dependency request.

### Weaknesses
- **Realism ceiling (the one real limitation).** No real typesetting. `fit_text()` does greedy, lossless word-wrap with `shrink` / `wrap` / `shrink_then_wrap` strategies, so text does break and never truncates — but that is line-filling, not typesetting. What is absent is paragraph-level (Knuth–Plass) line-breaking, hyphenation, justification, kerning and ligature control, and table row-height balancing (`row_height` is a fixed pitch, so every unbudgeted cell is exactly one row tall). Multi-column is `split` with hard pixel widths, not flow. Documents therefore read as *drawn to a grid* rather than typeset. For an IE benchmark this is the core problem, because accuracy on grid-drawn documents generalises poorly to real ones. This, and essentially only this, is what motivates a change.
- **Typographic palette is narrow.** Exactly two vendored families in two weights (DejaVu Sans and DejaVu Sans Mono, regular and bold), selected by a boolean `mono:` flag. DejaVu is also not metric-compatible with the Calibri/Arial register that real AU business documents actually use, so the *typeface itself* is a visible distribution mismatch — arguably a cheaper realism win than the backend swap (see the font section below).
- **Structural diversity is now cheap, stylistic diversity is not.** Because layouts are YAML body trees, a new document *arrangement* costs a YAML edit. What is still expensive is a new *look*: there is no theme layer, so fonts, colour schemes and spacing scales are set per-layout by hand rather than swapped wholesale the way CSS variables allow. Diversity of structure: good. Diversity of aesthetic: still narrow.
- **DSL ceiling, not a code ceiling.** The remaining brittleness is that the vocabulary is fixed at nine primitives: anything they cannot express (rotated text, flowing multi-column, nested tables, overlapping decoration) needs a new primitive in `engine.py` and its drawer. That is a real limit, but it is a much higher ceiling than "new document type = new drawing code," and most business-document work stays inside it.

**Net:** cheap, deterministic, dependency-light, already correct on labels and reproducibility, and already declarative. Its deficiency is narrower than it first appeared: not authoring ergonomics, but genuine typesetting and stylistic variety. The migration question is correspondingly narrower — is *real typesetting* worth a two-week sprint and a new PROD dependency, given that labels, byte-stability, and the declarative authoring layer are not problems to be fixed but a bar to be preserved?

---

## Backend A: HTML/CSS + browser engine (Playwright/Chromium)

Jinja2 emits HTML; a headless browser renders it; you extract labels from the DOM.

### Strengths
- **Highest realism and visual variety.** You inherit the browser's full typesetting stack: line-breaking, font fallback, sub-pixel positioning, ligatures, table reflow. Swapping CSS themes (fonts, colours, spacing via CSS variables) multiplies visual diversity cheaply, which broadens the synthetic distribution.
- **Easiest, reflow-safe label extraction.** Wrap each labelled field and read its box after layout runs:

  ```html
  <span data-field="invoice_total" data-value="{{ inv.total }}">{{ inv.total | currency }}</span>
  ```

  ```python
  # Playwright
  boxes = page.eval_on_selector_all(
      "[data-field]",
      """els => els.map(e => ({
          field: e.dataset.field,
          value: e.dataset.value,
          box: e.getBoundingClientRect()
      }))"""
  )
  ```

  The box comes from the engine *after* layout, so it stays correct even when text wraps. Note this matches, rather than beats, the PIL pipeline's existing draw-time `geometry.jsonl` capture: both record post-layout truth. The HTML advantage over PIL is realism, not label accuracy.
- **Multi-resolution.** Render once, rasterize at multiple DPIs for resolution-robustness sweeps.
- **Maintainable.** Templates are editable by anyone who knows HTML/CSS, decoupled from content logic.

### Weaknesses
- **Heavy dependency.** Chromium via Playwright is a large install and the slowest per-document backend here.
- **Determinism is version-sensitive.** Output can shift subtly across Chromium releases. You must pin the browser version for a reproducible benchmark.
- **Label plumbing is code you own.** The `data-field` instrumentation plus extraction layer must be written and tested.

**License:** Playwright and Chromium are Apache-2.0 / permissive. Jinja2 is BSD-3-Clause. Clean.

---

## Backend B: Typst (Jinja2→`.typ`, or Typst's own scripting)

A modern, Rust-based typesetting system. Single binary, fast compiles.

### Strengths
- **Label extraction far better than LaTeX.** First-class introspection (`here().position()`, `measure`, `metadata`) plus the `typst eval`/`query` command lets you capture the resolved position of any labelled element *at compile time* and emit a sidecar JSON of field → box → value. This is close to the DOM ergonomics without a browser, and it is **verified working locally** (see *Local feasibility check*).
- **Flexible layout.** Arbitrary placement, boxes, fills, and grids are natural, so business-document styling (coloured headers, logos, positioned blocks) is not a fight.
- **Deterministic and fast.** Millisecond compiles suit generating thousands of documents, and you keep byte-stable reproducibility.
- **Light dependency.** A single pinned binary, not a browser or a multi-GB TeX install.
- **Optional Jinja2.** Typst can ingest JSON and template internally, so you may not even need a separate content layer.

### Weaknesses
- **Younger ecosystem.** Fewer ready-made business templates, smaller community, occasional breaking changes. Pin the version.
- **Less visual diversity than HTML.** The aesthetic is "cleanly typeset." Closer to real business docs than LaTeX, but you can't swap themes as wildly as with CSS.
- **SVG export flattens to paths**, so don't rely on SVG structure for labels; use the introspection API.

**License:** Apache-2.0. Clean.

---

## Backend C: LaTeX (Jinja2→`.tex`)

The typographic gold standard, and the weakest fit for this specific job.

### Strengths
- **Best-in-class typography** and mature ecosystem.
- **Deterministic**, byte-stable if the TeX distribution is pinned. Vector PDF output.

### Weaknesses
- **Hardest label extraction of the four.** No DOM. You either instrument with `\zref-savepos` / `\pdfsavepos` (verbose, one macro per field) or post-parse the PDF text layer (PyMuPDF/pdfplumber) and fuzzily match extracted words back to semantic field keys. That matching step is exactly the error-prone failure mode a labelled benchmark cannot tolerate.
- **Wrong aesthetic.** LaTeX documents look like LaTeX. Real invoices, receipts, and AU business forms rarely do. Forcing the business-document look means swimming upstream, and the residual "LaTeX-ness" is a distribution mismatch working against benchmark validity.
- **Heavy install** (multi-GB TeX Live), slower compiles than Typst.

**License:** LPPL for LaTeX itself; permissive enough (generated PDFs are yours).

---

## Side-by-side matrix

| Backend | Realism / variety | Label extraction | Determinism | Speed | Deps | License |
|---|---|---|---|---|---|---|
| **PIL + YAML DSL** (current) | Low typesetting; structural variety already cheap (YAML `body:` trees), stylistic variety narrow (2 vendored families, no theme layer) | **Accurate** (draw-time `geometry.jsonl`) | High (pinned Pillow/FreeType) | High | None (already approved) | HPND (Pillow) |
| **HTML/CSS + Playwright** | **Highest** | **Easiest, reflow-safe** (DOM boxes) | Lower (engine-version sensitive) | Medium | Heavy (Chromium) | Apache-2.0 |
| **Typst** | High, moderate variety | Good (introspection + `eval`/query) | High | **Highest** | Light (single binary) | Apache-2.0 |
| **LaTeX** | High typography, wrong aesthetic | **Hardest** (PDF parse / zref) | High | Low | Heavy (TeX Live) | LPPL |

---

## The technical risk: preserving label fidelity across the migration

The risk here is **not** that the current labels are weak. They are already accurate (draw-time `geometry.jsonl`) and byte-stable (pinned Pillow/FreeType). The risk is that a new backend *regresses* that accuracy or reproducibility. The bar any backend must clear is "match PIL's existing label quality," and the question is how easily each backend does so.

- **PIL (current):** the reference bar. Draw-time capture, byte-stable, already correct.
- **Typst:** labels come from compile-time introspection to a sidecar JSON. Reflow-safe, no browser. **Verified locally** (see *Local feasibility check*): boxes overlay exactly on the rendered glyphs once the baseline offset is handled. Meets the bar.
- **HTML/Playwright:** labels come from `getBoundingClientRect` after real layout. Reflow-safe by construction. Meets the bar, at the cost of an instrumentation-and-extraction layer plus browser-version pinning to keep reproducibility.
- **LaTeX:** labels require either heavy per-field macro instrumentation or fuzzy PDF-text matching. Highest risk of *regressing below* the current bar, which is why it ranks last.

**Recommendation:** the label mechanism for Typst is already proven locally. The remaining verification is a parity check on one real document type (does the Typst-produced `geometry.jsonl` match PIL's for equivalent content?), plus confirming byte-stable reproducibility with pinned `typst` and vendored fonts.

---

## Realism and benchmark validity

External validity is the benchmark's product. A VLM's IE accuracy on grid-drawn PIL invoices tells you less about its accuracy on real invoices than its accuracy on properly typeset, visually varied invoices does. Moving to a real typesetting backend is therefore not cosmetic polish; it directly increases how much the benchmark's numbers mean. HTML/CSS gives the widest realism and diversity; Typst gives high quality with less variety; LaTeX gives high quality in the wrong visual register.

Be precise about the size of this gap, though, now that the DSL is accounted for. The current pages are not crude: they have real tables with headers, rules, fills and grouping, panels, banners, split columns, and lossless shrink/wrap text fitting. The residual tells are narrower than "looks hand-made" — fixed row pitch rather than balanced rows, greedy rather than optimal line breaks, no hyphenation or justification, no kerning control, and a typeface (DejaVu) that no Australian business actually uses. Two of those tells — typeface register and, on the degraded half, everything sub-pixel — are addressable without changing backend at all. That does not eliminate the case for Typst, but it does shrink the measured realism delta the sprint can claim, which is why pricing the cheap levers first (see the recommendation) is the honest sequence.

---

## Determinism and reproducibility

A benchmark should be reproducible. Ranked:

- **PIL (current):** deterministic and byte-stable *today*, via pinned Pillow/FreeType, verified across Mac↔PROD. This is the standing bar.
- **Typst and LaTeX:** deterministic, byte-stable with a pinned toolchain. Can match the PIL bar.
- **HTML/Playwright:** deterministic *only* if you pin the browser version; output can drift across Chromium releases, so matching the PIL bar takes more discipline.

If reproducibility is weighted heavily, Typst is attractive because it combines determinism with good realism and good labels.

---

## Camera-scan (mobile-phone) inputs and the backend decision

Real inputs are frequently mobile-phone photos of documents, not clean scans.

**Delivered 2026-08-06.** When this section was first written the pipeline had *two* degradation paths, and the weaker one was the one wired in: a mild, uniform seven-step PIL effect (`degrade_image`) applied to all three document types, while the far more realistic camera-scan warp sat in a standalone script that nothing called. Degradation is now receipt-only and tiered — see *What shipped* below. `rectify_camera_scan.py` remains an offline pass that re-detects the receipt quad from pixels and un-warps it upright.

**Orthogonal to the backend choice.** The degradation is a post-render pixel operation on the clean PNG. It consumes any raster identically, whether PIL or Typst produced it, so mobile-scan realism does not change the recommendation.

**Free under value-F1 scoring (confirmed).** The benchmark scores extracted field *values* (value-F1), not localization. Field values are invariant to any geometric or photometric distortion, so the phone-scan degradation needs no ground-truth transform and adds no label risk: the same value ground truth is valid on clean, degraded, and rectified images alike. (Had localization been scored, perspective would turn axis-aligned boxes into quadrilaterals and require pushing each box's corners through the homography plus a polygon ground-truth format. That workstream is not needed.)

**Realism ROI is partly discounted, not erased.** Blur, JPEG, and noise mask the fine typography (kerning, hinting, sub-pixel positioning) where Typst most beats PIL, so on the degraded half of the corpus those gains largely wash out. But the clean half shows typography fully, and layout-level realism (table structure, alignment, wrapping, multi-column) survives degradation, because a real phone photo of a real document still shows its real layout. The Typst gain is better read as *layout credibility*, which the camera preserves, than *prettier glyphs*, which it does not.

**This was the higher-ROI realism lever, and it was taken.** The domain gap versus real phone captures lived more in the degradation model than in the clean render. Enriching it was cheaper than a backend migration, independent of PIL vs Typst, and free under value-F1.

### The finding that redirected the design

An early draft of this section assumed Augraphy was the answer. Rendering one corpus receipt through every option side by side showed otherwise:

**Every Augraphy effect is a flat-page effect.** Ink bleed, lighting gradients, cast shadows, folds and dirty rollers all treat the document as a rectangle facing the camera square-on. None produce perspective, a background, or framing — and *that geometry* is the dominant visual difference between a flat degraded render and a real phone photo. Augraphy alone could not have closed the gap; the camera-scan warp already did, and had since it was written.

The conclusion was to use both, each for what the other cannot do. The full design and the stakeholder rationale that embedded the comparison sheet behind this finding were retired once the work shipped; both remain in git history, and the shipped behaviour is described in `generators/degradation/`.

### What shipped

- **Receipt-only.** Bank statements and invoices arrive as clean PDFs or printouts; degrading them modelled a workflow nobody has. The old corpus-wide `degrade_image` path is deleted, not deprecated — which also retired a `DEFAULT_DEGRADATION_PARAMS` merge that silently shadowed YAML with Python constants.
- **Three declared severity tiers** (light / moderate / heavy) declared entirely in `config/generation_config.yml`. The tier list *is* the variant count, so the config cannot contradict itself. The degraded evaluation set is 55 receipts × 3 tiers = 165 images, each with its own ground-truth row carrying its source receipt's values verbatim.
- **Ordering is load-bearing.** Augraphy damages the flat page (ink, creases, lighting) *before* the warp; blur, sensor noise and JPEG apply *after*, to the whole frame. A crease belongs to the paper and must be warped with it.
- **Reporting granularity.** Three tiers yield a curve — "94% light, 81% moderate, 58% heavy" — rather than one aggregate number, showing *where* a model degrades rather than merely *that* it does.

### What the integration actually cost

The three snags this document predicted were all real, and one it did not predict was the largest:

- **OpenCV conflict — predicted, and it fired.** `conda env update` did pull the full GUI `opencv-python` (48 MB), displacing the pinned headless build exactly as anticipated. Fix as documented: uninstall it and reinstall Augraphy `--no-deps`. `cv2` then resolves to the headless package throughout.
- **Dependency footprint — understated here by four times.** This document listed `numba`/`llvmlite`, `scikit-learn` and `scikit-image`. The real `--no-deps` closure is **16 packages**: those four plus `lazy_loader`, `imageio`, `tifffile`, `networkx`, `joblib`, `threadpoolctl`, `narwhals`, `requests`, `urllib3`, `idna`, `certifi` and `charset-normalizer`. All confirmed available in the internal mirror.

  Five of those — `requests`, `urllib3`, `idna`, `certifi`, `charset-normalizer` — turned out already to be present in PROD, which carries `requests` 2.34.2. So the request reduced to **13**: `augraphy`, `numpy`, and eleven `scikit`/`numba` transitives. All confirmed available.

  Those five are in the tree only because `import augraphy` loads *every* augmentation module, including `Scribbles` (fetches fonts by URL) and the Figshare dataset downloader. Neither is in the registered allow-list, so **the pipeline makes no network calls**: it will not hang or fail reaching out from an air-gapped PROD. That is a property of the allow-list rather than an accident, and registering a downloading augmentation later would quietly end it.
- **A NumPy ceiling, not predicted here.** `numba` caps NumPy at ≤2.4 while the environment resolved 2.5.1, so `numpy==2.3.5` is now pinned. This was verified render-neutral before anything downstream was built — the renderers draw through PIL, not NumPy, and the pixel snapshots still match.
- **A latent determinism hazard in Augraphy itself.** `AugraphyPipeline` writes its input into `os.getcwd()/augraphy_cache/` unconditionally, ignoring `save_outputs`, and its `PageBorder`, `BleedThrough` and `BookBinding` augmentations *read* from that cache. A pipeline including any of them would composite whatever the previous run left behind — output depending on directory state rather than on the seed. None of the registered augmentations read it, but the pipeline now runs in a throwaway directory so that is structurally true rather than true-by-inspection.

### Reproducibility

Each variant's seed derives from the entry's `degradation_seed` combined with the tier index, so a tier's output is stable and independent of tier ordering. Augraphy is pinned at 8.2.6.

**Values did not move; only geometry did.** Of the six derived artefacts, only `docile.jsonl` — the one consuming bounding boxes — changed. `ground_truth.csv`, `ground_truth.jsonl`, `cord.jsonl`, `native.jsonl` and `doc_refs.jsonl` were byte-identical. That is the correct signature of a pure rendering change under value-F1, and it is the same invariance any backend migration must demonstrate.

**Rectifier accuracy note (now corrected in the source).** The old docstring described degrade and rectify as "exact numerical inverses." They are approximate: the rectifier re-estimates the quad from Canny/contours rather than reusing the stored homography, sizes its output to the detected edge lengths, and the blur/noise/JPEG steps are not invertible at all. Moot under value-F1; it matters only for spatial round-trip validation.

**Open: tier calibration.** The `heavy` tier was tuned against rendered output rather than shipped as first written — Augraphy draws a fold as a hard black wedge rather than a shaded crease, which at two folds landed across the supplier name. But there are still no real phone photographs of Australian receipts to calibrate against, so "hard but fair" remains informed judgement. Every parameter is YAML, so retuning costs a config edit.

---

## Licensing summary (standing constraint: permissive preferred)

| Component | License | Verdict |
|---|---|---|
| Pillow | HPND | Permissive, fine |
| Jinja2 | BSD-3-Clause | Permissive, fine |
| Playwright / Chromium | Apache-2.0 | Permissive, fine |
| Typst | Apache-2.0 | Permissive, fine |
| LaTeX (LaTeX itself) | LPPL | Acceptable; generated PDFs are yours |
| Augraphy 8.2.6 (document degradation) | MIT | **In use.** Permissive; its declared dep is `opencv-python` (full), so it must be installed `--no-deps` to keep `opencv-python-headless`. 16 transitive packages, all confirmed mirrored; `numba` caps NumPy at ≤2.4 |
| wkhtmltopdf | LGPL/GPL, unmaintained | **Avoid** |
| WeasyPrint (HTML alt to Playwright) | BSD-3-Clause | Permissive, but weaker CSS and no easy post-layout box query |

No copyleft blockers in the recommended stacks. Avoid wkhtmltopdf.

---

## Migration cost and risk (the two-week sprint)

**What actually consumes the sprint:**

1. **Label-extraction layer (highest risk, highest value).** Instrumenting templates and building/validating the field → box → value extraction. This is the make-or-break component; budget it first.
2. **Template authoring — cheaper than it looks, because the templates already exist declaratively.** This is not authoring 18 layouts from scratch: it is *translating* 18 existing YAML `body:` trees, built from nine known primitives, into the target backend's equivalents. Much of it is mechanical and scriptable (a `body:` walker emitting `.typ` instead of PIL calls), and the per-primitive mapping is the real work: `text`/`pair`/`rule`/`spacer` are near-trivial in Typst, `table` and `split` are where the effort concentrates, and `panel`/`banner` are styling. Budget by *primitive*, not by document type.
3. **Content-layer rewiring.** Pointing the existing YAML/Faker content generation at the new templates. Genuinely straightforward here, because content and layout are already separated: values bind out of the ground-truth entry and computed/repeated values already come from declared `field_providers:` and row providers, which port as-is.
   **The corollary cuts the other way, though:** since the DSL already delivers declarative authoring, the sprint no longer buys "templates instead of code." It buys typesetting and stylistic variety, and nothing else. Weigh it on that alone.
4. **Dependency and reproducibility setup.** Pinning the browser or Typst binary, adding it to the conda environment YAML, ensuring headless rendering runs in the target environment (this is a common time-sink for Playwright specifically).
5. **Parity validation.** Confirming the new pipeline produces at least as many document types as the old one, with labels verified against a held-out check.

**What could blow the estimate:**

- Label extraction turns out fiddly for a specific document type (nested tables, rotated text, overflow). Mitigate with the upfront spike.
- Headless rendering environment issues (fonts missing in the container, Chromium sandbox flags). Mitigate by validating the environment on day one.
- Scope creep: trying to migrate *all* document types and add new realism features in the same sprint. Keep the sprint to a like-for-like migration of existing types plus the new label layer; defer new document types.

**De-risking plan (recommended):**

- **Days 1 to 3 (spike):** Pick the single most common document type. Stand up Jinja2→HTML→Playwright (or Typst), render it, extract labels, and verify the boxes round-trip exactly against a manual check. Confirm headless rendering works in the target conda environment. **Go/no-go gate here.**
- **Days 4 to 8:** Migrate the remaining existing document types as templates; port content wiring; add tests for label fidelity per type (this is the correctness contract, mirror source structure in `tests/`, keep coverage up).
- **Days 9 to 10:** Reproducibility hardening (pin toolchain, environment YAML), parity validation against the old pipeline, and documentation.

If the day-3 gate fails on label fidelity, you have spent three days rather than ten, and you can fall back to keeping PIL or trying the alternate backend.

---

## Local feasibility check (completed)

A disposable conda env (`typst_probe`, built from a scratch YAML so the `synthetic` env stayed untouched) was used to validate the recommended Typst path end to end on macOS. Findings:

- **Install:** `typst=0.15.1` from conda-forge installed cleanly as a **single 16.6 MB package with zero dependency sprawl**. Confirms the "light dependency" claim concretely.
- **Render:** compiled a small instrumented invoice to a 300 dpi PNG with no issues.
- **Label extraction:** the introspection query returned per-field records (`name`, `value`, `page`, `x`, `y`, `width`, `height`). Overlaying those boxes on the rendered PNG showed them **horizontally exact** and, after one baseline correction, **tight on every field** (supplier, ABN, invoice number, date, total). The mechanism works.
- **Two corrections to Appendix B, now folded in:**
  - `typst query` is **deprecated** in 0.15.1. Use `typst eval 'query(<field>).map(it => it.value)' --format json --in FILE`.
  - `here().position()` returns the text **baseline**, not the visual top. The box top is `baseline − height`. This is the difference between boxes landing one line low and landing tight.
- **pt→px** conversion confirmed: `px = pt × ppi / 72` (e.g. ×300/72 at 300 dpi).

**Scope of this check:** it proves the tooling works *on macOS from conda-forge*. PROD resolves from internal Artifactory mirrors with public repos unreachable, so this does not by itself put `typst` in PROD. Per the operator, Typst approval is **not in doubt, only delayed**, so this is a lead-time item rather than a go/no-go risk. The action is to submit the mirror request now so the delay overlaps other work, not to wait on a yes/no.

---

## Production provisioning and font reproducibility

The production environment is locked-down and reproducibility-strict: every package and font is a **request**, not a `pip install`. That makes *provisioning burden* a first-class selection axis, arguably the deciding one, and it cuts against a realism-first ranking.

### Fonts are the shared reproducibility landmine

This applies to every rendering backend, so state it once. If the renderer falls back to whatever fonts happen to be installed in PROD, the output is neither reproducible nor realistic: missing fonts silently become fallback fonts, so the same input yields different pixels across environments, and the changed glyph metrics shift text wrapping.

**Current state: both gaps now closed (implemented 2026-08-06).** The repo previously vendored four DejaVu faces with `load_font()` searching the bundled directory *first* but falling back to platform system paths. Both problems that created have been fixed:

- **The fallback hole is gone.** `load_font()` had fallen back to system paths (e.g. `/usr/share/fonts/truetype/dejavu/…`) when a bundled face was missing — exactly the silent-substitution path this section warns about, swapping glyph metrics and breaking byte-stability without erroring. Each `(family, weight)` now resolves to exactly one vendored file, and a missing file raises a four-element diagnostic. Note the measurement path was *already* guarded (`_text_width()` called `assert_bundled_font()`); the exposure was the ten draw-path `load_font()` calls that did not. Removing the fallback fixes that at the root, so every call site is safe by construction rather than by remembering to assert.
- **The realism gap is closed, and became a diversity win.** DejaVu is metric-compatible with nothing in the AU business register. The vendored set is now **Carlito** (Calibri-metric), **Liberation Sans** (Arial-metric) and **Liberation Mono** (Courier-metric), each with its OFL license file. Rather than a 1:1 swap, the boolean `mono:` flag was replaced by a `family:` selector resolved through the DSL's normal block → `defaults:` chain and validated at startup against the font registry — so layouts now *choose* a face. That directly attacks the "no stylistic diversity" weakness identified above: the eight bank layouts split by bank (CBA/NAB carlito, Westpac/ANZ liberation_sans), the four invoice layouts split two and two, and all six receipt layouts stay monospace because their fixed-advance separators and narrow pages depend on it.

Two measurements worth recording from that work, because they bear on the Typst decision:

- **Values did not move; only geometry did.** Of the six derived artefacts, only `docile.jsonl` — the one consuming bounding boxes — changed. `ground_truth.csv`, `ground_truth.jsonl`, `cord.jsonl`, `native.jsonl` and `doc_refs.jsonl` were byte-identical. That is the expected and correct signature of a pure rendering change under value-F1 scoring, and it is the same invariance any backend migration must demonstrate.
- **Fits got easier, not tighter.** Carlito measures ~19% narrower than DejaVu Sans at the same nominal size, and Liberation Mono is *advance-identical* to DejaVu Sans Mono, so receipt geometry was unchanged. All 165 documents re-rendered with no `FitError`. A backend migration should expect the opposite risk profile and budget for it.

The general fix is identical everywhere and is the thing to actually request:

1. **Vendor the font files inside the repo** (a `fonts/` directory) and point the renderer at that directory explicitly, so rendering never depends on system font state.
2. **Request appropriately-licensed fonts only.** Calibri, Arial, and Times are Microsoft/Monotype proprietary and *not* redistributable, so they cannot be vendored. Use OFL (redistributable, embeddable) metric-compatible substitutes:
   - **Carlito** (OFL) is metric-compatible with Calibri
   - **Liberation Sans / Serif / Mono** (OFL) match Arial / Times / Courier
   - **Noto** (OFL) for broad script coverage

   Ship each font's license file alongside it. The "font request" then reduces to "approve these OFL files in the repo," which is far easier to clear than a system-wide font install.

### Provisioning burden by backend

| Backend | What must be requested in PROD | Font handling | Approvability in locked-down PROD |
|---|---|---|---|
| **PIL** (current) | Pillow (almost certainly already approved) | `ImageFont.truetype(path)` loads a vendored TTF directly. Zero system-font dependency. | **Easiest.** Already present; fonts are just files. |
| **Typst** | One package: `typst` (on conda-forge; a PyPI wheel also bundles the binary). Goes in `environment.yml` like anything else. | `--font-path fonts/ --ignore-system-fonts`. Fully self-contained. | **Easy.** Single permissive (Apache-2.0) package, fonts vendored. |
| **LaTeX** | TeX Live: multi-GB, typically a root/OS install, not a conda package. | TeX font packages or fontspec. | Hard. Large OS-level request. |
| **HTML/Playwright** | `playwright` (pip) **plus** the Chromium binary (~150 MB, often blocked by egress policy) **plus** ~30 system shared libraries (`libnss3`, `libgbm`, `fontconfig`, …) installed as **root**. | Chromium renders via fontconfig; needs fonts installed system-wide or a fontconfig config plus `fc-cache`, another root-level request. | **Hardest.** Browser binary + root system libraries + system font configuration. The classic "works locally, fails in the locked container" trap. |

### Effect on the ranking

Under a hard PROD-provisioning gate, the realism-first ranking flips:

- **Typst** becomes the pragmatic winner: a single permissive package with fully vendorable, self-contained fonts is the dependency request you can actually get approved and reproduce byte-for-byte. It already wins the determinism axis.
- **Playwright's** realism edge comes at the cost of the heaviest, root-level, egress-sensitive footprint, which is exactly what a reproducibility-strict locked-down environment resists most.
- **PIL** stays the zero-provisioning option, and its labels and reproducibility are already correct; it simply cannot deliver the realism gain, which is the sole reason to move.

Concrete dependency-request manifests for each option are in Appendix C.

## Recommendation

The recommendation is stated against the binding constraint: a locked-down, reproducibility-strict production environment where dependencies and fonts must be requested.

1. **Target Typst.** It is the best fit under the PROD constraint: a single Apache-2.0 package, deterministic output, and self-contained vendored fonts, while still delivering real typesetting and reflow-safe labels via introspection. It beats LaTeX decisively on licensing, label extraction, and layout flexibility, and beats Playwright decisively on provisioning burden and reproducibility.
2. **Reserve HTML/CSS + Playwright** for the case where the production environment can absorb a browser binary, root-level system libraries, and system font configuration. It offers the highest realism and visual diversity and the easiest label extraction, but its provisioning and reproducibility costs are the highest here. Choose it only if visual diversity is weighted above provisioning cost *and* the environment can take the footprint.
3. **Rank LaTeX last** for this use case despite its quality, on aesthetic mismatch, label-extraction risk, and a heavy OS-level install.
4. **Keep PIL as the fallback**, not the destination: zero provisioning, labels and reproducibility already correct, and its authoring layer is already declarative. What it cannot deliver is real typesetting and wide stylistic variety — now the sole motivation for the sprint, and a narrower one than this document originally assumed. Note the DSL raises the bar Typst must clear: the migration must reproduce nine primitives' behaviour, per-field pixel budgets, the lossless no-truncation `FitError` contract, and block-path diagnostics, or it is a net regression in authoring quality even while it gains typesetting.
5. **Consider a hybrid later:** Typst for the clean-corporate slice of the distribution (deterministic, fast, cheap labels, easily provisioned) and, only if the environment allows, HTML/Playwright for a messier, more visually diverse slice, with one Jinja2 content layer feeding both. This is a larger commitment than a single sprint.
6. **Whatever backend, vendor OFL fonts in the repo** and render against them explicitly. This is the single highest-leverage reproducibility decision and is independent of the backend choice. **Done (2026-08-06):** the system-font fallback is deleted (a missing face now fails loudly), and DejaVu has been replaced by metric-compatible Carlito + Liberation Sans + Liberation Mono, selected per-layout through a new `family:` DSL key. All baselines were re-captured; the suite is green. This also means the OFL font set Typst would need is already vendored and cleared, so that part of the migration is pre-paid.
7. **The label mechanism is already de-risked locally** (see *Local feasibility check*), and Typst approval for PROD is assured, only delayed. So there is no go/no-go availability risk; the two remaining items are scheduling, not gating: (a) **submit the Artifactory mirror request for `typst` (and the OFL fonts) now**, so its lead time runs in parallel with the Augraphy work, and (b) a one-document parity check that Typst's `geometry.jsonl` matches PIL's for equivalent content. Neither blocks starting.
8. **Augraphy-based degradation enrichment — done (2026-08-06).** This was recommended first, ahead of the Typst request's lead time, on the grounds that the largest realism gap versus real captures lived in the degradation model rather than the clean render. That held. Degradation is now receipt-only and tiered, built on Augraphy for paper and ink damage plus the pre-existing camera-scan homography for geometry. The important correction the work produced: **Augraphy alone would not have sufficed**, because every one of its effects is a flat-page effect and the dominant gap is geometric. See *Camera-scan (mobile-phone) inputs*.

## Open questions before committing

- How many distinct document types must reach parity in the sprint?
- Is byte-stable reproducibility a hard requirement (favours Typst) or a nice-to-have (allows Playwright)?
- Where does rendering run (local vs. remote), and is a headless browser acceptable there?
- Is visual diversity or determinism the higher-weighted benchmark property?
- What is the *lead time* for the Typst Artifactory request (approval itself is assured), so the Augraphy work can be sized to overlap it? And can a request cover root-level system libraries and fonts, or only conda/pip packages? (The latter still determines whether Playwright is viable, since its browser binary and ~30 root libraries are a heavier class than a single package.)
- Can the vendored OFL `fonts/` directory be approved for the repo, and are these OFL families already cleared for use? (DejaVu is already vendored and tracked, so the precedent exists; the ask is to add Carlito/Liberation alongside or in place of it.)
- Given the DSL already provides declarative authoring, is *typesetting quality alone* worth the sprint — and can Typst reproduce the nine primitives, the per-field pixel budgets, and the lossless `FitError` contract without losing the block-path diagnostics? A parity check should cover these, not just box geometry.
- **Now the central question, since both cheap levers are spent:** did swapping DejaVu for Carlito/Liberation *and* rebuilding degradation close enough of the realism gap to defer the backend migration entirely? Both are shipped, so this is no longer hypothetical — it can be measured by scoring a model against the new corpus and comparing to the old numbers. Doing that before committing the sprint would price it honestly, and it is the single most useful next step.
- ~~Are Augraphy's transitive dependencies mirrored in Artifactory, and does a `--no-deps` install coexist with the pinned `opencv-python-headless`?~~ **Answered:** all 16 are mirrored (one at a different patch version, harmless since only `augraphy` itself is pinned), and `--no-deps` does preserve the headless build — though `conda env update` pulls the full GUI OpenCV first and it must be uninstalled. `numba` additionally caps NumPy at ≤2.4, now pinned.

---

# Appendix A: What the DOM is, and why it matters for labels

The label-extraction argument for the HTML/CSS backend rests on the DOM, so this appendix explains it from first principles.

## The core idea

The DOM (Document Object Model) is the browser's **live, in-memory representation of a web page as a tree of objects**. When a browser loads HTML, it does not keep the raw text and re-read it. It parses that text once into a tree of nodes, where every tag, every piece of text, and every attribute becomes an object you can query and change.

This HTML:

```html
<div class="invoice">
  <span data-field="invoice_total">$1,240.00</span>
</div>
```

becomes, conceptually, this tree:

```
document
└── div.invoice
    └── span (data-field="invoice_total")
        └── "text: $1,240.00"
```

Each box in that tree is a **node object** with properties (attributes, text content) and methods (functions you can call on it). "The DOM" is that whole tree plus the API for walking and manipulating it. It is the API that JavaScript uses to find elements, read their text, change styles, and respond to events. When a page updates without reloading, that is JavaScript mutating the DOM tree and the browser re-rendering from it.

## The key distinction: HTML text vs. the DOM

- **HTML** is static source text. It says *what* should be on the page.
- **The DOM** is what exists *after* the browser has parsed that text, applied the CSS, and run the layout engine.

The crucial consequence: **the DOM knows where things actually ended up on screen**, because layout has already run. The HTML source says "put this span here"; the DOM (post-layout) can report "this span occupies the rectangle from x=412, y=880 to x=498, y=902." The HTML text cannot tell you that, because the exact position depends on fonts, wrapping, and table reflow that only the layout engine resolves.

## Why this makes labels easy

`getBoundingClientRect()` is a DOM method: you call it on a node and it returns that node's real, rendered rectangle in pixels, after layout.

```python
boxes = page.eval_on_selector_all(
    "[data-field]",
    """els => els.map(e => ({
        field: e.dataset.field,               // read an attribute off the DOM node
        box:   e.getBoundingClientRect()      // ask the node where it actually is
    }))"""
)
```

The flow is:

1. Jinja2 emits HTML text with `data-field` markers on each labelled value.
2. The browser parses that text into the DOM and runs layout.
3. You query the DOM for every `[data-field]` node and ask each one for its rectangle.

Because the rectangle comes from the DOM *after* layout, it stays correct even when text wraps or a table reflows.

**This is not a guarantee the PIL pipeline lacks.** (An earlier draft claimed it was; that claim contradicted this document's own key correction and is withdrawn.) The PIL pipeline reaches the same guarantee by the opposite route: instead of querying position after layout, it *is* the layout engine, so it records each box at the moment it draws it (`BoxRecorder` to `../evaluation_data/derived/geometry.jsonl`). Post-hoc query and draw-time capture are two ways of arriving at the same post-layout truth. The DOM's advantage over PIL here is ergonomic and realism-related (you get a real typesetting engine for free), not label accuracy.

**One-line mental model:** HTML is the recipe; the DOM is the cooked dish the browser will actually let you inspect and poke.

---

# Appendix B: The equivalent position mechanism in Typst

Typst gives the same authoritative post-layout position without a DOM or a browser. It reaches the result through a different mechanism, so this appendix documents it.

## The mental model: multi-pass compilation instead of a live tree

A browser holds a persistent DOM you can poke at any time. Typst does not. Instead, Typst **compiles the document more than once**, in a loop, until the layout stops changing (the same reason LaTeX runs twice to resolve cross-references). On an early pass it lays everything out; on a later pass, any element can ask "where did I end up?" because the previous pass already computed it.

That capability is Typst's **introspection** system, and it is the direct analogue of `getBoundingClientRect`. The position is authoritative because it comes after layout has run and converged.

## The building blocks

- **`context`** enters a location-aware scope where position-dependent queries are allowed. (This replaced the older `locate(loc => ...)` callback style in recent Typst.)
- **`here()` / `.location().position()`** gives an element's absolute placement: a dictionary with `page`, `x`, `y`. That is the top-left point, the analogue of the rect's `left`/`top`.
- **`measure(content)`** gives the size: `width` and `height`. Combine point plus size and you have a full bounding box.

## Putting it together: a `field()` helper

The idiom is to wrap each labelled value in a helper that, inside a `context`, computes its own position and size and stashes them into a **`metadata`** element (an invisible node carrying arbitrary data), tagged with a label so it can be found later:

This is the exact helper validated in the feasibility check (positions converted to points with `.pt()` so the sidecar carries plain numbers):

```typst
#let field(name, value) = context {
  let pos  = here().position()      // absolute placement, post-layout (y is the BASELINE)
  let size = measure(value)         // width and height of the value
  [#metadata((
    name:   name,
    value:  value,
    page:   pos.page,
    x:      pos.x.pt(),
    y:      pos.y.pt(),
    width:  size.width.pt(),
    height: size.height.pt(),
  )) <field>]
  value                             // actually render the value
}

// usage in the template:
Total: #field("invoice_total", "$1,240.00")
```

Every `field(...)` call now renders the value *and* leaves behind an invisible metadata marker recording exactly where that value landed.

## Extracting it: `typst eval` to a JSON sidecar

A built-in CLI pulls those markers out as JSON, with no rendering-engine scripting required. Note: the older `typst query` subcommand is **deprecated** as of Typst 0.15.1; use the `typst eval` form:

```bash
# render the document
typst compile invoice.typ invoice.png --ppi 300

# extract the labels as a sidecar JSON (non-deprecated form)
typst eval 'query(<field>).map(it => it.value)' --format json --in invoice.typ > labels.json
```

Because the position and size were computed inside the `context` and stored in the metadata, the JSON contains the full field → box → value record. That JSON *is* the ground-truth label file.

## The Python glue

```python
import json, subprocess

subprocess.run(["typst", "compile", "invoice.typ", "invoice.png", "--ppi", "300"], check=True)
raw = subprocess.run(
    ["typst", "eval", "query(<field>).map(it => it.value)",
     "--format", "json", "--in", "invoice.typ"],
    capture_output=True, text=True, check=True,
).stdout
labels = json.loads(raw)  # each has name, value, page, x, y, width, height (all in pt)
```

## The one gotcha: points, not pixels

Typst positions come back as lengths in **typographic points (pt)**, not image pixels. When you rasterize with `--ppi 300`, convert to match the PNG:

```
pixels = pt * ppi / 72
```

This is the same class of issue as the CSS-px vs. device-px conversion on the browser side.

## Honest caveats

- **Baseline vs. top (confirmed in testing).** `here().position()` for inline content returns the text **baseline**, not the visual top of the glyphs. In the feasibility check, drawing the box downward from that y put every box exactly one line low; drawing it as `[top = baseline − height, bottom = baseline]` made every box tight. So the consumer must subtract the field height (or wrap the value in a `box`/`block` and offset by the font ascent). This is a one-line calibration, not a blocker.
- **Convergence.** Because positions feed into metadata, Typst iterates until stable. This is normally invisible and fast, but pathological self-referential layouts can fail to converge.

## The parallel in one line

| | HTML/Playwright | Typst |
|---|---|---|
| Source of truth | Live DOM tree | Multi-pass compile + introspection |
| "Where am I?" call | `el.getBoundingClientRect()` | `here().position()` + `measure()` |
| Extraction | JS eval over `[data-field]` nodes | `typst eval 'query(<field>)…' --format json` |
| Needs a browser? | Yes (Chromium) | No (single binary) |

Same outcome (reflow-safe, post-layout boxes), reached without a DOM or a browser, which is why Typst is the light, deterministic alternative in the comparison.

---

# Appendix C: Production dependency-request manifests

Concrete manifests to submit for approval, per backend option. The asymmetry between them is itself part of the argument: the Typst request is one package plus a folder of OFL font files; the Playwright request is one package plus three separate root-level or egress-gated requests.

## Recommended: Typst

`environment.yml` addition:

```yaml
dependencies:
  - typst        # conda-forge, Apache-2.0
  # Fonts are vendored in the repo under fonts/ (Carlito, Liberation, Noto — all OFL),
  # each shipped with its license file. Rendering ignores system fonts entirely:
  #   typst compile --font-path fonts/ --ignore-system-fonts invoice.typ invoice.png --ppi 300
```

What to request: **one package** (`typst`), plus repo approval for the vendored `fonts/` directory of OFL files. No root access, no egress, no system font configuration.

## Alternative: HTML/CSS + Playwright

`environment.yml` addition:

```yaml
dependencies:
  - pip:
      - playwright                 # Apache-2.0
```

Plus the following, which fall **outside** the conda environment and require root and/or egress approval:

```bash
playwright install chromium        # ~150 MB browser binary  (network egress)
playwright install-deps            # ~30 apt system libraries (root)
# system fonts + fontconfig cache:
#   place OFL fonts in a system font dir (root) and run: fc-cache -f
```

What to request: one pip package **plus** a browser-binary download (egress), **plus** ~30 OS libraries (root), **plus** system-level font installation and cache rebuild (root). This is the heaviest footprint of the options and the most likely to stall in a locked-down environment.

## Fonts (common to any backend)

Vendor these OFL families in the repo, each with its license file. All are redistributable and embeddable, and metric-compatible with the proprietary fonts common in AU business documents:

| Vendored font | License | Substitutes for |
|---|---|---|
| Carlito | OFL | Calibri |
| Liberation Sans / Serif / Mono | OFL | Arial / Times / Courier |
| Noto (as needed) | OFL | broad script coverage |

Do **not** vendor Calibri, Arial, or Times themselves: they are Microsoft/Monotype proprietary and not redistributable.
