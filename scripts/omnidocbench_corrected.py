#!/usr/bin/env python3
"""Run the pinned OmniDocBench evaluator with its reading-order fault in table matching fixed in memory.

    python scripts/omnidocbench_corrected.py --checkout <OmniDocBench checkout> --config end2end.yaml

At the pinned commit (and on upstream main), `match_gt2pred_simple` in `src/core/matching/match.py`
records a matched prediction's position and category only when its index is truthy:

    'pred_category_type': get_pred_category_type(pred_idx, pred_items) if pred_idx else "",
    'pred_position': pred_items[pred_idx]['position'][0] if pred_idx else "",

so a table matched to prediction index 0, the first predicted table of a page, loses its position and
drops out of the predicted reading order while its ground truth stays in: one edit on every such page,
even for a perfect prediction. OmniDocBench issue #229 reports it and a maintainer confirmed it; PR #230
proposes the fix this script applies, testing against the unmatched marker instead (`pred_idx != ""`).

The checkout is not changed. This script runs in the evaluator's own env: it imports the checkout's
matching module, rewrites those two conditions in `match_gt2pred_simple` and rebinds the function
wherever the evaluator imported it, then runs the checkout's own `pdf_validation.py` with the given config,
exactly as the unmodified run does. It refuses to run unless the function holds exactly those two
conditions, so a different evaluator commit cannot be patched blindly. Only reading order can move: the
fixed fields feed the reading-order sequence alone, and the caller checks that every other score is
identical to the unmodified run's.
"""

import argparse
import inspect
import runpy
import sys
from pathlib import Path

FUNCTION = "match_gt2pred_simple"
BROKEN = 'if pred_idx else ""'
FIXED = 'if pred_idx != "" else ""'
OCCURRENCES = 2


def fix_source(source: str, *, where: str) -> str:
    """`match_gt2pred_simple`'s source with its two truthiness tests replaced by the unmatched-marker test.

    Args:
        source: The function's source, as `inspect.getsource` returns it.
        where: The file it was read from, named in a refusal.

    Returns:
        The fixed source.

    Raises:
        ValueError: The function does not hold exactly two of the faulty conditions.
    """
    found = source.count(BROKEN)
    if found != OCCURRENCES:
        raise ValueError(
            "The evaluator's table matcher is not the code this fix was written for.\n"
            f"  What: {FUNCTION} holds {found} occurrences of `{BROKEN}`, not {OCCURRENCES}.\n"
            f"  Where: {where} -> {FUNCTION}\n"
            f"  Expected: exactly {OCCURRENCES}, at the pinned commit's lines 713 and 714.\n"
            "  Recover: check the checkout is at evaluator.commit in config/parsing_config.yml; if upstream "
            "has fixed the fault, score without the corrected run."
        )
    return source.replace(BROKEN, FIXED)


def install_fix(checkout: Path) -> None:
    """Rebind the fixed `match_gt2pred_simple` in every evaluator module that holds the faulty one."""
    sys.path.insert(0, str(checkout))
    import src.core.matching as matching_package  # noqa: PLC0415  (the checkout is on sys.path only now)
    import src.core.matching.match as match_module  # noqa: PLC0415

    faulty = getattr(match_module, FUNCTION)
    namespace = match_module.__dict__
    fixed_source = fix_source(inspect.getsource(faulty), where=str(match_module.__file__))
    exec(compile(fixed_source, str(match_module.__file__), "exec"), namespace)  # noqa: S102
    fixed = namespace[FUNCTION]
    matching_package.__dict__[FUNCTION] = fixed
    import src.dataset.end2end_dataset as end2end_dataset  # noqa: PLC0415

    end2end_dataset.__dict__[FUNCTION] = fixed
    stale = [
        name for name, module in list(sys.modules.items()) if getattr(module, FUNCTION, None) is faulty
    ]
    for name in stale:
        setattr(sys.modules[name], FUNCTION, fixed)
    holders = [m for m in list(sys.modules.values()) if getattr(m, FUNCTION, None) is not None]
    if any(getattr(m, FUNCTION) is not fixed for m in holders):
        raise RuntimeError(f"{FUNCTION} is still unfixed in some evaluator module; refusing to score.")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: fix the matcher in memory, then run the checkout's pdf_validation.py."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkout", type=Path, required=True, help="The pinned OmniDocBench checkout")
    parser.add_argument("--config", type=Path, required=True, help="The end-to-end config to run")
    args = parser.parse_args(argv)
    install_fix(args.checkout.resolve())
    script = args.checkout.resolve() / "pdf_validation.py"
    sys.argv = [str(script), "--config", str(args.config)]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
