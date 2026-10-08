"""Bank layouts validate through the DSL, and legacy dispatch keys are gone.

Companion to the deletion of the per-bank renderers in generators/bank_statement.py:
`pipeline validate` must reject a malformed layout body before any rendering
starts, and the YAML must carry no key only the deleted legacy renderers ever
read.
"""

import copy
from pathlib import Path

import pytest
from typer.testing import CliRunner


from generators import bank_statement
from generators.layout_dsl.schema import LayoutSchemaError, validate_body, validate_layout
from generators.pipeline import app
from generators.schema import field_names_for
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import layout_path_for, layouts_for, write_single_type_config

DOC = "bank_statement"
KNOWN = set(field_names_for("bank_statements"))

# Keys that only the deleted per-bank renderers read (removed in the dead-key sweep).
DEAD_KEYS = {
    "renderer",
    "variant",
    "column_headers",
    "date_grouping",
    "bank_name_color",
    "logo_color",
    "header_color",
    "header_bar_color",
    "balance_suffix",
    "balance_suffix_debit",
    "balance_suffix_credit",
    "bank_code",
    "date_format",
}


def validate(layout_id: str, layout: dict) -> None:
    validate_layout(
        layout, layout_id=layout_id, layout_path=layout_path_for(DOC), known_fields=KNOWN, doc_type=DOC
    )


def test_every_bank_layout_validates() -> None:
    """Every bank layout passes the same structural validation `pipeline validate` runs."""
    layouts = layouts_for(DOC)
    assert layouts
    for layout_id, layout in layouts.items():
        assert "body" in layout, layout_id
        validate(layout_id, layout)


def test_no_bank_layout_retains_dead_dispatch_keys() -> None:
    """No layout carries a key only the deleted per-bank renderers read (renderer, show_*, balance_suffix...)."""
    for layout_id, layout in layouts_for(DOC).items():
        stale = {k for k in layout if k in DEAD_KEYS or k.startswith("show_")}
        assert not stale, f"{layout_id} still carries {sorted(stale)}"


def test_bank_statement_module_no_longer_exposes_per_bank_renderers() -> None:
    """The module is a thin adapter: the per-bank renderers and their dispatch table are gone."""
    for name in ("render_cba", "render_westpac", "render_nab", "render_anz", "_BANK_RENDERERS"):
        assert not hasattr(bank_statement, name), name
    assert callable(bank_statement.render_bank_statement)
    assert callable(bank_statement.render_via_dsl)


def test_validation_rejects_a_broken_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed body fails with the four-element diagnostic, and `pipeline validate` rejects it too."""
    layout_id = next(iter(layouts_for(DOC)))
    cli_layout = copy.deepcopy(layouts_for(DOC)[layout_id])
    cli_layout["body"].append({"type": "no_such_primitive"})
    config = write_single_type_config(tmp_path, "bank_statements", {layout_id: cli_layout})
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert "no_such_primitive" in result.output

    broken = copy.deepcopy(layouts_for(DOC)[layout_id])
    broken["body"].append({"type": "no_such_primitive"})
    with pytest.raises(LayoutSchemaError) as err:
        validate(layout_id, broken)
    assert_diagnostic_error(err.value)
    assert "no_such_primitive" in str(err.value)

    unknown_field = copy.deepcopy(layouts_for(DOC)[layout_id])
    unknown_field["body"].append({"type": "text", "content": "{NOT_A_REAL_FIELD}"})
    with pytest.raises(LayoutSchemaError) as err2:
        validate(layout_id, unknown_field)
    assert_diagnostic_error(err2.value)
    assert "NOT_A_REAL_FIELD" in str(err2.value)


def test_unclosed_placeholder_is_rejected() -> None:
    """An unclosed '{FIELD' would render as a silent literal, so validation rejects it.

    Hand-authored receipt and invoice bodies are exactly where this typo happens,
    so all three document types are checked.
    """
    for stem in ("bank_statements", "receipts", "invoices"):
        known = set(field_names_for(stem))
        for bad in ("{SUPPLIER_NAME", "SUPPLIER_NAME}", "Name {SUPPLIER_NAME and more"):
            with pytest.raises(LayoutSchemaError) as err:
                validate_body(
                    [{"type": "text", "content": bad}],
                    layout_id="layout_under_test",
                    layout_path=f"config/layouts/{stem}.yml",
                    known_fields=known,
                )
            assert_diagnostic_error(err.value)
            assert "unbalanced placeholder brace" in str(err.value)
        validate_body(
            [{"type": "text", "content": "Supplier: {SUPPLIER_NAME}"}],
            layout_id="layout_under_test",
            layout_path=f"config/layouts/{stem}.yml",
            known_fields=known,
        )
