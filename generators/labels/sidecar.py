"""The label styles sidecar: what each labelled_varied page printed for its ABN, GST and total labels.

`generate --distractors --label-variation` writes one `label_styles.jsonl` into each document type's
labelled_varied directory, one row per image, so `generate --type receipts` never erases the other types'
rows (the half's single sidecar is assembled later, at export). A row records the page's choices as the
render recorded them at draw time, and the refusals met on the way; it carries nothing from the ground
truth beyond the case id, and `derive`, `eval-set` and the extraction ground-truth script never read it.
"""

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from generators.labels.render import LabelOutcome

# One file per type directory, beside the images it describes.
SIDECAR_NAME = "label_styles.jsonl"
# Every key of a row, in the sorted order they are written.
ROW_KEYS = ("case_id", "doc_type", "image_file", "layout", "refusals", "styles")
REFUSAL_KEYS = ("field", "rule", "style", "wording")


def sidecar_row(
    *, image_file: str, case_id: str, doc_type: str, layout_id: str, outcome: LabelOutcome, geometry: dict
) -> dict:
    """Return one image's sidecar row.

    `styles` holds every varied field whose label site the page drew, with the style chosen and the label
    the site recorded at draw time (surrounding whitespace trimmed; `""` when unlabelled), so the row says
    what the page shows rather than what was asked for. A bank statement varies nothing: `{}`.

    Args:
        image_file: The image's filename, shared with the plain and distractor renders.
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `receipt`.
        layout_id: The entry's layout.
        outcome: The accepted choice and the refusals met, from `render_labelled_varied`.
        geometry: The accepted render's geometry, whose `labels` records each drawn site's label.

    Returns:
        The row, ready to write.
    """
    drawn = geometry.get("labels", {})
    styles = {
        field: {"style": pick.style, "wording": drawn[field][0].strip()}
        for field, pick in outcome.choice.items()
        if field in drawn
    }
    refusals = [
        {"field": r.field, "rule": r.rule, "style": r.style, "wording": r.wording.strip()}
        for r in outcome.refusals
    ]
    return {
        "case_id": case_id,
        "doc_type": doc_type,
        "image_file": image_file,
        "layout": layout_id,
        "refusals": refusals,
        "styles": styles,
    }


def write_sidecar(directory: Path, rows: Iterable[dict]) -> Path:
    """Write a type directory's `label_styles.jsonl`, one row per line with its keys sorted.

    Args:
        directory: The type's labelled_varied image directory.
        rows: Its rows, one per image, in render order.

    Returns:
        The written file.
    """
    path = directory / SIDECAR_NAME
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def read_sidecar(directory: Path) -> list[dict]:
    """Read a type directory's sidecar rows back, in file order."""
    return [json.loads(line) for line in (directory / SIDECAR_NAME).read_text().splitlines()]


def summarise(rows: Iterable[dict]) -> tuple[Counter, Counter]:
    """Count a type's realised styles and refusals.

    Args:
        rows: The type's sidecar rows.

    Returns:
        (Counter of (field, style), Counter of refusal rule).
    """
    styles: Counter = Counter()
    rules: Counter = Counter()
    for row in rows:
        styles.update((field, pick["style"]) for field, pick in row["styles"].items())
        rules.update(refusal["rule"] for refusal in row["refusals"])
    return styles, rules
