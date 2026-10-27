"""The corrected launcher fixes exactly the two faulty conditions of the pinned table matcher, or refuses.

The launcher itself runs inside the evaluator's env, so here its source fix is tested on its own, and
against the pinned checkout's real `match_gt2pred_simple` when the checkout is on this machine.
"""

import ast

import pytest

from generators.snapshot.config import load_snapshot_config
from scripts.omnidocbench_corrected import BROKEN, FIXED, FUNCTION, fix_source
from tests.conftest import REPO_ROOT, assert_diagnostic_error

MATCHER = "src/core/matching/match.py"


def test_both_conditions_are_replaced_and_a_first_match_keeps_its_place() -> None:
    source = (
        "def matched(pred_idx):\n"
        f"    position = pred_idx * 10 {BROKEN}\n"
        f'    category = "table" {BROKEN}\n'
        "    return position, category\n"
    )
    before: dict = {}
    exec(compile(source, "faulty", "exec"), before)  # noqa: S102
    assert before["matched"](0) == ("", "")  # the fault: index 0 reads as unmatched
    fixed = fix_source(source, where="faulty.py")
    assert fixed.count(FIXED) == 2 and BROKEN not in fixed
    after: dict = {}
    exec(compile(fixed, "fixed", "exec"), after)  # noqa: S102
    assert after["matched"](0) == (0, "table")
    assert after["matched"](3) == (30, "table")
    assert after["matched"]("") == ("", "")  # the unmatched marker still reads as unmatched


@pytest.mark.parametrize("count", [0, 1, 3])
def test_other_code_is_refused(count: int) -> None:
    source = (
        "def f(pred_idx):\n" + "".join(f"    x{i} = 1 {BROKEN}\n" for i in range(count)) + "    return 0\n"
    )
    with pytest.raises(ValueError) as caught:
        fix_source(source, where="other.py")
    assert_diagnostic_error(caught.value)
    assert f"{count} occurrences" in str(caught.value)


def test_the_pinned_checkout_holds_exactly_the_two_conditions() -> None:
    config = REPO_ROOT / "config" / "generation_config.yml"
    checkout = load_snapshot_config(config).output_root / "tools" / "OmniDocBench"
    path = checkout / MATCHER
    if not path.is_file():
        pytest.skip(f"no evaluator checkout at {checkout}")
    text = path.read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.FunctionDef) and node.name == FUNCTION
    )
    source = ast.get_source_segment(text, function)
    assert source is not None
    fixed = fix_source(source, where=str(path))
    compile(fixed, str(path), "exec")
    assert fixed.count(FIXED) == 2
