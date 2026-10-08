"""The unambiguity rules on rendered pages (Stage 6, Task 2; Review Focus 2 and 4).

End to end through the real renderers: a second checksum-valid ABN printed beside an unlabelled one, and a
near-miss amount drawn at an unlabelled total's size and weight, each on a receipt layout given one extra
line; the inclusive invoice layout refusing an unlabelled GST printed below its total; 'Balance:' beside a
paid invoice's 'Balance due'; a long wording pushed across the inclusive totals column into the payment
details. Each is run under `fail` (the four-part diagnostic) and under `redraw` (the next candidate is
drawn, here the site's own wording, so the page is the distractor render). An unpaid invoice's bold
account-summary 'Amount due', a repeat of the total, is shown not to make an unlabelled total ambiguous.
"""

import copy
import dataclasses

import pytest

from generators.common import validate_abn
from generators.drawn_values import load_policy
from generators.labels import render as render_module
from generators.labels.config import load_label_config
from generators.labels.keys import LABELLED, UNLABELLED, VARIANT, LabelChoice, LabelPick
from generators.labels.render import LabelRefusalError, label_sites, render_labelled_varied
from generators.labels.unambiguity import check_label_choice
from generators.layout_dsl.field_providers import apply_field_providers
from tests.conftest import REPO_ROOT, assert_diagnostic_error
from tests.renderer_helpers import corpus_entries, entry_for, layouts_for, render_entry, renderer_for

# Invented and checksum-valid; printed nowhere in the corpus (asserted below).
RIVAL = "27 283 950 517"
# Long enough to be pushed across the 400px totals column into the payment details, and sharing no word
# with any distractor line on that page, so overlap is the only rule it breaks.
LONG_GST = (
    "GST component, reckoned at ten per cent, already carried within the invoiced figure appearing "
    "directly alongside:"
)


def _force(monkeypatch: pytest.MonkeyPatch, layout: dict, field: str, pick: LabelPick) -> None:
    orders = {f: [LabelPick(LABELLED)] for f in label_sites(layout)}
    orders[field] = [pick, LabelPick(LABELLED)]
    monkeypatch.setattr(render_module, "draw_label_orders", lambda *args: orders)


def _fail_config():
    return dataclasses.replace(load_label_config(), refusal_policy="fail")


def _with_line(layout: dict, block: dict) -> dict:
    changed = copy.deepcopy(layout)
    changed["body"] = [block, *changed["body"]]
    return changed


def _receipt_without(key: str) -> dict:
    for entry in corpus_entries("receipt").values():
        layout = layouts_for("receipt")[entry["layout"]]
        fields = apply_field_providers(layout, entry, distractors=True)["fields"]
        if key not in fields:
            return entry
    raise AssertionError(f"every receipt draws {key}")


def _dx_key(entry: dict, layout: dict) -> str:
    fields = apply_field_providers(layout, entry, distractors=True)["fields"]
    return sorted(k for k in fields if k.startswith("DX_"))[0]


def _rival_receipt() -> tuple[dict, dict]:
    entry = next(iter(corpus_entries("receipt").values()))
    layout = _with_line(
        layouts_for("receipt")[entry["layout"]], {"type": "text", "content": f"Ref {RIVAL}"}
    )
    return entry, layout


def _twin_receipt() -> tuple[dict, dict]:
    entry = _receipt_without("DX_YOU_SAVED")
    base = layouts_for("receipt")[entry["layout"]]
    assert entry["fields"]["TOTAL_AMOUNT"] != "12.34"
    block = {"type": "text", "content": "Club price $12.34", "bold": True, "when": _dx_key(entry, base)}
    return entry, _with_line(base, block)


def _inclusive() -> tuple[dict, dict]:
    entry = entry_for("invoice", "CASE003")
    assert entry["layout"] == "tax_invoice_gst_inclusive"
    return entry, layouts_for("invoice")[entry["layout"]]


def _balance_due() -> tuple[dict, dict]:
    entry = entry_for("invoice", "CASE020")
    layout = layouts_for("invoice")[entry["layout"]]
    assert "DX_BALANCE_DUE" in apply_field_providers(layout, entry, distractors=True)["fields"]
    return entry, layout


def _long_inclusive() -> tuple[dict, dict]:
    entry = entry_for("invoice", "CASE037")
    assert entry["layout"] == "tax_invoice_gst_inclusive"
    return entry, layouts_for("invoice")[entry["layout"]]


CASES = {
    "abn_rival": ("receipt", _rival_receipt, "BUSINESS_ABN", LabelPick(UNLABELLED)),
    "total_near_miss_twin": ("receipt", _twin_receipt, "TOTAL_AMOUNT", LabelPick(UNLABELLED)),
    "gst_below_total": ("invoice", _inclusive, "GST_AMOUNT", LabelPick(UNLABELLED)),
    "collision": ("invoice", _balance_due, "TOTAL_AMOUNT", LabelPick(VARIANT, "Balance:")),
    "overlap": ("invoice", _long_inclusive, "GST_AMOUNT", LabelPick(VARIANT, LONG_GST)),
}


def test_the_rival_abn_is_invented_and_checksum_valid() -> None:
    assert validate_abn(RIVAL)
    corpus = "".join(path.read_text() for path in (REPO_ROOT / "ground_truth").glob("*.yml"))
    assert RIVAL not in corpus and RIVAL.replace(" ", "") not in corpus


@pytest.mark.parametrize("rule", list(CASES))
def test_a_rendered_fixture_fails_four_part_under_fail(monkeypatch: pytest.MonkeyPatch, rule: str) -> None:
    doc_type, build, field, pick = CASES[rule]
    entry, layout = build()
    _force(monkeypatch, layout, field, pick)
    with pytest.raises(LabelRefusalError) as err:
        render_labelled_varied(
            renderer_for(doc_type), entry, layout, doc_type=doc_type, config=_fail_config()
        )
    assert_diagnostic_error(err.value)
    message = str(err.value)
    for token in (entry["case_id"], entry["layout"], field, rule):
        assert token in message, (token, message)


@pytest.mark.parametrize("rule", list(CASES))
def test_a_rendered_fixture_redraws_the_sites_own_wording(
    monkeypatch: pytest.MonkeyPatch, rule: str
) -> None:
    doc_type, build, field, pick = CASES[rule]
    entry, layout = build()
    _force(monkeypatch, layout, field, pick)
    geometry: dict = {}
    image, outcome = render_labelled_varied(
        renderer_for(doc_type),
        entry,
        layout,
        doc_type=doc_type,
        config=load_label_config(),
        geometry_out=geometry,
    )
    assert outcome.choice[field] == LabelPick(LABELLED)
    assert rule in {r.rule for r in outcome.refusals}
    assert {(r.field, r.style, r.wording) for r in outcome.refusals} == {(field, pick.style, pick.wording)}
    # Every field is now labelled: the page is the distractor render of the same layout.
    distractor = renderer_for(doc_type)(entry, layout, distractors=True)
    assert image.tobytes() == distractor.tobytes()
    assert geometry["labels"][field][0] == label_sites(layout)[field]["labelled"]


def test_the_twin_is_drawn_at_the_totals_size_and_weight() -> None:
    entry, layout = _twin_receipt()
    geometry: dict = {}
    renderer_for("receipt")(entry, layout, distractors=True, geometry_out=geometry)
    twin = next(r for r in geometry["ink"] if r.text == "Club price $12.34")
    total = next(
        r
        for r in geometry["ink"]
        if r.role == "scored" and r.text.endswith(entry["fields"]["TOTAL_AMOUNT"])
    )
    assert twin.role == "distractor"
    assert (twin.size, twin.bold) == (total.size, total.bold)


def test_an_unpaid_invoices_account_summary_leaves_an_unlabelled_total_unambiguous() -> None:
    """The bold 'Amount due' outside the totals region prints the total itself, so it is no rival.

    The unlabelled total stays the last and most prominent amount of the totals column (Subtotal, GST,
    then the header-sized bold total), and the summary's line, a repeat, gives the same answer to a
    reader who takes it instead. Every unpaid invoice of the corpus is checked, on its own layout.
    """
    policy = load_policy()
    config = load_label_config()
    unpaid = 0
    for entry in corpus_entries("invoice").values():
        layout = layouts_for("invoice")[entry["layout"]]
        if "DX_ACCOUNT_TOTAL" not in apply_field_providers(layout, entry, distractors=True)["fields"]:
            continue
        choice = LabelChoice({"TOTAL_AMOUNT": LabelPick(UNLABELLED)})
        geometry: dict = {}
        render_entry("invoice", entry, distractors=True, labels=choice, geometry_out=geometry)
        total = entry["fields"]["TOTAL_AMOUNT"]
        lines = [
            r for r in geometry["ink"] if r.role == "distractor" and r.bold and policy.money_in(r.text)
        ]
        summary = [r for r in lines if all(str(a) == total for a in policy.money_in(r.text))]
        assert summary, entry["case_id"]
        refusals = check_label_choice(
            geometry,
            choice,
            entry,
            case_id=entry["case_id"],
            layout_id=entry["layout"],
            doc_type="invoice",
            config=config,
            policy=policy,
        )
        if entry["layout"] != "tax_invoice_gst_inclusive":
            assert refusals == [], (entry["case_id"], refusals)
        else:
            # The inclusive column prints GST after the total, so its total is never the last amount.
            assert {r.rule for r in refusals} == {"total_not_last"}, (entry["case_id"], refusals)
        unpaid += 1
    assert unpaid >= 20
