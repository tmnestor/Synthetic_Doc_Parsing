"""Overflow backstop: render each entry and collect FitError violations."""

import copy

import pytest
from typer.testing import CliRunner

from generators.overflow_check import OverflowError_, build_overflow_error, check_overflow
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import entry_per_layout, layouts_for, renderer_for


def receipt_entry(case: str, supplier: str | None = None) -> dict:
    """A real receipt entry, optionally with an unbreakable supplier name that can never fit."""
    entry = copy.deepcopy(next(iter(entry_per_layout("receipt").values())))
    if supplier is not None:
        entry["fields"]["SUPPLIER_NAME"] = supplier
    entry.pop("case_id", None)
    return entry


def test_collects_all_violations_without_raising() -> None:
    """Every overflowing entry is reported in one pass, naming case, layout and the first line."""
    gt = {
        "CASE901": receipt_entry("CASE901", "W" * 400),
        "CASE902": receipt_entry("CASE902"),
        "CASE903": receipt_entry("CASE903", "Q" * 500),
    }
    violations = check_overflow(gt, layouts_for("receipt"), renderer_for("receipt"))
    # The shipped receipt layouts carry the distractor provider, so each is rendered in both modes.
    assert len(violations) == 4
    assert violations[0].startswith(f"CASE901 / {gt['CASE901']['layout']} (plain): ")
    assert violations[1].startswith(f"CASE901 / {gt['CASE901']['layout']} (with distractors): ")
    assert violations[2].startswith("CASE903 / ") and "(plain)" in violations[2]
    assert violations[3].startswith("CASE903 / ") and "(with distractors)" in violations[3]
    assert all("string cannot fit its box losslessly" in line for line in violations)
    assert not any("\n" in line for line in violations)


def test_no_violations_when_render_ok() -> None:
    """Entries that fit produce an empty list."""
    gt = {"CASE901": receipt_entry("CASE901"), "CASE902": receipt_entry("CASE902")}
    assert check_overflow(gt, layouts_for("receipt"), renderer_for("receipt")) == []


def test_skips_entries_with_unknown_layout() -> None:
    """An entry naming a layout the registry lacks is skipped, not rendered, and not an error here."""
    entry = receipt_entry("CASE901", "W" * 400)
    entry["layout"] = "receipt_does_not_exist"
    assert check_overflow({"CASE901": entry}, layouts_for("receipt"), renderer_for("receipt")) == []


def test_build_overflow_error_is_four_element_diagnostic() -> None:
    """The aggregated error carries What, Where, Expected and Recover, and lists each violation."""
    violations = [
        "CASE901 / receipt_fuel: string cannot fit its box losslessly.",
        "CASE903 / receipt_fuel: x",
    ]
    error = build_overflow_error(violations)
    assert isinstance(error, OverflowError_)
    assert_diagnostic_error(error)
    assert "2 field(s) overflow" in str(error)
    assert all(v in str(error) for v in violations)
    assert "config/layouts/" in str(error)
    assert "/tmp/elsewhere/" in str(build_overflow_error(violations, layout_dir="/tmp/elsewhere/"))


def test_validate_passes_on_current_ground_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped documents all fit losslessly; validate must exit 0."""
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output


# --- both render modes ----------------------------------------------------------------------------

DX_ITEMS = ["rc_website", "rc_store_number", "rc_phone"]
DX_KEYS = ["DX_WEBSITE", "DX_STORE_NUMBER", "DX_PHONE"]


def dx_receipt_layout(narrow: str) -> tuple[str, dict]:
    """A real receipt layout drawing three distractors, one gated block given a box far too narrow."""
    layout_id, base = next(iter(layouts_for("receipt").items()))
    layout = copy.deepcopy(base)
    # The shipped layout already carries the provider; it draws only these three items here.
    assert {"name": "receipt_distractors", "params": {}} in layout["field_providers"]
    layout["distractor_items"] = list(DX_ITEMS)
    layout["field_budgets"]["DX_NARROW"] = {"width": 12, "fit": "shrink", "min_font": 10, "max_lines": 1}
    blocks = [
        {
            "type": "text",
            "content": f"{{{key}}}",
            "when": key,
            **({"budget": "DX_NARROW"} if key == narrow else {}),
        }
        for key in DX_KEYS
    ]
    layout["body"] = [*layout["body"], *blocks]
    return layout_id, layout


def test_a_distractor_that_overflows_only_when_drawn_is_reported_with_its_mode() -> None:
    from generators.distractors.config import load_distractor_config

    assert load_distractor_config().selection["receipt"][0] == len(DX_ITEMS)  # all three always drawn
    layout_id, layout = dx_receipt_layout("DX_WEBSITE")
    entry = receipt_entry("CASE901")
    entry["layout"] = layout_id
    violations = check_overflow({"CASE901": entry}, {layout_id: layout}, renderer_for("receipt"))
    assert len(violations) == 1, violations
    assert violations[0].startswith(f"CASE901 / {layout_id} (with distractors): ")
    assert "string cannot fit its box losslessly" in violations[0]


def test_a_fitting_distractor_layout_reports_nothing() -> None:
    layout_id, layout = dx_receipt_layout("NONE")
    entry = receipt_entry("CASE901")
    entry["layout"] = layout_id
    assert check_overflow({"CASE901": entry}, {layout_id: layout}, renderer_for("receipt")) == []


def _modes(gt: dict, layouts: dict) -> list[bool]:
    modes: list[bool] = []

    def spy(entry: dict, layout: dict, *, distractors: bool) -> None:
        modes.append(distractors)

    check_overflow(gt, layouts, spy)
    return modes


def test_a_layout_with_a_distractor_provider_is_rendered_in_both_modes() -> None:
    layout_id, layout = dx_receipt_layout("NONE")
    entry = receipt_entry("CASE901")
    entry["layout"] = layout_id
    assert _modes({"CASE901": entry}, {layout_id: layout}) == [False, True]


def test_a_layout_without_a_distractor_provider_is_rendered_plain_only() -> None:
    """No distractor provider, so a distractor render would repeat the plain one: skipped."""
    gt = {"CASE901": receipt_entry("CASE901")}
    layout_id = gt["CASE901"]["layout"]
    layout = copy.deepcopy(layouts_for("receipt")[layout_id])
    layout["field_providers"] = [s for s in layout["field_providers"] if s["name"] != "receipt_distractors"]
    assert _modes(gt, {layout_id: layout}) == [False]


def test_a_shipped_receipt_layout_is_rendered_in_both_modes() -> None:
    gt = {"CASE901": receipt_entry("CASE901")}
    assert _modes(gt, layouts_for("receipt")) == [False, True]


def test_an_unregistered_provider_name_fails_fast_rather_than_rendering_plain_only() -> None:
    from generators.layout_dsl.provider_registry import FieldProviderError
    from generators.overflow_check import render_modes

    with pytest.raises(FieldProviderError) as err:
        render_modes({"field_providers": [{"name": "zz_unregistered"}]})
    assert_diagnostic_error(err.value)
    assert "zz_unregistered" in str(err.value)


# --- measuring the overflow pass's own renders ----------------------------------------------------


def test_measure_receives_each_fitting_distractor_render_once() -> None:
    layout_id, layout = dx_receipt_layout("NONE")
    entry = receipt_entry("CASE901")
    entry["layout"] = layout_id
    seen: list[tuple[str, str, list[str]]] = []
    violations = check_overflow(
        {"CASE901": entry},
        {layout_id: layout},
        renderer_for("receipt"),
        measure=lambda case_id, layout_ref, geometry: seen.append((case_id, layout_ref, sorted(geometry))),
    )
    assert violations == []
    assert seen == [
        ("CASE901", layout_id, ["blocks", "boxes", "height", "ink", "labels", "regions", "texts", "width"])
    ]


def test_measure_skips_a_render_that_overflows() -> None:
    layout_id, layout = dx_receipt_layout("DX_WEBSITE")
    entry = receipt_entry("CASE901")
    entry["layout"] = layout_id
    seen: list[str] = []
    violations = check_overflow(
        {"CASE901": entry},
        {layout_id: layout},
        renderer_for("receipt"),
        measure=lambda case_id, layout_ref, geometry: seen.append(case_id),
    )
    assert len(violations) == 1 and seen == []
