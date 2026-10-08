#!/usr/bin/env python3
"""Check a returned raw_extractions.jsonl against the ground truth before scoring it.

    python scripts/check_extractions_match_gt.py \
        --extractions /path/to/raw_extractions.jsonl \
        --ground-truth /path/to/ground_truth.csv

Run this on anything that comes back from the extraction host, BEFORE opening the
scoring notebook. A raw-extractions file names the images it processed; a ground
truth names the images it describes. If those two sets have drifted apart the
notebook still produces numbers -- it scores whatever overlap exists and reports
the rest as unmatched -- and a partial overlap is very easy to miss in a summary
that otherwise looks reasonable.

That is not hypothetical. A raw_extractions.jsonl from June, scored against the
ground truth as it stood in August, matched 22 of its 165 filenames: the corpus
had been reseeded in between, so the images the model saw no longer corresponded
to the answers. Any F1 computed from that pairing would have been noise wearing
the costume of a measurement.

Exits non-zero unless every extracted image has ground truth. Use --allow-partial
when scoring a deliberate subset.
"""

import argparse
import json
import sys
from pathlib import Path


_ID_COLUMNS = ("image_file", "filename", "image_name", "file")


def _diagnostic(what: str, where: str, example: str, fix: str) -> str:
    """Assemble a 4-element diagnostic error message."""
    return f"What: {what}\nWhere: {where}\nExpected: {example}\nHow to fix: {fix}"


def load_ground_truth_names(path: Path) -> set[str]:
    """Read the set of image filenames a ground-truth file describes.

    Accepts the two shapes `eval-set` writes -- a wide CSV and a per-record JSONL
    -- without pandas, so this stays runnable on a bare interpreter.

    Args:
        path: A ground_truth.csv or ground_truth.jsonl.

    Returns:
        Every image filename the file names.

    Raises:
        ValueError: The file carries no recognisable identifier column.
    """
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        import csv

        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(
            _diagnostic(
                what=f"the ground-truth file {path} is empty.",
                where=str(path),
                example="at least one row naming an image.",
                fix="regenerate it with `python -m generators.pipeline eval-set --out <dir>`.",
            )
        )

    column = next((c for c in _ID_COLUMNS if c in rows[0]), None)
    if column is None:
        raise ValueError(
            _diagnostic(
                what=f"no image-identifier column in {path}; found {sorted(rows[0])[:6]}.",
                where=str(path),
                example=f"a column named one of {list(_ID_COLUMNS)}.",
                fix=f"rename the filename column to '{_ID_COLUMNS[0]}'.",
            )
        )
    return {str(row[column]) for row in rows}


def load_extraction_records(path: Path) -> tuple[set[str], int]:
    """Read the image names a raw-extractions file reports, and its error count.

    Args:
        path: A raw_extractions.jsonl.

    Returns:
        (image names, number of records carrying an `error`).
    """
    names: set[str] = set()
    errors = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("error"):
            errors += 1
        name = record.get("image_name") or record.get("filename")
        if name:
            names.add(str(name))
    return names, errors


def report(extraction_names: set[str], gt_names: set[str], errors: int) -> bool:
    """Print the comparison and say whether it is safe to score.

    Args:
        extraction_names: Images the model processed.
        gt_names: Images the ground truth describes.
        errors: Records that came back carrying an error.

    Returns:
        True when every extracted image has ground truth.
    """
    matched = extraction_names & gt_names
    unmatched = extraction_names - gt_names
    unscored = gt_names - extraction_names

    print(f"extracted images : {len(extraction_names)}")
    print(f"ground truth rows: {len(gt_names)}")
    print(f"matched          : {len(matched)}")
    if extraction_names:
        print(f"coverage         : {len(matched) / len(extraction_names):.1%} of extracted images")
    if errors:
        print(f"errored records  : {errors} (the notebook skips these)")

    if unmatched:
        print(f"\n{len(unmatched)} extracted image(s) have NO ground truth, e.g.:")
        for name in sorted(unmatched)[:5]:
            print(f"    {name}")
    if unscored:
        print(f"\n{len(unscored)} ground-truth row(s) were never extracted, e.g.:")
        for name in sorted(unscored)[:5]:
            print(f"    {name}")

    return not unmatched


def main() -> int:
    """Compare the two files and return a shell exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractions", type=Path, required=True, help="raw_extractions.jsonl")
    parser.add_argument("--ground-truth", type=Path, required=True, help="ground_truth.csv or .jsonl")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="exit 0 even when some extracted images have no ground truth.",
    )
    args = parser.parse_args()

    for path, label in ((args.extractions, "--extractions"), (args.ground_truth, "--ground-truth")):
        if not path.is_file():
            print(
                _diagnostic(
                    what=f"no file at {path}.",
                    where=label,
                    example="a path to an existing file.",
                    fix="check the transfer from the extraction host completed.",
                ),
                file=sys.stderr,
            )
            return 1

    extraction_names, errors = load_extraction_records(args.extractions)
    gt_names = load_ground_truth_names(args.ground_truth)
    ok = report(extraction_names, gt_names, errors)

    if ok:
        print("\nPASS — every extracted image has ground truth. Safe to score.")
        return 0
    if args.allow_partial:
        print("\nPARTIAL — proceeding because --allow-partial was passed.")
        return 0

    print(
        "\n"
        + _diagnostic(
            what="some extracted images have no ground truth, so scoring them would "
            "silently drop those documents.",
            where=f"{args.extractions} vs {args.ground_truth}",
            example="both files describing the same export.",
            fix="score against the ground_truth.csv that sits BESIDE the images the model "
            "was given; if the corpus was reseeded after the run, re-extract rather than "
            "re-pairing. Pass --allow-partial only for a deliberate subset.",
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
