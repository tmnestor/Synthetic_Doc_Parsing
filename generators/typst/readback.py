"""Reshape a compiled page into stage one's records, and check its layout.

`page_geometry` turns a `CompiledPage` into the `geometry` dict stage one's renderers fill (Decision 2),
so `build_page` and `check_page` serve unchanged. Its blocks and scored texts are the translation's (stage
one's own records for the page); only the ink is new, read from the PDF:

- Words (Decision 3): `pypdfium2` gives the text layer's characters with their boxes. A word is a run of
  characters between whitespace (a typeset space, or one PDFium inserts at a line end or a gap), on one
  baseline.
- Ownership (Decision 4): a word belongs to the block, and inside a table to the cell, whose box (read
  from the page's `block-record` and `cell-record`) contains its centre. A word inside no block keeps no
  block, which `check_page`'s invariant 1 refuses.
- Lines (Decision 3): words that share a block, a cell and a baseline (within a quarter of the font size)
  are one line, its text the words left to right joined by one space and its box their union. A block's
  lines run top to bottom.
- Marks: a logo, barcode or QR code says what it encodes in pixels, not words, so its `InkRecord` takes
  the box of its block and the payload stage one recorded for it. The words a code also typesets (a
  barcode's caption, a logo's initials) are lines of its block, as the text layer holds them.
- Roles: the PDF does not say whether a line is a scored value, so a line takes stage one's role for the
  same cell (or, outside a table, the same block), a scored line there winning over a distractor and a
  distractor over plain text.
- Pixels (Decision 7): boxes are measured in points and scaled by `dpi / 72` into the PNG's pixels,
  rounded outward (floor the left and top, ceil the right and bottom). The PNG's own size is the page's
  rounded up, as `pypdfium2` sizes the bitmap it renders, so a box on the page in points stays on the PNG.
  A line's size is its font size in the same pixels, as stage one's is in its canvas's.

The exact (unrounded) pixel boxes of every block, with each block's column and the page's size, travel in
`geometry["measured"]`, which `layout_errors` reads.
"""

import ctypes
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

import pypdfium2
import pypdfium2.raw as pdfium

from generators.exporters.geometry import BlockRecord, Cell, InkRecord
from generators.parsing.omnidocbench import OVERLAY_PREFIX
from generators.typst.compile import CompiledPage, QueriedBox
from generators.typst.translate import Translation

# The primitives that draw a code mark, whose box is their block's.
MARK_KINDS = frozenset({"logo", "barcode", "qrcode"})
# Decision 3: words share a line when their baselines lie within this share of the font size.
BASELINE_SHARE = 0.25
# Typst measures in floating point; a difference below this, in pixels, is noise, not a misplaced box.
_NOISE_PX = 1e-3
# Ruling P9: a block stage one drew past its column may run out of it as far as stage one's ink did, plus
# this much, in points, for the two engines' differing glyph metrics and stage one's whole pixels.
OVERSHOOT_TOLERANCE_PT = 1.0
# Role precedence when one block or cell holds lines of several roles.
_ROLE_RANK = {"scored": 0, "distractor": 1, "text": 2}

Box = tuple[float, float, float, float]


class ReadbackError(RuntimeError):
    """Raised when a compiled page's records do not match its translation, or a word has two owners."""


def _floor(value: float) -> int:
    """Floor a pixel coordinate, ignoring measurement noise just below a whole pixel."""
    return math.floor(value + _NOISE_PX)


def _ceil(value: float) -> int:
    """Ceil a pixel coordinate, ignoring measurement noise just above a whole pixel."""
    return math.ceil(value - _NOISE_PX)


class Measured(NamedTuple):
    """The page's measured layout, in the PNG's pixels before rounding.

    Attributes:
        page: The page's width and height.
        boxes: Each block's own box, (left, top, right, bottom), by block id.
        columns: Each block's column, (left, right), by block id.
        allowances: How far each block may run out of its column's (left, right) edge: stage one's own
            overshoot for the block plus `OVERSHOOT_TOLERANCE_PT` on a side stage one overshot, nothing on
            a side it kept (Ruling P9). A block absent here must stay inside its column.
    """

    page: tuple[float, float]
    boxes: dict[int, Box]
    columns: dict[int, tuple[float, float]]
    allowances: dict[int, tuple[float, float]] = {}

    @property
    def size(self) -> tuple[int, int]:
        """The PNG's size: the page's, rounded up, as `pypdfium2` sizes the bitmap it renders."""
        return math.ceil(self.page[0]), math.ceil(self.page[1])

    def rounded(self, box: Box) -> tuple[int, int, int, int]:
        """Round a box outward to whole pixels (Decision 7).

        The page is rounded up the same way, so a box on the page in points stays on the PNG.

        Args:
            box: (left, top, right, bottom) in unrounded pixels.

        Returns:
            The box in whole pixels: floor of left and top, ceiling of right and bottom.
        """
        left, top, right, bottom = box
        return _floor(left), _floor(top), _ceil(right), _ceil(bottom)

    def pixel_box(self, block: int) -> tuple[int, int, int, int]:
        """Return a block's box rounded outward to whole pixels."""
        return self.rounded(self.boxes[block])


@dataclass(frozen=True)
class _Word:
    """One word of the text layer, in points from the page's top-left corner."""

    text: str
    box: Box
    baseline: float
    size: float
    bold: bool

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2


def _union(boxes: list[Box]) -> Box:
    """Return the smallest box holding every one of `boxes`."""
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _scaled(box: Box, scale: float) -> Box:
    """Return a box with every edge multiplied by `scale`."""
    return box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale


def _diagnostic(title: str, *, what: str, where: str, expected: str, recover: str) -> str:
    """Word a four-part diagnostic, each part on a labelled line."""
    return (
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def _font_is_bold(text_page: pypdfium2.PdfTextPage, index: int) -> bool:
    """Return whether a character is set in a bold face, read from its font's PostScript name."""
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    pdfium.FPDFText_GetFontInfo(text_page.raw, index, buffer, len(buffer), flags)
    # A subset font is named `ABCDEF+Family-Bold`; every vendored bold face ends in `-Bold`.
    return buffer.value.decode("latin-1").split("+")[-1].endswith("-Bold")


def _words(pdf: bytes) -> list[_Word]:
    """Read every word of a one-page PDF's text layer, in the text layer's order."""
    document = pypdfium2.PdfDocument(pdf)
    try:
        page = document[0]
        height = page.get_height()
        text_page = page.get_textpage()
        words: list[_Word] = []
        chars: list[tuple[str, Box, float, float, bool]] = []

        def flush() -> None:
            if chars:
                words.append(
                    _Word(
                        text="".join(char[0] for char in chars),
                        box=_union([char[1] for char in chars]),
                        baseline=chars[0][2],
                        size=max(char[3] for char in chars),
                        bold=all(char[4] for char in chars),
                    )
                )
                chars.clear()

        origin_x, origin_y = ctypes.c_double(), ctypes.c_double()
        for index in range(text_page.count_chars()):
            char = text_page.get_text_range(index, 1)
            if not char.strip() or pdfium.FPDFText_IsGenerated(text_page.raw, index) == 1:
                flush()
                continue
            left, bottom, right, top = text_page.get_charbox(index)
            pdfium.FPDFText_GetCharOrigin(text_page.raw, index, origin_x, origin_y)
            size = float(pdfium.FPDFText_GetFontSize(text_page.raw, index))
            baseline = height - origin_y.value
            if chars and abs(baseline - chars[0][2]) > BASELINE_SHARE * max(size, chars[0][3]):
                flush()
            box = (left, height - top, right, height - bottom)
            chars.append((char, box, baseline, size, _font_is_bold(text_page, index)))
        flush()
        return words
    finally:
        document.close()


def _contains(box: Box, point: tuple[float, float]) -> bool:
    """Return whether `point` lies in `box` (left, top, right, bottom), edges included."""
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _edges(queried: QueriedBox) -> Box:
    """Return a queried box as (left, top, right, bottom) in points."""
    x, y, w, h = queried.box_pt
    return x, y, x + w, y + h


def _matched_blocks(compiled: CompiledPage, translation: Translation, where: str) -> dict[int, QueriedBox]:
    """Pair each translated block with its one compiled record, refusing any mismatch."""
    by_id: dict[int, list[QueriedBox]] = {}
    for queried in compiled.blocks:
        by_id.setdefault(int(queried.record["id"]), []).append(queried)
    for block in translation.blocks:
        found = by_id.get(block.id, [])
        if len(found) != 1 or found[0].record["path"] != block.path:
            paths = [queried.record["path"] for queried in found]
            seen = f"{len(found)} record(s), at path(s) {paths},"
            raise ReadbackError(
                _diagnostic(
                    "A block's record is missing from the compiled page.",
                    what=f"block {block.id} at {block.path} has {seen} on the compiled page.",
                    where=f"{where}, block {block.path}",
                    expected="exactly one block-record per translated block, with its id and path, e.g. "
                    f'block-record(id: {block.id}, kind: "{block.kind}", path: "{block.path}", ...).',
                    recover="translate the page again and compile the source the translation returned; "
                    "a block the translator writes must be wrapped by its record (translate.py, record).",
                )
            )
    extra = sorted(set(by_id) - {block.id for block in translation.blocks})
    if extra:
        paths = [by_id[block][0].record["path"] for block in extra]
        raise ReadbackError(
            _diagnostic(
                "The compiled page carries a record its translation does not.",
                what=f"block record(s) {extra} at {paths} are on the page but not in the translation.",
                where=f"{where}, blocks {paths}",
                expected="the page's block records exactly the translation's blocks.",
                recover="compile the source the translation returned, unedited.",
            )
        )
    return {block: found[0] for block, found in by_id.items()}


def _owner[K](word: _Word, candidates: Iterable[tuple[K, str, Box]], *, where: str) -> K | None:
    """Return the key of the one candidate whose box contains the word's centre, or None; refuse two.

    Args:
        word: The word placed.
        candidates: (key, name in a diagnostic, box in points) per block or cell.
        where: The page, for a diagnostic.
    """
    owners = [(key, name) for key, name, box in candidates if _contains(box, word.centre)]
    if len(owners) > 1:
        names = [name for _, name in owners]
        centre = tuple(round(value, 2) for value in word.centre)
        raise ReadbackError(
            _diagnostic(
                "A word lies inside two boxes.",
                what=f"the word {word.text!r} centred at {centre} (points) lies inside {names}, so no one "
                "of them owns it.",
                where=f"{where}, {' and '.join(names)}",
                expected="every word inside at most one block's box, and one cell's within its table; no "
                "two boxes overlapping.",
                recover="fix the layout so the boxes no longer overlap (layout_errors names them).",
            )
        )
    return owners[0][0] if owners else None


def _roles(ink: list[InkRecord]) -> dict[tuple[int, Cell | None], str]:
    """Stage one's role for each (block, cell), and for each block under (block, None) as a fallback."""
    roles: dict[tuple[int, Cell | None], str] = {}

    def keep(key: tuple[int, Cell | None], role: str) -> None:
        if key not in roles or _ROLE_RANK[role] < _ROLE_RANK[roles[key]]:
            roles[key] = role

    for record in ink:
        if record.block is None:
            continue
        if record.cell is not None:
            keep((record.block, record.cell), record.role)
        keep((record.block, None), record.role)
    return roles


def _lines(words: list[_Word]) -> list[list[_Word]]:
    """Group one owner's words into lines by baseline, top to bottom, each line left to right."""
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: (w.baseline, w.box[0])):
        if lines and abs(word.baseline - lines[-1][0].baseline) <= BASELINE_SHARE * lines[-1][0].size:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.box[0]) for line in lines]


def _allowance(overshoot_pt: float, scale: float) -> float:
    """Return how far, in pixels, a block may pass one column edge stage one's ink passed by `overshoot_pt`."""
    return (overshoot_pt + OVERSHOOT_TOLERANCE_PT) * scale if overshoot_pt > 0 else 0.0


def page_geometry(compiled: CompiledPage, *, dpi: int, translation: Translation) -> dict:
    """Reshape a compiled page into stage one's `geometry` dict.

    Args:
        compiled: The compiled page and its records.
        dpi: The PNG's resolution (`TypesetConfig.raster_dpi`).
        translation: The translation the page was compiled from: its blocks, scored texts and stage one's
            ink (for the marks' payloads and the lines' roles).

    Returns:
        `ink` (an `InkRecord` per text line and code mark), `blocks`, `texts`, `width` and `height` (the
        PNG's size), in the PNG's pixels, and `measured` (a `Measured`).

    Raises:
        ReadbackError: The page's block records are not the translation's blocks, a word lies inside two
            blocks (or two cells), or a mark sits in a block that is not a code primitive.
    """
    where = f"the page compiled from {compiled.source_path.resolve()}"
    scale = dpi / 72
    matched = _matched_blocks(compiled, translation, where)
    blocks = {block.id: block for block in translation.blocks}
    measured = Measured(
        page=(compiled.width_pt * scale, compiled.height_pt * scale),
        boxes={block: _scaled(_edges(queried), scale) for block, queried in matched.items()},
        columns={
            block: (queried.column_pt[0] * scale, queried.column_pt[1] * scale)
            for block, queried in matched.items()
        },
        allowances={
            block: (_allowance(left, scale), _allowance(right, scale))
            for block, (left, right) in translation.overshoot.items()
        },
    )
    cells: dict[int, list[tuple[Cell, Box]]] = {}
    for queried in compiled.cells:
        fields = queried.record
        drawn = Cell(int(fields["row"]), int(fields["col"]), int(fields["span"]))
        cells.setdefault(int(fields["block"]), []).append((drawn, _edges(queried)))

    owned: dict[tuple[int | None, Cell | None], list[_Word]] = {}
    for word in _words(compiled.pdf):
        block_id = _owner(
            word,
            ((block, f"block {blocks[block].path}", _edges(q)) for block, q in matched.items()),
            where=where,
        )
        cell: Cell | None = None
        if block_id is not None:
            cell = _owner(
                word,
                (
                    (c, f"cell {tuple(c)} of block {blocks[block_id].path}", box)
                    for c, box in cells.get(block_id, [])
                ),
                where=where,
            )
        owned.setdefault((block_id, cell), []).append(word)

    roles = _roles(translation.ink)
    marks: dict[int, list[InkRecord]] = {}
    for record in translation.ink:
        if record.mark and record.block is not None:
            marks.setdefault(record.block, []).append(record)
    ink: list[InkRecord] = []

    def order(key: tuple[int | None, Cell | None]) -> tuple[int, int, int]:
        block_id, cell = key
        return (
            len(blocks) if block_id is None else block_id,
            -1 if cell is None else cell.row,
            -1 if cell is None else cell.column,
        )

    for key in sorted(owned, key=order):
        block_id, cell = key
        role = "text"
        if block_id is not None:
            role = roles.get((block_id, cell), roles.get((block_id, None), "text"))
        for line in _lines(owned[key]):
            ink.append(
                InkRecord(
                    role,
                    measured.rounded(_scaled(_union([w.box for w in line]), scale)),
                    False,
                    " ".join(w.text for w in line),
                    max(1, round(max(w.size for w in line) * scale)),
                    all(w.bold for w in line),
                    block_id,
                    cell,
                )
            )
    for block_id in sorted(marks):
        block = blocks[block_id]
        if block.kind not in MARK_KINDS or len(marks[block_id]) != 1:
            raise ReadbackError(
                _diagnostic(
                    "A code mark has no box of its own.",
                    what=f"block {block_id} at {block.path} is a {block.kind!r} holding "
                    f"{len(marks[block_id])} code mark(s); only a {sorted(MARK_KINDS)} block holding one "
                    "mark has the mark's box as its own.",
                    where=f"{where}, block {block.path}",
                    expected=f"each code mark drawn as a block of its own, one of {sorted(MARK_KINDS)}.",
                    recover="take parse_block off the container around the code in its layout YAML, so the "
                    "code records a block of its own.",
                )
            )
        (mark,) = marks[block_id]
        ink.append(
            InkRecord("distractor", measured.pixel_box(block_id), True, mark.text, 0, False, block_id)
        )

    width, height = measured.size
    return {
        "ink": ink,
        "blocks": list(translation.blocks),
        "texts": dict(translation.texts),
        "width": width,
        "height": height,
        "measured": measured,
    }


def _overlay(block: BlockRecord) -> bool:
    """Return whether a block is an overlay site, which may lie over the page beneath it."""
    return block.kind.startswith(OVERLAY_PREFIX)


def layout_errors(geometry: dict, *, where: str) -> list[str]:
    """Check the page's layout: no block off its page or out of its column, and no two blocks overlap.

    An overlay may lie over the page beneath it, so a pair with an overlay in it is never an overlap.
    Boxes are compared unrounded, so two blocks that merely touch are not refused. A block stage one itself
    drew past its column may pass it by its allowance (`Measured.allowances`, Ruling P9) and no further;
    the overlap check allows nothing.

    Args:
        geometry: A geometry `page_geometry` returned.
        where: The page checked, `CASE_ID / layout_id (mode)`.

    Returns:
        One four-part diagnostic per refused block or pair; empty when the layout keeps them all.
    """
    measured: Measured = geometry["measured"]
    blocks: dict[int, BlockRecord] = {block.id: block for block in geometry["blocks"]}
    width, height = measured.page
    errors: list[str] = []
    for block_id, (left, top, right, bottom) in sorted(measured.boxes.items()):
        path = blocks[block_id].path
        shown = tuple(round(edge, 2) for edge in (left, top, right, bottom))
        if (
            left < -_NOISE_PX
            or top < -_NOISE_PX
            or right > width + _NOISE_PX
            or bottom > height + _NOISE_PX
        ):
            errors.append(
                _diagnostic(
                    "A block runs off its page.",
                    what=f"block {path} has box {shown} (pixels), off its page of {width:.2f} x "
                    f"{height:.2f}.",
                    where=f"{where}, block {path}",
                    expected=f"every box within 0 <= x <= {width:.2f} and 0 <= y <= {height:.2f}, e.g. "
                    "(40.0, 60.0, 400.0, 90.0).",
                    recover="shorten the content, or give the block room in its layout YAML (a page: size, "
                    "a margin or a column width).",
                )
            )
        column = measured.columns.get(block_id)
        spill_left, spill_right = measured.allowances.get(block_id, (0.0, 0.0))
        if column is not None and (
            left < column[0] - spill_left - _NOISE_PX or right > column[1] + spill_right + _NOISE_PX
        ):
            errors.append(
                _diagnostic(
                    "A block runs out of its column.",
                    what=f"block {path} spans x {shown[0]} to {shown[2]} (pixels), out of its column, "
                    f"which spans {column[0]:.2f} to {column[1]:.2f}, further than stage one's own ink "
                    f"for it allows ({spill_left:.2f} left, {spill_right:.2f} right).",
                    where=f"{where}, block {path}",
                    expected="every block's left and right edges within its column's, e.g. a box from "
                    f"{column[0]:.2f} to {column[0]:.2f} + its width.",
                    recover="shorten the value (an unbreakable word wider than its column cannot wrap), or "
                    "widen the column in the layout YAML.",
                )
            )
    boxes = sorted(measured.boxes.items())
    for position, (first, a) in enumerate(boxes):
        for second, b in boxes[position + 1 :]:
            if _overlay(blocks[first]) or _overlay(blocks[second]):
                continue
            across = min(a[2], b[2]) - max(a[0], b[0])
            down = min(a[3], b[3]) - max(a[1], b[1])
            if across <= _NOISE_PX or down <= _NOISE_PX:
                continue
            one, two = blocks[first].path, blocks[second].path
            errors.append(
                _diagnostic(
                    "Two blocks overlap.",
                    what=f"blocks {one} and {two} overlap by {across:.2f} x {down:.2f} pixels, so a word "
                    "between them has no one owner.",
                    where=f"{where}, blocks {one} and {two}",
                    expected="no two block boxes overlapping, except an overlay over the page beneath it.",
                    recover=f"move {one} or {two} apart in the layout YAML (a foot anchor, a spacer, or a "
                    "split column), or shorten the content that pushes them together.",
                )
            )
    return errors
