"""The `parse_category`, `parse_order` and `parse_block` layout keys, and the vocabulary check."""

import copy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from generators.parsing.config import layout_parse_errors, load_parsing_config
from generators.pipeline import app
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.layout_dsl.test_schema import banner, check_body, pair, reject_body, table, text
from tests.renderer_helpers import layouts_for, write_single_type_config

pytestmark = pytest.mark.usefixtures("synthetic_layouts_skip_label_sites")

CONTAINERS = ("panel", "split", "stack")
CONFIG_PATH = REPO_ROOT / "config" / "parsing_config.yml"


def container(kind: str, **extra) -> dict:
    """Return a container block of `kind` holding one text block."""
    children = [[text()], [text()]] if kind == "split" else [text()]
    return {"type": kind, "children": children, **extra}


@pytest.mark.parametrize("block", [text(), pair(), table(), banner()])
def test_every_block_accepts_parse_category_and_parse_order(block: dict) -> None:
    check_body([{**block, "parse_category": "title", "parse_order": 3}])


@pytest.mark.parametrize("kind", CONTAINERS)
def test_a_container_accepts_all_three_parse_keys(kind: str) -> None:
    check_body([container(kind, parse_category="text_block", parse_order=0, parse_block=True)])
    check_body([container(kind, parse_block=False)])


@pytest.mark.parametrize("block", [text(), pair(), table(), banner()])
def test_parse_block_is_refused_on_a_non_container_naming_the_three_containers(block: dict) -> None:
    err = reject_body([{**block, "parse_block": True}], "parse_block", "panel", "split", "stack")
    assert "[0].parse_block" in str(err)


@pytest.mark.parametrize(
    ("body", "table_path"),
    [
        ([{"type": "stack", "parse_block": True, "children": [text(), table()]}], "body[0].children[1]"),
        ([{"type": "panel", "parse_block": True, "children": [table()]}], "body[0].children[0]"),
        (
            [{"type": "split", "parse_block": True, "children": [[text()], [text(), table()]]}],
            "body[0].children[1][1]",
        ),
        (
            # Deep beneath the parsed block, inside a plain container.
            [
                {
                    "type": "stack",
                    "parse_block": True,
                    "children": [{"type": "panel", "children": [table()]}],
                }
            ],
            "body[0].children[0].children[0]",
        ),
    ],
)
def test_a_table_beneath_a_parse_block_container_is_refused(body: list, table_path: str) -> None:
    """A table is a block of its own, with its own grid; it cannot fold into an enclosing parsed block."""
    err = reject_body(body, "table", "parse_block", f"layout_under_test.{table_path}", "Recover")
    assert_diagnostic_error(err)
    assert str(REPO_ROOT / "config" / "layouts" / "receipts.yml") in str(err)


def test_a_table_beside_a_parse_block_container_is_accepted() -> None:
    """Only tables beneath the parsed block are refused; one beside it or in a plain container is fine."""
    check_body([{"type": "stack", "parse_block": True, "children": [text()]}, table()])
    check_body([{"type": "stack", "parse_block": False, "children": [table()]}])


@pytest.mark.parametrize("bad", ["", 3, None, ["title"], True])
def test_parse_category_must_be_a_non_empty_string(bad: object) -> None:
    err = reject_body([text(parse_category=bad)], "parse_category")
    assert "[0].parse_category" in str(err)


@pytest.mark.parametrize("bad", ["1", 1.5, True, False, None, [1]])
def test_parse_order_must_be_an_integer_not_a_bool(bad: object) -> None:
    err = reject_body([text(parse_order=bad)], "parse_order")
    assert "[0].parse_order" in str(err)


@pytest.mark.parametrize("bad", ["yes", 1, 0, None])
def test_parse_block_must_be_a_bool(bad: object) -> None:
    err = reject_body([container("panel", parse_block=bad)], "parse_block")
    assert "[0].parse_block" in str(err)


def test_nested_children_are_checked_too() -> None:
    reject_body([container("panel", children=[text(parse_order="x")])], "parse_order")


def test_validate_reports_an_unknown_parse_category_in_a_scratch_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout_id = next(iter(layouts_for("bank_statement")))
    layout = copy.deepcopy(layouts_for("bank_statement")[layout_id])
    layout["body"].append(text(parse_category="paragraph"))
    config = write_single_type_config(tmp_path, "bank_statements", {layout_id: layout})
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["validate", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert "paragraph" in result.output
    assert "parse_category" in result.output
    assert "category_vocabulary" in result.output


def test_a_valid_category_passes_the_vocabulary_check() -> None:
    config = load_parsing_config(CONFIG_PATH)
    layouts = {"alpha": {"body": [text(parse_category="title"), container("panel")]}}
    assert layout_parse_errors(layouts, layout_path="layouts.yml", config=config) == []


def test_a_category_outside_the_vocabulary_is_reported_four_part() -> None:
    config = load_parsing_config(CONFIG_PATH)
    inner = text(parse_category="paragraph")
    layouts = {"alpha": {"body": [container("panel", children=[inner])]}}
    errors = layout_parse_errors(layouts, layout_path="layouts.yml", config=config)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert "paragraph" in errors[0]
    assert "alpha.body[0].children[0].parse_category" in errors[0]
    assert "layouts.yml" in errors[0]
    assert "title" in errors[0], "Expected should list the vocabulary"


# --- parse_join ------------------------------------------------------------------------------------------


def test_a_table_accepts_parse_join_previous_table() -> None:
    """A table may join its rows to the table drawn before it."""
    check_body([table(), table(parse_join="previous_table")])


@pytest.mark.parametrize("block", [text(), pair(), banner(), container("stack")])
def test_parse_join_is_refused_on_a_block_that_is_not_a_table(block: dict) -> None:
    """Only a table has rows to join; the refusal names the one primitive that accepts the key."""
    err = reject_body(
        [table(), {**block, "parse_join": "previous_table"}], "parse_join", "table", "Recover"
    )
    assert "[1].parse_join" in str(err)


@pytest.mark.parametrize("bad", ["next_table", "previous", "", 1, True, None, ["previous_table"]])
def test_parse_join_takes_only_previous_table(bad: object) -> None:
    """The key is an enum of one value; anything else is refused, naming the value allowed."""
    err = reject_body([table(), table(parse_join=bad)], "parse_join", "previous_table", "Recover")
    assert "[1].parse_join" in str(err)


def test_a_join_after_a_table_passes_the_layout_check() -> None:
    """A table joined to one drawn earlier, even one inside an earlier split column, is accepted."""
    config = load_parsing_config(CONFIG_PATH)
    split = {"type": "split", "children": [[text()], [table()]]}
    layouts = {"alpha": {"body": [split, text(), table(parse_join="previous_table")]}}
    assert layout_parse_errors(layouts, layout_path="layouts.yml", config=config) == []


@pytest.mark.parametrize(
    ("body", "join_path"),
    [
        ([text(), table(parse_join="previous_table")], "alpha.body[1].parse_join"),
        # The only other table is drawn after the join, so it cannot take the joined rows.
        ([table(parse_join="previous_table"), table()], "alpha.body[0].parse_join"),
        (
            [{"type": "stack", "children": [table(parse_join="previous_table")]}],
            "alpha.body[0].children[0].parse_join",
        ),
    ],
)
def test_a_join_with_no_table_drawn_before_it_is_reported_four_part(body: list, join_path: str) -> None:
    """A join needs a table earlier in draw order to append its rows to."""
    config = load_parsing_config(CONFIG_PATH)
    errors = layout_parse_errors({"alpha": {"body": body}}, layout_path="layouts.yml", config=config)
    assert len(errors) == 1
    assert_diagnostic_error(errors[0])
    assert join_path in errors[0]
    assert "layouts.yml" in errors[0]
    assert "previous_table" in errors[0]
