#!/usr/bin/env python3
"""Cut a parsing test kit from an evaluation snapshot: the pages, the parsing ground truth and a scorer.

    python scripts/build_parsing_kit.py --snapshot ../evaluation_data/snapshot_20261021

A snapshot serves two benchmarks, field extraction and whole-page parsing. Someone testing a parser needs
only the second, so the kit holds every page image and PDF, `parsing/`, a guide written for a reader with
no access to this repository, and the scoring notebook with the launcher it runs the evaluator through.
It leaves out the extraction answer keys, the field geometry, the label sidecar and the extraction tools.

The kit is written to `<eval_set.output_root>/parsing_kit_<date>/` and zipped beside it. An existing kit
of the same name is replaced. Every file copied from the snapshot is checked against the snapshot's own
manifest as it is copied, and the finished kit is checked with the verifier it ships.

The kit is refused unless `scripts/perfect_score_snapshot.json` records that the launcher passed the
perfect-score check on this snapshot: a scorer is not handed to someone else on trust. The guide's
evidence tables are written from that file, and its counts from the ground truth, so no figure in the
guide is typed by hand.

Two more refusals. The guide's examples may show no real line or cell of the ground truth. And the kit is
scanned, as a snapshot is, for the terms that would say how the pages were made
(`eval_set.forbidden_terms`): its guide, its notebook and its scripts say what the data is and how to use
it, and nothing else.

The kit's manifest lists the pages and the ground truth and nothing else, and declares the guide and
`tools/` outside its cover. Its SHA-256 therefore names the data: it is the same for every kit cut from
one snapshot, and correcting the guide, the notebook or a launcher does not change it.

What the scorer needs is recorded apart, in `tools/SCORER.json`: the SHA-256 of each source file of the
evaluator at the pinned commit, read from the local checkout, and of each launcher file, so that the
notebook can tell its reader whether the evaluator they installed and the launcher they run are the ones
the kit was checked with.

The guide states what a spanning row costs a parser that writes pipe tables, from
`scripts/spanning_rows_as_pipe.json`; the kit is refused without that measurement for this snapshot.
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.snapshot.config import load_snapshot_config
from generators.snapshot.manifest import (
    MANIFEST_NAME,
    hash_files,
    sha256_file,
    write_checksums,
    write_manifest,
)
from generators.snapshot.publication import find_forbidden
from scripts.omnidocbench_eval import EvaluatorError, ensure_checkout
from scripts.perfect_score_snapshot import LAUNCHERS, RESULTS, TABLE_FORMS, TRANSCRIPTIONS
from scripts.spanning_rows_as_pipe import RESULTS as SPANNING_RESULTS
from scripts.verify_snapshot import NOT_COVERED_KEY, inspect

KIT_PREFIX = "parsing_kit"
KIT_SOURCES = REPO_ROOT / "parsing_kit"
README_TEMPLATE = KIT_SOURCES / "README_template.md"
# What goes into the kit's tools/ folder, and where each file comes from.
TOOLS = {
    "score_parsing.ipynb": KIT_SOURCES / "score_parsing.ipynb",
    "omnidocbench_env.yml": KIT_SOURCES / "omnidocbench_env.yml",
    "omnidocbench_formula_free.py": REPO_ROOT / "scripts" / "omnidocbench_formula_free.py",
    "omnidocbench_corrected.py": REPO_ROOT / "scripts" / "omnidocbench_corrected.py",
    "pipe_tables.py": REPO_ROOT / "scripts" / "pipe_tables.py",
    "verify_snapshot.py": REPO_ROOT / "scripts" / "verify_snapshot.py",
}
# What the kit's manifest does not cover: the guide and the scorer, which may be corrected without the
# data changing.
NOT_COVERED = ("README.md", "tools")
# The scorer's own record, in tools/, and the launcher files whose SHA-256 it lists.
SCORER_NAME = "SCORER.json"
LAUNCHER_FILES = ("omnidocbench_formula_free.py", "omnidocbench_corrected.py", "pipe_tables.py")
PAGE_SUFFIXES = (".png", ".pdf")
# The manifest keys the kit keeps from the snapshot's: what the shipped verifier and the notebook read.
KEPT_KEYS = (
    "counts",
    "degradation_tiers",
    "halves",
    "parsing",
    "snapshot_date",
    "snapshot_dir",
    "typeset_raster_dpi",
    "typeset_sets",
)
SET_DESCRIPTIONS = {
    "plain": "Each document as laid out, with nothing added. The control.",
    "distractor": "The plain pages with realistic clutter added (below).",
    "labelled_varied": "The distractor pages with the labels beside the ABN, the GST amount and the total "
    "reworded or removed. The values themselves are unchanged.",
    "degraded": "Each distractor receipt photographed at three severities (below).",
    "typeset_plain": "The plain documents as native PDFs, each beside a PNG of its page (below).",
    "typeset_distractor": "The distractor documents as native PDFs, each beside a PNG of its page.",
}
CATEGORIES = ("title", "text_block", "table", "header", "footer", "figure", "abandon")
DOC_TYPES = {"bank_statement": "Bank statement", "invoice": "Tax invoice", "receipt": "Receipt"}
_SCORES = ("text_block_edit_dist", "table_teds", "table_edit_dist", "reading_order_edit_dist")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")  # fmt: skip
_TOKEN = re.compile(r"\{\{[A-Z_]+\}\}")
_FENCE = re.compile(r"```(markdown|json)\n(.*?)```", re.DOTALL)


class KitError(RuntimeError):
    """The kit cannot be built from what is on disk."""


def _refuse(title: str, *, what: str, where: str, expected: str, recover: str) -> KitError:
    """Build a four-part refusal, each part on a labelled line."""
    return KitError(
        f"{title}\n  What:     {what}\n  Where:    {where}\n  Expected: {expected}\n  Recover:  {recover}"
    )


def written_date(iso: str) -> str:
    """An ISO date as it is written in prose: `2026-10-21` becomes `21 October 2026`."""
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"


def set_directories(manifest: dict) -> dict:
    """Every set's name and directory, the four halves first and then the typeset sets."""
    order = [
        name for name in SET_DESCRIPTIONS if name in {**manifest["halves"], **manifest["typeset_sets"]}
    ]
    return {name: {**manifest["halves"], **manifest["typeset_sets"]}[name] for name in order}


def passed_check(snapshot_name: str, *, results: Path) -> dict:
    """Read the perfect-score results, refusing any that do not show this snapshot passing in full.

    Args:
        snapshot_name: The snapshot the kit is cut from.
        results: `scripts/perfect_score_snapshot.json`.

    Returns:
        The parsed results.

    Raises:
        KitError: The file is absent, is for another snapshot, records a failure or lacks a run.
    """
    recover = (
        "run `python scripts/perfect_score_snapshot.py --snapshot <the snapshot> --workers 4` and "
        "build the kit once it exits 0."
    )
    if not results.is_file():
        raise _refuse(
            "The perfect-score check has not been run.",
            what=f"{results} does not exist.",
            where=str(results),
            expected=f'the check\'s results for {snapshot_name}, with "failures": [].',
            recover=recover,
        )
    check = json.loads(results.read_text())
    if check["snapshot"] != snapshot_name:
        raise _refuse(
            "The perfect-score check was run on another snapshot.",
            what=f"the results are for {check['snapshot']}, and the kit is cut from {snapshot_name}.",
            where=f"{results} -> snapshot",
            expected=f'"snapshot": "{snapshot_name}".',
            recover=recover,
        )
    if check["failures"]:
        raise _refuse(
            "The launcher failed the perfect-score check, so it is not fit to hand over.",
            what="; ".join(check["failures"]),
            where=f"{results} -> failures",
            expected='"failures": [].',
            recover="correct the launcher, then " + recover,
        )
    return check


def run_of(check: dict, *, set_name: str, transcription: str, tables: str, launcher: str) -> dict:
    """The one run of the check with these four names.

    Raises:
        KitError: The results hold no such run, or more than one.
    """
    found = [
        run
        for run in check["runs"]
        if (run["set"], run["transcription"], run["tables"], run["launcher"])
        == (set_name, transcription, tables, launcher)
    ]
    if len(found) != 1:
        raise _refuse(
            "The perfect-score results do not cover every set.",
            what=f"{len(found)} runs for set {set_name}, {transcription}, {tables} tables, {launcher}.",
            where=f"{RESULTS} -> runs",
            expected=f"exactly one run for each set, each of {TRANSCRIPTIONS}, {TABLE_FORMS} and {LAUNCHERS}.",
            recover="run scripts/perfect_score_snapshot.py again on the whole snapshot.",
        )
    return found[0]


def _number(value: float) -> str:
    """A score as the guide prints it: three decimals, and a whole number without any."""
    return str(int(value)) if value == int(value) else f"{value:.3f}"


def _table(header: list, rows: list) -> str:
    """A markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return "\n".join(lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows])


def sets_table(manifest: dict) -> str:
    """The rows of the guide's table of sets (its header is in the template)."""
    rows = []
    for name, directory in set_directories(manifest).items():
        counts = manifest["counts"][name]
        by_type = ", ".join(
            f"{n} {DOC_TYPES[t].lower()}s" for t, n in sorted(counts["by_document_type"].items())
        )
        rows.append(
            f"| `{directory}/`, `{name}` | {counts['total']} | {by_type} | {SET_DESCRIPTIONS[name]} |"
        )
    return "\n".join(rows)


def sizes_table(pages_by_set: dict) -> str:
    """The rows of the guide's table of page sizes: per set and document type, the range of each side."""

    def span(values: list) -> str:
        return str(min(values)) if min(values) == max(values) else f"{min(values)} to {max(values)}"

    rows = []
    for name, pages in pages_by_set.items():
        for doc_type, label in DOC_TYPES.items():
            infos = [
                p["page_info"] for p in pages if p["page_info"]["page_attribute"]["doc_type"] == doc_type
            ]
            if infos:
                widths, heights = [i["width"] for i in infos], [i["height"] for i in infos]
                rows.append(f"| `{name}` | {label} | {span(widths)} | {span(heights)} |")
    return "\n".join(rows)


def blocks_table(pages_by_set: dict) -> str:
    """How many blocks of each category each set holds, and how many are marked `ignore`."""
    rows = []
    for name, pages in pages_by_set.items():
        blocks = [block for page in pages for block in page["layout_dets"]]
        counts = [sum(block["category_type"] == category for block in blocks) for category in CATEGORIES]
        rows.append([f"`{name}`", *counts, sum(bool(block["ignore"]) for block in blocks)])
    return _table(["Set", *(f"`{category}`" for category in CATEGORIES), "marked `ignore`"], rows)


def density_table(pages_by_set: dict) -> str:
    """The mean scored text blocks and table cells of a page, by document type, for the two image sets."""
    rows = []
    for doc_type, label in DOC_TYPES.items():
        row = [label]
        for name in ("plain", "distractor"):
            pages = [
                p for p in pages_by_set[name] if p["page_info"]["page_attribute"]["doc_type"] == doc_type
            ]
            if not pages:
                break
            texts = sum(
                b["category_type"] in ("title", "text_block") for p in pages for b in p["layout_dets"]
            )
            cells = sum(b.get("html", "").count("<td") for p in pages for b in p["layout_dets"])
            row += [str(round(texts / len(pages))), str(round(cells / len(pages)))]
        else:
            rows.append(row)
    header = [
        "Document type",
        "Text blocks, plain",
        "Table cells, plain",
        "Text blocks, distractor",
        "Table cells, distractor",
    ]
    return _table(header, rows)


def check_complete_table(check: dict, names: list) -> str:
    """What a complete transcription scored, as published and corrected, per set and table form."""
    rows = []
    for name in names:
        for tables in TABLE_FORMS:
            row = [f"`{name}`", tables]
            for launcher in LAUNCHERS:
                run = run_of(
                    check, set_name=name, transcription="complete", tables=tables, launcher=launcher
                )
                row += [_number(run[score]) for score in _SCORES]
            rows.append(row)
    scores = ["text edit", "TEDS", "table edit", "reading order"]
    header = [
        "Set",
        "Tables written as",
        *(f"As published: {s}" for s in scores),
        *(f"Corrected: {s}" for s in scores),
    ]
    return _table(header, rows)


def check_left_out_table(check: dict, names: list) -> str:
    """With one line left out of every page: how many pages were charged exactly that line's characters."""
    rows = []
    for name in names:
        for tables in TABLE_FORMS:
            row = [f"`{name}`", tables]
            for launcher in LAUNCHERS:
                run = run_of(
                    check,
                    set_name=name,
                    transcription="one_line_left_out",
                    tables=tables,
                    launcher=launcher,
                )
                row += [
                    f"{run['pages_charged_exactly_the_line']} of {run['pages']}",
                    _number(run["text_block_edit_dist"]),
                ]
            rows.append(row)
    header = [
        "Set",
        "Tables written as",
        "As published: pages charged exactly the missing line",
        "As published: text edit",
        "Corrected: pages charged exactly the missing line",
        "Corrected: text edit",
    ]
    return _table(header, rows)


def measured_spanning_rows(snapshot_name: str, *, results: Path) -> dict:
    """Read what spanning rows cost as pipe-table rows, refusing a measurement that is not this snapshot's.

    Args:
        snapshot_name: The snapshot the kit is cut from.
        results: `scripts/spanning_rows_as_pipe.json`.

    Returns:
        The parsed measurement.

    Raises:
        KitError: The file is absent, is for another snapshot or records a failure.
    """
    recover = (
        "run `python scripts/spanning_rows_as_pipe.py --snapshot <the snapshot> --workers 3` and build "
        "the kit once it exits 0."
    )
    if not results.is_file():
        raise _refuse(
            "What a spanning row costs as a pipe-table row has not been measured.",
            what=f"{results} does not exist, and the guide states that cost.",
            where=str(results),
            expected=f'the measurement for {snapshot_name}, with "failures": [].',
            recover=recover,
        )
    measured = json.loads(results.read_text())
    if measured["snapshot"] != snapshot_name or measured["failures"]:
        raise _refuse(
            "The measurement of spanning rows is not a clean one of this snapshot.",
            what=f"it is for {measured['snapshot']}, with failures {measured['failures']}; the kit is cut "
            f"from {snapshot_name}.",
            where=f"{results} -> snapshot, failures",
            expected=f'"snapshot": "{snapshot_name}" and "failures": [].',
            recover=recover,
        )
    return measured


def spanning_rows_table(measured: dict, names: list) -> str:
    """Per set: the tables with a spanning row and what they score with that row as an ordinary one.

    Raises:
        KitError: The measurement lacks a set of the kit.
    """
    by_set = {record["set"]: record for record in measured["sets"]}
    absent = [name for name in names if name not in by_set]
    if absent:
        raise _refuse(
            "The measurement of spanning rows does not cover every set.",
            what=f"no record for {absent}.",
            where=f"{SPANNING_RESULTS} -> sets",
            expected=f"one record for each of {names}.",
            recover="run scripts/spanning_rows_as_pipe.py again on the whole snapshot.",
        )
    rows, without = [], []
    for name in names:
        record = by_set[name]
        if not record["tables_with_spanning_rows"]:
            without.append(f"`{name}`")
            continue
        rows.append(
            [
                f"`{name}`",
                f"{record['tables_with_spanning_rows']} of {record['tables']}",
                (
                    f"{record['their_teds_mean']:.3f} ({record['their_teds_lowest']:.3f} to "
                    f"{record['their_teds_highest']:.3f})"
                ),
                _number(record["table_teds"]),
                _number(record["table_teds_structure_only"]),
                _number(record["table_edit_dist"]),
            ]
        )
    header = [
        "Set",
        "Tables with date rows",
        "TEDS of those tables: mean (lowest to highest)",
        "TEDS of the whole set",
        "TEDS, structure only, of the whole set",
        "Table edit distance of the whole set",
    ]
    table = _table(header, rows)
    if without:
        table += f"\n\nNo table of {', '.join(without)} has a spanning cell."
    return table


def render_readme(
    template: str, *, manifest: dict, pages_by_set: dict, check: dict, spanning: dict, kit_dir: str
) -> str:
    """Fill the guide's template from the snapshot's manifest, its ground truth and the stored results.

    Raises:
        KitError: The template holds a token this function does not fill.
    """
    names = list(pages_by_set)
    values = {
        "KIT_DIR": kit_dir,
        "SNAPSHOT": manifest["snapshot_dir"],
        "SNAPSHOT_DATE": written_date(manifest["snapshot_date"]),
        "DATE_TAG": manifest["snapshot_date"].replace("-", ""),
        "EVALUATOR_COMMIT": manifest["parsing"]["evaluator"]["commit"],
        "TYPESET_DPI": str(manifest["typeset_raster_dpi"]),
        "SETS_TABLE": sets_table(manifest),
        "SIZES_TABLE": sizes_table(pages_by_set),
        "BLOCKS_TABLE": blocks_table(pages_by_set),
        "DENSITY_TABLE": density_table(pages_by_set),
        "CHECK_DATE": written_date(check["measured"]),
        "CHECK_COMPLETE_TABLE": check_complete_table(check, names),
        "CHECK_LEFT_OUT_TABLE": check_left_out_table(check, names),
        "SPANNING_DATE": written_date(spanning["measured"]),
        "SPANNING_ROWS_TABLE": spanning_rows_table(spanning, names),
    }
    text = template
    for token, value in values.items():
        text = text.replace("{{" + token + "}}", value)
    unfilled = sorted(set(_TOKEN.findall(text)))
    if unfilled:
        raise _refuse(
            "The guide's template holds a token the builder does not fill.",
            what=f"{unfilled} would be printed as they stand.",
            where=str(README_TEMPLATE),
            expected=f"only the tokens {sorted('{{' + token + '}}' for token in values)}.",
            recover="remove the token from the template, or give it a value in render_readme.",
        )
    return text


def example_strings(readme: str) -> set:
    """Every piece of example content the guide shows in a fenced markdown or JSON block.

    A markdown example gives its lines and its table cells; a JSON example gives each `text` value and each
    cell of each `html` value. Heading marks and surrounding spaces are dropped, and case is ignored.
    """
    found = set()
    for language, body in _FENCE.findall(readme):
        if language == "json":
            lines = re.findall(r'"text": "(.*?)"', body) + re.findall(r"<td[^>]*>(.*?)</td>", body)
        else:
            lines = [
                cell
                for line in body.splitlines()
                for cell in ([line] if "|" not in line else line.split("|"))
            ]
        found |= {line.strip().lstrip("#").strip().lower() for line in lines}
    return {text for text in found if text and not re.fullmatch(r"[-:| ]*", text)}


def ground_truth_strings(pages_by_set: dict) -> set:
    """Every block's text and every table cell of the ground truth, with case ignored."""
    found = set()
    for pages in pages_by_set.values():
        for page in pages:
            for block in page["layout_dets"]:
                found.add(block.get("text", "").strip().lower())
                found |= {
                    cell.strip().lower()
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", block.get("html", ""))
                }
    return found - {""}


def refuse_real_examples(readme: str, pages_by_set: dict) -> None:
    """Refuse a guide whose examples show content of the ground truth.

    The guide is read by the people whose parsers are scored, and may be pasted into a parser's prompt, so
    an example that shows a real line or cell hands over part of an answer.

    Raises:
        KitError: An example line or cell equals a block's text or a table cell of the ground truth.
    """
    leaked = sorted(example_strings(readme) & ground_truth_strings(pages_by_set))
    if leaked:
        raise _refuse(
            "The guide's examples show real content of the ground truth.",
            what=f"{leaked} each equal a block's text or a table cell of the snapshot.",
            where=f"{README_TEMPLATE}, in a fenced markdown or json block",
            expected="invented example content that appears nowhere in parsing/*.json.",
            recover="replace each with an invented value and build again.",
        )


def copy_verified(source: Path, target: Path, *, expected: str, relative: str) -> None:
    """Copy one snapshot file into the kit, refusing one that differs from the snapshot's manifest."""
    if sha256_file(source) != expected:
        raise _refuse(
            "A snapshot file differs from the snapshot's own manifest.",
            what=f"{relative} does not have the SHA-256 the manifest lists.",
            where=str(source),
            expected=f"SHA-256 {expected}.",
            recover="verify the snapshot with its tools/verify_snapshot.py, and export it again if it fails.",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def evaluator_files(checkout: Path) -> dict:
    """The SHA-256 of each source file of the evaluator: its entry script and every module under `src/`.

    Args:
        checkout: The evaluator checkout at the pinned commit, as `ensure_checkout` returns it.

    Returns:
        POSIX path relative to the checkout -> hex SHA-256, sorted by path.
    """
    paths = [checkout / "pdf_validation.py", *(checkout / "src").rglob("*.py")]
    return {path.relative_to(checkout).as_posix(): sha256_file(path) for path in sorted(paths)}


def build(
    snapshot: Path,
    *,
    output_root: Path,
    results: Path,
    spanning_results: Path,
    checkout: Path,
    commit: str,
    forbidden_terms: tuple,
) -> tuple:
    """Build the kit directory and its zip, and return both paths.

    Args:
        snapshot: The snapshot directory the kit is cut from.
        output_root: Where the kit and its zip are written.
        results: The perfect-score results the kit is conditional on.
        spanning_results: The measurement of what spanning rows cost as pipe-table rows.
        checkout: The evaluator checkout whose source files the scorer's record lists.
        commit: The commit that checkout is at.
        forbidden_terms: The terms no file of the kit may hold.

    Raises:
        KitError: The check has not passed, the spanning rows are not measured, the snapshot's ground
            truth is for another evaluator commit, a snapshot file is wrong, the guide leaks real content,
            the kit says how the pages were made, or the finished kit fails its own verifier.
    """
    source = json.loads((snapshot / MANIFEST_NAME).read_text())
    named = source["parsing"]["evaluator"]["commit"]
    if named != commit:
        raise _refuse(
            "The snapshot's ground truth was written for another evaluator commit.",
            what=f"the snapshot names {named}, and the evaluator checkout is at {commit}.",
            where=f"{snapshot / MANIFEST_NAME} -> parsing.evaluator.commit; {PARSING_CONFIG_PATH} -> "
            "evaluator.commit",
            expected="the same 40-character commit in both.",
            recover="cut the kit from a snapshot exported with the current evaluator pin, or export one.",
        )
    check = passed_check(source["snapshot_dir"], results=results)
    spanning = measured_spanning_rows(source["snapshot_dir"], results=spanning_results)
    directories = set_directories(source)
    parsing_dir = source["parsing"]["dir"]
    pages_by_set = {
        name: json.loads((snapshot / parsing_dir / f"{name}.json").read_text()) for name in directories
    }
    kit_dir = f"{KIT_PREFIX}_{source['snapshot_date'].replace('-', '')}"
    readme = render_readme(
        README_TEMPLATE.read_text(),
        manifest=source,
        pages_by_set=pages_by_set,
        check=check,
        spanning=spanning,
        kit_dir=kit_dir,
    )
    refuse_real_examples(readme, pages_by_set)

    kit = output_root / kit_dir
    shutil.rmtree(kit, ignore_errors=True)
    kit.mkdir(parents=True)
    wanted = [
        relative
        for relative in source["files"]
        if (relative.split("/")[0] in directories.values() and relative.endswith(PAGE_SUFFIXES))
        or relative.split("/")[0] == parsing_dir
    ]
    for relative in wanted:
        copy_verified(
            snapshot / relative, kit / relative, expected=source["files"][relative], relative=relative
        )
    (kit / "tools").mkdir()
    for name, origin in TOOLS.items():
        shutil.copyfile(origin, kit / "tools" / name)
    (kit / "README.md").write_text(readme)
    scorer = {
        "evaluator_commit": commit,
        "perfect_score_check": {
            "snapshot": check["snapshot"],
            "measured": check["measured"],
            "evaluator_commit": check["evaluator_commit"],
        },
        "launcher_files": {name: sha256_file(kit / "tools" / name) for name in LAUNCHER_FILES},
        "evaluator_files": evaluator_files(checkout),
    }
    (kit / "tools" / SCORER_NAME).write_text(json.dumps(scorer, indent=2) + "\n")

    # The manifest names the data and nothing that can be corrected without the data changing, so that
    # its own SHA-256 is the same for every kit cut from this snapshot.
    manifest = {key: source[key] for key in KEPT_KEYS}
    manifest["kit_dir"] = kit_dir
    manifest["cut_from"] = {
        "snapshot_dir": source["snapshot_dir"],
        "manifest_sha256": sha256_file(snapshot / MANIFEST_NAME),
    }
    manifest[NOT_COVERED_KEY] = list(NOT_COVERED)
    manifest["files"] = {
        relative: digest
        for relative, digest in hash_files(kit).items()
        if relative.split("/")[0] not in NOT_COVERED
    }
    write_manifest(manifest, kit)
    write_checksums(manifest, kit)
    hits = find_forbidden(kit, forbidden_terms)
    if hits:
        raise _refuse(
            "The kit says how the pages were made, so it was not zipped.",
            what=f"{len(hits)} forbidden term(s): " + "; ".join(hits[:10]),
            where=f"{kit}, against eval_set.forbidden_terms in the generation configuration",
            expected="files that say what the data is and how to use it, and nothing about what made it.",
            recover="reword the source of each hit: the guide's template, the notebook or a shipped script.",
        )
    problems, _notes = inspect(kit)
    if problems:
        raise _refuse(
            "The finished kit fails its own verifier.",
            what="; ".join(problems[:10]),
            where=str(kit),
            expected="no problem from tools/verify_snapshot.py.",
            recover="read the problems above; the kit directory is left in place for inspection.",
        )

    archive = output_root / f"{kit_dir}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(kit.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(kit_dir) / path.relative_to(kit))
    return kit, archive


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True, help="The snapshot directory to cut from")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "generation_config.yml")
    args = parser.parse_args(argv)
    config = load_snapshot_config(args.config)
    pin = load_parsing_config(PARSING_CONFIG_PATH)
    try:
        kit, archive = build(
            args.snapshot.resolve(),
            output_root=config.output_root,
            results=RESULTS,
            spanning_results=SPANNING_RESULTS,
            checkout=ensure_checkout(pin, root=config.output_root / "tools"),
            commit=pin.evaluator.commit,
            forbidden_terms=config.forbidden_terms,
        )
    except (KitError, EvaluatorError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    files = json.loads((kit / MANIFEST_NAME).read_text())["files"]
    print(f"Kit: {kit} ({len(files)} pages and ground-truth files in the manifest)")
    print(f"Zip: {archive} ({archive.stat().st_size / 1e6:.0f} MB)")
    print(f"SHA-256 of {MANIFEST_NAME}, which names the data: {sha256_file(kit / MANIFEST_NAME)}")
    print(f"SHA-256 of the zip, which changes with any file in it: {sha256_file(archive)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
