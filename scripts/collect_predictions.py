#!/usr/bin/env python3
"""Turn one tool's raw output for one run into the evaluator's predictions: one `<image stem>.md` per page.

    python scripts/collect_predictions.py --tool docling --half plain

Each tool nests its markdown differently (MinerU `<stem>/<method>/<stem>.md`, Docling `<stem>.md`, Marker
`<stem>/<stem>.md`), so the collector does not depend on the nesting: for every page the run's `run.json`
lists, it finds the one file named `<image stem>.md` anywhere under
`<eval_set.output_root>/raw_predictions/<tool>/<half>/`, and copies it to
`<eval_set.output_root>/predictions/<tool>/<half>/<image stem>.md`, the name the evaluator pairs with the
page `<image stem>.png`. A page with no such file, or with two (a rerun with another method, say), is
refused before anything is written, and every such page is named. A second collection replaces the first,
so the predictions directory never holds a file from an earlier run.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.snapshot.config import load_snapshot_config  # noqa: E402
from scripts.run_local_parser import RAW_DIR, RUN_RECORD  # noqa: E402

PREDICTIONS_DIR = "predictions"


class CollectError(RuntimeError):
    """A run's raw output does not give exactly one markdown file per page."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> CollectError:
    """Build a four-part refusal, each part on a labelled line."""
    return CollectError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _pages(raw: Path) -> list[str]:
    """The image file names the run's record lists."""
    record = raw / RUN_RECORD
    try:
        pages = json.loads(record.read_text())["pages"]
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise _diagnostic(
            "The run has no readable record.",
            what=f"{record} is missing or holds no 'pages' list ({err!r}), so the pages the run covered "
            "are unknown.",
            where=f"{record}",
            expected='the run.json scripts/run_local_parser.py writes, e.g. {"pages": ["CASE001_invoice.png"]}',
            recover="run the tool with scripts/run_local_parser.py, which writes the record beside its output.",
        ) from err
    if not isinstance(pages, list) or not all(isinstance(page, str) for page in pages):
        raise _diagnostic(
            "The run's record lists no pages.",
            what=f"'pages' in {record} is {pages!r}, not a list of image file names.",
            where=f"{record} -> pages",
            expected='a list of image file names, e.g. "pages": ["CASE001_invoice.png"]',
            recover="run the tool again with scripts/run_local_parser.py.",
        )
    return pages


def collect(tool: str, half: str, *, output_root: Path) -> Path:
    """Copy each page's markdown from a run's raw output into the predictions directory.

    Args:
        tool: The tool's name.
        half: The run's name under `raw_predictions/<tool>/`, normally the half.
        output_root: The directory holding `raw_predictions/` and `predictions/`.

    Returns:
        The predictions directory, `<output_root>/predictions/<tool>/<half>/`.

    Raises:
        CollectError: The run has no record, or a page has no markdown file or more than one.
    """
    raw = output_root / RAW_DIR / tool / half
    stems = [Path(page).stem for page in _pages(raw)]
    found: dict[str, list[Path]] = {stem: [] for stem in stems}
    for path in raw.rglob("*.md"):
        if path.stem in found:
            found[path.stem].append(path)
    missing = [stem for stem, paths in found.items() if not paths]
    doubled = {stem: sorted(paths) for stem, paths in found.items() if len(paths) > 1}
    if missing or doubled:
        problems = []
        if missing:
            problems.append(f"{len(missing)} page(s) with no markdown: {missing}")
        for stem, paths in doubled.items():
            problems.append(f"page {stem} has {len(paths)} candidates: {[str(path) for path in paths]}")
        raise _diagnostic(
            "The run does not give exactly one markdown file per page.",
            what="; ".join(problems),
            where=f"{raw}",
            expected="one file named <image stem>.md under the run's raw output for every page run.json lists.",
            recover="for a missing page read the tool's run.log beside run.json; for two candidates remove the "
            "stale one (or move the run aside and run the tool again).",
        )
    target = output_root / PREDICTIONS_DIR / tool / half
    staging = target.with_name(f".{half}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for stem, (source,) in found.items():
        shutil.copyfile(source, staging / f"{stem}.md")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return target


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", required=True, help="The tool's name, as in tools in the parsing config")
    parser.add_argument("--half", required=True, help="The run's name under raw_predictions/<tool>/")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    args = parser.parse_args(argv)
    try:
        output_root = load_snapshot_config(args.config).output_root
        target = collect(args.tool, args.half, output_root=output_root)
    except (CollectError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{sum(1 for _ in target.glob('*.md'))} predictions: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
