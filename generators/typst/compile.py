"""Compile one typeset page with Typst, read its records back, and rasterise its PDF.

`compile_page` runs the pinned Typst over the repository's own fonts alone (system fonts and the faces
Typst embeds are both ignored), stamped with the configured creation date, so one source always gives
the same bytes. Pages import the template by its path from the repository root, so the source is fed on
stdin with the root set to the repository; a copy is written to the work directory so a refusal can name
a file whose line numbers match Typst's message. Typst then evaluates a query over the same source for
every `block-record` and `cell-record` (`generators/typst/template.typ`): each record's fields, its own
box and the column it was laid out in, all in points.

Any Typst warning fails the page (Decision 6), as does a page that breaks onto a second page.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pypdfium2
from PIL import Image

from generators.distractors.paths import REPO_ROOT
from generators.typst.config import TYPESET_CONFIG_PATH, TypesetConfig

# The source file a refusal names, written beside the PDF in the work directory.
SOURCE_NAME = "page.typ"
PDF_NAME = "page.pdf"
# Every block and cell record's value, in document order, as one JSON object.
_QUERY = "(blocks: query(<block-record>).map(m => m.value), cells: query(<cell-record>).map(m => m.value))"
# The keys of a record's value that place it, rather than describe it.
_PLACEMENT = ("page", "x", "y", "w", "h", "column-x", "column-w")


class TypstError(RuntimeError):
    """Raised when Typst fails or warns on a page, or the page is not exactly one page."""


@dataclass(frozen=True)
class QueriedBox:
    """One record read back from a compiled page.

    Attributes:
        record: The record's own fields (a block's id, kind, path, ...; a cell's block, row, col, span).
        box_pt: The element's own box, (x, y, w, h) in points from the page's top-left corner.
        column_pt: The column the element was laid out in, (left, right) in points.
    """

    record: dict
    box_pt: tuple[float, float, float, float]
    column_pt: tuple[float, float]


@dataclass(frozen=True)
class CompiledPage:
    """One compiled page.

    Attributes:
        pdf: The PDF's bytes.
        blocks: Every `block-record`, in document order.
        cells: Every `cell-record`, in document order.
        width_pt: The page's width in points.
        height_pt: The page's height in points.
        source_path: The copy of the page's source in the work directory, which a diagnostic names.
    """

    pdf: bytes
    blocks: list[QueriedBox]
    cells: list[QueriedBox]
    width_pt: float
    height_pt: float
    source_path: Path


def _refusal(title: str, *, what: str, source: Path, expected: str, recover: str) -> TypstError:
    """Build a four-part refusal naming the page's source file and the template it imports."""
    return TypstError(
        f"{title}\n"
        f"  What:     {what}\n"
        f"  Where:    {source.resolve()} (the page's source, compiled from stdin as <stdin>), importing "
        f"the template named in {TYPESET_CONFIG_PATH} -> template\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _typst(args: list[str], source: str, *, config: TypesetConfig) -> subprocess.CompletedProcess:
    """Run Typst on `source` (stdin) over the repository's fonts alone, rooted at the repository.

    Typst's own environment variables are cleared, so neither a font path nor a timestamp set in the
    shell reaches the compile.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TYPST_") and key != "SOURCE_DATE_EPOCH"
    }
    return subprocess.run(
        [
            "typst",
            *args,
            "--root",
            str(REPO_ROOT),
            "--font-path",
            str(REPO_ROOT / config.font_dir),
            "--ignore-system-fonts",
            "--ignore-embedded-fonts",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _checked(done: subprocess.CompletedProcess, step: str, *, source: Path) -> None:
    """Refuse a Typst run that failed or printed anything at all on stderr (a warning, Decision 6)."""
    if done.returncode == 0 and not done.stderr.strip():
        return
    failed = "failed" if done.returncode != 0 else "warned"
    raise _refusal(
        f"Typst {failed} on a page.",
        what=f"`typst {step}` exited {done.returncode} and said:\n{done.stderr.rstrip()}",
        source=source,
        expected="a page that compiles with no error and no warning (every warning fails the page), e.g. "
        "every font it names present in the configured font_dir.",
        recover="fix the markup or template line Typst names above (a missing font: add its .ttf to "
        "font_dir or name a vendored face), then compile again.",
    )


def _queried(value: dict, *, source: Path) -> QueriedBox:
    """Split a record's value into its fields, its own box and its column, refusing one off page 1."""
    if value["page"] != 1:
        raise _refusal(
            "A record lies on a later page.",
            what=f"the record {value!r} is on page {value['page']}.",
            source=source,
            expected="every record on page 1, the page's only page.",
            recover="shorten the page's content so it fits one page.",
        )
    record = {key: item for key, item in value.items() if key not in _PLACEMENT}
    box = (float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"]))
    column = (float(value["column-x"]), float(value["column-x"]) + float(value["column-w"]))
    return QueriedBox(record=record, box_pt=box, column_pt=column)


def compile_page(source: str, *, workdir: Path, config: TypesetConfig) -> CompiledPage:
    """Compile one page and read back every block and cell record on it.

    Args:
        source: The page's whole Typst source (a `Translation.source`).
        workdir: A directory for the page's source copy and PDF (`page.typ`, `page.pdf`).
        config: The typesetting configuration: font directory and creation timestamp.

    Returns:
        The PDF, its records and its size.

    Raises:
        TypstError: Typst failed or printed a warning, or the page broke onto more than one page; each
            four-part, quoting Typst.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    source_path = workdir / SOURCE_NAME
    source_path.write_text(source)
    pdf_path = workdir / PDF_NAME
    compiled = _typst(
        ["compile", "-", str(pdf_path), "--creation-timestamp", str(config.creation_timestamp)],
        source,
        config=config,
    )
    _checked(compiled, "compile", source=source_path)
    pdf = pdf_path.read_bytes()
    document = pypdfium2.PdfDocument(pdf)
    try:
        if len(document) != 1:
            raise _refusal(
                "A page broke onto more than one page.",
                what=f"the source compiled to {len(document)} pages.",
                source=source_path,
                expected="exactly one page, as each document is one page in stage one.",
                recover="shorten the content, or check the layout's page: key gives the page room for it.",
            )
        width, height = document[0].get_size()
    finally:
        document.close()
    queried = _typst(["eval", _QUERY, "--in", "-", "--format", "json"], source, config=config)
    _checked(queried, "eval (the record query)", source=source_path)
    values = json.loads(queried.stdout)
    return CompiledPage(
        pdf=pdf,
        blocks=[_queried(value, source=source_path) for value in values["blocks"]],
        cells=[_queried(value, source=source_path) for value in values["cells"]],
        width_pt=float(width),
        height_pt=float(height),
        source_path=source_path,
    )


def rasterise(pdf: bytes, *, dpi: int) -> Image.Image:
    """Render a one-page PDF to an RGB image at `dpi`.

    Args:
        pdf: The PDF's bytes.
        dpi: Dots per inch (`TypesetConfig.raster_dpi`).

    Returns:
        The page as an RGB image, `dpi / 72` pixels per point.
    """
    document = pypdfium2.PdfDocument(pdf)
    try:
        return document[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        document.close()
