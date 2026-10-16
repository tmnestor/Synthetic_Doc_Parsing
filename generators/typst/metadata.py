"""Set a compiled PDF's document information for publication: the Producer chosen, the tool unnamed.

Typst names itself in the document information dictionary (`/Creator`) and in the XMP metadata packet
(`xmp:CreatorTool`, and the XMP toolkit `x:xmptk`), and writes no Producer. A published PDF says what the
data is, never how it was made (spec section 5, ruling P13), so `publish_metadata` rewrites both: the
Producer becomes `eval_set.pdf_producer` (an empty string leaves it blank), and every field naming the
tool is blanked.

The rewrite works on the bytes Typst writes: a classic cross-reference table in one subsection, no
incremental update, an uncompressed XMP stream. Only the two objects change; every other byte is kept,
and the table and `startxref` are rebuilt for the new offsets. It is a pure function of its input, so a
rebuild stays byte-identical. A PDF of any other structure is refused rather than guessed at.
"""

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

import pypdfium2

_STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*$")
_TABLE = re.compile(rb"xref\r?\n0 (\d+)\r?\n")
_ENTRY = re.compile(rb"(\d{10}) (\d{5}) ([nf])(?: \r| \n|\r\n)")
_ENTRY_SIZE = 20
_TRAILER = re.compile(rb"trailer\s*<<(.*?)>>\s*startxref", re.S)
# A literal string, as Typst writes the information dictionary's values.
_INFO_STRING = rb"\((?:\\.|[^\\()])*\)"
_NAMING_KEYS = (b"Producer", b"Creator")
_CREATOR_TOOL = re.compile(rb"<xmp:CreatorTool>[^<]*</xmp:CreatorTool>")
_TOOLKIT = re.compile(rb'x:xmptk="[^"]*"')
_XMP_PRODUCER = re.compile(rb"<pdf:Producer>[^<]*</pdf:Producer>")
_DESCRIPTION_END = b"</rdf:Description>"


class PdfMetadataError(RuntimeError):
    """Raised when a compiled PDF's structure is not the one the rewrite knows, or the rewrite fails."""


def _refuse(what: str, *, where: str) -> PdfMetadataError:
    return PdfMetadataError(
        "A typeset PDF's document information cannot be set for publication.\n"
        f"  What:     {what}\n"
        f"  Where:    the PDF typeset for {where}; generators/typst/metadata.py rewrites it\n"
        "  Expected: a PDF as the pinned Typst writes it: a classic cross-reference table in one "
        "subsection, a trailer with /Info and /Root and no /Prev, and an uncompressed XMP stream under "
        "the catalogue's /Metadata, e.g. 'xref\\n0 92\\n' followed by 92 twenty-byte entries.\n"
        "  Recover:  compile the page with the Typst version config/typeset_config.yml pins; if that "
        "version writes another structure, teach generators/typst/metadata.py to read it."
    )


@dataclass(frozen=True)
class _Table:
    """The cross-reference table: where it starts, its entries, and where they end."""

    start: int
    head: bytes
    entries: list[tuple[int, int, bytes, bytes]]  # (offset, generation, kind, end of line)
    end: int


def _table(pdf: bytes, *, where: str) -> _Table:
    found = _STARTXREF.search(pdf)
    if found is None:
        raise _refuse("the file does not end in 'startxref <offset> %%EOF'.", where=where)
    start = int(found.group(1))
    head = _TABLE.match(pdf, start)
    if head is None:
        raise _refuse(
            f"startxref points at byte {start}, which does not begin a classic 'xref' table in one "
            "subsection starting at object 0.",
            where=where,
        )
    entries = []
    at = head.end()
    for _ in range(int(head.group(1))):
        entry = _ENTRY.fullmatch(pdf, at, at + _ENTRY_SIZE)
        if entry is None:
            raise _refuse(
                f"the cross-reference entry at byte {at} is not 20 bytes as specified.", where=where
            )
        entries.append((int(entry.group(1)), int(entry.group(2)), entry.group(3), pdf[at + 18 : at + 20]))
        at += _ENTRY_SIZE
    return _Table(start=start, head=head.group(0), entries=entries, end=at)


def _reference(dictionary: bytes, key: bytes, *, owner: str, where: str) -> int:
    found = re.search(rb"/" + key + rb"\s+(\d+)\s+0\s+R", dictionary)
    if found is None:
        raise _refuse(f"the {owner} has no /{key.decode()} reference.", where=where)
    return int(found.group(1))


def _info(span: bytes, producer: bytes, *, where: str) -> bytes:
    """The information dictionary with the Producer set and the Creator blank."""
    opened, closed = span.find(b"<<"), span.rfind(b">>")
    if opened < 0 or closed < opened:
        raise _refuse("the /Info object is not a dictionary.", where=where)
    inner = span[opened + 2 : closed]
    for key in _NAMING_KEYS:
        inner = re.sub(rb"/" + key + rb"\s*" + _INFO_STRING, b"", inner)
        if b"/" + key in inner:
            raise _refuse(f"the /Info dictionary's /{key.decode()} is not a literal string.", where=where)
    rewritten = b"<</Producer(" + producer + b")/Creator()" + inner + b">>"
    return span[:opened] + rewritten + span[closed + 2 :]


def _xmp(span: bytes, producer: str, *, where: str) -> bytes:
    """The XMP stream with the tool blanked and the Producer recorded, its /Length updated."""
    marker = re.search(rb"stream(\r\n|\n)", span)
    length = re.search(rb"/Length\s+(\d+)", span)
    if marker is None or length is None:
        raise _refuse("the /Metadata object is not a stream with a direct /Length.", where=where)
    header = span[: marker.start()]
    if b"/Filter" in header:
        raise _refuse("the /Metadata stream carries a /Filter, so its XMP cannot be read.", where=where)
    begin = marker.end()
    end = begin + int(length.group(1))
    packet = span[begin:end]
    if _DESCRIPTION_END not in packet:
        raise _refuse(f"the XMP packet has no {_DESCRIPTION_END.decode()}.", where=where)
    packet = _CREATOR_TOOL.sub(b"<xmp:CreatorTool></xmp:CreatorTool>", packet)
    packet = _TOOLKIT.sub(b'x:xmptk=""', packet)
    packet = _XMP_PRODUCER.sub(b"", packet)
    element = b"<pdf:Producer>" + escape(producer).encode() + b"</pdf:Producer>"
    packet = packet.replace(_DESCRIPTION_END, element + _DESCRIPTION_END, 1)
    header = header[: length.start(1)] + str(len(packet)).encode() + header[length.end(1) :]
    return header + span[marker.start() : begin] + packet + span[end:]


def _pdf_string(value: str) -> bytes:
    """A value as the inside of a PDF literal string (printable ASCII, as the config requires)."""
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")


def publish_metadata(pdf: bytes, *, producer: str, where: str) -> bytes:
    """Set the Producer and blank every field naming the tool, in the information dictionary and the XMP.

    Args:
        pdf: The PDF as Typst wrote it.
        producer: `eval_set.pdf_producer`, printable ASCII; an empty string leaves the Producer blank.
        where: The page the PDF is of, named in a refusal (`CASE001 / layout (typeset plain)`).

    Returns:
        The rewritten PDF: the same page, text layer and dates, with `/Producer(<producer>)`, `/Creator()`,
        an empty `xmp:CreatorTool` and `x:xmptk`, and `pdf:Producer` set to `producer`.

    Raises:
        PdfMetadataError: The PDF's structure is not the one Typst writes, or the rewrite does not read
            back as set.
    """
    table = _table(pdf, where=where)
    trailer = _TRAILER.search(pdf, table.end)
    if trailer is None:
        raise _refuse("no trailer dictionary follows the cross-reference table.", where=where)
    for key in (b"/Prev", b"/XRefStm"):
        if key in trailer.group(1):
            raise _refuse(
                f"the trailer carries {key.decode()}: the file was updated incrementally.", where=where
            )
    info = _reference(trailer.group(1), b"Info", owner="trailer", where=where)
    root = _reference(trailer.group(1), b"Root", owner="trailer", where=where)

    used = sorted(
        (offset, number) for number, (offset, _, kind, _) in enumerate(table.entries) if kind == b"n"
    )
    if not used:
        raise _refuse("the cross-reference table lists no object in use.", where=where)
    bounds = [offset for offset, _ in used[1:]] + [table.start]
    spans = {number: pdf[offset:bound] for (offset, number), bound in zip(used, bounds, strict=True)}
    for number, span in spans.items():
        if not re.match(rb"%d\s+0\s+obj\b" % number, span):
            raise _refuse(f"the table's offset for object {number} does not point at 'obj'.", where=where)
    if info not in spans or root not in spans:
        raise _refuse("the trailer's /Info or /Root names no object in the table.", where=where)
    metadata = _reference(spans[root], b"Metadata", owner="catalogue", where=where)
    if metadata not in spans:
        raise _refuse("the catalogue's /Metadata names no object in the table.", where=where)

    spans[info] = _info(spans[info], _pdf_string(producer), where=where)
    spans[metadata] = _xmp(spans[metadata], producer, where=where)

    out = bytearray(pdf[: used[0][0]])
    offsets = {}
    for _, number in used:
        offsets[number] = len(out)
        out += spans[number]
    start = len(out)
    out += table.head
    for number, (offset, generation, kind, eol) in enumerate(table.entries):
        moved = offsets[number] if kind == b"n" else offset
        out += b"%010d %05d " % (moved, generation) + kind + eol
    tail = pdf[table.end :]
    pointer = _STARTXREF.search(tail)
    assert pointer is not None  # the table was found through it
    out += tail[: pointer.start(1)] + str(start).encode() + tail[pointer.end(1) :]
    published = bytes(out)

    document = pypdfium2.PdfDocument(published)
    try:
        written = (document.get_metadata_value("Producer"), document.get_metadata_value("Creator"))
        pages = len(document)
    finally:
        document.close()
    if written != (producer, "") or pages != 1:
        raise _refuse(
            f"the rewritten PDF reads back Producer {written[0]!r}, Creator {written[1]!r} and {pages} "
            f"page(s), not Producer {producer!r}, a blank Creator and one page.",
            where=where,
        )
    return published
