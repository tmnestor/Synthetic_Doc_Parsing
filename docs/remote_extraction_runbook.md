# Running the scorer locally when extraction has to happen remotely

Inference runs on the remote GPU host; `score_extractions.ipynb` runs here. This is
the round trip between them: what goes up, what comes back, and the check that has
to pass before any number is quoted.

The notebook itself needs no GPU, no repo checkout and no remote access — only
`pandas`, `matplotlib`, `seaborn` and two files per run.

## 1. Export the set (local)

```bash
python -m generators.pipeline eval-set --out ~/Desktop/evaluation_data
```

Writes two dated sibling directories:

| Directory | Contents | Size |
|---|---|---|
| `synthetic_<YYYYMMDD>/` | 165 clean images, 3 document types | ~27 MB |
| `degraded_<YYYYMMDD>/` | 165 images: 55 receipts × 3 severity tiers | ~46 MB |

Each carries its own `ground_truth.csv` and `ground_truth.jsonl`. They are **not**
interchangeable: the degraded ground truth names `CASE001_receipt_v1.png` and
carries only the receipt columns.

## 2. Send the images up — and only the images

```bash
rsync -av --include='*.png' --exclude='*' \
    ~/Desktop/evaluation_data/synthetic_<YYYYMMDD>/  <host>:<path>/synthetic_<YYYYMMDD>/
rsync -av --include='*.png' --exclude='*' \
    ~/Desktop/evaluation_data/degraded_<YYYYMMDD>/   <host>:<path>/degraded_<YYYYMMDD>/
```

**Leave the ground truth here.** Extraction does not need it, and keeping the answer
key off the inference host removes any route by which a prompt, a debug print or a
retry loop could see it. Scoring happens locally, where the ground truth already is.

## 3. Extract (remote)

Run your extraction stage once per directory. This repo deliberately holds no
dependency on the extraction codebase — `docs/eval_export_plan.md` records that
removal — so the exact invocation is **not** something this repo can state. Whatever
it is, it must produce one `raw_extractions.jsonl` per directory, each record being
one image:

```json
{"image_name": "CASE001_receipt.png", "document_type": "RECEIPT", "raw_response": "..."}
```

`image_name`, `document_type` and `raw_response` are the only keys the notebook
reads; extra keys (`image_path`, `processing_time`, `prompt_used`) are ignored, and
a record carrying a non-empty `error` is skipped and counted.

Two things to get right, because both are silent if wrong:

- **Run the two directories separately.** Merging them into one file destroys the
  clean-vs-degraded pairing.
- **Keep the filenames the model was given.** The tier suffix is the only thing
  connecting a degraded result back to its clean counterpart.

## 4. Bring back two files

Nothing but the JSONL — roughly a megabyte, against 73 MB of images:

```bash
scp <host>:<path>/synthetic_<YYYYMMDD>/raw_extractions.jsonl  ~/Desktop/evaluation_data/runs/<model>_clean.jsonl
scp <host>:<path>/degraded_<YYYYMMDD>/raw_extractions.jsonl   ~/Desktop/evaluation_data/runs/<model>_degraded.jsonl
```

Name them for the model and the half. Two runs called `raw_extractions.jsonl` in
different folders is how the wrong one ends up scored.

## 5. Check before scoring — do not skip this

```bash
python scripts/check_extractions_match_gt.py \
    --extractions  ~/Desktop/evaluation_data/runs/<model>_clean.jsonl \
    --ground-truth ~/Desktop/evaluation_data/synthetic_<YYYYMMDD>/ground_truth.csv

python scripts/check_extractions_match_gt.py \
    --extractions  ~/Desktop/evaluation_data/runs/<model>_degraded.jsonl \
    --ground-truth ~/Desktop/evaluation_data/degraded_<YYYYMMDD>/ground_truth.csv
```

Both must print `PASS`. The check exists because the failure it catches is invisible
downstream: the notebook scores whatever overlap it finds and reports the remainder
as unmatched, so a run paired with the wrong ground truth yields a plausible-looking
F1 built on a fraction of the corpus. A June 2026 run scored against August ground
truth matched **22 of 165** filenames — the corpus had been reseeded in between.

If it fails, do not re-pair; re-extract against the current export.

## 6. Score (local)

Open `score_extractions.ipynb` and set four paths in the config cell:

```python
RAW_PATH   = Path("~/Desktop/evaluation_data/runs/<model>_clean.jsonl").expanduser()
GT_PATH    = Path("~/Desktop/evaluation_data/synthetic_<YYYYMMDD>/ground_truth.csv").expanduser()

RAW_PATH_B = Path("~/Desktop/evaluation_data/runs/<model>_degraded.jsonl").expanduser()
GT_PATH_B  = Path("~/Desktop/evaluation_data/degraded_<YYYYMMDD>/ground_truth.csv").expanduser()
```

`.jsonl` works in place of `.csv` for either ground truth and scores identically —
`tests/fixtures/notebook_smoke/check_gt_formats_agree.py` asserts it.

Leave `RAW_PATH_B` and `GT_PATH_B` as `None` to score the clean half alone; the
comparison section then prints that it is disabled and does nothing else.

Run all cells. Figures are written to `figures/` at 300 dpi as well as drawn inline.

## What to look at first

1. **The confusion table**, before any F1. A misclassified document is scored
   against the questions its predicted type was asked, so classification error
   propagates into every field number below it.
2. **`micro_precision` vs `micro_recall`** in the micro table. Precision 1.0 with
   low recall is truncation — a prompting or context-length problem. Low precision
   is hallucination, a different investigation entirely.
3. **The per-tier delta**, if you scored both halves. A metric that slides gently
   from `v1` to `v3` is telling you something quite different from one that falls
   off a cliff at `v2`.

The notebook's own "Quoting these numbers" section covers which figure to cite for
which claim, and the traps in each.
