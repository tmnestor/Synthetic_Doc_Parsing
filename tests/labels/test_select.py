"""Per-case label orders: deterministic, on their own seed, each ending in `labelled` (Stage 6, Task 2)."""

import dataclasses
import hashlib
import random
from pathlib import Path

import pytest
import yaml

from generators.distractors.config import load_distractor_config
from generators.distractors.paths import DISTRACTORS_PATH
from generators.distractors.providers import case_seed, draw_states
from generators.labels.config import StyleWeights, load_label_config
from generators.labels.keys import LABEL_STYLES, LABELLED, UNLABELLED, VARIANT, LabelPick
from generators.labels.render import label_sites
from generators.labels.select import draw_label_orders, label_seed
from tests.renderer_helpers import corpus_entries, layouts_for

TYPES = ("receipt", "invoice")


def _cases(doc_type: str) -> list[tuple[str, dict]]:
    layouts = layouts_for(doc_type)
    return [(cid, label_sites(layouts[e["layout"]])) for cid, e in corpus_entries(doc_type).items()]


def test_the_seed_is_the_first_eight_bytes_of_the_case_label_digest() -> None:
    digest = hashlib.sha256(b"CASE007:labels:invoice").digest()
    assert label_seed("CASE007", "invoice") == int.from_bytes(digest[:8], "big")


def test_the_label_seed_is_not_the_distractor_seed() -> None:
    for doc_type in TYPES:
        for case_id, _ in _cases(doc_type):
            assert label_seed(case_id, doc_type) != case_seed(case_id, doc_type)


@pytest.mark.parametrize("doc_type", TYPES)
def test_orders_are_deterministic_and_end_in_labelled(doc_type: str) -> None:
    config = load_label_config()
    for case_id, sites in _cases(doc_type):
        first = draw_label_orders(case_id, doc_type, sites, config)
        assert first == draw_label_orders(case_id, doc_type, sites, config)
        assert list(first) == list(config.doc_types[doc_type])
        for field, order in first.items():
            assert order[-1] == LabelPick(LABELLED), (case_id, field)
            assert [pick.style for pick in order].count(LABELLED) == 1
            assert [pick.style for pick in order].count(UNLABELLED) <= 1
            variants = [pick.wording for pick in order if pick.style == VARIANT]
            states = draw_states(load_distractor_config(), case_id, doc_type)
            withheld = config.withheld_for(doc_type, states)
            pool = [w for w in config.pools[sites[field]["pool"]] if w not in withheld]
            # A variant style expands to every wording of the site's pool the page's state does not
            # withhold, each once.
            assert variants == [] or sorted(variants) == sorted(pool), (case_id, field)


def test_a_bank_statement_varies_nothing() -> None:
    assert draw_label_orders("CASE001", "bank_statement", {}, load_label_config()) == {}


def test_one_fields_weights_never_move_another_fields_order() -> None:
    """Every draw is made up front, so changing one field's weights leaves the others' orders alone."""
    config = load_label_config()
    weights = dict(config.weights)
    weights["BUSINESS_ABN"] = StyleWeights(labelled=1000.0, variant=0.001, unlabelled=0.001)
    skewed = dataclasses.replace(config, weights=weights)
    for doc_type in TYPES:
        for case_id, sites in _cases(doc_type):
            plain = draw_label_orders(case_id, doc_type, sites, config)
            moved = draw_label_orders(case_id, doc_type, sites, skewed)
            # The stream draws the same number of values whatever the weights, so later fields agree.
            for field in ("GST_AMOUNT", "TOTAL_AMOUNT"):
                assert moved[field] == plain[field], (case_id, field)


def test_weights_steer_the_first_style() -> None:
    config = load_label_config()
    for style in LABEL_STYLES:
        heavy = {s: (1000.0 if s == style else 0.001) for s in LABEL_STYLES}
        skewed = dataclasses.replace(
            config, weights={field: StyleWeights(**heavy) for field in config.weights}
        )
        firsts = [
            order[0].style
            for case_id, sites in _cases("receipt")
            for order in draw_label_orders(case_id, "receipt", sites, skewed).values()
        ]
        assert firsts.count(style) >= len(firsts) - 1, style


def test_the_style_order_is_a_weighted_draw_without_replacement() -> None:
    """The first style of each order matches a direct weighted draw on the same seed."""
    config = load_label_config()
    for case_id, sites in _cases("invoice"):
        rng = random.Random(label_seed(case_id, "invoice"))
        weights = config.weights["BUSINESS_ABN"]
        first = rng.choices(list(LABEL_STYLES), weights=[weights.of(s) for s in LABEL_STYLES])[0]
        order = draw_label_orders(case_id, "invoice", sites, config)["BUSINESS_ABN"]
        assert order[0].style == first, case_id


def test_a_paid_invoice_is_never_offered_balance_and_nothing_else_moves() -> None:
    """Ruling: 'Balance:' over a paid invoice's total reads as an amount owing; it is withheld from paid
    pages only, after the shuffle, so every other candidate keeps its place."""
    config = load_label_config()
    unwithheld = dataclasses.replace(config, withheld={**config.withheld, "invoice": {}})
    seen = {"paid": 0, "unpaid": 0}
    for case_id, sites in _cases("invoice"):
        state = draw_states(load_distractor_config(), case_id, "invoice")["payment"]
        orders = draw_label_orders(case_id, "invoice", sites, config)
        full = draw_label_orders(case_id, "invoice", sites, unwithheld)
        offered = {pick.wording for order in orders.values() for pick in order}
        if state == "paid":
            assert "Balance:" not in offered, case_id
            stripped = {f: [p for p in order if p.wording != "Balance:"] for f, order in full.items()}
            assert orders == stripped, case_id
            seen["paid"] += 1
        else:
            assert orders == full, case_id
            seen["unpaid"] += "Balance:" in offered
    assert seen["paid"] > 0 and seen["unpaid"] > 0, seen


def test_the_states_are_drawn_from_the_catalogue_the_label_config_came_from(tmp_path: Path) -> None:
    """A catalogue listing the payment choices the other way round draws every invoice's opposite state,
    so withholding follows that catalogue's draw, not the shipped one's."""
    data = yaml.safe_load(DISTRACTORS_PATH.read_text())
    data["states"]["invoice"]["payment"]["choices"] = ["unpaid", "paid"]
    other = tmp_path / "distractors.yml"
    other.write_text(yaml.safe_dump(data, sort_keys=False))
    alternate = load_label_config(other)
    assert alternate.path == other.resolve()
    withheld_on = offered_on = 0
    for case_id, sites in _cases("invoice"):
        shipped_state = draw_states(load_distractor_config(), case_id, "invoice")["payment"]
        assert draw_states(load_distractor_config(other), case_id, "invoice")["payment"] != shipped_state
        total = draw_label_orders(case_id, "invoice", sites, alternate)["TOTAL_AMOUNT"]
        wordings = {pick.wording for pick in total}
        if not any(pick.style == VARIANT for pick in total):
            continue
        # The alternate catalogue draws "paid" exactly where the shipped one draws "unpaid".
        if shipped_state == "unpaid":
            assert "Balance:" not in wordings, case_id
            withheld_on += 1
        else:
            offered_on += 1
    assert withheld_on > 0 and offered_on > 0
