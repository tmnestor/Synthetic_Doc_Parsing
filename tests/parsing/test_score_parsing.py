"""Scoring: every collected run is scored with the pinned evaluator, and the leaderboard is written exactly.

`run_end2end` is replaced by a stub that returns fixed headline scores and writes the per-attribute
breakdown the evaluator leaves in its metric result, so the leaderboard's rows are checked to the digit
without running the evaluator. The page attribute values are invented.
"""

import csv
import json
import math
from datetime import date
from pathlib import Path

import pytest

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from scripts import score_parsing
from scripts.omnidocbench_eval import result_path
from scripts.run_local_parser import RUN_RECORD
from scripts.score_parsing import OVERALL, ScoreError, score
from tests.conftest import assert_diagnostic_error
from tests.eval_export_tools import scratch_config
from tests.parsing.parser_runs import SNAPSHOT_CONFIG

TODAY = date(2099, 1, 2)
NAN = math.nan

# (tool, half) -> text edit, TEDS, TEDS structure-only, table edit, reading-order edit.
HEADLINES = {
    ("docling", "plain"): (0.25, 0.5, 0.625, 0.75, 0.125),
    ("docling", "degraded"): (0.4375, NAN, NAN, NAN, 0.3125),
    ("marker", "plain"): (0.0625, 0.875, 0.9375, 0.1875, 0.0),
}
RUNS = {
    ("docling", "plain"): ("2.0.1", "mps", 4, 10.0),
    ("docling", "degraded"): ("2.0.1", "mps", 2, 3.0),
    ("marker", "plain"): ("1.2.3", "mps", 4, 6.0),
}
_NAMES = (
    "text_block_edit_dist",
    "table_teds",
    "table_teds_structure_only",
    "table_edit_dist",
    "reading_order_edit_dist",
)


def _breakdown(tool: str, half: str) -> dict:
    """An invented per-attribute breakdown: each value is the headline plus a small per-group offset."""
    text, teds, teds_s, table_edit, order = HEADLINES[(tool, half)]
    tier = "light" if half == "degraded" else "none"
    groups = {"doc_type: invoice": 0.0, "doc_type: receipt": 0.0625, f"tier: {tier}": 0.0}
    groups |= {"layout_id: invoice_layout_1": 0.0, "half: x": 0.0, "language: en": 0.0, "ALL": 0.0}

    def page(value: float) -> dict:
        return {key: value + offset for key, offset in groups.items()}

    return {
        "text_block": {"page": {"Edit_dist": page(text)}},
        "table": {
            "page": {"Edit_dist": page(table_edit), "TEDS": page(teds), "TEDS_structure_only": page(teds_s)}
        },
        "reading_order": {"page": {"Edit_dist": page(order)}},
    }


@pytest.fixture
def scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[tuple]]:
    """A snapshot, collected predictions and run records for three runs, scored with the stub."""
    snapshot = tmp_path / "snapshot_20990101"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    for half in ("plain", "degraded"):
        (snapshot / SNAPSHOT_CONFIG.parsing_dir / f"{SNAPSHOT_CONFIG.half_prefixes[half]}.json").write_text(
            "[]"
        )
    root = tmp_path / "out"
    for (tool, half), (version, device, pages, seconds) in RUNS.items():
        (root / "predictions" / tool / half).mkdir(parents=True)
        (root / "predictions" / tool / half / "CASE901_invoice.md").write_text("# Invented\n")
        raw = root / "raw_predictions" / tool / half
        raw.mkdir(parents=True)
        record: dict[str, object] = {"tool": tool, "backend": "default", "half": half}
        record |= {"tool_version": version, "device": device, "page_count": pages, "seconds": seconds}
        (raw / RUN_RECORD).write_text(json.dumps(record))
    # A tool the config does not list is not scored, even with predictions.
    (root / "predictions" / "unlisted_tool" / "plain").mkdir(parents=True)

    stub_calls: list[tuple] = []

    def stub(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
        tool, half = pred_dir.parent.name, pred_dir.name
        stub_calls.append((gt_json, pred_dir, checkout, work_dir))
        source = result_path(pred_dir, work_dir=work_dir)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(_breakdown(tool, half)))
        return dict(zip(_NAMES, HEADLINES[(tool, half)], strict=True))

    monkeypatch.setattr(score_parsing, "run_end2end", stub)
    markdown, table = score(
        snapshot,
        output_root=root,
        config=load_parsing_config(PARSING_CONFIG_PATH),
        snapshot_config=SNAPSHOT_CONFIG,
        checkout=tmp_path / "checkout",
        today=TODAY,
    )
    return markdown, table, stub_calls


def test_each_collected_run_is_scored_against_its_half(tmp_path: Path, scored: tuple) -> None:
    markdown, table, stub_calls = scored
    assert markdown == tmp_path / "out" / "leaderboard_20990102.md"
    assert table == tmp_path / "out" / "leaderboard_20990102.csv"
    parsing = tmp_path / "snapshot_20990101" / SNAPSHOT_CONFIG.parsing_dir
    assert [(gt, pred, work) for gt, pred, _, work in stub_calls] == [
        (
            parsing / "plain.json",
            tmp_path / "out/predictions/docling/plain",
            tmp_path / "out/scores/docling/plain",
        ),
        (
            parsing / "degraded.json",
            tmp_path / "out/predictions/docling/degraded",
            tmp_path / "out/scores/docling/degraded",
        ),
        (
            parsing / "plain.json",
            tmp_path / "out/predictions/marker/plain",
            tmp_path / "out/scores/marker/plain",
        ),
    ]
    assert {checkout for _, _, checkout, _ in stub_calls} == {tmp_path / "checkout"}


def _section(text: str, heading: str) -> list[str]:
    """The table rows (lines starting with '|') under `heading`, up to the next heading."""
    lines = text.splitlines()
    start = lines.index(heading) + 1
    rows = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            rows.append(line)
    return rows


def test_the_headline_tables_hold_the_exact_scores(scored: tuple) -> None:
    text = scored[0].read_text()
    header = "| Tool | plain | distractor | labelled_varied | degraded |"
    rule = "|---|---|---|---|---|"
    assert _section(text, "### Text edit distance") == [
        header,
        rule,
        "| docling | 0.250 |  |  | 0.438 |",
        "| marker | 0.062 |  |  |  |",
    ]
    assert _section(text, "### Table TEDS") == [
        header,
        rule,
        "| docling | 0.500 |  |  | n/a |",
        "| marker | 0.875 |  |  |  |",
    ]
    assert _section(text, "### Table TEDS, structure only")[2:] == [
        "| docling | 0.625 |  |  | n/a |",
        "| marker | 0.938 |  |  |  |",
    ]
    assert _section(text, "### Table edit distance")[2:] == [
        "| docling | 0.750 |  |  | n/a |",
        "| marker | 0.188 |  |  |  |",
    ]
    assert _section(text, "### Reading-order edit distance")[2:] == [
        "| docling | 0.125 |  |  | 0.312 |",
        "| marker | 0.000 |  |  |  |",
    ]
    assert _section(text, "## Overall")[2:] == [
        f"| docling | {OVERALL} |  |  | {OVERALL} |",
        f"| marker | {OVERALL} |  |  |  |",
    ]
    assert OVERALL == "n/a (no formulas in stage one)"


def test_tables_lead_with_teds_and_carry_the_output_form_caveat(scored: tuple) -> None:
    text = scored[0].read_text()
    assert text.index("### Table TEDS\n") < text.index("### Table edit distance")
    caveat = text[text.index("### Table edit distance") : text.index("### Reading-order edit distance")]
    assert "html strings" in caveat and "same output form" in caveat


def test_the_runs_table_records_each_run_device(scored: tuple) -> None:
    assert _section(scored[0].read_text(), "## Runs") == [
        "| Tool | Half | Backend | Version | Device | Pages | Seconds per page |",
        "|---|---|---|---|---|---|---|",
        "| docling | plain | default | 2.0.1 | mps | 4 | 2.5 |",
        "| docling | degraded | default | 2.0.1 | mps | 2 | 1.5 |",
        "| marker | plain | default | 1.2.3 | mps | 4 | 1.5 |",
    ]


def test_the_breakdowns_slice_by_document_type_tier_and_layout_key(scored: tuple) -> None:
    text = scored[0].read_text()
    by_type = text[text.index("## By document type") : text.index("## By degradation tier")]
    assert _section(by_type, "### Text edit distance") == [
        "| Tool | Document type | plain | distractor | labelled_varied | degraded |",
        "|---|---|---|---|---|---|",
        "| docling | invoice | 0.250 |  |  | 0.438 |",
        "| docling | receipt | 0.312 |  |  | 0.500 |",
        "| marker | invoice | 0.062 |  |  |  |",
        "| marker | receipt | 0.125 |  |  |  |",
    ]
    by_tier = text[text.index("## By degradation tier") : text.index("## By layout")]
    assert _section(by_tier, "### Reading-order edit distance")[2:] == [
        "| docling | light |  |  |  | 0.312 |",
        "| docling | none | 0.125 |  |  |  |",
        "| marker | none | 0.000 |  |  |  |",
    ]
    by_layout = text[text.index("## By layout") :]
    assert _section(by_layout, "### Table TEDS")[:3] == [
        "| Tool | Layout key | plain | distractor | labelled_varied | degraded |",
        "|---|---|---|---|---|---|",
        "| docling | invoice_layout_1 | 0.500 |  |  | n/a |",
    ]


def test_the_csv_holds_the_same_rows(scored: tuple) -> None:
    rows = list(csv.DictReader(scored[1].open()))
    assert list(rows[0]) == [
        "section",
        "metric",
        "tool",
        "group",
        "plain",
        "distractor",
        "labelled_varied",
        "degraded",
    ]
    by_key = {(row["section"], row["metric"], row["tool"], row["group"]): row for row in rows}
    text = by_key[("all", "text_block_edit_dist", "docling", "")]
    assert (text["plain"], text["distractor"], text["degraded"]) == ("0.25", "", "0.4375")
    assert by_key[("all", "table_teds", "docling", "")]["degraded"] == "n/a"
    assert by_key[("overall", "overall", "marker", "")]["plain"] == OVERALL
    assert by_key[("run", "device", "docling", "")]["degraded"] == "mps"
    assert by_key[("doc_type", "text_block_edit_dist", "marker", "receipt")]["plain"] == "0.125"
    assert by_key[("layout_id", "table_teds", "marker", "invoice_layout_1")]["plain"] == "0.875"
    assert by_key[("tier", "reading_order_edit_dist", "docling", "light")]["degraded"] == "0.3125"
    assert {row["tool"] for row in rows} == {"docling", "marker"}


def test_nothing_to_score_is_refused(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot_20990101"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    with pytest.raises(ScoreError) as caught:
        score(
            snapshot,
            output_root=tmp_path,
            config=load_parsing_config(PARSING_CONFIG_PATH),
            snapshot_config=SNAPSHOT_CONFIG,
            checkout=tmp_path,
            today=TODAY,
        )
    assert_diagnostic_error(caught.value)
    assert str(tmp_path / "predictions") in str(caught.value)


def test_a_run_without_its_record_is_refused(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot_20990101"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    (snapshot / SNAPSHOT_CONFIG.parsing_dir / "plain.json").write_text("[]")
    (tmp_path / "predictions" / "marker" / "plain").mkdir(parents=True)
    with pytest.raises(ScoreError) as caught:
        score(
            snapshot,
            output_root=tmp_path,
            config=load_parsing_config(PARSING_CONFIG_PATH),
            snapshot_config=SNAPSHOT_CONFIG,
            checkout=tmp_path,
            today=TODAY,
        )
    assert_diagnostic_error(caught.value)
    assert str(tmp_path / "raw_predictions" / "marker" / "plain" / RUN_RECORD) in str(caught.value)


def test_a_half_with_predictions_but_no_ground_truth_is_refused(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot_20990101"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    (tmp_path / "predictions" / "marker" / "degraded").mkdir(parents=True)
    raw = tmp_path / "raw_predictions" / "marker" / "degraded"
    raw.mkdir(parents=True)
    record = {
        "backend": "default",
        "tool_version": "1.2.3",
        "device": "mps",
        "page_count": 1,
        "seconds": 1.0,
    }
    (raw / RUN_RECORD).write_text(json.dumps(record))
    with pytest.raises(ScoreError) as caught:
        score(
            snapshot,
            output_root=tmp_path,
            config=load_parsing_config(PARSING_CONFIG_PATH),
            snapshot_config=SNAPSHOT_CONFIG,
            checkout=tmp_path,
            today=TODAY,
        )
    assert_diagnostic_error(caught.value)
    assert "degraded.json" in str(caught.value)


def _one_run(root: Path) -> Path:
    """A snapshot and one collected docling run on the plain half under `root`; return the snapshot."""
    snapshot = root / "snapshot_20990101"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    (snapshot / SNAPSHOT_CONFIG.parsing_dir / "plain.json").write_text("[]")
    (root / "predictions" / "docling" / "plain").mkdir(parents=True)
    raw = root / "raw_predictions" / "docling" / "plain"
    raw.mkdir(parents=True)
    record = {
        "backend": "default",
        "tool_version": "1.2.3",
        "device": "mps",
        "page_count": 1,
        "seconds": 1.0,
    }
    (raw / RUN_RECORD).write_text(json.dumps(record))
    return snapshot


def _stub(drop_headline: str | None = None, drop_page: tuple[str, str] | None = None):
    """A run_end2end stand-in whose headline or breakdown lacks one metric."""

    def stub(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
        result = _breakdown("docling", "plain")
        if drop_page is not None:
            del result[drop_page[0]]["page"][drop_page[1]]
        source = result_path(pred_dir, work_dir=work_dir)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(result))
        scores = dict(zip(_NAMES, HEADLINES[("docling", "plain")], strict=True))
        if drop_headline is not None:
            del scores[drop_headline]
        return scores

    return stub


@pytest.mark.parametrize(
    ("drop_headline", "drop_page", "dotted", "metric"),
    [
        ("table_teds", None, "table.all.TEDS", "table_teds"),
        (None, ("reading_order", "Edit_dist"), "reading_order.page.Edit_dist", "reading_order_edit_dist"),
    ],
)
def test_a_metric_missing_from_the_result_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drop_headline: str | None,
    drop_page: tuple[str, str] | None,
    dotted: str,
    metric: str,
) -> None:
    snapshot = _one_run(tmp_path)
    monkeypatch.setattr(score_parsing, "run_end2end", _stub(drop_headline, drop_page))
    with pytest.raises(ScoreError) as caught:
        score(
            snapshot,
            output_root=tmp_path,
            config=load_parsing_config(PARSING_CONFIG_PATH),
            snapshot_config=SNAPSHOT_CONFIG,
            checkout=tmp_path,
            today=TODAY,
        )
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    source = result_path(
        tmp_path / "predictions" / "docling" / "plain", work_dir=tmp_path / "scores" / "docling" / "plain"
    )
    assert f"{source} -> {dotted}" in message
    assert metric in message
    assert not list(tmp_path.glob("leaderboard_*"))


def test_the_command_line_reports_a_missing_metric_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = scratch_config(tmp_path)
    snapshot = _one_run(tmp_path / "out")
    monkeypatch.setattr(score_parsing, "ensure_checkout", lambda config, *, root: tmp_path / "checkout")
    monkeypatch.setattr(score_parsing, "run_end2end", _stub(drop_page=("table", "TEDS")))
    assert score_parsing.main(["--snapshot", str(snapshot), "--config", str(config)]) == 1
    error = capsys.readouterr().err
    assert_diagnostic_error(error)
    assert "table.page.TEDS" in error
