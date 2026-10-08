"""Parameter resolution: block key, then the layout's `defaults:`, then fail fast."""

import ast
from pathlib import Path

import pytest

from generators.layout_dsl import primitives_table
from generators.layout_dsl.defaults import PARAMETER_DEFAULTS, DefaultsError, resolve_param
from generators.layout_dsl.schema import LayoutSchemaError, validate_layout
from generators.loader import load_layout_registry
from tests.conftest import REPO_ROOT, assert_diagnostic_error

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

LAYOUT_DIR = REPO_ROOT / "config" / "layouts"
PRIMITIVE_FILES = sorted((REPO_ROOT / "generators" / "layout_dsl").glob("primitives_*.py"))

# Receivers whose `.get(...)` reads layout configuration an author wrote in YAML: a block, and a table
# column's nested sub-line spec. `row` (provider-set data) and `column` (structural table spec) are
# deliberately absent: they are not layout parameters.
LAYOUT_RECEIVERS = frozenset({"block", "sub_line"})

# Block keys a primitive reads directly because they are structure or content, not a pixel decision a
# layout default could supply: what to draw, where it is recorded, or a provider passthrough.
NON_PARAMETER_BLOCK_KEYS = frozenset(
    {
        "budget",
        "currency",
        "divider",
        "field",
        "fill_color",
        "heading",
        "height",
        "label_inset_y",
        "params",  # a table's passthrough to its row provider, not a pixel decision
        "params_from_layout",  # the same passthrough, its values read off the layout
        "region",  # a stack's declared region name, recorded with its box, not a pixel decision
        "span_fields",  # a sub-line's value extents -> recorded fields, not a pixel decision
        "suppress_if_equals",
        "synthetic_row_placement",
        "widths",
    }
)


def _calls(path: Path) -> list[ast.Call]:
    return [node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.Call)]


def _resolve_param_calls() -> list[tuple[str, str, str | None]]:
    """Return (receiver, key, block_key) for every resolve_param call in the primitive files."""
    found = []
    for path in PRIMITIVE_FILES:
        for call in _calls(path):
            if isinstance(call.func, ast.Name) and call.func.id == "resolve_param":
                key_node = call.args[2]
                assert isinstance(key_node, ast.Constant), (
                    f"{path.name}: non-literal key {ast.unparse(call)}"
                )
                block_key = next(
                    (kw.value.value for kw in call.keywords if kw.arg == "block_key"),  # type: ignore[attr-defined]
                    None,
                )
                found.append((ast.unparse(call.args[0]), str(key_node.value), block_key))
    return found


def _resolve(block: dict, layout: dict, key: str, **kwargs: str) -> object:
    return resolve_param(block, layout, key, layout_id="demo", layout_path="demo.yml", **kwargs)


def test_block_key_wins_over_defaults() -> None:
    assert _resolve({"color": "red"}, {"defaults": {"color": "black"}}, "color") == "red"


def test_defaults_supply_an_absent_block_key() -> None:
    assert _resolve({}, {"defaults": {"color": "black"}}, "color") == "black"


def test_falsy_default_is_not_treated_as_absent() -> None:
    """`bold: false` is a real configured value, not a missing key."""
    assert _resolve({}, {"defaults": {"bold": False}}, "bold") is False
    assert _resolve({"bold": False}, {"defaults": {"bold": True}}, "bold") is False
    assert _resolve({}, {"defaults": {"spacer_height": 0}}, "spacer_height") == 0


def test_missing_default_fails_with_a_four_element_diagnostic() -> None:
    with pytest.raises(DefaultsError) as exc_info:
        _resolve({}, {"defaults": {}}, "color")
    assert_diagnostic_error(exc_info.value)
    assert "color" in str(exc_info.value)
    assert "demo.yml" in str(exc_info.value)
    with pytest.raises(DefaultsError):
        _resolve({}, {}, "color")


def test_block_key_override_wins_when_it_differs_from_the_default_name() -> None:
    """A panel's `padding:` must beat the layout's `panel_padding` default, not fall through to it."""
    layout = {"defaults": {"panel_padding": 9}}
    assert _resolve({"padding": 3}, layout, "panel_padding", block_key="padding") == 3
    # Without block_key the lookup is under the namespaced name, which no block ever sets: the bug.
    assert _resolve({"padding": 3}, layout, "panel_padding") == 9


def test_block_key_falls_through_to_the_default_when_absent() -> None:
    """The default is still looked up under `key`, not `block_key`."""
    layout = {"defaults": {"panel_padding": 9, "padding": 1}}
    assert _resolve({}, layout, "panel_padding", block_key="padding") == 9


def test_parameter_defaults_covers_all_primitive_get_sites() -> None:
    """Every layout-parameter read in the primitive files resolves through a PARAMETER_DEFAULTS name.

    Keyed off the receiver: `resolve_param(block, ...)` keys must be PARAMETER_DEFAULTS names, and a bare
    `block.get(...)` / `sub_line.get(...)` may read only the explicitly named structural keys, never a
    parameter or a block key that a `resolve_param` call already owns.
    """
    resolved = _resolve_param_calls()
    assert len(resolved) > 30  # the scan is not vacuous
    for receiver, key, _ in resolved:
        assert key in PARAMETER_DEFAULTS, (
            f"resolve_param key {key!r} ({receiver}) is not in PARAMETER_DEFAULTS"
        )

    owned_block_keys = {block_key or key for _, key, block_key in resolved}
    seen_get_keys: set[str] = set()
    for path in PRIMITIVE_FILES:
        for call in _calls(path):
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id in LAYOUT_RECEIVERS):
                continue
            if not (call.args and isinstance(call.args[0], ast.Constant)):
                continue
            name = str(call.args[0].value)
            seen_get_keys.add(name)
            where = f"{path.name}: {ast.unparse(call)}"
            assert name not in PARAMETER_DEFAULTS, f"{where} bypasses resolve_param"
            if name in owned_block_keys and name not in NON_PARAMETER_BLOCK_KEYS:
                pytest.fail(f"{where} reads a block key that resolve_param owns")
            assert name in NON_PARAMETER_BLOCK_KEYS, f"{where} is neither a parameter nor a named block key"
    assert seen_get_keys, "no block.get calls found"


def test_row_data_never_resolves_through_the_layout() -> None:
    """Provider row data stays on `row.get(...)`: it is a fact about the row, not layout configuration."""
    for path in PRIMITIVE_FILES:
        for call in _calls(path):
            if isinstance(call.func, ast.Name) and call.func.id == "resolve_param":
                assert ast.unparse(call.args[0]) != "row", f"{path.name}: {ast.unparse(call)}"
    # The guard is meaningful: row data really is read via row.get in the table primitive.
    row_keys = {
        str(call.args[0].value)
        for call in _calls(Path(primitives_table.__file__))
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "row"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert {"bold", "date"} <= row_keys
    # Behaviour: a row's bold flag is decided by the row alone, with no layout in reach.
    assert primitives_table._cell_bold({"bold": True}, "amount") is True
    assert primitives_table._cell_bold({"bold": {"amount"}}, "amount") is True
    assert primitives_table._cell_bold({"bold": {"amount"}}, "date") is False
    assert primitives_table._cell_bold({}, "amount") is False


def test_incomplete_defaults_block_is_rejected() -> None:
    layouts = load_layout_registry(LAYOUT_DIR / "receipts.yml")
    layout = {**layouts["receipt_thermal_80mm"]}
    layout["defaults"] = {k: v for k, v in layout["defaults"].items() if k not in ("color", "split_gap")}
    with pytest.raises(LayoutSchemaError) as exc_info:
        validate_layout(
            layout, layout_id="demo", layout_path="demo.yml", known_fields=set(), doc_type="receipts"
        )
    assert_diagnostic_error(exc_info.value)
    assert "color" in str(exc_info.value)
    assert "split_gap" in str(exc_info.value)


def _assert_covers(path: Path) -> int:
    layouts = load_layout_registry(path)
    checked = 0
    for layout_id, layout in layouts.items():
        if "body" not in layout:
            continue
        missing = sorted(PARAMETER_DEFAULTS - set(layout["defaults"]))
        assert not missing, f"{path.name}:{layout_id} lacks defaults for {missing}"
        checked += 1
    return checked


def test_bank_layouts_cover_every_parameter_name() -> None:
    """A parameter added to PARAMETER_DEFAULTS must be seeded in the shipped bank layouts."""
    assert _assert_covers(LAYOUT_DIR / "bank_statements.yml") == 8


def test_all_layouts_cover_every_parameter_name() -> None:
    """Every declarative layout in the repo, not only the bank ones, carries a full `defaults:`."""
    total = sum(_assert_covers(path) for path in sorted(LAYOUT_DIR.glob("*.yml")))
    # 8 bank, 6 receipt, 8 invoice (the four general layouts and the four industry ones).
    assert total == 22
