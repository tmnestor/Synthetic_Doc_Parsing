"""Bank statements follow the brand-versus-supplier pattern.

Two defects this locks down:

1. Each bank renderer once hard-coded its letterhead brand as a Python string
   literal, shadowing the layout YAML, which is a breach of "YAML is the single
   source of truth". The header text must come from the layout's ``logo_text``
   key.
2. ``SUPPLIER_NAME`` was never drawn, so a statement whose content supplier
   differs from the letterhead brand silently contradicted the visible page. The
   supplier must be rendered as a secondary line whenever it differs from the
   drawn letterhead, and suppressed only when it matches it (to avoid
   duplication).
"""

import copy

from PIL import Image

from generators.content_engine import load_pools
from generators.layout_budgets import field_budget
from tests.conftest import REPO_ROOT
from tests.renderer_helpers import (
    corpus_entries,
    entry_per_layout,
    layout_path_for,
    layouts_for,
    pixels_equal,
    renderer_for,
)

DOC = "bank_statement"
INVENTED_SUPPLIER = "Kestrel Mutual Savings"
INVENTED_BRAND = "Zephyr Test Brand"


def reads_logo_text(node: object) -> bool:
    """True when any block, at any depth, draws its text from the layout key `logo_text`."""
    if isinstance(node, dict):
        return node.get("from_layout") == "logo_text" or any(reads_logo_text(v) for v in node.values())
    if isinstance(node, list):
        return any(reads_logo_text(v) for v in node)
    return False


def render(entry: dict, layout: dict, *, supplier: str | None = None) -> tuple[Image.Image, dict]:
    """Render with geometry, optionally overriding SUPPLIER_NAME on a copy of the entry."""
    entry = copy.deepcopy(entry)
    if supplier is not None:
        entry["fields"]["SUPPLIER_NAME"] = supplier
    geometry: dict = {}
    image = renderer_for(DOC)(entry, layout, distractors=False, geometry_out=geometry)
    return image, geometry["boxes"]


def test_header_brand_reads_from_layout_logo_text() -> None:
    """The header string must be the layout's logo_text, not a Python literal."""
    source = (REPO_ROOT / "generators" / "bank_statement.py").read_text()
    for layout_id, entry in entry_per_layout(DOC).items():
        layout = layouts_for(DOC)[layout_id]
        assert layout["logo_text"] not in source, f"{layout_id}: brand hard-coded in Python"
        assert reads_logo_text(layout["body"]), f"{layout_id}: no body block reads logo_text"
        original, _ = render(entry, layout)
        same, _ = render(entry, {**layout, "logo_text": layout["logo_text"]})
        changed, _ = render(entry, {**layout, "logo_text": INVENTED_BRAND})
        assert pixels_equal(original, same)
        assert not pixels_equal(original, changed), f"{layout_id}: header ignores logo_text"


def test_supplier_name_rendered_when_it_differs_from_bank() -> None:
    """SUPPLIER_NAME is drawn (and geometry-recorded) when it is neither the legal bank name nor the logo_text."""
    for layout_id, entry in entry_per_layout(DOC).items():
        _, boxes = render(entry, layouts_for(DOC)[layout_id], supplier=INVENTED_SUPPLIER)
        assert "SUPPLIER_NAME" in boxes, layout_id
        left, top, right, bottom = boxes["SUPPLIER_NAME"]
        assert 0.0 <= left < right <= 1.0
        assert 0.0 <= top < bottom <= 1.0


def test_supplier_name_suppressed_only_when_it_equals_drawn_logo_text() -> None:
    """The supplier line is suppressed only when already drawn verbatim as the letterhead (logo_text).

    It is never suppressed merely because it equals the legal bank name, which
    is a different string from what the header renders.
    """
    for layout_id, entry in entry_per_layout(DOC).items():
        layout = layouts_for(DOC)[layout_id]
        _, boxes = render(entry, layout, supplier=layout["logo_text"])
        assert "SUPPLIER_NAME" not in boxes, f"{layout_id}: duplicated the letterhead"
        _, boxes = render(entry, layout, supplier=layout["logo_text"] + " Group")
        assert "SUPPLIER_NAME" in boxes, f"{layout_id}: suppressed a supplier that is not the letterhead"


def test_supplier_name_drawn_when_it_equals_legal_bank_but_not_logo_text() -> None:
    """Benchmark guarantee: a supplier equal to the legal bank name must still be rendered.

    The legal name is not on the page otherwise, so a model scored against
    SUPPLIER_NAME could never extract it.
    """
    for layout_id, entry in entry_per_layout(DOC).items():
        layout = layouts_for(DOC)[layout_id]
        assert layout["bank"] != layout["logo_text"], f"{layout_id}: premise needs distinct strings"
        _, boxes = render(entry, layout, supplier=layout["bank"])
        assert "SUPPLIER_NAME" in boxes, layout_id


def test_every_corpus_supplier_name_is_extractable_on_the_page() -> None:
    """Across the whole corpus every non-empty SUPPLIER_NAME is on the page.

    It is either recorded as its own box or already present verbatim as the
    logo_text letterhead. Otherwise a model cannot extract it and the benchmark
    is unscoreable.
    """
    for case_id, entry in corpus_entries(DOC).items():
        supplier = entry["fields"].get("SUPPLIER_NAME", "")
        if not supplier:
            continue
        layout = layouts_for(DOC)[entry["layout"]]
        _, boxes = render(entry, layout)
        assert "SUPPLIER_NAME" in boxes or supplier == layout["logo_text"], (
            f"{case_id}: supplier {supplier!r} is nowhere on the page"
        )


def test_supplier_name_is_consistent_with_the_layout_bank_across_corpus() -> None:
    """Ground-truth invariant: a CBA-layout statement carries a CBA supplier, never ANZ.

    SUPPLIER_NAME is the canonical bank name implied by the layout, whose id
    prefix is a banks-pool code.
    """
    names = {bank["code"]: bank["name"] for bank in load_pools()["banks"]}
    for case_id, entry in corpus_entries(DOC).items():
        code = entry["layout"].split("_")[0]
        assert code in names, f"{case_id}: layout prefix {code!r} is not a banks-pool code"
        assert entry["fields"]["SUPPLIER_NAME"] == names[code], case_id


def test_every_bank_layout_has_a_supplier_name_budget() -> None:
    """Fit-safety: SUPPLIER_NAME is a fitted field, so every layout needs its budget."""
    for layout_id, layout in layouts_for(DOC).items():
        budget = field_budget(layout, layout_id, "SUPPLIER_NAME", layout_path=layout_path_for(DOC))
        assert budget["width"] > 0
