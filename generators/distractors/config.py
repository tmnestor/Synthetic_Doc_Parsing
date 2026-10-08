"""Fail-fast loader for the distractor catalogue, `config/distractors.yml`.

Every key is required and nothing is defaulted here. Each failure is a `DistractorConfigError` carrying
What / Where / Expected / Recover lines, with the absolute file path and the dotted key.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from generators.common import FONT_FAMILIES, vendored_weights
from generators.distractors.keys import DX_PREFIX
from generators.distractors.paths import DATA_POOLS_PATH, DISTRACTORS_PATH
from generators.distractors.values import POOL_PARAM, RULE_PARAMS, RULE_TYPES, rule_arity
from generators.exporters.eval_projection import load_extraction_schema

ITEM_KINDS = ("clutter", "near_miss", "repeat")

_TOP_LEVEL = (
    "selection",
    "max_redraws",
    "validator",
    "density",
    "states",
    "exclusive",
    "overlay",
    "items",
    # Required here so the catalogue is refused whole when it is missing; generators/labels/config.py
    # validates its contents.
    "label_variation",
)
_STATE_KEYS = ("choices", "items")
_DENSITY_KEYS = ("scored_ink_ratio_max", "distractor_share_min", "page_fill_min")
# The one density bound an explicit YAML null may switch off: a page cropped to its content (a receipt)
# has a fill near 1 by construction, so the bound would say nothing.
_NULLABLE_DENSITY_KEYS = frozenset({"page_fill_min"})
_ITEM_KEYS = ("doc_types", "kind", "emits", "requires", "rule")
# What opaque overlay ink may not touch, per document type (Decision 4 of the overlay plan): the scored
# boxes and code marks, or every recorded ink box of every role. `no_overlay` says the type carries no
# overlay at all: its layouts declare `overlay: []`, and validation and the renderer refuse any block.
NO_OVERLAY = "no_overlay"
OVERLAY_PROTECT_POLICIES = (NO_OVERLAY, "scored_and_marks", "all_ink")
_ANNOTATION_KEYS = ("highlighter", "ink", "handwriting")
_OVERLAY_KEYS = ("clearance_px", "page_inset_px", "protect", "contrast", "stamp", *_ANNOTATION_KEYS)
_HIGHLIGHTER_KEYS = (
    "colours",
    "opacity",
    "pad_px",
    "end_jitter_px",
    "edge_jitter_px",
    "density_wobble",
    "end_density",
    "end_length_px",
)
_INK_KEYS = ("colours", "stroke_px", "jitter_px", "tick", "circle", "underline")
# Each pen shape's keys; every one is required.
_INK_SHAPE_KEYS = {
    "tick": ("size_px", "start", "knee", "tip", "bow", "taper", "spacing_px"),
    "circle": ("gap_px", "overrun", "taper", "spacing_px"),
    "underline": ("gap_px", "lead_jitters", "taper", "spacing_px"),
}
_TICK_POINTS = ("start", "knee", "tip")
_HANDWRITING_KEYS = ("hands", "size_px", "glyph_jitter", "step_away", "below_column_dx_px")
_GLYPH_JITTER_KEYS = ("size_px", "slant_deg", "baseline_px")
_HIGHLIGHTER_EXAMPLE = (
    'highlighter: {colours: {yellow: "#FFF59D", green: "#CCF5A8"}, opacity: [0.6, 0.85], pad_px: 6, '
    "end_jitter_px: 10, edge_jitter_px: 3, density_wobble: 0.1, end_density: 1.3, end_length_px: 40}"
)
_INK_EXAMPLE = (
    'ink: {colours: ["#1B2F80", "#202020"], stroke_px: [3, 5], jitter_px: 2, tick: {size_px: 34, '
    "start: {x: [0.0, 0.1], y: [0.45, 0.65]}, knee: {x: [0.3, 0.45], y: [0.85, 1.0]}, tip: {x: [0.9, 1.0], "
    "y: [0.0, 0.1]}, bow: [0.02, 0.08], taper: {start: 0.7, end: 0.35}, spacing_px: 3.0}, "
    "circle: {gap_px: 8, "
    "overrun: [0.08, 0.22], taper: {start: 0.6, end: 0.5}, spacing_px: 3.0}, underline: {gap_px: 6, "
    "lead_jitters: [0, 3], taper: {start: 0.6, end: 0.4}, spacing_px: 4.0}}"
)
_HANDWRITING_EXAMPLE = (
    "handwriting: {hands: [{family: kalam, bold: false}, {family: caveat, bold: true}], size_px: [30, 38], "
    "glyph_jitter: {size_px: 3, slant_deg: 6, baseline_px: 3}, step_away: [0.0, 0.5], "
    "below_column_dx_px: [-48, 16]}"
)
_CONTRAST_KEYS = ("min_ratio", "dark_percentile", "light_percentile")
_STAMP_KEYS = (
    "colours",
    "opacity",
    "rotation_deg",
    "font",
    "date_size",
    "border_px",
    "padding_px",
    "speckle",
    "speckle_blob_px",
    "inking_gradient",
    "jitter_px",
)
_STAMP_FONT_KEYS = ("family", "bold", "size")
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_STAMP_EXAMPLE = (
    'stamp: {colours: ["#B3261E", "#1F3F8F"], opacity: [0.75, 0.9], rotation_deg: [-15, 15], font: '
    "{family: liberation_sans, bold: true, size: 64}, date_size: 30, border_px: 6, padding_px: 18, "
    "speckle: [0.04, 0.12], speckle_blob_px: 9, inking_gradient: [0.3, 0.9], jitter_px: 40}"
)
_OVERLAY_EXAMPLE = (
    "overlay: {clearance_px: 6, page_inset_px: 30, protect: {receipt: no_overlay, invoice: "
    "scored_and_marks, bank_statement: all_ink}, contrast: {min_ratio: 4.5, dark_percentile: 5, "
    f"light_percentile: 95}}, {_STAMP_EXAMPLE}, {_HIGHLIGHTER_EXAMPLE}, {_INK_EXAMPLE}, "
    f"{_HANDWRITING_EXAMPLE}}}"
)


class DistractorConfigError(ValueError):
    """Raised when `config/distractors.yml` is missing, malformed or incoherent."""


@dataclass(frozen=True)
class ValidatorSettings:
    """Settings for screening a drawn value against the scored values.

    Attributes:
        ignore_fields: Scored columns too short or generic to screen for.
        min_scored_length: Scored values shorter than this are not used in the contains-check.
    """

    ignore_fields: frozenset[str]
    min_scored_length: int


@dataclass(frozen=True)
class DensityBounds:
    """Density bounds one document type's distractor renders must meet (spec section 9).

    Attributes:
        scored_ink_ratio_max: The largest scored ink area allowed, as a fraction of all text ink.
        distractor_share_min: The smallest distractor text area allowed, as a fraction of all text ink
            (code marks excluded), so a render meeting it is known to carry distractor text.
        page_fill_min: The smallest page fill allowed (lowest ink y over page height), or None when the
            YAML sets it to null because the page is cropped to its content and fill is uninformative.
    """

    scored_ink_ratio_max: float
    distractor_share_min: float
    page_fill_min: float | None


@dataclass(frozen=True)
class ContrastSettings:
    """The legibility threshold a highlighted box must meet (Decision 6 of the overlay plan).

    Contrast is the WCAG ratio between a dark and a light relative-luminance percentile of the pixels
    inside an ink box.

    Attributes:
        min_ratio: The smallest ratio a box under a highlighter band may have after compositing.
        dark_percentile: The luminance percentile read as the text's ink, from 0 to 100.
        light_percentile: The luminance percentile read as the paper behind it, above `dark_percentile`.
    """

    min_ratio: float
    dark_percentile: float
    light_percentile: float


@dataclass(frozen=True)
class StampFont:
    """The face a stamp's word is set in.

    Attributes:
        family: A vendored font family (`generators.common.FONT_FAMILIES`).
        bold: Whether the bold face is used, for the word and the date line alike.
        size: The word's font size in pixels.
    """

    family: str
    bold: bool
    size: int


@dataclass(frozen=True)
class StampSettings:
    """How the `stamp` overlay primitive draws a PAID stamp (`overlay.stamp`).

    Each document's colour, opacity, rotation and speckle are drawn on its overlay seed from these ranges.

    Attributes:
        colours: The ink colours one is drawn from, as RGB.
        opacity: (min, max) alpha of the ink, each in (0, 1].
        rotation_deg: (min, max) rotation in degrees, counter-clockwise positive.
        font: The word's face and size.
        date_size: The date line's font size in pixels, in the word's face.
        border_px: The width of the frame drawn round the word and date.
        padding_px: The gap between the frame and the text, and between the word and the date line.
        speckle: (min, max) fraction of the stamp's ink pixels knocked out, for uneven ink.
        speckle_blob_px: The size of the knock-out's blotches: the noise field's feature size in pixels.
        inking_gradient: (min, max) weight of the ramp across the stamp added to the noise, so the
            knock-out gathers on one side, as a stamp pressed unevenly leaves it.
        jitter_px: The most a stamp's centre moves from its candidate, each way, drawn per document.
    """

    colours: tuple[tuple[int, int, int], ...]
    opacity: tuple[float, float]
    rotation_deg: tuple[float, float]
    font: StampFont
    date_size: int
    border_px: int
    padding_px: int
    speckle: tuple[float, float]
    speckle_blob_px: int
    inking_gradient: tuple[float, float]
    jitter_px: int


@dataclass(frozen=True)
class HighlighterSettings:
    """How the `highlight` overlay primitive draws a taxpayer's highlighter (`overlay.highlighter`).

    Attributes:
        colours: Colour name -> RGB; one is drawn per statement on the block's overlay seed.
        opacity: (min, max) blend weight of the multiply, each in (0, 1]; one drawn per statement.
        pad_px: Pixels a band extends past its row's ink on every side.
        end_jitter_px: The most a band's end may lean or stop short of, or run past, its padded row,
            drawn per end corner, so no two bands end alike.
        edge_jitter_px: The most a band's top or bottom edge wanders along its length.
        density_wobble: The most a band's ink density varies along its length, as a fraction.
        end_density: How much denser the ink is at each end than in the band's middle (a factor).
        end_length_px: How far from each end the denser ink fades out to the band's own.
    """

    colours: dict[str, tuple[int, int, int]]
    opacity: tuple[float, float]
    pad_px: int
    end_jitter_px: int
    edge_jitter_px: int
    density_wobble: float
    end_density: float
    end_length_px: int


@dataclass(frozen=True)
class Taper:
    """How a stroke's width swells and falls: a fraction of the pen's width where it lands and lifts.

    Attributes:
        start: The width where the pen lands, as a fraction of the full width.
        end: The width where it lifts.
    """

    start: float
    end: float


@dataclass(frozen=True)
class TickShape:
    """A tick's geometry (`overlay.ink.tick`): fractions of its square, each a (min, max) draw.

    Attributes:
        size_px: The square's side.
        start, knee, tip: ((x min, x max), (y min, y max)) where the pen lands, turns and lifts.
        bow: (min, max) outward bow of the rising stroke.
        taper: The stroke's width profile.
        spacing_px: The distance between sampled path points.
    """

    size_px: int
    start: tuple[tuple[float, float], tuple[float, float]]
    knee: tuple[tuple[float, float], tuple[float, float]]
    tip: tuple[tuple[float, float], tuple[float, float]]
    bow: tuple[float, float]
    taper: Taper
    spacing_px: float


@dataclass(frozen=True)
class CircleShape:
    """A loop's geometry (`overlay.ink.circle`).

    Attributes:
        gap_px: The gap a loop leaves round its amount's padded box, at its nearest.
        overrun: (min, max) fraction of a turn the loop runs past its start.
        taper: The stroke's width profile.
        spacing_px: The distance between sampled path points.
    """

    gap_px: int
    overrun: tuple[float, float]
    taper: Taper
    spacing_px: float


@dataclass(frozen=True)
class UnderlineShape:
    """An underline's geometry (`overlay.ink.underline`).

    Attributes:
        gap_px: The gap between an amount's padded box and the underline beneath it.
        lead_jitters: (min, max) how far the line starts before and runs past the amount, in jitter_px.
        taper: The stroke's width profile.
        spacing_px: The distance between sampled path points.
    """

    gap_px: int
    lead_jitters: tuple[float, float]
    taper: Taper
    spacing_px: float


@dataclass(frozen=True)
class InkSettings:
    """How the `ink_marks` primitive draws ticks, loops and underlines, and the pen handwriting uses.

    Attributes:
        colours: The pen colours; one is drawn per statement, so every mark and note on it shares a pen.
        stroke_px: (min, max) pen width in pixels, drawn per mark.
        jitter_px: The most any stroke point strays from its ideal path.
        tick: A tick's geometry.
        circle: A loop's geometry.
        underline: An underline's geometry.
    """

    colours: tuple[tuple[int, int, int], ...]
    stroke_px: tuple[int, int]
    jitter_px: int
    tick: TickShape
    circle: CircleShape
    underline: UnderlineShape


@dataclass(frozen=True)
class Hand:
    """One handwriting face and weight a statement's notes may be written in.

    Attributes:
        family: A vendored font family (`generators.common.FONT_FAMILIES`).
        bold: Whether its bold file is used; only a family that ships one may set it.
    """

    family: str
    bold: bool


@dataclass(frozen=True)
class GlyphJitter:
    """How far each handwritten glyph strays from the line it is set on.

    Attributes:
        size_px: The most a glyph's size differs from the line's.
        slant_deg: The most a glyph leans either way, in degrees.
        baseline_px: The most a glyph sits above or below the baseline.
    """

    size_px: int
    slant_deg: float
    baseline_px: int


@dataclass(frozen=True)
class HandwritingSettings:
    """How the `handwriting` primitive writes notes and sums (`overlay.handwriting`).

    Attributes:
        hands: Every face and weight a statement may be written in; one is drawn per statement.
        size_px: (min, max) line size in pixels, drawn per note.
        glyph_jitter: Per-glyph variation within the hand.
        step_away: (min, max) fraction of a note's height it moves away from the print it was placed
            against, where that stays clear.
        below_column_dx_px: (min, max) offset of a note's right edge from its column's right edge when
            written under the column (negative is to the left), drawn per note.
    """

    hands: tuple[Hand, ...]
    size_px: tuple[int, int]
    glyph_jitter: GlyphJitter
    step_away: tuple[float, float]
    below_column_dx_px: tuple[int, int]


@dataclass(frozen=True)
class OverlaySettings:
    """The settings every overlay block shares (spec section 8; overlay plan Decisions 4, 6 and 13).

    Attributes:
        clearance_px: Pixels every protected ink box is padded by before overlay ink is checked against it.
        page_inset_px: No overlay mark may come nearer a page edge than this.
        protect: Document type -> what opaque overlay ink may not touch, one of
            `OVERLAY_PROTECT_POLICIES`; `NO_OVERLAY` for a type that carries no overlay.
        contrast: The legibility threshold for highlighted boxes.
        stamp: How the `stamp` primitive draws.
        highlighter: How the `highlight` primitive draws.
        ink: How the `ink_marks` primitive draws, and the pens it and `handwriting` share.
        handwriting: How the `handwriting` primitive writes.
    """

    clearance_px: int
    page_inset_px: int
    protect: dict[str, str]
    contrast: ContrastSettings
    stamp: StampSettings
    highlighter: HighlighterSettings
    ink: InkSettings
    handwriting: HandwritingSettings


@dataclass(frozen=True)
class Item:
    """One catalogue entry.

    Attributes:
        id: The item id, prefixed by document type (`rc_`, `in_`, `bk_`).
        doc_types: Extraction-schema document types the item may appear on.
        kind: One of `ITEM_KINDS`.
        target: The scored column a near-miss imitates, else None.
        field: The scored column a repeat copies, else None.
        emits: The `DX_` keys the item provides.
        rule: The value rule mapping, with its `type`.
        requires: Ids of items that must be drawn with this one.
    """

    id: str
    doc_types: frozenset[str]
    kind: str
    target: str | None
    field: str | None
    emits: tuple[str, ...]
    rule: dict
    requires: tuple[str, ...]


@dataclass(frozen=True)
class StateSpec:
    """One per-document state (a paid or unpaid invoice) and the items each of its values allows.

    Attributes:
        choices: The values the state may take, one drawn per document.
        groups: Choice -> the items only a document in that state may print. A document in any other
            state never selects them, nor any item requiring one of them.
    """

    choices: tuple[str, ...]
    groups: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class DistractorConfig:
    """The validated catalogue.

    Attributes:
        selection: Document type -> inclusive (min, max) count of items drawn per document.
        max_redraws: Redraws allowed when a drawn value collides with a scored value.
        validator: The collision-screen settings.
        density: Document type -> the density bounds its distractor renders must meet.
        items: Item id -> item.
        states: Document type -> state name -> its spec; a type with no states maps to {}.
        exclusive: Document type -> sets of items no page prints together; () for a type with none.
        overlay: The settings shared by every overlay block drawn on a distractor render.
    """

    selection: dict[str, tuple[int, int]]
    max_redraws: int
    validator: ValidatorSettings
    density: dict[str, DensityBounds]
    items: dict[str, Item]
    states: dict[str, dict[str, StateSpec]]
    exclusive: dict[str, tuple[tuple[str, ...], ...]]
    overlay: OverlaySettings


def required_items(config: DistractorConfig, item_id: str) -> set[str]:
    """Return every item `item_id` requires, transitively (the loader refuses cycles).

    Args:
        config: The catalogue.
        item_id: A catalogue item id.

    Returns:
        The ids of the items `item_id` requires, directly or through other items; never `item_id`.
    """
    found: set[str] = set()
    stack = list(config.items[item_id].requires)
    while stack:
        current = stack.pop()
        if current not in found:
            found.add(current)
            stack.extend(config.items[current].requires)
    return found


def _fail(path: Path, key: str, what: str, expected: str, recover: str) -> DistractorConfigError:
    return DistractorConfigError(
        "Invalid distractor catalogue.\n"
        f"  What:     {what}\n"
        f"  Where:    {path.resolve()} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@lru_cache(maxsize=None)
def _load_default() -> DistractorConfig:
    return _load(DISTRACTORS_PATH)


def load_distractor_config(path: Path = DISTRACTORS_PATH) -> DistractorConfig:
    """Load and validate the distractor catalogue.

    The shipped catalogue is parsed once per process; any other path is parsed on every call.

    Args:
        path: The catalogue YAML.

    Returns:
        The validated, immutable configuration.

    Raises:
        DistractorConfigError: The file is absent, unparsable, or any key is missing or incoherent.
    """
    return _load_default() if path == DISTRACTORS_PATH else _load(path)


def _load(path: Path) -> DistractorConfig:
    if not path.exists():
        raise _fail(
            path,
            "(file)",
            "the distractor catalogue does not exist.",
            "a YAML mapping with selection, max_redraws, validator, density, states, exclusive, overlay "
            "and items.",
            f"restore {path.resolve()} from git.",
        )
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as err:
        raise _fail(
            path,
            "(file)",
            f"the YAML does not parse: {err}.",
            "valid YAML.",
            "fix the syntax error at the reported line.",
        ) from err
    if not isinstance(data, dict):
        raise _fail(
            path,
            "(file)",
            f"the top level is a {type(data).__name__}, not a mapping.",
            "a mapping with selection, max_redraws, validator, density, states, exclusive, overlay and "
            "items.",
            "rewrite the file as a mapping.",
        )
    for key in _TOP_LEVEL:
        if key not in data:
            raise _fail(
                path,
                key,
                f"required key '{key}' is missing.",
                f"a top-level '{key}:' entry, e.g. "
                + (_OVERLAY_EXAMPLE if key == "overlay" else "max_redraws: 25")
                + ".",
                f"add the '{key}:' block to {path.resolve()}.",
            )

    schema = load_extraction_schema().get_all_doc_type_fields()
    selection = _load_selection(path, data["selection"], schema)

    redraws = data["max_redraws"]
    if not _is_int(redraws) or redraws <= 0:
        raise _fail(
            path,
            "max_redraws",
            f"max_redraws is {redraws!r}.",
            "a positive integer, e.g. max_redraws: 25.",
            f"set max_redraws in {path.resolve()} to a positive integer.",
        )

    validator = _load_validator(path, data["validator"], schema)
    density = _load_density(path, data["density"], schema)
    pools = _load_pools()
    _check_industry_pools(pools)
    items = _load_items(path, data["items"], schema, pools)
    states = _load_states(path, data["states"], schema, items)
    _check_state_rules(path, items, states)
    exclusive = _load_exclusive(path, data["exclusive"], schema, items)
    overlay = _load_overlay(path, data["overlay"], schema)
    return DistractorConfig(
        selection=selection,
        max_redraws=redraws,
        validator=validator,
        density=density,
        items=items,
        states=states,
        exclusive=exclusive,
        overlay=overlay,
    )


_STATES_EXAMPLE = (
    "invoice: {payment: {choices: [paid, unpaid], items: {paid: [in_amount_paid], unpaid: "
    "[in_previous_balance]}}}, and receipt: {} for a type with no states"
)


def _load_states(
    path: Path, raw: object, schema: dict[str, list[str]], items: dict[str, Item]
) -> dict[str, dict[str, StateSpec]]:
    """Load `states:`: per document type, each state's choices and the items each choice allows."""
    if not isinstance(raw, dict):
        raise _fail(
            path,
            "states",
            f"states is {raw!r}, not a mapping.",
            f"one entry per document type, e.g. {_STATES_EXAMPLE}.",
            f"declare states.<type> for each of {sorted(schema)} in {path.resolve()}.",
        )
    states: dict[str, dict[str, StateSpec]] = {}
    for doc_type, spec in raw.items():
        where = f"states.{doc_type}"
        if doc_type not in schema:
            raise _fail(
                path,
                where,
                f"unknown document type {doc_type!r}.",
                f"one of {sorted(schema)}.",
                f"rename or remove {where}.",
            )
        if not isinstance(spec, dict):
            raise _fail(
                path,
                where,
                f"{where} is {spec!r}, not a mapping.",
                f"a mapping of state name to its spec, or {{}} for none, e.g. {_STATES_EXAMPLE}.",
                f"rewrite {where} as a mapping, or write {doc_type}: {{}} if it has no states.",
            )
        states[doc_type] = {
            name: _load_state(path, f"{where}.{name}", doc_type, state, items)
            for name, state in spec.items()
        }
        grouped: dict[str, str] = {}
        for name, state in states[doc_type].items():
            for choice, ids in state.groups.items():
                for item_id in ids:
                    other = grouped.setdefault(item_id, f"{where}.{name}.items.{choice}")
                    if other != f"{where}.{name}.items.{choice}":
                        raise _fail(
                            path,
                            f"{where}.{name}.items.{choice}",
                            f"item {item_id!r} is listed in {other} and again here.",
                            "each item in at most one group of a document type's states.",
                            f"remove {item_id!r} from one of the two groups.",
                        )
    missing = sorted(set(schema) - set(states))
    if missing:
        raise _fail(
            path,
            "states",
            f"no states entry for {missing}; a type with no states must say so explicitly.",
            f"an entry for each of {sorted(schema)}, e.g. {_STATES_EXAMPLE}.",
            f"add states.<type> for {missing}, written {{}} where a type has no states.",
        )
    return states


def _load_state(path: Path, where: str, doc_type: str, raw: object, items: dict[str, Item]) -> StateSpec:
    """Load one state: its choices, and a group of items per choice."""
    if not isinstance(raw, dict) or sorted(raw) != sorted(_STATE_KEYS):
        raise _fail(
            path,
            where,
            f"the state is {raw!r}, not a mapping of exactly {list(_STATE_KEYS)}.",
            f"choices: [<value>, ...] and items: {{<value>: [<item id>, ...]}}, e.g. {_STATES_EXAMPLE}.",
            f"rewrite {where} with exactly the keys {list(_STATE_KEYS)}.",
        )
    choices = raw["choices"]
    if (
        not isinstance(choices, list)
        or len(choices) < 2
        or not all(isinstance(c, str) and c for c in choices)
        or len(set(choices)) != len(choices)
    ):
        raise _fail(
            path,
            f"{where}.choices",
            f"choices is {choices!r}.",
            "a list of at least two distinct non-empty names, e.g. choices: [paid, unpaid].",
            f"fix {where}.choices in {path.resolve()}.",
        )
    groups = raw["items"]
    if not isinstance(groups, dict) or sorted(groups) != sorted(choices):
        keys = sorted(groups) if isinstance(groups, dict) else groups
        raise _fail(
            path,
            f"{where}.items",
            f"items has the groups {keys!r}, not one per choice of {choices}.",
            f"one list of item ids per choice, e.g. items: {{{choices[0]}: [...], {choices[1]}: [...]}}.",
            f"give {where}.items exactly the keys {choices}.",
        )
    loaded: dict[str, tuple[str, ...]] = {}
    for choice in choices:
        here = f"{where}.items.{choice}"
        ids = groups[choice]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise _fail(
                path,
                here,
                f"the group is {ids!r}, not a list of item ids.",
                "a list of catalogue item ids, e.g. [in_amount_paid, in_balance_due], or [].",
                f"make {here} a list of item ids.",
            )
        for item_id in ids:
            item = items.get(item_id)
            if item is None or doc_type not in item.doc_types:
                problem = "is not a catalogue item" if item is None else f"is for {sorted(item.doc_types)}"
                raise _fail(
                    path,
                    here,
                    f"group item {item_id!r} {problem}, so the state cannot gate it on {doc_type}.",
                    f"ids under items: whose doc_types include {doc_type}.",
                    f"correct or remove {item_id!r} in {here}.",
                )
        loaded[choice] = tuple(ids)
    for choice, ids in loaded.items():
        others = {i for c, group in loaded.items() if c != choice for i in group}
        for item_id in ids:
            closure: set[str] = set()
            stack = list(items[item_id].requires)
            while stack:
                current = stack.pop()
                if current not in closure:
                    closure.add(current)
                    stack.extend(items[current].requires)
            crossed = sorted(closure & others)
            if crossed:
                raise _fail(
                    path,
                    f"{where}.items.{choice}",
                    f"{item_id!r} requires {crossed}, which only another {where} group may print, so it "
                    "could never be drawn.",
                    "a group's items requiring nothing from another group of the same state.",
                    f"move {item_id!r} and {crossed} into one group, or drop the requires: entry.",
                )
    return StateSpec(choices=tuple(choices), groups=loaded)


_EXCLUSIVE_EXAMPLE = (
    "invoice: [[in_direct_debit, in_remittance_total]], and receipt: [] for a type with none"
)


def _load_exclusive(
    path: Path, raw: object, schema: dict[str, list[str]], items: dict[str, Item]
) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Load `exclusive:`: per document type, the sets of items no page prints together."""
    if not isinstance(raw, dict):
        raise _fail(
            path,
            "exclusive",
            f"exclusive is {raw!r}, not a mapping.",
            f"one entry per document type, e.g. {_EXCLUSIVE_EXAMPLE}.",
            f"declare exclusive.<type> for each of {sorted(schema)} in {path.resolve()}.",
        )
    loaded: dict[str, tuple[tuple[str, ...], ...]] = {}
    for doc_type, sets in raw.items():
        where = f"exclusive.{doc_type}"
        if doc_type not in schema:
            raise _fail(
                path,
                where,
                f"unknown document type {doc_type!r}.",
                f"one of {sorted(schema)}.",
                f"rename or remove {where}.",
            )
        if not isinstance(sets, list):
            raise _fail(
                path,
                where,
                f"{where} is {sets!r}, not a list of item sets.",
                f"a list of lists of item ids, or [] for none, e.g. {_EXCLUSIVE_EXAMPLE}.",
                f"rewrite {where} as a list, or write {doc_type}: [] if no items exclude each other.",
            )
        seen: dict[str, int] = {}
        out: list[tuple[str, ...]] = []
        for index, members in enumerate(sets):
            here = f"{where}[{index}]"
            if (
                not isinstance(members, list)
                or len(members) < 2
                or not all(isinstance(m, str) for m in members)
                or len(set(members)) != len(members)
            ):
                raise _fail(
                    path,
                    here,
                    f"the set is {members!r}, not a list of at least two distinct item ids.",
                    f"two or more catalogue item ids, e.g. {_EXCLUSIVE_EXAMPLE}.",
                    f"list at least two distinct item ids in {here}, or remove it.",
                )
            for item_id in members:
                item = items.get(item_id)
                if item is None or doc_type not in item.doc_types:
                    problem = (
                        "is not a catalogue item" if item is None else f"is for {sorted(item.doc_types)}"
                    )
                    raise _fail(
                        path,
                        here,
                        f"{item_id!r} {problem}, so it cannot exclude items on {doc_type}.",
                        f"ids under items: whose doc_types include {doc_type}.",
                        f"correct or remove {item_id!r} in {here}.",
                    )
                if item_id in seen:
                    raise _fail(
                        path,
                        here,
                        f"{item_id!r} is already in {where}[{seen[item_id]}].",
                        "each item in at most one exclusive set of a document type.",
                        f"merge the two sets, or remove {item_id!r} from one of them.",
                    )
                seen[item_id] = index
            out.append(tuple(members))
        loaded[doc_type] = tuple(out)
    missing = sorted(set(schema) - set(loaded))
    if missing:
        raise _fail(
            path,
            "exclusive",
            f"no exclusive entry for {missing}; a type with none must say so explicitly.",
            f"an entry for each of {sorted(schema)}, e.g. {_EXCLUSIVE_EXAMPLE}.",
            f"add exclusive.<type> for {missing}, written [] where a type has none.",
        )
    return loaded


def _check_state_rules(path: Path, items: dict[str, Item], states: dict[str, dict[str, StateSpec]]) -> None:
    """Refuse a `state_text` rule whose state or pools do not match the states its item's types declare.

    A `state_text` item prints wording keyed by a document's drawn state (a paid invoice's past tense, an
    unpaid one's future tense), so its `state` must be a state of every type it appears on, and its
    `pools` must give one text pool for exactly that state's choices.
    """
    for item in items.values():
        if item.rule["type"] != "state_text":
            continue
        here = f"items.{item.id}.rule"
        name, pools = item.rule["state"], item.rule["pools"]
        for doc_type in sorted(item.doc_types):
            spec = states.get(doc_type, {}).get(name)
            if spec is None:
                raise _fail(
                    path,
                    f"{here}.state",
                    f"{item.id!r} keys its text by the state {name!r}, which states.{doc_type} does not "
                    "declare.",
                    f"a state name under states.{doc_type}, e.g. state: payment.",
                    f"set {here}.state to a declared state, or declare {name!r} under states.{doc_type}.",
                )
            if sorted(pools) != sorted(spec.choices):
                raise _fail(
                    path,
                    f"{here}.pools",
                    f"pools has the keys {sorted(pools)}, not one per choice of states.{doc_type}.{name} "
                    f"{list(spec.choices)}.",
                    "one text pool per choice, e.g. pools: {paid: direct_debit_paid, unpaid: "
                    "direct_debit_notice}.",
                    f"give {here}.pools exactly the keys {list(spec.choices)}.",
                )


def _check_industry_pools(pools: dict) -> None:
    """Refuse an unmapped service category, or a text pool keyed by industry that misses one.

    `category_industries` maps every invoice service category to the industry whose distractor text its
    invoices print; a text pool under `distractor_text` that is a mapping is keyed by those industries
    and must carry a non-empty sub-pool for each, and for no other.
    """
    where = "category_industries"
    example = "category_industries: {accounting: finance, legal: legal}"
    mapping = pools.get(where)
    categories = pools.get("service_categories")
    if not isinstance(mapping, dict) or not mapping:
        raise _fail(
            DATA_POOLS_PATH,
            where,
            f"{where} is {mapping!r}, not a non-empty mapping of service category to industry.",
            f"every service category mapped to an industry, e.g. {example}.",
            f"add {where}: to {DATA_POOLS_PATH} mapping each of {categories} to an industry.",
        )
    if not isinstance(categories, list) or not all(isinstance(c, str) and c for c in categories):
        raise _fail(
            DATA_POOLS_PATH,
            "service_categories",
            f"service_categories is {categories!r}, not a list of category names, so no category can be "
            "checked against category_industries.",
            "a list of the invoice categories, each a key of business_name_parts.category_nouns, e.g. "
            "service_categories: [accounting, legal, plumbing].",
            f"restore service_categories: [...] at the top level of {DATA_POOLS_PATH}.",
        )
    for category, industry in mapping.items():
        if category not in categories or not isinstance(industry, str) or not industry:
            raise _fail(
                DATA_POOLS_PATH,
                f"{where}.{category}",
                f"{category!r} -> {industry!r} is not a service category mapped to an industry name.",
                f"a category from service_categories {categories} mapped to a non-empty name, "
                f"e.g. {example}.",
                f"correct or remove {where}.{category} in {DATA_POOLS_PATH}.",
            )
    unmapped = [category for category in categories if category not in mapping]
    if unmapped:
        raise _fail(
            DATA_POOLS_PATH,
            where,
            f"the service categories {unmapped} are mapped to no industry.",
            f"every category of service_categories mapped, e.g. {example}.",
            f"add {', '.join(f'{c}: <industry>' for c in unmapped)} under {where} in {DATA_POOLS_PATH}.",
        )
    nouns = pools.get("business_name_parts", {}).get("category_nouns", {})
    owner: dict[str, str] = {}
    for category in categories:
        for noun in nouns.get(category, []) if isinstance(nouns, dict) else []:
            other = owner.setdefault(noun, category)
            if other != category:
                raise _fail(
                    DATA_POOLS_PATH,
                    f"business_name_parts.category_nouns.{category}",
                    f"the noun {noun!r} belongs to both {other!r} and {category!r}, so a supplier named "
                    "with it has no single industry.",
                    "each service category's nouns distinct from every other's.",
                    f"rename {noun!r} in one of the two categories.",
                )
    industries = sorted(set(mapping.values()))
    texts = pools.get("distractor_text")
    for name, pool in texts.items() if isinstance(texts, dict) else []:
        if not isinstance(pool, dict):
            continue
        here = f"distractor_text.{name}"
        missing = [industry for industry in industries if industry not in pool]
        stray = sorted(set(pool) - set(industries))
        empty = sorted(k for k, sub in pool.items() if not isinstance(sub, list) or not sub)
        if missing or stray or empty:
            problem = (
                f"has no sub-pool for the industries {missing}"
                if missing
                else f"has sub-pools {stray} for no mapped industry"
                if stray
                else f"has empty or malformed sub-pools {empty}"
            )
            raise _fail(
                DATA_POOLS_PATH,
                here,
                f"the text pool '{name}' is keyed by industry and {problem}.",
                f"a non-empty list of invented strings for each industry of {where}: {industries}.",
                f"give {here} exactly one non-empty sub-pool per industry in {industries}.",
            )


def _load_selection(path: Path, raw: object, schema: dict[str, list[str]]) -> dict[str, tuple[int, int]]:
    if not isinstance(raw, dict) or not raw:
        raise _fail(
            path,
            "selection",
            "selection is not a non-empty mapping.",
            "one entry per document type, e.g. receipt: {count: [3, 6]}.",
            f"declare selection.<type>.count for each of {sorted(schema)}.",
        )
    selection: dict[str, tuple[int, int]] = {}
    for doc_type, spec in raw.items():
        where = f"selection.{doc_type}.count"
        if doc_type not in schema:
            raise _fail(
                path,
                f"selection.{doc_type}",
                f"unknown document type {doc_type!r}.",
                f"one of {sorted(schema)}.",
                f"rename or remove selection.{doc_type}.",
            )
        count = spec.get("count") if isinstance(spec, dict) else None
        if (
            not isinstance(count, list)
            or len(count) != 2
            or not all(_is_int(n) and n >= 0 for n in count)
            or count[0] > count[1]
        ):
            raise _fail(
                path,
                where,
                f"count is {count!r}.",
                "two non-negative integers [min, max] with min <= max, e.g. count: [3, 6].",
                f"fix {where} in {path.resolve()}.",
            )
        selection[doc_type] = (count[0], count[1])
    missing = sorted(set(schema) - set(selection))
    if missing:
        raise _fail(
            path,
            "selection",
            f"no selection count for {missing}.",
            f"an entry for each of {sorted(schema)}.",
            f"add selection.<type>.count for {missing}.",
        )
    return selection


def _load_validator(path: Path, raw: object, schema: dict[str, list[str]]) -> ValidatorSettings:
    scored = {column for columns in schema.values() for column in columns}
    if not isinstance(raw, dict):
        raw = {}
    for key in ("ignore_fields", "min_scored_length"):
        if key not in raw:
            raise _fail(
                path,
                f"validator.{key}",
                f"required key 'validator.{key}' is missing.",
                "ignore_fields: [DOCUMENT_TYPE, ...] and min_scored_length: 4.",
                f"add validator.{key} to {path.resolve()}.",
            )
    ignore = raw["ignore_fields"]
    if not isinstance(ignore, list):
        raise _fail(
            path,
            "validator.ignore_fields",
            f"ignore_fields is {ignore!r}, not a list.",
            "a list of scored columns, e.g. [DOCUMENT_TYPE, IS_GST_INCLUDED].",
            "make ignore_fields a list.",
        )
    for column in ignore:
        if column not in scored:
            raise _fail(
                path,
                "validator.ignore_fields",
                f"{column!r} is not a scored column.",
                f"columns from {sorted(scored)}.",
                f"correct or remove {column!r}.",
            )
    length = raw["min_scored_length"]
    if not _is_int(length) or length <= 0:
        raise _fail(
            path,
            "validator.min_scored_length",
            f"min_scored_length is {length!r}.",
            "a positive integer, e.g. 4.",
            "set min_scored_length to a positive integer.",
        )
    return ValidatorSettings(ignore_fields=frozenset(ignore), min_scored_length=length)


def _load_density(path: Path, raw: object, schema: dict[str, list[str]]) -> dict[str, DensityBounds]:
    example = "invoice: {scored_ink_ratio_max: 0.25, distractor_share_min: 0.4, page_fill_min: 0.75}"
    if not isinstance(raw, dict) or not raw:
        raise _fail(
            path,
            "density",
            f"density is {raw!r}, not a non-empty mapping.",
            f"one entry per document type, e.g. {example}.",
            f"declare density.<type> for each of {sorted(schema)}.",
        )
    density: dict[str, DensityBounds] = {}
    for doc_type, spec in raw.items():
        where = f"density.{doc_type}"
        if doc_type not in schema:
            raise _fail(
                path,
                where,
                f"unknown document type {doc_type!r}.",
                f"one of {sorted(schema)}.",
                f"rename or remove {where}.",
            )
        if not isinstance(spec, dict):
            raise _fail(
                path,
                where,
                f"{where} is {spec!r}, not a mapping.",
                f"a mapping with {list(_DENSITY_KEYS)}, e.g. {example}.",
                f"rewrite {where} as a mapping.",
            )
        unknown = sorted(set(spec) - set(_DENSITY_KEYS))
        if unknown:
            raise _fail(
                path,
                where,
                f"unknown key(s) {unknown}.",
                f"exactly the keys {list(_DENSITY_KEYS)}.",
                f"remove {unknown} from {where}.",
            )
        values: dict[str, float] = {}
        unbounded: set[str] = set()
        for key in _DENSITY_KEYS:
            nullable = key in _NULLABLE_DENSITY_KEYS
            if key not in spec:
                raise _fail(
                    path,
                    f"{where}.{key}",
                    f"required key '{key}' is missing"
                    + ("; an unbounded page fill must be written as an explicit null" if nullable else "")
                    + ".",
                    "a number from 0 to 1"
                    + (", or null for a page cropped to its content" if nullable else "")
                    + f", e.g. {example}.",
                    f"add {key} to {where} in {path.resolve()}.",
                )
            value = spec[key]
            if value is None and nullable:
                unbounded.add(key)
                continue
            if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
                raise _fail(
                    path,
                    f"{where}.{key}",
                    f"{key} is {value!r}.",
                    "a number from 0 to 1"
                    + (", or null for a page cropped to its content" if nullable else "")
                    + f", e.g. {example}.",
                    f"set {where}.{key} in {path.resolve()} to a fraction from 0 to 1.",
                )
            values[key] = float(value)
        density[doc_type] = DensityBounds(
            scored_ink_ratio_max=values["scored_ink_ratio_max"],
            distractor_share_min=values["distractor_share_min"],
            page_fill_min=None if "page_fill_min" in unbounded else values["page_fill_min"],
        )
    missing = sorted(set(schema) - set(density))
    if missing:
        raise _fail(
            path,
            "density",
            f"no density bounds for {missing}.",
            f"an entry for each of {sorted(schema)}, e.g. {example}.",
            f"add density.<type> for {missing}.",
        )
    return density


def _overlay_fail(path: Path, key: str, what: str, expected: str) -> DistractorConfigError:
    """Build an `overlay:` refusal: every one names the key, an example and the same recovery."""
    return _fail(
        path,
        key,
        what,
        f"{expected}, e.g. {_OVERLAY_EXAMPLE}.",
        f"add the key under overlay: in {path.resolve()}, or correct its value.",
    )


def _overlay_int(path: Path, raw: dict, key: str, *, minimum: int) -> int:
    if key not in raw:
        raise _overlay_fail(
            path, f"overlay.{key}", f"required key 'overlay.{key}' is missing.", "the key present"
        )
    value = raw[key]
    if not _is_int(value) or value < minimum:
        raise _overlay_fail(
            path, f"overlay.{key}", f"overlay.{key} is {value!r}.", f"an integer of at least {minimum}"
        )
    return int(value)


def _load_overlay(path: Path, raw: object, schema: dict[str, list[str]]) -> OverlaySettings:
    """Load `overlay:`, the settings every overlay block shares.

    Later overlay primitives add their own sub-blocks (`stamp:`, `highlighter:`, `ink:`, `handwriting:`)
    and extend `_OVERLAY_KEYS` as they do, so an unknown key is refused rather than silently ignored.
    """
    if not isinstance(raw, dict):
        raise _overlay_fail(path, "overlay", f"overlay is {raw!r}, not a mapping.", "a mapping")
    unknown = sorted(set(raw) - set(_OVERLAY_KEYS))
    if unknown:
        raise _overlay_fail(
            path,
            "overlay",
            f"unknown key(s) {unknown} under overlay.",
            f"exactly the keys {list(_OVERLAY_KEYS)}",
        )
    clearance = _overlay_int(path, raw, "clearance_px", minimum=1)
    inset = _overlay_int(path, raw, "page_inset_px", minimum=0)

    if "protect" not in raw:
        raise _overlay_fail(
            path, "overlay.protect", "required key 'overlay.protect' is missing.", "the key present"
        )
    protect = raw["protect"]
    if not isinstance(protect, dict):
        raise _overlay_fail(
            path,
            "overlay.protect",
            f"overlay.protect is {protect!r}, not a mapping.",
            f"a mapping of every document type in {sorted(schema)} to one of "
            f"{list(OVERLAY_PROTECT_POLICIES)}",
        )
    for doc_type, policy in protect.items():
        if doc_type not in schema:
            raise _overlay_fail(
                path,
                f"overlay.protect.{doc_type}",
                f"unknown document type {doc_type!r}.",
                f"document types from {sorted(schema)}",
            )
        if policy not in OVERLAY_PROTECT_POLICIES:
            raise _overlay_fail(
                path,
                f"overlay.protect.{doc_type}",
                f"overlay.protect.{doc_type} is {policy!r}.",
                f"one of {list(OVERLAY_PROTECT_POLICIES)}",
            )
    missing = sorted(set(schema) - set(protect))
    if missing:
        raise _overlay_fail(
            path,
            f"overlay.protect.{missing[0]}",
            f"no protection policy for {missing}; every document type must say what overlay ink may not "
            "touch.",
            f"an entry for each of {sorted(schema)}, each one of {list(OVERLAY_PROTECT_POLICIES)}",
        )

    if "contrast" not in raw:
        raise _overlay_fail(
            path, "overlay.contrast", "required key 'overlay.contrast' is missing.", "the key present"
        )
    contrast = raw["contrast"]
    if not isinstance(contrast, dict):
        raise _overlay_fail(
            path,
            "overlay.contrast",
            f"overlay.contrast is {contrast!r}, not a mapping.",
            f"a mapping with exactly {list(_CONTRAST_KEYS)}",
        )
    unknown = sorted(set(contrast) - set(_CONTRAST_KEYS))
    if unknown:
        raise _overlay_fail(
            path,
            "overlay.contrast",
            f"unknown key(s) {unknown} under overlay.contrast.",
            f"exactly the keys {list(_CONTRAST_KEYS)}",
        )
    numbers: dict[str, float] = {}
    for key in _CONTRAST_KEYS:
        where = f"overlay.contrast.{key}"
        if key not in contrast:
            raise _overlay_fail(path, where, f"required key '{where}' is missing.", "the key present")
        value = contrast[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _overlay_fail(path, where, f"{where} is {value!r}, not a number.", "a number")
        numbers[key] = float(value)
    if numbers["min_ratio"] <= 1.0:
        raise _overlay_fail(
            path,
            "overlay.contrast.min_ratio",
            f"overlay.contrast.min_ratio is {numbers['min_ratio']}; a ratio of 1 or less accepts text "
            "indistinguishable from its background.",
            "a WCAG contrast ratio above 1",
        )
    for key in ("dark_percentile", "light_percentile"):
        if not 0.0 <= numbers[key] <= 100.0:
            raise _overlay_fail(
                path,
                f"overlay.contrast.{key}",
                f"overlay.contrast.{key} is {numbers[key]}.",
                "a percentile from 0 to 100",
            )
    if numbers["dark_percentile"] >= numbers["light_percentile"]:
        raise _overlay_fail(
            path,
            "overlay.contrast.dark_percentile",
            f"overlay.contrast.dark_percentile {numbers['dark_percentile']} is not below light_percentile "
            f"{numbers['light_percentile']}.",
            "dark_percentile below light_percentile",
        )
    for key in ("stamp", *_ANNOTATION_KEYS):
        if key not in raw:
            raise _overlay_fail(
                path, f"overlay.{key}", f"required key 'overlay.{key}' is missing.", "the key present"
            )
    return OverlaySettings(
        clearance_px=clearance,
        page_inset_px=inset,
        protect=dict(protect),
        contrast=ContrastSettings(
            min_ratio=numbers["min_ratio"],
            dark_percentile=numbers["dark_percentile"],
            light_percentile=numbers["light_percentile"],
        ),
        stamp=_load_stamp(path, raw["stamp"]),
        highlighter=_load_highlighter(path, raw["highlighter"]),
        ink=_load_ink(path, raw["ink"]),
        handwriting=_load_handwriting(path, raw["handwriting"]),
    )


def _stamp_fail(path: Path, key: str, what: str, expected: str) -> DistractorConfigError:
    """Build an `overlay.stamp` refusal, with the stamp's own example."""
    return _fail(
        path,
        key,
        what,
        f"{expected}, e.g. {_STAMP_EXAMPLE}.",
        f"correct {key} under overlay: in {path.resolve()}.",
    )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _stamp_range(path: Path, raw: dict, key: str, *, low: float, high: float, low_open: bool) -> tuple:
    """Read a `[min, max]` pair of numbers with low <= min <= max <= high (low exclusive if `low_open`)."""
    where = f"overlay.stamp.{key}"
    value = raw[key]
    pair = value if isinstance(value, list) and len(value) == 2 and all(map(_is_number, value)) else None
    bound = "(" if low_open else "["
    if (
        pair is None
        or pair[0] > pair[1]
        or pair[1] > high
        or pair[0] < low
        or (low_open and pair[0] <= low)
    ):
        raise _stamp_fail(
            path,
            where,
            f"{where} is {value!r}.",
            f"[min, max], two numbers with min <= max, both in {bound}{low:g}, {high:g}]",
        )
    return float(pair[0]), float(pair[1])


def _stamp_int(path: Path, raw: dict, key: str, *, minimum: int) -> int:
    where = f"overlay.stamp.{key}"
    value = raw[key]
    if not _is_int(value) or value < minimum:
        raise _stamp_fail(path, where, f"{where} is {value!r}.", f"an integer of at least {minimum}")
    return int(value)


def _load_stamp(path: Path, raw: object) -> StampSettings:
    """Load `overlay.stamp`, how the `stamp` primitive draws; every key required, unknown keys refused."""
    if not isinstance(raw, dict):
        raise _stamp_fail(path, "overlay.stamp", f"overlay.stamp is {raw!r}, not a mapping.", "a mapping")
    unknown = sorted(set(raw) - set(_STAMP_KEYS))
    if unknown:
        raise _stamp_fail(
            path,
            "overlay.stamp",
            f"unknown key(s) {unknown} under overlay.stamp.",
            f"exactly the keys {list(_STAMP_KEYS)}",
        )
    for key in _STAMP_KEYS:
        if key not in raw:
            raise _stamp_fail(
                path, f"overlay.stamp.{key}", f"required key 'overlay.stamp.{key}' is missing.", "the key"
            )
    colours = raw["colours"]
    if (
        not isinstance(colours, list)
        or not colours
        or not all(isinstance(c, str) and _HEX_COLOUR.match(c) for c in colours)
    ):
        raise _stamp_fail(
            path,
            "overlay.stamp.colours",
            f"overlay.stamp.colours is {colours!r}.",
            'a non-empty list of #RRGGBB hex colours, e.g. colours: ["#B3261E"]',
        )
    font = raw["font"]
    if not isinstance(font, dict) or sorted(font) != sorted(_STAMP_FONT_KEYS):
        raise _stamp_fail(
            path,
            "overlay.stamp.font",
            f"overlay.stamp.font is {font!r}.",
            f"a mapping with exactly {list(_STAMP_FONT_KEYS)}",
        )
    if font["family"] not in FONT_FAMILIES:
        raise _stamp_fail(
            path,
            "overlay.stamp.font.family",
            f"overlay.stamp.font.family is {font['family']!r}, not a vendored family.",
            f"one of {sorted(FONT_FAMILIES)}",
        )
    if not isinstance(font["bold"], bool) or font["bold"] not in vendored_weights(font["family"]):
        raise _stamp_fail(
            path,
            "overlay.stamp.font.bold",
            f"overlay.stamp.font.bold is {font['bold']!r}"
            + (f", a weight '{font['family']}' does not ship" if isinstance(font["bold"], bool) else "")
            + ".",
            f"true or false, a weight the family ships ({font['family']}: "
            f"{['bold' if b else 'regular' for b in vendored_weights(font['family'])]})",
        )
    size = font["size"]
    if not _is_int(size) or size < 1:
        raise _stamp_fail(
            path,
            "overlay.stamp.font.size",
            f"overlay.stamp.font.size is {size!r}.",
            "an integer of at least 1",
        )
    return StampSettings(
        colours=tuple((int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) for c in colours),
        opacity=_stamp_range(path, raw, "opacity", low=0.0, high=1.0, low_open=True),
        rotation_deg=_stamp_range(path, raw, "rotation_deg", low=-45.0, high=45.0, low_open=False),
        font=StampFont(family=str(font["family"]), bold=bool(font["bold"]), size=int(size)),
        date_size=_stamp_int(path, raw, "date_size", minimum=1),
        border_px=_stamp_int(path, raw, "border_px", minimum=1),
        padding_px=_stamp_int(path, raw, "padding_px", minimum=0),
        speckle=_stamp_range(path, raw, "speckle", low=0.0, high=0.5, low_open=False),
        speckle_blob_px=_stamp_int(path, raw, "speckle_blob_px", minimum=1),
        inking_gradient=_stamp_range(path, raw, "inking_gradient", low=0.0, high=4.0, low_open=False),
        jitter_px=_stamp_int(path, raw, "jitter_px", minimum=0),
    )


def _sub_fail(path: Path, key: str, what: str, expected: str, example: str) -> DistractorConfigError:
    """Build a refusal for a key under one of the overlay's own sub-blocks, with that block's example."""
    return _fail(
        path,
        key,
        what,
        f"{expected}, e.g. {example}.",
        f"correct {key} under overlay: in {path.resolve()}.",
    )


def _sub_mapping(path: Path, raw: object, where: str, keys: tuple[str, ...], example: str) -> dict:
    """A sub-block mapping with exactly `keys`: every key required, unknown keys refused."""
    if not isinstance(raw, dict):
        raise _sub_fail(path, where, f"{where} is {raw!r}, not a mapping.", "a mapping", example)
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise _sub_fail(
            path,
            where,
            f"unknown key(s) {unknown} under {where}.",
            f"exactly the keys {list(keys)}",
            example,
        )
    for key in keys:
        if key not in raw:
            raise _sub_fail(
                path, f"{where}.{key}", f"required key '{where}.{key}' is missing.", "the key", example
            )
    return raw


def _sub_int(path: Path, raw: dict, where: str, key: str, *, minimum: int, example: str) -> int:
    value = raw[key]
    if not _is_int(value) or value < minimum:
        raise _sub_fail(
            path,
            f"{where}.{key}",
            f"{where}.{key} is {value!r}.",
            f"an integer of at least {minimum}",
            example,
        )
    return int(value)


def _sub_range(
    path: Path, raw: dict, where: str, key: str, *, low: float, high: float, integer: bool, example: str
) -> tuple:
    """A `[min, max]` pair with low < min <= max <= high (whole numbers when `integer`)."""
    value = raw[key]
    kind = _is_int if integer else _is_number
    pair = value if isinstance(value, list) and len(value) == 2 and all(map(kind, value)) else None
    if pair is None or not low < pair[0] <= pair[1] <= high:
        raise _sub_fail(
            path,
            f"{where}.{key}",
            f"{where}.{key} is {value!r}.",
            f"[min, max], two {'whole numbers' if integer else 'numbers'} with {low:g} < min <= max <= {high:g}",
            example,
        )
    return (int(pair[0]), int(pair[1])) if integer else (float(pair[0]), float(pair[1]))


def _sub_number(
    path: Path, raw: dict, where: str, key: str, *, low: float, high: float, example: str
) -> float:
    """A number with low <= value <= high."""
    value = raw[key]
    if not _is_number(value) or not low <= value <= high:
        raise _sub_fail(
            path,
            f"{where}.{key}",
            f"{where}.{key} is {value!r}.",
            f"a number from {low:g} to {high:g}",
            example,
        )
    return float(value)


def _hex(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and _HEX_COLOUR.match(value) is not None


def _load_highlighter(path: Path, raw: object) -> HighlighterSettings:
    """Load `overlay.highlighter`; every key required, unknown keys refused."""
    where, example = "overlay.highlighter", _HIGHLIGHTER_EXAMPLE
    block = _sub_mapping(path, raw, where, _HIGHLIGHTER_KEYS, example)
    colours = block["colours"]
    if (
        not isinstance(colours, dict)
        or not colours
        or not all(isinstance(name, str) and name and _is_hex(c) for name, c in colours.items())
    ):
        raise _sub_fail(
            path,
            f"{where}.colours",
            f"{where}.colours is {colours!r}.",
            "a non-empty mapping of colour name to #RRGGBB hex colour",
            example,
        )
    return HighlighterSettings(
        colours={str(name): _hex(c) for name, c in colours.items()},
        opacity=_sub_range(
            path, block, where, "opacity", low=0.0, high=1.0, integer=False, example=example
        ),
        pad_px=_sub_int(path, block, where, "pad_px", minimum=0, example=example),
        end_jitter_px=_sub_int(path, block, where, "end_jitter_px", minimum=0, example=example),
        edge_jitter_px=_sub_int(path, block, where, "edge_jitter_px", minimum=0, example=example),
        density_wobble=_sub_number(
            path, block, where, "density_wobble", low=0.0, high=0.5, example=example
        ),
        end_density=_sub_number(path, block, where, "end_density", low=1.0, high=2.0, example=example),
        end_length_px=_sub_int(path, block, where, "end_length_px", minimum=1, example=example),
    )


def _load_ink(path: Path, raw: object) -> InkSettings:
    """Load `overlay.ink`; every key required, unknown keys refused."""
    where, example = "overlay.ink", _INK_EXAMPLE
    block = _sub_mapping(path, raw, where, _INK_KEYS, example)
    colours = block["colours"]
    if not isinstance(colours, list) or not colours or not all(map(_is_hex, colours)):
        raise _sub_fail(
            path,
            f"{where}.colours",
            f"{where}.colours is {colours!r}.",
            'a non-empty list of #RRGGBB hex colours, e.g. colours: ["#1B2F80"]',
            example,
        )
    shapes = {
        shape: _sub_mapping(path, block[shape], f"{where}.{shape}", keys, example)
        for shape, keys in _INK_SHAPE_KEYS.items()
    }

    def taper(shape: str) -> Taper:
        at = f"{where}.{shape}.taper"
        raw_taper = _sub_mapping(path, shapes[shape]["taper"], at, ("start", "end"), example)
        return Taper(
            start=_sub_number(path, raw_taper, at, "start", low=0.0, high=1.0, example=example),
            end=_sub_number(path, raw_taper, at, "end", low=0.0, high=1.0, example=example),
        )

    def spacing(shape: str) -> float:
        return _sub_number(
            path, shapes[shape], f"{where}.{shape}", "spacing_px", low=0.5, high=50.0, example=example
        )

    def fraction(raw_shape: dict, at: str, key: str) -> tuple[float, float]:
        return _sub_range(path, raw_shape, at, key, low=-0.001, high=1.0, integer=False, example=example)

    tick, circle, underline = shapes["tick"], shapes["circle"], shapes["underline"]
    points = {}
    for name in _TICK_POINTS:
        at = f"{where}.tick.{name}"
        point = _sub_mapping(path, tick[name], at, ("x", "y"), example)
        points[name] = (fraction(point, at, "x"), fraction(point, at, "y"))
    return InkSettings(
        colours=tuple(_hex(c) for c in colours),
        stroke_px=_sub_range(
            path, block, where, "stroke_px", low=0, high=64, integer=True, example=example
        ),
        jitter_px=_sub_int(path, block, where, "jitter_px", minimum=0, example=example),
        tick=TickShape(
            size_px=_sub_int(path, tick, f"{where}.tick", "size_px", minimum=1, example=example),
            start=points["start"],
            knee=points["knee"],
            tip=points["tip"],
            bow=fraction(tick, f"{where}.tick", "bow"),
            taper=taper("tick"),
            spacing_px=spacing("tick"),
        ),
        circle=CircleShape(
            gap_px=_sub_int(path, circle, f"{where}.circle", "gap_px", minimum=1, example=example),
            overrun=fraction(circle, f"{where}.circle", "overrun"),
            taper=taper("circle"),
            spacing_px=spacing("circle"),
        ),
        underline=UnderlineShape(
            gap_px=_sub_int(path, underline, f"{where}.underline", "gap_px", minimum=1, example=example),
            lead_jitters=_sub_range(
                path,
                underline,
                f"{where}.underline",
                "lead_jitters",
                low=-0.001,
                high=20.0,
                integer=False,
                example=example,
            ),
            taper=taper("underline"),
            spacing_px=spacing("underline"),
        ),
    )


def _load_handwriting(path: Path, raw: object) -> HandwritingSettings:
    """Load `overlay.handwriting`; every key required, unknown keys refused.

    Every hand names a vendored family and a weight that family ships: a listed bold with no vendored
    bold file fails here, at load, rather than when a statement first draws that hand.
    """
    where, example = "overlay.handwriting", _HANDWRITING_EXAMPLE
    block = _sub_mapping(path, raw, where, _HANDWRITING_KEYS, example)
    hands_raw = block["hands"]
    if not isinstance(hands_raw, list) or not hands_raw:
        raise _sub_fail(
            path,
            f"{where}.hands",
            f"{where}.hands is {hands_raw!r}.",
            "a non-empty list of {family, bold} mappings",
            example,
        )
    hands: list[Hand] = []
    for index, hand in enumerate(hands_raw):
        at = f"{where}.hands[{index}]"
        hand = _sub_mapping(path, hand, at, ("family", "bold"), example)
        family, bold = hand["family"], hand["bold"]
        if family not in FONT_FAMILIES:
            raise _sub_fail(
                path,
                f"{at}.family",
                f"{at}.family is {family!r}, not a vendored family.",
                f"one of {sorted(FONT_FAMILIES)} (generators/common.py FONT_FAMILIES)",
                example,
            )
        if not isinstance(bold, bool) or bold not in vendored_weights(family):
            raise _sub_fail(
                path,
                f"{at}.bold",
                f"{at}.bold is {bold!r}, a weight '{family}' does not ship (no vendored file in fonts/).",
                f"a weight the family ships ({family}: "
                f"{['bold' if b else 'regular' for b in vendored_weights(family)]}), true or false",
                example,
            )
        if Hand(family, bold) in hands:
            raise _sub_fail(
                path,
                at,
                f"{at} repeats {{family: {family}, bold: {bold}}}.",
                "each hand listed once",
                example,
            )
        hands.append(Hand(family=str(family), bold=bool(bold)))
    jitter_where = f"{where}.glyph_jitter"
    jitter = _sub_mapping(path, block["glyph_jitter"], jitter_where, _GLYPH_JITTER_KEYS, example)
    slant = jitter["slant_deg"]
    if not _is_number(slant) or not 0 <= slant <= 30:
        raise _sub_fail(
            path,
            f"{jitter_where}.slant_deg",
            f"{jitter_where}.slant_deg is {slant!r}.",
            "a number of degrees from 0 to 30",
            example,
        )
    size = _sub_range(path, block, where, "size_px", low=0, high=200, integer=True, example=example)
    size_jitter = _sub_int(path, jitter, jitter_where, "size_px", minimum=0, example=example)
    if size_jitter >= size[0]:
        raise _sub_fail(
            path,
            f"{jitter_where}.size_px",
            f"{jitter_where}.size_px {size_jitter} is not below the smallest line size {size[0]}, so a "
            "glyph could be drawn at no size.",
            f"a jitter below {where}.size_px's minimum",
            example,
        )
    return HandwritingSettings(
        step_away=_sub_range(
            path, block, where, "step_away", low=-0.001, high=2.0, integer=False, example=example
        ),
        below_column_dx_px=_sub_range(
            path, block, where, "below_column_dx_px", low=-1000, high=1000, integer=True, example=example
        ),
        hands=tuple(hands),
        size_px=size,
        glyph_jitter=GlyphJitter(
            size_px=size_jitter,
            slant_deg=float(slant),
            baseline_px=_sub_int(path, jitter, jitter_where, "baseline_px", minimum=0, example=example),
        ),
    )


def _load_items(path: Path, raw: object, schema: dict[str, list[str]], pools: dict) -> dict[str, Item]:
    if not isinstance(raw, dict) or not raw:
        raise _fail(
            path,
            "items",
            "items is not a non-empty mapping.",
            "item id -> {doc_types, kind, emits, requires, rule}.",
            "declare the catalogue items.",
        )
    items: dict[str, Item] = {}
    for item_id, spec in raw.items():
        items[item_id] = _load_item(path, item_id, spec, schema, pools)

    emitted: dict[tuple[str, str], str] = {}
    for item in items.values():
        where = f"items.{item.id}"
        for required in item.requires:
            if required not in items:
                raise _fail(
                    path,
                    f"{where}.requires",
                    f"requires unknown item {required!r}.",
                    f"ids from {sorted(items)}.",
                    f"correct or remove {required!r}.",
                )
            if not item.doc_types & items[required].doc_types:
                raise _fail(
                    path,
                    f"{where}.requires",
                    f"requires {required!r}, which shares no document type with {item.id!r}.",
                    f"an item sharing one of {sorted(item.doc_types)}.",
                    f"correct or remove {required!r}.",
                )
        for doc_type in item.doc_types:
            for key in item.emits:
                other = emitted.setdefault((doc_type, key), item.id)
                if other != item.id:
                    raise _fail(
                        path,
                        f"{where}.emits",
                        f"{key} is emitted by both {other!r} and {item.id!r} for {doc_type}.",
                        "each DX_ key emitted by at most one item per document type.",
                        "rename the key on one of the two items.",
                    )
    for item in items.values():
        seen: set[str] = set()
        stack = list(item.requires)
        while stack:
            current = stack.pop()
            if current == item.id:
                raise _fail(
                    path,
                    f"items.{item.id}.requires",
                    f"{item.id!r} requires itself, directly or through other items.",
                    "an acyclic requires graph.",
                    f"break the cycle by removing a requires entry on the items involved with {item.id!r}.",
                )
            if current not in seen:
                seen.add(current)
                stack.extend(items[current].requires)
    return items


def _load_pools() -> dict:
    """The loaded `config/data_pools.yml`, for checking a rule's `pool:` names a text pool."""
    if not DATA_POOLS_PATH.exists():
        raise _fail(
            DATA_POOLS_PATH,
            "(file)",
            "the content pools file does not exist, so no rule's text pool can be checked.",
            "a YAML mapping carrying distractor_text: {<pool>: [...]}.",
            f"restore {DATA_POOLS_PATH} from git.",
        )
    data = yaml.safe_load(DATA_POOLS_PATH.read_text())
    return data if isinstance(data, dict) else {}


def _check_rule_params(path: Path, where: str, rule: dict, pools: dict) -> None:
    """Refuse a rule parameter that is missing, mistyped, or not one the rule reads (`RULE_PARAMS`)."""
    rule_type = rule["type"]
    declared = RULE_PARAMS[rule_type]
    for key, param in declared.items():
        here = f"{where}.rule.{key}"
        if key not in rule:
            raise _fail(
                path,
                here,
                f"rule type {rule_type!r} needs a '{key}' parameter, and it is missing.",
                f"{param.expected}.",
                f"add {key} to {where}.rule.",
            )
        if not param.valid(rule[key], rule, pools):
            pool_note = f" ({DATA_POOLS_PATH} -> distractor_text)" if param is POOL_PARAM else ""
            raise _fail(
                path,
                here + pool_note,
                f"rule type {rule_type!r} has {key}: {rule[key]!r}, which is not valid.",
                f"{param.expected}.",
                f"fix {here} in {path.resolve()}.",
            )
    unknown = sorted(set(rule) - {"type"} - set(declared))
    if unknown:
        raise _fail(
            path,
            f"{where}.rule",
            f"rule type {rule_type!r} carries {unknown}, which it never reads.",
            f"only {sorted(declared) or 'type alone'} beside type for a {rule_type} rule.",
            f"remove or correct {unknown} on {where}.rule.",
        )


def _load_item(path: Path, item_id: str, spec: object, schema: dict[str, list[str]], pools: dict) -> Item:
    where = f"items.{item_id}"
    if not isinstance(spec, dict):
        raise _fail(path, where, "the item is not a mapping.", "a mapping of item keys.", f"fix {where}.")
    for key in _ITEM_KEYS:
        if key not in spec:
            raise _fail(
                path,
                f"{where}.{key}",
                f"required key '{key}' is missing.",
                f"every item carries {list(_ITEM_KEYS)}.",
                f"add {key} to {where}.",
            )
    doc_types = spec["doc_types"]
    if not isinstance(doc_types, list) or not doc_types:
        raise _fail(
            path,
            f"{where}.doc_types",
            f"doc_types is {doc_types!r}.",
            f"a non-empty list from {sorted(schema)}.",
            f"fix {where}.doc_types.",
        )
    for doc_type in doc_types:
        if doc_type not in schema:
            raise _fail(
                path,
                f"{where}.doc_types",
                f"unknown document type {doc_type!r}.",
                f"one of {sorted(schema)}.",
                f"correct {doc_type!r}.",
            )
    kind = spec["kind"]
    if kind not in ITEM_KINDS:
        raise _fail(
            path,
            f"{where}.kind",
            f"kind is {kind!r}.",
            f"one of {list(ITEM_KINDS)}.",
            f"set {where}.kind to one of {list(ITEM_KINDS)}.",
        )
    target = spec.get("target")
    field = spec.get("field")
    unknown = sorted(set(spec) - set(_ITEM_KEYS) - {"target", "field"})
    if unknown:
        raise _fail(
            path,
            where,
            f"unknown item key(s) {unknown}.",
            f"only {sorted({*_ITEM_KEYS, 'target', 'field'})}.",
            f"remove or correct {unknown} on {where}.",
        )
    for needed, label, present in (("near_miss", "target", target), ("repeat", "field", field)):
        if kind != needed:
            if present is not None or label in spec:
                raise _fail(
                    path,
                    f"{where}.{label}",
                    f"a {kind} item carries '{label}', which only a {needed} item uses.",
                    f"no '{label}' key on a {kind} item.",
                    f"remove {label} from {where}, or change its kind to {needed}.",
                )
            continue
        if not isinstance(present, str) or any(present not in schema[t] for t in doc_types):
            raise _fail(
                path,
                f"{where}.{label}",
                f"a {kind} item's {label} is {present!r}, not a scored column of {sorted(doc_types)}.",
                f"a scored column present on every listed document type, e.g. {label}: TOTAL_AMOUNT.",
                f"set {where}.{label} to a scored column from config/extraction_schema.yml.",
            )
    emits = spec["emits"]
    if (
        not isinstance(emits, list)
        or not emits
        or not all(isinstance(k, str) and k.startswith(DX_PREFIX) for k in emits)
    ):
        raise _fail(
            path,
            f"{where}.emits",
            f"emits is {emits!r}.",
            "a non-empty list of keys that all start with DX_, e.g. [DX_STORE_NUMBER].",
            f"prefix every emitted key of {where} with DX_.",
        )
    requires = spec["requires"]
    if not isinstance(requires, list):
        raise _fail(
            path,
            f"{where}.requires",
            f"requires is {requires!r}, not a list.",
            "a list of item ids, or [].",
            f"fix {where}.requires.",
        )
    rule = spec["rule"]
    if not isinstance(rule, dict) or rule.get("type") not in RULE_TYPES:
        rule_type = rule.get("type") if isinstance(rule, dict) else rule
        raise _fail(
            path,
            f"{where}.rule.type",
            f"unknown rule type {rule_type!r}.",
            f"one of {sorted(RULE_TYPES)}.",
            f"set {where}.rule.type to a known rule.",
        )
    _check_rule_params(path, where, rule, pools)
    arity = rule_arity(rule["type"])
    if len(emits) != arity or len(set(emits)) != len(emits):
        raise _fail(
            path,
            f"{where}.emits",
            f"emits is {emits!r}, but rule type {rule['type']!r} draws {arity} value(s), one per key.",
            f"exactly {arity} distinct DX_ key(s), e.g. emits: "
            + ("[DX_BSB, DX_ACCOUNT_NUMBER]." if arity == 2 else "[DX_STORE_NUMBER]."),
            f"list {arity} key(s) in {where}.emits, or split the item into one item per key.",
        )
    return Item(
        id=item_id,
        doc_types=frozenset(doc_types),
        kind=kind,
        target=target if kind == "near_miss" else None,
        field=field if kind == "repeat" else None,
        emits=tuple(emits),
        rule=rule,
        requires=tuple(requires),
    )
