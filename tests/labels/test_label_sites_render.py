"""Label sites change no render, and every render says what it drew (Stage 6, Task 1).

Control invariance: each site's labelled wording is exactly the label the layout printed before label
sites existed (read from the commit before this task); every distractor render's ink boxes and density are
the ones measured on that commit (tests/fixtures/stage6_ink_baseline.json, captured there); a forced
all-`labelled` choice draws the distractor render's bytes. The pixel, distractor and derived snapshots
cover the rest.

What a render records: every text ink line its string, size and weight; every code mark its payload; the
stamp's and handwriting's words; one label per site and one totals region holding the GST and total
value boxes on every receipt and invoice; nothing of the kind on a bank statement.
"""

import hashlib
import json
import subprocess
from collections.abc import Iterator

import pytest
import yaml
from PIL import Image

from generators.distractors.density import density_report
from generators.exporters.geometry import InkRecord
from generators.labels.config import load_label_config
from generators.labels.keys import LabelChoice, LabelPick, label_key
from generators.layout_dsl.binding import interpolate, is_present
from generators.layout_dsl.field_providers import apply_field_providers
from generators.layout_dsl.primitives_codes import initials
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, entry_per_layout, layouts_for, render_entry, renderer_for

# The commit this task started from: the layouts as they printed before label sites existed.
BEFORE = "bace32e"
BASELINE = REPO_ROOT / "tests" / "fixtures" / "stage6_ink_baseline.json"
LABELLED_TYPES = ("receipt", "invoice")
STEMS = {"receipt": "receipts", "invoice": "invoices", "bank_statement": "bank_statements"}
SITE_FIELDS = ("BUSINESS_ABN", "GST_AMOUNT", "TOTAL_AMOUNT")


def _sites(layout: dict) -> dict:
    return next(s for s in layout["field_providers"] if s["name"] == "label_styles")["params"]["sites"]


def _label_only(layout: dict) -> list[dict]:
    return [s for s in layout["field_providers"] if s["name"] == "label_styles"]


def _blocks(node: object) -> Iterator[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _blocks(item)
    elif isinstance(node, dict):
        yield node
        yield from _blocks(node.get("children"))


@pytest.fixture(scope="module")
def corpus() -> dict[tuple[str, str], tuple[dict, dict, Image.Image]]:
    """Every case's distractor render: (entry, geometry, image), keyed (doc_type, case_id)."""
    renders = {}
    for doc_type in STEMS:
        for case_id, entry in corpus_entries(doc_type).items():
            geometry: dict = {}
            image = render_entry(doc_type, entry, distractors=True, geometry_out=geometry)
            renders[(doc_type, case_id)] = (entry, geometry, image)
    return renders


# --- control invariance ---------------------------------------------------------------------------


def _before_labels(doc_type: str) -> dict[str, dict[str, str]]:
    """Per layout, the label each site's block printed on the commit before this task."""
    text = subprocess.run(
        ["git", "show", f"{BEFORE}:config/layouts/{STEMS[doc_type]}.yml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    registry = yaml.safe_load(text)["layouts"]
    printed: dict[str, dict[str, str]] = {}
    for layout_id, layout in registry.items():
        found: dict[str, str] = {}
        for block in _blocks(layout["body"]):
            field = block.get("field")
            if field not in SITE_FIELDS:
                continue
            if block["type"] == "pair":
                separator = block.get("separator", layout["defaults"]["pair_separator"])
                found[field] = f"{block['label']}{separator}"
            elif block["type"] == "text":
                assert block["content"].endswith("{" + field + "}")
                found[field] = block["content"][: -len(field) - 2]
        printed[layout_id] = found
    return printed


@pytest.mark.parametrize("doc_type", LABELLED_TYPES)
def test_every_sites_wording_is_the_label_it_printed_before(doc_type: str) -> None:
    before = _before_labels(doc_type)
    assert set(before) == set(layouts_for(doc_type))
    for layout_id, layout in layouts_for(doc_type).items():
        entry = {"case_id": "CASE900", "layout": layout_id, "fields": {}}
        provided = apply_field_providers(
            {**layout, "field_providers": _label_only(layout)}, entry, distractors=False
        )
        emitted = {field: provided["fields"][label_key(field)] for field in _sites(layout)}
        assert set(emitted) == set(before[layout_id]) == set(SITE_FIELDS), layout_id
        for field, wording in emitted.items():
            assert wording == before[layout_id][field], (
                layout_id,
                field,
                wording,
                before[layout_id][field],
            )


@pytest.mark.parametrize("doc_type", LABELLED_TYPES)
def test_with_no_choice_the_provider_emits_each_sites_wording(doc_type: str) -> None:
    for entry in corpus_entries(doc_type).values():
        layout = layouts_for(doc_type)[entry["layout"]]
        for distractors in (False, True):
            fields = apply_field_providers(layout, entry, distractors=distractors)["fields"]
            for field, site in _sites(layout).items():
                assert fields[label_key(field)] == site["labelled"]


def test_every_render_keeps_its_ink_boxes_and_density(corpus: dict) -> None:
    """The ink each page records (role, box, mark) and its density are those measured before this task."""
    baseline = json.loads(BASELINE.read_text())
    assert len(baseline) == len(corpus) == 165
    for (doc_type, case_id), (_, geometry, _) in corpus.items():
        pinned = baseline[f"{doc_type}:{case_id}"]
        triples = [[r.role, list(r.box), r.mark] for r in geometry["ink"]]
        assert hashlib.sha256(json.dumps(triples).encode()).hexdigest() == pinned["ink_sha256"], case_id
        report = density_report(geometry["ink"], int(geometry["height"]))
        assert [report.scored_ink_ratio, report.page_fill, report.distractor_share] == pinned["density"]


# --- what the ink says ----------------------------------------------------------------------------


def test_every_text_ink_line_says_what_it_drew(corpus: dict) -> None:
    for key, (_, geometry, _) in corpus.items():
        for record in geometry["ink"]:
            assert isinstance(record, InkRecord)
            if record.mark:
                assert record.size == 0 and not record.bold, (key, record)
            else:
                assert record.text.strip() and record.size > 0, (key, record)


def _payloads(doc_type: str, entry: dict) -> set[str]:
    layout = layouts_for(doc_type)[entry["layout"]]
    fields = apply_field_providers(layout, entry, distractors=True)["fields"]
    found = set()
    for block in _blocks(layout["body"]):
        if block.get("type") in ("barcode", "qrcode", "logo"):
            try:
                value = interpolate(str(block["content"]), fields)
            except Exception:  # noqa: BLE001  a block whose key this case never drew
                continue
            found.add(initials(value) if block["type"] == "logo" else value)
    return found


def test_every_code_mark_records_its_payload(corpus: dict) -> None:
    barcodes = 0
    for (doc_type, case_id), (entry, geometry, _) in corpus.items():
        payloads = _payloads(doc_type, entry)
        texts = {record.text for record in geometry["ink"] if record.mark and record.text}
        assert texts <= payloads, (case_id, texts - payloads)
        fields = apply_field_providers(layouts_for(doc_type)[entry["layout"]], entry, distractors=True)[
            "fields"
        ]
        if doc_type == "receipt" and is_present(fields, "DX_TRANSACTION_ID"):
            assert fields["DX_TRANSACTION_ID"] in texts, case_id
            barcodes += 1
    assert barcodes > 0


def test_stamp_and_handwriting_record_their_words(corpus: dict) -> None:
    stamps = notes = 0
    for (doc_type, case_id), (entry, geometry, _) in corpus.items():
        layout = layouts_for(doc_type)[entry["layout"]]
        fields = apply_field_providers(layout, entry, distractors=True)["fields"]
        texts = {r.text for r in geometry["ink"] if r.role == "distractor" and not r.mark}
        if doc_type == "invoice" and is_present(fields, "DX_PAID_STAMP"):
            assert fields["DX_PAID_STAMP"] in texts, case_id
            stamps += 1
        if doc_type == "bank_statement" and is_present(fields, "DX_MARGIN_NOTE"):
            assert fields["DX_MARGIN_NOTE"] in texts, case_id
            notes += 1
        if doc_type == "bank_statement" and is_present(fields, "DX_ANNOTATION_TOTAL"):
            assert f"= {fields['DX_ANNOTATION_TOTAL']}" in texts, case_id
    assert stamps > 0 and notes > 0


# --- labels and regions ---------------------------------------------------------------------------


def _pixels(geometry: dict, field: str) -> tuple[float, float, float, float]:
    left, top, right, bottom = geometry["boxes"][field]
    width, height = geometry["width"], geometry["height"]
    return left * width, top * height, right * width, bottom * height


def _inside(inner: tuple, outer: tuple, slack: float = 1.0) -> bool:
    return (
        inner[0] >= outer[0] - slack
        and inner[1] >= outer[1] - slack
        and inner[2] <= outer[2] + slack
        and inner[3] <= outer[3] + slack
    )


def test_every_receipt_and_invoice_records_its_labels_and_totals_region(corpus: dict) -> None:
    for (doc_type, case_id), (entry, geometry, _) in corpus.items():
        if doc_type not in LABELLED_TYPES:
            assert geometry["labels"] == {} and geometry["regions"] == {}, case_id
            continue
        sites = _sites(layouts_for(doc_type)[entry["layout"]])
        drawn = {field for field in sites if is_present(entry["fields"], field)}
        assert set(geometry["labels"]) == drawn, case_id
        for field in drawn:
            text, box = geometry["labels"][field]
            assert text == sites[field]["labelled"] and box is not None, (case_id, field)
            value = _pixels(geometry, field)
            assert box[2] <= value[0] + 1, (case_id, field, box, value)
            if field != "BUSINESS_ABN":
                # A pair's label is its own ink line, and that line is the recorded label.
                assert any(r.text == text and r.box == box for r in geometry["ink"]), (case_id, field)
        assert set(geometry["regions"]) == {"totals"}, case_id
        region = geometry["regions"]["totals"]
        for field in ("GST_AMOUNT", "TOTAL_AMOUNT"):
            if field in drawn:
                assert _inside(_pixels(geometry, field), region), (case_id, field, region)
                assert _inside(geometry["labels"][field][1], region), (case_id, field, region)


# --- forced styles on every layout ----------------------------------------------------------------


def _choice(layout: dict, style: str) -> LabelChoice:
    pools = load_label_config().pools
    return LabelChoice(
        {
            field: LabelPick(style, pools[site["pool"]][0] if style == "variant" else "")
            for field, site in _sites(layout).items()
        }
    )


FORCED = [(doc_type, layout_id) for doc_type in LABELLED_TYPES for layout_id in layouts_for(doc_type)]


@pytest.mark.parametrize(("doc_type", "layout_id"), FORCED)
def test_each_style_forced_on_every_layout(doc_type: str, layout_id: str) -> None:
    entry = entry_per_layout(doc_type)[layout_id]
    layout = layouts_for(doc_type)[layout_id]
    sites = _sites(layout)
    renders = {}
    for style in ("labelled", "variant", "unlabelled"):
        geometry: dict = {}
        image = renderer_for(doc_type)(
            entry, layout, distractors=True, labels=_choice(layout, style), geometry_out=geometry
        )
        renders[style] = (image, geometry)
    plain_geometry: dict = {}
    distractor = render_entry(doc_type, entry, distractors=True, geometry_out=plain_geometry)
    # A forced all-labelled choice is the distractor render.
    assert renders["labelled"][0].tobytes() == distractor.tobytes()
    assert renders["labelled"][1]["labels"] == plain_geometry["labels"]

    pools = load_label_config().pools
    abn = entry["fields"]["BUSINESS_ABN"]
    for style, (_, geometry) in renders.items():
        for field, site in sites.items():
            if not is_present(entry["fields"], field):
                continue
            text, box = geometry["labels"][field]
            if style == "unlabelled":
                assert (text, box) == ("", None), (style, field)
            else:
                want = site["labelled"] if style == "labelled" else pools[site["pool"]][0]
                assert text == want and box is not None, (style, field, text)
        # The ABN's recorded string is the ground truth alone, whatever its label.
        assert geometry["texts"]["BUSINESS_ABN"] == abn, style
        # Every other scored box and string is the distractor render's; only the ABN may move (Decision 18).
        for field, value in plain_geometry["boxes"].items():
            if field != "BUSINESS_ABN":
                assert geometry["boxes"][field] == value, (style, field)
                assert geometry["texts"][field] == plain_geometry["texts"][field], (style, field)

    # Unlabelled: no label ink and no separator; the ABN line prints the digits alone.
    _, bare = renders["unlabelled"]
    labelled_texts = [r.text for r in renders["labelled"][1]["ink"] if not r.mark]
    bare_texts = [r.text for r in bare["ink"] if not r.mark]
    for field in ("GST_AMOUNT", "TOTAL_AMOUNT"):
        if is_present(entry["fields"], field):
            assert sites[field]["labelled"] in labelled_texts
            labelled_texts.remove(sites[field]["labelled"])
    assert sorted(
        t for t in labelled_texts if not t.startswith(sites["BUSINESS_ABN"]["labelled"])
    ) == sorted(t for t in bare_texts if t != abn)
    abn_lines = [r for r in bare["ink"] if r.role == "scored" and r.text == abn]
    assert len(abn_lines) == 1
    abn_box = _pixels(bare, "BUSINESS_ABN")
    assert abs(abn_box[0] - abn_lines[0].box[0]) <= 2, (abn_box, abn_lines[0].box)


@pytest.mark.parametrize("doc_type", list(STEMS))
def test_a_label_choice_without_distractors_is_refused(doc_type: str) -> None:
    entry = next(iter(corpus_entries(doc_type).values()))
    layout = layouts_for(doc_type)[entry["layout"]]
    with pytest.raises(ValueError) as err:
        renderer_for(doc_type)(entry, layout, distractors=False, labels=LabelChoice({}))
    assert_diagnostic_error(err.value)


def test_a_bank_render_with_an_empty_choice_is_the_distractor_render() -> None:
    entry = next(iter(corpus_entries("bank_statement").values()))
    layout = layouts_for("bank_statement")[entry["layout"]]
    render = renderer_for("bank_statement")
    assert (
        render(entry, layout, distractors=True, labels=LabelChoice({})).tobytes()
        == render(entry, layout, distractors=True).tobytes()
    )
