"""The recorder's block scopes and table cells: which block, and which cell, drew each line of ink."""

import pytest

from generators.exporters.geometry import BlockRecord, BoxRecorder, Cell, InkRecord

BOX = (10, 20, 30, 40)


def scope(recorder: BoxRecorder, kind: str = "text", *, path: str = "body[0]", **extra):
    """Open a block scope with an unranked, unanchored, uncategorised block unless `extra` says so."""
    params = {"anchor": None, "parse_category": None, "parse_order": 0, "join": None, **extra}
    return recorder.block_scope(kind, path=path, **params)


def ink(recorder: BoxRecorder, text: str = "Line") -> None:
    """Record one plain text line."""
    recorder.record_ink(BOX, "text", text=text, size=12, bold=False)


def test_ink_outside_any_scope_carries_no_block_and_no_cell() -> None:
    """A line drawn with no scope open belongs to no block."""
    recorder = BoxRecorder(100, 100)
    ink(recorder)
    (record,) = recorder.ink_records()
    assert record.block is None
    assert record.cell is None
    assert recorder.block_records() == []


def test_a_scope_records_one_block_and_stamps_its_ink() -> None:
    """A scope creates one block, numbered in draw order, and every line drawn inside carries its id."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "banner", path="body[0]", anchor="foot", parse_category="title", parse_order=-1):
        ink(recorder, "Heading")
    with scope(recorder, "text", path="body[1]"):
        ink(recorder, "Body")
    assert recorder.block_records() == [
        BlockRecord(0, "banner", "body[0]", "foot", "title", -1, None),
        BlockRecord(1, "text", "body[1]", None, None, 0, None),
    ]
    assert [record.block for record in recorder.ink_records()] == [0, 1]


def test_a_scope_records_a_table_join() -> None:
    """A table's join is recorded last, after its column count; a block built without one has none."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "table", path="body[3]", join="previous_table"):
        recorder.set_table_columns(5)
    assert recorder.block_records() == [
        BlockRecord(0, "table", "body[3]", None, None, 0, 5, "previous_table")
    ]
    assert BlockRecord(1, "table", "body[4]", None, None, 0, 5).join is None


def test_a_scope_opened_inside_a_scope_creates_no_block() -> None:
    """The outermost open scope owns the ink: an inner scope adds no block, its ink the outer id."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "stack", path="body[2]"):
        ink(recorder, "Before")
        with scope(recorder, "text", path="body[2].children[0]"):
            ink(recorder, "Inner")
        ink(recorder, "After")
    ink(recorder, "Outside")
    assert recorder.block_records() == [BlockRecord(0, "stack", "body[2]", None, None, 0, None)]
    assert [record.block for record in recorder.ink_records()] == [0, 0, 0, None]


def test_a_scope_closes_when_its_body_raises() -> None:
    """An exception inside a scope still closes it, so later ink is not attributed to the failed block."""
    recorder = BoxRecorder(100, 100)
    with pytest.raises(RuntimeError, match="boom"), scope(recorder):
        ink(recorder)
        raise RuntimeError("boom")
    ink(recorder, "Later")
    assert [record.block for record in recorder.ink_records()] == [0, None]


def test_set_cell_stamps_until_cleared() -> None:
    """Every line until the next set_cell carries the cell; None clears it."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "table"):
        recorder.set_table_columns(3)
        recorder.set_cell(Cell(row=0, column=1, span=1))
        ink(recorder, "Header")
        ink(recorder, "line two")
        recorder.set_cell(Cell(row=1, column=0, span=3))
        ink(recorder, "Date row")
        recorder.set_cell(None)
        ink(recorder, "No cell")
    assert [record.cell for record in recorder.ink_records()] == [
        Cell(0, 1, 1),
        Cell(0, 1, 1),
        Cell(1, 0, 3),
        None,
    ]
    assert recorder.block_records()[0].columns == 3


def test_closing_a_block_clears_the_cell() -> None:
    """A cell left set when its table's scope closes does not leak onto the next block's ink."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "table"):
        recorder.set_table_columns(2)
        recorder.set_cell(Cell(0, 0, 1))
        ink(recorder)
    with scope(recorder, "text", path="body[1]"):
        ink(recorder, "After")
    assert [record.cell for record in recorder.ink_records()] == [Cell(0, 0, 1), None]


def test_set_table_columns_refuses_with_no_block_open() -> None:
    """Columns belong to a table block; with none open there is nothing to set them on."""
    recorder = BoxRecorder(100, 100)
    with pytest.raises(ValueError, match="no block is open") as err:
        recorder.set_table_columns(4)
    assert "Remediation:" in str(err.value)


def test_set_table_columns_refuses_outside_a_table_scope() -> None:
    """A table nested in a block of another kind cannot declare that block's columns."""
    recorder = BoxRecorder(100, 100)
    with scope(recorder, "stack"), pytest.raises(ValueError, match="'stack'") as err:
        recorder.set_table_columns(4)
    assert "Remediation:" in str(err.value)


def test_ink_records_keep_their_positional_construction() -> None:
    """The two new fields trail with defaults, so a six-field record still builds and compares as before."""
    record = InkRecord("text", BOX, False, "Line", 12, False)
    assert record.block is None
    assert record.cell is None
    recorder = BoxRecorder(100, 100)
    ink(recorder)
    assert recorder.ink_records() == [record]
