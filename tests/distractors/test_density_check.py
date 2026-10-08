"""`validate` bounds the density of every distractor render of a layout carrying a distractor provider."""

import copy
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from generators import pipeline
from generators.distractors import density
from generators.distractors.config import DensityBounds, load_distractor_config
from generators.distractors.density import check_density, measure_render
from generators.distractors.paths import DISTRACTORS_PATH
from generators.pipeline import app
from generators.layout_dsl.provider_registry import is_distractor_provider
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, layouts_for, renderer_for

LOOSE = DensityBounds(scored_ink_ratio_max=1.0, distractor_share_min=0.0, page_fill_min=0.0)
NO_SCORED_INK = DensityBounds(scored_ink_ratio_max=0.0, distractor_share_min=0.0, page_fill_min=0.0)
FULL_PAGE = DensityBounds(scored_ink_ratio_max=1.0, distractor_share_min=0.0, page_fill_min=1.0)
ALL_DISTRACTOR = DensityBounds(scored_ink_ratio_max=1.0, distractor_share_min=1.0, page_fill_min=None)
UNBOUNDED_FILL = DensityBounds(scored_ink_ratio_max=1.0, distractor_share_min=0.0, page_fill_min=None)


def with_provider(layouts: dict) -> dict:
    """Copies of `layouts`, each carrying the receipt distractor provider and every receipt item."""
    items = [i for i, item in load_distractor_config().items.items() if "receipt" in item.doc_types]
    out = {}
    for layout_id, layout in layouts.items():
        copied = copy.deepcopy(layout)
        copied["field_providers"] = [
            *(s for s in copied["field_providers"] if s["name"] != "receipt_distractors"),
            {"name": "receipt_distractors", "params": {}},
        ]
        copied["distractor_items"] = items
        out[layout_id] = copied
    return out


def without_provider(layouts: dict) -> dict:
    """Copies of `layouts` with every distractor provider and the layout's items removed."""
    out = {}
    for layout_id, layout in layouts.items():
        copied = copy.deepcopy(layout)
        copied["field_providers"] = [
            s for s in copied["field_providers"] if not is_distractor_provider(s["name"])
        ]
        copied["distractor_items"] = []
        out[layout_id] = copied
    return out


def _gt(doc_type: str, count: int = 4) -> dict:
    return dict(list(corpus_entries(doc_type).items())[:count])


def _check(gt: dict, layouts: dict, bounds: DensityBounds, doc_type: str = "receipt") -> list[str]:
    return check_density(
        gt, layouts, renderer_for(doc_type), bounds, doc_type=doc_type, config_path=DISTRACTORS_PATH
    )


def test_a_layout_without_a_distractor_provider_is_never_enforced() -> None:
    """Spec section 9: plain renders are measured, never enforced."""
    assert _check(_gt("receipt"), without_provider(layouts_for("receipt")), NO_SCORED_INK) == []
    assert _check(_gt("invoice"), without_provider(layouts_for("invoice")), FULL_PAGE, "invoice") == []


def test_loose_bounds_pass_a_provider_layout() -> None:
    assert _check(_gt("receipt"), with_provider(layouts_for("receipt")), LOOSE) == []


def test_a_scored_ratio_above_its_bound_is_a_four_part_refusal() -> None:
    gt = _gt("receipt", 2)
    errors = _check(gt, with_provider(layouts_for("receipt")), NO_SCORED_INK)
    assert len(errors) == 2
    for (case_id, entry), error in zip(gt.items(), errors, strict=True):
        assert_diagnostic_error(error)
        assert case_id in error and entry["layout"] in error
        assert "scored_ink_ratio" in error and "0.0" in error
        assert f"{DISTRACTORS_PATH.resolve()} -> density.receipt.scored_ink_ratio_max" in error


def test_a_page_fill_below_its_bound_is_a_four_part_refusal() -> None:
    errors = _check(_gt("receipt", 1), with_provider(layouts_for("receipt")), FULL_PAGE)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "page_fill" in errors[0] and "density.receipt.page_fill_min" in errors[0]


def test_a_distractor_share_below_its_floor_is_a_four_part_refusal() -> None:
    """A page whose text is not all distractor fails a floor of 1, naming case, layout and value."""
    gt = _gt("receipt", 2)
    errors = _check(gt, with_provider(layouts_for("receipt")), ALL_DISTRACTOR)
    assert len(errors) == 2
    for (case_id, entry), error in zip(gt.items(), errors, strict=True):
        assert_diagnostic_error(error)
        assert case_id in error and entry["layout"] in error
        assert "distractor_share 0." in error and "below distractor_share_min 1.0" in error
        assert f"{DISTRACTORS_PATH.resolve()} -> density.receipt.distractor_share_min" in error


def test_a_null_page_fill_bound_skips_the_fill_check() -> None:
    gt = _gt("receipt", 2)
    layouts = with_provider(layouts_for("receipt"))
    assert _check(gt, layouts, FULL_PAGE)
    assert _check(gt, layouts, UNBOUNDED_FILL) == []


def test_only_the_distractor_render_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[bool] = []
    real = renderer_for("receipt")

    def spy(entry: dict, layout: dict, *, distractors: bool, geometry_out: dict | None = None) -> object:
        modes.append(distractors)
        return real(entry, layout, distractors=distractors, geometry_out=geometry_out)

    check_density(
        _gt("receipt", 2),
        with_provider(layouts_for("receipt")),
        spy,
        LOOSE,
        doc_type="receipt",
        config_path=DISTRACTORS_PATH,
    )
    assert modes == [True, True]


@pytest.mark.parametrize("doc_type", ["receipt", "bank_statement"])
def test_the_shipped_bounds_pass_every_current_render(doc_type: str) -> None:
    """The measured bounds hold for every case's distractor render.

    Plain renders are the control and are not held to the bounds, which distractors meet (bank statements
    joined in Stage 5, when their inline distractors landed with their measured bounds).
    """
    bounds = load_distractor_config().density[doc_type]
    layouts = layouts_for(doc_type)
    modes = (True,)
    for case_id, entry in corpus_entries(doc_type).items():
        for distractors in modes:
            report = measure_render(renderer_for(doc_type), entry, layouts[entry["layout"]], distractors)
            assert report.scored_ink_ratio <= bounds.scored_ink_ratio_max, (case_id, report)
            if bounds.page_fill_min is not None:
                assert report.page_fill >= bounds.page_fill_min, (case_id, report)
            if distractors:
                assert report.distractor_share >= bounds.distractor_share_min, (case_id, report)


def test_validate_refuses_a_distractor_render_out_of_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    real_registry = pipeline.load_layout_registry

    def registry(path: Path) -> dict:
        layouts = real_registry(path)
        return with_provider(layouts) if "receipts" in str(path) else layouts

    real_config = load_distractor_config()
    tight = {**real_config.density, "receipt": NO_SCORED_INK}
    monkeypatch.setattr(pipeline, "load_layout_registry", registry)
    monkeypatch.setattr(pipeline, "load_distractor_config", lambda: replace(real_config, density=tight))
    # The schema refuses listed items no block draws; density is what this test exercises.
    monkeypatch.setattr(pipeline, "validate_layout", lambda *args, **kwargs: None)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "Distractor density out of bounds" in output
    assert "density.receipt.scored_ink_ratio_max" in output
    assert "CASE001" in output


def test_validate_reports_density_through_the_density_module() -> None:
    assert pipeline.DensityCheck is density.DensityCheck


def _validate_with_receipt_provider(monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.chdir(REPO_ROOT)
    real_registry = pipeline.load_layout_registry

    def registry(path: Path) -> dict:
        layouts = real_registry(path)
        return with_provider(layouts) if "receipts" in str(path) else layouts

    monkeypatch.setattr(pipeline, "load_layout_registry", registry)
    monkeypatch.setattr(pipeline, "validate_layout", lambda *args, **kwargs: None)
    return CliRunner().invoke(app, ["validate"])


def test_validate_renders_each_distractor_case_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Density is measured on the overflow pass's own distractor render, not on a second one.

    The labelled varied render (a label choice passed as `labels`) is the third mode, drawn once per
    accepted choice and again for each redraw; it is counted apart and never stands in for the distractor
    render.
    """
    calls: Counter[tuple[str, bool]] = Counter()
    varied: Counter[str] = Counter()
    real = pipeline._RENDERERS["receipts"]

    def spy(entry: dict, layout: dict, **kwargs: object) -> object:
        if kwargs.get("labels") is not None:
            varied[entry["case_id"]] += 1
        else:
            calls[(entry["case_id"], bool(kwargs["distractors"]))] += 1
        return real(entry, layout, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setitem(pipeline._RENDERERS, "receipts", spy)
    result = _validate_with_receipt_provider(monkeypatch)
    assert result.exit_code == 0, result.output
    assert len(calls) == 2 * len(corpus_entries("receipt"))
    assert set(calls.values()) == {1}, sorted(key for key, count in calls.items() if count > 1)[:5]
    assert set(varied) == set(corpus_entries("receipt"))
