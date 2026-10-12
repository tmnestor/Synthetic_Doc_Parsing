"""The unambiguity rules: may a page print a field's label this way and still be read correctly?

`check_label_choice` reads a rendered page (its `geometry_out`: scored boxes and strings, every ink line
with its text, size and weight, the label each site drew and the totals region) and returns one `Refusal`
per broken rule. Only the styles that change what a reader relies on are checked: `labelled` is the
distractor render's own label and is never refused.

Unlabelled fields (spec section 6, as ruled in the label-variation plan's Decisions 11 to 14):

- `abn_rival`: another 11-digit number on the page (consecutive digit groups, joined by single spaces,
  holding exactly 11 digits between them, read from every ink line and every code mark's payload) passes
  the ABN checksum. Every such window of a longer run counts, so a stray group before an ABN-shaped number
  ('Ref 7 39 703 412 408') does not hide it. A second printing of the same ABN is not a rival: it gives
  the same answer.
- `gst_outside_totals`: the GST value line is not inside the totals region.
- `gst_below_total`: the GST value line is not above the total's value line.
- `gst_rival`: another amount line in the totals region equals TOTAL_AMOUNT / 11, rounded half-up to
  cents.
- `total_not_last`: an amount line in the totals region lies below the total, other than a repeat of it
  (a line whose every amount equals TOTAL_AMOUNT, which a reader may take for the total without error).
- `total_not_prominent`: another amount line in the totals region is drawn larger than the total, or at
  its size in bold where the total is regular.
- `total_near_miss_twin`: a distractor amount line anywhere on the page, other than a repeat of the
  total, is drawn at the total's size and weight together. A repeat drawn as prominently (an unpaid
  invoice's bold 'Amount due' in its account summary) is not a twin: it prints the total itself, so
  whichever of the two a reader takes, the answer is the total.

Variants:

- `collision`: the wording shares a word (case-folded, punctuation stripped, holding a letter, not in
  `label_variation.collision.ignore_words.<field>`) with a distractor text line on the page.
- `overlap`: the label's ink box meets another line's or mark's ink box (an area in common).

Every unlabelled field:

- `overlap`: the value's ink line meets another line's or mark's ink box. An unlabelled ABN on a
  left-aligned or centred line moves or recentres by its label's width (Decision 18), and may move onto
  its neighbours.

An amount line is an ink text line printing a money amount as `DrawnValuePolicy.money_in` reads it, the
reading the drawn-field guard applies to scored amounts, so the guard and these rules read money one way.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from generators.common import validate_abn
from generators.drawn_values import DrawnValuePolicy
from generators.exporters.geometry import InkRecord
from generators.labels.config import LabelConfig, normalise_wording
from generators.labels.keys import LABELLED, TOTALS_REGION, UNLABELLED, VARIANT, LabelChoice

ABN_FIELD = "BUSINESS_ABN"
GST_FIELD = "GST_AMOUNT"
TOTAL_FIELD = "TOTAL_AMOUNT"

RULES = (
    "abn_rival",
    "gst_outside_totals",
    "gst_below_total",
    "gst_rival",
    "total_not_last",
    "total_not_prominent",
    "total_near_miss_twin",
    "collision",
    "overlap",
)
# What each rule means, for diagnostics.
RULE_MEANINGS = {
    "abn_rival": "another 11-digit number on the page passes the ABN checksum",
    "gst_outside_totals": "the unlabelled GST figure is not inside the totals region",
    "gst_below_total": "the unlabelled GST figure is not above the total",
    "gst_rival": "another amount in the totals region equals the total / 11",
    "total_not_last": "an amount other than a repeat of the total lies below the unlabelled total in the "
    "totals region",
    "total_not_prominent": "another amount in the totals region is drawn more prominently than the "
    "unlabelled total",
    "total_near_miss_twin": "a distractor amount on the page shares the unlabelled total's size and weight",
    "collision": "the variant wording shares a word with a distractor line on the page",
    "overlap": "the variant label's ink, or the unlabelled value's line, meets another ink box",
}
# A run of digit groups joined by single spaces; maximal because the groups are greedy.
_DIGIT_RUN = re.compile(r"\d+(?: \d+)*")
_CENT = Decimal("0.01")
# Slack, in pixels, when asking whether a recorded value box holds an ink line or lies in a region:
# normalised boxes are rounded on the way back to pixels.
_SLACK = 2

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Refusal:
    """One rule a label choice breaks on one page.

    Attributes:
        field: The scored field whose label was refused.
        style: The refused pick's style.
        wording: The refused pick's wording ("" unless a variant).
        rule: One of `RULES`.
        evidence: What on the page breaks the rule: the line's text and its pixel box, or the boxes.
        case_id: The case rendered.
        layout_id: Its layout.
    """

    field: str
    style: str
    wording: str
    rule: str
    evidence: str
    case_id: str
    layout_id: str

    def describe(self) -> str:
        """Return the refusal in one line: case, layout, field, style, wording, rule and evidence."""
        wording = f" {self.wording!r}" if self.style == VARIANT else ""
        return (
            f"{self.case_id} / {self.layout_id}: {self.field} {self.style}{wording} refused by {self.rule} "
            f"({RULE_MEANINGS[self.rule]}): {self.evidence}"
        )


def _pixels(geometry: dict, field: str) -> tuple[float, float, float, float]:
    left, top, right, bottom = geometry["boxes"][field]
    width, height = geometry["width"], geometry["height"]
    return left * width, top * height, right * width, bottom * height


def _centre(box: Sequence[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _within(point: tuple[float, float], box: Sequence[float], slack: float = _SLACK) -> bool:
    return box[0] - slack <= point[0] <= box[2] + slack and box[1] - slack <= point[1] <= box[3] + slack


def _meets(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether two (left, top, right, bottom) boxes, right and bottom exclusive, share an area."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def value_lines(geometry: dict, field: str) -> list[InkRecord]:
    """Return the scored ink lines that draw a field's value (its recorded box holds their centres).

    Args:
        geometry: The render's `geometry_out`.
        field: A scored field with a recorded box.

    Returns:
        The value's ink lines, in draw order.

    Raises:
        RuntimeError: The field has a box but no scored ink line inside it, which means the recorder
            and the drawing disagree (a renderer defect, never a page to judge).
    """
    box = _pixels(geometry, field)
    lines = [
        record
        for record in geometry["ink"]
        if record.role == "scored" and not record.mark and _within(_centre(record.box), box)
    ]
    if not lines:
        raise RuntimeError(
            "A scored value has a box but no ink.\n"
            f"  What:     {field}'s recorded box {tuple(round(v) for v in box)} holds no scored ink "
            "line, so its size, weight and position cannot be read.\n"
            "  Where:    the renderer's recorder (generators/exporters/geometry.py BoxRecorder.record and "
            "record_ink, called by the block drawing the field)\n"
            "  Expected: every recorded scored box drawn as scored ink inside it, e.g. a pair's value "
            "recorded with record_text_ink(value_ink, ...).\n"
            "  Recover:  record the value's ink with the scored role where the block draws it."
        )
    return lines


def _amount_lines(geometry: dict, policy: DrawnValuePolicy) -> list[tuple[InkRecord, list[Decimal]]]:
    found = []
    for record in geometry["ink"]:
        if record.mark:
            continue
        amounts = policy.money_in(record.text)
        if amounts:
            found.append((record, amounts))
    return found


def _show(record: InkRecord) -> str:
    weight = "bold" if record.bold else "regular"
    return f"{record.text!r} at {record.box} ({record.size}pt {weight}, {record.role})"


def eleven_digit_runs(text: str) -> list[str]:
    """Return every 11-digit number in a text a reader could take for an ABN.

    A number is a window of consecutive digit groups within a run of groups joined by single spaces,
    holding exactly 11 digits between them. A group is never split, so a 16-digit transaction id or a
    10-digit phone number holds none, while 'Ref 7 39 703 412 408' yields '39 703 412 408'.

    Args:
        text: A drawn line or a code mark's payload.

    Returns:
        Each window holding exactly 11 digits, as drawn, in order of its first group.
    """
    found = []
    for run in _DIGIT_RUN.findall(text):
        groups = run.split(" ")
        for start in range(len(groups)):
            digits = 0
            for end in range(start, len(groups)):
                digits += len(groups[end])
                if digits == 11:
                    found.append(" ".join(groups[start : end + 1]))
                if digits >= 11:
                    break
    return found


def _abn_rules(geometry: dict, entry: dict) -> list[tuple[str, str]]:
    own = str(entry["fields"][ABN_FIELD]).replace(" ", "")
    found = []
    for record in geometry["ink"]:
        for run in eleven_digit_runs(record.text):
            if run.replace(" ", "") != own and validate_abn(run):
                found.append(("abn_rival", f"{run!r} passes the ABN checksum, in {_show(record)}"))
    return found


def _one_line(lines: list[InkRecord]) -> InkRecord:
    """Fold a value's ink lines into one record: their union box, largest size, bold only if all are."""
    box = (
        min(r.box[0] for r in lines),
        min(r.box[1] for r in lines),
        max(r.box[2] for r in lines),
        max(r.box[3] for r in lines),
    )
    text = " ".join(r.text for r in lines)
    return InkRecord("scored", box, False, text, max(r.size for r in lines), all(r.bold for r in lines))


def _gst_rules(geometry: dict, entry: dict, policy: DrawnValuePolicy) -> list[tuple[str, str]]:
    gst = _one_line(value_lines(geometry, GST_FIELD))
    region = geometry["regions"].get(TOTALS_REGION)
    found = []
    if region is None or not _within(_centre(gst.box), region, slack=1):
        where = "the page records no totals region" if region is None else f"the totals region is {region}"
        found.append(("gst_outside_totals", f"the GST figure {_show(gst)} lies outside it; {where}"))
    if TOTAL_FIELD in geometry["boxes"]:
        total = _one_line(value_lines(geometry, TOTAL_FIELD))
        if not _centre(gst.box)[1] < _centre(total.box)[1]:
            found.append(
                ("gst_below_total", f"the GST figure {_show(gst)} is not above the total {_show(total)}")
            )
    if region is not None and TOTAL_FIELD in entry["fields"]:
        eleventh = (Decimal(str(entry["fields"][TOTAL_FIELD])) / 11).quantize(_CENT, ROUND_HALF_UP)
        gst_own = set(value_lines(geometry, GST_FIELD))
        for record, amounts in _amount_lines(geometry, policy):
            if record in gst_own or not _within(_centre(record.box), region, slack=1):
                continue
            if eleventh in amounts:
                found.append(("gst_rival", f"{_show(record)} prints {eleventh}, the total / 11"))
    return found


def _total_rules(geometry: dict, entry: dict, policy: DrawnValuePolicy) -> list[tuple[str, str]]:
    own_lines = value_lines(geometry, TOTAL_FIELD)
    total = _one_line(own_lines)
    value = Decimal(str(entry["fields"][TOTAL_FIELD]))
    region = geometry["regions"].get(TOTALS_REGION)
    found = []
    for record, amounts in _amount_lines(geometry, policy):
        if record in own_lines:
            continue
        repeat = all(amount == value for amount in amounts)
        in_region = region is not None and _within(_centre(record.box), region, slack=1)
        if in_region and not repeat and _centre(record.box)[1] > _centre(total.box)[1]:
            found.append(("total_not_last", f"{_show(record)} lies below the total {_show(total)}"))
        if in_region and (
            record.size > total.size or (record.size == total.size and record.bold and not total.bold)
        ):
            found.append(("total_not_prominent", f"{_show(record)} outranks the total {_show(total)}"))
        if (
            record.role == "distractor"
            and not repeat
            and record.size == total.size
            and record.bold == total.bold
        ):
            found.append(
                (
                    "total_near_miss_twin",
                    f"{_show(record)} shares the total's size and weight {_show(total)}",
                )
            )
    return found


def wording_words(text: str, ignore: frozenset[str]) -> set[str]:
    """Return the words a collision compares: normalised, holding a letter, not ignored.

    Args:
        text: A wording or a drawn line.
        ignore: Normalised words a collision ignores.

    Returns:
        The words.
    """
    return {
        word
        for word in normalise_wording(text).split()
        if any(char.isalpha() for char in word) and word not in ignore
    }


def _unlabelled_overlap(geometry: dict, field: str) -> list[tuple[str, str]]:
    """Refuse an unlabelled value whose line, moved by its label's removal, meets other ink."""
    own = value_lines(geometry, field)
    return [
        ("overlap", f"the unlabelled value {_show(line)} meets {_show(record)}")
        for line in own
        for record in geometry["ink"]
        if record not in own and _meets(line.box, record.box)
    ]


def _variant_rules(geometry: dict, field: str, wording: str, config: LabelConfig) -> list[tuple[str, str]]:
    found = []
    ignore = config.ignore_words[field]
    words = wording_words(wording, ignore)
    for record in geometry["ink"]:
        if record.role != "distractor" or record.mark:
            continue
        shared = words & wording_words(record.text, ignore)
        if shared:
            found.append(("collision", f"shares {sorted(shared)} with {_show(record)}"))
    label = geometry["labels"].get(field)
    if label is None or label[1] is None:
        return found
    text, box = label
    for record in geometry["ink"]:
        # The label's own line: a pair's label is a line of its own; a text site's label is the head of
        # the line it shares with its value.
        own = (
            record.text.startswith(text.strip())
            and abs(record.box[1] - box[1]) <= _SLACK
            and abs(record.box[0] - box[0]) <= _SLACK
        )
        if not own and _meets(box, record.box):
            found.append(("overlap", f"the label {text!r} at {box} meets {_show(record)}"))
    return found


def check_label_choice(
    geometry: dict,
    choice: LabelChoice,
    entry: dict,
    *,
    case_id: str,
    layout_id: str,
    doc_type: str,
    config: LabelConfig,
    policy: DrawnValuePolicy,
) -> list[Refusal]:
    """Return every rule the page's label choice breaks, one `Refusal` per rule and line.

    A field the page does not draw (no recorded box) is not judged: there is nothing to misread.

    Args:
        geometry: The render's `geometry_out` (boxes, texts, ink, labels, regions, width, height).
        choice: The label choice the page was drawn with.
        entry: The entry rendered (its `fields` carry the ground-truth BUSINESS_ABN and TOTAL_AMOUNT;
            the provider-enriched entry serves equally).
        case_id: The case, named in each refusal.
        layout_id: The layout, named in each refusal.
        doc_type: The extraction-schema document type; only its varied fields are judged.
        config: The label configuration (the collision's ignored words).
        policy: The drawn-value policy, which reads money.

    Returns:
        The refusals, by field in the type's varied order, then rule; empty when the choice is accepted.
    """
    refusals: list[Refusal] = []
    for field in config.doc_types[doc_type]:
        pick = choice.get(field)
        if pick is None or pick.style == LABELLED or field not in geometry["boxes"]:
            continue
        found: list[tuple[str, str]] = []
        if pick.style == UNLABELLED:
            if field == ABN_FIELD:
                found = _abn_rules(geometry, entry)
            elif field == GST_FIELD:
                found = _gst_rules(geometry, entry, policy)
            elif field == TOTAL_FIELD:
                found = _total_rules(geometry, entry, policy)
            else:
                raise ValueError(
                    "A varied field has no unambiguity rule.\n"
                    f"  What:     {field} may be drawn unlabelled, but the unambiguity rules know only "
                    f"{[ABN_FIELD, GST_FIELD, TOTAL_FIELD]}, so an unlabelled {field} would pass "
                    "unjudged.\n"
                    f"  Where:    {config.path} -> label_variation.doc_types.{doc_type}\n"
                    f"  Expected: varied fields among {[ABN_FIELD, GST_FIELD, TOTAL_FIELD]}, e.g. "
                    f"{doc_type}: [{ABN_FIELD}, {GST_FIELD}, {TOTAL_FIELD}].\n"
                    f"  Recover:  remove {field} from label_variation.doc_types.{doc_type}, or add its "
                    "rule to generators/labels/unambiguity.py."
                )
            found = found + _unlabelled_overlap(geometry, field)
        else:
            found = _variant_rules(geometry, field, pick.wording, config)
        refusals.extend(
            Refusal(field, pick.style, pick.wording, rule, evidence, case_id, layout_id)
            for rule, evidence in found
        )
    return refusals
