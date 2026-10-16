"""Every receipt layout draws its selected distractors legibly and scannably; plain renders stay pinned.

Covers Stage 3's review focus: a selected item whose block never draws; a barcode or QR code that does
not decode at the receipt's real size; a distractor value too long for the narrowest (57 mm) page; and
a plain render changed by an edit to the shared body. The distractor renders themselves are pinned in
`tests/fixtures/distractor_pixel_snapshot.json`.
"""

import subprocess
from collections import Counter
import sys

import pytest
from typer.testing import CliRunner

from generators.distractors.config import load_distractor_config
from generators.distractors.density import measure_render
from generators.pipeline import app
from tests.conftest import REPO_ROOT
from tests.distractor_helpers import budgets as _budgets
from tests.distractor_helpers import decoded as _decoded
from tests.distractor_helpers import selected as _selected
from tests.renderer_helpers import corpus_entries, layouts_for, renderer_for
from tests.snapshot_tools import (
    DISTRACTOR_DOC_TYPES,
    DISTRACTOR_SNAPSHOT_PATH,
    EXPECTED_PER_TYPE,
    compare,
    digest,
    load_snapshot,
    render_corpus,
)

RECEIPT_ITEMS = sorted(
    i for i, item in load_distractor_config().items.items() if "receipt" in item.doc_types
)


# --- the shipped layouts ----------------------------------------------------------------------------


RETURNS_ITEMS = {"rc_returns_by", "rc_returns_policy"}


def test_every_receipt_layout_lists_all_fourteen_items_and_the_provider() -> None:
    """All fourteen, but the cafe's hospitality layout: a cafe takes no returns."""
    for layout_id, layout in layouts_for("receipt").items():
        expected = (
            sorted(set(RECEIPT_ITEMS) - RETURNS_ITEMS)
            if layout_id == "receipt_hospitality"
            else RECEIPT_ITEMS
        )
        assert sorted(layout["distractor_items"]) == expected, layout_id
        assert {"name": "receipt_distractors", "params": {}} in layout["field_providers"], layout_id


def _forced_drawn(entry: dict, layout: dict, drawn: set[str]) -> set[str]:
    drawn.clear()
    renderer_for("receipt")(entry, layout, distractors=True)
    return set(drawn)


def test_a_cafe_receipt_never_prints_returns_wording(drawn_keys: set[str], every_item_forced: None) -> None:
    """Every hospitality case, every item forced on: no returns-by date and no returns policy is drawn."""
    layouts = layouts_for("receipt")
    assert layouts["receipt_hospitality"]["merchant_categories"] == ["cafe"]
    entries = corpus_entries("receipt")
    cafes = {c: e for c, e in entries.items() if e["layout"] == "receipt_hospitality"}
    assert cafes
    for case_id, entry in cafes.items():
        keys = _forced_drawn(entry, layouts["receipt_hospitality"], drawn_keys)
        assert "DX_SURVEY_CODE" in keys, (case_id, keys)
        assert not keys & {"DX_RETURNS_BY", "DX_RETURNS_POLICY"}, (case_id, keys)
    # The check can fail: the fuel layout (which also serves automotive parts) prints the returns wording.
    entry = next(e for e in entries.values() if e["layout"] == "receipt_fuel")
    assert {"DX_RETURNS_BY", "DX_RETURNS_POLICY"} <= _forced_drawn(
        entry, layouts["receipt_fuel"], drawn_keys
    )


def test_no_distractor_text_uses_a_scored_fields_budget() -> None:
    """A DX-gated block fits through a distractor budget, never one a scored block uses."""
    for layout_id, layout in layouts_for("receipt").items():
        scored, distractor = _budgets(layout["body"])
        assert distractor, layout_id
        assert not distractor & scored, (layout_id, distractor & scored)


def test_the_budget_walk_reaches_nested_split_and_panel_children() -> None:
    nested_split = {
        "type": "split",
        "children": [[{"type": "text", "content": "{DX_PHONE}", "budget": "SUPPLIER_NAME"}], []],
    }
    gated_panel = {
        "type": "panel",
        "when": "DX_ACN",
        "children": [{"type": "text", "content": "ACN", "budget": "ABN_LINE"}],
    }
    body = [
        {"type": "text", "content": "{SUPPLIER_NAME}", "budget": "SUPPLIER_NAME"},
        {"type": "text", "content": "{BUSINESS_ABN}", "budget": "ABN_LINE"},
        {"type": "panel", "children": [nested_split]},
        gated_panel,
    ]
    scored, distractor = _budgets(body)
    assert distractor & scored == {"SUPPLIER_NAME", "ABN_LINE"}


# --- end to end: every case on its own layout ---------------------------------------------------------


def _without_store_line(layout: dict) -> dict:
    """The layout with its Store line removed, the logo gated on the same key kept.

    The body is three stacks; the Store line sits in the first.
    """
    main, *rest = layout["body"]
    children = [block for block in main["children"] if block.get("content") != "{DX_STORE_NUMBER}"]
    assert len(children) == len(main["children"]) - 1
    return {**layout, "body": [{**main, "children": children}, *rest]}


def test_a_missing_store_line_is_caught_although_the_logo_draws(
    drawn_keys: set[str], every_item_forced: None
) -> None:
    """The logo is gated on DX_STORE_NUMBER but never prints it, so it must not count as drawing it."""
    layout_id, layout = next(iter(layouts_for("receipt").items()))
    entry = {**next(iter(corpus_entries("receipt").values())), "layout": layout_id}
    broken = _without_store_line(layout)
    assert "DX_STORE_NUMBER" in _selected(entry, broken)
    renderer_for("receipt")(entry, broken, distractors=True)
    assert "DX_STORE_NUMBER" not in drawn_keys
    renderer_for("receipt")(entry, layout, distractors=True)
    assert "DX_STORE_NUMBER" in drawn_keys


def test_every_selected_item_draws_and_every_mark_decodes(drawn_keys: set[str]) -> None:
    plain_pins = load_snapshot()
    layouts = layouts_for("receipt")
    for case_id, entry in corpus_entries("receipt").items():
        layout = layouts[entry["layout"]]
        selected = _selected(entry, layout)
        low, _ = load_distractor_config().selection["receipt"]
        assert len(selected) >= low, (case_id, selected)

        drawn_keys.clear()
        geometry: dict = {}
        image = renderer_for("receipt")(entry, layout, distractors=True, geometry_out=geometry)
        assert set(selected) <= drawn_keys, (case_id, sorted(set(selected) - drawn_keys))
        assert any(r.role == "distractor" for r in geometry["ink"]), case_id

        expected = sorted(selected[k] for k in ("DX_TRANSACTION_ID", "DX_SURVEY_URL") if k in selected)
        assert _decoded(image) == expected, case_id

        plain = renderer_for("receipt")(entry, layout, distractors=False)
        assert digest(plain) == plain_pins[f"{case_id}_{entry['layout']}"]["hash"], case_id


# --- forced: every item, every case, every receipt layout ---------------------------------------------


@pytest.mark.slow
def test_every_item_on_every_case_fits_every_receipt_layout(every_item_forced: None) -> None:
    """Review focus 1: the 57 mm page included, nothing overflows, clips or fails to decode."""
    for layout_id, layout in layouts_for("receipt").items():
        for case_id, entry in corpus_entries("receipt").items():
            moved = {**entry, "layout": layout_id}
            selected = _selected(moved, layout)
            assert set(selected) >= {"DX_TRANSACTION_ID", "DX_SURVEY_URL"}, (case_id, layout_id)
            image = renderer_for("receipt")(moved, layout, distractors=True)
            assert image.height < int(layout["canvas_ceiling"]), (case_id, layout_id)
            assert _decoded(image) == sorted([selected["DX_TRANSACTION_ID"], selected["DX_SURVEY_URL"]]), (
                case_id,
                layout_id,
            )


@pytest.mark.slow
@pytest.mark.usefixtures("stub_typeset_pass")
def test_validate_passes_with_every_item_forced(
    every_item_forced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output


# --- density -----------------------------------------------------------------------------------------


def test_every_distractor_render_meets_the_receipt_bounds() -> None:
    bounds = load_distractor_config().density["receipt"]
    layouts = layouts_for("receipt")
    for case_id, entry in corpus_entries("receipt").items():
        report = measure_render(renderer_for("receipt"), entry, layouts[entry["layout"]], True)
        assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, report)
        assert report.distractor_share >= bounds.distractor_share_min, (case_id, report)
        assert report.distractor_area > 0 and report.mark_area >= 0, (case_id, report)


def test_receipt_fill_is_unbounded_because_the_page_is_cropped_to_content() -> None:
    assert load_distractor_config().density["receipt"].page_fill_min is None


def test_the_share_floor_proves_distractors_are_present() -> None:
    """No plain receipt reaches the floor, so a render meeting it carries distractor text."""
    floor = load_distractor_config().density["receipt"].distractor_share_min
    assert floor > 0
    layouts = layouts_for("receipt")
    for case_id, entry in corpus_entries("receipt").items():
        report = measure_render(renderer_for("receipt"), entry, layouts[entry["layout"]], False)
        assert report.distractor_share < floor, (case_id, report)


# --- the distractor snapshot -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def distractor_corpus() -> dict[str, dict]:
    return render_corpus(DISTRACTOR_DOC_TYPES, distractors=True)


def test_the_distractor_snapshot_pins_every_document_type() -> None:
    """Every type that draws distractors is pinned, 55 pages each (Stage 4 added the invoices, Stage 5 the
    bank statements)."""
    assert set(DISTRACTOR_DOC_TYPES) == {"receipt", "invoice", "bank_statement"}
    pinned = load_snapshot(DISTRACTOR_SNAPSHOT_PATH)
    per_type = Counter(record["type"] for record in pinned.values())
    assert per_type == dict.fromkeys(DISTRACTOR_DOC_TYPES, EXPECTED_PER_TYPE)


def test_every_distractor_render_matches_its_snapshot(distractor_corpus: dict[str, dict]) -> None:
    problems = compare(load_snapshot(DISTRACTOR_SNAPSHOT_PATH), distractor_corpus)
    assert not problems, "\n".join(problems)


def test_distractor_renders_are_deterministic(distractor_corpus: dict[str, dict]) -> None:
    assert not compare(distractor_corpus, render_corpus(DISTRACTOR_DOC_TYPES, distractors=True))


def test_the_distractor_renders_differ_from_the_plain_ones(distractor_corpus: dict[str, dict]) -> None:
    plain = load_snapshot()
    for key, record in distractor_corpus.items():
        assert record["hash"] != plain[key]["hash"], key


def test_regenerate_distractor_snapshot_without_confirm_writes_nothing() -> None:
    before = DISTRACTOR_SNAPSHOT_PATH.stat().st_mtime_ns
    result = subprocess.run(
        [sys.executable, "tests/regenerate_distractor_pixel_snapshot.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout.lower()
    assert DISTRACTOR_SNAPSHOT_PATH.stat().st_mtime_ns == before


def test_regenerate_distractor_snapshot_refuses_an_unpinned_pillow() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "tests/regenerate_distractor_pixel_snapshot.py",
            "--confirm",
            "--pretend-pillow",
            "0.0.0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "pillow" in (result.stdout + result.stderr).lower()
