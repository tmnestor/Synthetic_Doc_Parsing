# Parsing benchmark runbook

How to score document parsers against the parsing ground truth. Every command runs from the repository
root. Nothing it writes lands inside the repository: the evaluator checkout, probes, predictions and
scores all go under `eval_set.output_root` (`../evaluation_data`).

## The evaluator

OmniDocBench is pinned, not vendored. `config/parsing_config.yml` names its repository and the commit
(`evaluator.repo`, `evaluator.commit`); formula metrics are off, so no TeX, Ghostscript or ImageMagick is
needed. It runs from its own conda env, so none of its packages enter `environment.yml`.

### Building it (once per machine)

1. Clone the checkout at the pinned commit. This clones into `../evaluation_data/tools/OmniDocBench`, or
   checks that an existing checkout there is at the pinned commit and refuses it otherwise:

   ```bash
   conda run -n synthetic python scripts/omnidocbench_eval.py checkout
   ```

2. Build the env from its committed file, then install the checkout into it. The checkout's
   `pyproject.toml` carries the evaluator's own requirements, so they are not repeated in the env file:

   ```bash
   conda env create -n omnidocbench -f envs/omnidocbench.yml
   conda run -n omnidocbench pip install -e ../evaluation_data/tools/OmniDocBench
   ```

   Every requirement installs from a wheel on macOS (Apple silicon) and Linux; no Docker fallback is needed.

### Running it

```bash
conda run -n synthetic python scripts/omnidocbench_eval.py end2end \
    --gt <pages>.json --pred <predictions dir> --work-dir <scores dir>
```

`--gt` is an OmniDocBench ground-truth file (a JSON list of pages), `--pred` a directory holding one
`<image stem>.md` per page: the evaluator pairs the page `CASE001_receipt.png` with `CASE001_receipt.md`.
The script writes `end2end.yaml` into the work directory (text edit distance; table TEDS and edit
distance; reading-order edit distance; quick matching) and runs the checkout's `pdf_validation.py` there,
in the `omnidocbench` env. Hugging Face access is switched off for the run: the metrics used are pure
computation and load no model. It prints the headline scores as JSON.

### Where results land

The evaluator writes into `<work dir>/result/`, every file named after the predictions directory, then
`_quick_match_`. The headline file is `<predictions dir name>_quick_match_metric_result.json`; per element
(`text_block`, `table`, `reading_order`) it holds:

- `all`: the headline numbers. `Edit_dist` gives `ALL_page_avg` (the mean of the per-page normalised
  edit distances, the number the script reports), `edit_whole` (one ratio over every page together) and
  `edit_sample_avg` (the mean over matched blocks). `table` also gives `TEDS.all` and
  `TEDS_structure_only.all`. An element nothing was scored in reads `"NaN"`.
- `group`: the same metrics split by block attribute; `page`: split by each `page_info.page_attribute`
  value (document type, layout, half, tier).

Beside it, per element: `<name>_quick_match_<element>_result.json` (every matched pair, with its gt and
predicted text and its score), `<name>_quick_match_<element>_per_page_edit.json` (each page's edit
distance) and, for tables, `<name>_quick_match_table_per_table_TEDS.json`. The run also leaves
`<name>_quick_match_run_summary.json` and runtime and stage logs. A second run into the same work
directory with the same predictions directory name replaces the first.

### The perfect-score check

The probe's own ground truth, written back as markdown, must score perfectly: text edit distance 0,
table TEDS 1 and table edit distance 0. Reading order is exact up to one known evaluator artefact: the
pinned evaluator drops the first predicted table of a page from the predicted reading order
(`src/core/matching/match.py` line 714), costing one edit on every page with a table, for every tool
alike. The evaluator runs unmodified, so the test asserts that this is the only difference, page by page,
and that the aggregate equals the value it predicts (about 0.062 over the probe). Section 5.3 of
`docs/parsing_benchmark_design.md` records it, with the alternative left to the matcher gate. This proves
the export and the pinned evaluator agree on the format, and that the evaluator pairs `.png` pages with `.md` predictions. It is a slow test, skipped
with its reason where the env or the checkout is absent:

```bash
conda run -n synthetic pytest tests/parsing/test_perfect_score.py -m slow -s
```

The `-s` shows the scores. A score that is not perfect is either a writer defect, fixed in
`generators/parsing/omnidocbench.py`, or an evaluator behaviour, recorded under "Perfect-score findings"
in section 5.3 of `docs/parsing_benchmark_design.md`.

## The parsers

`tools` in `config/parsing_config.yml` lists the parsers scored. Each runs locally on Apple silicon, from
its own conda env, built from a committed file under `envs/`; none of their packages enters
`environment.yml`. The env file's `name:` key names the env, and the runner reads it from there.

| Tool (config name) | Back-end | Env file | Env | Pinned |
|---|---|---|---|---|
| MinerU (`mineru_pipeline`) | `pipeline` | `envs/mineru.yml` | `docparse-mineru` | MinerU 3.4.5, Python 3.12, torch 2.13.0 |
| MinerU (`mineru_vlm`) | `vlm-engine` | `envs/mineru.yml` | `docparse-mineru` | the same env |
| Docling (`docling`) | `default` (standard pipeline) | `envs/docling.yml` | `docparse-docling` | Docling 2.120.2, Python 3.12, torch 2.13.0 |
| Marker (`marker`) | `default` | `envs/marker.yml` | `marker` | marker-pdf 2.0.0, Python 3.12, torch 2.14.1, llama.cpp 11351 |

`device` is `mps` for every tool: the runner passes it as `MINERU_DEVICE_MODE` to MinerU, as `--device`
to Docling and as `TORCH_DEVICE` to Marker.

### Building the envs (once per machine)

The MinerU and Docling envs already exist on the machine the benchmark was first scored on, and their
files describe them (the tool, Python, torch and, for MinerU, the MLX packages are pinned; everything else
follows). Build one only where `conda env list` does not show it:

```bash
conda env create -f envs/mineru.yml     # docparse-mineru
conda env create -f envs/docling.yml    # docparse-docling
conda env create -f envs/marker.yml     # marker
```

Each env is checked by printing its tool's version or help:

```bash
conda run -n docparse-mineru mineru --version
conda run -n docparse-docling docling --version
conda run -n marker marker --help
```

### Model weights

Every run is made with the Hugging Face hub offline (`HF_HUB_OFFLINE=1`), so each tool's weights must be on
disk first. Where they are is a property of the machine, not of the repository, so no path is committed:
each tool that finds its weights through an environment variable gets it set once in its env, and the
runner checks it (`conda run -n <env> printenv <variable>`) before every run, refusing a run whose env
does not set it. Below, `<weights dir>` stands for the directory the machine keeps model weights in.

| Env | Variable the runner requires | Set once with |
|---|---|---|
| `docparse-mineru` | `MINERU_TOOLS_CONFIG_JSON` | `conda env config vars set -n docparse-mineru MINERU_TOOLS_CONFIG_JSON=<weights dir>/mineru.json` |
| `docparse-docling` | `DOCLING_ARTIFACTS_PATH` | `conda env config vars set -n docparse-docling DOCLING_ARTIFACTS_PATH=<weights dir>/docling-artifacts` |
| `marker` | none | |

Either way works: a variable exported by the shell profile (`export <variable>=<path>`) reaches the env
through `conda run`, which inherits the caller's environment, and is how the scoring machine provides
them today; setting it in the env makes the env self-sufficient. Marker's weights,
and anything else the tools fetch from the Hugging Face hub, sit in the hub cache, `$HF_HOME/hub` (by
default `~/.cache/huggingface/hub`).

- **MinerU** reads its model directories from the JSON file `MINERU_TOOLS_CONFIG_JSON` names, whose
  `models-dir` holds `pipeline: <weights dir>/PDF-Extract-Kit-1.0` (opendatalab/PDF-Extract-Kit-1.0) and
  `vlm: <weights dir>/MinerU2.5-Pro-2605-1.2B` (opendatalab/MinerU2.5-Pro-2605-1.2B, the model the
  `vlm-engine` back-end loads by default in 3.4.5). Where they are absent, `conda run -n docparse-mineru
  mineru-models-download` fetches them and writes that file. On Apple silicon `vlm-engine` runs the model
  through MLX on the GPU (its log says `Using mlx-engine as the inference engine for VLM`); the device
  setting does not reach it.
- **Docling**'s standard pipeline reads its layout, TableFormer and RapidOCR weights from
  `DOCLING_ARTIFACTS_PATH`. Download them there once (the download only adds folders:
  `docling-project--docling-layout-heron`, its `-onnx` twin, `docling-project--docling-models` and
  `RapidOcr`):

  ```bash
  conda run -n docparse-docling docling-tools models download layout tableformer rapidocr \
      --rapidocr-backend-lang torch:chinese -o <weights dir>/docling-artifacts
  ```

  The `torch:chinese` set is the PP-OCRv6 checkpoint the pipeline asks for; it reads Latin text too.
  Layout and TableFormer run on `mps`; RapidOCR (torch) runs on the CPU.
- **Marker** downloads its weights on its first conversion, which therefore runs online: convert one
  probe page with `marker_single` before the first runner call.

  ```bash
  env TORCH_DEVICE=mps conda run -n marker marker_single \
      ../evaluation_data/parsing_probe/CASE003_invoice.png --output_dir <scratch directory>
  ```

  The weights land in the hub cache (`datalab-to/surya-ocr-2-gguf`, 1.4 GB, the OCR model, and
  `datalab-to/surya_layout2`, 147 MB) and in Marker's own cache, `~/Library/Caches/datalab/models/`
  (`ocr_error_detection`, 277 MB). Marker 2.0.0 runs its OCR model through llama.cpp's `llama-server`,
  which `envs/marker.yml` installs (its Metal build uses the GPU) and Marker starts and stops itself.
  With no `--mode`, Marker runs its `fast` mode on Apple silicon (layout and tables by its lightweight
  detectors, OCR only where text is missing or garbled); the runner passes no mode, so that is what is
  scored.

## Running the parsers

The snapshot comes from `python -m generators.pipeline eval-set`, which writes
`../evaluation_data/snapshot_<YYYYMMDD>/` with its parsing ground truth in `parsing/` (one
OmniDocBench file per half). Run one tool over one half at a time:

```bash
conda run -n synthetic python scripts/run_local_parser.py \
    --tool <tool> --snapshot ../evaluation_data/snapshot_<YYYYMMDD> --half <half>
```

`<tool>` is a config name from the table above; `<half>` is `plain`, `distractor`, `labelled_varied` or
`degraded`. All four tools on all four halves is sixteen calls:

```bash
for tool in mineru_pipeline mineru_vlm docling marker; do
    for half in plain distractor labelled_varied degraded; do
        conda run -n synthetic python scripts/run_local_parser.py \
            --tool "$tool" --snapshot ../evaluation_data/snapshot_<YYYYMMDD> --half "$half" || break 2
    done
done
```

The runner refuses a tool whose runner is not `local`. It hands the tool exactly the images the half's
parsing file lists and writes into `../evaluation_data/raw_predictions/<tool>/<half>/`, which must be absent
or empty (a second run into the same directory would leave two outputs for a page; move the old one
aside). Beside the tool's output it leaves `run.log` (the tool's own output) and `run.json`: tool,
back-end, half, device, env, tool version, the command, start and end times, seconds and the pages run.
`--run-name <name>` writes to `raw_predictions/<tool>/<name>/` instead of the half's name, for a smoke run.

Each tool nests its markdown differently:

| Tool | Raw output per page |
|---|---|
| MinerU `pipeline` | `<stem>/auto/<stem>.md`, beside json, pdf and `images/` by-products |
| MinerU `vlm-engine` | `<stem>/vlm/<stem>.md`, beside the same by-products |
| Docling | `<stem>.md` and `<stem>.json`, flat |
| Marker | `<stem>/<stem>.md`, beside `<stem>_meta.json` and cropped pictures |

### Timing

Measured with the runner on the Apple M1 Pro (16 GB) the benchmark is scored on, on 13 October 2026: one
probe invoice page (a cold run, so model loading dominates; under `raw_predictions/<tool>/probe_smoke/`)
and the six clean probe pages (`probe_timing/`), from which the cost of each further page follows.

| Tool | One page (s) | Six pages (s) | Each further page (s) | A 165-page half, estimated |
|---|---|---|---|---|
| MinerU `pipeline` | 39.6 | 43.9 | 0.9 | about 3 minutes |
| MinerU `vlm-engine` | 42.9 | 206.0 | 32.6 | about 1.5 hours |
| Docling | 39.4 | 85.3 | 9.2 | about 25 minutes |
| Marker | 48.4 | 287.6 | 47.8 | about 2.2 hours |

The estimates scale the further-page cost to a half; bank statements take longest for every tool, so they
are rough. All four tools on all four halves is therefore about 17 hours of machine time, most of it
Marker and MinerU's `vlm-engine`. Run one tool at a time: each uses the GPU, and two at once would
contend for its memory.

## Collecting

```bash
conda run -n synthetic python scripts/collect_predictions.py --tool <tool> --half <half>
```

For every page `run.json` lists, the collector finds the one file named `<image stem>.md` anywhere under the
raw output, whatever the tool's nesting, and copies it to
`../evaluation_data/predictions/<tool>/<half>/<image stem>.md`, the name the evaluator pairs with the page
`<image stem>.png`. A page with no markdown file, or with two, is refused before anything is written, and
the refusal names every such page; a second collection replaces the first.

## Scoring and the leaderboard

```bash
conda run -n synthetic python scripts/score_parsing.py --snapshot ../evaluation_data/snapshot_<YYYYMMDD>
```

For every configured tool and every half with a predictions directory, the pinned evaluator (run through
`run_end2end`, unmodified) scores the predictions against the half's `parsing/<half>.json`, working in
`../evaluation_data/scores/<tool>/<half>/`. The leaderboard is written to
`../evaluation_data/leaderboard_<YYYYMMDD>.md` and `.csv`. A run's `run.json` must name the tool and half
scored; a run made on another snapshot is scored only when every image of its half has the same name and
SHA-256 in both snapshots' `MANIFEST.json` (a re-export that changed only the ground truth), and refused
otherwise. `--snapshot-for <half>=<snapshot dir>` (repeatable) scores one half against another
snapshot's ground truth, with the same image check against that snapshot. It exists for the degraded
half: the heavy tier's fold is not reproducible under a fixed seed, so every re-export writes different
`_v3` images, and degraded runs made on an earlier snapshot can only be scored against that snapshot.
After a re-export that leaves the degraded ground truth alone, score with

```bash
conda run -n synthetic python scripts/score_parsing.py --snapshot ../evaluation_data/snapshot_<new> \
    --snapshot-for degraded=../evaluation_data/snapshot_<runs>
```

The leaderboard opens by naming the snapshot each half was scored against and, for an overridden half,
whether its ground truth is identical in the two snapshots, compared by SHA-256 with each page's
`image_path` cut to its file name, since each snapshot names its own dated image directory there. If it
is not, the degraded runs must be made again on the new snapshot.

- *Overall*, shown as 'n/a (no formulas in stage one)': it needs formula scores, which arrive in stage three,
  and is never replaced by an average of the other components.
- *Runs*: each run's back-end, version, device, pages and seconds per page, from its `run.json`.
- *Scores*: rows per tool, columns per half: text edit distance, table TEDS, structure-only TEDS, table
  edit distance and reading-order edit distance. Tables lead with TEDS. Table edit distance carries its
  caveat: it compares html strings, and the evaluator writes a markdown pipe table's cells with
  `colspan="1" rowspan="1"`, which an html table does not carry, so it compares only tools with the same
  output form (both MinerU back-ends write html tables; Docling and Marker write markdown).
- *Breakdowns* of every score by document type, by degradation tier and by layout, the layout being the
  opaque key the published pages carry (`<doc_type>_layout_<n>`).

The csv holds the same rows, one per section, metric, tool and group, with a column per half; its scores
are unrounded.

## First leaderboard, 14 October 2026

All sixteen runs (four tools, four halves, 165 pages each) collected without a refusal and were first
scored against `snapshot_20261013`; that leaderboard is kept as `leaderboard_20261014_before_footer_fix.md`
and `.csv` under the output root. The invoice remittance slip was then given scored categories (see the
first finding under 'Scores that the evaluator, not the tool, gets wrong or flatters'), the snapshot
re-exported as `snapshot_20261014`, and the plain, distractor and labelled-varied runs re-scored against
it, without running any parser, into `leaderboard_20261014.md` and `.csv`: their images are
byte-identical in both snapshots, which `score_parsing.py` checks against each `MANIFEST.json`. The
degraded runs were scored in the same leaderboard against `snapshot_20261013`, the snapshot they were made
on (`--snapshot-for degraded=../evaluation_data/snapshot_20261013`): the heavy tier's fold is not
reproducible, so its 55 images differ between the snapshots and the check refuses them against
`snapshot_20261014`. The degraded ground truth is identical in both apart from each page's dated image
directory, which the leaderboard's opening lines confirm by hash, and every degraded score equals the
earlier leaderboard's. Each score below was read against the page
image, the tool's markdown and the ground-truth page, at least one page per document type for each tool;
pages are named by case id and role only. Unless a finding says otherwise, the score is right: it
measures what the tool wrote.

Two things to know before reading any number:

- *The per-page mean weights short pages heavily.* A statement has five to eight scored text lines above
  and below its transaction table, so one omitted line moves its page by 0.1 to 0.2, where it moves an
  invoice with thirty lines by 0.03. Statement text edit distance is therefore volatile, and mostly
  measures what a tool does with top-of-page text.
- *Statements repeat between halves.* Labelled-varied pages vary only the labels of invoices and
  receipts, so their statements are the distractor statements, byte for byte; every tool scores them
  identically in both halves.

Scores that are right, but only once the tool's reading is known:

- *MinerU `pipeline` loses most of many receipts.* Plain receipt CASE044 scored text 0.87 and reading
  order 0.92: its layout model found two regions on the page (the store name and address) and the
  markdown holds those two lines and nothing else. The score is right. It does this on 38 of the 55 plain
  receipts (text above 0.8), 3 of 55 distractor and 25 of 165 degraded receipts; the plain receipts are
  the shortest images (about 540 to 770 pixels tall against 750 to 1,070 for distractor receipts), which
  is the likely cause, not yet tested. On plain invoice CASE002 (text 0.24, TEDS 0.63) it read the item
  table well but carried the subtotal, GST and total lines into it as extra rows, so those three text
  blocks go unmatched and the table loses TEDS: the folding the matcher check found, on every layout.
- *MinerU `vlm-engine` drops page-top text it takes for a page header.* Plain invoice CASE002 scored text
  0.22 with TEDS 1: its content list classes the invoice heading as a page header, which the markdown
  omits, and writes the totals as a second, two-column table. The page's first scored line is missing
  from its markdown on all 55 plain invoices. Distractor statement CASE008 scored text 0.86 with TEDS 0.999: the table is perfect, but the
  brand line, page number, account holder and statement period above it are all left out, and on a page
  with seven scored lines that is most of the text. Plain receipt CASE044 (text 0.07, reading order 0.08)
  is typical of its receipts, the best of any tool: it writes the item lines as text, which costs nothing
  in text edit distance but leaves the item table unmatched (TEDS 0). Receipt TEDS is near 0 for every
  tool (0.16 at best): a receipt's items are written as text, or as part of a table spanning the whole
  receipt, almost never as a table of their own.
- *Docling reads 24 of the 55 plain invoices as one page-wide table.* Plain invoice CASE033 scored text
  0.91 and TEDS 0.12: the supplier, customer, date and totals lines all sit in one markdown table with
  the items, so no scored text block has a text prediction to match. The same layouts with distractors
  added are read as text and a table (distractor CASE033 scored 0.01), so Docling's plain invoices score
  worse than its distractor invoices (0.433 against 0.177). On distractor statement CASE008 (text 0.19,
  TEDS 0.999) it is right throughout, missing only the page number and the handwritten note.
- *Marker leaves out page headers and footers.* Plain invoice CASE033 scored text 0.36: the heading and
  supplier name at the top of the page are missing from its markdown, as on every plain invoice (the
  page's first scored line is absent from all 55), and on plain receipts it drops the store name (51 of 55) and the
  closing line. Distractor receipt CASE015 scored text 0.87: Marker wrote the whole clean receipt as one
  three-column table, so no text block matches; the photographed light-tier copy of the same receipt
  scored 0.02, read as text. This is why Marker's degraded half (0.132) scores better than its clean
  receipts (0.204 on the distractor half).

Scores that the evaluator, not the tool, gets wrong or flatters:

- *The ignored-category hazard, found here and now fixed.* Against `snapshot_20261013`, on 30 of the 55
  distractor invoices (and the same 30 labelled-varied ones) a scored line lay within normalised edit
  distance 0.25 of a `footer` line, because the remittance slip at the foot, then `footer`, repeats the
  invoice-number and amount-due lines of the body word for word. The quick matcher pairs each ignored
  line with every prediction within normalised edit distance 0.25 and removes them first (design section
  5.3), so the body's own lines lost their prediction: on distractor invoice CASE002 all four tools
  transcribed the body's invoice number correctly and all four were charged between 0.92 and 1 for it.
  The slip is tear-off content, not running page furniture, so its detach note, heading and lines are
  now `text_block` and its barcode a `figure`; the website line in the foot band, 0.24 from the
  letterhead email or PayID line on two invoices, is `text_block` too. `validate` now refuses any
  ignored block within the evaluator's distance of a scored one (design section 5.1, invariant 6), and
  no page of `snapshot_20261014` has one.

  The fix makes distractor and labelled-varied invoice text edit distance higher, not lower, for every
  tool (plain invoices are unchanged):

  | Tool | Distractor invoices, before / after | Labelled-varied invoices, before / after |
  |---|---|---|
  | MinerU `pipeline` | 0.528 / 0.569 | 0.541 / 0.579 |
  | MinerU `vlm-engine` | 0.292 / 0.308 | 0.265 / 0.283 |
  | Docling | 0.160 / 0.177 | 0.146 / 0.166 |
  | Marker | 0.264 / 0.318 | 0.253 / 0.309 |

  The slip's five lines and the website line are now read or charged. MinerU `pipeline` and Marker
  leave the foot band out almost entirely (mean edit 0.99 and 0.95 on the slip's heading, detach note and
  amount lines), MinerU `vlm-engine` mostly (0.79), and Docling reads most of it (0.40). The body's
  invoice-number line is no longer struck, but the matcher merges the two identical invoice-number lines
  into one ground-truth item, so a tool that reads only the body's copy is charged 0.5 for the pair
  (CASE002: 0.5 for both MinerU back-ends and Marker, 0 for Docling, which reads both). The before
  figures charged correct readings; the after figures charge text a tool left out, which is what the
  metric is meant to measure. Overall distractor and labelled-varied text edit distance and reading-order
  edit distance (more blocks to order on those invoices) rose by 0.005 to 0.019 for every tool; tables,
  every plain figure and every receipt and statement figure are unchanged.

  A per-line check bears this out. The affected pages are the 30 distractor invoices whose body
  invoice-number or amount-due line lay within normalised edit distance 0.25 of a footer line in the old
  ground truth (36 if the company-name, email and PayID lines repeated in the footer are counted). The
  footer pre-emption is gone: the evaluator now merges the body line and its scored slip twin into one
  ground-truth entry, so a tool that prints the line once is charged 0.5, which reflects a real omission
  of the slip copy. Of those 30 pages, the number whose worst such body line is charged above 0.5, before
  and after, is 29 to 26 for MinerU `pipeline`, 30 to 24 for MinerU `vlm-engine`, 28 to 6 for Docling and
  26 to 18 for Marker. The CASE002 body invoice-number line moves from 1.00 to 0.50 (MinerU `pipeline`),
  1.00 to 0.50 (MinerU `vlm-engine`), 0.92 to 0.00 (Docling) and 0.94 to 0.50 (Marker). There is one
  regression: on CASE002 a MinerU `vlm-engine` amount-due line now pairs with the slip twin and is charged
  more than before.
- *A markdown table loses every cell holding a colon.* The pinned evaluator converts a pipe table to html
  and then drops each converted line that matches its table-separator test (`find_md_table_mode` in
  `src/core/preprocess/table_utils.py`), which any colon satisfies, so a cell such as a labelled total or
  a time vanishes from the predicted table (and from the text). An invented one-row table with a cell
  'Ref: Q7' loses that cell. Html tables (both MinerU back-ends) are untouched. Ground-truth table cells
  rarely hold a colon (87 of 8,304 statement cells, 33 of 768 invoice cells, none on receipts), so the
  direct cost to Docling and Marker's TEDS is small; it mostly removes folded-in total labels, as on
  Marker's plain invoice CASE033 (TEDS 0.28).
- *Dollar signs in markdown become inline formulas.* The evaluator reads the text between two `$` signs
  as a formula, across lines, and keeps the pieces as formula items; in a pipe table of amounts, cell
  fragments such as an amount and the next column separator then appear as spare predicted text. Text
  blocks still score correctly (the perfect-score check holds with unescaped `$`), but these fragments and
  the date cells the table-formula rescue turns into formulas are what unmatched ground-truth lines get
  paired with, so per-block results show a statement's brand line 'matched' to a date.
- *Those leftover pairings move reading order.* On distractor statement CASE008, Marker's markdown has
  the page in the right order, but the brand line and page number, unmatched, are paired with date cells
  inside the table and so placed after the account-holder and period lines: reading order 0.57 for a correctly ordered
  page.
- *One merged paragraph flatters both text and reading order.* Docling writes each receipt as one
  paragraph; distractor receipt CASE044 scored text 0.12 and reading order 0.03, the matcher merging all
  29 ground-truth text blocks into it. Reading order is scored between matched blocks, so a page that is
  one block has almost no order to get wrong, and the item table's text inside the paragraph is charged
  as text, not as the table Docling did not find (TEDS 0).
- *Table edit distance follows the output form, as expected.* On distractor statement CASE008, Docling,
  Marker and MinerU `vlm-engine` all scored TEDS 0.999, but table edit distance 0.53 and 0.53 for the two
  markdown tools against 0.003 for MinerU: the `colspan="1" rowspan="1"` the evaluator adds to markdown
  cells. Compare table edit distance within an output form only.
- *The reading-order artefact.* Every page whose first predicted table matched loses that table's
  position (statement CASE008: the table's place is missing from every tool's predicted order), one edit
  for every tool alike.
- *Cropped degraded blocks.* The 10 cropped blocks (4 pages) are ignored as intended. Where a parser runs
  a cropped line into its neighbour, the neighbour is charged for the extra text: on moderate-tier
  receipt CASE044, MinerU `vlm-engine` joined the cropped exchange-policy line to the closing thanks line,
  which then scored 0.70.

## Licences of the tools scored

Each tool's code and model weights are licensed separately. The terms below were read from their official
sources on 14 October 2026 (Marker's on 13 October 2026). They are the author's reading of the quoted
text, not legal advice, and each finding waits on the project owner's confirmation before any score is
published. The leaderboard ends with a 'Tools and licences' block, generated from `credits` in
`config/parsing_config.yml`, that credits each scored project with its URL and its code and weights
licences; where a weights licence could not be confirmed from an official source it reads 'see <URL>'.

### MinerU

- **Code: the MinerU Open Source License,** Apache License 2.0 with additional terms
  (<https://github.com/opendatalab/MinerU/blob/master/LICENSE.md>; the same file ships in the pinned
  3.4.5 package as `LICENSE.md`). The README says: 'This repository is licensed under the MinerU Open
  Source License'. It is not AGPL-3.0. Commercial use needs no separate licence below two thresholds
  (monthly active users above 100 million, or monthly revenue above US$20 million, for the user and its
  affiliates together); anyone offering an online service built on MinerU must show that MinerU is used,
  in the interface or in public documentation. It says nothing about publishing benchmark results.
- **Weights.** MinerU2.5-Pro, the `vlm-engine` model, declares `apache-2.0`
  (<https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B>). PDF-Extract-Kit-1.0, the `pipeline`
  models, sets no licence tag and carries only a heading reading 'license: agpl-3.0'
  (<https://huggingface.co/opendatalab/PDF-Extract-Kit-1.0>), so its weights licence is not confirmed:
  see that page.
- **Finding.** Nothing in the code licence restricts publishing scores, and this benchmark offers no
  online service; the leaderboard credits MinerU and links its licence in any case. The PDF-Extract-Kit
  licence should be settled with its authors before the `pipeline` scores are published.

### Docling

- **Code: MIT.** The README: 'The Docling codebase is under MIT license.'
  (<https://github.com/docling-project/docling>, `LICENSE` in the repository). For the models it says
  to 'refer to the model licenses found in the original packages'.
- **Weights.** The layout model (`docling-layout-heron`) declares `apache-2.0`
  (<https://huggingface.co/docling-project/docling-layout-heron>); `docling-models`, which holds
  TableFormer, declares both `apache-2.0` and `cdla-permissive-2.0`
  (<https://huggingface.co/docling-project/docling-models>). The RapidOCR weights the pipeline also
  loads were not confirmed from an official source: see <https://github.com/RapidAI/RapidOCR>.
- **Finding.** Permissive terms; none restricts publishing scores. The leaderboard credits the project.

### Marker

- **Code: Apache License 2.0.** The repository's `LICENSE` file is the Apache License, Version 2.0
  (<https://github.com/datalab-to/marker/blob/master/LICENSE>), and its README says the code is free to
  use, including commercially (<https://github.com/datalab-to/marker#commercial-usage>).
- **Model weights: a modified AI Pubs Open RAIL-M licence.** The README: 'Our model weights use a modified
  AI Pubs Open Rail-M license (free for research, personal use, and startups under $5M
  funding/revenue).' The weights' model card says the same
  (<https://huggingface.co/datalab-to/surya-ocr-2-gguf>, licence tag `openrail`), and the full text is the
  `MODEL_LICENSE` file of the surya repository
  (<https://github.com/datalab-to/surya/blob/master/MODEL_LICENSE>), titled 'AI PUBS OPEN RAIL-M LICENSE
  (MODIFIED)'. Its operative terms:
  - paragraph 5: 'Use may include creating any content with, fine-tuning, updating, running, training,
    evaluating and/or re-parametrizing the Model.';
  - paragraph 6: 'Except as set forth herein, Licensor claims no rights in the Output You generate using
    the Model.';
  - paragraph 7 asks for attribution with any Output: credit to the licensor, a link to the model, a copy
    of the licence, and no suggestion of endorsement;
  - paragraph 8 applies the licence to the Output and its derivatives;
  - Attachment A, 2(a) and 2(b) bar use by anyone (or their employer) with more than five million US
    dollars of revenue in the prior year, or of funding, 'except where Your Use is limited to personal use
    or research purposes'; 2(c) bars use by anyone whose employer offers a product or service that
    competes with the licensor's.

**Finding.** This is the author's reading, pending the project owner's confirmation; it is not legal
advice. Nothing in the quoted text forbids publishing benchmark scores, and evaluating the model is a
named permitted use (paragraph 5).

- Clauses 2(a) and 2(b): the revenue and funding limits carry the exception 'except where Your Use is
  limited to personal use or research purposes', so research use, which a published benchmark is, stays
  inside them. They would bind a large employer running Marker as part of a commercial service.
- Clause 2(c): the quoted text bars use 'for any purpose if You (your employer, or the entity you are
  affiliated with) provides or otherwise makes available' a product or service 'that competes with any
  product or service offered by or made available by Licensor'. The research exception sits in 2(a)
  and 2(b) only, so on the text as quoted it does not soften 2(c): research use is no answer to it.
  'Competes' is not defined. Whether 2(c) applies turns on a fact the repository cannot show, namely
  whether the person running the benchmark, or their employer or affiliated entity, offers a document
  conversion or OCR product or service. The project owner must confirm that none does before Marker's
  scores are published; if one does, the safe course is to leave Marker out. *Confirmed 15 October 2026:*
  the project owner has confirmed that neither they nor their employer offers a product or service that
  competes with Datalab's, so 2(c) does not apply, and the benchmark's use is research, inside the
  exception to 2(a) and 2(b). The licence text was re-read from `MODEL_LICENSE` that day and matches the
  quotations above.
- Attribution (paragraph 7): the leaderboard's 'Tools and licences' block, and the README's section of
  the same name, credit Marker and its models with their links and licence, state that the models were
  used unchanged, and suggest no endorsement. Marker's raw markdown (the model's Output)
  is not published, or is published only under the same licence (paragraph 8). The scores themselves
  are computed by OmniDocBench, not by the model.

### Marker's weights revision

Marker takes whichever revision of its weights the hub serves when they are first fetched, so a score is
reproducible only against the revision recorded with it. Read it from the hub cache, where each model
keeps one directory per revision named by its commit hash (`$HF_HOME` is the hub cache's parent, by
default `~/.cache/huggingface`):

```bash
ls $HF_HOME/hub/models--datalab-to--surya-ocr-2-gguf/snapshots/
ls $HF_HOME/hub/models--datalab-to--surya_layout2/snapshots/
```

The first benchmark run used `datalab-to/surya-ocr-2-gguf` at revision
`6a3a4c30e5e74446d4f8b6afd05b2f2da970f470` and `datalab-to/surya_layout2` at
`0aee81d5fd9275c0582e545bf3a56944b1e75679`. To fetch exactly those again, set `HF_HUB_OFFLINE=0` once
and download with `huggingface-cli download <repo> --revision <hash>`. The small `ocr_error_detection`
model sits outside the hub, in Marker's own cache under `~/Library/Caches/datalab/models/`, in a
directory named by its date (`2025_02_18`).

## The matcher-check probe runs

The matcher check (section 5.3) parsed the probe before the runner existed, from the same
`docparse-mineru` and `docparse-docling` envs, with the commands below. Raw output went under
`raw_predictions/<tool>/probe/`; each page's markdown was then copied to
`predictions/<tool>/probe/<image stem>.md`. Unlike the rest of this runbook, these commands run from
`eval_set.output_root`, not the repository root.

MinerU, pipeline back-end, on `mps`; it writes `<stem>/auto/<stem>.md` per page:

```bash
env MINERU_DEVICE_MODE=mps HF_HUB_OFFLINE=1 conda run -n docparse-mineru \
    mineru -p parsing_probe -o raw_predictions/mineru_pipeline/probe -b pipeline
for f in raw_predictions/mineru_pipeline/probe/*/auto/*.md; do
    \cp -f "$f" predictions/mineru_pipeline/probe/
done
```

Docling, standard pipeline with default settings:

```bash
env DOCLING_DEVICE=mps HF_HUB_OFFLINE=1 conda run -n docparse-docling \
    docling convert parsing_probe --from image --to md --to json --image-export-mode placeholder \
    --output raw_predictions/docling/probe
\cp -f raw_predictions/docling/probe/*.md predictions/docling/probe/
```

Docling's VLM pipeline (GraniteDocling preset through MLX, weights already in the same folder) was run
as an aside under its own name, `docling_granite_vlm`; it is not the configured Docling tool:

```bash
env DOCLING_DEVICE=mps HF_HUB_OFFLINE=1 conda run -n docparse-docling \
    docling convert parsing_probe --from image --to md --to json --image-export-mode placeholder \
    --pipeline vlm --vlm-model granite_docling --output raw_predictions/docling_granite_vlm/probe
\cp -f raw_predictions/docling_granite_vlm/probe/*.md predictions/docling_granite_vlm/probe/
```

Each was then scored with `end2end` above, `--gt parsing_probe/probe.json`, into
`parsing_gate/<tool>/`.
