#!/usr/bin/env python3
"""Score every collected parser run against a snapshot's parsing ground truth, and write the leaderboard.

    python scripts/score_parsing.py --snapshot ../evaluation_data/snapshot_20261013

For each tool in `tools` of `config/parsing_config.yml` and each half with predictions under
`<eval_set.output_root>/predictions/<tool>/<half>/`, the pinned evaluator (`run_end2end`, unmodified)
scores them against `<snapshot>/<parsing_dir>/<half prefix>.json`, working in
`<eval_set.output_root>/scores/<tool>/<half>/`. The leaderboard is written to
`<eval_set.output_root>/leaderboard_<YYYYMMDD>.md` and `.csv`: rows per tool, columns per half, for text
edit distance, table TEDS (with structure-only TEDS), table edit distance and reading-order edit distance,
then the same broken down by document type, degradation tier and layout key (the page attributes the
evaluator slices by, read from its metric result), and each run's back-end, version and device, read from
the `run.json` the runner left beside the raw output.

Tables lead with TEDS. Table edit distance is reported beside it with its caveat: it compares html
strings, and the evaluator converts a markdown pipe table to html with attributes an html table does not
carry, so it compares only tools with the same output form (the design's section 5.3, recommendation 8).
Overall needs formula scores, which arrive in stage three, so it is shown as not applicable and never
replaced by an average of the other components.
"""

import argparse
import csv
import io
import json
import math
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import (  # noqa: E402
    PARSING_CONFIG_PATH,
    ParsingConfig,
    ParsingConfigError,
    load_parsing_config,
)
from generators.snapshot.config import HALVES, SnapshotConfig, load_snapshot_config  # noqa: E402
from scripts.collect_predictions import PREDICTIONS_DIR  # noqa: E402
from scripts.omnidocbench_eval import (  # noqa: E402
    EvaluatorError,
    ensure_checkout,
    result_path,
    run_end2end,
)
from scripts.run_local_parser import RAW_DIR, RUN_RECORD  # noqa: E402

OVERALL = "n/a (no formulas in stage one)"
SCORES_DIR = "scores"
# Each metric: its name in run_end2end's scores, its heading, and where the evaluator's metric result
# keeps its per-attribute values (element, metric under `page`). Tables lead with TEDS.
METRICS = (
    ("text_block_edit_dist", "Text edit distance", ("text_block", "Edit_dist")),
    ("table_teds", "Table TEDS", ("table", "TEDS")),
    ("table_teds_structure_only", "Table TEDS, structure only", ("table", "TEDS_structure_only")),
    ("table_edit_dist", "Table edit distance", ("table", "Edit_dist")),
    ("reading_order_edit_dist", "Reading-order edit distance", ("reading_order", "Edit_dist")),
)
# The page attributes broken down, as the evaluator names them, with each one's section and column title.
BREAKDOWNS = (
    ("doc_type", "By document type", "Document type"),
    ("tier", "By degradation tier", "Tier"),
    ("layout_id", "By layout", "Layout key"),
)
_NOTES = {
    "table_edit_dist": "Table edit distance compares html strings. The evaluator turns a markdown pipe table "
    'into html with `colspan="1" rowspan="1"` on every cell, which an html table does not carry, so a '
    "perfect markdown table can score 0.5 here while scoring TEDS 1: compare it only between tools with the "
    "same output form (html or markdown). TEDS is not affected.",
    "reading_order_edit_dist": "Reading order includes one known artefact of the pinned evaluator: once "
    "matched, a page's first predicted table drops out of the predicted order, one edit on every such page "
    "for every tool alike (the design's section 5.3).",
}
_RUN_FIELDS = (("backend", "Backend"), ("tool_version", "Version"), ("device", "Device"))


class ScoreError(RuntimeError):
    """The runs cannot be scored: nothing to score, or a run's record or ground truth is missing."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> ScoreError:
    """Build a four-part refusal, each part on a labelled line."""
    return ScoreError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _run_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise TypeError(f"the record is {type(record).__name__}, not an object")
        for key in ("backend", "tool_version", "device", "page_count", "seconds"):
            record[key]  # noqa: B018  every field the leaderboard reports must be present
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise _diagnostic(
            "A scored run has no readable record.",
            what=f"{path} is missing or incomplete ({err!r}); the leaderboard reports each run's device "
            "and version from it.",
            where=f"{path}",
            expected='the run.json scripts/run_local_parser.py writes, e.g. {"backend": "default", '
            '"tool_version": "2.0.0", "device": "mps", "page_count": 165, "seconds": 900.0}',
            recover="run the tool with scripts/run_local_parser.py and collect it again; a run made another "
            "way needs a run.json beside its raw output.",
        ) from err
    return record


def _missing_metric(source: Path, dotted: str, metric: str, err: Exception) -> ScoreError:
    """The refusal for a metric the evaluator's result does not hold where the pinned commit writes it."""
    return _diagnostic(
        "The evaluator's result lacks a metric the leaderboard reports.",
        what=f"{metric} is missing or not a number at {dotted} ({err!r}).",
        where=f"{source} -> {dotted}",
        expected=f"a number at {dotted}, as the pinned evaluator writes it, e.g. "
        '{"table": {"page": {"TEDS": {"doc_type: invoice": 0.5}}}}',
        recover="check the evaluator checkout is at evaluator.commit and that the run scored text, tables "
        "and reading order (its log lists each element), then score again.",
    )


def _headline(scores: dict, *, source: Path) -> dict[str, float]:
    """The headline scores the leaderboard reports, refusing four-part when one is missing."""
    headline: dict[str, float] = {}
    for name, _, (element, metric) in METRICS:
        try:
            headline[name] = float(scores[name])
        except (KeyError, TypeError, ValueError) as err:
            raise _missing_metric(source, f"{element}.all.{metric}", name, err) from err
    return headline


def _breakdown(source: Path) -> dict[tuple[str, str], dict[str, float]]:
    """(element, metric) -> {page attribute group: value}, from the evaluator's metric result."""
    try:
        result = json.loads(source.read_text())
    except (OSError, ValueError) as err:
        raise _missing_metric(source, "(file)", "the metric result", err) from err
    breakdown: dict[tuple[str, str], dict[str, float]] = {}
    for name, _, (element, metric) in METRICS:
        dotted = f"{element}.page.{metric}"
        try:
            values = result[element]["page"][metric]
            breakdown[(element, metric)] = {group: float(value) for group, value in values.items()}
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise _missing_metric(source, dotted, name, err) from err
    return breakdown


def _number(value: float | None) -> str:
    if value is None:
        return ""
    return "n/a" if math.isnan(value) else f"{value:.3f}"


def _raw(value: float | None) -> str:
    if value is None:
        return ""
    return "n/a" if math.isnan(value) else repr(value)


def score(
    snapshot: Path,
    *,
    output_root: Path,
    config: ParsingConfig,
    snapshot_config: SnapshotConfig,
    checkout: Path,
    today: date,
) -> tuple[Path, Path]:
    """Score every collected run and write the leaderboard.

    Args:
        snapshot: The snapshot directory holding the parsing ground truth.
        output_root: The directory holding `predictions/` and `raw_predictions/`; scores and the
            leaderboard are written there too.
        config: The validated parsing configuration, listing the tools.
        snapshot_config: The snapshot configuration, naming the parsing directory and half prefixes.
        checkout: The evaluator checkout.
        today: The date the leaderboard is named and headed with.

    Returns:
        The leaderboard's markdown and csv paths.

    Raises:
        ScoreError: No configured tool has predictions, a scored run lacks its record or its half's
            ground truth, or the evaluator's result lacks a reported metric.
        EvaluatorError: The evaluator failed.
    """
    runs: dict[tuple[str, str], tuple[dict, dict[str, float], dict]] = {}
    for tool in config.tools:
        for half in HALVES:
            pred = output_root / PREDICTIONS_DIR / tool.name / half
            if not pred.is_dir():
                continue
            record = _run_record(output_root / RAW_DIR / tool.name / half / RUN_RECORD)
            gt = snapshot / snapshot_config.parsing_dir / f"{snapshot_config.half_prefixes[half]}.json"
            if not gt.is_file():
                raise _diagnostic(
                    "A scored half has no ground truth in the snapshot.",
                    what=f"{tool.name} has predictions for {half}, but {gt} does not exist.",
                    where=f"{gt}",
                    expected=f"the snapshot's parsing file for the half, {gt.name}, as `eval-set` writes it.",
                    recover="pass --snapshot the snapshot the predictions were made from.",
                )
            work = output_root / SCORES_DIR / tool.name / half
            source = result_path(pred, work_dir=work)
            headline = _headline(run_end2end(gt, pred, checkout=checkout, work_dir=work), source=source)
            runs[(tool.name, half)] = (record, headline, _breakdown(source))
    if not runs:
        raise _diagnostic(
            "There is nothing to score.",
            what=f"no configured tool {[tool.name for tool in config.tools]} has a predictions directory for "
            f"any half {list(HALVES)}.",
            where=f"{output_root / PREDICTIONS_DIR}",
            expected="predictions/<tool>/<half>/<image stem>.md, e.g. predictions/docling/plain/",
            recover="run scripts/run_local_parser.py and scripts/collect_predictions.py for a tool first.",
        )
    tools = list(dict.fromkeys(tool for tool, _ in runs))
    rows = _rows(runs, tools)
    stamp = today.strftime("%Y%m%d")
    markdown = output_root / f"leaderboard_{stamp}.md"
    table = output_root / f"leaderboard_{stamp}.csv"
    markdown.write_text(_markdown(rows, runs, tools, snapshot=snapshot, config=config, today=today))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "metric", "tool", "group", *HALVES])
    for (section, metric, name, group), cells in rows.items():
        writer.writerow([section, metric, name, group, *(cells.get(half, "") for half in HALVES)])
    table.write_text(buffer.getvalue())
    return markdown, table


Rows = dict[tuple[str, str, str, str], dict[str, str]]


def _rows(runs: dict, tools: list[str]) -> Rows:
    """Every csv row: (section, metric, tool, group) -> half -> cell, in leaderboard order."""
    rows: Rows = {}

    def put(key: tuple[str, str, str, str], half: str, cell: str) -> None:
        rows.setdefault(key, {})[half] = cell

    for tool in tools:
        for half in HALVES:
            if (tool, half) in runs:
                put(("overall", "overall", tool, ""), half, OVERALL)
    for tool in tools:
        for half in HALVES:
            if (tool, half) not in runs:
                continue
            record = runs[(tool, half)][0]
            for field, _ in _RUN_FIELDS:
                put(("run", field, tool, ""), half, str(record[field]))
            put(("run", "pages", tool, ""), half, str(record["page_count"]))
            put(
                ("run", "seconds_per_page", tool, ""),
                half,
                f"{record['seconds'] / record['page_count']:.1f}",
            )
    for name, _, _ in METRICS:
        for tool in tools:
            for half in HALVES:
                if (tool, half) in runs:
                    put(("all", name, tool, ""), half, _raw(runs[(tool, half)][1][name]))
    for attribute, _, _ in BREAKDOWNS:
        prefix = f"{attribute}: "
        for name, _, source in METRICS:
            for tool in tools:
                groups = sorted(
                    {
                        group.removeprefix(prefix)
                        for half in HALVES
                        if (tool, half) in runs
                        for group in runs[(tool, half)][2][source]
                        if group.startswith(prefix)
                    }
                )
                for group in groups:
                    for half in HALVES:
                        value = (
                            runs[(tool, half)][2][source].get(prefix + group)
                            if (tool, half) in runs
                            else None
                        )
                        if value is not None:
                            put((attribute, name, tool, group), half, _raw(value))
    return rows


def _table(header: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return lines


def _cells(rows: Rows, key: tuple[str, str, str, str]) -> list[str]:
    """A row's cells for each half, three decimals, blank where the tool was not run."""
    cells = rows.get(key, {})
    return [_number(None if half not in cells else _parse(cells[half])) for half in HALVES]


def _parse(cell: str) -> float:
    return math.nan if cell == "n/a" else float(cell)


def _markdown(
    rows: Rows, runs: dict, tools: list[str], *, snapshot: Path, config: ParsingConfig, today: date
) -> str:
    halves = list(HALVES)
    lines = [
        "# Parsing leaderboard",
        "",
        f"Snapshot `{snapshot.name}`, scored {today.day} {today.strftime('%B %Y')} with OmniDocBench at commit "
        f"`{config.evaluator.commit}`, unmodified, with quick matching.",
        "",
        "Edit distances are normalised: 0 is a perfect reading and 1 the worst. TEDS runs from 0 to 1, 1 "
        "being a perfect table. Text, table and reading-order edit distances are means over a half's pages; "
        "TEDS is the mean over its tables. A blank cell is a half the tool was not run on; n/a is a score "
        "the evaluator could not compute, because nothing of that kind was matched.",
        "",
        "## Overall",
        "",
        *_table(
            ["Tool", *halves],
            [
                [tool, *(rows.get(("overall", "overall", tool, ""), {}).get(half, "") for half in halves)]
                for tool in tools
            ],
        ),
        "",
        "Overall is the mean of the text, table and formula scores. It needs formula scores (CDM), which "
        "arrive in stage three; the components below are never averaged in its place.",
        "",
        "## Runs",
        "",
        *_table(
            ["Tool", "Half", *(title for _, title in _RUN_FIELDS), "Pages", "Seconds per page"],
            [
                [
                    tool,
                    half,
                    *(rows[("run", field, tool, "")][half] for field, _ in _RUN_FIELDS),
                    rows[("run", "pages", tool, "")][half],
                    rows[("run", "seconds_per_page", tool, "")][half],
                ]
                for tool in tools
                for half in halves
                if (tool, half) in runs
            ],
        ),
        "",
        "## Scores",
    ]
    for name, title, _ in METRICS:
        lines += ["", f"### {title}", ""]
        lines += _table(
            ["Tool", *halves], [[tool, *_cells(rows, ("all", name, tool, ""))] for tool in tools]
        )
        if name in _NOTES:
            lines += ["", _NOTES[name]]
    for attribute, section, column in BREAKDOWNS:
        lines += ["", f"## {section}"]
        for name, title, _ in METRICS:
            body = [
                [tool, group, *_cells(rows, (attribute, name, tool, group))]
                for (kind, metric, tool, group) in rows
                if kind == attribute and metric == name
            ]
            lines += ["", f"### {title}", ""]
            lines += _table(["Tool", column, *halves], body)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    args = parser.parse_args(argv)
    try:
        snapshot_config = load_snapshot_config(args.config)
        config = load_parsing_config(PARSING_CONFIG_PATH)
        output_root = snapshot_config.output_root
        checkout = ensure_checkout(config, root=output_root / "tools")
        markdown, table = score(
            args.snapshot.resolve(),
            output_root=output_root,
            config=config,
            snapshot_config=snapshot_config,
            checkout=checkout,
            today=date.today(),
        )
    except (ScoreError, EvaluatorError, ParsingConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Leaderboard: {markdown}\n             {table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
