# Synthetic Australian business documents: snapshot $snapshot_date

This directory, `$snapshot_dir/`, is one dated, self-contained snapshot of a synthetic benchmark for
extracting fields from Australian business documents (bank statements, invoices and receipts). Every
document is invented. Its ground truth is authored, not annotated: each value was printed on the page
verbatim from the answer key, so an extraction that disagrees with the answer key misread the page.

It was generated on $snapshot_date from generator commit `$commit`. This README was generated from the
snapshot's `MANIFEST.json`, so its counts cannot disagree with the files.

## 1. Purpose and the four halves

The snapshot holds $image_total images in four halves. The first three render every case of every
document type from identical ground truth under identical filenames; each differs from the one before it
in one respect only, so a paired difference in score measures that one respect.

| Half | What it varies relative to the one before |
| --- | --- |
| `$plain_dir/` | The control: each document as originally designed, with no distractors and with every label (`ABN:`, `GST`, `TOTAL`) printed. |
| `$distractor_dir/` | The same pages with realistic unscored distractors added (section 5). Plain against distractor isolates the distractor effect. |
| `$labelled_varied_dir/` | The distractor pages with the labels beside BUSINESS_ABN, GST_AMOUNT and TOTAL_AMOUNT varied or removed (section 6). Distractor against labelled varied isolates the label effect. |
| `$degraded_dir/` | Each distractor receipt photographed at $tier_count severity tiers: $tier_list. Receipts only, because receipts are the documents people photograph. |

$count_table

Each half carries its own ground truth as `$csv_name` and `$jsonl_name`. The labelled varied half also
carries `label_styles.jsonl` (section 6). `$geometry_dir/` holds the scored fields' bounding boxes for the
three clean halves, one JSON line per image (`plain.jsonl`, `distractor.jsonl`, `labelled_varied.jsonl`),
in pixels of the image as shipped; only scored fields have boxes. `tools/` holds the verifier, the
preflight and the scoring notebook (sections 2 and 7).

## 2. The role of MANIFEST.json

`MANIFEST.json` records what this snapshot is, so that three questions can be answered without the
generator.

**Integrity: is this copy exactly what was generated?** The manifest lists the SHA-256 of every file
except itself and `SHA256SUMS`, which repeats the same list in the form the standard checksum tools read.
From the directory that holds the snapshot:

```sh
cd $snapshot_dir
shasum -a 256 -c SHA256SUMS        # macOS
sha256sum -c SHA256SUMS            # Linux
python tools/verify_snapshot.py    # any platform, Python 3.9 or later, standard library only
```

The checksum tools confirm every listed file. `tools/verify_snapshot.py` also fails on a missing file, an
extra file (a `.DS_Store` is reported as a note and tolerated), a `SHA256SUMS` that disagrees with the
manifest, or an image count that disagrees with the files present. Run it on arrival, before anything
else. Writing into this directory (for example a notebook's figures) makes it fail, which is correct: keep
outputs elsewhere.

The manifest cannot vouch for itself. Its own SHA-256 was printed when the snapshot was made and is
recorded beside the upload; compare it with `shasum -a 256 MANIFEST.json`.

**Provenance: what produced it?** `generator_commit` names the code. `config_sha256` and
`ground_truth_sha256` hash every input file the generator read, and `versions` records the libraries that
shape the pixels ($versions). The input hashes:

$input_table

**Comparability: may two scores be compared?** Only when both come from the same manifest. Two runs on
different snapshots differ in their documents, not only in their models, even where the filenames
coincide. Record the manifest's SHA-256 beside every score.

## 3. Heavy-tier images are not bit-reproducible

The heaviest degradation tier (`_$heavy_suffix` files) includes a paper fold whose noise comes from a
random generator that no seed reaches. Regenerating this snapshot from the same commit gives the same
pixels for the three clean halves and the lighter tiers, but different pixels for every `_$heavy_suffix`
receipt. The images in this snapshot are the record: its manifest hashes exactly what was produced, and
`tools/verify_snapshot.py` checks the snapshot against its own manifest, never against a regeneration.
To compare models on the heavy tier, run them on the same snapshot.

## 4. Fields and formats

The answer key has $field_count fields: 14 for invoices and receipts (the same 14, so a misclassified
document is still asked the right questions) and 5 for bank statements.

$field_table

Conventions:

- `NOT_FOUND` is an answer, not a gap: the field is asked, and the page does not show it (a receipt names
  no payer, so its PAYER_NAME is `NOT_FOUND`). Emitting a value there is a hallucination.
- ABNs are 11 digits grouped 2-3-3-3 with single spaces (`NN NNN NNN NNN`), and every ABN passes the
  official ABN checksum.
- Amounts carry a leading `$$` and two decimals with no thousands separator, for example `$$4086.15`.
- Dates are `DD/MM/YYYY`, for example `14/02/2031`.
- Booleans are lowercase `true` or `false`.
- List fields join their items with ` | ` (space, pipe, space), and parallel lists (descriptions,
  quantities, prices, totals; dates and amounts) have the same number of items, in printed order.
- Bank statements are scored on debits only.

## 5. Distractor and overlay policy

Distractors are unscored text and marks a real document carries: on receipts, merchant and terminal
numbers, website and loyalty lines, QR codes and survey codes; on invoices, contact and licence lines,
site addresses, payment blocks, offers, remittance slips and a PAID stamp; on bank statements, barcodes
and a taxpayer's annotations (highlighter, ticks, circled amounts and handwritten notes and sums). Some
are deliberate near-misses: labelled, same-type lookalikes of a scored field, such as a due date beside the
invoice date. A near-miss never equals or contains a scored value.

What never happens: no stamp and no opaque ink (ticks, circles, underlines, handwriting) touches a scored
value; this is verified on the drawn pixels. Highlighter may cover a scored row, but it is translucent and
each highlighted value is contrast-checked so that it stays legible. Every distractor and labelled varied
render was checked against density bounds; the plain renders are the control and are measured only.

## 6. Label variation and the sidecar

In the labelled varied half, each receipt and invoice draws one style for each of BUSINESS_ABN,
GST_AMOUNT and TOTAL_AMOUNT: `labelled` (the original label), `variant` (another wording a real document
uses) or `unlabelled` (the value alone). The value itself is never changed, so the ground truth is the
same as in the other clean halves. An unlabelled value is still unambiguous to a careful reader: no other
checksum-valid ABN on the page, the GST figure sits in the totals block above the total, and the total is
the last and most prominent amount there. Bank statements print none of these fields, so their labelled
varied pages equal their distractor pages.

`label_styles.jsonl` has one JSON line per image, sorted by `image_file`: `styles` maps each varied field
to its `style` and the `wording` printed (empty for a bank statement), and `refusals` lists any style
refused by the unambiguity rules before the one drawn. It is metadata, never ground truth, and is not
given to the model. To stratify, join it to your per-field scores on `image_file` and group by
`styles.<FIELD>.style`. The realised counts:

$style_table

## 7. Pairing, joining, the preflight and the notebook

- **Clean halves:** join directly on the filename, `CASE001_invoice.png` and so on, which is the same in
  all three clean halves; their ground-truth files are byte-identical. Filenames name the case and the
  document type, never the visual template.
- **Degraded half:** filenames carry the tier suffix (`CASE001_receipt_$light_suffix.png`). Strip the
  suffix to pair a degraded receipt with its clean counterparts. The degraded half's own ground truth
  repeats the source receipt's values under each tier's filename.
- **Give the model the images only.** The ground truth, the sidecar and the geometry stay with the scorer.
- **Preflight:** before scoring, check that the extractions and the answer key describe the same images.
  A run paired with the wrong ground truth still produces a plausible score from whatever filenames
  happen to overlap.

```sh
python tools/check_extractions_match_gt.py \
    --extractions  path/to/raw_extractions.jsonl \
    --ground-truth $plain_dir/$csv_name
```

  Use the ground truth of the half the run was given. The check exits 1 unless every extracted image
  has ground truth (`--allow-partial` for a deliberate subset).
- **Scoring:** `tools/score_extractions.ipynb` reports per-field F1, a field by document type heatmap and
  an optional paired comparison of two runs. It needs pandas, matplotlib and seaborn. Its outputs are
  cleared. Set `RAW_PATH` and `GT_PATH` (and `RAW_PATH_B`, `GT_PATH_B` for a comparison) in its
  configuration cell, and set `FIGURE_DIR`, which defaults to `figures/` under the working directory,
  to a directory outside this snapshot.

## 8. Changes and comparability

$changes

These notes hold for every snapshot from this generator:

- The control half was once called `synthetic_`; it is now `plain_`, since every half is synthetic.
- Two invoice rendering defects were fixed in the plain control: a 'Total' label that overlapped its
  amount, and line-total amounts that overran the right margin.
- **No score from an export made before the first manifested snapshot is comparable with a score on
  this snapshot.** Before that export, bank statements, receipt unit prices and one bank layout's closing balance
  changed, and every receipt and invoice was regenerated when the invented ABNs were screened so that none
  belongs to a registered business. Re-run any earlier model on this snapshot rather than reuse its score.
