"""Scoring: every collected run is scored with the pinned evaluator, and the leaderboard is written exactly.

`run_end2end` is replaced by a stub that returns fixed headline scores and writes the per-attribute
breakdown the evaluator leaves in its metric result, so the leaderboard's rows are checked to the digit
without running the evaluator. The page attribute values are invented.
"""

import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import pytest

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.snapshot.config import HALVES, TYPESET_SETS
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
        record["snapshot"] = str(snapshot)
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


def _degraded_page(stamp: str, text: str) -> dict:
    """One invented degraded page, its image under the snapshot's dated directory."""
    image = f"degraded_{stamp}/CASE901_receipt_v1.png"
    return {
        "layout_dets": [{"category_type": "text_block", "text": text}],
        "page_info": {"image_path": image},
    }


def _overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, earlier_text: str = "Quokka Bakery"
) -> tuple[Path, list]:
    """Docling's plain run on snapshot_20990202 and degraded run on snapshot_20990101, scored with the
    degraded half overridden to the earlier snapshot. Both degraded files hold one page, under each
    snapshot's own dated image directory; the earlier one's block reads `earlier_text`."""
    later = tmp_path / "snapshot_20990202"
    earlier = tmp_path / "snapshot_20990101"
    for snapshot in (later, earlier):
        (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
        (snapshot / SNAPSHOT_CONFIG.parsing_dir / "plain.json").write_text("[]")
    later_pages = [_degraded_page("20990202", "Quokka Bakery")]
    earlier_pages = [_degraded_page("20990101", earlier_text)]
    (later / SNAPSHOT_CONFIG.parsing_dir / "degraded.json").write_text(json.dumps(later_pages))
    (earlier / SNAPSHOT_CONFIG.parsing_dir / "degraded.json").write_text(json.dumps(earlier_pages))
    root = tmp_path / "out"
    for half, made_on in (("plain", later), ("degraded", earlier)):
        (root / "predictions" / "docling" / half).mkdir(parents=True)
        raw = root / "raw_predictions" / "docling" / half
        raw.mkdir(parents=True)
        record: dict[str, object] = {"tool": "docling", "half": half, "snapshot": str(made_on)}
        record |= {"backend": "default", "tool_version": "1.2.3", "device": "mps"}
        record |= {"page_count": 1, "seconds": 1.0}
        (raw / RUN_RECORD).write_text(json.dumps(record))
    calls: list[Path] = []

    def stub(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
        calls.append(gt_json)
        return _stub()(gt_json, pred_dir, checkout=checkout, work_dir=work_dir)

    monkeypatch.setattr(score_parsing, "run_end2end", stub)
    markdown, _ = score(
        later,
        output_root=root,
        config=load_parsing_config(PARSING_CONFIG_PATH),
        snapshot_config=SNAPSHOT_CONFIG,
        checkout=tmp_path,
        today=TODAY,
        snapshot_for=score_parsing.parse_snapshot_for([f"degraded={earlier}"]),
    )
    return markdown, calls


def test_a_half_overridden_is_scored_against_its_own_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--snapshot-for scores the degraded half on the snapshot its run was made on, and the leaderboard
    names each half's snapshot and finds the two degraded ground truths identical: they differ only in
    the dated image directory each page's image_path names."""
    markdown, calls = _overridden(tmp_path, monkeypatch)
    parsing = SNAPSHOT_CONFIG.parsing_dir
    assert calls == [
        tmp_path / "snapshot_20990202" / parsing / "plain.json",
        tmp_path / "snapshot_20990101" / parsing / "degraded.json",
    ]
    text = markdown.read_text()
    assert (
        "Snapshot `snapshot_20990202`. Each half is scored against: plain `snapshot_20990202`, distractor "
        "`snapshot_20990202`, labelled_varied `snapshot_20990202`, degraded `snapshot_20990101`."
    ) in text
    page = _degraded_page("", "Quokka Bakery")
    page["page_info"]["image_path"] = "CASE901_receipt_v1.png"
    digest = hashlib.sha256(json.dumps([page], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert "its ground truth (`parsing/degraded.json`) is identical in the two snapshots" in text
    assert f"(SHA-256 {digest} and {digest})" in text


def test_an_overridden_half_whose_ground_truth_differs_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A block that reads differently is a real difference, not a dated directory."""
    markdown, _ = _overridden(tmp_path, monkeypatch, earlier_text="Quokka Bakeries")
    assert "is DIFFERENT between the two snapshots" in markdown.read_text()


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ("degraded", "is not <half>=<snapshot dir>"),
        ("scanned={dir}", "'scanned'"),
        ("degraded={missing}", "not a directory"),
    ],
)
def test_an_invalid_snapshot_override_is_refused(tmp_path: Path, value: str, named: str) -> None:
    value = value.format(dir=tmp_path, missing=tmp_path / "snapshot_20990303")
    with pytest.raises(ScoreError) as caught:
        score_parsing.parse_snapshot_for([value])
    assert_diagnostic_error(caught.value)
    assert named in str(caught.value) and "--snapshot-for" in str(caught.value)


def test_a_half_overridden_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScoreError) as caught:
        score_parsing.parse_snapshot_for([f"degraded={tmp_path}", f"degraded={tmp_path}"])
    assert_diagnostic_error(caught.value)
    assert "second snapshot" in str(caught.value)


def test_the_command_line_refuses_an_invalid_override_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = scratch_config(tmp_path)
    snapshot = _one_run(tmp_path / "out")
    monkeypatch.setattr(score_parsing, "ensure_checkout", lambda config, *, root: tmp_path / "checkout")
    argv = ["--snapshot", str(snapshot), "--config", str(config), "--snapshot-for", "scanned=x"]
    assert score_parsing.main(argv) == 1
    error = capsys.readouterr().err
    assert_diagnostic_error(error)
    assert "--snapshot-for scanned=x" in error


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
        "| Tool | Half or set | Backend | Version | Device | Pages | Seconds per page |",
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
        "tool": "marker",
        "half": "degraded",
        "snapshot": str(snapshot),
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
        "tool": "docling",
        "half": "plain",
        "snapshot": str(snapshot),
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


def test_the_leaderboard_ends_with_the_tools_and_licences_block(scored: tuple) -> None:
    text = scored[0].read_text()
    heading = "## Tools and licences"
    assert text.count(heading) == 1
    block = text[text.index(heading) :]
    assert text.rstrip("\n").endswith(block.rstrip("\n"))
    config = load_parsing_config(PARSING_CONFIG_PATH)
    credits = {tool.name: tool.credits for tool in config.tools}
    rows = _section(block, heading)
    # Docling and Marker were scored; the unscored MinerU tools are not credited.
    assert rows == [
        "| Project | Project page | Code licence | Weights licence |",
        "|---|---|---|---|",
        *(
            f"| {c.project} | {c.url} | {c.code_licence} | {c.weights_licence} |"
            for c in (credits["docling"], credits["marker"])
        ),
    ]
    assert "MinerU" not in block


def test_a_project_run_as_two_tools_is_credited_once(tmp_path: Path) -> None:
    config = load_parsing_config(PARSING_CONFIG_PATH)
    mineru = [tool for tool in config.tools if tool.credits.project == "MinerU"]
    assert len(mineru) == 2
    lines = score_parsing._credits([tool.name for tool in mineru], config)
    assert [line for line in lines if line.startswith("| MinerU")] == [
        f"| MinerU | {mineru[0].credits.url} | {mineru[0].credits.code_licence} | "
        f"{mineru[0].credits.weights_licence} |"
    ]


@pytest.mark.parametrize(
    ("key", "value", "stated"),
    [
        ("tool", "marker", "tool 'marker'"),
        ("half", "degraded", "half 'degraded'"),
        ("snapshot", "/x/snapshot_20000101", "snapshot_20000101"),
    ],
)
def test_predictions_left_from_another_run_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str, stated: str
) -> None:
    snapshot = _one_run(tmp_path)
    record_path = tmp_path / "raw_predictions" / "docling" / "plain" / RUN_RECORD
    record = json.loads(record_path.read_text())
    record[key] = value
    record_path.write_text(json.dumps(record))
    monkeypatch.setattr(score_parsing, "run_end2end", _stub())
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
    assert str(record_path) in str(caught.value)
    assert stated in str(caught.value)
    assert not list(tmp_path.glob("leaderboard_*"))


def test_a_record_without_its_snapshot_is_refused(tmp_path: Path) -> None:
    snapshot = _one_run(tmp_path)
    record_path = tmp_path / "raw_predictions" / "docling" / "plain" / RUN_RECORD
    record = json.loads(record_path.read_text())
    del record["snapshot"]
    record_path.write_text(json.dumps(record))
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
    assert "snapshot" in str(caught.value)


def _stamped(root: Path, name: str, digests: dict[str, str]) -> Path:
    """A snapshot under `root` whose plain half lists `digests'` images, each hashed in its MANIFEST.json."""
    snapshot = root / name
    half_dir = f"plain_{name.removeprefix('snapshot_')}"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    pages = [{"page_info": {"image_path": f"{half_dir}/{image}"}} for image in digests]
    (snapshot / SNAPSHOT_CONFIG.parsing_dir / "plain.json").write_text(json.dumps(pages))
    files = {f"{half_dir}/{image}": digest for image, digest in digests.items()}
    (snapshot / "MANIFEST.json").write_text(json.dumps({"files": files}))
    return snapshot


IMAGES = {"CASE901_invoice.png": "a1" * 32, "CASE902_receipt.png": "b2" * 32}


def _rescore(
    root: Path, monkeypatch: pytest.MonkeyPatch, scored_images: dict[str, str], *, manifest: bool = True
) -> Path:
    """A docling plain run made on snapshot_20990101 (IMAGES), scored against snapshot_20990202."""
    made_on = _stamped(root, "snapshot_20990101", IMAGES)
    if not manifest:
        (made_on / "MANIFEST.json").unlink()
    later = _stamped(root, "snapshot_20990202", scored_images)
    (root / "predictions" / "docling" / "plain").mkdir(parents=True)
    raw = root / "raw_predictions" / "docling" / "plain"
    raw.mkdir(parents=True)
    record: dict[str, object] = {"tool": "docling", "half": "plain", "snapshot": str(made_on)}
    record |= {"backend": "default"}
    record |= {"tool_version": "1.2.3", "device": "mps", "page_count": 2, "seconds": 1.0}
    (raw / RUN_RECORD).write_text(json.dumps(record))
    monkeypatch.setattr(score_parsing, "run_end2end", _stub())
    score(
        later,
        output_root=root,
        config=load_parsing_config(PARSING_CONFIG_PATH),
        snapshot_config=SNAPSHOT_CONFIG,
        checkout=root,
        today=TODAY,
    )
    return raw / RUN_RECORD


def test_a_run_on_another_snapshot_with_identical_images_is_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-export that changed only the ground truth is scored against the run made before it."""
    _rescore(tmp_path, monkeypatch, dict(IMAGES))
    assert (tmp_path / "leaderboard_20990102.md").is_file()


@pytest.mark.parametrize(
    ("scored_images", "named"),
    [
        ({**IMAGES, "CASE902_receipt.png": "c3" * 32}, "CASE902_receipt.png"),
        ({"CASE901_invoice.png": IMAGES["CASE901_invoice.png"]}, "CASE902_receipt.png"),
        ({**IMAGES, "CASE903_receipt.png": "d4" * 32}, "CASE903_receipt.png"),
    ],
    ids=["a-changed-hash", "an-image-dropped", "an-image-added"],
)
def test_a_run_on_another_snapshot_with_different_images_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scored_images: dict[str, str], named: str
) -> None:
    """One image that differs, or is in only one snapshot, means the predictions did not read the page."""
    with pytest.raises(ScoreError) as caught:
        _rescore(tmp_path, monkeypatch, scored_images)
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert named in message and "1 of the plain images" in message
    assert "snapshot_20990101" in message and "snapshot_20990202" in message and "MANIFEST.json" in message
    assert not list(tmp_path.glob("leaderboard_*"))


def test_a_run_on_another_snapshot_without_a_manifest_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Images that cannot be compared are not assumed identical."""
    with pytest.raises(ScoreError) as caught:
        _rescore(tmp_path, monkeypatch, dict(IMAGES), manifest=False)
    assert_diagnostic_error(caught.value)
    assert "cannot be compared" in str(caught.value)


# The native PDFs. (tool, run) -> text edit, TEDS, TEDS structure-only, table edit, reading-order edit; a
# typeset run is named `<set>_<input>`. Docling's PDF run reads text and TEDS better and TEDS structure and
# reading order worse than its stage one plain run; its typeset PNG run is the control.
TYPESET_HEADLINES = {
    ("mineru_pipeline", "distractor"): (0.5, NAN, NAN, NAN, 0.25),
    ("mineru_pipeline", "typeset_distractor_pdf"): (0.375, 0.625, 0.75, 0.5, 0.25),
    ("mineru_vlm", "plain"): (0.3125, 0.5, 0.5, 0.5, 0.125),
    ("docling", "plain"): (0.25, 0.5, 0.625, 0.75, 0.125),
    ("docling", "typeset_plain_pdf"): (0.125, 0.75, 0.5, 0.75, 0.25),
    ("marker", "plain"): (0.0625, 0.875, 0.9375, 0.1875, 0.0),
    ("text_layer", "typeset_plain_pdf"): (0.0625, NAN, NAN, NAN, 0.5),
}
# Text edit and TEDS (the checked metrics) within 0.02 of docling's stage one plain score; the unchecked
# table edit distance and reading order well beyond it, which must not count.
CONTROL_WITHIN = (0.265625, 0.484375, 0.5, 0.875, 0.25)
CONTROL_BEYOND = (0.265625, 0.4375, 0.5, 0.875, 0.25)


def _typeset_scored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: tuple | None = CONTROL_WITHIN,
    record_input: str | None = None,
) -> tuple[Path, Path, list[tuple[Path, Path, Path]]]:
    """Every TYPESET_HEADLINES run (and docling's typeset_plain PNG control unless `control` is None),
    collected beside a snapshot holding each set's ground truth, scored with a stub; `record_input`
    overrides what docling's PDF run records as its input. Returns the leaderboard's two paths and each
    (ground truth, predictions, work directory) the stub was handed."""
    snapshot = tmp_path / "snapshot_20990101"
    parsing = snapshot / SNAPSHOT_CONFIG.parsing_dir
    parsing.mkdir(parents=True)
    for half in ("plain", "distractor"):
        (parsing / f"{SNAPSHOT_CONFIG.half_prefixes[half]}.json").write_text("[]")
    for name in ("typeset_plain", "typeset_distractor"):
        (parsing / f"{SNAPSHOT_CONFIG.typeset_prefixes[name]}.json").write_text("[]")
    headlines = dict(TYPESET_HEADLINES)
    if control is not None:
        headlines[("docling", "typeset_plain_png")] = control
    root = tmp_path / "out"
    for tool, run in headlines:
        (root / "predictions" / tool / run).mkdir(parents=True)
        raw = root / "raw_predictions" / tool / run
        raw.mkdir(parents=True)
        record: dict[str, object] = {"tool": tool, "backend": "default", "snapshot": str(snapshot)}
        record |= {"tool_version": "1.2.3", "device": "mps", "page_count": 2, "seconds": 3.0}
        if run.startswith("typeset_"):
            name, _, kind = run.rpartition("_")
            record |= {"half": name, "input": kind, "run_name": run}
            if (tool, run) == ("docling", "typeset_plain_pdf") and record_input is not None:
                record["input"] = record_input
        else:
            record["half"] = run
        (raw / RUN_RECORD).write_text(json.dumps(record))

    calls: list[tuple[Path, Path, Path]] = []

    def stub(gt_json: Path, pred_dir: Path, *, checkout: Path, work_dir: Path) -> dict:
        calls.append((gt_json, pred_dir, work_dir))
        source = result_path(pred_dir, work_dir=work_dir)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(_breakdown("docling", "plain")))
        return dict(zip(_NAMES, headlines[(pred_dir.parent.name, pred_dir.name)], strict=True))

    monkeypatch.setattr(score_parsing, "run_end2end", stub)
    markdown, table = score(
        snapshot,
        output_root=root,
        config=load_parsing_config(PARSING_CONFIG_PATH),
        snapshot_config=SNAPSHOT_CONFIG,
        checkout=tmp_path / "checkout",
        today=TODAY,
    )
    return markdown, table, calls


def test_the_gain_is_signed_so_a_positive_gain_means_the_pdf_helped() -> None:
    # An edit distance is better lower: image minus PDF.
    assert score_parsing.pdf_gain("text_block_edit_dist", image=0.25, pdf=0.125) == 0.125
    assert score_parsing.pdf_gain("reading_order_edit_dist", image=0.125, pdf=0.25) == -0.125
    # TEDS is better higher: PDF minus image.
    assert score_parsing.pdf_gain("table_teds", image=0.5, pdf=0.75) == 0.25
    assert score_parsing.pdf_gain("table_teds_structure_only", image=0.625, pdf=0.5) == -0.125
    assert math.isnan(score_parsing.pdf_gain("table_teds", image=NAN, pdf=0.75))


def test_each_typeset_run_is_scored_against_its_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    markdown, _, calls = _typeset_scored(tmp_path, monkeypatch)
    text = markdown.read_text()
    header = (
        "| Tool | plain | distractor | labelled_varied | degraded | typeset_plain_png | typeset_plain_pdf "
        "| typeset_distractor_pdf |"
    )
    assert _section(text, "### Text edit distance") == [
        header,
        "|---|---|---|---|---|---|---|---|",
        "| mineru_pipeline |  | 0.500 |  |  |  |  | 0.375 |",
        "| mineru_vlm | 0.312 |  |  |  |  |  |  |",
        "| docling | 0.250 |  |  |  | 0.266 | 0.125 |  |",
        "| marker | 0.062 |  |  |  |  |  |  |",
        "| text_layer |  |  |  |  |  | 0.062 |  |",
    ]
    gt = tmp_path / "snapshot_20990101" / SNAPSHOT_CONFIG.parsing_dir
    plain = gt / f"{SNAPSHOT_CONFIG.typeset_prefixes['typeset_plain']}.json"
    out = tmp_path / "out"
    assert (
        plain,
        out / "predictions/docling/typeset_plain_png",
        out / "scores/docling/typeset_plain_png",
    ) in calls
    assert (
        plain,
        out / "predictions/docling/typeset_plain_pdf",
        out / "scores/docling/typeset_plain_pdf",
    ) in calls
    distractor = gt / f"{SNAPSHOT_CONFIG.typeset_prefixes['typeset_distractor']}.json"
    assert (distractor, out / "predictions/mineru_pipeline/typeset_distractor_pdf") == calls[1][:2]
    assert len(calls) == len(TYPESET_HEADLINES) + 1
    assert (
        "The typeset sets are scored against: typeset_plain `snapshot_20990101`, typeset_distractor "
        "`snapshot_20990101`." in text
    )
    assert "| docling | typeset_plain_pdf | default | 1.2.3 | mps | 2 | 1.5 |" in _section(text, "## Runs")


def test_the_gain_table_holds_each_tool_s_gain_over_its_stage_one_image_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _typeset_scored(tmp_path, monkeypatch)[0].read_text()
    assert _section(text, "### Native-PDF gain over the stage one image score") == [
        "| Tool | Set | Metric | Stage one image score | PDF score | Gain |",
        "|---|---|---|---|---|---|",
        "| mineru_pipeline | typeset_distractor | Text edit distance | 0.500 | 0.375 | +0.125 |",
        "| mineru_pipeline | typeset_distractor | Table TEDS | n/a | 0.625 | n/a |",
        "| mineru_pipeline | typeset_distractor | Table TEDS, structure only | n/a | 0.750 | n/a |",
        "| mineru_pipeline | typeset_distractor | Table edit distance | n/a | 0.500 | n/a |",
        "| mineru_pipeline | typeset_distractor | Reading-order edit distance | 0.250 | 0.250 | +0.000 |",
        "| docling | typeset_plain | Text edit distance | 0.250 | 0.125 | +0.125 |",
        "| docling | typeset_plain | Table TEDS | 0.500 | 0.750 | +0.250 |",
        "| docling | typeset_plain | Table TEDS, structure only | 0.625 | 0.500 | -0.125 |",
        "| docling | typeset_plain | Table edit distance | 0.750 | 0.750 | +0.000 |",
        "| docling | typeset_plain | Reading-order edit distance | 0.125 | 0.250 | -0.125 |",
    ]
    section = text[text.index("## Native PDFs") :]
    # The baseline is named for what it is: not a same-render comparison.
    assert "stage one image score" in section and "not a controlled same-render comparison" in section
    assert "a positive gain always means the PDF helped" in section


def test_tools_without_a_gain_are_named_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _typeset_scored(tmp_path, monkeypatch)[0].read_text()
    section = text[text.index("## Native PDFs") : text.index("### Native-PDF gain")]
    # Marker takes images only, as stage one ran it; its stage one scores stand.
    assert (
        "`marker` takes images only (`inputs: [png]`) and was not run on the native PDFs; its stage one "
        "image scores stand in the tables above." in section
    )
    # A tool that takes PDFs but has no PDF run shows no gain.
    assert "`mineru_vlm` has no PDF run scored, so it shows no gain." in section
    # The text-layer baseline appears in the PDF columns only.
    assert (
        "`text_layer` takes PDFs only (`inputs: [pdf]`): it has no stage one image score, so it appears in "
        "the PDF columns only and shows no gain." in section
    )
    gains = _section(text, "### Native-PDF gain over the stage one image score")
    assert not [row for row in gains if row.startswith(("| mineru_vlm", "| marker", "| text_layer"))]


def test_the_control_within_its_threshold_is_reported_beside_the_stage_one_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _typeset_scored(tmp_path, monkeypatch)[0].read_text()
    heading = "### Control: the typeset PNG against the stage one image"
    assert _section(text, heading) == [
        "| Tool | Set | Metric | Typeset PNG | Stage one image score | Difference | Checked |",
        "|---|---|---|---|---|---|---|",
        "| docling | typeset_plain | Text edit distance | 0.266 | 0.250 | +0.016 | within 0.02 |",
        "| docling | typeset_plain | Table TEDS | 0.484 | 0.500 | -0.016 | within 0.02 |",
        "| docling | typeset_plain | Table TEDS, structure only | 0.500 | 0.625 | -0.125 | not checked |",
        "| docling | typeset_plain | Table edit distance | 0.875 | 0.750 | +0.125 | not checked |",
        "| docling | typeset_plain | Reading-order edit distance | 0.250 | 0.125 | +0.125 | not checked |",
    ]
    control = text[text.index(heading) : text.index("### Native-PDF gain")]
    assert (
        "Every checked metric (Text edit distance, Table TEDS) of every typeset PNG run is within 0.02 "
        "absolute of the tool's stage one image score" in control
    )
    assert "native_pdf_control" in control
    assert "Warning" not in text
    # The control precedes the gain table.
    assert text.index(heading) < text.index("### Native-PDF gain")


def test_a_control_beyond_its_threshold_is_flagged_above_the_gain_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _typeset_scored(tmp_path, monkeypatch, control=CONTROL_BEYOND)[0].read_text()
    assert "| docling | typeset_plain | Table TEDS | 0.438 | 0.500 | -0.062 | BEYOND 0.02 |" in text
    warning = (
        "**Warning: the control exceeds its threshold.** docling on typeset_plain: Table TEDS differs from "
        "its stage one image score by 0.062, more than 0.02."
    )
    assert warning in text
    # Prominently: straight under the section heading, above the control and the gain tables.
    lines = text.splitlines()
    native = lines.index("## Native PDFs")
    assert lines[native + 2].startswith(warning)
    assert "Every checked metric" not in text


def test_without_a_typeset_png_run_there_is_no_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _typeset_scored(tmp_path, monkeypatch, control=None)[0].read_text()
    assert "No typeset PNG run was scored, so no control separates the change of render" in text
    assert "### Control:" not in text
    assert "typeset_plain_png" not in text


def test_the_csv_holds_the_typeset_columns_the_gain_and_the_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = list(csv.DictReader(_typeset_scored(tmp_path, monkeypatch)[1].open()))
    assert list(rows[0])[4:] == [
        "plain",
        "distractor",
        "labelled_varied",
        "degraded",
        "typeset_plain_png",
        "typeset_plain_pdf",
        "typeset_distractor_pdf",
    ]
    by_key = {(row["section"], row["metric"], row["tool"], row["group"]): row for row in rows}
    assert by_key[("all", "text_block_edit_dist", "text_layer", "")]["typeset_plain_pdf"] == "0.0625"
    assert by_key[("native_pdf_gain", "table_teds", "docling", "")]["typeset_plain_pdf"] == "0.25"
    assert (
        by_key[("native_pdf_gain", "table_teds", "mineru_pipeline", "")]["typeset_distractor_pdf"] == "n/a"
    )
    assert by_key[("typeset_png_control", "table_teds", "docling", "")]["typeset_plain_png"] == "-0.015625"
    assert ("native_pdf_gain", "text_block_edit_dist", "text_layer", "") not in by_key


def test_without_typeset_runs_there_is_no_native_pdf_section(scored: tuple) -> None:
    assert "## Native PDFs" not in scored[0].read_text()


def test_a_typeset_run_recording_another_input_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ScoreError) as caught:
        _typeset_scored(tmp_path, monkeypatch, record_input="png")
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert "input 'png', not 'pdf'" in message
    assert (
        str(tmp_path / "out" / "raw_predictions" / "docling" / "typeset_plain_pdf" / RUN_RECORD) in message
    )


def test_predictions_for_an_input_the_tool_does_not_take_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The text-layer baseline reads PDFs only: an image column for it is a stray run, not a score."""
    (tmp_path / "out" / "predictions" / "text_layer" / "plain").mkdir(parents=True)
    with pytest.raises(ScoreError) as caught:
        _typeset_scored(tmp_path, monkeypatch)
    assert_diagnostic_error(caught.value)
    message = str(caught.value)
    assert str(tmp_path / "out" / "predictions" / "text_layer" / "plain") in message
    assert "tools[4].inputs" in message


def test_a_typeset_set_can_be_scored_against_another_snapshot(tmp_path: Path) -> None:
    other = tmp_path / "snapshot_20990303"
    other.mkdir()
    assert score_parsing.parse_snapshot_for([f"typeset_plain={other}"]) == {"typeset_plain": other}


def test_every_typeset_set_has_its_stage_one_baseline_half() -> None:
    assert sorted(score_parsing.IMAGE_BASELINE) == sorted(TYPESET_SETS)
    assert set(score_parsing.IMAGE_BASELINE.values()) <= set(HALVES)


def _typeset_stamped(root: Path, name: str, pdf_digest: str) -> Path:
    """A snapshot under `root` whose typeset_plain set lists one page, its PNG and PDF in MANIFEST.json."""
    snapshot = root / name
    set_dir = f"{SNAPSHOT_CONFIG.typeset_prefixes['typeset_plain']}_{name.removeprefix('snapshot_')}"
    (snapshot / SNAPSHOT_CONFIG.parsing_dir).mkdir(parents=True)
    pages = [{"page_info": {"image_path": f"{set_dir}/CASE901_invoice.png"}}]
    gt = (
        snapshot / SNAPSHOT_CONFIG.parsing_dir / f"{SNAPSHOT_CONFIG.typeset_prefixes['typeset_plain']}.json"
    )
    gt.write_text(json.dumps(pages))
    files = {f"{set_dir}/CASE901_invoice.png": "a1" * 32, f"{set_dir}/CASE901_invoice.pdf": pdf_digest}
    (snapshot / "MANIFEST.json").write_text(json.dumps({"files": files}))
    return snapshot


@pytest.mark.parametrize("pdf_digest", ["e5" * 32, "f6" * 32], ids=["same-pdf", "another-pdf"])
def test_a_pdf_run_on_another_snapshot_is_compared_by_its_pdfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdf_digest: str
) -> None:
    """Identical PNGs are not enough for a PDF run: the PDFs it read must be byte-identical too."""
    made_on = _typeset_stamped(tmp_path, "snapshot_20990101", "e5" * 32)
    later = _typeset_stamped(tmp_path, "snapshot_20990202", pdf_digest)
    (tmp_path / "predictions" / "docling" / "typeset_plain_pdf").mkdir(parents=True)
    raw = tmp_path / "raw_predictions" / "docling" / "typeset_plain_pdf"
    raw.mkdir(parents=True)
    record: dict[str, object] = {"tool": "docling", "half": "typeset_plain", "input": "pdf"}
    record |= {"snapshot": str(made_on), "backend": "default", "tool_version": "1.2.3", "device": "mps"}
    record |= {"page_count": 1, "seconds": 1.0}
    (raw / RUN_RECORD).write_text(json.dumps(record))
    monkeypatch.setattr(score_parsing, "run_end2end", _stub())

    def scored() -> None:
        score(
            later,
            output_root=tmp_path,
            config=load_parsing_config(PARSING_CONFIG_PATH),
            snapshot_config=SNAPSHOT_CONFIG,
            checkout=tmp_path,
            today=TODAY,
        )

    if pdf_digest == "e5" * 32:
        scored()
        assert (tmp_path / "leaderboard_20990102.md").is_file()
        return
    with pytest.raises(ScoreError) as caught:
        scored()
    assert_diagnostic_error(caught.value)
    assert "CASE901_invoice.pdf" in str(caught.value) and "CASE901_invoice.png" not in str(caught.value)
