"""The compiler wrapper: a page compiled by Typst, its records read back, and its PDF rasterised.

Pages are written by hand against the shared template, with invented text, except where a gate page is
compiled twice to check a rebuild reproduces it byte for byte (Review Focus 4).
"""

import ctypes
import math
import subprocess
import sys
from pathlib import Path

import pypdfium2
import pypdfium2.raw as pdfium
import pytest

from generators.typst.compile import CompiledPage, TypstError, compile_page, rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, layout_page, load_typeset_config
from generators.typst.translate import GATE_LAYOUTS, translate_page
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, layouts_for

CONFIG = load_typeset_config(TYPESET_CONFIG_PATH)
GATE_DOC_TYPES = {"invoices": "invoice", "receipts": "receipt", "bank_statements": "bank_statement"}

# Invented text: none of it appears in ground_truth/.
MINIMAL = "\n".join(
    [
        f'#import "/{CONFIG.template}": *',
        "#set page(width: 80mm, height: auto, margin: 10pt)",
        "#show: typeset",
        '#block-record(id: 0, kind: "text", path: "body[0]", align: left, dsl-lines((dsl-line(font: '
        '"Liberation Mono", size: 9pt, bold: false, fill: black, ascent: 7pt, advance: 11pt, '
        '"Brackenridge Ferry Timetable"),)))',
        "",
    ]
)

# Compile the source file named by argv[1] in a fresh process and write the PDF to argv[2].
_COMPILE_SCRIPT = (
    "import sys\n"
    "from pathlib import Path\n"
    "from generators.typst.compile import compile_page\n"
    "from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config\n"
    "out = Path(sys.argv[2])\n"
    "page = compile_page(Path(sys.argv[1]).read_text(), workdir=out.parent,\n"
    "    config=load_typeset_config(TYPESET_CONFIG_PATH))\n"
    "out.write_bytes(page.pdf)\n"
)


def compile_in_new_process(source: Path, out: Path) -> bytes:
    """Compile `source` in a separate Python process and return the PDF it wrote."""
    out.parent.mkdir(parents=True)
    done = subprocess.run(
        [sys.executable, "-c", _COMPILE_SCRIPT, str(source), str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return out.read_bytes()


def test_a_minimal_page_compiles_and_reports_its_record(tmp_path: Path) -> None:
    page = compile_page(MINIMAL, workdir=tmp_path, config=CONFIG)
    assert isinstance(page, CompiledPage)
    assert page.pdf.startswith(b"%PDF")
    assert page.width_pt == pytest.approx(80 * 72 / 25.4, abs=0.01)
    assert page.height_pt > 0
    (block,) = page.blocks
    assert block.record["id"] == 0 and block.record["path"] == "body[0]"
    x, y, w, h = block.box_pt
    assert (x, y) == pytest.approx((10.0, 10.0), abs=0.01)
    assert 0 < w <= page.width_pt - 20 and h == pytest.approx(11.0, abs=0.01)
    assert page.cells == []


def test_the_same_source_compiled_in_two_processes_is_byte_identical(tmp_path: Path) -> None:
    source = tmp_path / "page.typ"
    source.write_text(MINIMAL)
    first = compile_in_new_process(source, tmp_path / "one" / "page.pdf")
    second = compile_in_new_process(source, tmp_path / "two" / "page.pdf")
    assert first == second


@pytest.mark.parametrize(("layout_file", "layout_id"), GATE_LAYOUTS)
def test_a_gate_page_compiled_in_two_processes_is_byte_identical(
    layout_file: str, layout_id: str, tmp_path: Path
) -> None:
    doc_type = GATE_DOC_TYPES[Path(layout_file).stem]
    layout = layouts_for(doc_type)[layout_id]
    entry = next(e for e in corpus_entries(doc_type).values() if e["layout"] == layout_id)
    layout_path = REPO_ROOT / layout_file
    translation = translate_page(
        layout,
        entry,
        layout_id=layout_id,
        layout_path=layout_path,
        doc_type=GATE_DOC_TYPES[Path(layout_path).stem],
        page=layout_page(layout, layout_path=str(layout_path), layout_id=layout_id),
        distractors=True,
        config=CONFIG,
        overlay_dir=tmp_path,
    )
    source = tmp_path / "page.typ"
    source.write_text(translation.source)
    first = compile_in_new_process(source, tmp_path / "one" / "page.pdf")
    second = compile_in_new_process(source, tmp_path / "two" / "page.pdf")
    assert first == second


def test_a_font_absent_from_the_font_directory_fails_four_part_quoting_typst(tmp_path: Path) -> None:
    source = MINIMAL.replace('"Liberation Mono"', '"Fernwhistle Grotesk"')
    with pytest.raises(TypstError) as exc_info:
        compile_page(source, workdir=tmp_path, config=CONFIG)
    message = str(exc_info.value)
    assert_diagnostic_error(exc_info.value)
    assert "unknown font family: fernwhistle grotesk" in message
    assert str((tmp_path / "page.typ").resolve()) in message
    assert (tmp_path / "page.typ").read_text() == source


def test_only_the_repository_fonts_are_embedded(tmp_path: Path) -> None:
    # Text set without a font would take Typst's own embedded default face if it were allowed.
    page = compile_page(MINIMAL + "#text[Brackenridge]\n", workdir=tmp_path, config=CONFIG)
    # A face's PostScript name begins with its family, as its file name does (`LiberationMono-Bold`).
    vendored = {path.stem.split("-")[0] for path in (REPO_ROOT / CONFIG.font_dir).glob("*.ttf")}
    text_page = pypdfium2.PdfDocument(page.pdf)[0].get_textpage()
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    used = set()
    for index in range(text_page.count_chars()):
        if pdfium.FPDFText_GetFontInfo(text_page.raw, index, buffer, len(buffer), flags):
            used.add(buffer.value.decode("latin-1").split("+")[-1].split("-")[0])
    assert used and used <= vendored, used


def test_a_compile_error_fails_four_part_quoting_typst(tmp_path: Path) -> None:
    with pytest.raises(TypstError) as exc_info:
        compile_page(MINIMAL + "#dsl-no-such-function()\n", workdir=tmp_path, config=CONFIG)
    assert_diagnostic_error(exc_info.value)
    assert "unknown variable: dsl-no-such-function" in str(exc_info.value)


def test_a_page_that_breaks_onto_a_second_page_fails_four_part(tmp_path: Path) -> None:
    with pytest.raises(TypstError) as exc_info:
        compile_page(
            MINIMAL + '#pagebreak()\n#text(font: "Liberation Mono")[Kestrel]\n',
            workdir=tmp_path,
            config=CONFIG,
        )
    assert_diagnostic_error(exc_info.value)
    assert "2 pages" in str(exc_info.value)


def test_the_png_is_the_pdf_page_at_the_raster_resolution(tmp_path: Path) -> None:
    page = compile_page(MINIMAL, workdir=tmp_path, config=CONFIG)
    image = rasterise(page.pdf, dpi=CONFIG.raster_dpi)
    scale = CONFIG.raster_dpi / 72
    # Rounded up, so the bitmap covers the whole page (Decision 7's outward rounding, on the page itself).
    assert image.size == (math.ceil(page.width_pt * scale), math.ceil(page.height_pt * scale))
    assert image.mode == "RGB"
