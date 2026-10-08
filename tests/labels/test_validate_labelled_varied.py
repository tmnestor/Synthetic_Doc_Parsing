"""`validate` checks a third render, labelled varied, of every receipt and invoice (Stage 6, Task 2).

The overflow pass renders each layout carrying label sites through `render_labelled_varied` and hands the
accepted page to the drawn-field guard and the density bounds; a refusal under `redraw` is no error, one
under `fail` is a violation naming the render. Bank layouts are not rendered a third time (Decision 1).
Also here: the totals-region fields come from YAML, and a totals stack that draws nothing records no region.
"""

import copy
import dataclasses
from pathlib import Path

import pytest
from typer.testing import CliRunner

from generators import pipeline
from generators.distractors.config import DensityBounds
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH
from generators.drawn_fields import build_check
from generators.labels import render as render_module
from generators.labels.config import load_label_config
from generators.labels.keys import LABELLED, UNLABELLED, LabelPick
from generators.labels.render import label_sites
from generators.layout_dsl import schema as schema_module
from generators.layout_dsl.field_providers import apply_field_providers
from generators.overflow_check import LABELLED_VARIED_MODE, LabelledVaried, check_overflow
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.labels.test_schema_sites import RECEIPT, _block, _layout, _validate
from tests.renderer_helpers import corpus_entries, layouts_for, renderer_for


def _gt(doc_type: str, count: int) -> dict:
    return {cid: entry for cid, entry in list(corpus_entries(doc_type).items())[:count]}


def _mode(doc_type: str, *measures) -> LabelledVaried:
    return LabelledVaried(doc_type=doc_type, config=load_label_config(), measures=tuple(measures))


def test_a_receipt_is_rendered_a_third_time_and_measured() -> None:
    seen: list[tuple[str, str, dict]] = []
    gt = _gt("receipt", 4)
    violations = check_overflow(
        gt,
        layouts_for("receipt"),
        renderer_for("receipt"),
        labelled_varied=_mode("receipt", lambda c, lay, g: seen.append((c, lay, g))),
    )
    assert violations == []
    assert [case for case, _, _ in seen] == list(gt)
    assert all(set(g["labels"]) and "totals" in g["regions"] for _, _, g in seen)


def test_a_bank_statement_is_not_rendered_a_third_time() -> None:
    seen: list[str] = []
    check_overflow(
        _gt("bank_statement", 2),
        layouts_for("bank_statement"),
        renderer_for("bank_statement"),
        labelled_varied=_mode("bank_statement", lambda c, lay, g: seen.append(c)),
    )
    assert seen == []


def _receipt_with_you_saved() -> dict:
    for case_id, entry in corpus_entries("receipt").items():
        layout = layouts_for("receipt")[entry["layout"]]
        if "DX_YOU_SAVED" in apply_field_providers(layout, entry, distractors=True)["fields"]:
            return {case_id: entry}
    raise AssertionError("no receipt prints 'You saved'")


def _force_unlabelled_total(monkeypatch: pytest.MonkeyPatch) -> None:
    def orders(case_id: str, doc_type: str, sites: dict, config: object) -> dict:
        found = {field: [LabelPick(LABELLED)] for field in sites}
        found["TOTAL_AMOUNT"] = [LabelPick(UNLABELLED), LabelPick(LABELLED)]
        return found

    monkeypatch.setattr(render_module, "draw_label_orders", orders)


def test_a_refusal_under_redraw_is_no_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_unlabelled_total(monkeypatch)
    seen: list[str] = []
    violations = check_overflow(
        _receipt_with_you_saved(),
        layouts_for("receipt"),
        renderer_for("receipt"),
        labelled_varied=_mode("receipt", lambda c, lay, g: seen.append(g["labels"]["TOTAL_AMOUNT"][0])),
    )
    assert violations == [] and seen == ["TOTAL"]


def test_a_refusal_under_fail_is_a_violation_naming_the_render(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_unlabelled_total(monkeypatch)
    mode = LabelledVaried(
        doc_type="receipt",
        config=dataclasses.replace(load_label_config(), refusal_policy="fail"),
        measures=(),
    )
    violations = check_overflow(
        _receipt_with_you_saved(), layouts_for("receipt"), renderer_for("receipt"), labelled_varied=mode
    )
    assert len(violations) == 1
    assert f"({LABELLED_VARIED_MODE}): label refused" in violations[0]
    assert "total_not_last" in violations[0]
    assert_diagnostic_error(violations[0])


def test_density_and_the_guard_name_the_labelled_varied_render() -> None:
    entry_id, entry = next(iter(corpus_entries("receipt").items()))
    geometry: dict = {}
    renderer_for("receipt")(
        entry, layouts_for("receipt")[entry["layout"]], distractors=True, geometry_out=geometry
    )
    tight = DensityCheck(
        DensityBounds(scored_ink_ratio_max=0.0, distractor_share_min=0.0, page_fill_min=None),
        doc_type="receipt",
        config_path=DISTRACTORS_PATH,
        render=LABELLED_VARIED_MODE,
    )
    tight(entry_id, entry["layout"], geometry)
    assert tight.errors and f"({LABELLED_VARIED_MODE})" in tight.errors[0]
    guard = build_check(
        "receipts",
        REPO_ROOT / "ground_truth" / "receipts.yml",
        REPO_ROOT / "config" / "layouts" / "receipts.yml",
        render=LABELLED_VARIED_MODE,
    )
    del geometry["texts"]["TOTAL_AMOUNT"]
    guard(entry_id, entry["layout"], geometry)
    assert (
        len(guard.errors) == 1 and f"the {LABELLED_VARIED_MODE} render recorded no box" in guard.errors[0]
    )
    assert_diagnostic_error(guard.errors[0])


@pytest.fixture
def at_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)


def test_validate_hands_each_type_its_labelled_varied_mode(
    at_root: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    modes: dict[str, LabelledVaried | None] = {}

    def spy(gt: dict, layouts: dict, renderer: object, **kwargs: object) -> list[str]:
        mode = kwargs["labelled_varied"]
        assert isinstance(mode, LabelledVaried)
        modes[mode.doc_type] = mode
        return []

    monkeypatch.setattr(pipeline, "check_overflow", spy)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert set(modes) == {"receipt", "invoice", "bank_statement"}
    for mode in modes.values():
        assert mode is not None and len(mode.measures) == 2
        assert {type(m).__name__ for m in mode.measures} == {"DensityCheck", "DrawnFieldsCheck"}
        assert all(m.render == LABELLED_VARIED_MODE for m in mode.measures)  # type: ignore[attr-defined]


# --- the totals region ----------------------------------------------------------------------------


def test_the_totals_region_fields_come_from_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GST site outside the totals region is refused only while YAML lists GST_AMOUNT there."""
    layout = _layout("receipt", RECEIPT)
    stack = _block(layout, type="stack", region="totals")
    gst = _block(layout, field="GST_AMOUNT")
    stack["children"].remove(gst)
    layout["body"][0]["children"].insert(0, gst)
    narrowed = dataclasses.replace(load_label_config(), totals_region_fields=("TOTAL_AMOUNT",))
    monkeypatch.setattr(schema_module, "load_label_config", lambda: narrowed)
    _validate("receipt", RECEIPT, layout)


def test_a_totals_stack_that_draws_nothing_records_no_region() -> None:
    entry = next(iter(corpus_entries("receipt").values()))
    layout = copy.deepcopy(layouts_for("receipt")[entry["layout"]])
    stack = _block(layout, type="stack", region="totals")
    # Gate every child on a key no entry carries, so the stack draws nothing at all.
    for child in stack["children"]:
        child["when"] = "ZZ_NEVER_PRESENT"
    geometry: dict = {}
    renderer_for("receipt")(entry, layout, distractors=True, geometry_out=geometry)
    assert geometry["regions"] == {}
    # The shipped layout draws its totals, so it records the region.
    shipped: dict = {}
    renderer_for("receipt")(
        entry, layouts_for("receipt")[entry["layout"]], distractors=True, geometry_out=shipped
    )
    region = shipped["regions"]["totals"]
    assert region[3] > region[1]


def test_label_sites_reads_the_provider_entry() -> None:
    layout = layouts_for("invoice")["tax_invoice_gst_inclusive"]
    assert label_sites(layout)["GST_AMOUNT"]["labelled"] == "GST included (10%):"
    assert label_sites(layouts_for("bank_statement")[next(iter(layouts_for("bank_statement")))]) == {}
    assert Path(DISTRACTORS_PATH).exists()
