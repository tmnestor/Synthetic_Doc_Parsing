"""Compare a typeset page with stage one's page of the same case and mode, block by block.

A typeset page carries stage one's block records, so the two pages' OmniDocBench blocks pair up by id.
`stage_one_differences` names every block whose category, text or table html differs once whitespace is
collapsed. Overlay blocks are compared as every other block is: their text is what stage one recorded for
the marks the typeset page places as images.
"""

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.omnidocbench import PageContext, build_page


def _collapsed(text: str) -> str:
    """Collapse every run of whitespace to one space."""
    return " ".join(text.split())


def stage_one_differences(page: dict, stage_one: dict, *, context: PageContext) -> list[str]:
    """Compare the typeset page block by block with stage one's page of the same case and mode.

    Blocks are matched by id (`anno_id`); the text of a block, or the html of a table, is compared with
    whitespace collapsed. Overlay blocks are included.

    Args:
        page: The OmniDocBench page built from the read-back geometry.
        stage_one: The geometry of stage one's own render of the same case and mode.
        context: The page's context, reused for stage one's page.

    Returns:
        One line per block that differs, is missing, or is extra; empty when the two agree.
    """
    parsing = load_parsing_config(PARSING_CONFIG_PATH)
    theirs = {
        det["anno_id"]: det for det in build_page(stage_one, context=context, config=parsing)["layout_dets"]
    }
    ours = {det["anno_id"]: det for det in page["layout_dets"]}
    differences = []
    for block in sorted(set(theirs) | set(ours)):
        one, two = theirs.get(block), ours.get(block)
        if one is None or two is None:
            where = "stage one" if one is None else "the typeset page"
            differences.append(f"block {block} is missing from {where}")
            continue
        if one["category_type"] != two["category_type"]:
            differences.append(
                f"block {block}: category {two['category_type']!r}, stage one {one['category_type']!r}"
            )
        key = "html" if "html" in one or "html" in two else "text"
        mine, wanted = _collapsed(two.get(key, "")), _collapsed(one.get(key, ""))
        if mine != wanted:
            differences.append(f"block {block}: {key} {mine!r}, stage one {wanted!r}")
    return differences
