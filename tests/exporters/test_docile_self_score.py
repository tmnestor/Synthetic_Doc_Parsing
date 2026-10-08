"""The DocILE export, scored against itself via the published evaluate_dataset,
must be perfect, and the gate must be able to fail.

Dataset is not an in-memory container: it expects an on-disk directory
following DocILE's own layout, ``{root}/annotations/{docid}.json`` and
``{root}/ocr/{docid}.json``, loaded lazily through ``CachedObject.content``.
``Dataset(split_name, root, docids=[...])`` accepts an explicit docid list, so
no index file is required.

``Field.from_dict``'s expected keys: {bbox, page, score, text, fieldtype,
line_item_id, use_only_for_ap} are exactly the keys ``to_docile`` already
emits, so the *same* record dicts serve as both the on-disk gold annotation
(field_extractions / line_item_extractions) and the in-memory ``Field``
predictions. Self-score is therefore a genuine round trip through the
library's own matching code, not a hand-rolled stub of it.

The published matching metric (``docile.evaluation.pcc.pccs_iou``) computes
IoU over OCR-derived Pseudo-Character-Centers (PCCs), not raw bbox overlap.
This project's ground truth carries no independent OCR word stream, so each
document's ``ocr/{docid}.json`` here is built from the *same* field
text+bbox pairs already in the export: one "OCR word" per field, with its
``snapped_geometry`` pre-supplied so no PDF rendering is required.
``_calculate_pccs`` then distributes one pseudo-character-center per
character across each field's own bbox width, so for identical gold and
prediction boxes the covered-PCC sets are identical and the IoU is a
genuinely computed 1.0, not the library's degenerate empty-PCC-set fallback
(with zero PCCs of any kind, *any* two intersecting boxes would score IoU 1
regardless of true overlap, which would make the gate untestable).

A self-score alone (predictions == gold) is near-tautological: it proves the
adapter's shape round-trips through the library, not that the library can
detect a wrong prediction. Every perfect-score assertion below is therefore
paired with a mutation test that perturbs one field's bbox to a location with
no covered PCCs, proving the corresponding gate can actually fail.

The export is the session-shared `derived` fixture (read-only); the on-disk
dataset is built under tmp_path_factory.
"""

import copy
import json
from pathlib import Path

import pytest
import yaml
from docile.dataset import Dataset, Field
from docile.evaluation.evaluate import EvaluationResult, evaluate_dataset

from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.filterwarnings("ignore:biadjacency_matrix will return:FutureWarning")

# A page corner no field is drawn in: it covers no pseudo-character-centre of any OCR word.
EMPTY_CORNER = [0.001, 0.001, 0.004, 0.004]


@pytest.fixture(scope="module")
def docile_records(derived: Path) -> list[dict]:
    return [json.loads(line) for line in (derived / "docile.jsonl").read_text().splitlines()]


def _docid(record: dict) -> str:
    return Path(record["image_file"]).stem


@pytest.fixture(scope="module")
def dataset(docile_records: list[dict], tmp_path_factory: pytest.TempPathFactory) -> Dataset:
    """An on-disk DocILE dataset whose gold and synthesised OCR come from the export itself."""
    root = tmp_path_factory.mktemp("docile_dataset")
    (root / "annotations").mkdir()
    (root / "ocr").mkdir()
    for record in docile_records:
        docid = _docid(record)
        fields = [*record["kile"], *record["lir"]]
        words = [
            {
                "value": field["text"],
                "geometry": [field["bbox"][:2], field["bbox"][2:]],
                "snapped_geometry": [field["bbox"][:2], field["bbox"][2:]],
            }
            for field in fields
        ]
        ocr = {"pages": [{"blocks": [{"lines": [{"words": words}]}]}]}
        annotation = {
            "metadata": {"page_count": 1},
            "field_extractions": record["kile"],
            "line_item_extractions": record["lir"],
            "line_item_headers": [],
        }
        (root / "annotations" / f"{docid}.json").write_text(json.dumps(annotation))
        (root / "ocr" / f"{docid}.json").write_text(json.dumps(ocr))
    return Dataset("export", root, docids=[_docid(r) for r in docile_records])


def _predictions(records: list[dict], key: str) -> dict[str, list[Field]]:
    return {_docid(r): [Field.from_dict(entry) for entry in r[key]] for r in records}


def _score(dataset: Dataset, records: list[dict]) -> EvaluationResult:
    return evaluate_dataset(dataset, _predictions(records, "kile"), _predictions(records, "lir"))


def _metrics(result: EvaluationResult) -> dict[str, dict]:
    return {task: result.get_metrics(task) for task in ("kile", "lir")}


def test_export_is_populated(docile_records: list[dict]) -> None:
    """Structural guard: exactly one record per ground-truth invoice (spec section 8.3, invoice-only scope
    supersedes any receipt+invoice count named elsewhere), every one carrying non-empty kile and lir, before
    the self-score test spends any time on them."""
    invoices = yaml.safe_load((REPO_ROOT / "ground_truth" / "invoices.yml").read_text())
    assert len(docile_records) == len(invoices) > 0
    assert len({_docid(r) for r in docile_records}) == len(docile_records)
    assert all(r["kile"] and r["lir"] for r in docile_records)


def test_docile_self_score_is_perfect(dataset: Dataset, docile_records: list[dict]) -> None:
    """Feed the gold annotations back as predictions; AP and f1 must be 1.0.

    What this proves: the export round-trips through the published library: bboxes validate as in-range,
    fieldtypes are accepted KILE/LIR enum members, and the Dataset/Field/evaluate_dataset gauntlet accepts
    our structure. This is exactly the conformance spec section 8.3 asks for.

    What this does NOT prove, and must not be trusted to catch: that our bboxes land on the real text
    pixels, or that the source-column to fieldtype mapping is semantically correct. Both the gold
    annotations and the predictions here derive from the same to_docile output, and the synthesised OCR is
    built from those same bboxes, so a *systematic* export bug (a transposed axis, a shifted origin, a
    swapped fieldtype mapping) moves gold, OCR and prediction in lockstep and still scores 1.0.
    Pixel-correctness of a bbox is guaranteed upstream instead, by construction in generators/common.py's
    capture (the recorder stores the exact draw coordinates), not by this gate. The mutation tests below
    perturb only the prediction side, proving the library discriminates a wrong prediction from a right
    one, not that this self-score would catch a wrong exporter.
    """
    result = _score(dataset, docile_records)
    kile, lir = result.get_metrics("kile"), result.get_metrics("lir")
    assert result.get_primary_metric("kile") == 1.0
    assert result.get_primary_metric("lir") == 1.0
    assert kile["f1"] == 1.0
    assert lir["f1"] == 1.0
    assert kile["FP"] == kile["FN"] == lir["FP"] == lir["FN"] == 0
    # The score is over every exported field, not an empty set.
    assert kile["TP"] == sum(len(r["kile"]) for r in docile_records)
    assert lir["TP"] == sum(len(r["lir"]) for r in docile_records)


def test_corrupting_one_kile_bbox_degrades_only_the_kile_score(
    dataset: Dataset, docile_records: list[dict]
) -> None:
    """Moving one KILE prediction's bbox to an empty page corner must turn that field into a false negative
    (its own gold annotation goes unmatched), strictly lowering KILE's AP while leaving LIR's f1 untouched at
    1.0, proving the KILE gate can actually fail, not just tautologically pass."""
    baseline = _metrics(_score(dataset, docile_records))
    mutated = copy.deepcopy(docile_records)
    mutated[0]["kile"][0]["bbox"] = list(EMPTY_CORNER)
    metrics = _metrics(_score(dataset, mutated))
    assert metrics["kile"]["AP"] < baseline["kile"]["AP"] == 1.0
    assert metrics["kile"]["FN"] >= 1
    assert metrics["kile"]["TP"] == baseline["kile"]["TP"] - 1
    assert metrics["kile"]["recall"] < 1.0
    assert metrics["lir"]["f1"] == 1.0
    assert metrics["lir"]["FN"] == metrics["lir"]["FP"] == 0


def test_corrupting_one_lir_bbox_degrades_only_the_lir_score(
    dataset: Dataset, docile_records: list[dict]
) -> None:
    """The same proof for LIR: moving one LIR prediction's bbox away from its gold annotation strictly lowers
    LIR's f1 while KILE's AP stays at 1.0."""
    baseline = _metrics(_score(dataset, docile_records))
    mutated = copy.deepcopy(docile_records)
    mutated[0]["lir"][0]["bbox"] = list(EMPTY_CORNER)
    metrics = _metrics(_score(dataset, mutated))
    assert metrics["lir"]["f1"] < baseline["lir"]["f1"] == 1.0
    assert metrics["lir"]["TP"] == baseline["lir"]["TP"] - 1
    assert metrics["lir"]["FN"] >= 1
    assert metrics["kile"]["AP"] == 1.0
    assert metrics["kile"]["FN"] == metrics["kile"]["FP"] == 0


def test_corrupting_one_kile_fieldtype_also_degrades_the_score(
    dataset: Dataset, docile_records: list[dict]
) -> None:
    """A wrong fieldtype string (rather than a wrong bbox) must also break the match: the corrupted
    prediction is compared against a different gold field's candidates and its original gold field goes
    unmatched, degrading precision/recall even though every bbox is untouched."""
    mutated = copy.deepcopy(docile_records)
    original = mutated[0]["kile"][0]["fieldtype"]
    swapped = next(e["fieldtype"] for e in mutated[0]["kile"] if e["fieldtype"] != original)
    mutated[0]["kile"][0]["fieldtype"] = swapped
    metrics = _metrics(_score(dataset, mutated))
    assert metrics["kile"]["precision"] < 1.0
    assert metrics["kile"]["recall"] < 1.0
    assert metrics["kile"]["FN"] >= 1
    assert metrics["kile"]["FP"] >= 1
    assert metrics["kile"]["AP"] < 1.0
    assert metrics["lir"]["f1"] == 1.0
