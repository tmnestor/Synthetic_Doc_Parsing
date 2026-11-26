#!/usr/bin/env python3
"""Run the pinned OmniDocBench evaluator on pages that hold no formula, with four faults put right.

    python omnidocbench_formula_free.py --checkout <OmniDocBench checkout> --config end2end.yaml

The evaluator was written for papers and books. On business documents, which hold tables of amounts and
no formula, four of its behaviours mark a transcription down for text it read correctly, or charge it
for a missing line by an amount that depends on unrelated text. All four are in how the evaluator reads
a prediction; none is in the ground truth. This launcher puts each right in memory and leaves the
checkout untouched:

1. **The first table of a page drops out of the predicted reading order** (OmniDocBench issue #229), one
   edit on every page with a table even for a perfect transcription. Fixed by
   `omnidocbench_corrected.py`, which this launcher calls.
2. **A pipe table is misread**: a cell holding a colon is dropped, the row after one ending in a space is
   lost, a table without outer pipes is not seen, and a pipe table's cells come back with attributes an
   html table's lack, so its table edit distance is not comparable. Fixed by rewriting each pipe table as
   the one-line html table the evaluator reads without loss (`pipe_tables.py`) before the
   evaluator sees the page.
3. **Text between two dollar signs is taken for a formula, and so is a table cell that looks like one**
   (a date, for instance). The evaluator then pairs a ground-truth line the transcription left out with
   one of these fragments, and charges for the fragment's length, not for the missing line. Fixed by
   dropping every formula item the evaluator makes from a prediction. That is right only where the ground
   truth holds no formula, so the launcher refuses ground truth that holds one.
4. **A run of underscores or spaces in a prediction is cut to four characters, and the same run in the
   ground truth is not**, so a line that ends in a rule to write on is charged for the underscores it
   transcribed correctly. Fixed by leaving a prediction's repeated characters as they were written.

The fixes wrap `md_tex_filter`, the function through which every prediction enters the evaluator,
replace the function it shortens repeated characters with, and replace the table matcher's two faulty
conditions. The launcher refuses a `md_tex_filter` that returns a category it does not know or that
does not hold the shortening function, and `omnidocbench_corrected.py` refuses a matcher that is not the
code its fix was written for, so none is applied blindly to another evaluator commit.

One consequence of the third fix: text a transcription wraps in LaTeX display delimiters (`\\[ \\]` or
`$$ $$`) is taken out of the page by the evaluator as a formula, and so is not scored as text.

This file, `omnidocbench_corrected.py` and `pipe_tables.py` must sit in one directory. They need only the
evaluator's own environment, and no syntax newer than Python 3.9.
"""

import argparse
import json
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    # Run as the launcher, this file imports the two beside it without leaving a bytecode cache next to
    # them: where the three are shipped in a verified folder, any file the folder's manifest does not list
    # is reported as a fault.
    sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import omnidocbench_corrected
import yaml
from pipe_tables import pipe_tables_to_html

FILTER = "md_tex_filter"
# The function `md_tex_filter` calls to cut a prediction's runs of underscores and spaces to four.
SHORTENER = "replace_repeated_chars"
# Every category the pinned `md_tex_filter` files a prediction's items under, and those made from formulas.
KNOWN_CATEGORIES = frozenset(
    {"text_all", "html_table", "latex_table", "equation_isolated", "formula_rescue"}
)
FORMULA_CATEGORIES = frozenset({"equation_isolated", "formula_rescue"})
# A text item the evaluator makes from the words beside a formula: it repeats text the page already holds.
FORMULA_TEXT = "equation_caption"
# What marks a formula in OmniDocBench ground truth: a block's category, or a span inside a line of text.
_GROUND_TRUTH_FORMULAS = ("equation_isolated", "equation_inline")


def without_formula_items(dataset: dict) -> dict:
    """A prediction's items, as `md_tex_filter` returns them, with every item made from a formula removed.

    Args:
        dataset: The items by category.

    Returns:
        The same mapping without the formula categories and without text made from a formula's margin.

    Raises:
        RuntimeError: The mapping holds a category this launcher does not know.
    """
    unknown = sorted(set(dataset) - KNOWN_CATEGORIES)
    if unknown:
        raise RuntimeError(
            "The evaluator filed a prediction's items under a category this launcher does not know.\n"
            f"  What: {FILTER} returned {unknown}, which may hold formula items under another name.\n"
            f"  Where: src/core/preprocess/extract.py -> {FILTER}, in the evaluator checkout\n"
            f"  Expected: only {sorted(KNOWN_CATEGORIES)}, as the pinned commit returns.\n"
            "  Recover: check the checkout is at the pinned commit; for another commit, read what the new "
            "category holds and add it to KNOWN_CATEGORIES, and to FORMULA_CATEGORIES if it holds formulas."
        )
    for category in FORMULA_CATEGORIES:
        dataset.pop(category, None)
    if "text_all" in dataset:
        dataset["text_all"] = [
            item for item in dataset["text_all"] if item.get("fine_category_type") != FORMULA_TEXT
        ]
    return dataset


def formula_free(md_tex_filter):
    """Wrap the evaluator's `md_tex_filter`: pipe tables go in as html, and no formula item comes out."""

    def filtered(content):
        return without_formula_items(md_tex_filter(pipe_tables_to_html(content)))

    return filtered


def refuse_ground_truth_with_formulas(config: Path) -> None:
    """Refuse an end-to-end config whose ground truth holds a formula.

    Args:
        config: The evaluator's end-to-end config, naming the ground-truth file.

    Raises:
        ValueError: A page of the ground truth holds a formula block or an inline formula.
    """
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    ground_truth = Path(settings["end2end_eval"]["dataset"]["ground_truth"]["data_path"])
    for page in json.loads(ground_truth.read_text(encoding="utf-8")):
        for block in page["layout_dets"]:
            spans = [span.get("category_type") for span in block.get("line_with_spans") or []]
            if block.get("category_type") in _GROUND_TRUTH_FORMULAS or "equation_inline" in spans:
                raise ValueError(
                    "The ground truth holds a formula, so formula items cannot be dropped from predictions.\n"
                    f"  What: page {page['page_info']['image_path']} has a block or span of category "
                    f"{_GROUND_TRUTH_FORMULAS[0]} or {_GROUND_TRUTH_FORMULAS[1]}.\n"
                    f"  Where: {ground_truth}, named by {config} -> "
                    "end2end_eval.dataset.ground_truth.data_path\n"
                    "  Expected: ground truth with no formula on any page, such as this benchmark's "
                    "parsing/<set>.json.\n"
                    "  Recover: score ground truth that holds formulas with omnidocbench_corrected.py "
                    "instead, which fixes the reading-order fault alone."
                )


def keep_repeated_characters(extract) -> None:
    """Stop `md_tex_filter` shortening a prediction's repeated characters.

    Args:
        extract: The evaluator's `src.core.preprocess.extract` module, whose `md_tex_filter` calls the
            shortening function by its name in that module.

    Raises:
        RuntimeError: The module holds no such function, so this is not the code the fix was written for.
    """
    if not callable(getattr(extract, SHORTENER, None)):
        raise RuntimeError(  # noqa: TRY004  (another evaluator commit, not an argument of the wrong type)
            "The evaluator does not shorten repeated characters where this launcher expects.\n"
            f"  What: the module holds no function named {SHORTENER}.\n"
            f"  Where: src/core/preprocess/extract.py, in the evaluator checkout\n"
            f"  Expected: {FILTER} calling {SHORTENER}(content), as at the pinned commit.\n"
            "  Recover: check the checkout is at the pinned commit; for another commit, read how it treats "
            "runs of underscores in a prediction and in the ground truth before scoring."
        )
    setattr(extract, SHORTENER, lambda content: content)


def install_formula_free_filter() -> None:
    """Rebind the wrapped `md_tex_filter` in every evaluator module that holds the evaluator's own."""
    from src.core.preprocess import extract
    from src.dataset import end2end_dataset  # noqa: F401  (loads its binding)

    keep_repeated_characters(extract)
    original = getattr(extract, FILTER)
    wrapped = formula_free(original)
    holders = [module for module in list(sys.modules.values()) if getattr(module, FILTER, None) is original]
    for module in holders:
        setattr(module, FILTER, wrapped)
    if not any(module.__name__ == "src.dataset.end2end_dataset" for module in holders):
        raise RuntimeError(
            f"{FILTER} was not found where the evaluator reads predictions; refusing to score."
        )


def main(argv=None) -> int:
    """Command-line entry point: put the three faults right in memory, then run the evaluator."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkout", type=Path, required=True, help="The pinned OmniDocBench checkout")
    parser.add_argument("--config", type=Path, required=True, help="The end-to-end config to run")
    args = parser.parse_args(argv)
    refuse_ground_truth_with_formulas(args.config)
    checkout = args.checkout.resolve()
    omnidocbench_corrected.install_fix(checkout)
    install_formula_free_filter()
    script = checkout / "pdf_validation.py"
    sys.argv = [str(script), "--config", str(args.config)]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
