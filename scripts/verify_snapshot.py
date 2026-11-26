#!/usr/bin/env python3
"""Verify a snapshot against its own MANIFEST.json, using the Python standard library only.

    python tools/verify_snapshot.py [snapshot_dir]

The snapshot directory defaults to the parent of the `tools/` directory this script sits in. The check
fails (exit 1) on:

- a file listed in the manifest that is missing, or whose SHA-256 differs;
- a file present that the manifest does not list (a `.DS_Store`, and anything in an `.ipynb_checkpoints`
  folder, is tolerated and reported as a note: a file manager and a Jupyter server write those beside any
  file that is opened, and neither is data);
- a file the manifest both lists and declares outside its cover;
- a manifest path that is absolute or climbs out of the snapshot with `..`;
- a `SHA256SUMS` that disagrees with the manifest's `files` map;
- a half whose image count disagrees with the images present;
- a typeset set whose image or PDF count disagrees with the manifest, or an image without its PDF (or a
  PDF without its image) of the same name.

Every file is checked by its SHA-256, the PDFs included.

A manifest may declare paths it does not cover, under `not_covered`: a file or a directory at any depth,
relative to the snapshot. Nothing at or under such a path is hashed, counted as extra or required to be
present, and each is reported as a note. A manifest with no such key covers every file, as a snapshot's
does; one that covers the data alone keeps the same SHA-256 when a guide or a tool beside the data is
corrected.

It never regenerates anything: the snapshot is checked against the manifest it shipped with. The manifest
cannot vouch for itself; compare its own SHA-256 with the one recorded beside the upload.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS"
UNHASHED = (MANIFEST_NAME, CHECKSUMS_NAME)
TOLERATED = ".DS_Store"
# The folder a Jupyter server writes beside any file opened in it, at any depth. Nothing in one is data.
TOLERATED_FOLDER = ".ipynb_checkpoints"
# The manifest key that names the paths its `files` map does not cover.
NOT_COVERED_KEY = "not_covered"
# The most paths named per kind of problem; the total is always given.
LISTED = 20
_CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _named(kind: str, paths: list[str]) -> list[str]:
    """One line per offending path, up to the cap, then the total."""
    if not paths:
        return []
    lines = [f"{kind}: {path}" for path in sorted(paths)[:LISTED]]
    if len(paths) > LISTED:
        lines.append(f"{kind}: ... and {len(paths) - LISTED} more ({len(paths)} in all)")
    return lines


def _unsafe(path: str) -> bool:
    """A manifest path that is absolute, uses backslashes or climbs out of the snapshot."""
    pure = PurePosixPath(path)
    return pure.is_absolute() or "\\" in path or ".." in pure.parts or not path or path.startswith("/")


def _under(path: str, tops: list[str]) -> bool:
    """Whether `path` is one of `tops` or lies under one of them."""
    return any(path == top or path.startswith(top.rstrip("/") + "/") for top in tops)


def _read_checksums(path: Path) -> tuple[dict[str, str], list[str]]:
    """Parse `<hash>  <path>` lines; return the map and any malformed lines."""
    entries: dict[str, str] = {}
    malformed = []
    for line in path.read_text().splitlines():
        digest, sep, name = line.partition("  ")
        if not sep or len(digest) != 64 or not name:
            malformed.append(line)
        else:
            entries[name] = digest
    return entries, malformed


def inspect(snapshot_root: Path) -> tuple[list[str], list[str]]:
    """Check a snapshot against its manifest.

    Args:
        snapshot_root: The snapshot directory (holding MANIFEST.json).

    Returns:
        (problems, notes): problems make the check fail; notes do not.
    """
    root = snapshot_root.resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"missing manifest: no {MANIFEST_NAME} in {root}"], []
    try:
        manifest = json.loads(manifest_path.read_text())
        files = dict(manifest["files"])
        counts = dict(manifest["counts"])
        halves = dict(manifest["halves"])
        typeset_sets = dict(manifest["typeset_sets"])
        outside = [str(path) for path in list(manifest.get(NOT_COVERED_KEY, []))]
    except (ValueError, KeyError, TypeError) as err:
        return [f"unreadable manifest: {MANIFEST_NAME} ({err})"], []

    problems: list[str] = []
    notes: list[str] = []
    unsafe = [path for path in files if _unsafe(str(path))]
    problems += _named("unsafe manifest path", unsafe)
    problems += _named(f"unsafe {NOT_COVERED_KEY} path", [path for path in outside if _unsafe(path)])
    outside = [path for path in outside if not _unsafe(path)]
    both = [path for path in files if path not in unsafe and _under(str(path), outside)]
    problems += _named(f"listed and declared {NOT_COVERED_KEY}", both)
    notes += _named("not covered by the manifest", outside)
    listed = {path: digest for path, digest in files.items() if path not in unsafe and path not in both}

    present = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
    missing = [path for path in listed if path not in present]
    changed = [path for path in listed if path in present and _sha256(root / path) != listed[path]]
    extra = [
        path
        for path in present
        if path not in listed and path not in UNHASHED and not _under(path, outside)
    ]
    checkpoints = [path for path in extra if TOLERATED_FOLDER in PurePosixPath(path).parts[:-1]]
    tolerated = [
        path for path in extra if PurePosixPath(path).name == TOLERATED and path not in checkpoints
    ]
    problems += _named("missing file", missing)
    problems += _named("changed file", changed)
    problems += _named(
        "extra file", [path for path in extra if path not in tolerated and path not in checkpoints]
    )
    notes += _named(f"tolerated {TOLERATED}", tolerated)
    notes += _named(f"tolerated {TOLERATED_FOLDER}", checkpoints)

    checksums_path = root / CHECKSUMS_NAME
    if not checksums_path.is_file():
        problems.append(f"missing file: {CHECKSUMS_NAME}")
    else:
        sums, malformed = _read_checksums(checksums_path)
        problems += _named(f"malformed {CHECKSUMS_NAME} line", malformed)
        disagree = [p for p in set(sums) | set(files) if sums.get(p) != files.get(p)]
        problems += _named(f"{CHECKSUMS_NAME} disagrees with {MANIFEST_NAME}", disagree)

    for half, directory in sorted(halves.items()):
        expected = counts.get(half, {}).get("total") if isinstance(counts.get(half), dict) else None
        found = len(list((root / str(directory)).glob("*.png")))
        if expected != found:
            problems.append(
                f"count mismatch: {directory}/ holds {found} images, the manifest says {expected}"
            )
    for name, directory in sorted(typeset_sets.items()):
        expected = counts.get(name, {}).get("total") if isinstance(counts.get(name), dict) else None
        images = {p.stem for p in (root / str(directory)).glob("*.png")}
        pdfs = {p.stem for p in (root / str(directory)).glob("*.pdf")}
        if not len(images) == len(pdfs) == expected:
            problems.append(
                f"count mismatch: {directory}/ holds {len(images)} images and {len(pdfs)} PDFs, "
                f"the manifest says {expected} of each"
            )
        problems += _named("unpaired image", [f"{directory}/{stem}.png" for stem in images - pdfs])
        problems += _named("unpaired PDF", [f"{directory}/{stem}.pdf" for stem in pdfs - images])
    return problems, notes


def verify(snapshot_root: Path) -> list[str]:
    """Return every problem found (empty when the snapshot matches its manifest)."""
    return inspect(snapshot_root)[0]


def main() -> int:
    """Verify the snapshot named on the command line and return a shell exit status."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "snapshot_dir",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="the snapshot directory (default: the parent of this script's directory)",
    )
    args = parser.parse_args()
    problems, notes = inspect(args.snapshot_dir)
    for note in notes:
        print(f"note: {note}")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"FAIL: {len(problems)} problem(s) in {args.snapshot_dir.resolve().name}", file=sys.stderr)
        return 1
    count = len(json.loads((args.snapshot_dir / MANIFEST_NAME).read_text())["files"])
    print(f"PASS: {count} files match {MANIFEST_NAME} in {args.snapshot_dir.resolve().name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
