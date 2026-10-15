"""Horizontal geometry and per-render state for the layout engine.

`Region` is the only place nesting arithmetic lives: a container narrows or
divides its own region and hands the result to its children, so no primitive
needs to know how deeply it is nested.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from PIL import ImageDraw

from generators.common import InkSink
from generators.distractors.keys import DX_PREFIX
from generators.exporters.geometry import BoxRecorder
from generators.layout_dsl.binding import referenced_fields


@dataclass(frozen=True)
class Region:
    """A horizontal slice of the page available to a block.

    Attributes:
        x: Absolute left edge in pixels.
        width: Usable content width in pixels.
    """

    x: int
    width: int

    @property
    def right(self) -> int:
        """Absolute right edge in pixels."""
        return self.x + self.width

    def indent(self, left: int, right: int = 0) -> "Region":
        """Return a narrowed region inset from this one.

        Args:
            left: Pixels to inset from the left edge.
            right: Pixels to inset from the right edge.

        Returns:
            A new Region shifted right by `left` and narrowed by `left + right`.

        Raises:
            ValueError: If the insets consume the whole region.
        """
        width = self.width - left - right
        if width < 1:
            raise self._indent_error(left, right, width)
        return Region(x=self.x + left, width=width)

    def _indent_error(self, left: int, right: int, width: int) -> ValueError:
        """Build a four-element diagnostic for an indent that consumes the region.

        `panel` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        always with `left == right == padding`, so the remediation names that key.
        """
        max_padding = max((self.width - 1) // 2, 0)
        return ValueError(
            "Region.indent leaves no usable width.\n"
            f"  What:     indent(left={left}, right={right}) leaves width {width} from "
            f"a {self.width}px region.\n"
            "  Where:    config/layouts/*.yml -> a panel block's `padding` key.\n"
            f"  Expected: padding <= {max_padding} for a {self.width}px region, e.g. "
            f"padding: {max_padding}.\n"
            "  Recover:  reduce the panel's padding, or widen its content_width."
        )

    def divide(self, n: int, gap: int) -> list["Region"]:
        """Split this region into `n` columns separated by `gap` px.

        Columns differ by at most 1px: the remainder left over by floor
        division is handed out one pixel at a time to the leftmost columns,
        so the last column's right edge always reaches `self.right` instead
        of falling short by up to `n - 1` px.

        Args:
            n: Number of columns; must be at least 1.
            gap: Pixels between adjacent columns.

        Returns:
            `n` Regions, left to right.

        Raises:
            ValueError: If `n` < 1, or the gaps leave no usable column width.
        """
        if n < 1:
            msg = f"Region.divide needs n >= 1, got {n}. Remediation: pass a positive column count."
            raise ValueError(msg)
        total_gap = gap * (n - 1)
        usable = self.width - total_gap
        column = usable // n
        if column < 1:
            raise self._divide_error(n, gap, column)
        # Hand the remainder out one pixel at a time to the leftmost columns, so
        # the columns differ by at most 1px and the last one reaches self.right.
        remainder = usable - column * n
        regions: list[Region] = []
        x = self.x
        for i in range(n):
            width = column + (1 if i < remainder else 0)
            regions.append(Region(x=x, width=width))
            x += width + gap
        return regions

    def divide_widths(self, widths: list[int], gap: int) -> list["Region"]:
        """Split this region into columns of explicit widths, separated by `gap` px.

        Unlike `divide`, columns need not be equal: invoice totals occupy a
        fixed 400px column at the page's right edge (`right_edge - 400`),
        which equal division cannot express. Each column's x is the previous
        column's right edge plus `gap` -- the same stepping `divide` uses --
        so the two behave consistently wherever a layout mixes them.

        Args:
            widths: Column widths in pixels, left to right.
            gap: Pixels between adjacent columns.

        Returns:
            `len(widths)` Regions, left to right.

        Raises:
            ValueError: If `sum(widths) + gap * (len(widths) - 1)` exceeds
                `self.width`.
        """
        total = sum(widths) + gap * (len(widths) - 1)
        if total > self.width:
            raise self._divide_widths_error(widths, gap, total)
        regions: list[Region] = []
        x = self.x
        for width in widths:
            regions.append(Region(x=x, width=width))
            x += width + gap
        return regions

    def _divide_widths_error(self, widths: list[int], gap: int, total: int) -> ValueError:
        """Build a four-element diagnostic for divide_widths widths that overflow the region.

        `split` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        so the remediation names its `widths` key.
        """
        return ValueError(
            "Region.divide_widths overflows the region.\n"
            f"  What:     widths {widths} with gap={gap} need {total}px but this region "
            f"is only {self.width}px.\n"
            "  Where:    config/layouts/*.yml -> a split block's `widths` key.\n"
            f"  Expected: sum(widths) + gap * (len(widths) - 1) <= {self.width}, e.g. "
            "shrink one or more of the declared widths.\n"
            "  Recover:  reduce a width in split.widths, or reduce the split's gap."
        )

    def _divide_error(self, n: int, gap: int, column: int) -> ValueError:
        """Build a four-element diagnostic for a divide that leaves no column width.

        `split` is the only caller (see `generators/layout_dsl/primitives_container.py`),
        so the remediation names its `gap` key.
        """
        max_gap = max((self.width - n) // max(n - 1, 1), 0)
        return ValueError(
            "Region.divide leaves no usable column width.\n"
            f"  What:     divide({n}, gap={gap}) leaves column width {column} from a "
            f"{self.width}px region.\n"
            "  Where:    config/layouts/*.yml -> a split block's `gap` key.\n"
            f"  Expected: gap <= {max_gap} for {n} columns in a {self.width}px region, "
            f"e.g. gap: {max_gap}.\n"
            "  Recover:  reduce the split's gap, or its number of columns."
        )


@dataclass
class RenderContext:
    """Everything a primitive needs besides its own block dict and the y-cursor.

    Attributes:
        draw: The PIL drawing surface.
        entry: The ground-truth entry being rendered.
        layout: The resolved layout dict.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        region: The horizontal slice this block may draw into.
        recorder: Optional draw-time bounding-box capture of scored fields. A
            table with `capture: false` clears it for its own cells.
        ink_recorder: Optional capture of every drawn line's ink and role, for
            the density metrics. Kept apart from `recorder` because a table
            that suppresses scored capture still draws ink.
        dx_gated: True while drawing a block gated (`when:`) on a `DX_` key, or
            any block nested inside one; the engine sets it per block.
        render_children: The walker, injected by the engine so containers can
            render nested blocks without importing the engine — which would be
            a circular import, since the engine's dispatch table imports them.
        block_path: Where the blocks this context walks sit in the layout, e.g. `body` or
            `body[3].children`; the walker appends each block's position to name the block it scopes.
        block_anchor: The `anchor:` of the nearest enclosing container that declares one (e.g. `foot`), or
            None; a block with no anchor of its own records this one.
        drawn_out: Optional (opt-in): when given, the walker records in it, by block path, every block
            it draws onto the recorded page (see `generators.layout_dsl.engine.DrawnBlock`).
    """

    draw: ImageDraw.ImageDraw
    entry: dict
    layout: dict
    layout_id: str
    layout_path: str
    region: Region
    recorder: BoxRecorder | None = None
    render_children: "Callable[[list, RenderContext, int], int] | None" = None
    ink_recorder: BoxRecorder | None = None
    dx_gated: bool = False
    block_path: str = "body"
    block_anchor: str | None = None
    drawn_out: dict | None = None

    def within(self, region: Region) -> "RenderContext":
        """Return a copy of this context scoped to a different region.

        Args:
            region: The child region.

        Returns:
            A new RenderContext sharing all state but the region. `dataclasses.replace` copies every
            field, so one added later cannot be dropped here.
        """
        return replace(self, region=region)

    def ink_role(self, *templates: str, field: str | None = None) -> str:
        """Return the ink role of a line this context draws.

        Args:
            templates: The block's text templates (content, label, value, lines);
                any `{DX_...}` placeholder in them makes the line a distractor.
            field: The scored field the line records, if any.

        Returns:
            'scored' when `field` is given; 'distractor' when the block is
            DX-gated or a template references a `DX_` key; else 'text'.
        """
        if field is not None:
            return "scored"
        if self.dx_gated or any(
            name.startswith(DX_PREFIX)
            for template in templates
            for name in referenced_fields(str(template))
        ):
            return "distractor"
        return "text"

    def ink(self, role: str) -> InkSink | None:
        """Return a sink recording ink boxes with `role`, or None when ink is not captured.

        Args:
            role: One of `generators.exporters.geometry.INK_ROLES`.

        Returns:
            A callable taking a pixel box and the line's text, size and weight, or None.
        """
        recorder = self.ink_recorder
        if recorder is None:
            return None

        def sink(box: tuple[int, int, int, int], *, text: str, size: int, bold: bool) -> None:
            recorder.record_ink(box, role, text=text, size=size, bold=bold)

        return sink
