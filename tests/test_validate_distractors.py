"""`pipeline validate` draws every entry's distractor values with every eligible item and checks them."""

import pytest
from typer.testing import CliRunner

from generators import pipeline
from generators.distractors import providers
from generators.distractors.values import RULES, RuleContext
from generators.pipeline import app
from tests.conftest import REPO_ROOT


@pytest.fixture
def at_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def no_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the render-everything overflow backstop, which these tests do not exercise."""
    monkeypatch.setattr(pipeline, "check_overflow", lambda *args, **kwargs: [])


def test_validate_passes_on_the_shipped_corpus(at_root: None) -> None:
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "Validation passed" in result.output


def test_validate_draws_every_entry_with_every_eligible_item(
    at_root: None, no_overflow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, int]] = []
    original = providers.draw_all_eligible

    def spy(entry: dict, doc_type: str) -> dict[str, str]:
        out = original(entry, doc_type)
        seen.append((doc_type, entry["case_id"], len(out)))
        return out

    monkeypatch.setattr(pipeline, "draw_all_eligible", spy)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert len(seen) == 165
    assert {doc_type for doc_type, _, _ in seen} == {"receipt", "invoice", "bank_statement"}
    assert all(count > 0 for _, _, count in seen)


def test_validate_fails_when_a_rule_collides(
    at_root: None, no_overflow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule that always reproduces a scored value fails every redraw; validate reports it."""

    def colliding(ctx: RuleContext, rule: dict) -> str:
        return f"Ref {ctx.fields.get('TOTAL_AMOUNT', ctx.fields.get('STATEMENT_DATE_RANGE'))}"

    monkeypatch.setitem(RULES, "digits", colliding)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 1, result.output
    assert "collides with the scored field" in result.output
    assert "CASE001" in result.output


def test_validate_checks_the_values_independently_of_the_redraw_screen(
    at_root: None, no_overflow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the providers' own screen blinded, the validator in `validate` still catches the value."""

    def colliding(ctx: RuleContext, rule: dict) -> str:
        return f"Ref {ctx.fields.get('TOTAL_AMOUNT', ctx.fields.get('STATEMENT_DATE_RANGE'))}"

    monkeypatch.setitem(RULES, "digits", colliding)
    monkeypatch.setattr(providers, "collision", lambda *args: None)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 1, result.output
    output = " ".join(result.output.split())
    assert "Distractor value check failed" in output
    assert "contains the value of the scored field TOTAL_AMOUNT" in output
