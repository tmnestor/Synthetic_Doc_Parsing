"""Every live inventory module exists, or is explicitly dropped with a reason."""

import ast
import re
import sys

import pytest

from tests.conftest import REPO_ROOT

INVENTORY = REPO_ROOT / "tests" / "RECOVERED_INVENTORY.md"
HEADER = re.compile(r"^## `(tests/[^`]+\.py)`(.*)$")
TICKED = re.compile(r"^- \[x\] `(test_[A-Za-z0-9_]+)`")


def _sections() -> list[tuple[str, str, list[str]]]:
    """Return (path, status, lines) per `## ` block after the preamble, asserting each has a header."""
    blocks = INVENTORY.read_text().split("\n## ")[1:]
    sections = []
    for block in blocks:
        header, *lines = ("## " + block).splitlines()
        match = HEADER.match(header)
        assert match, f"inventory section header not recognised: {header!r}"
        sections.append((match.group(1), match.group(2), lines))
    assert sections, "inventory has no sections"
    return sections


def _is_live(status: str) -> bool:
    return "OBSOLETE" not in status and "DROPPED" not in status


def test_every_live_inventory_module_exists() -> None:
    """Each inventory header not marked OBSOLETE or DROPPED names a test file that exists."""
    headers = re.findall(r"^## `(tests/[^`]+\.py)`(.*)$", INVENTORY.read_text(), re.MULTILINE)
    assert len(headers) == len(_sections()), "a `## ` section has an unparseable header"
    missing = [path for path, status in headers if _is_live(status) and not (REPO_ROOT / path).exists()]
    assert not missing, f"inventory modules with no test file: {missing}"


def test_no_live_inventory_line_is_left_unticked() -> None:
    """No live inventory section keeps an unticked test line."""
    unticked = []
    for path, status, lines in _sections():
        if _is_live(status):
            unticked += [f"{path}: {line}" for line in lines if line.startswith("- [ ] `")]
    assert not unticked, f"{len(unticked)} live lines unticked, first: {unticked[:3]}"


def test_every_ticked_test_exists_in_its_module() -> None:
    """Each ticked (not struck-through) name is a `def test_...` in the section's module."""
    absent = []
    for path, status, lines in _sections():
        if not _is_live(status) or not (REPO_ROOT / path).exists():
            continue
        defined = {
            node.name
            for node in ast.walk(ast.parse((REPO_ROOT / path).read_text()))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for line in lines:
            match = TICKED.match(line)
            if match and match.group(1) not in defined:
                absent.append(f"{path}: {match.group(1)}")
    assert not absent, f"ticked tests with no definition: {absent}"


def test_the_integrity_check_can_fail(tmp_path, monkeypatch) -> None:
    """Each check rejects a fabricated inventory, so none can pass vacuously."""
    module = sys.modules[__name__]
    fake = tmp_path / "RECOVERED_INVENTORY.md"
    monkeypatch.setattr(module, "INVENTORY", fake)

    fake.write_text("# x\n\n## `tests/test_nowhere.py`\n\n- [ ] `test_a`\n")
    with pytest.raises(AssertionError):
        test_every_live_inventory_module_exists()
    with pytest.raises(AssertionError):
        test_no_live_inventory_line_is_left_unticked()

    fake.write_text("# x\n\n## tests/malformed header\n\n- [ ] `test_a`\n")
    with pytest.raises(AssertionError, match="not recognised"):
        test_every_live_inventory_module_exists()

    fake.write_text("# x\n\nno sections at all\n")
    with pytest.raises(AssertionError, match="no sections"):
        test_no_live_inventory_line_is_left_unticked()

    fake.write_text("# x\n\n## `tests/test_conftest.py`\n\n- [x] `test_not_defined_anywhere`\n")
    with pytest.raises(AssertionError, match="test_not_defined_anywhere"):
        test_every_ticked_test_exists_in_its_module()

    fake.write_text(
        "# x\n\n## `tests/test_conftest.py`\n\n- [x] ~~test_not_defined_anywhere~~ dropped: x\n"
    )
    test_every_ticked_test_exists_in_its_module()
