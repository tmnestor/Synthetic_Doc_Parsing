# Running the scorer locally when extraction has to happen remotely

Inference runs on the remote GPU host; `score_extractions.ipynb` runs here. This is
the round trip between them: what goes up, what comes back, and the check that has
to pass before any number is quoted.

The notebook needs no GPU, no repository checkout and no remote access: only `pandas`,
`matplotlib`, `seaborn` and two files per run. Every snapshot ships it in `tools/`, with its
outputs cleared, beside the preflight and the verifier.

Paths below use `$SNAP` for the snapshot directory, for example
`SNAP=~/Desktop/evaluation_data/snapshot_<YYYYMMDD>`, and `<D>` for its date stamp.

## 1. Validate, then export the snapshot (local)

```bash
python -m generators.pipeline validate
python -m generators.pipeline eval-set
```

`validate` comes first: the export repeats every render check, but `validate` names each problem in
full. The export runs from a modified tree too (it records the changes in `MANIFEST.json` and
`working_tree.patch`, and the snapshot's README says so near its top), writes one directory,
`snapshot_<YYYYMMDD>/`, under `eval_set.output_root`, and prints `MANIFEST.json`'s own SHA-256:
record it beside the upload, since the manifest cannot vouch for itself.

| Half | Contents |
|---|---|
| `plain_<D>/` | 165 images, 3 document types, no distractors (the control) |
| `distractor_<D>/` | the same 165 filenames, with distractors |
| `labelled_varied_<D>/` | the same again, ABN, GST and total labels varied; plus `label_styles.jsonl` |
| `degraded_<D>/` | 165 images: the 55 distractor receipts x 3 severity tiers |

Each half carries its own `ground_truth.csv` and `ground_truth.jsonl`. The three clean halves' files
are byte-identical; the degraded ground truth names `CASE001_receipt_v1.png` and carries only the
receipt columns, so it is **not** interchangeable with them. `geometry/` holds the clean halves'
scored boxes.

## 2. Verify on arrival

On whichever machine receives the snapshot (after an S3 download, say), before anything else:

```bash
cd $SNAP
shasum -a 256 MANIFEST.json            # compare with the hash recorded beside the upload
python tools/verify_snapshot.py        # standard library only; exits 1 on any mismatch
```

## 3. Send the images up, and only the images

```bash
for half in plain distractor labelled_varied degraded; do
    rsync -av --include='*.png' --exclude='*' "$SNAP/${half}_<D>/" "<host>:<path>/${half}_<D>/"
done
```

**Leave the ground truth, the sidecar and the geometry here.** Extraction does not need them, and
keeping the answer key off the inference host removes any route by which a prompt, a debug print or
a retry loop could see it. The sidecar records which label each image prints and the geometry where
each answer sits, so either would leak answers too. Scoring happens locally.

## 4. Extract (remote)

Run your extraction stage once per half. This repo deliberately holds no dependency on the
extraction codebase (`docs/eval_export_plan.md` records that removal), so the exact invocation is
**not** something this repo can state. Whatever it is, it must produce one `raw_extractions.jsonl`
per half, each record being one image:

```json
{"image_name": "CASE001_receipt.png", "document_type": "RECEIPT", "raw_response": "..."}
```

`image_name`, `document_type` and `raw_response` are the only keys the notebook reads; extra keys
(`image_path`, `processing_time`, `prompt_used`) are ignored, and a record carrying a non-empty
`error` is skipped and counted.

Two things to get right, because both are silent if wrong:

- **Run the halves separately.** Merging them into one file destroys the pairing.
- **Keep the filenames the model was given.** The filename is the pairing key across the clean
  halves, and the tier suffix is the only thing connecting a degraded result back to its receipt.

## 5. Bring back one file per half

Nothing but the JSONL:

```bash
for half in plain distractor labelled_varied degraded; do
    scp "<host>:<path>/${half}_<D>/raw_extractions.jsonl" "$HOME/Desktop/evaluation_data/runs/<model>_${half}_<D>.jsonl"
done
```

Name them for the model, the half and the snapshot. Several runs all called
`raw_extractions.jsonl` in different folders is how the wrong one ends up scored. Keep run outputs
outside the snapshot directory: anything written inside it makes `verify_snapshot.py` fail.

## 6. Check before scoring; do not skip this

```bash
python $SNAP/tools/check_extractions_match_gt.py \
    --extractions  ~/Desktop/evaluation_data/runs/<model>_plain_<D>.jsonl \
    --ground-truth $SNAP/plain_<D>/ground_truth.csv

python $SNAP/tools/check_extractions_match_gt.py \
    --extractions  ~/Desktop/evaluation_data/runs/<model>_degraded_<D>.jsonl \
    --ground-truth $SNAP/degraded_<D>/ground_truth.csv
```

(and likewise for `distractor` and `labelled_varied`). Each must print `PASS`. The check exists
because the failure it catches is invisible downstream: the notebook scores whatever overlap it
finds and reports the remainder as unmatched, so a run paired with the wrong ground truth yields a
plausible-looking F1 built on a fraction of the corpus. A June 2026 run scored against August
ground truth matched **22 of 165** filenames: the corpus had been reseeded in between.

If it fails, do not re-pair; re-extract against the current snapshot. Two scores are comparable
only when they come from the same `MANIFEST.json`.

## 7. Score (local)

Open `tools/score_extractions.ipynb` (or the repository's copy) and set the paths in the config cell,
for example plain against distractor:

```python
RAW_PATH   = Path("~/Desktop/evaluation_data/runs/<model>_plain_<D>.jsonl").expanduser()
GT_PATH    = Path("<SNAP>/plain_<D>/ground_truth.csv").expanduser()

RAW_PATH_B = Path("~/Desktop/evaluation_data/runs/<model>_distractor_<D>.jsonl").expanduser()
GT_PATH_B  = Path("<SNAP>/distractor_<D>/ground_truth.csv").expanduser()
```

`.jsonl` works in place of `.csv` for either ground truth and scores identically;
`tests/fixtures/notebook_smoke/check_gt_formats_agree.py` asserts it.

Leave `RAW_PATH_B` and `GT_PATH_B` as `None` to score one half alone; the comparison section then
prints that it is disabled and does nothing else.

Run all cells. Figures are written to `figures/` at 300 dpi as well as drawn inline; run the notebook
from outside the snapshot, so they are not written into it. To stratify the labelled varied half by
label style, join `labelled_varied_<D>/label_styles.jsonl` to the per-field scores on `image_file`.

## What to look at first

1. **The confusion table**, before any F1. A misclassified document is scored
   against the questions its predicted type was asked, so classification error
   propagates into every field number below it.
2. **`micro_precision` vs `micro_recall`** in the micro table. Precision 1.0 with
   low recall is truncation (a prompting or context-length problem). Low precision
   is hallucination, a different investigation entirely.
3. **The per-tier delta**, if you scored a degraded run against its clean receipts. A metric that slides gently
   from `v1` to `v3` is telling you something quite different from one that falls
   off a cliff at `v2`.

The notebook's own "Quoting these numbers" section covers which figure to cite for
which claim, and the traps in each.
