"""The PAID stamp on paid invoices: its catalogue rules, its payment state, its drawing and its refusals.

Covers the overlay plan's Task 2: the `pick` and `link_date` rules (Decision 2), the stamp in the paid
group only, the stamp primitive (colour, opacity, rotation, speckle and candidate order on the overlay
seed, Decision 8; text recorded as distractor ink, Decision 7), and the fixtures that must fail: a stamp
whose only candidate sits on TOTAL_AMOUNT, and one over a barcode (Review Focus 1). The slow tests render
every invoice case with its own selection, and on every layout with every paid-group item forced.
"""

import copy
import dataclasses
import math
import random
from typing import Any
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image, ImageChops

from generators.distractors import providers, values
from generators.distractors.config import load_distractor_config
from generators.distractors.paths import DISTRACTORS_PATH, LINKS_PATH
from generators.distractors.providers import DistractorProviderError, draw_all_eligible, item_seed
from generators.distractors.values import FAIL_AT_ONCE, REDRAWABLE, RuleContext, ValueRuleError, draw_values
from generators.exporters.geometry import InkRecord
from generators.invoice import render_invoice
from generators.overflow_check import check_overflow
from generators.overlay import registry
from generators.overlay.engine import overlay_seed
from generators.overlay.stamp import NO_DATE, compose, draw_stamp
from generators.overlay.verify import OverlayError, protected_boxes
from tests.conftest import assert_diagnostic_error
from tests.distractor_helpers import decoded, force_every_item, selected
from tests.overlay.overlay_helpers import context
from tests.renderer_helpers import corpus_entries, layouts_for
from tests.test_invoice_distractors import force_state

DOC = "invoice"
CONFIG = load_distractor_config()
WORDS = ["PAID", "PAID IN FULL", "RECEIVED"]
PAYMENT = CONFIG.states[DOC]["payment"]
UNPAID_KEYS = {k for i in PAYMENT.groups["unpaid"] for k in CONFIG.items[i].emits}
STAMP_KEYS = {"DX_PAID_STAMP", "DX_PAID_DATE"}


def _stamped_case(*, dated: bool) -> str:
    """The first corpus invoice whose own selection draws the stamp, with or without its date."""
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        if "DX_PAID_STAMP" in chosen and ("DX_PAID_DATE" in chosen) == dated:
            return case_id
    raise AssertionError(f"no corpus invoice draws the stamp {'with' if dated else 'without'} a date")


DATED = _stamped_case(dated=True)
UNDATED = _stamped_case(dated=False)


# --- the catalogue ----------------------------------------------------------------------------------


def test_the_catalogue_declares_the_stamp_and_its_date() -> None:
    stamp, date = CONFIG.items["in_paid_stamp"], CONFIG.items["in_paid_date"]
    assert (stamp.kind, stamp.emits, stamp.requires) == ("clutter", ("DX_PAID_STAMP",), ())
    assert stamp.rule == {"type": "pick", "values": WORDS}
    assert (date.kind, date.field, date.emits) == ("repeat", "INVOICE_DATE", ("DX_PAID_DATE",))
    assert date.requires == ("in_paid_stamp",) and date.rule == {"type": "link_date"}
    assert {"in_paid_stamp", "in_paid_date"} <= set(PAYMENT.groups["paid"])
    assert not {"in_paid_stamp", "in_paid_date"} & set(PAYMENT.groups["unpaid"])
    # A pick draws from the generator, so a collision redraws; the date copies the links file.
    assert "pick" in REDRAWABLE and "link_date" in FAIL_AT_ONCE


def test_every_invoice_layout_lists_both_stamp_items() -> None:
    for layout_id, layout in layouts_for(DOC).items():
        assert {"in_paid_stamp", "in_paid_date"} <= set(layout["distractor_items"]), layout_id


# --- pick -------------------------------------------------------------------------------------------


def test_pick_is_deterministic_per_case_and_within_its_list() -> None:
    seen = set()
    for case_id, entry in corpus_entries(DOC).items():
        word = draw_all_eligible(entry, DOC)["DX_PAID_STAMP"]
        assert word == draw_all_eligible(entry, DOC)["DX_PAID_STAMP"], case_id
        # The first draw on the item's own seed: what any selection holding the item prints.
        assert word == random.Random(item_seed(case_id, DOC, "in_paid_stamp")).choice(WORDS), case_id
        seen.add(word)
    assert seen == set(WORDS)


def _ctx(seed: int = 0) -> RuleContext:
    engine = providers._engine()
    return RuleContext(
        rng=random.Random(seed),
        fields={},
        engine=engine,
        pools=engine.pools,
        case_id="CASE999",
        rows=None,
        selected={},
    )


@pytest.mark.parametrize("bad", [[], [""], "PAID", [1], ["PAID", None]])
def test_pick_refuses_a_list_that_is_not_of_non_empty_strings(bad: object) -> None:
    with pytest.raises(ValueRuleError) as err:
        draw_values(_ctx(), {"type": "pick", "values": bad})
    assert_diagnostic_error(err.value)
    assert "rule.values" in str(err.value) and "non-empty list of non-empty strings" in str(err.value)


@pytest.mark.parametrize("bad", [[], [""], "PAID", [1]])
def test_the_loader_refuses_a_pick_rule_with_bad_values(tmp_path: Path, bad: object) -> None:
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["items"]["in_paid_stamp"]["rule"]["values"] = bad
    path = tmp_path / "distractors.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError) as err:
        load_distractor_config(path)
    assert_diagnostic_error(err.value)
    assert "items.in_paid_stamp.rule.values" in str(err.value)


def test_a_pick_that_collides_with_a_scored_value_redraws() -> None:
    """A payer named 'Paid' is contained in PAID and PAID IN FULL, so every case redraws to RECEIVED."""
    words = set()
    for entry in list(corpus_entries(DOC).values())[:12]:
        renamed = {**entry, "fields": {**entry["fields"], "PAYER_NAME": "Paid"}}
        words.add(draw_all_eligible(renamed, DOC)["DX_PAID_STAMP"])
    assert words == {"RECEIVED"}


# --- link_date --------------------------------------------------------------------------------------


def test_link_date_is_the_links_bank_date_and_the_invoice_date_for_every_invoice() -> None:
    links = yaml.safe_load(LINKS_PATH.read_text())
    for case_id, entry in corpus_entries(DOC).items():
        (link,) = links[f"{case_id}_{entry['layout']}.png"]
        date = draw_all_eligible(entry, DOC)["DX_PAID_DATE"]
        # The repeat check passes: the paying transaction's date is the invoice date.
        assert date == str(link["bank_date"]) == entry["fields"]["INVOICE_DATE"], case_id


def test_link_date_reads_the_cases_own_invoice_image_whatever_layout_draws_it() -> None:
    case_id, entry = next(iter(corpus_entries(DOC).items()))
    other = next(layout_id for layout_id in layouts_for(DOC) if layout_id != entry["layout"])
    moved = {**entry, "layout": other}
    assert draw_all_eligible(moved, DOC)["DX_PAID_DATE"] == entry["fields"]["INVOICE_DATE"]


def _links_edited(monkeypatch: pytest.MonkeyPatch, edit) -> tuple[str, dict]:  # noqa: ANN001
    case_id, entry = next(iter(corpus_entries(DOC).items()))
    key = f"{case_id}_{entry['layout']}.png"
    links = copy.deepcopy(values._load_links(LINKS_PATH))
    edit(links, key, case_id)
    monkeypatch.setattr(values, "_load_links", lambda path: links)
    return key, entry


@pytest.mark.parametrize(
    ("edit", "tokens"),
    [
        (lambda links, key, case: links.pop(key), ("no invoice image", "_<invoice layout>.png")),
        (lambda links, key, case: links[key].append(dict(links[key][0])), ("carries 2 links", "{key}")),
        (lambda links, key, case: links.__setitem__(key, []), ("carries 0 links", "{key}")),
        (
            lambda links, key, case: links.__setitem__(f"{case}_tax_invoice_mixed.png", links[key]),
            ("2 invoice images", "_tax_invoice_mixed.png"),
        ),
    ],
    ids=["missing", "duplicate", "empty", "two_images"],
)
def test_a_missing_or_duplicate_link_fails_four_part(
    monkeypatch: pytest.MonkeyPatch,
    edit,
    tokens: tuple,  # noqa: ANN001
) -> None:
    key, entry = _links_edited(monkeypatch, edit)
    with pytest.raises(DistractorProviderError) as err:
        draw_all_eligible(entry, DOC)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    assert "in_paid_date" in message and f"{LINKS_PATH} -> " in message
    for token in tokens:
        assert token.replace("{key}", key) in message, (token, message)


def test_a_link_paid_on_another_day_fails_the_repeat_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decision 2: a reseed that pays invoices later fails at the repeat check, prompting a change of kind."""

    def later(links: dict, key: str, case: str) -> None:
        links[key] = [{**links[key][0], "bank_date": "01/01/2099"}]

    _, entry = _links_edited(monkeypatch, later)
    with pytest.raises(ValueError) as err:
        draw_all_eligible(entry, DOC)
    assert_diagnostic_error(err.value)
    assert "01/01/2099" in str(err.value) and "INVOICE_DATE" in str(err.value)


# --- the payment state ------------------------------------------------------------------------------


def test_no_case_selects_the_stamp_unless_paid_and_never_beside_an_unpaid_item() -> None:
    stamped = 0
    for case_id, entry in corpus_entries(DOC).items():
        chosen = set(selected(entry, layouts_for(DOC)[entry["layout"]]))
        state = providers.draw_states(CONFIG, case_id, DOC)["payment"]
        if chosen & STAMP_KEYS:
            stamped += 1
            assert state == "paid" and not chosen & UNPAID_KEYS, (case_id, sorted(chosen))
    assert stamped > 0


def test_an_unpaid_invoice_never_selects_the_stamp_even_with_every_item_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_state(monkeypatch, "unpaid")
    force_every_item(monkeypatch)
    for case_id, entry in corpus_entries(DOC).items():
        for layout_id, layout in layouts_for(DOC).items():
            chosen = selected({**entry, "layout": layout_id}, layout)
            assert not set(chosen) & STAMP_KEYS, (case_id, layout_id)


def test_with_every_paid_item_forced_every_case_selects_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    force_state(monkeypatch, "paid")
    force_every_item(monkeypatch)
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        assert STAMP_KEYS <= set(chosen), case_id
        assert not set(chosen) & UNPAID_KEYS, case_id
        assert chosen["DX_PAID_DATE"] == entry["fields"]["INVOICE_DATE"], case_id


# --- the stamp primitive, on a blank page --------------------------------------------------------------


PAGE = (1900, 3508)


def _block(**changes: object) -> dict:
    block = copy.deepcopy(layouts_for(DOC)["tax_invoice_standard"]["overlay"][0])
    block.update(changes)
    return block


def _blank_ctx(fields: dict, ink: list[InkRecord] | None = None, seed: int = 0):  # noqa: ANN202
    ctx = context(Image.new("RGB", PAGE, "white"), ink or [])
    entry = {"case_id": "CASE999", "fields": fields}
    rng = random.Random(seed)
    return dataclasses.replace(ctx, entry=entry, rng=rng)


FIELDS = {"DX_PAID_STAMP": "PAID IN FULL", "DX_PAID_DATE": "15/03/2031"}


def _replayed(seed: int, candidates: list) -> tuple:
    """The draws `draw_stamp` makes from its overlay seed, in its documented order."""
    stamp = CONFIG.overlay.stamp
    rng = random.Random(seed)
    colour = stamp.colours[rng.randrange(len(stamp.colours))]
    opacity = rng.uniform(*stamp.opacity)
    angle = rng.uniform(*stamp.rotation_deg)
    speckle = rng.uniform(*stamp.speckle)
    rng.getrandbits(64)
    rng.uniform(*stamp.inking_gradient)
    rng.uniform(0, 2 * math.pi)
    order = list(range(len(candidates)))
    rng.shuffle(order)
    nudges = {
        i: (rng.uniform(-1, 1) * stamp.jitter_px, rng.uniform(-1, 1) * stamp.jitter_px) for i in order
    }
    return colour, opacity, angle, speckle, order, nudges


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_colour_opacity_rotation_speckle_and_order_come_from_the_overlay_seed(seed: int) -> None:
    candidates = [{"x": 0.3, "y": 0.2}, {"x": 0.5, "y": 0.5}, {"x": 0.7, "y": 0.8}]
    block = _block(candidates=candidates)
    drawing = draw_stamp(block, _blank_ctx(FIELDS, seed=seed))
    colour, opacity, angle, speckle, order, nudges = _replayed(seed, candidates)
    stamp = CONFIG.overlay.stamp
    assert colour in stamp.colours
    layer = np.asarray(drawing.layer)
    inked = layer[..., 3] > 0
    # One ink colour, at an alpha no higher than the drawn opacity.
    assert {tuple(px) for px in layer[inked][:, :3]} == {colour}
    assert layer[..., 3].max() <= round(255 * opacity)
    # The rotated art at the drawn angle, less the speckled pixels, is exactly the layer's ink.
    art = compose(stamp, FIELDS["DX_PAID_STAMP"], FIELDS["DX_PAID_DATE"], angle)
    covered = int((np.asarray(art.ink) > 0).sum())
    assert int(inked.sum()) == covered - round(speckle * covered)
    # Every candidate is legal on a blank page, so the first in the drawn order is used.
    # The first candidate in the drawn order, moved by its drawn nudge (a blank page blocks nothing).
    first, (dx, dy) = candidates[order[0]], nudges[order[0]]
    left, top, right, bottom = drawing.marks[0].box
    centre = ((left + right) / 2, (top + bottom) / 2)
    assert (
        abs(centre[0] - first["x"] * PAGE[0] - dx) <= 3 and abs(centre[1] - first["y"] * PAGE[1] - dy) <= 3
    )


def test_the_same_seed_draws_the_same_stamp_and_another_seed_another() -> None:
    a = draw_stamp(_block(), _blank_ctx(FIELDS, seed=7)).layer
    b = draw_stamp(_block(), _blank_ctx(FIELDS, seed=7)).layer
    c = draw_stamp(_block(), _blank_ctx(FIELDS, seed=8)).layer
    assert a is not None and b is not None and c is not None
    assert a.tobytes() == b.tobytes() and a.tobytes() != c.tobytes()


def test_the_stamp_records_one_text_line_per_line_and_no_mark() -> None:
    dated = draw_stamp(_block(), _blank_ctx(FIELDS))
    undated = draw_stamp(_block(), _blank_ctx({"DX_PAID_STAMP": "PAID IN FULL"}))
    no_date_key = draw_stamp(_block(date_key=NO_DATE), _blank_ctx(FIELDS))
    assert len(dated.text_ink) == 2 and dated.mark_ink == ()
    assert [line.text for line in dated.text_ink] == [FIELDS["DX_PAID_STAMP"], FIELDS["DX_PAID_DATE"]]
    assert all(line.size > 0 for line in dated.text_ink)
    assert len(undated.text_ink) == 1 and len(no_date_key.text_ink) == 1
    for drawing in (dated, undated, no_date_key):
        (mark,) = drawing.marks
        assert mark.kind == "stamp" and mark.opaque and mark.key == "DX_PAID_STAMP"
        # Each text line lies inside the stamp's footprint.
        for left, top, right, bottom in (line.box for line in drawing.text_ink):
            assert (
                mark.box[0] <= left
                and mark.box[1] <= top
                and right <= mark.box[2]
                and bottom <= mark.box[3]
            )


def test_a_rotated_stamp_is_turned_within_the_configured_range() -> None:
    stamp = CONFIG.overlay.stamp
    flat = compose(stamp, "PAID", "15/03/2031", 0.0).ink.size
    for seed in range(6):
        angle = _replayed(seed, [{}])[2]
        assert stamp.rotation_deg[0] <= angle <= stamp.rotation_deg[1]
        turned = compose(stamp, "PAID", "15/03/2031", angle).ink.size
        assert turned[1] >= flat[1]


def test_a_candidate_whose_footprint_meets_a_protected_box_is_passed_over() -> None:
    candidates = [{"x": 0.3, "y": 0.2}, {"x": 0.7, "y": 0.8}]
    centre = (round(0.3 * PAGE[0]), round(0.2 * PAGE[1]))
    scored = InkRecord(
        "scored", (centre[0] - 10, centre[1] - 5, centre[0] + 10, centre[1] + 5), False, "x", 10, False
    )
    for seed in range(8):
        drawing = draw_stamp(_block(candidates=candidates), _blank_ctx(FIELDS, [scored], seed=seed))
        left, top, right, bottom = drawing.marks[0].box
        assert abs((left + right) / 2 - 0.7 * PAGE[0]) <= CONFIG.overlay.stamp.jitter_px + 3, seed


def test_with_no_legal_candidate_every_candidate_and_its_blockers_are_named() -> None:
    candidates = [{"x": 0.3, "y": 0.2}, {"x": 0.01, "y": 0.5}]
    centre = (round(0.3 * PAGE[0]), round(0.2 * PAGE[1]))
    scored = InkRecord(
        "scored", (centre[0] - 10, centre[1] - 5, centre[0] + 10, centre[1] + 5), False, "x", 10, False
    )
    with pytest.raises(OverlayError) as err:
        draw_stamp(_block(candidates=candidates), _blank_ctx(FIELDS, [scored]))
    message = str(err.value)
    assert_diagnostic_error(err.value)
    assert "none of its 2 candidate position(s)" in message
    assert "candidates[0] {x: 0.3, y: 0.2}" in message and f"scored ink {scored.box}" in message
    assert "candidates[1] {x: 0.01, y: 0.5}" in message and "overlay.page_inset_px" in message
    assert "synthetic_layout.overlay[0].candidates" in message and "overlay.stamp" in message


# --- the stamp on real pages -------------------------------------------------------------------------


def _render(case_id: str, layout: dict | None = None, **kwargs: Any) -> Image.Image:
    entry = corpus_entries(DOC)[case_id]
    return render_invoice(entry, layout or layouts_for(DOC)[entry["layout"]], distractors=True, **kwargs)


@pytest.mark.parametrize("dated", [True, False])
def test_a_stamped_page_adds_only_the_stamp_and_records_its_text(dated: bool) -> None:
    case_id = DATED if dated else UNDATED
    layout = layouts_for(DOC)[corpus_entries(DOC)[case_id]["layout"]]
    geometry: dict = {}
    stamped = _render(case_id, geometry_out=geometry)
    bare_geometry: dict = {}
    bare = _render(case_id, {**layout, "overlay": []}, geometry_out=bare_geometry)
    # The stamp's lines are the last ink, distractor text, and the scored boxes are unchanged.
    added = geometry["ink"][len(bare_geometry["ink"]) :]
    assert geometry["ink"][: len(bare_geometry["ink"])] == bare_geometry["ink"]
    assert len(added) == (2 if dated else 1) and all(r.role == "distractor" and not r.mark for r in added)
    assert geometry["boxes"] == bare_geometry["boxes"]
    # Every changed pixel lies in the stamp, clear of every padded scored box and code mark.
    diff = ImageChops.difference(stamped, bare).convert("L")
    changed = np.asarray(diff) > 0
    assert changed.any()
    clearance = CONFIG.overlay.clearance_px
    for entry in protected_boxes(bare_geometry["ink"], "scored_and_marks", clearance):
        left, top, right, bottom = entry.box
        assert not changed[max(top, 0) : bottom, max(left, 0) : right].any(), entry.describe()
    box = diff.getbbox()
    assert box is not None
    for left, top, right, bottom in (r.box for r in added):
        assert box[0] <= left and box[1] <= top and right <= box[2] and bottom <= box[3]
    assert _render(case_id).tobytes() == stamped.tobytes()


def test_the_stamp_draws_only_where_its_key_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = registry.OVERLAY_DRAWERS["stamp"]

    def spy(block: dict, ctx: registry.OverlayContext):  # noqa: ANN202
        case_id = ctx.entry["case_id"]
        calls.append(case_id)
        # Decision 8: the stamp block (the layout's overlay[0]) draws on its own overlay seed.
        assert ctx.rng.getstate() == random.Random(overlay_seed(case_id, DOC, 0)).getstate()
        return real(block, ctx)

    monkeypatch.setitem(registry.OVERLAY_DRAWERS, "stamp", spy)
    expected = []
    for case_id, entry in list(corpus_entries(DOC).items())[:20]:
        if "DX_PAID_STAMP" in selected(entry, layouts_for(DOC)[entry["layout"]]):
            expected.append(case_id)
        _render(case_id)
    assert calls == expected and expected


def _centre_fraction(box: tuple[float, ...], *, normalised: bool) -> dict:
    left, top, right, bottom = box
    scale = (1.0, 1.0) if normalised else PAGE
    return {"x": (left + right) / 2 / scale[0], "y": (top + bottom) / 2 / scale[1]}


def test_a_stamp_whose_only_candidate_centres_on_the_total_fails_naming_it() -> None:
    """Review Focus 1: the fixture forcing a stamp onto TOTAL_AMOUNT fails with the four-part diagnostic."""
    entry = corpus_entries(DOC)[DATED]
    layout = copy.deepcopy(layouts_for(DOC)[entry["layout"]])
    geometry: dict = {}
    render_invoice(entry, {**layout, "overlay": []}, distractors=True, geometry_out=geometry)
    on_total = _centre_fraction(tuple(geometry["boxes"]["TOTAL_AMOUNT"]), normalised=True)
    layout["overlay"][0]["candidates"] = [on_total]
    with pytest.raises(OverlayError) as err:
        render_invoice(entry, layout, distractors=True)
    message = str(err.value)
    assert_diagnostic_error(err.value)
    for token in (
        DATED,
        entry["layout"],
        f"{entry['layout']}.overlay[0]",
        "candidates[0]",
        "[TOTAL_AMOUNT]",
    ):
        assert token in message, (token, message)
    assert "invoices.yml" in message


def test_a_stamp_whose_only_candidate_sits_on_a_barcode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stamp never shares a page with the slip; forced together here, the barcode still blocks it."""
    monkeypatch.setattr(providers, "excluded_items", lambda config, doc_type, states: set())
    monkeypatch.setattr(
        providers, "exclusive_excluded", lambda config, case_id, doc_type, candidates: set()
    )
    force_every_item(monkeypatch)
    entry = {**corpus_entries(DOC)[DATED], "layout": "tax_invoice_standard"}
    layout = copy.deepcopy(layouts_for(DOC)["tax_invoice_standard"])
    geometry: dict = {}
    image = render_invoice(entry, {**layout, "overlay": []}, distractors=True, geometry_out=geometry)
    assert decoded(image), "the forced page carries its remittance barcode"
    (barcode,) = [r.box for r in geometry["ink"] if r.mark and r.box[1] > PAGE[1] // 2]
    layout["overlay"][0]["candidates"] = [_centre_fraction(barcode, normalised=False)]
    with pytest.raises(OverlayError) as err:
        render_invoice(entry, layout, distractors=True)
    assert_diagnostic_error(err.value)
    assert f"code mark {barcode}" in str(err.value) and "candidates[0]" in str(err.value)


# --- slow: every case ---------------------------------------------------------------------------------


@pytest.mark.slow
def test_every_invoice_on_its_own_layout_renders_its_stamp_legally(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every one of the 55 invoices with its own selection: renders, places a selected stamp, passes the
    verifier (a refusal would raise) and decodes every barcode on the page."""
    placed: list[str] = []
    real = registry.OVERLAY_DRAWERS["stamp"]

    def spy(block: dict, ctx: registry.OverlayContext):  # noqa: ANN202
        drawing = real(block, ctx)
        placed.append(ctx.entry["case_id"])
        return drawing

    monkeypatch.setitem(registry.OVERLAY_DRAWERS, "stamp", spy)
    stamped = []
    for case_id, entry in corpus_entries(DOC).items():
        chosen = selected(entry, layouts_for(DOC)[entry["layout"]])
        image = _render(case_id)
        barcodes = [chosen["DX_REMIT_BARCODE"]] if "DX_REMIT_BARCODE" in chosen else []
        assert decoded(image) == barcodes, case_id
        if "DX_PAID_STAMP" in chosen:
            stamped.append(case_id)
    assert placed == stamped and stamped


def _with_paid_group(real):  # noqa: ANN001, ANN202
    """`select_items` with every paid-group item (and what each requires) added to the seeded selection."""

    def select(config, doc_type, eligible, rng, *, where):  # noqa: ANN001, ANN202
        chosen = set(real(config, doc_type, eligible, rng, where=where))
        for item_id in PAYMENT.groups["paid"]:
            if item_id in eligible:
                chosen |= {item_id, *providers.required_items(config, item_id)}
        return providers.draw_order(config, sorted(chosen & set(eligible)))

    return select


@pytest.mark.slow
@pytest.mark.parametrize("layout_id", list(layouts_for(DOC)))
def test_every_case_on_every_layout_with_the_paid_group_forced_places_its_stamp(
    monkeypatch: pytest.MonkeyPatch, layout_id: str
) -> None:
    force_state(monkeypatch, "paid")
    monkeypatch.setattr(providers, "select_items", _with_paid_group(providers.select_items))
    placed: list[str] = []
    real = registry.OVERLAY_DRAWERS["stamp"]

    def spy(block: dict, ctx: registry.OverlayContext):  # noqa: ANN202
        drawing = real(block, ctx)
        placed.append(ctx.entry["case_id"])
        return drawing

    monkeypatch.setitem(registry.OVERLAY_DRAWERS, "stamp", spy)
    layout = layouts_for(DOC)[layout_id]
    first = None
    for case_id, entry in corpus_entries(DOC).items():
        moved = {**entry, "layout": layout_id}
        chosen = selected(moved, layout)
        assert STAMP_KEYS <= set(chosen) and not set(chosen) & UNPAID_KEYS, case_id
        image = render_invoice(moved, layout, distractors=True)
        assert decoded(image) == [], (case_id, layout_id)
        first = first or (moved, image.tobytes())
    assert placed == list(corpus_entries(DOC))
    assert first is not None
    assert render_invoice(first[0], layout, distractors=True).tobytes() == first[1]


# --- validate collects overlay refusals -------------------------------------------------------------


def test_check_overflow_collects_every_overlay_refusal_whole_and_labelled_as_an_overlay() -> None:
    """Carried from Task 1: an OverlayError is collected (every case, not the first) with its whole
    four-part message, and is never described as a field-budget overflow."""
    layouts = copy.deepcopy(layouts_for(DOC))
    for layout in layouts.values():
        layout["overlay"][0]["candidates"] = [{"x": 0.5, "y": 0.01}]
    stamped = [
        case_id
        for case_id, entry in corpus_entries(DOC).items()
        if "DX_PAID_STAMP" in selected(entry, layouts_for(DOC)[entry["layout"]])
    ]
    violations = check_overflow(corpus_entries(DOC), layouts, render_invoice)
    assert len(violations) == len(stamped) > 1
    for case_id, line in zip(stamped, violations, strict=True):
        layout_id = corpus_entries(DOC)[case_id]["layout"]
        assert line.startswith(f"{case_id} / {layout_id} (with distractors): overlay refused:\n"), line
        assert_diagnostic_error(line)
        assert "overlay.page_inset_px" in line and "field_budgets" not in line


def test_links_in_memory_replace_the_links_file_only_while_open() -> None:
    """The seed dry run renders entries the committed links file does not describe; it hands their links in."""
    case_id, entry = next(iter(corpus_entries(DOC).items()))
    moved = {**entry, "fields": {**entry["fields"], "INVOICE_DATE": "15/03/2031"}}
    links = {f"{case_id}_{entry['layout']}.png": [{"bank_statement": "x.png", "bank_date": "15/03/2031"}]}
    with values.links_in_memory(links):
        assert draw_all_eligible(moved, DOC)["DX_PAID_DATE"] == "15/03/2031"
    with pytest.raises(ValueError, match="15/03/2031"):
        draw_all_eligible(moved, DOC)
