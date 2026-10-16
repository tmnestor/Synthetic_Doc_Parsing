"""The scan a snapshot passes before it is published: whole-word, case-insensitive, names and texts.

A PDF is read for its document information and its text layer (Review Focus 5); its pages are compiled
by hand with invented text, none of it in ground_truth/.
"""

from pathlib import Path

import pytest

from generators.snapshot.publication import LISTED, find_forbidden, refusal
from generators.typst.compile import compile_page
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from generators.typst.metadata import publish_metadata
from tests.conftest import assert_diagnostic_error

TYPESET = load_typeset_config(TYPESET_CONFIG_PATH)
# Invented: a term an operator might forbid, and a layout id in the form the registries use.
PLANTED_TERM = "invented-lib"
PLANTED_LAYOUT = "invented_layout_alpha"
BODY = "Brackenridge Ferry Timetable"


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


def _pdf(workdir: Path, *, document: str = "", body: str = BODY, publish: bool = True) -> bytes:
    """Compile a one-page PDF whose `#set document(...)` arguments are `document`, holding `body`."""
    source = "\n".join(
        [
            f"#set document({document})" if document else "",
            '#set text(font: "Carlito")',
            body,
            "",
        ]
    )
    pdf = compile_page(source, workdir=workdir, config=TYPESET).pdf
    return publish_metadata(pdf, producer="", where="a test page") if publish else pdf


@pytest.mark.parametrize(
    ("argument", "field"),
    [("title", "title"), ("author", "author"), ("keywords", "keywords"), ("description", "subject")],
)
@pytest.mark.parametrize("term", [PLANTED_TERM, PLANTED_LAYOUT])
def test_a_term_in_a_pdfs_document_information_is_found(
    root: Path, tmp_path: Path, argument: str, field: str, term: str
) -> None:
    (root / "page.pdf").write_bytes(_pdf(tmp_path / "work", document=f'{argument}: "made by {term}"'))
    hits = find_forbidden(root, (term,))
    assert f"page.pdf: metadata {field} contains {term!r}" in hits
    assert all(hit.startswith("page.pdf: metadata ") for hit in hits)


@pytest.mark.parametrize("term", [PLANTED_TERM, PLANTED_LAYOUT])
def test_a_term_in_a_pdfs_text_layer_is_found(root: Path, tmp_path: Path, term: str) -> None:
    (root / "page.pdf").write_bytes(_pdf(tmp_path / "work", body=f"{BODY} {term}".replace("_", "\\_")))
    assert find_forbidden(root, (term,)) == [f"page.pdf: page text contains {term!r}"]


def test_a_pdf_as_typst_writes_it_names_typst_and_a_published_one_does_not(
    root: Path, tmp_path: Path
) -> None:
    (root / "page.pdf").write_bytes(_pdf(tmp_path / "raw", publish=False))
    assert find_forbidden(root, ("Typst",)) == [
        "page.pdf: metadata creator contains 'Typst'",
        "page.pdf: metadata xmp contains 'Typst'",
    ]
    (root / "page.pdf").write_bytes(_pdf(tmp_path / "published"))
    assert find_forbidden(root, ("Typst", "xmp-writer")) == []


def test_a_clean_pdf_finds_nothing_but_its_name_is_scanned(root: Path, tmp_path: Path) -> None:
    (root / "invented_lib_page.pdf").write_bytes(_pdf(tmp_path / "work"))
    assert find_forbidden(root, ("invented_lib", "Ferry Schedule")) == [
        "invented_lib_page.pdf: name contains 'invented_lib'"
    ]


def test_a_pdf_that_cannot_be_read_is_reported(root: Path) -> None:
    (root / "page.pdf").write_bytes(b"%PDF-1.7 not really")
    assert find_forbidden(root, ("anything",)) == ["page.pdf: not a readable PDF, so it cannot be scanned"]
