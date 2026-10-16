"""The snapshot's two typeset sets: each case as a native PDF beside the PNG rasterised from it.

One single-invoice snapshot (the module's `typeset` fixture) is shared read-only; a test that needs a
refusal, a mutation or a second export makes its own under its own tmp_path.
"""

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pypdfium2
import pytest
import yaml
from PIL import Image

from generators.loader import REPO_ROOT, load_ground_truth, load_layout_registry
from generators.parsing.config import PARSING_CONFIG_PATH, load_parsing_config
from generators.parsing.omnidocbench import PageContext, build_page, check_page
from generators.snapshot import export as export_module
from generators.snapshot.config import HALVES, TYPESET_SETS, load_snapshot_config
from generators.snapshot.export import SnapshotError, SnapshotSummary, export_snapshot, opaque_layout_keys
from generators.snapshot.publication import find_forbidden
from generators.typst import validate as typeset_pass
from generators.typst.compile import rasterise
from generators.typst.config import TYPESET_CONFIG_PATH, load_typeset_config
from generators.typst.typeset import typeset_case
from scripts.verify_snapshot import verify
from tests.conftest import assert_diagnostic_error
from tests.eval_export_tools import (
    CONFIG_PATH,
    STAMP,
    STAMP_DATE,
    clean_state,
    scratch_config,
    single_case_config,
)

PARSING = load_parsing_config(PARSING_CONFIG_PATH)
TYPESET = load_typeset_config(TYPESET_CONFIG_PATH)
MODES = {"typeset_plain": False, "typeset_distractor": True}


def _export(config: Path) -> SnapshotSummary:
    return export_snapshot(config, today=STAMP_DATE, repo_state=clean_state)


def _invoice_config(root: Path, *, mutate: Callable[[dict], None] | None = None) -> Path:
    """A single-invoice scratch config, with `mutate` applied to its `eval_set` block."""
    path = single_case_config(root, ["invoices"])
    if mutate is not None:
        config = yaml.safe_load(path.read_text())
        mutate(config["eval_set"])
        path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


@pytest.fixture(scope="module")
def typeset(tmp_path_factory: pytest.TempPathFactory) -> SnapshotSummary:
    """ONE single-invoice snapshot, shared READ-ONLY by the tests that only read it."""
    return _export(_invoice_config(tmp_path_factory.mktemp("typeset_snapshot")))


def _case(summary: SnapshotSummary) -> tuple[str, dict, str, dict, Path]:
    """The snapshot's one invoice: (case id, entry, layout id, layout, layout file)."""
    config = yaml.safe_load((summary.path.parent.parent / "generation_config.yml").read_text())
    doc_cfg = config["document_types"]["invoices"]
    case_id, entry = next(iter(load_ground_truth(Path(doc_cfg["ground_truth"])).items()))
    layout_path = Path(doc_cfg["layouts"])
    layouts = load_layout_registry(layout_path)
    return str(case_id), entry, entry["layout"], layouts[entry["layout"]], layout_path


def _files(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _pdf_metadata(path: Path) -> dict[str, str]:
    document = pypdfium2.PdfDocument(path)
    try:
        return document.get_metadata_dict()
    finally:
        document.close()


# --- configuration ---------------------------------------------------------------------------------


def test_the_shipped_config_names_both_sets_and_a_blank_producer() -> None:
    config = load_snapshot_config(CONFIG_PATH)
    assert TYPESET_SETS == ("typeset_plain", "typeset_distractor")
    assert config.typeset_prefixes == {name: name for name in TYPESET_SETS}
    assert config.pdf_producer == ""
    assert 'pdf_producer: ""' in CONFIG_PATH.read_text()


def _drop(key: str) -> Callable[[dict], None]:
    def mutate(config: dict) -> None:
        del config["eval_set"][key]

    return mutate


def _set(key: str, value: object) -> Callable[[dict], None]:
    def mutate(config: dict) -> None:
        config["eval_set"][key] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (_drop("typeset_prefixes"), "eval_set.typeset_prefixes"),
        (_drop("pdf_producer"), "eval_set.pdf_producer"),
        (_set("typeset_prefixes", {"typeset_plain": "typeset_plain"}), "eval_set.typeset_prefixes"),
        (_set("typeset_prefixes", ["typeset_plain", "typeset_distractor"]), "eval_set.typeset_prefixes"),
        (
            _set("typeset_prefixes", {"typeset_plain": "a/b", "typeset_distractor": "typeset_distractor"}),
            "eval_set.typeset_prefixes.typeset_plain",
        ),
        (
            _set("typeset_prefixes", {"typeset_plain": "same", "typeset_distractor": "same"}),
            "eval_set.typeset_prefixes",
        ),
        (
            _set(
                "typeset_prefixes", {"typeset_plain": "plain", "typeset_distractor": "typeset_distractor"}
            ),
            "eval_set.typeset_prefixes",
        ),
        (_set("pdf_producer", None), "eval_set.pdf_producer"),
        (_set("pdf_producer", 3), "eval_set.pdf_producer"),
        (_set("pdf_producer", "Brackenridgeé Press"), "eval_set.pdf_producer"),
        (_set("pdf_producer", "Brackenridge\nPress"), "eval_set.pdf_producer"),
    ],
)
def test_a_missing_or_invalid_typeset_key_is_refused(
    tmp_path: Path, mutate: Callable[[dict], None], named: str
) -> None:
    path = scratch_config(tmp_path, mutate=mutate)
    with pytest.raises(ValueError) as err:
        load_snapshot_config(path)
    assert_diagnostic_error(err.value)
    assert named in str(err.value) and str(path.resolve()) in str(err.value)


# --- the two sets ----------------------------------------------------------------------------------


def test_each_set_holds_a_pdf_and_a_png_per_case_and_the_plain_answer_key(typeset: SnapshotSummary) -> None:
    case_id = _case(typeset)[0]
    plain = typeset.half_dirs["plain"]
    for name in TYPESET_SETS:
        directory = typeset.typeset_dirs[name]
        assert directory == typeset.path / f"{name}_{STAMP}"
        assert sorted(p.name for p in directory.iterdir()) == sorted(
            [f"{case_id}_invoice.pdf", f"{case_id}_invoice.png", "ground_truth.csv", "ground_truth.jsonl"]
        )
        for answer_key in ("ground_truth.csv", "ground_truth.jsonl"):
            assert (directory / answer_key).read_bytes() == (plain / answer_key).read_bytes()
        assert typeset.counts[name] == {"invoice": 1}


def test_each_png_is_its_pdf_rasterised(typeset: SnapshotSummary) -> None:
    for name in TYPESET_SETS:
        (pdf,) = typeset.typeset_dirs[name].glob("*.pdf")
        image = rasterise(pdf.read_bytes(), dpi=TYPESET.raster_dpi)
        with pypdfium2.PdfDocument(pdf) as document:
            assert len(document) == 1
        with Image.open(pdf.with_suffix(".png")) as png:
            assert png.size == image.size and png.convert("RGB").tobytes() == image.tobytes()


def test_each_parsing_page_is_build_page_of_the_typeset_geometry(
    typeset: SnapshotSummary, tmp_path: Path
) -> None:
    case_id, entry, layout_id, layout, layout_path = _case(typeset)
    layout_key = opaque_layout_keys("invoice", load_layout_registry(layout_path))[layout_id]
    for name, distractors in MODES.items():
        pages = json.loads((typeset.path / "parsing" / f"{name}.json").read_text())
        assert typeset.parsing.pages[name] == f"parsing/{name}.json"
        result = typeset_case(
            {**entry, "case_id": case_id},
            layout,
            layout_id=layout_id,
            layout_path=layout_path,
            doc_type="invoice",
            distractors=distractors,
            config=TYPESET,
            workdir=tmp_path / name,
        )
        image_path = f"{typeset.typeset_dirs[name].name}/{case_id}_invoice.png"
        context = PageContext(image_path, "invoice", layout_key, name, "none", True)
        page = build_page(result.geometry, context=context, config=PARSING)
        assert pages == [page]
        assert page["page_info"]["page_attribute"]["layout_id"] == layout_key != layout_id
        assert check_page(page, result.geometry, where=name, config=PARSING) == []


def test_every_pdf_carries_the_configured_producer_and_names_no_tool(typeset: SnapshotSummary) -> None:
    pdfs = sorted(typeset.path.rglob("*.pdf"))
    assert len(pdfs) == 2
    for pdf in pdfs:
        metadata = _pdf_metadata(pdf)
        assert metadata["Producer"] == "" and metadata["Creator"] == ""
        assert b"typst" not in pdf.read_bytes().lower()
    assert find_forbidden(typeset.path, ("Typst", "pypdfium2", "xmp-writer")) == []
    # The layout-id scan covers the PDFs too: the case's own layout id appears in none.
    assert find_forbidden(typeset.path, (_case(typeset)[2],)) == []


def test_the_manifest_and_the_readme_describe_both_sets(typeset: SnapshotSummary) -> None:
    manifest = json.loads((typeset.path / "MANIFEST.json").read_text())
    assert manifest["typeset_sets"] == {name: f"{name}_{STAMP}" for name in TYPESET_SETS}
    assert sorted(manifest["halves"]) == sorted(HALVES)
    readme = (typeset.path / "README.md").read_text()
    for name in TYPESET_SETS:
        assert manifest["counts"][name]["total"] == 1
        assert f"| `{name}_{STAMP}/` | 1 |" in readme
        assert f"`parsing/{name}.json`" in readme
    assert "typst" not in readme.lower()


def test_the_snapshot_verifies_and_a_changed_or_missing_pdf_fails(
    typeset: SnapshotSummary, tmp_path: Path
) -> None:
    copy = tmp_path / typeset.path.name
    shutil.copytree(typeset.path, copy)
    assert verify(copy) == []
    (pdf,) = (copy / f"typeset_plain_{STAMP}").glob("*.pdf")
    data = bytearray(pdf.read_bytes())
    data[len(data) // 2] ^= 0x01
    pdf.write_bytes(bytes(data))
    relative = pdf.relative_to(copy).as_posix()
    assert verify(copy) == [f"changed file: {relative}"]
    pdf.unlink()
    problems = verify(copy)
    assert f"missing file: {relative}" in problems
    assert any("1 images and 0 PDFs" in problem for problem in problems), problems
    assert any(f"unpaired image: {relative.removesuffix('.pdf')}.png" in problem for problem in problems)


# --- stage one unchanged, and reproducible ---------------------------------------------------------


def _stage_one_files(summary: SnapshotSummary) -> dict[str, bytes]:
    """Every file of stage one's halves, geometry, half parsing pages and tools."""
    files: dict[str, bytes] = {}
    for directory in [*summary.half_dirs.values(), summary.path / "geometry", summary.path / "tools"]:
        files |= {f"{directory.name}/{k}": v for k, v in _files(directory).items()}
    for half in HALVES:
        relative = summary.parsing.pages[half]
        files[relative] = (summary.path / relative).read_bytes()
    return files


def test_stage_one_exports_byte_for_byte_as_without_the_typeset_sets(
    typeset: SnapshotSummary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    again = _export(_invoice_config(tmp_path / "again"))
    # Typeset in separate worker processes, both times: a rebuild is byte-identical, metadata included.
    for name in TYPESET_SETS:
        assert _files(again.typeset_dirs[name]) == _files(typeset.typeset_dirs[name])
    # Without the sets: none is typeset, and the README (which describes them) is not compared.
    monkeypatch.setattr(export_module, "TYPESET_SETS", ())
    monkeypatch.setattr(export_module, "render_readme", lambda description, schema: "")
    without = _export(_invoice_config(tmp_path / "without"))
    assert without.typeset_dirs == {}
    assert not list(without.path.rglob("*.pdf"))
    assert _stage_one_files(without) == _stage_one_files(typeset)


# --- refusals --------------------------------------------------------------------------------------


def _refused(config: Path) -> str:
    with pytest.raises(SnapshotError) as err:
        _export(config)
    assert_diagnostic_error(err.value)
    out = config.parent / "out"
    assert sorted(p.name for p in out.iterdir()) == [], "a refused export left something behind"
    return str(err.value)


def test_a_producer_naming_a_forbidden_term_is_refused(tmp_path: Path) -> None:
    message = _refused(_invoice_config(tmp_path, mutate=lambda block: block.update(pdf_producer="git")))
    assert "metadata producer contains 'git'" in message and "metadata xmp contains 'git'" in message


def test_a_producer_naming_a_layout_id_is_refused(tmp_path: Path) -> None:
    layout_id = str(
        next(iter(load_ground_truth(REPO_ROOT / "ground_truth" / "invoices.yml").values()))["layout"]
    )
    message = _refused(_invoice_config(tmp_path, mutate=lambda block: block.update(pdf_producer=layout_id)))
    assert "layout id(s)" in message and f"metadata producer contains {layout_id!r}" in message


def test_a_typeset_page_failing_its_checks_refuses_the_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typeset serially, in this process, so the planted check failure reaches the pass."""
    serial = replace(TYPESET, export_workers=1)
    monkeypatch.setattr(export_module, "load_typeset_config", lambda path: serial)
    planted = "A planted typeset failure.\n  What: x\n  Where: y\n  Expected: z\n  Recover: w"
    monkeypatch.setattr(typeset_pass, "check_page", lambda *args, **kwargs: [planted])
    message = _refused(_invoice_config(tmp_path))
    assert message.count("A planted typeset failure.") == 2
