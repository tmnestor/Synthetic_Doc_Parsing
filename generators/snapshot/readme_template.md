# Synthetic Australian business documents: snapshot $snapshot_date

This directory, `$snapshot_dir/`, is a self-contained benchmark for reading named fields off Australian
business documents: bank statements, invoices and receipts. It holds $image_total page images, in four
halves and two typeset sets, the $pdf_total native PDFs the typeset sets' images are pages of, the answer
key for every image, and the tools to verify the download and score a model.

**Every page is synthetic.** No real document, and no real person's or business's data, appears here.
Every name, address, ABN, line item and transaction was invented, and every ABN was checked so that none
belongs to a registered business. All of it is safe to send to a hosted model.

The answer key is authored, not annotated: each value was printed on the page verbatim from the answer
key, so an extraction that disagrees with it misread the page.

## 1. What is here

| Path | What it holds |
| --- | --- |
| `$plain_dir/` | The control: each document with no distractors and with every label (`ABN:`, `GST`, `TOTAL`) printed. |
| `$distractor_dir/` | The same pages with realistic unscored distractors added (section 3). Plain against distractor isolates the distractor effect. |
| `$labelled_varied_dir/` | The distractor pages with the labels beside BUSINESS_ABN, GST_AMOUNT and TOTAL_AMOUNT varied or removed (section 4). Distractor against labelled varied isolates the label effect. |
| `$degraded_dir/` | Each distractor receipt photographed at $tier_count severity tiers: $tier_list. Receipts only, because receipts are the documents people photograph; statements and invoices arrive as clean PDFs or printouts. |
| `$typeset_plain_dir/` | The plain half's documents as native PDFs with a text layer, each beside a PNG of its page under the same name (`CASE001_invoice.pdf`, `CASE001_invoice.png`), section 9. |
| `$typeset_distractor_dir/` | The distractor half's documents, distractors and annotations included, as native PDFs beside their PNGs in the same way. Typeset plain against typeset distractor isolates the distractor effect on a native PDF. |
| `$geometry_dir/` | The bounding box of every scored field on the three clean halves, one JSON line per image. |
| `$parsing_dir/` | Parsing ground truth: every image of every half and typeset set as an OmniDocBench page (section 8). |
| `tools/` | A verifier, a preflight check and a scoring notebook (sections 5 and 6). |
| `MANIFEST.json`, `SHA256SUMS` | What the snapshot holds, and the SHA-256 of every file (section 6). |

$count_table

Each half and typeset set carries its own answer key twice, as `$csv_name` and as `$jsonl_name`; the
two hold the same rows and score identically. The labelled varied half also carries `label_styles.jsonl`
(section 4).

The first three halves render the same $clean_total documents from the same answer key under the same
filenames, and each differs from the one before it in one respect only, so a paired difference in score
measures that one respect. Their answer-key files are byte-identical. The two typeset sets hold the same
documents again, under the same filenames, with the same answer-key files byte for byte (section 9).

## 2. Fields and formats

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

## 3. Distractors and annotations

Distractors are unscored text and marks a real document carries: on receipts, merchant and terminal
numbers, website and loyalty lines, QR codes and survey codes; on invoices, contact and licence lines,
site addresses, payment blocks, offers, remittance slips and a PAID stamp; on bank statements, barcodes
and the account holder's annotations (highlighter, ticks, circled amounts and handwritten notes and
sums). Some are deliberate near-misses: labelled, same-type lookalikes of a scored field, such as a due
date beside the invoice date. A near-miss never equals or contains a scored value.

What never happens: no stamp and no opaque ink (ticks, circles, underlines, handwriting) touches a scored
value; this was checked on the drawn pixels. Highlighter may cover a scored row, but it is translucent and
each highlighted value was contrast-checked so that it stays legible.

## 4. Label variation and the sidecar

In the labelled varied half, each receipt and invoice draws one style for each of BUSINESS_ABN,
GST_AMOUNT and TOTAL_AMOUNT: `labelled` (the original label), `variant` (another wording a real document
uses) or `unlabelled` (the value alone). The value itself is never changed, so the answer key is the same
as in the other clean halves. An unlabelled value is still unambiguous to a careful reader: no other
checksum-valid ABN on the page, the GST figure sits in the totals block above the total, and the total is
the last and most prominent amount there. Bank statements print none of these fields, so their labelled
varied pages equal their distractor pages.

`label_styles.jsonl` has one JSON line per image, sorted by `image_file`: `styles` maps each varied field
to its `style` and the `wording` printed (empty for a bank statement), and `refusals` lists any style
refused by the unambiguity rules before the one drawn; `layout` is the page's opaque layout key, the same
one its parsing page carries (section 8). It is metadata, never answer key, and is not given
to the model. To stratify, join it to your per-field scores on `image_file` and group by
`styles.<FIELD>.style`. The realised counts:

$style_table

## 5. Running a model and scoring it

- **Give the model the images only** (or, in a typeset set, the PDFs only: section 9). The answer key,
  the sidecar, the geometry and the parsing pages stay with the scorer. Ask for the fields of section 2,
  in the formats of section 2.
- **Clean halves and typeset sets:** join on the filename, `CASE001_invoice.png` and so on, which is the
  same in all three clean halves and both typeset sets (a typeset PDF has the same name ending `.pdf`).
  Filenames name the case and the document type, never the visual template.
- **Degraded half:** filenames carry the tier suffix (`CASE001_receipt_$light_suffix.png`). Strip the
  suffix to pair a degraded receipt with the page that was photographed, the receipt of the same name in
  `$distractor_dir/`. The degraded half's own answer key repeats that receipt's values under each tier's
  filename. Compare a tier with that clean receipt, document by document; never compare the degraded
  half's overall score with a clean half's, since one holds receipts only and the other all three types.
- **Predictions** are read as JSON Lines, one object per image: `image_name` (the image's filename),
  `document_type` (the type the model assigned) and `raw_response` (the model's text, either a JSON
  object of field to value or one `FIELD: value` line per field). A record carrying a non-empty `error`
  is skipped and reported.
- **Preflight:** before scoring, check that the predictions and the answer key describe the same images.
  A run paired with the wrong answer key still produces a plausible score from whatever filenames happen
  to overlap.

```sh
python tools/check_extractions_match_gt.py \
    --extractions  path/to/raw_extractions.jsonl \
    --ground-truth $plain_dir/$csv_name
```

  Use the answer key of the half the run was given. The check exits 1 unless every predicted image has
  an answer (`--allow-partial` for a deliberate subset).
- **Scoring:** `tools/score_extractions.ipynb` reports per-field F1, a field by document type heatmap and
  an optional paired comparison of two runs, such as a clean half against the degraded half. It needs
  pandas, matplotlib and seaborn, and its outputs are cleared. Set `RAW_PATH` and `GT_PATH` (and
  `RAW_PATH_B`, `GT_PATH_B` for a comparison) in its configuration cell, and set `FIGURE_DIR`, which
  defaults to `figures/` under the working directory, to a directory outside this snapshot.

## 6. Verifying what you downloaded

`MANIFEST.json` lists the SHA-256 of every file except itself and `SHA256SUMS`, which repeats the same
list in the form the standard checksum tools read. From the directory that holds the snapshot:

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
outputs elsewhere. The manifest cannot vouch for itself; whoever gave you access can give you its
SHA-256 to compare with `shasum -a 256 MANIFEST.json`.

## 7. Comparing scores

Two scores are comparable only when both were computed on this snapshot. Another snapshot holds
different documents, not merely a different copy of these, even where its filenames coincide with these.
Record the snapshot's name, `$snapshot_dir`, beside every score, and re-run a model on this snapshot
rather than reuse a score from another.

## 8. Parsing ground truth

`$parsing_dir/` holds a second answer key, for parsing a whole page rather than reading named fields:
$parsing_files, one per half and typeset set. Each is a JSON list in the OmniDocBench ground-truth
format, one page per image of its half or set, sorted by `page_info.image_path`. That path is the image's
path relative to this directory (for example `$plain_dir/CASE001_invoice.png`), so the files hold
wherever the snapshot is unpacked. Each page lists its blocks under `layout_dets`: category, polygon,
reading order, and the text (a table as HTML). `page_info.page_attribute` names the page's document
type, its layout as an opaque key (`invoice_layout_2` and so on, the same key for every page drawn from
one layout), its half or set, its degradation tier (`none` on a clean half or typeset set) and
`layout_scored`. The layout keys are stable within this
snapshot only: they renumber when a layout is added, so do not compare them across snapshots.

Score parsing output with the OmniDocBench evaluator, $evaluator_repository, at commit
`$evaluator_commit`, whose format these pages follow. The metrics that apply are text edit distance,
table TEDS (with table edit distance beside it) and reading-order edit distance. No page carries a
formula, so no formula metric applies, and OmniDocBench's overall score, which includes one, is not
reported.

Every page of the three clean halves and both typeset sets counts for layout detection (its
`layout_scored` is true); in `$degraded_dir/`, $layout_tiers.$excluded_tiers

## 9. The typeset sets

`$typeset_plain_dir/` and `$typeset_distractor_dir/` hold the documents of `$plain_dir/` and
`$distractor_dir/` once more, each typeset as a one-page native PDF: its words are text, not pixels, so
the PDF carries a text layer, as a document exported from accounting or banking software does. Beside each
PDF is the PNG of its page at $raster_dpi dots per inch, under the same name: `CASE001_invoice.pdf` and
`CASE001_invoice.png`. Give a model that reads PDFs the PDF, and one that reads images the PNG. The two
are the same page, so their scores pair directly, and a tool's score on the PDFs against its score on the
PNGs measures what reading the text layer is worth to it.

A typeset page has the same values, blocks and reading order as the page of the same name in
`$plain_dir/` or `$distractor_dir/`, each line of text at the same size, and the same answer key. It is
set in type rather than drawn, so glyph shapes and spacing differ slightly: compare a typeset set's scores
with its image half's by filename, never pixel by pixel.

The text layer holds the printed text of the page and nothing else. In `$typeset_distractor_dir/` the
annotations (highlighter, ticks, circled amounts, handwritten notes and sums, and stamps) are images placed
on the page, so their text is not in the text layer; it is in the parsing ground truth, as for
`$distractor_dir/`.

A typeset set's parsing page names the PNG as its `page_info.image_path`, and its polygons are in that
PNG's pixels. The PDF of the same name is the same page: divide a polygon by $raster_dpi/72 to place it
in the PDF's points, measured from the page's top-left corner.
