"""Startup validation for a layout's `body:` tree.

Every layout is fully checked before any rendering begins, per the project's
fail-fast rule: unknown primitives, missing keys, unknown field references and
unregistered row providers all fail here with a four-element diagnostic.
"""

import functools
import itertools
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import segno
from PIL import Image, ImageColor, ImageDraw

from generators.common import FONT_FAMILIES, load_font
from generators.content_engine import load_pools
from generators.distractors.config import (
    NO_OVERLAY,
    DistractorConfig,
    load_distractor_config,
    required_items,
)
from generators.distractors.keys import DX_PREFIX
from generators.distractors.paths import DATA_POOLS_PATH, DISTRACTORS_PATH, REPO_ROOT
from generators.exporters.eval_projection import load_extraction_schema
from generators.labels.config import load_label_config, normalise_wording
from generators.labels.keys import (
    LABEL_PREFIX,
    REGION_NAMES,
    SITE_KEYS,
    SITES_PARAM,
    TOTALS_REGION,
    label_placeholder,
)
from generators.labels.keys import PROVIDER_NAME as LABEL_PROVIDER
from generators.layout_dsl.binding import referenced_fields
from generators.layout_dsl.defaults import PARAMETER_DEFAULTS
from generators.layout_dsl.field_providers import (
    collect_emit_collisions,
    distractor_doc_type,
    distractor_provider_names,
    field_provider_emits,
    field_provider_names,
    field_provider_param_keys,
    is_distractor_provider,
)
from generators.layout_dsl.primitives_codes import (
    ERROR_LEVELS,
    LOGO_STYLES,
    SYMBOLOGIES,
    BarcodeGeometry,
    barcode_geometry,
    measure_caption,
    ean13_check_digit,
)
from generators.layout_dsl.primitives_table import CELL_LINE_SPACINGS, COLUMN_ALIGNMENTS
from generators.layout_dsl.primitives_text import ALIGNMENTS, PAIR_CURRENCIES, PAIR_VALUE_ALIGNS
from generators.layout_dsl.providers import provider_names, provider_param_keys
from generators.overlay.registry import OVERLAY_DRAWERS

# Matches a `{` opened but never closed, or a `}` closed but never opened --
# `referenced_fields`'s `\{([A-Z][A-Z0-9_]*)\}` only matches well-formed
# `{FIELD}`, so a typo like `{PAYER_NAME` (missing `}`) is invisible to it and
# draws as a silent literal instead of failing validation.
#
# The second alternative's lookbehind excludes `[A-Z0-9_{]`, not just `{`: a
# lone `(?<!\{)` only rejects a match starting immediately after `{`, but
# `.search()` also tries starting mid-identifier -- e.g. at the "A" in a
# well-formed "{PAYER_NAME}", where the preceding character is "P", not "{" --
# and would wrongly flag "AYER_NAME}" as an unopened placeholder. Excluding
# every identifier character too forces a match to start at a token's true
# beginning.
_UNBALANCED = re.compile(r"\{[A-Z][A-Z0-9_]*(?![A-Z0-9_]*\})|(?<![A-Z0-9_{])[A-Z][A-Z0-9_]*\}")

# `frame` and `grouping` are independent axes describing a table's row style.
#
# `frame` -- how rows and the header are decorated:
#   ruled    -- rule lines above/below the header.
#   bordered -- a bordered header box + interior column dividers, a rule
#               above every row but the first.
#   filled   -- a `fill_color` rectangle drawn behind the header bar and
#               behind each group's dedicated date row (NAB's light-blue bar).
#   plain    -- no header or row decoration at all.
#
# `grouping` -- how repeated transaction dates are handled:
#   none          -- dates repeat on every row.
#   dedicated_row -- a separate bold date sub-header row is inserted whenever
#                    the date changes (CBA's "grouped").
#   inline        -- the repeated date is blanked within the row and the row
#                    above a new group is ruled/bordered, without consuming a
#                    row of its own (Westpac premium's "bordered_grouped").
FRAMES = ("ruled", "bordered", "filled", "plain")
GROUPINGS = ("none", "dedicated_row", "inline")

# primitive -> (required keys, optional keys)
PRIMITIVES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # `content` and `from_layout` are mutually exclusive alternatives, not two
    # independent optional keys — see the "exactly one of" check in
    # _validate_block. Neither is in `required` because the choice between
    # them is validated there, with a diagnostic naming both options.
    "text": (
        (),
        (
            "content",
            "from_layout",
            "role",
            "align",
            "color",
            "field",
            "bold",
            "suppress_if_equals",
            "family",
            "line_advance",
            "budget",
        ),
    ),
    "pair": (
        ("label", "value"),
        (
            "role",
            "color",
            "field",
            "family",
            "line_advance",
            "budget",
            "value_align",
            "min_gap",
            "separator",
            "bold",
            "currency",
        ),
    ),
    "block": (("lines",), ("role", "color", "heading", "family", "line_advance")),
    "rule": ((), ("color", "thickness", "pad_above", "pad_below", "fill_char")),
    "spacer": ((), ("height",)),
    "panel": (("children",), ("border_color", "padding", "height", "anchor")),
    "split": (("children",), ("gap", "divider", "divider_color", "widths", "anchor")),
    "stack": (("children",), ("anchor", "region")),
    "banner": (
        ("height", "color"),
        ("content", "from_layout", "text_color", "role", "text_y", "bold", "family"),
    ),
    "barcode": (
        ("content", "symbology", "height", "module_px", "quiet_zone", "human_readable", "caption_gap"),
        ("align", "color", "role", "family"),
    ),
    "qrcode": (
        ("content", "module_px", "quiet_zone", "error_level"),
        ("caption", "align", "color", "role", "family"),
    ),
    "logo": (
        ("content", "style", "size", "min_text_size", "text_color", "inset"),
        ("align", "color", "family"),
    ),
    "table": (
        ("rows", "columns", "frame", "grouping"),
        (
            "params",
            # Provider params read off the layout, {param: layout_key}, so a
            # table shared through an anchor can take a per-layout value (a
            # receipt's unit-line wording) as text blocks do through from_layout.
            "params_from_layout",
            "row_height",
            "header",
            "header_bold",
            "header_height",
            "dividers",
            "fill_color",
            "fill_inset",
            "fill_height",
            "label_inset_y",
            "row_inset_y",
            "cell_line_spacing",
            "group_gap",
            "synthetic_row_placement",
            "header_rule_top",
            "header_rule_gap",
            "family",
            "role",
            "line_advance",
            "capture",
        ),
    ),
}

_CONTAINERS = ("panel", "split", "stack")
# Parsing ground-truth keys every block accepts; `parse_block` is added for a container only, and
# `parse_join` for a table only.
_PARSE_KEYS = frozenset({"parse_category", "parse_order"})
# The values `parse_join` takes: `previous_table` writes the table's rows as more rows of the nearest table
# drawn before it.
PARSE_JOINS = ("previous_table",)
# The one container `anchor:` value: drawn upward from the bottom margin (primitives_container.foot_origin).
ANCHORS = ("foot",)

# A table column's own allowed keys — the "one level down" counterpart to
# PRIMITIVES above. Without this, a typo (e.g. `feild` for `field`) is a
# silent no-op: the column simply never draws that cell, indistinguishable
# at a glance from the field genuinely being absent from the entry.
_COLUMN_KEYS = frozenset(
    {
        "key",
        "label",
        "align",
        "x",
        "x_right",
        # A header label whose anchor and alignment differ from its own cells'
        # — the legacy invoice renderer left-aligns "Unit Price" at the
        # column's left edge above amounts that right-align 200px further on.
        # Each falls back to the column's own `x`/`align` when absent.
        "label_x",
        "label_align",
        "budget",
        "field",
        "last_row_field",
        "currency",
        "currency_suffix",
        "sub_line",
        # A second ground-truth value living inside this column's own cell
        # text: `prefix_key` names the row key holding it (a line item's
        # "2x " quantity marker), `prefix_field` the field its box is
        # recorded against. Both or neither — see the pairing check in
        # `_validate_table`.
        "prefix_key",
        "prefix_field",
    }
)

# Where a provider's leading synthetic row (opening_balance / brought_forward)
# renders relative to `grouping: dedicated_row`'s first date sub-header row:
# "leading" (default) renders it first, ahead of any group header -- CBA's
# Opening Balance. "after_first_group_header" defers it until that header has
# been drawn -- NAB's Brought-forward row, which sits *under* the first date.
SYNTHETIC_ROW_PLACEMENTS = ("leading", "after_first_group_header")


# Top-level layout keys the DSL itself reads off the layout dict, each named
# with the call site that reads it. A key absent from this set, from the page
# keys below, and from what the layout's own body references is a key nothing
# reads -- see `known_layout_keys`.
_ENGINE_LAYOUT_KEYS = frozenset(
    {
        "body",  # engine.render_body
        "defaults",  # defaults.resolve_param
        "distractor_items",  # _validate_distractor_items: the catalogue items the body draws
        "field_budgets",  # layout_budgets.field_budget
        "field_providers",  # field_providers.apply_field_providers
        "font_sizes",  # primitives_text.resolve_role
        "overlay",  # overlay.engine.apply_overlay: the marks drawn over a distractor page
        "row_height",  # primitives_table._resolve_row_height (layout fallback)
    }
)

# Page-box keys the per-document adapters (generators/{bank_statement,receipt,
# invoice}.py) read to build the canvas and content region before handing the
# layout to the engine. `page_dimensions` is read by the engine too, in
# primitives_text.draw_banner, which spans the full page rather than the region.
_PAGE_LAYOUT_KEYS = frozenset(
    {
        "margin",  # every adapter: Region.x and the starting y-cursor
        "content_width",  # every adapter: Region.width
        "page_dimensions",  # bank_statement.py, invoice.py, draw_banner
        "width",  # receipt.py: the thermal roll's pixel width
        "canvas_ceiling",  # receipt.py: the pre-crop canvas height
        "page",  # generators/typst/config.layout_page: the physical page; the PIL renderer ignores it
    }
)

# The sizes a layout's `page: {size: ...}` may name, in the physical dimensions Typst typesets onto.
PAGE_SIZES_MM: dict[str, tuple[float, float]] = {"a4": (210.0, 297.0)}

# Keys no render path reads, but which the corpus tooling around the renderers
# does. They are not dead: deleting one breaks seeding or a ground-truth
# invariant rather than a page.
#
#   bank               -- the full legal bank name a bank layout's letterhead
#                         stands for. tests/test_bank_supplier_header.py holds
#                         SUPPLIER_NAME to it, so the scored field cannot
#                         contradict the letterhead ("bank = f(layout)").
#   transaction_count  -- {min, max} row count scripts/seed_ground_truth.py
#                         sizes a statement's transaction list to, so a dense
#                         layout is seeded with enough rows to look dense.
#
# and, on every invoice layout (`_validate_service_categories`):
#
#   service_categories: the invoice categories (config/data_pools.yml) that
#   scripts/seed_ground_truth.py draws an invoice's supplier from, so a trade
#   layout never bills legal advice.
#
# and, on every receipt layout (`_validate_merchant_categories`):
#
#   merchant_categories: the receipt categories (config/data_pools.yml) whose
#   merchants it prints, so a pharmacy's receipt never signs off "Drive safely".
_CORPUS_LAYOUT_KEYS = frozenset({"bank", "transaction_count", "service_categories", "merchant_categories"})

# Block keys whose *value* is the name of a layout key, rather than a `{FIELD}`
# template or a literal. Both are resolved against the layout dict at render
# time -- `from_layout` in draw_text_block/draw_banner, `suppress_if_equals` in
# draw_text_block -- and both are checked to exist by `_validate_geometry`.
_LAYOUT_KEY_REFERENCES = ("from_layout", "suppress_if_equals")


class LayoutSchemaError(RuntimeError):
    """Raised when a layout body fails structural validation."""


def _err(what: str, *, layout_path: str, key_path: str, expected: str, recover: str) -> LayoutSchemaError:
    """Build a four-element fail-fast diagnostic error.

    Args:
        what: What is wrong.
        layout_path: Path to the offending layout YAML.
        key_path: Dotted path to the offending key inside that file.
        expected: What a valid value looks like.
        recover: One-line remediation.

    Returns:
        The constructed error.
    """
    return LayoutSchemaError(
        "Invalid layout body.\n"
        f"  What:     {what}\n"
        f"  Where:    {layout_path} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _check_braces(template: str, *, layout_path: str, key_path: str) -> None:
    """Reject a placeholder missing its opening or closing brace.

    `referenced_fields` only matches well-formed `{FIELD}`, so a typo like
    `{PAYER_NAME` is invisible to it and draws as a literal.
    """
    if _UNBALANCED.search(template):
        raise _err(
            f"template {template!r} contains an unbalanced placeholder brace.",
            layout_path=layout_path,
            key_path=key_path,
            expected='every placeholder written as {FIELD_NAME}, e.g. content: "Account: {PAYER_NAME}".',
            recover=f"add the missing brace in {key_path}.",
        )


def validate_body(
    body: list,
    *,
    layout_id: str,
    layout_path: str,
    known_fields: set[str],
) -> None:
    """Validate a layout's body tree, recursing into containers.

    Args:
        body: The layout's `body:` list of block dicts.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        known_fields: Field names the document type may reference.

    Raises:
        LayoutSchemaError: On any structural or reference problem.
    """
    if not isinstance(body, list):
        raise _err(
            f"layout '{layout_id}' body is {type(body).__name__}, not a list.",
            layout_path=layout_path,
            key_path=f"{layout_id}.body",
            expected="a list of block mappings, each with a 'type' key.",
            recover=f"make {layout_id}.body a YAML list.",
        )
    _validate_blocks(
        body,
        layout_id=layout_id,
        layout_path=layout_path,
        known_fields=known_fields,
        key_path=f"{layout_id}.body",
    )
    _validate_anchors(body, layout_id=layout_id, layout_path=layout_path)


def _validate_anchors(body: list, *, layout_id: str, layout_path: str) -> None:
    """Check every `anchor:` is `foot`, on the last top-level block of the body only.

    A foot-anchored container is drawn upward from the bottom margin, so it must be the body's last
    block: anything after it would draw below the margin, and one nested in another container has no
    page foot of its own to reach.

    Raises:
        LayoutSchemaError: An anchor value is unknown, or an anchored block is nested or not last.
    """
    last = len(body) - 1
    for block, here in _iter_blocks(body, f"{layout_id}.body"):
        if "anchor" not in block:
            continue
        value = block["anchor"]
        if value not in ANCHORS:
            raise _err(
                f"unknown anchor {value!r}.",
                layout_path=layout_path,
                key_path=f"{here}.anchor",
                expected=f"one of {list(ANCHORS)}, e.g. {{type: stack, anchor: foot, children: [...]}}.",
                recover=f"set anchor: to one of {list(ANCHORS)}, or remove it.",
            )
        if here != f"{layout_id}.body[{last}]":
            raise _err(
                "a foot-anchored container is not the last top-level block of the body, so blocks "
                "after it would draw below the bottom margin (or, nested, it has no page foot to reach).",
                layout_path=layout_path,
                key_path=f"{here}.anchor",
                expected=f"anchor: foot only on {layout_id}.body[{last}], the body's last block, e.g.\n"
                "              - {type: stack, anchor: foot, children: [<footer blocks>]}",
                recover="move the anchored container to the end of the body, or remove its anchor.",
            )


def _validate_blocks(
    blocks: list, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Validate a list of blocks at one nesting level."""
    for index, block in enumerate(blocks):
        here = f"{key_path}[{index}]"
        if not isinstance(block, dict):
            raise _err(
                f"block is {type(block).__name__}, not a mapping.",
                layout_path=layout_path,
                key_path=here,
                expected='a mapping such as {type: text, content: "{PAYER_NAME}"}.',
                recover="replace the entry with a block mapping.",
            )
        _validate_block(
            block, layout_id=layout_id, layout_path=layout_path, known_fields=known_fields, key_path=here
        )


def _validate_block(
    block: dict, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Validate one block and recurse into any children."""
    kind = block.get("type")
    if kind is None:
        raise _err(
            "block has no 'type' key.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"type: one of {sorted(PRIMITIVES)}.",
            recover="add a type: key naming the primitive to render.",
        )
    if kind not in PRIMITIVES:
        raise _err(
            f"unknown primitive '{kind}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.type",
            expected=f"one of {sorted(PRIMITIVES)}.",
            recover="use a supported primitive, or add one to PRIMITIVES in "
            "generators/layout_dsl/schema.py.",
        )

    required, optional = PRIMITIVES[kind]
    missing = [key for key in required if key not in block]
    if missing:
        raise _err(
            f"'{kind}' block missing required key(s): {missing}.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"required {list(required)}; optional {list(optional)}.",
            recover=f"add {missing} to the {kind} block.",
        )
    if "region" in block and kind != "stack":
        # Named before the generic unknown-key check: only a stack declares a region (Decision 9).
        _validate_regions([block], layout_id=layout_id, layout_path=layout_path)
    if "parse_block" in block and kind not in _CONTAINERS:
        # Named before the generic unknown-key check: only a container can be one parsed block.
        raise _err(
            f"'{kind}' block sets parse_block, which only {list(_CONTAINERS)} accept.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_block",
            expected=f"parse_block on a block of type {' / '.join(_CONTAINERS)} (true makes the container "
            "one parsed block).",
            recover="remove parse_block from this block, or move it to the enclosing container.",
        )
    if "parse_join" in block and kind != "table":
        # Named before the generic unknown-key check: only a table has rows to join to another.
        raise _err(
            f"'{kind}' block sets parse_join, which only 'table' accepts.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_join",
            expected="parse_join on a block of type table, e.g.\n"
            "              - {type: table, rows: ..., columns: [...], parse_join: previous_table}",
            recover="remove parse_join from this block.",
        )
    allowed = set(required) | set(optional) | _PARSE_KEYS | {"type", "when"}
    if kind in _CONTAINERS:
        allowed.add("parse_block")
    if kind == "table":
        allowed.add("parse_join")
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise _err(
            f"'{kind}' block has unknown key(s): {unknown}.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"only {sorted(allowed)}.",
            recover=f"remove {unknown}, or add them to PRIMITIVES in generators/layout_dsl/schema.py.",
        )

    _validate_parse_keys(block, layout_path=layout_path, key_path=key_path)

    # A block-level `family:` override is caught here rather than at render
    # time: load_font would raise FontFamilyError on the first block that
    # happens to draw, which is both later than startup and harder to trace
    # back to the YAML that caused it.
    family = block.get("family")
    if family is not None and family not in FONT_FAMILIES:
        raise _err(
            f"unknown font family '{family}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.family",
            expected=f"one of {sorted(FONT_FAMILIES)}.",
            recover=f"set family: to one of {sorted(FONT_FAMILIES)}, or vendor the new "
            "face in fonts/ and register it in FONT_FAMILIES in generators/common.py.",
        )

    if kind in ("text", "banner"):
        has_content = "content" in block
        has_from_layout = "from_layout" in block
        if has_content == has_from_layout:
            raise _err(
                f"'{kind}' block sets "
                + (
                    "both 'content' and 'from_layout'."
                    if has_content
                    else "neither 'content' nor 'from_layout'."
                ),
                layout_path=layout_path,
                key_path=key_path,
                expected="exactly one of: content: '{FIELD}' (an entry-field template) or "
                "from_layout: <layout_key> (a layout value read literally).",
                recover="set 'content:' or 'from_layout:', not both or neither.",
            )

    if kind in ("text", "barcode", "qrcode", "logo"):
        align = block.get("align")
        if align is not None and align not in ALIGNMENTS:
            raise _err(
                f"unknown align '{align}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.align",
                expected=f"one of {list(ALIGNMENTS)}.",
                recover=f"set align: to one of {list(ALIGNMENTS)}.",
            )

    if kind == "pair":
        value_align = block.get("value_align")
        if value_align is not None and value_align not in PAIR_VALUE_ALIGNS:
            raise _err(
                f"unknown value_align '{value_align}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.value_align",
                expected=f"one of {list(PAIR_VALUE_ALIGNS)}.",
                recover=f"set value_align: to one of {list(PAIR_VALUE_ALIGNS)}.",
            )
        currency = block.get("currency")
        if currency is not None and currency not in PAIR_CURRENCIES:
            raise _err(
                f"unknown currency '{currency}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.currency",
                expected=f"one of {list(PAIR_CURRENCIES)}: 'symbol' keeps the $ prefix, 'plain' drops it.",
                recover=f"set currency: to one of {list(PAIR_CURRENCIES)}, or remove it to "
                "draw the value exactly as it interpolates.",
            )

    _validate_references(block, layout_path=layout_path, known_fields=known_fields, key_path=key_path)

    if kind in ("barcode", "qrcode", "logo") and "color" in block:
        _check_colour(block, "color", layout_path=layout_path, key_path=key_path)
    if kind == "barcode":
        _validate_barcode(block, layout_path=layout_path, key_path=key_path)
    if kind == "qrcode":
        _validate_qrcode(block, layout_path=layout_path, key_path=key_path)
    if kind == "logo":
        _validate_logo(block, layout_path=layout_path, key_path=key_path)
    if kind == "table":
        _validate_table(block, known_fields=known_fields, layout_path=layout_path, key_path=key_path)
    if kind in _CONTAINERS:
        _validate_children(
            block,
            layout_id=layout_id,
            layout_path=layout_path,
            known_fields=known_fields,
            key_path=key_path,
        )
        if block.get("parse_block") is True:
            _refuse_tables_beneath(block, layout_path=layout_path, key_path=key_path)


def _refuse_tables_beneath(block: dict, *, layout_path: str, key_path: str) -> None:
    """Refuse a table at any depth beneath a `parse_block: true` container.

    The container's block owns all ink drawn beneath it, but a table needs a block of its own to declare
    its columns and cells, so the two cannot combine.

    Args:
        block: The container, carrying `parse_block: true`.
        layout_path: Path to the layout YAML.
        key_path: The container's dotted path.

    Raises:
        LayoutSchemaError: A table sits beneath the container.
    """
    children = block.get("children")
    if block.get("type") == "split" and isinstance(children, list):
        nested: Iterator[tuple[dict, str]] = (
            pair
            for column, blocks in enumerate(children)
            for pair in _iter_blocks(blocks, f"{key_path}.children[{column}]")
        )
    else:
        nested = _iter_blocks(children, f"{key_path}.children")
    for child, child_path in nested:
        if child.get("type") == "table":
            raise _err(
                f"a table sits beneath the {block.get('type')} at {key_path}, which sets "
                "parse_block: true; "
                "the container's one parsed block would swallow the table, which needs a block of its own "
                "for its columns and cells.",
                layout_path=layout_path,
                key_path=child_path,
                expected="a table outside any parse_block container, e.g. beside it in the body:\n"
                "              - {type: stack, parse_block: true, children: [...]}\n"
                "              - {type: table, rows: ..., columns: [...], frame: ruled, grouping: none}",
                recover=f"set parse_block: false on {key_path}, or move the table out of it.",
            )


def validate_page(layout: dict, *, layout_path: str, layout_id: str) -> tuple[str, float | str]:
    """Check a layout's required `page:` key, the physical page it is typeset onto.

    Exactly one of `size` (a named sheet, `a4`) or `width_mm` (a receipt roll, whose height follows its
    content) is given. The PIL renderer never reads the key; the Typst translator does.

    Args:
        layout: The resolved layout dict.
        layout_path: Path to the layout YAML, for the diagnostic.
        layout_id: The layout's id.

    Returns:
        `("size", name)` or `("width_mm", millimetres)`.

    Raises:
        LayoutSchemaError: If the key is absent or malformed.
    """
    key_path = f"{layout_id}.page"
    if "page" not in layout:
        raise _err(
            f"layout '{layout_id}' has no 'page' key.",
            layout_path=layout_path,
            key_path=key_path,
            expected="page: {size: a4} for an invoice or bank statement, or page: {width_mm: 80} for a "
            "receipt roll of that width in millimetres.",
            recover=f"add a 'page:' key to {layout_id}: size for a sheet, width_mm for a roll.",
        )
    page = layout["page"]
    if not isinstance(page, dict):
        raise _err(
            f"page is {page!r}, not a mapping.",
            layout_path=layout_path,
            key_path=key_path,
            expected="a mapping with exactly one of size or width_mm, e.g. page: {size: a4}.",
            recover=f"replace {key_path} with page: {{size: a4}} or page: {{width_mm: 80}}.",
        )
    unknown = sorted(set(page) - {"size", "width_mm"})
    if unknown:
        raise _err(
            f"page carries key(s) {unknown} that nothing reads.",
            layout_path=layout_path,
            key_path=key_path,
            expected="only size or width_mm, e.g. page: {size: a4}.",
            recover=f"remove {unknown} from {key_path}.",
        )
    if "size" in page and "width_mm" in page:
        raise _err(
            "page declares both size and width_mm.",
            layout_path=layout_path,
            key_path=key_path,
            expected="exactly one: size for a sheet (page: {size: a4}), width_mm for a roll "
            "(page: {width_mm: 80}).",
            recover=f"remove one of size or width_mm from {key_path}.",
        )
    if "size" in page:
        size = page["size"]
        if not isinstance(size, str) or size not in PAGE_SIZES_MM:
            raise _err(
                f"page.size is {size!r}, not one of {sorted(PAGE_SIZES_MM)}.",
                layout_path=layout_path,
                key_path=f"{key_path}.size",
                expected=f"one of {sorted(PAGE_SIZES_MM)}, e.g. page: {{size: a4}}.",
                recover=f"set {key_path}.size to a4, or use width_mm for a roll.",
            )
        return "size", size
    if "width_mm" in page:
        width = page["width_mm"]
        if isinstance(width, bool) or not isinstance(width, int | float) or not 0 < width < float("inf"):
            raise _err(
                f"page.width_mm is {width!r}, not a positive number.",
                layout_path=layout_path,
                key_path=f"{key_path}.width_mm",
                expected="a positive number of millimetres, e.g. page: {width_mm: 80}.",
                recover=f"set {key_path}.width_mm to the roll width in millimetres, e.g. 80.",
            )
        return "width_mm", float(width)
    raise _err(
        "page declares neither size nor width_mm.",
        layout_path=layout_path,
        key_path=key_path,
        expected="exactly one: size for a sheet (page: {size: a4}), width_mm for a roll "
        "(page: {width_mm: 80}).",
        recover=f"add size or width_mm to {key_path}.",
    )


def _validate_parse_keys(block: dict, *, layout_path: str, key_path: str) -> None:
    """Check the parsing-ground-truth keys' types; the category vocabulary is `validate`'s concern."""
    category = block.get("parse_category", "x")
    if not isinstance(category, str) or not category:
        raise _err(
            f"parse_category is {category!r}, not a non-empty string.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_category",
            expected="a category_vocabulary value such as parse_category: title.",
            recover="set parse_category to a non-empty string, or remove it.",
        )
    order = block.get("parse_order", 0)
    if isinstance(order, bool) or not isinstance(order, int):
        raise _err(
            f"parse_order is {order!r}, not an integer.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_order",
            expected="an integer reading-order rank such as parse_order: 2 (not a boolean).",
            recover="set parse_order to an integer, or remove it.",
        )
    if "parse_block" in block and not isinstance(block["parse_block"], bool):
        raise _err(
            f"parse_block is {block['parse_block']!r}, not a boolean.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_block",
            expected="parse_block: true or parse_block: false.",
            recover="set parse_block to true or false, or remove it.",
        )
    if "parse_join" in block and (
        not isinstance(block["parse_join"], str) or block["parse_join"] not in PARSE_JOINS
    ):
        raise _err(
            f"parse_join is {block['parse_join']!r}, not one of {list(PARSE_JOINS)}.",
            layout_path=layout_path,
            key_path=f"{key_path}.parse_join",
            expected=f"one of {list(PARSE_JOINS)}, e.g. parse_join: previous_table (the table's rows are "
            "written as more rows of the nearest table drawn before it).",
            recover="set parse_join to previous_table, or remove it.",
        )


def _check_colour(block: dict, key: str, *, layout_path: str, key_path: str) -> None:
    """Reject a colour PIL cannot parse, so it fails at validate time rather than mid-render."""
    value = block[key]
    try:
        ImageColor.getrgb(value)
    except (ValueError, AttributeError):
        raise _err(
            f"'{key}' is {value!r}, not a colour.",
            layout_path=layout_path,
            key_path=f"{key_path}.{key}",
            expected="a colour name or hex string, e.g. white or '#ffffff'.",
            recover=f"set {key}: to a valid colour.",
        ) from None


def _reject_empty_literal(text: str, kind: str, key: str, *, layout_path: str, key_path: str) -> None:
    """Reject a literal (brace-free) `content` or `caption` that is empty, since it would draw nothing."""
    if not text.strip():
        raise _err(
            f"{kind} '{key}' is {text!r}, an empty literal.",
            layout_path=layout_path,
            key_path=f"{key_path}.{key}",
            expected=f"non-empty text, or a '{{FIELD}}' template, e.g. {key}: \"TX-2031\".",
            recover=f"give '{key}' some text or a '{{FIELD}}' template.",
        )


def _validate_barcode(block: dict, *, layout_path: str, key_path: str) -> None:
    """Check a barcode's symbology, sizes, caption flag and any literal content."""
    symbology = block["symbology"]
    if symbology not in SYMBOLOGIES:
        raise _err(
            f"unknown barcode symbology {symbology!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.symbology",
            expected=f"one of {list(SYMBOLOGIES)}.",
            recover=f"set symbology: to one of {list(SYMBOLOGIES)}.",
        )
    for key, minimum in (("height", 1), ("module_px", 1), ("quiet_zone", 0), ("caption_gap", 0)):
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise _err(
                f"barcode '{key}' is {value!r}.",
                layout_path=layout_path,
                key_path=f"{key_path}.{key}",
                expected=f"an integer >= {minimum}, e.g. {key}: {max(minimum, 2)}.",
                recover=f"set {key}: to an integer >= {minimum}.",
            )
    if not isinstance(block["human_readable"], bool):
        raise _err(
            f"barcode 'human_readable' is {block['human_readable']!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.human_readable",
            expected="true or false.",
            recover="set human_readable: true to print the digits beneath the bars, or false.",
        )
    content = block["content"]
    if not isinstance(content, str):
        raise _err(
            f"barcode 'content' is {content!r}, not a string.",
            layout_path=layout_path,
            key_path=f"{key_path}.content",
            expected="a string: a literal or a '{FIELD}' template.",
            recover="quote the value in the YAML.",
        )
    if "{" in content:
        return
    _reject_empty_literal(content, "barcode", "content", layout_path=layout_path, key_path=key_path)
    if symbology == "ean13" and not (content.isdigit() and len(content) in (12, 13)):
        raise _err(
            f"literal ean13 content {content!r} is not 12 or 13 digits.",
            layout_path=layout_path,
            key_path=f"{key_path}.content",
            expected="12 digits (the check digit is computed) or 13 digits including it.",
            recover="correct the digits, or use symbology: code128.",
        )
    if symbology == "ean13" and len(content) == 13 and content[12] != ean13_check_digit(content):
        raise _err(
            f"literal ean13 content {content!r} has check digit {content[12]}, "
            f"expected {ean13_check_digit(content)}.",
            layout_path=layout_path,
            key_path=f"{key_path}.content",
            expected="13 digits whose last digit is the EAN-13 check digit, or 12 digits.",
            recover="drop the 13th digit to let it be computed, or correct it.",
        )
    if symbology == "code128" and not all(32 <= ord(ch) < 127 for ch in content):
        raise _err(
            f"literal code128 content {content!r} has characters outside printable ASCII.",
            layout_path=layout_path,
            key_path=f"{key_path}.content",
            expected="content made of ASCII characters 32 to 126.",
            recover="transliterate the content to ASCII.",
        )


def _validate_qrcode(block: dict, *, layout_path: str, key_path: str) -> None:
    """Check a qrcode's error level, sizes and the types of its templates."""
    level = block["error_level"]
    if level not in ERROR_LEVELS:
        raise _err(
            f"unknown qrcode error_level {level!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.error_level",
            expected=f"one of {list(ERROR_LEVELS)}.",
            recover=f"set error_level: to one of {list(ERROR_LEVELS)}.",
        )
    for key, minimum in (("module_px", 1), ("quiet_zone", 0)):
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise _err(
                f"qrcode '{key}' is {value!r}.",
                layout_path=layout_path,
                key_path=f"{key_path}.{key}",
                expected=f"an integer >= {minimum}, e.g. {key}: {max(minimum, 4)}.",
                recover=f"set {key}: to an integer >= {minimum}.",
            )
    for key in ("content", "caption"):
        if key in block and not isinstance(block[key], str):
            raise _err(
                f"qrcode '{key}' is {block[key]!r}, not a string.",
                layout_path=layout_path,
                key_path=f"{key_path}.{key}",
                expected="a string: a literal or a '{FIELD}' template.",
                recover="quote the value in the YAML.",
            )
        if key in block and "{" not in block[key]:
            _reject_empty_literal(block[key], "qrcode", key, layout_path=layout_path, key_path=key_path)
    content = block["content"]
    if "{" not in content:
        try:
            segno.make(content, error=level, micro=False, boost_error=False)
        except segno.DataOverflowError:
            raise _err(
                f"literal qrcode content of {len(content)} characters does not fit any QR version at "
                f"error level {level!r}.",
                layout_path=layout_path,
                key_path=f"{key_path}.content",
                expected="content short enough for a version-40 symbol at this error level.",
                recover="shorten the content or lower error_level.",
            ) from None


def _validate_logo(block: dict, *, layout_path: str, key_path: str) -> None:
    """Check a logo's style, size, text colour and the type of its content template."""
    style = block["style"]
    if style not in LOGO_STYLES:
        raise _err(
            f"unknown logo style {style!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.style",
            expected=f"one of {list(LOGO_STYLES)}.",
            recover=f"set style: to one of {list(LOGO_STYLES)}.",
        )
    size = block["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise _err(
            f"logo 'size' is {size!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.size",
            expected="a positive integer number of pixels, e.g. size: 80.",
            recover="set size: to an integer >= 1.",
        )
    floor = block["min_text_size"]
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1 or floor > size // 2:
        raise _err(
            f"logo 'min_text_size' is {floor!r}, with size {size}.",
            layout_path=layout_path,
            key_path=f"{key_path}.min_text_size",
            expected=f"a positive integer no greater than size // 2 ({size // 2}), e.g. min_text_size: 12.",
            recover=f"set min_text_size: to an integer between 1 and {size // 2}.",
        )
    inset = block["inset"]
    if isinstance(inset, bool) or not isinstance(inset, int) or inset < 0 or inset > size // 4:
        raise _err(
            f"logo 'inset' is {inset!r}, with size {size}.",
            layout_path=layout_path,
            key_path=f"{key_path}.inset",
            expected="the initials' clear gap from the shape's rim in pixels: an integer from 0 to "
            f"size // 4 ({size // 4}), e.g. inset: 12.",
            recover=f"set inset: to an integer between 0 and {size // 4}.",
        )
    text_color = block["text_color"]
    if not isinstance(text_color, str) or not text_color.strip():
        raise _err(
            f"logo 'text_color' is {text_color!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.text_color",
            expected="a non-empty colour string, e.g. text_color: white.",
            recover="set text_color: to a colour name or hex string.",
        )
    _check_colour(block, "text_color", layout_path=layout_path, key_path=key_path)
    if "color" in block and ImageColor.getrgb(block["color"]) == ImageColor.getrgb(text_color):
        raise _err(
            f"logo 'text_color' {text_color!r} and 'color' {block['color']!r} are the same colour, so "
            "the initials would be invisible.",
            layout_path=layout_path,
            key_path=f"{key_path}.text_color",
            expected="a text_color that differs from color.",
            recover="change text_color or color.",
        )
    if not isinstance(block["content"], str):
        raise _err(
            f"logo 'content' is {block['content']!r}, not a string.",
            layout_path=layout_path,
            key_path=f"{key_path}.content",
            expected="a string: a literal or a '{FIELD}' template.",
            recover="quote the value in the YAML.",
        )
    content = block["content"]
    if "{" not in content:
        _reject_empty_literal(content, "logo", "content", layout_path=layout_path, key_path=key_path)
        if not any(ch.isalpha() for ch in content):
            raise _err(
                f"literal logo content {content!r} has no letters to take initials from.",
                layout_path=layout_path,
                key_path=f"{key_path}.content",
                expected="a name with at least one letter, or a '{FIELD}' template.",
                recover="give content a business name, or a '{FIELD}' template.",
            )


# Block keys whose string value the renderer interpolates as a `{FIELD}` template; a block's `lines:`
# entries are interpolated too. Shared by `_validate_references` and the distractor cross-check.
_TEMPLATE_KEYS = ("content", "label", "value", "heading", "caption")


def _unknown_dx_error(name: str, *, layout_path: str, key_path: str) -> LayoutSchemaError:
    """The diagnostic for a `DX_` key no provider on this layout emits.

    A `DX_` key is never a ground-truth field, so the generic advice to add it to
    config/field_definitions.yml would be wrong: it becomes known only when the layout lists the item
    emitting it and carries its document type's distractor provider.
    """
    items = sorted(i.id for i in load_distractor_config().items.values() if name in i.emits)
    providers = distractor_provider_names()
    return _err(
        f"'{name}' is a distractor key, but no field provider on this layout emits it.",
        layout_path=layout_path,
        key_path=key_path,
        expected=f"the layout lists an item emitting {name} ({items or 'none in config/distractors.yml'}) "
        "in distractor_items and carries its document type's distractor provider (one of "
        f"{providers}) in field_providers, e.g.\n"
        f"              field_providers: [{{name: {providers[0] if providers else '<provider>'}}}]\n"
        f"              distractor_items: [{items[0] if items else '<item id>'}, ...]",
        recover=f"add the document type's distractor provider to field_providers and list one of {items} "
        f"in distractor_items, or remove the reference to {name}.",
    )


def _dx_geometry_error(name: str, *, layout_path: str, key_path: str) -> LayoutSchemaError:
    """The diagnostic for a `field:` (a geometry record name) naming a `DX_` key."""
    return _err(
        f"'{name}' is a distractor key used as a geometry record name ('field:'), which would record a "
        "box for a value that is not ground truth.",
        layout_path=layout_path,
        key_path=key_path,
        expected="field: naming a scored ground-truth field only; a distractor block draws its value "
        "through content and when, with no field: key, e.g.\n"
        f'              {{type: text, content: "{{{name}}}", when: {name}}}',
        recover=f"remove field: {name} from that block; distractor geometry arrives with the overlay "
        "stage, which records distractor boxes under their own role.",
    )


def _validate_references(block: dict, *, layout_path: str, known_fields: set[str], key_path: str) -> None:
    """Check every {FIELD} placeholder, `when:`, and `field:` is a known field.

    `field:` decides what a block's drawn geometry gets recorded as in
    `geometry.jsonl` in the configured `derived_dir`: a typo there is invisible to both the pixel
    snapshot (pixels are unaffected) and a `{FIELD}`-reference check (it is
    not a template), so it needs its own check here.
    """
    texts: list[str] = []
    for key in _TEMPLATE_KEYS:
        if isinstance(block.get(key), str):
            texts.append(block[key])
    for line in block.get("lines", []) or []:
        if isinstance(line, str):
            texts.append(line)

    field = block.get("field")
    if isinstance(field, str) and field.startswith(DX_PREFIX):
        raise _dx_geometry_error(field, layout_path=layout_path, key_path=f"{key_path}.field")

    for text in texts:
        _check_braces(text, layout_path=layout_path, key_path=key_path)
        for name in referenced_fields(text):
            if name.startswith(DX_PREFIX) and name not in known_fields:
                raise _unknown_dx_error(name, layout_path=layout_path, key_path=key_path)
            if name not in known_fields:
                raise _err(
                    f"unknown field reference '{{{name}}}'.",
                    layout_path=layout_path,
                    key_path=key_path,
                    expected=f"a field defined for this document type: {sorted(known_fields)}.",
                    recover=f"fix the field name, or add '{name}' to config/field_definitions.yml.",
                )

    when = block.get("when")
    if isinstance(when, str) and when.startswith(DX_PREFIX) and when not in known_fields:
        raise _unknown_dx_error(when, layout_path=layout_path, key_path=f"{key_path}.when")
    if when is not None and when not in known_fields:
        raise _err(
            f"'when' references unknown field '{when}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.when",
            expected=f"a field defined for this document type: {sorted(known_fields)}.",
            recover=f"fix the field name, or add '{when}' to config/field_definitions.yml.",
        )

    if field is not None and field not in known_fields:
        raise _err(
            f"'field' references unknown field '{field}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.field",
            expected=f"a field defined for this document type: {sorted(known_fields)}.",
            recover=f"fix the field name, or add '{field}' to config/field_definitions.yml.",
        )


def _validate_sub_line_spans(
    sub_line: dict, *, known_fields: set[str], layout_path: str, key_path: str
) -> None:
    """Check a sub-line's `span_fields`, which records boxes for values inside the drawn line.

    The boxes are measured on a single fitted line, so a sub-line recording them must carry a `budget:`
    (checked against the layout's field_budgets, and for a single-line fit, by `_validate_column_budgets`),
    and every field it names must be a known, non-distractor field.

    Raises:
        LayoutSchemaError: `span_fields` is malformed, names an unknown or distractor field, or is set
            on a sub-line without a budget.
    """
    spans = sub_line.get("span_fields")
    if spans is None:
        return
    example = (
        "span_fields: {key: unit_line_spans, fields: {quantity: LINE_ITEM_QUANTITIES, "
        "price: LINE_ITEM_PRICES}}, alongside budget: LINE_ITEM_UNIT"
    )
    fields = spans.get("fields") if isinstance(spans, dict) else None
    if not (
        isinstance(spans, dict)
        and isinstance(spans.get("key"), str)
        and isinstance(fields, dict)
        and fields
    ):
        raise _err(
            f"sub_line span_fields is {spans!r}, not {{key, fields}}.",
            layout_path=layout_path,
            key_path=f"{key_path}.span_fields",
            expected=f"a row key naming each value's extent and a non-empty span -> field mapping, e.g. {example}.",
            recover="give span_fields both key: and fields:, or remove it.",
        )
    if "budget" not in sub_line:
        raise _err(
            "sub_line sets span_fields without a budget.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"a budgeted sub-line, so it fits (or fails) on one line, e.g. {example}.",
            recover="add budget: naming a single-line field budget to the sub_line.",
        )
    for name, field in fields.items():
        here = f"{key_path}.span_fields.fields.{name}"
        if isinstance(field, str) and field.startswith(DX_PREFIX):
            raise _dx_geometry_error(field, layout_path=layout_path, key_path=here)
        if field not in known_fields:
            raise _err(
                f"sub_line span '{name}' references unknown field '{field}'.",
                layout_path=layout_path,
                key_path=here,
                expected=f"a field defined for this document type: {sorted(known_fields)}.",
                recover=f"fix the field name, or add '{field}' to config/field_definitions.yml.",
            )


def _validate_table(block: dict, *, known_fields: set[str], layout_path: str, key_path: str) -> None:
    """Check a table's provider, params, row style, and column definitions."""
    rows = block["rows"]
    if rows not in provider_names():
        raise _err(
            f"unknown row provider '{rows}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.rows",
            expected=f"one of {provider_names()}.",
            recover="set rows: to a registered provider, or register one with "
            "@row_provider in generators/layout_dsl/providers.py.",
        )

    params = block.get("params", {})
    accepted_params = provider_param_keys(rows)
    from_layout = block.get("params_from_layout", {})
    if not isinstance(from_layout, dict) or not all(
        isinstance(name, str) and isinstance(key, str) for name, key in from_layout.items()
    ):
        raise _err(
            f"table params_from_layout is {from_layout!r}, not a mapping of param name to layout key.",
            layout_path=layout_path,
            key_path=f"{key_path}.params_from_layout",
            expected="params_from_layout: {unit_line_format: unit_line_format}",
            recover="map each provider param to the name of a top-level key of the layout.",
        )
    doubled = sorted(set(params) & set(from_layout))
    if doubled:
        raise _err(
            f"table sets {doubled} in both params and params_from_layout.",
            layout_path=layout_path,
            key_path=f"{key_path}.params_from_layout",
            expected="each provider param set in exactly one of the two.",
            recover=f"remove {doubled} from params or from params_from_layout.",
        )
    params = {**params, **from_layout}
    unknown_params = sorted(set(params) - accepted_params)
    if unknown_params:
        raise _err(
            f"table params names unknown key(s) {unknown_params} for provider '{rows}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.params",
            expected=f"one of {sorted(accepted_params)} for provider '{rows}'.",
            recover=f"fix the typo, or add {unknown_params} to provider '{rows}''s "
            "params=frozenset({...}) in generators/layout_dsl/providers.py.",
        )

    frame = block["frame"]
    if frame not in FRAMES:
        raise _err(
            f"unknown frame '{frame}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.frame",
            expected=f"one of {list(FRAMES)}.",
            recover=f"set frame: to one of {list(FRAMES)}.",
        )

    grouping = block["grouping"]
    if grouping not in GROUPINGS:
        raise _err(
            f"unknown grouping '{grouping}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.grouping",
            expected=f"one of {list(GROUPINGS)}.",
            recover=f"set grouping: to one of {list(GROUPINGS)}.",
        )

    cell_line_spacing = block.get("cell_line_spacing")
    if cell_line_spacing is not None and cell_line_spacing not in CELL_LINE_SPACINGS:
        raise _err(
            f"unknown cell_line_spacing '{cell_line_spacing}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.cell_line_spacing",
            expected=f"one of {list(CELL_LINE_SPACINGS)} — 'row_height' advances a budgeted "
            "cell by the table's own row pitch, 'font' by the fitted font's own line height.",
            recover=f"set cell_line_spacing: to one of {list(CELL_LINE_SPACINGS)}, or remove it "
            "to use the layout's table_cell_line_spacing default.",
        )

    if "capture" in block and not isinstance(block["capture"], bool):
        raise _err(
            f"capture must be a bool, got {block['capture']!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.capture",
            expected="capture: true or capture: false.",
            recover="set capture: to true or false, or remove it to use the layout's "
            "table_capture default.",
        )

    if frame == "filled" and "fill_color" not in block:
        raise _err(
            "frame: filled requires fill_color, which this table block does not set.",
            layout_path=layout_path,
            key_path=f"{key_path}.fill_color",
            expected='a hex color string, e.g. fill_color: "#E8F0FE".',
            recover=f"add fill_color: to the table block, or use a different frame ({list(FRAMES)}).",
        )
    if frame != "filled" and "fill_color" in block:
        raise _err(
            f"fill_color is set but frame is '{frame}', which never draws it.",
            layout_path=layout_path,
            key_path=f"{key_path}.fill_color",
            expected="fill_color only alongside frame: filled.",
            recover="remove fill_color, or set frame: filled.",
        )

    for filled_only_key in ("fill_height", "fill_inset"):
        if frame != "filled" and filled_only_key in block:
            raise _err(
                f"{filled_only_key} is set but frame is '{frame}', which never reads it — only "
                "frame: filled draws a fill for it to adjust.",
                layout_path=layout_path,
                key_path=f"{key_path}.{filled_only_key}",
                expected=f"{filled_only_key} only alongside frame: filled.",
                recover=f"remove {filled_only_key}, or set frame: filled.",
            )

    if grouping != "dedicated_row" and "group_gap" in block:
        raise _err(
            "group_gap is set but grouping is not 'dedicated_row', which never reads it — "
            "only dedicated_row inserts date sub-header rows with a gap between them.",
            layout_path=layout_path,
            key_path=f"{key_path}.group_gap",
            expected="group_gap only alongside grouping: dedicated_row.",
            recover="remove group_gap, or set grouping: dedicated_row.",
        )

    if "synthetic_row_placement" in block:
        if grouping != "dedicated_row":
            raise _err(
                "synthetic_row_placement is set but grouping is not 'dedicated_row', which "
                "never reads it — there is no group header for a synthetic row to be placed "
                "relative to.",
                layout_path=layout_path,
                key_path=f"{key_path}.synthetic_row_placement",
                expected="synthetic_row_placement only alongside grouping: dedicated_row.",
                recover="remove synthetic_row_placement, or set grouping: dedicated_row.",
            )
        if block["synthetic_row_placement"] not in SYNTHETIC_ROW_PLACEMENTS:
            raise _err(
                f"unknown synthetic_row_placement {block['synthetic_row_placement']!r}.",
                layout_path=layout_path,
                key_path=f"{key_path}.synthetic_row_placement",
                expected=f"one of {list(SYNTHETIC_ROW_PLACEMENTS)}.",
                recover=f"set synthetic_row_placement: to one of {list(SYNTHETIC_ROW_PLACEMENTS)}.",
            )

    if frame != "bordered" and "dividers" in block:
        raise _err(
            f"dividers is set but frame is '{frame}', which never draws them — only "
            "frame: bordered cuts the header/body into columns with dividers.",
            layout_path=layout_path,
            key_path=f"{key_path}.dividers",
            expected="dividers only alongside frame: bordered.",
            recover="remove dividers, or set frame: bordered.",
        )

    for ruled_only_key in ("header_rule_top", "header_rule_gap"):
        if frame != "ruled" and ruled_only_key in block:
            raise _err(
                f"{ruled_only_key} is set but frame is '{frame}', which never draws a header rule "
                "to adjust — only frame: ruled draws one.",
                layout_path=layout_path,
                key_path=f"{key_path}.{ruled_only_key}",
                expected=f"{ruled_only_key} only alongside frame: ruled.",
                recover=f"remove {ruled_only_key}, or set frame: ruled.",
            )

    columns = block["columns"]
    if not isinstance(columns, list) or not columns:
        raise _err(
            "table has no columns.",
            layout_path=layout_path,
            key_path=f"{key_path}.columns",
            expected="a non-empty list of {key, label, align, x|x_right} mappings.",
            recover="add at least one column.",
        )
    for index, column in enumerate(columns):
        for required in ("key", "label"):
            if not isinstance(column, dict) or required not in column:
                raise _err(
                    f"column {index} missing '{required}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}]",
                    expected="{key: date, label: Date, align: left, x: 0}.",
                    recover=f"add {required}: to the column.",
                )

        unknown_col_keys = sorted(set(column) - _COLUMN_KEYS)
        if unknown_col_keys:
            raise _err(
                f"column {index} has unknown key(s) {unknown_col_keys}.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected=f"only {sorted(_COLUMN_KEYS)}.",
                recover=f"remove {unknown_col_keys}, or add them to _COLUMN_KEYS in "
                "generators/layout_dsl/schema.py.",
            )

        for align_key in ("align", "label_align"):
            col_align = column.get(align_key)
            if col_align is not None and col_align not in COLUMN_ALIGNMENTS:
                raise _err(
                    f"column {index} has unknown {align_key} '{col_align}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}].{align_key}",
                    expected=f"one of {list(COLUMN_ALIGNMENTS)} (table columns do not support 'center').",
                    recover=f"set {align_key}: to one of {list(COLUMN_ALIGNMENTS)}.",
                )

        label_x = column.get("label_x")
        if label_x is not None and (not isinstance(label_x, int) or isinstance(label_x, bool)):
            raise _err(
                f"column {index} label_x is {label_x!r}, not an int.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].label_x",
                expected="an integer offset from the region's left edge, e.g. label_x: 1050.",
                recover="set label_x: to an int, or remove it so the header label sits at the "
                "column's own anchor.",
            )

        for field_key in ("field", "last_row_field", "prefix_field"):
            name = column.get(field_key)
            if isinstance(name, str) and name.startswith(DX_PREFIX):
                raise _dx_geometry_error(
                    name, layout_path=layout_path, key_path=f"{key_path}.columns[{index}].{field_key}"
                )
            if name is not None and name not in known_fields:
                raise _err(
                    f"column {index} {field_key!r} references unknown field '{name}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}].{field_key}",
                    expected=f"a field defined for this document type: {sorted(known_fields)}.",
                    recover=f"fix the field name, or add '{name}' to config/field_definitions.yml.",
                )

        # `prefix_key`/`prefix_field` only reach a drawing path through
        # `draw_fitted_left` (see `_draw_cell` in primitives_table.py): the
        # right-aligned and unbudgeted helpers take no prefix at all, so a
        # column declaring them anywhere else would silently record nothing.
        has_prefix = [key for key in ("prefix_key", "prefix_field") if key in column]
        if len(has_prefix) == 1:
            missing_key = "prefix_field" if has_prefix[0] == "prefix_key" else "prefix_key"
            raise _err(
                f"column {index} sets '{has_prefix[0]}' without '{missing_key}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].{has_prefix[0]}",
                expected="both keys together, e.g. prefix_key: quantity_prefix, "
                "prefix_field: LINE_ITEM_QUANTITIES.",
                recover=f"add '{missing_key}:' to the column, or remove '{has_prefix[0]}'.",
            )
        if has_prefix and ("budget" not in column or column.get("align") == "right"):
            raise _err(
                f"column {index} sets prefix_key/prefix_field but is "
                f"{'right-aligned' if column.get('align') == 'right' else 'unbudgeted'}.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected="a left-aligned column carrying a budget: — only the "
                "left-budgeted draw path measures a prefix sub-box.",
                recover="add a budget: to the column and leave it left-aligned, or remove "
                "prefix_key/prefix_field.",
            )

        sub_line = column.get("sub_line")
        if sub_line is not None and (not isinstance(sub_line, dict) or "key" not in sub_line):
            raise _err(
                f"column {index} sub_line is missing 'key'.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].sub_line",
                expected="{key: reference, role: sub_description, color: '#999999', "
                "offset_y: 34, height: 32} — only 'key' is required.",
                recover="add key: to the column's sub_line, naming the row field it reads.",
            )
        if sub_line is not None:
            _validate_sub_line_spans(
                sub_line,
                known_fields=known_fields,
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].sub_line",
            )
        if "x" not in column and "x_right" not in column:
            raise _err(
                f"column {index} has neither 'x' nor 'x_right'.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected="x: <offset from region left> or x_right: <offset from region right>.",
                recover="add x: or x_right: to position the column.",
            )

    for index, divider in enumerate(block.get("dividers", [])):
        if not isinstance(divider, dict) or ("x" not in divider and "x_right" not in divider):
            raise _err(
                f"divider {index} has neither 'x' nor 'x_right'.",
                layout_path=layout_path,
                key_path=f"{key_path}.dividers[{index}]",
                expected="x: <offset from region left> or x_right: <offset from region right>.",
                recover="add x: or x_right: to position the divider, e.g. {x_right: -320}.",
            )


def _validate_field_providers(layout: dict, *, layout_id: str, layout_path: str) -> list[str]:
    """Check a layout's `field_providers:` entries and return their combined emits.

    Presence of the `field_providers` key itself is checked by the caller
    (`validate_layout`, alongside `body` and `content_width`) -- this assumes
    the key exists and validates each entry's shape and content.

    Args:
        layout: The resolved layout dict, carrying `field_providers`.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        Every `emits` name from every provider this layout's
        `field_providers:` references -- the derived-field vocabulary
        `validate_body`'s `known_fields` must additionally accept for this
        one layout. Deliberately scoped to providers *this* layout actually
        references, not every provider ever registered: unioning in every
        registered provider's emits regardless of whether this layout uses it
        would let a `{FIELD}` typo that happens to collide with some other
        layout's derived field silently resolve, weakening the very check
        this exists to perform.

    Raises:
        LayoutSchemaError: If an entry names an unregistered provider, an
            entry's `params` key is not among that provider's declared
            params, or two providers on this layout declare an overlapping
            `emits` name.
    """
    emits: list[str] = []
    # Maps an emitted name to the provider that declared it, so a second
    # provider on this same layout declaring the same name is caught here --
    # statically, from the emits= declarations alone, no provider call
    # needed. This is the primary defence against two providers silently
    # overwriting one another's value; apply_field_providers in
    # field_providers.py keeps a second, defensive check at merge time for
    # callers that build a layout dict by hand and never go through
    # validate_layout -- both call the shared collect_emit_collisions() so
    # the two diagnostics cannot drift apart the way an earlier round of
    # this task found them doing.
    emitted_by: dict[str, str] = {}
    for index, spec in enumerate(layout["field_providers"]):
        here = f"{layout_id}.field_providers[{index}]"
        name = spec.get("name") if isinstance(spec, dict) else None
        if name is None:
            raise _err(
                "field_providers entry has no 'name' key.",
                layout_path=layout_path,
                key_path=here,
                expected="{name: <registered field provider>, params: {...}}.",
                recover=f"add a name: key to {here}.",
            )
        if name not in field_provider_names():
            raise _err(
                f"unknown field provider '{name}'.",
                layout_path=layout_path,
                key_path=f"{here}.name",
                expected=f"one of {field_provider_names()}.",
                recover="set name: to a registered field provider, or register one with "
                "@field_provider in generators/layout_dsl/field_providers.py.",
            )

        params = spec.get("params", {})
        accepted_params = field_provider_param_keys(name)
        unknown_params = sorted(set(params) - accepted_params)
        if unknown_params:
            raise _err(
                f"field_providers entry names unknown param(s) {unknown_params} for provider '{name}'.",
                layout_path=layout_path,
                key_path=f"{here}.params",
                expected=f"one of {sorted(accepted_params)} for provider '{name}'.",
                recover=f"fix the typo, or add {unknown_params} to provider '{name}''s "
                "params=frozenset({...}) in generators/layout_dsl/field_providers.py.",
            )

        provider_emits = field_provider_emits(name)
        collision = collect_emit_collisions(emitted_by, str(name), provider_emits)
        if collision is not None:
            what, expected, recover = collision
            raise _err(
                what,
                layout_path=layout_path,
                key_path=f"{here}.name",
                expected=expected,
                recover=recover,
            )
        emitted_by.update(dict.fromkeys(provider_emits, str(name)))

        emits.extend(provider_emits)

    return emits


def _validate_split_widths(widths: object, *, children: list, layout_path: str, key_path: str) -> None:
    """Check a split's explicit `widths` list is a non-empty list of positive
    ints, one per column.

    `widths` replaces equal division for columns that must not be equal --
    invoice totals' fixed 400px column at the right edge -- so a mismatch
    with the actual column count (e.g. a column added without updating
    widths) is an authoring error, not silently absorbed by falling back to
    equal division.
    """
    if not isinstance(widths, list) or not widths:
        raise _err(
            f"split widths must be a non-empty list, got {widths!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected="a non-empty list of positive ints, one per column, e.g. widths: [1300, 400].",
            recover=f"set widths: to a list of {len(children)} positive ints, one per child column.",
        )
    non_positive = [w for w in widths if not isinstance(w, int) or isinstance(w, bool) or w <= 0]
    if non_positive:
        raise _err(
            f"split widths must all be positive ints, got {widths!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected="a list of positive ints, e.g. widths: [1300, 400].",
            recover=f"fix the non-positive or non-int entr{'y' if len(non_positive) == 1 else 'ies'} "
            "in widths.",
        )
    if len(widths) != len(children):
        raise _err(
            f"split widths has {len(widths)} entries but children has {len(children)} columns.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected=f"widths: a list of exactly {len(children)} ints, matching children's column count.",
            recover=f"add or remove entries so widths has exactly {len(children)} values, "
            "one per child column.",
        )


def _validate_children(
    block: dict, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Recurse into a container's children.

    `panel` takes a flat list of blocks; `split` takes a list of such lists,
    one per column.
    """
    children = block["children"]
    if block["type"] == "split":
        if not isinstance(children, list) or len(children) < 2:
            raise _err(
                "split needs at least two child columns.",
                layout_path=layout_path,
                key_path=f"{key_path}.children",
                expected="a list of at least two lists of blocks.",
                recover="add a second column, or use panel for a single column.",
            )
        if "widths" in block:
            _validate_split_widths(
                block["widths"], children=children, layout_path=layout_path, key_path=key_path
            )
        for index, column in enumerate(children):
            _validate_blocks(
                column,
                layout_id=layout_id,
                layout_path=layout_path,
                known_fields=known_fields,
                key_path=f"{key_path}.children[{index}]",
            )
    else:
        _validate_blocks(
            children,
            layout_id=layout_id,
            layout_path=layout_path,
            known_fields=known_fields,
            key_path=f"{key_path}.children",
        )


# Primitive kinds whose own `role:` key (explicit, or the layout's default
# role when omitted) is resolved through line_advance() at render time --
# text/pair/block draw a line and advance the y-cursor by it. A table block
# also resolves its own advance this way (for its header row / multi-line
# header labels), through its own `role:` key when it carries one -- a table
# draws every label and cell at that role's size -- and the layout's default
# role otherwise.
# banner is deliberately excluded: its own `role:` key selects banner_role's
# font size, but draw_banner never calls line_advance() -- it always leaves
# the y-cursor unchanged -- so a banner's role has no bearing on this check.
_LINE_ADVANCE_ROLE_PRIMITIVES = ("text", "pair", "block", "barcode", "qrcode")


def _line_advance_roles(blocks: list, *, default_role: str) -> set[str]:
    """Collect every role a layout's body resolves through line_advance().

    Recurses into panel/split children (the same reach `_validate_blocks`/
    `_validate_children` give every other structural check in this module)
    and into each table column's `sub_line` spec -- a role buried inside a
    nested container or a sub_line must not be invisible to this walk, the
    same way `bank_statements.yml`'s `sub_line: {role: sub_description}` on
    a table column would be to a naive top-level-only scan.

    Args:
        blocks: A list of block dicts (a body, or one nesting level of it).
        default_role: The layout's own `defaults.role`, used wherever a
            block (or sub_line) resolving through line_advance omits its
            own `role:` key.

    A block (or sub_line) carrying its own bare-integer `line_advance:`
    override is skipped: `line_advance()`'s block-key-wins-over-layout
    resolution means that block's role is never actually looked up in the
    layout's mapping at render time, so requiring the layout to cover it
    anyway would make this check stricter than the code it is guarding.

    Returns:
        Every role name this subtree resolves through line_advance().
    """
    roles: set[str] = set()
    for block in blocks:
        kind = block.get("type")
        if kind in _LINE_ADVANCE_ROLE_PRIMITIVES:
            if "line_advance" not in block:
                roles.add(str(block.get("role", default_role)))
        elif kind == "table":
            if "line_advance" not in block:
                roles.add(str(block.get("role", default_role)))
            for column in block.get("columns", []):
                sub_line = column.get("sub_line") if isinstance(column, dict) else None
                if isinstance(sub_line, dict) and "line_advance" not in sub_line:
                    roles.add(str(sub_line.get("role", default_role)))
        elif kind in _CONTAINERS:
            children = block.get("children", [])
            if kind == "split":
                for column in children:
                    roles |= _line_advance_roles(column, default_role=default_role)
            else:
                roles |= _line_advance_roles(children, default_role=default_role)
    return roles


def _layout_key_references(blocks: list) -> set[str]:
    """Collect every layout key this subtree names through a block key.

    Recurses into `panel` and `split` children -- the same reach
    `_validate_blocks`/`_validate_children` give every other structural check
    in this module -- so a `from_layout:` buried in a nested container is not
    invisible to it. A `table` block is deliberately not descended into: its
    columns hold no blocks, and a column's `sub_line: {key: ...}` names a key
    of the *row* the provider yields, not a layout key. `_COLUMN_KEYS` admits
    no `from_layout` at any depth below a table, so there is nothing there to
    find.

    Args:
        blocks: A list of block dicts (a body, or one nesting level of it).

    Returns:
        Every layout key named by a `from_layout:` or `suppress_if_equals:`
        in this subtree. Whether each key actually exists on the layout is
        `_validate_geometry`'s job, not this one's.
    """
    names: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in _LAYOUT_KEY_REFERENCES:
            value = block.get(key)
            if isinstance(value, str):
                names.add(value)
        from_layout = block.get("params_from_layout")
        if isinstance(from_layout, dict):
            names |= {str(key) for key in from_layout.values()}
        if block.get("type") in _CONTAINERS:
            children = block.get("children", []) or []
            if block.get("type") == "split":
                for column in children:
                    if isinstance(column, list):
                        names |= _layout_key_references(column)
            elif isinstance(children, list):
                names |= _layout_key_references(children)
    return names


def known_layout_keys(layout: dict) -> frozenset[str]:
    """Return the top-level keys this layout is permitted to carry.

    The permitted set cannot be hand-listed, because `from_layout:` (and
    `suppress_if_equals:`) let a `text` or `banner` block name an *arbitrary*
    layout key as its content -- `bank_statements.yml`'s `logo_text`,
    `receipts.yml`'s `footer_text`. So it is the fixed set of keys the engine,
    the page adapters and the corpus tooling read, widened by whatever this
    layout's own body actually references. A layout adding a new
    `from_layout:` target needs no change here.

    Args:
        layout: The resolved layout dict.

    Returns:
        Every key this layout may carry. Anything else it carries is read by
        nothing, and `validate_layout` rejects it.
    """
    return (
        _ENGINE_LAYOUT_KEYS
        | _PAGE_LAYOUT_KEYS
        | _CORPUS_LAYOUT_KEYS
        | frozenset(_layout_key_references(layout.get("body", []) or []))
    )


class _Ungated(NamedTuple):
    """A drawn `DX_` placeholder whose block's `when:` is not that same key."""

    path: str  # the block's dotted path below the layout id, e.g. body[3].children[0]
    key: str  # the block key holding the placeholder, e.g. caption
    name: str  # the DX_ key drawn
    when: object  # the block's `when:` value, or None


def _collect_dx_references(
    node: object,
    *,
    found: set[str],
    ungated: list[_Ungated],
    misplaced: list[tuple[str, str]],
    key: str = "",
    path: str = "body",
) -> None:
    """Walk a body subtree, recording every `DX_` name it references.

    Only the strings the renderer interpolates (`_TEMPLATE_KEYS` and the entries of `lines`, on a
    block that has a `type`) count as drawn; a `DX_` placeholder anywhere else is not drawn.

    Args:
        node: A block, a list of blocks, or any nested value.
        found: Receives each `DX_` name drawn by a template string, and each `DX_` `when:`.
        ungated: Receives an `_Ungated` for each drawn `DX_` placeholder whose block's `when:` is not
            that same name; `_check_dx_gating` decides whether the block's gate covers it.
        misplaced: Receives (key, name) for each `DX_` placeholder in a string the renderer never
            interpolates (a table column label, say).
        key: The key under which `node` sits.
        path: The dotted path of `node` below the layout id, named in diagnostics.
    """
    if isinstance(node, str):
        return
    if isinstance(node, list):
        for index, child in enumerate(node):
            if isinstance(child, str):
                names = [n for n in referenced_fields(child) if n.startswith(DX_PREFIX)]
                if names:
                    misplaced.extend((key, n) for n in names)
                continue
            _collect_dx_references(
                child, found=found, ungated=ungated, misplaced=misplaced, key=key, path=f"{path}[{index}]"
            )
        return
    if not isinstance(node, dict):
        return
    typed = "type" in node
    when = node.get("when")
    if isinstance(when, str) and when.startswith(DX_PREFIX):
        found.add(when)
    for name, value in node.items():
        if isinstance(value, str):
            names = [n for n in referenced_fields(value) if n.startswith(DX_PREFIX)]
            if typed and name in _TEMPLATE_KEYS:
                found.update(names)
                ungated.extend(_Ungated(path, name, n, when) for n in names if when != n)
            else:
                misplaced.extend((name, n) for n in names)
        elif typed and name == "lines" and isinstance(value, list):
            for line in value:
                if isinstance(line, str):
                    names = [n for n in referenced_fields(line) if n.startswith(DX_PREFIX)]
                    found.update(names)
                    ungated.extend(_Ungated(path, "lines", n, when) for n in names if when != n)
        else:
            _collect_dx_references(
                value, found=found, ungated=ungated, misplaced=misplaced, key=name, path=f"{path}.{name}"
            )


def _check_dx_gating(
    ungated: list[_Ungated], listed: list[str], *, layout_id: str, layout_path: str
) -> None:
    """Refuse a block drawing a `DX_` key its `when:` does not cover (Ruling 7).

    A block is gated on one key. It may draw that key and any other `DX_` key whose catalogue item the
    gate's item requires, directly or transitively: the provider draws a required item whenever it draws
    its dependant, so the gate's presence guarantees every covered key is present too (a QR survey URL
    gated on `DX_SURVEY_URL` may caption `{DX_SURVEY_CODE}`, since `rc_survey_url` requires
    `rc_survey_code`). Any other drawn key is uncovered.

    Args:
        ungated: Every drawn placeholder whose block's `when:` is not that same key.
        listed: The layout's `distractor_items`, each already checked to be a catalogue item; every drawn
            `DX_` key is already known to be emitted by one of them.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Raises:
        LayoutSchemaError: A block draws a `DX_` key its gate does not cover, naming every uncovered key
            on that block.
    """
    config = load_distractor_config()
    owner = {key: item_id for item_id in listed for key in config.items[item_id].emits}
    by_block: dict[str, list[_Ungated]] = {}
    for ref in ungated:
        by_block.setdefault(ref.path, []).append(ref)
    for path, refs in by_block.items():
        when = refs[0].when
        gate_item = owner.get(when) if isinstance(when, str) else None
        # The gate's own item counts as covered: every key an item emits is drawn with the others.
        covered = required_items(config, gate_item) | {gate_item} if gate_item is not None else set()
        uncovered = sorted({ref.name for ref in refs if owner[ref.name] not in covered})
        if not uncovered:
            continue
        keys = sorted({ref.key for ref in refs if ref.name in uncovered})
        if gate_item is None:
            gate = f"gated on {when!r}, not a DX_ key" if when is not None else "not gated with when:"
        else:
            gate = (
                f"gated on '{when}', whose item '{gate_item}' requires "
                f"{sorted(covered) if covered else 'no other item'}"
            )
        example = uncovered[0]
        raise _err(
            f"a block draws {uncovered} (in its {keys}) but is {gate}, so those keys are uncovered: an "
            "ordinary render would print the placeholder text blank or fail.",
            layout_path=layout_path,
            key_path=f"{layout_id}.{path}",
            expected="a block gated on one DX_ key, drawing only that key and keys whose items the gate's "
            "item requires (directly or transitively) in config/distractors.yml, e.g.\n"
            f'              {{type: text, content: "{{{example}}}", when: {example}}}',
            recover=f"gate the block on a key whose item requires the items of {uncovered}, split it into "
            f"one block per key, or add the requires: entry in config/distractors.yml.",
        )


def _validate_distractor_provider(
    layout: dict, listed: list[str], *, canonical: str, layout_id: str, layout_path: str
) -> None:
    """Check `distractor_items:` and the layout's distractor provider agree.

    The provider draws from the layout's own list (`apply_field_providers` hands it over), so the two
    must arrive together: a list with no provider would never be drawn, a provider for another type
    would screen against the wrong scored columns, and a provider with fewer listed items than the
    catalogue's minimum would fail on the first render.

    Args:
        layout: The resolved layout dict.
        listed: Its `distractor_items`, already checked to be catalogue ids for `canonical`.
        canonical: The layout's extraction-schema document type.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Raises:
        LayoutSchemaError: A distractor provider for another document type is listed; the list is
            non-empty with no distractor provider for `canonical`; or the provider is present with
            fewer listed items than `selection.<type>.count[0]` in config/distractors.yml.
    """
    own = distractor_provider_names(canonical)
    example_provider = own[0] if own else f"<the {canonical} distractor provider>"
    present: list[tuple[int, str]] = [
        (index, spec["name"])
        for index, spec in enumerate(layout["field_providers"])
        if isinstance(spec, dict)
        and spec.get("name") in field_provider_names()
        and is_distractor_provider(spec["name"])
    ]
    for index, name in present:
        drawn_for = distractor_doc_type(name)
        if drawn_for != canonical:
            raise _err(
                f"field provider '{name}' draws {drawn_for} distractors, but this is a {canonical} layout.",
                layout_path=layout_path,
                key_path=f"{layout_id}.field_providers[{index}].name",
                expected=f"only the {canonical} distractor provider, {{name: {example_provider}}}.",
                recover=f"replace '{name}' with '{example_provider}', or remove it from "
                f"{layout_id}.field_providers.",
            )
    if listed and not present:
        raise _err(
            f"distractor_items lists {listed}, but field_providers has no {canonical} distractor "
            "provider to draw them.",
            layout_path=layout_path,
            key_path=f"{layout_id}.field_providers",
            expected=f"a field_providers entry {{name: {example_provider}}} beside the listed items, e.g.\n"
            "              field_providers:\n"
            f"                - {{name: {example_provider}}}",
            recover=f"add {{name: {example_provider}}} to {layout_id}.field_providers, or empty "
            f"{layout_id}.distractor_items.",
        )
    low = load_distractor_config().selection[canonical][0]
    if present and len(listed) < low:
        raise _err(
            f"field provider '{present[0][1]}' selects at least {low} items, but distractor_items lists "
            f"{len(listed)}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.distractor_items",
            expected=f"at least {low} items (config/distractors.yml -> selection.{canonical}.count[0]), "
            "each drawn by a gated block.",
            recover=f"list at least {low} items in {layout_id}.distractor_items, or lower "
            f"selection.{canonical}.count in config/distractors.yml.",
        )


def _validate_state_groups(listed: list[str], *, canonical: str, layout_id: str, layout_path: str) -> None:
    """Check a layout drawing distractors lists every item its type's states group.

    Each value of a state (a paid or an unpaid invoice) prints its own group of items; a layout missing
    one would print an incomplete state, a paid invoice with no amount paid, say.

    Raises:
        LayoutSchemaError: The layout lists items but not every item of every state group.
    """
    if not listed:
        return
    for name, state in load_distractor_config().states[canonical].items():
        for choice, ids in state.groups.items():
            missing = [item_id for item_id in ids if item_id not in listed]
            if missing:
                raise _err(
                    f"distractor_items omits {missing}, the items config/distractors.yml groups under "
                    f"the {name} state's '{choice}' value, so a {choice} {canonical} would print an "
                    "incomplete state.",
                    layout_path=layout_path,
                    key_path=f"{layout_id}.distractor_items",
                    expected=f"every item of states.{canonical}.{name}.items.{choice} in the list, "
                    f"each drawn by a gated block, e.g. distractor_items: [..., {missing[0]}].",
                    recover=f"add {missing} to {layout_id}.distractor_items with blocks drawing them, or "
                    f"remove them from states.{canonical}.{name}.items.{choice} in config/distractors.yml.",
                )


_BARE_DX_KEY = re.compile(rf"^{DX_PREFIX}[A-Z0-9_]+$")
# Overlay block parameters naming an optional key (Decision 10 of the overlay plan): the block draws
# whether or not that key is present, so the gate need not cover it; instead its item must require the
# gate's item, so the key is never drawn without the block that prints it.
_OPTIONAL_KEY_PARAMS = ("date_key",)


def _absolute_layout_path(layout_path: str) -> Path:
    path = Path(layout_path)
    return path if path.is_absolute() else REPO_ROOT / path


def _overlay_references(node: object) -> set[str]:
    """Every `DX_` key an overlay block's parameters name, as a `{placeholder}` or as a bare key value."""
    if isinstance(node, str):
        names = {n for n in referenced_fields(node) if n.startswith(DX_PREFIX)}
        if _BARE_DX_KEY.match(node):
            names.add(node)
        return names
    if isinstance(node, list):
        return {name for child in node for name in _overlay_references(child)}
    if isinstance(node, dict):
        return {name for key, value in node.items() for name in _overlay_references(value)}
    return set()


def _validate_overlay(
    layout: dict, *, layout_id: str, layout_path: str, canonical: str, listed: list[str]
) -> set[str]:
    """Check a layout's `overlay:` blocks and return every `DX_` key they draw.

    Each block names a registered overlay type and is gated (`when:`) on a `DX_` key a listed item
    emits; every other `DX_` key it names must be covered by that gate (Ruling 7), as for body blocks,
    except a key named by an optional-key parameter (`date_key`), whose item must instead require the
    gate's item (Decision 10). A layout of a document type whose `overlay.protect` policy is
    `no_overlay` (receipts, in config/distractors.yml) must declare an empty overlay. A `stamp` block's
    own keys are checked by `_validate_stamp_block`.

    Args:
        layout: The resolved layout dict, already known to carry `overlay`.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, made absolute in diagnostics.
        canonical: The layout's extraction-schema document type.
        listed: Its `distractor_items`, each already checked to be a catalogue item for `canonical`.

    Returns:
        The `DX_` keys the overlay gates on or draws, so a listed item drawn only by the overlay counts as
        drawn.

    Raises:
        LayoutSchemaError: The overlay is not a list; a receipt's is non-empty; a block is not a mapping,
            names an unregistered type, is not gated on a `DX_` key a listed item emits, or names a `DX_`
            key its gate does not cover.
    """
    where = str(_absolute_layout_path(layout_path))
    here = f"{layout_id}.overlay"
    blocks = layout["overlay"]
    if not isinstance(blocks, list):
        raise _err(
            f"{here} is {blocks!r}, not a list of blocks.",
            layout_path=where,
            key_path=here,
            expected="overlay: [] or a list of blocks, each with type and when, e.g.\n"
            "              overlay: []",
            recover="add overlay: [] to the layout, or rewrite it as a list of blocks.",
        )
    config = load_distractor_config()
    if blocks and config.overlay.protect[canonical] == NO_OVERLAY:
        raise _err(
            f"{here} lists {len(blocks)} block(s), but overlay.protect.{canonical} is {NO_OVERLAY} in "
            f"{DISTRACTORS_PATH}: a {canonical} carries inline distractors only.",
            layout_path=where,
            key_path=here,
            expected=f"overlay: [] on every {canonical} layout.",
            recover=f"set {here} to [] and draw the marks as inline blocks in {layout_id}.body.",
        )
    # The overlay primitives register themselves when the overlay engine is imported; importing it here,
    # at call time, keeps the schema free of an import cycle (the primitives use the DSL's binding).
    import generators.overlay.engine  # noqa: F401, PLC0415
    from generators.overlay.stamp import STAMP_TYPE  # noqa: PLC0415

    page = layout.get("page_dimensions")

    owner = {key: item_id for item_id in listed for key in config.items[item_id].emits}
    drawn: set[str] = set()
    for index, block in enumerate(blocks):
        at = f"{here}[{index}]"
        registered = sorted(OVERLAY_DRAWERS)
        if not isinstance(block, dict):
            raise _err(
                f"{at} is {block!r}, not a block mapping.",
                layout_path=where,
                key_path=at,
                expected=f"a mapping with type (one of {registered}), when and the type's own keys.",
                recover=f"rewrite {at} as a block mapping, or remove it.",
            )
        kind = block.get("type")
        if kind not in OVERLAY_DRAWERS:
            raise _err(
                f"{at}.type is {kind!r}, which no overlay primitive registers.",
                layout_path=where,
                key_path=f"{at}.type",
                expected=f"one of the registered overlay types {registered}.",
                recover=f"use a registered type in {at}, or remove the block.",
            )
        when = block.get("when")
        example = sorted(owner)[0] if owner else "DX_<key>"
        if not isinstance(when, str) or not when.startswith(DX_PREFIX) or when not in owner:
            raise _err(
                f"{at}.when is {when!r}, which no item in {layout_id}.distractor_items emits; an overlay "
                "block draws only when its distractor was selected.",
                layout_path=where,
                key_path=f"{at}.when",
                expected=f"when: a DX_ key emitted by a listed item, e.g. when: {example}.",
                recover=f"gate {at} on a listed item's key, or list the item that emits {when!r} in "
                f"{layout_id}.distractor_items.",
            )
        gate_item = owner[when]
        covered = required_items(config, gate_item) | {gate_item}
        optional = _overlay_references({k: v for k, v in block.items() if k in _OPTIONAL_KEY_PARAMS})
        required = _overlay_references(
            {k: v for k, v in block.items() if k not in ("type", "when", *_OPTIONAL_KEY_PARAMS)}
        )
        named = required | optional
        unlisted = sorted(named - set(owner))
        if unlisted:
            raise _err(
                f"{at} names {unlisted}, which no item in {layout_id}.distractor_items emits.",
                layout_path=where,
                key_path=at,
                expected="every DX_ key an overlay block names is emitted by a listed item.",
                recover=f"list the item that emits {unlisted} in {layout_id}.distractor_items, or remove "
                "the reference.",
            )
        uncovered = sorted(name for name in required if owner[name] not in covered)
        loose = sorted(name for name in optional if gate_item not in required_items(config, owner[name]))
        if loose:
            params = [k for k in _OPTIONAL_KEY_PARAMS if k in block]
            raise _err(
                f"{at} names {loose} in its optional {params}, but their items "
                f"{sorted({owner[name] for name in loose})} do not require '{gate_item}', the item of its "
                f"gate '{when}', so the optional key could be drawn on a page where the block never prints "
                "it.",
                layout_path=where,
                key_path=f"{at}.{params[0]}",
                expected=f"an optional key whose item requires the gate's item in config/distractors.yml, "
                f"e.g. requires: [{gate_item}] on the item emitting {loose[0]}.",
                recover=f"add {gate_item} to requires: of {sorted({owner[name] for name in loose})} in "
                f"{DISTRACTORS_PATH}, or set {at}.{params[0]} to none.",
            )
        if uncovered:
            raise _err(
                f"{at} names {uncovered} but is gated on '{when}', whose item '{gate_item}' requires "
                f"{sorted(covered - {gate_item}) or 'no other item'}, so those keys may be absent when it "
                "draws.",
                layout_path=where,
                key_path=at,
                expected="a block naming only its gate's key and keys whose items the gate's item "
                "requires (directly or transitively) in config/distractors.yml.",
                recover=f"gate {at} on a key whose item requires the items of {uncovered}, or add the "
                "requires: entry in config/distractors.yml.",
            )
        if kind == STAMP_TYPE:
            _validate_stamp_block(
                block, at=at, where=where, canonical=canonical, gate_item=gate_item, owner=owner, page=page
            )
        elif kind in _annotation_keys():
            _validate_annotation_block(
                block, layout, at=at, where=where, gate_item=gate_item, covered=covered, owner=owner
            )
        drawn |= named | {when}
    return drawn


def _annotation_keys() -> dict[str, tuple[tuple[str, ...], str]]:
    """Each annotation block type -> its exact keys and an example block (see `_validate_overlay`)."""
    from generators.overlay.annotation import (  # noqa: PLC0415  (see _validate_overlay)
        HIGHLIGHT_BLOCK_KEYS,
        HIGHLIGHT_TYPE,
        INK_MARKS_BLOCK_KEYS,
        INK_MARKS_TYPE,
    )
    from generators.overlay.handwriting import HANDWRITING_BLOCK_KEYS, HANDWRITING_TYPE  # noqa: PLC0415

    return {
        HIGHLIGHT_TYPE: (
            HIGHLIGHT_BLOCK_KEYS,
            "{type: highlight, when: DX_ANNOTATED_ROWS, rows: DX_ANNOTATED_ROWS, row_fields: "
            "[TRANSACTION_DESCRIPTIONS, TRANSACTION_AMOUNTS_PAID], exclude_colours: []}",
        ),
        INK_MARKS_TYPE: (
            INK_MARKS_BLOCK_KEYS,
            "{type: ink_marks, when: DX_ROW_MARKS, rows: DX_ANNOTATED_ROWS, marks: DX_ROW_MARKS, "
            "target_field: TRANSACTION_AMOUNTS_PAID, kinds: [tick, circle, underline], tick_lane: left_margin}",
        ),
        HANDWRITING_TYPE: (
            HANDWRITING_BLOCK_KEYS,
            "{type: handwriting, when: DX_MARGIN_NOTE, content: '{DX_MARGIN_NOTE}', rows: DX_ANNOTATED_ROWS, "
            "description_field: TRANSACTION_DESCRIPTIONS, column_field: TRANSACTION_AMOUNTS_PAID, lanes: "
            "[after_description, left_margin]}",
        ),
    }


def _table_fields(node: object) -> set[str]:
    """Every indexed field a table in the body records: its columns' `field:` values."""
    found: set[str] = set()
    if isinstance(node, list):
        for child in node:
            found |= _table_fields(child)
    elif isinstance(node, dict):
        if node.get("type") == "table":
            found |= {
                str(c["field"]) for c in node.get("columns", []) if isinstance(c, dict) and c.get("field")
            }
        for value in node.values():
            if isinstance(value, list | dict):
                found |= _table_fields(value)
    return found


def _validate_annotation_block(
    block: dict,
    layout: dict,
    *,
    at: str,
    where: str,
    gate_item: str,
    covered: set[str],
    owner: dict[str, str],
) -> None:
    """Check a `highlight`, `ink_marks` or `handwriting` block's own keys (generators/overlay/).

    Raises:
        LayoutSchemaError: A key is missing or unknown; a `rows:` or `marks:` value is not a `DX_` key
            of the gate's item or an item it requires; `kinds:` misses a kind the `row_marks` rule of
            the `marks:` item can choose, or names one the primitive cannot draw; a lane is unknown (or,
            for `tick_lane`, not a margin); or a field key names a field the layout's table does not
            record per row.
    """
    from generators.overlay.annotation import LANES, MARK_KINDS, TICK_LANES  # noqa: PLC0415

    config = load_distractor_config()
    kind = block["type"]
    keys, example = _annotation_keys()[kind]
    present = set(block)
    if present != set(keys):
        missing, unknown = sorted(set(keys) - present), sorted(present - set(keys))
        raise _err(
            f"{at} has the keys {sorted(present)}; a {kind} block carries exactly {list(keys)}"
            + (f" (missing {missing})" if missing else "")
            + (f" (unknown {unknown})" if unknown else "")
            + ".",
            layout_path=where,
            key_path=at,
            expected=f"a {kind} block such as {example}.",
            recover=f"give {at} exactly the keys {list(keys)}.",
        )
    if "exclude_colours" in block:
        excluded = block["exclude_colours"]
        names = sorted(config.overlay.highlighter.colours)
        listed = isinstance(excluded, list) and all(name in names for name in excluded)
        if not listed or (isinstance(excluded, list) and set(names) <= set(excluded)):
            raise _err(
                f"{at}.exclude_colours is {excluded!r}: it must list highlighter colour names, leaving at "
                "least one to draw.",
                layout_path=where,
                key_path=f"{at}.exclude_colours",
                expected=f"a list of names from overlay.highlighter.colours {names} in {DISTRACTORS_PATH}, "
                "not all of them; [] for none, e.g. exclude_colours: [blue].",
                recover=f"correct {at}.exclude_colours.",
            )
    for param in ("rows", "marks"):
        if param not in block:
            continue
        value = block[param]
        if not (isinstance(value, str) and _BARE_DX_KEY.match(value) and owner.get(value) in covered):
            raise _err(
                f"{at}.{param} is {value!r}, not a DX_ key of '{gate_item}' (the gate's item) or of an item "
                "it requires, so it may be absent when the block draws.",
                layout_path=where,
                key_path=f"{at}.{param}",
                expected=f"one of {sorted(k for k, item in owner.items() if item in covered)}, e.g. "
                f"{param}: DX_ANNOTATED_ROWS.",
                recover=f"set {at}.{param} to a key of {sorted(covered)}, or add the item emitting it to "
                f"requires: of {gate_item} in {DISTRACTORS_PATH}.",
            )
    if "kinds" in block:
        kinds = block["kinds"]
        rule = config.items[owner[block["marks"]]].rule
        chosen = list(rule.get("marks", [])) if rule["type"] == "row_marks" else []
        valid = isinstance(kinds, list) and kinds and all(k in MARK_KINDS for k in kinds)
        unmet = sorted(set(chosen) - set(kinds)) if isinstance(kinds, list) else chosen
        if not valid or unmet or not chosen:
            raise _err(
                f"{at}.kinds is {kinds!r}"
                + (
                    f", which misses {unmet}, kind(s) the row_marks rule of '{owner[block['marks']]}' can "
                    "choose, so a row could be given a mark this block never draws"
                    if unmet
                    else ""
                )
                + (f"; '{owner[block['marks']]}' does not draw a row_marks rule" if not chosen else "")
                + ".",
                layout_path=where,
                key_path=f"{at}.kinds",
                expected=f"a list of kinds from {list(MARK_KINDS)} covering every kind the rule can choose "
                f"({chosen or list(MARK_KINDS)}), e.g. kinds: [tick, circle, underline].",
                recover=f"add {unmet or 'the missing kinds'} to {at}.kinds, or narrow the rule's marks in "
                f"{DISTRACTORS_PATH} (a kind is never swapped for another at render time).",
            )
    lanes = {"tick_lane": [block["tick_lane"]]} if "tick_lane" in block else {}
    if "lanes" in block:
        lanes["lanes"] = block["lanes"] if isinstance(block["lanes"], list) and block["lanes"] else [None]
    for param, values in lanes.items():
        allowed = TICK_LANES if param == "tick_lane" else LANES
        bad = [v for v in values if v not in allowed]
        if bad:
            raise _err(
                f"{at}.{param} names {bad}, not "
                + (
                    "a margin lane (a tick sits level with its row in a margin)"
                    if param == "tick_lane"
                    else "a lane"
                )
                + f"; the lanes are {list(LANES)}.",
                layout_path=where,
                key_path=f"{at}.{param}",
                expected=f"{'one' if param == 'tick_lane' else 'a non-empty list'} of {list(allowed)}, e.g. "
                + (
                    "tick_lane: left_margin."
                    if param == "tick_lane"
                    else "lanes: [after_description, left_margin]."
                ),
                recover=f"correct {at}.{param}.",
            )
    recorded = _table_fields(layout.get("body", []))
    for param in ("row_fields", "target_field", "description_field", "column_field"):
        if param not in block:
            continue
        names = block[param] if param == "row_fields" else [block[param]]
        bad = [n for n in names if n not in recorded] if isinstance(names, list) and names else [names]
        if bad:
            raise _err(
                f"{at}.{param} names {bad}, which no table in the layout records per row (a column's "
                "field:), so the annotated rows cannot be found by their boxes.",
                layout_path=where,
                key_path=f"{at}.{param}",
                expected=f"fields from {sorted(recorded) or 'a table column field:'}, e.g. "
                f"{param}: "
                + (
                    "[TRANSACTION_DESCRIPTIONS, TRANSACTION_AMOUNTS_PAID]."
                    if param == "row_fields"
                    else "TRANSACTION_AMOUNTS_PAID."
                ),
                recover=f"name a recorded table field in {at}.{param}, or add field: to the table column.",
            )


def _validate_stamp_block(
    block: dict,
    *,
    at: str,
    where: str,
    canonical: str,
    gate_item: str,
    owner: dict[str, str],
    page: object,
) -> None:
    """Check a `stamp` overlay block's own keys (generators/overlay/stamp.py).

    Raises:
        LayoutSchemaError: A key is missing or unknown; `content` is not a string naming only `pick`
            words; `date_key` is neither a `DX_` key nor `none`; `candidates` is not a non-empty list of
            `{x, y}` page fractions in [0, 1]; a candidate's footprint at the largest rotation, word and
            date cannot fit inside the page inset; or the gate's item is not grouped as paid under the
            document type's payment state.
    """
    from generators.overlay.stamp import (  # noqa: PLC0415  (see _validate_overlay)
        DATE_SAMPLES,
        NO_DATE,
        PAID_STATE,
        STAMP_BLOCK_KEYS,
        rotated_extent,
        tile_size,
    )

    state_name, paid = PAID_STATE

    config = load_distractor_config()
    example = (
        "{type: stamp, when: DX_PAID_STAMP, content: '{DX_PAID_STAMP}', date_key: DX_PAID_DATE, "
        "candidates: [{x: 0.72, y: 0.16}, {x: 0.45, y: 0.5}]}"
    )
    keys = set(block)
    if keys != set(STAMP_BLOCK_KEYS):
        raise _err(
            f"{at} has the keys {sorted(keys)}; a stamp block carries exactly {list(STAMP_BLOCK_KEYS)}"
            + (f" (missing {sorted(set(STAMP_BLOCK_KEYS) - keys)})" if set(STAMP_BLOCK_KEYS) - keys else "")
            + (f" (unknown {sorted(keys - set(STAMP_BLOCK_KEYS))})" if keys - set(STAMP_BLOCK_KEYS) else "")
            + ".",
            layout_path=where,
            key_path=at,
            expected=f"a stamp block such as {example}.",
            recover=f"give {at} exactly the keys {list(STAMP_BLOCK_KEYS)}.",
        )
    state = config.states.get(canonical, {}).get(state_name)
    if state is None or gate_item not in state.groups.get(paid, ()):
        raise _err(
            f"{at} stamps '{block['when']}', whose item '{gate_item}' is not grouped under "
            f"states.{canonical}.{state_name}.items.{paid} in {DISTRACTORS_PATH}, so the stamp "
            "could share a page with an unpaid invoice's remittance slip or account summary.",
            layout_path=where,
            key_path=f"{at}.when",
            expected=f"the stamp's item in the {paid} group, e.g. states: {{{canonical}: "
            f"{{{state_name}: {{items: {{{paid}: [..., {gate_item}]}}}}}}}}.",
            recover=f"add '{gate_item}' to states.{canonical}.{state_name}.items.{paid} in "
            f"{DISTRACTORS_PATH}, or remove {at}.",
        )
    content = block["content"]
    picked = referenced_fields(content) if isinstance(content, str) else []
    unpicked = [name for name in picked if config.items[owner[name]].rule["type"] != "pick"]
    if not isinstance(content, str) or not content.strip() or not picked or unpicked:
        raise _err(
            f"{at}.content is {content!r}; a stamp prints words drawn by a pick rule, so its size is known "
            "before any case is drawn"
            + (f", but {unpicked} are drawn by other rules" if unpicked else "")
            + ".",
            layout_path=where,
            key_path=f"{at}.content",
            expected="a string naming the stamp's word, e.g. content: '{DX_PAID_STAMP}', its item drawn by "
            "{type: pick, values: [PAID, PAID IN FULL, RECEIVED]}.",
            recover=f"set {at}.content to the gate's placeholder, or draw its item with a pick rule.",
        )
    date_key = block["date_key"]
    if date_key != NO_DATE and not (isinstance(date_key, str) and _BARE_DX_KEY.match(date_key)):
        raise _err(
            f"{at}.date_key is {date_key!r}, neither a DX_ key nor {NO_DATE}.",
            layout_path=where,
            key_path=f"{at}.date_key",
            expected=f"date_key: DX_PAID_DATE (a listed item's key requiring the stamp's item), or "
            f"date_key: {NO_DATE} for a stamp with no date line.",
            recover=f"set {at}.date_key to the paid date's key or to {NO_DATE}.",
        )
    candidates = block["candidates"]
    fractions = (
        isinstance(candidates, list)
        and bool(candidates)
        and all(
            isinstance(c, dict)
            and set(c) == {"x", "y"}
            and all(isinstance(c[k], int | float) and not isinstance(c[k], bool) for k in ("x", "y"))
            for c in candidates
        )
    )
    outside = [c for c in candidates if not (0 <= c["x"] <= 1 and 0 <= c["y"] <= 1)] if fractions else []
    if not fractions or outside:
        raise _err(
            f"{at}.candidates is {candidates!r}"
            + (f"; {outside} lie outside [0, 1]" if outside else "")
            + "; each candidate is the stamp's centre as page fractions.",
            layout_path=where,
            key_path=f"{at}.candidates",
            expected="a non-empty list of {x, y} page fractions, each from 0 to 1, e.g. candidates: "
            "[{x: 0.72, y: 0.16}, {x: 0.45, y: 0.5}].",
            recover=f"give every entry of {at}.candidates an x and a y between 0 and 1.",
        )
    stamp = config.overlay.stamp
    words = _stamp_words(content, owner, config)
    dates = DATE_SAMPLES if date_key != NO_DATE else (None,)
    widest = tallest = 0
    for word in words:
        for date in dates:
            width, height = rotated_extent(tile_size(stamp, word, date), stamp.rotation_deg)
            widest, tallest = max(widest, width), max(tallest, height)
    if not (isinstance(page, dict) and all(isinstance(page.get(k), int) for k in ("width", "height"))):
        raise _err(
            f"{at} is a stamp on a layout with page_dimensions {page!r}, so its candidates have no page.",
            layout_path=where,
            key_path=at,
            expected="page_dimensions: {width: <px>, height: <px>} on a layout carrying a stamp.",
            recover="declare page_dimensions on the layout, or remove the stamp block.",
        )
    page = (int(page["width"]), int(page["height"]))
    inset = config.overlay.page_inset_px
    for index, candidate in enumerate(candidates):
        cx, cy = candidate["x"] * page[0], candidate["y"] * page[1]
        box = (cx - widest / 2, cy - tallest / 2, cx + widest / 2, cy + tallest / 2)
        if box[0] < inset or box[1] < inset or box[2] > page[0] - inset or box[3] > page[1] - inset:
            raise _err(
                f"{at}.candidates[{index}] {candidate} centres the stamp at ({cx:.0f}, {cy:.0f}), where its "
                f"largest footprint ({widest}x{tallest}px: the widest word and date at the largest rotation "
                f"in overlay.stamp) reaches {tuple(round(v) for v in box)}, past the page inset of "
                f"{inset}px on the {page[0]}x{page[1]} page.",
                layout_path=where,
                key_path=f"{at}.candidates[{index}]",
                expected=f"every candidate at least {widest / 2:.0f}px across and {tallest / 2:.0f}px down "
                f"from the inset edge, e.g. {{x: 0.5, y: 0.5}}.",
                recover=f"move {at}.candidates[{index}] inward, or make the stamp smaller in "
                f"overlay.stamp of {DISTRACTORS_PATH}.",
            )


def _stamp_words(content: str, owner: dict[str, str], config: DistractorConfig) -> list[str]:
    """Every string a stamp's content can print: each combination of its pick rules' values."""
    names = list(dict.fromkeys(referenced_fields(content)))
    choices = [config.items[owner[name]].rule["values"] for name in names]
    words = []
    for combination in itertools.product(*choices):
        text = content
        for name, value in zip(names, combination, strict=True):
            text = text.replace("{" + name + "}", str(value))
        words.append(text)
    return words


def _validate_distractor_items(layout: dict, *, layout_id: str, layout_path: str, doc_type: str) -> None:
    """Check `distractor_items:` lists exactly the catalogue items the body draws.

    Args:
        layout: The resolved layout dict.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        doc_type: The layout's document type, in any convention the extraction schema resolves.

    Raises:
        LayoutSchemaError: The list is malformed, names an unknown item or one for another document
            type, disagrees with the layout's distractor provider (see
            `_validate_distractor_provider`), the body binds a `DX_` key no listed item emits, a
            listed item is never drawn (by the body or the overlay), a block draws a `DX_` key its
            `when:` does not cover (`_check_dx_gating`), or the overlay is malformed
            (`_validate_overlay`).
    """
    here = f"{layout_id}.distractor_items"
    listed = layout["distractor_items"]
    if not isinstance(listed, list) or not all(isinstance(i, str) for i in listed):
        raise _err(
            f"distractor_items is {listed!r}, not a list of item ids.",
            layout_path=layout_path,
            key_path=here,
            expected="a list of ids from config/distractors.yml, or distractor_items: [].",
            recover=f"make {here} a list of catalogue item ids.",
        )
    canonical = load_extraction_schema().resolve_doc_type(doc_type)
    catalogue = load_distractor_config().items
    emitted: set[str] = set()
    for item_id in listed:
        item = catalogue.get(item_id)
        if item is None:
            raise _err(
                f"distractor_items names unknown item '{item_id}'.",
                layout_path=layout_path,
                key_path=here,
                expected=f"ids from config/distractors.yml for {canonical}: "
                f"{sorted(i for i, c in catalogue.items() if canonical in c.doc_types)}.",
                recover=f"correct '{item_id}', or add it to config/distractors.yml.",
            )
        if canonical not in item.doc_types:
            raise _err(
                f"distractor_items lists '{item_id}', which is for {sorted(item.doc_types)}, "
                f"not {canonical}.",
                layout_path=layout_path,
                key_path=here,
                expected=f"only items whose doc_types include {canonical}.",
                recover=f"remove '{item_id}' from {here}.",
            )
        emitted.update(item.emits)

    _validate_distractor_provider(
        layout, listed, canonical=canonical, layout_id=layout_id, layout_path=layout_path
    )
    _validate_state_groups(listed, canonical=canonical, layout_id=layout_id, layout_path=layout_path)

    found: set[str] = set()
    ungated: list[_Ungated] = []
    misplaced: list[tuple[str, str]] = []
    _collect_dx_references(layout["body"], found=found, ungated=ungated, misplaced=misplaced)
    found |= _validate_overlay(
        layout, layout_id=layout_id, layout_path=layout_path, canonical=canonical, listed=listed
    )

    if misplaced:
        key, name = misplaced[0]
        raise _err(
            f"{{{name}}} sits in a '{key}' string the renderer never interpolates, so it would print "
            "literally.",
            layout_path=layout_path,
            key_path=f"{layout_id}.body",
            expected=f"DX_ placeholders only in {list(_TEMPLATE_KEYS)} or in the entries of lines.",
            recover=f"move {name} to a block's content, or remove it from '{key}'.",
        )

    unlisted = sorted(found - emitted)
    if unlisted:
        raise _err(
            f"the body references {unlisted}, which no listed distractor item emits.",
            layout_path=layout_path,
            key_path=here,
            expected="every DX_ key the body binds is emitted by an item listed in distractor_items.",
            recover=f"list the item that emits {unlisted} in {here}, or remove the reference.",
        )
    for item_id in listed:
        if not set(catalogue[item_id].emits) & found:
            raise _err(
                f"distractor_items lists '{item_id}', but no block draws any of "
                f"{list(catalogue[item_id].emits)}.",
                layout_path=layout_path,
                key_path=here,
                expected="every listed item is drawn by at least one block.",
                recover=f"add a block drawing {list(catalogue[item_id].emits)}, or remove '{item_id}'.",
            )
    _check_dx_gating(ungated, listed, layout_id=layout_id, layout_path=layout_path)


# The document type whose layouts declare the content categories they may carry.
_CATEGORISED_DOC_TYPE = "invoice"


@functools.cache
def _service_categories() -> tuple[str, ...]:
    """The service categories of config/data_pools.yml, loaded (and validated) once per process."""
    return tuple(load_pools()["service_categories"])


@functools.cache
def _catalogued_categories() -> frozenset[str]:
    """The service categories with invoice lines in `category_catalog`, loaded once per process."""
    return frozenset(load_pools()["category_catalog"])


def _validate_service_categories(layout: dict, *, layout_id: str, layout_path: str, doc_type: str) -> None:
    """Check an invoice layout's `service_categories:`, and that no other layout carries one.

    The seed draws an invoice's supplier, and so its line items, from its layout's categories, so a
    layout designed for trades never bills legal advice; the list is required, with no default.

    Args:
        layout: The resolved layout dict.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        doc_type: The layout's document type, in any convention the extraction schema resolves.

    Raises:
        LayoutSchemaError: An invoice layout has no `service_categories`, or it is not a non-empty list
            of distinct names from `service_categories` in config/data_pools.yml, or one of them has no
            `category_catalog` lines; or a layout of another document type carries the key.
    """
    here = f"{layout_id}.service_categories"
    canonical = load_extraction_schema().resolve_doc_type(doc_type)
    if canonical != _CATEGORISED_DOC_TYPE:
        if "service_categories" in layout:
            raise _err(
                f"layout '{layout_id}' is a {canonical} layout but carries service_categories, which only "
                f"{_CATEGORISED_DOC_TYPE} layouts declare.",
                layout_path=layout_path,
                key_path=here,
                expected=f"no service_categories key on a {canonical} layout.",
                recover=f"remove {here}.",
            )
        return
    known = list(_service_categories())
    example = f"service_categories: [{', '.join(known[:2])}]"
    value = layout.get("service_categories")
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(c, str) for c in value)
        or len(set(value)) != len(value)
        or any(c not in known for c in value)
    ):
        unknown = [c for c in value if c not in known] if isinstance(value, list) else []
        what = (
            f"layout '{layout_id}' has no service_categories key"
            if "service_categories" not in layout
            else f"service_categories is {value!r}"
            + (f"; {unknown} are not service categories" if unknown else "")
        )
        raise _err(
            f"{what}; every invoice layout declares the categories its content may draw.",
            layout_path=layout_path,
            key_path=here,
            expected=f"a non-empty list of distinct names from service_categories in "
            f"{DATA_POOLS_PATH} ({known}), e.g. {example}.",
            recover=f"set {here} to the categories this layout's design fits, adding any new one to "
            f"service_categories (with its nouns, industry and line catalogue) in {DATA_POOLS_PATH}.",
        )
    # The seed bills a case only lines from its category's catalogue entry, so every category the layout
    # may draw needs one.
    uncovered = [c for c in value if c not in _catalogued_categories()]
    if uncovered:
        raise _err(
            f"the categories {uncovered} of layout '{layout_id}' have no lines in category_catalog, "
            "so a case "
            "seeded with one could not be billed.",
            layout_path=layout_path,
            key_path=f"{here} -> {DATA_POOLS_PATH} category_catalog.{uncovered[0]}",
            expected=f"a category_catalog entry for each of {value}, e.g. "
            f"category_catalog: {{{uncovered[0]}: "
            '[{description: "...", unit: hrs, price_low: 90, price_high: 150, quantity: [1, 4]}]}.',
            recover=f"add category_catalog.{uncovered[0]} (the lines that category's invoices print) to "
            f"{DATA_POOLS_PATH}, or remove {uncovered[0]!r} from {here}.",
        )


# The document type whose layouts declare the merchant categories they print.
_MERCHANT_DOC_TYPE = "receipt"


@functools.cache
def _receipt_categories() -> tuple[str, ...]:
    """The receipt categories of config/data_pools.yml, loaded (and validated) once per process."""
    return tuple(load_pools()["receipt_categories"])


def _validate_merchant_categories(layout: dict, *, layout_id: str, layout_path: str, doc_type: str) -> None:
    """Check a receipt layout's `merchant_categories:`, and that no other layout carries one.

    The seed draws a receipt's layout from those serving its merchant's category, so a fuel layout's
    "Drive safely" is printed only by merchants it suits; the list is required, with no default.

    Raises:
        LayoutSchemaError: A receipt layout has no `merchant_categories`, or it is not a non-empty list of
            distinct names from `receipt_categories` in config/data_pools.yml; or a layout of another
            document type carries the key.
    """
    here = f"{layout_id}.merchant_categories"
    canonical = load_extraction_schema().resolve_doc_type(doc_type)
    if canonical != _MERCHANT_DOC_TYPE:
        if "merchant_categories" in layout:
            raise _err(
                f"layout '{layout_id}' is a {canonical} layout but carries merchant_categories, which only "
                f"{_MERCHANT_DOC_TYPE} layouts declare.",
                layout_path=layout_path,
                key_path=here,
                expected=f"no merchant_categories key on a {canonical} layout.",
                recover=f"remove {here}.",
            )
        return
    known = list(_receipt_categories())
    value = layout.get("merchant_categories")
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(c, str) for c in value)
        or len(set(value)) != len(value)
        or any(c not in known for c in value)
    ):
        unknown = [c for c in value if c not in known] if isinstance(value, list) else []
        what = (
            f"layout '{layout_id}' has no merchant_categories key"
            if "merchant_categories" not in layout
            else f"merchant_categories is {value!r}"
            + (f"; {unknown} are not receipt categories" if unknown else "")
        )
        raise _err(
            f"{what}; every receipt layout declares the merchant categories whose receipts it prints.",
            layout_path=layout_path,
            key_path=here,
            expected=f"a non-empty list of distinct names from receipt_categories in {DATA_POOLS_PATH} "
            f"({known}), e.g. merchant_categories: [fuel, automotive].",
            recover=f"set {here} to the merchants this layout's chrome suits, adding any new category to "
            f"receipt_categories (with its nouns and product_catalog lines) in {DATA_POOLS_PATH}.",
        )


def _iter_blocks(blocks: object, path: str) -> Iterator[tuple[dict, str]]:
    """Yield every block in a body subtree with its dotted path, descending into panels and splits."""
    if not isinstance(blocks, list):
        return
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        here = f"{path}[{index}]"
        yield block, here
        children = block.get("children")
        if block.get("type") == "split" and isinstance(children, list):
            for column_index, column in enumerate(children):
                yield from _iter_blocks(column, f"{here}.children[{column_index}]")
        elif block.get("type") in _CONTAINERS:
            yield from _iter_blocks(children, f"{here}.children")


# The longest digit string a DX-bound barcode may carry, and the one symbology it may use: Code 128
# packs an even run of digits two to a symbol (set C), so the worst-case width is known from the length.
_DX_BARCODE_MAX_DIGITS = 16
_DX_BARCODE_SYMBOLOGY = "code128"


def _widest_digit_barcode(
    length: int,
    block: dict,
    layout: dict,
    draw: ImageDraw.ImageDraw,
    *,
    layout_id: str,
    layout_path: str,
) -> tuple[str, BarcodeGeometry]:
    """Return the widest-drawn digit string of `length` and its geometry.

    An even run of digits always encodes to the same Code 128 bars, so only the caption varies. A
    proportional face (Kalam) gives its digits different advances, and a bold face's ink can overhang
    its advance at the first or last glyph, so every repeated digit is measured, and every pair of first
    and last digits around a run of the widest-advance digit. Each candidate goes through
    `barcode_geometry`, the helper `draw_barcode` itself uses.

    Args:
        length: The digit count, even and positive.
        block: The `barcode` block.
        layout: The resolved layout dict.
        draw: Any drawing surface, for measuring.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        The widest candidate string and its geometry (the first found, on a tie).
    """
    digits = "0123456789"

    def geometry_of(text: str) -> BarcodeGeometry:
        return barcode_geometry(text, block, layout, draw, layout_id=layout_id, layout_path=layout_path)

    candidates = [d * length for d in digits]
    if block["human_readable"]:
        font = measure_caption("0", block, layout, draw, layout_id=layout_id, layout_path=layout_path).font
        widest = max(digits, key=lambda d: draw.textlength(d, font=font))
        middle = widest * (length - 2)
        candidates += [f"{first}{middle}{last}" for first in digits for last in digits]
    best = candidates[0]
    best_geometry = geometry_of(best)
    for text in candidates[1:]:
        geometry = geometry_of(text)
        if geometry.span > best_geometry.span:
            best, best_geometry = text, geometry
    return best, best_geometry


def _validate_dx_barcodes(layout: dict, *, layout_id: str, layout_path: str) -> None:
    """Check every barcode bound to a `DX_` key draws a value that is always encodable and always fits.

    A barcode's content is drawn per case, so validate checks the rule instead: the content is exactly
    one `{DX_...}` placeholder, its catalogue item's rule is `digits` with an empty prefix and an even
    length of at most 16, and the worst case (the widest digit string of that length, see
    `_widest_digit_barcode`, at the block's `module_px` and `quiet_zone`, with its caption at the
    block's role size and family when printed) fits the layout's `content_width`.

    Args:
        layout: The resolved layout dict, already structurally valid.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Raises:
        LayoutSchemaError: The content is not a single `DX_` placeholder, the symbology is not Code 128,
            the item's rule is not an unprefixed even-length digit string of at most 16 digits, or the
            worst-case mark is wider than the layout's content width.
    """
    config = load_distractor_config()
    owner = {key: item_id for item_id in layout["distractor_items"] for key in config.items[item_id].emits}
    content_width = int(layout["content_width"])
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for block, path in _iter_blocks(layout["body"], "body"):
        if block.get("type") != "barcode":
            continue
        content = str(block["content"])
        names = referenced_fields(content)
        dx_names = [n for n in names if n.startswith(DX_PREFIX)]
        if not dx_names:
            continue
        here = f"{layout_id}.{path}"
        if len(names) != 1 or content.strip() != f"{{{dx_names[0]}}}":
            raise _err(
                f"barcode content {content!r} binds {sorted(set(names))}, not a single DX_ key alone.",
                layout_path=layout_path,
                key_path=f"{here}.content",
                expected=f'content: "{{{dx_names[0]}}}", the one placeholder and nothing else, so the bars '
                "encode exactly the drawn digit string.",
                recover=f'set content to "{{{dx_names[0]}}}", and draw any other text in its own '
                "text block.",
            )
        key = dx_names[0]
        item_id = owner[key]
        rule = config.items[item_id].rule
        symbology = block["symbology"]
        if symbology != _DX_BARCODE_SYMBOLOGY:
            raise _err(
                f"barcode bound to {key} (item '{item_id}') uses symbology {symbology!r}.",
                layout_path=layout_path,
                key_path=f"{here}.symbology",
                expected=f"symbology: {_DX_BARCODE_SYMBOLOGY} for a barcode bound to a DX_ key (an ean13 "
                "takes exactly 12 or 13 digits, which no distractor rule promises).",
                recover=f"set {here}.symbology to {_DX_BARCODE_SYMBOLOGY}.",
            )
        length = rule.get("length")
        problem = None
        if rule.get("type") != "digits":
            problem = f"its rule type is {rule.get('type')!r}, not digits"
        elif rule.get("prefix") != "":
            problem = f"its rule has prefix {rule.get('prefix')!r}, not an empty prefix"
        elif not isinstance(length, int) or isinstance(length, bool) or length <= 0:
            problem = f"its rule length is {length!r}, not a positive even integer"
        elif length % 2:
            problem = f"its rule length {length} is odd, not even"
        elif length > _DX_BARCODE_MAX_DIGITS:
            problem = f"its rule length {length} is not at most {_DX_BARCODE_MAX_DIGITS}"
        if problem is not None:
            raise _err(
                f"barcode bound to {key} draws item '{item_id}', but {problem}.",
                layout_path=layout_path,
                key_path=f"{here}.content; {DISTRACTORS_PATH} -> items.{item_id}.rule",
                expected="an item whose rule is {type: digits, length: <even, at most "
                f'{_DX_BARCODE_MAX_DIGITS}>, prefix: ""}}, e.g. rc_transaction_id.',
                recover=f"bind the barcode to a qualifying item, draw {key} in a text block instead, or "
                f"change items.{item_id}.rule in config/distractors.yml.",
            )
        worst, geometry = _widest_digit_barcode(
            rule["length"], block, layout, measure, layout_id=layout_id, layout_path=layout_path
        )
        if geometry.span > content_width:
            caption = f", caption {geometry.caption.width}px" if geometry.caption is not None else ""
            raise _err(
                f"layout '{layout_id}': the barcode bound to {key} (item '{item_id}', {length} digits) is "
                f"{geometry.span}px wide at worst (for {worst!r}: bars with quiet zones "
                f"{geometry.bars_span}px{caption}), wider than the layout's content width of "
                f"{content_width}px.",
                layout_path=layout_path,
                key_path=f"{here}.module_px",
                expected=f"a worst-case width of at most {content_width}px; renderers never clip.",
                recover=f"lower {here}.module_px or quiet_zone, or bind an item with fewer digits.",
            )


_CODE_MARKS = ("barcode", "qrcode", "logo")


def _dx_gate(block: dict) -> bool:
    when = block.get("when")
    return isinstance(when, str) and when.startswith(DX_PREFIX)


def _validate_code_mark_gating(
    blocks: object, *, layout_id: str, layout_path: str, path: str, gated: bool
) -> None:
    """Refuse a code mark drawn outside a `DX_` gate, its own `when:` or an enclosing container's.

    A barcode, QR code or generated logo is never a scored field: every one serves as a distractor, so
    it must draw only in a distractor render, where its item was selected. A mark gated on an ordinary
    field, or not at all, would draw on every plain render too.

    Args:
        blocks: A body subtree (a list of blocks).
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        path: The dotted path of `blocks` below the layout id.
        gated: Whether an enclosing container is gated on a `DX_` key.

    Raises:
        LayoutSchemaError: A code mark is drawn with no `DX_` gate on it or on any enclosing container.
    """
    if not isinstance(blocks, list):
        return
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        here = f"{path}[{index}]"
        inside = gated or _dx_gate(block)
        kind = block.get("type")
        if kind in _CODE_MARKS and not inside:
            when = block.get("when")
            gate = f"gated on {when!r}, not a DX_ key" if when is not None else "not gated with when:"
            raise _err(
                f"a '{kind}' block is {gate}, so it would draw on every plain render; a code mark is "
                "never scored and always serves as a distractor.",
                layout_path=layout_path,
                key_path=f"{layout_id}.{here}.when",
                expected="when: naming a DX_ key, on the mark or on a container enclosing it: the key the "
                "mark draws, or the distractor item it stands beside, e.g.\n"
                f"              {{type: {kind}, ..., when: DX_TRANSACTION_ID}}",
                recover=f"add when: <DX_ key> to {layout_id}.{here} and list that key's item in "
                f"{layout_id}.distractor_items, or remove the mark.",
            )
        children = block.get("children")
        if kind == "split" and isinstance(children, list):
            for column_index, column in enumerate(children):
                _validate_code_mark_gating(
                    column,
                    layout_id=layout_id,
                    layout_path=layout_path,
                    path=f"{here}.children[{column_index}]",
                    gated=inside,
                )
        elif kind in _CONTAINERS:
            _validate_code_mark_gating(
                children,
                layout_id=layout_id,
                layout_path=layout_path,
                path=f"{here}.children",
                gated=inside,
            )


# A 14-character ABN-shaped value: a text site's wording must fit its budget at the nominal size with it.
_ABN_SHAPED = "00 000 000 000"
_LABEL_SITE_EXAMPLE = (
    "field_providers:\n"
    "                - name: label_styles\n"
    "                  params:\n"
    "                    sites:\n"
    '                      BUSINESS_ABN: {labelled: "ABN: ", pool: abn_receipt}\n'
    '                      GST_AMOUNT: {labelled: "GST", pool: gst_receipt}\n'
    '                      TOTAL_AMOUNT: {labelled: "TOTAL", pool: total_receipt}'
)


def _site_err(
    what: str, *, layout_path: str, key_path: str, expected: str, recover: str
) -> LayoutSchemaError:
    """A label-site or region diagnostic, its Where naming the layout file by its absolute path."""
    return _err(
        what,
        layout_path=str(_absolute_layout_path(layout_path)),
        key_path=key_path,
        expected=expected,
        recover=recover,
    )


def _validate_regions(body: list, *, layout_id: str, layout_path: str) -> dict[str, str]:
    """Check every `region:` sits on a stack, names a known region, and is declared at most once.

    Returns:
        Region name -> the dotted path of the stack declaring it.

    Raises:
        LayoutSchemaError: A region on anything but a stack, an unknown name, or a name declared twice.
    """
    found: dict[str, str] = {}
    for block, here in _iter_blocks(body, f"{layout_id}.body"):
        if "region" not in block:
            continue
        name = block["region"]
        if block.get("type") != "stack":
            raise _site_err(
                f"region: {name!r} sits on a '{block.get('type')}' block; only a stack declares a region.",
                layout_path=layout_path,
                key_path=f"{here}.region",
                expected="region: on a stack wrapping the blocks it names, e.g.\n"
                "              - {type: stack, region: totals, children: [<the totals lines>]}",
                recover="wrap the blocks in a stack carrying the region:, and remove it from this block.",
            )
        if name not in REGION_NAMES:
            raise _site_err(
                f"unknown region {name!r}.",
                layout_path=layout_path,
                key_path=f"{here}.region",
                expected=f"one of {list(REGION_NAMES)}, e.g. {{type: stack, region: {REGION_NAMES[0]}, "
                "children: [...]}.",
                recover=f"set region: to one of {list(REGION_NAMES)}, or remove it.",
            )
        if name in found:
            raise _site_err(
                f"region {name!r} is declared twice, at {found[name]} and {here}; a page has one.",
                layout_path=layout_path,
                key_path=f"{here}.region",
                expected=f"one stack per layout carrying region: {name}.",
                recover=f"merge the two stacks, or remove region: from {here}.",
            )
        found[str(name)] = here
    return found


def _site_spec(spec: object, field: str, *, here: str, layout_path: str, pools: dict) -> tuple[str, str]:
    """Check one site of a `label_styles` entry; return its (labelled wording, pool name)."""
    if not isinstance(spec, dict) or sorted(spec) != sorted(SITE_KEYS):
        raise _site_err(
            f"label site {field} is {spec!r}, not a mapping of exactly {list(SITE_KEYS)}.",
            layout_path=layout_path,
            key_path=here,
            expected=f"each site carries {list(SITE_KEYS)}, e.g.\n              {_LABEL_SITE_EXAMPLE}",
            recover=f"give {here} a labelled: wording and a pool: name, and nothing else.",
        )
    labelled, pool = spec["labelled"], spec["pool"]
    if not isinstance(labelled, str) or not normalise_wording(labelled):
        raise _site_err(
            f"label site {field} has labelled wording {labelled!r}, not the label the site prints today.",
            layout_path=layout_path,
            key_path=f"{here}.labelled",
            expected="the exact label the site prints, separator folded in, e.g.\n              "
            f"{_LABEL_SITE_EXAMPLE}",
            recover=f"set {here}.labelled to the site's printed label.",
        )
    if pool not in pools:
        raise _site_err(
            f"label site {field} names pool {pool!r}, which config/distractors.yml does not declare.",
            layout_path=layout_path,
            key_path=f"{here}.pool",
            expected=f"one of {sorted(pools)} (config/distractors.yml -> label_variation.pools), e.g.\n"
            f"              {_LABEL_SITE_EXAMPLE}",
            recover=f"name a declared pool in {here}.pool, or add the pool under label_variation.pools.",
        )
    own = normalise_wording(labelled)
    echoes = [wording for wording in pools[pool] if normalise_wording(wording) == own]
    if echoes:
        raise _site_err(
            f"pool '{pool}' of label site {field} holds {echoes}, which read as the site's own wording "
            f"{labelled!r}: a variant drawn from it would print today's label under another style.",
            layout_path=layout_path,
            key_path=f"{here}.pool",
            expected="a pool none of whose wordings normalises (case-folded, punctuation stripped, "
            f"whitespace collapsed) to '{own}'.",
            recover=f"remove {echoes} from {DISTRACTORS_PATH} -> label_variation.pools.{pool}, or name "
            "another pool.",
        )
    return labelled, str(pool)


def _site_block_error(
    block: dict, field: str, problem: str, *, here: str, layout_path: str, expected: str
) -> LayoutSchemaError:
    return _site_err(
        f"the label site of {field} at {here} {problem}.",
        layout_path=layout_path,
        key_path=here,
        expected=expected,
        recover=f"rewrite {here} as the expected label site, or remove its {label_placeholder(field)}.",
    )


_PAIR_SITE_EXAMPLE = (
    '{type: pair, label: "{LABEL_TOTAL_AMOUNT}", value: "{TOTAL_AMOUNT}", value_align: right, '
    'separator: "", field: TOTAL_AMOUNT}'
)
_TEXT_SITE_EXAMPLE = (
    '{type: text, content: "{LABEL_BUSINESS_ABN}{BUSINESS_ABN}", budget: ABN_LINE, field: BUSINESS_ABN}'
)


def _site_blocks(
    layout: dict, sites: dict[str, tuple[str, str]], *, layout_id: str, layout_path: str
) -> dict[str, tuple[dict, str]]:
    """Find each site's one block, checking every `{LABEL_X}` placeholder sits where a site may draw it.

    Returns:
        Field -> (its site block, the block's dotted path).
    """
    found: dict[str, tuple[dict, str]] = {}
    for block, here in _iter_blocks(layout["body"], f"{layout_id}.body"):
        for key in (*_TEMPLATE_KEYS, "lines"):
            raw = block.get(key)
            templates = raw if isinstance(raw, list) else [raw]
            for template in templates:
                if not isinstance(template, str):
                    continue
                for name in referenced_fields(template):
                    if not name.startswith(LABEL_PREFIX):
                        continue
                    field = name[len(LABEL_PREFIX) :]
                    if field not in sites:
                        raise _site_err(
                            f"{{{name}}} has no label site: {layout_id}'s label_styles declares sites "
                            f"for {sorted(sites)} only.",
                            layout_path=layout_path,
                            key_path=f"{here}.{key}",
                            expected=f"a site for {field} under label_styles.params.sites, e.g.\n"
                            f"              {_LABEL_SITE_EXAMPLE}",
                            recover=f"add a {field} site to {layout_id}'s label_styles, or remove "
                            f"{{{name}}}.",
                        )
                    kind = block.get("type")
                    bound = block.get("field") == field
                    placeholder = label_placeholder(field)
                    if kind == "pair" and key == "label" and bound and template == placeholder:
                        pass
                    elif (
                        kind == "text"
                        and key == "content"
                        and bound
                        and template == placeholder + "{" + field + "}"
                    ):
                        pass
                    else:
                        raise _site_block_error(
                            block,
                            field,
                            f"draws {{{name}}} in '{key}' of a '{kind}' block "
                            + ("bound to it" if bound else f"not bound field: {field}"),
                            here=here,
                            layout_path=layout_path,
                            expected="the placeholder alone as a bound pair's label, e.g. "
                            f"{_PAIR_SITE_EXAMPLE}, "
                            "or before the value as a bound text line's content, e.g. "
                            f"{_TEXT_SITE_EXAMPLE}.",
                        )
                    if field in found:
                        raise _site_block_error(
                            block,
                            field,
                            f"is the second block drawing {{{name}}} (the first is "
                            f"{found[field][1]}); a page "
                            "has one label per field",
                            here=here,
                            layout_path=layout_path,
                            expected=f"exactly one block drawing {{{name}}}.",
                        )
                    found[field] = (block, here)
    return found


def _validate_label_sites(layout: dict, *, layout_id: str, layout_path: str, doc_type: str) -> None:
    """Check a layout's label sites against `label_variation:` (Decisions 1, 3, 9, 16 and 17).

    A receipt or invoice layout carries exactly one `label_styles` provider whose sites are the fields
    its document type varies; a bank layout carries none. Each site names its labelled wording and a
    declared pool none of whose wordings reads as that wording; each site's placeholder sits in exactly
    one block bound to its field: a right-aligned pair with the empty separator, or a budgeted text line
    whose every wording ends in whitespace and fits its budget at the nominal size beside an ABN-shaped
    value. A layout with a GST or total site declares one totals region holding both.

    Raises:
        LayoutSchemaError: On any of the above.
    """
    config = load_label_config()
    canonical = load_extraction_schema().resolve_doc_type(doc_type)
    varied = config.doc_types[canonical]
    here = f"{layout_id}.field_providers"
    providers = layout.get("field_providers") or []
    entries = [
        (index, spec)
        for index, spec in enumerate(providers)
        if isinstance(spec, dict) and spec.get("name") == LABEL_PROVIDER
    ]
    regions = _validate_regions(layout.get("body") or [], layout_id=layout_id, layout_path=layout_path)
    if not varied:
        if entries:
            raise _site_err(
                f"layout '{layout_id}' declares {LABEL_PROVIDER}, but a {canonical} varies no label "
                f"({config.path} -> label_variation.doc_types.{canonical} is []).",
                layout_path=layout_path,
                key_path=here,
                expected=f"no {LABEL_PROVIDER} entry on a {canonical} layout.",
                recover=f"remove the {LABEL_PROVIDER} entry from {here}.",
            )
        return
    if len(entries) != 1:
        raise _site_err(
            f"layout '{layout_id}' declares {len(entries)} {LABEL_PROVIDER} entries; a {canonical} layout "
            f"declares exactly one, with a site for each of {list(varied)}.",
            layout_path=layout_path,
            key_path=here,
            expected=f"one entry, e.g.\n              {_LABEL_SITE_EXAMPLE}",
            recover=f"add (or keep exactly one) {LABEL_PROVIDER} entry in {here}.",
        )
    index, spec = entries[0]
    params = spec.get("params")
    raw_sites = params.get(SITES_PARAM) if isinstance(params, dict) else None
    where = f"{here}[{index}].params.{SITES_PARAM}"
    if not isinstance(raw_sites, dict) or sorted(raw_sites) != sorted(varied):
        raise _site_err(
            f"{LABEL_PROVIDER} sites are {raw_sites!r}; a {canonical} varies {list(varied)} "
            f"({config.path} -> label_variation.doc_types.{canonical}), each with one site.",
            layout_path=layout_path,
            key_path=where,
            expected=f"a site for exactly {list(varied)}, e.g.\n              {_LABEL_SITE_EXAMPLE}",
            recover=f"declare one site per field of label_variation.doc_types.{canonical} under {where}.",
        )
    sites = {
        str(field): _site_spec(
            raw_sites[field], field, here=f"{where}.{field}", layout_path=layout_path, pools=config.pools
        )
        for field in varied
    }
    blocks = _site_blocks(layout, sites, layout_id=layout_id, layout_path=layout_path)
    for field, (labelled, pool) in sites.items():
        if field not in blocks:
            raise _site_err(
                f"label site {field} has no block drawing {label_placeholder(field)} bound field: {field}, "
                "so its label would never be drawn.",
                layout_path=layout_path,
                key_path=f"{layout_id}.body",
                expected=f"one block bound to {field} drawing the placeholder, e.g. "
                f"{_PAIR_SITE_EXAMPLE} or "
                f"{_TEXT_SITE_EXAMPLE}.",
                recover=f"bind the {field} block's label through {label_placeholder(field)}, or remove the "
                f"site from {where}.",
            )
        block, block_path = blocks[field]
        if block["type"] == "pair":
            _check_pair_site(
                block, field, layout, here=block_path, layout_id=layout_id, layout_path=layout_path
            )
        else:
            _check_text_site(
                block,
                field,
                [labelled, *config.pools[pool]],
                layout,
                here=block_path,
                layout_id=layout_id,
                layout_path=layout_path,
            )
    # Which sites must lie in the totals region is the label config's to say, never this module's.
    region_fields = config.totals_region_fields
    in_region = [field for field in region_fields if field in blocks]
    if not in_region:
        return
    region_path = regions.get(TOTALS_REGION)
    if region_path is None:
        raise _site_err(
            f"layout '{layout_id}' has label sites for {in_region} but declares no {TOTALS_REGION} region.",
            layout_path=layout_path,
            key_path=f"{layout_id}.body",
            expected=f"one stack with region: {TOTALS_REGION} wrapping the totals lines, e.g.\n"
            f"              - {{type: stack, region: {TOTALS_REGION}, "
            "children: [<subtotal, GST, total, ...>]}}",
            recover=f"wrap the totals lines of {layout_id} in a stack carrying region: {TOTALS_REGION}.",
        )
    outside = [field for field in in_region if not blocks[field][1].startswith(f"{region_path}.")]
    if outside:
        raise _site_err(
            f"label site(s) {outside} lie outside the {TOTALS_REGION} region at {region_path}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.body",
            expected=f"the {' and '.join(region_fields)} sites inside the stack carrying region: "
            f"{TOTALS_REGION} ({config.path} -> label_variation.totals_region_fields).",
            recover=f"move {outside} into the {TOTALS_REGION} stack at {region_path}.",
        )


def _check_pair_site(
    block: dict, field: str, layout: dict, *, here: str, layout_id: str, layout_path: str
) -> None:
    """A pair site right-aligns its value and keeps an empty separator (Decision 3)."""
    defaults = layout.get("defaults", {})
    align = block.get("value_align", defaults.get("pair_value_align"))
    separator = block.get("separator", defaults.get("pair_separator"))
    if align != "right" or separator != "":
        raise _site_block_error(
            block,
            field,
            f"resolves value_align {align!r} and separator {separator!r}",
            here=here,
            layout_path=layout_path,
            expected=f'value_align: right and separator: "", the wording carrying its own punctuation, '
            f"e.g. {_PAIR_SITE_EXAMPLE}.",
        )


def _check_text_site(
    block: dict,
    field: str,
    wordings: list[str],
    layout: dict,
    *,
    here: str,
    layout_id: str,
    layout_path: str,
) -> None:
    """A text site's every wording ends in whitespace and fits its budget beside an ABN.

    Decisions 3 and 16 of the label-variation plan.
    """
    budget_name = block.get("budget")
    budgets = layout.get("field_budgets", {})
    if budget_name not in budgets:
        raise _site_block_error(
            block,
            field,
            f"has budget {budget_name!r}, not one of this layout's field_budgets",
            here=here,
            layout_path=layout_path,
            expected=f"a budgeted text line, e.g. {_TEXT_SITE_EXAMPLE}.",
        )
    unended = [wording for wording in wordings if wording and not wording[-1].isspace()]
    if unended:
        raise _site_block_error(
            block,
            field,
            f"would print {unended} hard against its value: a text site's wording ends in whitespace",
            here=here,
            layout_path=layout_path,
            expected="every wording of the site (labelled and pool) ending in a space, "
            'e.g. "ABN: ", "A.B.N. ".',
        )
    defaults = layout.get("defaults", {})
    role = str(block.get("role", defaults.get("role")))
    size = int(layout.get("font_sizes", {}).get(role, 0))
    family = str(block.get("family", defaults.get("family")))
    bold = bool(block.get("bold", defaults.get("bold")))
    width = int(budgets[budget_name]["width"])
    font = load_font(size, family=family, bold=bold)
    for wording in wordings:
        bbox = font.getbbox(wording + _ABN_SHAPED)
        drawn = int(bbox[2] - bbox[0])
        if drawn > width:
            raise _site_err(
                f"wording {wording!r} with an ABN-shaped value ({_ABN_SHAPED!r}) is {drawn}px wide at "
                f"{family}{' bold' if bold else ''} {size}, but the label site of {field} at {here} fits "
                f"budget {budget_name} of {width}px, so the value would shrink to fit its label.",
                layout_path=layout_path,
                key_path=f"{here}.budget",
                expected="every wording of the site, followed by a 14-character ABN, at most "
                f"{width}px wide "
                "at the nominal size.",
                recover=f"remove {wording!r} from its pool in {DISTRACTORS_PATH} -> "
                "label_variation.pools, or "
                f"widen field_budgets.{budget_name}.",
            )


def validate_layout(
    layout: dict, *, layout_id: str, layout_path: str, known_fields: set[str], doc_type: str
) -> None:
    """Validate a whole layout: its body tree plus geometry-dependent checks.

    Adds the checks that need the surrounding layout and cannot be made from
    the body alone — column budgets against column geometry, nested container
    widths against their parent, and each `field_providers:` entry against the
    field-provider registry. `known_fields` is widened by the emits of every
    provider this layout actually references before `validate_body` runs, so
    a `{FIELD}` naming a derived value resolves while a typo still fails.

    Args:
        layout: The resolved layout dict, carrying `body`, `content_width`,
            `field_budgets`, and `field_providers` (required — a layout that
            derives no fields must set `field_providers: []` explicitly).
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        known_fields: Field names the document type may reference.
        doc_type: The layout's document type (the pipeline's `document_types` key, or the
            extraction schema's), for the `distractor_items:` cross-check.

    Raises:
        LayoutSchemaError: On any structural, reference, or geometry problem.
    """
    missing = sorted(PARAMETER_DEFAULTS - set(layout.get("defaults", {})))
    if missing:
        raise _err(
            f"layout '{layout_id}' declares no default for: {', '.join(missing)}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults",
            expected="a defaults: mapping covering every parameter a primitive can read: "
            f"{sorted(PARAMETER_DEFAULTS)}.",
            recover=f"add the missing keys under {layout_id}.defaults, sharing a common "
            "block through a YAML anchor as field_budgets already does.",
        )

    # `defaults:` only has to *carry* every key; nothing above checks the
    # values are meaningful. A bad family is worth catching here for the same
    # reason line_advance's shape is: load_font would otherwise raise on the
    # first block that draws, long after startup.
    default_family = layout["defaults"]["family"]
    if default_family not in FONT_FAMILIES:
        raise _err(
            f"layout '{layout_id}' declares an unknown defaults.family ({default_family!r}).",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.family",
            expected=f"one of {sorted(FONT_FAMILIES)}, e.g.\n"
            "              defaults:\n"
            "                family: carlito",
            recover=f"set {layout_id}.defaults.family to one of {sorted(FONT_FAMILIES)}, "
            "or vendor the new face in fonts/ and register it in FONT_FAMILIES in "
            "generators/common.py.",
        )

    # line_advance replaces the old int(size * 1.4) ratio, which varied by
    # the drawing block's own role -- a single flat number cannot express
    # that a header-role line and a footer-role line need different
    # advances, so `resolve_param` alone (which only checks the key is
    # present, not its shape) cannot catch a layout that mistypes this as
    # one number. Checked here, at validate time, rather than left for
    # line_advance() to discover per-block at render time.
    line_advance_defaults = layout["defaults"]["line_advance"]
    if not isinstance(line_advance_defaults, dict):
        raise _err(
            f"layout '{layout_id}' declares defaults.line_advance as a single number "
            f"({line_advance_defaults!r}), not a per-role mapping.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.line_advance",
            expected="a mapping of role -> pixels, one entry per font_sizes role this "
            "layout's body draws (each int(font_sizes.<role> * 1.4)), e.g.\n"
            "              defaults:\n"
            "                line_advance:\n"
            "                  header: 61\n"
            "                  body: 44\n"
            "                  footer: 25\n"
            "            A single number cannot express that a header-role line and a "
            "footer-role line need different advances.",
            recover=f"replace {layout_id}.defaults.line_advance with a role: pixels "
            "mapping covering every role this layout's body uses.",
        )

    for key, example in (
        ("body", "a list of block mappings"),
        ("content_width", "1600"),
        (
            "field_providers",
            "a list of {name, params} mappings, or field_providers: [] if this layout derives no fields",
        ),
        (
            "distractor_items",
            "a list of config/distractors.yml item ids, or distractor_items: [] if it draws none",
        ),
    ):
        if key not in layout:
            raise _err(
                f"layout '{layout_id}' has no '{key}' key.",
                layout_path=layout_path,
                key_path=f"{layout_id}.{key}",
                expected=f"{key}: {example}.",
                recover=f"add a '{key}:' key to {layout_id}, or do not pass this layout "
                f"to validate_layout.",
            )

    # Required on every layout, with its own diagnostic: a layout drawing no overlay says so with [].
    if "overlay" not in layout:
        raise _err(
            f"layout '{layout_id}' has no 'overlay' key.",
            layout_path=str(_absolute_layout_path(layout_path)),
            key_path=f"{layout_id}.overlay",
            expected="overlay: [] or a list of blocks, each with type and when, e.g.\n"
            "              overlay: []",
            recover="add overlay: [] to the layout.",
        )

    validate_page(layout, layout_path=str(_absolute_layout_path(layout_path)), layout_id=layout_id)

    # A layout key nothing reads is worse than no key at all: it tells an
    # operator the document is configured a way it is not. The invoice YAML
    # carried `minimum_amount: 10000` on tax_invoice_high_value long after
    # layout assignment stopped consulting it, so the file read as though the
    # corpus routed large invoices there when it round-robins them.
    permitted = known_layout_keys(layout)
    unread = sorted(set(layout) - permitted)
    if unread:
        raise _err(
            f"layout '{layout_id}' carries key(s) {unread} that no code path reads.",
            layout_path=layout_path,
            key_path=layout_id,
            expected=f"only {sorted(permitted)} — the engine's and the page adapters' own "
            "keys, plus every layout key this body names through a 'from_layout:' or "
            "'suppress_if_equals:'.",
            recover=f"remove {unread} from {layout_id}; or, if something genuinely reads "
            "one now, add it to _ENGINE_LAYOUT_KEYS / _PAGE_LAYOUT_KEYS / "
            "_CORPUS_LAYOUT_KEYS in generators/layout_dsl/schema.py, naming the reader.",
        )

    provider_emits = _validate_field_providers(layout, layout_id=layout_id, layout_path=layout_path)
    # Before the body: a label placeholder with no label_styles provider is a missing site, not an
    # unknown field.
    _validate_label_sites(layout, layout_id=layout_id, layout_path=layout_path, doc_type=doc_type)
    validate_body(
        layout["body"],
        layout_id=layout_id,
        layout_path=layout_path,
        known_fields=known_fields | set(provider_emits),
    )
    _validate_distractor_items(layout, layout_id=layout_id, layout_path=layout_path, doc_type=doc_type)
    _validate_service_categories(layout, layout_id=layout_id, layout_path=layout_path, doc_type=doc_type)
    _validate_merchant_categories(layout, layout_id=layout_id, layout_path=layout_path, doc_type=doc_type)

    # Converts a render-time DefaultsError on one unlucky document (the
    # first entry whose body happens to reach an uncovered role) into a
    # startup failure naming the layout and the role, for every layout, not
    # just the ones whose sampled ground truth exercises it.
    default_role = str(layout["defaults"]["role"])
    used_roles = _line_advance_roles(layout["body"], default_role=default_role)
    missing_roles = sorted(used_roles - set(line_advance_defaults))
    if missing_roles:
        raise _err(
            f"layout '{layout_id}' body uses role(s) {missing_roles} that "
            "defaults.line_advance does not cover.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.line_advance",
            expected=f"a defaults.line_advance entry for every role in {sorted(used_roles)}, e.g.\n"
            "              defaults:\n"
            "                line_advance:\n"
            + "".join(
                f"                  {role}: <int(font_sizes.{role} * 1.4)>\n" for role in missing_roles
            ),
            recover=f"add {missing_roles} to {layout_id}.defaults.line_advance.",
        )

    content_width = int(layout["content_width"])
    _validate_geometry(
        layout["body"],
        layout=layout,
        layout_path=layout_path,
        width=content_width,
        key_path=f"{layout_id}.body",
    )
    _validate_dx_barcodes(layout, layout_id=layout_id, layout_path=layout_path)
    _validate_code_mark_gating(
        layout["body"], layout_id=layout_id, layout_path=layout_path, path="body", gated=False
    )


def _column_anchor(column: dict, width: int) -> int:
    """Resolve a column's anchor as an offset from the region's left edge."""
    return int(column["x"]) if "x" in column else width + int(column["x_right"])


def _validate_geometry(blocks: list, *, layout: dict, layout_path: str, width: int, key_path: str) -> None:
    """Recursively check budgets, container widths, and layout-key references."""
    for index, block in enumerate(blocks):
        here = f"{key_path}[{index}]"
        kind = block["type"]

        # `from_layout` and `suppress_if_equals` (text/banner) each hold a
        # layout key name directly (not a `{FIELD}` template) — check the
        # key actually exists here, where the layout dict is in scope, since
        # `_validate_references` only knows entry fields. Each is validated
        # against its own value at its own key_path — do not conflate the
        # two: `from_layout`'s value never lives in `content`.
        if "from_layout" in block:
            key = block["from_layout"]
            if key not in layout:
                raise _err(
                    f"'from_layout' names layout key '{key}', which this layout does not define.",
                    layout_path=layout_path,
                    key_path=f"{here}.from_layout",
                    expected=f"a key present in the layout, e.g. {key}: <value>.",
                    recover=f"add '{key}:' to the layout, or fix the key name.",
                )
        suppress_key = block.get("suppress_if_equals")
        if suppress_key is not None and suppress_key not in layout:
            raise _err(
                f"'suppress_if_equals' names layout key '{suppress_key}', which this layout "
                "does not define.",
                layout_path=layout_path,
                key_path=f"{here}.suppress_if_equals",
                expected=f"a key present in the layout, e.g. {suppress_key}: <value>.",
                recover=f"add '{suppress_key}:' to the layout, or fix the key name.",
            )

        if kind == "table":
            for param, layout_key in (block.get("params_from_layout") or {}).items():
                if layout_key not in layout:
                    raise _err(
                        f"params_from_layout.{param} names layout key '{layout_key}', which this "
                        "layout does not define.",
                        layout_path=layout_path,
                        key_path=f"{here}.params_from_layout.{param}",
                        expected=f"a key present in the layout, e.g. {layout_key}: <value>.",
                        recover=f"add '{layout_key}:' to the layout, or fix the key name.",
                    )
            _validate_column_budgets(
                block, layout=layout, layout_path=layout_path, width=width, key_path=here
            )
        elif kind in ("text", "pair"):
            _validate_text_budget(block, layout=layout, layout_path=layout_path, width=width, key_path=here)
        elif kind == "panel":
            # Resolved the way draw_panel resolves it (primitives_container.py):
            # the block's own `padding:` if it has one, else the layout's
            # `defaults.panel_padding`. Reading `block.get("padding", 0)`
            # instead would check a layout declaring `panel_padding: 40`
            # against a padding of 0 and pass a budget the renderer cannot
            # honour. Same reasoning as `_validate_text_budget` below, which
            # already reads `defaults.pair_value_align` this way.
            padding = int(block.get("padding", layout["defaults"]["panel_padding"]))
            inner = width - 2 * padding
            if inner < 1:
                raise _err(
                    f"panel padding {padding} leaves width {inner} inside a {width}px region.",
                    layout_path=layout_path,
                    key_path=f"{here}.padding",
                    expected=f"padding below {width // 2}, e.g. padding: 10.",
                    recover="reduce the panel's padding, or widen content_width.",
                )
            declared = block.get("height")
            if declared is not None and int(declared) < 2 * padding:
                raise _err(
                    f"panel declares height {int(declared)} but its padding alone needs {2 * padding}px.",
                    layout_path=layout_path,
                    key_path=f"{here}.height",
                    expected=f"height >= {2 * padding}, or padding <= {int(declared) // 2}.",
                    recover="raise the panel's height, or reduce its padding.",
                )
            _validate_geometry(
                block["children"],
                layout=layout,
                layout_path=layout_path,
                width=inner,
                key_path=f"{here}.children",
            )
        elif kind == "stack":
            _validate_geometry(
                block["children"],
                layout=layout,
                layout_path=layout_path,
                width=width,
                key_path=f"{here}.children",
            )
        elif kind == "split":
            columns = block["children"]
            # As with panel padding above: draw_split resolves `gap:` against
            # `defaults.split_gap`, so validation must too.
            gap = int(block.get("gap", layout["defaults"]["split_gap"]))
            widths = block.get("widths")
            if widths is not None:
                # Explicit per-column widths (invoice's fixed 400px totals
                # column) replace equal division entirely -- nested budget
                # checks below must see each column's real width, not a
                # fraction of the region. _validate_children (called earlier
                # by _validate_block, from the same top-down walk) has
                # already checked widths is a list of len(columns) positive
                # ints, so this only re-checks the geometry fit.
                column_widths = [int(w) for w in widths]
                total = sum(column_widths) + gap * (len(column_widths) - 1)
                if total > width:
                    raise _err(
                        f"split widths {column_widths} with gap {gap} need {total}px but "
                        f"this region is only {width}px.",
                        layout_path=layout_path,
                        key_path=f"{here}.widths",
                        expected=f"sum(widths) + gap * (n - 1) <= {width}.",
                        recover="reduce a width in split.widths, or reduce the split's gap.",
                    )
            else:
                inner = (width - gap * (len(columns) - 1)) // len(columns)
                if inner < 1:
                    raise _err(
                        f"split of {len(columns)} columns with gap {gap} leaves column "
                        f"width {inner} inside a {width}px region.",
                        layout_path=layout_path,
                        key_path=f"{here}.gap",
                        expected=f"gap below {width // max(len(columns) - 1, 1)}, e.g. gap: 30.",
                        recover="reduce the gap or the column count.",
                    )
                column_widths = [inner] * len(columns)
            for column_index, child_blocks in enumerate(columns):
                _validate_geometry(
                    child_blocks,
                    layout=layout,
                    layout_path=layout_path,
                    width=column_widths[column_index],
                    key_path=f"{here}.children[{column_index}]",
                )


def _validate_text_budget(
    block: dict, *, layout: dict, layout_path: str, width: int, key_path: str
) -> None:
    """Check a `text`/`pair` block's fit budget, if any, exists and fits its region.

    The simpler counterpart to `_validate_column_budgets`: a text/pair block
    has no column anchors to reason about (it is not one of several columns
    sharing a row), so the only geometry question is whether the declared
    budget width fits inside the block's own region -- `width`, the same
    value `_validate_geometry` already threads through panel/split narrowing
    for every other check at this nesting level.

    A right-aligned `pair` (`value_align: right`) additionally may not carry a
    non-zero `min_gap`, and that is rejected here rather than checked. The
    three keys have no combined meaning: `draw_pair`'s budgeted path draws the
    label first and fits the value into whatever region is left, so it never
    performs the render-time label repositioning `min_gap` exists to request
    (see `draw_pair`'s own docstring, which says so). A layout declaring all
    three would get a gap it asked for and did not receive, on a page where
    nothing shows that it was dropped -- the same class of defect as a layout
    key no code path reads. This was recorded as a constraint while the
    primitive was being built and honoured by every layout since; the check is
    that constraint made enforceable, so the next author to reach for the
    combination is told at validate time instead of discovering it in pixels.
    """
    name = block.get("budget")
    if name is None:
        return
    budgets = layout.get("field_budgets", {})
    if name not in budgets:
        raise _err(
            f"budget '{name}' is not defined by this layout.",
            layout_path=layout_path,
            key_path=f"{key_path}.budget",
            expected=f"a key present in field_budgets: {sorted(budgets)}.",
            recover=f"add '{name}: {{width, fit, min_font, max_lines}}' to field_budgets.",
        )
    declared = int(budgets[name]["width"])

    if block.get("type") == "pair":
        value_align = block.get("value_align", layout["defaults"]["pair_value_align"])
        if value_align == "right":
            min_gap = int(block.get("min_gap", layout["defaults"]["pair_min_gap"]))
            if min_gap > 0:
                raise _err(
                    f"pair combines budget '{name}' with value_align: right and min_gap "
                    f"{min_gap}; the budgeted path does not apply min_gap, so the gap "
                    "would be silently dropped.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.min_gap",
                    expected="a budgeted right-aligned pair to resolve min_gap to 0, e.g.\n"
                    "              {type: pair, label: 'Total', value: '{TOTAL_AMOUNT}',\n"
                    "               value_align: right, budget: TOTALS, min_gap: 0}",
                    recover="set min_gap: 0 on this block, or drop its budget: and let "
                    "min_gap reposition the label, or left-align the value.",
                )

    if declared > width:
        raise _err(
            f"budget '{name}' declares width {declared}px but this block's region is only {width}px.",
            layout_path=layout_path,
            key_path=f"{key_path}.budget",
            expected=f"field_budgets.{name}.width <= {width}.",
            recover=f"set field_budgets.{name}.width to {width} or less, or widen the region "
            "this block draws into.",
        )


def _validate_column_budgets(
    block: dict, *, layout: dict, layout_path: str, width: int, key_path: str
) -> None:
    """Check each budgeted column's declared width fits its column geometry.

    The budget is validated, never derived: a mismatch is an authoring error the
    operator must fix in YAML, so the intended width stays visible in the file.
    """
    budgets = layout.get("field_budgets", {})
    columns = block["columns"]
    anchors = sorted(_column_anchor(column, width) for column in columns)

    for index, column in enumerate(columns):
        sub_line = column.get("sub_line")
        if isinstance(sub_line, dict) and sub_line.get("budget") is not None:
            _validate_sub_line_budget(
                sub_line,
                column=column,
                budgets=budgets,
                available=_left_room(column, anchors, width),
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].sub_line.budget",
            )
        name = column.get("budget")
        if name is None:
            continue
        if name not in budgets:
            raise _err(
                f"column {index} names budget '{name}', which the layout does not define.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].budget",
                expected=f"a key present in field_budgets: {sorted(budgets)}.",
                recover=f"add '{name}: {{width, fit, min_font, max_lines}}' to field_budgets.",
            )

        anchor = _column_anchor(column, width)
        if column.get("align") == "right":
            # Right-aligned text extends LEFT from its anchor (the anchor is
            # its right edge), so the room available is measured back to the
            # previous anchor -- or the region's own left edge (0) if this is
            # the leftmost column -- the mirror image of the left-aligned
            # case below, where text grows right from the anchor instead.
            preceding = [value for value in anchors if value < anchor]
            available = anchor - (max(preceding) if preceding else 0)
            direction = "before the previous column"
        else:
            following = [value for value in anchors if value > anchor]
            available = (min(following) if following else width) - anchor
            direction = "before the next column"
        declared = int(budgets[name]["width"])
        if declared > available:
            raise _err(
                f"column {index} budget '{name}' declares width {declared}px but only "
                f"{available}px is available {direction}.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected=f"field_budgets.{name}.width <= {available}.",
                recover=f"set field_budgets.{name}.width to {available} or less, or move "
                f"the {'previous' if column.get('align') == 'right' else 'following'} column.",
            )


def _left_room(column: dict, anchors: list[int], width: int) -> int:
    """Return the room a left-drawn line has from a column's anchor to the next anchor, or the region edge."""
    anchor = _column_anchor(column, width)
    following = [value for value in anchors if value > anchor]
    return (min(following) if following else width) - anchor


def _validate_sub_line_budget(
    sub_line: dict, *, column: dict, budgets: dict, available: int, layout_path: str, key_path: str
) -> None:
    """Check a budgeted sub-line: a left-aligned column, a defined single-line budget, and room for it.

    A sub-line is drawn left-aligned at its column's anchor, and its values' boxes are measured on one
    line, so its budget must fit with `max_lines: 1` inside the room the column leaves.

    Raises:
        LayoutSchemaError: The column is right-aligned, the budget is undefined, allows more than one
            line, or is wider than the room available.
    """
    name = sub_line["budget"]
    example = "{width: 318, fit: shrink, min_font: 10, max_lines: 1}"
    if column.get("align") == "right":
        raise _err(
            f"budgeted sub_line '{name}' sits under a right-aligned column.",
            layout_path=layout_path,
            key_path=key_path,
            expected="a budgeted sub_line on a left-aligned column; a sub-line is drawn left-aligned "
            "at its column's anchor.",
            recover="move the sub_line to a left-aligned column.",
        )
    if name not in budgets:
        raise _err(
            f"sub_line names budget '{name}', which the layout does not define.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"a key present in field_budgets: {sorted(budgets)}, e.g. {name}: {example}.",
            recover=f"add '{name}: {example}' to field_budgets.",
        )
    spec = budgets[name]
    if int(spec["max_lines"]) != 1 or spec["fit"] != "shrink":
        raise _err(
            f"sub_line budget '{name}' is fit {spec['fit']!r} with max_lines {spec['max_lines']}.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"a single-line shrink fit, e.g. {name}: {example}.",
            recover=f"set field_budgets.{name} to fit: shrink and max_lines: 1.",
        )
    if int(spec["width"]) > available:
        raise _err(
            f"sub_line budget '{name}' declares width {spec['width']}px but only {available}px is "
            "available before the next column.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"field_budgets.{name}.width <= {available}.",
            recover=f"set field_budgets.{name}.width to {available} or less.",
        )
