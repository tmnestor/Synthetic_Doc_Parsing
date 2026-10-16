"""The text-layer baseline: a native PDF's own text lines, one paragraph per line, with no model.

Each PDF is compiled here from invented text, none of it in ground_truth/.
"""

from pathlib import Path

import pytest

from scripts import text_layer_baseline
from scripts.text_layer_baseline import text_layer_baseline as baseline
from tests.conftest import assert_diagnostic_error
from tests.parsing.parser_runs import INVENTED_LINES, invented_pdf


def test_a_two_line_pdf_gives_two_paragraphs(tmp_path: Path) -> None:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(invented_pdf(tmp_path / "typst"))
    assert baseline(pdf) == "Harbour lantern ledger\n\nQuarterly kelp tally\n"
    assert INVENTED_LINES == ("Harbour lantern ledger", "Quarterly kelp tally")


def test_a_pdf_with_no_text_gives_an_empty_page(tmp_path: Path) -> None:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(invented_pdf(tmp_path / "typst", lines=("#box(width: 1cm, height: 1cm)",)))
    assert baseline(pdf) == ""


def test_the_command_writes_one_markdown_file_per_pdf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pages, out = tmp_path / "pages", tmp_path / "out"
    pages.mkdir()
    for stem in ("CASE901_invoice", "CASE902_receipt"):
        (pages / f"{stem}.pdf").write_bytes(invented_pdf(tmp_path / "typst" / stem))
    assert text_layer_baseline.main([str(pages), str(out)]) == 0
    assert sorted(path.name for path in out.iterdir()) == ["CASE901_invoice.md", "CASE902_receipt.md"]
    assert (out / "CASE902_receipt.md").read_text() == "\n\n".join(INVENTED_LINES) + "\n"
    assert "2 pages" in capsys.readouterr().out


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "CASE901_invoice.pdf").write_bytes(b"not a PDF")
    assert text_layer_baseline.main([str(pages), str(tmp_path / "out")]) == 1
    message = capsys.readouterr().err
    assert_diagnostic_error(message)
    assert str(pages / "CASE901_invoice.pdf") in message


def test_a_directory_with_no_pdf_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "CASE901_invoice.png").write_bytes(b"\x89PNG")
    assert text_layer_baseline.main([str(pages), str(tmp_path / "out")]) == 1
    message = capsys.readouterr().err
    assert_diagnostic_error(message)
    assert str(pages) in message
