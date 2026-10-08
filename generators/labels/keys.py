"""The label-site vocabulary: the key prefix, the three styles, a region's name and a render's choice.

A leaf module: it imports nothing from `generators`, so the geometry recorder, the layout engine, the
binding helpers, the field-provider registry and the layout schema can all share it without an import
cycle.

A label site is a layout block that draws a scored field's label through a `{LABEL_<FIELD>}` placeholder
(`label: "{LABEL_TOTAL_AMOUNT}"` on a pair, `content: "{LABEL_BUSINESS_ABN}{BUSINESS_ABN}"` on a text
line). The `label_styles` field provider fills each such key; which wording it emits is chosen per field
by a `LabelChoice`, and with no choice it emits the wording the layout prints today.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

# The prefix every label key carries, e.g. LABEL_BUSINESS_ABN.
LABEL_PREFIX = "LABEL_"

# How a site draws its label: the layout's own wording, a wording from the site's pool, or none at all.
LABELLED = "labelled"
VARIANT = "variant"
UNLABELLED = "unlabelled"
LABEL_STYLES = (LABELLED, VARIANT, UNLABELLED)

# The label provider's registered name, its one params key, and every key a site under it carries (each
# required): `{name: label_styles, params: {sites: {TOTAL_AMOUNT: {labelled: TOTAL, pool:
# total_receipt}}}}`.
PROVIDER_NAME = "label_styles"
SITES_PARAM = "sites"
SITE_KEYS = ("labelled", "pool")

# The one region name a `stack` may declare (`region: totals`). Which fields' label sites must lie inside
# it is config/distractors.yml's to say (label_variation.totals_region_fields).
TOTALS_REGION = "totals"
REGION_NAMES = (TOTALS_REGION,)


def label_key(field: str) -> str:
    """Return the label key a site of `field` binds, e.g. `LABEL_TOTAL_AMOUNT`.

    Args:
        field: A scored field name.

    Returns:
        `LABEL_PREFIX` followed by the field name.
    """
    return f"{LABEL_PREFIX}{field}"


def label_placeholder(field: str) -> str:
    """Return the `{LABEL_<FIELD>}` placeholder a site of `field` writes in its template."""
    return "{" + label_key(field) + "}"


def is_label_site(template: str, field: str | None) -> bool:
    """Return whether a block bound to `field` draws that field's label through `template`.

    Args:
        template: The block's label-bearing template (a pair's `label`, a text block's `content`).
        field: The block's `field:` binding, or None for an unbound block.

    Returns:
        True when the block is bound and the template carries the field's own label placeholder.
    """
    return field is not None and label_placeholder(field) in template


@dataclass(frozen=True)
class LabelPick:
    """How one field's label is drawn on one render.

    Attributes:
        style: One of `LABEL_STYLES`.
        wording: The wording drawn for a `variant`; ignored for `labelled` (the site's own wording) and
            `unlabelled` (nothing). Empty for those two by convention.
    """

    style: str
    wording: str = ""

    def __post_init__(self) -> None:
        """Refuse an unknown style, or a variant with no wording to draw."""
        if self.style not in LABEL_STYLES:
            raise ValueError(
                "Unknown label style.\n"
                f"  What:     LabelPick style {self.style!r} is not one of {list(LABEL_STYLES)}.\n"
                "  Where:    the LabelChoice handed to the renderer (generators/labels/keys.py LabelPick)\n"
                f"  Expected: one of {list(LABEL_STYLES)}, e.g. LabelPick('variant', 'Amount due:').\n"
                "  Recover:  build the pick with a known style."
            )
        if self.style == VARIANT and not self.wording.strip():
            raise ValueError(
                "A variant label has no wording.\n"
                f"  What:     LabelPick('variant', {self.wording!r}) would draw nothing, which is the "
                "unlabelled style recorded under another name.\n"
                "  Where:    the LabelChoice handed to the renderer (generators/labels/keys.py LabelPick)\n"
                "  Expected: a wording from the site's pool in config/distractors.yml -> "
                "label_variation.pools, e.g. LabelPick('variant', 'Amount due:').\n"
                "  Recover:  pass a non-empty wording, or LabelPick('unlabelled')."
            )


class LabelChoice(Mapping[str, LabelPick]):
    """A frozen mapping of scored field to the `LabelPick` a render draws its label with.

    A field the choice does not name draws its `labelled` wording.
    """

    def __init__(self, picks: Mapping[str, LabelPick]) -> None:
        """Copy `picks`, refusing anything that is not a field name mapped to a `LabelPick`.

        Args:
            picks: Field name -> pick.

        Raises:
            TypeError: A key is not a string or a value is not a `LabelPick`.
        """
        for field, pick in picks.items():
            if not isinstance(field, str) or not isinstance(pick, LabelPick):
                raise TypeError(
                    "A label choice holds a malformed entry.\n"
                    f"  What:     {field!r} -> {pick!r} is not a field name mapped to a LabelPick.\n"
                    "  Where:    the LabelChoice handed to the renderer (generators/labels/keys.py)\n"
                    "  Expected: LabelChoice({'TOTAL_AMOUNT': LabelPick('unlabelled')}).\n"
                    "  Recover:  map each scored field name to a LabelPick."
                )
        self._picks = dict(picks)

    def __getitem__(self, field: str) -> LabelPick:
        """Return the pick for `field`."""
        return self._picks[field]

    def __iter__(self) -> Iterator[str]:
        """Iterate the fields, in the order given."""
        return iter(self._picks)

    def __len__(self) -> int:
        """Return how many fields the choice names."""
        return len(self._picks)

    def __hash__(self) -> int:
        """Hash by content, so equal choices hash equally."""
        return hash(tuple(sorted(self._picks.items(), key=lambda item: item[0])))

    def __repr__(self) -> str:
        """Show the picks."""
        return f"LabelChoice({self._picks!r})"
