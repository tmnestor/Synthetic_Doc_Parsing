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
disk first. On the scoring machine `HF_HOME` is `<weights dir>`, so the hub cache is
`<weights dir>/hub`.

- **MinerU** reads its model directories from the file `MINERU_TOOLS_CONFIG_JSON` names
  (`<weights dir>/mineru.json`), whose `models-dir` holds `pipeline:
  <weights dir>/PDF-Extract-Kit-1.0` (opendatalab/PDF-Extract-Kit-1.0) and `vlm:
  <weights dir>/MinerU2.5-Pro-2605-1.2B` (opendatalab/MinerU2.5-Pro-2605-1.2B, the model the
  `vlm-engine` back-end loads by default in 3.4.5). Where they are absent, `conda run -n docparse-mineru
  mineru-models-download` fetches them and writes that file. On Apple silicon `vlm-engine` runs the model
  through MLX on the GPU (its log says `Using mlx-engine as the inference engine for VLM`); the device
  setting does not reach it.
- **Docling**'s standard pipeline reads its layout, TableFormer and RapidOCR weights from
  `DOCLING_ARTIFACTS_PATH` (`<weights dir>/docling-artifacts`). Download them there once (the
  download only adds folders: `docling-project--docling-layout-heron`, its `-onnx` twin,
  `docling-project--docling-models` and `RapidOcr`):

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
`../evaluation_data/leaderboard_<YYYYMMDD>.md` and `.csv`:

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

## Marker's licences

Marker's code and its model weights are licensed separately; both were read from their official sources on
13 October 2026.

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

**Finding.** Nothing in either licence forbids publishing benchmark scores, and evaluating the model is a
named permitted use. Publishing Marker's scores is therefore permitted as research use, with three
conditions to keep: the benchmark is run for research, not as part of a commercial service (the revenue
and funding limits of Attachment A, 2(a) and 2(b), would otherwise bind a large employer); the leaderboard
credits Marker and its model with links and the licence; and Marker's raw markdown (the model's Output)
is not published, or is published only under the same licence (paragraph 8). The scores themselves are
computed by OmniDocBench, not by the model. Docling is MIT; this section covers Marker only.

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
