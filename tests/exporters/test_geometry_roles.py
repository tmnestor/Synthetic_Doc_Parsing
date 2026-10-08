"""BoxRecorder ink roles: every drawn line is recorded with a role, and exports stay scored-only."""

import pytest

from generators.exporters.geometry import INK_ROLES, BoxRecorder, InkRecord


def test_ink_roles_are_the_three_documented_roles() -> None:
    assert INK_ROLES == ("scored", "distractor", "text")


def test_record_ink_keeps_pixel_boxes_in_draw_order() -> None:
    recorder = BoxRecorder(100, 200)
    recorder.record_ink((1, 2, 3, 4), "text", text="x", size=10, bold=False)
    recorder.record_ink((5, 6, 7, 8), "scored", text="x", size=10, bold=False)
    recorder.record_ink((9, 10, 11, 12), "distractor", text="x", size=10, bold=False)
    assert recorder.ink_boxes() == [
        ("text", (1, 2, 3, 4)),
        ("scored", (5, 6, 7, 8)),
        ("distractor", (9, 10, 11, 12)),
    ]


def test_a_mark_is_a_distractor_box_flagged_as_a_mark() -> None:
    recorder = BoxRecorder(100, 200)
    recorder.record_ink((0, 0, 10, 10), "distractor", text="", size=0, bold=False, mark=True)
    recorder.record_ink((0, 20, 10, 30), "distractor", text="x", size=10, bold=False)
    assert recorder.ink_boxes() == [("distractor", (0, 0, 10, 10)), ("distractor", (0, 20, 10, 30))]
    assert recorder.ink_records() == [
        InkRecord("distractor", (0, 0, 10, 10), True, "", 0, False),
        InkRecord("distractor", (0, 20, 10, 30), False, "x", 10, False),
    ]


def test_ink_never_reaches_the_exported_boxes() -> None:
    recorder = BoxRecorder(100, 200)
    recorder.record("TOTAL_AMOUNT", (10, 20, 30, 40), text="x")
    before = recorder.as_dict()
    recorder.record_ink((10, 20, 30, 40), "scored", text="x", size=10, bold=False)
    recorder.record_ink((0, 0, 100, 200), "distractor", text="", size=0, bold=False, mark=True)
    assert recorder.as_dict() == before == {"TOTAL_AMOUNT": [0.1, 0.1, 0.3, 0.2]}


def test_the_same_box_may_be_inked_twice() -> None:
    """Ink is a measure of drawn area, not a field location, so repeats are legitimate."""
    recorder = BoxRecorder(100, 200)
    recorder.record_ink((1, 1, 2, 2), "text", text="x", size=10, bold=False)
    recorder.record_ink((1, 1, 2, 2), "text", text="x", size=10, bold=False)
    assert len(recorder.ink_boxes()) == 2


@pytest.mark.parametrize("role", ["Scored", "mark", "", "field"])
def test_an_unknown_role_is_refused(role: str) -> None:
    recorder = BoxRecorder(100, 200)
    with pytest.raises(ValueError, match="role"):
        recorder.record_ink((0, 0, 1, 1), role, text="x", size=10, bold=False)


def test_a_scored_mark_is_refused() -> None:
    """A code mark is never a scored value; marking a scored box would hide it from the ratio."""
    recorder = BoxRecorder(100, 200)
    with pytest.raises(ValueError, match="mark"):
        recorder.record_ink((0, 0, 1, 1), "scored", text="", size=0, bold=False, mark=True)


def test_an_inverted_box_is_refused() -> None:
    recorder = BoxRecorder(100, 200)
    with pytest.raises(ValueError, match="box"):
        recorder.record_ink((5, 0, 1, 1), "text", text="x", size=10, bold=False)
