"""Parameter resolution for layout primitives.

Resolution order is block key -> the layout's `defaults:` -> fail fast. There is
deliberately no fourth step: a Python literal supplying a value YAML omitted is
exactly what the project's "every config key is required" rule forbids, and it is
how `role`, `color`, `align` and 28 other pixel decisions came to live in Python
rather than in the layout files.
"""

from typing import Any

# Every parameter a primitive may read. schema.py asserts a layout's `defaults:`
# covers all of them, so an omission fails at startup rather than at whichever
# block first happens to need it.
PARAMETER_DEFAULTS: frozenset[str] = frozenset(
    {
        "role",
        "color",
        "align",
        "bold",
        "line_advance",
        "family",
        "rule_thickness",
        "rule_pad_above",
        "rule_pad_below",
        "rule_fill_char",
        "spacer_height",
        "pair_value_align",
        "pair_min_gap",
        "pair_separator",
        "table_header",
        "table_header_bold",
        "table_header_rule_top",
        "table_header_rule_gap",
        "table_row_inset_y",
        "table_cell_line_spacing",
        "table_group_gap",
        "table_fill_inset",
        "table_dividers",
        "table_offset_y",
        "table_capture",
        "table_sub_line_height",
        "banner_text_color",
        "banner_role",
        "banner_text_y",
        "panel_padding",
        "panel_border_color",
        "split_gap",
        "split_divider_color",
    }
)


class DefaultsError(RuntimeError):
    """Raised when neither a block nor its layout supplies a parameter."""


_SENTINEL = object()


def resolve_param(
    block: dict,
    layout: dict,
    key: str,
    *,
    layout_id: str,
    layout_path: str,
    block_key: str | None = None,
) -> Any:
    """Resolve one primitive parameter.

    Resolution order is `block[block_key]` -> `layout["defaults"][key]` -> fail
    fast. `key` and `block_key` are the same string in the common case, and
    `block_key` may be omitted -- they diverge only where `PARAMETER_DEFAULTS`
    namespaces a key a primitive shares with another. A panel's own YAML key is
    still `padding:`, but its default lives under `panel_padding` in the
    layout's flat `defaults:` namespace, since a bare `padding` default could
    not carry two different primitives' defaults at once; banner's own key is
    still `role:`, but its default lives under `banner_role`, since a bare
    `role` default could not simultaneously be "body" for text/pair/block and
    "header" for banner. Passing only `key` in these cases would silently drop
    every per-block override under the short name: the block's own YAML key
    never renames itself to match the namespaced default, so `block.get(key)`
    would never find it and would fall straight through to the shared default
    -- exactly the bug this argument exists to prevent.

    Args:
        block: The block dict, whose own `block_key` wins if present.
        layout: The resolved layout dict, carrying a `defaults:` mapping.
        key: The parameter name in `PARAMETER_DEFAULTS`, e.g. "color" or
            "panel_padding" -- looked up against `layout["defaults"]`.
        layout_id: Layout id, used in the diagnostic.
        layout_path: Path to the layout YAML, used in the diagnostic.
        block_key: The block's own literal YAML key for this value, if it
            differs from `key`. Defaults to `key` itself.

    Returns:
        The block's value if it carries `block_key`, otherwise the layout
        default for `key`. Typed `Any`, not `object`: this resolves
        heterogeneous YAML values (str, int, bool, list) that every caller
        immediately casts to the concrete type it needs (`int(...)`,
        `bool(...)`, `str(...)`) -- `object` buys no real type safety here
        (a caller's own cast is what actually enforces the type) and only
        forces every call site to route the result through an extra
        `Any`-typed local variable first, since `object` alone does not
        satisfy `int()`'s overloads.

    Raises:
        DefaultsError: If neither supplies a value.
    """
    block_key = key if block_key is None else block_key
    value = block.get(block_key, _SENTINEL)
    if value is not _SENTINEL:
        return value

    default = layout.get("defaults", {}).get(key, _SENTINEL)
    if default is not _SENTINEL:
        return default

    raise DefaultsError(
        "Missing layout default.\n"
        f"  What:     no value for '{block_key}' on this block, and layout "
        f"'{layout_id}' declares no default for '{key}'.\n"
        f"  Where:    {layout_path} -> {layout_id}.defaults.{key}\n"
        f"  Expected: a defaults: mapping covering every parameter, e.g.\n"
        f"              defaults:\n"
        f"                {key}: <value>\n"
        f"  Recover:  add '{key}:' under {layout_id}.defaults, or set "
        f"'{block_key}:' on the block itself when it varies block to block."
    )
