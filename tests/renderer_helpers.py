"""Shared loaders for the renderer, fit and geometry tests.

Reads the real ground truth and layout registries once per process and hands out
entries ready to render. `entry_per_layout` returns one representative entry for
every layout the registry holds, so a test that must cover every layout iterates
layouts (not cases) and picks up a layout added later without a code change.
"""

import functools
from collections.abc import Callable
from pathlib import Path

import yaml
from PIL import Image

from generators.loader import load_ground_truth, load_layout_registry
from tests.conftest import REPO_ROOT
from tests.snapshot_tools import DOC_TYPES

Renderer = Callable[..., Image.Image]


@functools.cache
def _load(doc_type: str) -> tuple[dict, dict]:
    stem, _ = DOC_TYPES[doc_type]
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / f"{stem}.yml")
    layouts = load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml")
    return entries, layouts


def layouts_for(doc_type: str) -> dict:
    """Return the layout registry for a document type (shared; do not mutate)."""
    return _load(doc_type)[1]


def renderer_for(doc_type: str) -> Renderer:
    """Return the renderer function for a document type."""
    return DOC_TYPES[doc_type][1]


def layout_path_for(doc_type: str) -> str:
    """Return the repo-relative layout YAML path for a document type."""
    return f"config/layouts/{DOC_TYPES[doc_type][0]}.yml"


def corpus_entries(doc_type: str) -> dict[str, dict]:
    """Return every ground-truth entry of a type, each carrying its `case_id`."""
    entries, _ = _load(doc_type)
    return {str(cid): {**raw, "case_id": str(cid)} for cid, raw in entries.items()}


def entry_for(doc_type: str, case_id: str) -> dict:
    """Return one ground-truth entry with its `case_id` set."""
    return corpus_entries(doc_type)[case_id]


def entry_per_layout(doc_type: str) -> dict[str, dict]:
    """Return one entry for every registered layout of a type.

    Uses the first ground-truth entry that names the layout. A layout no entry
    uses yet borrows the type's first entry with `layout` re-pointed at it, so
    it is still rendered.
    """
    entries = corpus_entries(doc_type)
    chosen: dict[str, dict] = {}
    for layout_id in layouts_for(doc_type):
        found = next((e for e in entries.values() if e["layout"] == layout_id), None)
        if found is None:
            found = {**next(iter(entries.values())), "layout": layout_id}
        chosen[layout_id] = found
    return chosen


def render_entry(doc_type: str, entry: dict, *, distractors: bool, **kwargs: object) -> Image.Image:
    """Render an entry with its own layout from the real registry."""
    return renderer_for(doc_type)(
        entry, layouts_for(doc_type)[entry["layout"]], distractors=distractors, **kwargs
    )


def write_single_type_config(tmp_path: Path, stem: str, layouts: dict) -> Path:
    """Write a generation config for one document type over a custom layout registry.

    The ground truth is the real file for that type; the layouts and the output
    directories live under `tmp_path`, so nothing is written inside the repository.

    Args:
        tmp_path: Scratch directory.
        stem: `bank_statements`, `receipts` or `invoices`.
        layouts: Layout registry to write (layout id -> layout dict).

    Returns:
        Path of the generation config.
    """
    layout_file = tmp_path / f"{stem}_layouts.yml"
    layout_file.write_text(yaml.safe_dump({"layouts": layouts}))
    config = {
        "output_dir": str(tmp_path / "output"),
        "derived_dir": str(tmp_path / "derived"),
        "ground_truth_dir": str(REPO_ROOT / "ground_truth"),
        "distractor_output_subdir": "distractor",
        "labelled_varied_output_subdir": "labelled_varied",
        "document_types": {
            stem: {
                "ground_truth": str(REPO_ROOT / "ground_truth" / f"{stem}.yml"),
                "layouts": str(layout_file),
                "output_subdir": stem,
                "generate_clean": True,
            }
        },
    }
    config_path = tmp_path / "generation_config.yml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


def pixels_equal(a: Image.Image, b: Image.Image) -> bool:
    """True when two images have the same size and identical RGB pixels."""
    return a.size == b.size and a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
