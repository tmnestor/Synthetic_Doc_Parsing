#!/usr/bin/env python3
"""Measure what a table's spanning rows cost a parser that writes pipe tables.

    python scripts/spanning_rows_as_pipe.py --snapshot ../evaluation_data/snapshot_20261021 --workers 3

Some bank statements print each day's date as a row of its own across the whole table. The ground truth
records that row as one cell with `colspan`. A pipe table cannot say that a cell spans, so a parser that
writes pipe tables writes such a row as an ordinary one: the date in its first cell and the others empty.
Every character is read correctly, and the evaluator still charges for the structure: tree edit distance
counts the one spanning cell as changed and the empty cells beside it as added.

This script measures that charge, so that a guide can state it. For every set of a snapshot that holds a
spanning cell it writes the ground truth back as markdown, complete and in reading order, with every table
as a pipe table and each spanning row as an ordinary row, and scores it through
`scripts/omnidocbench_formula_free.py`. Nothing else differs from a perfect transcription, so the run
fails (exit 1) if the text or the reading order is charged at all, or if a table with no spanning cell
scores less than perfectly: the figures would then measure something besides the spanning rows.

The results are written beside this script, to `scripts/spanning_rows_as_pipe.json`; the evaluator's own
files go to `<eval_set.output_root>/spanning_rows/<snapshot>/`.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.snapshot.config import load_snapshot_config
from scripts.omnidocbench_eval import ensure_checkout, result_path, run_end2end
from scripts.perfect_score_snapshot import _git_head, pipe_table
from tests.parsing.gt_to_markdown import gt_to_markdown

RESULTS = Path(__file__).with_suffix(".json")
_SPANNING_CELL = re.compile(r'<td colspan="(\d+)">(.*?)</td>', re.DOTALL)
# What a transcription that differs from a perfect one only in its spanning rows must still score.
_UNCHARGED = {"text_block_edit_dist": 0.0, "reading_order_edit_dist": 0.0}


def spanning_rows(table_html: str) -> int:
    """How many cells of a ground-truth table span more than one column."""
    return len(_SPANNING_CELL.findall(table_html))


def ordinary_rows(table_html: str) -> str:
    """A ground-truth table with each spanning cell written as a pipe table has to write it.

    Args:
        table_html: The table as the export writes it, a spanning cell as `<td colspan="5">...</td>`.

    Returns:
        The table with each spanning cell as a plain cell followed by the empty cells it covered.

    Raises:
        ValueError: A cell spans rows, which no row of a pipe table can stand for.
    """
    if "rowspan" in table_html:
        raise ValueError(
            "A ground-truth table has a cell that spans rows.\n"
            "  What: `rowspan` in the table's html; this script rewrites cells that span columns only.\n"
            "  Where: the `html` of a table block in the snapshot's parsing/<set>.json\n"
            '  Expected: spanning cells of the form <td colspan="5">...</td>, as the export writes them.\n'
            "  Recover: decide how a pipe table would write that cell, and teach ordinary_rows to write it."
        )
    return _SPANNING_CELL.sub(
        lambda cell: f"<td>{cell.group(2)}</td>" + "<td></td>" * (int(cell.group(1)) - 1), table_html
    )


def written_page(page: dict) -> str:
    """One page as complete markdown, every table a pipe table and each spanning row an ordinary one."""
    blocks = []
    for det in page["layout_dets"]:
        if det.get("ignore"):
            continue
        if det["category_type"] == "table":
            det = {**det, "html": pipe_table(ordinary_rows(det["html"]))}
        blocks.append(det)
    return gt_to_markdown({**page, "layout_dets": blocks})


def tables_with_spanning_rows(pages: list) -> dict:
    """Image name -> spanning cells, for every page whose table has one."""
    found = {}
    for page in pages:
        cells = sum(
            spanning_rows(det["html"]) for det in page["layout_dets"] if det["category_type"] == "table"
        )
        if cells:
            found[Path(page["page_info"]["image_path"]).name] = cells
    return found


def summary(pages: list, rows: list) -> dict:
    """What the tables with spanning rows scored, and whether every other table stayed perfect.

    Args:
        pages: The ground-truth pages of one set.
        rows: The evaluator's table result: one row per ground-truth table, with its image and metrics.

    Returns:
        Counts of the tables and of those with a spanning row, the mean, lowest and highest TEDS of
        those tables, their mean structure-only TEDS and table edit distance, and the lowest TEDS of a
        table with no spanning row.
    """
    spanning = tables_with_spanning_rows(pages)
    affected = [row["metric"] for row in rows if row["img_id"] in spanning]
    others = [row["metric"]["TEDS"] for row in rows if row["img_id"] not in spanning]
    teds = [metric["TEDS"] for metric in affected]
    return {
        "tables": len(rows),
        "tables_with_spanning_rows": len(affected),
        "spanning_rows": sum(spanning.values()),
        "their_teds_mean": mean(teds),
        "their_teds_lowest": min(teds),
        "their_teds_highest": max(teds),
        "their_teds_structure_only_mean": mean(metric["TEDS_structure_only"] for metric in affected),
        "their_table_edit_dist_mean": mean(metric["Edit_dist"] for metric in affected),
        "lowest_teds_of_a_table_without_one": min(others) if others else None,
    }


def measure(snapshot: Path, *, work_root: Path, checkout: Path, workers: int) -> list:
    """Score every set that holds a spanning cell, and return one record per set of the snapshot."""
    jobs, records = [], {}
    for gt in sorted((snapshot / "parsing").glob("*.json")):
        pages = json.loads(gt.read_text())
        if not tables_with_spanning_rows(pages):
            records[gt.stem] = {"set": gt.stem, "pages": len(pages), "tables_with_spanning_rows": 0}
            continue
        predictions = work_root / "predictions" / f"{gt.stem}_spanning_rows_as_pipe"
        predictions.mkdir(parents=True, exist_ok=True)
        for page in pages:
            stem = Path(page["page_info"]["image_path"]).stem
            (predictions / f"{stem}.md").write_text(written_page(page))
        jobs.append((gt, pages, predictions))

    def run(job: tuple) -> dict:
        gt, pages, predictions = job
        work = work_root / "scores" / predictions.name
        scores = run_end2end(gt, predictions, checkout=checkout, work_dir=work, formula_free=True)
        rows = json.loads(
            result_path(predictions, work_dir=work)
            .with_name(f"{predictions.name}_quick_match_table_result.json")
            .read_text()
        )
        record = {"set": gt.stem, "pages": len(pages), **scores, **summary(pages, rows)}
        print(json.dumps(record), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(run, jobs):
            records[record["set"]] = record
    return [records[name] for name in sorted(records)]


def failures(records: list) -> list:
    """Where a run was charged for something besides its spanning rows: one line per finding."""
    failed = []
    for record in records:
        if not record["tables_with_spanning_rows"]:
            continue
        wrong = {key: record[key] for key, value in _UNCHARGED.items() if record[key] != value}
        if wrong:
            failed.append(f"{record['set']}: the text or the reading order was charged: {wrong}")
        if record["lowest_teds_of_a_table_without_one"] not in (None, 1.0):
            failed.append(
                f"{record['set']}: a table with no spanning row scored "
                f"{record['lowest_teds_of_a_table_without_one']}, not 1"
            )
    return failed


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory to measure")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    parser.add_argument("--workers", type=int, required=True, help="How many evaluator runs at once")
    args = parser.parse_args(argv)

    snapshot = args.snapshot.resolve()
    output_root = load_snapshot_config(args.config).output_root
    parsing_config = load_parsing_config(PARSING_CONFIG_PATH)
    checkout = ensure_checkout(parsing_config, root=output_root / "tools")
    records = measure(
        snapshot,
        work_root=output_root / "spanning_rows" / snapshot.name,
        checkout=checkout,
        workers=args.workers,
    )
    failed = failures(records)
    RESULTS.write_text(
        json.dumps(
            {
                "snapshot": snapshot.name,
                "measured": date.today().isoformat(),
                "evaluator_commit": parsing_config.evaluator.commit,
                "repository_commit": _git_head(REPO_ROOT),
                "failures": failed,
                "sets": records,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {RESULTS}")
    for line in failed:
        print(f"FAIL: {line}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
