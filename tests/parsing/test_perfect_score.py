"""The perfect-score check (the design's section 5.2): ground truth, written as markdown, scores perfectly.

The probe (six clean pages and three degraded ones, whose polys lie on the photographed frame) is exported
into `tmp_path`; each page is written by `gt_to_markdown` as `<image stem>.md`; and
the pinned evaluator scores those predictions against `probe.json`. Text edit distance 0, table TEDS 1 and
table edit distance 0 prove the export and the evaluator agree on the format, and that the evaluator pairs
`<stem>.png` ground truth with `<stem>.md` predictions (Decision 13), which no invariant can.

Reading order is asserted exactly up to one known evaluator artefact, recorded in section 5.3 of the
design: at the pinned commit, `src/core/matching/match.py` line 714 writes a matched table's predicted
position as `pred_items[pred_idx]['position'][0] if pred_idx else ""` (line 713 drops its category the same
way), so the table matched to prediction index 0, the first table of the page, leaves the predicted order
while its ground truth stays. Per page, the evaluator's predicted order must equal the ground-truth order
with only that table's position missing, and the ground-truth order must be exactly the page's scored
blocks; the aggregate is asserted to be the value that artefact predicts, worked out from the pages.

Slow (the evaluator takes about a minute), and skipped with its reason where the evaluator's conda env or
its checkout is absent, so the suite runs on a machine that has neither.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.degraded import DEGRADED_HALF
from generators.parsing.probe import PROBE_JSON, export_probe
from generators.snapshot.config import load_snapshot_config
from scripts.omnidocbench_eval import (
    CHECKOUT_NAME,
    EVALUATOR_ENV,
    ensure_checkout,
    result_path,
    run_end2end,
)
from tests.conftest import REPO_ROOT
from tests.eval_export_tools import scratch_config
from tests.parsing.gt_to_markdown import gt_to_markdown

pytestmark = pytest.mark.slow

# The categories whose blocks the evaluator puts in reading order for this export: text it scores, and
# tables. Footers (and headers) are matched but left out of reading order, as are textless blocks.
_ORDERED = ("title", "text_block", "table")


def _evaluator_env_exists() -> bool:
    """Whether conda knows an env named after the evaluator."""
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if conda is None:
        return False
    listed = subprocess.run([conda, "env", "list", "--json"], capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        return False
    return any(Path(prefix).name == EVALUATOR_ENV for prefix in json.loads(listed.stdout)["envs"])


def _checkout_root() -> Path:
    """Where the evaluator checkout lives: `<eval_set.output_root>/tools`."""
    return load_snapshot_config(REPO_ROOT / "config" / "generation_config.yml").output_root / "tools"


def test_the_probe_ground_truth_scores_perfectly_against_itself(tmp_path: Path) -> None:
    if not _evaluator_env_exists():
        pytest.skip(f"the conda env {EVALUATOR_ENV!r} is absent (build it from envs/omnidocbench.yml)")
    root = _checkout_root()
    if not (root / CHECKOUT_NAME / ".git").exists():
        pytest.skip(f"the evaluator checkout {root / CHECKOUT_NAME} is absent")
    parsing_config = load_parsing_config(PARSING_CONFIG_PATH)
    checkout = ensure_checkout(parsing_config, root=root)

    probe = export_probe(scratch_config(tmp_path), parsing_config)
    pages = json.loads((probe / PROBE_JSON).read_text())
    # Six clean pages and the first receipt at three degradation tiers, each scored like the rest.
    halves = [page["page_info"]["page_attribute"]["half"] for page in pages]
    assert (len(pages), halves.count(DEGRADED_HALF)) == (9, 3), halves
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    for page in pages:
        stem = Path(page["page_info"]["image_path"]).stem
        (predictions / f"{stem}.md").write_text(gt_to_markdown(page))

    work = tmp_path / "scores"
    scores = run_end2end(probe / PROBE_JSON, predictions, checkout=checkout, work_dir=work)
    print(f"perfect-score check over {len(pages)} pages: {scores}")
    assert scores["text_block_edit_dist"] == 0, scores
    assert scores["table_teds"] == 1, scores
    assert scores["table_teds_structure_only"] == 1, scores
    assert scores["table_edit_dist"] == 0, scores

    reading = result_path(predictions, work_dir=work).with_name(
        f"{predictions.name}_quick_match_reading_order_result.json"
    )
    by_page = {sample["img_id"]: sample for sample in json.loads(reading.read_text())}
    assert sorted(by_page) == sorted(page["page_info"]["image_path"] for page in pages)
    expected_edits = []
    for page in pages:
        sample = by_page[page["page_info"]["image_path"]]
        ordered = sorted(det["order"] for det in page["layout_dets"] if det["category_type"] in _ORDERED)
        tables = [det["order"] for det in page["layout_dets"] if det["category_type"] == "table"]
        dropped = min(tables) if tables else None
        assert sample["gt"] == ordered, sample
        assert sample["pred"] == [order for order in ordered if order != dropped], sample
        expected_edits.append((1 if dropped is not None else 0) / len(ordered))
    expected = sum(expected_edits) / len(expected_edits)
    print(f"reading order: {scores['reading_order_edit_dist']} (the table artefact predicts {expected})")
    assert scores["reading_order_edit_dist"] == pytest.approx(expected, abs=1e-12), scores

    # The corrected run (the matcher fault fixed in memory, checkout untouched) scores the same pages
    # perfectly on every number, reading order included, and moves nothing but reading order.
    fixed = run_end2end(
        probe / PROBE_JSON, predictions, checkout=checkout, work_dir=tmp_path / "corrected", corrected=True
    )
    print(f"corrected run: {fixed}")
    assert fixed["reading_order_edit_dist"] == 0, fixed
    assert {k: v for k, v in fixed.items() if k != "reading_order_edit_dist"} == {
        k: v for k, v in scores.items() if k != "reading_order_edit_dist"
    }
    head = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    )
    assert head.stdout == "", f"the corrected run changed the checkout: {head.stdout}"
