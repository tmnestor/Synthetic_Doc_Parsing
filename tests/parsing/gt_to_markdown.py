"""Turn one OmniDocBench ground-truth page into the markdown a perfect parser would return (test-only).

Blocks are written in `order`: a `title` as `# text`; a `text_block`, `header` or `footer` as a paragraph;
a `table` as its `html`; figures and `abandon` blocks are left out, as they carry no text. Blocks are
separated by a blank line. Any other category is refused, so a category added to the export later is
handled here deliberately rather than dropped.
"""

_PARAGRAPHS = ("text_block", "header", "footer")
_OMITTED = ("figure", "abandon")


def gt_to_markdown(page: dict) -> str:
    """Write `page` as markdown.

    Args:
        page: One OmniDocBench page, as `build_page` writes it.

    Returns:
        The page's blocks as markdown, in reading order.

    Raises:
        ValueError: A block's category is none this converter knows.
    """
    parts = []
    for det in sorted(page["layout_dets"], key=lambda det: det["order"]):
        category = det["category_type"]
        if category == "title":
            parts.append(f"# {det['text']}")
        elif category in _PARAGRAPHS:
            parts.append(det["text"])
        elif category == "table":
            parts.append(det["html"])
        elif category not in _OMITTED:
            raise ValueError(f"gt_to_markdown has no rule for category {category!r}")
    return "\n\n".join(parts) + "\n"
