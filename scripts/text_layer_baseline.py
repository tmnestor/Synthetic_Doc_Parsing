#!/usr/bin/env python3
"""The no-model baseline: each native PDF's own text layer, written as markdown, one paragraph per line.

    python -I scripts/text_layer_baseline.py <directory of PDFs> <output directory>

For every `<stem>.pdf` in the input directory it writes `<output directory>/<stem>.md`: the text lines
PDFium reads from the PDF's text layer (through `pypdfium2`), in its reading order, each line its own
paragraph. No model and no OCR run, so the score is what a native PDF gives for nothing. It is run by
`run_local_parser.py` as the builtin tool `text_layer` (`config/parsing_config.yml`), in the generator's own
env, and is listed there with `inputs: [pdf]`: an image has no text layer.

Only `pypdfium2` is imported, so the script runs in isolated mode with nothing from the repository.
"""

import argparse
import sys
from pathlib import Path

import pypdfium2


class TextLayerError(RuntimeError):
    """A PDF cannot be read, or the input directory holds none."""


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> TextLayerError:
    """Build a four-part refusal, each part on a labelled line."""
    return TextLayerError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def text_layer_baseline(pdf: Path) -> str:
    """Return a PDF's text lines as markdown: one paragraph per line, every page in order.

    Args:
        pdf: A native PDF.

    Returns:
        The lines joined by blank lines and ending in a newline; the empty string for a PDF with no text.

    Raises:
        TextLayerError: The file cannot be opened as a PDF.
    """
    try:
        document = pypdfium2.PdfDocument(pdf)
    except pypdfium2.PdfiumError as err:
        raise _diagnostic(
            "A page cannot be read as a PDF.",
            what=f"PDFium could not open {pdf} ({err}).",
            where=f"{pdf}",
            expected="a native PDF, as the typeset export writes beside each page's PNG.",
            recover="re-export the snapshot, or remove the file from the input directory.",
        ) from err
    lines: list[str] = []
    try:
        for page in document:
            text = page.get_textpage().get_text_range()
            lines.extend(line.strip() for line in text.splitlines() if line.strip())
    finally:
        document.close()
    return "\n\n".join(lines) + "\n" if lines else ""


def run(pages: Path, out: Path) -> int:
    """Write `<stem>.md` under `out` for every `<stem>.pdf` in `pages`; return the number written.

    Raises:
        TextLayerError: `pages` holds no PDF, or one cannot be read.
    """
    pdfs = sorted(pages.glob("*.pdf"))
    if not pdfs:
        raise _diagnostic(
            "There is no PDF to read.",
            what=f"{pages} holds no *.pdf file; the baseline reads only a PDF's text layer.",
            where=f"{pages}",
            expected="a directory of native PDFs, e.g. a typeset set run with --input pdf.",
            recover="run the text_layer tool on a typeset set with --input pdf.",
        )
    out.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        (out / f"{pdf.stem}.md").write_text(text_layer_baseline(pdf))
        print(f"read {pdf.name}")
    return len(pdfs)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pages", type=Path, help="The directory of PDFs")
    parser.add_argument("out", type=Path, help="The directory the markdown is written to")
    args = parser.parse_args(argv)
    try:
        count = run(args.pages, args.out)
    except TextLayerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {count} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
