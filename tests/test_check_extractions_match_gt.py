"""`scripts/check_extractions_match_gt.py`, the preflight every snapshot ships (Decision 12).

Run as a subprocess under the same interpreter, as a user would, on invented filenames.
"""

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import REPO_ROOT, assert_diagnostic_error

SCRIPT = REPO_ROOT / "scripts" / "check_extractions_match_gt.py"


def _run(*args: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def _extractions(tmp_path: Path, names: list[str], *, errors: int = 0) -> Path:
    path = tmp_path / "raw_extractions.jsonl"
    rows = [{"image_name": name} for name in names] + [
        {"filename": "X9_receipt.png", "error": "boom"}
    ] * errors
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _ground_truth_csv(tmp_path: Path, names: list[str], column: str = "image_file") -> Path:
    path = tmp_path / "ground_truth.csv"
    path.write_text(f"{column},TOTAL_AMOUNT\n" + "".join(f"{name},$1.00\n" for name in names))
    return path


def test_a_full_match_passes(tmp_path: Path) -> None:
    names = ["Q1_receipt.png", "Q2_invoice.png"]
    result = _run(
        "--extractions", _extractions(tmp_path, names), "--ground-truth", _ground_truth_csv(tmp_path, names)
    )
    assert result.returncode == 0, result.stderr
    assert "PASS: every extracted image has ground truth" in result.stdout
    assert chr(0x2014) not in result.stdout


def test_a_jsonl_ground_truth_is_read(tmp_path: Path) -> None:
    gt = tmp_path / "ground_truth.jsonl"
    gt.write_text(json.dumps({"image_file": "Q1_receipt.png"}) + "\n")
    result = _run("--extractions", _extractions(tmp_path, ["Q1_receipt.png"]), "--ground-truth", gt)
    assert result.returncode == 0, result.stderr


def test_an_extraction_without_ground_truth_fails_four_part(tmp_path: Path) -> None:
    extractions = _extractions(tmp_path, ["Q1_receipt.png", "Q7_receipt.png"], errors=1)
    result = _run(
        "--extractions", extractions, "--ground-truth", _ground_truth_csv(tmp_path, ["Q1_receipt.png"])
    )
    assert result.returncode == 1
    assert_diagnostic_error(result.stderr)
    assert "Q7_receipt.png" in result.stdout and "errored records  : 1" in result.stdout
    assert "--out" not in result.stderr


def test_allow_partial_proceeds(tmp_path: Path) -> None:
    extractions = _extractions(tmp_path, ["Q1_receipt.png", "Q7_receipt.png"])
    gt = _ground_truth_csv(tmp_path, ["Q1_receipt.png"])
    result = _run("--extractions", extractions, "--ground-truth", gt, "--allow-partial")
    assert result.returncode == 0
    assert "PARTIAL: proceeding because --allow-partial was passed." in result.stdout


def test_a_missing_file_fails_four_part(tmp_path: Path) -> None:
    result = _run(
        "--extractions", tmp_path / "absent.jsonl", "--ground-truth", _ground_truth_csv(tmp_path, ["a"])
    )
    assert result.returncode == 1
    assert_diagnostic_error(result.stderr)
    assert "--extractions" in result.stderr


def test_a_ground_truth_with_no_identifier_fails_four_part(tmp_path: Path) -> None:
    gt = _ground_truth_csv(tmp_path, ["Q1_receipt.png"], column="name_of_picture")
    result = _run("--extractions", _extractions(tmp_path, ["Q1_receipt.png"]), "--ground-truth", gt)
    assert result.returncode == 1
    assert_diagnostic_error(result.stderr)
    assert "image_file" in result.stderr and "Traceback" not in result.stderr


def test_an_empty_ground_truth_fails_four_part(tmp_path: Path) -> None:
    gt = tmp_path / "ground_truth.jsonl"
    gt.write_text("")
    result = _run("--extractions", _extractions(tmp_path, ["Q1_receipt.png"]), "--ground-truth", gt)
    assert result.returncode == 1
    assert_diagnostic_error(result.stderr)
    assert "--out" not in result.stderr and "Traceback" not in result.stderr
