# Parsing Benchmark, Stage 1: OmniDocBench Ground Truth for the Business Documents

Execute task by task, in order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every page of the evaluation snapshot (plain, distractor, labelled_varied and degraded halves,
660 pages) parsing ground truth in OmniDocBench's JSON format, recorded at draw time, and score MinerU,
Docling and Marker against it with OmniDocBench's own pinned evaluator: text edit distance, table TEDS and
reading-order edit distance, with layout ground truth ready for detection scoring. Exit criterion: the
perfect-score check passes on all four halves, and a first leaderboard exists for the four tool runs.

**Architecture:** Rendering is unchanged pixel for pixel. Task 1 adds the configuration and three optional
DSL keys. Task 2 makes the renderer say, for every line of ink it already records, which block drew it and,
inside a table, which cell. Task 3 turns one render's records into one OmniDocBench page and checks it.
Task 4 pins the evaluator, proves the format with a perfect-score check and exports a probe set. Task 5 is
the matcher gate, run with the project owner. Task 6 carries boxes through the camera warp. Task 7 adds a
`parsing/` directory to the snapshot. Task 8 runs the parsers and builds the leaderboard.

**Tech Stack:** Python 3.12, Pillow 12.2.0, PyYAML, Typer, the layout DSL, the overlay package, Augraphy and
OpenCV (degradation), pytest, conda env `synthetic`; OmniDocBench (pinned commit, its own conda env);
MinerU on the remote GPU server; Docling and Marker locally, each in its own conda env.

**Spec:** `docs/parsing_benchmark_design.md`, sections 3 (metrics), 4 (stage one ground truth) and 5
(validation, evaluation and runs). Where the spec is silent or disagrees with the code, the Decisions below
rule.

## Global Constraints

- Conda env `synthetic` for the generator; run every tool as `conda run -n synthetic <command>` from the
  repository root. The evaluator and the local parsers run in their own envs (`omnidocbench`, `docling`,
  `marker`), each built from a committed YAML file under `envs/`; none of their packages enters
  `environment.yml`.
- No render changes: every existing pixel, geometry, derived and eval-format pin passes with no re-bless.
  Ground truth, links and selections are untouched (no reseed).
- YAML is the single source of truth: every key in `config/parsing_config.yml` is required, and Python holds
  no defaults for any of them. OmniDocBench's own field names (`layout_dets`, `poly`, `category_type`,
  `page_info`) are contract constants.
- Every fail-fast error names What, Where (absolute file path and dotted key), Expected (with an example)
  and Recover, on lines starting with those labels; tests assert this with `assert_diagnostic_error`.
- Nothing generated is written inside the repository: probes, predictions and scores go under
  `eval_set.output_root` (`../evaluation_data`); test output goes to `tmp_path`.
- Model inference: MinerU runs on the remote GPU server only. Docling and Marker run locally, a deliberate,
  owner-approved exception for this benchmark. No other model runs locally.
- No real business or brand names, and no value copied from `ground_truth/`, in code, tests or prose;
  example values are invented and checked absent from `ground_truth/`.
- Line length 108; Python 3.12 types; `pathlib.Path`; Google-style docstrings; `raise ... from err` in
  `except` blocks.
- `tests/` is tracked in git: test changes are committed with their task. Per-task gate:
  `conda run -n synthetic pytest tests/ -m "not slow"` plus the slow tests the task names,
  `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format --check generators scripts linking tests *.py`,
  `mypy . --ignore-missing-imports`, `conda run -n synthetic python -m generators.pipeline validate`. The full
  suite runs once, at the end of Task 8. One review per task; review fixes are applied without a second
  review. Commit messages carry no trailers.
- Prose uses British spelling and no em-dashes; no tooling references in tracked files.

## Decisions

Numbered so reviews can cite them.

1. **The outermost open scope owns the ink.** The walker opens a block scope around each leaf content
   primitive (`text`, `pair`, `block`, `table`, `banner`, `logo`, `barcode`, `qrcode`) and around any
   container (`panel`, `split`, `stack`) that declares `parse_block: true`. A scope opened while another is
   open creates no block: its ink joins the outer one. So `parse_block` needs no further machinery, and a
   leaf never splits a block it sits inside.
2. **Overlay sites are blocks too.** Each drawn overlay site (`stamp`, `highlight`, `ink_marks`,
   `handwriting`) opens one scope of kind `overlay:<type>` around the ink the overlay engine records for it.
   Overlays draw after the body, so they come last in draw order.
3. **A block's text is its non-mark ink lines joined by one space, in draw order.** Wrapped lines are one
   paragraph to a parser. A code mark's payload (barcode digits, QR content, logo initials) is not page text
   and never enters `text`.
4. **Blocks with no ink are omitted,** whatever drew them: a suppressed `text`, a `highlight` that only
   tints. Blocks whose category is listed in `textless_categories` (`figure`, `abandon`) carry no `text`
   field.
5. **Category precedence, highest first:** the block's `parse_category:`; `category_map.anchors[<anchor>]`
   when the block has an anchor listed there; `category_map.overlays[<type>]` for an overlay block;
   `category_map.primitives[<kind>]`.
6. **Reading order is a stable sort by `(parse_order, draw sequence)`.** `parse_order` is an integer rank,
   0 when absent; a block given `parse_order: 1` reads after every unranked block, `-1` before. `order` in the
   output is the 1-based position after the sort, as OmniDocBench numbers its own pages (its matcher reads an
   order of 0 as no order, so a 0-based first block would drop out of reading order); `anno_id` is the draw
   sequence.
7. **Tables are written as `<table>` of `<tr>` of `<td>`, with no `<thead>` or `<th>`.** Header rows are the
   first rows. A header label drawn on two lines (`"Date of\nTransaction"`) is one cell, its lines joined by
   a space. A date sub-header row (`grouping: dedicated_row`) is one `<td colspan="N">` for an N-column
   table. A synthetic row is a row. A sub-line sits in the cell of the column that declares it, after the
   main text, joined by a space. A cell the table blanks (the redundant date under `inline` grouping, an
   empty value) is an empty `<td></td>`, so every row has N columns. Whether `<th>` would score better under
   TEDS is a question for the Task 5 gate, not a reason to guess now.
8. **Invariant 2 is checked in the form that can fail.** Block text is built from ink, so equality is true by
   construction; what can go wrong is ink in a table block with no cell. The check: every ink record whose
   block is a table carries a cell, and every cell index lies inside the table's declared column count.
9. **Page attributes.** `page_info.page_attribute` carries `language: en` plus this corpus's own keys:
   `doc_type`, `layout_id`, `half`, `tier` (`none` for clean halves) and `layout_scored` (Decision 11).
   OmniDocBench's `data_source` and `layout` vocabularies describe other genres and are not used.
   In the snapshot, `layout_id` carries an opaque key, `<doc_type>_layout_<n>` (n from 1 over the type's
   sorted layout ids), since the published snapshot names no template and some layout ids name a real
   bank; the probe keeps the real ids.
10. **The evaluator is pinned, not vendored, and runs from its own env.** `config/parsing_config.yml` names
    the repository URL and commit; `scripts/omnidocbench_eval.py` clones it once into
    `<output_root>/tools/OmniDocBench` at that commit and refuses a checkout at any other commit.
    Formula metrics are off, so no TeX, Ghostscript or ImageMagick is needed. If `pip install -e .` fails on
    macOS, the fallback is OmniDocBench's published Docker image at the same commit, recorded in the
    runbook; the config gains no Docker key unless that fallback is actually taken.
11. **Heavy-tier exclusion is an explicit YAML list, proven by a test.** `layout_scored_tiers` lists the
    degradation tiers whose pages count for layout detection. A test measures the worst pixel displacement
    each listed tier's Augraphy phase causes (Task 6) and fails if any exceeds `fold_displacement_max_px`. So
    listing `heavy` while its fold moves ink too far breaks the build, and leaving it out is visible in YAML.
    Text, table and reading-order scores never depend on this list.
12. **Probe pages are chosen deterministically:** for each document type, the first case (by case id) of
    each of its first two layouts (by layout id), plus the light, moderate and heavy degraded variants of the
    first receipt among them. That gives six clean pages and three degraded ones: nine probes.
13. **Predictions are named `<image stem>.md`** in `<output_root>/predictions/<tool>/<half>/`. Task 4's
    perfect-score test proves the pinned evaluator pairs `.png` ground truth with them; if it does not, the
    pairing is fixed in the scoring script, never by renaming images.
14. **This repository is a clone of the extraction generator, and takes its fixes by cherry-pick.** It
    shares that repository's history; the remote `upstream` fetches from it and cannot push to it. The
    extraction repository is never edited from here. Upstream work that lands later (content, layout and
    distractor fixes) is brought across with `git fetch upstream && git cherry-pick <sha>`. In particular,
    the publishable-snapshot work (a rewrite of `generators/snapshot/`: manifest, README template,
    publication) was unmerged upstream when this repository was seeded, so Task 7 starts only after those
    commits are cherry-picked in with the suite green. Tasks 1 to 6 touch no file under
    `generators/snapshot/`; Task 6 adds a homography-returning variant beside `degrade_receipt` and leaves
    its signature alone so the snapshot export is not edited before Task 7. Extraction-only code (the CORD,
    DocILE and native exporters, linking, the scoring notebook) is kept, so upstream cherry-picks touching it
    apply cleanly; pruning it is a later, separate decision.
15. **The writer lives in a new `generators/parsing/` package,** not `generators/exporters/` as the spec's
    key-files table has it. The exporters re-project ground truth without rendering; the parsing writer
    consumes renders, as do its config, probe and degraded mapping, so they belong together.
16. **`config/parsing_config.yml` carries two keys the spec's list omits:** `textless_categories`
    (Decision 4) and `layout_scored_tiers` (Decision 11). Both are required like the rest.
17. **A table may join the table before it.** `parse_join: previous_table`, accepted on `table` blocks only,
    writes the table's rows as more rows of the nearest table written before it in draw order: no
    `layout_dets` entry of its own, its ink widening that table's `poly`, each of its cells in the column
    whose horizontal span best overlaps the cell's ink, so every row keeps that table's N columns. A join
    with no table before it is refused four-part, by `validate` and again by the writer. The statement
    totals table carries it (section 5.3 of the design, recommendation 7).
18. **The matcher gate passed.** On 13 October 2026 the owner said go and accepted all nine
    recommendations of the design's section 5.3; its decision table records how each is realised and
    where. Only recommendations 2, 3 and 7 change the layouts or the writer.

## Review Focus

1. **Ink outside any block.** A path that draws text without passing through a block scope (a label site, a
   barcode caption, an overlay drawn outside the scope, an adapter drawing directly) would leave orphan ink
   that the page's text silently loses. Tests: for every one of the 22 layouts, the plain and distractor
   renders of its first case have zero orphan ink records; one forced orphan fails `check_page` four-part.
   Tasks 2 and 3.
2. **A render that changes by being recorded.** Scopes and cell markers must not move a pixel or a box.
   Tests: every existing pixel, geometry and eval-format pin passes unchanged; `BoxRecorder.as_dict()` and
   `ink_boxes()` are identical with and without scopes for one layout per type. Task 2.
3. **Tables that are not rectangular.** Multi-line headers, dedicated date rows, inline-blanked dates,
   synthetic leading and trailing rows, sub-lines and empty cells. Tests: for each of the 8 bank layouts and
   one receipt and one invoice layout, every `<tr>` spans exactly N columns (counting `colspan`), header row
   count equals the number of header lines drawn, and the table's cell texts, flattened, equal its non-mark
   ink texts in draw order. Tasks 2 and 3.
4. **A scored value that cannot be found in the page text.** A wrapped description, a value drawn as a
   table cell, a value joined to its label on one line. Tests: invariant 3 passes on every layout's first
   case; a value forced to differ from what was drawn fails four-part naming the field. Task 3.
5. **A degraded box that misses its ink.** The homography applied to the wrong canvas (receipt crop,
   desk padding), or corners in the wrong order. Tests: a white page with one black square at a known
   position, warped by each tier, has the square's centroid inside its mapped `poly`; the `poly` corners are
   clockwise from top-left; a real degraded receipt's mapped polys all lie within the frame. Task 6.

---

### Task 1: Parsing configuration and the three DSL keys

**Files:** `config/parsing_config.yml` (new); `generators/parsing/__init__.py`, `generators/parsing/config.py`
(new); `generators/layout_dsl/schema.py`; `generators/pipeline.py` (`validate`); `tests/parsing/test_config.py`
(new); `tests/layout_dsl/test_schema_parse_keys.py` (new).

Interfaces:
- `ParsingConfig` (frozen dataclass): `evaluator: EvaluatorPin`, `category_vocabulary: tuple[str, ...]`,
  `textless_categories: tuple[str, ...]`, `primitives: dict[str, str]`, `overlays: dict[str, str]`,
  `anchors: dict[str, str]`, `fold_displacement_max_px: int`, `layout_scored_tiers: tuple[str, ...]`,
  `tools: tuple[ToolSpec, ...]`.
- `EvaluatorPin(repo: str, commit: str, conda_env_file: str, formula_metrics: bool)`.
- `ToolSpec(name: str, backend: str, runner: str, conda_env_file: str | None)`; `runner` is `local` or
  `remote`; a local tool must name its env file, a remote tool must give `null`.
- `load_parsing_config(path: Path) -> ParsingConfig`.
- `BLOCK_PRIMITIVES: frozenset[str]` in `generators/parsing/config.py`: the 8 leaves and 3 containers of
  Decision 1. `category_map.primitives` must have exactly these keys; `category_map.overlays` exactly the
  keys of `OVERLAY_DRAWERS`.
- `layout_parse_errors(layouts: dict, *, layout_path: str, config: ParsingConfig) -> list[str]`: every
  `parse_category` used in a layout body is in `category_vocabulary`.

Required YAML (`config/parsing_config.yml`), with these starting values:

```yaml
evaluator:
  repo: https://github.com/opendatalab/OmniDocBench.git
  commit: <full 40-character SHA of the v1.7 release, looked up and pasted in step 1>
  conda_env_file: envs/omnidocbench.yml
  formula_metrics: false
category_vocabulary: [title, text_block, figure, figure_caption, figure_footnote, table, table_caption,
  table_footnote, equation_isolated, equation_caption, header, footer, page_number, page_footnote,
  abandon, code_txt, code_txt_caption, reference]
textless_categories: [figure, abandon]
category_map:
  primitives: {text: text_block, pair: text_block, block: text_block, table: table, banner: title,
               logo: figure, barcode: figure, qrcode: figure, panel: text_block, split: text_block,
               stack: text_block}
  overlays: {stamp: text_block, handwriting: text_block, ink_marks: abandon, highlight: abandon}
  anchors: {foot: footer}
fold_displacement_max_px: 3
layout_scored_tiers: [light, moderate, heavy]
tools:
  - {name: mineru_pipeline, backend: pipeline, runner: remote, conda_env_file: null}
  - {name: mineru_vlm, backend: vlm-transformers, runner: remote, conda_env_file: null}
  - {name: docling, backend: default, runner: local, conda_env_file: envs/docling.yml}
  - {name: marker, backend: default, runner: local, conda_env_file: envs/marker.yml}
```

The comment above each block says what it controls; the comment above `layout_scored_tiers` says Task 6's
test decides whether `heavy` may stay.

DSL keys (`schema.py`): every block accepts `parse_category` (non-empty string) and `parse_order`
(integer, not bool); only `panel`, `split` and `stack` accept `parse_block` (bool). They join `type` and
`when` in the always-allowed set; `parse_block` on any other primitive is refused four-part naming the
three containers. The schema does not know the vocabulary; `validate` checks it via
`layout_parse_errors`.

Fail-fast diagnostics (four-part, each tested): file missing; each key missing or mistyped (Where
`<abs>/config/parsing_config.yml -> <dotted key>`); a vocabulary value duplicated; a `category_map` value
outside the vocabulary; `primitives` or `overlays` with a missing or extra key (What lists both sets);
`layout_scored_tiers` naming a tier absent from `config/generation_config.yml`; a tool name duplicated; a
bad runner; a local tool without an env file or a remote tool with one; a commit that is not 40 hex
characters; `formula_metrics: true` (refused in stage one: Expected `formula_metrics: false`, Recover
'formulas arrive with stage three'); the three DSL key errors; a `parse_category` outside the vocabulary.

- [ ] Step 1: look up the full commit SHA of OmniDocBench's v1.7 release tag
  (`git ls-remote https://github.com/opendatalab/OmniDocBench.git 'refs/tags/*'`); if no v1.7 tag exists,
  use the commit of the 30 April 2026 release note and say so in the YAML comment.
- [ ] Step 2: write `tests/parsing/test_config.py`: the shipped config loads; each refusal above, built from
  a copy of the shipped YAML with one key removed or changed in `tmp_path`, fails through
  `assert_diagnostic_error`. Run; expect import failure.
- [ ] Step 3: implement `generators/parsing/config.py` and the YAML. Run; expect pass.
- [ ] Step 4: write `tests/layout_dsl/test_schema_parse_keys.py`: the three keys accepted where allowed and
  refused elsewhere; wrong types refused. Run; expect failure. Implement in `schema.py`. Run; expect pass.
- [ ] Step 5: call `load_parsing_config` and `layout_parse_errors` from `validate`, appending to
  `all_errors`. A test runs `validate` with a scratch layout using an unknown `parse_category` and
  asserts the diagnostic.
- [ ] Step 6: per-task gate; commit `🔧 config: add parsing ground-truth configuration and parse_* layout keys`.

---

### Task 2: Block scopes and table cells, recorded at draw time

**Files:** `generators/exporters/geometry.py`; `generators/layout_dsl/context.py`; `generators/layout_dsl/engine.py`;
`generators/layout_dsl/primitives_table.py`; `generators/overlay/engine.py`; `generators/invoice.py`,
`generators/receipt.py`, `generators/bank_statement.py` (publish `geometry_out["blocks"]`);
`tests/exporters/test_block_records.py`, `tests/layout_dsl/test_block_scopes.py`,
`tests/layout_dsl/test_table_cells.py` (new).

Interfaces:
- `Cell(row: int, column: int, span: int)` (NamedTuple) in `geometry.py`; `span` is 1, or the table's
  column count for a date sub-header.
- `InkRecord` gains two trailing fields with defaults: `block: int | None = None`, `cell: Cell | None = None`.
  Existing positional construction keeps working.
- `BlockRecord(id: int, kind: str, path: str, anchor: str | None, parse_category: str | None,
  parse_order: int, columns: int | None)` (NamedTuple). `kind` is the primitive name or `overlay:<type>`;
  `columns` is set for tables only.
- `BoxRecorder.block_scope(kind: str, *, path: str, anchor: str | None, parse_category: str | None,
  parse_order: int) -> ContextManager[None]`: opens a block if none is open (Decision 1), else does
  nothing; closes on exit, exceptions included.
- `BoxRecorder.set_table_columns(columns: int) -> None`: sets `columns` on the open block; refuses (plain
  `ValueError` with a Remediation line, as the recorder's other guards do) if no block is open or the open
  block is not a table.
- `BoxRecorder.set_cell(cell: Cell | None) -> None`: every ink record until the next call carries `cell`;
  `None` clears it. Closing a block clears it.
- `BoxRecorder.block_records() -> list[BlockRecord]`, in draw order.
- `record_ink` stamps `block` (the open block's id, or None) and `cell` on each record.
- `RenderContext.block_path: str = "body"`: `render_blocks` computes `here = f"{ctx.block_path}[{position}]"`
  for each block and hands nested containers `block_path=f"{here}.children"`, so a nested leaf records
  `body[3].children[1]`.
- Renderers add `geometry_out["blocks"] = recorder.block_records()` beside the existing `ink` line.

Behaviour:
- `render_blocks` wraps the `drawer(...)` call in `ctx.ink_recorder.block_scope(...)` when an ink recorder
  exists and the block is a leaf of Decision 1 or a container with `parse_block: true`; `anchor`,
  `parse_category` and `parse_order` (0 when absent) come from the block dict.
- `draw_table` calls `set_table_columns(len(columns))` first. `_draw_header` sets
  `Cell(row=r, column=i, span=1)` before each label line, where `r` is the header row index and multi-line
  labels keep one `r` (Decision 7). The dedicated date row sets `Cell(row, 0, len(columns))`. `_draw_row`
  sets `Cell(row, i, 1)` before each `_draw_cell`; `_draw_sub_lines` sets the same row and the declaring
  column's index. The row counter is local to `draw_table`, counts every drawn row (header, date, synthetic,
  data) and starts at 0. `set_cell(None)` after the last row.
- Cells go through `ctx.ink_recorder`, which `capture: false` leaves in place (it clears only
  `ctx.recorder`), so a table that records no scored boxes still records its grid (spec section 4.3).
- `apply_overlay` (in `generators/overlay/engine.py`) wraps the two loops that record a site's text and
  mark ink in `recorder.block_scope(f"overlay:{kind}", path=site.block_path, anchor=None,
  parse_category=None, parse_order=0)`.
- Nothing draws differently. No pixel, box or ink box moves.

Tests:
- Recorder: a scope opened inside a scope creates one block; ink inside both carries the outer id; ink
  outside any scope carries None; `set_cell` stamps and clears; `set_table_columns` outside a table scope
  refuses; a scope closes when its body raises.
- Scopes (one test per primitive kind, on a tiny scratch layout): each leaf yields one block of its kind
  with its path; a `stack` with `parse_block: true` yields one block for all its children; without it, one
  per child; a `foot`-anchored block records `anchor: foot`; overlay ink lands in an `overlay:<type>` block.
- Table cells: Review Focus 3, on the real layouts listed there (first case each).
- Review Focus 1 (orphans): parametrised over all 22 layouts, plain and distractor renders of the first case:
  no ink record has `block is None`.
- Review Focus 2: the full existing pixel and geometry pins, unchanged.

- [ ] Step 1: recorder tests; run, expect failure; implement in `geometry.py`; run, expect pass.
- [ ] Step 2: scope tests; run, expect failure; implement `block_path` and the scope in `context.py` and
  `engine.py`, the overlay scope, and the three renderers' `blocks` line; run, expect pass.
- [ ] Step 3: table cell tests; run, expect failure; implement in `primitives_table.py`; run, expect pass.
- [ ] Step 4: orphan test over 22 layouts; fix any drawing path it exposes by putting it inside a scope (not
  by exempting it); run, expect pass.
- [ ] Step 5: per-task gate, including every pixel pin; commit
  `✨ feat: record which block and table cell drew each line of ink`.

---

### Task 3: OmniDocBench page writer and its invariants

**Files:** `generators/parsing/omnidocbench.py` (new); `generators/pipeline.py` (`validate`);
`tests/parsing/test_omnidocbench.py` (new).

Interfaces:
- `PageContext(image_path: str, doc_type: str, layout_id: str, half: str, tier: str, layout_scored: bool)`.
- `build_page(geometry: dict, *, context: PageContext, config: ParsingConfig) -> dict`: one OmniDocBench
  page from a render's `geometry_out` (`width`, `height`, `ink`, `blocks`, `texts`). Output:
  `{"layout_dets": [...], "page_info": {"page_no": 0, "height": H, "width": W, "image_path": ...,
  "page_attribute": {...}}, "extra": {"relation": []}}`. Each `layout_dets` entry has `category_type`,
  `poly` (8 numbers: top-left, top-right, bottom-right, bottom-left of the union of the block's ink boxes,
  pixel coordinates), `ignore: false`, `order`, `anno_id`, `attribute: {}`, `line_with_spans: []`, and
  `text` (Decision 3 and 4) or, for a table, `html` (Decision 7).
- `map_polys(page: dict, transform: Callable[[list[float]], list[float]], *, width: int, height: int) -> dict`:
  a copy of `page` with every `poly` passed through `transform` and `page_info` given the new size. Task 6
  supplies the homography.
- `check_page(page: dict, geometry: dict, *, where: str, config: ParsingConfig) -> list[str]`: the five
  invariants of spec section 5.1, invariant 2 in Decision 8's form. Invariant 1 checks the ink, so it reads
  `geometry`. Invariant 3 compares each `geometry["texts"]` value with whitespace collapsed to single spaces
  against every block `text` and every table cell text with the same collapse; a value found nowhere is an
  error naming the field and `where`. Each error is four-part; `where` is `CASE_ID / layout_id (mode)`.
- `table_html(block: BlockRecord, ink: list[InkRecord]) -> str`.

`validate` builds and checks a page for every plain and distractor render it already makes, appending
errors to `all_errors`. It renders nothing extra.

Tests:
- `build_page` on a hand-built geometry (invented text, two blocks, one table with a two-line header, a
  date row, a blank cell and a sub-line): exact expected dict, including `order` after a `parse_order: -1`
  block, a figure block without `text`, an empty block omitted.
- `table_html` on the same: the exact expected string.
- Each invariant fails four-part on a geometry built to break it: an orphan ink record; table ink without a
  cell; a cell column beyond the declared count; a scored value absent from every block; a `poly` outside the
  page; a `parse_category` outside the vocabulary.
- Review Focus 4: invariant 3 passes on the first case of every layout, plain and distractor.
- `map_polys` with an identity transform returns an equal page; with a shift, every corner moves.

- [ ] Step 1: tests; run, expect failure.
- [ ] Step 2: implement `omnidocbench.py`; run, expect pass.
- [ ] Step 3: wire into `validate`; run `python -m generators.pipeline validate` on the real corpus; fix
  any genuine failure at its cause (a layout, or a drawing path), never by loosening a check.
- [ ] Step 4: per-task gate; commit `✨ feat: write and check OmniDocBench pages from draw-time records`.

---

### Task 4: Pinned evaluator, perfect-score check and probe export

**Files:** `envs/omnidocbench.yml` (new); `scripts/omnidocbench_eval.py` (new); `generators/parsing/probe.py`
(new); `generators/pipeline.py` (`parsing-probe` command); `tests/parsing/gt_to_markdown.py` (new, test-only);
`tests/parsing/test_probe.py`, `tests/parsing/test_perfect_score.py` (new, the latter marked `slow`).

Interfaces:
- `scripts/omnidocbench_eval.py` (CLI and importable):
  - `ensure_checkout(config: ParsingConfig, *, root: Path) -> Path`: clones into `root / "OmniDocBench"` at
    the pinned commit, or verifies an existing checkout is at it; refuses four-part otherwise.
  - `run_end2end(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict`: writes an
    end-to-end config into `work_dir` (metrics: `text_block: [Edit_dist]`, `table: [TEDS, Edit_dist]`,
    `reading_order: [Edit_dist]`, no `display_formula`; `match_method: quick_match`), runs
    `conda run -n omnidocbench python pdf_validation.py --config <file>` in `checkout`, and returns the
    parsed metrics. Step 3 below establishes where the evaluator writes its result and which keys it holds;
    the parser reads exactly those, and a captured result file under `tests/fixtures/` pins it.
- `gt_to_markdown(page: dict) -> str` (test-only): blocks in `order`; `title` as `# text`; `text_block`,
  `header`, `footer` as paragraphs; `table` as its `html`; figures and `abandon` omitted.
- `export_probe(config_path: Path, parsing_config: ParsingConfig) -> Path`: renders the nine probe pages of
  Decision 12 (degraded ones once Task 6 exists; before that, the six clean ones), writes their images and
  one `probe.json` (a list of pages) to `<output_root>/parsing_probe/`, replacing an earlier probe.
- CLI: `python -m generators.pipeline parsing-probe [--config ...]` prints the directory and page count.

Steps:
- [ ] Step 1: write `envs/omnidocbench.yml` (Python 3.10, as OmniDocBench documents; its requirements
  installed by `pip install -e <checkout>` in the runbook, not listed twice). Create the env.
- [ ] Step 2: `ensure_checkout` with tests (a fake remote made with `git init` under `tmp_path`; a checkout at
  the wrong commit refused four-part).
- [ ] Step 3: run the evaluator once by hand on its own bundled demo data at the pinned commit; record in the
  runbook draft where results land and what they contain; copy one result file into
  `tests/fixtures/omnidocbench_result.json` and write the parser and its test against it.
- [ ] Step 4: `export_probe` and the CLI, with a test on a scratch config that the six clean probe pages are
  the ones Decision 12 names and that `probe.json` passes `check_page`.
- [ ] Step 5: the slow perfect-score test: export the probe into `tmp_path`, write `gt_to_markdown` of each
  page as `<image stem>.md`, run `run_end2end`, and assert text edit distance 0, table TEDS 1 and
  reading-order edit distance 0. If the evaluator does not pair `.png` ground truth with `<stem>.md`
  predictions, fix the pairing in `run_end2end` (Decision 13). If a score is not perfect, find out why
  before going further: it is either a writer bug (fix it) or an evaluator normalisation this plan did not
  foresee (record it in the spec's section 5.3 and bring it to the Task 5 gate).
- [ ] Step 6: per-task gate plus the perfect-score test; commit
  `✨ feat: pin the OmniDocBench evaluator and export a probe set it scores perfectly`.

---

### Task 5: Matcher gate (with the project owner)

**Files:** `docs/parsing_benchmark_design.md` (section 5.3 findings); `config/layouts/*.yml` (only
`parse_block`, `parse_category` or `parse_order` keys the findings call for); tests for any such key.

This task decides whether the rest goes ahead. It needs one remote MinerU run, so it is done with the
project owner.

- [ ] Step 1: `python -m generators.pipeline parsing-probe`. Copy `<output_root>/parsing_probe/` to the
  remote GPU server; run MinerU's pipeline back-end on the images (command as recorded in the runbook
  draft); copy its markdown back to `<output_root>/predictions/mineru_pipeline/probe/`, one `<stem>.md`
  per page.
- [ ] Step 2: run Docling locally on the same images into `<output_root>/predictions/docling/probe/`.
- [ ] Step 3: score both with `run_end2end`; open the evaluator's per-page match output and, for each page,
  read how ground-truth blocks were matched to predicted text.
- [ ] Step 4: write the findings into spec section 5.3: which block grouping the matcher handles well,
  which it splits or merges wrongly, and whether `<td>`-only tables score as expected under TEDS
  (Decision 7).
- [ ] Step 5: the owner decides: go (with any layout keys the findings call for, e.g. `parse_block: true` on
  a key-value panel), or stop and revisit the design. Record the decision and its date in section 5.3.
- [ ] Step 6: on go, add the layout keys, a test per key asserting the block it produces, re-run the probe
  and the perfect-score test; per-task gate; commit `📝 docs: record the matcher check and the grouping it set`.

---

### Task 6: Degraded receipts keep their boxes

**Files:** `generators/degradation/camera.py`; `generators/degradation/__init__.py`;
`generators/parsing/degraded.py` (new); `generators/parsing/probe.py` (degraded probes);
`tests/degradation/test_homography.py`, `tests/parsing/test_degraded.py` (new).

Interfaces:
- `warp_to_photo_mapped(image, warp, rng) -> tuple[Image.Image, np.ndarray]`: the existing body, returning
  the frame and the 3x3 homography `m`. `warp_to_photo` becomes a one-line call to it returning the frame,
  so every caller and every existing pin is unchanged.
- `degrade_receipt_mapped(image, tier, seed) -> tuple[Image.Image, np.ndarray]`, beside `degrade_receipt`,
  which becomes a call to it returning the image. Same draws from the same seed, so the images are
  identical.
- `homography_transform(m: np.ndarray) -> Callable[[list[float]], list[float]]`: maps an 8-number poly
  through `cv2.perspectiveTransform`, keeping corner order.
- `fold_displacement_px(tier: Tier, seeds: range, *, size: tuple[int, int]) -> float`: renders a white page
  of `size` with a grid of 5-pixel black dots every 40 pixels, applies only the tier's Augraphy ink and
  paper phases (`apply_augraphy`) for each seed, locates each dot's centroid within a 20-pixel window, and
  returns the worst distance from its drawn position.

Behaviour: a degraded page is `map_polys(build_page(distractor_geometry, ...), homography_transform(m),
width=frame.width, height=frame.height)`, with `half: degraded`, `tier: <name>` and `layout_scored: tier in
config.layout_scored_tiers`.

Tests:
- `warp_to_photo` and `degrade_receipt` outputs are byte-identical to before for the light and moderate
  tiers (the heavy tier is not bit-reproducible, as the snapshot README already records, so it is compared
  by size only).
- Review Focus 5: the single-square page, warped by each tier's warp parameters with a fixed seed, has the
  square's centroid inside its mapped poly; corners stay clockwise from top-left; every mapped poly of a
  real degraded receipt lies within the frame.
- Decision 11: `fold_displacement_px` for every tier in `layout_scored_tiers`, over seeds `range(5)`, is at
  most `fold_displacement_max_px`. Marked `slow`.

- [ ] Step 1: tests for the mapped variants and identity of the existing outputs; run, expect failure;
  implement; run, expect pass.
- [ ] Step 2: Review Focus 5 tests; implement `homography_transform` and `degraded.py`; run, expect pass.
- [ ] Step 3: the fold test. Run it. If `heavy` fails, remove it from `layout_scored_tiers`, write the
  measured value into the YAML comment above the list, and tell the project owner; do not raise
  `fold_displacement_max_px` to make it pass.
- [ ] Step 4: add the three degraded probes to `export_probe`; extend the perfect-score test to them.
- [ ] Step 5: per-task gate plus the fold and perfect-score tests; commit
  `✨ feat: carry parsing boxes through the camera warp`.

---

### Task 7: The snapshot's `parsing/` directory

Precondition (Decision 14): the upstream publishable-snapshot commits cherry-picked into this repository,
with the suite green.

**Files:** `config/generation_config.yml` (`eval_set.parsing_dir: parsing`);
`generators/snapshot/config.py`; `generators/snapshot/export.py`; the manifest and README modules as they
stand after the merge; `scripts/verify_snapshot.py` if its allow-list needs the new directory;
`tests/snapshot/` (the export, manifest, README and verify tests).

Behaviour:
- The export builds a page with `build_page` for every plain, distractor and labelled_varied render it
  already makes, and a degraded page per tier from `degrade_receipt_mapped`, which replaces its
  `degrade_receipt` call. It runs `check_page` on each and adds failures to the collected failures, so a
  bad page refuses the export like any other failed check.
- `<snapshot>/<parsing_dir>/<half prefix>.json`: one list of pages per half, sorted by `image_path`, which
  is the image's path relative to the snapshot root (so the file is valid wherever the snapshot is
  unpacked).
- The manifest hashes the four files like every other file; the README gains a section, generated from the
  config and the manifest like the rest, saying what the files are, which OmniDocBench commit they target,
  which metrics apply, and which tiers count for layout detection.

Tests: the small session snapshot carries four parsing files with one page per image of its half; every
page passes `check_page`; the files appear in `MANIFEST.json` and `SHA256SUMS`; `verify_snapshot.py` fails
on a flipped byte in one of them; the README section names the evaluator commit and the scored tiers;
`parsing_dir` missing from the config fails four-part. The existing slow full-corpus export test also
asserts 165 pages per parsing file.

- [ ] Step 1: tests; run, expect failure.
- [ ] Step 2: implement; run, expect pass.
- [ ] Step 3: per-task gate plus the full-corpus export test; commit
  `✨ feat: ship OmniDocBench parsing ground truth with the snapshot`.

---

### Task 8: Parser runs, scoring and the first leaderboard

**Files:** `envs/docling.yml`, `envs/marker.yml` (new); `scripts/run_local_parser.py` (new);
`scripts/collect_predictions.py` (new); `scripts/score_parsing.py` (new); `docs/parsing_benchmark_runbook.md`
(new); `README.md` (a short section pointing to the runbook); `tests/parsing/test_collect_predictions.py`,
`tests/parsing/test_score_parsing.py` (new).

Interfaces:
- `run_local_parser.py --tool <name> --snapshot <dir> --half <half>`: refuses a tool whose runner is not
  `local`; runs the tool's CLI through `conda run -n <env>` on the half's images into a raw output
  directory under `<output_root>/raw_predictions/<tool>/<half>/`.
- `collect_predictions.py --tool <name> --half <half>`: normalises a tool's raw output (each tool nests its
  markdown differently) into `<output_root>/predictions/<tool>/<half>/<image stem>.md`; refuses four-part
  when an image has no markdown or two candidates.
- `score_parsing.py --snapshot <dir>`: for each tool with predictions and each half, `run_end2end`, then
  writes `<output_root>/leaderboard_<YYYYMMDD>.md` and `.csv`: rows per tool, columns per half for text
  edit distance, table TEDS and reading-order edit distance, then breakdowns per document type and per
  degradation tier, plus the device each run used (read from a `run.json` the runner writes). Overall is
  shown as 'n/a (no formulas in stage one)'. Tables lead with TEDS, and table edit distance is reported
  beside it with the caveat that it compares html strings, so it is comparable only between tools with
  the same output form (design section 5.3, recommendation 8).

The runbook covers: building each env; the remote MinerU run (environment on the remote server, how model
weights reach it if it has no public network access, the exact commands for both back-ends, copying images
there and markdown back); local Docling and Marker runs; collecting; scoring; and a check that Marker's
model-weight licence permits publishing the scores, with the finding recorded in the runbook.

Tests: `collect_predictions` on fixture directory trees shaped like each tool's output (built in `tmp_path`
from invented markdown) produces one `<stem>.md` per image and refuses the missing and duplicate cases;
`score_parsing` with `run_end2end` replaced by a stub returning fixed metrics writes the exact expected
leaderboard rows; `run_local_parser` refuses a remote tool.

- [ ] Step 1: tests; run, expect failure; implement the three scripts; run, expect pass.
- [ ] Step 2: build the Docling and Marker envs; run both on all four halves; run MinerU (both back-ends)
  remotely per the runbook; collect; score.
- [ ] Step 3: read the leaderboard against the images for three pages per tool, and describe in the
  runbook any score that looks wrong and why.
- [ ] Step 4: full suite; per-task gate; commit
  `✨ feat: score MinerU, Docling and Marker against the parsing ground truth`.

---

## Stage 1 outcome

- *Matcher findings.* Line-level blocks match fairly against both paragraph and line-style parsers, and
  the gate passed with the grouping changes listed in the design's section 5.3 (Decision 18).

The rest is filled in when Task 8 is done: the tiers scored for layout, the evaluator commit, the first
leaderboard's location, and what stage two should know.
