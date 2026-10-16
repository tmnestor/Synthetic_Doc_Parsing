"""The scan a snapshot passes before it is published: no file name or text says how it was produced.

The snapshot goes to a bucket that hosted models and their operators read, so it may say what the data
is and how to use it, never which repository, command, input file or library made it. The terms are the
operator's, declared under `eval_set.forbidden_terms`; this module only finds them.

A term matches case-insensitively and as a whole word: where the term begins or ends with a letter or
digit, the neighbouring character must not be one, so `PROD` does not match `product`. A term that ends
in punctuation (`config/`) is matched up to that punctuation.

Images are not read. A PDF is read through pypdfium2 (Review Focus 5): its document information (title,
author, subject, keywords, creator, producer), its XMP metadata packet and the text layer of every page,
so a PDF can name neither the tool that made it nor the layout it was drawn from (the snapshot's PDFs
keep their XMP uncompressed, `generators.typst.metadata`). Every other file is read as UTF-8 text. A file
that cannot be read in one of these ways refuses the export, since it can be neither scanned nor trusted
to be what the manifest says.
"""

import re
from pathlib import Path

import pypdfium2

# Image files carry pixels, not text, and are not scanned.
IMAGE_SUFFIXES = (".png",)
PDF_SUFFIX = ".pdf"
# The document information fields read, as the hits name them (pypdfium2's keys, lowercased).
PDF_FIELDS = ("Title", "Author", "Subject", "Keywords", "Creator", "Producer")
# An XMP packet, which a PDF stores as a metadata stream and pypdfium2 does not read.
_XMP_PACKET = re.compile(rb"<\?xpacket begin.*?<\?xpacket end[^>]*>", re.S)
# The most hits named in a refusal; the total is always given.
LISTED = 20


def _pattern(term: str) -> re.Pattern[str]:
    """A case-insensitive pattern for one term, bounded only where the term ends in a word character."""
    head = r"(?<![A-Za-z0-9])" if term[0].isalnum() else ""
    tail = r"(?![A-Za-z0-9])" if term[-1].isalnum() else ""
    return re.compile(head + re.escape(term) + tail, re.IGNORECASE)


def _pdf_texts(path: Path) -> list[tuple[str, str]] | None:
    """A PDF's scanned texts as (where, text): each information field, each XMP packet, the text layer.

    Returns:
        The texts, or None when the file is not a PDF pypdfium2 can open.
    """
    data = path.read_bytes()
    try:
        document = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError:
        return None
    try:
        texts = [(f"metadata {field.lower()}", document.get_metadata_value(field)) for field in PDF_FIELDS]
        texts += [
            ("metadata xmp", packet.decode("utf-8", "replace")) for packet in _XMP_PACKET.findall(data)
        ]
        pages = []
        for page in document:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
            textpage.close()
            page.close()
        texts.append(("page text", "\n".join(pages)))
    finally:
        document.close()
    return texts


def find_forbidden(root: Path, terms: tuple[str, ...]) -> list[str]:
    """Find every forbidden term in the file names and texts under `root`.

    Args:
        root: The directory about to be published.
        terms: The forbidden terms (empty finds nothing).

    Returns:
        One line per hit, sorted by path then line: `<path>: name contains 'term'`,
        `<path>:<line>: contains 'term'` in a text file, and in a PDF `<path>: metadata <field> contains
        'term'` (a field of `PDF_FIELDS`, lowercased, or `xmp`) or `<path>: page text contains 'term'`.
        A file that is not UTF-8 text, or a PDF that cannot be opened, is reported as unreadable.
    """
    patterns = [(term, _pattern(term)) for term in terms]
    if not patterns:
        return []
    hits: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        hits += [f"{relative}: name contains {term!r}" for term, pat in patterns if pat.search(relative)]
        if path.suffix.lower() in IMAGE_SUFFIXES:
            continue
        if path.suffix.lower() == PDF_SUFFIX:
            texts = _pdf_texts(path)
            if texts is None:
                hits.append(f"{relative}: not a readable PDF, so it cannot be scanned")
                continue
            hits += [
                f"{relative}: {where} contains {term!r}"
                for where, text in texts
                for term, pat in patterns
                if pat.search(text)
            ]
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            hits.append(f"{relative}: not UTF-8 text, so it cannot be scanned")
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            hits += [
                f"{relative}:{number}: contains {term!r}" for term, pat in patterns if pat.search(line)
            ]
    return hits


def refusal(hits: list[str], *, root: Path, config_path: Path) -> str:
    """The four-part diagnostic for a snapshot that names how it was produced.

    Args:
        hits: `find_forbidden`'s result, non-empty.
        root: The directory scanned.
        config_path: The generation config declaring the terms.

    Returns:
        The message.
    """
    named = hits[:LISTED]
    more = [f"... and {len(hits) - LISTED} more ({len(hits)} in all)"] if len(hits) > LISTED else []
    listing = "\n".join(f"            {line}" for line in named + more)
    return (
        "The snapshot names how it was produced, so it was not published.\n"
        f"  What:     {len(hits)} forbidden term(s) in the files about to be published:\n{listing}\n"
        f"  Where:    {root} (the staging copy), against {config_path} -> eval_set.forbidden_terms\n"
        "  Expected: published files say what the data is and how to use it, never which repository,\n"
        "            command, input file or library made it.\n"
        "  Recover:  reword the source of each hit (the README template, the notebook or a shipped\n"
        "            script), or remove a term from eval_set.forbidden_terms if it discloses nothing."
    )


def layout_refusal(hits: list[str], *, root: Path) -> str:
    """The four-part diagnostic for a snapshot that names a layout by its id rather than its opaque key.

    Args:
        hits: `find_forbidden`'s result over the layout ids, non-empty.
        root: The directory scanned.

    Returns:
        The message.
    """
    named = hits[:LISTED]
    more = [f"... and {len(hits) - LISTED} more ({len(hits)} in all)"] if len(hits) > LISTED else []
    listing = "\n".join(f"            {line}" for line in named + more)
    return (
        "The snapshot names the layouts its pages were drawn from, so it was not published.\n"
        f"  What:     {len(hits)} layout id(s) in the files about to be published:\n{listing}\n"
        f"  Where:    {root} (the staging copy); the ids are the keys under layouts: in\n"
        "            config/layouts/*.yml, and the file named is written by generators/snapshot/export.py\n"
        "  Expected: a published file names a layout only by its opaque key, <doc_type>_layout_<n>\n"
        "            (from opaque_layout_keys), as the parsing pages do.\n"
        "  Recover:  pass plan.layout_keys[layout_id], not layout_id, where export.py writes the file named."
    )
