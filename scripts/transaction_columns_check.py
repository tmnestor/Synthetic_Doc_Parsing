#!/usr/bin/env python3
"""Prove the transaction check on a snapshot's own bank statements before it is handed to anyone.

    python scripts/transaction_columns_check.py --snapshot ../evaluation_data/snapshot_20261021

`scripts/transaction_columns.py` counts, for every row of a statement that holds an amount, whether a
parsed table holds that amount in the right column. Like any scorer it is fit to hand over only if a
perfect answer is judged perfect and an answer with one thing wrong is charged for that thing alone.
This script measures both on every bank statement of every set:

- **Through the evaluator.** The scoring notebook judges the table the evaluator matched to the page, as
  the evaluator recorded it. So the perfect transcriptions already scored by
  `scripts/perfect_score_snapshot.py` (tables as html, and as pipe tables) and by
  `scripts/spanning_rows_as_pipe.py` (date rows written as ordinary rows) are read back from the
  evaluator's saved results, and every row of every statement must be judged right, with no row added.
- **One thing wrong.** In each statement's ground-truth table the middle row that holds one amount is
  spoiled in one of three ways (its amount moved to the other column, one digit of it changed, the row
  left out), the table is written as html and as a pipe table, and the judgement must name that one row
  and that one fault, with every other row right.

The results are written beside this script, to `scripts/transaction_columns_check.json`. Exits 1 on any
failure. It runs nothing but Python: the evaluator's results must already be on disk.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.snapshot.config import load_snapshot_config
from scripts.perfect_score_snapshot import _git_head, pipe_table
from scripts.pipe_tables import pipe_tables_to_html
from scripts.spanning_rows_as_pipe import ordinary_rows
from scripts.transaction_columns import KINDS, amount, amount_columns, judge_table, table_rows

RESULTS = Path(__file__).with_suffix(".json")
STATEMENT = "bank_statement"
# The evaluator runs whose saved results are read back: where each is, under the output root.
SAVED_RUNS = {
    "complete, tables as html": "perfect_score/{snapshot}/scores/{set}_complete_html_formula_free/"
    "result/{set}_complete_html_quick_match_table_result.json",
    "complete, tables as pipe tables": "perfect_score/{snapshot}/scores/{set}_complete_pipe_formula_free/"
    "result/{set}_complete_pipe_quick_match_table_result.json",
    "complete, date rows as ordinary pipe rows": "spanning_rows/{snapshot}/scores/"
    "{set}_spanning_rows_as_pipe/result/{set}_spanning_rows_as_pipe_quick_match_table_result.json",
}
FAULTS = {
    "amount_in_the_other_column": "wrong_column",
    "one_digit_changed": "amount_misread",
    "row_left_out": "row_missing",
}
_ROW = re.compile(r"<tr>.*?</tr>", re.DOTALL)
_CELL = re.compile(r"<td>(.*?)</td>", re.DOTALL)


def statement_tables(pages: list) -> dict:
    """Image name -> the html of the table, for every bank statement page."""
    return {
        Path(page["page_info"]["image_path"]).name: det["html"]
        for page in pages
        if page["page_info"]["page_attribute"]["doc_type"] == STATEMENT
        for det in page["layout_dets"]
        if det["category_type"] == "table"
    }


def spoiled(table_html: str, fault: str) -> str:
    """A ground-truth statement table with one fault in its middle row that holds a single amount.

    Args:
        table_html: The table as the export writes it.
        fault: One of `FAULTS`.

    Returns:
        The table with that row's amount in the other column, with one digit of it changed, or without
        the row.
    """
    rows = _ROW.findall(table_html)
    if "<table>" + "".join(rows) + "</table>" != table_html:
        raise ValueError(
            "A ground-truth table is not written as this check expects.\n"
            "  What: the table is not <table> followed by rows of <tr>...</tr> and nothing else.\n"
            "  Where: the `html` of a table block in the snapshot's parsing/<set>.json\n"
            "  Expected: <table><tr><td>...</td></tr>...</table> on one line, as the export writes it.\n"
            "  Recover: teach spoiled() the new form before trusting this check on that snapshot."
        )
    withdrawals, deposits = amount_columns(table_rows(table_html)[0])
    single = []
    for index, row in enumerate(rows):
        cells = _CELL.findall(row)
        if (
            index
            and len(cells) > max(withdrawals, deposits)
            and bool(amount(cells[withdrawals])) != bool(amount(cells[deposits]))
        ):
            single.append(index)
    index = single[len(single) // 2]
    cells = _CELL.findall(rows[index])
    filled = withdrawals if amount(cells[withdrawals]) else deposits
    if fault == "amount_in_the_other_column":
        cells[withdrawals], cells[deposits] = cells[deposits], cells[withdrawals]
    elif fault == "one_digit_changed":
        last = max(i for i, character in enumerate(cells[filled]) if character.isdigit())
        digit = "7" if cells[filled][last] != "7" else "3"
        cells[filled] = cells[filled][:last] + digit + cells[filled][last + 1 :]
    rows[index] = (
        "" if fault == "row_left_out" else "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    )
    return "<table>" + "".join(rows) + "</table>"


def as_pipe(table_html: str) -> str:
    """A ground-truth table as the html the launcher makes of the same table written as a pipe table."""
    return pipe_tables_to_html(pipe_table(ordinary_rows(table_html)) + "\n")


def _total(judgements: list) -> dict:
    total = dict.fromkeys(("rows_with_an_amount", *KINDS, "rows_added"), 0)
    for judgement in judgements:
        for key in total:
            total[key] += judgement[key]
    return total


def read_back(tables: dict, saved: Path) -> dict:
    """Judge the tables one saved evaluator run recorded for a set's statements.

    Raises:
        FileNotFoundError: The run's saved result is absent.
    """
    if not saved.is_file():
        raise FileNotFoundError(
            "An evaluator run this check reads back has no saved result.\n"
            f"  What: {saved} does not exist.\n"
            "  Where: the output root's perfect_score/ and spanning_rows/ folders\n"
            "  Expected: the per-table results of scripts/perfect_score_snapshot.py and "
            "scripts/spanning_rows_as_pipe.py for this snapshot.\n"
            "  Recover: run those two scripts on this snapshot, then this one."
        )
    recorded = {row["img_id"]: row["pred"] for row in json.loads(saved.read_text())}
    judgements = [judge_table(table, recorded.get(image, "")) for image, table in tables.items()]
    return {"statements": len(tables), **_total(judgements)}


def one_fault(tables: dict, fault: str) -> dict:
    """Spoil each statement's table with one fault, in both table forms, and count the right judgements."""
    named = FAULTS[fault]
    exact, failed = 0, []
    for image, table in tables.items():
        bad = spoiled(table, fault)
        for form, parsed in (("html", bad), ("pipe", as_pipe(bad))):
            judgement = judge_table(table, parsed)
            counts = {kind: judgement[kind] for kind in KINDS if judgement[kind]}
            wanted = {"right": judgement["rows_with_an_amount"] - 1, named: 1}
            if counts == wanted and judgement["rows_added"] == 0:
                exact += 1
            else:
                failed.append({"image": image, "tables": form, "judged": counts})
    return {"judged": 2 * len(tables), "named_that_fault_alone": exact, "exceptions": failed}


def measure(snapshot: Path, *, output_root: Path) -> list:
    """One record per set of the snapshot that holds a bank statement."""
    records = []
    for gt in sorted((snapshot / "parsing").glob("*.json")):
        tables = statement_tables(json.loads(gt.read_text()))
        if not tables:
            continue
        record: dict = {"set": gt.stem, "statements": len(tables), "read_back": {}, "one_fault": {}}
        for name, where in SAVED_RUNS.items():
            saved = output_root / where.format(snapshot=snapshot.name, set=gt.stem)
            record["read_back"][name] = read_back(tables, saved)
        for fault in FAULTS:
            record["one_fault"][fault] = one_fault(tables, fault)
        print(json.dumps({"set": gt.stem, "statements": len(tables)}), flush=True)
        records.append(record)
    return records


def failures(records: list) -> list:
    """Every requirement a record fails: one line each."""
    failed = []
    for record in records:
        for name, total in record["read_back"].items():
            if total["right"] != total["rows_with_an_amount"] or total["rows_added"]:
                failed.append(
                    f"{record['set']}, {name}: a perfect transcription is not judged right: {total}"
                )
        for fault, result in record["one_fault"].items():
            if result["named_that_fault_alone"] != result["judged"]:
                failed.append(
                    f"{record['set']}, {fault}: {len(result['exceptions'])} of {result['judged']} tables are "
                    "not judged as that one fault and nothing else"
                )
    return failed


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory to check")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    args = parser.parse_args(argv)
    snapshot = args.snapshot.resolve()
    records = measure(snapshot, output_root=load_snapshot_config(args.config).output_root)
    failed = failures(records)
    RESULTS.write_text(
        json.dumps(
            {
                "snapshot": snapshot.name,
                "measured": date.today().isoformat(),
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
