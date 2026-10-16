"""`validate`'s typeset pass: each case typeset plain and with distractors, every failure collected.

The pass runs over real cases here, one statement (which draws overlays) at a time; each failure is
provoked by handing the pass something wrong (a stage-one page that says something else, a bound no page
meets, a page Typst refuses), never by editing a layout.
"""

import dataclasses
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import generators.pipeline as pipeline
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


def _run(  # noqa: ANN001
    gt_data: dict, *, renderer=None, distractor_config=None, layouts=None, workers: int = 1
) -> list[str]:
    """Run the pass over `gt_data`; one worker (in this process) unless `workers` says otherwise."""
    registry = load_layout_registry(LAYOUT_PATH)
    return typeset_errors(
        gt_data,
        layouts or registry,
        _stage_one(gt_data, registry, renderer or _RENDERERS[KEY]),
        doc_type=load_extraction_schema().resolve_doc_type(KEY),
        layout_path=LAYOUT_PATH,
        parsing=load_parsing_config(PARSING_CONFIG_PATH),
        distractor_config=distractor_config or load_distractor_config(),
        config=dataclasses.replace(load_typeset_config(TYPESET_CONFIG_PATH), validate_workers=workers),
    )


def _saying_quokka(entry: dict, layout: dict, *, distractors: bool, geometry_out: dict) -> object:
    """Stage one's renderer, but its page says something the typeset page does not."""
    image = _RENDERERS[KEY](entry, layout, distractors=distractors, geometry_out=geometry_out)
    ink = geometry_out["ink"]
    index = next(i for i, record in enumerate(ink) if record.role == "scored")
    ink[index] = ink[index]._replace(text="Quokka")
    return image


@pytest.mark.slow
def test_a_statement_typesets_plain_and_with_its_overlays_with_no_failure() -> None:
    assert _run(_cases(1)) == []


@pytest.mark.slow
def test_a_page_that_differs_from_stage_one_is_refused_four_part() -> None:
    errors = _run(_cases(1), renderer=_saying_quokka)
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


# --- Ruling P12: the pass in a process pool -------------------------------------------------------------


@pytest.mark.slow
def test_the_pooled_pass_reports_the_same_failures_in_the_same_order_as_one_worker() -> None:
    cases = _cases(3)
    pooled = _run(cases, renderer=_saying_quokka, workers=3)
    serial = _run(cases, renderer=_saying_quokka, workers=1)
    assert len(serial) == 6  # each case, plain and with distractors
    assert pooled == serial
    # Case order, and within a case plain before distractor.
    named = [error.split("What:", 1)[1].split(":", 1)[0].strip() for error in serial]
    assert named == [
        f"{case} / {cases[case]['layout']} (typeset {mode})"
        for case in cases
        for mode in ("plain", "distractor")
    ]


@pytest.mark.slow
@pytest.mark.parametrize("workers", [1, 2])
def test_a_page_whose_check_raises_in_a_worker_is_reported_four_part_naming_its_case(workers: int) -> None:
    entries = load_ground_truth(REPO_ROOT / "ground_truth" / f"{KEY}.yml")
    by_layout: dict[str, str] = {}
    for case in sorted(entries, key=str):
        by_layout.setdefault(entries[case]["layout"], case)
    (broken_layout, broken_case), (_, sound_case) = list(by_layout.items())[:2]
    cases = {broken_case: entries[broken_case], sound_case: entries[sound_case]}
    registry = load_layout_registry(LAYOUT_PATH)
    # An invented fault: one layout loses its body after stage one has rendered its case.
    layouts = {**registry, broken_layout: {k: v for k, v in registry[broken_layout].items() if k != "body"}}
    errors = _run(cases, layouts=layouts, workers=workers)
    # Its two pages (plain and with distractors) are each reported, naming the case; the other case's
    # pages are still typeset and checked, and pass.
    assert len(errors) == 2
    for error, mode in zip(errors, ("plain", "distractor"), strict=True):
        assert_diagnostic_error(error)
        assert "could not be checked" in error and "KeyError" in error
        assert f"{broken_case} / {broken_layout} (typeset {mode})" in error


@pytest.mark.slow
def test_validate_runs_the_real_typeset_pass_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # The one test that runs the whole `validate` with its typeset pass (Ruling P12): every case of every
    # document type typeset, plain and with distractors, and checked. The spy only counts the pages the
    # real pass was handed; it changes nothing.
    real = pipeline.typeset_errors
    handed: dict[str, int] = {}

    def counting(gt_data: dict, layouts: dict, stage_one: dict, **kwargs: object) -> list[str]:
        handed[str(kwargs["doc_type"])] = len(stage_one)
        return real(gt_data, layouts, stage_one, **kwargs)  # type: ignore[arg-type]

    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(pipeline, "typeset_errors", counting)
    result = CliRunner().invoke(pipeline.app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output
    # Each type's 55 cases, plain and with distractors.
    assert handed == {"bank_statement": 110, "invoice": 110, "receipt": 110}


@pytest.mark.slow
def test_validate_with_a_scratch_layouts_file_typesets_under_the_type_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The typeset pass draws each overlay under the document type `validate` already holds, not one looked
    # up by layouts file in the repository's own config/generation_config.yml: a config naming a scratch copy
    # of the statements' layouts (which draw overlays) at another path typesets with no false failure.
    generation = yaml.safe_load((REPO_ROOT / "config" / "generation_config.yml").read_text())
    layouts = tmp_path / "scratch_layouts.yml"
    layouts.write_text((REPO_ROOT / generation["document_types"][KEY]["layouts"]).read_text())
    generation["document_types"] = {KEY: {**generation["document_types"][KEY], "layouts": str(layouts)}}
    config = tmp_path / "generation_config.yml"
    config.write_text(yaml.safe_dump(generation, sort_keys=False))
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(pipeline.app, ["validate", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output
