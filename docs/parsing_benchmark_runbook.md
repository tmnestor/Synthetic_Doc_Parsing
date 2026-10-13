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
