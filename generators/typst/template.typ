// The shared template every typeset page imports: one function per layout primitive, the page's text
// settings, and the two record wrappers the ground truth is read back from.
//
// generators/typst/translate.py writes each page as calls to these functions. Every length it passes is
// already in points, converted from stage one's pixels at the page's own scale, so nothing here knows a
// pixel. Text, sizes, fonts and colours come from the layout and the entry; this file holds layout
// mechanics only.

// Decision 5: no hyphenation, no ligatures and no justification, so the PDF's text layer holds exactly
// the letters the ground truth records. Typst still wraps at spaces. Blocks stack with no gap of their
// own: every vertical distance on the page is one the layout states.
#let typeset(body) = {
  set text(hyphenate: false, ligatures: false, lang: "en")
  set par(justify: false, leading: 0pt, spacing: 0pt)
  set block(spacing: 0pt)
  body
}

// `body` as a block of its own measured size, aligned within its region, with its box handed to `emit`
// as a dictionary of points: the page number, the top-left corner (`x`, `y`), the size (`w`, `h`) and
// the column it was laid out in (`column-x`, `column-w`: its region's left edge and width). The size is
// the body's own as laid out in its region (a short line is narrower than its column, a wrapped one as
// wide as its widest line), and the corner is where the alignment puts it. Each line of the body keeps
// the alignment inside that block too, set there explicitly rather than inherited. The record is the
// first thing inside the block, so `here()` is the block's top-left; the column's left edge is read the
// same way at the start of the full-width block around it.
#let _box-of(body, emit, align) = block(width: 100%, context {
  let column = here().position()
  std.align(align, layout(region => {
    let size = measure(body, width: region.width)
    block(width: size.width, {
      context {
        let p = here().position()
        emit((page: p.page, x: p.x.pt(), y: p.y.pt(), w: size.width.pt(), h: size.height.pt(),
          column-x: column.x.pt(), column-w: region.width.pt()))
      }
      std.align(align, body)
    })
  }))
})

// One block a parser can find: a labelled `metadata` element carrying stage one's `BlockRecord` fields
// and the block's own position and size, then the block itself, aligned as the layout places it.
// `query(<block-record>)` (run by generators/typst/compile.py) lists them in draw order.
#let block-record(id: 0, kind: "", path: "", anchor: none, category: none, order: 0, columns: none,
  join: none, align: left, body) = _box-of(body, where => [#metadata((
    id: id, kind: kind, path: path, anchor: anchor, category: category, order: order, columns: columns,
    join: join,
  ) + where)<block-record>], align)

// One table cell: its block's id, its drawn row and column and the columns it spans, and its box.
#let cell-record(block: 0, row: 0, col: 0, span: 1, align: left, body) = _box-of(body, where => [
  #metadata((block: block, row: row, col: col, span: span) + where)<cell-record>], align)

// A leaf drawn inside a block an enclosing container opened: placed as a record would place it.
#let dsl-align(align: left, body) = block(width: 100%, std.align(align, body))

// The text style of one line box. A line is `advance` tall with its baseline `ascent` below its top,
// as stage one draws it, so consecutive lines and blocks keep the layout's own vertical rhythm.
#let _line-style(font: "", size: 10pt, bold: false, fill: black, ascent: 8pt, advance: 12pt, body) = {
  set text(font: font, size: size, weight: if bold { "bold" } else { "regular" }, fill: fill,
    top-edge: ascent, bottom-edge: ascent - advance)
  body
}

// Every leaf below is drawn at its own size; the record (or `dsl-align`) around it places it.

// `text`: one value, wrapped by Typst within its region.
#let dsl-text(..style, body) = _line-style(..style, body)

// `pair`: a label and its value, the value either following the label or pinned to the right edge.
// A right-aligned value spans the region; a following one is as wide as the label and value.
#let dsl-pair(value-align: left, label: "", ..style, value) = _line-style(..style,
  if value-align == right {
    grid(columns: (auto, 1fr), label, std.align(right, value))
  } else {
    label + value
  })

// `rule`: a drawn line `thickness` tall, or (`dashed`) a dashed line through a line box `advance`
// tall, standing for stage one's row of repeated glyphs without putting them in the text layer.
#let dsl-rule(thickness: 1pt, fill: black, above: 0pt, below: 0pt, dashed: false, advance: 0pt) = {
  v(above)
  if dashed {
    block(width: 100%, height: advance, std.align(horizon, line(length: 100%,
      stroke: (paint: fill, thickness: thickness, dash: "dashed"))))
  } else {
    block(width: 100%, height: thickness, fill: fill)
  }
  v(below)
}

// `spacer`: a fixed vertical gap.
#let dsl-spacer(height: 0pt) = v(height)

// `split`: child block lists side by side, in columns of the given widths separated by `gap`.
#let dsl-split(widths: (), gap: 0pt, ..columns) = grid(columns: widths, column-gutter: gap,
  ..columns.pos())

// `stack`: child blocks one under another.
#let dsl-stack(body) = block(width: 100%, body)

// `anchor: foot`: a block drawn up from the bottom of the page rather than after the one above it.
#let dsl-foot(body) = place(bottom + left, block(width: 100%, body))

// `panel`: child blocks inside a bordered box, inset by `padding`.
#let dsl-panel(padding: 0pt, stroke: 1pt + black, height: auto, body) = block(width: 100%,
  height: height, inset: padding, stroke: stroke, body)

// One line in its own style: a table cell's line, or a barcode's caption.
#let dsl-line(..style, body) = _line-style(..style, body)

// One table cell: a `cell-record` inset within its grid cell and aligned as its column is.
#let dsl-cell(block: 0, row: 0, col: 0, span: 1, inset: (:), align: left, lines) = grid.cell(
  colspan: span, inset: inset, cell-record(block: block, row: row, col: col, span: span, align: align,
    lines.join(linebreak())))

// `table`: a grid of cells. `filled` shades the header row, `bordered` rules every cell, `plain` draws
// no frame.
#let dsl-table(widths: (), frame: "plain", fill: none, stroke: 1pt + black, header: false, ..cells) = {
  grid(
    columns: widths,
    fill: (x, y) => if frame == "filled" and header and y == 0 { fill } else { none },
    stroke: if frame == "bordered" { stroke } else { none },
    ..cells.pos(),
  )
}

// `logo`: a filled circle, square or chevron with the name's initials centred inside it.
#let dsl-logo(size: 10pt, style: "circle", fill: black, notch: 0pt, initials: none) = {
  let shape = if style == "circle" {
    circle(radius: size / 2, fill: fill, stroke: none)
  } else if style == "square" {
    rect(width: size, height: size, fill: fill, stroke: none)
  } else {
    polygon(fill: fill, stroke: none, (0pt, 0pt), (size - notch, 0pt), (size, size / 2),
      (size - notch, size), (0pt, size), (notch, size / 2))
  }
  box(width: size, height: size, {
    place(top + left, shape)
    place(center + horizon, dx: notch / 2, initials)
  })
}

// `barcode`: bars drawn from runs of dark modules, each `(start, length)` in modules, then the
// human-readable caption `gap` below, centred under the bars.
#let dsl-barcode(runs: (), modules: 0, unit: 1pt, height: 10pt, quiet: 0pt, fill: black, gap: 0pt,
  caption: none) = {
  let bars = box(width: modules * unit + 2 * quiet, height: height, {
    for run in runs {
      place(top + left, dx: quiet + run.at(0) * unit, rect(width: run.at(1) * unit, height: height,
        fill: fill, stroke: none))
    }
  })
  if caption == none {
    bars
  } else {
    grid(columns: (auto,), row-gutter: gap, align: center, bars, caption)
  }
}

// `qrcode`: dark modules drawn row by row from runs `(row, start, length)` in modules, inside a quiet
// zone.
#let dsl-qrcode(runs: (), modules: 0, unit: 1pt, quiet: 0pt, fill: black) = box(
  width: modules * unit + 2 * quiet, height: modules * unit + 2 * quiet, {
    for run in runs {
      place(top + left, dx: quiet + run.at(1) * unit, dy: quiet + run.at(0) * unit,
        rect(width: run.at(2) * unit, height: unit, fill: fill, stroke: none))
    }
  })
