#!/usr/bin/env python3
"""Check the committed ground truth is exactly what the seed scripts produce.

    python scripts/check_seed_reproducibility.py

`scripts/seed_ground_truth.py` then `scripts/seed_transaction_links.py` — the
second rewriting what the first wrote — must regenerate the four committed
`ground_truth/*.yml` files byte-identically. This runs that pair against an
isolated copy of the tree and compares, so the real corpus is never touched.

Why this needs its own check: nothing else notices. The pixel snapshots and the
derived-baseline tests render from the committed YAML rather than from a reseed,
so an edit to either seed script can break reproducibility while every other
check stays green.

Note that the fixed point belongs to the PAIR, in order. Running
`seed_transaction_links.py` alone against the committed corpus feeds it an
already-linked corpus — a different input from the one it is written for — and
produces a different, self-consistent result. That is not a reproducibility
failure; it is half a pipeline.

Exits 0 when the corpus matches, 1 when it has drifted or a seed script fails.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The subset of the tree the two seed scripts need: generators/ (content engine,
# schema, loader, overflow check, common — including its
# generators/exporters/geometry.py dependency), config/ (data_pools.yml,
# field_definitions.yml, layouts/), fonts/ (generators/common.py resolves
# _BUNDLED_FONTS_DIR relative to its own file), and scripts/ itself.
_COPY_SUBTREES = ("generators", "config", "fonts", "scripts")

_SEED_SCRIPTS = ("scripts/seed_ground_truth.py", "scripts/seed_transaction_links.py")

_SEEDED_PATHS = (
    "ground_truth/bank_statements.yml",
    "ground_truth/receipts.yml",
    "ground_truth/invoices.yml",
    "ground_truth/transaction_links.yml",
)


def _diagnostic(what: str, where: str, expected: str, fix: str) -> str:
    """Assemble a 4-element diagnostic message."""
    return f"What: {what}\nWhere: {where}\nExpected: {expected}\nHow to fix: {fix}"


def copy_isolated_tree(dest: Path) -> None:
    """Copy the subtrees the seed scripts read into an empty working directory.

    Args:
        dest: Directory to build the isolated copy in.
    """
    for name in _COPY_SUBTREES:
        shutil.copytree(
            _REPO_ROOT / name,
            dest / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (dest / "ground_truth").mkdir()


def run_seed_scripts(work_dir: Path) -> str | None:
    """Run both seed scripts in order inside the isolated copy.

    Args:
        work_dir: The isolated tree, holding an empty ground_truth/.

    Returns:
        None on success, or a diagnostic describing the first failure.
    """
    for script in _SEED_SCRIPTS:
        result = subprocess.run(  # noqa: S603
            [sys.executable, script],
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return _diagnostic(
                what=f"{script} exited {result.returncode} in an isolated copy of the tree.",
                where=f"{work_dir}\nstdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}",
                expected="both seed scripts to run cleanly against an empty ground_truth/.",
                fix="fix the seed script; the committed corpus is untouched by this check.",
            )
    return None


def compare(work_dir: Path) -> list[str]:
    """Compare each regenerated file against the committed one.

    Args:
        work_dir: The isolated tree, after both seed scripts have run.

    Returns:
        The relative paths that differ, in declaration order.
    """
    drifted = []
    for rel_path in _SEEDED_PATHS:
        committed = (_REPO_ROOT / rel_path).read_text()
        regenerated = (work_dir / rel_path).read_text()
        if regenerated != committed:
            drifted.append(rel_path)
    return drifted


def main() -> int:
    """Run the check, returning a shell exit status."""
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "repo_copy"
        copy_isolated_tree(work_dir)

        failure = run_seed_scripts(work_dir)
        if failure:
            print(failure, file=sys.stderr)
            return 1

        drifted = compare(work_dir)

    if not drifted:
        print(f"PASS — all {len(_SEEDED_PATHS)} ground-truth files reproduce byte-identically.")
        return 0

    print(
        _diagnostic(
            what=f"{len(drifted)} committed ground-truth file(s) are not what the seed "
            f"scripts produce: {', '.join(drifted)}.",
            where="ground_truth/, against a reseed in an isolated copy of the tree.",
            expected="the committed corpus to be exactly the output of "
            "seed_ground_truth.py followed by seed_transaction_links.py.",
            fix="if a seed script changed deliberately, re-run both in order and commit "
            "the result, then re-capture the content-pinned baselines the reseed "
            "invalidates. If not, the corpus was hand-edited — restore it from git.",
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
