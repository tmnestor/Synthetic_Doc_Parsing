"""The parsing kit: what it holds, what it leaves out, and what stops it being built.

A small stand-in snapshot is written into `tmp_path` with the keys and files of a real one. Every name
and line of text in it is invented for the test.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pypdfium2
import pytest

from scripts import build_parsing_kit
from scripts.build_parsing_kit import (
    README_TEMPLATE,
    KitError,
    build,
    example_strings,
    render_readme,
    written_date,
)
from scripts.perfect_score_snapshot import LAUNCHERS, TABLE_FORMS, TRANSCRIPTIONS
from scripts.verify_snapshot import inspect
from tests.conftest import assert_diagnostic_error

SETS = {
    "plain": "plain_20310102",
    "distractor": "distractor_20310102",
    "typeset_plain": "typeset_plain_20310102",
}
TABLE = "<table><tr><td>Sprocket</td><td>$8.20</td></tr></table>"


def _page(directory: str, text: str) -> dict:
    return {
        "page_info": {
            "page_no": 0,
            "width": 300,
            "height": 500,
            "image_path": f"{directory}/CASE901_receipt.png",
            "page_attribute": {
                "language": "en",
                "doc_type": "receipt",
                "layout_id": "receipt_layout_1",
                "half": directory.rsplit("_", 1)[0],
                "tier": "none",
                "layout_scored": True,
            },
        },
        "layout_dets": [
            {"category_type": "text_block", "order": 1, "ignore": False, "text": text},
            {"category_type": "table", "order": 2, "ignore": False, "html": TABLE},
        ],
        "extra": {"relation": []},
    }


def _blank_pdf() -> bytes:
    """A one-page PDF with nothing on it: the kit's scan opens every PDF it holds."""
    document = pypdfium2.PdfDocument.new()
    document.new_page(200, 300)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def _snapshot(root: Path, *, text: str = "Marrowbone Tackle Supplies") -> Path:
    snapshot = root / "snapshot_20310102"
    files = {
        "README.md": b"the snapshot's own guide\n",
        "geometry/plain.jsonl": b"{}\n",
        "tools/extraction_tool.py": b"\n",
    }
    for name, directory in SETS.items():
        files[f"{directory}/CASE901_receipt.png"] = f"png of {name}".encode()
        files[f"{directory}/ground_truth.csv"] = b"image_file,TOTAL_AMOUNT\n"
        files[f"parsing/{name}.json"] = json.dumps([_page(directory, text)]).encode()
    files[f"{SETS['typeset_plain']}/CASE901_receipt.pdf"] = _blank_pdf()
    for relative, content in files.items():
        (snapshot / relative).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / relative).write_bytes(content)
    manifest = {
        "counts": {name: {"by_document_type": {"receipt": 1}, "total": 1} for name in SETS},
        "degradation_tiers": [],
        "files": {relative: hashlib.sha256(content).hexdigest() for relative, content in files.items()},
        "geometry_dir": "geometry",
        "ground_truth_files": ["ground_truth.csv"],
        "halves": {name: SETS[name] for name in ("plain", "distractor")},
        "parsing": {
            "dir": "parsing",
            "evaluator": {"commit": "c" * 40, "repository": "https://example.invalid/e"},
        },
        "snapshot_date": "2031-01-02",
        "snapshot_dir": "snapshot_20310102",
        "typeset_raster_dpi": 300,
        "typeset_sets": {"typeset_plain": SETS["typeset_plain"]},
    }
    (snapshot / "MANIFEST.json").write_text(json.dumps(manifest))
    return snapshot


def _results(root: Path, **changes: object) -> Path:
    runs = []
    for name in SETS:
        for transcription in TRANSCRIPTIONS:
            for tables in TABLE_FORMS:
                for launcher in LAUNCHERS:
                    run = {
                        "set": name,
                        "pages": 1,
                        "transcription": transcription,
                        "tables": tables,
                        "launcher": launcher,
                        "text_block_edit_dist": 0.0 if launcher == "formula_free" else 0.125,
                        "table_teds": 1.0,
                        "table_teds_structure_only": 1.0,
                        "table_edit_dist": 0.0,
                        "reading_order_edit_dist": 0.0,
                    }
                    if transcription == "one_line_left_out":
                        run["pages_charged_exactly_the_line"] = 1
                    runs.append(run)
    check = {
        "snapshot": "snapshot_20310102",
        "measured": "2031-01-03",
        "evaluator_commit": "c" * 40,
        "failures": [],
        "runs": runs,
        **changes,
    }
    path = root / "perfect_score_snapshot.json"
    path.write_text(json.dumps(check))
    return path


def _evaluator(
    root: Path, *, forbidden_terms: tuple = ("Marrowbone_Forge",), commit: str = "c" * 40
) -> dict:
    """A stand-in evaluator checkout, with the arguments `build` takes about it."""
    checkout = root / "evaluator"
    (checkout / "src" / "core").mkdir(parents=True, exist_ok=True)
    (checkout / "pdf_validation.py").write_text("entry = 1\n")
    (checkout / "src" / "core" / "match.py").write_text("matcher = 1\n")
    (checkout / "src" / "notes.txt").write_text("not a source file\n")
    return {"checkout": checkout, "commit": commit, "forbidden_terms": forbidden_terms}


def test_a_date_is_written_day_month_year() -> None:
    assert written_date("2031-01-02") == "2 January 2031"


def test_the_kit_holds_the_pages_the_ground_truth_and_the_scorer_and_no_extraction_file(
    tmp_path: Path,
) -> None:
    kit, archive = build(
        _snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path)
    )

    assert kit == tmp_path / "parsing_kit_20310102"
    held = sorted(path.relative_to(kit).as_posix() for path in kit.rglob("*") if path.is_file())
    assert held == sorted(
        [
            "MANIFEST.json",
            "README.md",
            "SHA256SUMS",
            *(f"{directory}/CASE901_receipt.png" for directory in SETS.values()),
            f"{SETS['typeset_plain']}/CASE901_receipt.pdf",
            *(f"parsing/{name}.json" for name in SETS),
            *(f"tools/{name}" for name in build_parsing_kit.TOOLS),
        ]
    )
    assert inspect(kit) == ([], [])
    with zipfile.ZipFile(archive) as bundle:
        assert sorted(bundle.namelist()) == [f"parsing_kit_20310102/{path}" for path in held]

    readme = (kit / "README.md").read_text()
    assert "{{" not in readme
    assert "snapshot `snapshot_20310102`" in readme and "(2 January 2031)" in readme
    assert "| `plain` | html | 0.125 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |" in readme
    assert "| `plain` | pipe | 1 of 1 | 0.125 | 1 of 1 | 0 |" in readme
    manifest = json.loads((kit / "MANIFEST.json").read_text())
    assert manifest["parsing"]["evaluator"]["commit"] == "c" * 40
    assert "geometry_dir" not in manifest and "ground_truth_files" not in manifest
    assert manifest["evaluator_files"] == {
        "pdf_validation.py": hashlib.sha256(b"entry = 1\n").hexdigest(),
        "src/core/match.py": hashlib.sha256(b"matcher = 1\n").hexdigest(),
    }


def test_a_kit_that_says_how_the_pages_were_made_is_not_zipped(tmp_path: Path) -> None:
    with pytest.raises(KitError) as caught:
        build(
            _snapshot(tmp_path),
            output_root=tmp_path,
            results=_results(tmp_path),
            **_evaluator(tmp_path, forbidden_terms=("verifier",)),
        )
    assert_diagnostic_error(caught.value)
    assert "README.md" in str(caught.value) and "'verifier'" in str(caught.value)
    assert not (tmp_path / "parsing_kit_20310102.zip").exists()


def test_a_snapshot_written_for_another_evaluator_commit_stops_the_build(tmp_path: Path) -> None:
    with pytest.raises(KitError) as caught:
        build(
            _snapshot(tmp_path),
            output_root=tmp_path,
            results=_results(tmp_path),
            **_evaluator(tmp_path, commit="d" * 40),
        )
    assert_diagnostic_error(caught.value)
    assert "d" * 40 in str(caught.value)


def test_an_existing_kit_of_the_same_name_is_replaced(tmp_path: Path) -> None:
    stale = tmp_path / "parsing_kit_20310102" / "left_over.txt"
    stale.parent.mkdir()
    stale.write_text("from an earlier build")
    kit, _archive = build(
        _snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path)
    )
    assert not stale.exists() and inspect(kit) == ([], [])


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"snapshot": "snapshot_20300101"}, "snapshot_20300101"),
        (
            {"failures": ["plain, complete, pipe tables: a complete transcription is not perfect"]},
            "not perfect",
        ),
        ({"runs": []}, "0 runs"),
    ],
)
def test_a_check_that_does_not_show_this_snapshot_passing_stops_the_build(
    tmp_path: Path, changes: dict, named: str
) -> None:
    with pytest.raises(KitError) as caught:
        build(
            _snapshot(tmp_path),
            output_root=tmp_path,
            results=_results(tmp_path, **changes),
            **_evaluator(tmp_path),
        )
    assert_diagnostic_error(caught.value)
    assert named in str(caught.value)
    assert not (tmp_path / "parsing_kit_20310102.zip").exists()


def test_a_check_that_was_never_run_stops_the_build(tmp_path: Path) -> None:
    with pytest.raises(KitError) as caught:
        build(
            _snapshot(tmp_path),
            output_root=tmp_path,
            results=tmp_path / "absent.json",
            **_evaluator(tmp_path),
        )
    assert_diagnostic_error(caught.value)


def test_the_guides_examples_are_found_so_the_leak_check_is_not_vacuous() -> None:
    examples = example_strings(README_TEMPLATE.read_text())
    assert {"quillfeather joinery works", "oak dowel 9mm", "workshop docket", "$4.15"} <= examples
    assert "---" not in examples and "" not in examples


def test_an_example_that_shows_a_real_line_of_the_ground_truth_stops_the_build(tmp_path: Path) -> None:
    leaking = _snapshot(tmp_path, text="Quillfeather Joinery Works")
    with pytest.raises(KitError) as caught:
        build(leaking, output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path))
    assert_diagnostic_error(caught.value)
    assert "quillfeather joinery works" in str(caught.value)
    assert not (tmp_path / "parsing_kit_20310102").exists()


def test_a_snapshot_file_that_differs_from_the_snapshots_manifest_stops_the_build(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    (snapshot / SETS["plain"] / "CASE901_receipt.png").write_bytes(b"another image")
    with pytest.raises(KitError) as caught:
        build(snapshot, output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path))
    assert_diagnostic_error(caught.value)
    assert "CASE901_receipt.png" in str(caught.value)


def test_a_token_the_builder_does_not_fill_is_refused(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    manifest = json.loads((snapshot / "MANIFEST.json").read_text())
    pages = {name: json.loads((snapshot / "parsing" / f"{name}.json").read_text()) for name in SETS}
    check = json.loads(_results(tmp_path).read_text())
    with pytest.raises(KitError) as caught:
        render_readme(
            "{{SNAPSHOT}} and {{NOT_A_TOKEN}}",
            manifest=manifest,
            pages_by_set=pages,
            check=check,
            kit_dir="k",
        )
    assert_diagnostic_error(caught.value)
    assert "NOT_A_TOKEN" in str(caught.value)
