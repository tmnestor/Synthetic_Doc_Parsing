# Parsing Benchmark, Stage 2: Native PDFs Typeset with Typst

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Typeset stage one's 165 documents with Typst, plain and with distractors, each page as a native
PDF and as a 300 dpi PNG rasterised from it, with OmniDocBench ground truth proven against the PDF's own
text layer; score MinerU (both back-ends), Docling, Marker and a no-model text-layer baseline on both
inputs; report each tool's native-PDF gain. Exit criterion: the perfect-score check passes on both
typeset sets, and the leaderboard carries all 18 runs and the gain.

**Architecture:** Content is unchanged: field values, distractors and providers are chosen by today's
Python. Task 1 pins Typst and `pypdfium2` and gives every layout a physical page. Task 2 translates a
layout tree into Typst markup through one shared template. Task 3 compiles a page, reads Typst's block
boxes and the PDF's words, and reshapes them into stage one's ink and block records, so stage one's
`build_page` and `check_page` serve unchanged. Task 4 is the feasibility gate, run with the project owner.
Task 5 translates every primitive and layout and moves the distractor checks onto Typst geometry. Task 6
adds the two sets to the snapshot. Task 7 teaches the runner PDF input and adds the baseline. Task 8 runs,
scores and documents.

**Tech Stack:** Python 3.12, the layout DSL, Typst (pinned CLI), `pypdfium2` (pinned), PyYAML, Pillow
(overlay images, rasterisation), segno and python-barcode (unchanged), pytest, conda env `synthetic`;
OmniDocBench at its stage-one pin; MinerU, Docling and Marker in their stage-one envs.

**Spec:** `docs/parsing_benchmark_stage2_design.md`. Where the spec is silent or disagrees with the code,
the Decisions below rule.

## Global Constraints

- Conda env `synthetic`; run every command as `conda run -n synthetic <command>` from the repository root.
  Typst and `pypdfium2` enter `environment.yml` (conda-forge) at exact versions; the parsers and the
  evaluator keep their stage-one envs under `envs/`.
- The PIL path is untouched: every stage-one pixel, geometry, derived, eval-format and parsing pin passes
  with no re-bless, and stage one's halves export byte for byte as before.
- YAML is the single source of truth: every new key (the layouts' `page:` and everything in
  `config/typeset_config.yml`) is required, and Python holds no default for any of them.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected (with an example)
  and Recover; tests assert this with `assert_diagnostic_error`.
- Nothing generated is written inside the repository: typeset pages, predictions and scores go under
  `eval_set.output_root` (`../evaluation_data`); test output goes to `tmp_path`.
- The parsers run locally on Apple silicon, as in stage one (the owner-approved exception); one at a time.
- No real business or brand names, and no value copied from `ground_truth/`, in code, tests or prose;
  example values are invented and checked absent from `ground_truth/`.
- Line length 108; Python 3.12 types; `pathlib.Path`; Google-style docstrings; `raise ... from err` in
  `except` blocks.
- `tests/` is tracked: test changes are committed with their task. Per-task gate: the tests the task names
  and the touched test directories, `ruff check --fix --ignore ARG001,ARG002,F841`,
  `ruff format --check`, `mypy . --ignore-missing-imports`, `python -m generators.pipeline validate`. The
  full fast suite runs once, at the end of Task 8. Every commit lands on `main` and is pushed straight
  away. Commit messages carry no trailers.
- Prose uses British spelling and no em-dashes; no tooling references in tracked files.

## Decisions

Numbered so reviews can cite them.

1. **Typeset sets are not halves.** Stage one's `HALVES` tuple and everything keyed on it stay as they are.
   The new sets are `TYPESET_SETS = ("typeset_plain", "typeset_distractor")` in
   `generators/snapshot/config.py`, with their own required key `eval_set.typeset_prefixes`.
2. **Typst output is reshaped into stage one's records.** Task 3 turns Typst's blocks and the PDF's words
   into a `geometry` dict of the shape stage one's renderers fill (`ink`: `InkRecord` per text line,
   `blocks`: `BlockRecord` per block, `width`, `height`), in PNG pixels. `build_page`, `check_page`,
   `table_html` and the scorer are reused unchanged; only the source of the records is new.
3. **Lines come from words.** `pypdfium2` gives characters with boxes. Characters are grouped into words
   at spaces, and words into one line where they share a block and a baseline (within a quarter of the
   font size). A line's text is its words joined by one space, in left-to-right order.
4. **Block and cell ownership comes from Typst.** Each block, and each table cell, is wrapped in a labelled
   `metadata` element carrying its block id (and cell row and column); `typst query` returns each one's
   position and measured size. A word belongs to the block (and cell) whose box contains its centre.
5. **No hyphenation, no ligatures, no justification.** `#set text(hyphenate: false, ligatures: false)` and
   left-aligned paragraphs, so the text layer holds exactly the letters the ground truth records. Typst
   still wraps at spaces.
6. **Any Typst warning fails the page.** A missing glyph, an unknown font or an overflowing box is a
   warning in Typst; the compiler wrapper treats every warning as a page failure, four-part, quoting it.
7. **Points in the PDF, pixels in the ground truth.** Boxes are measured in points and scaled by
   `raster_dpi / 72` (300/72) into the PNG's pixels, rounded outward (floor the left and top, ceil the
   right and bottom), so a box never cuts its ink.
8. **Overlays are images.** Handwriting, ink marks, highlights and stamps are drawn by today's overlay code
   onto a transparent canvas the size of the overlay's site, saved as PNG and placed in the Typst page at
   the site's position. Their ground truth (text and category) is what stage one records for them.
9. **Gate layouts:** `tax_invoice_standard`, `receipt_thermal_80mm`, `westpac_standard`, each on its first
   case in `ground_truth/`.
10. **Runner input.** `--input pdf|png` is required; the raw output directory becomes
    `raw_predictions/<tool>/<set>_<input>/`, and the predictions directory
    `predictions/<tool>/<set>_<input>/`. Stage one's halves keep their directory names; `--input` is
    refused for them except as `png`.

## Review Focus

1. **A value split across lines by Typst's wrapping.** Stage one fitted fields to a single line by
   shrinking; Typst wraps instead, so a long description or address may break mid-value, and invariant 3
   (every scored value appears verbatim in some block) must still find it, since a block's text joins its
   lines with a space. Test: a fixed layout whose description is forced to wrap passes invariant 3; a
   value forced into two different blocks fails four-part naming the field. Task 3.
2. **The text layer saying more or less than the page shows.** A ligature, an invisible character, a
   font's own spacing or Typst's soft hyphen could put text in the PDF that the ground truth lacks.
   Test: invariant 1 refuses a page into which a hidden word (white, 1 pt) is typeset outside every block;
   a page with ligatures switched on is compiled in the gate and its outcome recorded. Tasks 3 and 4.
3. **A receipt whose height is content-driven.** The page's height comes from Typst, not the layout, so a
   box near the bottom edge can be cut by rounding. Test: every receipt layout's first case has every
   `poly` inside the page after scaling, and the PNG's height equals the PDF's height times 300/72 rounded.
   Tasks 3 and 5.
4. **Two compiles that differ.** A timestamp, a font subset tag or dictionary order can make two compiles
   of one page differ, so a snapshot would not reproduce. Test: each gate page and one page of every
   layout compiled twice in separate processes is byte-identical. Tasks 3 and 5.
5. **A PDF in the snapshot that names how it was made.** Typst writes a Producer field and may write a
   creator or title. Test: the publication scan reads a PDF's metadata and text layer and refuses a
   planted forbidden term and a planted layout id in each. Task 6.

---

### Task 1: Pins, the typeset configuration and every layout's physical page

**Files:** `environment.yml`; `config/typeset_config.yml` (new); `generators/typst/__init__.py`,
`generators/typst/config.py` (new); `config/layouts/receipts.yml`, `invoices.yml`, `bank_statements.yml`;
`generators/layout_dsl/schema.py`; `tests/typst/test_config.py` (new);
`tests/layout_dsl/test_schema_page.py` (new).

Interfaces:
- `TypesetConfig` (frozen dataclass): `typst_version: str`, `font_dir: str`, `creation_timestamp: int`,
  `raster_dpi: int`, `template: str`.
- `load_typeset_config(path: Path) -> TypesetConfig`; `TYPESET_CONFIG_PATH: Path`.
- `PageSpec` (frozen dataclass) in `generators/typst/config.py`: `width_mm: float`,
  `height_mm: float | None` (None for a receipt roll, whose height follows its content).
- `layout_page(layout: dict, *, layout_path: str, layout_id: str) -> PageSpec`.

Required YAML (`config/typeset_config.yml`), each block with a comment saying what it controls:

```yaml
typst_version: "<the exact version pinned in environment.yml, checked against `typst --version`>"
font_dir: fonts
creation_timestamp: 1767225600    # 1 January 2026, 00.00 UTC: every PDF's creation date
raster_dpi: 300
template: generators/typst/template.typ
```

Layout key, required on every one of the 22 layouts: `page: {size: a4}` for invoices and bank statements,
`page: {width_mm: <roll width>}` for receipts. The two 420 px thermal layouts and `receipt_retail_tax` and
`receipt_fuel` take `width_mm: 80`; `receipt_thermal_57mm` takes `width_mm: 57`; the two 440 px layouts
take `width_mm: 80` too unless step 1 finds they model a wider roll, in which case the step records the
width and why in the layout's comment.

Fail-fast diagnostics (four-part, each tested): config file missing; each key missing or mistyped; a
`typst_version` that differs from the installed `typst --version`; a `font_dir` that does not exist or
holds no `.ttf`; `raster_dpi` not a positive integer; a layout without `page:`; `page:` with neither or
both of `size` and `width_mm`; a `size` other than `a4`; a `width_mm` not a positive number.

- [ ] Step 1: pick the newest Typst and `pypdfium2` releases on conda-forge that are at least two weeks old,
  pin both exactly in `environment.yml`, and run `conda env update -f environment.yml --prune`. Measure
  the four receipt widths against their stage-one pixel widths and settle `width_mm` for each.
- [ ] Step 2: write `tests/typst/test_config.py` and `tests/layout_dsl/test_schema_page.py`: the shipped
  config and every shipped layout load; each refusal above, from a copy of the shipped YAML with one key
  removed or changed in `tmp_path`, fails through `assert_diagnostic_error`. Run; expect import failure.
- [ ] Step 3: implement `generators/typst/config.py` and the `page:` check in `schema.py`; add `page:` to
  all 22 layouts. Run the two test files; expect pass. Run the stage-one layout and pixel tests; expect
  pass with no re-bless.
- [ ] Step 4: per-task gate; commit `🔧 config: pin Typst and pypdfium2 and give every layout a physical
  page`; merge to `main` and push.

### Task 2: The translator and the template, for the gate layouts' primitives

**Files:** `generators/typst/template.typ`, `generators/typst/translate.py` (new);
`tests/typst/test_translate.py` (new).

Interfaces:
- `translate_page(layout: dict, entry: dict, *, layout_id: str, page: PageSpec, distractors: bool,
  config: TypesetConfig, overlay_dir: Path) -> str`: the whole `.typ` source of one page. It walks the
  layout tree as `generators/layout_dsl/engine.py` walks it, with the entry's values filled by the same
  field providers, and emits one template call per primitive.
- `TRANSLATED: frozenset[str]`: the primitive types the translator handles. Task 2 fills it with every type
  the three gate layouts (Decision 9) use, listed by `gate_primitives() -> frozenset[str]`, which walks
  those layouts' trees; Task 5 completes it.
- Every block call is wrapped as `#block-record(id: <n>, kind: "<kind>", category: "<cat>", order: <n>,
  path: "<path>")[...]`; every table cell as `#cell-record(block: <n>, row: <r>, col: <c>)[...]`. Both are
  template functions that emit a labelled `metadata` element and their body, so Task 3's query finds them.
- An unknown primitive raises `TranslateError`, four-part, naming the layout file, the dotted path of the
  block and the types `TRANSLATED` holds.

Template rules (`template.typ`): the page is set from `PageSpec` (`#set page(width: ..., height: auto)`
for a roll); Decision 5's text settings; fonts named exactly as the layout names them, found only in
`font_dir`; spacing, rules and column splits taken from the layout's own values, converted from stage
one's pixels at the page's own scale (A4 width over 1900 px for invoices, 1800 px for bank statements,
the roll width over the layout's pixel width for receipts).

- [ ] Step 1: write `tests/typst/test_translate.py`: `gate_primitives()` equals the set the test computes by
  its own walk; each gate primitive, in a minimal invented layout, translates to source containing its
  template call and its `block-record`; a table translates with one `cell-record` per cell; an unknown
  type is refused four-part. Run; expect failure.
- [ ] Step 2: write `template.typ` and `translate.py` for the gate primitives. Run the tests; expect pass.
- [ ] Step 3: per-task gate; commit `✨ feat: translate a layout tree into Typst markup`; merge and push.

### Task 3: Compile, read back, and reshape into stage one's records

**Files:** `generators/typst/compile.py`, `generators/typst/readback.py` (new);
`tests/typst/test_compile.py`, `tests/typst/test_readback.py` (new).

Interfaces:
- `compile_page(source: str, *, workdir: Path, config: TypesetConfig) -> CompiledPage`: runs
  `typst compile --font-path <font_dir> --ignore-system-fonts --creation-timestamp <ts>` and
  `typst query <file> "<block-record>"` (and `"<cell-record>"`) on it.
- `CompiledPage` (frozen dataclass): `pdf: bytes`, `blocks: list[QueriedBox]`, `cells: list[QueriedBox]`,
  `width_pt: float`, `height_pt: float`.
- `QueriedBox(record: dict, box_pt: tuple[float, float, float, float])`.
- `rasterise(pdf: bytes, *, dpi: int) -> Image.Image` via `pypdfium2`.
- `page_geometry(compiled: CompiledPage, *, dpi: int, block_records: list[BlockRecord]) -> dict`: the
  stage-one `geometry` shape of Decision 2, built per Decisions 3, 4 and 7.
- `layout_errors(geometry: dict, *, where: str) -> list[str]`: the spec's layout checks (no block off its
  page or out of its column; no two block boxes overlap except an overlay over its page), four-part.
- `TypstError` for a compile failure or any warning (Decision 6), quoting Typst's message.

- [ ] Step 1: write `tests/typst/test_compile.py`: a minimal page compiles; the same source compiled twice
  in two processes gives byte-identical PDFs; a source naming a font absent from `font_dir` fails
  four-part quoting Typst's warning; the PNG's size is the PDF's size times 300/72.
- [ ] Step 2: write `tests/typst/test_readback.py`: on an invented two-block page with a 2 x 2 table, each
  word lands in the right block and cell; a line's text is its words in order; `build_page` of the
  geometry passes `check_page`; Review Focus 1 (a wrapped value passes invariant 3; a value split across
  two blocks fails naming the field); Review Focus 2 (a hidden 1 pt white word outside every block fails
  invariant 1); every `poly` lies inside the page after scaling; `layout_errors` refuses an overlap and a
  box off the page. Run both files; expect failure.
- [ ] Step 3: implement `compile.py` and `readback.py`. Run; expect pass.
- [ ] Step 4: per-task gate; commit `✨ feat: compile a typeset page and read its blocks and words back`;
  merge and push.

### Task 4: Feasibility gate (with the project owner)

**Files:** `scripts/typeset_gate.py` (new); `docs/parsing_benchmark_stage2_design.md` (section 7 findings);
`tests/typst/test_gate.py` (new, slow).

Interfaces:
- `run_gate(*, output_root: Path) -> GateReport`: typesets the three gate layouts' first cases, plain and
  with distractors, writes PDF, PNG and `gate.json` (one OmniDocBench page each) under
  `<output_root>/typeset_gate/`, compiles each twice, and once more with ligatures on.
- `GateReport`: per page, whether the box query, the six invariants, the layout checks and the byte check
  passed, and the ligature compile's invariant-1 outcome.

- [ ] Step 1: write `tests/typst/test_gate.py` (marked slow): all six pages pass every check; the evaluator,
  given each page's ground truth as its own prediction, scores text and table perfectly and reading order
  up to stage one's known artefact (the first matched table drops out of the predicted reading order:
  one edit on a page with a table). Run; expect failure.
- [ ] Step 2: implement `scripts/typeset_gate.py`. Run the gate and the slow test.
- [ ] Step 3: show the project owner the six PDFs beside their stage-one PNGs and the report. Record the
  findings, the ligature outcome and the owner's go or no-go in section 7 of the spec.
- [ ] Step 4: on no-go, stop: the fallback (spec section 7) gets its own plan. On go: per-task gate; commit
  `✅ test: pass the typesetting feasibility gate on one layout of each type`; merge and push.

### Task 5: Every primitive, every layout, and the distractor checks on Typst geometry

**Files:** `generators/typst/template.typ`, `generators/typst/translate.py`, `generators/typst/overlays.py`
(new); `generators/distractors/density.py`; `generators/overlay/verify.py`; `generators/pipeline.py`
(`validate`); `tests/typst/test_layouts.py` (new, slow).

Interfaces:
- `TRANSLATED` covers all 17 primitive types in use.
- `overlay_image(overlay: dict, entry: dict, *, site_px: tuple[int, int]) -> Image.Image`: today's overlay
  drawer onto a transparent canvas (Decision 8).
- `typeset_case(entry: dict, layout: dict, *, layout_id: str, distractors: bool, config: TypesetConfig,
  workdir: Path) -> TypesetResult`; `TypesetResult(pdf: bytes, image: Image.Image, geometry: dict)`.
- `density_report` and the overlay placement check accept a typeset `geometry` unchanged in shape.
- `validate` gains a typeset pass over all 165 cases, plain and with distractors, collecting every
  failure before refusing.

- [ ] Step 1: write `tests/typst/test_layouts.py` (slow): for each of the 22 layouts' first case, plain and
  with distractors, the page passes the six invariants, the layout checks and the distractor density
  bounds of `config/distractors.yml`; Review Focus 3 (every receipt layout's polys inside its page, PNG
  height as scaled); Review Focus 4 (one page per layout byte-identical across two processes). Run;
  expect failure.
- [ ] Step 2: translate the remaining primitives; implement `overlays.py` and `typeset_case`; point the
  density and overlay checks at the typeset geometry; add the typeset pass to `validate`.
- [ ] Step 3: run the slow test and `validate`; expect pass. Run the stage-one layout, pixel, distractor and
  overlay tests; expect pass with no re-bless.
- [ ] Step 4: per-task gate; commit `✨ feat: typeset every layout and check its distractors on Typst
  geometry`; merge and push.

### Task 6: The snapshot's two typeset sets, and a publication scan that reads PDFs

**Files:** `config/generation_config.yml` (`eval_set.typeset_prefixes`, `eval_set.pdf_producer`);
`generators/snapshot/config.py`, `generators/snapshot/export.py`, `generators/snapshot/publication.py`,
`generators/snapshot/manifest.py`, `generators/snapshot/readme_template.md`; `scripts/verify_snapshot.py`;
`tests/snapshot/test_typeset_export.py` (new); `tests/snapshot/test_publication.py`.

Interfaces:
- `TYPESET_SETS` (Decision 1); `SnapshotConfig.typeset_prefixes: dict[str, str]`;
  `SnapshotConfig.pdf_producer: str`.
- The export writes, per set, `<prefix>_<date>/` with `CASE001_invoice.pdf` and `.png` and so on, the
  set's `ground_truth.csv` and `.jsonl`, and `parsing/<set>.json`, the PNG's name as `image_path`.
- `find_forbidden` reads a `.pdf` through `pypdfium2`: its metadata (title, author, subject, keywords,
  creator, producer) and its text layer, reporting hits as `<path>: metadata <field> contains '<term>'`
  or `<path>: page text contains '<term>'`; the layout-id scan of stage one covers PDFs the same way.
- `eval_set.pdf_producer`: the Producer value every PDF carries, set by the export with `pypdfium2`
  after compiling. Its value is the project owner's decision (spec section 5), asked in step 1 and
  committed explicitly; an empty string is a valid explicit value meaning 'blank'.

- [ ] Step 1: ask the project owner whether each PDF's Producer field should be blank or name Typst, and
  commit the answer as `eval_set.pdf_producer`.
- [ ] Step 2: write `tests/snapshot/test_typeset_export.py`: a single-case export holds both sets with
  paired PDF and PNG; each `parsing/<set>.json` page is `build_page` of that case's typeset geometry and
  passes `check_page`; every PDF's Producer equals the configured value; `verify_snapshot.py` passes and
  fails on a changed PDF; stage one's halves are byte-identical to an export without the typeset sets.
  Extend `tests/snapshot/test_publication.py` with Review Focus 5. Run; expect failure.
- [ ] Step 3: implement. Run; expect pass. Run `tests/snapshot`; expect only stage one's two known
  worktree failures.
- [ ] Step 4: per-task gate; commit `✨ feat: ship the typeset sets as paired PDFs and images`; merge and
  push.

### Task 7: PDF input for the runner, and the text-layer baseline

**Files:** `scripts/run_local_parser.py`, `scripts/collect_predictions.py`, `scripts/text_layer_baseline.py`
(new); `config/parsing_config.yml` (`tools`: a `text_layer` entry and each tool's `inputs`);
`generators/parsing/config.py`; `tests/parsing/test_run_local_parser.py`,
`tests/parsing/test_collect_predictions.py`, `tests/parsing/test_text_layer_baseline.py` (new).

Interfaces:
- `ToolSpec.inputs: tuple[str, ...]`, required in YAML: `[png, pdf]` for the four parsers, `[pdf]` for
  `text_layer`.
- `run_parser(..., input_kind: str)`; Decision 10's directories; `run.json` records `input`.
- Each tool's argument builder takes the input kind: MinerU and Docling pass the PDF directory as they pass
  images (each reads the text layer by default); Marker likewise. Step 1 confirms each tool's default PDF
  behaviour on one gate PDF and records it in the runbook.
- `text_layer_baseline(pdf: Path) -> str`: the PDF's text lines from `pypdfium2`, one paragraph per line,
  as markdown; run through the same runner, writing `run.json` like any tool.
- `ToolSpec.runner` accepts a third value, `builtin`: a tool run in the generator's own env with no conda
  env file (`conda_env_file: null`) and device `cpu`; `local_tool` and its diagnostics name all three
  values, and a `builtin` tool with an env file is refused four-part.

- [ ] Step 1: run each parser once on one gate PDF; note in the runbook what each does with a native PDF.
- [ ] Step 2: write the tests: `--input` refused when missing, when not in the tool's `inputs`, and as
  `pdf` on a stage-one half; directories as Decision 10; the baseline's markdown for an invented
  two-line PDF; collection pairs `<stem>.md` with the PNG's stem for both inputs. Run; expect failure.
- [ ] Step 3: implement. Run; expect pass.
- [ ] Step 4: per-task gate; commit `✨ feat: run parsers on native PDFs and add a text-layer baseline`;
  merge and push.

### Task 8: Runs, scoring, the native-PDF gain, and the documents

**Files:** `scripts/score_parsing.py`; `tests/parsing/test_score_parsing.py`; `README.md`;
`docs/parsing_benchmark_runbook.md`; `docs/parsing_benchmark_stage2_design.md` (outcome).

Interfaces:
- `score_parsing.py` scores every `<set>_<input>` predictions directory against `parsing/<set>.json`.
- The leaderboard gains, per metric, a column per set and input, and a **native-PDF gain** table: per
  tool, set and metric, image score minus PDF score for an edit distance (lower is better) and PDF score
  minus image score for TEDS (higher is better), so a positive gain always means the PDF helped;
  `text_layer` appears in the PDF columns only.

- [ ] Step 1: write the tests: a fake run pair gives the right gain and sign for an edit distance and for
  TEDS; a tool without a PDF run shows no gain. Run; expect failure. Implement; expect pass.
- [ ] Step 2: export a fresh snapshot from a clean `main`; run the 16 parser runs and the 2 baseline runs
  one tool at a time; collect; score.
- [ ] Step 3: update the README (abstract, results, tools tested, the two new sets) and the runbook
  (running on PDFs, timings, the leaderboard's new sections); record the outcome in the spec.
- [ ] Step 4: full fast suite once; per-task gate; commit `📝 docs: report native-PDF scores and each
  tool's gain`; merge and push.
