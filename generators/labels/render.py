"""The labelled_varied render: draw a case's label choice, refuse what a reader could misread, redraw.

`render_labelled_varied` renders a distractor page with each varied field's first candidate (see
`generators.labels.select`), reads the page with `check_label_choice`, and on a refusal moves the first
refused field (in the type's varied order) to its next candidate and renders again, until the page is
accepted. One field moves per pass: a refusal one field's label causes in another's (two labels meeting)
is often cured by the first field's move, so the other keeps its candidate. `labelled` ends every order and
is never refused, so the loop always ends. What a refusal then does is `label_variation.refusal_policy`'s
to say: `redraw` records it in the returned `LabelOutcome` and continues; `fail` raises
`LabelRefusalError` on the first.
"""

from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from generators.distractors.paths import layout_file
from generators.drawn_values import load_policy
from generators.exporters.geometry import InkRecord
from generators.labels.config import LabelConfig
from generators.labels.keys import PROVIDER_NAME, SITES_PARAM, LabelChoice
from generators.labels.select import draw_label_orders
from generators.labels.unambiguity import Refusal, check_label_choice

REDRAW = "redraw"
FAIL = "fail"


class LabelRefusalError(ValueError):
    """A label choice was refused under `refusal_policy: fail`."""


@dataclass(frozen=True)
class LabelOutcome:
    """What a labelled_varied render drew, and what it refused on the way.

    Attributes:
        choice: The accepted choice, one pick per varied field (empty for a type that varies none).
        refusals: Every refusal, in the order met.
    """

    choice: LabelChoice
    refusals: tuple[Refusal, ...]


def label_sites(layout: dict) -> dict:
    """Return a layout's `label_styles` sites (field -> {labelled, pool}), or {} for a layout with none."""
    for spec in layout.get("field_providers", []):
        if isinstance(spec, dict) and spec.get("name") == PROVIDER_NAME:
            return dict(spec["params"][SITES_PARAM])
    return {}


def refusal_error(refusal: Refusal, *, doc_type: str, config: LabelConfig) -> LabelRefusalError:
    """Build the four-part diagnostic for a refusal met under `refusal_policy: fail`.

    Args:
        refusal: The refusal.
        doc_type: The extraction-schema document type, which names the layout file.
        config: The label configuration, which names the catalogue file.

    Returns:
        The error, ready to raise.
    """
    sites = f"{refusal.layout_id}.field_providers[{PROVIDER_NAME}].params.sites.{refusal.field}"
    return LabelRefusalError(
        "A label choice would leave its field ambiguous.\n"
        f"  What:     {refusal.describe()}.\n"
        f"  Where:    {config.path} -> label_variation.refusal_policy; "
        f"{layout_file(doc_type)} -> {sites}\n"
        "  Expected: refusal_policy: redraw (the field's next candidate is drawn and the refusal "
        "recorded), or a site and pool under which the field stays identifiable, e.g.\n"
        "              label_variation: {refusal_policy: redraw}\n"
        "  Recover:  set refusal_policy: redraw, or remove the wording from its pool, or move the "
        "near-miss out of the totals region."
    )


def render_labelled_varied(
    renderer: Callable[..., Image.Image],
    entry: dict,
    layout: dict,
    *,
    doc_type: str,
    config: LabelConfig,
    geometry_out: dict | None = None,
    ink_out: list[InkRecord] | None = None,
) -> tuple[Image.Image, LabelOutcome]:
    """Render a case's distractor page with its label choice, redrawing any refused field.

    Args:
        renderer: The document type's renderer, called as renderer(entry, layout, distractors=True,
            labels=..., geometry_out=...).
        entry: The ground-truth entry, carrying `case_id` and `layout`.
        layout: Its layout.
        doc_type: The extraction-schema document type.
        config: The label configuration (orders, rules' words, refusal policy).
        geometry_out: Optional; populated in place with the accepted render's geometry.
        ink_out: Optional; extended in place with the accepted render's ink.

    Returns:
        The accepted page, and its outcome. A layout without label sites (a bank statement) is drawn once
        with an empty choice, which is its distractor render.

    Raises:
        LabelRefusalError: Under `refusal_policy: fail`, on the first refusal.
    """
    case_id = str(entry["case_id"])
    layout_id = str(entry.get("layout", ""))
    policy = load_policy()
    sites = label_sites(layout)
    orders = draw_label_orders(case_id, doc_type, sites, config) if sites else {}
    position = dict.fromkeys(orders, 0)
    refusals: list[Refusal] = []
    while True:
        choice = LabelChoice({field: orders[field][position[field]] for field in orders})
        geometry: dict = {}
        image = renderer(entry, layout, distractors=True, labels=choice, geometry_out=geometry)
        met = check_label_choice(
            geometry,
            choice,
            entry,
            case_id=case_id,
            layout_id=layout_id,
            doc_type=doc_type,
            config=config,
            policy=policy,
        )
        if not met:
            break
        if config.refusal_policy == FAIL:
            raise refusal_error(met[0], doc_type=doc_type, config=config)
        # Only the first refused field moves; the others are judged again on the next page.
        field = met[0].field
        refusals.extend(refusal for refusal in met if refusal.field == field)
        position[field] += 1
    if geometry_out is not None:
        geometry_out.update(geometry)
    if ink_out is not None:
        ink_out.extend(geometry["ink"])
    return image, LabelOutcome(choice, tuple(refusals))
