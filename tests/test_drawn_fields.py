"""The drawn-field guard, and the NAB statement period it was written for."""

import copy
from pathlib import Path

import pytest
import yaml
from PIL import ImageDraw, ImageStat
from typer.testing import CliRunner

from generators import drawn_fields
from generators.drawn_fields import DrawnFieldsCheck, DrawnFieldsError, has_value
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import (
    corpus_entries,
    layout_path_for,
    layouts_for,
    renderer_for,
    write_single_type_config,
)

NAB_LAYOUTS = ("nab_classic", "nab_dense")
DOC_TYPE_KEYS = {"bank_statement": "bank_statements", "receipt": "receipts", "invoice": "invoices"}


def _nab_cases() -> list[str]:
    return [cid for cid, e in corpus_entries("bank_statement").items() if e["layout"] in NAB_LAYOUTS]


def _strip_binding(node: object, field: str) -> int:
    """Remove every `field: <field>` binding below `node`; return how many were removed."""
    removed = 0
    if isinstance(node, dict):
        if node.get("field") == field:
            del node["field"]
            removed += 1
        for child in node.values():
            removed += _strip_binding(child, field)
    elif isinstance(node, list):
        for child in node:
            removed += _strip_binding(child, field)
    return removed


def _check(doc_type: str, gt: dict, layout_path: Path | None = None) -> DrawnFieldsCheck:
    stem = DOC_TYPE_KEYS[doc_type]
    return DrawnFieldsCheck(
        gt,
        doc_type=stem,
        layout_path=layout_path or REPO_ROOT / layout_path_for(doc_type),
        ground_truth_path=REPO_ROOT / "ground_truth" / f"{stem}.yml",
    )


def _run(check: DrawnFieldsCheck, doc_type: str, layouts: dict, gt: dict) -> None:
    for case_id, entry in gt.items():
        geometry: dict = {}
        renderer_for(doc_type)(
            {**entry, "case_id": case_id},
            layouts[entry["layout"]],
            distractors=False,
            geometry_out=geometry,
        )
        check(case_id, entry["layout"], geometry)


@pytest.fixture(autouse=True)
def repo_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard reads config/field_definitions.yml relative to the repository root."""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr("generators.schema._FIELD_DEFS", None)


def test_nab_corpus_size() -> None:
    """Fourteen NAB statements carry the period (the defect's scope)."""
    assert len(_nab_cases()) == 14


@pytest.mark.parametrize("doc_type", list(DOC_TYPE_KEYS))
def test_corpus_passes(doc_type: str) -> None:
    """Every entry of the shipped corpus draws every scored value, or is declared exempt."""
    gt = corpus_entries(doc_type)
    check = _check(doc_type, gt)
    _run(check, doc_type, layouts_for(doc_type), gt)
    assert check.errors == []


def test_guard_fails_when_binding_removed() -> None:
    """Stripping the period binding from the NAB layouts refuses all 14 statements, four-part."""
    layouts = copy.deepcopy(layouts_for("bank_statement"))
    # The two layouts share one account-box block through a YAML anchor, so one strip clears both.
    assert sum(_strip_binding(layouts[i]["body"], "STATEMENT_DATE_RANGE") for i in NAB_LAYOUTS) >= 1
    gt = {cid: e for cid, e in corpus_entries("bank_statement").items() if e["layout"] in NAB_LAYOUTS}
    check = _check("bank_statement", gt)
    _run(check, "bank_statement", layouts, gt)
    assert len(check.errors) == 14
    for error in check.errors:
        assert_diagnostic_error(error)
        assert "STATEMENT_DATE_RANGE" in error
        assert str((REPO_ROOT / "config/layouts/bank_statements.yml").resolve()) in error
        assert str((REPO_ROOT / "ground_truth/bank_statements.yml").resolve()) in error
        assert "field: STATEMENT_DATE_RANGE" in error
    assert any("CASE" in e and "nab_classic" in e for e in check.errors)
    assert any("layouts.nab_dense.body" in e for e in check.errors)


def test_guard_fails_for_a_different_type_and_field() -> None:
    """The guard is not specific to bank statements: an unbound invoice date is refused."""
    layouts = copy.deepcopy(layouts_for("invoice"))
    layout_id = "tax_invoice_standard"
    assert _strip_binding(layouts[layout_id], "INVOICE_DATE") >= 1
    gt = {cid: e for cid, e in corpus_entries("invoice").items() if e["layout"] == layout_id}
    check = _check("invoice", gt)
    _run(check, "invoice", layouts, gt)
    assert check.errors and all("INVOICE_DATE" in e for e in check.errors)
    assert_diagnostic_error(check.errors[0])


def test_list_of_not_found_is_not_a_value() -> None:
    """A pipe list whose every item is NOT_FOUND needs no box; one real item does."""
    assert not has_value("NOT_FOUND|NOT_FOUND")
    assert not has_value(None)
    assert has_value("NOT_FOUND|1836.06")
    assert has_value("02/08/2024 - 31/08/2024")


def test_malformed_exemption_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exemption without a reason fails fast with the four parts."""
    defs = yaml.safe_load((REPO_ROOT / "config/field_definitions.yml").read_text())
    defs["geometry_exemptions"]["BANK_STATEMENT"]["DOCUMENT_TYPE"] = {"layouts": "all"}
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    with pytest.raises(DrawnFieldsError) as caught:
        drawn_fields.load_exemptions("bank_statements")
    assert_diagnostic_error(caught.value)


def test_missing_exemptions_block_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `geometry_exemptions` block is an error, never a silent empty default."""
    defs = yaml.safe_load((REPO_ROOT / "config/field_definitions.yml").read_text())
    del defs["geometry_exemptions"]
    monkeypatch.setattr("generators.schema._FIELD_DEFS", defs)
    with pytest.raises(DrawnFieldsError) as caught:
        drawn_fields.load_exemptions("receipts")
    assert_diagnostic_error(caught.value)


@pytest.mark.parametrize("case_id", _nab_cases())
def test_nab_period_box_has_ink_and_matches_ground_truth(
    case_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded period box holds ink, and the string drawn is the labelled ground-truth value."""
    entry = corpus_entries("bank_statement")[case_id]
    expected = entry["fields"]["STATEMENT_DATE_RANGE"]
    drawn: list[str] = []
    original = ImageDraw.ImageDraw.text

    def spy(self: ImageDraw.ImageDraw, xy: object, text: str, *args: object, **kwargs: object) -> object:
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    geometry: dict = {}
    image = renderer_for("bank_statement")(
        entry, layouts_for("bank_statement")[entry["layout"]], distractors=False, geometry_out=geometry
    )
    assert f"Statement Period: {expected}" in drawn
    left, top, right, bottom = geometry["boxes"]["STATEMENT_DATE_RANGE"]
    box = (
        int(left * geometry["width"]),
        int(top * geometry["height"]),
        int(right * geometry["width"]) + 1,
        int(bottom * geometry["height"]) + 1,
    )
    region = image.convert("L").crop(box)
    darkest = ImageStat.Stat(region).extrema[0][0]
    assert darkest < 160, f"{case_id}: no ink in the recorded period box {box}"
    # The box lies inside the account-details panel, right of the account number, not beyond the page.
    assert 0 < left < right < 1


@pytest.mark.parametrize("case_id", _nab_cases()[:4])
def test_nab_period_is_distinct_from_the_issued_date(case_id: str) -> None:
    """Drawn with distractors, the period and the 'Statement issued' date never share a box."""
    entry = corpus_entries("bank_statement")[case_id]
    geometry: dict = {}
    renderer_for("bank_statement")(
        entry, layouts_for("bank_statement")[entry["layout"]], distractors=True, geometry_out=geometry
    )
    period = geometry["boxes"]["STATEMENT_DATE_RANGE"]
    w, h = geometry["width"], geometry["height"]
    p = (period[0] * w, period[1] * h, period[2] * w, period[3] * h)
    distractor_boxes = [r.box for r in geometry["ink"] if r.role == "distractor"]
    assert distractor_boxes
    for dl, dt, dr, db in distractor_boxes:
        overlap = p[0] < dr and dl < p[2] and p[1] < db and dt < p[3]
        assert not overlap, f"{case_id}: period box {p} meets a distractor box {(dl, dt, dr, db)}"


def test_validate_refuses_a_layout_that_drops_the_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pipeline validate` over a temporary NAB layout without the period binding exits 1, naming it."""
    layouts = copy.deepcopy(layouts_for("bank_statement"))
    _strip_binding(layouts["nab_classic"]["body"], "STATEMENT_DATE_RANGE")
    config = write_single_type_config(tmp_path, "bank_statements", layouts)
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert "STATEMENT_DATE_RANGE" in result.output
    assert "does not draw" in result.output
