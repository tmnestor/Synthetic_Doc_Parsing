"""Tests for draw-time bounding-box capture."""

import pytest

from generators.exporters.geometry import BoxRecorder, rescale_vertical


def test_records_a_box_normalised_to_the_page() -> None:
    """A pixel box is divided by the page width and height, giving [left, top, right, bottom]."""
    recorder = BoxRecorder(200, 400)
    recorder.record("SUPPLIER_NAME", (20, 40, 100, 80), text="x")
    assert recorder.as_dict() == {"SUPPLIER_NAME": [0.1, 0.1, 0.5, 0.2]}


def test_line_item_members_are_indexed() -> None:
    """Members of one line-item column are recorded under distinct '[i]' keys and kept apart."""
    recorder = BoxRecorder(100, 100)
    recorder.record("LINE_ITEM_DESCRIPTIONS[0]", (10, 10, 50, 20), text="x")
    recorder.record("LINE_ITEM_DESCRIPTIONS[1]", (10, 30, 60, 40), text="x")
    boxes = recorder.as_dict()
    assert boxes["LINE_ITEM_DESCRIPTIONS[0]"] == [0.1, 0.1, 0.5, 0.2]
    assert boxes["LINE_ITEM_DESCRIPTIONS[1]"] == [0.1, 0.3, 0.6, 0.4]


def test_coordinates_stay_within_the_unit_square() -> None:
    """A box poking past the page edge, or above/left of it, is clamped into [0, 1]."""
    recorder = BoxRecorder(100, 200)
    recorder.record("OVER", (-10, -5, 130, 250), text="x")
    assert recorder.as_dict()["OVER"] == [0.0, 0.0, 1.0, 1.0]
    recorder.record("INSIDE", (0, 0, 100, 200), text="x")
    assert recorder.as_dict()["INSIDE"] == [0.0, 0.0, 1.0, 1.0]
    recorder.record("ROUNDED", (1, 1, 2, 2), text="x")
    assert recorder.as_dict()["ROUNDED"] == [0.01, 0.005, 0.02, 0.01]


def test_duplicate_field_is_rejected() -> None:
    """Two boxes for one field means the renderer drew it twice: ambiguous truth."""
    recorder = BoxRecorder(100, 100)
    recorder.record("TOTAL_AMOUNT", (1, 1, 5, 5), text="x")
    with pytest.raises(ValueError, match="TOTAL_AMOUNT") as err:
        recorder.record("TOTAL_AMOUNT", (2, 2, 6, 6), text="x")
    assert "give the second occurrence a distinct field key, or suppress the duplicate draw call" in str(
        err.value
    )
    # The first box survives untouched.
    assert recorder.as_dict()["TOTAL_AMOUNT"] == [0.01, 0.01, 0.05, 0.05]


@pytest.mark.parametrize(
    ("width", "height", "name"), [(0, 100, "width"), (100, 0, "height"), (-5, 100, "width")]
)
def test_zero_dimension_page_is_rejected(width: int, height: int, name: str) -> None:
    """A zero or negative page dimension is rejected, naming the offending dimension."""
    with pytest.raises(ValueError, match=f"BoxRecorder {name}") as err:
        BoxRecorder(width, height)
    assert f"pass the rendered image's pixel {name}" in str(err.value)


def test_rescale_vertical_is_a_noop_when_heights_match() -> None:
    """With equal heights the boxes come back unchanged."""
    boxes = {"A": [0.1, 0.2, 0.3, 0.4]}
    assert rescale_vertical(boxes, old_height=500, new_height=500) == boxes


def test_rescale_vertical_corrects_y_after_a_crop() -> None:
    """Halving the page height doubles the y fractions and leaves x untouched."""
    boxes = {"A": [0.1, 0.1, 0.3, 0.2]}
    assert rescale_vertical(boxes, old_height=1000, new_height=500) == {"A": [0.1, 0.2, 0.3, 0.4]}
    # Pixel-level cross-check: a box at y 50..100 of a 1000 px canvas is y 50..100 of the 500 px crop.
    recorder = BoxRecorder(200, 1000)
    recorder.record("A", (20, 50, 60, 100), text="x")
    rescaled = rescale_vertical(recorder.as_dict(), old_height=1000, new_height=500)["A"]
    assert [round(rescaled[1] * 500), round(rescaled[3] * 500)] == [50, 100]
    assert [round(rescaled[0] * 200), round(rescaled[2] * 200)] == [20, 60]


def test_rescale_vertical_clamps_results_into_the_unit_square() -> None:
    """A box below the cropped page bottom is clamped to 1.0 rather than escaping the unit square."""
    boxes = {"A": [0.1, 0.4, 0.3, 0.9]}
    assert rescale_vertical(boxes, old_height=1000, new_height=500) == {"A": [0.1, 0.8, 0.3, 1.0]}


def test_rescale_vertical_does_not_mutate_the_input() -> None:
    """The input mapping and its inner lists are left exactly as passed, and the result is a new object."""
    boxes = {"A": [0.1, 0.1, 0.3, 0.2]}
    snapshot = {"A": list(boxes["A"])}
    result = rescale_vertical(boxes, old_height=1000, new_height=500)
    assert boxes == snapshot
    assert result is not boxes
    same = rescale_vertical(boxes, old_height=10, new_height=10)
    assert same is not boxes
