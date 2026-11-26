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
from scripts.transaction_columns_check import FAULTS, SAVED_RUNS
from scripts.verify_snapshot import inspect
from tests.conftest import assert_diagnostic_error

SETS = {
    "plain": "plain_20310102",
    "distractor": "distractor_20310102",
    "typeset_plain": "typeset_plain_20310102",
}
TABLE = "<table><tr><td>Sprocket</td><td>$8.20</td></tr></table>"
# What the verifier notes about a sound kit: the two paths its manifest declares outside its cover.
NOT_COVERED_NOTES = ["not covered by the manifest: README.md", "not covered by the manifest: tools"]


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
    return {
        "checkout": checkout,
        "commit": commit,
        "forbidden_terms": forbidden_terms,
        "spanning_results": _spanning(root),
        "transaction_results": _transactions(root),
    }


def _transactions(root: Path, **changes: object) -> Path:
    """Stand-in results of the transaction check: one set holds a statement, and it passed."""
    judged = {"statements": 1, "rows_with_an_amount": 31, "right": 31, "rows_added": 0}
    proved = {
        "snapshot": "snapshot_20310102",
        "measured": "2031-01-05",
        "failures": [],
        "sets": [
            {
                "set": "plain",
                "statements": 1,
                "read_back": {key: dict(judged) for key in SAVED_RUNS},
                "one_fault": {key: {"judged": 2, "named_that_fault_alone": 2} for key in FAULTS},
            }
        ],
        **changes,
    }
    path = root / "transaction_columns_check.json"
    path.write_text(json.dumps(proved))
    return path


def _spanning(root: Path, **changes: object) -> Path:
    """A stand-in measurement of spanning rows: one set has them, the others have none."""
    measured = {
        "snapshot": "snapshot_20310102",
        "measured": "2031-01-04",
        "failures": [],
        "sets": [
            {
                "set": "plain",
                "pages": 1,
                "tables": 1,
                "tables_with_spanning_rows": 1,
                "their_teds_mean": 0.6704,
                "their_teds_lowest": 0.625,
                "their_teds_highest": 0.75,
                "table_teds": 0.9581,
                "table_teds_structure_only": 0.9581,
                "table_edit_dist": 0.023,
            },
            {"set": "distractor", "pages": 1, "tables_with_spanning_rows": 0},
            {"set": "typeset_plain", "pages": 1, "tables_with_spanning_rows": 0},
        ],
        **changes,
    }
    path = root / "spanning_rows_as_pipe.json"
    path.write_text(json.dumps(measured))
    return path


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
            "tools/SCORER.json",
        ]
    )
    assert inspect(kit) == ([], NOT_COVERED_NOTES)
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
    assert "| `plain` | 1 of 1 | 0.670 (0.625 to 0.750) | 0.958 | 0.958 | 0.023 |" in readme
    assert "No table of `distractor`, `typeset_plain` has a spanning cell." in readme
    assert "on 4 January 2031" in readme
    scorer = json.loads((kit / "tools" / "SCORER.json").read_text())
    assert scorer["evaluator_commit"] == "c" * 40
    assert scorer["perfect_score_check"]["measured"] == "2031-01-03"
    assert scorer["evaluator_files"] == {
        "pdf_validation.py": hashlib.sha256(b"entry = 1\n").hexdigest(),
        "src/core/match.py": hashlib.sha256(b"matcher = 1\n").hexdigest(),
    }
    assert scorer["launcher_files"] == {
        name: hashlib.sha256((kit / "tools" / name).read_bytes()).hexdigest()
        for name in build_parsing_kit.LAUNCHER_FILES
    }
    assert scorer["notebook_files"] == {
        "transaction_columns.py": hashlib.sha256(
            (kit / "tools" / "transaction_columns.py").read_bytes()
        ).hexdigest()
    }
    assert scorer["transaction_check"] == {"snapshot": "snapshot_20310102", "measured": "2031-01-05"}
    assert "| `plain` | 1 | 31 of 31 | 31 of 31 | 31 of 31 | 2 of 2 | 2 of 2 | 2 of 2 |" in readme
    assert "`distractor`, `typeset_plain` holds no bank statement." in readme
    assert "on 5 January 2031" in readme


def test_the_manifest_names_the_pages_and_the_ground_truth_and_nothing_else(tmp_path: Path) -> None:
    """The manifest's own SHA-256 is the data's: no guide, tool or check result can move it."""
    snapshot = _snapshot(tmp_path)
    source = json.loads((snapshot / "MANIFEST.json").read_text())
    kit, _archive = build(
        snapshot, output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path)
    )
    manifest = json.loads((kit / "MANIFEST.json").read_text())
    data = sorted(
        [
            *(f"{directory}/CASE901_receipt.png" for directory in SETS.values()),
            f"{SETS['typeset_plain']}/CASE901_receipt.pdf",
            *(f"parsing/{name}.json" for name in SETS),
        ]
    )
    assert sorted(manifest["files"]) == data
    assert manifest["files"] == {relative: source["files"][relative] for relative in data}
    assert manifest["not_covered"] == ["README.md", "tools"]
    assert not {"evaluator_files", "perfect_score_check"} & set(manifest)
    assert (kit / "SHA256SUMS").read_text().count("\n") == len(data)


def test_correcting_the_guide_or_a_tool_leaves_the_manifest_and_the_verifier_as_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = {"output_root": tmp_path, "results": _results(tmp_path), **_evaluator(tmp_path)}
    kit, _archive = build(_snapshot(tmp_path), **arguments)
    before = (kit / "MANIFEST.json").read_bytes()

    # A reader puts a corrected guide in place, and a notebook checkpoint appears in tools/.
    (kit / "README.md").write_text("a corrected guide\n")
    (kit / "tools" / ".ipynb_checkpoints").mkdir()
    (kit / "tools" / ".ipynb_checkpoints" / "score_parsing-checkpoint.ipynb").write_text("{}")
    assert inspect(kit) == ([], NOT_COVERED_NOTES)

    # A later build, with another guide, another check date and another launcher, has the same manifest.
    template = tmp_path / "template.md"
    template.write_text(README_TEMPLATE.read_text() + "\nA paragraph added to the guide later.\n")
    launcher = tmp_path / "pipe_tables.py"
    launcher.write_text("# a corrected launcher file\n")
    monkeypatch.setattr(build_parsing_kit, "README_TEMPLATE", template)
    monkeypatch.setitem(build_parsing_kit.TOOLS, "pipe_tables.py", launcher)
    arguments["results"] = _results(tmp_path, measured="2031-02-05")
    rebuilt, _archive = build(tmp_path / "snapshot_20310102", **arguments)
    assert (rebuilt / "MANIFEST.json").read_bytes() == before
    assert "A paragraph added to the guide later." in (rebuilt / "README.md").read_text()


def test_a_changed_page_still_fails_the_kits_verifier(tmp_path: Path) -> None:
    kit, _archive = build(
        _snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **_evaluator(tmp_path)
    )
    (kit / "parsing" / "plain.json").write_text("[]")
    (kit / SETS["plain"] / "stray.png").write_bytes(b"an extra page")
    problems, _notes = inspect(kit)
    assert any("changed file: parsing/plain.json" in problem for problem in problems)
    assert any("extra file" in problem and "stray.png" in problem for problem in problems)


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"snapshot": "snapshot_20300101"}, "snapshot_20300101"),
        ({"failures": ["plain: the text or the reading order was charged"]}, "was charged"),
        ({"sets": []}, "no record for"),
    ],
)
def test_a_measurement_of_spanning_rows_that_is_not_this_snapshots_stops_the_build(
    tmp_path: Path, changes: dict, named: str
) -> None:
    arguments = {**_evaluator(tmp_path), "spanning_results": _spanning(tmp_path, **changes)}
    with pytest.raises(KitError) as caught:
        build(_snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **arguments)
    assert_diagnostic_error(caught.value)
    assert named in str(caught.value)
    assert not (tmp_path / "parsing_kit_20310102.zip").exists()


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"snapshot": "snapshot_20300101"}, "snapshot_20300101"),
        ({"failures": ["plain, row_left_out: 1 of 2 tables are not judged"]}, "1 of 2 tables"),
        ({"sets": [{"set": "plain", "statements": 1, "read_back": {}, "one_fault": {}}]}, "no result for"),
    ],
)
def test_a_transaction_check_that_does_not_show_this_snapshot_passing_stops_the_build(
    tmp_path: Path, changes: dict, named: str
) -> None:
    arguments = {**_evaluator(tmp_path), "transaction_results": _transactions(tmp_path, **changes)}
    with pytest.raises(KitError) as caught:
        build(_snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **arguments)
    assert_diagnostic_error(caught.value)
    assert named in str(caught.value)
    assert not (tmp_path / "parsing_kit_20310102.zip").exists()


def test_a_transaction_check_that_was_never_run_stops_the_build(tmp_path: Path) -> None:
    arguments = {**_evaluator(tmp_path), "transaction_results": tmp_path / "absent.json"}
    with pytest.raises(KitError) as caught:
        build(_snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **arguments)
    assert_diagnostic_error(caught.value)
    assert "transaction_columns_check.py" in str(caught.value)


def test_spanning_rows_that_were_never_measured_stop_the_build(tmp_path: Path) -> None:
    arguments = {**_evaluator(tmp_path), "spanning_results": tmp_path / "absent.json"}
    with pytest.raises(KitError) as caught:
        build(_snapshot(tmp_path), output_root=tmp_path, results=_results(tmp_path), **arguments)
    assert_diagnostic_error(caught.value)
    assert "spanning_rows_as_pipe.py" in str(caught.value)


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
    assert not stale.exists() and inspect(kit) == ([], NOT_COVERED_NOTES)


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
            spanning=json.loads(_spanning(tmp_path).read_text()),
            transactions=json.loads(_transactions(tmp_path).read_text()),
            kit_dir="k",
        )
    assert_diagnostic_error(caught.value)
    assert "NOT_A_TOKEN" in str(caught.value)
