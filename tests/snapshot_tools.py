"""Render the whole corpus and reduce each page to a comparable record."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from generators.bank_statement import render_bank_statement
from generators.invoice import render_invoice
from generators.labels.config import load_label_config
from generators.labels.render import render_labelled_varied
from generators.labels.sidecar import sidecar_row
from generators.loader import load_ground_truth, load_layout_registry
from generators.receipt import render_receipt

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "pixel_snapshot.json"
# Every receipt's, invoice's and bank statement's render with its selected distractors drawn, a paid
# invoice's PAID stamp (the overlay) included; bank statements joined with their inline items (Stage 5).
DISTRACTOR_SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "distractor_pixel_snapshot.json"
DISTRACTOR_DOC_TYPES = ("receipt", "invoice", "bank_statement")
# Every page's labelled varied render (Stage 6): its distractor page with the case's ABN, GST and total
# label styles, each record also pinning the choices drawn and the refusals met. Bank statements vary no
# label, so theirs equal their distractor pins.
LABELLED_VARIED_SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "labelled_varied_pixel_snapshot.json"
EXPECTED_PER_TYPE = 55
DOC_TYPES: dict[str, tuple[str, Callable[..., Image.Image]]] = {
    "receipt": ("receipts", render_receipt),
    "invoice": ("invoices", render_invoice),
    "bank_statement": ("bank_statements", render_bank_statement),
}


def digest(image: Image.Image) -> str:
    """Hash a page's RGB pixels; PNG encoding is deliberately excluded."""
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def render_corpus(
    doc_types: tuple[str, ...] = tuple(DOC_TYPES), *, distractors: bool = False, labels: bool = False
) -> dict[str, dict]:
    """Render every entry of the given types straight from ground truth.

    Renders directly rather than through `pipeline generate`, which skips an
    entry whose layout is missing; here a missing layout raises KeyError.
    `distractors` renders each page with its selected distractors drawn; the
    pinned scored boxes are then the ones that render records. `labels` (with
    `distractors`) renders each page labelled varied, and each record also
    carries the page's `choices` (its label styles sidecar row's `styles` and
    `refusals`).
    """
    if labels and not distractors:
        msg = "a labelled varied render is a distractor render: pass distractors=True with labels=True"
        raise ValueError(msg)
    config = load_label_config() if labels else None
    records: dict[str, dict] = {}
    for doc_type in doc_types:
        stem, render = DOC_TYPES[doc_type]
        entries = load_ground_truth(REPO_ROOT / "ground_truth" / f"{stem}.yml")
        layouts = load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml")
        for case_id, raw in entries.items():
            entry = {**raw, "case_id": str(case_id)}
            geometry: dict = {}
            key = f"{case_id}_{entry['layout']}"
            choices = None
            if config is not None:
                image, outcome = render_labelled_varied(
                    render,
                    entry,
                    layouts[entry["layout"]],
                    doc_type=doc_type,
                    config=config,
                    geometry_out=geometry,
                )
                row = sidecar_row(
                    image_file=f"{key}.png",
                    case_id=str(case_id),
                    doc_type=doc_type,
                    layout_id=entry["layout"],
                    outcome=outcome,
                    geometry=geometry,
                )
                choices = {"styles": row["styles"], "refusals": row["refusals"]}
            else:
                image = render(
                    entry, layouts[entry["layout"]], distractors=distractors, geometry_out=geometry
                )
            records[key] = {
                "type": doc_type,
                "hash": digest(image),
                "size": [image.width, image.height],
                "boxes": json.loads(json.dumps(geometry["boxes"])),
            }
            if choices is not None:
                records[key]["choices"] = choices
    return records


def compare(pinned: dict[str, dict], current: dict[str, dict]) -> list[str]:
    """List every difference between a pinned and a current snapshot."""
    problems: list[str] = []
    for key in sorted(set(pinned) | set(current)):
        if key not in current:
            problems.append(f"{key}: pinned but no longer rendered")
        elif key not in pinned:
            problems.append(f"{key}: rendered but not pinned")
        else:
            problems += [
                f"{key}: {part} differs"
                for part in ("hash", "size", "boxes", "choices")
                if pinned[key].get(part) != current[key].get(part)
            ]
    return problems


def merge_types(
    pinned: dict[str, dict], rendered: dict[str, dict], doc_types: tuple[str, ...]
) -> dict[str, dict]:
    """A snapshot whose `doc_types` entries are `rendered` and every other type's are `pinned`, unchanged.

    Re-blessing one document type (invoices, after the PAID stamp) must leave every other type's pins
    exactly as they were; this is how the regeneration script keeps them.
    """
    kept = {key: record for key, record in pinned.items() if record["type"] not in doc_types}
    stray = sorted(key for key, record in rendered.items() if record["type"] not in doc_types)
    if stray:
        msg = f"rendered entries {stray[:3]} are not of {doc_types}; render only the types being re-blessed"
        raise ValueError(msg)
    return {**kept, **rendered}


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, dict]:
    """Read a pinned snapshot, the plain one unless `path` names another."""
    return json.loads(path.read_text())
