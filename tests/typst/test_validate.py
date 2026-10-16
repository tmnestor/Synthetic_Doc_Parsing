"""`validate`'s typeset pass: each case typeset plain and with distractors, every failure collected.

The pass runs over real cases here, one statement (which draws overlays) at a time; each failure is
provoked by handing the pass something wrong (a stage-one page that says something else, a bound no page
meets, a page Typst refuses), never by editing a layout.
"""

import dataclasses

import pytest

import generators.typst.validate as typeset_validate
from generators.distractors.config import load_distractor_config
from generators.exporters.eval_projection import load_extraction_schema
from generators.loader import load_ground_truth, load_layout_registry
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.pipeline import _RENDERERS
from generators.typst.compile import TypstError
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from generators.typst.validate import TYPESET_DISTRACTOR_RENDER, StageOnePages, typeset_errors
from tests.conftest import REPO_ROOT, assert_diagnostic_error

KEY = "bank_statements"
LAYOUT_PATH = REPO_ROOT / "config" / "layouts" / f"{KEY}.yml"


def _cases(count: int) -> dict:
    """The first `count` statement cases by case id."""
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / f"{KEY}.yml")
    return {case_id: entries[case_id] for case_id in sorted(entries, key=str)[:count]}


def _stage_one(gt_data: dict, layouts: dict, renderer) -> dict[tuple[str, bool], dict]:  # noqa: ANN001
    """Stage one's render of each case in each mode, as validate's own pass keeps them."""
    pages: dict[tuple[str, bool], dict] = {}
    for case_id, entry in gt_data.items():
        for distractors in (False, True):
            geometry: dict = {}
            renderer(
                {**entry, "case_id": str(case_id)},
                layouts[entry["layout"]],
                distractors=distractors,
                geometry_out=geometry,
            )
            pages[(str(case_id), distractors)] = geometry
    return pages


def _run(gt_data: dict, *, renderer=None, distractor_config=None) -> list[str]:  # noqa: ANN001
    layouts = load_layout_registry(LAYOUT_PATH)
    return typeset_errors(
        gt_data,
        layouts,
        _stage_one(gt_data, layouts, renderer or _RENDERERS[KEY]),
        doc_type=load_extraction_schema().resolve_doc_type(KEY),
        layout_path=LAYOUT_PATH,
        parsing=load_parsing_config(PARSING_CONFIG_PATH),
        distractor_config=distractor_config or load_distractor_config(),
        config=load_typeset_config(TYPESET_CONFIG_PATH),
    )


@pytest.mark.slow
def test_a_statement_typesets_plain_and_with_its_overlays_with_no_failure() -> None:
    assert _run(_cases(1)) == []


@pytest.mark.slow
def test_a_page_that_differs_from_stage_one_is_refused_four_part() -> None:
    real = _RENDERERS[KEY]

    def renderer(entry: dict, layout: dict, *, distractors: bool, geometry_out: dict) -> object:
        image = real(entry, layout, distractors=distractors, geometry_out=geometry_out)
        # Stage one's page says something the typeset page does not.
        ink = geometry_out["ink"]
        index = next(i for i, record in enumerate(ink) if record.role == "scored")
        ink[index] = ink[index]._replace(text="Quokka")
        return image

    errors = _run(_cases(1), renderer=renderer)
    assert len(errors) == 2  # plain and with distractors
    for error in errors:
        assert_diagnostic_error(error)
        assert "differs from stage one" in error and "Quokka" in error


@pytest.mark.slow
def test_a_typeset_distractor_page_out_of_its_density_bounds_is_refused_four_part() -> None:
    config = load_distractor_config()
    doc_type = load_extraction_schema().resolve_doc_type(KEY)
    tight = dataclasses.replace(config.density[doc_type], scored_ink_ratio_max=0.0)
    errors = _run(
        _cases(1),
        distractor_config=dataclasses.replace(config, density={**config.density, doc_type: tight}),
    )
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "scored_ink_ratio" in errors[0] and TYPESET_DISTRACTOR_RENDER in errors[0]


@pytest.mark.slow
def test_a_page_typst_refuses_is_collected_and_every_other_page_still_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = typeset_validate.typeset_case
    seen: list[tuple[str, bool]] = []

    def refusing(entry: dict, layout: dict, **kwargs: object):  # noqa: ANN202
        seen.append((entry["case_id"], bool(kwargs["distractors"])))
        if len(seen) == 1:
            raise TypstError("Typst warned on a page.\n  What: w\n  Where: x\n  Expected: y\n  Recover: z")
        return real(entry, layout, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(typeset_validate, "typeset_case", refusing)
    cases = _cases(2)
    errors = _run(cases)
    assert errors == ["Typst warned on a page.\n  What: w\n  Where: x\n  Expected: y\n  Recover: z"]
    assert seen == [(str(case), mode) for case in cases for mode in (False, True)]


def test_stage_one_pages_keeps_each_render_and_hands_it_on() -> None:
    pages: dict[tuple[str, bool], dict] = {}
    handed: list[tuple[str, str, dict]] = []
    keep = StageOnePages(pages, distractors=True, then=lambda *args: handed.append(args))
    geometry: dict = {"ink": []}
    keep("CASE001", "some_layout", geometry)
    assert pages == {("CASE001", True): geometry}
    assert handed == [("CASE001", "some_layout", geometry)]
    StageOnePages(pages, distractors=False, then=None)("CASE001", "some_layout", geometry)
    assert set(pages) == {("CASE001", True), ("CASE001", False)}


def test_a_case_stage_one_did_not_render_is_not_typeset(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("typeset a page stage one never rendered")

    monkeypatch.setattr(typeset_validate, "typeset_case", never)
    layouts = load_layout_registry(LAYOUT_PATH)
    assert (
        typeset_errors(
            _cases(1),
            layouts,
            {},
            doc_type=load_extraction_schema().resolve_doc_type(KEY),
            layout_path=LAYOUT_PATH,
            parsing=load_parsing_config(PARSING_CONFIG_PATH),
            distractor_config=load_distractor_config(),
            config=load_typeset_config(TYPESET_CONFIG_PATH),
        )
        == []
    )
