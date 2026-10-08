"""The diagnostic helper must fail when any element is missing, or it proves nothing."""

from pathlib import Path

import pytest

from tests.conftest import assert_diagnostic_error

COMPLETE = "What: x is missing\nWhere: a.yml -> k\nExpected: k: 1\nRecover: add k"


def test_complete_labelled_message_passes() -> None:
    assert_diagnostic_error(COMPLETE)
    assert_diagnostic_error(ValueError(COMPLETE))


@pytest.mark.parametrize("label", ["What:", "Where:", "Expected:", "Recover:"])
def test_each_missing_label_fails(label: str) -> None:
    with pytest.raises(AssertionError):
        assert_diagnostic_error(COMPLETE.replace(label, "Nope:"))


def test_config_style_requires_path_token_example_and_remediation(tmp_path: Path) -> None:
    cfg = tmp_path / "export_config.yml"
    good = f"Missing key 'mode' in {cfg.resolve()}. Example: mode: strict. Remediation: add it."
    assert_diagnostic_error(ValueError(good), cfg, "mode")
    with pytest.raises(AssertionError):
        assert_diagnostic_error(ValueError(good.replace(str(cfg.resolve()), "somewhere")), cfg, "mode")
    with pytest.raises(AssertionError):
        assert_diagnostic_error(ValueError(good.replace("Remediation:", "Fix:")), cfg, "mode")


def test_path_without_token_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="both `path` and `token`"):
        assert_diagnostic_error(COMPLETE, tmp_path, None)


def test_a_label_must_start_a_line_not_merely_appear() -> None:
    """'somewhere:' contains 'where:' but must not satisfy WHERE."""
    message = "What: x is missing\nsomewhere: a.yml -> k\nExpected: k: 1\nRecover: add k"
    with pytest.raises(AssertionError, match="missing WHERE"):
        assert_diagnostic_error(message)


def test_an_indented_label_still_counts() -> None:
    assert_diagnostic_error("  What: x\n\tWhere: y\n    Expected: z\n  Recover: w")


def test_config_style_fails_when_the_token_is_absent(tmp_path: Path) -> None:
    cfg = tmp_path / "export_config.yml"
    message = f"Missing key in {cfg.resolve()}. Example: mode: strict. Remediation: add it."
    with pytest.raises(AssertionError, match="missing WHAT"):
        assert_diagnostic_error(ValueError(message), cfg, "threshold")


def test_config_style_fails_when_the_example_is_removed(tmp_path: Path) -> None:
    cfg = tmp_path / "export_config.yml"
    message = f"Missing key 'mode' in {cfg.resolve()}. Remediation: add it."
    with pytest.raises(AssertionError, match="missing EXPECTED"):
        assert_diagnostic_error(ValueError(message), cfg, "mode")
