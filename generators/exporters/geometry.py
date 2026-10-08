"""Draw-time bounding-box capture for the DocILE export.

The Pillow renderers already know where every field is drawn; this records
those pixel boxes and normalises them to relative [left, top, right, bottom]
coordinates in [0, 1], as DocILE requires (spec section 5).

Alongside those scored field boxes it keeps the ink of every drawn text line and
code mark, each with a role and what it says (text, size and weight), so page density
can be measured and the page read back; the string each box was drawn from, so
`pipeline validate` can compare what the page prints with the ground truth; the label
each label site drew; and the box of each declared region (the totals block). Ink,
drawn strings, labels and regions stay internal: `as_dict()`, and so every export,
carries the scored field boxes only.
"""

from typing import NamedTuple

from generators.distractors.keys import DX_PREFIX

# What a drawn line or mark is: a scored value, a distractor, or any other printed text.
INK_ROLES = ("scored", "distractor", "text")


class InkRecord(NamedTuple):
    """One drawn line or code mark, and what it says.

    Attributes:
        role: One of `INK_ROLES`.
        box: Pixel coordinates (left, top, right, bottom), right and bottom exclusive.
        mark: True for a code mark (barcode, QR code, logo) or a pen stroke, whose box is its whole drawn
            rectangle rather than a line of text.
        text: For a text line, the string drawn (never blank); for a code mark, its encoded payload (a
            barcode's digits, a QR code's content, a logo's initials); "" for a pen stroke, which says
            nothing.
        size: A text line's font size in points (above zero); 0 for a mark.
        bold: Whether a text line is drawn in the bold weight; False for a mark.
    """

    role: str
    box: tuple[int, int, int, int]
    mark: bool
    text: str
    size: int
    bold: bool


class BoxRecorder:
    """Collect field bounding boxes while a document is rendered."""

    def __init__(self, width: int, height: int) -> None:
        """Initialise a recorder for one page.

        Args:
            width: Page width in pixels.
            height: Page height in pixels.

        Raises:
            ValueError: If either dimension is not positive.
        """
        if width <= 0:
            msg = (
                f"BoxRecorder width must be positive, got {width}. "
                f"Remediation: pass the rendered image's pixel width."
            )
            raise ValueError(msg)
        if height <= 0:
            msg = (
                f"BoxRecorder height must be positive, got {height}. "
                f"Remediation: pass the rendered image's pixel height."
            )
            raise ValueError(msg)

        self.width = width
        self.height = height
        self._boxes: dict[str, list[float]] = {}
        self._texts: dict[str, str] = {}
        self._ink: list[InkRecord] = []
        self._labels: dict[str, tuple[str, tuple[int, int, int, int] | None]] = {}
        self._regions: dict[str, tuple[int, int, int, int]] = {}

    def record(self, field: str, box: tuple[int, int, int, int], *, text: str) -> None:
        """Record one field's pixel box, normalised to the page, and the string drawn in it.

        Args:
            field: The source column name, suffixed '[i]' for line-item
                members, e.g. 'LINE_ITEM_DESCRIPTIONS[0]'.
            box: Pixel coordinates as (left, top, right, bottom).
            text: The string the box's glyphs were drawn from, its wrapped lines joined by a newline
                (the value alone, without any label drawn beside it).

        Raises:
            ValueError: If this field already has a recorded box.
        """
        if field in self._boxes:
            msg = (
                f"Field '{field}' already has a recorded box "
                f"{self._boxes[field]}. A field drawn twice has no unambiguous "
                f"ground-truth location. Remediation: give the second occurrence "
                f"a distinct field key, or suppress the duplicate draw call."
            )
            raise ValueError(msg)

        left, top, right, bottom = box
        self._boxes[field] = [
            _clamp(left / self.width),
            _clamp(top / self.height),
            _clamp(right / self.width),
            _clamp(bottom / self.height),
        ]
        self._texts[field] = text

    def record_ink(
        self,
        box: tuple[int, int, int, int],
        role: str,
        *,
        text: str,
        size: int,
        bold: bool,
        mark: bool = False,
    ) -> None:
        """Record the pixel box of one drawn text line or code mark, and what it says.

        Unlike `record`, the same box may be recorded more than once: ink measures
        drawn area, not where a field lives.

        Args:
            box: Pixel coordinates as (left, top, right, bottom), right and bottom exclusive.
            role: One of `INK_ROLES`.
            text: The drawn string of a text line; a code mark's payload; "" for a pen stroke.
            size: A text line's font size in points; 0 for a mark.
            bold: Whether a text line is bold; False for a mark.
            mark: True for a code mark's or pen stroke's whole drawn rectangle; always a distractor.

        Raises:
            ValueError: If the role is unknown, a mark is not a distractor, the box is inverted, a text
                line is blank or has no positive size, or a mark carries a size or weight.
        """
        if not mark and (not text.strip() or size <= 0):
            msg = (
                f"A text line was recorded with text {text!r} and size {size}. Remediation: record a "
                f"drawn line with its non-blank string and its font size in points (common.record_text_ink "
                f"does), or record a code mark with mark=True."
            )
            raise ValueError(msg)
        if mark and (size != 0 or bold):
            msg = (
                f"A mark was recorded with size {size} and bold={bold}; a mark has no font. Remediation: "
                f"record code marks and pen strokes with size=0 and bold=False."
            )
            raise ValueError(msg)
        if role not in INK_ROLES:
            msg = (
                f"Ink role {role!r} is not one of {INK_ROLES}. "
                f"Remediation: record the line as 'scored' when its block carries field:, "
                f"'distractor' when it is gated on or references a {DX_PREFIX} key, else 'text'."
            )
            raise ValueError(msg)
        if mark and role != "distractor":
            msg = (
                f"A code mark was recorded with role {role!r}; a mark is never a scored value or "
                f"plain text. Remediation: record code marks with role 'distractor'."
            )
            raise ValueError(msg)
        left, top, right, bottom = box
        if right < left or bottom < top:
            msg = (
                f"Ink box {box} is inverted. Remediation: pass (left, top, right, bottom) with "
                f"right >= left and bottom >= top."
            )
            raise ValueError(msg)
        self._ink.append(
            InkRecord(role, (int(left), int(top), int(right), int(bottom)), mark, text, int(size), bold)
        )

    def record_label(self, field: str, text: str, box: tuple[int, int, int, int] | None) -> None:
        """Record the label a label site drew for `field`, once per field per page.

        Args:
            field: The scored field the site labels, e.g. `TOTAL_AMOUNT`.
            text: The label as drawn ("" when the site drew none).
            box: The label's pixel ink box, or None when it drew none.

        Raises:
            ValueError: The field already has a label on this page, or text and box disagree about
                whether a label was drawn.
        """
        if field in self._labels:
            msg = (
                f"Field '{field}' already has a recorded label {self._labels[field]!r}. A field labelled "
                f"twice on one page has no one label. Remediation: give the layout one label site per "
                f"field (the layout schema checks this)."
            )
            raise ValueError(msg)
        if (box is None) != (not text.strip()):
            msg = (
                f"Label of '{field}' recorded with text {text!r} and box {box}. Remediation: record "
                "a drawn "
                f"label with its ink box, and an undrawn one as text '' with box None."
            )
            raise ValueError(msg)
        self._labels[field] = (text, box)

    def labels(self) -> dict[str, tuple[str, tuple[int, int, int, int] | None]]:
        """Return the label each label site drew, by field.

        Returns:
            Field -> (label text, pixel ink box), the box None when the site drew no label.
        """
        return dict(self._labels)

    def record_region(self, name: str, box: tuple[int, int, int, int]) -> None:
        """Record a declared region's pixel box (a stack's `region:`), once per name per page.

        Args:
            name: The region's name, e.g. `totals`.
            box: Pixel coordinates (left, top, right, bottom): the stack's column, from its start y to
                its end y.

        Raises:
            ValueError: A region of this name is already recorded.
        """
        if name in self._regions:
            msg = (
                f"Region '{name}' is already recorded at {self._regions[name]}. A page has one region of "
                f"each name. Remediation: declare region: {name} on one stack of the layout (the layout "
                f"schema checks this)."
            )
            raise ValueError(msg)
        self._regions[name] = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))

    def regions(self) -> dict[str, tuple[int, int, int, int]]:
        """Return every recorded region's pixel box, by name."""
        return dict(self._regions)

    def ink_boxes(self) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Return every recorded ink box with its role, in draw order.

        Returns:
            (role, pixel box) pairs; code marks appear with role 'distractor'.
        """
        return [(record.role, record.box) for record in self._ink]

    def ink_records(self) -> list[InkRecord]:
        """Return every recorded ink box with its role and mark flag, in draw order.

        Returns:
            The ink records, which the density metrics consume.
        """
        return list(self._ink)

    def as_dict(self) -> dict[str, list[float]]:
        """Return the recorded boxes.

        Returns:
            Mapping of field key to [left, top, right, bottom] in [0, 1].
        """
        return dict(self._boxes)

    def texts(self) -> dict[str, str]:
        """Return the string drawn in each recorded box.

        Returns:
            Mapping of field key (as in `as_dict`) to the drawn string, wrapped lines joined by a newline.
        """
        return dict(self._texts)


def records_ink(distractors: bool, geometry_out: dict | None, ink_out: list[InkRecord] | None) -> bool:
    """Return whether a render keeps a recorder.

    A distractor render always does, so its ink exists for any caller that places marks around the
    drawn lines, whether or not it asked for geometry. A plain render keeps one only when a caller asks
    for geometry or ink, so a bare plain render is drawn exactly as before.

    Args:
        distractors: Whether the render draws distractors.
        geometry_out: The caller's geometry dict, or None.
        ink_out: The caller's ink list, or None.

    Returns:
        True when the render should create a `BoxRecorder`.
    """
    return distractors or geometry_out is not None or ink_out is not None


def rescale_vertical(
    boxes: dict[str, list[float]], *, old_height: int, new_height: int
) -> dict[str, list[float]]:
    """Correct normalised y-coordinates after a page is cropped post-hoc.

    Receipts render onto an oversized canvas (unknown final length up front)
    and crop to content afterwards; boxes captured against the oversized
    canvas need their vertical fractions rescaled to the final, cropped page
    height. Horizontal fractions are unaffected — width never changes.

    Args:
        boxes: Normalised boxes as returned by `BoxRecorder.as_dict()`,
            captured while `old_height` was the recorder's page height.
        old_height: The page height (px) the boxes were normalised against.
        new_height: The actual final page height (px) after cropping.

    Returns:
        A new mapping with the same field keys and corrected y-coordinates.
    """
    if old_height == new_height:
        return dict(boxes)
    factor = old_height / new_height
    return {
        field: [left, _clamp(top * factor), right, _clamp(bottom * factor)]
        for field, (left, top, right, bottom) in boxes.items()
    }


def _clamp(value: float) -> float:
    """Clamp a normalised coordinate into [0, 1].

    Args:
        value: A coordinate that may fall outside the page.

    Returns:
        The coordinate, bounded to the unit interval and rounded to 6 places.
    """
    return round(min(max(value, 0.0), 1.0), 6)
