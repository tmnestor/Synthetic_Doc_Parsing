# Evaluation-Set Export — Implementation Plan

> **⚠️ Superseded — kept as a historical record of the 2026-08-05 design.**
> This plan describes the export as it was decided *before* the degraded half was
> redesigned. Two later commits changed it: `a77fb7a` (2026-08-06) made the degraded
> half receipts-only at three severity tiers, and `1076e1f` (2026-08-07) gave each
> variant a `_v1`/`_v2`/`_v3` suffix and wrote its ground truth rather than copying it.
>
> The claims below that **filenames match across both directories**, that the ground
> truths are **byte-identical copies**, and that **one ground truth scores both** are no
> longer true. Pairing now joins on the filename with the tier suffix removed, and a
> clean-vs-degraded comparison covers only the 55 receipts. For current behaviour see
> the README's "The evaluation datasets" section and the module docstring in
> `generators/eval_set.py`.

> Steps use checkbox (`- [ ]`) syntax for tracking; work through them in order.

**Goal:** `python -m generators.pipeline eval_set` produces two self-contained sibling directories — `synthetic_<YYYYMMDD>/` and `degraded_<YYYYMMDD>/` — each holding 165 images and its own copy of `ground_truth.csv` / `ground_truth.jsonl`, with no dependency on any other repository.

**Architecture:** The export's schema projection moves in-house. `config/extraction_schema.yml` already carries everything LMM_POC's `common.field_schema` provided; a new local module reads it, and `generators/eval_set.py` calls that module directly instead of shelling out to a script from another repo.

**Tech Stack:** Python 3.12, Pillow 12.2.0 (pinned), PyYAML `safe_load`, pytest, conda env `synthetic`.

## Why

The export currently depends on LMM_POC in two ways: `config/generation_config.yml` points at `../LMM_POC/scripts/relabel_evaluation_set.py`, and this repo's own `scripts/relabel_evaluation_set.py` — a **diverged copy** of that file — imports `from common.field_schema import ...`, an LMM_POC package. Neither can run without that checkout present.

The dataset these produce is the deliverable: competing vision-language models are scored on information-extraction accuracy against it. A deliverable this repo cannot build alone is a liability.

## What the products look like

```
<out>/synthetic_20260805/          <out>/degraded_20260805/
  CASE001_bank_statement.png         CASE001_bank_statement.png
  CASE001_invoice.png                CASE001_invoice.png
  CASE001_receipt.png                CASE001_receipt.png
  ...  (165 total)                   ...  (165 total)
  ground_truth.csv                   ground_truth.csv     ← byte-identical copy
  ground_truth.jsonl                 ground_truth.jsonl   ← byte-identical copy
```

Decided by the repo owner:

- **Filenames match across both directories.** A degraded image carries the same ground truth as its clean counterpart, so one ground truth scores both and clean-vs-degraded isolates image quality as the only variable.
- **Filenames are generic** — `CASE001_bank_statement.png`, never `CASE001_cba_standard.png`. The layout variant must not leak, or a model could infer the template from the filename.
- **Each directory is self-contained.** Both carry their own copy, so a model run points at one path and finds everything.
- **The date suffix is today's, `YYYYMMDD`**, stamped when the export runs.
- **`evaluation_data/synthetic_20260731` is disposable.** It need not be preserved, but nothing in this plan deletes it — that is the owner's call, after the replacement exists and is verified.

## Global Constraints

- Conda env is `synthetic`. Run everything as `conda run -n synthetic <cmd>`.
- `tests/` is gitignored — write and run tests, never `git add tests/`.
- Every commit passes, in order: `pytest tests/`, `ruff check --fix --ignore ARG001,ARG002,F841 *.py`, `ruff format .`, `mypy . --ignore-missing-imports`. Never `--no-verify`.
- Line length 108. Google-style docstrings. `pathlib.Path`. Python 3.12 types. B904 in except blocks.
- Every config key is required; a missing one fails fast with a four-element diagnostic (WHAT / WHERE with path and dotted key / WHAT IT SHOULD LOOK LIKE / HOW TO RECOVER). Tests assert all four via `assert_diagnostic_error`.
- Refer to the production environment as PROD; never name the organisation.
- **`environment.yml` carries an uncommitted change owned by the repo owner.** Never stage, revert or touch it. Stage by explicit path only.
- Work on `main`. Commit only when asked.

### Gates

**`tests/test_bank_pixel_snapshot.py`, `test_receipt_pixel_snapshot.py`, `test_invoice_pixel_snapshot.py` and `tests/test_derived_baseline.py` — 127 tests — must stay green throughout.** This refactor changes how documents are *exported*, never how they are *rendered* or *derived*. A red gate means something strayed.

Do **NOT** run any `regenerate_*_snapshot.py` script or re-capture the derived baseline.

### Do not touch

`../LMM_POC/` — another repository. Read it to understand what the current script does; change nothing in it.

---

## Task 1: Pin the target format

Before changing the export, capture what the existing dataset's *structure* is, so the replacement can be checked against it. Values will differ — the corpus was reseeded — but shape must not.

**Files:**
- Create: `tests/fixtures/eval_format_baseline.json`, `tests/test_eval_format.py`

- [ ] **Step 1: Read `../evaluation_data/synthetic_20260731` read-only**

Record: the exact filename pattern and count; `ground_truth.csv`'s header and column order; `ground_truth.jsonl`'s key set and their order within a record; which files exist beside the images.

- [ ] **Step 2: Write the fixture and a test asserting a given directory matches that structure**

The test takes a directory path and checks shape only — never values. It must be reusable, because later tasks point it at freshly exported directories.

- [ ] **Step 3: Confirm it passes against the existing dataset**

Run it against `synthetic_20260731` and confirm green. That proves the fixture describes reality rather than an assumption.

- [ ] **Step 4: No commit** — `tests/` is gitignored. Record completion in the report.

---

## Task 2: Bring the schema projection in-house

**Files:**
- Create: `generators/exporters/eval_projection.py`
- Test: `tests/exporters/test_eval_projection.py`

**Interfaces:**
- Produces: a loader over `config/extraction_schema.yml` exposing what the export needs — the extraction field list per document type, the canonical document-type names, and the monetary and boolean field-type classifications. Task 3 consumes it.

`config/extraction_schema.yml` already holds all of it: `document_fields` keyed by `invoice` / `receipt` / `bank_statement`, and `evaluation.field_types` with `monetary` (9 entries) and `boolean` (1).

- [ ] **Step 1: Read what LMM_POC's `common/field_schema.py` actually provides**

`scripts/relabel_evaluation_set.py` uses `schema.monetary_fields`, `schema.boolean_fields`, `schema.resolve_doc_type(...)`, `schema.get_all_doc_type_fields()` and `schema.get_extraction_fields(...)`. Read each in the external repo and write down its exact contract — including how it normalises a document-type name — before implementing a local equivalent. Do not modify anything in that repo.

- [ ] **Step 2: Write the failing tests**

Cover: field list per document type; document-type name resolution including whatever case or separator handling the original does; monetary and boolean classification; and a four-element diagnostic when the config is missing or malformed.

- [ ] **Step 3: Implement, with fail-fast validation**

- [ ] **Step 4: Run the tests and the gates, then commit**

---

## Task 3: Rewrite the export

**Files:**
- Modify: `generators/eval_set.py`, `generators/pipeline.py`, `config/generation_config.yml`
- Delete: `scripts/relabel_evaluation_set.py`
- Test: `tests/test_eval_set.py`

**Interfaces:**
- Consumes: `eval_projection` from Task 2, and Task 1's format test.

- [ ] **Step 1: Replace the subprocess call with a direct projection**

`generators/eval_set.py` currently shells out to the external script. It should instead: render the corpus, copy clean images into `synthetic_<date>/` and degraded into `degraded_<date>/` under generic names, project each ground-truth entry onto its type's extraction fields, and write `ground_truth.csv` and `ground_truth.jsonl` into **both** directories.

- [ ] **Step 2: Drop `relabel_script` and `relabel_repo_root` from `config/generation_config.yml`**

And from `eval_set.py`'s required-keys validation. Removing a required key means removing its validation too, or the loader rejects a valid config.

- [ ] **Step 3: Delete `scripts/relabel_evaluation_set.py`**

It is a diverged copy of another repo's file that cannot run without that repo. Confirm nothing else imports it first.

- [ ] **Step 4: Export to a scratch directory and assert the format**

Point Task 1's format test at the fresh export. Both directories must match the pinned structure, and their `ground_truth.csv` files must be byte-identical to each other.

- [ ] **Step 5: Run the gates, the full suite, then commit**

---

## Task 4: Produce the datasets and verify

- [ ] **Step 1: Export to `../evaluation_data/`**

Producing `synthetic_<today>/` and `degraded_<today>/`. **Do not delete or overwrite `synthetic_20260731`** — retiring it is the owner's call once the replacement is verified.

- [ ] **Step 2: Verify the products**

165 images in each; filenames identical across both; `ground_truth.csv` byte-identical between them; every `image_file` value resolving to a file that exists in both; the format test green against both.

- [ ] **Step 3: Confirm the images differ between clean and degraded**

Same filename, same ground truth, different pixels — that is the whole point. Assert it rather than assuming the degradation ran.

- [ ] **Step 4: Report what the owner should know**

Sizes, counts, and what to do about `synthetic_20260731`.
