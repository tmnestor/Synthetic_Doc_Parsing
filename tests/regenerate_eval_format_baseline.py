"""Re-capture tests/fixtures/eval_format_baseline.json. Dry run unless --confirm.

Exports a fresh evaluation set into a scratch directory, captures its SHAPE (filenames,
counts, CSV columns, JSONL key order; never values) and reports how it differs from the
pinned fixture. Pass --confirm to overwrite the fixture, which should be a deliberate act:
a re-bless accepts the current exporter's shape as the contract.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval_export_tools import BASELINE_PATH, capture_shape, export_into  # noqa: E402

COMMENT = (
    "Pins the SHAPE of an evaluation-set export (filenames, counts, CSV columns, JSONL key order), "
    "never values. Re-captured from the current exporter on 6 October 2026, because history before "
    "the test-suite rebuild is not preserved (owner's decision). The independent cross-check is "
    "test_fixture_describes_the_extraction_schema, which derives the same lists from "
    "config/extraction_schema.yml and the ground truth. Re-bless with "
    "`python tests/regenerate_eval_format_baseline.py --confirm`."
)


def differences(old: dict, new: dict) -> list[str]:
    """List the top-level keys whose values differ between two shapes."""
    return [
        key for key in sorted(set(old) | set(new)) if key != "_comment" and old.get(key) != new.get(key)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="write the fixture")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        summary = export_into(Path(scratch))
        shape = capture_shape(Path(summary["clean_dir"]))
    old = json.loads(BASELINE_PATH.read_text()) if BASELINE_PATH.exists() else {}
    changed = differences(old, shape)
    print(f"{shape['image_count']} images captured; differing keys: {changed or 'none'}")
    if not args.confirm:
        print("Dry run: nothing written. Pass --confirm to write.")
        return
    BASELINE_PATH.write_text(json.dumps({"_comment": COMMENT, **shape}, indent=2) + "\n")
    print(f"Wrote {BASELINE_PATH}")


if __name__ == "__main__":
    main()
