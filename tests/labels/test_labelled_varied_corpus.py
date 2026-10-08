"""Every case renders labelled varied, accepted, differing from its distractor render in labels alone.

Stage 6, Task 2. Per case, all 55 receipts and all 55 invoices on their own layouts: the render is
accepted; every scored string equals the distractor render's and passes the drawn-field guard; every
scored box but BUSINESS_ABN equals the distractor render's, and BUSINESS_ABN keeps its line (Decision 18);
the distractor values and the distractor ink (overlay marks and stamp words included) equal the distractor
render's; each site's recorded label is the choice; density is within the bounds; two runs are
byte-identical. All 55 bank statements: the labelled varied bytes are the distractor bytes. Coverage
(Review Focus 5): every varied field shows all three styles in each of receipts and invoices. Forced: every
wording of every site's pool, and each field unlabelled, on every case; and a forced all-`labelled` choice
reproduces the distractor bytes.
"""

import collections
import functools
from pathlib import Path

import pytest
from PIL import Image

from generators.distractors.config import load_distractor_config
from generators.distractors.density import DensityCheck
from generators.distractors.paths import DISTRACTORS_PATH
from generators.drawn_fields import build_check
from generators.drawn_values import load_policy
from generators.labels.config import load_label_config, normalise_wording
from generators.labels.keys import LABEL_STYLES, LABELLED, UNLABELLED, VARIANT, LabelChoice, LabelPick
from generators.labels.render import LabelOutcome, label_sites, render_labelled_varied
from generators.labels.unambiguity import check_label_choice
from generators.layout_dsl.field_providers import apply_field_providers
from tests.conftest import REPO_ROOT
from tests.renderer_helpers import corpus_entries, layouts_for, render_entry, renderer_for

STEMS = {"receipt": "receipts", "invoice": "invoices", "bank_statement": "bank_statements"}
TYPES = [
    pytest.param("receipt"),
    pytest.param("invoice", marks=pytest.mark.slow),
]


@functools.cache
def _renders(doc_type: str) -> dict[str, tuple[Image.Image, LabelOutcome, dict, Image.Image, dict]]:
    """Per case: (labelled varied image, outcome, its geometry, distractor image, its geometry)."""
    config = load_label_config()
    found = {}
    for case_id, entry in corpus_entries(doc_type).items():
        layout = layouts_for(doc_type)[entry["layout"]]
        geometry: dict = {}
        image, outcome = render_labelled_varied(
            renderer_for(doc_type), entry, layout, doc_type=doc_type, config=config, geometry_out=geometry
        )
        plain: dict = {}
        distractor = render_entry(doc_type, entry, distractors=True, geometry_out=plain)
        found[case_id] = (image, outcome, geometry, distractor, plain)
    return found


def _distractor_ink(geometry: dict) -> list[tuple]:
    return sorted((r.text, r.mark, r.size, r.bold) for r in geometry["ink"] if r.role == "distractor")


def _guard(doc_type: str):
    stem = STEMS[doc_type]
    return build_check(
        f"{stem}",
        REPO_ROOT / "ground_truth" / f"{stem}.yml",
        REPO_ROOT / "config" / "layouts" / f"{stem}.yml",
        render="labelled varied",
    )


@pytest.mark.parametrize("doc_type", TYPES)
def test_every_case_renders_accepted_and_differs_only_in_labels(doc_type: str) -> None:
    config = load_label_config()
    policy = load_policy()
    guard = _guard(doc_type)
    density = DensityCheck(
        load_distractor_config().density[doc_type],
        doc_type=doc_type,
        config_path=DISTRACTORS_PATH,
        render="labelled varied",
    )
    for case_id, (image, outcome, geometry, _, plain) in _renders(doc_type).items():
        entry = corpus_entries(doc_type)[case_id]
        layout = layouts_for(doc_type)[entry["layout"]]
        sites = label_sites(layout)
        assert list(outcome.choice) == list(config.doc_types[doc_type])
        # Accepted: the page as drawn passes every rule.
        assert (
            check_label_choice(
                geometry,
                outcome.choice,
                entry,
                case_id=case_id,
                layout_id=entry["layout"],
                doc_type=doc_type,
                config=config,
                policy=policy,
            )
            == []
        )
        assert geometry["texts"] == plain["texts"], case_id
        for field, box in plain["boxes"].items():
            if field == "BUSINESS_ABN":
                # The value may move along its line when its label goes, never off it.
                assert geometry["boxes"][field][1] == box[1] and geometry["boxes"][field][3] == box[3]
            else:
                assert geometry["boxes"][field] == box, (case_id, field)
        guard(case_id, entry["layout"], geometry)
        density(case_id, entry["layout"], geometry)
        fields = apply_field_providers(layout, entry, distractors=True)["fields"]
        varied = apply_field_providers(layout, entry, distractors=True, labels=outcome.choice)["fields"]
        assert {k: v for k, v in varied.items() if k.startswith("DX_")} == {
            k: v for k, v in fields.items() if k.startswith("DX_")
        }
        assert _distractor_ink(geometry) == _distractor_ink(plain), case_id
        for field, pick in outcome.choice.items():
            if field not in geometry["labels"]:
                continue
            want = {LABELLED: sites[field]["labelled"], VARIANT: pick.wording, UNLABELLED: ""}[pick.style]
            assert geometry["labels"][field][0] == want, (case_id, field)
    assert guard.errors == [] and density.errors == []


@pytest.mark.parametrize("doc_type", TYPES)
def test_every_case_renders_the_same_bytes_twice(doc_type: str) -> None:
    config = load_label_config()
    for case_id, (image, outcome, _, _, _) in _renders(doc_type).items():
        entry = corpus_entries(doc_type)[case_id]
        again, second = render_labelled_varied(
            renderer_for(doc_type),
            entry,
            layouts_for(doc_type)[entry["layout"]],
            doc_type=doc_type,
            config=config,
        )
        assert again.tobytes() == image.tobytes() and second == outcome, case_id


@pytest.mark.parametrize("doc_type", TYPES)
def test_every_style_occurs_for_every_varied_field(doc_type: str) -> None:
    counts: collections.Counter = collections.Counter()
    for _, outcome, geometry, _, _ in _renders(doc_type).values():
        for field, pick in outcome.choice.items():
            if field in geometry["labels"]:
                counts[(field, pick.style)] += 1
    for field in load_label_config().doc_types[doc_type]:
        for style in LABEL_STYLES:
            assert counts[(field, style)] > 0, (field, style, dict(counts))


@pytest.mark.slow
def test_a_bank_statement_renders_its_distractor_bytes() -> None:
    config = load_label_config()
    for entry in corpus_entries("bank_statement").values():
        layout = layouts_for("bank_statement")[entry["layout"]]
        image, outcome = render_labelled_varied(
            renderer_for("bank_statement"), entry, layout, doc_type="bank_statement", config=config
        )
        assert outcome == LabelOutcome(LabelChoice({}), ())
        assert image.tobytes() == render_entry("bank_statement", entry, distractors=True).tobytes()


def _words(text: str, ignore: frozenset[str]) -> set[str]:
    return {w for w in normalise_wording(text).split() if any(c.isalpha() for c in w)} - ignore


def _meets(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.slow
@pytest.mark.parametrize("doc_type", ["receipt", "invoice"])
def test_every_wording_and_unlabelled_forced_on_every_case(doc_type: str) -> None:
    """No forced pick overflows; an accepted variant shares no word with a distractor and meets no ink."""
    config = load_label_config()
    policy = load_policy()
    guard = _guard(doc_type)
    accepted: collections.Counter = collections.Counter()
    for case_id, entry in corpus_entries(doc_type).items():
        layout = layouts_for(doc_type)[entry["layout"]]
        sites = label_sites(layout)
        distractor = render_entry(doc_type, entry, distractors=True)
        labelled = LabelChoice({field: LabelPick(LABELLED) for field in sites})
        forced = renderer_for(doc_type)(entry, layout, distractors=True, labels=labelled)
        assert forced.tobytes() == distractor.tobytes(), case_id
        for field, site in sites.items():
            picks = [LabelPick(VARIANT, w) for w in config.pools[site["pool"]]] + [LabelPick(UNLABELLED)]
            for pick in picks:
                choice = LabelChoice({field: pick})
                geometry: dict = {}
                renderer_for(doc_type)(
                    entry, layout, distractors=True, labels=choice, geometry_out=geometry
                )
                refusals = check_label_choice(
                    geometry,
                    choice,
                    entry,
                    case_id=case_id,
                    layout_id=entry["layout"],
                    doc_type=doc_type,
                    config=config,
                    policy=policy,
                )
                guard(case_id, entry["layout"], geometry)
                if refusals or field not in geometry["labels"]:
                    continue
                accepted[(field, pick.style)] += 1
                if pick.style != VARIANT:
                    continue
                text, box = geometry["labels"][field]
                ignore = config.ignore_words[field]
                words = _words(pick.wording, ignore)
                for record in geometry["ink"]:
                    if record.role == "distractor" and not record.mark:
                        assert not words & _words(record.text, ignore), (case_id, pick, record)
                    own = record.text.startswith(text.strip()) and abs(record.box[1] - box[1]) <= 2
                    assert own or not _meets(box, record.box), (case_id, pick, record)
    assert guard.errors == []
    for field in config.doc_types[doc_type]:
        assert accepted[(field, VARIANT)] > 0 and accepted[(field, UNLABELLED)] > 0, dict(accepted)


def realised_lines(doc_type: str) -> list[str]:
    """The catalogue comment lines recording a type's realised styles, refusals and wordings."""
    config = load_label_config()
    counts: collections.Counter = collections.Counter()
    rules: collections.Counter = collections.Counter()
    printed: collections.Counter = collections.Counter()
    for case_id, (_, outcome, geometry, _, _) in _renders(doc_type).items():
        sites = label_sites(layouts_for(doc_type)[corpus_entries(doc_type)[case_id]["layout"]])
        for field, pick in outcome.choice.items():
            if field in geometry["labels"]:
                counts[(field, pick.style)] += 1
                if pick.style == VARIANT:
                    printed[(sites[field]["pool"], pick.wording)] += 1
        rules.update(refusal.rule for refusal in outcome.refusals)
    styles = ", ".join(
        f"{field} {'/'.join(str(counts[(field, style)]) for style in LABEL_STYLES)}"
        for field in config.doc_types[doc_type]
    )
    refused = ", ".join(f"{rule} {rules[rule]}" for rule in sorted(rules)) or "none"
    lines = [f"#   {doc_type}: {styles};", f"#     refused: {refused}"]
    pools = [name for name in config.pools if any(pool == name for pool, _ in printed)]
    for name in pools:
        shown = ", ".join(f"{w.strip()} {printed[(name, w)]}" for w in config.pools[name])
        lines.append(f"#     {name}: {shown}")
    return lines


@pytest.mark.parametrize("doc_type", TYPES)
def test_the_realised_counts_are_recorded_in_the_catalogue(doc_type: str) -> None:
    """The measured style counts and refusals sit beside the weights, dated (Decision 19)."""
    text = Path(DISTRACTORS_PATH).read_text()
    block = text[text.index("label_variation:") : text.index("  fields:", text.index("label_variation:"))]
    lines = realised_lines(doc_type)
    rows = [row.strip() for row in block.splitlines()]
    assert any(rows[i : i + len(lines)] == lines for i in range(len(rows))), lines
