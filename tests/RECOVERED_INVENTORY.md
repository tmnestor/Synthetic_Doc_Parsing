# Recovered test inventory

Coverage log: generators.layout_dsl 97% (375 passed, 3 xfailed), 6 October 2026, after Task 0.4.
Coverage log: total 85.93% over generators, linking and scripts (1028 passed, 10 xfailed), 6 October 2026, after Task 0.9.

Extracted on 6 October 2026 from the bytecode the lost suite left in
`tests/**/__pycache__/` (last compiled 13 September 2026). Test names and
docstrings are verbatim; bodies were not recoverable. Use each docstring as
the specification when rebuilding that test against current code. A test
whose behaviour no longer exists in the code is dropped, not resurrected.

## `tests/degradation/test_augment.py`

```text
The augmentation registry rejects unknown names at build time, and the
augmented output is deterministic for a given seed.
```

- [x] `test_registry_exposes_the_tier_augmentations`
- [x] `test_photocopier_augmentations_are_not_registered`: Excluded by design, they model a different damage story.
- [x] `test_unknown_augmentation_is_a_diagnostic`
- [x] `test_spec_without_an_augmentation_key_is_a_diagnostic`
- [x] `test_unmapped_parameter_is_a_diagnostic_not_a_bare_keyerror`: The trap for whoever next reaches for an Augraphy knob. Augraphy's parameter names are longer and less consistent than the YAML's, so every one has to be mapped by hand. An unmapped key used to raise a bare KeyError naming only the key, no file, no list of what IS accepted, and no hint that the mapping table is where to look. (adjusted: asserts only that the key is named; the diagnostic part is the xfailed `_bug` sibling)
- [x] ~~test_the_fold_width_is_configurable~~ dropped: the YAML-to-Augraphy mapping for Folding (augment.py _PARAM_NAMES) exposes only fold_count and fold_noise; gradient_width is no longer configurable, so there is nothing to guard
- [x] `test_same_seed_gives_identical_output` (adjusted: asserts non-folding tiers; the folding tier is the xfailed `_bug` sibling)
- [x] `test_augmentation_changes_the_image`
- [x] `test_empty_phases_pass_the_image_through_unchanged`
- [x] `test_dimensions_are_preserved`: Ground truth is value-F1, but a size change would break the warp's source quad, which assumes the augmented page is the original size.

## `tests/degradation/test_camera.py`

```text
The warp produces a photo-shaped frame, the pipeline is deterministic per
(case seed, tier index), and every drawn value is reported back.

The provenance tests matter as much as the image ones. Nothing downstream can
tell whether a recorded rotation is the rotation that was actually applied, so
if provenance ever drifts from the transform it describes, every quality label
built on it is quietly wrong and no test of the images would notice.
```

- [x] `test_warp_enlarges_the_frame_by_the_margin`: The document must occupy a sub-region, surrounded by background, that framing is the whole point of the camera model.
- [x] `test_warp_leaves_background_in_the_corners`: A corner pixel should be desk, not paper: if the page still filled the frame, the perspective warp did not happen.
- [x] `test_photometrics_preserve_dimensions`
- [x] `test_warp_reports_values_inside_the_declared_range`: The seam between what a tier permits and what an image got. (adjusted: no provenance; the drawn rotation is observed at the point of use)
- [x] `test_photometrics_report_values_inside_the_declared_range` (adjusted: no provenance; blur and JPEG quality observed at the PIL call)
- [x] ~~test_reported_rotation_is_the_rotation_applied~~ dropped: warp_to_photo no longer returns or records provenance; there is no reported value to compare with the pixels
- [x] ~~test_degrade_document_provenance_names_the_tiers_augmentations~~ dropped: degrade_receipt returns a bare image; provenance was removed with the labels module
- [x] `test_tier_seed_is_stable_and_distinct_per_tier`
- [x] `test_degrade_document_is_deterministic` (adjusted: asserts non-folding tiers plus different-seed difference; folding tier is the xfailed `_bug` sibling; function is degrade_receipt)
- [x] `test_tiers_differ_from_each_other`
- [x] `test_degrade_document_returns_rgb`
- [x] ~~test_compose_clean_frames_the_page_without_distorting_it~~ dropped: compose_clean no longer exists; clean renders are not photographed
- [x] ~~test_compose_clean_reports_no_defect_bearing_values~~ dropped: compose_clean no longer exists
- [x] ~~test_clean_provenance_carries_the_same_keys_as_a_degraded_one~~ dropped: provenance and compose_clean no longer exist
- [x] ~~test_compose_clean_leaves_the_document_legible~~ dropped: compose_clean no longer exists

## `tests/degradation/test_collage.py` (OBSOLETE: generators/degradation/collage.py no longer exists)

```text
Collage plates: several receipts, one camera.

The image-quality screen asks whether a picture holds one document or several.
These build the images that answer is scored against, so what matters here is
not that a plate looks pretty but that:

  * every receipt actually lands on it, a receipt clipped away silently makes
    a two-document plate a one-document plate while the label still says two;
  * the plate is TRANSPARENT where there is no paper, because that is what lets
    the single-page camera supply the desk and one perspective;
  * the provenance records what was DRAWN, since the ground-truth label is
    derived from it rather than from the config range.
```

- [ ] `TestThePlate`: TestThePlate
- [ ] `TestThePlate.test_every_arrangement_produces_a_transparent_plate`
- [ ] `TestThePlate.test_the_plate_holds_as_much_paper_as_it_was_given`: The failure this guards: a receipt placed off-canvas is invisible, and the plate is then labelled MULTIPLE while showing one document.
- [ ] `TestThePlate.test_a_single_page_still_works`: Not a collage, but the boundary. It must not crash or need a special case at the call site.
- [ ] `TestArrangementsDiffer`: TestArrangementsDiffer
- [ ] `TestArrangementsDiffer.test_there_is_no_piled_arrangement`: It existed and was removed. A pile buries the totals of the receipts underneath, which models a taxpayer working against their own interest: someone photographing receipts is SUBSTANTIATING an expense and lays them out so the business name, date and total show. That is not a hard case, it is a wrong one, and it accounted for a quarter of the plates.
- [ ] `TestArrangementsDiffer.test_every_arrangement_keeps_the_receipts_readable`: The property that replaced it. At the configured ceiling the receipts touch at their margins, where a receipt carries "Thank you for shopping with us" rather than an amount, and no receipt loses a meaningful share of itself to a neighbour.
- [ ] `TestArrangementsDiffer.test_side_by_side_is_one_row`
- [ ] `TestArrangementsDiffer.test_more_overlap_makes_a_smaller_plate`
- [ ] `TestProvenance`: TestProvenance
- [ ] `TestProvenance.test_it_records_the_overlap_that_was_used`: The label must reflect the plate that exists, not the config range that permitted it: a range of [0.0, 0.18] can draw 0.02, and that is not an overlapping collage.
- [ ] `TestProvenance.test_every_placement_is_recorded`
- [ ] `TestProvenance.test_receipts_are_rotated_independently`: A plate where every receipt is turned by the same angle looks like one rotated document, not several.
- [ ] `TestProvenance.test_the_recorded_angle_is_the_angle_actually_applied`: The angle was drawn TWICE, once to rotate, once to report, so the provenance carried angles that had been applied to nothing. The test above did not catch it, because the reported angles still differed from each other; only comparing them to the pixels does. A page rotated by t with expand=True grows to w|cos t| + h|sin t| by w|sin t| + h|cos t|, so the recorded angle predicts the recorded size. If they were drawn separately they will not agree.
- [ ] `TestProvenance.test_the_same_seed_gives_the_same_plate`: Regeneration has to be reproducible, or a disputed label cannot be re-examined.
- [ ] `TestRejections`: TestRejections
- [ ] `TestRejections.test_no_pages_is_a_diagnostic_error`
- [ ] `TestRejections.test_an_unknown_arrangement_names_the_valid_ones`
- [ ] `TestFoldedReceipt`: TestFoldedReceipt
- [ ] `TestFoldedReceipt.test_it_is_one_document`: The label falls out of this. Getting it wrong makes the corpus teach the opposite of what it is for.
- [ ] `TestFoldedReceipt.test_the_halves_are_joined_at_the_crease`: The bug this replaced: the far half was rotated about its own centre, which swung its top edge away from the fold and left a transparent gap. That renders as two separate receipts lying near each other, a MULTIPLE, labelled SINGLE, which is the worst error this corpus could contain. Nothing in the provenance showed it; only the pixels do.
- [ ] `TestFoldedReceipt.test_the_far_half_is_shaded`: A fold shows as a tonal step, because the two halves lie at different angles to the light. Without it the crease reads as a cut.
- [ ] `TestFoldedReceipt.test_the_provenance_records_the_fold_that_was_drawn`
- [ ] `TestFoldedReceipt.test_it_reads_like_a_collage_to_the_caller`: Same provenance shape as compose_collage, so the render pass can treat both alike and the label falls out of document_count.
- [ ] `TestFoldedReceipt.test_a_fold_outside_the_page_is_refused`
- [ ] `test_the_plate_survives_the_single_page_camera`: The whole design rests on this: a collage is handed to the SAME warp_to_photo a single page uses, so it gets one perspective and one desk, and the drop shadow falls under each receipt from the alpha channel without that function knowing there is more than one document.

## `tests/degradation/test_tiers.py`

```text
Tier config loads from YAML only, and every omission fails with a diagnostic.

Tiers are declared per document type. That is not decoration: every tier value
is in pixels, and a receipt is 440x674 against an invoice's 1900x3508, so one
shared list made `heavy` mean two different severities. The tests below pin the
two properties that keeps honest, each type gets its own values, and every
type declares the same rungs.
```

- [x] `test_loads_the_real_project_config`: Assert the ladder is well-formed, not which rungs it has. The rungs are an operator decision, already changed once, from three tiers to two, so pinning names here made this fail for expressing a preference rather than a contract. (adjusted: asserts the shipped ladder against the YAML itself)
- [x] ~~test_every_type_declares_the_same_rungs~~ dropped: tiers are one flat list for receipts only; per-type ladders were removed
- [x] ~~test_types_carry_their_own_measured_values~~ dropped: per-type ladders were removed
- [x] `test_missing_block_is_a_diagnostic`
- [x] ~~test_a_flat_tier_list_is_a_diagnostic_naming_the_old_shape~~ dropped: the flat list is now the one valid shape, so there is no old shape to diagnose
- [x] `test_empty_tier_mapping_is_a_diagnostic` (adjusted: tiers are a flat list now; covers empty, absent and empty-block cases)
- [x] ~~test_empty_tier_list_for_a_type_is_a_diagnostic~~ dropped: per-type ladders were removed (empty `tiers` is covered by test_empty_tier_mapping_is_a_diagnostic)
- [x] `test_missing_tier_key_is_a_diagnostic`
- [x] `test_duplicate_suffix_within_a_type_is_a_diagnostic` (adjusted: no per-type ladders; duplicate suffix across the one flat list)
- [x] ~~test_the_same_suffix_across_types_is_fine~~ dropped: per-type ladders were removed
- [x] ~~test_types_declaring_different_rungs_is_a_diagnostic~~ dropped: per-type ladders were removed
- [x] ~~test_types_declaring_rungs_in_a_different_order_is_a_diagnostic~~ dropped: per-type ladders were removed (YAML order is covered by test_tier_order_is_yaml_order)
- [x] `test_tier_that_is_not_a_mapping_is_a_diagnostic`

## `tests/exporters/test_config.py`

```text
Tests for export config loading and validation.
```

- [x] `test_loads_valid_config`
- [x] `test_missing_key_is_diagnostic`
- [x] `test_invalid_enum_value_is_diagnostic`
- [x] `test_empty_export_targets_is_allowed`: An explicit empty list ships every target as a no-op; it is not a missing key.
- [x] `test_missing_file_is_diagnostic`
- [x] `test_malformed_yaml_is_diagnostic`: An unparseable YAML file must hit the yaml.YAMLError branch, not crash raw.
- [x] `test_non_dict_top_level_is_diagnostic`: A YAML file whose top level is a list (not a mapping) must be rejected.
- [x] `test_export_targets_wrong_type_is_diagnostic`: export_targets must be a list, not e.g. a bare scalar.
- [x] `test_export_targets_unknown_member_is_diagnostic`: Every export_targets member must be one of the allowed targets.
- [x] `test_docile_fieldtypes_wrong_type_is_diagnostic`: docile_fieldtypes must be a mapping, not e.g. a list.

## `tests/exporters/test_cord.py`

```text
Tests for the CORD gt_parse mapper (spec Mapping A).
```

- [x] `test_case001_matches_the_spec_worked_example` (adjusted: live CASE001 no longer matches the spec's values; fixture is an invented receipt of the spec shape)
- [x] `test_is_gst_included_is_not_emitted`: Spec section 3 rule 6: the boolean is generator metadata, not tree content. Strengthened beyond the field-name check: given cord.py's allowlist architecture, "IS_GST_INCLUDED" not in str(tree) can only fail if the whole fields dict leaked wholesale. The requirement actually means the *value* "true" must never appear anywhere in the emitted tree either.
- [x] `test_document_type_is_not_emitted`: As above: also assert the value "RECEIPT" is absent, not just the key.
- [x] `test_not_found_fields_are_absent`: Spec section 3 rule 5: NOT_FOUND is never emitted.
- [x] `test_invoice_adds_payer_to_extension`
- [x] `test_digits_only_form_applies_to_abn`
- [x] `test_digits_only_form_does_not_strip_other_extension_fields`: Identifier-form scoping must be limited to BUSINESS_ABN alone. A regression that ran canonical_identifier over every extension value (instead of only the IDENTIFIER_COLUMNS-listed ones) would strip spaces out of business_address, invoice_date and payer_name/payer_address too. The only pre-existing digits_only test checks business_abn in isolation, so it would not catch that. INVOICE_DATE is overridden here to a value that itself contains a space ("02 03 2023" rather than "02/03/2023") so that a blanket space-stripping regression has something to strip in every field under test, not just business_address and the payer name.
- [x] `test_line_item_totals_sum_to_the_document_total`: Arithmetic sanity: 4.73 + 8.87 == 13.60.
- [x] `test_line_item_unit_price_and_total_price_map_to_distinct_columns`: CASE001's rows all have unit_price == total_price (4.73/4.73, 8.87/8.87), so a bug that swapped the LINE_ITEM_PRICES -> unitprice and LINE_ITEM_TOTAL_PRICES -> price mappings would pass every existing assertion. This fixture's quantity of 2 makes the two genuinely differ (12.50 unit price vs 25.00 line total), so a swap is caught either by the direct field checks below or by the sum-to-total check.
- [x] `test_no_line_items_omits_menu_key`: A record with no LINE_ITEM_* columns at all must omit "menu" entirely rather than emit an empty list. Absence here is via omitted keys.
- [x] `test_gst_amount_absent_omits_sub_total_key`: GST_AMOUNT absent (via the NOT_FOUND sentinel) must omit "sub_total" entirely rather than emit an empty container.
- [x] `test_total_amount_absent_omits_total_key`: TOTAL_AMOUNT absent (via an omitted key) must omit "total" entirely rather than emit an empty container.
- [x] `test_all_extension_sources_absent_omits_extension_key`: When every EXTENSION_COLUMNS source is absent, "extension" must be omitted entirely rather than emitted as an empty dict. Mixes both real forms absence takes: NOT_FOUND sentinels for the columns present in the fields dict, and simply omitted keys for the rest.

## `tests/exporters/test_cord_eval.py`

```text
Tests for the vendored, apted-backed CORD evaluator (generators.exporters.cord_eval).

This is the real deliverable of Task 6: a vendored tree-edit-distance metric that
silently always returned 1.0 would make every later self-scoring gate worthless, so
these tests exist to prove the metric actually discriminates between correct and
incorrect predictions, not just that it round-trips identity.
```

- [x] `test_case001_cord_self_score_is_perfect`: A perfect prediction must score exactly 1.0 against itself.
- [x] `test_case001_tree_is_not_empty`: Guard against a no-op mapper: the CASE001 tree must actually be populated. cal_acc(x, x) == 1.0 is trivially true for x == {} too, so the identity test above would pass even if to_cord emitted nothing. This checks real content.
- [x] `test_cal_acc_on_two_empty_dicts_fails_fast_instead_of_lying`: cal_acc({}, {}) must not silently report a perfect score. The upstream Donut evaluator actually raises a bare, undiagnosed ZeroDivisionError for this input (confirmed directly against unmodified upstream code during this port) rather than returning 1.0. This vendored version raises a diagnostic ValueError instead: a degenerate empty-vs-empty comparison carries no information and must never be read as "perfect".
- [x] `test_one_field_wrong_scores_strictly_between_zero_and_one`
- [x] `test_everything_different_scores_near_zero`
- [x] `test_two_fields_wrong_does_not_score_higher_than_one_field_wrong`
- [x] `test_cal_f1_perfect_predictions_score_one`
- [x] `test_cal_f1_one_wrong_document_scores_strictly_less_than_one`
- [x] `test_cal_f1_rejects_mismatched_list_lengths`: Silent zip()-truncation on length mismatch is exactly the kind of silent failure this project's fail-fast convention forbids; must raise instead.
- [x] `test_missing_menu_item_reduces_score`
- [x] `test_extra_menu_item_reduces_score`

## `tests/exporters/test_cord_score.py`

```text
Tests for score_cord (generators.exporters.cord_score).

Wires the previously-unconsumed ``cord_extension_scoring`` config key into
real behaviour: the headline CORD F1 must stay comparable to public CORD
leaderboards (no ``extension`` slot exists there), while the extension
subtree (supplier/ABN/address/date/payer) is either folded back in, scored
separately, or dropped, per the configured mode.
```

- [x] `test_in_tree_mode_scores_the_full_tree_and_reports_no_extension_figure`
- [x] `test_in_tree_mode_is_sensitive_to_an_extension_only_corruption`: Unlike the excluded modes, in_tree must still be dragged down by an extension-only mutation, proving the mode genuinely changes behaviour rather than the extension key being ignored everywhere.
- [x] `test_excluded_unscored_mode_drops_extension_from_headline_and_reports_none`
- [x] `test_excluded_unscored_mode_ignores_an_extension_only_corruption`
- [x] `test_excluded_scored_separately_mode_reports_both_figures_perfectly`
- [x] `test_excluded_scored_separately_mode_isolates_an_extension_only_corruption`: The whole point of the exclusion: corrupting ONLY the extension subtree must leave headline_f1 untouched while extension_f1 drops.
- [x] `test_excluded_scored_separately_mode_still_catches_a_non_extension_corruption`: The inverse check: corrupting a non-extension field (total_price) must still drag the headline score down; exclusion only removes `extension`, not sensitivity in general.
- [x] `test_inputs_are_never_mutated`
- [x] `test_unknown_mode_raises_a_diagnostic_error`

## `tests/exporters/test_docile.py`

```text
Tests for the DocILE KILE/LIR mapper (spec Mapping B).

DocILE is restricted to invoices (see generators/exporters/docile.py module
docstring), so every fixture here is an invoice with complete geometry -
every DocILE field renders on an invoice, so a missing box is always a real
capture bug, never a structural absence.
```

- [x] `test_kile_emits_one_entry_per_present_field`: Structural: exactly 9 kile entries (8 KILE_COLUMNS + 1 total variant). Content: the fieldtype set matches every configured DocILE fieldtype.
- [x] `test_gst_inclusive_selects_the_gross_total_fieldtype`: Spec section 5.1: IS_GST_INCLUDED chooses between gross and net.
- [x] `test_gst_exclusive_selects_the_net_total_fieldtype`
- [x] `test_lir_groups_by_line_item_id`: Structural: 8 lir entries total, 4 per row. Content: row 0's texts are the description, quantity and price/total (a quantity-one item collapses unit price and line total to one value), and indices align; description[0] and quantity[0] both carry line_item_id 0, description[1] and quantity[1] both carry line_item_id 1.
- [x] `test_every_entry_carries_a_page_and_bbox`
- [x] `test_missing_geometry_is_a_hard_error`: A silently dropped field is absent from both predictions and truth, which inflates precision instead of failing. Every DocILE field on an invoice has geometry, so a missing box always signals a real capture bug.
- [x] `test_unknown_fieldtype_is_rejected_at_export_time`
- [x] `test_missing_geometry_for_a_line_item_is_a_hard_error`: The hard-error rule applies to LIR fields too, not just KILE fields.
- [x] `test_absent_field_is_omitted_without_requiring_its_box`: A field absent from ground truth (e.g. NOT_FOUND) must not be emitted, and must not require geometry either; only *present* fields are hard errors when their box is missing.

## `tests/exporters/test_docile_self_score.py`

```text
The DocILE export, scored against itself via the published evaluate_dataset,
must be perfect, and the gate must be able to fail.

Step 1 (this task's brief) was to inspect the real interfaces rather than trust
guessed ones. Reading Dataset.__init__, Document.__init__ and DataPaths (all in
docile.dataset) showed that Dataset is not an in-memory container: it expects
an on-disk directory following DocILE's own layout:
``{root}/annotations/{docid}.json`` and ``{root}/ocr/{docid}.json``, loaded
lazily through ``CachedObject.content`` (reads from disk if the path exists).
``Dataset(split_name, root, docids=[...])`` accepts an explicit docid list, so
no index file is required.

``Field.from_dict``'s expected keys, {bbox, page, score, text, fieldtype,
line_item_id, use_only_for_ap}, are exactly the keys ``to_docile`` already
emits (confirmed by reading ``Field.from_dict``'s source), so the *same*
record dicts serve as both the on-disk gold annotation
(field_extractions / line_item_extractions) and the in-memory ``Field``
predictions. Self-score is therefore a genuine round trip through the
library's own matching code, not a hand-rolled stub of it.

The published matching metric (``docile.evaluation.pcc.pccs_iou``) computes
IoU over OCR-derived Pseudo-Character-Centers (PCCs), not raw bbox overlap,
confirmed by reading ``get_document_pccs`` / ``pccs_iou`` / ``_calculate_pccs``
directly. This project's ground truth carries no independent OCR word stream,
so each document's ``ocr/{docid}.json`` here is built from the *same*
field text+bbox pairs already in the export: one "OCR word" per field, with
its ``snapped_geometry`` pre-supplied so no PDF rendering is required
(``DocumentOCR._get_bbox_from_ocr_word`` uses the cached snapped geometry
directly when present). ``_calculate_pccs`` then distributes one
pseudo-character-center per character across each field's own bbox width,
so for identical gold/prediction boxes, the covered-PCC sets are identical
and the resulting IoU is a genuinely computed 1.0, not the library's
degenerate empty-PCC-set fallback (confirmed present in ``pccs_iou``'s
source, but deliberately not relied on here since it would make the gate
untestable: with zero PCCs of any kind, *any* two intersecting boxes score
IoU 1 regardless of true overlap).

A self-score alone (predictions == gold) is near-tautological: it proves the
adapter's shape round-trips through the library, not that the library can
detect a wrong prediction. Every perfect-score assertion below is therefore
paired with a mutation test that perturbs one field's bbox to a location with
no covered PCCs, proving the corresponding gate can actually fail.
```

- [x] `test_export_is_populated`: Structural guard: exactly 55 invoices (spec section 8.3, invoice-only scope supersedes any receipt+invoice count named elsewhere), every one carrying non-empty kile and lir, before the self-score test spends any time on them.
- [x] `test_docile_self_score_is_perfect`: Feed the gold annotations back as predictions; AP and f1 must be 1.0. What this proves: the export round-trips through the published library, bboxes validate as in-range, fieldtypes are accepted KILE/LIR enum members, and the Dataset/Field/evaluate_dataset gauntlet accepts our structure. This is exactly the conformance spec section 8.3 asks for. What this does NOT prove, and must not be trusted to catch: that our bboxes land on the real text pixels, or that the source-column -> fieldtype mapping is semantically correct. Both the gold annotations and the predictions here derive from the same to_docile output, and the synthesised OCR is built from those same bboxes, so a *systematic* export bug, a transposed axis, a shifted origin, a swapped fieldtype mapping, moves gold, OCR, and prediction in lockstep and still scores 1.0. Verified by simulation in the Task 12 review. Pixel-correctness of a bbox is guaranteed upstream instead, by construction in generators/common.py's capture (the recorder stores the exact draw coordinates), not by this gate. The mutation tests below perturb only the prediction side, proving the library discriminates a wrong prediction from a right one, not that this self-score would catch a wrong exporter.
- [x] `test_corrupting_one_kile_bbox_degrades_only_the_kile_score`: Moving one KILE prediction's bbox to an empty page corner must turn that field into a false negative (its own gold annotation goes unmatched), strictly lowering KILE's AP while leaving LIR's f1 untouched at 1.0, proving the KILE gate can actually fail, not just tautologically pass.
- [x] `test_corrupting_one_lir_bbox_degrades_only_the_lir_score`: The same proof for LIR: moving one LIR prediction's bbox away from its gold annotation strictly lowers LIR's f1 while KILE's AP stays at 1.0.
- [x] `test_corrupting_one_kile_fieldtype_also_degrades_the_score`: A wrong fieldtype string (rather than a wrong bbox) must also break the match: the corrupted prediction is compared against a different gold field's candidates and its original gold field goes unmatched, degrading precision/recall even though every bbox is untouched.

## `tests/exporters/test_eval_projection.py`

```text
Tests for the in-house extraction-schema projection.

Covers the five accessors `scripts/relabel_evaluation_set.py` needs
(`monetary_fields`, `boolean_fields`, `resolve_doc_type`,
`get_all_doc_type_fields`, `get_extraction_fields`), sourced from
`config/extraction_schema.yml` instead of LMM_POC's `common.field_schema`.
```

- [x] `test_loads_real_repo_config`: The actual config/extraction_schema.yml loads and exposes all three types.
- [x] `test_extraction_fields_preserve_declared_order`
- [x] `test_extraction_fields_returns_new_list`: Callers can mutate the returned list without corrupting the schema.
- [x] `test_get_all_doc_type_fields_matches_yaml_order`
- [x] `test_get_all_doc_type_fields_returns_new_dict`
- [x] `test_resolve_doc_type_across_conventions`
- [x] `test_resolve_doc_type_strips_surrounding_whitespace`: More lenient than LMM_POC by design: padding only ever helps resolution.
- [x] `test_resolve_doc_type_unresolved_returns_normalised_unchanged`: Mirrors LMM_POC: no match means normalised-but-unchanged, not a raise.
- [x] `test_monetary_and_boolean_fields`
- [x] `test_unknown_document_type_raises`
- [x] `test_get_all_doc_type_fields_excludes_unknown_type`
- [x] `test_missing_file_is_diagnostic`
- [x] `test_malformed_yaml_is_diagnostic`
- [x] `test_non_dict_top_level_is_diagnostic`
- [x] `test_missing_document_fields_is_diagnostic`
- [x] `test_doc_type_missing_fields_list_is_diagnostic`
- [x] `test_empty_fields_list_is_diagnostic`
- [x] `test_missing_evaluation_is_diagnostic`
- [x] `test_missing_field_types_is_diagnostic`
- [x] `test_missing_monetary_is_diagnostic`
- [x] `test_missing_boolean_is_diagnostic`

## `tests/exporters/test_geometry.py`

```text
Tests for draw-time bounding-box capture.
```

- [x] `test_records_a_box_normalised_to_the_page`
- [x] `test_line_item_members_are_indexed`
- [x] `test_coordinates_stay_within_the_unit_square`
- [x] `test_duplicate_field_is_rejected`: Two boxes for one field means the renderer drew it twice; ambiguous truth.
- [x] `test_zero_dimension_page_is_rejected`
- [x] `test_rescale_vertical_is_a_noop_when_heights_match`
- [x] `test_rescale_vertical_corrects_y_after_a_crop`
- [x] `test_rescale_vertical_clamps_results_into_the_unit_square`
- [x] `test_rescale_vertical_does_not_mutate_the_input`

## `tests/exporters/test_geometry_pixel_anchor.py`

```text
Pixel-level anchor test for draw-time bounding boxes (Task 12b).

Task 12's DocILE self-score is structurally blind to systematic bbox bugs:
the gold annotations, synthesised OCR, and predictions all derive from the
same `to_docile` output, so a transposed axis or a wrong offset moves all
three in lockstep and still scores 1.0. Nothing before this file verified
that a captured bounding box actually lands on the *rendered pixels* of its
field's text.

For most fields the recorded box is exactly the renderer's own draw
coordinate, so it cannot drift from the glyphs. INVOICE_DATE is the
exception: `capture_label_prefixed_value` (generators/common.py) computes the
box by measuring the pixel width of the literal "Date: " label and shifting
the box right by that amount, so the recorded box covers the value, not the
label. That arithmetic (added in commit 434bc82) is exactly the kind of bug
that could be subtly wrong while every existing (geometry-only,
label-vs-margin) test still passes, because none of them look at pixels.

This file renders real documents with a BoxRecorder, converts each
normalised box back to pixel coordinates, crops that region out of the
actual rendered PIL image, and asserts the crop contains dark ink, not
blank paper. A negative control (a page region known to be empty) proves the
check can actually fail, rather than trivially passing on any crop.
```

- [x] `test_blank_margin_region_fails_the_text_check_on_invoice`: A known-empty strip of page must NOT pass `_assert_covers_text`. This is the negative control: without it, a test that simply measures "some darkness" could pass on any crop, including one on blank paper, which is exactly the failure mode this file exists to catch.
- [x] `test_blank_margin_region_fails_the_text_check_on_receipt`: Same negative control on a receipt page (different aspect ratio/DPI).
- [x] `test_invoice_date_box_lands_on_the_value_pixels`: The INVOICE_DATE box (offset past 'Date: ' by Task 10b) must cover ink.
- [x] `test_invoice_date_box_excludes_the_label_to_its_left`: The region immediately LEFT of the captured box must ALSO contain ink. That region is where the "Date: " label itself sits. If the offset arithmetic were wrong and the box's left edge started at the line origin (i.e. included the label), there would be nothing left of the box to inspect, this test would degenerate or find blank space. Finding ink here, with the value box starting to its right, is the positive proof that the box begins after the label rather than at the label's start.
- [x] `test_receipt_date_box_lands_on_value_and_excludes_label`: Same label-offset anchor on the receipt renderer. generators/receipt.py draws INVOICE_DATE via the same `capture_label_prefixed_value("Date: ", ...)` helper as invoices (see the receipt_meta/title/metadata section branches), so it carries the same Task 10b offset-arithmetic risk and gets the same two-sided check.
- [x] `test_invoice_supplier_name_box_lands_on_text`: SUPPLIER_NAME's box is the renderer's own draw_fitted_left extent, a sanity check that the anchoring method itself is sound, independent of any Task 10b offset arithmetic.
- [x] `test_invoice_total_amount_box_lands_on_text`: TOTAL_AMOUNT is captured via draw_text_right's own extent.
- [x] `test_invoice_line_item_description_box_lands_on_text`: LINE_ITEM_DESCRIPTIONS[0] is captured via draw_fitted_left's own extent.

## `tests/exporters/test_links.py`

```text
Tests for the doc_refs link mapper (spec Mapping C).
```

- [x] `test_transaction_link_maps_to_doc_refs`
- [x] `test_one_source_with_several_targets_yields_several_records`: The YAML value is a list; one receipt may match several bank rows.
- [x] `test_transaction_links_rejects_unknown_identifier_form_diagnostic`: Transaction links carry no ABN or TFN, so identifier_form is otherwise unused on this path; but an invalid value (e.g. a typo'd abn_tfn_canonical_form in config/export_config.yml) must still fail fast here, rather than being silently accepted because this path never happens to call canonical_identifier itself.
- [x] `test_derive_links_writes_transaction_jsonl`
- [x] `test_derive_links_creates_parent_directories`: Mirrors derive_csv/derive_jsonl/derive_cord's mkdir(parents=True) behaviour.
- [x] `test_real_derived_output_has_expected_counts`: A silent record drop is the main failure mode of this task: run the real derivation over the actual repo ground truth and pin exact counts per difficulty band, per the Task 1 Step 1 figures.

## `tests/exporters/test_links_self_score.py`

```text
The doc_refs export, scored against its own ground truth, must be perfect.

``linking.link_validator.validate_links`` already computes precision/recall/F1
overall and per difficulty (spec section 8.6, the ``LinkScore``/``DifficultyScore``
dataclasses), this file proves the Task 8 doc_refs export round-trips through
that *existing* validator rather than standing up a second scorer.

Step 1 of this task's brief was to inspect the real interface rather than trust
a guessed one. The actual signature (confirmed via
``inspect.signature(link_validator.validate_links)``) is::

    validate_links(ground_truth: dict, predictions: dict[str, dict]) -> LinkScore

which differs from the brief's guessed shape (a flat list of prediction dicts)
in two ways:

- ``ground_truth`` is keyed by receipt filename (``source_doc`` in doc_refs
  terms); each value is a *list* of link records (only index ``[0]`` is read
  by the validator), each with ``bank_statement``, ``bank_amount`` and
  ``match_difficulty`` keys.
- ``predictions`` is keyed the same way, but each value is a single dict
  ``{"bank_statement": ..., "bank_amount": ...}``, no list wrapper.

``_ground_truth_and_predictions`` below reshapes the exported doc_refs
records (which carry ``target_doc``, ``match_keys.amount`` and
``difficulty``) into that exact shape.

A self-score alone (``score(x, x) == 1.0``) is near-tautological: it proves
the scorer ingests our shape but not that it can catch a wrong mapping. Every
perfect-score assertion below is therefore paired with a mutation test that
proves the corresponding gate can actually fail.
```

- [x] `test_every_difficulty_band_is_populated`: A band with no links would self-score perfectly while testing nothing.
- [x] `test_transaction_links_self_score_is_perfect`: Feeding the export back to itself as predictions must score a perfect 1.0 overall and in every per-difficulty band.
- [x] `test_dropping_one_link_degrades_recall_only`: Removing one prediction must strictly lower recall (a real link is no longer predicted) while leaving precision at 1.0 (no spurious link was added), proving the perfect-score gate can actually fail on a missing link, not just pass on a tautological self-comparison.
- [x] `test_adding_a_spurious_link_degrades_precision_only`: Adding one prediction absent from ground truth must strictly lower precision while recall (every real link is still predicted) stays 1.0.
- [x] `test_corrupting_a_target_doc_degrades_the_score`: Changing one prediction's bank_statement to a wrong value (rather than deleting the prediction outright) must turn that true positive into a false negative, proving the equality check on bank_statement is actually exercised rather than the validator merely checking key presence.
- [x] `test_removing_only_hard_links_degrades_the_hard_band_alone`: Dropping a single hard-difficulty prediction must degrade only the hard per-difficulty band, proving by_difficulty is wired through to real per-link difficulty data rather than merely reported alongside the overall score.

## `tests/exporters/test_native.py`

```text
Tests for the native statement schema (spec section 7).
```

- [x] `test_statement_transactions_are_unzipped_per_row`
- [x] `test_statement_scalars_are_preserved_alongside_transactions`
- [x] `test_non_statement_document_has_no_transactions_array`
- [x] `test_not_found_is_dropped`
- [x] `test_ragged_transaction_lists_are_rejected`
- [x] `test_ragged_transaction_lists_raise_a_four_element_diagnostic`: The mismatch error must be a fail-fast diagnostic, not a bare ValueError: it must name what disagrees, where to fix it, what a valid entry looks like, and how to recover; per the project's fail-fast standard.
- [x] `test_per_member_not_found_sentinel_becomes_empty_string`: The real corpus (ground_truth/bank_statements.yml) writes an absent register member as the literal NOT_FOUND token between pipes, e.g. 'TRANSACTION_AMOUNTS_PAID: 219.04|NOT_FOUND'. That must be scrubbed to "" like a genuinely empty member; the NOT_FOUND sentinel must never survive into a transactions row.
- [x] `test_not_found_sentinel_never_appears_anywhere_in_output`: Sweeps every emitted string (top-level and per-transaction-row) for the literal NOT_FOUND token, rather than checking just the one field the other tests target.
- [x] `test_statement_missing_amounts_received_column_defaults_to_empty`: A statement entry with no TRANSACTION_AMOUNTS_RECEIVED column at all (not merely an empty value for it) must still carry the key with "" on each transaction row.
- [x] `test_statement_type_with_no_register_columns_present_has_no_transactions_key`: A statement-type document with none of the TRANSACTION_* columns present must omit the 'transactions' key entirely, not emit an empty list.
- [x] `test_structural_shape_of_a_statement_record`: Structural assertion alongside the content assertions above: every transaction row has exactly the four expected keys, in the schema's column mapping, and 'transactions' is a list of dicts.
- [x] `test_document_type_membership_constants`: Guards the module's public constants against silent drift.

## `tests/exporters/test_normalise.py`

```text
Tests for the shared normalisation rules (spec section 3).
```

- [x] `test_split_pipe_list_splits_on_pipe`
- [x] `test_split_pipe_list_handles_single_value`: A single-item list has no pipe; CASE002 stores '3.76' with no delimiter.
- [x] `test_split_pipe_list_strips_whitespace`
- [x] `test_zip_line_items_pairs_by_index`
- [x] `test_zip_line_items_returns_empty_for_missing_columns`: If none of the LINE_ITEM_* columns are present, return empty list.
- [x] `test_zip_line_items_rejects_count_mismatch`: A short zip would silently emit a partial menu that scores as a near-match.
- [x] `test_canonical_identifier_spaced_is_unchanged`
- [x] `test_canonical_identifier_digits_only_strips_spaces`
- [x] `test_canonical_identifier_rejects_unknown_form`
- [x] `test_is_present_rejects_absent_markers`
- [x] `test_is_present_accepts_a_real_value`
- [x] `test_present_fields_drops_not_found`
- [x] `test_present_fields_keeps_all_present`
- [x] `test_present_fields_empty_when_all_absent`: present_fields takes dict[str, str]; None-absence is covered by is_present above.

## `tests/layout_dsl/test_binding.py`

- [x] `test_referenced_fields_finds_all_placeholders`
- [x] `test_referenced_fields_ignores_lowercase_braces`
- [x] `test_referenced_fields_on_literal_returns_empty`
- [x] `test_interpolate_substitutes_values`
- [x] `test_interpolate_raises_on_unknown_field`
- [x] `test_interpolate_renders_not_found_as_empty`
- [x] `test_is_present_false_for_missing_blank_and_not_found`
- [x] `test_is_present_true_for_real_value`

## `tests/layout_dsl/test_context.py`

- [x] `test_indent_narrows_and_shifts`
- [x] `test_indent_applies_right_inset`
- [x] `test_divide_splits_evenly_with_gap`
- [x] `test_divide_rejects_gap_wider_than_region`
- [x] `test_right_edge`
- [x] `test_divide_last_column_reaches_region_right`: Floor division drops up to n-1 px, so the last column never reaches the right edge. Westpac's only split is 1600 // 2 = 800 (exact), which is why this was never seen; any odd width or 3+ columns hits it.
- [x] `test_divide_widths_places_explicit_columns`: Invoice totals occupy a fixed 400px column at the page's right edge, which equal division cannot express, this is the exact-fit boundary: sum(widths) == region.width.
- [x] `test_divide_widths_steps_by_width_plus_gap_like_divide`: Consistency with `divide`: each column's x is the previous column's right edge plus the gap, not a fraction of it.
- [x] `test_divide_widths_rejects_a_sum_that_overflows_the_region`
- [x] `test_divide_widths_accepts_a_sum_exactly_equal_to_the_region`: Boundary: sum(widths) + gap * (n - 1) == self.width must not raise.
- [x] `test_region_errors_are_diagnostic`

## `tests/layout_dsl/test_defaults.py`

- [x] `test_block_key_wins_over_defaults`
- [x] `test_defaults_supply_an_absent_block_key`
- [x] `test_falsy_default_is_not_treated_as_absent`: `bold: false` is a real configured value, not a missing key. A truthiness test here would silently fall through to the fail-fast branch.
- [x] `test_missing_default_fails_with_a_four_element_diagnostic`
- [x] `test_block_key_override_wins_when_it_differs_from_the_default_name`: A per-block override under its own short YAML key must win over the layout default even when `PARAMETER_DEFAULTS` namespaces the two names differently, e.g. a panel's `padding:` block key vs. its `panel_padding` layout default. This is the exact bug Task 3's first pass introduced: calling `resolve_param(block, layout, "panel_padding", ...)` with no `block_key` looked for `block["panel_padding"]`, which no panel block ever sets (the YAML key stays `padding:`), so it silently fell through to the shared layout default. The pixel snapshot caught it on NAB's real `group_gap: 0` / `fill_inset: 2` overrides, both of which differ from their shared defaults and would have rendered wrong.
- [x] `test_block_key_falls_through_to_the_default_when_absent`: When `block_key` differs from `key` and the block doesn't set it, the layout default (looked up under `key`, not `block_key`) still applies.
- [x] `test_parameter_defaults_covers_all_primitive_get_sites`: Every layout-parameter `.get()`/`resolve_param()` call in the primitive files must resolve through a name in `PARAMETER_DEFAULTS`. Keyed off the *receiver*, the dict a call reads from, not the bare key name. `block.get(...)` and `resolve_param(block, ...)` read layout configuration written by a layout author in YAML (as does `sub_line`, a table column's nested sub-line spec); `row.get(...)` reads provider-set row *data* and is never checked here, `row` is simply absent from `LAYOUT_RECEIVERS`, rather than its keys ("bold", "date") being excluded by name. Excluding by key name would also silence the guard on `bold`'s *legitimate* block-level use in `draw_text_block`/`draw_banner`, exactly the anti-pattern two earlier Task 2 review rounds caught. See `test_row_data_never_resolves_through_the_layout` for the companion check that `row` never gains a `resolve_param` call in the first place: routing row data through a layout's `defaults:` is a real bug Task 3's first pass introduced (`row["bold"]` silently resolved through the layout, green only because every bank layout happened to seed `bold: false`). Every `resolve_param(receiver, ctx.layout, "key", ...)` call site already passes the literal `PARAMETER_DEFAULTS` name as `key`, namespacing, where a block's own YAML key differs, lives in the separate `block_key=` argument, not in `key`, so no key-name translation table is needed here (unlike the pre-Task-3 `.get()` calls this test used to map through a hand-maintained `KEY_TO_DEFAULTS`, which could silently go stale). The one surviving parameter-shaped `.get()` call is `block.get("params", {})`: a table's own params passthrough to its row provider, not a pixel decision, named explicitly in `NON_PARAMETER_BLOCK_KEYS` rather than silently ignored.
- [x] `test_row_data_never_resolves_through_the_layout`: Provider row data must stay on `row.get(...)` and never route through `resolve_param`. `row["bold"]`/`row["date"]` are facts a row provider stamps onto its own data (e.g. NAB's "Carried forward" row, or ANZ's mixed-weight "BALANCE BROUGHT FORWARD" row), not layout configuration an author writes in YAML. Resolving them through a layout's `defaults:` is a real bug a green pixel snapshot does not, by itself, catch: every bank layout happens to seed `bold: false`, so a silently-wrong resolution renders identically to a correct one until some layout's default differs. This is exactly what Task 3's first pass shipped (`_cell_bold`/`_validate_bold_spec` calling `resolve_param(row, ...)`), caught in review, not by any automated check, which is why this test now exists.
- [x] `test_incomplete_defaults_block_is_rejected`
- [x] `test_bank_layouts_cover_every_parameter_name`: Guards against adding a parameter to PARAMETER_DEFAULTS and forgetting to seed it in the shipped layouts; validate would then fail only at render. Kept alongside the widened check below: this one names the file whose layouts have carried `defaults:` since Task 3, so a bank-only regression still fails under its own name.
- [x] `test_all_layouts_cover_every_parameter_name`: The check above was scoped to bank_statements.yml because receipts and invoices had no `defaults:` yet. All three carry it now (Tasks 14 and 15), so every declarative layout in the repo is covered.

## `tests/layout_dsl/test_engine.py`

- [x] `test_every_schema_primitive_has_a_drawer`
- [x] `test_render_blocks_advances_through_the_list`
- [x] `test_when_suppresses_a_block_whose_field_is_absent`
- [x] `test_when_admits_a_block_whose_field_is_present`
- [x] `test_unknown_primitive_fails_with_diagnostic`
- [x] `test_nested_panel_renders_through_the_walker`
- [x] `test_render_body_missing_body_key_fails_with_diagnostic`
- [x] `test_render_body_renders_a_valid_layout_and_returns_advanced_y`
- [x] `test_render_body_injects_render_children_so_a_panel_renders_its_contents`
- [x] `test_render_body_reports_the_failing_blocks_nested_location`
- [x] `test_render_body_reports_the_nested_location_of_a_missing_default`: A missing `defaults:` entry is the likeliest render-time DSL failure, every primitive parameter resolves through resolve_param, and it was the one error escaping the walker untagged. resolve_param can only name the parameter and the layout; only the walker knows which block asked.
- [x] `test_render_body_reports_nested_location_for_a_fit_error`

## `tests/layout_dsl/test_field_providers.py`

- [x] `test_provider_output_is_merged_into_entry_fields`
- [x] `test_provider_emitting_an_undeclared_key_fails`: `emits` is the contract validate checks placeholders against. A provider returning a key it never declared makes that check a lie.
- [x] `test_emit_colliding_with_a_scored_column_is_rejected`: A derived presentation value must never be mistaken for extraction ground truth, config/field_definitions.yml owns those column names.
- [x] `test_apply_field_providers_merges_multiple_providers`: Two providers with disjoint emits on one layout must both land, the positive case the collision test below exists to distinguish from.
- [x] `test_apply_field_providers_rejects_two_providers_emitting_the_same_key`: A silent dict.update() overwrite here would make one provider's value vanish from the page with no error, exactly the defect this defensive, merge-time check exists to catch for a caller that builds a layout dict by hand and never goes through validate_layout's own static check (see tests/layout_dsl/test_schema.py's test_field_providers_declaring_overlapping_emits_is_rejected_at_validate_time for that one).
- [x] `test_apply_field_providers_with_no_providers_returns_an_unchanged_copy`
- [x] `test_apply_field_providers_rejects_an_unregistered_provider_name`
- [x] `test_get_field_provider_rejects_unknown_name_and_lists_known`
- [x] `test_field_provider_names_includes_registered`
- [x] `test_field_provider_rejects_duplicate_registration`
- [x] `test_field_provider_emits_and_param_keys_return_declared_values`
- [x] `test_field_provider_emits_and_param_keys_are_empty_for_unknown_name`: Mirrors provider_param_keys' identical rationale in providers.py: schema.py's own unknown-provider check already reports this case with its own diagnostic, so these lookups degrade gracefully instead of raising.
- [x] `test_when_admits_a_block_whose_field_is_a_provider_emitted_value`: POS_STAFF exists nowhere in `entry["fields"]`, only probe_pos derives it, yet the block is admitted, proving apply_field_providers ran first.
- [x] `test_when_suppresses_a_block_whose_field_absent_even_with_a_provider_declared`: A field_providers: declaration does not make every field present, only the keys the provider actually emits.
- [x] `test_receipt_pos_matches_the_pinned_derivation`
- [x] `test_receipt_pos_stays_within_its_declared_pools`: Every derived value lands inside the ranges `pos_terminal` declares.
- [x] `test_receipt_pos_is_registered_with_its_declared_emits`
- [x] `test_a_pools_key_param_is_now_rejected_rather_than_documented`: Both providers always load the one pool they were ever going to load, so `pools_key` read as a switch and was not one. Neither declares it now, which means the schema rejects it if it is ever re-added, the same standard applied to layout keys no code path reads.
- [x] `test_receipt_pos_runs_through_apply_field_providers`: The layout-facing path, not just direct provider invocation.
- [x] `test_computed_totals_matches_the_legacy_subtraction`: Matches receipt.py:303's `str(Decimal(total) - Decimal(gst))`.
- [x] `test_computed_totals_emits_nothing_when_a_required_field_is_absent_or_not_found`
- [x] `test_computed_totals_is_registered_with_its_declared_emits`
- [x] `test_computed_totals_absent_case_does_not_violate_the_undeclared_emit_check`: `emits` is an upper bound, not an exact contract: apply_field_providers only rejects a KEY the provider returns that it never declared, it does not require every declared key to appear in every call's result. A layout combining computed_totals with a NOT_FOUND GST_AMOUNT must merge cleanly, with no FieldProviderError and no SUBTOTAL_AMOUNT key in the result.
- [x] `test_receipt_payment_matches_derive_payment`: Phase A parity: the provider must reproduce derive_payment's values exactly for every entry in the corpus, including the linked ones, whose scheme comes from the bank statement rather than the weighted pool.
- [x] `test_receipt_payment_never_emits_a_purchase_total`: The slip's 'Purchase AUD' line binds {TOTAL_AMOUNT} directly (see PaymentDetails' docstring in payment_block.py), a separate PAYMENT_PURCHASE_TOTAL key would be a second copy of a scored value that could drift from the field the benchmark scores.
- [x] `test_receipt_payment_exercises_at_least_one_linked_receipt`: Every receipt in this corpus is linked (test_payment_block.py's test_link_index_covers_every_receipt_stem asserts 55/55), so the parity test above already exercises the linked path for all 55, this just proves the corpus is not accidentally empty of links.
- [x] `test_cash_receipts_suppress_the_card_keys`: The three slip variants are selected by when:, so a cash payment must emit NOT_FOUND for every card key rather than an empty string. No entry in ground_truth/receipts.yml is actually cash: every one of the 55 is linked to a bank transaction, and receipt_method_weights.Cash is 0 precisely because of that, test_payment_block.py's test_no_linked_receipt_is_cash asserts the same invariant. So this exercises the cash path the way test_payment_block.py's own _cash_pools()/_details_of_kind() do: a cash-weighted pool via a monkeypatched load_terminal_pools, and a fabricated case id absent from load_link_index() so derive_payment falls back to the weighted pool instead of a bank-forced scheme.
- [x] `test_card_receipts_suppress_the_cash_and_wallet_keys`
- [x] `test_receipt_payment_is_registered_with_its_declared_emits`
- [x] `test_receipt_payment_runs_through_apply_field_providers`: The layout-facing path, not just direct provider invocation.

## `tests/layout_dsl/test_primitives_container.py`

- [x] `test_container_without_injected_walker_fails_loudly`
- [x] `test_panel_advances_past_its_children`
- [x] `test_panel_honours_fixed_height`
- [x] `test_split_returns_tallest_column`
- [x] `test_split_gives_each_column_its_own_non_overlapping_region`
- [x] `test_split_widths_gives_each_column_its_explicit_region`: Invoice totals occupy a fixed 400px column at the right edge, which equal division (the default path) cannot express.
- [x] `test_split_without_widths_still_divides_equally`: Absent `widths`, draw_split's default path is unchanged.
- [x] `test_split_without_injected_walker_fails_loudly`
- [x] `test_panel_with_fixed_height_overflowing_children_fails`
- [x] `test_panel_border_rectangle_encloses_padded_children`
- [x] `test_split_without_divider_draws_no_line`
- [x] `test_split_with_divider_draws_a_line_at_the_gap_midpoint`: Westpac's rewards panel splits into two equal columns with a ruled line between them.
- [x] `test_split_divider_color_is_configurable`

## `tests/layout_dsl/test_primitives_table.py`

- [x] `test_column_x_resolves_left_offset`
- [x] `test_column_x_resolves_right_offset`
- [x] `test_table_advances_by_header_plus_rows`
- [x] `test_table_records_each_row_description`
- [x] `test_synthetic_opening_row_is_not_recorded`
- [x] `test_synthetic_opening_row_does_not_shift_real_row_indices`: Inserting the synthetic opening row must not renumber the real rows beneath it.
- [x] `test_decimal_cells_format_as_amounts`
- [x] `test_every_frame_and_grouping_combination_renders`
- [x] `test_missing_row_height_raises_diagnostic_with_four_elements`
- [x] `test_dedicated_row_grouping_with_a_row_inset_is_rejected`: An inset moves the cells but not the bold date sub-header row grouping inserts, so the two together have no defined meaning.
- [x] `test_dedicated_row_grouping_rejects_an_inset_inherited_from_the_layout`: The block need not carry the inset itself: `defaults.table_row_inset_y` reaches the same code, which is why this cannot live in schema.py's `_validate_table` (it never sees the layout's defaults).
- [x] `test_dedicated_row_grouping_accepts_a_zero_inset`
- [x] `test_a_row_inset_is_still_allowed_without_dedicated_row_grouping`
- [x] `test_block_row_height_used_when_layout_omits_it`
- [x] `test_debit_cell_renders_with_currency_formatting`: A debit cell must be drawn with the same glyphs as fmt_amount, not the raw string. The bank_transactions provider coerces real (non-sentinel) debit/credit amounts to Decimal so _cell_text formats them through fmt_amount, matching the legacy renderer's `$100.00` rather than a bare `100.00`.
- [x] `test_right_aligned_column_with_field_records_geometry`: Right-aligned amount columns must record geometry, same as the description column.
- [x] `test_synthetic_row_records_no_amount_geometry`
- [x] `test_amount_field_index_stability_with_and_without_synthetic_row`: Real rows must keep identical FIELD[i] keys whether or not a synthetic row precedes them.
- [x] `test_right_aligned_budgeted_column_is_not_silently_ignored`: A `budget` on a right-aligned column must actually fit/record, not go inert.
- [x] `test_last_row_field_records_once_unindexed_on_final_real_row`
- [x] `test_last_row_field_still_targets_the_final_real_row_with_synthetic_prepended`: A prepended synthetic row must not shift which row counts as 'last'. Absolute pixel position legitimately shifts down by one row height (the synthetic row occupies space above it), the invariant under test is that the closing balance is still recorded exactly once, unindexed, and that its horizontal extent (unaffected by the vertical shift) matches.
- [x] `test_frame_and_grouping_axes_compose_into_distinct_output`: Neither axis may be a no-op, and combinations must not collapse onto each other. This replaces the old single-enum "every style draws differently" check: with two independent axes, distinctness has to be demonstrated across combinations, not just across a flat list of named styles.
- [x] `test_currency_plain_strips_dollar_sign`: A `currency: plain` column must drop the $ prefix fmt_amount otherwise adds.
- [x] `test_inline_grouping_blanks_repeated_date_and_skips_its_divider`: Two same-date rows: the second must draw no date text and no divider above it.
- [x] `test_inline_grouping_does_not_insert_a_dedicated_date_row`: Unlike `dedicated_row`, `inline` grouping must draw exactly one row per transaction.
- [x] `test_dividers_are_drawn_across_header_and_body`: Divider x-positions must appear as vertical line calls spanning the table.
- [x] `test_multiline_header_label_draws_each_line`
- [x] `test_filled_frame_fills_the_header_bar_with_fill_color`: The header bar itself must be filled with fill_color, not merely outlined.
- [x] `test_filled_frame_fills_each_dedicated_group_row_not_only_the_header`: NAB fills the header bar AND each date sub-header row it inserts. ENTRY has two distinct dates, so dedicated_row grouping inserts two date sub-header rows; each must be filled in addition to the header bar.
- [x] `test_filled_frame_with_none_grouping_only_fills_the_header`: No dedicated row exists without dedicated_row grouping, so nothing else fills.
- [x] `test_filled_frame_draws_no_dividers`: NAB's header bar has no interior column dividers, unlike `bordered`.
- [x] `test_fill_inset_absent_leaves_the_group_row_fill_at_full_row_height`
- [x] `test_fill_inset_shrinks_the_group_row_fill_by_the_given_pixels`
- [x] `test_fill_height_absent_fills_the_full_header_height`
- [x] `test_fill_height_overrides_the_fill_without_changing_the_row_advance`: NAB fills a 44px bar but advances 50px, header_height alone cannot express that gap.
- [x] `test_label_inset_y_absent_uses_computed_centring`: Unchanged default: filled/bordered headers centre the label block.
- [x] `test_group_gap_default_matches_the_old_hardcoded_ten_pixels`: Absent group_gap must reproduce today's CBA behaviour exactly (10px).
- [x] `test_group_gap_zero_matches_nabs_real_behaviour`: NAB's legacy renderer inserts no gap between consecutive date groups.
- [x] `test_synthetic_row_placement_defaults_to_leading`: Absent synthetic_row_placement: the synthetic row renders before any group header.
- [x] `test_synthetic_row_placement_after_first_group_header`: NAB's real order: the group header for the first date, then Brought forward.
- [x] `test_synthetic_row_placement_after_first_group_header_is_not_recorded`: The deferred synthetic row still carries no ground-truth identity.
- [x] `test_currency_suffix_appends_after_the_formatted_amount`
- [x] `test_bold_row_uses_bold_font_for_unbudgeted_cells`: A row provider marks with `bold: True` (e.g. carried_forward) draws in bold, unlike ordinary rows, traced against legacy's font_body_bold Carried-forward row.
- [x] `test_bold_row_threads_through_the_budgeted_path_too`: A budgeted (fitted) cell must also honour the row's bold flag, not just plain cells.
- [x] `test_rows_without_a_bold_key_render_regular_as_before`: Absent `bold` on a row (every row type except carried_forward): unchanged behaviour.
- [x] `test_currency_suffix_absent_by_default`: COLUMNS' balance column has no currency_suffix, must render unchanged.
- [x] `test_is_last_targets_a_trailing_synthetic_row_over_the_last_real_row`: carried_forward's trailing row, not the last transaction, gets last_row_field.
- [x] `test_is_last_without_carried_forward_still_targets_the_last_real_row`: No trailing synthetic row: unchanged behaviour, same as before this round.
- [x] `test_sub_line_draws_beneath_the_main_cell_and_extends_row_height`: A column's sub_line renders its own text and adds its declared height once.
- [x] `test_sub_line_absent_when_row_has_no_data_for_its_key`: Without `references: true`, no row carries a `reference` key, no sub_line, no extra height.
- [x] `test_label_inset_y_overrides_computed_centring`: NAB's real offset (y+10 inside a 44px bar) the centring formula cannot reproduce.
- [x] `test_ruled_frame_draws_no_line_under_each_row`: Legacy CBA and ANZ both draw zero per-row rules; only NAB/Westpac's own frames (filled/bordered) decorate individual rows.
- [x] `test_header_rule_top_false_skips_the_rule_above_the_labels`
- [x] `test_header_rule_top_defaults_true_and_draws_both_rules`
- [x] `test_header_rule_gap_overrides_the_default_16px_advance`
- [x] `test_rule_above_draws_a_black_rule_before_the_row_with_a_12px_gap`
- [x] `test_rule_above_absent_by_default_draws_no_rule`
- [x] `test_capture_false_records_no_field_boxes`: tax_invoice_mixed renders the same line items twice. The second table must not record boxes, one ground-truth value has one box.
- [x] `test_capture_default_true_still_records_field_boxes`: Default (capture omitted) must keep recording, capture: false is opt-in, not a silent behaviour change for every other table.
- [x] `test_capture_false_still_renders_the_table_geometry`: capture: false suppresses recording only, the table itself still draws (same y-advance as the default), so it is not a silent no-op render.
- [x] `test_bank_transaction_totals_row_renders_through_the_table`
- [x] `test_cell_bold_collection_bolds_only_the_named_column`: ANZ's 'BALANCE BROUGHT FORWARD' row: label bold, balance value not, a single row with two different font weights, which only a per-column (not per-row) bold spec can express.
- [x] `test_cell_bold_true_still_bolds_every_cell`: The whole-row form (NAB's Carried forward, ANZ's totals row) is unchanged.
- [x] `test_cell_bold_empty_collection_bolds_nothing_and_does_not_crash`
- [x] `test_cell_bold_unknown_column_name_raises_four_element_diagnostic`: A misspelled *_bold entry (e.g. a params: typo) must fail loudly, not silently render the row entirely unbolded, indistinguishable, at a glance, from the typo never having been caught at all.
- [x] `test_cell_bold_unknown_column_name_raises_before_drawing_any_cell`: The check happens before the row's cells draw, a bad row is rejected outright, not partially rendered then rejected.
- [x] `test_rule_above_does_not_draw_before_an_invalid_bold_spec_raises`: A row combining rule_above with an invalid bold collection must raise before marking the canvas, not draw the separator rule first.
- [x] `test_budgeted_cell_honours_the_layouts_monospace_face`: A budgeted cell used to omit mono= entirely, silently drawing every receipt line item in the sans face.
- [x] `test_prefix_key_records_the_prefixs_own_box_inside_the_cell`
- [x] `test_prefix_is_not_recorded_when_the_row_has_none`
- [x] `test_prefix_is_not_recorded_on_a_synthetic_row`: Synthetic rows carry no index and are never recorded; the prefix box must follow the same rule as the cell's own `field`.
- [x] `test_table_draws_every_label_and_cell_at_its_own_role`: draw_table used to resolve `resolve_role(layout, "body")`; a hardcoded role name deciding a pixel outcome. An invoice table draws at `subheader` (28px) on a page whose `body` role is the 20px supplier-name line.
- [x] `test_table_role_block_key_wins_over_the_layout_default`: The test above covers the omitted case (defaults.role, which is what every bank and receipt table relies on); this covers the override.
- [x] `test_header_bold_false_draws_regular_weight_column_headings`
- [x] `test_header_bold_defaults_to_the_layout_default_which_is_bold`
- [x] `test_row_inset_y_shifts_cell_ink_without_changing_the_row_pitch`: Legacy draws each cell at `y + 12` inside a 52px row and still advances exactly 52; the inset must not leak into the table's own flow.
- [x] `test_label_x_and_label_align_position_the_heading_apart_from_its_cells`: The legacy invoice header draws "Total" left-aligned at the column's own left edge while its amounts right-align 200px further along.
- [x] `test_cell_line_spacing_font_records_the_fitted_fonts_own_height`: A budgeted cell in a 52px invoice row records a 28px-tall box, because legacy passed no line_spacing to draw_fitted_left at all.
- [x] `test_cell_line_spacing_row_height_records_the_row_pitch`: The default, and what every bank and receipt table's legacy renderer did.
- [x] `test_unknown_cell_line_spacing_raises_a_four_element_diagnostic`: Checked at render time as well as in schema.py, because the value may come from defaults.table_cell_line_spacing, which schema.py never reads.

## `tests/layout_dsl/test_primitives_text.py`

- [x] `test_resolve_role_returns_font_size`
- [x] `test_resolve_role_fails_fast_on_unknown_role`
- [x] `test_text_advances_y`
- [x] `test_line_advance_resolves_by_the_blocks_own_role`: A layout's line_advance is a role -> pixels mapping, not one flat number: a header-role block and a footer-role block need different advances, since each was historically int(that role's own font size * 1.4), a flat per-layout number cannot express both at once.
- [x] `test_line_advance_missing_role_raises_a_four_element_diagnostic`: A role present in font_sizes but absent from defaults.line_advance must fail fast, not silently fall back to some other role's advance.
- [x] `test_line_advance_block_override_wins_over_the_layout_default`: A block's own bare-integer line_advance wins outright, without any role lookup, the escape hatch for the rare line that is not simply 'this role's usual advance'.
- [x] `test_line_advance_comes_from_the_layout_not_a_1_4_ratio`: Receipts declare `line_advance: {body: 20}` against an 18pt body font, a ratio of 1.11, not the 1.4 the old `line_height(size)` hardcoded. If draw_text_block silently reverted to that ratio, this would advance to 100 + int(18 * 1.4) == 125, not the declared 120.
- [x] `test_font_for_resolves_mono_from_the_layout_default`
- [x] `test_font_for_block_override_wins_over_the_layout_default`
- [x] `test_monospace_is_honoured_end_to_end`: Every load_font call in this module used to omit mono=, so a layout declaring `mono: true` (as all six receipt layouts do, via `font_family: monospace`) still silently rendered the sans face. Proven by an actual pixel difference, not just that a flag was threaded.
- [x] `test_text_records_geometry_when_field_given`
- [x] `test_text_without_field_records_nothing`
- [x] `test_text_bold_uses_the_bold_font`: Westpac's brand header and section titles are bold in the legacy renderer. `bold` is opt-in and defaults to false, so it must not change advance or plain-text rendering for every other consumer of `text`.
- [x] `test_budgeted_text_wraps_and_advances_by_the_wrapped_height`: shrink_then_wrap with max_lines 2 puts a long supplier name on two lines. The cursor must advance by both, or every block below it overlaps.
- [x] `test_budget_names_an_undeclared_field_budget`
- [x] `test_budgeted_text_centers_within_the_full_page_width_not_the_region`: draw_fitted_center centres in a canvas width, not a region, receipts centre within the full page width (receipt.py:161's `width` argument), not the margin-inset region. LAYOUT's region is x=100, width=1600 inside an 1800px page (page_dimensions.width), a symmetric 100px margin each side. `region.x * 2 + region.width` reconstructs that 1800px page width; if draw_text_block instead centred within the 1600px region alone, this x would differ (100 + (1600-150)//2 == 825 too, by coincidence of the symmetric margin, see the left/right tests below for the case that actually discriminates budgeted from unbudgeted rendering).
- [x] `test_budgeted_text_left_shrinks_to_fit_and_stays_anchored_at_region_x`: Left alignment always anchors at region.x regardless of font size, so position alone cannot prove the budget was honoured, the drawn extent must actually have shrunk to fit, not stayed at the too-wide nominal 193px ("Robin Wood" at 32pt).
- [x] `test_budgeted_text_right_shrinks_to_fit_and_anchors_at_region_right`
- [x] `test_budgeted_text_records_the_full_wrapped_extent`
- [x] `test_unbudgeted_text_is_unaffected_by_budget_support`: A block without `budget:` must render byte-identical to before, the engine gap this task closes must be inert unless a layout opts in.
- [x] `test_pair_renders_label_and_value`
- [x] `test_pair_records_only_the_value_not_the_label`
- [x] `test_pair_records_nothing_without_a_field`
- [x] `test_pair_budget_wraps_only_the_value_and_advances_by_the_wrapped_height`: Apply the fit budget to the pair's value only, the label always draws in full, unshrunk and unwrapped.
- [x] `test_pair_budget_positions_the_value_after_the_full_label`
- [x] `test_pair_right_aligns_its_value_to_the_region`: value_align: right must draw two separate strings, label (plus its separator, here "") at region.x, value right-aligned at region.right, not the single joined string the default left style draws.
- [x] `test_pair_separator_applies_on_the_right_aligned_path_too`: One label convention, not one per value_align. Before `pair_separator` existed the right-aligned path drew the label verbatim, so invoice layouts wrote their own colons into `label:` while left-aligned ones did not. The separator is now the layout's on both paths, and it is the drawn string that must carry it, an assertion on the label alone would pass even if the separator were dropped.
- [x] `test_pair_separator_is_required_not_defaulted`: A layout that declares no pair_separator fails fast rather than printing whichever punctuation this module happens to prefer.
- [x] `test_pair_min_gap_pushes_a_long_label_left`: invoice.py:283-285 computes label_x so a long label never merges with a right-aligned amount into one OCR token. Region is deliberately narrower than label + value so the collision-avoidance actually fires, proven by a sanity assertion, not assumed. The region width is *derived* from the measured strings rather than hardcoded: a fixed width is only narrow enough for the face it was tuned against, and a narrower face (this corpus moved from DejaVu to Carlito) silently turns the collision into a comfortable fit, so the test stops exercising the branch it names.
- [x] `test_pair_min_gap_zero_never_moves_the_label`: `min_gap: 0` demands no gap and so never repositions the label, which is what both legacy renderers do: `draw_line_item` (receipts) and the invoice totals' "separate" branch each draw the label at a fixed x and let a long value run into it. Enforcing a zero gap instead would shift the label left of where legacy put it; real on 1 of the 55 corpus invoices, whose 48px "Total:" plus $19,176.69 needs 456px of a 400px totals column. As above, the region width is derived so the overlap this test depends on survives a change of typeface.
- [x] `test_pair_value_align_right_records_the_values_own_box`: Both alignments must record the value's own extent, not the label's, the right-aligned counterpart to test_pair_records_only_the_value_not_the_label.
- [x] `test_pair_unaligned_left_is_unaffected_by_value_align_support`: Default (left) rendering must stay byte-identical, the engine gap this task closes must be inert unless a layout opts in.
- [x] `test_block_advances_once_per_line`
- [x] `test_spacer_advances_by_height`
- [x] `test_rule_advances_by_padding`
- [x] `test_rule_advances_by_custom_thickness`
- [x] `test_rule_default_fill_char_none_draws_a_line_not_glyphs`: LAYOUT's defaults.rule_fill_char is "none" (bank statements' own value), confirms the unaligned/undecorated path is unaffected.
- [x] `test_rule_fill_char_draws_glyphs_not_a_line`: fill_char must dispatch to a row of repeated glyphs (common.py's draw_separator), not the drawn `line` the default style uses, and reuse its glyph-count arithmetic rather than reimplementing it.
- [x] `test_rule_fill_char_advances_by_line_advance_not_thickness`: A character separator occupies a text line, proven by a custom thickness making no difference to the advance when fill_char is set.
- [x] `test_banner_leaves_the_y_cursor_untouched`: Legacy jumps to a hardcoded y after its header bar rather than flowing from the bar's own height; a spacer after the banner covers that jump.
- [x] `test_banner_paints_a_full_bleed_rectangle_at_the_page_top`
- [x] `test_banner_draws_text_at_the_declared_text_y_and_region_x`
- [x] `test_banner_text_color_and_role_are_overridable`
- [x] `test_banner_content_is_interpolated`
- [x] `test_format_currency_symbol_keeps_the_dollar_sign`
- [x] `test_format_currency_plain_drops_the_dollar_sign`
- [x] `test_pair_currency_formats_the_value_not_the_label`
- [x] `test_pair_without_currency_draws_the_value_verbatim`: A provider-formatted value must not be re-formatted, the receipt's cash lines bind an already-`fmt_amount`ed PAYMENT_TENDERED.
- [x] `test_pair_currency_on_a_non_amount_is_a_four_element_diagnostic`
- [x] `test_pair_currency_is_skipped_for_an_absent_value`: An empty interpolation (a NOT_FOUND field) must not raise: a `when:` guard, not the formatter, is what suppresses an absent value.
- [x] `test_pair_bold_draws_both_label_and_value_in_the_bold_face`: Legacy drew the receipt's TOTAL line's label and amount in font_bold.
- [x] `test_pair_defaults_to_regular_weight`

## `tests/layout_dsl/test_providers.py`

- [x] `test_pipe_fields_zips_list_fields_into_rows`
- [x] `test_pipe_fields_rejects_ragged_lists`
- [x] `test_pipe_fields_requires_fields_mapping`
- [x] `test_get_provider_rejects_unknown_name_and_lists_known` (BUG: diagnostic lacks Recover label; the bug half is in `test_get_provider_rejects_unknown_name_and_lists_known_bug`, strict xfail)
- [x] `test_provider_names_includes_registered`
- [x] `test_row_provider_rejects_duplicate_registration` (BUG: diagnostic lacks What/Where/Expected/Recover; the bug half is in `test_row_provider_rejects_duplicate_registration_bug`, strict xfail)
- [x] `test_bank_transactions_computes_running_balances_backwards`
- [x] `test_bank_transactions_prepends_opening_row_when_requested`
- [x] `test_bank_transactions_brought_forward_uses_its_own_label`
- [x] `test_bank_transactions_rejects_both_synthetic_rows` (BUG: diagnostic lacks What/Where/Expected/Recover; the bug half is in `test_bank_transactions_rejects_both_synthetic_rows_bug`, strict xfail)
- [x] `test_bank_transactions_without_synthetic_row_marks_all_real`
- [x] `test_malformed_amount_fails_loudly_rather_than_becoming_zero`
- [x] `test_absent_value_sentinels_still_read_as_zero`
- [x] `test_carried_forward_appends_a_trailing_synthetic_row`
- [x] `test_carried_forward_row_is_bold_unlike_leading_synthetic_rows`: Legacy draws Carried forward in font_body_bold; Brought forward/Opening Balance stay regular.
- [x] `test_carried_forward_combines_with_a_leading_synthetic_row`: NAB shows both: Brought-forward leading, Carried-forward trailing.
- [x] `test_carried_forward_without_transactions_appends_nothing`
- [x] `test_references_adds_a_reference_key_to_every_real_row`
- [x] `test_references_absent_by_default`
- [x] `test_references_not_added_to_synthetic_rows`
- [x] `test_brought_forward_label_is_printed_verbatim`
- [x] `test_opening_balance_label_is_printed_verbatim`
- [x] `test_differing_labels_leave_the_computed_balance_untouched`
- [x] `test_printed_text_params_are_required_not_defaulted`: Every string this provider prints comes from the layout, or it fails fast. Each of these six params decides text on a rendered bank statement page. A Python fallback would put the decision back in this module, so the provider refuses to guess.
- [x] `test_balance_suffix_formats_a_positive_balance_with_the_credit_suffix` (adjusted: fmt_amount prints a leading dollar sign)
- [x] `test_balance_suffix_formats_a_negative_balance_with_the_debit_suffix_and_abs_value` (adjusted: fmt_amount prints a leading dollar sign)
- [x] `test_balance_suffix_applies_to_a_leading_synthetic_row_too` (adjusted: fmt_amount prints a leading dollar sign)
- [x] `test_balance_suffix_absent_by_default_leaves_balance_as_decimal`
- [x] `test_bank_transaction_totals_sums_debits_and_credits`
- [x] `test_bank_transaction_totals_row_is_synthetic_bold_and_ruled_above`
- [x] `test_bank_transaction_totals_label_overrides_the_default`
- [x] `test_bank_transaction_totals_ignores_not_found_amounts`
- [x] `test_empty_transaction_list_with_synthetic_row_requested_returns_no_rows`
- [x] `test_bank_transaction_totals_label_must_come_from_yaml`
- [x] ~~test_quantity_prefix_is_applied_only_above_one~~ dropped (7 October 2026): receipts no longer prefix a description with "2x "; a line of more than one unit prints a unit line beneath it. Replaced by `test_unit_line_is_built_only_above_one`
- [x] ~~test_applied_quantity_prefix_is_re_exposed_for_geometry_capture~~ dropped (7 October 2026): the provider no longer builds a quantity prefix. Replaced by `test_unit_line_spans_locate_each_value`
- [x] `test_amount_columns_are_coerced_to_decimal`: The legacy renderer printed f"{Decimal(total):,.2f}", not the raw string, so a one-decimal-place amount must not render as `9.5`.
- [x] `test_absent_amounts_are_left_as_sentinels`: `_cell_text` maps both to a cell that draws nothing; coercing them to Decimal("0") would print a fabricated $0.00.
- [x] `test_malformed_amount_is_a_diagnostic_not_a_silent_zero`
- [x] ~~test_missing_prefix_format_fails_diagnostically~~ dropped (7 October 2026): the param is now `unit_line_format`. Replaced by `test_missing_unit_line_format_fails_diagnostically`
- [x] `test_missing_fields_delegates_validation_to_pipe_fields`: Verify that missing 'fields' delegates to pipe_fields for diagnostic error.
- [x] `test_decimal_keys_coerce_named_columns_to_decimal`
- [x] `test_decimal_keys_leave_unnamed_columns_and_sentinels_alone`
- [x] `test_absent_decimal_keys_changes_nothing`
- [x] `test_decimal_keys_naming_an_unmapped_row_key_fails_diagnostically`: A typo here would otherwise silently coerce nothing; indistinguishable from the amounts having been drawn correctly all along.
- [x] `test_malformed_decimal_key_amount_names_its_source_field`

## `tests/layout_dsl/test_schema.py`

- [x] `test_accepts_a_minimal_valid_body`
- [x] `test_rejects_unknown_primitive_and_lists_allowed`
- [x] `test_rejects_block_without_type`
- [x] `test_rejects_missing_required_key`
- [x] `test_rejects_unknown_field_reference`
- [x] `test_rejects_unknown_when_field`
- [x] `test_rejects_unregistered_row_provider`
- [x] `test_recurses_into_containers`
- [x] `test_recurses_into_split_columns`
- [x] `test_rejects_table_column_without_key`
- [x] `test_table_requires_frame`
- [x] `test_table_requires_grouping`
- [x] `test_rejects_unknown_frame`
- [x] `test_rejects_unknown_grouping`
- [x] `test_bordered_frame_with_inline_grouping_is_accepted`: Westpac premium's date-grouped bordered table, must not be rejected.
- [x] `test_filled_frame_without_fill_color_is_rejected`
- [x] `test_fill_color_without_filled_frame_is_rejected`
- [x] `test_filled_frame_with_fill_color_is_accepted`
- [x] `test_accepts_fill_inset_fill_height_and_label_inset_y`: Fix round 1's three geometry overrides, all optional, all schema-legal.
- [x] `test_rejects_table_divider_without_x_or_x_right`
- [x] `test_accepts_table_dividers_with_x_and_x_right`
- [x] `test_rejects_dividers_on_a_non_bordered_frame`: dividers only cuts the header/body into columns under frame: bordered.
- [x] `test_rejects_fill_height_on_a_non_filled_frame`
- [x] `test_rejects_fill_inset_on_a_non_filled_frame`
- [x] `test_accepts_fill_height_and_fill_inset_with_filled_frame`
- [x] `test_rejects_group_gap_on_non_dedicated_row_grouping`
- [x] `test_accepts_group_gap_with_dedicated_row_grouping`
- [x] `test_rejects_synthetic_row_placement_on_non_dedicated_row_grouping`
- [x] `test_rejects_unknown_synthetic_row_placement_value`
- [x] `test_accepts_synthetic_row_placement_after_first_group_header`
- [x] `test_rejects_column_sub_line_without_key`
- [x] `test_accepts_column_sub_line_with_key`
- [x] `test_accepts_split_divider_keys`
- [x] `test_accepts_split_with_widths_matching_children_count`
- [x] `test_rejects_split_widths_with_wrong_entry_count`
- [x] `test_rejects_split_widths_that_is_empty`
- [x] `test_rejects_split_widths_with_a_non_positive_entry`
- [x] `test_rejects_split_widths_with_a_non_int_entry`
- [x] `test_accepts_table_capture_true_and_false`
- [x] `test_rejects_table_capture_that_is_not_a_bool`
- [x] `test_accepts_the_invoice_table_typography_keys`
- [x] `test_rejects_unknown_cell_line_spacing`
- [x] `test_accepts_a_column_label_anchor_that_differs_from_its_cells`
- [x] `test_rejects_unknown_column_label_align`
- [x] `test_rejects_non_int_column_label_x`
- [x] `test_a_tables_own_role_is_what_the_line_advance_coverage_check_requires`: A table draws its labels and cells at its own role, and resolves that role's advance, so covering only the layout's default role is not enough.
- [x] `test_column_budget_matching_its_geometry_is_accepted`
- [x] `test_column_budget_wider_than_its_geometry_is_rejected`
- [x] `test_column_naming_a_missing_budget_is_rejected`
- [x] `test_panel_padding_wider_than_the_page_is_rejected`
- [x] `test_split_with_too_large_a_gap_is_rejected`
- [x] `test_split_widths_summing_over_the_region_is_rejected`: Geometry-level check, distinct from the body-level widths/children count check: the widths themselves are shaped correctly but overflow the page.
- [x] `test_split_widths_exactly_filling_the_region_is_accepted`
- [x] `test_split_gap_comes_from_the_layout_default_when_the_block_omits_it`: draw_split resolves `gap:` against `defaults.split_gap`; so must validation. Checking a gap-less split at 0 would pass widths the renderer then cannot fit, and the overflow would surface as a wrong-looking page rather than a validate-time error naming the block.
- [x] `test_panel_padding_comes_from_the_layout_default_when_the_block_omits_it`: The panel counterpart: draw_panel resolves `padding:` against `defaults.panel_padding`, so a padding-less panel in a layout declaring a large one must be checked at that padding, not at 0.
- [x] `test_budgeted_table_nested_in_a_split_with_widths_uses_the_explicit_column_width`: A budget that fits equal division (800px columns) but not the invoice's real fixed-width totals column (400px) must be rejected, not silently validated against the wrong (equal-division) width.
- [x] `test_validate_layout_rejects_a_layout_with_no_body_key`
- [x] `test_validate_layout_rejects_a_layout_with_no_content_width_key`
- [x] `test_budgeted_table_nested_in_a_panel_uses_the_narrowed_width`
- [x] `test_text_budget_matching_its_region_is_accepted`
- [x] `test_text_budget_wider_than_its_region_is_rejected`
- [x] `test_text_budget_naming_a_missing_budget_is_rejected`
- [x] `test_pair_budget_matching_its_region_is_accepted`
- [x] `test_pair_budget_wider_than_its_region_is_rejected`
- [x] `test_accepts_pair_value_align_and_min_gap_keys`
- [x] `test_rejects_unknown_pair_value_align`
- [x] `test_accepts_rule_fill_char_key`
- [x] `test_budgeted_right_aligned_pair_with_a_min_gap_is_rejected`: draw_pair's budgeted path draws the label first and fits the value into what is left; it never repositions the label, so a declared min_gap is dropped with nothing on the page to show it. The combination was a written constraint while the primitive was built; this is that constraint made enforceable.
- [x] `test_budgeted_right_aligned_pair_with_a_zero_min_gap_is_accepted`: Zero is the resolved value every shipped layout carries, and it asks for nothing the budgeted path cannot deliver.
- [x] `test_budgeted_right_aligned_pair_rejects_a_block_level_min_gap_too`: A block's own min_gap is as unhonoured as a layout-level one.
- [x] `test_unbudgeted_right_aligned_pair_may_still_carry_a_min_gap`: The live invoice case: no budget, so min_gap does reposition the label at render time and must stay legal.
- [x] `test_left_aligned_pair_budget_is_still_checked_against_the_full_width`: pair_value_align: left (the default) must reproduce the pre-existing, unnarrowed check, min_gap plays no part in it.
- [x] `test_text_budget_nested_in_a_panel_uses_the_narrowed_width`
- [x] `test_text_without_budget_is_unaffected_by_budget_validation`
- [x] `test_accepts_a_minimal_banner_block`
- [x] `test_banner_requires_content_height_and_color` (adjusted: the banner key is `height`, not `content_height`)
- [x] `test_banner_rejects_unknown_field_reference`
- [x] `test_banner_accepts_its_optional_keys`
- [x] `test_accepts_header_rule_top_and_header_rule_gap_with_ruled_frame`
- [x] `test_rejects_header_rule_top_on_a_non_ruled_frame`
- [x] `test_rejects_header_rule_gap_on_a_non_ruled_frame`
- [x] `test_line_advance_as_a_single_number_is_rejected`: A layout author's most natural mistake for a mostly-one-font-size document (receipts), `line_advance: 20`, must fail at validate time, not silently give every role the same flat advance at render time.
- [x] `test_line_advance_missing_a_used_role_is_rejected`
- [x] `test_line_advance_missing_role_inside_a_sub_line_is_rejected`: A role buried inside a table column's `sub_line` (bank_statements.yml's real `sub_line: {role: sub_description}`) must not be invisible to a naive top-level-only role scan.
- [x] `test_line_advance_role_inside_a_container_is_reached`: A role nested inside a panel/split (not just a top-level block) must still be caught, the same reach `_validate_blocks`/`_validate_children` give every other structural check.
- [x] `test_line_advance_block_override_exempts_that_role_from_coverage`: A block's own bare-integer line_advance: override means its role is never actually looked up in the layout's mapping at render time (line_advance()'s block-key-wins resolution), so validate_layout must not demand the layout cover it either.
- [x] `test_bank_layouts_pass_the_line_advance_checks`: The 8 shipped bank layouts must still validate clean under both new checks, see test_every_bank_layout_validates in tests/test_bank_dsl_validation.py for the full end-to-end version; this one pins it to line_advance specifically.
- [x] `test_validate_layout_rejects_a_layout_with_no_field_providers_key`: field_providers is required, see engine.py's apply_field_providers and the 8 bank layouts, which all set field_providers: [] explicitly. Calls validate_layout directly (bypassing _validate_layout's auto-default) on BUDGETED_LAYOUT, which has content_width/defaults/field_budgets/body but deliberately no field_providers key.
- [x] `test_field_providers_entry_naming_an_unregistered_provider_is_rejected`
- [x] `test_field_providers_entry_with_an_unknown_param_key_is_rejected`
- [x] `test_field_providers_entry_accepts_its_declared_param`
- [x] `test_a_referenced_providers_emits_resolve_as_known_fields`: A {FIELD} naming a value this layout's own field_providers: derives must resolve, known_fields is widened by the provider's emits.
- [x] `test_field_providers_declaring_overlapping_emits_is_rejected_at_validate_time`: Two providers on one layout declaring the same emits name must fail here, from the emits= declarations alone, statically, before any provider is ever called, so a receipt layout combining receipt_pos, receipt_payment, and computed_totals (Tasks 10/11) cannot silently ship a page where one provider's value overwrites another's with no error. apply_field_providers in field_providers.py keeps a second, defensive check at merge time for a caller that bypasses validate_layout (see test_apply_field_providers_rejects_two_providers_emitting_the_same_key in tests/layout_dsl/test_field_providers.py).
- [x] `test_an_unreferenced_providers_emits_do_not_resolve_as_known_fields`: The permissive-direction trap this check exists to avoid: a provider's emits must only widen known_fields for a layout that actually declares that provider in field_providers, never globally for every layout, which would let a typo coincidentally matching another layout's derived field name silently pass.
- [x] `test_pair_accepts_the_two_currency_styles`
- [x] `test_pair_rejects_an_unknown_currency_style`
- [x] `test_prefix_keys_are_accepted_on_a_left_budgeted_column`
- [x] `test_prefix_key_without_prefix_field_is_rejected`
- [x] `test_prefix_field_without_prefix_key_is_rejected`
- [x] `test_prefix_keys_are_rejected_on_an_unbudgeted_column`: Only `draw_fitted_left` measures a prefix sub-box; an unbudgeted cell draws through `draw_text_left`, which would silently record nothing.
- [x] `test_prefix_keys_are_rejected_on_a_right_aligned_column`
- [x] `test_prefix_field_must_name_a_known_field`
- [x] `test_layout_carries_no_key_the_engine_never_reads`: The invoice YAML shipped six-column table specs, table_start_y, and per- section font sizes that no code path read. A layout key that does nothing is worse than no key: it tells an operator the document is configured a way it is not.
- [x] `test_known_layout_keys_admits_a_from_layout_target`: `from_layout:` names an arbitrary layout key, so the permitted set has to grow with the body rather than being hand-listed.
- [x] `test_known_layout_keys_admits_a_suppress_if_equals_target`
- [x] `test_known_layout_keys_reaches_into_panel_and_split_children`
- [x] `test_validate_layout_rejects_a_key_nothing_reads`
- [x] `test_validate_layout_accepts_a_key_the_body_names_via_from_layout`

## `tests/test_bank_dsl_validation.py`

```text
Bank layouts validate through the DSL, and legacy dispatch keys are gone.

Companion to Task 14's deletion of generators/bank_statement.py's per-bank
renderers: `pipeline validate` must reject a malformed layout body before any
rendering starts, and the YAML must carry no key only the deleted legacy
renderers ever read.
```

- [x] `test_every_bank_layout_validates`
- [x] `test_no_bank_layout_retains_dead_dispatch_keys`
- [x] `test_bank_statement_module_no_longer_exposes_per_bank_renderers`
- [x] `test_validation_rejects_a_broken_layout`
- [x] `test_unclosed_placeholder_is_rejected`: An unclosed '{FIELD' renders as a silent literal today. Hand-authored receipt and invoice bodies are exactly where this typo happens.

## `tests/test_bank_fit.py`

```text
Bank statement fit-safety: the old silent character-truncation of transaction
descriptions is replaced by lossless wrap. Budgets present, nothing clips, and
docs whose descriptions already fit stay byte-identical.
```

- [x] `test_every_bank_layout_has_transaction_desc_budget_that_wraps`
- [x] `test_no_bank_description_overflows_after_fitting`
- [x] `test_unchanged_bank_docs_byte_identical` (adjusted: pre-fit renderer is gone; compares with the pinned pixel snapshot)

## `tests/test_bank_pixel_snapshot.py` (OBSOLETE: replaced by the fresh three-type pixel snapshot)

```text
Persisted pixel-snapshot regression test; prerequisite for Task 14.

`tests/test_bank_dsl_equivalence.py` only compares recorded field geometry.
It cannot see colour, font weight, rules, fills, or anything drawn in a
region that carries no ground-truth field; and four consecutive banks
shipped exactly that kind of defect (Westpac grey-vs-black text, NAB/ANZ
bold-weight mismatches, a stray #CCCCCC row rule CBA shipped for four tasks:
53,217 spurious pixels against legacy's 386). Every one was caught by ad-hoc
pixel inspection, never by an assertion, and none of that evidence persisted.

Task 14 deletes `generators/bank_statement.py`. After that there is no
oracle left to diff against, so this snapshot is captured now, while legacy
still exists, and split into two halves with different lifetimes:

- `test_dsl_pixel_snapshot_unchanged` is the permanent regression guard. It
  hashes the full page of every DSL-rendered bank ground-truth entry and
  asserts the hash is unchanged. This is what survives Task 14 and catches
  future regressions in colour, weight, rules and fills anywhere on the
  page; not just at recorded field boxes.
- `test_legacy_dsl_identity_snapshot_is_well_formed` checks the *shape* of
  the historical legacy-vs-DSL identity record captured by
  `regenerate_bank_pixel_snapshot.py`. It deliberately does not assert that
  any entry's legacy/DSL identity status holds; most entries are not
  byte-identical and that is expected (see the snapshot's `_meta` and
  `summary` for the recorded facts: today ANZ is 14/14 identical, 0 diff
  pixels; CBA/Westpac/NAB are not). This half retires with Task 14.

To re-bless after an *intended* rendering change:

    conda run -n synthetic python tests/regenerate_bank_pixel_snapshot.py --confirm

There is no other way to update these fixtures; `--confirm` is required and
defaults to False, so a bare run only prints a dry-run summary.
```

- [ ] `test_dsl_pixel_snapshot_unchanged`: The permanent regression guard: every DSL-rendered page hashes to its recorded baseline. Checks the full corpus, not a sample, for the same reason `test_bank_dsl_equivalence.py` did; it was the long, wrapped-row statements least likely to show up in a small sample. Renders with `geometry_out={}` (a real dict, not omitted) so this exercises the same recorder-present path `generators/pipeline.py` always uses in production; not a recorder-absent shortcut. A prior version of this test called `render_via_dsl` with no `geometry_out` at all, which certified a render path production never takes: `primitives_table.py`'s `last_row_field` handling used to draw certain cells a second time only when a recorder was present, and PIL alpha-composites each `draw.text` call's antialiased glyph mask onto what's already there, so that second draw measurably darkened every soft edge; invisible to a recorder-absent snapshot, live on every production image. Fixed in `_draw_row` (single draw, field name selected up front); this call now guards against that class of gap recurring.
- [ ] `test_legacy_dsl_identity_snapshot_is_well_formed`: Sanity-checks the historical legacy-vs-DSL record's shape and internal consistency. Deliberately does NOT assert that any entry's identical/ diff_pixel_count value holds; that data documents what the migration changed (most entries are not byte-identical, by design), not a regression contract. It retires with Task 14 alongside the legacy renderers it was captured from.
- [ ] `test_legacy_dsl_identity_summary_has_expected_shape`: Coverage check only: each bank's summary counts entries and stays within [0, entries]; not a claim about what the counts should be.

## `tests/test_bank_supplier_header.py`

```text
Bank statements must follow the brand-vs-supplier pattern credit-card
statements used before Stage 4 deleted that document type.

Two defects this locks down:

1. Each bank renderer hard-coded its letterhead brand as a Python string
   literal (``"Commonwealth Bank"`` etc.), shadowing the layout YAML; a
   "YAML is the single source of truth" violation. The header text must come
   from the layout's ``logo_text`` key.
2. ``SUPPLIER_NAME`` was never drawn, so a statement whose content supplier
   differs from the letterhead brand (50 of 55 in the corpus) silently
   contradicted the visible page. The supplier must be rendered as a
   secondary line whenever it differs from ``layout["bank"]`` (and
   suppressed when it matches, to avoid duplication).
```

- [x] `test_header_brand_reads_from_layout_logo_text`: The header string must be the layout's logo_text, not a Python literal.
- [x] `test_supplier_name_rendered_when_it_differs_from_bank`: SUPPLIER_NAME must be drawn (and geometry-recorded) when it is neither the legal bank name nor the drawn logo_text letterhead.
- [x] `test_supplier_name_suppressed_only_when_it_equals_drawn_logo_text`: Suppress the supplier line only when the supplier is already drawn verbatim as the letterhead (logo_text); never merely because it equals the legal bank name, which is a different string from what the header renders.
- [x] `test_supplier_name_drawn_when_it_equals_legal_bank_but_not_logo_text`: Benchmark guarantee: a supplier equal to the legal bank name (but not the short drawn logo_text) must still be rendered; the legal name is not on the page otherwise, so a VLM scored against SUPPLIER_NAME could never extract it.
- [x] `test_every_corpus_supplier_name_is_extractable_on_the_page`: The whole corpus: every non-empty SUPPLIER_NAME must appear on the rendered page (recorded as its own box, or already present verbatim as the logo_text letterhead). If it is not on the page, a VLM cannot extract it and the benchmark is unscoreable.
- [x] `test_supplier_name_is_consistent_with_the_layout_bank_across_corpus`: Ground-truth invariant: a CBA-layout statement must carry a CBA supplier, never ANZ. SUPPLIER_NAME must be the canonical bank name implied by the layout (its prefix maps to a banks-pool code).
- [x] `test_every_bank_layout_has_a_supplier_name_budget`: Fit-safety: SUPPLIER_NAME is a fitted field, so every layout needs its budget.

## `tests/test_blocklist_reachability.py`

```text
No real business name may be reachable by the fictional-name grammar.

`fictional_business_name` composes `{surname | suburb_prefix} {category_noun}` and
rejects blocklisted candidates at runtime. That guard is the last line of defence;
`reachable_blocked_names` is the first, and `pipeline validate` calls it so a pool
edit that makes a real company emittable fails when the pool is edited rather than
when a corpus is seeded.

Today nothing on the blocklist is reachable; the nouns are trade phrases and real
brands are not shaped like that; so these tests pin a property that currently holds
with room to spare, and would break loudly the moment it stopped.
```

- [x] `test_no_blocklisted_name_is_reachable_from_the_real_pools`
- [x] `test_a_colliding_pool_edit_is_detected`: Adding 'Origin' + 'Energy' must surface Origin Energy as reachable.
- [x] `test_matching_is_case_insensitive`: A lowercase pool entry must still be caught; the runtime guard lowercases too.
- [x] `test_a_near_miss_is_not_flagged`: Only exact `{prefix} {noun}` matches count; resemblance is not reachability.

## `tests/test_collage_export.py` (OBSOLETE: generators/degradation/collage.py no longer exists)

```text
The collage half of the eval-set export: config, labels, rotation.

The layout functions are covered in tests/degradation/test_collage.py. These
cover what sits between them and the corpus, the config the operator writes,
the label the screen is scored against, and the one derived value that a plate
gets wrong if nobody thinks about it.

No rendering here. Everything below is a second, so it runs on every change
rather than only when someone rebuilds a corpus.
```

- [ ] `TestShippedConfig`: TestShippedConfig
- [ ] `TestShippedConfig.test_the_committed_config_validates`
- [ ] `TestShippedConfig.test_the_seed_is_declared_in_yaml_not_defaulted_in_python`: A Python-side default would let the config look complete while the seed came from somewhere else, and a corpus nobody can regenerate is a corpus whose labels cannot be disputed.
- [ ] `TestConfigRejections`: TestConfigRejections
- [ ] `TestConfigRejections.test_a_missing_block_is_refused`
- [ ] `TestConfigRejections.test_every_key_is_required`: Required even when disabled, so turning collages back on is a one-word change rather than archaeology.
- [ ] `TestConfigRejections.test_an_unknown_arrangement_is_refused`
- [ ] `TestConfigRejections.test_a_single_document_plate_is_refused`: documents_per_image starting at 1 would label a one-receipt plate MULTIPLE, teaching the screen the opposite of the truth.
- [ ] `TestConfigRejections.test_an_empty_range_is_refused`
- [ ] `TestConfigRejections.test_an_overlap_that_buries_the_totals_is_refused`: A taxpayer photographing receipts is SUBSTANTIATING an expense, they lay them out so the business name, date and total can be read. Deep overlap models someone working against their own interest. Checked in config because the failure is invisible downstream: an obscured plate still labels correctly as MULTIPLE, still crosses the severity ladder, and still scores. Only looking at it shows the total is gone. The first corpus built had 54 of 90 plates like that.
- [ ] `TestConfigRejections.test_the_shipped_overlap_is_within_the_ceiling`
- [ ] `TestConfigRejections.test_dropping_the_folded_hard_negative_is_refused`: Without it every collage is an obvious MULTIPLE, the screen scores near 1.0, and the measurement means nothing. Worth failing the build over, because the resulting number LOOKS like a good result.
- [ ] `TestCompositionLabel`: TestCompositionLabel
- [ ] `TestCompositionLabel.test_several_documents_are_multiple`
- [ ] `TestCompositionLabel.test_one_document_is_single`
- [ ] `TestCompositionLabel.test_an_ordinary_image_is_labelled_single_explicitly`: By VALUE, never by omission. Absent has to keep meaning 'this corpus predates the label', which is what makes the screen report NOT SCORED rather than a number, so a corpus that HAS been labelled must say SINGLE out loud.
- [ ] `TestCompositionLabel.test_it_is_not_a_defect`: Composition answers a different question with a different remedy: split the image, not re-photograph it. Folding it into the defect rules is the conflation the screen's prompt already refuses.
- [ ] `TestEffectiveRotation`: TestEffectiveRotation
- [ ] `TestEffectiveRotation.test_a_single_document_reports_its_own_rotation`
- [ ] `TestEffectiveRotation.test_a_plate_reports_its_most_turned_receipt`: The tilt label is `rotation_deg_abs at_least 5.0`. A plate sitting square with its receipts at 15 degrees would otherwise record ~0 and be labelled tilt-free while every receipt in it is visibly crooked.
- [ ] `TestEffectiveRotation.test_it_keeps_the_sign_of_the_largest_turn`
- [ ] `TestEffectiveRotation.test_plate_rotation_compounds_with_the_receipt_s_own`: Both turns are real and they add: a receipt at 10 degrees on a plate photographed at 10 degrees is 20 degrees off square in the image.
- [ ] `TestEffectiveRotation.test_no_placements_falls_back_rather_than_failing`

## `tests/test_content_engine.py`

```text
Unit tests for generators/content_engine.py.
```

- [x] `test_load_pools_succeeds_with_all_required_keys`
- [x] `test_load_pools_fails_fast_on_missing_top_level_key`
- [x] `test_load_pools_fails_fast_on_missing_nested_key`
- [x] `test_load_pools_fails_fast_on_missing_file`
- [x] `test_load_pools_fails_fast_on_empty_file`
- [x] `test_load_pools_fails_fast_on_non_mapping_nested_parent`
- [x] `test_sample_is_deterministic_for_same_seed`
- [x] `test_sample_empty_pool_fails_fast`
- [x] `test_non_repeating_sampler_visits_every_item_before_repeating`
- [x] `test_non_repeating_sampler_reshuffles_on_exhaustion`
- [x] `test_non_repeating_sampler_is_deterministic_for_same_seed`
- [x] `test_non_repeating_sampler_distribution_is_not_lockstep_across_seeds`
- [x] `test_non_repeating_sampler_empty_pool_fails_fast`
- [x] `test_person_returns_en_au_name_shape`
- [x] `test_person_is_deterministic_for_same_seed`
- [x] `test_location_draws_from_the_locations_pool`
- [x] `test_address_matches_expected_format`
- [x] `test_address_is_deterministic_for_same_seed`
- [x] `test_fictional_business_never_emits_a_blocklisted_name`
- [x] `test_fictional_business_returns_a_valid_abn`
- [x] `test_fictional_business_unknown_category_fails_fast`
- [x] `test_fictional_business_exhausted_retries_fails_fast`
- [x] `test_fictional_business_name_is_name_only_and_screened`
- [x] `test_fictional_business_name_unknown_category_fails_fast`
- [x] `test_fictional_business_unchanged_by_name_only_refactor`

## `tests/test_cord_corpus.py`

```text
Every CORD-exported document must score 1.0 against itself; and the gate
that says so must be able to fail.

Uses the vendored, apted-backed evaluator at generators/exporters/cord_eval.py
(cal_acc/cal_f1) rather than the unavailable ``donut.util.JSONParseEvaluator``
- see generators/exporters/cord_eval.py's module docstring for provenance.

The self-score below (``test_whole_corpus_self_scores_perfectly``) proves the
evaluator ACCEPTS our schema: it normalises, flattens and tree-edits our trees
without choking. It cannot, however, prove the evaluator would NOTICE a wrong
field mapping; scoring any non-degenerate corpus against itself is
mathematically guaranteed to return 1.0. A gate that cannot fail manufactures
false confidence.

The mutation tests that follow supply the missing half: they take the real
derived corpus, perturb copies of it, and assert the score genuinely degrades
- for a single wrong value, for a dropped menu row, for a dropped top-level
key, monotonically with the amount of wrongness, and (most importantly) for a
unitprice/price field swap, the exact class of mapping bug the self-score is
blind to.

Every mutation operates on a ``copy.deepcopy`` of the pristine trees. If a
mutation leaked into the baseline, these tests would compare wrong against
wrong and pass spuriously, so ``test_mutation_helpers_never_touch_the_pristine_corpus``
asserts the isolation directly.
```

- [x] `test_corpus_is_populated_and_non_trivial`: Guard for the self-score test below: a perfect score over an empty or trivial corpus (zero records, or records with no menu/total/sub_total) would be meaningless. This must pass before the self-score assertion is trusted.
- [x] `test_whole_corpus_self_scores_perfectly`
- [x] `test_whole_corpus_self_scores_perfectly_via_score_cord`: The production path: score through score_cord with the REAL export config, not cal_f1 called directly. config/export_config.yml's cord_extension_scoring is 'excluded_scored_separately', so this must report a perfect headline_f1 (extension excluded) AND a perfect extension_f1 (scored separately); proving cord_extension_scoring is actually consumed by production code, not merely validated.
- [x] `test_every_record_has_a_populated_tree`
- [x] `test_mutation_helpers_never_touch_the_pristine_corpus`: Every mutation below must operate on a deepcopy. If a mutation leaked into the baseline list, the mutated corpus would be compared against an equally-mutated reference and every assertion below would pass for the wrong reason. This asserts the isolation explicitly rather than trusting it.
- [x] `test_single_value_mutation_degrades_the_score`: Mutation 1: corrupting ONE leaf value in ONE document must score strictly below a perfect 1.0, and; because 109 of 110 documents and all but one field of the 110th are still correct; well above 0.
- [x] `test_dropping_one_menu_item_degrades_the_score`: Mutation 2: deleting one menu row from one document (structural, not just a wrong value) must score strictly below 1.0.
- [x] `test_dropping_a_top_level_key_degrades_the_score`: Mutation 3: deleting an entire top-level subtree ("total") from one document must score strictly below 1.0.
- [x] `test_score_degrades_monotonically_with_the_number_of_wrong_documents`: Mutation 4: the gate must be sensitive to HOW MUCH is wrong, not merely that something is. Corrupting the same field in ten documents must score strictly lower than corrupting it in one. A metric that merely flagged "some mismatch" would return the same number for both.
- [x] `test_swapping_unitprice_and_price_degrades_the_score`: Mutation 5: a unitprice/price field swap; the exact mapping bug the self-score cannot catch; must score strictly below 1.0. Only rows where the two values genuinely differ (quantity > 1) are swapped: with quantity 1 the unit price equals the line total, so the swap is a no-op that no value-based metric could see. This test asserts such rows exist in the corpus AND that swapping them is detected. NOTE ON WHAT THIS DOES AND DOES NOT PROVE. This proves the METRIC is sensitive to a unitprice/price swap. It does not, and cannot, detect a real swap inside generators/exporters/cord.py, because both sides of a self-score come from that same code path; a swapped to_cord would emit a consistently swapped corpus that still self-scores 1.0, and this test would still pass. Catching a real swap needs an INDEPENDENT oracle that pins the emitted menu row against the source LINE_ITEM_PRICES / LINE_ITEM_TOTAL_PRICES columns using values that actually differ; that oracle is test_derive_cord_maps_unit_price_and_total_price_distinctly in tests/test_derive_cord.py. The two tests are complements, not substitutes.
- [x] `test_extension_only_corruption_leaves_headline_perfect_but_drops_extension_score`: This is the whole point of cord_extension_scoring=excluded_scored_separately: corrupting ONLY the extension subtree (supplier/ABN/address/date/payer) across the whole corpus must leave headline_f1 at a perfect 1.0; because the headline is computed over the tree with 'extension' removed; while extension_f1 drops strictly below 1.0, proving the corruption was not simply invisible to the evaluator. Without this test, cord_extension_scoring could be silently ignored (the whole tree scored every time) and every other test in this file would still pass.
- [x] `test_non_extension_corruption_still_drops_the_headline_score`: The inverse of the test above: corrupting a NON-extension value (a total_price) must still drag headline_f1 down. Exclusion applies only to 'extension', not to sensitivity in general.

## `tests/test_data_pools_core.py`

```text
Loader-coverage test: the real config/data_pools.yml satisfies content_engine's
required core keys and has the expected shape (local-only; tests/ is gitignored).
```

- [x] `test_real_data_pools_loads_without_error`
- [x] `test_business_name_parts_covers_every_retailer_and_service_category`
- [x] `test_product_and_service_catalogs_have_positive_price_ranges`
- [x] `test_bank_descriptions_grammar_never_embeds_the_forbidden_acronym`
- [x] `test_retailers_and_professional_services_are_still_the_real_pools`
- [x] `test_deleted_pools_are_gone`
- [x] `test_receipt_and_service_categories_present_and_cover_category_nouns`
- [x] `test_loads_real_pos_pools_file`
- [x] `test_pos_staff_names_are_byte_identical_to_the_legacy_pool_in_order`: The digest indexes into this list by position (`staff_idx = hash % len(staff_names)`), so a reordering, not just a membership change, would silently change every derived staff name.
- [x] `test_pos_windows_match_the_legacy_hardcoded_ranges`: 08:00-19:59 (receipt.py:104), register 01-08 (receipt.py:109), 'R-' plus 6 hex chars (receipt.py:81-82).
- [x] `test_pos_missing_required_key_is_four_element_diagnostic`
- [x] `test_pos_missing_pos_terminal_block_is_diagnostic`
- [x] `test_pos_staff_names_empty_list_is_diagnostic`
- [x] `test_pos_staff_names_non_string_entry_is_diagnostic`
- [x] `test_pos_non_negative_int_key_rejects_a_negative_value`
- [x] `test_pos_positive_int_key_rejects_zero`
- [x] `test_pos_int_key_rejects_a_string_value`
- [x] `test_pos_receipt_number_prefix_empty_string_is_diagnostic`
- [x] `test_pos_missing_file_raises_file_not_found`

## `tests/test_debit_only_projection.py`

```text
The extraction projection must emit debit-only bank transaction rows.

tests/ is gitignored; local-only.

The canonical register in ground_truth/bank_statements.yml is the FULL table:
index-aligned TRANSACTION_* columns where a credit row carries NOT_FOUND in the
paid column and a debit row carries NOT_FOUND in the received column. That is
correct for the register and for transaction linking, and is not changed here.

But the extraction contract (config/extraction_schema.yml, bank_statement) asks
for only five fields and has no home for a credit amount; there is no
TRANSACTION_AMOUNTS_RECEIVED in it. Projecting the full register into that
5-field answer key leaves placeholder rows a model is never asked to produce.
LMM_POC's order-aware scorer then compares position-by-position, the two lists
sit offset by the credit count, and a correct extraction scores 0.000.

Older ground truths (bank/, synthetic_original, synthetic_21, synthetic_20260622)
were debit-only; every 55-case corpus since has carried placeholders. This
restores the earlier contract for the extraction projection only.
```

- [x] `TestDropsCreditRows`: TestDropsCreditRows
- [x] `TestDropsCreditRows.test_every_register_column_is_filtered_together`
- [x] `TestDropsCreditRows.test_no_placeholder_survives_the_paid_column`
- [x] `TestDropsCreditRows.test_scalars_untouched`
- [x] `TestDropsCreditRows.test_key_order_is_preserved`
- [x] `TestDropsCreditRows.test_input_not_mutated`
- [x] `TestDropsCreditRows.test_separator_style_is_preserved`
- [x] `TestLeavesEverythingElseAlone`: TestLeavesEverythingElseAlone
- [x] `TestLeavesEverythingElseAlone.test_non_bank_untouched`
- [x] `TestLeavesEverythingElseAlone.test_already_debit_only_unchanged`
- [x] `TestLeavesEverythingElseAlone.test_all_rows_credit_unchanged`
- [x] `TestLeavesEverythingElseAlone.test_absent_paid_column_unchanged`
- [x] `TestLeavesEverythingElseAlone.test_mismatched_column_length_is_left_whole`
- [x] `TestLeavesEverythingElseAlone.test_scalar_paid_value_unchanged`
- [x] `test_projection_applies_the_filter_to_the_real_register (added)`

## `tests/test_degradation_removal.py` (OBSOLETE: guarded a completed removal)

```text
The old corpus-wide degradation path is deleted, not deprecated.

A leftover `degrade_image` would be a second, silently-diverging way to
degrade a document, and its Python-side DEFAULT_DEGRADATION_PARAMS merge is
exactly the "silent fallback" the project's engineering standards forbid.
```

- [ ] `test_degrade_image_is_gone`
- [ ] `test_default_degradation_params_is_gone`
- [ ] `test_generation_config_has_no_degradation_block`
- [ ] `test_no_generate_degraded_flags_remain`
- [ ] `test_root_camera_scan_script_is_gone`

## `tests/test_derive_cord.py`

```text
Tests for the CORD JSONL derivation.
```

- [x] `test_derive_cord_writes_one_line_per_document`
- [x] `test_derive_cord_maps_unit_price_and_total_price_distinctly`: The independent oracle for the unitprice/price mapping. Every other CORD test uses quantity-1 line items, where LINE_ITEM_PRICES equals LINE_ITEM_TOTAL_PRICES and a swap of the two is invisible. The corpus self-score cannot catch such a swap either, since both sides of a self-comparison come from the same to_cord call. This fixture uses quantity 3 so unit price and line total genuinely differ, pinning LINE_ITEM_PRICES -> menu.unitprice and LINE_ITEM_TOTAL_PRICES -> menu.price against literal expected values.
- [x] `test_derive_cord_skips_non_receipt_document_types`: Bank statements have no CORD equivalent (spec section 7).
- [x] `test_derive_cord_mixes_and_filters_multiple_document_types`: A single gt_files list mixing eligible and ineligible types emits only the eligible ones, and multiple gt_files are concatenated in file order.
- [x] `test_derive_cord_creates_parent_directories`: Mirrors derive_csv/derive_jsonl's mkdir(parents=True) behaviour.
- [x] `test_derive_cord_applies_identifier_form_from_export_config`: The export_config's abn_tfn_canonical_form must actually flow through to to_cord's identifier_form argument (not just be accepted and ignored).
- [x] `test_derive_cord_propagates_line_item_mismatch_diagnostic`: A malformed ground-truth entry (mismatched LINE_ITEM_* list lengths) must surface zip_line_items' four-element diagnostic, not a bare traceback or a silently truncated/wrong tree.
- [x] `test_derive_cord_real_corpus_is_receipts_plus_invoices` (added: real ground truth yields one record per receipt and invoice)

## `tests/test_derive_docile.py`

```text
Tests for the DocILE JSONL derivation (invoice-only scope, spec section 8.3).

Mirrors tests/test_derive_cord.py's structure. DocILE is restricted to
invoices: receipts structurally never render unit price or quantity-when-1,
so DOCILE_DOCUMENT_TYPES = frozenset({"INVOICE"}) here, superseding any
receipt+invoice count implied elsewhere, the real corpus emits exactly 55
records (one per invoice), not 110.
```

- [x] `test_derive_docile_writes_one_line_per_invoice`
- [x] `test_derive_docile_skips_non_invoice_document_types`: Receipts have no DocILE equivalent, structurally missing geometry for unit price / quantity-when-1 (spec section 8.3, invoice-only scope).
- [x] `test_derive_docile_mixes_and_filters_multiple_document_types`
- [x] `test_derive_docile_creates_parent_directories`
- [x] `test_derive_docile_raises_when_geometry_file_is_missing`
- [x] `test_derive_docile_raises_when_an_invoice_has_no_geometry_record`: Every invoice has captured geometry in the real corpus; an absent record always signals a real capture bug, never a structural absence.

## `tests/test_derive_native.py`

```text
Tests for the native JSONL derivation (spec section 7).

Mirrors tests/test_derive_cord.py's structure. Native covers the one document
type with no CORD or DocILE equivalent: bank statements.
```

- [x] `test_derive_native_writes_one_line_per_document`
- [x] `test_derive_native_skips_document_types_with_a_cord_equivalent`: Receipts and invoices have a CORD equivalent and must not appear here; the whole corpus partitions into CORD xor native, never both.
- [x] `test_derive_native_mixes_and_filters_multiple_document_types`
- [x] `test_derive_native_creates_parent_directories`
- [x] `test_derive_native_propagates_ragged_transaction_diagnostic`: A malformed ground-truth entry (mismatched TRANSACTION_* list lengths) must surface to_native's four-element diagnostic, not a bare traceback.
- [x] `test_native_document_types_constant`: Guards the module's document-type membership against silent drift; exactly the one type with no CORD/DocILE equivalent (spec section 7).

## `tests/test_derived_baseline.py` (OBSOLETE: superseded by the fresh derived snapshot, tests/test_derived_snapshot.py)

```text
Baseline gate for the surviving corpus's derived outputs (Stage 4 Task 1
of the layout-DSL narrowing plan, docs/layout_dsl_stage4_plan.md).

`tests/test_bank_pixel_snapshot.py`, `test_receipt_pixel_snapshot.py` and
`test_invoice_pixel_snapshot.py` pin what the three surviving document types
(bank statements, receipts, invoices) render as pixels. Nothing pins what
`python -m generators.pipeline derive` produces from the same ground truth -
`ground_truth.csv`, `ground_truth.jsonl`, `cord.jsonl`, `docile.jsonl`,
`native.jsonl`, `doc_refs.jsonl`. The schema trim due later in Stage 4
(`config/field_definitions.yml` losing columns only the doomed types used)
is exactly the kind of change that could silently alter a surviving type's
CSV header or JSONL key set without changing a single pixel; this test
closes that gap.

`tests/regenerate_derived_baseline.py` captures the fixture this compares
against (`tests/fixtures/derived_baseline.json`) by calling the same
`derive_*` functions this test calls, filtering every artefact down to
BANK_STATEMENT / RECEIPT / INVOICE rows (and, for doc_refs.jsonl, the
'receipt_to_bank' link type) and hashing each subset. The doomed types
(cc_statement and the four trust types) are excluded on both sides by
construction, so this test stays green across their deletion in Tasks 2-3 -
it only ever asserts about the three survivors.

Task 4 (the schema trim) then hit the one artefact whose shape is a function
of `all_columns` rather than of each entry's own fields: `ground_truth.csv`,
whose header is `["image_file", *all_columns]` and whose every row carries a
NOT_FOUND cell for each column the entry doesn't use. Trimming 26 doomed
columns necessarily changes that header and those rows for all three
survivors, so "unchanged" was never satisfiable there. The fixture was NOT
re-captured; instead the two CSV assertions became *delta* assertions against
the still-47-column fixture (see DOOMED_COLUMNS below), and a fourth test
pins the CSV's surviving values against `ground_truth.jsonl`, which the trim
left byte-identical and which is still hash-compared to the fixture. The
other five artefacts are untouched by the trim and still assert equality.

To re-bless after an *intended* change to a surviving type's derived output:

    conda run -n synthetic python tests/regenerate_derived_baseline.py --confirm

There is no other way to update this fixture; `--confirm` is required and
defaults to False, so a bare run only prints a dry-run summary.
```

- [ ] `test_baseline_fixture_covers_the_expected_artefacts`: Guards against an artefact silently going uncovered; the fixture must name exactly the six files `pipeline.py derive` writes.
- [ ] `test_no_doomed_column_survives_in_the_header`: None of the columns the schema trim removed may reappear. The fixture is now captured post-trim, so the delta projection these tests used during the trim itself has retired; comparing a post-trim baseline against a post-trim export is plain equality, done by the tests below. What is still worth asserting is the one-way property: a deleted document type's column must never come back, which would mean a column describing a document this corpus no longer contains.
- [ ] `test_ground_truth_csv_header_unchanged`: The CSV header; names and order; matches the baseline exactly.
- [ ] `test_ground_truth_csv_surviving_types_unchanged`: Per-type column set, in order, plus row count.
- [ ] `test_ground_truth_csv_cells_agree_with_the_hash_pinned_jsonl`: Pins the CSV's *values*, which the trim's changed row hash can no longer do on its own. `ground_truth.jsonl` emits only the fields an entry actually carries, so the trim left it byte-identical; `test_ground_truth_jsonl_surviving_types_unchanged` still compares its hash against the un-recaptured fixture. Every surviving CSV cell is the same `str(value)` off the same ground-truth entry, so checking the CSV against the JSONL transitively pins the CSV to the baseline without re-blessing anything.
- [ ] `test_ground_truth_jsonl_surviving_types_unchanged`
- [ ] `test_native_jsonl_bank_statement_unchanged`: Only BANK_STATEMENT is a native document among the three survivors; receipts and invoices route through CORD instead (see generators/derive_outputs.py's NATIVE_DOCUMENT_TYPES / CORD_DOCUMENT_TYPES partition), so their native.jsonl row_count is 0 by design, not a gap.
- [ ] `test_cord_jsonl_unchanged`
- [ ] `test_docile_jsonl_unchanged`
- [ ] `test_doc_refs_receipt_to_bank_unchanged`: Only the receipt<->bank half of doc_refs.jsonl survives Stage 4; the 'trust_distribution_quad' half is entirely doomed and excluded from both sides of this comparison (see module docstring).
- [ ] `test_row_counts_match_the_known_corpus_size`: Sanity-anchors the baseline itself against the documented corpus shape (the project's engineering standards: 55 cases per business type) so a baseline captured against a corrupted tree can't silently pass everything else in this file.

## `tests/test_environment_pins.py`

```text
Ensure Pillow is pinned so FreeType font metrics are reproducible.

fit_text() measurement depends on Pillow's FreeType version; an unpinned Pillow
could shift getbbox() widths and change rendered pixels / fit decisions.
```

- [x] `test_pillow_is_pinned`

## `tests/test_eval_format.py`

```text
Structural contract for evaluation-set export directories.

Pins the *shape* of an exported evaluation-set directory, filename
pattern and count, `ground_truth.csv` header and column order, and
`ground_truth.jsonl` key set and key order per document type, against
`tests/fixtures/eval_format_baseline.json`. That fixture was captured from
the existing hand-verified dataset at
`../evaluation_data/synthetic_20260731`.

Field *values* are deliberately never inspected: the corpus is reseeded
independently of this format, so a value-based comparison would fail for
the wrong reason forever. Shape is the only part of the dataset that is a
contract with external consumers (competing vision-language models scored
against it), and shape is what stays meaningful as a regression signal.

`assert_eval_export_matches_baseline` takes a directory path rather than
being hard-coded to the legacy dataset, because later tasks in the eval-set
export refactor point it at freshly produced export directories to confirm
the in-house replacement reproduces the same contract.
```

- [x] ~~test_legacy_dataset_matches_baseline~~ dropped: read a dataset outside the repository, which these tests must not depend on; replaced by test_fresh_export_matches_baseline and test_fixture_describes_the_extraction_schema
- [x] `test_fixture_describes_the_extraction_schema (added)`
- [x] `test_fresh_export_matches_baseline (added)`
- [x] `test_bank_statements_follow_the_schema (added)`
- [x] `test_header_leads_with_the_first_sorted_record (added)`
- [x] `test_comparison_fails_when_the_shape_drifts (added, 3 parametrised cases)`

## `tests/test_eval_set.py`

```text
Eval-set export: config validation, projection, CSV/JSONL shape.

The full export is exercised once, in a module-scoped fixture, because
rendering the whole corpus twice (clean + degraded) is the expensive part;
every property of the products is then asserted against that one export.

Counts are derived from config/generation_config.yml and from the export
itself, never written as literals. The corpus size is an operator decision,
it has already gone from 165 images across three types to 110 across two,
and a test that hardcodes it fails for expressing a preference rather than a
contract.
```

- [x] `test_loads_real_config`: Assert the config is coherent, not that it names particular types. Which types the corpus carries is an operator decision that has already changed once; pinning the list here made this test fail for expressing a preference rather than a contract. What must hold is that every declared type is renderable and every degraded type is one the export actually produces.
- [x] `test_relabel_keys_are_gone_from_the_config`: The export owns its projection; no path into another repo may survive.
- [x] `test_missing_key_is_four_element_diagnostic`
- [x] `test_missing_eval_set_block_is_diagnostic`
- [x] `test_unknown_document_type_is_diagnostic`
- [x] `test_format_value_applies_monetary_boolean_and_pipe_conventions`
- [x] `test_project_fields_uses_schema_order_and_fills_gaps`
- [x] `test_project_fields_unknown_document_type_is_diagnostic`
- [x] `test_csv_from_jsonl_matches_records`
- [x] `test_write_jsonl_preserves_field_order`
- [x] `test_export_creates_dated_sibling_directories`: One directory per declared prefix, all sharing the same date stamp. Was `..._two_dated_sibling_directories`, pinning the count at two. A third was added, the combined half, for consumers that take a single input directory, so the assertion now derives the expected names from the configured prefixes rather than listing them.
- [x] `test_the_two_directories_share_one_date_stamp` (renamed from `..._three_directories_...`): A mismatched stamp would let a run score new images against an older answer key and report plausible numbers. (adjusted: export writes two directories)
- [x] `test_export_matches_pinned_format`: The pinned format describes the *clean* export: every document type once, under `CASE###_<type>.png`. The degraded export deliberately no longer has that shape, it is receipts only, one image per severity tier, under `CASE###_receipt_v#.png`. Its shape is pinned separately in tests/test_eval_set_variants.py. (adjusted: fixture re-captured from the current three-type exporter)
- [x] `test_the_two_directories_no_longer_mirror_each_other`: Different counts and different contents. The two halves used to happen to share a count; they no longer do, and asserting equality was pinning a coincidence. What is load-bearing is that every degraded name carries a tier suffix declared in the config, so the two halves can be joined by stripping it. (adjusted: with three types clean and three tiers degraded the counts coincide, so only contents and tier suffixes are asserted)
- [x] `test_export_filenames_carry_no_layout_variant`: A leaked layout name would let a model infer the template for free.
- [x] `test_each_directory_carries_its_own_ground_truth`: The degraded ground truth is written, not copied. It describes different rows entirely, receipt variants rather than one document per type, so a copy of the clean file would name images that do not exist in that directory.
- [x] `test_every_csv_image_file_exists_in_its_own_directory`
- [x] `test_jsonl_and_csv_agree_row_for_row`: CSV is a transposition of the JSONL: same order, same values.
- [x] `test_export_refuses_non_empty_dir_without_force`: Either target directory being occupied stops the export, not just the first.
- [x] `test_export_force_overwrites_non_empty_dir`
- [x] `test_missing_config_file_is_diagnostic (added)`
- [x] `test_cli_eval_set_prints_both_lines (added)`
- [x] `test_cli_eval_set_prints_both_lines_bug (added, xfail strict: pipeline.py:366 prints the clean count on the Degraded line)`

## `tests/test_eval_set_variants.py`

```text
The degraded eval set holds the declared types at each declared tier, with
extraction ground truth duplicated per variant and a quality ground truth
describing each image's condition.

Every expectation here is read from config/generation_config.yml rather than
written as a literal. These tests are the executable spec of the degradation
design, and that design is a configuration: when it said "receipts only, three
tiers" the literals agreed with it, and when it changed to "whatever
degrade_types says, two tiers" fifteen tests failed for asserting a decision
rather than a behaviour. Deriving means retuning the ladder in YAML does not
require editing this file, and that a type or tier silently vanishing from
the config still fails, because the counts stop matching the images.
```

- [x] `test_clean_dir_holds_every_declared_type_once_per_case`
- [x] `test_degraded_dir_holds_exactly_the_types_declared_for_degradation`: Which types get degraded is a config decision, so read it from config. Replaces a test that asserted "receipts only", true when written, and a statement about intent rather than about the exporter. (adjusted: no degrade_types config key exists; the degraded type is generators.eval_set._DEGRADED_TYPE (receipt))
- [x] `test_types_not_declared_for_degradation_have_no_variants`: A clean-only type must appear in the clean half and nowhere else.
- [x] `test_every_degraded_document_has_one_image_per_tier`
- [x] `test_degraded_count_is_cases_times_types_times_tiers`
- [x] `test_degraded_ground_truth_has_one_row_per_variant`
- [x] `test_variants_of_a_case_share_identical_field_values`: The value-F1 contract: distortion never changes the answer.
- [x] `test_degraded_jsonl_matches_the_csv`: The JSONL keys the image as `filename`; only the CSV renames it to `image_file` (see csv_from_jsonl).
- [x] `test_variant_values_match_the_clean_source_row`: A variant must carry its *source document's* answers, not merely be self-consistent across tiers.
- [x] `test_degraded_header_carries_no_column_for_an_absent_type`: No column that would be NOT_FOUND in every row. Was `test_degraded_header_is_receipt_shaped`, which asserted the degraded half held receipts only. That premise is gone, the half now spans every degraded type, but the guarantee underneath it survives: a type excluded from the corpus must not leave its columns behind. Bank statements are the excluded type, so their columns are the ones to check for.
- [x] `test_case_ids_still_resolve_for_linking`: Case ids are unsuffixed, so transaction_links.yml keeps resolving.
- [x] `test_summary_reports_both_counts`
- [x] ~~test_quality_ground_truth_covers_every_image~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)
- [x] ~~test_defect_vocabulary_matches_the_declared_rules~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)
- [x] ~~test_clean_images_carry_no_defects~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)
- [x] `test_every_degraded_row_names_a_declared_tier` (adjusted: checked on the extraction ground truth filenames, since the quality ground truth is gone)
- [x] ~~test_categorical_defects_follow_their_tier_declaration~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)
- [x] ~~test_combined_dir_holds_both_halves_plus_the_collages~~ dropped: the exporter no longer writes a combined directory or collages (two halves only)
- [x] ~~test_combined_images_are_real_copies_not_links~~ dropped: the exporter no longer writes a combined directory or collages (two halves only)
- [x] ~~test_the_quality_ground_truth_covers_every_image~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)
- [x] ~~test_the_extraction_ground_truth_covers_exactly_the_extractable_images~~ dropped: the exporter no longer writes a combined directory or collages (two halves only)
- [x] ~~test_combined_set_holds_every_condition~~ dropped: the exporter no longer writes a combined directory or collages (two halves only)
- [x] `test_a_case_carries_identical_extraction_values_across_conditions`: Distortion never changes the answer, now checked across the clean row and its variants together, which only the combined set makes possible. (adjusted: checked across the clean and degraded halves, since there is no combined directory)
- [x] ~~test_drawn_values_are_recorded_for_every_threshold_rule~~ dropped: the exporter no longer writes a quality ground truth (no defect labels exist in generators/eval_set.py)

## `tests/test_fit_text.py`

```text
Tests for fit_text lossless fitting in generators/common.py.
```

- [x] `test_fits_as_is_returns_nominal_single_line`
- [x] `test_shrink_reduces_size_until_it_fits`
- [x] `test_shrink_raises_fiterror_below_floor`
- [x] `test_wrap_splits_into_allowed_lines`
- [x] `test_wrap_raises_when_exceeds_max_lines`
- [x] `test_shrink_then_wrap_prefers_shrink_then_wraps` (adjusted: wrap-first is intended per Tod; design doc corrected)
- [x] `test_impossible_fit_raises_four_element_diagnostic`

## `tests/test_fitted_helpers.py`

```text
Tests for the fit-aware draw helpers in generators/common.py.
```

- [x] `test_fitted_left_returns_advanced_y`
- [x] `test_fitted_center_wraps_and_advances_more_for_two_lines`
- [x] `test_fitted_right_draws_within_canvas`

## `tests/test_font_guard.py`

```text
Guard: fit measurement must use a vendored face, never a system font.

`load_font` no longer has a system fallback at all, so the family registry is
the first line of defence and these tests cover the second: a caller who builds
an `ImageFont` itself and hands it to measurement.
```

- [x] `test_every_vendored_family_loads_and_passes_guard`
- [x] `test_system_font_fails_guard`
- [x] `test_unknown_family_fails_fast_with_a_diagnostic`: The fallback is gone, so a typo'd family must fail rather than degrade.

## `tests/test_generate_extraction_gt.py`

```text
Tests for scripts/generate_extraction_gt.py after its move from LMM_POC.

The script has no package, so it is loaded by path. Its `--schema` argument must be
LMM_POC's extraction contract; this repo's own config/field_definitions.yml is the
generation contract and would silently produce a wrong-shaped CSV, so the loader
rejects it.
```

- [x] `test_script_lives_in_this_repo`
- [x] `test_this_repos_generation_schema_is_rejected`: The generation contract must not be accepted as the extraction contract.
- [x] `test_this_repos_generation_schema_is_rejected_bug (added, xfail strict: script labels the fourth element How to fix, not Recover)`
- [x] `test_missing_schema_file_is_rejected`
- [x] `test_missing_schema_file_is_rejected_bug (added, xfail strict: same label bug)`
- [x] `test_schema_without_evaluation_key_is_rejected`: A YAML with document_fields but no evaluation block must not slip through.
- [x] `test_schema_without_evaluation_key_is_rejected_bug (added, xfail strict: same label bug)`
- [x] `test_valid_schema_yields_field_lists_and_type_sets`
- [x] `test_columns_put_image_file_first_and_append_bank_only_fields`
- [x] `test_yaml_dir_defaults_to_this_repos_ground_truth`: The default must be repo-relative, not a path on the author's machine.
- [x] `test_schema_defaults_to_the_repo_local_extraction_contract`: The generator must be self-contained; no path into another checkout.
- [x] `test_shipped_extraction_contract_loads_and_matches_the_prompts`: The vendored contract must be the extraction field set, not the generation one.
- [x] `test_validation_only_fields_are_not_csv_columns`: ACCOUNT_BALANCE etc. have formatting declared but must add no columns.

## `tests/test_generate_validates_layouts.py`

```text
`generate` validates every DSL layout before it renders anything.

Before the layout DSL, an incomplete layout degraded gracefully: a missing key
took a Python default and the run finished. It does not any more; every
primitive parameter resolves through `resolve_param`, so an omission is a
render-time exception. `validate` catching that is no use to an operator who
ran `generate` directly, which is why `generate` now runs the same check
itself, before the first image is written.
```

- [x] `test_generate_rejects_an_incomplete_layout_before_rendering`
- [x] `test_generate_still_produces_documents_when_layouts_are_valid`: The guard must not cost a valid run anything. One document type, so the test stays fast; the eight-type run is covered by the CLI itself.

## `tests/test_geometry_capture.py`

```text
Draw-time geometry capture across all three renderers.

Verifies the opt-in contract end to end: passing `geometry_out=None` (the
default) must render pixel-identical to no signature change at all, and
passing `geometry_out={}` must never crash, including the one document
type (tax_invoice_mixed) that draws the same LINE_ITEM_* list into two
separate table sections.
```

- [x] `test_default_call_has_no_geometry_out_and_returns_an_image`: The plain two-arg call (no geometry_out at all) must still work.
- [x] `test_geometry_out_none_is_identical_to_omitting_it`: Explicit geometry_out=None must render exactly like the bare call.
- [x] `test_every_layout_across_every_renderer_captures_geometry_without_crashing`: geometry_out={} must populate width/height/boxes for every layout id. This is Step 9's central check: it would have caught the tax_invoice_mixed duplicate-table crash (two "table" sections drawing the same LINE_ITEM_* list) before it ever reached `pipeline generate`.
- [x] `test_invoice_mixed_layout_only_captures_the_first_table`: tax_invoice_mixed draws LINE_ITEM_DESCRIPTIONS twice (taxable + tax-free tables), only the first table's boxes may be recorded, or the second draw would collide with the first in the recorder. (adjusted: tax_invoice_mixed now draws a single table; asserts one table and one box per item)

## `tests/test_ground_truth_schema.py`

```text
Tests for generators/schema.py's ground-truth fail-fast diagnostics (local-only; tests/ is gitignored).

Named test_ground_truth_schema.py, not test_schema.py, to avoid a pytest
basename collision with tests/layout_dsl/test_schema.py (this test tree has
no __init__.py files, so module basenames must be unique across it).

Covers the two config-driven lookups that read config/field_definitions.yml
with bare dict subscripts: _valid_doc_types() (the 'document_type_values' key)
and _field_type_group() (the 'field_types' key). Both must raise SchemaError
with a four-element diagnostic on a missing key rather than an unhandled
KeyError, since generators/pipeline.py's `validate` command does not catch
KeyError around generators.schema.validate_entry.
```

- [x] `test_valid_doc_types_raises_schema_error_when_document_type_values_missing`
- [x] `test_valid_doc_types_returns_correct_values_against_real_config`
- [x] `test_field_type_group_raises_schema_error_when_field_types_key_missing`
- [x] `test_field_type_group_returns_empty_set_when_requested_group_absent`: field_types present but with no 'abn' key is not a schema error; this is deliberate existing behaviour (not every field type applies to every document type) and must not change.
- [x] `test_field_type_group_returns_correct_values_against_real_config`
- [x] `test_validate_entry_surfaces_missing_document_type_values_as_error_string`: pipeline.py:76 calls validate_entry() without a try/except around it, so a SchemaError raised deep inside must be caught and turned into an error string here rather than propagating as a raw exception.
- [x] `test_validate_entry_surfaces_missing_field_types_as_error_string`

## `tests/test_invoice_fit.py`

```text
Invoice fit-safety: budgets present, no field clips, unchanged docs byte-identical.
```

- [x] `test_every_invoice_layout_has_budgets`
- [x] `test_no_invoice_field_overflows_after_fitting`
- [x] `test_unchanged_invoices_byte_identical` (adjusted: pre-fit renderer is gone; compares with the pinned pixel snapshot)

## `tests/test_invoice_pixel_snapshot.py` (OBSOLETE: replaced by the fresh three-type pixel snapshot)

```text
Phase A parity gate. Renders every invoice entry and asserts the page hash
and every recorded box match the baseline captured from the legacy renderer.

This does not retire with the legacy path the way the bank equivalence harness
did. After Task 15 it becomes the permanent regression guard for invoices, and
it is re-blessed only when Phase B intentionally changes the rendering.

To re-bless after an *intended* rendering change:

    conda run -n synthetic python tests/regenerate_doc_pixel_snapshot.py --confirm

There is no other way to update this fixture; `--confirm` is required and
defaults to False, so a bare run only prints a dry-run summary.
```

- [ ] `test_invoice_render_matches_baseline`

## `tests/test_labels.py` (OBSOLETE: generators/degradation/labels.py no longer exists)

```text
Unit tests for generators/degradation/labels.py.

No rendering happens here. Every case builds a provenance dict by hand, so the
whole file runs in milliseconds, which matters, because these are the paths
where a wrong answer is silent: a mislabelled image does not crash, it just
scores a correct model answer as wrong.

The boundary cases are the point. A threshold rule is the only thing standing
between "the tier could have tilted this image" and "this image is tilted", so
the value exactly on the threshold, just under it, and just over it all get an
explicit test.
```

- [ ] `test_at_least_is_inclusive_at_the_threshold`
- [ ] `test_at_most_is_inclusive_at_the_threshold`: jpeg_quality runs the other way: lower quality means more blocking.
- [ ] `test_rotation_is_compared_on_magnitude_not_sign`: Which way the page leans is not something any question asks about.
- [ ] `test_a_tier_that_allows_a_defect_does_not_by_itself_label_one`: The whole reason provenance is recorded. A heavy-tier image drawing 3.9 degrees from a [-14, 14] range is not a tilted document, and labelling it one would mark a correct model answer wrong.
- [ ] `test_augmentation_rule_reads_the_tiers_phase_list`
- [ ] `test_any_of_is_true_when_a_single_clause_is`
- [ ] `test_labels_keep_declaration_order`: The order the config declares is the order the record carries, so a diff of two ground-truth files stays readable.
- [ ] `test_missing_block_is_diagnosed`
- [ ] `test_empty_rules_are_diagnosed`
- [ ] `test_missing_filename_is_diagnosed`
- [ ] `test_a_threshold_rule_without_a_threshold_is_diagnosed`: `drawn:` with nothing to compare against would quietly never fire.
- [ ] `test_a_rule_naming_no_known_form_is_diagnosed`
- [ ] `test_an_empty_any_of_is_diagnosed`
- [ ] `test_a_rule_reading_an_unrecorded_value_is_diagnosed`: A config/generator mismatch must name the gap, not label everything false.
- [ ] `test_the_shipped_config_loads_and_labels_a_clean_provenance_false`
- [ ] `test_every_shipped_rule_is_decidable_from_recorded_provenance`: Guards the seam between camera.py's provenance and the config's rules: add a rule reading a value nobody records and this fails immediately.

## `tests/test_layout_budgets.py`

```text
Tests for the fail-fast field_budgets loader.
```

- [x] `test_returns_valid_budget`
- [x] `test_missing_field_budgets_block_is_diagnostic`
- [x] `test_missing_field_entry_is_diagnostic`
- [x] `test_missing_required_key_is_diagnostic`
- [x] `test_invalid_fit_enum_is_diagnostic`
- [x] `test_invalid_numeric_value_is_diagnostic`

## `tests/test_loader_anchors.py`

```text
Tests for load_layout_registry's handling of top-level anchor siblings.

The de-duplication scheme puts anchor definitions (`_bank_base`, `_cba`, …) at
top level as siblings of `layouts:`, so the loader must unwrap `layouts:`
even when it is not the sole top-level key; as long as every other key is
underscore-prefixed. A non-underscore sibling is a genuine authoring error
(a mis-indented layout) and must fail fast.
```

- [x] `test_anchor_siblings_are_not_mistaken_for_layouts`
- [x] `test_a_non_anchor_top_level_key_fails_fast`
- [x] `test_a_file_with_only_layouts_still_unwraps`

## `tests/test_overflow_backstop.py`

```text
Overflow backstop: render each entry and collect FitError violations.
```

- [x] `test_collects_all_violations_without_raising`
- [x] `test_no_violations_when_render_ok`
- [x] `test_skips_entries_with_unknown_layout`
- [x] `test_build_overflow_error_is_four_element_diagnostic`
- [x] `test_validate_passes_on_current_ground_truth`: The shipped documents all fit losslessly; validate must exit 0.

## `tests/test_payment_block.py`

```text
Terminal-block config loading, derivation, and rendering (local-only).

The slip itself is no longer drawn by Python: `render_payment_block` was
deleted in the layout-DSL migration and the three variants (card, wallet,
cash) are now `when:`-guarded blocks in config/layouts/receipts.yml. The
rendering tests below therefore drive the DSL body directly, through
`generators.receipt.render_receipt` for the corpus cases and through a
provider-free layout with injected PAYMENT_* fields for the cash variant,
which the corpus itself can never reach (Cash carries weight 0).
```

- [x] `test_loads_real_pools_file`
- [x] `test_every_scheme_has_required_subkeys`
- [x] `test_every_weighted_method_resolves`
- [x] `test_missing_required_key_is_four_element_diagnostic`
- [x] `test_missing_payment_terminal_block_is_diagnostic`
- [x] `test_scheme_missing_subkey_is_diagnostic`
- [x] `test_unknown_weighted_method_is_diagnostic`
- [x] `test_missing_file_raises_file_not_found`
- [x] `test_the_receipt_body_prints_the_expected_slip_wording`
- [x] `test_payment_terminal_declares_no_printed_slip_wording`: `payment_terminal` holds sampled values only; fixed wording is chrome. Re-adding a `customer_copy_text`/`slip_labels`-style key here would put a second copy of a printed string in a second file, which is what this sweep removed.
- [x] `test_cash_step_and_extra_notes_reject_non_positive_ints`
- [x] `test_cash_tender_step_drives_the_note_denomination`: A pools override with a larger step must change the derived tender.
- [x] `test_derivation_is_deterministic`
- [x] `test_different_cases_differ`
- [x] `test_kind_matches_method`
- [x] `test_all_methods_appear_across_the_corpus`: The weighted pool must actually produce every configured method.
- [x] `test_masked_pan_width_matches_scheme_pan_digits`
- [x] `test_account_type_comes_from_its_own_scheme`
- [x] `test_timestamp_is_twelve_hour_rendering_of_receipt_meta_time`
- [x] `test_cash_change_is_tendered_minus_total_and_tender_exceeds_total`
- [x] `test_exact_multiple_of_five_still_gives_change`: A total that is already a round note must never render CHANGE 0.00.
- [x] `test_card_details_have_no_cash_values`
- [x] `test_derive_payment_carries_no_purchase_total_of_its_own`: The printed 'Purchase AUD' amount must never diverge from TOTAL_AMOUNT. The body binds {TOTAL_AMOUNT} directly, so PaymentDetails deliberately holds no derived copy for it to drift from; the structural version of the invariant `test_purchase_total_is_the_scored_total_verbatim` used to assert about a field nothing read.
- [x] `test_card_block_is_taller_than_cash_block`
- [x] `test_cash_block_draws_only_two_lines`: Cash advances exactly two line heights (tendered + change), nothing more.
- [x] `test_a_whole_cash_receipt_page_renders_on_every_layout`: The cash slip's only whole-page oracle. `_render_lines` above draws the two cash blocks onto a scratch canvas with the provider chain switched off. What it cannot see is the page: a cash payment suppresses every `when: PAYMENT_AID` block, collapsing the slip from roughly twenty lines to two, and that collapse meets the surrounding rules and spacers and then `render_receipt`'s crop-to-content height. No corpus receipt reaches this and the legacy renderer that could have been an oracle is deleted, so this drives the real body through the real renderer with the pool patched, and asserts the two labels actually land on the cropped page rather than below its bottom edge.
- [x] `test_a_cash_receipt_page_is_shorter_than_the_same_receipt_paid_by_card`: The collapse is real, and it is the crop that shows it.
- [x] `test_block_renders_for_every_layout_and_method`: No layout/method combination may raise (FitError included).
- [x] `test_every_corpus_receipt_renders_under_every_layout`: Broader fit guard than the per-block one above: whole pages, real data. The corpus assigns one layout per receipt, so a fit failure that only appears when a long supplier name lands on the narrowest layout would otherwise go unseen until a reseed happened to pair them.
- [x] `test_every_layout_has_payment_budgets`
- [x] `test_receipt_render_grows_by_the_block`: Rendering with the payment section must add the block's lines. Compares against the same entry rendered with the slip blocks stripped from its layout body, so the assertion measures the block itself rather than a fixed pixel threshold (receipt heights vary with line-item count). Only the card path is exercised through the corpus: every receipt is linked to a bank transaction, so no ground-truth receipt is cash. The cash block's height is covered directly by test_cash_block_draws_only_two_lines.
- [x] `test_receipt_module_has_no_hardcoded_methods`
- [x] `test_bank_description_maps_to_scheme`
- [x] `test_longest_prefix_wins`: A longer, more specific prefix must beat a shorter one.
- [x] `test_unmapped_description_is_four_element_diagnostic`
- [x] `test_mapped_schemes_exist`
- [x] `test_debit_schemes_are_configured`
- [x] `test_zero_weight_is_allowed_but_all_zero_is_not`: Cash: 0 must be accepted; a pool with no positive weight must not.
- [x] `test_negative_weight_is_rejected`
- [x] `test_link_index_covers_every_receipt_stem`
- [x] `test_link_index_excludes_invoices`
- [x] `test_link_index_missing_file_raises`
- [x] `test_bank_description_forces_the_scheme`
- [x] `test_forced_scheme_keeps_hash_derived_values`: Only the scheme changes; the rest of the slip stays hash-derived.
- [x] `test_account_type_belongs_to_the_forced_scheme`
- [x] `test_wallet_presentation_keeps_the_bank_scheme`: A wallet presentation must not change the scheme the bank row names.
- [x] `test_unlinked_receipt_falls_back_to_the_weighted_pick`: Without a description the derivation is byte-identical to before.
- [x] `test_cash_never_appears_now_that_its_weight_is_zero`
- [x] `test_every_linked_receipt_matches_its_bank_row`: The invariant this whole change exists to establish.
- [x] `test_no_linked_receipt_is_cash`
- [x] `test_renderer_actually_consults_the_link_index`: End-to-end: the bank scheme reaches the page, not just the dataclass. Renders a case whose linked scheme differs from the scheme it would pick unlinked, with the link index live and then stubbed empty. If the renderer ignored the links, both renders would be byte-identical.
- [x] `test_validate_passes_on_the_shipped_corpus`
- [x] `test_validate_reports_unmapped_bank_descriptions`: A link the mapping cannot resolve must fail validation, not render wrongly.

## `tests/test_printed_unit_price_projection.py`

```text
The extraction projection must not emit a price the receipt never printed.

tests/ is gitignored; local-only.

A receipt line carries ONE amount, and that amount is the line total:

    3x <item name>                          9.72

The per-unit price (3.24) is nowhere on the page. The generator knows it because
it composed the line, so the projection was writing a DERIVED value into the
answer key. That makes LINE_ITEM_PRICES unscoreable in the honest sense: a model
reporting exactly what the receipt prints is marked wrong, and the only route to
a good score is division, which is arithmetic rather than extraction. Measured
on LMM_POC's 55-receipt corpus, two VLMs both read the printed amount correctly
and scored 0.20-0.46 on this field for it; with the derived positions blanked, a
model that reports what is printed scores 1.000.

Where the quantity is 1 the unit price and the line total coincide, so the
printed amount IS the unit price and is kept.

INVOICES ARE EXCLUDED, and that gate is the load-bearing part of this change:
the invoice layout has an explicit "Unit Price" column, so the value is on the
page and the answer key is already correct. Blanking it would destroy correct
ground truth.
```

- [x] `TestBlanksOnlyWhatIsNotPrinted`: TestBlanksOnlyWhatIsNotPrinted
- [x] `TestBlanksOnlyWhatIsNotPrinted.test_quantity_one_keeps_its_amount`
- [x] `TestBlanksOnlyWhatIsNotPrinted.test_quantity_above_one_is_blanked`
- [x] `TestBlanksOnlyWhatIsNotPrinted.test_nothing_printed_collapses_to_the_scalar`: A list of nothing but NOT_FOUND is noise; the field is simply absent.
- [x] `TestBlanksOnlyWhatIsNotPrinted.test_decimal_quantity_is_not_treated_as_one`
- [x] `TestInvoicesAreUntouched`: TestInvoicesAreUntouched
- [x] `TestInvoicesAreUntouched.test_invoice_keeps_every_price`
- [x] `TestInvoicesAreUntouched.test_bank_statement_keeps_every_price`
- [x] `TestRefusesToGuess`: TestRefusesToGuess
- [x] `TestRefusesToGuess.test_leaves_the_field_alone`
- [x] `TestProjectionContract`: TestProjectionContract
- [x] `TestProjectionContract.test_key_order_is_preserved`
- [x] `TestProjectionContract.test_the_input_is_not_mutated`
- [x] `TestProjectionContract.test_other_fields_are_untouched`
- [x] `TestProjectionContract.test_is_idempotent`
- [x] `test_projection_applies_the_filter_to_a_raw_receipt (added)`

## `tests/test_receipt_fit.py`

```text
Receipt fit-safety: budgets present, no field clips, and unchanged docs stay
byte-identical while pre-existing overflows are fixed (wrapped/shrunk).
```

- [x] `test_every_receipt_layout_has_budgets_for_variable_fields`
- [x] `test_no_variable_field_overflows_after_fitting`: Every current string fits its budget losslessly (fit_text never raises).
- [x] `test_unchanged_docs_byte_identical` (adjusted: pre-fit renderer is gone; compares with the pinned pixel snapshot)

## `tests/test_receipt_pixel_snapshot.py` (OBSOLETE: replaced by the fresh three-type pixel snapshot)

```text
Phase A parity gate. Renders every receipt entry and asserts the page hash
and every recorded box match the baseline captured from the legacy renderer.

This does not retire with the legacy path the way the bank equivalence harness
did. After Task 14 it becomes the permanent regression guard for receipts, and
it is re-blessed only when Phase B intentionally changes the rendering.

To re-bless after an *intended* rendering change:

    conda run -n synthetic python tests/regenerate_doc_pixel_snapshot.py --confirm

There is no other way to update this fixture; `--confirm` is required and
defaults to False, so a bare run only prints a dry-run summary.
```

- [ ] `test_receipt_render_matches_baseline`

## `tests/test_schema_invariants.py`

```text
Arithmetic invariants `pipeline validate` enforces on ground truth.

Two rules that a hand-edited entry can violate while still rendering and
exporting happily, because nothing downstream recomputes them:

* parallel pipe-delimited fields must carry the same number of items;
* GST_AMOUNT must equal TOTAL_AMOUNT / 11 when the total is GST-inclusive.

Both rules are declared in config/field_definitions.yml, so these tests also
cover the fail-fast path when that declaration is missing.
```

- [x] `test_gst_inconsistent_with_total_is_reported`
- [x] `test_gst_consistent_with_total_passes`
- [x] `test_gst_rounds_half_up_like_the_seeder`: 100.00 / 11 is 9.0909..., which must round to 9.09 rather than truncate.
- [x] `test_gst_not_checked_when_total_excludes_gst`: IS_GST_INCLUDED false means the total is not the base for the fraction.
- [x] `test_missing_gst_consistency_block_fails_with_a_diagnostic`
- [x] `test_incomplete_gst_consistency_block_names_the_missing_keys`
- [x] `test_mismatched_parallel_list_lengths_are_reported`
- [x] `test_equal_parallel_list_lengths_pass`
- [x] `test_missing_parallel_field_groups_fails_with_a_diagnostic`
- [x] `test_real_corpus_satisfies_both_invariants`: The shipped ground truth must pass what validate now enforces.

## `tests/test_seed_ground_truth_dry_run.py`

```text
Tests for the rewired scripts/seed_ground_truth.py (local-only; tests/ is gitignored).
```

- [x] `test_generate_case_entities_produces_one_bundle_per_case`
- [x] `test_bank_and_invoice_share_holder_for_the_same_case`: PAYER_NAME must be identical across bank/invoice for a case (cross-case parity).
- [x] `test_receipt_supplier_never_matches_a_real_retailer`
- [x] `test_invoice_provider_never_matches_a_real_service_and_has_payer_address`
- [x] `test_seeded_bank_supplier_matches_its_layout`: Root cause: the bank must be derived from the layout, not drawn independently, so SUPPLIER_NAME is always the canonical bank name implied by the layout prefix.
- [x] `test_dry_run_validates_without_writing_ground_truth`

## `tests/test_seed_reproducibility.py`

```text
Runs the shipped seed-reproducibility check as part of the suite.

The check itself lives in `scripts/check_seed_reproducibility.py` rather than
here, because `tests/` is gitignored and this property is one a consumer of the
repo needs: it is the only thing that notices when an edit to a seed script
stops the committed corpus being reproducible. This module is a thin wrapper so
the local suite still exercises it; the logic has one home.
```

- [x] `test_seed_scripts_reproduce_the_committed_ground_truth_byte_identically`

## `tests/test_geometry_layouts.py`

```text
Task 10b: draw-time geometry capture for INVOICE_DATE, LINE_ITEM_QUANTITIES,
and LINE_ITEM_PRICES, the three DocILE-required fields Step 9 found had no
geometry at all (drawn via raw `draw.text` calls, not the capture-hooked
helpers).

Each test renders WITH a recorder (`geometry_out={}`) and asserts the
captured box is present, normalised within [0, 1], and positioned plausibly
(a label-prefixed value's box must not start at the page's left margin, it
must be offset past the label).

LINE_ITEM_PRICES on receipts is a documented, verified gap: receipts have no
draw site at all for the per-unit price (only the line total is rendered), so
there is nothing on the page to capture without adding new visible content,
which would violate the opt-in/non-destructive contract (pixels must stay
byte-identical with no recorder). That sub-case is asserted as BLOCKED, not
faked.
```

- [x] `test_receipt_invoice_date_captured_and_offset_past_label`: The receipt_meta branch draws 'Date: <value>' as one string; the recorded box must cover only the value, so it must not start at the left margin (it must be offset past the "Date: " label).
- [x] `test_invoice_invoice_date_captured_and_offset_past_label`: Same label-prefix check on the invoice renderer's live section/name==invoice_metadata branch.
- [x] `test_invoice_date_value_matches_ground_truth_string_width`: The box should be sized for the *value* substring, not the whole 'Date: <value>' string, i.e. narrower than the full label+value run.
- [x] `test_receipt_line_item_quantity_captured_when_rendered`: CASE003 has quantities > 1, so the receipt renders a '2x ' prefix on the description line; that prefix's own sub-box must be captured.
- [x] `test_receipt_line_item_quantity_of_one_has_no_box_by_design`: Quantity 1 is never printed on receipts (real receipts don't write '1x Coffee'), so there is genuinely nothing to capture for those rows. This documents the known, deliberate shortfall rather than hiding it.
- [x] `test_invoice_line_item_quantity_captured_for_every_item`: Invoices always print the quantity column regardless of its value, so every line item's quantity must be captured.
- [x] `test_invoice_line_item_price_captured_for_every_item`: Regression guard: invoices already had full LINE_ITEM_PRICES coverage before this change; must remain so.
- [x] ~~test_receipt_line_item_price_is_blocked_no_draw_site_exists~~ dropped (7 October 2026): the gap is fixed; every receipt line of more than one unit prints its unit price on a unit line. Replaced by `test_receipt_line_item_price_boxed_only_where_quantity_exceeds_one`
- [x] `test_no_recorder_still_byte_identical_after_task10b_changes`: The new capture calls must never alter pixels when there is no recorder, same invariant Task 10 Step 9 established. (adjusted: pre-capture renderer is gone; compares with the pinned pixel snapshot)

## `tests/test_transaction_dates.py`

```text
Bank-statement transaction dates must be realistic and internally consistent.

Target state (all four user directives):
1. A linked receipt/invoice and its bank transaction share the SAME date
   (the common real-world case: a purchase appears on the statement on its date).
2. Every transaction date falls within the statement's ~1-month period
   (all dates share one calendar month + year).
3. Each statement's transactions are in chronological (ascending) order.
4. STATEMENT_DATE_RANGE == earliest - latest of the date column.

Difficulty is now defined by description quality (date is always an exact match):
easy = full merchant name in the bank description, medium = an abbreviation,
hard = no merchant reference at all.
```

- [x] `test_bank_transaction_dates_are_chronological`
- [x] `test_statement_date_range_equals_earliest_to_latest`
- [x] `test_all_transaction_dates_fall_within_one_month`
- [x] `test_linked_receipt_and_bank_dates_are_identical`
- [x] `test_link_dates_match_the_underlying_ground_truth`: The link's date must actually be the receipt/invoice date AND appear on the named bank statement against the linked amount; otherwise the link is unscoreable.
- [x] `test_links_are_complete_and_every_difficulty_is_present` (was test_difficulty_distribution_is_preserved; the fixed 52/36/22 split is no longer pinned, 7 October 2026)
- [x] `test_description_quality_matches_difficulty`: easy => full merchant name present; hard => no merchant reference.
