"""Report each scored field's drawn type size per invoice layout (Stage 6, Decision 22).

Stage 4 asked for scored-field type sizes to be reviewed beside label variation (PAYER_NAME on every
invoice layout and SUPPLIER_NAME on four print at the body size). Sizes are not changed in Stage 6; this
test measures them, on the first case of each invoice layout rendered plain, and prints the table for the
stage's outcome. It asserts only what the table needs: every scored box holds drawn ink with a size.
"""

import collections

from generators.labels.unambiguity import value_lines
from tests.renderer_helpers import corpus_entries, layouts_for, render_entry


def measure() -> dict[str, dict[str, str]]:
    """Layout -> scored field -> its drawn size and weight (e.g. `11pt bold`; sizes joined when mixed)."""
    firsts: dict[str, dict] = {}
    for entry in corpus_entries("invoice").values():
        firsts.setdefault(entry["layout"], entry)
    table: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for layout_id, entry in sorted(firsts.items()):
        geometry: dict = {}
        render_entry("invoice", entry, distractors=False, geometry_out=geometry)
        found: dict[str, set] = collections.defaultdict(set)
        for field in sorted(geometry["boxes"]):
            # Line-item cells are reported per column, not per row.
            found[field.split("[")[0]].update(
                (line.size, line.bold) for line in value_lines(geometry, field)
            )
        for field, sizes in found.items():
            table[layout_id][field] = "/".join(f"{s}pt{' bold' if b else ''}" for s, b in sorted(sizes))
    return dict(table)


def test_every_invoice_layouts_scored_sizes_are_measured_and_reported() -> None:
    table = measure()
    assert set(table) == set(layouts_for("invoice"))
    fields = sorted({field for row in table.values() for field in row})
    print()
    print("layout | " + " | ".join(fields))
    for layout_id, row in table.items():
        print(f"{layout_id} | " + " | ".join(row.get(field, "-") for field in fields))
        assert all(value and "0pt" != value[:3] for value in row.values()), (layout_id, row)
