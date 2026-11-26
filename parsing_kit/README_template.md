# Document parsing test kit: synthetic Australian business documents ({{SNAPSHOT_DATE}})

This folder, `{{KIT_DIR}}/`, is everything needed to test a document parser on bank statements, tax
invoices and receipts, and to score its output:

- **990 page images** in six sets, and for two of the sets the **330 native PDFs** the images are pages of;
- the **parsing ground truth** for every page: each block of text, its position, its category and its
  place in the reading order, and every table cell by cell;
- a **scoring notebook** that runs the OmniDocBench evaluator on your parser's output and breaks the
  scores down, with the corrections that evaluator needs on documents like these.

**Every page is synthetic.** No real document and no real person's or business's data appears here.
Every name, address, ABN, line item and transaction was invented, and every ABN was checked so that none
belongs to a registered business. It is safe to send any page to a hosted model.

The ground truth is authored, not annotated. It was recorded by the code that drew each page, at the
moment it drew it, so there is no annotation error to allow for: where your parser and the ground truth
disagree about what is printed, the parser misread the page.

This kit is cut from the evaluation snapshot `{{SNAPSHOT}}`. Quote that name beside every score you
report (section 10).

## Contents

1. [Quick start](#1-quick-start)
2. [What is in the folder](#2-what-is-in-the-folder)
3. [The pages](#3-the-pages)
4. [The ground truth](#4-the-ground-truth)
5. [What your parser must write](#5-what-your-parser-must-write)
6. [Installing the evaluator](#6-installing-the-evaluator)
7. [The corrections made to the evaluator, and why](#7-the-corrections-made-to-the-evaluator-and-why)
8. [Running the scoring notebook](#8-running-the-scoring-notebook)
9. [Reading the scores](#9-reading-the-scores)
10. [Rules for a fair run](#10-rules-for-a-fair-run)
11. [Verifying the download](#11-verifying-the-download)
12. [Limits of this kit](#12-limits-of-this-kit)
13. [Troubleshooting](#13-troubleshooting)

## 1. Quick start

1. Check the download is whole: `python tools/verify_snapshot.py` (section 11).
2. Run your parser over the images of one set, say `plain_{{DATE_TAG}}/`, and write one markdown file per
   page into a folder of your own: `CASE001_invoice.md` for `CASE001_invoice.png` (section 5).
3. Install the evaluator once (section 6).
4. Copy `tools/score_parsing.ipynb` to a folder of your own, open the copy, set the paths in its first
   code cell, and run it from top to bottom (section 8).

Start with the `plain` set. It is the easiest, and every other set is a variation on it.

## 2. What is in the folder

| Path | What it holds |
| --- | --- |
| `README.md` | This guide. |
| `plain_{{DATE_TAG}}/` | 165 page images (PNG): each document as laid out, with nothing added. |
| `distractor_{{DATE_TAG}}/` | The same 165 pages with realistic clutter added. |
| `labelled_varied_{{DATE_TAG}}/` | The distractor pages with the wording of three labels varied or removed. |
| `degraded_{{DATE_TAG}}/` | 165 photographs: each of the 55 distractor receipts at three severities. |
| `typeset_plain_{{DATE_TAG}}/` | The plain documents again as native PDFs with a text layer, each beside a PNG of its page. |
| `typeset_distractor_{{DATE_TAG}}/` | The distractor documents as native PDFs, each beside its PNG. |
| `parsing/` | The ground truth: one JSON file per set (section 4). |
| `tools/score_parsing.ipynb` | The scoring notebook (section 8). |
| `tools/omnidocbench_formula_free.py`, `tools/omnidocbench_corrected.py`, `tools/pipe_tables.py` | The launcher the notebook runs the evaluator through, and the two files it uses. They correct the evaluator in memory (section 7). Keep the three together. |
| `tools/omnidocbench_env.yml` | The conda environment file for the evaluator (section 6). |
| `tools/verify_snapshot.py` | Checks every file against the manifest (section 11). |
| `MANIFEST.json`, `SHA256SUMS` | What the kit holds, and the SHA-256 of every file. |

Nothing here needs editing, and nothing should be written into this folder: the verifier fails on any
file it does not expect. Keep your parser's output and the scores somewhere else.

## 3. The pages

### The documents

There are 165 documents: 55 bank statements, 55 tax invoices and 55 receipts, all single pages, all in
English, all Australian in their conventions (ABNs, GST, `DD/MM/YYYY` dates, dollar amounts).

| Document type | Documents | Distinct layouts | What a page is like |
| --- | --- | --- | --- |
| Bank statement | 55 | 8 | A bank's heading, the account holder and statement period, then one long table of transactions (date, description, withdrawal, deposit, balance), sometimes with a totals block. Nearly all of the page is table. |
| Tax invoice | 55 | 8 | A supplier's letterhead, the customer, the invoice number and dates, a table of line items, then subtotal, GST and total. A mixture of text and table. |
| Receipt | 55 | 6 | A narrow till receipt: store name and address, a short table of items, totals, payment details. Mostly short lines of text. |

File names are generic: `CASE001_bank_statement.png`, `CASE001_invoice.png`, `CASE001_receipt.png`, up to
`CASE055_...`. Each page stands alone for parsing. A file name tells you the case and the document type
and never the visual layout.

### The six sets

| Set (its folder, and its name in `parsing/` and in the notebook) | Pages | By type | What is on the pages |
| --- | --- | --- | --- |
{{SETS_TABLE}}

The first three sets hold **the same 165 documents under the same file names**, and each differs from
the one before it in one respect only. So a difference in score between two of them, taken page by page,
measures that one respect:

- **`plain` against `distractor`**: what clutter costs your parser.
- **`distractor` against `labelled_varied`**: whether your parser reads the values or leans on familiar
  label wording. (Bank statements print none of the varied labels, so their labelled-varied pages are
  the same as their distractor pages.)
- **`degraded` against the `distractor` receipts**: what a phone photograph costs (section 9 says how to
  pair them).
- **a typeset set against its image set** (`typeset_plain` against `plain`): what a native PDF's text
  layer is worth to your parser.

### What the distractors are

Distractors are text and marks a real document carries that are not its main content:

- on **receipts**: merchant and terminal numbers, website and loyalty lines, QR codes and survey codes;
- on **invoices**: contact and licence lines, site addresses, payment blocks, offers, a tear-off
  remittance slip with a barcode, and a PAID stamp;
- on **bank statements**: barcodes, and the account holder's own annotations: highlighter, ticks,
  circled amounts, and handwritten notes and sums.

All of it is part of the page, so all of it is in the parsing ground truth: a parser is expected to
transcribe a handwritten note or a stamp as text, and to leave a barcode or a tick as a mark with no
text (section 4).

### The photographed receipts

`degraded_{{DATE_TAG}}/` holds each of the 55 distractor receipts photographed at three severities. The
file name carries the tier: `CASE001_receipt_v1.png` (light), `_v2.png` (moderate), `_v3.png` (heavy).
Light is nearly flat: up to 3 degrees of rotation, a lighting gradient and mild blur. Moderate turns the
receipt up to 8 degrees and adds a cast shadow, stronger blur and heavier JPEG compression. Heavy is the
hardest and adds a fold across the paper; it is meant to be hard to read, never impossible. Receipts
only, because receipts are the documents people photograph: statements and invoices arrive as clean PDFs
or printouts.

On four pages a photograph crops a line or two off the receipt. Those lines are marked `ignore` in the
ground truth and are not scored (section 4).

### The typeset sets and their PDFs

`typeset_plain_{{DATE_TAG}}/` and `typeset_distractor_{{DATE_TAG}}/` hold the documents of the plain and
distractor sets once more, each typeset as a one-page **native PDF**: its words are text, not pixels, so
the PDF has a text layer, as a document exported from accounting or banking software does. Beside each
PDF is a PNG of the same page at {{TYPESET_DPI}} dots per inch, under the same name
(`CASE001_invoice.pdf`, `CASE001_invoice.png`).

- Give a parser that reads PDFs the PDF, and one that reads images the PNG. Both are the same page and
  are scored against the same ground truth, under the same markdown file name (`CASE001_invoice.md`).
  To score both, write the two runs into two folders and score each.
- A typeset page has the same text, blocks, tables and reading order as the page of the same name in the
  plain or distractor set. It is set in type rather than drawn, so glyph shapes and spacing differ a
  little: compare the two by score, never pixel by pixel.
- In `typeset_distractor`, the annotations (highlighter, ticks, circled amounts, handwriting, stamps)
  are images placed on the page. Their text is **not** in the PDF's text layer, but it **is** in the
  ground truth, exactly as for the distractor images.

### Page sizes

Sizes are in pixels. The statements and invoices are full pages; the receipts are narrow strips.

| Set | Document type | Width | Height |
| --- | --- | --- | --- |
{{SIZES_TABLE}}

## 4. The ground truth

`parsing/` holds one file per set: `plain.json`, `distractor.json`, `labelled_varied.json`,
`degraded.json`, `typeset_plain.json` and `typeset_distractor.json`. Each is a JSON list in the
**OmniDocBench ground-truth format**, one entry per page, sorted by image path.

### One page

The example below shows the shape of a page entry. Its content is invented for this guide and is not a
page of the kit.

```json
{
  "page_info": {
    "page_no": 0,
    "width": 1900,
    "height": 3508,
    "image_path": "plain_{{DATE_TAG}}/CASE999_invoice.png",
    "page_attribute": {
      "language": "en",
      "doc_type": "invoice",
      "layout_id": "invoice_layout_3",
      "half": "plain",
      "tier": "none",
      "layout_scored": true
    }
  },
  "layout_dets": [
    {
      "category_type": "text_block",
      "poly": [120, 96, 880, 96, 880, 140, 120, 140],
      "ignore": false,
      "order": 1,
      "anno_id": 0,
      "attribute": {},
      "line_with_spans": [],
      "text": "Quillfeather Joinery Works"
    },
    {
      "category_type": "table",
      "poly": [120, 900, 1780, 900, 1780, 1420, 120, 1420],
      "ignore": false,
      "order": 2,
      "anno_id": 1,
      "attribute": {},
      "line_with_spans": [],
      "html": "<table><tr><td>Part</td><td>Count</td><td>Each</td></tr><tr><td>Oak dowel 9mm</td><td>3 off</td><td>$4.15</td></tr></table>"
    }
  ],
  "extra": {"relation": []}
}
```

### The fields of `page_info`

| Field | Meaning |
| --- | --- |
| `image_path` | The page's image, relative to this folder. For a typeset set it names the PNG; the PDF has the same name ending `.pdf`. |
| `width`, `height` | The image's size in pixels. Every polygon on the page is in these pixels. |
| `page_no` | Always 0: every document is one page. |
| `page_attribute.doc_type` | `bank_statement`, `invoice` or `receipt`. |
| `page_attribute.layout_id` | The page's visual layout as an opaque key, such as `invoice_layout_3`. Pages with the same key were drawn from the same layout. Use it to group scores; it says nothing else. |
| `page_attribute.half` | The set the page belongs to: `plain`, `distractor` and so on. |
| `page_attribute.tier` | `light`, `moderate` or `heavy` on a photographed receipt, `none` everywhere else. |
| `page_attribute.layout_scored` | Whether the page's polygons are exact enough to score layout detection against. True everywhere except the heavy tier, whose fold moves ink further than a polygon can follow. It does not affect the text, table or reading-order scores. |
| `page_attribute.language` | Always `en`. |

### The fields of a block (an entry of `layout_dets`)

| Field | Meaning |
| --- | --- |
| `category_type` | What kind of block it is (the table below). |
| `poly` | The block's outline: four corners as `[x1, y1, x2, y2, x3, y3, x4, y4]`, clockwise from the top left, in image pixels. On a photographed receipt the corners follow the photograph, so the outline is a tilted quadrilateral with fractional coordinates. |
| `order` | The block's place in the reading order of the page, counting from 1. |
| `text` | The block's text, exactly as printed. Present on every block that carries text other than a table. |
| `html` | A table, as html. Present on `table` blocks only. |
| `ignore` | True on a block a photograph cropped. An ignored block is not scored; six blocks on four pages of the degraded set are ignored and none anywhere else. |
| `anno_id` | The block's number within the page, counting from 0. |
| `attribute`, `line_with_spans` | Always empty here. They are part of the OmniDocBench format. |

### The block categories

| `category_type` | What it is here | Has text? | Scored for text? |
| --- | --- | --- | --- |
| `title` | The banner heading of an invoice, which names the document type. | Yes | Yes |
| `text_block` | Every other piece of printed text: names, addresses, labels with their values, totals, payment lines, terms, and also stamps and handwritten notes. | Yes | Yes |
| `table` | A table: the transactions of a statement, the line items of an invoice or receipt. | As `html` | Yes, as a table |
| `header` | The bank's name printed as a running header at the top of some statements. | Yes | No: the evaluator sets headers aside |
| `footer` | A line of running furniture at the foot of a page: a page number, a thank-you line on an invoice, a notice or an offer from the bank on a statement. | Yes | No: the evaluator sets footers aside |
| `figure` | A logo, a barcode or a QR code. Located, never transcribed. | No | No |
| `abandon` | Ink that is not text: highlighter, ticks, circles. Located, never transcribed. | No | No |

How many blocks of each category each set holds:

{{BLOCKS_TABLE}}

An average page, by document type (plain set, then distractor set):

{{DENSITY_TABLE}}

### How text is recorded

- **A block is usually one printed line.** A label and its value on one line are one block
  (`Docket No:  QJ-00417`), with the gap between them written as spaces. The evaluator is built for
  this: it will pair one paragraph of your output with several consecutive ground-truth lines, or the
  reverse (section 9).
- **Text is exactly what is printed**, character for character, including currency signs, thousands
  separators and letter case.
- **Everything printed is recorded.** Every line of ink on a page belongs to a block: the export refuses
  a page where one does not. An optical check of 495 pages with every block masked found no text left
  over.

### How tables are recorded

- A table is `<table>`, then one `<tr>` per row, then one `<td>` per cell, on a single line, with no
  other tags and no attributes except `colspan`.
- The header row is the first `<tr>`. It uses `<td>`, not `<th>`.
- An empty cell is `<td></td>`.
- 21 bank statements in each clean set print each day's date as a row of its own across the whole
  table. That row is one cell with `colspan`, for example `<td colspan="5">`. No other table has a
  spanning cell, and no table has `rowspan`.
- Cell text is exactly what is printed. An ampersand is written `&amp;`.

### Reading order

`order` numbers the blocks of a page from 1 in the order a person would read them. The evaluator scores
reading order over titles,
text blocks and tables; headers, footers, figures and marks take a number but are not part of the score.

### Polygons and the PDFs

A typeset set's ground truth names the PNG and gives polygons in the PNG's pixels. To place a polygon on
the PDF of the same name, divide by {{TYPESET_DPI}}/72: PDF points are measured from the page's top-left
corner here.

The notebook does not score layout detection (whether your parser's boxes match the polygons). The
polygons are here so that you can, with OmniDocBench's layout evaluation or your own.

## 5. What your parser must write

### One markdown file per page

For each image of the set you are scoring, write one UTF-8 text file whose name is the image's name with
`.md` in place of `.png`: `CASE001_bank_statement.md`, `CASE001_receipt_v2.md`. Put all the files of one
run in one folder. This is the form the OmniDocBench evaluator reads. A PDF's output takes the same name
(`CASE001_invoice.md`).

The file holds the page as markdown, **in reading order**. For example (invented content):

```markdown
# WORKSHOP DOCKET

Quillfeather Joinery Works

14 Harrowgate Mews, Wombat Flat TAS 7999

Docket No:  QJ-00417

| Part | Count | Each |
|---|---|---|
| Oak dowel 9mm | 3 off | $4.15 |
| Brass hinge pair | 1 off | $11.80 |

Docket sum:  $24.25

Made to measure since the flood
```

### The rules

**Text**

- Separate paragraphs with a blank line. Each paragraph is compared with the ground-truth line or lines
  it matches best, so it does not matter whether your parser writes an address as three paragraphs or
  one.
- A heading may be written with `#` or as plain text; the marker is not scored. Bold and italic markers
  around text (`**Docket sum**`) are not scored either.
- Write text as plain markdown, **not as html**: a paragraph wrapped in `<p>...</p>` is charged for the
  tags.
- Do not wrap text in LaTeX delimiters (`\[ \]`, `$$ $$`, `\( \)`). These documents hold no formula, and
  text inside display delimiters is taken out of the page and not scored as text. A plain dollar sign
  before an amount is fine and needs no escaping.
- Image links (`![logo](logo.png)`) are ignored. A whole file wrapped in a ```` ```markdown ```` fence is
  read as if the fence were not there.

**Tables**

- Write a table **either as html or as a markdown pipe table**. The two score identically for the same
  content: the scorer rewrites a pipe table as html before the evaluator sees it (section 7).
- A pipe table needs a header row and a delimiter row (`|---|---|`). Outer pipes are optional, alignment
  colons are allowed, and a pipe inside a cell must be written `\|`. Leave a blank line after the table.
- A pipe table cannot say that a cell spans several columns. If your parser can detect a spanning cell,
  write that table as html with `colspan`; if it cannot, the date rows of the 21 statements that have
  them (section 4) will cost a little table score, which is a real difference between the two outputs.
- An html table may use `<th>`, `<thead>` and `<tbody>`, attributes on `<table>`, and line breaks
  between rows; none of these is scored.
- **Write each cell as plain text.** Inside a table cell, unlike in a paragraph, everything is compared
  as written: bold or italic markup (`<b>Each</b>`, `**Each**`), letter case and punctuation all count.
  A table that is right but has bold header cells loses table score. Strip emphasis from cells when you
  export. Extra spaces inside a cell do not count.

**What to leave out, and what does not matter**

- Logos, barcodes, QR codes, ticks and highlighter are not text. Write nothing for them.
- Page headers and footers may be written or left out: the evaluator sets them aside either way.
- If a page has no output, write an empty file or no file. Either is scored as a wholly wrong page; the
  notebook asks you to confirm before it scores a set with files missing.

### How the output is compared, in one paragraph

Paragraph text is compared on **letters, digits and underscores only**: spaces and punctuation are
removed from both sides first, and letter case is kept. Table cells are compared in full. Section 9
explains each score and what follows from these two facts.

## 6. Installing the evaluator

The scores are computed by the **OmniDocBench evaluator**, an open-source tool
(https://github.com/opendatalab/OmniDocBench, Apache-2.0). It is not included here. Install it once:

```sh
# 1. Download the evaluator's source at exactly this commit, and unpack it as a folder named OmniDocBench.
curl -L -o omnidocbench.zip https://github.com/opendatalab/OmniDocBench/archive/{{EVALUATOR_COMMIT}}.zip
unzip omnidocbench.zip
mv OmniDocBench-{{EVALUATOR_COMMIT}} OmniDocBench

# 2. Build its environment and install it there.
conda env create -n omnidocbench -f {{KIT_DIR}}/tools/omnidocbench_env.yml
conda run -n omnidocbench pip install -e OmniDocBench

# 3. Make the environment available to Jupyter as a kernel.
conda run -n omnidocbench python -m ipykernel install --user --name omnidocbench
```

Then open the notebook with the `omnidocbench` kernel.

- **The commit matters.** The ground truth follows that commit's format, and the corrections in section 7
  were written for and checked against that commit. `MANIFEST.json` lists the SHA-256 of each of the
  evaluator's source files at that commit; the notebook's preflight compares your copy with that list and
  refuses any other, and the launcher refuses evaluator code that is not what it expects.
- **Python 3.10 or 3.11.** The evaluator pins its own package versions (pandas 2.0.3, numpy 1.24.4 and
  others), which is why it has an environment of its own. Do not install it into the environment your
  parser runs in.
- **If you would rather keep your usual notebook kernel**, skip step 3 and set `EVALUATOR_PYTHON` in the
  notebook to the environment's interpreter (for example `/opt/conda/envs/omnidocbench/bin/python`). Your
  kernel then needs only pandas.
- **No GPU and no model are needed.** The scores used here are pure computation. The notebook switches
  Hugging Face access off for the run, so the evaluator downloads nothing. Formula scoring, which would
  need TeX, is off.
- Steps 1 and 2 need access to github.com and to a Python package index. If the machine has neither, do
  them on one that has and copy the `OmniDocBench` folder and the environment across.

## 7. The corrections made to the evaluator, and why

The OmniDocBench evaluator was written for papers, books and reports. On business documents, which are
tables of dollar amounts with no formulas, four of its behaviours give a wrong score. They were found by
a simple test: **write the ground truth itself back as markdown and score it; a perfect transcription
must get a perfect score.** With the evaluator as published, it does not.

The notebook therefore runs the evaluator through `tools/omnidocbench_formula_free.py`. That launcher
puts the four faults right in memory, as the evaluator starts, and then runs the evaluator's own code.
**The evaluator's files are never changed**, and nothing about the metrics themselves is altered: the
edit distances and TEDS are the evaluator's own.

| # | The fault in the evaluator as published | What it costs | The correction |
| --- | --- | --- | --- |
| 1 | The first table of a page drops out of the predicted reading order (a matched table at position 0 is read as unmatched). It is reported upstream as issue #229. | One reading-order edit on every page with a table, which here is every page, even for a perfect transcription. | The two faulty conditions in the table matcher are replaced, as upstream pull request #230 proposes. |
| 2 | A markdown pipe table is misread: a cell holding a colon is dropped, the row after one ending in a space is lost, a table without outer pipes is not seen as a table, and the table's html comes back with attributes an html table lacks. | A correct pipe table loses table score, and its table edit distance is not comparable with an html table's. | Each pipe table is rewritten as a plain one-line html table before the evaluator reads the page (`tools/pipe_tables.py`). |
| 3 | Text between two dollar signs is taken for a formula, and so is any table cell that looks like one, such as a date. When a transcription leaves a line out, the evaluator pairs the missing line with one of these fragments and charges for the fragment's length. | A missing line is charged several times over, by an amount that depends on unrelated text. | Every formula item the evaluator makes from a parser's output is dropped. This is right because no page here holds a formula; the launcher refuses ground truth that holds one. |
| 4 | A run of underscores or spaces in a parser's output is cut to four characters, and the same run in the ground truth is not. | A correctly transcribed line that ends in a rule to write on (a label followed by a row of underscores) is charged for its underscores. | A parser's repeated characters are left as written. |

### The evidence

The test was run on every page of all six sets on {{CHECK_DATE}}, with each page written back in the two
forms a parser writes tables in. "As published" is the evaluator unmodified; "corrected" is the launcher
in this kit. A complete transcription should score 0, 1, 0 and 0.

{{CHECK_COMPLETE_TABLE}}

Then one line of text was left out of every page, the line in the middle of the page. A fair scorer
charges that page exactly the characters of the missing line, no more and no fewer. The table counts the
pages where it does. As published, the evaluator pairs the missing line with a fragment of unrelated text
on most pages and charges for the fragment: on one receipt, 90 characters for a missing line of 12.

{{CHECK_LEFT_OUT_TABLE}}

You can repeat the first test on your own installation at any time: it is section 4 of the notebook.

### What this means for comparing scores

Scores from this kit's launcher are **not comparable with published OmniDocBench figures**, or with
figures anyone computed on these pages with the evaluator unmodified: the reading-order scores differ on
every page, and the others differ wherever faults 2 to 4 bit. Compare a score from this kit only with
another score from this kit.

## 8. Running the scoring notebook

**Copy `tools/score_parsing.ipynb` out of the kit and work on the copy.** Saving a notebook changes it,
and Jupyter leaves a checkpoint folder beside it; either would make the kit's verifier fail. The copy
finds the kit through its `KIT` setting. Run it from the top; only the first code cell needs editing.

### The settings (section 1 of the notebook)

| Setting | What to give |
| --- | --- |
| `KIT` | This folder: the one that holds `README.md`, `MANIFEST.json`, `parsing/` and `tools/`. |
| `SET` | The set to score: `plain`, `distractor`, `labelled_varied`, `degraded`, `typeset_plain` or `typeset_distractor`. |
| `PREDICTIONS` | The folder of your parser's markdown files for that set. The evaluator names its result files after this folder, so give each run a folder name of its own (for example `myparser_v2_plain`). |
| `WORK_DIR` | Where results are written. Outside this folder, and one per run. |
| `OMNIDOCBENCH` | The evaluator's folder from section 6. |
| `EVALUATOR_PYTHON` | The Python of the evaluator's environment. The default, the notebook's own kernel, is right if you followed section 6. |
| `SCORE_MISSING_PAGES_AS_WRONG` | Leave it False. The preflight then stops if any page has no markdown file. Set it True only to score an incomplete run deliberately: each missing page then scores as wholly wrong. |

### What each section of the notebook does

| Section | What it does | How long |
| --- | --- | --- |
| 2. Functions | Defines everything; changes nothing on disk. | Instant |
| 3. Preflight | Checks that the evaluator's files are those of the right commit, the paths, and that every page has a markdown file. Lists missing, empty and stray files, and warns if your table cells hold bold or italic markup. Stops with a list of what to fix. | Seconds |
| 4. Self-test | Writes the set's own ground truth back as markdown, with html tables and with pipe tables, and scores both. Both must be perfect. Run it once after installing, and again after any change to the installation. It also leaves you two folders of perfectly formed output to look at. | A few minutes |
| 5. Score your parser | The real run. Shows the five overall scores. | A few minutes for 165 pages |
| 6. By document type, layout and tier | One row per page (`scores`, also saved as `scores_per_page.csv`), then the means by document type and by layout or tier. | Instant |
| 7. Worst pages | The pages with the highest text edit distance. | Instant |
| 8. One page, line by line | For any page: each ground-truth line beside the text of your output it was paired with, and the page's table beside yours. This is where to look to understand a score. | Instant |
| 9. Comparing two sets | How to pair two runs page by page. | |

### What is written to `WORK_DIR`

| File | What it is |
| --- | --- |
| `end2end.json` | The configuration the evaluator ran with. |
| `evaluator.log` | Everything the evaluator printed. Read it if a run fails. |
| `scores_per_page.csv` | One row per page: document type, layout, tier and the five scores. |
| `result/<your folder>_quick_match_metric_result.json` | The evaluator's own summary. |
| `result/<your folder>_quick_match_text_block_result.json` | Every ground-truth line with the text it was paired with and its edit distance. |
| `result/<your folder>_quick_match_table_result.json`, `..._table_per_table_TEDS.json` | Every table pair and its scores. |
| `result/<your folder>_quick_match_reading_order_result.json` | Each page's true and predicted order. |
| `self_test/` | The self-test's perfect output and its scores. |

### Without the notebook

The notebook only prepares a configuration and runs one command. To score from a shell or a script:

```sh
cd /path/to/an/empty/work/folder
HF_HUB_OFFLINE=1 /path/to/envs/omnidocbench/bin/python /path/to/{{KIT_DIR}}/tools/omnidocbench_formula_free.py \
    --checkout /path/to/OmniDocBench --config end2end.json
```

where `end2end.json` is the file the notebook writes (copy one from a `WORK_DIR` and change its two
paths). The results appear in `./result/`.

## 9. Reading the scores

### The five scores

| Score | What it measures | Range |
| --- | --- | --- |
| **Text edit distance** | How much of the page's text your output reproduces. Each ground-truth line is paired with the best-matching text of your output and the two are compared by normalised edit distance; the page's score weights each line by its length. | 0 is perfect, 1 is nothing right. |
| **Table TEDS** | Tree-edit-distance similarity between each ground-truth table and yours: the structure of rows, cells and spans, and the text of each cell. | 1 is perfect, 0 is no table. |
| **Table TEDS, structure only** | The same, ignoring cell text. It says whether the grid is right, whatever the reading of the cells. | 1 is perfect. |
| **Table edit distance** | Normalised edit distance between the two tables as html strings. A second view of the same thing; prefer TEDS. | 0 is perfect. |
| **Reading-order edit distance** | Whether the blocks your output matched come in the ground truth's order. | 0 is perfect. |

Each overall score is the plain mean over pages (or over tables, which is the same here: every page has
exactly one table).

### Things to know when interpreting them

- **Read the three document types separately.** A statement's score is nearly all table, a receipt's
  nearly all text, and they differ in difficulty. The notebook's breakdown by `doc_type` is the table to
  report, not the overall mean alone.
- **Text is compared on letters, digits and underscores only.** Spaces and punctuation are stripped from
  both sides before comparison, and case is kept. So the text score does not see a missing decimal point,
  a comma read as a full stop, or a dropped currency sign in a paragraph: `$1,204.50` and `120450` are
  equal to it. Table cells are compared in full, so the same slip inside a table does cost TEDS. If
  exact amounts outside tables matter to you, check them separately.
- **Text in your output that is on no line of the page is not charged by itself.** The text score
  measures what was reproduced, not what was invented: a parser that adds a sentence of its own loses
  nothing for it. But if a ground-truth line has no counterpart and your output holds unmatched text,
  the evaluator pairs the two, and the line is then charged against that text. Section 8 of the notebook
  shows every such pairing.
- **Lines are merged and split to fit.** If your parser writes several printed lines as one paragraph,
  the evaluator pairs the paragraph with all of them together, and the reverse. This is intended. One
  consequence: a parser that writes a whole receipt as a single paragraph has almost no reading order
  left to get wrong, so its reading-order score flatters it. Look at the text score beside it.
- **A table your parser writes as paragraphs is not a table.** Its text is then matched as text, which
  can make the text score look good while TEDS for that page is 0. Always read the two together.
- **Headers and footers are set aside.** Writing them or not changes nothing.
- **An invoice's remittance slip repeats two lines of the invoice** (the invoice number and the amount
  due) word for word. The evaluator treats the two identical lines as one entry, so a parser that
  transcribes the line once is charged half for the pair. That is a real omission, of the slip's copy,
  not a fault in the score. It affects distractor and labelled-varied invoices only.
- **On photographed receipts, expect tables to be hard.** A receipt's item list is a table in the ground
  truth; many parsers read a photographed receipt as plain lines of text and score near 0 on TEDS while
  reading the text well.

### Comparing sets

The three clean sets and the two typeset sets share file names, so two runs pair page by page: subtract
one `scores_per_page.csv` from the other and group by document type (the notebook's section 9 has the
code). For the photographs, pair each degraded page with the receipt it is a photograph of by stripping
`_v1`, `_v2` or `_v3` from its name and looking that receipt up in the `distractor` scores. Never compare
the degraded set's overall mean with a clean set's: one holds receipts only.

## 10. Rules for a fair run

- **Give the parser the images (or PDFs) and nothing else.** Nothing from `parsing/` may reach the
  parser, a prompt, a few-shot example or a training set. If your parser is driven by a prompt, any
  example in that prompt must be invented, not taken from these pages: a model that has seen a page's
  text can reproduce it without reading the page.
- **Do not tune on the pages you report.** If you adjust a parser while looking at its scores here, say
  so beside the scores, or hold some cases back.
- **Score complete sets.** A set scored with pages missing is not comparable with one scored whole.
- **Record the snapshot.** Write `{{SNAPSHOT}}` beside every score. Another snapshot holds different
  documents, even where the file names are the same.
- **Record the output form.** Say whether tables were written as html or as pipe tables, since only html
  can carry spanning cells.

## 11. Verifying the download

`MANIFEST.json` lists the SHA-256 of every file except itself and `SHA256SUMS`, which repeats the same
list in the form the standard checksum tools read. From the folder that holds the kit:

```sh
cd {{KIT_DIR}}
python tools/verify_snapshot.py    # any platform, Python 3.9 or later, standard library only
sha256sum -c SHA256SUMS            # Linux
shasum -a 256 -c SHA256SUMS        # macOS
```

`verify_snapshot.py` fails on a missing file, a changed file, an extra file, or an image count that
disagrees with the manifest. Run it on arrival, before anything else. It also fails if anything has been
written into the folder since, which is the reason to keep outputs elsewhere. The manifest cannot vouch
for itself: whoever gave you the kit can give you its SHA-256 to compare with
`sha256sum MANIFEST.json`.

## 12. Limits of this kit

- **Three document types, one page each.** No multi-page statements, no purchase orders, payslips,
  letters or forms.
- **Synthetic pages.** They are drawn to look like the real thing, with realistic clutter and realistic
  photographs of receipts, but they are cleaner and more regular than a real mailbag. A parser that does
  well here has passed a necessary test, not a sufficient one.
- **English only, Australian conventions only.**
- **No formulas, no charts, no multi-column prose.** The pages test text, tables and reading order on
  business layouts and nothing else.
- **Photographs are of receipts only**, at three fixed severities.
- **Layout detection is not scored by the notebook** (section 4).
- **The text score does not see punctuation or spacing** outside tables (section 9).

## 13. Troubleshooting

| What you see | What to do |
| --- | --- |
| The preflight says the evaluator's files differ | The `OmniDocBench` folder is not the evaluator at the commit in section 6, or a file in it was edited. Download and unpack it again (section 6, step 1); the installed environment can stay as it is. |
| The preflight lists pages with no markdown file | Check the file names: the image's name with `.md`, in the folder `PREDICTIONS` names. `CASE001_invoice.png.md` and `case001_invoice.md` do not match. |
| "The evaluator did not finish" | Read `WORK_DIR/evaluator.log`. `No module named src` or `No module named yaml` means `EVALUATOR_PYTHON` is not the environment the evaluator was installed in. |
| "The evaluator's table matcher is not the code this fix was written for" | The `OmniDocBench` folder is not at the commit in section 6. |
| "The ground truth holds a formula" | The launcher was pointed at ground truth that is not this kit's. |
| The self-test fails | The installation differs from the one this kit was checked with. Check the commit, and that the three launcher files in `tools/` are the kit's own (`python tools/verify_snapshot.py`). Do not use scores from that installation. |
| TEDS is 0 on pages where the table looks right | Open the page in section 8 of the notebook. If "Your output" shows no table, your file's table was not recognised: check the pipe table has a delimiter row and a blank line after it, or that the html has `<table>` and `</table>`. |
| TEDS is lower than the table looks | Look for bold or italic markup inside cells (section 5), and for spanning rows written as ordinary rows. |
| `verify_snapshot.py` reports an extra or changed file | Something was written into the kit, most often by running the notebook in place: a `.ipynb_checkpoints` folder, or the notebook saved with its outputs. Unpack a fresh copy, and work on a copy of the notebook outside it. |
