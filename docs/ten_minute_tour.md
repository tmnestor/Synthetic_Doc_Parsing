# Ten-Minute Tour — for an image-processing audience

A walkthrough of this repository for someone who works in image processing or
computer vision and has ten minutes. It assumes no prior knowledge of the
project and stays on the pixel side: rendering, degradation, and rectification.
For the full capability account see
[`generator_capability_overview.md`](generator_capability_overview.md); for the
export schemas see [`GroundTruth_Export_Spec.md`](GroundTruth_Export_Spec.md).

## The one-line version

A synthetic document generator that **builds its own ground truth**. 165 clean
Australian business documents (55 cases × bank statement / receipt / invoice)
drawn pixel-by-pixel from YAML, plus 165 degraded receipts simulating phone
photos. Because every value is *drawn* rather than *annotated*, the ground truth
is exact by construction — no labelling pass, no annotator disagreement.

That framing is the hook for a vision audience: it inverts the usual
document-AI dataset problem. Instead of scanning real documents and paying
someone to box them, the truth is authored first and the pixels rendered from
it.

---

## The arc

### 0–1 min — What and why

YAML → PIL renderer → PNG. `ground_truth/*.yml` holds the 165 entries;
`config/layouts/*.yml` holds 22 rendering specs (8 bank, 6 receipt, 8 invoice)
expressed as a declarative thirteen-primitive DSL: `text`, `pair`, `block`, `rule`,
`spacer`, `panel`, `split`, `stack`, `table`, `banner`, plus three decorative marks that
carry no scored field, `barcode` (Code 128 or EAN-13), `qrcode` and `logo` (a
generated initials mark, never a stored image). Adding or altering a layout is a
YAML edit, not a code change.

### 1–3 min — Geometry is captured at draw time

`generators/exporters/geometry.py` holds a `BoxRecorder` that sits inside the
renderer and records each field's pixel box as it is drawn, normalised to
`[0, 1]`. This is the part worth dwelling on: **bounding boxes cost nothing,
because the drawer already knows where it put the text.** Drawing a field twice
raises rather than overwrites — a field drawn twice has no unambiguous
ground-truth location.

One wrinkle worth showing: receipts render onto an oversized canvas, because
the final length is unknown up front, and crop to content afterwards. The
boxes captured against the oversized canvas therefore need their vertical
fractions rescaled to the final page height, which is what
`rescale_vertical()` does. Horizontal fractions are untouched — width never
changes.

Related, and load-bearing: **fit-safety**. Every variable field is drawn
through `fit_text` against per-layout pixel budgets (`field_budgets:` in
`config/layouts/*.yml`). Text that does not fit wraps or shrinks losslessly,
and a genuinely impossible fit raises `FitError`. Silent clipping would corrupt
the benchmark — a field that is on the page but truncated scores as a model
failure when it is really a renderer bug. `validate` runs an overflow backstop
across all three document types so this fails loudly instead.

### 3–6 min — The degradation pipeline

`generators/degradation/`. Receipts only, three severity tiers, every parameter
declared in `config/generation_config.yml` under `receipt_degradation:`.

```
clean receipt (flat)
  ↓  Augraphy ink phase     — ink bleed                    } damage to the
  ↓  Augraphy paper phase   — creases, lighting, shadow    } paper itself
  ↓  camera warp            — desk, perspective, shadow
  ↓  camera photometrics    — brightness, blur, noise, JPEG } the act of
degraded variant                                           } photographing
```

**The ordering is the argument to make.** A crease belongs to the paper, so it
must be warped *with* the page. Painting one flat across an already-tilted photo
would read as a defect in the *image* rather than in the *document*. Conversely,
blur, sensor noise and JPEG blocking are properties of the lens and the file, so
they apply to the whole frame after compositing.

Two further points a vision audience tends to appreciate:

- **Augraphy's geometric augmentations are deliberately unused.**
  `degradation/augment.py` registers an allow-list of exactly four effects —
  `InkBleed`, `LightingGradient`, `ShadowCast`, `Folding`. Geometry is owned
  solely by `degradation/camera.py`, because a second perspective transform
  would defeat the rectifier's quad detection downstream. The allow-list also
  turns a YAML typo into a startup diagnostic naming the valid options.
- **Everything is RGB end to end.** PIL does I/O and NumPy arrays feed straight
  into `cv2`, so there is no BGR channel swap anywhere. `camera.py` builds the
  desk background as a NumPy array (muted base tone, linear lighting gradient,
  faint Gaussian noise), warps the page as RGBA through `cv2.warpPerspective`,
  derives a drop shadow by blurring and offsetting the alpha channel, then
  alpha-composites the two.

Severity is pure configuration. The `heavy` tier carries an in-file comment
recording that it was calibrated against rendered output rather than guessed:
fold count dropped 2 → 1 because Augraphy renders a fold as a hard black wedge
that landed across the supplier name, and the two darkening effects (lighting
gradient and cast shadow) stack, so neither can sit at its own limit. The
guiding principle is that heavy must be *hard* to read and never *impossible* —
a field a careful human cannot read measures noise, not robustness.

### 6–8 min — The rectifier, an explicit inverse

`rectify_camera_scan.py`, at the repository root:

> grayscale → Gaussian blur → Canny → dilate → largest external contour →
> `approxPolyDP` at escalating epsilon until a convex 4-gon appears →
> `cv2.getPerspectiveTransform` / `cv2.warpPerspective` to an upright crop sized
> by the detected edge lengths

It falls back to `minAreaRect` — which corrects rotation but not perspective —
if no quad converges. Two design calls are worth naming:

- **Fail-open.** No convincing quad means the image is returned unchanged. A
  missed rectification is cheap; a wrong crop that drops a line item is a
  regression.
- **It is an *approximate* inverse, on purpose.** It re-estimates the quad from
  pixels rather than reusing the stored homography, and sizes its output to the
  detected edge lengths, while the blur, noise and JPEG steps are not invertible
  at all. That is moot under value-F1 scoring, which reads field values rather
  than positions, and matters only for spatial round-trip validation.

The generator reference's [Rectification](generator_reference.md#rectification-undoing-the-camera-scan-offline-preprocessing)
section records 55/55 quad detection, with dimensions recovering close to the
clean original (CASE001: clean 420×374 → degraded 516×490 → rectified 425×380).

### 8–10 min — Where it lands, and the honest gaps

`derive` re-projects the corpus onto public benchmark schemas — CORD for
receipts and invoices, DocILE for invoices, a project-defined native schema for
bank statements — so results stay comparable to public leaderboards. The DocILE
export is what consumes `../evaluation_data/derived/geometry.jsonl`. Inference runs on the remote
GPU host; scoring happens locally through `score_extractions.ipynb`.

Gaps worth volunteering before a visitor finds them:

- **No geometry on degraded images.** Boxes exist for clean renders only; the
  warp does not carry them forward. So the degraded set supports extraction
  evaluation, not detection or layout analysis.
- **The homography is not stored.** `camera.py` computes its transform locally
  and discards it. Persisting it would yield exact warped-box ground truth and
  make the rectifier measurable end to end. This is the most obvious place for
  a collaborator to contribute.

---

## What to have on screen

| Open | Why |
|---|---|
| `docs/assets/degradation_comparison.png` | Clean against the three tiers, side by side. Open it first; it does most of the explaining |
| `generators/degradation/__init__.py` | The module docstring *is* the ordering argument, in about twelve lines |
| `rectify_camera_scan.py` → `detect_document_quad` | The whole CV pipeline fits on one screen |
| `figures/paired_clean_vs_degraded.png` | If the question is what the degradation actually costs a model |

## Likely pushback

**"Why not just augment real receipts?"** Nothing real ever goes in. Every page
is composed from vocabularies and rules, so there is no underlying record to
re-identify. People and addresses come from Faker (`en_AU`), businesses are
invented from curated name-parts and screened against a real-business
blocklist, and ABNs carry valid checksums. See
[`generator_capability_overview.md`](generator_capability_overview.md) §8 for
the full account, including the two things on the page that are deliberately
real.

**"Is synthetic degradation realistic enough to transfer?"** A fair challenge,
and untested here. The tiers are calibrated for legibility, not validated
against a measured distribution of real phone photos. Say so.

**"Why only receipts?"** Bank statements and invoices reach the business as
clean PDFs or printouts. Degrading them would model a workflow nobody has.
