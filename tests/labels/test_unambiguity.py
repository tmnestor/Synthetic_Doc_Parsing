"""Each unambiguity rule refuses its page, on synthetic geometry (Stage 6, Task 2; Review Focus 2).

One synthetic totals page is accepted with every field unlabelled; each fixture adds the one thing a rule
exists to catch and asserts that rule, and that rule alone, refuses it. Every fixture is run through the
render loop with a renderer that draws the synthetic page: under `refusal_policy: fail` it raises the
four-part diagnostic naming the case, layout, field, style, rule and evidence; under `redraw` the field's
next candidate is drawn and the refusal recorded.
"""

import dataclasses
from collections.abc import Callable, Sequence

import pytest
from PIL import Image

from generators.common import validate_abn
from generators.drawn_values import load_policy
from generators.exporters.geometry import InkRecord
from generators.labels import render as render_module
from generators.labels.config import load_label_config
from generators.labels.keys import LABELLED, UNLABELLED, VARIANT, LabelChoice, LabelPick
from generators.labels.render import LabelRefusalError, render_labelled_varied
from generators.labels.unambiguity import RULES, check_label_choice, eleven_digit_runs, wording_words
from tests.conftest import REPO_ROOT, assert_diagnostic_error

W = H = 1000
ABN = "13 703 703 751"
# Invented, checksum-valid and printed nowhere in the corpus (asserted below).
RIVAL = "27 283 950 517"
SITES = {
    "BUSINESS_ABN": {"labelled": "ABN: ", "pool": "abn_receipt"},
    "GST_AMOUNT": {"labelled": "GST", "pool": "gst_receipt"},
    "TOTAL_AMOUNT": {"labelled": "TOTAL", "pool": "total_receipt"},
}
LAYOUT = {"field_providers": [{"name": "label_styles", "params": {"sites": SITES}}]}
ENTRY = {
    "case_id": "CASE900",
    "layout": "synthetic_totals",
    "fields": {"BUSINESS_ABN": ABN, "GST_AMOUNT": "10.00", "TOTAL_AMOUNT": "110.00"},
}
REGION = (500, 400, 900, 520)
LABEL_BOXES = {
    "BUSINESS_ABN": (100, 50, 150, 70),
    "GST_AMOUNT": (500, 420, 540, 436),
    "TOTAL_AMOUNT": (500, 440, 560, 456),
}


def _ink(text: str, box: tuple, *, role: str = "text", size: int = 16, bold: bool = False) -> InkRecord:
    return InkRecord(role, box, False, text, size, bold)


def page(choice: LabelChoice, extra: Sequence[InkRecord] = (), moves: dict | None = None) -> dict:
    """A synthetic receipt totals page drawn with `choice`, plus `extra` ink; `moves` relocates values."""
    values = {
        "BUSINESS_ABN": (150, 50, 300, 70),
        "GST_AMOUNT": (850, 420, 900, 436),
        "TOTAL_AMOUNT": (830, 440, 900, 456),
        **(moves or {}),
    }
    texts = {"BUSINESS_ABN": ABN, "GST_AMOUNT": "10.00", "TOTAL_AMOUNT": "$110.00"}
    ink = [
        _ink("SUBTOTAL", (500, 400, 600, 416)),
        _ink("100.00", (850, 400, 900, 416)),
        _ink(texts["GST_AMOUNT"], values["GST_AMOUNT"], role="scored"),
        _ink(texts["TOTAL_AMOUNT"], values["TOTAL_AMOUNT"], role="scored", bold=True),
    ]
    labels: dict = {}
    for field, site in SITES.items():
        pick = choice.get(field, LabelPick(LABELLED))
        wording = {LABELLED: site["labelled"], VARIANT: pick.wording, UNLABELLED: ""}[pick.style]
        box = LABEL_BOXES[field] if wording else None
        labels[field] = (wording, box)
        if field == "BUSINESS_ABN":
            # A text site: label and value share one line.
            ink.append(_ink(f"{wording}{ABN}", (box[0] if box else 150, 50, 300, 70), role="scored"))
        elif box is not None:
            ink.append(_ink(wording, box, bold=field == "TOTAL_AMOUNT"))
    ink.extend(extra)
    boxes = {f: [b[0] / W, b[1] / H, b[2] / W, b[3] / H] for f, b in values.items()}
    return {
        "width": W,
        "height": H,
        "boxes": boxes,
        "texts": texts,
        "ink": ink,
        "labels": labels,
        "regions": {"totals": REGION},
    }


ALL_UNLABELLED = LabelChoice({field: LabelPick(UNLABELLED) for field in SITES})

# rule -> (field, pick, extra ink, value moves)
FIXTURES: dict[str, tuple[str, LabelPick, list[InkRecord], dict]] = {
    "abn_rival": ("BUSINESS_ABN", LabelPick(UNLABELLED), [_ink(f"Ref {RIVAL}", (100, 900, 400, 916))], {}),
    "gst_outside_totals": ("GST_AMOUNT", LabelPick(UNLABELLED), [], {"GST_AMOUNT": (850, 300, 900, 316)}),
    "gst_below_total": ("GST_AMOUNT", LabelPick(UNLABELLED), [], {"GST_AMOUNT": (850, 470, 900, 486)}),
    "gst_rival": ("GST_AMOUNT", LabelPick(UNLABELLED), [_ink("ROUNDING 10.00", (500, 470, 700, 486))], {}),
    "total_not_last": (
        "TOTAL_AMOUNT",
        LabelPick(UNLABELLED),
        [_ink("You saved $5.00", (550, 480, 750, 496), role="distractor")],
        {},
    ),
    "total_not_prominent": (
        "TOTAL_AMOUNT",
        LabelPick(UNLABELLED),
        [_ink("CARD $50.00", (600, 404, 800, 428), size=20)],
        {},
    ),
    "total_near_miss_twin": (
        "TOTAL_AMOUNT",
        LabelPick(UNLABELLED),
        [_ink("Club price $99.00", (100, 600, 300, 616), role="distractor", bold=True)],
        {},
    ),
    "collision": (
        "TOTAL_AMOUNT",
        LabelPick(VARIANT, "BALANCE"),
        [_ink("Balance due: $0.00", (100, 700, 300, 716), role="distractor")],
        {},
    ),
    "overlap": (
        "TOTAL_AMOUNT",
        LabelPick(VARIANT, "AMOUNT"),
        [_ink("Pay by card", (420, 440, 520, 456))],
        {},
    ),
}


def _check(geometry: dict, choice: LabelChoice) -> list:
    return check_label_choice(
        geometry,
        choice,
        ENTRY,
        case_id="CASE900",
        layout_id="synthetic_totals",
        doc_type="receipt",
        config=load_label_config(),
        policy=load_policy(),
    )


def test_every_rule_has_a_fixture() -> None:
    assert set(FIXTURES) == set(RULES)


def test_the_bare_page_is_accepted_with_every_field_unlabelled() -> None:
    assert _check(page(ALL_UNLABELLED), ALL_UNLABELLED) == []


def test_the_rival_abn_is_invented_and_checksum_valid() -> None:
    assert validate_abn(RIVAL) and validate_abn(ABN)
    corpus = "".join(path.read_text() for path in (REPO_ROOT / "ground_truth").glob("*.yml"))
    for number in (RIVAL, ABN):
        assert number not in corpus and number.replace(" ", "") not in corpus


@pytest.mark.parametrize("rule", RULES)
def test_each_rule_refuses_its_fixture_and_nothing_else_does(rule: str) -> None:
    field, pick, extra, moves = FIXTURES[rule]
    choice = LabelChoice({field: pick})
    refusals = _check(page(choice, extra, moves), choice)
    assert {r.rule for r in refusals} == {rule}, refusals
    for refusal in refusals:
        assert (refusal.field, refusal.style, refusal.wording) == (field, pick.style, pick.wording)
        assert refusal.case_id == "CASE900" and refusal.layout_id == "synthetic_totals"
        assert refusal.evidence
    # The same page with the field labelled is never judged.
    labelled = LabelChoice({field: LabelPick(LABELLED)})
    assert _check(page(labelled, extra, moves), labelled) == []


def _renderer(extra: list[InkRecord], moves: dict) -> Callable[..., Image.Image]:
    def render(entry: dict, layout: dict, *, distractors: bool, labels: LabelChoice, geometry_out: dict):
        assert distractors
        geometry_out.update(page(labels, extra, moves))
        return Image.new("RGB", (W, H), "white")

    return render


def _forced(monkeypatch: pytest.MonkeyPatch, field: str, pick: LabelPick) -> None:
    orders = {f: [LabelPick(LABELLED)] for f in SITES}
    orders[field] = [pick, LabelPick(LABELLED)]
    monkeypatch.setattr(render_module, "draw_label_orders", lambda *args: orders)


@pytest.mark.parametrize("rule", RULES)
def test_each_rule_fails_four_part_under_fail(monkeypatch: pytest.MonkeyPatch, rule: str) -> None:
    field, pick, extra, moves = FIXTURES[rule]
    _forced(monkeypatch, field, pick)
    config = dataclasses.replace(load_label_config(), refusal_policy="fail")
    with pytest.raises(LabelRefusalError) as err:
        render_labelled_varied(_renderer(extra, moves), ENTRY, LAYOUT, doc_type="receipt", config=config)
    assert_diagnostic_error(err.value)
    message = str(err.value)
    for token in ("CASE900", "synthetic_totals", field, pick.style, rule, "refusal_policy", "params.sites"):
        assert token in message, (token, message)
    assert str(config.path) in message and str(REPO_ROOT / "config" / "layouts" / "receipts.yml") in message


@pytest.mark.parametrize("rule", RULES)
def test_each_rule_redraws_the_next_candidate_under_redraw(
    monkeypatch: pytest.MonkeyPatch, rule: str
) -> None:
    field, pick, extra, moves = FIXTURES[rule]
    _forced(monkeypatch, field, pick)
    geometry: dict = {}
    _, outcome = render_labelled_varied(
        _renderer(extra, moves),
        ENTRY,
        LAYOUT,
        doc_type="receipt",
        config=load_label_config(),
        geometry_out=geometry,
    )
    assert outcome.choice[field] == LabelPick(LABELLED)
    assert {r.rule for r in outcome.refusals} == {rule}
    assert all((r.field, r.style, r.wording) == (field, pick.style, pick.wording) for r in outcome.refusals)
    assert geometry["labels"][field] == (SITES[field]["labelled"], LABEL_BOXES[field])


def test_a_redraw_moves_only_the_refused_field(monkeypatch: pytest.MonkeyPatch) -> None:
    orders = {
        "BUSINESS_ABN": [LabelPick(UNLABELLED), LabelPick(LABELLED)],
        "GST_AMOUNT": [LabelPick(VARIANT, "TAX"), LabelPick(LABELLED)],
        "TOTAL_AMOUNT": [LabelPick(UNLABELLED), LabelPick(VARIANT, "AMOUNT"), LabelPick(LABELLED)],
    }
    monkeypatch.setattr(render_module, "draw_label_orders", lambda *args: orders)
    extra = [_ink("You saved $5.00", (550, 480, 750, 496), role="distractor")]
    _, outcome = render_labelled_varied(
        _renderer(extra, {}), ENTRY, LAYOUT, doc_type="receipt", config=load_label_config()
    )
    assert dict(outcome.choice) == {
        "BUSINESS_ABN": LabelPick(UNLABELLED),
        "GST_AMOUNT": LabelPick(VARIANT, "TAX"),
        "TOTAL_AMOUNT": LabelPick(VARIANT, "AMOUNT"),
    }
    assert [(r.field, r.rule) for r in outcome.refusals] == [("TOTAL_AMOUNT", "total_not_last")]


def test_a_repeat_of_the_total_is_no_rival_however_prominent() -> None:
    """An unpaid invoice's bold 'Amount due' (a repeat of the total) beside an unlabelled total.

    Outside the region at the total's own size and weight, and inside it below the total, a line printing
    the total itself is neither a twin nor a later amount: whichever a reader takes, the answer is right.
    A near-miss in the same places is refused.
    """
    choice = LabelChoice({"TOTAL_AMOUNT": LabelPick(UNLABELLED)})
    repeats = [
        _ink("Amount due:", (100, 600, 220, 616), role="distractor", bold=True),
        _ink("$110.00", (250, 600, 320, 616), role="distractor", bold=True),
        _ink("Amount paid: $110.00", (600, 480, 900, 496), role="distractor", bold=True),
    ]
    assert _check(page(choice, repeats), choice) == []
    near = [_ink("Previous balance: $95.00", (100, 640, 320, 656), role="distractor", bold=True)]
    assert {r.rule for r in _check(page(choice, repeats + near), choice)} == {"total_near_miss_twin"}


def test_a_page_without_a_totals_region_refuses_an_unlabelled_gst() -> None:
    choice = LabelChoice({"GST_AMOUNT": LabelPick(UNLABELLED)})
    geometry = page(choice)
    geometry["regions"] = {}
    assert {r.rule for r in _check(geometry, choice)} == {"gst_outside_totals"}


@pytest.mark.parametrize(
    ("text", "runs"),
    [
        ("ABN 13 703 703 751", ["13 703 703 751"]),
        ("13703703751", ["13703703751"]),
        ("Ph 02 9876 5432", []),
        ("Txn 1234567890123456", []),
        ("ACN 123 456 789", []),
        ("1 13 703 703 751", ["13 703 703 751"]),
        ("Ref 7 27 283 950 517", ["27 283 950 517"]),
        ("13 703 703 751 4", ["13 703 703 751"]),
        ("1 2 3 4 5 6 7 8 9 1 2 3", ["1 2 3 4 5 6 7 8 9 1 2", "2 3 4 5 6 7 8 9 1 2 3"]),
        ("13  703 703 751", []),
    ],
)
def test_an_eleven_digit_number_is_any_window_of_whole_single_spaced_groups(
    text: str, runs: list[str]
) -> None:
    assert eleven_digit_runs(text) == runs


def test_a_stray_group_before_a_rival_abn_does_not_hide_it() -> None:
    """A count or page number printed before a checksum-valid ABN joins it into a 12-digit run; the
    reader still sees the 11 digits, so the window is a rival."""
    choice = LabelChoice({"BUSINESS_ABN": LabelPick(UNLABELLED)})
    extra = [_ink(f"Ref 7 {RIVAL}", (100, 900, 400, 916))]
    assert {r.rule for r in _check(page(choice, extra), choice)} == {"abn_rival"}


def test_an_unlabelled_value_moved_onto_neighbouring_ink_is_refused_by_overlap() -> None:
    """An unlabelled ABN recentres by its label's width; a line it then meets refuses it."""
    choice = LabelChoice({"BUSINESS_ABN": LabelPick(UNLABELLED)})
    extra = [_ink("Shop 4", (290, 52, 360, 68))]
    refusals = _check(page(choice, extra), choice)
    assert {r.rule for r in refusals} == {"overlap"} and "Shop 4" in refusals[0].evidence
    clear = [_ink("Shop 4", (300, 52, 360, 68))]
    assert _check(page(choice, clear), choice) == []


def test_one_refused_field_moves_per_pass_so_a_cross_field_refusal_spares_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two variant labels meeting refuse both fields; moving the first alone cures the second."""
    monkeypatch.setitem(LABEL_BOXES, "GST_AMOUNT", (500, 430, 560, 446))
    orders = {
        "BUSINESS_ABN": [LabelPick(LABELLED)],
        "GST_AMOUNT": [LabelPick(VARIANT, "TAX"), LabelPick(UNLABELLED), LabelPick(LABELLED)],
        "TOTAL_AMOUNT": [LabelPick(VARIANT, "AMOUNT"), LabelPick(LABELLED)],
    }
    first = LabelChoice({"GST_AMOUNT": orders["GST_AMOUNT"][0], "TOTAL_AMOUNT": orders["TOTAL_AMOUNT"][0]})
    assert {(r.field, r.rule) for r in _check(page(first), first)} == {
        ("GST_AMOUNT", "overlap"),
        ("TOTAL_AMOUNT", "overlap"),
    }
    monkeypatch.setattr(render_module, "draw_label_orders", lambda *args: orders)
    _, outcome = render_labelled_varied(
        _renderer([], {}), ENTRY, LAYOUT, doc_type="receipt", config=load_label_config()
    )
    assert outcome.choice["GST_AMOUNT"] == LabelPick(UNLABELLED)
    assert outcome.choice["TOTAL_AMOUNT"] == LabelPick(VARIANT, "AMOUNT")
    assert [(r.field, r.rule) for r in outcome.refusals] == [("GST_AMOUNT", "overlap")]


def test_independent_refusals_are_each_recorded_once() -> None:
    """Two fields refused for reasons of their own both end labelled, each refusal recorded once."""
    orders = {
        "BUSINESS_ABN": [LabelPick(LABELLED)],
        "GST_AMOUNT": [LabelPick(VARIANT, "TAX"), LabelPick(LABELLED)],
        "TOTAL_AMOUNT": [LabelPick(UNLABELLED), LabelPick(LABELLED)],
    }
    extra = [
        _ink("Tax invoice copy", (100, 700, 300, 716), role="distractor"),
        _ink("You saved $5.00", (550, 480, 750, 496), role="distractor"),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_module, "draw_label_orders", lambda *args: orders)
        _, outcome = render_labelled_varied(
            _renderer(extra, {}), ENTRY, LAYOUT, doc_type="receipt", config=load_label_config()
        )
    assert dict(outcome.choice) == {f: LabelPick(LABELLED) for f in SITES}
    assert [(r.field, r.rule) for r in outcome.refusals] == [
        ("GST_AMOUNT", "collision"),
        ("TOTAL_AMOUNT", "total_not_last"),
    ]


def test_a_barcode_payload_counts_as_a_printed_number() -> None:
    choice = LabelChoice({"BUSINESS_ABN": LabelPick(UNLABELLED)})
    mark = InkRecord("distractor", (100, 800, 400, 860), True, RIVAL.replace(" ", ""), 0, False)
    assert {r.rule for r in _check(page(choice, [mark]), choice)} == {"abn_rival"}


def test_a_second_printing_of_the_same_abn_is_no_rival() -> None:
    choice = LabelChoice({"BUSINESS_ABN": LabelPick(UNLABELLED)})
    assert _check(page(choice, [_ink(f"Remit to ABN {ABN}", (100, 900, 400, 916))]), choice) == []


def test_collision_ignores_connectives_numbers_and_plain_text() -> None:
    ignore = load_label_config().ignore_words
    assert wording_words("Total (inc GST):", ignore["TOTAL_AMOUNT"]) == {"total", "gst"}
    assert wording_words("ABN No. ", ignore["BUSINESS_ABN"]) == {"abn"}
    assert wording_words("GST 10%", ignore["GST_AMOUNT"]) == {"gst"}
    choice = LabelChoice({"TOTAL_AMOUNT": LabelPick(VARIANT, "AMOUNT")})
    # A plain text line ('Amount' as a table heading) and a scored line never collide.
    extra = [_ink("Amount", (100, 700, 200, 716)), _ink("Amount 5", (100, 740, 200, 756), role="scored")]
    assert _check(page(choice, extra), choice) == []


def test_an_unknown_varied_field_has_no_rule_and_is_refused_four_part() -> None:
    config = load_label_config()
    widened = dataclasses.replace(config, doc_types={**config.doc_types, "receipt": ("SUPPLIER_NAME",)})
    geometry = page(LabelChoice({}))
    geometry["boxes"]["SUPPLIER_NAME"] = [0.1, 0.1, 0.2, 0.12]
    with pytest.raises(ValueError) as err:
        check_label_choice(
            geometry,
            LabelChoice({"SUPPLIER_NAME": LabelPick(UNLABELLED)}),
            ENTRY,
            case_id="CASE900",
            layout_id="synthetic_totals",
            doc_type="receipt",
            config=widened,
            policy=load_policy(),
        )
    assert_diagnostic_error(err.value)


def test_ignored_words_are_per_field() -> None:
    """'number' is ignored beside the ABN only: a total wording naming a number collides with an
    identifier line."""
    ignore = load_label_config().ignore_words
    assert "number" in ignore["BUSINESS_ABN"] and "number" not in ignore["TOTAL_AMOUNT"]
    extra = [_ink("Invoice number: 4471", (100, 700, 300, 716), role="distractor")]
    total = LabelChoice({"TOTAL_AMOUNT": LabelPick(VARIANT, "TOTAL NUMBER")})
    assert {r.rule for r in _check(page(total, extra), total)} == {"collision"}
    abn = LabelChoice({"BUSINESS_ABN": LabelPick(VARIANT, "ABN NUMBER: ")})
    assert _check(page(abn, extra), abn) == []
