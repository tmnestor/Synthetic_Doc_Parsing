"""The `label_styles` field provider: one `{LABEL_<FIELD>}` key per label site.

Each receipt and invoice layout declares, under `field_providers:`, a `label_styles` entry whose
`params.sites` names, per varied field, the wording the site prints today (`labelled:`) and the pool its
variants come from (`pool:`, a key of `label_variation.pools` in config/distractors.yml):

    {name: label_styles, params: {sites: {TOTAL_AMOUNT: {labelled: "TOTAL", pool: total_receipt}}}}

The provider only maps a choice onto keys; it never draws one. With no choice (a plain or distractor
render), or a field picked `labelled`, it emits the site's own wording, which is exactly what the site
printed before label sites existed, so those renders are unchanged. A `variant` emits the pick's wording
and `unlabelled` the empty string, which the site then draws as nothing.
"""

from generators.labels.config import load_label_config
from generators.labels.keys import (
    LABELLED,
    PROVIDER_NAME,
    SITES_PARAM,
    UNLABELLED,
    LabelChoice,
    label_key,
)
from generators.layout_dsl.provider_registry import field_provider


def _emits() -> tuple[str, ...]:
    """Every label key some document type varies, in YAML order."""
    doc_types = load_label_config().doc_types
    return tuple(dict.fromkeys(label_key(field) for fields in doc_types.values() for field in fields))


@field_provider(PROVIDER_NAME, params=frozenset({SITES_PARAM}), emits=_emits(), labels=True)
def label_styles(entry: dict, params: dict, *, choice: LabelChoice | None) -> dict[str, str]:
    """Emit `LABEL_<FIELD>` for every site in `params.sites`, worded as the render's choice says.

    Args:
        entry: The ground-truth entry; unused (a label never depends on a value).
        params: The layout's `params:`, carrying `sites` (validated by the layout schema).
        choice: The render's label choice, or None for a plain or distractor render.

    Returns:
        One key per site: the site's `labelled` wording when `choice` is None or picks `labelled` (or
        does not name the field), the pick's wording for `variant`, and "" for `unlabelled`.

    Raises:
        ValueError: The choice names a field this layout has no label site for.
    """
    sites: dict = params[SITES_PARAM]
    if choice is not None:
        unknown = sorted(set(choice) - set(sites))
        if unknown:
            raise ValueError(
                "A label choice names a field with no label site.\n"
                f"  What:     the choice picks {unknown}, but this layout's label_styles sites are "
                f"{sorted(sites)}, so the pick would be recorded and never drawn.\n"
                "  Where:    the layout's field_providers -> label_styles.params.sites; the LabelChoice "
                "handed to the renderer\n"
                f"  Expected: picks only for {sorted(sites)}, e.g. LabelChoice({{'TOTAL_AMOUNT': "
                "LabelPick('unlabelled')}).\n"
                f"  Recover:  drop {unknown} from the choice, or add a site for each to the layout."
            )
    emitted: dict[str, str] = {}
    for field, site in sites.items():
        pick = choice.get(field) if choice is not None else None
        if pick is None or pick.style == LABELLED:
            wording = str(site["labelled"])
        elif pick.style == UNLABELLED:
            wording = ""
        else:
            wording = pick.wording
        emitted[label_key(field)] = wording
    return emitted
