"""The formula-free launcher's own steps, tested without the evaluator: it runs in the evaluator's env.

What it does to a page is tested on a stand-in for the evaluator's `md_tex_filter`; that the real one
then scores a perfect page perfectly is the slow test in `test_perfect_score.py`. Every line of text
here is invented for the test.
"""

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.omnidocbench_formula_free import (
    SHORTENER,
    formula_free,
    keep_repeated_characters,
    refuse_ground_truth_with_formulas,
    without_formula_items,
)
from tests.conftest import assert_diagnostic_error


def _dataset(**categories: list) -> defaultdict:
    dataset: defaultdict = defaultdict(list)
    dataset.update(categories)
    return dataset


def test_formula_items_are_dropped_and_text_and_tables_are_kept() -> None:
    text = {"category_type": "text_all", "content": "Paid $4.00", "fine_category_type": "text_block"}
    caption = {"category_type": "text_all", "content": "4.00", "fine_category_type": "equation_caption"}
    table = {"category_type": "html_table", "content": "<table><tr><td>$4.00</td></tr></table>"}
    dataset = _dataset(
        text_all=[text, caption],
        html_table=[table],
        equation_isolated=[{"category_type": "equation_isolated", "content": "\\[4.00 | \\]"}],
        formula_rescue=[{"category_type": "formula_rescue", "content": "\\[ 02/03/2031 \\]"}],
    )
    assert without_formula_items(dataset) == {"text_all": [text], "html_table": [table]}


def test_a_category_the_launcher_does_not_know_is_refused() -> None:
    with pytest.raises(RuntimeError) as caught:
        without_formula_items(_dataset(text_all=[], equation_semantic=[]))
    assert_diagnostic_error(caught.value)
    assert "equation_semantic" in str(caught.value)


def test_the_page_reaches_the_evaluator_with_its_pipe_tables_as_html_and_leaves_without_formulas() -> None:
    seen = []

    def md_tex_filter(content: str) -> defaultdict:
        seen.append(content)
        return _dataset(
            text_all=[{"category_type": "text_all", "content": "Paid", "fine_category_type": "text_block"}],
            equation_isolated=[{"category_type": "equation_isolated", "content": "\\[x\\]"}],
        )

    filtered = formula_free(md_tex_filter)
    result = filtered("Paid\n\n| Part | Cost |\n|---|---|\n| Ref: Z9 | $7.10 |\n")
    assert seen == [
        "Paid\n\n<table><tr><td>Part</td><td>Cost</td></tr><tr><td>Ref: Z9</td><td>$7.10</td></tr></table>\n"
    ]
    assert list(result) == ["text_all"]


def _config(tmp_path: Path, pages: list) -> Path:
    ground_truth = tmp_path / "pages.json"
    ground_truth.write_text(json.dumps(pages))
    config = tmp_path / "end2end.yaml"
    config.write_text(
        yaml.safe_dump({"end2end_eval": {"dataset": {"ground_truth": {"data_path": str(ground_truth)}}}})
    )
    return config


def _page(*blocks: dict) -> dict:
    return {"page_info": {"image_path": "set/CASE900_invoice.png"}, "layout_dets": list(blocks)}


def test_ground_truth_without_a_formula_is_accepted(tmp_path: Path) -> None:
    block = {"category_type": "text_block", "text": "Paid $4.00", "line_with_spans": []}
    refuse_ground_truth_with_formulas(_config(tmp_path, [_page(block)]))


@pytest.mark.parametrize(
    "block",
    [
        {"category_type": "equation_isolated", "latex": "x"},
        {
            "category_type": "text_block",
            "text": "x",
            "line_with_spans": [{"category_type": "equation_inline"}],
        },
    ],
)
def test_ground_truth_with_a_formula_is_refused(tmp_path: Path, block: dict) -> None:
    with pytest.raises(ValueError) as caught:
        refuse_ground_truth_with_formulas(_config(tmp_path, [_page(block)]))
    assert_diagnostic_error(caught.value)
    assert "CASE900_invoice.png" in str(caught.value)


def test_a_prediction_keeps_its_repeated_characters() -> None:
    extract = SimpleNamespace(**{SHORTENER: lambda content: content.replace("______", "____")})
    keep_repeated_characters(extract)
    assert getattr(extract, SHORTENER)("Sign here: ______") == "Sign here: ______"


def test_an_evaluator_without_the_shortening_function_is_refused() -> None:
    with pytest.raises(RuntimeError) as caught:
        keep_repeated_characters(SimpleNamespace())
    assert_diagnostic_error(caught.value)
    assert SHORTENER in str(caught.value)
