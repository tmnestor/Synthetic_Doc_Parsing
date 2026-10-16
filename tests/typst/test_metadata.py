"""A published PDF's document information: the Producer set, every field naming the tool blanked.

Pages are compiled by hand against the shared template, with invented text (none of it appears in
ground_truth/).
"""

import re
import subprocess
import sys
from pathlib import Path

import pypdfium2
import pytest

from generators.typst.compile import compile_page, rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from generators.typst.metadata import PdfMetadataError, publish_metadata
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.typst.test_compile import MINIMAL

CONFIG = load_typeset_config(TYPESET_CONFIG_PATH)
# An invented Producer value, for the case the owner had chosen to name one.
INVENTED_PRODUCER = "Brackenridge (Ferry) Press \\ 2"

# Compile MINIMAL and rewrite its metadata in a fresh process, writing the PDF to argv[1].
_PUBLISH_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "from generators.typst.compile import compile_page\n"
    "from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config\n"
    "from generators.typst.metadata import publish_metadata\n"
    "from tests.typst.test_compile import MINIMAL\n"
    "out = Path(sys.argv[1])\n"
    "page = compile_page(MINIMAL, workdir=out.parent, config=load_typeset_config(TYPESET_CONFIG_PATH))\n"
    "out.write_bytes(publish_metadata(page.pdf, producer='', where='a test page'))\n"
)


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """MINIMAL compiled as Typst writes it."""
    return compile_page(MINIMAL, workdir=tmp_path_factory.mktemp("metadata"), config=CONFIG).pdf


def _metadata(pdf: bytes) -> dict[str, str]:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return document.get_metadata_dict()
    finally:
        document.close()


def _text(pdf: bytes) -> str:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return document[0].get_textpage().get_text_range()
    finally:
        document.close()


def _xmp(pdf: bytes) -> str:
    match = re.search(rb"<\?xpacket begin.*?<\?xpacket end[^>]*>", pdf, re.S)
    assert match is not None
    return match.group(0).decode()


def test_typst_names_itself_in_the_creator_and_the_xmp(compiled: bytes) -> None:
    """What the rewrite exists to remove; if Typst stops writing these, the rewrite still holds."""
    assert _metadata(compiled)["Creator"].startswith("Typst")
    assert "Typst" in _xmp(compiled)


@pytest.mark.parametrize("producer", ["", INVENTED_PRODUCER])
def test_the_producer_is_set_and_every_field_naming_the_tool_is_blank(
    compiled: bytes, producer: str
) -> None:
    published = publish_metadata(compiled, producer=producer, where="a test page")
    metadata = _metadata(published)
    assert metadata["Producer"] == producer
    assert metadata["Creator"] == ""
    assert b"typst" not in published.lower()
    xmp = _xmp(published)
    assert "<xmp:CreatorTool></xmp:CreatorTool>" in xmp and 'x:xmptk=""' in xmp
    escaped = producer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    assert f"<pdf:Producer>{escaped}</pdf:Producer>" in xmp
    # The creation date is the configured one, untouched.
    assert metadata["CreationDate"] == _metadata(compiled)["CreationDate"]


def test_every_cross_reference_points_at_its_object(compiled: bytes) -> None:
    published = publish_metadata(compiled, producer="", where="a test page")
    pointer = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", published)
    assert pointer is not None
    start = int(pointer.group(1))
    table = re.match(rb"xref\r?\n0 (\d+)\r?\n", published[start:])
    assert table is not None
    entries = published[start + table.end() :]
    for number in range(int(table.group(1))):
        offset, _, kind = entries[number * 20 : number * 20 + 18].split(b" ")
        if kind == b"n":
            assert published[int(offset) :].startswith(f"{number} 0 obj".encode()), number


def test_the_page_and_its_text_layer_are_unchanged(compiled: bytes) -> None:
    published = publish_metadata(compiled, producer="", where="a test page")
    assert _text(published) == _text(compiled)
    assert rasterise(published, dpi=72).tobytes() == rasterise(compiled, dpi=72).tobytes()


def test_rewriting_is_deterministic_and_idempotent(compiled: bytes) -> None:
    once = publish_metadata(compiled, producer="", where="a test page")
    assert publish_metadata(compiled, producer="", where="a test page") == once
    assert publish_metadata(once, producer="", where="a test page") == once


def test_a_page_compiled_and_rewritten_in_two_processes_is_byte_identical(tmp_path: Path) -> None:
    pdfs = []
    for name in ("one", "two"):
        out = tmp_path / name / "page.pdf"
        out.parent.mkdir()
        done = subprocess.run(
            [sys.executable, "-c", _PUBLISH_SCRIPT, str(out)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        pdfs.append(out.read_bytes())
    assert pdfs[0] == pdfs[1]
    assert _metadata(pdfs[0])["Producer"] == "" and _metadata(pdfs[0])["Creator"] == ""


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda pdf: pdf[: pdf.rfind(b"startxref")], "startxref"),
        (lambda pdf: pdf.replace(b"/Info ", b"/Inf0 "), "/Info"),
        (lambda pdf: pdf.replace(b"trailer\n<<", b"trailer\n<</Prev 9"), "/Prev"),
        (lambda pdf: pdf.replace(b"/Subtype/XML", b"/Filter/None"), "/Filter"),
    ],
)
def test_a_pdf_of_an_unexpected_structure_is_refused(compiled: bytes, mutate, named: str) -> None:
    with pytest.raises(PdfMetadataError) as err:
        publish_metadata(mutate(compiled), producer="", where="CASE900 / invented_layout (typeset plain)")
    assert_diagnostic_error(err.value)
    assert named in str(err.value) and "CASE900 / invented_layout" in str(err.value)
