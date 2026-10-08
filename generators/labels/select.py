"""Per-case label orders: for each varied field, every candidate a render may draw, best first.

Each case draws from one stream, `random.Random` seeded by the first eight bytes of
`sha256("{case_id}:labels:{doc_type}")`, a domain of its own, so no distractor, overlay or position draw
moves when labels vary. For each varied field, in the YAML order of `label_variation.doc_types.<type>`,
the stream yields a weighted ordering of the three styles (draw one by weight, remove it, repeat) and then
a shuffled ordering of the field's site pool. Expanding the style order, `variant` into one candidate per
wording in that shuffled order, gives the field's candidate order; it is cut after `labelled`, which is
never refused. A render draws each field's first candidate, and a refusal moves only that field to its
next one. Nothing is drawn after the orders, so one field's redraw never shifts another's draws. A
wording the case's state withholds (`label_variation.withheld_by_state`, a paid invoice's `Balance:`) is
set aside after the shuffle, so withholding it moves no other draw.
"""

import hashlib
import random

from generators.distractors.config import load_distractor_config
from generators.distractors.providers import draw_states
from generators.labels.config import LabelConfig
from generators.labels.keys import LABEL_STYLES, LABELLED, VARIANT, LabelPick

SEED_DOMAIN = "labels"


def label_seed(case_id: str, doc_type: str) -> int:
    """Return a case's label seed.

    Args:
        case_id: The case, e.g. `CASE001`.
        doc_type: The extraction-schema document type, e.g. `receipt`.

    Returns:
        The first eight bytes of `sha256(f"{case_id}:labels:{doc_type}")` as a big-endian integer.
    """
    digest = hashlib.sha256(f"{case_id}:{SEED_DOMAIN}:{doc_type}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _style_order(rng: random.Random, weights: dict[str, float]) -> list[str]:
    """Draw the three styles one at a time by weight, without replacement."""
    remaining = list(LABEL_STYLES)
    order: list[str] = []
    while remaining:
        picked = rng.choices(remaining, weights=[weights[style] for style in remaining])[0]
        order.append(picked)
        remaining.remove(picked)
    return order


def draw_label_orders(
    case_id: str, doc_type: str, sites: dict, config: LabelConfig
) -> dict[str, list[LabelPick]]:
    """Return, per varied field, the candidates a render draws in turn, ending in `labelled`.

    Args:
        case_id: The case.
        doc_type: The extraction-schema document type.
        sites: The layout's `label_styles` sites (field -> {labelled, pool}), validated by the layout
            schema to name exactly the type's varied fields.
        config: The label configuration (weights, pools, varied fields).

    Returns:
        Field -> its candidate order, in the YAML order of the type's varied fields; empty for a type
        that varies none. Every order holds `labelled` exactly once, as its last candidate.

    Raises:
        KeyError: A varied field has no site (the layout schema refuses such a layout first).
    """
    rng = random.Random(label_seed(case_id, doc_type))
    withheld = config.withheld_for(
        doc_type, draw_states(load_distractor_config(config.path), case_id, doc_type)
    )
    orders: dict[str, list[LabelPick]] = {}
    for field in config.doc_types[doc_type]:
        weights = config.weights[field]
        styles = _style_order(rng, {style: weights.of(style) for style in LABEL_STYLES})
        wordings = list(config.pools[sites[field]["pool"]])
        rng.shuffle(wordings)
        # Set aside after the shuffle, so a withheld wording moves no other draw.
        wordings = [wording for wording in wordings if wording not in withheld]
        order: list[LabelPick] = []
        for style in styles:
            if style == VARIANT:
                order.extend(LabelPick(VARIANT, wording) for wording in wordings)
            else:
                order.append(LabelPick(style))
            if style == LABELLED:
                break
        orders[field] = order
    return orders
