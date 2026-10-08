"""Tests for load_layout_registry's handling of top-level anchor siblings.

The de-duplication scheme puts anchor definitions (`_bank_base`, `_cba`, ...) at
top level as siblings of `layouts:`, so the loader must unwrap `layouts:` even
when it is not the sole top-level key, as long as every other key is
underscore-prefixed. A non-underscore sibling is a genuine authoring error (a
mis-indented layout) and must fail fast.
"""

from pathlib import Path

import pytest

from generators.loader import load_layout_registry
from tests.conftest import REPO_ROOT, assert_diagnostic_error

ANCHORED = """\
_base: &base
  margin: 12
  width: 300

_extra: &extra
  tint: grey

layouts:
  alpha:
    <<: *base
    name: first
  beta:
    <<: [*base, *extra]
    name: second
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "layouts.yml"
    path.write_text(text)
    return path


def test_anchor_siblings_are_not_mistaken_for_layouts(tmp_path: Path) -> None:
    """Underscore-prefixed siblings stay out of the registry, but their merged values reach each layout."""
    registry = load_layout_registry(write(tmp_path, ANCHORED))
    assert sorted(registry) == ["alpha", "beta"]
    assert registry["alpha"] == {"margin": 12, "width": 300, "name": "first"}
    assert registry["beta"]["tint"] == "grey"

    for stem in ("bank_statements", "receipts", "invoices"):
        real = load_layout_registry(REPO_ROOT / "config" / "layouts" / f"{stem}.yml")
        assert real
        assert not [k for k in real if k.startswith("_")], stem


def test_a_non_anchor_top_level_key_fails_fast(tmp_path: Path) -> None:
    """A stray non-underscore sibling of `layouts:` is a mis-indented layout and raises a diagnostic."""
    path = write(tmp_path, ANCHORED + "\nmisindented_layout:\n  name: oops\n")
    with pytest.raises(ValueError) as err:
        load_layout_registry(path)
    assert_diagnostic_error(err.value)
    assert "misindented_layout" in str(err.value)
    assert str(path.resolve()) in str(err.value)
    assert "_misindented_layout" in str(err.value), "the message must offer the anchor spelling"


def test_a_file_with_only_layouts_still_unwraps(tmp_path: Path) -> None:
    """A file whose only top-level key is `layouts:` returns the inner mapping, as before."""
    registry = load_layout_registry(write(tmp_path, "layouts:\n  solo:\n    name: only\n"))
    assert registry == {"solo": {"name": "only"}}
