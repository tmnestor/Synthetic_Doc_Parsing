"""The feasibility gate: the three gate layouts' first cases, typeset plain and with distractors.

`run_gate` typesets six pages, reads each back, checks stage one's six invariants and the layout, compiles
each twice for the byte check and once more with ligatures on, and has the pinned evaluator score each
page's ground truth written as its own prediction. Text and table must score perfectly; reading order up
to stage one's recorded artefact (the first matched table drops out of the predicted order: one edit on a
page with a table). The evaluator takes minutes, so the full run is slow, and skipped where the
evaluator's conda env or checkout is absent.
"""

import json
import re
from pathlib import Path

import pytest

from scripts.typeset_gate import GateError, GateReport, run_gate
from tests.conftest import assert_diagnostic_error
from tests.parsing.test_perfect_score import _checkout_root, _evaluator_env_exists
from scripts.omnidocbench_eval import CHECKOUT_NAME, EVALUATOR_ENV

_ORDERED = ("title", "text_block", "table")
UNDERSCORE_RUN = re.compile(r"_{4,}")


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> GateReport:
    if not _evaluator_env_exists():
        pytest.skip(f"the conda env {EVALUATOR_ENV!r} is absent (build it from envs/omnidocbench.yml)")
    if not (_checkout_root() / CHECKOUT_NAME / ".git").exists():
        pytest.skip(f"the evaluator checkout {_checkout_root() / CHECKOUT_NAME} is absent")
    return run_gate(output_root=tmp_path_factory.mktemp("gate_output"))


def test_a_missing_output_root_is_refused_with_a_diagnostic(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(GateError) as caught:
        run_gate(output_root=missing)
    assert_diagnostic_error(caught.value)
    assert str(missing) in str(caught.value)


@pytest.mark.slow
def test_the_gate_covers_three_layouts_plain_and_with_distractors(report: GateReport) -> None:
    assert len(report.pages) == 6
    assert sorted({page.layout_id for page in report.pages}) == [
        "receipt_thermal_80mm",
        "tax_invoice_standard",
        "westpac_standard",
    ]
    assert sorted(page.mode for page in report.pages) == ["distractor"] * 3 + ["plain"] * 3


@pytest.mark.slow
def test_every_page_passes_every_check(report: GateReport) -> None:
    for page in report.pages:
        assert page.box_query_ok, page
        assert page.invariant_errors == [], page
        assert page.layout_errors == [], page
        assert page.bytes_identical, page
        assert page.page_count == 1, page
        assert page.passed, page
    assert report.passed


@pytest.mark.slow
def test_overlays_are_reported_as_not_yet_placed(report: GateReport) -> None:
    for page in report.pages:
        assert page.overlays_placed is False, page
        assert page.overlay_sites_in_stage_one >= 0
    assert all(page.overlay_sites_in_stage_one == 0 for page in report.pages if page.mode == "plain")


@pytest.mark.slow
def test_the_ligature_probe_reports_its_outcome(report: GateReport) -> None:
    for page in report.pages:
        assert isinstance(page.ligature_invariant_1_holds, bool), page
        assert isinstance(page.ligatures_in_text_layer, list), page


@pytest.mark.slow
def test_the_files_are_written_under_typeset_gate(report: GateReport) -> None:
    root = report.directory
    assert root.name == "typeset_gate"
    pages = json.loads((root / "gate.json").read_text())
    assert len(pages) == 6
    for page in report.pages:
        for name in (page.pdf, page.png, page.stage_one_png):
            assert (root / name).is_file(), name


@pytest.mark.slow
def test_the_evaluator_scores_each_pages_ground_truth_perfectly(report: GateReport) -> None:
    by_image = {
        page["page_info"]["image_path"]: page
        for page in json.loads((report.directory / "gate.json").read_text())
    }
    for page in report.pages:
        gt = by_image[page.png]
        tables = [det["order"] for det in gt["layout_dets"] if det["category_type"] == "table"]
        ordered = [det["order"] for det in gt["layout_dets"] if det["category_type"] in _ORDERED]
        scores = page.scores
        assert scores is not None, page
        texts = [det.get("text", "") for det in gt["layout_dets"]]
        if any(UNDERSCORE_RUN.search(text) for text in texts):
            # The evaluator shortens a run of four or more underscores in a prediction (data_preprocess.py,
            # line 23) but not in the ground truth, so a blank-to-fill-in line costs a small, known edit.
            assert 0 < (scores["text_block_edit_dist"] or 0) < 0.02, (page.png, scores)
        else:
            assert scores["text_block_edit_dist"] == 0, (page.png, scores)
        if tables:
            assert scores["table_teds"] == 1, (page.png, scores)
            assert scores["table_edit_dist"] == 0, (page.png, scores)
        expected = (1 if tables else 0) / len(ordered)
        assert scores["reading_order_edit_dist"] == pytest.approx(expected, abs=1e-12), (page.png, scores)
