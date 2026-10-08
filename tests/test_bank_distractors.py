"""Every bank statement layout draws its inline distractors, labelled, decodable and on one page.

Covers Stage 5's review focus 5 for the inline items: a statement pushed off its fixed page (every item
forced on, every case each layout may carry, every layout), the page-fit refusal, a selected item whose
block never draws, a mailing barcode that does not decode, an issued date that could be read as the
statement period, and a plain render changed by the new blocks. Inline items sit beside existing lines
or at the foot, so no scored box moves. The distractor renders themselves are pinned in
`tests/fixtures/distractor_pixel_snapshot.json` (see tests/test_receipt_distractors.py).
"""

import copy
import re

import pytest

from generators.bank_statement import _LAYOUT_FILE, render_bank_statement
from generators.common import PageOverflowError
from generators.distractors.config import load_distractor_config
from generators.distractors.density import density_report, measure_render
from generators.overflow_check import check_overflow
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import (
    budgets,
    capture_bank_overlay,
    decoded,
    printed_dx_names,
    selected,
    walk_blocks,
)
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.snapshot_tools import digest, load_snapshot

DOC = "bank_statement"
CONFIG = load_distractor_config()
INLINE_ITEMS = ["bk_issued_date", "bk_mail_barcode", "bk_promo_message"]
ANNOTATION_ITEMS = ["bk_annotated_rows", "bk_row_marks", "bk_annotation_total", "bk_margin_note"]
ALL_ITEMS = INLINE_ITEMS + ANNOTATION_ITEMS
BARCODE_KEYS = ("DX_MAIL_BARCODE",)
_PLACEHOLDER = re.compile(r"\{[A-Z0-9_]+\}")


def _count(entry: dict) -> int:
    return len(entry["fields"]["TRANSACTION_DATES"].split("|"))


def carried(layout_id: str) -> dict[str, dict]:
    """Every corpus case a layout may carry (its transaction count within the layout's declared range),
    moved onto that layout: the seeder puts a case on a layout only within that range."""
    bounds = layouts_for(DOC)[layout_id]["transaction_count"]
    return {
        case_id: {**entry, "layout": layout_id}
        for case_id, entry in corpus_entries(DOC).items()
        if bounds["min"] <= _count(entry) <= bounds["max"]
    }


def _ink_outside_the_page_box(geometry: dict, layout: dict, before: int) -> list[str]:
    """Every inline ink record (the first `before`, recorded ahead of the overlay) drawn outside the page's
    margins (the ANZ banner aside): renderers never clip. Overlay marks may sit in the margins, inside
    the page inset, which the overlay engine checks itself."""
    margin = int(layout["margin"])
    right = margin + int(layout["content_width"])
    bottom = int(layout["page_dimensions"]["height"]) - margin
    return [
        f"{record.role} ink at {record.box}"
        for record in geometry["ink"][:before]
        if record.role == "distractor"
        and (
            record.box[0] < margin
            or record.box[1] < margin
            or record.box[2] > right
            or record.box[3] > bottom
        )
    ]


# --- the shipped layouts ------------------------------------------------------------------------------


def test_every_bank_layout_lists_the_inline_items_and_the_provider() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        assert sorted(layout["distractor_items"]) == sorted(ALL_ITEMS), layout_id
        assert {"name": "bank_distractors", "params": {}} in layout["field_providers"], layout_id


def test_the_selection_suits_the_seven_items() -> None:
    """At least two items, so a page never carries the highlighter alone (a tint records no ink)."""
    low, high = CONFIG.selection[DOC]
    assert 2 <= low <= high <= len(ALL_ITEMS)


def test_no_distractor_text_uses_a_scored_fields_budget() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        scored, distractor = budgets(layout["body"])
        assert distractor, layout_id
        assert not distractor & scored, (layout_id, distractor & scored)
        assert all(name.startswith("DISTRACTOR_") for name in distractor), (layout_id, distractor)


def _scored_labels(body: list) -> set[str]:
    labels = set()
    for block in walk_blocks(body):
        if str(block.get("when", "")).startswith("DX_") or block.get("type") not in ("text", "pair"):
            continue
        text = block.get("label", block.get("content", ""))
        labels.add(_PLACEHOLDER.sub("", str(text)).strip(" :").lower())
    return labels - {""}


def issued_date_labels(body: list) -> list[str]:
    """The words every block printing the issued date prints beside it."""
    labels = []
    for block in walk_blocks(body):
        if "DX_ISSUED_DATE" in printed_dx_names(block):
            text = block["label"] if block.get("type") == "pair" else str(block.get("content", ""))
            labels.append(_PLACEHOLDER.sub("", text).strip(" :").lower())
    return labels


def test_the_issued_date_prints_under_its_own_label_never_the_periods() -> None:
    """The near-miss for STATEMENT_DATE_RANGE says it is the issue date; it never borrows a scored label."""
    for layout_id, layout in layouts_for(DOC).items():
        labels = issued_date_labels(layout["body"])
        assert labels == ["statement issued"], (layout_id, labels)
        assert not set(labels) & _scored_labels(layout["body"]), layout_id


def test_an_unlabelled_issued_date_is_caught() -> None:
    body = copy.deepcopy(layouts_for(DOC)["cba_standard"]["body"])
    for block in walk_blocks(body):
        if "DX_ISSUED_DATE" in printed_dx_names(block):
            block["content"] = "{DX_ISSUED_DATE}"
    assert issued_date_labels(body) == [""]


def test_the_mailing_barcode_prints_no_digits() -> None:
    """A mail-sort barcode carries no human-readable line: it is a mark, never text a reader could take."""
    for layout_id, layout in layouts_for(DOC).items():
        marks = [b for b in walk_blocks(layout["body"]) if b.get("type") == "barcode"]
        assert len(marks) == 1, layout_id
        assert marks[0]["content"] == "{DX_MAIL_BARCODE}" and marks[0]["human_readable"] is False


# --- end to end: every case on its own layout ---------------------------------------------------------


def test_every_selected_item_draws_and_every_mark_decodes(
    drawn_keys: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = capture_bank_overlay(monkeypatch)
    plain_pins = load_snapshot()
    layouts = layouts_for(DOC)
    low, _ = CONFIG.selection[DOC]
    for case_id, entry in corpus_entries(DOC).items():
        layout = layouts[entry["layout"]]
        chosen = selected(entry, layout)
        assert len(chosen) >= low, (case_id, chosen)

        drawn_keys.clear()
        geometry: dict = {}
        image = render_bank_statement(entry, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, sorted(set(chosen) - drawn_keys))
        assert any(r.role == "distractor" for r in geometry["ink"]), case_id
        assert not _ink_outside_the_page_box(geometry, layout, runs[-1].before), case_id
        assert decoded(image) == sorted(chosen[k] for k in BARCODE_KEYS if k in chosen), case_id

        plain_geometry: dict = {}
        plain = render_bank_statement(entry, layout, distractors=False, geometry_out=plain_geometry)
        assert digest(plain) == plain_pins[f"{case_id}_{entry['layout']}"]["hash"], case_id
        # Inline items sit beside existing lines or at the foot: no scored value moves.
        assert geometry["boxes"] == plain_geometry["boxes"], case_id


def test_every_layout_draws_each_item_somewhere_in_the_corpus() -> None:
    seen: dict[str, set[str]] = {}
    layouts = layouts_for(DOC)
    for entry in corpus_entries(DOC).values():
        seen.setdefault(entry["layout"], set()).update(selected(entry, layouts[entry["layout"]]))
    assert set(seen) == set(layouts)
    everything = {k for i in ALL_ITEMS for k in CONFIG.items[i].emits}
    assert set().union(*seen.values()) == everything


# --- forced: every item, every case a layout may carry, every bank layout ------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", list(layouts_for(DOC)))
def test_every_item_on_every_carried_case_fits_one_page_of_every_bank_layout(
    every_item_forced: None, drawn_keys: set[str], layout_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review focus 5: with every item drawn, every case fits one page, unclipped, nothing moved, and
    meets the bank density bounds (Ruling 5)."""
    runs = capture_bank_overlay(monkeypatch)
    bounds = CONFIG.density[DOC]
    layout = layouts_for(DOC)[layout_id]
    cases = carried(layout_id)
    assert len(cases) >= 6, layout_id
    for case_id, entry in cases.items():
        chosen = selected(entry, layout)
        assert set(chosen) == {k for i in ALL_ITEMS for k in CONFIG.items[i].emits}, case_id
        drawn_keys.clear()
        geometry: dict = {}
        image = render_bank_statement(entry, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, layout_id, sorted(set(chosen) - drawn_keys))
        outside = _ink_outside_the_page_box(geometry, layout, runs[-1].before)
        report = density_report(geometry["ink"], int(geometry["height"]))
        assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, layout_id, report)
        assert report.distractor_share >= bounds.distractor_share_min, (case_id, layout_id, report)
        assert bounds.page_fill_min is not None and report.page_fill >= bounds.page_fill_min
        assert not outside, (case_id, layout_id, outside[:3])
        assert decoded(image) == [chosen["DX_MAIL_BARCODE"]], (case_id, layout_id)
        plain: dict = {}
        render_bank_statement(entry, layout, distractors=False, geometry_out=plain)
        assert geometry["boxes"] == plain["boxes"], (case_id, layout_id)


def test_the_promotional_panel_sits_at_the_foot(every_item_forced: None) -> None:
    """The panel is drawn up from the bottom margin, so its text ends just above it on every layout."""
    for layout_id, layout in layouts_for(DOC).items():
        entry = next(iter(carried(layout_id).values()))
        geometry: dict = {}
        render_bank_statement(entry, layout, distractors=True, geometry_out=geometry)
        bottom = int(layout["page_dimensions"]["height"]) - int(layout["margin"])
        promo = [r.box for r in geometry["ink"] if r.role == "distractor" and not r.mark]
        lowest = max(box[3] for box in promo)
        assert bottom - 80 <= lowest <= bottom, (layout_id, lowest, bottom)


# --- a page overrun fails fast ------------------------------------------------------------------------


def _too_long(entry: dict, times: int) -> dict:
    """A synthetic statement with its transactions repeated, longer than any page holds."""
    fields = dict(entry["fields"])
    for key in (
        "TRANSACTION_DATES",
        "TRANSACTION_DESCRIPTIONS",
        "TRANSACTION_AMOUNTS_PAID",
        "TRANSACTION_AMOUNTS_RECEIVED",
    ):
        fields[key] = "|".join([fields[key]] * times)
    return {**entry, "fields": fields, "case_id": "CASE900"}


def _short_page(layout: dict, height: int) -> dict:
    shorter = copy.deepcopy(layout)
    shorter["page_dimensions"]["height"] = height
    return shorter


def _select(monkeypatch: pytest.MonkeyPatch, items: list[str]) -> None:
    from generators.distractors import providers

    monkeypatch.setattr(providers, "select_items", lambda config, doc_type, eligible, rng, *, where: items)


@pytest.mark.parametrize("distractors", [False, True])
def test_a_statement_too_long_for_its_page_fails_fast(
    distractors: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page-fit check itself: with distractors, the inline items beside the period rows (a selected
    promotional panel would be refused first, by the foot anchor; see the next test)."""
    _select(monkeypatch, ["bk_issued_date", "bk_mail_barcode"])
    layout_id = "cba_standard"
    entry = _too_long(next(iter(carried(layout_id).values())), 3)
    with pytest.raises(PageOverflowError) as caught:
        render_bank_statement(entry, layouts_for(DOC)[layout_id], distractors=distractors)
    message = str(caught.value)
    assert_diagnostic_error(message)
    mode = "with distractors" if distractors else "plain"
    assert "CASE900" in message and f"'{layout_id}'" in message and f"({mode})" in message
    assert re.search(r"\d+px below the bottom margin", message), message
    assert f"{_LAYOUT_FILE} -> {layout_id}.body" in message and _LAYOUT_FILE.is_absolute()
    recover = message.split("Recover:")[1]
    assert "split" in recover and "DISTRACTOR_" in recover and f"{layout_id}.distractor_items" in recover


def test_a_promotional_panel_with_no_room_at_the_foot_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    _select(monkeypatch, ["bk_promo_message"])
    layout_id = "westpac_standard"
    entry = _too_long(next(iter(carried(layout_id).values())), 3)
    with pytest.raises(PageOverflowError) as caught:
        render_bank_statement(entry, layouts_for(DOC)[layout_id], distractors=True)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert "anchor: foot" in message and f"{layout_id}.body[" in message and "CASE900" in message


def test_a_body_that_ends_at_the_bottom_margin_renders() -> None:
    """The bound is the bottom margin itself: a body ending exactly there is not refused."""
    layout_id = "anz_standard"
    layout = layouts_for(DOC)[layout_id]
    entry = next(iter(carried(layout_id).values()))
    with pytest.raises(PageOverflowError) as caught:
        render_bank_statement(entry, _short_page(layout, 900), distractors=False)
    end = int(re.search(r"ends at y=(\d+)", str(caught.value)).group(1))  # type: ignore[union-attr]
    render_bank_statement(entry, _short_page(layout, end + int(layout["margin"])), distractors=False)
    with pytest.raises(PageOverflowError):
        render_bank_statement(
            entry, _short_page(layout, end + int(layout["margin"]) - 1), distractors=False
        )


def test_validate_reports_a_page_overrun_as_an_overflow() -> None:
    layouts = {layout_id: _short_page(layout, 900) for layout_id, layout in layouts_for(DOC).items()}
    gt = dict(list(corpus_entries(DOC).items())[:2])
    violations = check_overflow(gt, layouts, render_bank_statement)
    assert len(violations) >= 2, violations
    assert all("bottom margin" in line for line in violations), violations


# --- density -------------------------------------------------------------------------------------------


def test_every_distractor_render_meets_the_bank_bounds() -> None:
    bounds = CONFIG.density[DOC]
    layouts = layouts_for(DOC)
    for case_id, entry in corpus_entries(DOC).items():
        report = measure_render(render_bank_statement, entry, layouts[entry["layout"]], True)
        assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, report)
        assert report.distractor_share >= bounds.distractor_share_min, (case_id, report)
        assert bounds.page_fill_min is not None and report.page_fill >= bounds.page_fill_min, (
            case_id,
            report,
        )


def test_the_bank_bounds_keep_their_headroom_with_every_item_forced(every_item_forced: None) -> None:
    """Each bound sits at least 10% (relative) outside the extreme measured over the corpus's own
    selections and over every case with all three items forced (config/distractors.yml)."""
    bounds = CONFIG.density[DOC]
    assert bounds.page_fill_min is not None
    layouts = layouts_for(DOC)
    reports = []
    for entry in corpus_entries(DOC).values():
        reports.append(measure_render(render_bank_statement, entry, layouts[entry["layout"]], True))
    assert bounds.scored_ink_ratio_max >= 1.10 * max(r.scored_ink_ratio for r in reports)
    assert bounds.page_fill_min <= 0.90 * min(r.page_fill for r in reports)
    assert bounds.distractor_share_min <= 0.90 * min(r.distractor_share for r in reports)


def test_the_bank_bounds_hold_the_corpus_selections_with_headroom() -> None:
    bounds = CONFIG.density[DOC]
    assert bounds.page_fill_min is not None
    layouts = layouts_for(DOC)
    reports = [
        measure_render(render_bank_statement, entry, layouts[entry["layout"]], True)
        for entry in corpus_entries(DOC).values()
    ]
    assert bounds.scored_ink_ratio_max >= 1.10 * max(r.scored_ink_ratio for r in reports)
    assert bounds.page_fill_min <= 0.90 * min(r.page_fill for r in reports)
    assert bounds.distractor_share_min <= 0.90 * min(r.distractor_share for r in reports)


def _distractor_ink(entry: dict, layout: dict, distractors: bool) -> list:
    geometry: dict = {}
    render_bank_statement(entry, layout, distractors=distractors, geometry_out=geometry)
    return [r for r in geometry["ink"] if r.role == "distractor"]


def test_every_distractor_render_carries_distractor_ink_and_no_plain_render_does() -> None:
    """Decision 14: a statement whose only item is the mailing barcode has a zero text share, so the
    share floor cannot separate the two; recorded distractor ink (text or mark) does."""
    layouts = layouts_for(DOC)
    for case_id, entry in corpus_entries(DOC).items():
        layout = layouts[entry["layout"]]
        assert _distractor_ink(entry, layout, True), case_id
        assert not _distractor_ink(entry, layout, False), case_id


def test_a_barcode_only_statement_has_a_zero_share_but_still_records_a_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from generators.distractors import providers

    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: ["bk_mail_barcode"]
    )
    layout_id = "westpac_standard"
    entry = next(iter(carried(layout_id).values()))
    report = measure_render(render_bank_statement, entry, layouts_for(DOC)[layout_id], True)
    assert report.distractor_share == 0.0
    ink = _distractor_ink(entry, layouts_for(DOC)[layout_id], True)
    assert ink and all(record.mark for record in ink)
