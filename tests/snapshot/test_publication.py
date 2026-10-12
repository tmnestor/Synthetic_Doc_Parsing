"""The scan a snapshot passes before it is published: whole-word, case-insensitive, names and texts."""

from pathlib import Path

import pytest

from generators.snapshot.publication import LISTED, find_forbidden, refusal
from tests.conftest import assert_diagnostic_error


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "snapshot_20310309"
    (base / "tools").mkdir(parents=True)
    return base


def test_a_term_is_found_in_a_text_file_with_its_line(root: Path) -> None:
    (root / "README.md").write_text("first line\nmade with Invented-Lib 1.0\n")
    assert find_forbidden(root, ("invented-lib",)) == ["README.md:2: contains 'invented-lib'"]


def test_a_term_is_matched_as_a_whole_word_only(root: Path) -> None:
    """`PROD` must not match `product`, nor `git` match `digit`."""
    (root / "README.md").write_text("every product line\nten digits\n")
    assert find_forbidden(root, ("PROD", "git")) == []
    (root / "README.md").write_text("sent to prod\nkept in git.\n")
    assert find_forbidden(root, ("PROD", "git")) == [
        "README.md:1: contains 'PROD'",
        "README.md:2: contains 'git'",
    ]


def test_a_term_ending_in_punctuation_matches_up_to_it(root: Path) -> None:
    (root / "tools" / "note.py").write_text("# read from config/invented.yml\n# configuration\n")
    assert find_forbidden(root, ("config/",)) == ["tools/note.py:1: contains 'config/'"]


def test_a_file_name_is_scanned(root: Path) -> None:
    (root / "tools" / "invented_lib_helper.py").write_text("")
    assert find_forbidden(root, ("invented_lib",)) == [
        "tools/invented_lib_helper.py: name contains 'invented_lib'"
    ]


def test_an_image_is_not_read_but_its_name_is(root: Path) -> None:
    (root / "invented_lib.png").write_bytes(b"\x89PNG invented_lib \xff\xfe")
    assert find_forbidden(root, ("invented_lib",)) == ["invented_lib.png: name contains 'invented_lib'"]


def test_a_file_that_is_not_text_is_reported(root: Path) -> None:
    (root / "data.bin").write_bytes(b"\xff\xfe\x00invalid")
    assert find_forbidden(root, ("anything",)) == ["data.bin: not UTF-8 text, so it cannot be scanned"]


def test_no_terms_finds_nothing(root: Path) -> None:
    (root / "data.bin").write_bytes(b"\xff\xfe")
    assert find_forbidden(root, ()) == []


def test_the_refusal_is_four_part_and_caps_the_listing(root: Path, tmp_path: Path) -> None:
    hits = [f"README.md:{n}: contains 'x'" for n in range(1, LISTED + 6)]
    message = refusal(hits, root=root, config_path=tmp_path / "generation_config.yml")
    assert_diagnostic_error(ValueError(message))
    assert f"{len(hits)} forbidden term(s)" in message
    assert f"README.md:{LISTED}:" in message and f"README.md:{LISTED + 1}:" not in message
    assert f"and 5 more ({len(hits)} in all)" in message
    assert "eval_set.forbidden_terms" in message
