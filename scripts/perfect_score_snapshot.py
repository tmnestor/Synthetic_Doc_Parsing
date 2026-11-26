#!/usr/bin/env python3
"""Score a snapshot's own parsing ground truth, written back as markdown, with the pinned evaluator.

    python scripts/perfect_score_snapshot.py --snapshot ../evaluation_data/snapshot_20261021

A scorer is fit to hand to someone else only if a perfect transcription scores perfectly on it and a
transcription that leaves one line out is charged for that line and nothing else. This script measures
both, on every page of every set of a snapshot, in the two forms a parser writes tables in:

- `complete`: every block of the page, in reading order. Must score text edit distance 0, table TEDS 1,
  table edit distance 0 and reading-order edit distance 0.
- `one_line_left_out`: the same, less the middle text block of the page. The page must be charged
  exactly the characters of the left-out line, no more and no fewer, with the tables still perfect. The
  evaluator compares text on letters, digits and underscores, so those are the characters counted. It
  usually shows the left-out line as a row of its own, paired with nothing; where the line resembles the
  line beside it, it merges the two into one row and charges the missing characters there, which is the
  same charge. Both are counted, and the second is reported.

Neither writes a block the ground truth marks `ignore`, a line a photograph cropped: a parser cannot read
it, and the evaluator pairs a ground-truth line that has no counterpart with whatever text of the
transcription matched nothing, so a cropped line written out would be paired with the line left out.

Each is written with its tables as html and again with every table that has no spanning cell as a pipe
table (a pipe table cannot say that a cell spans), and each of the four is scored twice: by the
evaluator unmodified, which records what its faults cost, and through
`scripts/omnidocbench_formula_free.py`, which must pass.

The results are written beside this script, to `scripts/perfect_score_snapshot.json`; the evaluator's
own files go to `<eval_set.output_root>/perfect_score/<snapshot>/`. Exits 1 when a formula-free run
fails either requirement.

`--judge-only` reads the evaluator's saved files in that directory instead of running it again. It is
for a change to how a result is judged, which the evaluator's output does not depend on; after any change
to a launcher, run the evaluator again.
"""

import argparse
import html
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.snapshot.config import load_snapshot_config
from scripts.omnidocbench_eval import ensure_checkout, parse_result, result_path, run_end2end
from tests.parsing.gt_to_markdown import gt_to_markdown

RESULTS = Path(__file__).with_suffix(".json")
TRANSCRIPTIONS = ("complete", "one_line_left_out")
TABLE_FORMS = ("html", "pipe")
LAUNCHERS = ("unmodified", "formula_free")
_PERFECT = {
    "text_block_edit_dist": 0.0,
    "table_teds": 1.0,
    "table_teds_structure_only": 1.0,
    "table_edit_dist": 0.0,
    "reading_order_edit_dist": 0.0,
}
_TABLES_PERFECT = ("table_teds", "table_teds_structure_only", "table_edit_dist")
# What the evaluator strips from text before comparing it: everything but letters, digits and underscores
# (`clean_string` in its src/core/preprocess/data_preprocess.py).
_NOT_COMPARED = re.compile(r"[^\w\u4e00-\u9fff]")


def pipe_table(table_html: str) -> str:
    """A ground-truth html table as a pipe table; one with a spanning cell is returned unchanged.

    Args:
        table_html: The table as the export writes it: `<table><tr><td>...</td></tr>...</table>`.

    Returns:
        The table as markdown, its first row the header, or `table_html` when a cell spans.
    """
    if re.search(r"(col|row)span", table_html):
        return table_html
    rows = [
        re.findall(r"<td>(.*?)</td>", row, re.DOTALL)
        for row in re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)
    ]
    lines = [
        "| " + " | ".join(html.unescape(cell).replace("|", "\\|") for cell in row) + " |" for row in rows
    ]
    lines.insert(1, "|" + "|".join("---" for _ in rows[0]) + "|")
    return "\n".join(lines)


def left_out_block(page: dict) -> dict:
    """The block `one_line_left_out` leaves out: the middle one of the page's scored text blocks."""
    texts = sorted(
        (
            det
            for det in page["layout_dets"]
            if det["category_type"] == "text_block" and not det.get("ignore")
        ),
        key=lambda det: det["order"],
    )
    return texts[len(texts) // 2]


def written_page(page: dict, *, transcription: str, tables: str) -> str:
    """One page as the markdown of the named transcription and table form."""
    gone = left_out_block(page) if transcription == "one_line_left_out" else None
    blocks = []
    for det in page["layout_dets"]:
        if det is gone or det.get("ignore"):
            continue
        if det["category_type"] == "table" and tables == "pipe":
            det = {**det, "html": pipe_table(det["html"])}
        blocks.append(det)
    return gt_to_markdown({**page, "layout_dets": blocks})


def compared_length(text: str) -> int:
    """How many characters of `text` the evaluator compares: its letters, digits and underscores."""
    return len(_NOT_COMPARED.sub("", text))


def left_out_judgement(pages: list, rows: list) -> dict:
    """Judge, page by page, what a transcription with one line left out was charged.

    Args:
        pages: The ground-truth pages.
        rows: The evaluator's text result: one row per ground-truth line or merged run of lines, with the
            text it was paired with, its normalised edit distance and its count of edits.

    Returns:
        `pages_charged_exactly_the_line`: the pages whose edits add up to the left-out line's compared
        characters; `of_which_as_a_row_of_its_own`: those where the line is the only charged row, paired
        with nothing; and `exceptions`: every page charged more or less, with its charged rows.
    """
    charged: dict = {}
    for row in rows:
        if row["edit"] != 0:
            charged.setdefault(row["img_id"], []).append(row)
    exact = alone = 0
    exceptions = []
    for page in pages:
        image = Path(page["page_info"]["image_path"]).name
        text = left_out_block(page)["text"]
        found = charged.get(image, [])
        edits = sum(row["Edit_num"] for row in found)
        if edits == compared_length(text):
            exact += 1
            alone += len(found) == 1 and found[0]["gt"] == text and found[0]["pred"] == ""
            continue
        exceptions.append(
            {
                "image": image,
                "left_out": text,
                "characters_left_out": compared_length(text),
                "edits_charged": edits,
                "charged": [{"gt": row["gt"], "pred": row["pred"], "edit": row["edit"]} for row in found],
            }
        )
    return {
        "pages_charged_exactly_the_line": exact,
        "of_which_as_a_row_of_its_own": alone,
        "exceptions": exceptions,
    }


def _git_head(directory: Path) -> str:
    """The commit `directory` has out, with `-dirty` when tracked files differ from it."""
    head = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"{head}-dirty" if dirty else head


def measure(snapshot: Path, *, work_root: Path, checkout: Path, workers: int, judge_only: bool) -> list:
    """Score every set, transcription, table form and launcher, and return one record per run.

    Args:
        snapshot: The snapshot directory.
        work_root: Where the written-back pages and the evaluator's files are kept.
        checkout: The evaluator checkout.
        workers: How many evaluator runs at once.
        judge_only: Read each run's saved result from `work_root` instead of running the evaluator.

    Raises:
        FileNotFoundError: `judge_only` was asked for and a run's saved result is absent.
    """
    jobs = []
    for gt in sorted((snapshot / "parsing").glob("*.json")):
        pages = json.loads(gt.read_text())
        for transcription in TRANSCRIPTIONS:
            for tables in TABLE_FORMS:
                name = f"{gt.stem}_{transcription}_{tables}"
                predictions = work_root / "predictions" / name
                predictions.mkdir(parents=True, exist_ok=True)
                for page in [] if judge_only else pages:
                    stem = Path(page["page_info"]["image_path"]).stem
                    (predictions / f"{stem}.md").write_text(
                        written_page(page, transcription=transcription, tables=tables)
                    )
                jobs += [
                    (gt, pages, transcription, tables, launcher, predictions) for launcher in LAUNCHERS
                ]

    def run(job: tuple) -> dict:
        gt, pages, transcription, tables, launcher, predictions = job
        work = work_root / "scores" / f"{predictions.name}_{launcher}"
        saved = result_path(predictions, work_dir=work)
        if judge_only and not saved.is_file():
            raise FileNotFoundError(
                "A run has no saved result to judge.\n"
                f"  What: {saved} does not exist.\n"
                f"  Where: {work_root}, the evaluator's files from the last full run\n"
                "  Expected: one saved result for every set, transcription, table form and launcher.\n"
                "  Recover: run the script without --judge-only, which runs the evaluator."
            )
        if judge_only:
            scores = parse_result(json.loads(saved.read_text()), source=saved)
        else:
            formula_free = launcher == "formula_free"
            scores = run_end2end(
                gt, predictions, checkout=checkout, work_dir=work, formula_free=formula_free
            )
        record = {
            "set": gt.stem,
            "pages": len(pages),
            "transcription": transcription,
            "tables": tables,
            "launcher": launcher,
            **scores,
        }
        if transcription == "one_line_left_out":
            rows = json.loads(
                result_path(predictions, work_dir=work)
                .with_name(f"{predictions.name}_quick_match_text_block_result.json")
                .read_text()
            )
            record.update(left_out_judgement(pages, rows))
        print(json.dumps({k: v for k, v in record.items() if k != "exceptions"}), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, jobs))


def failures(records: list) -> list:
    """What the formula-free runs got wrong: one line per failed requirement."""
    failed = []
    for record in records:
        if record["launcher"] != "formula_free":
            continue
        name = f"{record['set']}, {record['transcription']}, {record['tables']} tables"
        if record["transcription"] == "complete":
            wrong = {key: record[key] for key, value in _PERFECT.items() if record[key] != value}
            if wrong:
                failed.append(f"{name}: a complete transcription is not perfect: {wrong}")
        else:
            wrong = {key: record[key] for key in _TABLES_PERFECT if record[key] != _PERFECT[key]}
            if wrong:
                failed.append(f"{name}: leaving a line of text out moved a table score: {wrong}")
            if record["exceptions"]:
                failed.append(
                    f"{name}: {len(record['exceptions'])} of {record['pages']} pages are charged more or "
                    "fewer characters than the left-out line holds"
                )
    return failed


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory to check")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    parser.add_argument("--workers", type=int, required=True, help="How many evaluator runs at once")
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="Judge the evaluator's saved results again without running it (not after a launcher change)",
    )
    args = parser.parse_args(argv)

    snapshot = args.snapshot.resolve()
    output_root = load_snapshot_config(args.config).output_root
    parsing_config = load_parsing_config(PARSING_CONFIG_PATH)
    checkout = ensure_checkout(parsing_config, root=output_root / "tools")
    records = measure(
        snapshot,
        work_root=output_root / "perfect_score" / snapshot.name,
        checkout=checkout,
        workers=args.workers,
        judge_only=args.judge_only,
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
                "runs": records,
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
