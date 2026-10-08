"""Density metrics on synthetic ink with known areas."""

import pytest

from generators.distractors.density import (
    DensityReport,
    density_report,
    distractor_share,
    page_fill,
    scored_ink_ratio,
)
from generators.exporters.geometry import InkRecord


def ink(role: str, box: tuple[int, int, int, int], mark: bool = False) -> InkRecord:
    return InkRecord(role, box, mark, "" if mark else "x", 0 if mark else 10, False)


# Areas: scored 10x10 = 100, distractor 20x10 = 200, text 10x30 = 300, mark 40x40 = 1600.
SCORED = ink("scored", (0, 0, 10, 10))
DISTRACTOR = ink("distractor", (0, 20, 20, 30))
TEXT = ink("text", (0, 40, 10, 70))
MARK = ink("distractor", (50, 100, 90, 140), mark=True)


def test_scored_ratio_is_scored_area_over_all_text_area() -> None:
    assert scored_ink_ratio([SCORED, DISTRACTOR, TEXT]) == pytest.approx(100 / 600)


def test_a_mark_is_left_out_of_the_ratio() -> None:
    assert scored_ink_ratio([SCORED, DISTRACTOR, TEXT, MARK]) == pytest.approx(100 / 600)


def test_only_scored_ink_gives_a_ratio_of_one() -> None:
    assert scored_ink_ratio([SCORED]) == 1.0


def test_no_scored_ink_gives_a_ratio_of_zero() -> None:
    assert scored_ink_ratio([TEXT, DISTRACTOR]) == 0.0


def test_a_scored_box_never_counts_as_distractor() -> None:
    report = density_report([SCORED, SCORED], page_height=100)
    assert report.scored_area == 200
    assert report.distractor_area == 0
    assert report.text_area == 0


def test_page_fill_is_the_lowest_ink_over_page_height() -> None:
    assert page_fill([SCORED, TEXT], page_height=140) == pytest.approx(70 / 140)


def test_page_fill_counts_a_mark() -> None:
    assert page_fill([SCORED, MARK], page_height=200) == pytest.approx(140 / 200)


def test_the_report_carries_both_metrics_and_every_area() -> None:
    report = density_report([SCORED, DISTRACTOR, TEXT, MARK], page_height=280)
    assert report._replace(scored_ink_ratio=0.0, page_fill=0.0, distractor_share=0.0) == DensityReport(
        scored_ink_ratio=0.0,
        page_fill=0.0,
        distractor_share=0.0,
        scored_area=100,
        distractor_area=200,
        text_area=300,
        mark_area=1600,
    )
    assert report.scored_ink_ratio == pytest.approx(100 / 600)
    assert report.page_fill == pytest.approx(0.5)
    assert report.distractor_share == pytest.approx(200 / 600)


def test_a_render_without_text_ink_has_no_ratio() -> None:
    with pytest.raises(ValueError, match="no text ink"):
        scored_ink_ratio([MARK])


def test_a_render_without_ink_has_no_page_fill() -> None:
    with pytest.raises(ValueError, match="no ink"):
        page_fill([], page_height=100)


def test_a_non_positive_page_height_is_refused() -> None:
    with pytest.raises(ValueError, match="page_height"):
        page_fill([SCORED], page_height=0)


def test_distractor_share_is_distractor_text_over_all_text_marks_excluded() -> None:
    assert distractor_share([SCORED, DISTRACTOR, TEXT, MARK]) == pytest.approx(200 / 600)


def test_a_mark_alone_adds_no_distractor_share() -> None:
    assert distractor_share([SCORED, TEXT, MARK]) == 0.0


def test_a_render_without_text_ink_has_no_distractor_share() -> None:
    with pytest.raises(ValueError, match="no text ink"):
        distractor_share([MARK])
