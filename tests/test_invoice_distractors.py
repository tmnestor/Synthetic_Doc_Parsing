"""Every invoice layout draws its selected distractors, labelled, legibly and on one A4 page.

Covers Stage 4's review focus for invoices: a dense invoice that runs off its fixed-height page (every
item forced on, every case, every layout); a near-miss a careful reader cannot tell from its scored
field (every near-miss and repeat carries its own label, never a scored block's); a selected item whose
block never draws; a remittance barcode that does not decode; and a plain render changed by the new
blocks. The distractor renders themselves are pinned in `tests/fixtures/distractor_pixel_snapshot.json`
(see tests/test_receipt_distractors.py for the snapshot tests).
"""

import copy
import re
from collections.abc import Callable

import pytest

from generators.common import FitError
from generators.distractors.config import load_distractor_config
from generators.distractors.density import measure_render
from generators.invoice import render_invoice
from generators.overflow_check import check_overflow
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import budgets, decoded, printed_dx_names, selected, walk_blocks
from generators.distractors import providers
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.snapshot_tools import digest, load_snapshot

DOC = "invoice"
CONFIG = load_distractor_config()
INVOICE_ITEMS = sorted(i for i, item in CONFIG.items.items() if DOC in item.doc_types)
KEY_KIND = {key: item.kind for item in CONFIG.items.values() if DOC in item.doc_types for key in item.emits}
# Items listed only on the industry layouts whose trade prints them (tests/test_industry_invoice_layouts.py
# checks each industry layout's own list): the four general layouts list none of them.
UNLISTED = {
    "in_licence",
    "in_warranty",
    "in_ship_to",
    "in_site_address",
    "in_matter_reference",
    "in_delivery_docket",
    "in_billing_period",
    "in_direct_debit",
    "in_engagement_terms",
    "in_upgrade_price",
    "in_client_reference",
}
GENERAL_LAYOUTS = (
    "tax_invoice_standard",
    "tax_invoice_gst_inclusive",
    "tax_invoice_high_value",
    "tax_invoice_mixed",
)
PAYMENT = CONFIG.states[DOC]["payment"]
BARCODE_KEYS = ("DX_REMIT_BARCODE",)
_PLACEHOLDER = re.compile(r"\{[A-Z0-9_]+\}")


def _sites(layout: dict) -> dict[str, str]:
    """Each label site's key and the wording a plain or distractor render prints for it."""
    spec = next(s for s in layout["field_providers"] if s["name"] == "label_styles")
    return {f"{{LABEL_{field}}}": site["labelled"] for field, site in spec["params"]["sites"].items()}


def _label(block: dict, sites: dict[str, str] | None = None) -> str:
    """The literal words a block prints beside its values, normalised for comparison.

    A label site's placeholder reads as the wording the layout prints for it (`sites`).
    """
    text = block["label"] if block.get("type") == "pair" else str(block.get("content", ""))
    for placeholder, wording in (sites or {}).items():
        text = text.replace(placeholder, wording)
    return _PLACEHOLDER.sub("", text).strip(" :$").lower()


def _labelled(blocks: list) -> list[tuple[dict, str]]:
    """Every block with its label: its own words, else a heading line just above it on the same gate.

    "Ship To:" heads the delivery address as "Bill To:" heads the payer, on a line of its own gated on
    the same key; the heading counts as the address's label. Anything else unlabelled stays unlabelled.
    """
    out: list[tuple[dict, str]] = []
    previous: dict | None = None
    for item in blocks:
        if isinstance(item, list):
            out += _labelled(item)
            previous = None
            continue
        label = _label(item) if item.get("type") in ("text", "pair") else ""
        if (
            not label
            and previous is not None
            and previous.get("when") == item.get("when")
            and not printed_dx_names(previous)
        ):
            label = _label(previous)
        out.append((item, label))
        children = item.get("children", []) or []
        out += _labelled(children)
        previous = item
    return out


def _scored_labels(body: list, sites: dict[str, str] | None = None) -> set[str]:
    """The labels of every block that draws on a plain render (Date, Total, Bill To and the rest)."""
    labels = set()
    for block in walk_blocks(body):
        if str(block.get("when", "")).startswith("DX_") or block.get("type") not in ("text", "pair"):
            continue
        if "label" in block or "content" in block:
            labels.add(_label(block, sites))
    return labels - {""}


# --- the catalogue and the shipped layouts ----------------------------------------------------------


def test_the_catalogue_declares_the_remittance_barcode_and_the_page_number() -> None:
    barcode = CONFIG.items["in_remit_barcode"]
    assert barcode.kind == "clutter" and list(barcode.emits) == ["DX_REMIT_BARCODE"]
    assert barcode.rule["type"] == "digits" and barcode.rule["prefix"] == ""
    assert barcode.rule["length"] % 2 == 0 and 0 < barcode.rule["length"] <= 16
    page = CONFIG.items["in_page_number"]
    assert page.kind == "clutter" and list(page.emits) == ["DX_PAGE_NUMBER"]
    assert page.rule == {"type": "fixed", "value": "Page 1 of 1"}


def test_every_invoice_layout_lists_every_invoice_item_and_the_provider() -> None:
    assert set(GENERAL_LAYOUTS) <= set(layouts_for(DOC))
    for layout_id, layout in layouts_for(DOC).items():
        if layout_id in GENERAL_LAYOUTS:
            assert sorted(layout["distractor_items"]) == sorted(set(INVOICE_ITEMS) - UNLISTED), layout_id
        assert {"name": "invoice_distractors", "params": {}} in layout["field_providers"], layout_id


def test_no_distractor_text_uses_a_scored_fields_budget() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        scored, distractor = budgets(layout["body"])
        assert distractor, layout_id
        assert not distractor & scored, (layout_id, distractor & scored)
        assert all(name.startswith("DISTRACTOR_") for name in distractor), (layout_id, distractor)


def _label_problems(body: list, sites: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Every near-miss or repeat printed unlabelled or under a scored label, and each key's label."""
    scored = _scored_labels(body, sites)
    problems: list[str] = []
    labels: dict[str, str] = {}
    for block, label in _labelled(body):
        for key in printed_dx_names(block):
            if KEY_KIND[key] not in ("near_miss", "repeat"):
                continue
            if not label:
                problems.append(f"{key} prints with no label: {block}")
            elif label in scored:
                problems.append(f"{key} prints under the scored label {label!r}")
            # A key printed twice (the previous balance, and the payment that cleared it) is named by
            # its first label.
            labels.setdefault(key, label)
    return problems, labels


def _overlay_labels(overlay: list) -> dict[str, str]:
    """Keys an overlay block prints under its own words.

    A PAID stamp's date line sits under the stamp's word, which labels it as the date the invoice was paid
    (never a scored label).
    """
    return {
        block["date_key"]: "paid stamp"
        for block in overlay
        if block["type"] == "stamp" and block["date_key"] != "none"
    }


def test_every_near_miss_and_repeat_prints_beside_its_own_label() -> None:
    """Review focus 2: no near-miss is unlabelled, and none borrows a scored block's label."""
    for layout_id, layout in layouts_for(DOC).items():
        assert {"date", "total", "bill to"} <= _scored_labels(layout["body"], _sites(layout)), layout_id
        problems, labels = _label_problems(layout["body"], _sites(layout))
        assert not problems, (layout_id, problems)
        stamped = _overlay_labels(layout["overlay"])
        assert stamped == {"DX_PAID_DATE": "paid stamp"} and not set(stamped) & set(labels), layout_id
        labels.update(stamped)
        listed = {k for i in layout["distractor_items"] for k in CONFIG.items[i].emits}
        assert {k for k, kind in KEY_KIND.items() if kind in ("near_miss", "repeat")} & listed <= set(
            labels
        )
        if layout_id not in GENERAL_LAYOUTS:
            continue
        for key, words in {
            "DX_AMOUNT_PAID": "amount paid",
            "DX_BALANCE_DUE": "balance due",
            "DX_PREVIOUS_BALANCE": "previous balance",
            "DX_ACCOUNT_TOTAL": "this invoice",
            "DX_REMITTANCE_TOTAL": "amount due",
            "DX_DUE_DATE": "due date",
            "DX_SUPPLY_DATE": "date of supply",
        }.items():
            assert labels[key] == words, (layout_id, key, labels[key])


def _edited(body: list, edit: Callable[[dict], dict | None]) -> list:
    """A deep copy of `body` with `edit` applied to every block; a block it maps to None is dropped."""
    out: list = []
    for item in body:
        if isinstance(item, list):
            out.append(_edited(item, edit))
            continue
        block = edit(copy.deepcopy(item))
        if block is None:
            continue
        if "children" in block:
            block["children"] = _edited(block["children"], edit)
        out.append(block)
    return out


def test_an_unlabelled_or_borrowed_near_miss_label_is_caught() -> None:
    first = next(iter(layouts_for(DOC).values()))
    body, sites = first["body"], _sites(first)

    def bare_due_date(block: dict) -> dict | None:
        return (
            {**block, "content": "{DX_DUE_DATE}"}
            if block.get("content") == "Due date: {DX_DUE_DATE}"
            else block
        )

    def balance_labelled_total(block: dict) -> dict | None:
        return {**block, "label": "Total"} if block.get("label") == "Balance due" else block

    problems, _ = _label_problems(_edited(body, bare_due_date), sites)
    assert any(p.startswith("DX_DUE_DATE prints with no label") for p in problems), problems
    problems, _ = _label_problems(_edited(body, balance_labelled_total), sites)
    assert problems == ["DX_BALANCE_DUE prints under the scored label 'total'"], problems


def force_state(monkeypatch: pytest.MonkeyPatch, choice: str) -> None:
    """Draw every invoice in the given payment state."""
    monkeypatch.setattr(
        providers,
        "draw_states",
        lambda config, case_id, doc_type: {"payment": choice} if config.states[doc_type] else {},
    )


def test_a_missing_amount_due_is_caught_although_the_slip_heading_draws(
    drawn_keys: set[str], every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slip's heading is gated on DX_REMITTANCE_TOTAL but never prints it, so it must not count."""
    force_state(monkeypatch, "unpaid")
    layout_id, layout = next(iter(layouts_for(DOC).items()))
    entry = {**next(iter(corpus_entries(DOC).values())), "layout": layout_id}
    broken = {
        **layout,
        "body": _edited(layout["body"], lambda b: None if b.get("value") == "{DX_REMITTANCE_TOTAL}" else b),
    }
    assert "DX_REMITTANCE_TOTAL" in selected(entry, broken)
    render_invoice(entry, broken, distractors=True)
    assert "DX_REMITTANCE_TOTAL" not in drawn_keys
    render_invoice(entry, layout, distractors=True)
    assert "DX_REMITTANCE_TOTAL" in drawn_keys


# --- end to end: every case on its own layout ---------------------------------------------------------


def _ink_inside_the_page_box(geometry: dict, layout: dict) -> list[str]:
    """Every ink record drawn outside the page's margins: renderers never clip."""
    margin = int(layout["margin"])
    right = margin + int(layout["content_width"])
    bottom = int(layout["page_dimensions"]["height"]) - margin
    return [
        f"{record.role} ink at {record.box}"
        for record in geometry["ink"]
        if record.box[0] < margin
        or record.box[1] < margin
        or record.box[2] > right
        or record.box[3] > bottom
    ]


def test_every_selected_item_draws_and_every_mark_decodes(drawn_keys: set[str]) -> None:
    plain_pins = load_snapshot()
    layouts = layouts_for(DOC)
    low, _ = CONFIG.selection[DOC]
    for case_id, entry in corpus_entries(DOC).items():
        layout = layouts[entry["layout"]]
        chosen = selected(entry, layout)
        assert len(chosen) >= low, (case_id, chosen)

        drawn_keys.clear()
        geometry: dict = {}
        image = render_invoice(entry, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, sorted(set(chosen) - drawn_keys))
        assert any(r.role == "distractor" for r in geometry["ink"]), case_id
        assert not _ink_inside_the_page_box(geometry, layout), case_id
        assert decoded(image) == sorted(chosen[k] for k in BARCODE_KEYS if k in chosen), case_id

        plain = render_invoice(entry, layout, distractors=False)
        assert digest(plain) == plain_pins[f"{case_id}_{entry['layout']}"]["hash"], case_id


# --- forced: every item, every case, every invoice layout ---------------------------------------------


def _group_keys(choice: str) -> set[str]:
    return {k for i in PAYMENT.groups[choice] for k in CONFIG.items[i].emits}


def expected_forced_keys(case_id: str, layout: dict, state: str) -> set[str]:
    """The keys a page with every item forced draws in `state`: the layout's items less the other state's
    group and the members its exclusive sets drop (config/distractors.yml -> exclusive)."""
    other = "unpaid" if state == "paid" else "paid"
    excluded = providers.excluded_items(CONFIG, DOC, {"payment": state})
    left = [i for i in layout["distractor_items"] if i not in excluded]
    excluded |= providers.exclusive_excluded(CONFIG, case_id, DOC, left)
    keys = {k for i in layout["distractor_items"] if i not in excluded for k in CONFIG.items[i].emits}
    assert not keys & _group_keys(other)
    return keys


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", list(layouts_for(DOC)))
@pytest.mark.parametrize("state", ["paid", "unpaid"])
def test_every_item_on_every_case_fits_one_page_of_every_invoice_layout(
    every_item_forced: None,
    drawn_keys: set[str],
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    layout_id: str,
) -> None:
    """Review focus 1: with every item of a payment state drawn, every case fits one A4 page, unclipped.

    Every paid-group item prints on a paid invoice and none of the unpaid group, and the reverse, less
    the members of an exclusive set the page does not keep (a direct-debit page carries no slip).
    """
    force_state(monkeypatch, state)
    other = "unpaid" if state == "paid" else "paid"
    layout = layouts_for(DOC)[layout_id]
    for case_id, entry in corpus_entries(DOC).items():
        moved = {**entry, "layout": layout_id}
        chosen = selected(moved, layout)
        assert "DX_PAGE_NUMBER" in chosen, (case_id, layout_id)
        assert set(chosen) == expected_forced_keys(case_id, layout, state), (case_id, layout_id)
        assert not _group_keys(other) & set(chosen), (case_id, layout_id)
        drawn_keys.clear()
        geometry: dict = {}
        image = render_invoice(moved, layout, distractors=True, geometry_out=geometry)
        assert set(chosen) <= drawn_keys, (case_id, layout_id, sorted(set(chosen) - drawn_keys))
        outside = _ink_inside_the_page_box(geometry, layout)
        assert not outside, (case_id, layout_id, outside[:3])
        barcodes = [chosen["DX_REMIT_BARCODE"]] if "DX_REMIT_BARCODE" in chosen else []
        assert ("DX_REMIT_BARCODE" in chosen) == (state == "unpaid" and "DX_DIRECT_DEBIT" not in chosen)
        assert decoded(image) == barcodes, (case_id, layout_id)


# --- the footer sits at the foot of the page -----------------------------------------------------------


def _footer_ink_bottom(layout: dict, entry: dict, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """The lowest ink y of the foot-anchored footer, and the y its first block starts at."""
    from generators.layout_dsl import engine

    starts: list[int] = []
    real = engine.PRIMITIVE_DRAWERS["stack"]

    def spy(block: dict, ctx: engine.RenderContext, y: int) -> int:
        # Only the foot-anchored stack: an industry layout groups body blocks in plain stacks too.
        if ctx.ink_recorder is not None and block.get("anchor") == "foot":
            starts.append(y)
        return real(block, ctx, y)

    monkeypatch.setitem(engine.PRIMITIVE_DRAWERS, "stack", spy)
    geometry: dict = {}
    render_invoice(entry, layout, distractors=True, geometry_out=geometry)
    monkeypatch.setitem(engine.PRIMITIVE_DRAWERS, "stack", real)
    assert len(starts) == 1
    footer = [r.box for r in geometry["ink"] if r.box[1] >= starts[0]]
    assert footer
    return max(box[3] for box in footer), starts[0]


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", list(layouts_for(DOC)))
@pytest.mark.parametrize("state", ["paid", "unpaid"])
def test_every_foot_anchored_footer_ends_within_the_bottom_margin_band(
    monkeypatch: pytest.MonkeyPatch, state: str, layout_id: str
) -> None:
    """The footer's ink ends just above the bottom margin, on every case and layout, in either state.

    The band is the last line's advance: the page number's 28px line on the 100px margin.
    """
    force_state(monkeypatch, state)
    layout = layouts_for(DOC)[layout_id]
    bottom = int(layout["page_dimensions"]["height"]) - int(layout["margin"])
    for case_id, entry in corpus_entries(DOC).items():
        moved = {**entry, "layout": layout_id}
        if "DX_PAGE_NUMBER" not in selected(moved, layout):
            continue
        ink_bottom, start = _footer_ink_bottom(layout, moved, monkeypatch)
        assert bottom - 28 <= ink_bottom <= bottom, (case_id, layout_id, ink_bottom)
        assert start > bottom // 2, (case_id, layout_id, start)


def test_a_footer_drawn_from_the_top_would_fail_the_band_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The band check can fail: with the anchor ignored, the footer ends just under the body."""
    from generators.layout_dsl import primitives_container

    monkeypatch.setattr(primitives_container, "foot_origin", lambda block, ctx, y, draw, *, at: y)
    from generators.layout_dsl import engine

    monkeypatch.setattr(engine, "foot_origin", lambda block, ctx, y, draw, *, at: y)
    force_state(monkeypatch, "paid")
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: eligible
    )
    layout_id, layout = next(iter(layouts_for(DOC).items()))
    entry = {**next(iter(corpus_entries(DOC).values())), "layout": layout_id}
    ink_bottom, _ = _footer_ink_bottom(layout, entry, monkeypatch)
    assert ink_bottom < int(layout["page_dimensions"]["height"]) - int(layout["margin"]) - 28


def test_a_footer_that_would_meet_the_body_fails_fast(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_state(monkeypatch, "unpaid")
    layout_id, layout = next(iter(layouts_for(DOC).items()))
    entry = {**next(iter(corpus_entries(DOC).values())), "layout": layout_id}
    with pytest.raises(FitError) as caught:
        render_invoice(entry, _short_page(layout, 1700), distractors=True)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert "anchor: foot" in message and f"{layout_id}.body[" in message
    assert "config/layouts/invoices.yml" in message


def test_the_footer_check_reports_through_validate(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_state(monkeypatch, "unpaid")
    layouts = {layout_id: _short_page(layout, 1700) for layout_id, layout in layouts_for(DOC).items()}
    gt = dict(list(corpus_entries(DOC).items())[:2])
    violations = check_overflow(gt, layouts, render_invoice)
    assert any("foot-anchored" in line for line in violations), violations


# --- a page overrun fails fast ------------------------------------------------------------------------


def _short_page(layout: dict, height: int) -> dict:
    shorter = copy.deepcopy(layout)
    shorter["page_dimensions"]["height"] = height
    return shorter


def test_a_body_that_runs_past_the_bottom_margin_fails_fast() -> None:
    layout_id, layout = next(iter(layouts_for(DOC).items()))
    entry = {**next(iter(corpus_entries(DOC).values())), "layout": layout_id}
    with pytest.raises(FitError) as caught:
        render_invoice(entry, _short_page(layout, 900), distractors=False)
    message = str(caught.value)
    assert_diagnostic_error(message)
    assert layout_id in message and "page_dimensions.height" in message
    assert "config/layouts/invoices.yml" in message


def test_a_body_that_ends_at_the_bottom_margin_renders() -> None:
    """The bound is the bottom margin itself: a body ending exactly there is not refused."""
    layout_id, layout = next(iter(layouts_for(DOC).items()))
    entry = {**next(iter(corpus_entries(DOC).values())), "layout": layout_id}
    with pytest.raises(FitError) as caught:
        render_invoice(entry, _short_page(layout, 900), distractors=False)
    end = int(re.search(r"ends at y=(\d+)", str(caught.value)).group(1))  # type: ignore[union-attr]
    render_invoice(entry, _short_page(layout, end + int(layout["margin"])), distractors=False)


def test_validate_reports_a_page_overrun_as_an_overflow() -> None:
    layouts = {layout_id: _short_page(layout, 900) for layout_id, layout in layouts_for(DOC).items()}
    gt = dict(list(corpus_entries(DOC).items())[:2])
    violations = check_overflow(gt, layouts, render_invoice)
    assert len(violations) >= 2, violations
    assert all("bottom margin" in line for line in violations), violations


# --- density -----------------------------------------------------------------------------------------


def test_every_distractor_render_meets_the_invoice_bounds() -> None:
    bounds = CONFIG.density[DOC]
    layouts = layouts_for(DOC)
    for case_id, entry in corpus_entries(DOC).items():
        report = measure_render(render_invoice, entry, layouts[entry["layout"]], True)
        assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, report)
        assert report.distractor_share >= bounds.distractor_share_min, (case_id, report)
        assert bounds.page_fill_min is not None and report.page_fill >= bounds.page_fill_min, (
            case_id,
            report,
        )


def test_the_share_floor_proves_distractors_are_present() -> None:
    """No plain invoice reaches the floor, so a render meeting it carries distractor text."""
    floor = CONFIG.density[DOC].distractor_share_min
    assert floor > 0
    layouts = layouts_for(DOC)
    for case_id, entry in corpus_entries(DOC).items():
        report = measure_render(render_invoice, entry, layouts[entry["layout"]], False)
        assert report.distractor_share < floor, (case_id, report)


def _lone_website_centre(layout: dict, monkeypatch: pytest.MonkeyPatch) -> float:
    """The horizontal centre of the only distractor line a page prints when only its website is drawn."""
    monkeypatch.setattr(
        providers, "select_items", lambda config, doc_type, eligible, rng, *, where: ["in_website"]
    )
    entry = next(e for e in corpus_entries(DOC).values() if e["layout"] == "tax_invoice_standard")
    geometry: dict = {}
    render_invoice(entry, layout, distractors=True, geometry_out=geometry)
    boxes = [r.box for r in geometry["ink"] if r.role == "distractor" and not r.mark]
    assert len(boxes) == 1, boxes
    left, _, right, _ = boxes[0]
    return (left + right) / 2


def test_a_website_printed_alone_in_the_footer_is_centred(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minor 3: a lone website sits on the page centre line, under the centred thank-you line."""
    layout = layouts_for(DOC)["tax_invoice_standard"]
    page_centre = layout["page_dimensions"]["width"] / 2
    assert abs(_lone_website_centre(layout, monkeypatch) - page_centre) <= 2
    # The check can fail: the old band centred the website in the left half of a two-column split.
    old = copy.deepcopy(layout)
    footer = next(b for b in old["body"] if b.get("anchor") == "foot")
    band = next(i for i, b in enumerate(footer["children"]) if b.get("type") == "stack")
    lines = [b for b in footer["children"][band]["children"] if b.get("type") == "text"]
    footer["children"][band] = {"type": "split", "widths": [850, 850], "children": [[lines[0]], [lines[1]]]}
    assert abs(_lone_website_centre(old, monkeypatch) - page_centre) > 100


def test_the_invoice_bounds_keep_their_headroom_and_the_median_page_is_dense() -> None:
    """Final review, Important 2: the median sampled page sits near the spec's 0.25 to 0.30 target, and
    each bound keeps at least 10% (relative) headroom over the corpus, so drift is noticed but a corpus
    edit does not fail on noise. The ceiling separates distractor renders from plain ones with room to
    spare, rather than sitting a hair under the lowest plain ratio."""
    import statistics

    bounds = CONFIG.density[DOC]
    reports, plain = [], []
    for entry in corpus_entries(DOC).values():
        layout = layouts_for(DOC)[entry["layout"]]
        reports.append(measure_render(render_invoice, entry, layout, True))
        plain.append(measure_render(render_invoice, entry, layout, False))
    ratios = [r.scored_ink_ratio for r in reports]
    assert 0.22 <= statistics.median(ratios) <= 0.30, statistics.median(ratios)
    assert bounds.scored_ink_ratio_max >= 1.10 * max(ratios), max(ratios)
    assert bounds.distractor_share_min <= 0.90 * min(r.distractor_share for r in reports)
    assert bounds.page_fill_min is not None
    assert bounds.page_fill_min <= 0.90 * min(r.page_fill for r in reports)
    assert bounds.scored_ink_ratio_max <= 0.95 * min(p.scored_ink_ratio for p in plain)
